#!/usr/bin/env python3
"""Market-factor-residual cross-sectional DEV check (ADR 0051).

Runs the four residual hypotheses x two universes (U50, W200) fixed in
``run/audit/evidence_residual_signals_20261029/prereg_residual_signals_v1.json``
(committed before any data was downloaded), DEV segments only.  The engine,
universe rule, cost model, statistics and flag are those of
``run/wide_universe_dev.py`` (ADR 0047); only the signals, hypothesis set,
universe set and one reference cost row are replaced.

Never reads: 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards; BTCUSDT
in S2.  No orders, no private API, no NEW2.

Usage: same data steps as run/wide_universe_dev.py, then::

    python3 run/residual_signals_dev.py --data-dir <dir>
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
import broad_universe_dev as bu
import wide_universe_dev as wu

VERSION = "residual-signals-dev-v1"
EVID = HERE / "audit" / "evidence_residual_signals_20261029"
PREREG = EVID / "prereg_residual_signals_v1.json"
FACTOR_UNIVERSE = 50
BETA_DAYS = 90
BETA_MIN_OBS = 60
LIMIT_RATIO = 0.70  # ADR 0049 reference row only, never in the flag
HYPS = {"R1_RESMOM28": (True, 28), "R2_RESREV7": (False, 7),
        "R3_IDIOVOL30": (False, 28), "R4_RESMAX28": (False, 28)}
UNIVERSES = {"U50": (0, 50), "W200": (0, 200)}
COST_ROWS = ("cal", "cal_x2", "cal_tail_x2", "cost0", "cal_limit070")
RAW_SIBLING = {"R1_RESMOM28": "H1_MOM28", "R2_RESREV7": "H2_REV7", "R3_IDIOVOL30": "H4_LOWVOL30",
               "R4_RESMAX28": "MAX28"}

#: (first day of the segment, hypothesis) -> list of cross-sectional Spearman rho(residual signal, raw sibling)
_rank_corr: dict = {}
_n_bars_cache: dict = {}


def market_factor(rets: pd.DataFrame, members: list) -> pd.Series:
    """Equal-weight mean daily return of the factor members (NaN skipped) for every row of ``rets``."""
    return rets[members].mean(axis=1)


def residuals(rets: pd.DataFrame, m: pd.Series) -> tuple:
    """OLS beta (with intercept) of each column on m over the window; e = r - beta*m.

    Columns with fewer than BETA_MIN_OBS valid pairs get NaN beta and an all-NaN residual.
    """
    r = rets.values
    mv = m.values[:, None]
    ok = ~np.isnan(r) & ~np.isnan(mv)
    n = ok.sum(axis=0)
    rz = np.where(ok, r, 0.0)
    mz = np.where(ok, mv, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        rbar, mbar = rz.sum(axis=0) / n, mz.sum(axis=0) / n
        dm = np.where(ok, mv - mbar, 0.0)
        dr = np.where(ok, r - rbar, 0.0)
        beta = (dm * dr).sum(axis=0) / (dm * dm).sum(axis=0)
    beta[n < BETA_MIN_OBS] = np.nan
    e = pd.DataFrame(r - beta[None, :] * mv, index=rets.index, columns=rets.columns)
    return pd.Series(beta, index=rets.columns), e


def window_stat(e: pd.DataFrame, days: int, fn) -> pd.Series:
    w = e.iloc[-days:]
    out = fn(w)
    out[w.notna().sum() < days] = np.nan
    return out


def residual_signals(e: pd.DataFrame) -> dict:
    scale = e.iloc[-28:].std()
    mom = e.iloc[-28:].sum() / scale
    mom[(e.iloc[-28:].notna().sum() < 28) | ~(scale > 0)] = np.nan
    return {"R1_RESMOM28": mom,
            "R2_RESREV7": window_stat(e, 7, lambda w: w.sum()),
            "R3_IDIOVOL30": window_stat(e, 30, lambda w: w.std()),
            "R4_RESMAX28": window_stat(e, 28, lambda w: w.max())}


def hyp_signals(piv: dict, fund_daily: pd.DataFrame, cut: int) -> dict:
    """Residual signals from bars <= cut; market factor = U50 members at rebalance day i = cut + 2."""
    close = piv["close"]
    key = id(close)
    if key not in _n_bars_cache:
        _n_bars_cache[key] = close.notna().cumsum()
    ranked = wu.eligible(piv, _n_bars_cache[key], cut + 2)
    members = ranked[:FACTOR_UNIVERSE]
    nan = close.iloc[cut] * np.nan
    if len(members) < wu.MIN_UNIVERSE or cut < BETA_DAYS:
        return {h: nan for h in HYPS}
    rets = close.iloc[cut - BETA_DAYS: cut + 1].pct_change(fill_method=None).iloc[1:]
    _, e = residuals(rets, market_factor(rets, members))
    out = residual_signals(e)
    raw = bu.signals(piv, fund_daily, cut)
    raw["MAX28"] = rets.iloc[-28:].max()
    seg = int(close.index[0])
    for h, sib in RAW_SIBLING.items():
        a, b = out[h].reindex(members), raw[sib].reindex(members)
        ok = a.notna() & b.notna()
        if ok.sum() >= 20:
            _rank_corr.setdefault((seg, h), []).append(float(a[ok].rank().corr(b[ok].rank())))
    return out


_side_cost = wu.side_cost


def side_cost(row: str, cal: dict, b: float, q15: float) -> float:
    if row == "cal_limit070":
        return LIMIT_RATIO * _side_cost("cal", cal, b, q15)
    return _side_cost(row, cal, b, q15)


def patch_engine() -> None:
    wu.VERSION, wu.EVID, wu.PREREG = VERSION, EVID, PREREG
    wu.HYPS, wu.UNIVERSES, wu.COST_ROWS = HYPS, UNIVERSES, COST_ROWS
    wu.N_TRIALS = len(HYPS) * len(UNIVERSES)
    wu.hyp_signals, wu.side_cost = hyp_signals, side_cost


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "residual_signals_dev.json")
    a = ap.parse_args(argv)
    patch_engine()
    rc = wu.main(["--data-dir", str(a.data_dir), "--out", str(a.out)])
    out = json.loads(a.out.read_text())
    out["residual_script_sha256"] = bu.sha(Path(__file__))
    out["wide_engine_sha256"] = bu.sha(HERE / "wide_universe_dev.py")
    out["limit070_row_note"] = "cal_limit070 = 0.70 x cal (ADR 0049, ESTIMATED_FROM_PUBLIC_DATA, unverifiable); reference only"
    seg_of = {bu.ms(lo): s for s, (lo, _hi, _f) in bu.SEGMENTS.items()}
    out["signal_rank_corr_with_raw_sibling"] = {
        f"{seg_of.get(seg, seg)}:{h}": {"sibling": RAW_SIBLING[h], "mean": float(np.mean(v)), "n": len(v)}
        for (seg, h), v in sorted(_rank_corr.items())}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    for k, v in out["signal_rank_corr_with_raw_sibling"].items():
        print("rank corr", k, v)
    return rc


if __name__ == "__main__":
    sys.exit(main())
