#!/usr/bin/env python3
"""Round 13 of the repeated new-source search, DEV only (ADR 0108).

Pairs trading on log-price spreads of the most correlated U50 pairs (K=20,
|z|>1 and K=50, |z|>1.5) and peer-group relative reversal.  Runs exactly the
3 trials of ``run/audit/evidence_newsrc_r13_20261120/prereg_newsrc_r13_v1.json``.

Usage::

    python3 run/newsrc_r13_dev.py --data-dir <dir>
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

VERSION = "newsrc-r13-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r13_20261120"
PREREG = EVID / "prereg_newsrc_r13_v1.json"
CONTEXT_ID = "U50-14d-newsrc-r13-v1"
KEYS = [("PR1_PAIRS_K20_Z1", "U50"), ("PR2_PAIRS_K50_Z15", "U50"), ("PG1_PEER_REVERSAL", "U50")]
PAIRS = {"PR1_PAIRS_K20_Z1": (20, 1.0), "PR2_PAIRS_K50_Z15": (50, 1.5)}
N_PEERS = 5
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}


def pair_weights(close: pd.DataFrame, cut: int, k: int, entry: float) -> pd.Series:
    w = close.iloc[cut - 89: cut + 1]
    w = w.loc[:, w.notna().all()]
    names = sorted(w.columns)
    out = pd.Series(0.0, index=close.columns)
    if len(names) < 2:
        return out
    lp = np.log(w[names])
    c = lp.diff().iloc[1:].corr().values
    iu = np.triu_indices(len(names), 1)
    order = sorted(range(len(iu[0])), key=lambda j: (-c[iu[0][j], iu[1][j]], j))[:k]
    for j in order:
        a, b = names[iu[0][j]], names[iu[1][j]]
        y, x = lp[a].values, lp[b].values
        x1 = np.column_stack([np.ones(len(x)), x])
        coef, *_ = np.linalg.lstsq(x1, y, rcond=None)
        res = y - x1 @ coef
        sd = res.std(ddof=1)
        if not sd > 0:
            continue
        z = res[-1] / sd
        leg = 0.5 / k
        if z > entry:
            out[a] -= leg
            out[b] += leg
        elif z < -entry:
            out[a] += leg
            out[b] -= leg
    return out


def peer_reversal(close: pd.DataFrame, r: pd.DataFrame, cut: int) -> pd.Series:
    c = r.iloc[cut - 89: cut - 13].corr(min_periods=60)
    r14 = close.iloc[cut] / close.iloc[cut - 13] - 1.0
    out = {}
    for s in close.columns:
        if s not in c.columns or not np.isfinite(r14.get(s, np.nan)):
            continue
        cs = c[s].drop(s).dropna()
        cs = cs[[p for p in cs.index if np.isfinite(r14[p])]]
        if len(cs) < N_PEERS:
            continue
        peers = sorted(cs.index, key=lambda p: (-cs[p], p))[:N_PEERS]
        out[s] = float(r14[s] - r14[peers].mean())
    return pd.Series(out, dtype=float)


def signals(ctx: dict) -> dict:
    cut, ranked = ctx["cut"], ctx["ranked"]
    u50 = wu.slice_universe(ranked, "U50")
    if not u50 or cut - 89 < 0:
        return {}
    close, r = ctx["piv"]["close"][u50], ctx["cret"][u50]
    out = {(h, "U50"): pair_weights(close, cut, k, e) for h, (k, e) in PAIRS.items()}
    out[("PG1_PEER_REVERSAL", "U50")] = peer_reversal(close, r, cut)
    return out


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    if h in PAIRS:
        return {s: float(v) for s, v in sig.reindex(universe).items() if np.isfinite(v) and v != 0.0}
    return wu.pick(sig, universe, False)


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r13_dev.json")
    a = ap.parse_args(argv)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__))}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
