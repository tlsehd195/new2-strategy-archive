"""Record NEW Binance USDT-M perpetual listings as they happen (ADR 0061, forward data only).

Public, key-less market data only.  Runs on a machine that can reach ``fapi.binance.com`` (the Oracle VM or the
user's PC — NOT the cloud research container).  No account, API key, signature or order code path exists: the URL
builder refuses endpoints outside the allow-list and authentication parameters.

    python newlisting_collect.py --allow-network --hours 720 --out ~/newlisting_data

What it does
------------
* Every ``--discover-sec`` (default 60) it reads ``/fapi/v1/exchangeInfo``.  The FIRST run stores every symbol that
  exists then as the BASELINE (``state.json``); those are never tracked.  Any later USDT PERPETUAL symbol that is not
  in the baseline is a NEW LISTING: its raw exchangeInfo entry (``onboardDate``, status, filters) is appended to
  ``listing_events.jsonl`` together with the local detection time (``detected_at_ms``).
* A tracked symbol is sampled for 30 days after its ``onboardDate``: every 30 s in the first 24 h, every 5 min up to
  day 3, every 15 min up to day 30.  A sample = depth (limit 100, top 50 levels), mark/index/funding
  (``premiumIndex``) in ``listing_book_<SYMBOL>_<YYYYMMDD>.jsonl``; new 1-minute klines and aggregated trades since
  the last sample go to ``listing_klines_*`` and ``listing_trades_*`` (every trade, paged by ``fromId``).
* Nothing is invented: a failed request is written as a ``FETCH_FAILED`` record.  A symbol that is not TRADING yet
  (status PENDING_TRADING) is only watched until it opens; ``first_sample_ms - onboardDate`` is stored so a late start
  is visible.  ``--track-recent-days N`` (first run only) also tracks baseline symbols listed within N days and marks
  them ``late_start`` — they are NOT point-in-time events and the analysis must treat them as such.
* The run is bounded (``--hours`` <= 720), resumable (state is kept) and ends with a SHA-256 manifest
  ``listing_manifest.json`` (``--manifest-only`` re-seals without network).

It records market data, not anybody's fills, so it can never make a cost model VERIFIED by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Optional, Sequence

from data_fetch import FetchFailure, fetch_with_retry
from portable_util import append_line, iso_now

SCHEMA = "newlisting_v1"
BASE = "https://fapi.binance.com"
ALLOWED = {"/fapi/v1/exchangeInfo", "/fapi/v1/depth", "/fapi/v1/klines", "/fapi/v1/aggTrades",
           "/fapi/v1/premiumIndex", "/fapi/v1/time"}
FORBIDDEN_PARAMS = {"signature", "timestamp", "recvwindow", "apikey", "listenkey"}
DEPTH_LEVELS = 50
MIN_FREE_BYTES = 500 * 1024 * 1024
TRACK_DAYS = 30
DAY_MS = 86_400_000
MAX_TRADE_PAGES = 5          # per sample; a missed tail is flagged TRADES_TRUNCATED, never filled in
MAX_CONCURRENT_WARN = 25


class CollectRefused(RuntimeError):
    pass


def public_url(path: str, **params) -> str:
    if path not in ALLOWED:
        raise CollectRefused(f"{path} is not an allowed public endpoint")
    bad = {k for k in params if k.lower() in FORBIDDEN_PARAMS}
    if bad:
        raise CollectRefused(f"authentication parameters are never sent: {sorted(bad)}")
    return f"{BASE}{path}?{urllib.parse.urlencode(params)}" if params else BASE + path


def _get_json(url: str, opener, sleep):
    return json.loads(fetch_with_retry(url, opener=opener, attempts=4, base_delay=2.0, sleep=sleep))


def interval_for_age(age_ms: int) -> float:
    """Sampling period in seconds for a symbol that is ``age_ms`` past its onboardDate."""
    if age_ms < DAY_MS:
        return 30.0
    if age_ms < 3 * DAY_MS:
        return 300.0
    return 900.0


def is_new_perp(info: dict) -> bool:
    return (info.get("contractType") == "PERPETUAL" and info.get("quoteAsset") == "USDT"
            and info.get("symbol", "").endswith("USDT"))


def _day(t_ms: int) -> str:
    return time.strftime("%Y%m%d", time.gmtime(t_ms / 1000))


def _file(out: Path, kind: str, symbol: str, t_ms: int) -> Path:
    return out / symbol / f"listing_{kind}_{symbol}_{_day(t_ms)}.jsonl"


class StateCorrupt(RuntimeError):
    """state.json exists but cannot be read (ADR 0070): never re-baseline silently."""


def load_state(out: Path) -> dict:
    p = out / "state.json"
    if not p.exists():
        return {}
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # re-baselining would forget every listing tracked so far without a trace
        raise StateCorrupt(f"{p} unreadable ({e}); fix or move it aside explicitly") from e
    if not isinstance(st, dict):
        raise StateCorrupt(f"{p} is not a JSON object")
    return st


def save_state(out: Path, state: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "state.json.tmp"
    tmp.write_text(json.dumps(state, sort_keys=True, indent=1), encoding="utf-8")
    tmp.replace(out / "state.json")


def discover(out: Path, state: dict, now_ms: int, *, opener=None, sleep=time.sleep,
             track_recent_days: float = 0.0, report: Optional[dict] = None) -> list:
    """Read exchangeInfo, update ``state`` in place, return the list of newly tracked
    symbols.  ``report["ok"]`` says whether exchangeInfo was read (ADR 0078, H-05:
    a failed read also returned [] and the run counted it as a quiet success)."""
    if report is not None:
        report["ok"] = False
    try:
        info = _get_json(public_url("/fapi/v1/exchangeInfo"), opener, sleep)
        syms = [s for s in info["symbols"] if is_new_perp(s)]
    except (FetchFailure, KeyError, TypeError, ValueError) as e:
        append_line(out / "listing_events.jsonl", {"schema": SCHEMA, "event": "DISCOVER_FAILED",
                                                    "at_ms": now_ms, "detail": f"{type(e).__name__}: {e}"[:300]})
        return []
    if report is not None:
        report["ok"] = True
    first_run = "known" not in state
    state.setdefault("known", [])
    state.setdefault("tracked", {})
    known = set(state["known"])
    new = []
    for s in syms:
        name = s["symbol"]
        if name in known:
            continue
        known.add(name)
        late = False
        if first_run:
            recent = track_recent_days > 0 and now_ms - int(s.get("onboardDate", 0)) <= track_recent_days * DAY_MS
            if not recent:
                continue
            late = True
        state["tracked"][name] = {"onboardDate": int(s.get("onboardDate", 0)), "detected_at_ms": now_ms,
                                  "late_start": late, "first_sample_ms": None, "next_ms": 0,
                                  "last_trade_id": None, "last_kline_ms": None, "done": False}
        append_line(out / "listing_events.jsonl", {"schema": SCHEMA, "event": "NEW_LISTING", "symbol": name,
                                                    "detected_at_ms": now_ms, "late_start": late, "exchange_info": s})
        new.append(name)
    state["known"] = sorted(known)
    if first_run:
        state["baseline_at_ms"] = now_ms
        state["baseline_count"] = len(state["known"])
    return new


def sample(out: Path, symbol: str, st: dict, now_ms: int, *, opener=None, sleep=time.sleep) -> bool:
    """One sample for one tracked symbol.  Returns True if the book snapshot succeeded."""
    rec = {"schema": SCHEMA, "symbol": symbol, "fetched_at_ms": now_ms,
           "age_ms": now_ms - st["onboardDate"] if st["onboardDate"] else None}
    ok = False
    try:
        d = _get_json(public_url("/fapi/v1/depth", symbol=symbol, limit=100), opener, sleep)
        rec["depth"] = {"E": d.get("E"), "T": d.get("T"), "lastUpdateId": d.get("lastUpdateId"),
                        "bids": d["bids"][:DEPTH_LEVELS], "asks": d["asks"][:DEPTH_LEVELS]}
        p = _get_json(public_url("/fapi/v1/premiumIndex", symbol=symbol), opener, sleep)
        rec["premium"] = {k: p.get(k) for k in ("markPrice", "indexPrice", "lastFundingRate",
                                                 "nextFundingTime", "interestRate", "time")}
        rec["status"] = "OK"
        ok = True
        if st["first_sample_ms"] is None:
            st["first_sample_ms"] = now_ms
            rec["first_sample"] = True
            rec["delay_after_onboard_ms"] = rec["age_ms"]
    except FetchFailure as e:
        rec.update(status="FETCH_FAILED", failure_kind=e.kind, detail=e.detail[:300])
    except (KeyError, TypeError, ValueError, IndexError) as e:
        rec.update(status="BAD_RESPONSE", detail=f"{type(e).__name__}: {e}"[:300])
    append_line(_file(out, "book", symbol, now_ms), rec)
    if not ok:
        return False
    _pull_klines(out, symbol, st, now_ms, opener, sleep)
    _pull_trades(out, symbol, st, now_ms, opener, sleep)
    return True


def _pull_klines(out, symbol, st, now_ms, opener, sleep):
    try:
        params = dict(symbol=symbol, interval="1m", limit=1000)
        if st["last_kline_ms"] is not None:
            params["startTime"] = st["last_kline_ms"] + 1
        else:
            params["startTime"] = st["onboardDate"] or max(0, now_ms - DAY_MS)
        rows = _get_json(public_url("/fapi/v1/klines", **params), opener, sleep)
        closed = [r for r in rows if int(r[6]) < now_ms]
        for r in closed:
            append_line(_file(out, "klines", symbol, int(r[0])),
                        {"t": int(r[0]), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5], "qv": r[7], "n": r[8]})
        if closed:
            st["last_kline_ms"] = int(closed[-1][0])
    except (FetchFailure, KeyError, TypeError, ValueError, IndexError) as e:
        append_line(_file(out, "klines", symbol, now_ms), {"fetched_at_ms": now_ms, "status": "KLINES_FAILED",
                                                           "detail": f"{type(e).__name__}: {e}"[:300]})


def _pull_trades(out, symbol, st, now_ms, opener, sleep):
    try:
        for page in range(MAX_TRADE_PAGES):
            params = dict(symbol=symbol, limit=1000)
            if st["last_trade_id"] is not None:
                params["fromId"] = st["last_trade_id"] + 1
            elif st["onboardDate"]:
                params["startTime"] = st["onboardDate"]
            rows = _get_json(public_url("/fapi/v1/aggTrades", **params), opener, sleep)
            for x in rows:
                append_line(_file(out, "trades", symbol, int(x["T"])),
                            {"a": x["a"], "p": x["p"], "q": x["q"], "T": x["T"], "m": x["m"]})
            if rows:
                st["last_trade_id"] = int(rows[-1]["a"])
            if len(rows) < 1000:
                return
        append_line(_file(out, "trades", symbol, now_ms), {"fetched_at_ms": now_ms, "status": "TRADES_TRUNCATED",
                                                           "detail": f"more than {MAX_TRADE_PAGES * 1000} trades pending"})
    except (FetchFailure, KeyError, TypeError, ValueError, IndexError) as e:
        append_line(_file(out, "trades", symbol, now_ms), {"fetched_at_ms": now_ms, "status": "TRADES_FAILED",
                                                           "detail": f"{type(e).__name__}: {e}"[:300]})


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(out: Path) -> dict:
    files = []
    for p in sorted(out.rglob("listing_*.jsonl")):
        files.append({"path": p.relative_to(out).as_posix(), "bytes": p.stat().st_size, "sha256": _sha256_file(p)})
    man = {"schema": SCHEMA, "written_at": iso_now(), "files": files}
    (out / "listing_manifest.json").write_text(json.dumps(man, indent=1), encoding="utf-8")
    return man


def collect(out: Path, *, hours: float, discover_sec: float = 60.0, allow_network: bool = False,
            track_recent_days: float = 0.0, opener=None, sleep: Callable[[float], None] = time.sleep,
            clock: Callable[[], float] = time.time, max_failures_in_row: int = 30) -> dict:
    if not allow_network:
        raise CollectRefused("network access is opt-in: pass --allow-network")
    if not (0 < hours <= 24 * 30):
        raise ValueError("hours must be in (0, 720] — the run is always bounded")
    if discover_sec < 30:
        raise ValueError("discover-sec must be >= 30")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    state = load_state(out)
    end = clock() + hours * 3600
    next_discover = 0.0
    fails, stop, samples, new_total = 0, "DURATION_REACHED", 0, 0
    disc_fails, disc_ok = 0, False
    try:
        while clock() < end:
            t0 = clock()
            now_ms = int(t0 * 1000)
            if shutil.disk_usage(out).free < MIN_FREE_BYTES:
                stop = "DISK_LOW"
                break
            if t0 >= next_discover:
                drep: dict = {}
                new_total += len(discover(out, state, now_ms, opener=opener, sleep=sleep,
                                          track_recent_days=track_recent_days, report=drep))
                next_discover = t0 + discover_sec
                disc_ok = disc_ok or drep["ok"]
                disc_fails = 0 if drep["ok"] else disc_fails + 1
                if disc_fails >= max_failures_in_row:
                    stop = "DISCOVER_FAILING"
                    break
            for sym, st in state.get("tracked", {}).items():
                if st["done"]:
                    continue
                if st["onboardDate"] and now_ms - st["onboardDate"] > TRACK_DAYS * DAY_MS:
                    st["done"] = True
                    continue
                if now_ms < st["next_ms"]:
                    continue
                age = now_ms - st["onboardDate"] if st["onboardDate"] else 0
                if sample(out, sym, st, now_ms, opener=opener, sleep=sleep):
                    fails = 0
                    samples += 1
                else:
                    fails += 1
                st["next_ms"] = now_ms + int(interval_for_age(max(age, 0)) * 1000)
            save_state(out, state)
            if fails >= max_failures_in_row:
                stop = "TOO_MANY_FAILURES"
                break
            sleep(max(0.0, 5.0 - (clock() - t0)))
    except KeyboardInterrupt:
        stop = "INTERRUPTED"
    if stop == "DURATION_REACHED" and not disc_ok:
        stop = "NO_SUCCESSFUL_DISCOVER"          # ADR 0078 (H-05): never a quiet exit 0
    save_state(out, state)
    man = write_manifest(out)
    active = [s for s, v in state.get("tracked", {}).items() if not v["done"]]
    return {"stopped": stop, "samples": samples, "new_listings_this_run": new_total, "active": active,
            "files": len(man["files"]), "at": iso_now()}


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--allow-network", action="store_true", help="required: opt in to public HTTP requests")
    ap.add_argument("--hours", type=float, help="how long to run (<= 720; required unless --manifest-only)")
    ap.add_argument("--manifest-only", action="store_true", help="no network: only re-seal listing_manifest.json")
    ap.add_argument("--discover-sec", type=float, default=60.0)
    ap.add_argument("--track-recent-days", type=float, default=0.0,
                    help="first run only: also track baseline symbols listed within N days (marked late_start)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.manifest_only:
        if not Path(a.out).is_dir():
            print(f"REFUSED: {a.out} is not a directory", file=sys.stderr)
            return 1
        man = write_manifest(Path(a.out))
        print(json.dumps({"files": len(man["files"])}))
        return 0
    if a.hours is None:
        ap.error("--hours is required (the run is always bounded)")
    try:
        r = collect(Path(a.out), hours=a.hours, discover_sec=a.discover_sec, allow_network=a.allow_network,
                    track_recent_days=a.track_recent_days)
    except (CollectRefused, StateCorrupt, ValueError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    print(json.dumps(r, indent=1))
    return 0 if r["stopped"] in ("DURATION_REACHED", "INTERRUPTED") else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
