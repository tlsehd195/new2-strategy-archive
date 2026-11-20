#!/usr/bin/env python3
"""Round 10 of the repeated new-source search, DEV only (ADR 0105).

Volatility term structure, market-correlation change, price-volume
correlation, OBV divergence (U50 quintiles).  Runs exactly the 4 trials of
``run/audit/evidence_newsrc_r10_20261120/prereg_newsrc_r10_v1.json``.

Usage::

    python3 run/newsrc_r10_dev.py --data-dir <dir>
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

VERSION = "newsrc-r10-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r10_20261120"
PREREG = EVID / "prereg_newsrc_r10_v1.json"
CONTEXT_ID = "U50-14d-newsrc-r10-v1"
KEYS = [("VR1_VOLRATIO", "U50"), ("CR1_CORRCHANGE", "U50"), ("PV1_PRICEVOL_CORR", "U50"), ("OB1_OBV_DIVERGENCE", "U50")]
LONG_TOP = {"VR1_VOLRATIO": False, "CR1_CORRCHANGE": False, "PV1_PRICEVOL_CORR": True, "OB1_OBV_DIVERGENCE": True}
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}


def corr_with(x: pd.DataFrame, m: pd.Series, min_n: int) -> pd.Series:
    out = {}
    for s in x.columns:
        ok = x[s].notna() & m.notna()
        out[s] = float(np.corrcoef(x[s][ok], m[ok])[0, 1]) if ok.sum() >= min_n and x[s][ok].std() > 0 else np.nan
    return pd.Series(out, dtype=float)


def z(s: pd.Series) -> pd.Series:
    sd = s.std()
    return (s - s.mean()) / sd if sd > 0 else s * np.nan


def signals(ctx: dict) -> dict:
    cut, ranked = ctx["cut"], ctx["ranked"]
    u50 = wu.slice_universe(ranked, "U50")
    if not u50 or cut - 89 < 0:
        return {}
    r = ctx["cret"][u50]
    close, qv = ctx["piv"]["close"][u50], ctx["piv"]["qv"][u50]
    m = r.mean(axis=1, skipna=True)
    s7, s90 = r.iloc[cut - 6: cut + 1], r.iloc[cut - 89: cut + 1]
    vr = (s7.std() / s90.std()).where((s7.notna().sum() >= 5) & (s90.notna().sum() >= 60))
    c_new = corr_with(r.iloc[cut - 27: cut + 1], m.iloc[cut - 27: cut + 1], 20)
    c_old = corr_with(r.iloc[cut - 89: cut - 27], m.iloc[cut - 89: cut - 27], 40)
    dlq = np.log(qv / qv.shift(1)).replace([np.inf, -np.inf], np.nan)
    pv = {}
    for s in u50:
        a, b = r[s].iloc[cut - 27: cut + 1], dlq[s].iloc[cut - 27: cut + 1]
        ok = a.notna() & b.notna()
        pv[s] = float(np.corrcoef(a[ok], b[ok])[0, 1]) if ok.sum() >= 20 and a[ok].std() > 0 and b[ok].std() > 0 else np.nan
    rw, qw = r.iloc[cut - 27: cut + 1], qv.iloc[cut - 27: cut + 1]
    obv = (np.sign(rw) * qw).sum() / qw.sum()
    r28 = close.iloc[cut] / close.iloc[cut - 28] - 1.0
    ob = (z(obv.where(rw.notna().sum() >= 20)) - z(r28))
    sig = {"VR1_VOLRATIO": vr, "CR1_CORRCHANGE": c_new - c_old, "PV1_PRICEVOL_CORR": pd.Series(pv, dtype=float),
           "OB1_OBV_DIVERGENCE": ob}
    return {(h, "U50"): sig[h].replace([np.inf, -np.inf], np.nan) for h, _ in KEYS}


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    return wu.pick(sig, universe, LONG_TOP[h])


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r10_dev.json")
    a = ap.parse_args(argv)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__))}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
