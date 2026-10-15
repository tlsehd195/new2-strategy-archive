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
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional, Sequence

BASE = "https://data.binance.vision/data/futures/um/monthly"
KINDS = {
    "klines": ("klines/{sym}/15m/{sym}-15m-{ym}.zip", "klines", "{sym}-15m-{ym}.csv"),
    "funding": ("fundingRate/{sym}/{sym}-fundingRate-{ym}.zip", "funding", "{sym}-fundingRate-{ym}.csv"),
}


class FetchError(RuntimeError):
    pass


def _default_opener(url: str) -> bytes:  # pragma: no cover - network
    import urllib.request
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def months(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-")[:2])
    y1, m1 = map(int, end.split("-")[:2])
    out = []
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _quality_ok(kind: str, sym: str, path: Path) -> tuple[bool, str]:
    if kind == "klines":
        from data_quality import dataset_for
        h = dataset_for(sym, path)
        if h is None or not h.quality.ok:
            return False, f"kline quality {getattr(h, 'missing', None) or 'BLOCK'}"
        return True, h.sha256
    from data_quality import funding_quality, load_funding_csv_checked
    pairs, rep = load_funding_csv_checked(path)
    fq = funding_quality(pairs, parse_errors=rep["parse_errors"], torn_tail=rep["torn_tail"],
                         intervals=rep["intervals"])
    return (True, rep["sha256"]) if fq["ok"] else (False, f"funding quality {fq['issues']}")


def fetch_month(data_dir: Path, kind: str, sym: str, ym: str, *,
                opener: Callable[[str], bytes]) -> dict:
    rel, sub, name = KINDS[kind]
    url = f"{BASE}/{rel.format(sym=sym, ym=ym)}"
    target = Path(data_dir) / sub / name.format(sym=sym, ym=ym)
    out = {"kind": kind, "symbol": sym, "month": ym, "url": url, "path": str(target)}
    try:
        blob = opener(url)
        chk = opener(url + ".CHECKSUM").decode("ascii", "replace").split()
    except Exception as e:  # noqa: BLE001 — any transport failure is BLOCKED_DATA
        return {**out, "status": "BLOCKED_DATA", "reason": f"download failed: {type(e).__name__}: {e}"}
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
        ok, info = _quality_ok(kind, sym, Path(tmp))
        if not ok:
            return {**out, "status": "BLOCKED_DATA", "reason": info}
        if target.exists():                       # raced with another writer: keep theirs
            return {**out, "status": "CONFLICT", "reason": "appeared during fetch — kept"}
        os.replace(tmp, target)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    rec = {**out, "status": "FETCHED", "zip_sha256": sha,
           "csv_sha256": hashlib.sha256(csv).hexdigest(),
           "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    with open(Path(data_dir) / "_provenance.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def fetch_missing(data_dir: Path, symbols: Sequence[str], start: str, end: str, *,
                  allow: bool = False, kinds: Sequence[str] = ("klines", "funding"),
                  opener: Optional[Callable[[str], bytes]] = None,
                  today: Optional[dt.date] = None) -> dict:
    """Fetch every missing COMPLETE month in [start, end] (the running month is
    never fetched: monthly archives exist only for finished months)."""
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
                results.append(fetch_month(data_dir, kind, sym, ym, opener=opener or _default_opener))
    blocked = [r for r in results if r["status"] in ("BLOCKED_DATA", "CONFLICT")]
    return {"status": "BLOCKED_DATA" if blocked else "OK", "results": results,
            "fetched": sum(r["status"] == "FETCHED" for r in results), "blocked": len(blocked)}


def main(argv: Sequence[str]) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default=os.environ.get("NEW2_DATA_DIR"))
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--start", default="2021-01")
    ap.add_argument("--end", default="2099-12")
    ap.add_argument("--allow-network", action="store_true")
    a = ap.parse_args(argv)
    if not a.data_dir:
        print("CONFIG_ERROR: --data-dir or NEW2_DATA_DIR required", file=sys.stderr)
        return 1
    r = fetch_missing(Path(a.data_dir), a.symbols, a.start, a.end, allow=a.allow_network)
    print(json.dumps({k: v for k, v in r.items() if k != "results"}))
    for x in r["results"]:
        if x["status"] != "FETCHED":
            print(json.dumps(x), file=sys.stderr)
    return {"OK": 0, "DISABLED": 0}.get(r["status"], 4)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
