#!/usr/bin/env python3
"""Round 15d, DEV only (ADR 0113): rules of recent well-known traders on the U50 alts.

QM1 Qullamaggie breakout (prior 30% move, tight consolidation above the 20-day SMA,
closing breakout; stop at the consolidation low, trail with the 10-day SMA from day 5,
60 days max; 5% per position, at most 20) and MV1 Minervini Trend Template (8
criteria, equal weight of passing coins).  Runs exactly the 2 trials of
``run/audit/evidence_newsrc_r15_20261120/prereg_traders_r15d_v1.json`` on the shared
newsrc engine with daily rebalancing.

    python3 run/traders_r15d_dev.py --data-dir <dir>
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

VERSION = "traders-r15d-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r15_20261120"
PREREG = EVID / "prereg_traders_r15d_v1.json"
CONTEXT_ID = "U50-1d-traders-r15d-v1"
KEYS = [("QM1_BREAKOUT", "U50"), ("MV1_TREND_TEMPLATE", "U50")]
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}
POS_W, MAX_POS, MIN_DAYS_TRAIL, MAX_DAYS = 0.05, 20, 5, 60
_STATE: dict = {}


def qm_setup(close: pd.Series, c: int) -> float:
    """Prior-move ratio if the Qullamaggie breakout triggers at cut c, else NaN."""
    if c - 90 < 0:
        return float("nan")
    x = close.iloc[: c + 1].values
    a, b = x[c - 90: c - 29], x[c - 60: c - 9]
    if np.isnan(a).any() or np.isnan(b).any() or np.isnan(x[c - 29: c + 1]).any():
        return float("nan")
    lo_i, hi_i = c - 90 + int(np.argmin(a)), c - 60 + int(np.argmax(b))
    move = x[hi_i] / x[lo_i]
    if not (move >= 1.30 and lo_i < hi_i):
        return float("nan")
    cons, prev = x[c - 14: c], x[c - 29: c - 14]
    rng, rng_prev = cons.max() / cons.min() - 1.0, prev.max() / prev.min() - 1.0
    sma20 = x[c - 20: c].mean()
    if rng <= 0.20 and rng <= rng_prev and x[c - 1] >= sma20 and x[c] > cons.max():
        return float(move)
    return float("nan")


def trend_template(close: pd.DataFrame, c: int, uni: list) -> list:
    """Coins of uni passing all 8 Minervini criteria at cut c."""
    if c - 364 < 0:
        return []
    h = close[uni].iloc[c - 364: c + 1]
    last = h.iloc[-1]
    sma = {n: h.iloc[-n:].mean(skipna=False) for n in (50, 150, 200)}
    sma200_old = close[uni].iloc[c - 21 - 199: c - 21 + 1].mean(skipna=False)
    lo, hi = h.min(skipna=False), h.max(skipna=False)
    r126 = last / close[uni].iloc[c - 126] - 1.0
    rs = r126.rank(pct=True)
    ok = ((last > sma[150]) & (last > sma[200]) & (sma[150] > sma[200]) & (sma[200] > sma200_old)
          & (sma[50] > sma[150]) & (sma[50] > sma[200]) & (last > sma[50])
          & (last >= 1.30 * lo) & (last >= 0.75 * hi) & (rs >= 0.70))
    return sorted(ok[ok.fillna(False)].index)


def drifted(prev: dict, opn: pd.DataFrame, j: int, i: int) -> dict:
    out = {}
    for s, w in prev.items():
        for d in range(j, i):
            r = opn[s].iloc[d + 1] / opn[s].iloc[d] - 1.0
            w = w * (1.0 + (0.0 if math.isnan(r) else r))
        out[s] = w
    return out


def signals(ctx: dict) -> dict:
    uni = wu.slice_universe(ctx["ranked"], "U50")
    if not uni:
        return {}
    close, c = ctx["piv"]["close"], ctx["cut"]
    qm = pd.Series({s: qm_setup(close[s], c) for s in uni}, dtype=float)
    mv = pd.Series(1.0, index=trend_template(close, c, uni), dtype=float)
    return {("QM1_BREAKOUT", "U50"): qm, ("MV1_TREND_TEMPLATE", "U50"): mv}


def qm_step(st: dict, sig: pd.Series, close: pd.DataFrame, c: int) -> tuple:
    """Update open Qullamaggie positions at cut c; returns (kept symbols, new entries)."""
    kept = []
    for s, p in list(st["pos"].items()):
        p["days"] += 1
        x = close[s].iloc[c]
        sma10 = close[s].iloc[c - 9: c + 1].mean(skipna=False)
        if (np.isfinite(x) and x < p["stop"]) or (p["days"] >= MIN_DAYS_TRAIL and np.isfinite(sma10) and x < sma10) \
                or p["days"] >= MAX_DAYS:
            del st["pos"][s]
        else:
            kept.append(s)
    cand = sig.dropna()
    cand = cand[[s for s in cand.index if s not in st["pos"]]]
    new = []
    for s in sorted(cand.index, key=lambda n: (-cand[n], n)):
        if len(st["pos"]) >= MAX_POS:
            break
        st["pos"][s] = {"stop": float(close[s].iloc[c - 14: c].min()), "days": 1}
        new.append(s)
    return kept, new


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    key = (str(ctx["data"]), ctx["seg"], h)
    st = _STATE.setdefault(key, {"w": {}, "i": None, "pos": {}, "set": ()})
    i, opn = ctx["i"], ctx["piv"]["open"]
    held = drifted(st["w"], opn, st["i"], i) if st["w"] and st["i"] is not None else {}
    if h == "QM1_BREAKOUT":
        kept, new = qm_step(st, sig, ctx["piv"]["close"], ctx["cut"])
        w = {s: held[s] for s in kept if s in held}
        w.update({s: POS_W for s in new})
    else:
        cur = tuple(sorted(sig.index))
        if not cur:
            w = {}
        elif cur == st["set"] and held:
            w = held
        else:
            w = {s: 1.0 / len(cur) for s in cur}
        st["set"] = cur
    st["w"], st["i"] = dict(w), i
    return w


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "traders_r15d_dev.json")
    a = ap.parse_args(argv)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
            "source": "Binance USDT-M daily/funding (S3 mirror), DEV segments only"}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
