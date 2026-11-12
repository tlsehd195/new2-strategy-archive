"""Opt-in acquisition of Binance USD-M monthly archives (auto-evolution §7-2).

Public market-data files only (data.binance.vision): 15m klines and funding
rates.  No account, no key, no order endpoint.  Network access happens only
when explicitly allowed (``--allow-network`` / ``allow=True``); otherwise the
call reports DISABLED and nothing is touched.

Per monthly file:  temp download -> SHA-256 against the published CHECKSUM
-> safe unzip of the single CSV -> the same quality gate the engine uses ->
atomic rename into ``data_dir`` -> provenance line.  An existing file is NEVER
overwritten (a different upstream file is reported as CONFLICT).  Any
failure leaves no partial file behind and is reported as BLOCKED_DATA for
that month — the engine then stays fail-closed on the gap.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional, Sequence

BASE = "https://data.binance.vision/data/futures/um/monthly"
#: the same public bucket served straight from S3 (data.binance.vision is a
#: CDN in front of it).  Identical files and CHECKSUMs; used where the CDN host
#: is blocked by an egress policy.  The URL actually used is in the provenance.
S3_MIRROR = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/data/futures/um/monthly"
KINDS = {
    "klines": ("klines/{sym}/15m/{sym}-15m-{ym}.zip", "klines", "{sym}-15m-{ym}.csv"),
    "funding": ("fundingRate/{sym}/{sym}-fundingRate-{ym}.zip", "funding", "{sym}-fundingRate-{ym}.csv"),
}


PROVENANCE_SCHEMA = "provenance_v2"


class FetchError(RuntimeError):
    pass


class FetchFailure(RuntimeError):
    """A download that failed after the retry policy, with its class:

    NOT_PUBLISHED   HTTP 404 — the archive does not exist (yet): a data
                    shortage, not a transport problem; never retried
    PROXY_ERROR     the egress proxy refused the tunnel (HTTP 403/407 on
                    CONNECT, "Tunnel connection failed"): an access policy
    EXCHANGE_ERROR  any other HTTP 4xx from the server; never retried
    TRANSIENT       HTTP 429/5xx, timeouts, resets, DNS — retried with
                    exponential backoff, at most ``attempts`` times
    """

    def __init__(self, kind: str, detail: str, attempts: int):
        super().__init__(f"{kind} after {attempts} attempt(s): {detail}")
        self.kind, self.detail, self.attempts = kind, detail, attempts


def classify_error(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    text = f"{type(exc).__name__}: {exc}".lower()
    if "tunnel connection failed" in text or "proxy" in text or code == 407:
        return "PROXY_ERROR"
    if isinstance(code, int):
        if code == 404:
            return "NOT_PUBLISHED"
        if code == 403:
            return "PROXY_ERROR" if "tunnel" in text else "EXCHANGE_ERROR"
        if code == 429 or code >= 500:
            return "TRANSIENT"
        if 400 <= code < 500:
            return "EXCHANGE_ERROR"
    return "TRANSIENT"


def fetch_with_retry(url: str, *, opener: Optional[Callable[[str], bytes]] = None, attempts: int = 4,
                     base_delay: float = 2.0, sleep: Callable[[float], None] = time.sleep) -> bytes:
    """Bounded retries (``attempts`` total, delays base, 2*base, 4*base ...).
    Only TRANSIENT failures are retried; never an infinite loop."""
    opener = opener or _default_opener
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last = None
    for k in range(attempts):
        try:
            return opener(url)
        except Exception as e:  # noqa: BLE001 — classified below
            last = e
            kind = classify_error(e)
            if kind != "TRANSIENT" or k == attempts - 1:
                raise FetchFailure(kind, f"{type(e).__name__}: {e}", k + 1) from e
            sleep(base_delay * (2 ** k))
    raise FetchFailure("TRANSIENT", str(last), attempts)  # pragma: no cover


def _default_opener(url: str) -> bytes:  # pragma: no cover - network
    import urllib.request
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def code_version() -> str:
    h = hashlib.sha256()
    for name in ("data_fetch.py", "data_quality.py"):
        h.update((Path(__file__).resolve().parent / name).read_bytes())
    return h.hexdigest()[:16]


def months(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-")[:2])
    y1, m1 = map(int, end.split("-")[:2])
    out = []
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _quality_ok(kind: str, sym: str, path: Path) -> tuple[bool, str, dict]:
    if kind == "klines":
        from data_quality import dataset_for
        h = dataset_for(sym, path)
        if h is None or not h.quality.ok:
            return False, f"kline quality {getattr(h, 'missing', None) or 'BLOCK'}", {}
        q = h.quality
        return True, h.sha256, {"verdict": q.verdict, "rows": q.n_rows, "first_ts": q.first_ts,
                                "last_ts": q.last_ts, "missing_bars": q.missing_candles,
                                "known_gap_bars": q.known_gap_bars, "zero_volume": q.zero_volume,
                                "warnings": sorted({i["code"] for i in q.issues if i["severity"] == "WARN"})}
    from data_quality import funding_quality, load_funding_csv_checked
    pairs, rep = load_funding_csv_checked(path)
    fq = funding_quality(pairs, parse_errors=rep["parse_errors"], torn_tail=rep["torn_tail"],
                         intervals=rep["intervals"], source_order_errors=rep["source_order_errors"],
                         interval_missing_rows=rep["interval_missing_rows"])
    summ = {"verdict": "OK" if fq["ok"] else "BLOCK", "rows": fq.get("n"), "first_ts": fq.get("first"),
            "last_ts": fq.get("last"), "gaps": len(fq.get("gaps", [])), "warnings": fq.get("warnings", [])}
    return (True, rep["sha256"], summ) if fq["ok"] else (False, f"funding quality {fq['issues']}", summ)


def fetch_month(data_dir: Path, kind: str, sym: str, ym: str, *,
                opener: Callable[[str], bytes], attempts: int = 4,
                sleep: Callable[[float], None] = time.sleep, base: str = BASE) -> dict:
    rel, sub, name = KINDS[kind]
    url = f"{base}/{rel.format(sym=sym, ym=ym)}"
    target = Path(data_dir) / sub / name.format(sym=sym, ym=ym)
    out = {"kind": kind, "symbol": sym, "month": ym, "url": url, "path": str(target)}
    try:
        blob = fetch_with_retry(url, opener=opener, attempts=attempts, sleep=sleep)
        chk = fetch_with_retry(url + ".CHECKSUM", opener=opener, attempts=attempts,
                               sleep=sleep).decode("ascii", "replace").split()
    except FetchFailure as e:  # any transport failure is BLOCKED_DATA, with its class
        return {**out, "status": "BLOCKED_DATA", "failure_kind": e.kind, "attempts": e.attempts,
                "reason": f"download failed: {e.detail}"}
    sha = hashlib.sha256(blob).hexdigest()
    if not chk or chk[0].lower() != sha:
        return {**out, "status": "BLOCKED_DATA", "reason": "checksum mismatch", "sha256": sha}
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        members = [i for i in zf.infolist() if not i.is_dir()]
        if len(members) != 1 or not members[0].filename.endswith(".csv") \
                or "/" in members[0].filename or "\\" in members[0].filename \
                or members[0].filename.startswith(".."):
            raise FetchError(f"unexpected archive content {[i.filename for i in members]}")
        csv = zf.read(members[0])
    except (zipfile.BadZipFile, FetchError) as e:
        return {**out, "status": "BLOCKED_DATA", "reason": f"archive rejected: {e}"}
    if target.exists():
        same = hashlib.sha256(target.read_bytes()).hexdigest() == hashlib.sha256(csv).hexdigest()
        return {**out, "status": "PRESENT" if same else "CONFLICT",
                "reason": None if same else "existing file differs — kept, not overwritten"}
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(csv)
            f.flush()
            os.fsync(f.fileno())
        ok, info, quality = _quality_ok(kind, sym, Path(tmp))
        if not ok:
            return {**out, "status": "BLOCKED_DATA", "failure_kind": "QUALITY", "reason": info,
                    "quality": quality}
        if target.exists():                       # raced with another writer: keep theirs
            return {**out, "status": "CONFLICT", "reason": "appeared during fetch — kept"}
        os.replace(tmp, target)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    rec = {**out, "status": "FETCHED", "zip_sha256": sha,
           "csv_sha256": hashlib.sha256(csv).hexdigest(),
           "schema": PROVENANCE_SCHEMA, "source": "data.binance.vision (public USD-M monthly archive)",
           "code_version": code_version(), "quality": quality,
           "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    with open(Path(data_dir) / "_provenance.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def fetch_missing(data_dir: Path, symbols: Sequence[str], start: str, end: str, *,
                  allow: bool = False, kinds: Sequence[str] = ("klines", "funding"),
                  opener: Optional[Callable[[str], bytes]] = None,
                  today: Optional[dt.date] = None, attempts: int = 4,
                  sleep: Callable[[float], None] = time.sleep, base: str = BASE) -> dict:
    """Fetch every missing COMPLETE month in [start, end] (the running month is
    never fetched: monthly archives exist only for finished months)."""
    # ADR 0078 (H-09): a symbol / month becomes a path and a URL — only plain
    # exchange symbols and YYYY-MM are accepted (no "../", no separators)
    bad = [x for x in symbols if not re.fullmatch(r"[A-Z0-9]{2,30}", str(x))]
    bad += [x for x in (start, end) if not re.fullmatch(r"\d{4}-\d{2}", str(x))]
    if bad:
        raise ValueError(f"invalid symbol / month {bad}")
    if not allow:
        return {"status": "DISABLED", "note": "network fetch not allowed (opt-in)", "results": []}
    today = today or dt.datetime.now(dt.timezone.utc).date()
    last_complete = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    end = min(end, last_complete)
    results = []
    for sym in symbols:
        for kind in kinds:
            rel, sub, name = KINDS[kind]
            for ym in months(start, end):
                if (Path(data_dir) / sub / name.format(sym=sym, ym=ym)).exists():
                    continue
                results.append(fetch_month(data_dir, kind, sym, ym, opener=opener or _default_opener,
                                           attempts=attempts, sleep=sleep, base=base))
    blocked = [r for r in results if r["status"] in ("BLOCKED_DATA", "CONFLICT")]
    kinds_count: dict = {}
    for r in blocked:
        k = r.get("failure_kind") or r["status"]
        kinds_count[k] = kinds_count.get(k, 0) + 1
    return {"status": "BLOCKED_DATA" if blocked else "OK", "results": results,
            "fetched": sum(r["status"] == "FETCHED" for r in results), "blocked": len(blocked),
            "blocked_by_kind": kinds_count}


def main(argv: Sequence[str]) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default=os.environ.get("NEW2_DATA_DIR"))
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--start", default="2021-01")
    ap.add_argument("--end", default="2099-12")
    ap.add_argument("--allow-network", action="store_true")
    ap.add_argument("--base-url", default=os.environ.get("NEW2_DATA_BASE_URL", BASE),
                    help=f"archive root (default the CDN; the S3 mirror is {S3_MIRROR})")
    a = ap.parse_args(argv)
    if not a.data_dir:
        print("CONFIG_ERROR: --data-dir or NEW2_DATA_DIR required", file=sys.stderr)
        return 1
    r = fetch_missing(Path(a.data_dir), a.symbols, a.start, a.end, allow=a.allow_network, base=a.base_url)
    print(json.dumps({k: v for k, v in r.items() if k != "results"}))
    for x in r["results"]:
        if x["status"] != "FETCHED":
            print(json.dumps(x), file=sys.stderr)
    return {"OK": 0, "DISABLED": 0}.get(r["status"], 4)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
