#!/usr/bin/env python3
"""Round 3 of the repeated new-source search, DEV only (ADR 0098).

Runs exactly the 6 trials of
``run/audit/evidence_newsrc_r3_20261120/prereg_newsrc_r3_v1.json``:
aggregate-funding timing, alt vs ETH rotation, turnover-conditioned momentum,
realised skewness, quiet reversal, perp-vs-spot close gap.  Engine:
``newsrc_engine``.  Data: the ADR 0096 directory (spot 1d files included).

Usage::

    python3 run/newsrc_r3_dev.py --data-dir <dir>
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
import newsrc_engine as ne  # noqa: E402
import newsrc_r1_dev as r1  # noqa: E402
import wide_universe_dev as wu  # noqa: E402

VERSION = "newsrc-r3-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r3_20261120"
PREREG = EVID / "prereg_newsrc_r3_v1.json"
CONTEXT_ID = "U50-14d-newsrc-r3-v1"
KEYS = [("AF1_AGGFUND_TIMING", "U50"), ("AE1_ALT_VS_ETH", "U50"), ("MT1_MOM_TURNOVER", "U50"),
        ("SK1_REALSKEW", "U50"), ("RV1_QUIET_REVERSAL", "U50"), ("PS1_PERP_SPOT_GAP", "U50")]
DIRECT = {"AF1_AGGFUND_TIMING", "AE1_ALT_VS_ETH", "MT1_MOM_TURNOVER", "RV1_QUIET_REVERSAL"}
LONG_TOP = {"SK1_REALSKEW": False, "PS1_PERP_SPOT_GAP": False}
EVAL_START = {"S1": "2021-07-05", "S2": "2024-12-16"}
ANCHOR = "ETHUSDT"
_spot_close: dict = {}


def vol_shock(qv: pd.DataFrame, cut: int, names: list) -> pd.Series:
    rec = qv.iloc[cut - 6: cut + 1][names].mean()
    old = qv.iloc[cut - 34: cut - 6][names].mean()
    return np.log(rec / old).replace([np.inf, -np.inf], np.nan)


def af1(fund_daily: pd.DataFrame, cut: int, u50: list) -> pd.Series:
    f7 = fund_daily[u50].rolling(7, min_periods=5).sum()
    hist = f7.iloc[cut - 179: cut + 1].median(axis=1, skipna=True)
    if hist.notna().sum() < 120 or np.isnan(hist.iloc[-1]):
        return pd.Series(np.nan, index=u50)
    on = hist.iloc[-1] <= hist.median()
    return pd.Series(1.0 / len(u50) if on else 0.0, index=u50)


def ae1(close: pd.DataFrame, cut: int, u50: list) -> pd.Series | None:
    if ANCHOR not in u50:
        return None
    rest = [s for s in u50 if s != ANCHOR]
    r = close.iloc[cut][rest] / close.iloc[cut - 27][rest] - 1.0
    rest = list(r.dropna().index)
    if not rest:
        return None
    a = float(r[rest].mean())
    e = float(close.iloc[cut][ANCHOR] / close.iloc[cut - 27][ANCHOR] - 1.0)
    sgn = 1.0 if a > e else -1.0
    w = {s: sgn * 0.5 / len(rest) for s in rest}
    w[ANCHOR] = -sgn * 0.5
    return pd.Series(w)


def mt1(close: pd.DataFrame, qv: pd.DataFrame, cut: int, u50: list) -> pd.Series:
    mom = (close.iloc[cut][u50] / close.iloc[cut - 27][u50] - 1.0)
    tch = vol_shock(qv, cut, u50)
    ok = mom.notna() & tch.notna()
    mom, tch = mom[ok], tch[ok]
    if len(mom) < 10:
        return pd.Series(np.nan, index=u50)
    hm, ht = mom >= mom.median(), tch >= tch.median()
    longs = list(mom.index[hm & ~ht])
    shorts = list(mom.index[~hm & ht])
    w = {}
    if longs and shorts:
        w.update({s: 0.5 / len(longs) for s in longs})
        w.update({s: -0.5 / len(shorts) for s in shorts})
    return pd.Series(w, dtype=float) if w else pd.Series(np.nan, index=u50)


def rv1(close: pd.DataFrame, qv: pd.DataFrame, cut: int, u50: list) -> pd.Series:
    shock = vol_shock(qv, cut, u50).dropna()
    quiet = list(shock.index[shock < shock.median()])
    r7 = (close.iloc[cut][quiet] / close.iloc[cut - 7][quiet] - 1.0).dropna()
    k = len(quiet) // 5
    if k < 1 or len(r7) < 2 * k:
        return pd.Series(np.nan, index=u50)
    order = sorted(r7.index, key=lambda n: (-r7[n], n))
    w = {n: 1.0 / (2 * k) for n in order[-k:]}
    w.update({n: -1.0 / (2 * k) for n in order[:k]})
    return pd.Series(w)


def spot_close(ctx: dict) -> pd.DataFrame:
    key = ctx["seg"]
    if key not in _spot_close:
        _spot_close[key] = r1.load_side(ctx["data"], "spot1d", 4, ctx["days"], ctx["syms"])
    return _spot_close[key]


def signals(ctx: dict) -> dict:
    piv, cut, ranked = ctx["piv"], ctx["cut"], ctx["ranked"]
    close, qv = piv["close"], piv["qv"]
    u50 = wu.slice_universe(ranked, "U50")
    if not u50 or cut - 179 < 0:
        return {}
    out = {("AF1_AGGFUND_TIMING", "U50"): af1(ctx["fund_daily"], cut, u50),
           ("MT1_MOM_TURNOVER", "U50"): mt1(close, qv, cut, u50),
           ("RV1_QUIET_REVERSAL", "U50"): rv1(close, qv, cut, u50)}
    ae = ae1(close, cut, u50)
    if ae is not None:
        out[("AE1_ALT_VS_ETH", "U50")] = ae
    rets = ctx["cret"].iloc[cut - 89: cut + 1][u50]
    out[("SK1_REALSKEW", "U50")] = rets.skew().where(rets.notna().sum() >= 60)
    sc = spot_close(ctx)
    gap = (close.iloc[cut - 6: cut + 1][u50] / sc.iloc[cut - 6: cut + 1][u50] - 1.0)
    out[("PS1_PERP_SPOT_GAP", "U50")] = gap.mean().where(gap.notna().sum() >= 5)
    return out


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    if h in DIRECT:
        s = sig.dropna()
        return {k: float(v) for k, v in s.items() if v != 0 and k in universe}
    return wu.pick(sig, universe, LONG_TOP[h])


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r3_dev.json")
    a = ap.parse_args(argv)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__))}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
