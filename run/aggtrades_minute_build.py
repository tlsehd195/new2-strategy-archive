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
import io
import json
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

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


def fetch(sym: str, kind: str, stamp: str, tries: int = 5):
    url = f"{S3}/{kind}/aggTrades/{sym}/{sym}-aggTrades-{stamp}.zip"
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=600) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 ** i)
        except Exception:  # network hiccup
            time.sleep(2 ** i)
    raise RuntimeError(f"download failed {url}")


def parse(blob: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        raw = z.read(z.namelist()[0])
    head = raw[:20]
    has_header = head.startswith(b"agg_trade_id")
    kw = dict(header=0 if has_header else None, usecols=[1, 2, 5, 6],
              names=None if has_header else ["a", "price", "quantity", "f", "l", "transact_time", "is_buyer_maker"])
    try:
        df = pd.read_csv(io.BytesIO(raw), engine="pyarrow", **kw)
    except Exception:
        df = pd.read_csv(io.BytesIO(raw), **kw)
    df.columns = ["price", "qty", "t", "ibm"]
    if df["ibm"].dtype != bool:
        df["ibm"] = df["ibm"].astype(str).str.lower().eq("true")
    return df


def aggregate(df: pd.DataFrame, big: float) -> dict:
    df = df.sort_values("t", kind="stable")
    p = df["price"].to_numpy(float)
    qv = p * df["qty"].to_numpy(float)
    buy = ~df["ibm"].to_numpy(bool)
    mi = (df["t"].to_numpy(np.int64) // 60_000)
    starts = np.flatnonzero(np.r_[True, mi[1:] != mi[:-1]])
    seg_id = np.cumsum(np.r_[True, mi[1:] != mi[:-1]]) - 1
    hi = np.maximum.reduceat(p, starts)
    lo = np.minimum.reduceat(p, starts)
    qsum = np.add.reduceat(qv, starts)
    lo_r, hi_r = lo[seg_id], hi[seg_id]
    at_low = p <= lo_r * (1 + EXTREME_BP)
    at_high = p >= hi_r * (1 - EXTREME_BP)
    isbig = qv >= big
    out = {
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
    return out


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
        frames = []
        for kind, stamp in parts:
            blob = fetch(sym, kind, stamp)
            if blob is None:
                continue
            df = parse(blob)
            del blob
            df = df[(df["t"] >= t0) & (df["t"] <= t1)]  # filter to DEV the moment it is parsed
            frames.append(df)
        if not frames or sum(len(f) for f in frames) == 0:
            log["months"][ym] = "missing"
            continue
        df = pd.concat(frames, ignore_index=True)
        notional = (df["price"] * df["qty"]).to_numpy(float)
        own_q = float(np.quantile(notional, BIG_Q))
        big = prev_q.get(seg, own_q)
        bars = aggregate(df, big)
        np.savez_compressed(target, **{k: np.asarray(v) for k, v in bars.items()})
        thr[ym] = {"segment": seg, "big_threshold_used": big, "q995_next": own_q,
                   "warmup_own_month": seg not in prev_q, "rows": int(len(df)), "minutes": int(len(bars["t"]))}
        prev_q[seg] = own_q
        thr_path.write_text(json.dumps(thr, indent=1))
        log["months"][ym] = len(df)
        del df, frames
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
