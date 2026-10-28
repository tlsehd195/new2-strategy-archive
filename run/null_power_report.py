#!/usr/bin/env python3
"""Summarise runs.jsonl of null_power_sim.py by the metrics fixed in the prereg (ADR 0048).

    python3 run/null_power_report.py --runs <dir>/runs.jsonl --out <dir>/null_power_summary.json
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

REAL_S1_BEST = 1.0380758864949031          # ADR 0019 / strong_search_dev.json, Track A best S1 annual SR
THRESHOLDS = (1.0, 1.5, 2.0, 2.8)


def wilson(k: int, n: int, z: float = 1.96) -> list:
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def verdict(emp: np.ndarray, formula: float) -> str:
    med, p95 = float(np.median(emp)), float(np.percentile(emp, 95))
    if p95 < formula:
        return "conservative"
    if med > formula:
        return "anti-conservative"
    return "about right"


def null_group(rows: list) -> dict:
    best = np.array([r["best_sr"] for r in rows])
    ef = np.array([r["emax_unit_all"] for r in rows])
    ee = np.array([r["emax_unit_eff"] for r in rows])
    n = len(rows)
    out = {"runs": n,
           "trials_mean": float(np.mean([r["trials"]["total"] for r in rows])),
           "n_eff_mean": float(np.mean([r["trials"]["n_eff"] for r in rows])),
           "best_sr": {"mean": float(best.mean()), "sd": float(best.std(ddof=1)) if n > 1 else None,
                       "p05": float(np.percentile(best, 5)), "median": float(np.median(best)),
                       "p95": float(np.percentile(best, 95)), "max": float(best.max())},
           "formula_all_trials": float(ef.mean()), "formula_effective_trials": float(ee.mean()),
           "verdict_all_trials": verdict(best, float(ef.mean())),
           "verdict_effective_trials": verdict(best, float(ee.mean())),
           "share_best_at_least": {str(t): {"share": float((best >= t).mean()), "k": int((best >= t).sum()),
                                           "wilson95": wilson(int((best >= t).sum()), n)} for t in THRESHOLDS},
           "share_dsr_all_ge_0.95": float(np.mean([r["dsr_all"] >= 0.95 for r in rows])),
           "share_best_is_track_b": float(np.mean([r["best_is_track_b"] for r in rows])),
           "median_n_trials_sr_ge_1.0": float(np.median([r["n_sr_ge"]["1.0"] for r in rows])),
           "median_A_sr_quantiles": {q: float(np.median([r["sr_quantiles_A"][q] for r in rows]))
                                     for q in ("1", "50", "90", "99")},
           "pbo_median": float(np.median([r["pbo"] for r in rows])),
           "real_best_percentile_in_group": float((best < REAL_S1_BEST).mean())}
    return out


def planted_group(rows: list, thr95: float) -> dict:
    n = len(rows)
    best = np.array([r["best_sr"] for r in rows])
    pl = [r["planted"] for r in rows]
    ranks = [p.get("p_rank") for p in pl]
    like = np.array([bool(p.get("p_like_in_top10")) for p in pl])
    caught = like & (best >= thr95)
    return {"runs": n, "p_realized_sr_median": float(np.median([p.get("p_sr", np.nan) for p in pl])),
            "p_realized_sr_p10_p90": [float(np.nanpercentile([p.get("p_sr", np.nan) for p in pl], q)) for q in (10, 90)],
            "best_sr_median": float(np.median(best)),
            "share_best_ge_1.0": float((best >= 1.0).mean()), "share_best_ge_1.5": float((best >= 1.5).mean()),
            "share_best_ge_null95": float((best >= thr95).mean()),
            "share_p_like_in_top10": float(like.mean()),
            "share_p_rank_le_25": float(np.mean([(rk is not None and rk <= 25) for rk in ranks])),
            "p_rank_median": float(np.median([rk if rk is not None else 99999 for rk in ranks])),
            "share_caught": float(caught.mean()), "caught_wilson95": wilson(int(caught.sum()), n)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    rows = [json.loads(line) for line in a.runs.read_text().splitlines() if line.strip()]
    groups: dict = {}
    for r in rows:
        groups.setdefault(r["job"]["group"], []).append(r)
    out = {"runs_total": len(rows), "real_s1_best_sr_reference": REAL_S1_BEST, "null": {}, "planted": {}}
    for g, rs in sorted(groups.items()):
        if g.startswith("null") or g.startswith("posthoc_null"):
            out["null"][g] = null_group(rs)
    thr95 = out["null"]["null_day"]["best_sr"]["p95"]
    out["null_day_best_sr_p95_threshold"] = thr95
    for g, rs in sorted(groups.items()):
        if g.startswith("plant"):
            out["planted"][g] = planted_group(rs, thr95)
    a.out.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
