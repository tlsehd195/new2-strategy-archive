#!/usr/bin/env python3
"""Build 1-minute order-flow bars from Binance USD-M aggTrades, DEV ranges only (ADR 0114).

Data tool, no signals and no P&L.  Downloads the public aggTrades archive from the
data.binance.vision S3 mirror, one symbol-month at a time, filters every row to the
DEV segments the moment the file is parsed, aggregates to 1-minute bars and deletes
the raw file.  Boundary months that touch a never-read range (2023-09, 2024-06,
2025-10) are fetched as DAILY files, only for the allowed days, so no file that
covers a never-read day is downloaded at all.

Per minute (UTC, open time):  open high low close vwap qv buy_qv big_buy_qv
big_sell_qv n_trades sell_at_low_qv buy_at_high_qv.

* ``qv``: quote volume (price x qty).  ``buy_qv``: aggressive buy quote volume
  (is_buyer_maker == false).  Sell volume = qv - buy_qv.
* "Big trade": one aggTrade (one taker order at one price) with notional >= the
  99.5th percentile of that symbol's aggTrade notionals in the PREVIOUS processed
  month (first month of each segment: its own value, warm-up only, trading starts
  2 months later in S1 and 2 months later in S2).
* ``sell_at_low_qv`` / ``buy_at_high_qv``: aggressive sell (buy) quote volume printed
  within 2 bp of the minute's low (high), the footprint "volume at the extreme"
  used for absorption.

Output: <out>/<SYM>/<YYYY-MM>.npz plus <out>/<SYM>/thresholds.json.

    python3 run/aggtrades_minute_build.py --out <dir> [--symbols A,B] [--workers 4]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import subprocess
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402

VERSION = "aggtrades-minute-build-v1"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/data/futures/um"
SYMBOLS = ["ADAUSDT", "AVAXUSDT", "BCHUSDT", "BNBUSDT", "DOGEUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT",
           "SOLUSDT", "TRXUSDT", "UNIUSDT", "XLMUSDT", "XMRUSDT", "XRPUSDT", "ZECUSDT"]
BIG_Q = 0.995
EXTREME_BP = 0.0002
FIELDS = ("t", "open", "high", "low", "close", "vwap", "qv", "buy_qv", "big_buy_qv", "big_sell_qv",
          "n_trades", "sell_at_low_qv", "buy_at_high_qv")


def months(lo: str, hi: str) -> list:
    y, m = int(lo[:4]), int(lo[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= hi:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def plan() -> list:
    """[(segment, label, [urls-template parts])]: monthly files, daily files for boundary months."""
    jobs = []
    for seg, (lo, hi, _first) in bu.SEGMENTS.items():
        t0, t1 = bu.ms(lo), bu.ms(hi) + bu.DAY_MS - 1
        bu.assert_allowed(t0, t1)
        for ym in months(lo[:7], hi[:7]):
            y, m = int(ym[:4]), int(ym[5:7])
            first = dt.date(y, m, 1)
            last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
            d_lo, d_hi = max(first, dt.date.fromisoformat(lo)), min(last, dt.date.fromisoformat(hi))
            if d_lo == first and d_hi == last:
                jobs.append((seg, ym, [("monthly", ym)]))
            else:
                days = [(d_lo + dt.timedelta(days=i)).isoformat() for i in range((d_hi - d_lo).days + 1)]
                jobs.append((seg, ym, [("daily", d) for d in days]))
    return jobs


def fetch(sym: str, kind: str, stamp: str, tmp: Path, tries: int = 6):
    """Download one zip to ``tmp`` with curl (stalled transfers are aborted and retried); None on 404."""
    url = f"{S3}/{kind}/aggTrades/{sym}/{sym}-aggTrades-{stamp}.zip"
    dst = tmp / f"{sym}-{stamp}.zip"
    for i in range(tries):
        r = subprocess.run(["curl", "-s", "-o", str(dst), "-w", "%{http_code}", "--speed-limit", "200000",
                            "--speed-time", "20", "--max-time", "1800", url], capture_output=True, text=True)
        code = r.stdout.strip()
        if code == "404":
            dst.unlink(missing_ok=True)
            return None
        if r.returncode == 0 and code == "200":
            return dst
        time.sleep(2 ** i)
    raise RuntimeError(f"download failed {url}")


def parse(path: Path) -> dict:
    """Columns price, qty, t, ibm as numpy arrays (streamed from the zip, only 4 columns kept)."""
    import pyarrow.csv as pc
    with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as fh:
        head = fh.peek(20)[:20] if hasattr(fh, "peek") else b""
    with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as fh:
        has_header = head.startswith(b"agg_trade_id")
        names = ["a", "price", "quantity", "f", "l", "transact_time", "is_buyer_maker"]
        ro = pc.ReadOptions(column_names=None if has_header else names, block_size=1 << 26)
        co = pc.ConvertOptions(include_columns=["price", "quantity", "transact_time", "is_buyer_maker"])
        tb = pc.read_csv(fh, read_options=ro, convert_options=co)
    ibm = tb.column("is_buyer_maker")
    ibm = ibm.to_numpy() if str(ibm.type) == "bool" else np.char.lower(ibm.to_numpy().astype(str)) == "true"
    return {"price": tb.column("price").to_numpy().astype(float), "qty": tb.column("quantity").to_numpy().astype(float),
            "t": tb.column("transact_time").to_numpy().astype(np.int64), "ibm": np.asarray(ibm, bool)}


def aggregate(d: dict, big: float) -> dict:
    """1-minute bars from time-sorted trade arrays, built one UTC day at a time (bounded memory)."""
    o = np.argsort(d["t"], kind="stable")
    t_all = d["t"][o]
    day = t_all // 86_400_000
    cuts = np.flatnonzero(np.r_[True, day[1:] != day[:-1], True])
    parts = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        idx = o[a:b]
        parts.append(_agg_day(d["price"][idx], d["qty"][idx], t_all[a:b], d["ibm"][idx], big))
    return {k: np.concatenate([p[k] for p in parts]) for k in FIELDS}


def _agg_day(p, qty, t, ibm, big) -> dict:
    qv = p * qty
    buy = ~ibm
    mi = t // 60_000
    new = np.r_[True, mi[1:] != mi[:-1]]
    starts = np.flatnonzero(new)
    seg_id = np.cumsum(new) - 1
    hi = np.maximum.reduceat(p, starts)
    lo = np.minimum.reduceat(p, starts)
    qsum = np.add.reduceat(qv, starts)
    at_low = p <= lo[seg_id] * (1 + EXTREME_BP)
    at_high = p >= hi[seg_id] * (1 - EXTREME_BP)
    isbig = qv >= big
    return {
        "t": mi[starts] * 60_000, "open": p[starts], "high": hi, "low": lo,
        "close": p[np.r_[starts[1:] - 1, len(p) - 1]],
        "vwap": np.add.reduceat(qv * p, starts) / np.where(qsum > 0, qsum, np.nan),
        "qv": qsum, "buy_qv": np.add.reduceat(np.where(buy, qv, 0.0), starts),
        "big_buy_qv": np.add.reduceat(np.where(buy & isbig, qv, 0.0), starts),
        "big_sell_qv": np.add.reduceat(np.where(~buy & isbig, qv, 0.0), starts),
        "n_trades": np.diff(np.r_[starts, len(p)]).astype(float),
        "sell_at_low_qv": np.add.reduceat(np.where(~buy & at_low, qv, 0.0), starts),
        "buy_at_high_qv": np.add.reduceat(np.where(buy & at_high, qv, 0.0), starts),
    }


def run_symbol(sym: str, out: str) -> dict:
    od = Path(out) / sym
    od.mkdir(parents=True, exist_ok=True)
    thr_path = od / "thresholds.json"
    thr = json.loads(thr_path.read_text()) if thr_path.exists() else {}
    log = {"symbol": sym, "months": {}}
    prev_q = {}
    for seg, ym, parts in plan():
        lo, hi, _ = bu.SEGMENTS[seg]
        t0, t1 = bu.ms(lo), bu.ms(hi) + bu.DAY_MS - 1
        target = od / f"{ym}.npz"
        if target.exists() and ym in thr:
            prev_q[seg] = thr[ym]["q995_next"]
            log["months"][ym] = "cached"
            continue
        tmp = Path(out) / "_raw"
        tmp.mkdir(exist_ok=True)
        frames = []
        for kind, stamp in parts:
            path = fetch(sym, kind, stamp, tmp)
            if path is None:
                continue
            d = parse(path)
            path.unlink()
            keep = (d["t"] >= t0) & (d["t"] <= t1)  # filter to DEV the moment it is parsed
            frames.append({k: v[keep] for k, v in d.items()})
            del d
        n_rows = sum(len(f["t"]) for f in frames)
        if n_rows == 0:
            log["months"][ym] = "missing"
            continue
        df = {k: np.concatenate([f[k] for f in frames]) for k in ("price", "qty", "t", "ibm")}
        del frames
        notional = df["price"] * df["qty"]
        own_q = float(np.quantile(notional, BIG_Q))
        del notional
        big = prev_q.get(seg, own_q)
        bars = aggregate(df, big)
        np.savez_compressed(target, **{k: np.asarray(v) for k, v in bars.items()})
        thr[ym] = {"segment": seg, "big_threshold_used": big, "q995_next": own_q,
                   "warmup_own_month": seg not in prev_q, "rows": int(n_rows), "minutes": int(len(bars["t"]))}
        prev_q[seg] = own_q
        thr_path.write_text(json.dumps(thr, indent=1))
        log["months"][ym] = n_rows
        del df
    return log


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    syms = [s for s in a.symbols.split(",") if s]
    a.out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(a.workers) as ex:
        for log in ex.map(run_symbol, syms, [str(a.out)] * len(syms)):
            print(log["symbol"], sum(1 for v in log["months"].values() if v not in ("missing",)), "months", flush=True)
    (a.out / "build_manifest.json").write_text(json.dumps({"version": VERSION, "symbols": syms,
                                                            "big_quantile": BIG_Q, "extreme_bp": EXTREME_BP}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
