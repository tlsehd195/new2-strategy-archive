#!/usr/bin/env python3
"""Round 2 of the repeated new-source search, DEV only (ADR 0097).

Runs exactly the 6 trials of
``run/audit/evidence_newsrc_r2_20261120/prereg_newsrc_r2_v1.json``:
volatility-managed EW U50 long, betting against beta, downside beta,
coskewness, price delay (U50, W200).  Engine: ``newsrc_engine`` (ADR 0047
accounting, ADR 0041 calibrated cost).  Data: the same directory as ADR 0096
(daily, funding, 23:45 bar of ever-W200 names).

Usage::

    python3 run/newsrc_r2_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import newsrc_engine as ne  # noqa: E402
import wide_universe_dev as wu  # noqa: E402

VERSION = "newsrc-r2-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r2_20261120"
PREREG = EVID / "prereg_newsrc_r2_v1.json"
CONTEXT_ID = "U50W200-14d-newsrc-r2-v1"
KEYS = [("VM1_VOLMANAGED", "U50"), ("BAB_BETA", "U50"), ("DB_DOWNBETA", "U50"),
        ("CSK_COSKEW", "U50"), ("DLY_DELAY", "U50"), ("DLY_DELAY", "W200")]
LONG_TOP = {"BAB_BETA": False, "DB_DOWNBETA": True, "CSK_COSKEW": False, "DLY_DELAY": True}
LOOKBACK = 90
MIN_OBS = 60
MIN_DOWN = 25
VM_TARGET = 0.60
VM_CAP = 2.0
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}


def r2(y: np.ndarray, x: np.ndarray) -> float:
    x1 = np.column_stack([np.ones(len(y)), x])
    coef, *_ = np.linalg.lstsq(x1, y, rcond=None)
    res = y - x1 @ coef
    tss = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - float(np.sum(res ** 2)) / tss if tss > 0 else float("nan")


def risk_signals(rets: pd.DataFrame, names: list) -> dict:
    """rets: daily close-to-close returns, rows = lookback days ending at cut (+4 earlier rows for lags)."""
    sub = rets[names]
    m_full = sub.mean(axis=1, skipna=True)
    lags = np.column_stack([m_full.shift(k).values for k in range(5)])[4:]
    sub, m = sub.iloc[4:], m_full.iloc[4:].values
    out = {h: {} for h in LONG_TOP}
    for s in names:
        y = sub[s].values
        ok = np.isfinite(y) & np.isfinite(m) & np.isfinite(lags).all(axis=1)
        if ok.sum() < MIN_OBS:
            continue
        yy, mm = y[ok], m[ok]
        vm = np.var(mm, ddof=1)
        if not vm > 0:
            continue
        out["BAB_BETA"][s] = float(np.cov(yy, mm)[0, 1] / vm)
        dn = mm < 0
        if dn.sum() >= MIN_DOWN and np.var(mm[dn], ddof=1) > 0:
            out["DB_DOWNBETA"][s] = float(np.cov(yy[dn], mm[dn])[0, 1] / np.var(mm[dn], ddof=1))
        sy = yy.std(ddof=1)
        if sy > 0:
            out["CSK_COSKEW"][s] = float(np.mean((yy - yy.mean()) * (mm - mm.mean()) ** 2) / (sy * vm))
        rr = r2(yy, mm)
        ru = r2(yy, lags[ok])
        if ru > 0 and math.isfinite(rr):
            out["DLY_DELAY"][s] = 1.0 - rr / ru
    return {h: pd.Series(v, dtype=float) for h, v in out.items()}


def signals(ctx: dict) -> dict:
    cret, cut, ranked = ctx["cret"], ctx["cut"], ctx["ranked"]
    lo = cut - LOOKBACK + 1 - 4
    if lo < 1:
        return {}
    win = cret.iloc[lo: cut + 1]
    out = {}
    u50 = wu.slice_universe(ranked, "U50")
    w200 = wu.slice_universe(ranked, "W200")
    s50 = risk_signals(win, u50) if u50 else {}
    for h in LONG_TOP:
        if s50:
            out[(h, "U50")] = s50[h]
    if w200:
        out[("DLY_DELAY", "W200")] = risk_signals(win, w200)["DLY_DELAY"]
    m30 = cret.iloc[cut - 29: cut + 1][u50].mean(axis=1, skipna=True)
    vol = float(m30.std(ddof=1) * math.sqrt(365)) if m30.notna().sum() >= 20 else float("nan")
    out[("VM1_VOLMANAGED", "U50")] = pd.Series(vol, index=u50, dtype=float)
    return out


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    if h == "VM1_VOLMANAGED":
        vol = float(sig.iloc[0]) if len(sig) else float("nan")
        if not (vol > 0):
            return {}
        g = min(VM_CAP, VM_TARGET / vol)
        return {s: g / len(universe) for s in universe}
    return wu.pick(sig, universe, LONG_TOP[h])


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r2_dev.json")
    a = ap.parse_args(argv)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__))}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
