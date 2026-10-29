#!/usr/bin/env python3
"""Unsupervised (k-means) regime classifier trained on DEV S1 only, applied frozen to S2 (ADR 0050).

Prereg: run/audit/evidence_unsup_regime_20261029/prereg_unsup_regime_v1.json (committed before any result, 19716da).
Uses the saved candidate daily series of ADR 0044 and the same basket index; reads only DEV stretches S1 / S2
(``candidate_regime_dev.load_all`` runs ``assert_allowed``).  Diagnostic for elimination, not evidence of passing.

    python3 run/unsup_regime_dev.py --data-dir <klines/ funding/> --out <dir>
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import candidate_regime_dev as C  # noqa: E402

EV = HERE / "audit" / "evidence_unsup_regime_20261029"
SERIES = HERE / "audit" / "evidence_candidate_regime_20261028" / "candidate_daily_series.json.gz"
LOOK30, LOOK60, K, SEED, RESTARTS, ITERS = 30, 60, 3, 20261029, 50, 200
MIN_DAYS = C.MIN_DAYS
N_SHIFT = 2000
NAMES = ("U1", "U2", "U3")


def features(idx: np.ndarray) -> np.ndarray:
    """Row j = features known at the open of day r0+j (closes through j-1); NaN while not valid."""
    n = len(idx)
    f = np.full((n, 3), np.nan)
    ret = np.r_[np.nan, idx[1:] / idx[:-1] - 1.0]
    for j in range(1, n):
        p = j - 1
        if p < LOOK60:
            continue
        f[j, 0] = idx[p] / idx[p - LOOK30] - 1.0
        f[j, 1] = ret[p - LOOK30 + 1:p + 1].std()
        f[j, 2] = idx[p] / idx[p - LOOK60 + 1:p + 1].max() - 1.0
    return f


def kmeans(x: np.ndarray, k: int = K, seed: int = SEED, restarts: int = RESTARTS, iters: int = ITERS):
    rng = np.random.default_rng(seed)
    best = None
    for _ in range(restarts):
        c = [x[rng.integers(len(x))]]
        for _ in range(k - 1):
            d2 = np.min([((x - ci) ** 2).sum(1) for ci in c], axis=0)
            c.append(x[rng.choice(len(x), p=d2 / d2.sum())])
        c = np.array(c)
        for _ in range(iters):
            lab = ((x[:, None, :] - c[None]) ** 2).sum(2).argmin(1)
            new = np.array([x[lab == i].mean(0) if (lab == i).any() else c[i] for i in range(k)])
            if np.allclose(new, c):
                break
            c = new
        lab = ((x[:, None, :] - c[None]) ** 2).sum(2).argmin(1)
        inertia = float(((x - c[lab]) ** 2).sum())
        if best is None or inertia < best[0]:
            best = (inertia, c)
    return best


def assign(f: np.ndarray, mu, sd, cent) -> np.ndarray:
    """Label array of 'U1'..'U3' (centroids already ordered), 'NA' where features are invalid."""
    out = np.full(len(f), "NA", dtype=object)
    ok = np.isfinite(f).all(1)
    z = (f[ok] - mu) / sd
    out[ok] = np.array(NAMES, dtype=object)[((z[:, None, :] - cent[None]) ** 2).sum(2).argmin(1)]
    return out


def run_stats(lab: np.ndarray) -> dict:
    v = lab[lab != "NA"]
    runs, cur = [], 1
    for a, b in zip(v[:-1], v[1:]):
        if a == b:
            cur += 1
        else:
            runs.append(cur)
            cur = 1
    runs.append(cur)
    return {"days": int(len(v)), "median_run": float(np.median(runs)), "switches_per_year": float((len(runs) - 1) / len(v) * 365.25)}


def stability(series: list[dict], labs: dict, names) -> tuple[int, int, int]:
    """A_same, M_pos, n_cells over candidates x label names (cells with >=MIN_DAYS in both segments)."""
    a = m = n = 0
    for name in names:
        masks = {s: labs[s] == name for s in ("S1", "S2")}
        if min(m_.sum() for m_ in masks.values()) < MIN_DAYS:
            continue
        for r in series:
            x1 = np.array(r["S1"]["net_entry"])[masks["S1"]].mean()
            x2 = np.array(r["S2"]["net_entry"])[masks["S2"]].mean()
            n += 1
            a += (x1 > 0) == (x2 > 0)
            m += (x1 > 0) and (x2 > 0)
    return a, m, n


def null_dist(series, labs, names, rng, n_shift=N_SHIFT) -> dict:
    A, M = [], []
    for _ in range(n_shift):
        sh = {s: np.roll(labs[s], int(rng.integers(len(labs[s])))) for s in labs}
        a, m, _ = stability(series, sh, names)
        A.append(a)
        M.append(m)
    return {"A": np.array(A), "M": np.array(M)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--n-shift", type=int, default=N_SHIFT)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    regs = C.index_and_regimes(a.data_dir)
    series = json.loads(gzip.open(SERIES).read())
    # ---- train on S1 only, freeze
    F = {}
    for s in ("S1", "S2"):
        g = regs[s]
        F[s] = features(g["idx"])[g["d0"] - g["r0"]: g["d1"] - g["r0"] + 1]
    ok1 = np.isfinite(F["S1"]).all(1)
    mu, sd = F["S1"][ok1].mean(0), F["S1"][ok1].std(0)
    inertia, cent = kmeans((F["S1"][ok1] - mu) / sd)
    order = np.argsort(cent[:, 0])                       # U1 = lowest 30d-return centroid
    cent = cent[order]
    frozen = {"mu": mu.tolist(), "sd": sd.tolist(), "centroids_z": cent.tolist(),
              "centroids_raw": (cent * sd + mu).tolist(), "inertia": inertia, "train_days": int(ok1.sum()),
              "features": ["f1_ret30", "f2_vol30", "f3_dist_high60"], "labels": list(NAMES)}
    (a.out / "frozen_classifier.json").write_text(json.dumps(frozen, indent=1) + "\n")   # frozen BEFORE S2 is labelled
    labs = {s: assign(F[s], mu, sd, cent) for s in ("S1", "S2")}
    # ---- scorecards
    human = {s: {"R1_BULL": regs[s]["r1"] == "BULL", "R1_SIDE": regs[s]["r1"] == "SIDE", "R1_BEAR": regs[s]["r1"] == "BEAR",
                 "R2_DD": regs[s]["r2"] == "DD"} for s in ("S1", "S2")}
    unsup = {s: {n: labs[s] == n for n in NAMES} for s in ("S1", "S2")}

    def card(r, masks):
        out = {}
        for s in ("S1", "S2"):
            x, cnt, w = (np.array(r[s][k]) for k in ("net_entry", "trades", "wins"))
            out[s] = {n: {**C.stats(x[m], cnt[m], w[m]), "nw_t": C.nw_t(x[m])} for n, m in masks[s].items()}
        return out

    byid = {r["strategy_id"]: r for r in series}
    top5 = sorted(series, key=lambda r: -np.nan_to_num(C.T.sr_ann(np.array(r["S1"]["net_exit"])), nan=-9))[:5]
    bundles = {"B1_first5": series[:5], "B2_all33": series, "B3_top5_S1": top5,
               "B4_long_only": [r for r in series if r["direction"] == "long"], "B5_first20": series[:20]}
    rep = {"version": "unsup-regime-dev-v1", "prereg": "prereg_unsup_regime_v1.json", "frozen": frozen,
           "label_days": {s: {n: int(m.sum()) for n, m in unsup[s].items()} | {"NA": int((labs[s] == "NA").sum())} for s in unsup},
           "persistence": {s: run_stats(labs[s]) for s in labs},
           "persistence_human_R1": {s: run_stats(regs[s]["r1"]) for s in labs},
           "confusion_vs_R1": {s: {n: {h: int(((labs[s] == n) & (regs[s]["r1"] == h)).sum()) for h in ("BULL", "SIDE", "BEAR", "NA")}
                                   for n in NAMES} for s in labs},
           "basket_hold_gross_bp_day": {s: {n: float(regs[s]["mean_r"][: len(m)][m].mean() * 1e4) if m.sum() else None
                                            for n, m in unsup[s].items()} for s in unsup},
           "bundles": {}, "candidates": {}}
    for name, members in bundles.items():
        comb = C.combine(members)
        rep["bundles"][name] = {"members": [m["strategy_id"] for m in members], "unsup": card(comb, unsup), "human": card(comb, human)}
    for r in series:
        rep["candidates"][r["strategy_id"]] = {"rank": r["rank"], "direction": r["direction"], "unsup": card(r, unsup), "human": card(r, human)}
    # ---- stability + null
    rng = np.random.default_rng(SEED)
    hl = {s: regs[s]["r1"] for s in ("S1", "S2")}
    obs_u, obs_h = stability(series, labs, NAMES), stability(series, hl, ("BULL", "SIDE", "BEAR"))
    nu = null_dist(series, labs, NAMES, rng, a.n_shift)
    nh = null_dist(series, hl, ("BULL", "SIDE", "BEAR"), rng, a.n_shift)

    def summ(obs, nd):
        return {"A_same": obs[0], "M_pos": obs[1], "cells": obs[2], "same_sign_rate": obs[0] / obs[2] if obs[2] else None, "null_mean_A": float(nd["A"].mean()), "null_mean_M": float(nd["M"].mean()),
                "frac_null_A_ge_obs": float((nd["A"] >= obs[0]).mean()), "frac_null_M_ge_obs": float((nd["M"] >= obs[1]).mean())}
    rep["stability"] = {"unsup": summ(obs_u, nu), "human_R1": summ(obs_h, nh), "n_shift": a.n_shift}
    cells_ok = all(unsup[s][n].sum() >= MIN_DAYS for s in unsup for n in NAMES)
    pu, ph = rep["persistence"], rep["stability"]
    rep["verdict"] = {
        "a_null_frac_A_le_0.05": bool(rep["stability"]["unsup"]["frac_null_A_ge_obs"] <= 0.05),
        "b_A_unsup_gt_A_human": bool(obs_u[0] > obs_h[0]),
        "c_median_run_ge_5_both": bool(pu["S1"]["median_run"] >= 5 and pu["S2"]["median_run"] >= 5),
        "d_all_clusters_60d_both": bool(cells_ok)}
    rep["verdict"]["result"] = "USEFUL_AS_DIAGNOSTIC" if all(rep["verdict"].values()) else "NOT_USEFUL"
    rep["runtime_seconds"] = time.time() - t0
    (a.out / "unsup_regime_dev.json").write_text(json.dumps(rep, indent=1, default=float, ensure_ascii=False) + "\n")
    print(json.dumps({"verdict": rep["verdict"], "stability": rep["stability"], "label_days": rep["label_days"]}, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
