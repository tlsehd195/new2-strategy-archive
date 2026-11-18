#!/usr/bin/env python3
"""Structural tail fixes of the daily prior-extreme scale-in strategy, DEV only (ADR 0091).

Same engine as ADR 0088 (``dca_extreme_dev``: market entry at next open, 3% band, adds at
L0*(1-s*k), target avg*1.10, timeout 60, cooldown 5, no stop, isolated liquidation, real
funding, measured alt cost) with leverage as a parameter and six structural options, all fixed
in ``run/audit/evidence_dca_struct_20261118/prereg_dca_struct_v1.json`` (committed before any
data was read): tranche taper (weights r^i), concurrent-cycle cap, market-trend entry filter,
time-based halving, partial take-profit, volatility-normalised size, and two pre-declared
combinations.  With every option off and lev 5 the result equals ``dca_extreme_dev`` exactly.

    python3 run/dca_extreme_prepare.py --zip <zip> --out <dir>      # as ADR 0088
    python3 run/dca_struct_dev.py --data-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import dca_extreme_dev as base  # noqa: E402
from broad_universe_dev import expected_max_sr  # noqa: E402

VERSION = "dca-struct-dev-v1"
EVID = HERE / "audit" / "evidence_dca_struct_20261118"
PREREG = EVID / "prereg_dca_struct_v1.json"
TRIALS = base.TRIALS
BASES = {"B1": (30, 0.04, 4), "B4": (90, 0.08, 2)}
SIDES = base.SIDES
COST_ROWS = base.COST_ROWS
W, TP, TIMEOUT, COOLDOWN, MMR, BAND = base.W, base.TP, base.TIMEOUT, base.COOLDOWN, base.MMR, base.BAND
LOOKBACK_SKIP = base.LOOKBACK_SKIP
MIN_CYCLES, MAX_ENV_DD, MAX_LIQ_SHARE = base.MIN_CYCLES, base.MAX_ENV_DD, base.MAX_LIQ_SHARE

OPTS = {"lev": 3.0, "r": None, "cap": None, "reg": None, "tm": None, "pt": None, "vn": None}
VARIANTS = {
    "C5": {"lev": 5.0}, "C3": {},
    "T60": {"r": 0.6}, "T35": {"r": 0.35},
    "CAP3": {"cap": 3}, "CAP6": {"cap": 6},
    "REG10": {"reg": 0.10}, "REG20": {"reg": 0.20},
    "TM20": {"tm": 20}, "TM40": {"tm": 40},
    "PT4": {"pt": 0.04}, "PT6": {"pt": 0.06},
    "VN4": {"vn": 0.04}, "VN3": {"vn": 0.03},
    "CMB3": {"r": 0.6, "cap": 6, "reg": 0.20, "pt": 0.06},
    "CMB2": {"lev": 2.0, "r": 0.6, "cap": 6, "reg": 0.20, "pt": 0.06},
}


def make_configs() -> list:
    out = []
    for b, (n, s, k) in BASES.items():
        for v, o in VARIANTS.items():
            out.append({"id": f"{b}_{v}", "base": b, "variant": v, "N": n, "s": s, "K": k, **OPTS, **o})
    return out


def weights(K: int, r) -> list:
    w = [1.0 if r is None else r ** i for i in range(K + 1)]
    t = sum(w)
    return [x / t for x in w]


def simulate_struct(cfg: dict, ctx: dict) -> dict:
    n_days, n_sym = ctx["open"].shape
    K, s, lev = cfg["K"], cfg["s"], cfg["lev"]
    wts = weights(K, cfg["r"])
    o, h, l, c = ctx["open"], ctx["high"], ctx["low"], ctx["close"]
    Lref, Href = ctx["lines"][cfg["N"]]
    cr = ctx["close_raw"]
    ret30 = np.full(n_days, np.nan)
    sig20 = np.full((n_days, n_sym), np.nan)
    cv = cr.values
    for d in range(30, n_days):
        with np.errstate(all="ignore"):
            r30 = cv[d] / cv[d - 30] - 1.0
        if np.isfinite(r30).any():
            ret30[d] = float(np.nanmean(r30))
    if cfg["vn"]:
        sig20 = cr.pct_change(fill_method=None).rolling(20, min_periods=15).std().values
    gross = {sd: np.zeros(n_days) for sd in SIDES}
    cost = {r: {sd: np.zeros(n_days) for sd in SIDES} for r in COST_ROWS}
    adv = np.zeros(n_days)
    expo = np.zeros(n_days)
    cyc = [None] * n_sym
    cool = np.zeros(n_sym, int)
    cycles = []
    pend_entry = {}                 # k -> (side, L0, size multiplier)
    refused = {"cap": 0, "regime": 0}

    def fill_cost(k: int, d: int, notional: float, sd: float) -> float:
        b, q15 = ctx["impact"](k, d)
        tot = {}
        for r in COST_ROWS:
            tot[r] = notional * ctx["side_cost"](b, r, q15)
            cost[r][sd][d] -= tot[r]
        return tot["measured"]

    def tranche_notional(cy: dict, i: int) -> float:
        return W * cy["mult"] * wts[i]

    for d in range(LOOKBACK_SKIP, n_days - 1):
        gap_total = 0.0
        # 1) fills / exits / cuts at today's open ------------------------------------------------
        for k in range(n_sym):
            cy = cyc[k]
            od = o.iat[d, k]
            if cy is None:
                if k in pend_entry and not math.isnan(od):
                    sd, L0, mult = pend_entry.pop(k)
                    cyc[k] = cy = {"k": k, "side": sd, "L0": L0, "units": [], "px": [], "ref": [], "d0": d,
                                   "levels": 0, "pend_add": 0, "pend_exit": None, "pp": 0.0, "fund": 0.0, "cost": 0.0,
                                   "maxnot": 0.0, "ntr": 0, "mult": mult, "cut_pp": 0.0, "pend_cut": None,
                                   "tm_done": False, "pt_done": False}
                    nt = tranche_notional(cy, 0)
                    cy["units"].append(nt / od)
                    cy["px"].append(od)
                    cy["ref"].append(od)
                    cy["ntr"] = 1
                    cy["cost"] += fill_cost(k, d, nt, sd)
                continue
            sd = cy["side"]
            if cy["pend_exit"] is not None:
                U = sum(cy["units"])
                pnl = sd * sum(u * (od - rf) for u, rf in zip(cy["units"], cy["ref"]))
                gross[sd][d] += pnl
                cy["pp"] += pnl
                gap_total += pnl
                cy["cost"] += fill_cost(k, d, U * od, sd)
                cy["exit"] = (d, cy["pend_exit"])
                cycles.append(cy)
                cyc[k] = None
                cool[k] = d + COOLDOWN
                continue
            for _ in range(cy["pend_add"]):
                nt = tranche_notional(cy, cy["ntr"])
                cy["units"].append(nt / od)
                cy["px"].append(od)
                cy["ref"].append(od)
                cy["ntr"] += 1
                cy["cost"] += fill_cost(k, d, nt, sd)
            n_old = len(cy["units"]) - cy["pend_add"]
            for i in range(n_old):
                pnl = sd * cy["units"][i] * (od - cy["ref"][i])
                gross[sd][d] += pnl
                cy["pp"] += pnl
                gap_total += pnl
                cy["ref"][i] = od
            cy["pend_add"] = 0
            if cy["pend_cut"] is not None:
                # halve pro rata at today's open: sold half realises (od - px) per unit, full cost on the sold notional
                sold = [0.5 * u for u in cy["units"]]
                cy["cut_pp"] += sd * sum(u * (od - p) for u, p in zip(sold, cy["px"]))
                cy["cost"] += fill_cost(k, d, sum(sold) * od, sd)
                cy["units"] = [u - x for u, x in zip(cy["units"], sold)]
                cy["pend_cut"] = None
        # 2) intraday: liquidation, envelope, close mark, funding --------------------------------
        tot_adv = min(0.0, gap_total)
        for k in range(n_sym):
            cy = cyc[k]
            if cy is None:
                continue
            sd = cy["side"]
            U = sum(cy["units"])
            notional_px = sum(u * p for u, p in zip(cy["units"], cy["px"]))
            avg = notional_px / U
            expo[d] += U * o.iat[d, k]
            cy["maxnot"] = max(cy["maxnot"], notional_px)
            liq = avg * (1 - 1 / lev + MMR) if sd > 0 else avg * (1 + 1 / lev - MMR)
            ext = l.iat[d, k] if sd > 0 else h.iat[d, k]
            cl = c.iat[d, k]
            f = ctx["fnd"][d, k]
            if not math.isnan(f):
                fp = -sd * f * U * o.iat[d, k]
                gross[sd][d] += fp
                cy["fund"] += fp
            hit = (not math.isnan(ext)) and ((sd > 0 and ext <= liq) or (sd < 0 and ext >= liq))
            if hit:
                margin = notional_px / lev
                loss_today = (cy["cut_pp"] - margin) - cy["pp"]     # remaining position loses its margin; realised cut stays
                gross[sd][d] += loss_today
                cy["pp"] = cy["cut_pp"] - margin
                tot_adv += min(0.0, loss_today)
                cy["exit"] = (d, "LIQUIDATED")
                cycles.append(cy)
                cyc[k] = None
                cool[k] = d + COOLDOWN
                continue
            pnl_c = sd * sum(u * (cl - rf) for u, rf in zip(cy["units"], cy["ref"]))
            pnl_x = sd * sum(u * (ext - rf) for u, rf in zip(cy["units"], cy["ref"])) if not math.isnan(ext) else pnl_c
            gross[sd][d] += pnl_c
            cy["pp"] += pnl_c
            tot_adv += min(0.0, pnl_x)
            cy["ref"] = [cl] * len(cy["units"])
        adv[d] = tot_adv
        # 3) close signals for tomorrow's open ---------------------------------------------------
        cand = []
        for k in range(n_sym):
            cy = cyc[k]
            cl = ctx["close_raw"].iat[d, k]
            if math.isnan(cl):
                continue
            if cy is not None:
                sd, L0 = cy["side"], cy["L0"]
                U = sum(cy["units"])
                avg = sum(u * p for u, p in zip(cy["units"], cy["px"])) / U
                held = d - cy["d0"]
                reason = None
                if (sd > 0 and cl >= avg * (1 + TP)) or (sd < 0 and cl <= avg * (1 - TP)):
                    reason = "TARGET"
                if reason is None and held >= TIMEOUT:
                    reason = "TIMEOUT"
                if reason:
                    cy["pend_exit"] = reason
                    continue
                if cfg["tm"] and not cy["tm_done"] and held >= cfg["tm"]:
                    cy["tm_done"] = True
                    cy["pend_cut"] = "TIME_HALF"
                if cfg["pt"] and not cy["pt_done"] and ((sd > 0 and cl >= avg * (1 + cfg["pt"])) or (sd < 0 and cl <= avg * (1 - cfg["pt"]))):
                    cy["pt_done"] = True
                    cy["pend_cut"] = "PARTIAL_TP"
                if K > 0 and cy["levels"] < K:
                    crossed = 0
                    for kk in range(cy["levels"] + 1, K + 1):
                        px_lvl = L0 * (1 - s * kk) if sd > 0 else L0 * (1 + s * kk)
                        if (sd > 0 and cl <= px_lvl) or (sd < 0 and cl >= px_lvl):
                            crossed = kk
                    if crossed > cy["levels"]:
                        cy["pend_add"] = crossed - cy["levels"]
                        cy["levels"] = crossed
            elif cool[k] <= d and k not in pend_entry:
                Lk, Hk = Lref[d, k], Href[d, k]
                if math.isnan(Lk) or math.isnan(Hk):
                    continue
                near_lo = cl <= Lk * (1 + BAND)
                near_hi = cl >= Hk * (1 - BAND)
                if near_lo == near_hi:
                    continue
                sd = 1.0 if near_lo else -1.0
                if cfg["reg"] and np.isfinite(ret30[d]) and ((sd > 0 and ret30[d] <= -cfg["reg"]) or (sd < 0 and ret30[d] >= cfg["reg"])):
                    refused["regime"] += 1
                    continue
                mult = 1.0
                if cfg["vn"]:
                    sg = sig20[d, k]
                    mult = float(np.clip(cfg["vn"] / sg, 0.5, 1.5)) if np.isfinite(sg) and sg > 0 else 1.0
                dist = (cl / Lk - 1.0) if near_lo else (1.0 - cl / Hk)
                cand.append((dist, k, sd, Lk if near_lo else Hk, mult))
        cand.sort(key=lambda z: (z[0], z[1]))
        room = (cfg["cap"] - sum(1 for z in cyc if z is not None) - len(pend_entry)) if cfg["cap"] else len(cand)
        for i, (dist, k, sd, line, mult) in enumerate(cand):
            if i < max(room, 0):
                pend_entry[k] = (sd, line, mult)
            else:
                refused["cap"] += 1
    open_end = sum(1 for z in cyc if z is not None)
    return {"gross": gross, "cost": cost, "adv": adv, "expo": expo, "cycles": cycles, "open_at_end": open_end,
            "refused": refused}


def cycle_extra(cycles: list) -> dict:
    if not cycles:
        return {}
    net = np.array([(cy["pp"] + cy["fund"] - cy["cost"]) / cy["maxnot"] for cy in cycles])
    w, lo = net[net > 0], net[net <= 0]
    return {"p5_net_pct": float(np.percentile(net, 5) * 100), "avg_win_pct": float(w.mean() * 100) if len(w) else None,
            "avg_loss_pct": float(lo.mean() * 100) if len(lo) else None,
            "payoff": float(w.mean() / -lo.mean()) if len(w) and len(lo) and lo.mean() < 0 else None}


def flag(row: dict, null_sr: float) -> tuple:
    return base.flag(row, null_sr)


def improves(row: dict, ctrl: dict, seg: str) -> bool:
    a, b = row[seg], ctrl[seg]
    return (a["tail"]["envelope_max_dd"] < b["tail"]["envelope_max_dd"]
            and a["extra"]["p5_net_pct"] > b["extra"]["p5_net_pct"]
            and a["cycles"]["worst_cycle_net_pct"] > b["cycles"]["worst_cycle_net_pct"]
            and a["measured"]["sr_ann"] >= b["measured"]["sr_ann"] - 0.10)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "dca_struct_dev.json")
    a = ap.parse_args(argv)
    base.simulate = simulate_struct
    cfgs = make_configs()
    costs = {"measured": bu.load_cost_record(base.COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(base.CALIBRATION)}
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "base_script_sha256": bu.sha(HERE / "dca_extreme_dev.py"), "helper_sha256": bu.sha(HERE / "broad_universe_dev.py"),
           "cost_record_sha256": bu.sha(base.COST_RECORD), "calibration_sha256": bu.sha(base.CALIBRATION),
           "cost_profile_sha256": bu.sha(bu.COST_PROFILE), "segments": {}, "configs": {}}
    segs = {s: base.run_segment(a.data_dir, s, cfgs, costs) for s in base.SEGMENTS}
    for s, r in segs.items():
        out["segments"][s] = {"first_day": r["days"][0], "last_day": r["days"][-1], "n_days": len(r["days"]),
                              "n_symbols": r["n_symbols"], "flags": r["flags"],
                              "market_ew": bu.stats(np.nan_to_num(r["mkt"]), r["mkt"])}
    s1_years = len(segs["S1"]["days"]) / 365.0
    n_cfg = len(cfgs)
    prior = sum(json.loads(l).get("dev_evaluated", 0) for l in TRIALS.read_text().splitlines() if l.strip()
                and "ADR 0091" not in json.loads(l).get("search", ""))
    out["null_max_sr_ann"] = {f"N={n_cfg}": expected_max_sr(n_cfg) / math.sqrt(s1_years),
                              f"N={prior + n_cfg} (dev_search_trials.jsonl sum excluding ADR 0091 + this)": expected_max_sr(prior + n_cfg) / math.sqrt(s1_years)}
    null_sr = out["null_max_sr_ann"][f"N={n_cfg}"]
    for cfg in cfgs:
        row = {"params": dict(cfg)}
        for s, r in segs.items():
            run, mk, sp = r["runs"][cfg["id"]], r["mkt"], r["span"]
            tot = {c: sum(run["gross"][sd] + run["cost"][c][sd] for sd in SIDES)[sp] for c in COST_ROWS}
            d = {c: bu.stats(tot[c], mk) for c in COST_ROWS}
            net = tot["measured"]
            half = len(net) // 2
            d["halves_sr_ann"] = [bu.stats(net[:half], mk[:half])["sr_ann"], bu.stats(net[half:], mk[half:])["sr_ann"]]
            d["cycles"] = base.cycle_stats(run["cycles"])
            d["extra"] = cycle_extra(run["cycles"])
            d["open_at_end"] = run["open_at_end"]
            d["refused"] = run["refused"]
            d["tail"] = base.tail_stats(net, run["adv"][sp], run["expo"][sp])
            row[s] = d
        row["flag"], row["criteria"] = flag(row, null_sr)
        out["configs"][cfg["id"]] = row
    for cfg in cfgs:
        row = out["configs"][cfg["id"]]
        ctrl = out["configs"][f"{cfg['base']}_C3"]
        row["vs_C3"] = {s: {"sr_delta": row[s]["measured"]["sr_ann"] - ctrl[s]["measured"]["sr_ann"],
                            "env_dd_delta": row[s]["tail"]["envelope_max_dd"] - ctrl[s]["tail"]["envelope_max_dd"],
                            "improves": improves(row, ctrl, s)} for s in base.SEGMENTS}
        row["improves_s1_confirmed_s2"] = bool(row["vs_C3"]["S1"]["improves"] and row["vs_C3"]["S2"]["improves"]) if cfg["variant"] != "C3" else None
        row["sr_sign_consistent"] = bool(row["S1"]["measured"]["sr_ann"] > 0 and row["S2"]["measured"]["sr_ann"] > 0)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("null max SR", {k: round(v, 2) for k, v in out["null_max_sr_ann"].items()}, "prior trials", prior)
    for cfg in cfgs:
        row = out["configs"][cfg["id"]]
        s1, s2 = row["S1"], row["S2"]
        print(f"{cfg['id']:11s} {row['flag'][:3]} S1 n={s1['cycles']['n']:3d} win {s1['cycles']['hit_rate']:.2f} pay {s1['extra']['payoff'] or 0:4.2f} p5 {s1['extra']['p5_net_pct']:6.1f} worst {s1['cycles']['worst_cycle_net_pct']:6.1f} "
              f"SR {s1['measured']['sr_ann']:5.2f} envDD {s1['tail']['envelope_max_dd']:.2f} liq {s1['cycles']['liquidated']:2d} | S2 n={s2['cycles']['n']:3d} SR {s2['measured']['sr_ann']:5.2f} "
              f"p5 {s2['extra']['p5_net_pct']:6.1f} worst {s2['cycles']['worst_cycle_net_pct']:6.1f} envDD {s2['tail']['envelope_max_dd']:.2f} liq {s2['cycles']['liquidated']:2d} "
              f"imp {int(row['vs_C3']['S1']['improves'])}{int(row['vs_C3']['S2']['improves'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
