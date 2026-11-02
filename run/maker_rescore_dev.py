#!/usr/bin/env python3
"""Limit-order (maker) cost re-pricing of published DEV cross-sectional candidates (ADR 0056).

Reads only already published DEV result JSONs (no market data, no validation/locked/IW1/paper
window).  Rules are fixed in run/audit/evidence_maker_rescore_20261102/prereg_maker_rescore_v1.json.
R(lambda, h) = (1-h)*R_cost0 - lambda*(R_cost0 - R_base);  SR = R / sigma,  sigma = R_cost0 / SR_cost0.

Usage:  python3 run/maker_rescore_dev.py [--out PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUD = HERE / "audit"
EVID = AUD / "evidence_maker_rescore_20261102"
PREREG = EVID / "prereg_maker_rescore_v1.json"

#: (label, file, base row, cost0 row, x2 row, validation row or None)
SOURCES = [
    ("lowliq", AUD / "lowliq_cost_20261027/broad_universe_lowliq.json", "assumed", "cost0x", "cost2x", None),
    ("cs_v2", AUD / "evidence_cross_section_v2_20261027/cross_section_v2_dev.json", "measured", "cost0", "measured_x2", None),
    ("alpha101", AUD / "evidence_alpha101_20261101/alpha101_dev.json", "measured", "cost0", "measured_x2", None),
    ("residual", AUD / "evidence_residual_signals_20261029/residual_signals_dev.json", "cal", "cost0", "cal_x2", "cal_limit070"),
]
FOCUS = ["lowliq:H1_MOM28", "cs_v2:N4_TAKERFLOW", "alpha101:A101", "residual:W200:R4_RESMAX28"]
SCENARIOS = {  # name: (lambda, h)
    "BREAKEVEN_FEE": (1.00, 0.0),
    "OPTIMISTIC": (0.47, 0.0),
    "CENTRAL": (0.70, 0.0),
    "CONSERVATIVE": (0.79, 0.34),
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def reprice(r0: dict, rb: dict, lam: float, h: float) -> dict:
    """Linear re-pricing of one segment. r0 = cost0 row, rb = base-cost row."""
    sigma = r0["ann_return_pct"] / r0["sr_ann"] if r0["sr_ann"] else None
    if not sigma:
        return {"sr": None, "ret": None, "alpha_t": None}
    ret = (1 - h) * r0["ann_return_pct"] - lam * (r0["ann_return_pct"] - rb["ann_return_pct"])
    a = (1 - h) * r0["alpha_bp_day"] - lam * (r0["alpha_bp_day"] - rb["alpha_bp_day"])
    se = r0["alpha_bp_day"] / r0["alpha_t"] if r0["alpha_t"] else None
    return {"sr": ret / sigma, "ret": ret, "alpha_t": (a / se) if se else None}


def breakeven_lambda(r0: dict, rb: dict):
    c = r0["ann_return_pct"] - rb["ann_return_pct"]
    if r0["ann_return_pct"] <= 0 or c <= 0:
        return None
    return r0["ann_return_pct"] / c


def collect() -> list[dict]:
    out = []
    for label, path, base, c0, x2, val in SOURCES:
        d = json.loads(path.read_text())
        chance = list(d["null_max_sr_ann"].values())
        for name, v in d["hypotheses"].items():
            key = f"{label}:{name}"
            s1, s2 = v["S1"], v["S2"]
            ent = {"id": key, "file": path.name, "chance_small": chance[0], "chance_cum": chance[-1],
                   "base_s1": s1[base]["sr_ann"], "base_s2": s2[base]["sr_ann"],
                   "cost_pct_capital": s1.get("cost_paid_pct_of_capital"),
                   "cost0_s1": s1[c0]["sr_ann"], "cost0_s2": s2[c0]["sr_ann"],
                   "halves_base": s1.get("halves_sr_ann"), "scenarios": {}, "validation": {}}
            # linearity validation against published rows
            for tag, row, lam in (("x2", x2, 2.0), ("limit070", val, 0.70)):
                if row is None:
                    continue
                p1 = reprice(s1[c0], s1[base], lam, 0.0)["sr"]
                p2 = reprice(s2[c0], s2[base], lam, 0.0)["sr"]
                ent["validation"][tag] = {"S1_pred": p1, "S1_pub": s1[row]["sr_ann"], "S2_pred": p2, "S2_pub": s2[row]["sr_ann"]}
            for sc, (lam, h) in SCENARIOS.items():
                a1, a2 = reprice(s1[c0], s1[base], lam, h), reprice(s2[c0], s2[base], lam, h)
                x1 = reprice(s1[c0], s1[base], 2 * lam, h)
                shift = (a1["sr"] - ent["base_s1"]) if a1["sr"] is not None and ent["halves_base"] else None
                halves = [x + shift for x in ent["halves_base"]] if shift is not None else None
                conds = {
                    "a": a1["sr"] is not None and a1["sr"] > chance[0],
                    "a_cum": a1["sr"] is not None and a1["sr"] > chance[-1],
                    "b": a1["alpha_t"] is not None and a1["alpha_t"] >= 2,
                    "c": halves is not None and all(x > 0 for x in halves),
                    "d": a2["sr"] is not None and a2["sr"] > 0.5,
                    "e": x1["sr"] is not None and x1["sr"] > 0,
                }
                ent["scenarios"][sc] = {"S1": a1["sr"], "S2": a2["sr"], "alpha_t": a1["alpha_t"], "halves": halves,
                                        "x2_S1": x1["sr"], "pos_both": bool(a1["sr"] is not None and a2["sr"] is not None and a1["sr"] > 0 and a2["sr"] > 0),
                                        "conds": conds, "promising": all(conds[k] for k in ("a", "b", "c", "d", "e"))}
            ent["breakeven_lambda_S1"] = breakeven_lambda(s1[c0], s1[base])
            ent["breakeven_lambda_S2"] = breakeven_lambda(s2[c0], s2[base])
            out.append(ent)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=EVID / "maker_rescore_dev.json")
    a = ap.parse_args()
    rows = collect()
    summary = {sc: {"n": len(rows), "S1_pos": sum(1 for r in rows if (r["scenarios"][sc]["S1"] or -1) > 0),
                    "pos_both": sum(r["scenarios"][sc]["pos_both"] for r in rows),
                    "promising": sum(r["scenarios"][sc]["promising"] for r in rows)} for sc in SCENARIOS}
    res = {"version": "maker-rescore-dev-v1", "prereg_sha256": sha(PREREG), "script_sha256": sha(Path(__file__)),
           "inputs_sha256": {p.name: sha(p) for _, p, *_ in SOURCES}, "scenarios_def": SCENARIOS,
           "focus": FOCUS, "summary": summary, "rows": rows}
    a.out.write_text(json.dumps(res, indent=1))
    print("validation (published vs linear prediction):")
    for r in rows:
        for tag, v in r["validation"].items():
            print(f"  {r['id']:28s} {tag:8s} S1 pred {v['S1_pred']:+.2f} pub {v['S1_pub']:+.2f} | S2 pred {v['S2_pred']:+.2f} pub {v['S2_pub']:+.2f}")
    print()
    hdr = f"{'candidate':28s} {'base S1/S2':>12s} " + " ".join(f"{sc[:5]:>5s}S1 {sc[:5]:>5s}S2" for sc in SCENARIOS) + "  be_lam"
    print(hdr)
    for r in rows:
        be = r["breakeven_lambda_S1"]
        print(f"{r['id']:28s} {r['base_s1']:+5.2f}/{r['base_s2']:+5.2f} " + " ".join(
            f"{r['scenarios'][sc]['S1']:+9.2f} {r['scenarios'][sc]['S2']:+9.2f}" for sc in SCENARIOS) + f"  {be if be is None else round(be,2)}")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
