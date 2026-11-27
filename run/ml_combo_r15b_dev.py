#!/usr/bin/env python3
"""Round 15b, DEV only (ADR 0113): supervised + unsupervised + RL walk-forward combination.

ML1: ridge regression on 8 cross-sectional rank features predicts the next-7-day
return rank of U50 coins; quintile long-short.  ML2: ML1 weights times an exposure
{0, 0.5, 1} chosen by offline tabular RL (contextual bandit, gamma 0) whose state is
a k-means market-state cluster plus the sign of ML1's last completed week, with a
30bp-per-week cost in the reward.  Everything is refitted at each weekly rebalance on
samples whose labels were complete before the cut-off.  Runs exactly the 2 trials of
``run/audit/evidence_newsrc_r15_20261120/prereg_ml_combo_r15b_v1.json``.

Usage::

    python3 run/ml_combo_r15b_dev.py --data-dir <dir>
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
import cross_section_v2_dev as cs  # noqa: E402
import newsrc_engine as ne  # noqa: E402
import wide_universe_dev as wu  # noqa: E402

VERSION = "ml-combo-r15b-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r15_20261120"
PREREG = EVID / "prereg_ml_combo_r15b_v1.json"
CONTEXT_ID = "U50-7d-ml-combo-r15b-v1"
KEYS = [("ML1_SUP", "U50"), ("ML2_COMBO", "U50")]
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}
HOLD, LAMBDA, K, MIN_WEEKS, MIN_STATE, RL_COST = 7, 10.0, 3, 8, 4, 0.0030
ACTIONS = (0.0, 0.5, 1.0)
DAY = bu.DAY_MS
FEATS = ("r1", "r7", "r28", "vol28", "vshock", "taker7", "fund7", "max28")
_PANEL: dict = {}
_EXPO: dict = {}


def crank(s: pd.Series) -> pd.Series:
    """Centred percentile rank in (-0.5, 0.5); NaN stays NaN."""
    return s.rank(pct=True) - 0.5


def features(piv: dict, fund: pd.DataFrame, cut: int, uni: list) -> pd.DataFrame:
    c, q, tb = piv["close"][uni], piv["qv"][uni], piv["tbq"][uni]
    dr = c.iloc[max(0, cut - 28): cut + 1].pct_change(fill_method=None).iloc[1:]
    raw = {
        "r1": c.iloc[cut] / c.iloc[cut - 1] - 1.0,
        "r7": c.iloc[cut] / c.iloc[cut - 7] - 1.0,
        "r28": c.iloc[cut] / c.iloc[cut - 28] - 1.0,
        "vol28": dr.std(),
        "vshock": np.log(q.iloc[cut - 6: cut + 1].mean() / q.iloc[cut - 34: cut - 6].mean()),
        "taker7": tb.iloc[cut - 6: cut + 1].sum(min_count=1) / q.iloc[cut - 6: cut + 1].sum(min_count=1),
        "fund7": fund[uni].iloc[cut - 6: cut + 1].sum(min_count=1),
        "max28": dr.max(),
    }
    df = pd.DataFrame({k: crank(v.replace([np.inf, -np.inf], np.nan)) for k, v in raw.items()})
    return df[list(FEATS)].fillna(0.0)


def market_state(piv: dict, cut: int, uni: list) -> np.ndarray:
    c = piv["close"][uni]
    dr = c.iloc[max(0, cut - 28): cut + 1].pct_change(fill_method=None).iloc[1:]
    r7 = c.iloc[cut] / c.iloc[cut - 7] - 1.0
    r28 = c.iloc[cut] / c.iloc[cut - 28] - 1.0
    return np.array([np.nanmean(r7), np.nanmean(r28), np.nanmean(dr.std()), np.nanstd(r7)], float)


def ridge(X: np.ndarray, y: np.ndarray, lam: float = LAMBDA) -> np.ndarray:
    return np.linalg.solve(X.T @ X + lam * np.eye(X.shape[1]), X.T @ y)


def kmeans(Z: np.ndarray, k: int = K, iters: int = 100, seed: int = 0) -> np.ndarray:
    """k-means++ init (seeded) + Lloyd; returns centres sorted by column 1 (28d return)."""
    rng = np.random.default_rng(seed)
    cen = [Z[rng.integers(len(Z))]]
    for _ in range(1, k):
        d2 = np.min([((Z - c) ** 2).sum(1) for c in cen], axis=0)
        p = d2 / d2.sum() if d2.sum() > 0 else np.full(len(Z), 1 / len(Z))
        cen.append(Z[rng.choice(len(Z), p=p)])
    cen = np.array(cen)
    for _ in range(iters):
        lab = np.argmin(((Z[:, None, :] - cen[None]) ** 2).sum(2), 1)
        new = np.array([Z[lab == j].mean(0) if (lab == j).any() else cen[j] for j in range(k)])
        if np.allclose(new, cen):
            break
        cen = new
    return cen[np.argsort(cen[:, 1])]


def assign(Z: np.ndarray, cen: np.ndarray) -> np.ndarray:
    return np.argmin(((Z[:, None, :] - cen[None]) ** 2).sum(2), 1)


def build_panel(data: Path, seg: str) -> list:
    """Weekly samples of one segment: day, U50 features, label rank, raw forward return, market state."""
    piv = cs.load_daily(data, seg)
    days = piv["open"].index.values
    syms = list(piv["open"].columns)
    fund = bu.load_funding(data, syms, int(days[0]), int(days[-1]))
    fund["day"] = bu.rdi.funding_day(fund["t"].values)  # ADR 0116 F-04: settlement jitter stays on its day
    fd = fund.groupby(["day", "sym"])["rate"].sum().unstack().reindex(days).reindex(columns=syms)
    n_bars = piv["close"].notna().cumsum()
    g0 = wu.rebalance_days(days, bu.SEGMENTS[seg][2], HOLD)[0]
    out = []
    for s in range(g0 % HOLD, len(days) - HOLD, HOLD):
        if s < 35:
            continue
        uni = wu.slice_universe(wu.eligible(piv, n_bars, s), "U50")
        if len(uni) < 10:
            continue
        cut = s - 2
        fwd = (piv["open"][uni].iloc[s + HOLD] / piv["open"][uni].iloc[s] - 1.0)
        out.append({"t": int(days[s]), "syms": uni, "X": features(piv, fd, cut, uni), "fwd": fwd,
                    "y": crank(fwd), "state": market_state(piv, cut, uni)})
    return out


def usable(samples: list, t_now: int) -> list:
    """Samples whose label window ended at or before the cut-off of a rebalance at t_now."""
    return [p for p in samples if p["t"] + HOLD * DAY <= t_now - 2 * DAY]


def fit(samples: list):
    rows = [(p["X"].values[m], p["y"].values[m]) for p in samples for m in [p["y"].notna().values] if m.any()]
    if len(samples) < MIN_WEEKS or not rows:
        return None
    return ridge(np.vstack([r[0] for r in rows]), np.concatenate([r[1] for r in rows]))


def ls_return(pred: pd.Series, fwd: pd.Series, uni: list) -> float:
    w = wu.pick(pred, uni, True)
    return float(sum(v * (0.0 if np.isnan(fwd.get(n, np.nan)) else fwd[n]) for n, v in w.items())) if w else float("nan")


def history(samples: list) -> list:
    """Walk-forward ML1 gross weekly return at each sample (NaN before a model exists)."""
    out = []
    for p in samples:
        beta = fit(usable(samples, p["t"]))
        out.append(float("nan") if beta is None else
                   ls_return(pd.Series(p["X"].values @ beta, index=p["syms"]), p["fwd"], p["syms"]))
    return out


def rl_exposure(samples: list, hist: list, t_now: int, state_now: np.ndarray) -> float:
    idx = [j for j, p in enumerate(samples) if p["t"] + HOLD * DAY <= t_now - 2 * DAY]
    if len(idx) < MIN_WEEKS:
        return ACTIONS[1]
    S = np.array([samples[j]["state"] for j in idx])
    mu, sd = np.nanmean(S, 0), np.nanstd(S, 0)
    sd[sd == 0] = 1.0
    Z = np.nan_to_num((S - mu) / sd)
    cen = kmeans(Z)
    lab = assign(Z, cen)
    pos = {j: n for n, j in enumerate(idx)}

    def last_sign(t):
        prev = [j for j in idx if samples[j]["t"] + HOLD * DAY <= t - 2 * DAY and np.isfinite(hist[j])]
        return None if not prev else int(hist[prev[-1]] > 0)

    q = {}
    for j in idx:
        sg = last_sign(samples[j]["t"])
        if sg is None or not np.isfinite(hist[j]):
            continue
        q.setdefault((int(lab[pos[j]]), sg), []).append(hist[j])
    cur = (int(assign(np.nan_to_num((state_now - mu) / sd)[None], cen)[0]), last_sign(t_now))
    r = q.get(cur, [])
    if cur[1] is None or len(r) < MIN_STATE:
        return ACTIONS[1]
    vals = [a * (np.mean(r) - RL_COST) for a in ACTIONS]
    return ACTIONS[int(np.argmax(vals))]


def panel_for(data: Path, seg: str) -> tuple:
    key = (str(data), seg)
    if key not in _PANEL:
        own = build_panel(data, seg)
        samples = (panel_for(data, "S1")[0] + own) if seg == "S2" else own
        _PANEL[key] = (samples, history(samples)) if seg == "S2" else (own, history(own))
    return _PANEL[key]


def signals(ctx: dict) -> dict:
    uni = wu.slice_universe(ctx["ranked"], "U50")
    if not uni or ctx["cut"] < 35:
        return {}
    samples, hist = panel_for(ctx["data"], ctx["seg"])
    t_now = int(ctx["days"][ctx["i"]])
    beta = fit(usable(samples, t_now))
    if beta is None:
        return {}
    X = features(ctx["piv"], ctx["fund_daily"], ctx["cut"], uni)
    pred = pd.Series(X.values @ beta, index=uni)
    _EXPO[(str(ctx["data"]), ctx["seg"], ctx["i"])] = rl_exposure(samples, hist, t_now,
                                                                  market_state(ctx["piv"], ctx["cut"], uni))
    return {("ML1_SUP", "U50"): pred, ("ML2_COMBO", "U50"): pred}


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    w = wu.pick(sig, universe, True)
    if h == "ML1_SUP":
        return w
    a = _EXPO.get((str(ctx["data"]), ctx["seg"], ctx["i"]), 0.0)
    return {n: v * a for n, v in w.items()} if a > 0 else {}


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=HOLD)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "ml_combo_r15b_dev.json")
    a = ap.parse_args(argv)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
            "source": "Binance USDT-M daily/funding (S3 mirror), DEV segments only"}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    exp = {s: [v for (d, sg, i), v in _EXPO.items() if sg == s] for s in ("S1", "S2")}
    out["rl_exposure_counts"] = {s: {str(x): v.count(x) for x in ACTIONS} for s, v in exp.items()}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    print("RL exposure counts", out["rl_exposure_counts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
