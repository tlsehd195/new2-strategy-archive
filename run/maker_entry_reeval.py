#!/usr/bin/env python3
"""Re-evaluate the DEV candidates with limit-order (maker) entry costs, DEV only (ADR 0049).

Rows and verdict rule fixed before this ran, in ``prereg_maker_entry_v1.json`` and its addendum.
The matched ratio r_X = max over symbols of C_X / taker_side (same simulation, QUEUE, 120 s, 0.5 s latency)
scales the PUBLISHED measured side cost, so a limit order is compared with a market order inside one framework
and the project's conservative cost convention is not loosened by a less conservative method.

    python3 run/maker_entry_reeval.py fast --data-dir <alts dir>   # the 20 configs of ADR 0045
    python3 run/maker_entry_reeval.py r9   --data-dir <merged dir> # the 33 R9 candidates of ADR 0038
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))

EVID = HERE / "audit" / "evidence_maker_entry_20261028"
ESTIMATE = EVID / "maker_entry_estimate.json"
LATENCY = "0.5s"
CELL = "QUEUE|120s"
TAKER_FEE_BP = 5.0
R9_ROWS = ("R_B_matched", "R_B_matched_stress", "R_B_raw")


def ratios(est: dict) -> dict:
    """Matched ratios per scenario row from the estimate: max over symbols of C_X / taker side."""
    per = est["by_latency"][LATENCY]["per_symbol"]
    out = {}
    for sl in ("pooled", "stress"):
        for fee in (2, 4):
            key = f"{sl}|fee{fee}"
            chase, aband, raw_c, raw_t = [], [], [], []
            for s, v in per.items():
                c = v[CELL][key]
                chase.append(c["C_side_chase_bp"] / c["taker_side_bp"])
                aband.append(c["C_f_abandon_bp"] / c["taker_side_bp"])
                raw_c.append((c["C_side_chase_bp"], c["fill_rate"]))
                raw_t.append(c["taker_side_bp"])
            out[key] = {"r_chase": max(chase), "r_abandon": max(aband),
                        "raw_chase_worst_bp": max(x[0] for x in raw_c), "raw_taker_worst_bp": max(raw_t),
                        "fill_rate_at_worst": [x[1] for x in raw_c][int(max(range(len(raw_c)), key=lambda i: raw_c[i][0]))]}
    return out


def row_factors(r: dict) -> dict:
    """Entry factor and exit factor on the published side cost, per row."""
    p, s, p4 = r["pooled|fee2"], r["stress|fee2"], r["pooled|fee4"]
    return {
        "published_measured": (1.0, 1.0),
        "R_A_matched": (p["r_abandon"], 1.0),
        "R_B_matched": (p["r_chase"], p["r_chase"]),
        "R_C_matched": (p["r_chase"], 1.0),
        "R_B_matched_stress": (s["r_chase"], s["r_chase"]),
        "R_B_matched_fee4": (p4["r_chase"], p4["r_chase"]),
    }


# fast_short_dev row name -> reeval row name (the tool is reused unchanged, only its cost function is replaced)
FAST_MAP = {"measured": "R_B_matched", "measured_x2": "R_A_matched", "volume_scaled": "R_B_matched_stress",
            "assumed_public_v1": "R_C_matched", "cost0": "cost0", "maker_both": "R_B_matched_fee4",
            "maker_entry_taker_exit": "R_mkt_own", "published_measured": "published_measured", "R_B_raw": "R_B_raw"}


def run_fast(a, est: dict) -> int:
    import fast_short_dev as FS
    r = ratios(est)
    fac = row_factors(r)
    raw = r["pooled|fee2"]
    FS.COST_ROWS = tuple(FAST_MAP)

    def make_cost_fns(costs: dict) -> dict:
        meas = costs["measured"]
        bu = FS.bu

        def one(row: str, b: float, q15: float, entry: bool) -> float:
            name = FAST_MAP[row]
            if name == "cost0":
                return 0.0
            if name == "R_B_raw":
                return bu.side_cost(b, {"fixed": raw["raw_chase_worst_bp"] / 1e4, "imp": 0.0, "maxp": meas["maxp"]}, "")
            if name == "R_mkt_own":
                return bu.side_cost(b, {"fixed": raw["raw_taker_worst_bp"] / 1e4, "imp": 0.0, "maxp": meas["maxp"]}, "")
            f = fac[name][0 if entry else 1]
            return f * bu.side_cost(b, meas, "")
        return {"one": one, "maxp": meas["maxp"]}

    FS.make_cost_fns = make_cost_fns
    tmp = a.out.with_suffix(".raw.json")
    rc = FS.main(["--data-dir", str(a.data_dir), "--out", str(tmp)])
    d = json.loads(tmp.read_text())
    tmp.unlink()

    def rename(o):
        if isinstance(o, dict):
            return {FAST_MAP.get(k, k): rename(v) for k, v in o.items()}
        if isinstance(o, list):
            return [rename(x) for x in o]
        return o
    d = rename(d)
    d["row_note"] = ("the unchanged fast_short_dev.py was run with its cost function replaced; keys in this file are renamed "
                     "to the ADR 0049 row names; 'flag' uses R_B_matched in place of 'measured' and R_B_matched_stress in "
                     "place of 'volume_scaled'; null max SR counts and DEV pass rules are the tool's own")
    d["matched_ratios"] = r
    d["estimate_sha256"] = FS.bu.sha(ESTIMATE)
    d["reeval_script_sha256"] = FS.bu.sha(Path(__file__))
    a.out.write_text(json.dumps(d, indent=1, default=float))
    return rc


_R9: dict = {}


def _score_idx(i: int) -> dict:
    g = _R9
    c = g["cand"][i]
    spec = g["canon"](c["spec"])
    row = {"strategy_id": c["strategy_id"], "rank": c.get("rank")}
    for n, cc in g["scen"].items():
        row[n] = {s: g["I"].score(spec, g["data"][s], s, cc) for s in ("S1", "S2")}
        row[n]["entry_bar_pass"] = g["F"].entry_bar_pass({"S1": row[n]["S1"]})
    print(row["strategy_id"], {n: round(row[n]["S1"]["sr_ann"], 2) for n in g["scen"]}, flush=True)
    return row


def run_r9(a, est: dict) -> int:
    import forward_v2_entry_bar_dev as F
    import iw1_statistic_dev as I
    import strong_search_dev as T
    from costs_bridge import CostContext
    from strategy_spec import canonicalize_spec
    r = ratios(est)
    fac = row_factors(r)
    raw = r["pooled|fee2"]
    base = json.loads((HERE / "audit" / "alt_cost_measured_20261027" / "estimate_v3_alts.json").read_text())["params"]
    cand = json.loads((HERE / "audit" / "alt_cost_measured_20261027" / "r9_candidates_with_specs.json").read_text())
    symbols = json.loads((HERE / "audit" / "alt_cost_measured_20261027" / "entry_bar_liquid9_measured.json").read_text())["symbols"]
    T.SEGMENTS = F.segments_for(symbols)
    for seg in T.SEGMENTS.values():
        for s in seg["symbols"]:
            T.assert_allowed(s, seg["read_start"], seg["read_end"])
    data = {s: T.load_segment(s, a.data_dir) for s in ("S1", "S2")}

    def ctx(scale_e: float, scale_x: float, label: str):
        f = (scale_e + scale_x) / 2.0    # symmetric engine: the round trip is what the entry and exit scales add up to
        p = {k: (v * f if k != "max_participation" else v) for k, v in base.items()}
        return CostContext.from_params(p, version=f"maker-matched:{label}")

    def fixed(bp: float, label: str):
        p = {"fee_rate": 0.0, "half_spread": bp / 1e4, "impact_coefficient": 0.0, "max_participation": base["max_participation"]}
        return CostContext.from_params(p, version=f"maker-own:{label}")

    scen = {n: ctx(*fac[n], n) for n in fac}
    scen["R_B_raw"] = fixed(raw["raw_chase_worst_bp"], "R_B_raw")
    scen["R_mkt_own"] = fixed(raw["raw_taker_worst_bp"], "R_mkt_own")
    # scoring one candidate under one cost takes about a minute: only the rows that decide or bound the verdict are scored
    scen = {n: scen[n] for n in R9_ROWS}
    out = {"rows": {n: {"cost_identity": c.identity()} for n, c in scen.items()}, "matched_ratios": r,
           "entry_bar_s1_sr": F.ENTRY_BAR, "symbols": symbols, "candidates": [],
           "rows_not_scored": "R_A_matched, R_C_matched, R_B_matched_fee4, R_mkt_own and published_measured (published: see entry_bar_liquid9_measured.json, ADR 0038)"}

    global _R9
    _R9 = {"cand": cand, "scen": scen, "data": data, "I": I, "F": F, "canon": canonicalize_spec}
    import multiprocessing as mp
    with mp.get_context("fork").Pool(a.jobs) as pool:
        out["candidates"] = pool.map(_score_idx, range(len(cand)), chunksize=1)
    out["pass_counts_s1_ge_1"] = {n: sum(1 for x in out["candidates"] if x[n]["entry_bar_pass"]) for n in scen}
    import broad_universe_dev as bu
    out["estimate_sha256"] = bu.sha(ESTIMATE)
    out["reeval_script_sha256"] = bu.sha(Path(__file__))
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("pass counts (S1 SR >= 1.0)", out["pass_counts_s1_ge_1"])
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("which", choices=("fast", "r9"))
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    a.out = a.out or EVID / ("reeval_fast_short.json" if a.which == "fast" else "reeval_r9_entry_bar.json")
    est = json.loads(ESTIMATE.read_text())
    return run_fast(a, est) if a.which == "fast" else run_r9(a, est)


if __name__ == "__main__":
    sys.exit(main())
