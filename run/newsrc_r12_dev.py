#!/usr/bin/env python3
"""Round 12 of the repeated new-source search, DEV only (ADR 0107).

Market breadth timing and cross-sectional dispersion timing of the
equal-weight U50 index, and frog-in-the-pan momentum (U50 quintiles).  Runs
exactly the 3 trials of
``run/audit/evidence_newsrc_r12_20261120/prereg_newsrc_r12_v1.json``.

Usage::

    python3 run/newsrc_r12_dev.py --data-dir <dir>
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
import wide_universe_dev as wu  # noqa: E402

VERSION = "newsrc-r12-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r12_20261120"
PREREG = EVID / "prereg_newsrc_r12_v1.json"
CONTEXT_ID = "U50-14d-newsrc-r12-v1"
KEYS = [("BR1_BREADTH", "U50"), ("DS1_DISPERSION", "U50"), ("FIP1_FROGPAN", "U50")]
TIMING = {"BR1_BREADTH", "DS1_DISPERSION"}
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}


def z(s: pd.Series) -> pd.Series:
    sd = s.std()
    return (s - s.mean()) / sd if sd > 0 else s * np.nan


def breadth(close: pd.DataFrame, cut: int) -> float:
    w = close.iloc[cut - 49: cut + 1]
    ok = w.notna().sum() >= 40
    above = (close.iloc[cut] > w.mean())[ok & close.iloc[cut].notna()]
    return float(above.mean()) if len(above) else float("nan")


def dispersion_on(r: pd.DataFrame, cut: int) -> float:
    """1.0 = calm (current 14-day mean dispersion below its 180-day rolling median), 0.0 = not, NaN = too little data."""
    disp = r.iloc[cut - 179 - 13: cut + 1].std(axis=1, ddof=1)
    roll = disp.rolling(14, min_periods=10).mean().iloc[13:]
    if roll.notna().sum() < 120 or not np.isfinite(roll.iloc[-1]):
        return float("nan")
    return 1.0 if roll.iloc[-1] < roll.median() else 0.0


def frog(close: pd.DataFrame, r: pd.DataFrame, cut: int) -> pd.Series:
    pret = close.iloc[cut] / close.iloc[cut - 89] - 1.0
    w = r.iloc[cut - 89: cut + 1]
    n = w.notna().sum()
    idisc = np.sign(pret) * ((w < 0).sum() / n - (w > 0).sum() / n)
    ok = (n >= 60) & pret.notna()
    return (z(pret.where(ok)) - z(idisc.where(ok))).replace([np.inf, -np.inf], np.nan)


def signals(ctx: dict) -> dict:
    cut, ranked = ctx["cut"], ctx["ranked"]
    u50 = wu.slice_universe(ranked, "U50")
    if not u50 or cut - 192 < 0:
        return {}
    close, r = ctx["piv"]["close"][u50], ctx["cret"][u50]
    return {("BR1_BREADTH", "U50"): pd.Series(1.0 if breadth(close, cut) > 0.5 else 0.0, index=u50),
            ("DS1_DISPERSION", "U50"): pd.Series(dispersion_on(r, cut), index=u50),
            ("FIP1_FROGPAN", "U50"): frog(close, r, cut)}


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    if h in TIMING:
        on = float(sig.iloc[0]) if len(sig) else float("nan")
        return {s: 1.0 / len(universe) for s in universe} if on == 1.0 else {}
    return wu.pick(sig, universe, True)


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r12_dev.json")
    a = ap.parse_args(argv)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__))}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
