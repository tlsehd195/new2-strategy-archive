#!/usr/bin/env python3
"""Round 15 of the new-source search, DEV only (ADR 0113): strategies from external sources.

HR1 Hash Ribbon (BTC hash rate SMA30 > SMA60) as timing of the equal-weight U50 long,
SE1 cross-sectional same-weekday seasonality (Long et al. 2020), EX1 futures-expiry
window (Thursday before, weekend and Monday after the last Friday of the month).
Runs exactly the 3 trials of ``run/audit/evidence_newsrc_r15_20261120/prereg_newsrc_r15_v1.json``.

Usage::

    python3 run/newsrc_r15_dev.py --data-dir <dir> [--fetch]
    # --fetch downloads csv/btc.csv of github.com/coinmetrics/data into <dir>/cm_btc
"""

from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import json
import math
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import newsrc_engine as ne  # noqa: E402
import wide_universe_dev as wu  # noqa: E402

VERSION = "newsrc-r15-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r15_20261120"
PREREG = EVID / "prereg_newsrc_r15_v1.json"
CONTEXT_ID = "U50-1d-newsrc-r15-v1"
KEYS = [("HR1_HASHRIBBON", "U50"), ("SE1_SAMEWEEKDAY", "U50"), ("EX1_EXPIRY_WINDOW", "U50")]
TIMING = {"HR1_HASHRIBBON", "EX1_EXPIRY_WINDOW"}
RAW = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv"
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}
_CACHE: dict = {}
_STATE: dict = {}


def fetch(data: Path) -> None:
    out = data / "cm_btc"
    out.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(RAW, timeout=300) as r:
        (out / "btc.csv").write_bytes(r.read())


def hashrate(data: Path, seg: str, days: np.ndarray) -> pd.Series:
    """Coin Metrics BTC HashRate by day, segment read-range rows only."""
    lo, hi, _ = bu.SEGMENTS[seg]
    t0, t1 = bu.ms(lo), bu.ms(hi)
    bu.assert_allowed(t0, t1)
    vals = {}
    with open(data / "cm_btc" / "btc.csv", newline="") as f:
        for row in csv.DictReader(f):
            t = bu.ms(row["time"][:10])
            if t0 <= t <= t1 and row.get("HashRate"):
                vals[t] = float(row["HashRate"])
    return pd.Series(vals, dtype=float).reindex(days)


def ribbon_on(h: pd.Series, cut: int) -> float:
    if cut - 59 < 0:
        return float("nan")
    a, b = h.iloc[cut - 29: cut + 1], h.iloc[cut - 59: cut + 1]
    if a.notna().sum() < 25 or b.notna().sum() < 50:
        return float("nan")
    return 1.0 if a.mean() > b.mean() else 0.0


def weekday(t_ms: int) -> int:
    return dt.datetime.fromtimestamp(int(t_ms) / 1000, dt.timezone.utc).weekday()


def last_friday(y: int, m: int) -> dt.date:
    d = dt.date(y, m, calendar.monthrange(y, m)[1])
    return d - dt.timedelta(days=(d.weekday() - 4) % 7)


def expiry_on(t_ms: int) -> float:
    """1.0 on Thursday F-1, Saturday F+1, Sunday F+2, Monday F+3 of a month's last Friday F."""
    d = dt.datetime.fromtimestamp(int(t_ms) / 1000, dt.timezone.utc).date()
    for y, m in ((d.year, d.month), ((d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1))):
        f = last_friday(y, m)
        if (d - f).days in (-1, 1, 2, 3):
            return 1.0
    return 0.0


def same_weekday_signal(ret: pd.DataFrame, days: np.ndarray, i: int, cut: int) -> pd.Series:
    """Mean of open-to-open returns r_d (d <= cut, d >= i-364, same weekday as day i), >= 12 values."""
    w = weekday(days[i])
    idx = [d for d in range(max(0, i - 364), cut + 1) if weekday(days[d]) == w]
    sub = ret.iloc[idx]
    return sub.mean().where(sub.notna().sum() >= 12)


def extras(ctx: dict) -> tuple:
    key = (str(ctx["data"]), ctx["seg"])
    if key not in _CACHE:
        opn = ctx["piv"]["open"]
        _CACHE[key] = (hashrate(ctx["data"], ctx["seg"], ctx["days"]), opn.shift(-1) / opn - 1.0)
    return _CACHE[key]


def signals(ctx: dict) -> dict:
    cut, ranked, i, days = ctx["cut"], ctx["ranked"], ctx["i"], ctx["days"]
    u50 = wu.slice_universe(ranked, "U50")
    if not u50:
        return {}
    h, ret = extras(ctx)
    return {("HR1_HASHRIBBON", "U50"): pd.Series(ribbon_on(h, cut), index=u50),
            ("SE1_SAMEWEEKDAY", "U50"): same_weekday_signal(ret[u50], days, i, cut),
            ("EX1_EXPIRY_WINDOW", "U50"): pd.Series(expiry_on(days[i]), index=u50)}


def drifted(prev: dict, opn: pd.DataFrame, j: int, i: int) -> dict:
    """Holdings the engine carries from day j to day i (open-to-open drift, NaN return -> 0)."""
    out = {}
    for s, w in prev.items():
        for d in range(j, i):
            r = opn[s].iloc[d + 1] / opn[s].iloc[d] - 1.0
            w = w * (1.0 + (0.0 if math.isnan(r) else r))
        out[s] = w
    return out


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    if h not in TIMING:
        return wu.pick(sig, universe, True)
    key = (str(ctx["data"]), ctx["seg"], h)
    on = float(sig.iloc[0]) if len(sig) else float("nan")
    i = ctx["i"]
    prev = _STATE.get(key)
    if on != 1.0:
        w = {}
    elif prev and prev[0] and set(prev[0]) == set(universe):
        w = drifted(prev[0], ctx["piv"]["open"], prev[1], i)
    else:
        w = {s: 1.0 / len(universe) for s in universe}
    _STATE[key] = (w, i)
    return w


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r15_dev.json")
    a = ap.parse_args(argv)
    if a.fetch:
        fetch(a.data_dir)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
            "source": "Binance USDT-M daily/funding (S3 mirror); Coin Metrics community data csv/btc.csv HashRate"}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
