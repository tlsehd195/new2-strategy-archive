"""Collect PUBLIC Binance USDT-M market data for cost estimation (ADR 0007).

Runs on a machine that can reach ``fapi.binance.com`` (this research
container cannot).  Only key-less public endpoints are used — order book
depth, the latest 15m klines and recent aggregated trades.  There is no
account, API key, signature or order code path: the URL builder refuses
any endpoint outside the allow-list and any authentication parameter.

    python public_market_collect.py --allow-network --hours 72 --out ../../data/public_market

One JSON line per symbol per tick is appended to
``<out>/<SYMBOL>/public_<YYYYMMDD>.jsonl`` (UTC day).  The run is bounded
(``--hours`` is required), can be stopped with Ctrl+C at any time and
resumed by running the same command again (lines are only appended).
``<out>/collect_manifest.json`` lists every file with its SHA-256 at the end
of a run.  The output feeds ``public_cost_estimate.py``; it is market data,
not a record of anyone's fills, and can never verify a cost model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Optional, Sequence

from data_fetch import FetchFailure, fetch_with_retry
from portable_util import append_line, iso_now

SCHEMA = "public_market_snapshot_v1"
BASE = "https://fapi.binance.com"
#: key-less public endpoints only (Binance USDT-M futures market data)
ALLOWED = {"/fapi/v1/depth", "/fapi/v1/klines", "/fapi/v1/aggTrades", "/fapi/v1/time"}
FORBIDDEN_PARAMS = {"signature", "timestamp", "recvwindow", "apikey", "listenkey"}
DEPTH_LEVELS = 50          # stored per side (fetched with limit=100)


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


def snapshot(symbol: str, *, opener=None, sleep=time.sleep, now_ms: Optional[int] = None) -> dict:
    """One tick for one symbol.  Failures are recorded, never invented."""
    t = int(now_ms if now_ms is not None else time.time() * 1000)
    rec = {"schema": SCHEMA, "symbol": symbol, "fetched_at_ms": t}
    try:
        d = _get_json(public_url("/fapi/v1/depth", symbol=symbol, limit=100), opener, sleep)
        rec["depth"] = {"E": d.get("E"), "T": d.get("T"), "lastUpdateId": d.get("lastUpdateId"),
                        "bids": d["bids"][:DEPTH_LEVELS], "asks": d["asks"][:DEPTH_LEVELS]}
        k = _get_json(public_url("/fapi/v1/klines", symbol=symbol, interval="15m", limit=2), opener, sleep)
        # the last row is the bar still forming; keep only the last CLOSED one
        closed = [r for r in k if int(r[6]) < t]
        if closed:
            r = closed[-1]
            rec["kline_closed"] = {"open_time": int(r[0]), "close_time": int(r[6]), "high": r[2],
                                   "low": r[3], "close": r[4], "quote_volume": r[7]}
        a = _get_json(public_url("/fapi/v1/aggTrades", symbol=symbol, limit=100), opener, sleep)
        rec["agg_trades"] = [{"p": x["p"], "q": x["q"], "T": x["T"], "m": x["m"]} for x in a]
        rec["status"] = "OK"
    except FetchFailure as e:
        rec.update(status="FETCH_FAILED", failure_kind=e.kind, detail=e.detail[:300])
    except (KeyError, TypeError, ValueError, IndexError) as e:
        rec.update(status="BAD_RESPONSE", detail=f"{type(e).__name__}: {e}"[:300])
    return rec


def _day_file(out: Path, symbol: str, t_ms: int) -> Path:
    return out / symbol / ("public_" + time.strftime("%Y%m%d", time.gmtime(t_ms / 1000)) + ".jsonl")


def write_manifest(out: Path) -> dict:
    files = []
    for p in sorted(out.rglob("public_*.jsonl")):
        files.append({"path": str(p.relative_to(out)), "bytes": p.stat().st_size,
                      "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    man = {"schema": "public_market_manifest_v1", "generated_at": iso_now(), "source": BASE,
           "endpoints": sorted(ALLOWED), "files": files}
    (out / "collect_manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True), encoding="utf-8")
    return man


def collect(out: Path, symbols: Sequence[str], *, hours: float, interval: float = 30.0,
            allow_network: bool = False, opener=None, sleep: Callable[[float], None] = time.sleep,
            clock: Callable[[], float] = time.time, max_failures_in_row: int = 20) -> dict:
    if not allow_network:
        raise CollectRefused("network access is opt-in: pass --allow-network")
    if not (0 < hours <= 24 * 30):
        raise ValueError("hours must be in (0, 720] — the run is always bounded")
    if interval < 5:
        raise ValueError("interval must be >= 5 seconds (stay far below public rate limits)")
    out = Path(out)
    end = clock() + hours * 3600
    counts = {s: {"OK": 0, "FAILED": 0} for s in symbols}
    fails_in_row, stop = 0, "DURATION_REACHED"
    try:
        while clock() < end:
            t0 = clock()
            for s in symbols:
                rec = snapshot(s, opener=opener, sleep=sleep, now_ms=int(t0 * 1000))
                append_line(_day_file(out, s, rec["fetched_at_ms"]), rec)
                ok = rec["status"] == "OK"
                counts[s]["OK" if ok else "FAILED"] += 1
                fails_in_row = 0 if ok else fails_in_row + 1
            if fails_in_row >= max_failures_in_row:
                stop = "TOO_MANY_FAILURES"
                break
            sleep(max(0.0, interval - (clock() - t0)))
    except KeyboardInterrupt:
        stop = "INTERRUPTED"
    man = write_manifest(out) if out.exists() else {"files": []}
    return {"stopped": stop, "counts": counts, "files": len(man["files"]), "at": iso_now()}


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--allow-network", action="store_true", help="required: opt in to public HTTP requests")
    ap.add_argument("--hours", type=float, required=True, help="how long to collect (bounded)")
    ap.add_argument("--interval", type=float, default=30.0, help="seconds between ticks (>= 5)")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        r = collect(Path(a.out), a.symbols, hours=a.hours, interval=a.interval,
                    allow_network=a.allow_network)
    except (CollectRefused, ValueError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    print(json.dumps(r, indent=1))
    return 0 if r["stopped"] in ("DURATION_REACHED", "INTERRUPTED") else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
