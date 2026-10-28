#!/usr/bin/env python3
"""Download the public data for the broad-universe DEV check (ADR 0035).

Binance USD-M monthly archives from the S3 mirror of data.binance.vision
(public files, no account, no key).  Every zip is checked against its
published CHECKSUM before use.  Only the months that touch the allowed DEV
segments are fetched; rows inside never-read ranges are dropped by the
analysis loader, not here (a monthly file can straddle a boundary).

Steps (each skips files that already exist):

  daily    1d klines of every USDT symbol in the archive listing
  funding  funding files of the symbols that ever enter the top-50 universe
  bar2345  the 23:45 UTC 15m bar of each day (quote volume, for the impact
           term) of the same symbols; the 15m zips are streamed and dropped
  m15      every 15m bar (open time, o, h, l, c, quote volume) of the same
           symbols as float64 .npy per symbol-month (ADR 0044, intraday lines)

Usage::

    python3 run/broad_universe_fetch.py --out <dir> --step daily
    python3 run/broad_universe_fetch.py --out <dir> --step funding --symbols <file>
    python3 run/broad_universe_fetch.py --out <dir> --step bar2345 --symbols <file>
    python3 run/broad_universe_fetch.py --out <dir> --step m15 --symbols <file>
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
BASE = S3 + "/data/futures/um/monthly"
LIST = S3 + "?delimiter=/&prefix=data/futures/um/monthly/klines/"
EXCLUDE_BASES = ("USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "USDE", "EUR", "GBP", "AEUR")


def months() -> list:
    out = []
    for y, m0, m1 in ((2021, 1, 12), (2022, 1, 12), (2023, 1, 9), (2024, 6, 12), (2025, 1, 10)):
        out += [f"{y}-{m:02d}" for m in range(m0, m1 + 1)]
    return out


def get(url: str, tries: int = 4) -> bytes | None:
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code < 500 and e.code != 429:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(2 ** (i + 1))
    raise RuntimeError(f"failed after {tries} tries: {url}")


def fetch_checked(path: str) -> bytes | None:
    path = urllib.parse.quote(path)
    body = get(f"{BASE}/{path}")
    if body is None:
        return None
    chk = get(f"{BASE}/{path}.CHECKSUM")
    if chk is None:
        raise RuntimeError(f"no CHECKSUM for {path}")
    want = chk.decode().split()[0].strip().lower()
    got = hashlib.sha256(body).hexdigest()
    if want != got:
        raise RuntimeError(f"CHECKSUM mismatch {path}")
    return body


def unzip_one(body: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = z.namelist()
        if len(names) != 1 or "/" in names[0] or ".." in names[0]:
            raise RuntimeError(f"unexpected zip members {names}")
        return z.read(names[0]).decode()


def list_symbols() -> list:
    syms, marker = [], ""
    while True:
        x = get(LIST + (f"&marker={marker}" if marker else "")).decode()
        syms += re.findall(r"<Prefix>data/futures/um/monthly/klines/([^/]+)/</Prefix>", x)
        nm = re.search(r"<NextMarker>(.*?)</NextMarker>", x)
        if not nm:
            break
        marker = nm.group(1)
    keep = []
    for s in syms:
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        if base in EXCLUDE_BASES or "DOM" in base or "DEFI" in base:
            continue
        keep.append(s)
    return sorted(set(keep))


def job_daily(out: Path, sym: str, ym: str) -> str:
    dst = out / "daily" / f"{sym}-1d-{ym}.csv"
    if dst.exists():
        return "skip"
    body = fetch_checked(f"klines/{sym}/1d/{sym}-1d-{ym}.zip")
    if body is None:
        return "404"
    dst.write_text(unzip_one(body))
    return "ok"


def job_funding(out: Path, sym: str, ym: str) -> str:
    dst = out / "funding" / f"{sym}-fundingRate-{ym}.csv"
    if dst.exists():
        return "skip"
    body = fetch_checked(f"fundingRate/{sym}/{sym}-fundingRate-{ym}.zip")
    if body is None:
        return "404"
    dst.write_text(unzip_one(body))
    return "ok"


def job_bar2345(out: Path, sym: str, ym: str) -> str:
    dst = out / "bar2345" / f"{sym}-2345-{ym}.csv"
    if dst.exists():
        return "skip"
    body = fetch_checked(f"klines/{sym}/15m/{sym}-15m-{ym}.zip")
    if body is None:
        return "404"
    rows = []
    for r in csv.reader(io.StringIO(unzip_one(body))):
        if not r or not r[0].isdigit():
            continue
        if (int(r[0]) // 60_000) % 1440 == 23 * 60 + 45:
            rows.append(f"{r[0]},{r[7]}")
    dst.write_text("open_time,quote_volume\n" + "\n".join(rows) + "\n")
    return "ok"


def job_m15(out: Path, sym: str, ym: str) -> str:
    dst = out / "m15" / f"{sym}-15m-{ym}.npy"
    if dst.exists():
        return "skip"
    body = fetch_checked(f"klines/{sym}/15m/{sym}-15m-{ym}.zip")
    if body is None:
        return "404"
    rows = [[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])]
            for r in csv.reader(io.StringIO(unzip_one(body))) if r and r[0].isdigit()]
    np.save(dst, np.array(rows, dtype="float64").reshape(-1, 6))
    return "ok"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--step", required=True, choices=("daily", "funding", "bar2345", "m15"))
    ap.add_argument("--symbols", type=Path)
    ap.add_argument("--workers", type=int, default=32)
    a = ap.parse_args(argv)
    (a.out / a.step).mkdir(parents=True, exist_ok=True)
    if a.step == "daily":
        syms = list_symbols()
        (a.out / "symbols_listed.json").write_text(json.dumps(syms))
        fn = job_daily
    else:
        syms = json.loads(a.symbols.read_text())
        fn = {"funding": job_funding, "bar2345": job_bar2345, "m15": job_m15}[a.step]
    jobs = [(s, ym) for s in syms for ym in months()]
    counts: dict = {}
    errors = []
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(fn, a.out, s, ym): (s, ym) for s, ym in jobs}
        for f in cf.as_completed(futs):
            try:
                k = f.result()
            except Exception as e:  # recorded, never silently skipped
                k = "error"
                errors.append(f"{futs[f]}: {e}")
            counts[k] = counts.get(k, 0) + 1
    print(a.step, len(syms), "symbols", counts)
    for e in errors[:20]:
        print("ERROR", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
