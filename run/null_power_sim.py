#!/usr/bin/env python3
"""Null (no-edge) and planted-edge runs of the DEV search pipeline (ADR 0045).

Question (동동, 2026-10-28, option 4 of the data-shortage options): is the
search pipeline's chance-pass rate and its "expected best by luck" what we
think it is, and how much true edge does it actually catch?  Synthetic data
is a CHECK OF THE INSTRUMENT (error rate, power), never new evidence about a
strategy.

Pipeline under test = ``strong_search_dev.py`` Track A (single-condition
grid + 17 filters on the 40 best distinct rules) and Track B on segment S1
(2021-01..2023-09-18, six IW1-A symbols, the IW1 statistic and cost
``assumed_public_v1``), with the same trial count (~3,850).  Only S1 is read
(the same allowed DEV span the strong-search tool reads; ``assert_allowed``
is inherited), no validation, locked, IW1 or paper time.

Surrogates keep the real |returns| path (volatility clustering, fat tails,
intraday seasonality), volumes, funding and the bar calendar, and replace
only the SIGN of the price moves by coin flips, so no rule can have a true
edge from past prices or volume (martingale-difference null):

- ``day``   one sign per UTC day, common to all symbols (keeps the
            cross-symbol co-movement, keeps within-day autocorrelation);
- ``bar``   one sign per 15m bar, common to all symbols (no autocorrelation
            at all, co-movement kept);
- ``bar_ind`` independent sign per symbol and bar (no co-movement either).

A flipped bar mirrors open/high/low/close offsets from the previous close
and swaps taker-buy with taker-sell volume.  Planted edge: a known rule P
gets a constant extra log-drift of ``delta_bp`` per trade (side-signed,
spread over the holding bars) on its own events.

    python3 run/null_power_sim.py --data-dir <dir with klines/ funding/> --out <dir> \
        --prereg run/audit/evidence_null_power_20261028/prereg_null_power_v1.json --workers 4
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))
import strong_search_dev as S  # noqa: E402

VERSION = "null-power-sim-v1"
PREREG = HERE / "audit" / "evidence_null_power_20261028" / "prereg_null_power_v1.json"
GRID0 = S.day_ms("2021-01-01") // S.BAR_MS


# ---------------------------------------------------------------------------
# surrogate construction
# ---------------------------------------------------------------------------

def offsets(sd) -> tuple:
    """Per-bar log offsets from the previous close: open, high, low, close."""
    pc = np.concatenate([[sd.o[0]], sd.c[:-1]])
    return (np.log(sd.o / pc), np.log(sd.h / pc), np.log(sd.l / pc), np.log(sd.c / pc))


def rebuild(sd, a, hi, lo, cl, tbv):
    """A SymData copy whose prices follow the given offsets (first open kept)."""
    base = math.log(sd.o[0])
    lc = base + np.cumsum(cl)
    prev = np.concatenate([[base], lc[:-1]])
    new = copy.copy(sd)
    new.o, new.h, new.l, new.c = np.exp(prev + a), np.exp(prev + hi), np.exp(prev + lo), np.exp(lc)
    new.tbv = tbv
    new.feat = {}
    return new


def flip(sd, s: np.ndarray):
    """s: +1/-1 per bar.  Mirror the bar's moves where s < 0."""
    a, hi, lo, cl = offsets(sd)
    pos = s > 0
    return rebuild(sd, s * a, np.where(pos, hi, -lo), np.where(pos, lo, -hi), s * cl,
                   np.where(pos, sd.tbv, sd.v - sd.tbv))


def sign_grid(kind: str, seed: int, sym_index: int, n_bars: int = 120_000) -> np.ndarray:
    """Signs on the global 15m grid starting 2021-01-01 (shared across symbols
    unless ``kind == 'bar_ind'``)."""
    if kind == "bar_ind":
        rng = np.random.default_rng([seed, 1000 + sym_index])
        return rng.choice([-1.0, 1.0], n_bars)
    rng = np.random.default_rng([seed, 7])
    if kind == "bar":
        return rng.choice([-1.0, 1.0], n_bars)
    if kind == "day":
        per_day = S.DAY_MS // S.BAR_MS
        days = rng.choice([-1.0, 1.0], n_bars // per_day + 2)
        return np.repeat(days, per_day)[:n_bars]
    raise ValueError(kind)


def surrogate(data: dict, kind: str, seed: int) -> dict:
    out = {}
    for i, (sym, entry) in enumerate(data.items()):
        g = sign_grid(kind, seed, i)
        pieces = []
        for sd in entry["pieces"]:
            idx = (sd.ts // S.BAR_MS - GRID0).astype(int)
            pieces.append(flip(sd, g[idx]))
        out[sym] = {"pieces": pieces, "gap_days": entry["gap_days"]}
    return out


def plant(data: dict, spec: dict, delta_bp: float, cost: dict) -> dict:
    """Add ``delta_bp`` (log, per trade, side-signed) to the events the rule
    ``spec`` takes on ``data``; spread evenly over the holding bars."""
    out = {}
    h = spec["exit"]["horizon"]
    for sym, entry in data.items():
        pieces = []
        for sd in entry["pieces"]:
            evs = S.events(sd, S.spec_side(sd, spec), h, cost)
            a, hi, lo, cl = offsets(sd)
            d = np.zeros(len(cl))
            for e, x, side, *_ in evs:
                d[e:x] += side * delta_bp * 1e-4 / (x - e)   # bars e..x-1 move open[e] -> open[x]
            pieces.append(rebuild(sd, a, hi + d, lo + d, cl + d, sd.tbv))
        out[sym] = {"pieces": pieces, "gap_days": entry["gap_days"]}
    return out


# ---------------------------------------------------------------------------
# the pipeline (strong_search_dev.main, segment S1 only)
# ---------------------------------------------------------------------------

_SPECS = None


def specs_stage1() -> list:
    global _SPECS
    if _SPECS is None:
        _SPECS = S.stage1_specs()
    return _SPECS


def run_pipeline(data1: dict, cost: dict, cost_mult: float = 1.0, stage2_top: int = 40) -> dict:
    ev1 = S.Evaluator("S1", data1, cost)
    ids, spec_by, mat, nev = [], {}, [], []

    def add(sid, s):
        if sid in spec_by:
            return
        r = ev1.spec(s, cost_mult=cost_mult)
        ids.append(sid)
        spec_by[sid] = s
        mat.append(r["daily"])
        nev.append(r["n"])
    for sid, s in specs_stage1():
        add(sid, s)
    n_stage1 = len(ids)
    M = np.array(mat).T
    srs = np.array([S.sr_ann(M[:, j]) for j in range(M.shape[1])])
    order = [int(j) for j in np.argsort(-np.nan_to_num(srs, nan=-9)) if nev[j] >= 60]
    base = [r for r, _ in S.clusters(M, order, rho=0.7, limit=stage2_top)]
    for j in base:
        s = spec_by[ids[j]]
        for f in S.FILTERS:
            try:
                sid, s2 = S.make_spec(s["direction"], s["entry"]["conditions"], s["exit"]["horizon"], [f])
            except Exception:
                continue
            add(sid, s2)
    M = np.array(mat).T
    srs = np.array([S.sr_ann(M[:, j]) for j in range(M.shape[1])])
    nev = np.array(nev)
    N_A = M.shape[1]
    # Track B
    dl = S.Daily(data1, S.SEGMENTS["S1"])
    cfgs = S.track_b_configs()
    MB = np.array([dl.run(S.track_b_weights(dl, c), cost, cost_mult) for c in cfgs]).T
    srB = np.array([S.sr_ann(MB[:, j]) for j in range(MB.shape[1])])
    N_B = MB.shape[1]
    Mall = np.concatenate([M, MB], axis=1)
    sr_all = np.concatenate([srs, srB])
    valid_all = np.concatenate([nev >= 60, np.ones(N_B, bool)]) & np.isfinite(sr_all)
    years = Mall.shape[0] / S.DAYS_PER_YEAR
    vmask_A = (nev >= 60) & np.isfinite(srs)
    var_sr_daily = float(np.nanvar(srs[vmask_A] / math.sqrt(S.DAYS_PER_YEAR), ddof=1))
    n_eff = S.effective_trials(M[:, vmask_A])
    pbo = S.pbo_cscv(M[:, vmask_A])
    cand = np.flatnonzero(valid_all)
    best_order = cand[np.argsort(-sr_all[cand])]
    j1 = int(best_order[0])
    x1 = Mall[:, j1]
    n_trials = N_A + N_B
    dsr_all = S.dsr_block(x1, n_trials, var_sr_daily)
    dsr_eff = S.dsr_block(x1, max(2.0, n_eff), var_sr_daily)
    return {
        "trials": {"A_stage1": n_stage1, "A_total": N_A, "B": N_B, "total": n_trials,
                   "A_valid_60plus": int(vmask_A.sum()), "n_eff": float(n_eff)},
        "years": years, "var_sr_daily": var_sr_daily,
        "sr_quantiles_A": {str(q): float(np.nanpercentile(srs[vmask_A], q)) for q in (1, 50, 90, 99, 100)},
        "top5_sr": [float(sr_all[j]) for j in best_order[:5]],
        "n_sr_ge": {str(t): int((sr_all[cand] >= t).sum()) for t in (1.0, 1.5, 2.0, 2.5)},
        "best_is_track_b": j1 >= N_A,
        "best_sr": float(sr_all[j1]),
        "best_id": ids[j1] if j1 < N_A else f"B{j1 - N_A}",
        "best_events": int(nev[j1]) if j1 < N_A else None,
        "dsr_all": dsr_all["dsr"], "dsr_eff": dsr_eff["dsr"],
        "emax_unit_all": dsr_all["expected_max_sr_ann_null_unit"],
        "emax_unit_eff": dsr_eff["expected_max_sr_ann_null_unit"],
        "emax_scaled_all": dsr_all["expected_max_sr_ann_null_scaled"],
        "pbo": pbo["pbo"], "pbo_oos_sr_of_is_best_median": pbo["median_oos_sr_ann_of_is_best"],
        "_M": Mall, "_sr": sr_all, "_valid": valid_all, "_ids": ids, "_n_a": N_A,
    }


def p_report(res: dict, spec: dict) -> dict:
    """Where the planted rule landed (ground truth view)."""
    sid, _ = S.make_spec(spec["direction"], spec["entry"]["conditions"], spec["exit"]["horizon"])
    ids = res["_ids"]
    if sid not in ids:
        return {"in_grid": False}
    jp = ids.index(sid)
    sr, valid, M = res["_sr"], res["_valid"], res["_M"]
    order = np.flatnonzero(valid)[np.argsort(-sr[valid])]
    rank = int(np.flatnonzero(order == jp)[0]) + 1 if valid[jp] else None
    xp = M[:, jp]

    def corr(j):
        return float(np.corrcoef(xp, M[:, j])[0, 1]) if M[:, j].std() > 0 and xp.std() > 0 else 0.0
    return {"in_grid": True, "p_sr": float(sr[jp]), "p_rank": rank,
            "top1_corr_with_p": corr(int(order[0])),
            "p_like_in_top10": any(corr(int(j)) >= 0.5 for j in order[:10])}


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

def job_list(pre: dict) -> list:
    jobs = []
    for g in pre["null_groups"]:
        for r in range(g["reps"]):
            jobs.append({"group": g["name"], "kind": g["generator"], "cost_mult": g["cost_mult"],
                         "seed": g["seed0"] + r, "rep": r})
    for g in pre["planted_groups"]:
        for lvl in g["levels"]:
            for r in range(g["reps"]):
                jobs.append({"group": f'{g["name"]}_sr{lvl["target_sr"]}', "kind": g["generator"],
                             "cost_mult": 1.0, "seed": g["seed0"] + r, "rep": r, "plant": g["rule"],
                             "delta_bp": lvl["delta_bp"], "target_sr": lvl["target_sr"]})
    return jobs


def rule_spec(r: dict) -> dict:
    c = S.C(r["feature"], r["params"], r["op"], r["value"])
    return S.make_spec(r["direction"], [c], r["horizon"])[1]


W: dict = {}


def worker_init(data_dir: str, pre_path: str):
    W["data"] = S.load_segment("S1", Path(data_dir))
    W["cost"] = S.load_cost()
    W["pre"] = json.loads(Path(pre_path).read_text())


def do_job(job: dict) -> dict:
    t0 = time.time()
    data = W["data"] if job["kind"] == "real" else surrogate(W["data"], job["kind"], job["seed"])
    p_spec = None
    if "plant" in job:
        p_spec = rule_spec(W["pre"]["planted_rules"][job["plant"]])
        data = plant(data, p_spec, job["delta_bp"], W["cost"])
    res = run_pipeline(data, W["cost"], job["cost_mult"])
    row = {k: v for k, v in res.items() if not k.startswith("_")}
    row["job"] = job
    if p_spec is not None:
        row["planted"] = p_report(res, p_spec)
    row["seconds"] = time.time() - t0
    return row


def calibrate(data_dir: Path, pre: dict, seeds=(9001, 9002, 9003)) -> dict:
    """Pilot: the per-trade log edge (bp) at which the planted rule's own S1
    annual SR (equal-weight, assumed_public_v1 cost) averages the target over
    the pilot surrogates.  Secant on the (near linear) SR(delta)."""
    data, cost = S.load_segment("S1", data_dir), S.load_cost()
    sur = [surrogate(data, "day", s) for s in seeds]
    out = {}
    for name, r in pre["planted_rules"].items():
        spec = rule_spec(r)

        def f(delta):
            xs = []
            for sd in sur:
                d2 = plant(sd, spec, delta, cost) if delta else sd
                xs.append(S.sr_ann(S.Evaluator("S1", d2, cost).spec(spec)["daily"]))
            return float(np.mean(xs))
        s0 = f(0.0)
        res = {"sr_at_zero": s0, "levels": {}}
        for t in pre["calibration_targets"]:
            lo_d, lo_s, hi_d = 0.0, s0, None
            d = 100.0
            for _ in range(8):                      # bracket, then secant/regula falsi
                v = f(d)
                if abs(v - t) < 0.03:
                    break
                if v < t:
                    lo_d, lo_s = d, v
                    d = d * 1.5 if hi_d is None else d
                else:
                    hi_d = d if hi_d is None else min(hi_d, d)
                if hi_d is not None:
                    d = lo_d + (hi_d - lo_d) * (t - lo_s) / max(1e-9, (v if v >= t else lo_s + 1.0) - lo_s) \
                        if v >= t else 0.5 * (lo_d + hi_d)
            res["levels"][str(t)] = {"delta_bp": round(d, 1), "mean_sr_at_delta": v}
        out[name] = res
        print(name, json.dumps(res), flush=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--calibrate", action="store_true", help="pilot: print the delta per target SR")
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("."))
    ap.add_argument("--prereg", type=Path, default=PREREG)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default=None, help="run only groups whose name contains this text")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)
    pre = json.loads(a.prereg.read_text())
    if a.calibrate:
        calibrate(a.data_dir, pre)
        return 0
    a.out.mkdir(parents=True, exist_ok=True)
    res_path = a.out / "runs.jsonl"
    done = set()
    if res_path.exists():
        for line in res_path.read_text().splitlines():
            j = json.loads(line)["job"]
            done.add((j["group"], j["rep"]))
    jobs = [j for j in job_list(pre) if (j["group"], j["rep"]) not in done
            and (a.only is None or a.only in j["group"])]
    if a.limit:
        jobs = jobs[:a.limit]
    print(f"{len(jobs)} jobs ({len(done)} already done)", flush=True)
    t0 = time.time()
    with mp.Pool(a.workers, initializer=worker_init, initargs=(str(a.data_dir), str(a.prereg))) as pool:
        for k, row in enumerate(pool.imap_unordered(do_job, jobs)):
            with res_path.open("a") as fh:
                fh.write(json.dumps(row, default=float) + "\n")
            print(f"{k + 1}/{len(jobs)} {row['job']['group']} rep{row['job']['rep']} "
                  f"best {row['best_sr']:.2f} dsr {row['dsr_all']:.2f} {row['seconds']:.0f}s "
                  f"(elapsed {time.time() - t0:.0f}s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
