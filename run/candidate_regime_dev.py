#!/usr/bin/env python3
"""Per-regime scorecard of the R9 candidates and five bundles, DEV only (ADR 0044).

Question (동동, 2026-10-28, "1 2 3 다 해봐", item 2): how do the current DEV candidates do in bull / side / bear markets?
Regimes, bundles, flags and attribution rule are fixed in
run/audit/evidence_candidate_regime_20261028/prereg_candidate_regime_v1.json (committed before any result).
Scores each candidate exactly like ``forward_v2_entry_bar_dev`` / ``evalue_v3_block.dev_bundle_series`` (engine signal, stop and event
code, basket-average daily net bp) but keeps the events, so each trade can be booked on its entry day (primary) or exit day.
Reads only the DEV stretches S1 / S2 of ``strong_search_dev`` (``assert_allowed`` refuses everything else).  Diagnostic for elimination;
not evidence of passing, and DEV was used to select the candidates, so not an independent test.

    python3 run/candidate_regime_dev.py --data-dir <klines/ funding/> --out <dir>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))
import evalue_v3_block as B  # noqa: E402
import forward_v2_entry_bar_dev as F  # noqa: E402
import strong_search_dev as T  # noqa: E402
from costs_bridge import CostContext  # noqa: E402
from strategy_spec import canonicalize_spec  # noqa: E402

EV = HERE / "audit" / "evidence_candidate_regime_20261028"
CAND = HERE / "audit" / "alt_cost_measured_20261027" / "r9_candidates_with_specs.json"
COST = HERE / "audit" / "alt_cost_measured_20261027" / "estimate_v3_alts.json"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT", "LTCUSDT", "LINKUSDT"]
MIN_DAYS = 60
TRAIL_DAYS, TRAIL_BAND, DD_LEVEL = 90, 0.20, 0.75
KINDS = ("net_entry", "net_exit", "nocost_entry")


def load_all(data_dir: Path) -> dict:
    T.SEGMENTS = F.segments_for(SYMBOLS)
    for seg in T.SEGMENTS.values():
        for s in seg["symbols"]:
            T.assert_allowed(s, seg["read_start"], seg["read_end"])
    return {s: T.load_segment(s, data_dir) for s in ("S1", "S2")}


def score_candidate(args) -> dict:
    """Basket-average daily series per segment: net by entry day, net by exit day, cost-free by entry day, trade list."""
    cid, spec, data_dir = args
    data = load_all(Path(data_dir))
    cost_ctx = CostContext.from_public_estimate(json.loads(COST.read_text()))
    spec = canonicalize_spec(spec)
    out = {"strategy_id": cid, "direction": spec["direction"]}
    for seg_name, seg_data in data.items():
        d0, d1 = T.day_grid(T.SEGMENTS[seg_name])
        n = d1 - d0 + 1
        ser = {k: np.zeros(n) for k in KINDS}
        cnt, wins = np.zeros(n), np.zeros(n)
        for sym, entry in seg_data.items():
            for sd in entry["pieces"]:
                for e in B.events_of(spec, sd.bars, sd.funding, sym, sd.entry_lo, cost_ctx):
                    de, dx = e["entry_ts"] // T.DAY_MS, e["exit_ts"] // T.DAY_MS
                    if d0 <= de <= d1:
                        ser["net_entry"][de - d0] += e["net_bp"]
                        ser["nocost_entry"][de - d0] += e["net_bp"] + e["cost_bp"]
                        cnt[de - d0] += 1
                        wins[de - d0] += e["net_bp"] > 0
                    if d0 <= dx <= d1:
                        ser["net_exit"][dx - d0] += e["net_bp"]
        k = len(seg_data)
        out[seg_name] = {kk: (v / k).tolist() for kk, v in ser.items()}
        out[seg_name]["trades"] = cnt.tolist()
        out[seg_name]["wins"] = wins.tolist()
    return out


def index_and_regimes(data_dir: Path) -> dict:
    """Equal-weight close-to-close index of the segment's symbols, labels known at each day's open."""
    data = load_all(data_dir)
    res = {}
    for name, seg_data in data.items():
        seg = T.SEGMENTS[name]
        r0 = T.day_ms(seg["read_start"]) // T.DAY_MS
        d0, d1 = T.day_grid(seg)
        close = {}
        for sym, entry in seg_data.items():
            c = np.full(d1 - r0 + 1, np.nan)
            for sd in entry["pieces"]:
                days = sd.ts // T.DAY_MS
                last = np.r_[days[1:] != days[:-1], True]
                for d, px in zip(days[last], sd.c[last]):
                    if r0 <= d <= d1:
                        c[d - r0] = px
            close[sym] = c
        C = np.array(list(close.values()))
        ret = C[:, 1:] / C[:, :-1] - 1.0                       # return of day j+1 (close to close)
        mean_r = np.where(np.isfinite(ret).any(0), np.nansum(ret, 0) / np.maximum(np.isfinite(ret).sum(0), 1), 0.0)
        idx = np.concatenate([[1.0], np.cumprod(1.0 + mean_r)])    # idx[j] = index at close of day r0+j
        n = d1 - r0 + 1
        r1, r2 = np.full(n, "NA", dtype=object), np.full(n, "OK", dtype=object)
        peak = np.maximum.accumulate(idx)
        for j in range(1, n):                                    # label of day j uses closes through day j-1
            p = j - 1
            if p >= TRAIL_DAYS:
                t = idx[p] / idx[p - TRAIL_DAYS] - 1.0
                r1[j] = "BULL" if t >= TRAIL_BAND else "BEAR" if t <= -TRAIL_BAND else "SIDE"
            if idx[p] <= DD_LEVEL * peak[p]:
                r2[j] = "DD"
        res[name] = {"d0": d0, "d1": d1, "r1": r1[d0 - r0:], "r2": r2[d0 - r0:], "mean_r": mean_r[d0 - r0 - 1:],
                     "idx": idx, "r0": r0, "symbols": list(seg_data)}
    return res


def stats(x: np.ndarray, cnt: np.ndarray | None = None, wins: np.ndarray | None = None) -> dict:
    out = {"days": int(len(x))}
    if cnt is not None:
        out["trades"] = int(cnt.sum())
        out["hit_rate"] = float(wins.sum() / cnt.sum()) if cnt.sum() else None
    if len(x) < MIN_DAYS:
        out["note"] = f"insufficient (<{MIN_DAYS} days)"
        out["cum_bp"] = float(x.sum()) if len(x) else 0.0
        return out
    out.update({"mean_bp_day": float(x.mean()), "sr_ann": T.sr_ann(x), "cum_bp": float(x.sum())})
    return out


def nw_t(x: np.ndarray, lag: int = 5) -> float | None:
    n = len(x)
    if n < MIN_DAYS:
        return None
    e = x - x.mean()
    s = float(e @ e)
    for k in range(1, lag + 1):
        s += 2 * (1 - k / (lag + 1)) * float(e[k:] @ e[:-k])
    return float(x.mean() / math.sqrt(s / n / n)) if s > 0 else None


def masks_for(reg: dict) -> dict:
    m = {"ALL": np.ones(len(reg["r1"]), bool)}
    for lab in ("BULL", "SIDE", "BEAR", "NA"):
        m[f"R1_{lab}"] = reg["r1"] == lab
    m["R2_DD"] = reg["r2"] == "DD"
    m["R2_OK"] = reg["r2"] == "OK"
    return m


def scorecard(series: dict, regs: dict) -> dict:
    out = {}
    for seg in ("S1", "S2"):
        masks = masks_for(regs[seg])
        s = series[seg]
        cnt, wins = np.array(s["trades"]), np.array(s["wins"])
        out[seg] = {}
        for kind in KINDS:
            x = np.array(s[kind])
            out[seg][kind] = {rg: stats(x[m], cnt[m] if kind != "net_exit" else None, wins[m] if kind != "net_exit" else None)
                              for rg, m in masks.items()}
        out[seg]["nw_t_net_entry"] = {rg: nw_t(np.array(s["net_entry"])[m]) for rg, m in masks.items()}
    return out


def flags(sc: dict) -> dict:
    def mean(seg, rg):
        return sc[seg]["net_entry"][rg].get("mean_bp_day")

    s1 = {rg: mean("S1", rg) for rg in ("R1_BULL", "R1_SIDE", "R1_BEAR", "R2_DD")}
    cum = {rg: sc["S1"]["net_entry"][rg]["cum_bp"] for rg in ("R1_BULL", "R1_SIDE", "R1_BEAR")}
    tot = sum(cum.values())
    best = max(cum, key=cum.get)
    same = {}
    for rg in ("R1_BULL", "R1_SIDE", "R1_BEAR", "R2_DD"):
        a, b = s1[rg], mean("S2", rg)
        same[rg] = None if a is None or b is None else bool((a > 0) == (b > 0))
    return {"REGIME_ALL3_POS": all(s1[r] is not None and s1[r] > 0 for r in ("R1_BULL", "R1_SIDE", "R1_BEAR")),
            "BEAR_NEG": s1["R1_BEAR"] is None or s1["R1_BEAR"] <= 0,
            "DD_NEG": s1["R2_DD"] is None or s1["R2_DD"] <= 0,
            "ONE_REGIME": bool(tot > 0 and cum[best] > tot),
            "best_regime_S1": best, "S2_SAME_SIGN": same}


def combine(rows: list[dict]) -> dict:
    out = {}
    for seg in ("S1", "S2"):
        out[seg] = {k: np.mean([r[seg][k] for r in rows], axis=0).tolist() for k in KINDS}
        out[seg]["trades"] = np.sum([r[seg]["trades"] for r in rows], axis=0).tolist()
        out[seg]["wins"] = np.sum([r[seg]["wins"] for r in rows], axis=0).tolist()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, help="score only the first N candidates (smoke test)")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    cands = json.loads(CAND.read_text())[: a.limit]
    regs = index_and_regimes(a.data_dir)
    with ProcessPoolExecutor(a.workers) as ex:
        rows = list(ex.map(score_candidate, [(c["strategy_id"], c["spec"], str(a.data_dir)) for c in cands]))
    for r, c in zip(rows, cands):
        r["rank"] = c["rank"]
    (a.out / "candidate_daily_series.json").write_text(json.dumps(rows) + "\n")
    s1_sr = {r["strategy_id"]: T.sr_ann(np.array(r["S1"]["net_entry"])) for r in rows}
    by_id = {r["strategy_id"]: r for r in rows}
    top5 = sorted(rows, key=lambda r: -np.nan_to_num(T.sr_ann(np.array(r["S1"]["net_exit"])), nan=-9))[:5]
    bundles = {"B1_first5": rows[:5], "B2_all33": rows, "B3_top5_S1": top5,
               "B4_long_only": [r for r in rows if r["direction"] == "long"], "B5_first20": rows[:20]}
    rep = {"version": "candidate-regime-dev-v1", "prereg": "prereg_candidate_regime_v1.json",
           "cost_record": str(COST.relative_to(HERE.parent)), "symbols": SYMBOLS,
           "regime_days": {seg: {rg: int(m.sum()) for rg, m in masks_for(regs[seg]).items()} for seg in ("S1", "S2")},
           "basket_hold_gross_bp_day": {seg: {rg: float(regs[seg]["mean_r"][: len(m)][m].mean() * 1e4) if m.sum() else None
                                              for rg, m in masks_for(regs[seg]).items()} for seg in ("S1", "S2")},
           "candidates": {}, "bundles": {}}
    for r in rows:
        sc = scorecard(r, regs)
        rep["candidates"][r["strategy_id"]] = {"rank": r["rank"], "direction": r["direction"],
                                               "scorecard": sc, "flags": flags(sc)}
    for name, members in bundles.items():
        sc = scorecard(combine(members), regs)
        rep["bundles"][name] = {"members": [m["strategy_id"] for m in members], "scorecard": sc, "flags": flags(sc)}
    rep["runtime_seconds"] = time.time() - t0
    (a.out / "candidate_regime_dev.json").write_text(json.dumps(rep, indent=1, default=float, ensure_ascii=False) + "\n")
    print(json.dumps({"n": len(rows), "runtime": rep["runtime_seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
