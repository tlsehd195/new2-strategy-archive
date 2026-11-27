#!/usr/bin/env python3
"""Round 11 of the repeated new-source search, DEV only (ADR 0106).

Average trade size (quote volume per trade) change and level net of
liquidity, close location inside the daily range, Amihud illiquidity change
(U50 quintiles).  Runs exactly the 4 trials of
``run/audit/evidence_newsrc_r11_20261120/prereg_newsrc_r11_v1.json``.

Usage::

    python3 run/newsrc_r11_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import research_data_integrity as rdi  # noqa: E402
import newsrc_engine as ne  # noqa: E402
import wide_universe_dev as wu  # noqa: E402

VERSION = "newsrc-r11-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r11_20261120"
PREREG = EVID / "prereg_newsrc_r11_v1.json"
CONTEXT_ID = "U50-14d-newsrc-r11-v1"
KEYS = [("TS1_TRADESIZE_CHANGE", "U50"), ("TS2_TRADESIZE_RESID", "U50"), ("CL1_CLOSE_LOCATION", "U50"),
        ("IL1_AMIHUD_CHANGE", "U50")]
LONG_TOP = {"TS1_TRADESIZE_CHANGE": True, "TS2_TRADESIZE_RESID": True, "CL1_CLOSE_LOCATION": True,
            "IL1_AMIHUD_CHANGE": False}
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}
_EXTRA: dict = {}


def load_extra(data: Path, seg: str, days: np.ndarray, syms: list) -> dict:
    """high, low, trade count from the daily klines, inside the allowed segment only."""
    key = (str(data), seg)
    if key in _EXTRA:
        return _EXTRA[key]
    lo, hi, _ = bu.SEGMENTS[seg]
    t0, t1 = bu.ms(lo), bu.ms(hi)
    bu.assert_allowed(t0, t1)
    recs = []
    for p in sorted((data / "daily").glob("*.csv")):
        sym = p.name.split("-1d-")[0]
        if seg == "S2" and sym == "BTCUSDT":
            continue
        for f in bu.read_rows(p):
            t = int(f[0])
            if t0 <= t <= t1:
                recs.append((sym, t, float(f[2]), float(f[3]), float(f[8])))
    df = pd.DataFrame(recs, columns=["sym", "t", "high", "low", "count"])
    # ADR 0116: same panel rule as the open/close loader
    df, _ = rdi.clean_panel(df, ("high", "low", "count"), positive_cols=("high", "low"), nonneg_cols=("count",),
                            log=rdi.panel_log("newsrc_r11_dev.load_extra", data, seg))
    out = {k: df.pivot(index="t", columns="sym", values=k).reindex(days).reindex(columns=syms)
           for k in ("high", "low", "count")}
    _EXTRA[key] = out
    return out


def ratio_of_means(x: pd.DataFrame, a: int, b: int, c: int, d: int, n1: int, n2: int) -> pd.Series:
    """ln(mean x[a..b] / mean x[c..d]) per column, NaN below the valid-day minimums."""
    w1, w2 = x.iloc[a: b + 1], x.iloc[c: d + 1]
    r = np.log(w1.mean() / w2.mean())
    return r.where((w1.notna().sum() >= n1) & (w2.notna().sum() >= n2)).replace([np.inf, -np.inf], np.nan)


def resid(y: pd.Series, x: pd.Series) -> pd.Series:
    ok = y.notna() & x.notna()
    if ok.sum() < 5:
        return y * np.nan
    x1 = np.column_stack([np.ones(int(ok.sum())), x[ok].values])
    coef, *_ = np.linalg.lstsq(x1, y[ok].values, rcond=None)
    out = pd.Series(np.nan, index=y.index)
    out[ok] = y[ok].values - x1 @ coef
    return out


def compute(close, qv, high, low, cnt, r, cut) -> dict:
    ats = (qv / cnt).where(cnt > 0)
    ts1 = ratio_of_means(ats, cut - 13, cut, cut - 89, cut - 14, 10, 50)
    w = slice(cut - 27, cut + 1)
    a28, q28 = ats.iloc[w], qv.iloc[w]
    ok28 = (a28.notna().sum() >= 20)
    ts2 = resid(np.log(a28.mean()).where(ok28), np.log(q28.mean()).where(ok28))
    rng = (high - low)
    clv = ((2 * close - high - low) / rng).where(rng > 0)
    cw = clv.iloc[cut - 13: cut + 1]
    cl1 = cw.mean().where(cw.notna().sum() >= 10)
    ami = (r.abs() / qv).where(qv > 0)
    il1 = ratio_of_means(ami, cut - 27, cut, cut - 89, cut - 28, 20, 40)
    return {"TS1_TRADESIZE_CHANGE": ts1, "TS2_TRADESIZE_RESID": ts2, "CL1_CLOSE_LOCATION": cl1,
            "IL1_AMIHUD_CHANGE": il1}


def signals(ctx: dict) -> dict:
    cut, ranked = ctx["cut"], ctx["ranked"]
    u50 = wu.slice_universe(ranked, "U50")
    if not u50 or cut - 89 < 0:
        return {}
    ex = load_extra(ctx["data"], ctx["seg"], ctx["days"], ctx["syms"])
    piv = ctx["piv"]
    sig = compute(piv["close"][u50], piv["qv"][u50], ex["high"][u50], ex["low"][u50], ex["count"][u50],
                  ctx["cret"][u50], cut)
    return {(h, "U50"): sig[h].replace([np.inf, -np.inf], np.nan) for h, _ in KEYS}


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    return wu.pick(sig, universe, LONG_TOP[h])


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r11_dev.json")
    a = ap.parse_args(argv)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__))}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
