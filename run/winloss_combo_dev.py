#!/usr/bin/env python3
"""High-win-rate group vs low-win-rate / small-loss group: profile, correlation and combination, DEV only (ADR 0092).

Fixed in ``run/audit/evidence_winloss_combo_20261118/prereg_winloss_combo_v1.json`` (commit b7ac219, before any
daily series of this study was computed).

Stages (each caches its output in --work so the analysis can be re-run cheaply):

* ``dca``  : ADR 0088 bases B1..B4 (no stop, 5x) and the ADR 0089 avg-close stop x .06/.10 of the same bases on
             the 17-alt daily engine (``dca_stop_dev.simulate_stop``, stop none == ADR 0088 exactly).
             Daily net series (measured cost), cycle nets, and the 17-alt equal-weight market return.
* ``r9``   : the 33 R9 entry-bar candidates (ADR 0044 path: ``evalue_v3_block.events_of``), symbols restricted to
             the 7 liquid ones in the project archive (BNB XRP SOL ADA DOGE LTC LINK), public-estimate alt cost
             ``estimate_v3_alts``, trade net booked on the entry day, basket average.
* ``analyze``: group membership, per-trade profile, correlations, regime cells, gate, and the combination grid.

Never reads 2020, 2023-09-19..2024-06-18 or 2025-10-21 onwards (both loaders assert this).  No orders, no NEW2.

    python3 run/dca_extreme_prepare.py --zip <alts zip> --out <dca dir>
    python3 run/winloss_combo_dev.py --stage dca --dca-data <dca dir> --work <w>
    python3 run/winloss_combo_dev.py --stage r9 --r9-data <dir with klines/ funding/> --work <w>
    python3 run/winloss_combo_dev.py --stage analyze --work <w>
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))

VERSION = "winloss-combo-dev-v1"
EVID = HERE / "audit" / "evidence_winloss_combo_20261118"
PREREG = EVID / "prereg_winloss_combo_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"
R9_CAND = HERE / "audit" / "alt_cost_measured_20261027" / "r9_candidates_with_specs.json"
R9_COST = HERE / "audit" / "alt_cost_measured_20261027" / "estimate_v3_alts.json"
R9_SYMBOLS = ["BNBUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT", "LTCUSDT", "LINKUSDT"]
DAY_MS = 86_400_000
BASES = {"B1": (30, 0.04, 4), "B2": (30, 0.08, 2), "B3": (90, 0.04, 4), "B4": (90, 0.08, 2)}
STOP_X = (0.06, 0.10)
B_HIT, B_PAYOFF, MIN_N = 0.45, 1.5, 30
GATE_CORR = 0.30
PENDING_ADR0091 = 32           # ADR 0091 (PR #91) trials, added only while its line is not yet in the trials file
CONTEXT_ID = "ALT17-1d-winloss-combo-v1"


def dca_id(b: str, stop: str, x: float | None = None) -> str:
    n, s, k = BASES[b]
    return f"N{n}_s{s:.2f}_K{k}_{stop}" + (f"{x:.2f}" if x is not None else "")


# ---------------------------------------------------------------------------- stage dca

def stage_dca(data: Path, work: Path) -> None:
    import broad_universe_dev as bu
    import dca_extreme_dev as base
    import dca_stop_dev as ds
    base.simulate = ds.simulate_stop
    cfgs = []
    for b, (n, s, k) in BASES.items():
        cfgs.append({"id": dca_id(b, "none"), "base": b, "N": n, "s": s, "K": k, "stop": "none", "x": None})
        for x in STOP_X:
            cfgs.append({"id": dca_id(b, "avg_close", x), "base": b, "N": n, "s": s, "K": k, "stop": "avg_close", "x": x})
    costs = {"measured": bu.load_cost_record(base.COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(base.CALIBRATION)}
    out = {"version": VERSION, "stage": "dca", "segments": {}}
    for seg in base.SEGMENTS:
        r = base.run_segment(data, seg, cfgs, costs)
        sp = r["span"]
        piv = base.load_ohlcv(data, seg)
        opn = piv["open"]
        ret = (opn.shift(-1) / opn - 1.0).values
        mkt_full = np.array([float(np.nanmean(x)) if np.isfinite(x).any() else 0.0 for x in ret])
        days_full = [int(d) for d in opn.index.values]
        segout = {"days": r["days"], "days_full": days_full, "mkt_full": mkt_full.tolist(), "configs": {}}
        for cfg in cfgs:
            run = r["runs"][cfg["id"]]
            net = sum(run["gross"][sd] + run["cost"]["measured"][sd] for sd in base.SIDES)[sp]
            cyc = [float((c["pp"] + c["fund"] - c["cost"]) / c["maxnot"]) for c in run["cycles"]]
            reasons = [c["exit"][1] for c in run["cycles"]]
            tail = base.tail_stats(net, run["adv"][sp], run["expo"][sp])
            segout["configs"][cfg["id"]] = {"params": cfg, "net": net.tolist(), "cycle_net": cyc, "exit_reasons": reasons,
                                            "envelope_max_dd": tail["envelope_max_dd"]}
        out["segments"][seg] = segout
    (work / "dca_series.json").write_text(json.dumps(out))


# ---------------------------------------------------------------------------- stage r9

def _r9_one(args) -> dict:
    cid, spec, data_dir = args
    import evalue_v3_block as B
    import forward_v2_entry_bar_dev as F
    import strong_search_dev as T
    from costs_bridge import CostContext
    from strategy_spec import canonicalize_spec
    T.SEGMENTS = F.segments_for(R9_SYMBOLS)
    data = {}
    for s in ("S1", "S2"):
        for sym in T.SEGMENTS[s]["symbols"]:
            T.assert_allowed(sym, T.SEGMENTS[s]["read_start"], T.SEGMENTS[s]["read_end"])
        data[s] = T.load_segment(s, Path(data_dir))
    cost_ctx = CostContext.from_public_estimate(json.loads(R9_COST.read_text()))
    spec = canonicalize_spec(spec)
    out = {"strategy_id": cid, "direction": spec["direction"], "exit": spec["exit"]}
    for seg, seg_data in data.items():
        d0, d1 = T.day_grid(T.SEGMENTS[seg])
        ser = np.zeros(d1 - d0 + 1)
        trades = []
        for sym, entry in seg_data.items():
            for sd in entry["pieces"]:
                for e in B.events_of(spec, sd.bars, sd.funding, sym, sd.entry_lo, cost_ctx):
                    de = e["entry_ts"] // DAY_MS
                    if d0 <= de <= d1:
                        ser[de - d0] += e["net_bp"]
                        trades.append(float(e["net_bp"]))
        out[seg] = {"day0": int(d0), "net_bp": (ser / len(seg_data)).tolist(), "trade_net_bp": trades,
                    "n_symbols": len(seg_data)}
    return out


def stage_r9(data: Path, work: Path, workers: int) -> None:
    cands = json.loads(R9_CAND.read_text())
    jobs = [(c["strategy_id"], c["spec"], str(data)) for c in cands]
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(_r9_one, jobs))
    (work / "r9_series.json").write_text(json.dumps({"version": VERSION, "stage": "r9", "symbols": R9_SYMBOLS,
                                                     "candidates": res}))


# ---------------------------------------------------------------------------- analysis helpers

def sr_ann(x: np.ndarray) -> float:
    x = np.asarray(x, float)
    sd = x.std(ddof=1) if len(x) > 1 else 0.0
    return float(x.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0


def series_stats(x: np.ndarray) -> dict:
    x = np.asarray(x, float)
    cum = np.cumsum(x)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:] - cum)) if len(x) else 0.0
    r20 = np.convolve(x, np.ones(20), "valid") if len(x) >= 20 else x
    active = x[x != 0]
    return {"n_days": int(len(x)), "sr_ann": sr_ann(x), "ann_return_pct": float(x.mean() * 365 * 100),
            "close_max_dd_pct": dd * 100, "worst_20d_pct": float(r20.min() * 100) if len(r20) else 0.0,
            "daily_hit_rate_active": float((active > 0).mean()) if len(active) else None}


def trade_profile(nets_frac: list[float]) -> dict:
    a = np.asarray(nets_frac, float)
    if len(a) == 0:
        return {"n": 0}
    w, lo = a[a > 0], a[a <= 0]
    aw = float(w.mean() * 1e4) if len(w) else None
    al = float(lo.mean() * 1e4) if len(lo) else None
    return {"n": int(len(a)), "hit_rate": float((a > 0).mean()), "mean_net_bp": float(a.mean() * 1e4),
            "avg_win_bp": aw, "avg_loss_bp": al, "payoff": (aw / abs(al)) if (aw is not None and al) else None,
            "worst_bp": float(a.min() * 1e4), "p05_bp": float(np.quantile(a, 0.05) * 1e4),
            "breakeven_hit_rate": (abs(al) / (aw + abs(al))) if (aw is not None and al) else None}


def corr(a: np.ndarray, b: np.ndarray) -> dict:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 30 or a.std() == 0 or b.std() == 0:
        return {"pearson": None, "spearman": None, "n": int(len(a))}
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return {"pearson": float(np.corrcoef(a, b)[0, 1]), "spearman": float(np.corrcoef(ra, rb)[0, 1]), "n": int(len(a))}


def trailing_regime(days_full: list[int], mkt_full: list[float], days: list[int], look: int) -> np.ndarray:
    """+1 if the market's cumulative open-to-open return over the `look` days before day d is > 0 (known at d's open)."""
    idx = {d: i for i, d in enumerate(days_full)}
    m = np.nan_to_num(np.asarray(mkt_full, float))
    cs = np.concatenate([[0.0], np.cumsum(m)])
    out = np.zeros(len(days))
    for j, d in enumerate(days):
        i = idx[d]
        lo = max(0, i - look)
        out[j] = 1.0 if (cs[i] - cs[lo]) > 0 else -1.0
    return out


def expected_max_sr_ann(n: int, years: float) -> float:
    from broad_universe_dev import expected_max_sr
    return float(expected_max_sr(n) / math.sqrt(years))


def prior_trials() -> tuple[int, int]:
    lines = [json.loads(x) for x in TRIALS.read_text().splitlines() if x.strip()]
    tot = sum(x.get("dev_evaluated", 0) for x in lines if x.get("context_id") != CONTEXT_ID)
    pending = 0 if any("(ADR 0091)" in x.get("search", "") for x in lines) else PENDING_ADR0091
    return tot, pending


# ---------------------------------------------------------------------------- analyze

def analyze(work: Path, out_path: Path) -> dict:
    dca = json.loads((work / "dca_series.json").read_text())
    r9 = json.loads((work / "r9_series.json").read_text())
    res = {"version": VERSION, "prereg": PREREG.name, "segments": {}, "members": {}, "profiles": {}}

    # common day grid per segment = DCA span days that the R9 grid also covers
    grids = {}
    for seg in ("S1", "S2"):
        dd = [d // DAY_MS for d in dca["segments"][seg]["days"]]
        r0 = r9["candidates"][0][seg]["day0"]
        rn = len(r9["candidates"][0][seg]["net_bp"])
        keep = [i for i, d in enumerate(dd) if r0 <= d < r0 + rn]
        grids[seg] = {"dca_idx": np.array(keep), "r9_idx": np.array([dd[i] - r0 for i in keep]),
                      "days_ms": [dca["segments"][seg]["days"][i] for i in keep]}
        res["segments"][seg] = {"first_day": grids[seg]["days_ms"][0], "last_day": grids[seg]["days_ms"][-1],
                                "n_days": len(keep)}

    # profiles of every component
    for cid, c in dca["segments"]["S1"]["configs"].items():
        res["profiles"][cid] = {"family": "scale-in (ADR 0088/0089)", **{
            seg: {**trade_profile(dca["segments"][seg]["configs"][cid]["cycle_net"]),
                  "liquidated_share": (lambda r: r.count("LIQUIDATED") / len(r) if r else None)(dca["segments"][seg]["configs"][cid]["exit_reasons"]),
                  "series": series_stats(np.asarray(dca["segments"][seg]["configs"][cid]["net"])[grids[seg]["dca_idx"]]),
                  "envelope_max_dd": dca["segments"][seg]["configs"][cid]["envelope_max_dd"]}
            for seg in ("S1", "S2")}}
    for c in r9["candidates"]:
        res["profiles"][c["strategy_id"]] = {"family": "R9 entry-bar (7 symbols)", "direction": c["direction"], "exit": c["exit"], **{
            seg: {**trade_profile([x / 1e4 for x in c[seg]["trade_net_bp"]]),
                  "series": series_stats(np.asarray(c[seg]["net_bp"])[grids[seg]["r9_idx"]] / 1e4)}
            for seg in ("S1", "S2")}}

    def is_b(p):
        s1 = p["S1"]
        return s1.get("n", 0) >= MIN_N and s1["hit_rate"] <= B_HIT and (s1.get("payoff") or 0) >= B_PAYOFF

    members = {
        "A": [dca_id(b, "none") for b in BASES],
        "B_DS": [dca_id(b, "avg_close", x) for b in BASES for x in STOP_X if is_b(res["profiles"][dca_id(b, "avg_close", x)])],
        "B_R9": [c["strategy_id"] for c in r9["candidates"] if is_b(res["profiles"][c["strategy_id"]])],
    }
    res["members"] = {k: v for k, v in members.items()}
    res["A_member_s1_hit"] = {m: res["profiles"][m]["S1"]["hit_rate"] for m in members["A"]}

    def comp_series(name: str, seg: str) -> np.ndarray:
        if name in dca["segments"][seg]["configs"]:
            return np.asarray(dca["segments"][seg]["configs"][name]["net"])[grids[seg]["dca_idx"]]
        c = next(x for x in r9["candidates"] if x["strategy_id"] == name)
        return np.asarray(c[seg]["net_bp"])[grids[seg]["r9_idx"]] / 1e4

    bundles = {}
    for g, ms in members.items():
        if g != "A" and len(ms) < 2:
            continue
        bundles[g] = {seg: np.mean([comp_series(m, seg) for m in ms], axis=0) for seg in ("S1", "S2")}
    res["bundles_dropped"] = [g for g, ms in members.items() if g != "A" and len(ms) < 2]

    # regimes, correlations, sign cells
    regs = {}
    for seg in ("S1", "S2"):
        sd = dca["segments"][seg]
        regs[seg] = {look: trailing_regime(sd["days_full"], sd["mkt_full"], grids[seg]["days_ms"], look) for look in (30, 90)}
    years = {seg: res["segments"][seg]["n_days"] / 365.0 for seg in ("S1", "S2")}
    res["bundle_stats"] = {g: {seg: series_stats(v[seg]) for seg in ("S1", "S2")} for g, v in bundles.items()}
    cells = {}
    for g, v in bundles.items():
        cells[g] = {}
        for seg in ("S1", "S2"):
            for look in (30, 90):
                r = regs[seg][look]
                for name, mask in (("up", r > 0), ("down", r < 0)):
                    x = v[seg][mask]
                    cells[g][f"{seg}_{name}{look}"] = {"days": int(mask.sum()), "mean_bp_day": float(x.mean() * 1e4) if len(x) else None,
                                                       "sr_ann": sr_ann(x) if len(x) > 30 else None}
        yr = np.array([int(np.datetime64(int(t), "ms").astype("datetime64[Y]").astype(int) + 1970) for t in grids["S1"]["days_ms"]])
        x = v["S1"][yr == 2022]
        cells[g]["S1_2022"] = {"days": int(len(x)), "mean_bp_day": float(x.mean() * 1e4) if len(x) else None, "sr_ann": sr_ann(x)}
    res["regime_cells"] = cells
    correlations = {}
    for g in bundles:
        if g == "A":
            continue
        correlations[g] = {}
        for seg in ("S1", "S2"):
            a, b = bundles["A"][seg], bundles[g][seg]
            correlations[g][seg] = corr(a, b)
            for look in (30, 90):
                r = regs[seg][look]
                correlations[g][f"{seg}_up{look}"] = corr(a[r > 0], b[r > 0])
                correlations[g][f"{seg}_down{look}"] = corr(a[r < 0], b[r < 0])
    res["correlations"] = correlations
    gate = {g: (c["S1"]["pearson"] is not None and c["S1"]["pearson"] <= GATE_CORR) for g, c in correlations.items()}
    res["gate"] = {"rule": f"S1 Pearson(A, B) <= {GATE_CORR}", "pass": gate}

    # combination grid
    rows = {}
    scale = {g: (bundles["A"]["S1"].std(ddof=1) / bundles[g]["S1"].std(ddof=1)) for g in bundles}
    res["vol_scale_from_S1"] = scale

    def scaled(g, seg):
        return bundles[g][seg] * scale[g]

    def trailing_vol(x: np.ndarray, n: int = 60) -> np.ndarray:
        out = np.full(len(x), np.nan)
        for i in range(len(x)):
            if i >= 20:
                out[i] = x[max(0, i - n):i].std(ddof=1)
        return out

    for g, ok in gate.items():
        if not ok:
            continue
        for seg in ("S1", "S2"):
            A, Bx = scaled("A", seg), scaled(g, seg)
            r90, r30 = regs[seg][90], regs[seg][30]
            defs = {
                f"{g}|static_wA0.25": 0.25 * A + 0.75 * Bx,
                f"{g}|static_wA0.50": 0.5 * A + 0.5 * Bx,
                f"{g}|static_wA0.75": 0.75 * A + 0.25 * Bx,
            }
            va, vb = trailing_vol(A), trailing_vol(Bx)
            wa = np.where(np.isfinite(va) & np.isfinite(vb) & (va > 0) & (vb > 0), (1 / va) / (1 / np.where(va > 0, va, 1) + 1 / np.where(vb > 0, vb, 1)), 0.5)
            defs[f"{g}|invvol60"] = wa * A + (1 - wa) * Bx
            for look, r in ((90, r90), (30, r30)):
                defs[f"{g}|switch{look}_upA_downB"] = np.where(r > 0, A, Bx)
                defs[f"{g}|switch{look}_upB_downA"] = np.where(r > 0, Bx, A)
            defs[f"{g}|tilt90_upA_downB"] = np.where(r90 > 0, 0.75 * A + 0.25 * Bx, 0.25 * A + 0.75 * Bx)
            defs[f"{g}|tilt90_upB_downA"] = np.where(r90 > 0, 0.25 * A + 0.75 * Bx, 0.75 * A + 0.25 * Bx)
            for k, v in defs.items():
                rows.setdefault(k, {"components": ["A", g]})[seg] = series_stats(v)
    if gate.get("B_DS"):
        for b in BASES:
            for x in STOP_X:
                k = f"exitmix|{b}|none+avg_close{x:.2f}"
                for seg in ("S1", "S2"):
                    v = 0.5 * comp_series(dca_id(b, "none"), seg) + 0.5 * comp_series(dca_id(b, "avg_close", x), seg)
                    rows.setdefault(k, {"components": [dca_id(b, "none"), dca_id(b, "avg_close", x)]})[seg] = series_stats(v)
    # component references (not counted)
    ref = {}
    for g in bundles:
        ref[g] = {seg: series_stats(scaled(g, seg)) for seg in ("S1", "S2")}
    for cid in res["profiles"]:
        if cid in dca["segments"]["S1"]["configs"]:
            ref[cid] = {seg: res["profiles"][cid][seg]["series"] for seg in ("S1", "S2")}
    res["component_refs"] = ref
    for k, r in rows.items():
        a, b = r["components"]
        for seg in ("S1", "S2"):
            best = max(ref[a][seg]["sr_ann"], ref[b][seg]["sr_ann"])
            worst_dd = max(ref[a][seg]["close_max_dd_pct"], ref[b][seg]["close_max_dd_pct"])
            r[f"{seg}_beats_both"] = r[seg]["sr_ann"] > best
            r[f"{seg}_dd_not_worse"] = r[seg]["close_max_dd_pct"] <= worst_dd + 1e-12
    res["grid"] = rows
    n_grid = len(rows)
    prior, pending = prior_trials()
    res["trials"] = {"this_grid": n_grid, "prior_file_sum": prior, "pending_unmerged_adr0091": pending,
                     "cumulative": prior + pending + n_grid}
    if n_grid:
        null_g = expected_max_sr_ann(n_grid, years["S1"])
        null_c = expected_max_sr_ann(prior + pending + n_grid, years["S1"])
        best_k = max(rows, key=lambda k: rows[k]["S1"]["sr_ann"])
        b = rows[best_k]
        crit = {"a_S1_gt_null_grid": b["S1"]["sr_ann"] > null_g, "a_S1_gt_null_cumulative": b["S1"]["sr_ann"] > null_c,
                "b_S2_gt_0.5": b["S2"]["sr_ann"] > 0.5, "c_beats_both_S1": b["S1_beats_both"], "c_beats_both_S2": b["S2_beats_both"],
                "d_dd_not_worse_S1": b["S1_dd_not_worse"], "d_dd_not_worse_S2": b["S2_dd_not_worse"]}
        res["selection"] = {"chosen": best_k, "S1": b["S1"], "S2": b["S2"], "null_max_sr_ann": {f"N={n_grid}": null_g, f"N={prior + pending + n_grid}": null_c},
                            "criteria": crit, "verdict": "USEFUL" if all(crit.values()) else "NOT_USEFUL",
                            "rows_beating_both_S1": sum(r["S1_beats_both"] for r in rows.values()),
                            "rows_beating_both_S2": sum(r["S2_beats_both"] for r in rows.values()),
                            "rows_beating_both_S1_and_S2": sum(r["S1_beats_both"] and r["S2_beats_both"] for r in rows.values())}
    else:
        res["selection"] = {"verdict": "NOT_RUN (gate failed for every B bundle)"}
    out_path.write_text(json.dumps(res, indent=1, default=float))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("dca", "r9", "analyze"), required=True)
    ap.add_argument("--dca-data", type=Path)
    ap.add_argument("--r9-data", type=Path)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--out", type=Path, default=EVID / "winloss_combo_dev.json")
    a = ap.parse_args(argv)
    a.work.mkdir(parents=True, exist_ok=True)
    if a.stage == "dca":
        stage_dca(a.dca_data, a.work)
    elif a.stage == "r9":
        stage_r9(a.r9_data, a.work, a.workers)
    else:
        r = analyze(a.work, a.out)
        print(json.dumps({k: r[k] for k in ("members", "gate", "correlations", "trials", "selection")}, indent=1, default=float)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
