#!/usr/bin/env python3
"""Daily prior-extreme scale-in with a STOP rule added, DEV only (ADR 0089).

Entry / add / size / leverage / target / timeout / cooldown / universe / cost are exactly
ADR 0088 (``dca_extreme_dev``).  Only the stop rule differs.  The 52 counted configs and the
reference runs are fixed in ``run/audit/evidence_dca_stop_20261118/prereg_dca_stop_v1.json``
(committed before any data was read).  ADR 0088's ``simulate`` is replaced (monkey patch of
``dca_extreme_dev.simulate``) by ``simulate_stop``, which is the same engine plus stops; with
``stop`` in {none, struct} it reproduces ADR 0088 exactly (checked in main and in tests).

Stops (long; short is the mirror):
* avg_close / last_close : daily close <= ref*(1-x) -> exit signal, fill next open (gap included)
* avg_intra / last_intra : day's low reaches ref*(1-x) -> exit same day at the stop price, or at the
  open if it gapped through; optional extra adverse slippage ``slip`` (robustness rows)
  ref = average entry price (recomputed after each add) or the last tranche fill price.
* liquidation (isolated 5x, avg*(1-1/5+mmr)) takes precedence when it is nearer the open than the stop
  or when the open itself gaps beyond it.

    python3 run/dca_extreme_prepare.py --zip <zip> --out <dir>      # as ADR 0088
    python3 run/dca_stop_dev.py --data-dir <dir>
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

VERSION = "dca-stop-dev-v1"
EVID = HERE / "audit" / "evidence_dca_stop_20261118"
PREREG = EVID / "prereg_dca_stop_v1.json"
ADR88 = HERE / "audit" / "evidence_dca_extreme_20261117" / "dca_extreme_dev.json"
TRIALS = base.TRIALS
BASES = {"B1": (30, 0.04, 4), "B2": (30, 0.08, 2), "B3": (90, 0.04, 4), "B4": (90, 0.08, 2)}
AVG_X = (0.06, 0.10, 0.15)
LAST_W = (0.05, 0.10)
SLIP = 0.005
SIDES = base.SIDES
COST_ROWS = base.COST_ROWS
W, TP, TIMEOUT, COOLDOWN, LEV, MMR, BAND = base.W, base.TP, base.TIMEOUT, base.COOLDOWN, base.LEV, base.MMR, base.BAND
LOOKBACK_SKIP = base.LOOKBACK_SKIP


def cid(n, s, k, stop, x=None, slip=0.0):
    return f"N{n}_s{s:.2f}_K{k}_{stop}" + (f"{x:.2f}" if x is not None else "") + ("_slip50" if slip else "")


def make_configs() -> list:
    """52 counted configs (prereg)."""
    out = []
    for b, (n, s, k) in BASES.items():
        for st in ("avg_close", "avg_intra"):
            out += [{"id": cid(n, s, k, st, x), "base": b, "N": n, "s": s, "K": k, "stop": st, "x": x, "counted": True} for x in AVG_X]
        for st in ("last_close", "last_intra"):
            out += [{"id": cid(n, s, k, st, w), "base": b, "N": n, "s": s, "K": k, "stop": st, "x": w, "counted": True} for w in LAST_W]
    for n in (30, 90):
        for st in ("avg_close", "avg_intra"):
            out += [{"id": cid(n, 0.04, 0, st, x), "base": f"K0_N{n}", "N": n, "s": 0.04, "K": 0, "stop": st, "x": x, "counted": True} for x in AVG_X]
    return out


def make_refs() -> list:
    """Reference runs reproducing ADR 0088 (not counted again)."""
    out = []
    for b, (n, s, k) in BASES.items():
        out.append({"id": cid(n, s, k, "none"), "base": b, "N": n, "s": s, "K": k, "stop": "none", "x": None, "counted": False})
        out.append({"id": cid(n, s, k, "struct"), "base": b, "N": n, "s": s, "K": k, "stop": "struct", "x": None, "counted": False})
    for n in (30, 90):
        out.append({"id": cid(n, 0.04, 0, "none"), "base": f"K0_N{n}", "N": n, "s": 0.04, "K": 0, "stop": "none", "x": None, "counted": False})
    return out


def make_slip(cfgs: list) -> list:
    return [{**c, "id": c["id"] + "_slip50", "slip": SLIP, "counted": False} for c in cfgs if c["stop"].endswith("_intra")]


def simulate_stop(cfg: dict, ctx: dict) -> dict:
    n_days, n_sym = ctx["open"].shape
    K, s = cfg["K"], cfg["s"]
    st, xv, slip = cfg["stop"], cfg.get("x"), cfg.get("slip", 0.0)
    intra = st.endswith("_intra")
    tr_notional = W / (K + 1)
    o, h, l, c = ctx["open"], ctx["high"], ctx["low"], ctx["close"]
    Lref, Href = ctx["lines"][cfg["N"]]
    gross = {sd: np.zeros(n_days) for sd in SIDES}
    cost = {r: {sd: np.zeros(n_days) for sd in SIDES} for r in COST_ROWS}
    adv = np.zeros(n_days)
    expo = np.zeros(n_days)
    cyc = [None] * n_sym
    cool = np.zeros(n_sym, int)
    cycles = []
    pend_entry = {}

    def fill_cost(k: int, d: int, notional: float, sd: float) -> float:
        b, q15 = ctx["impact"](k, d)
        tot = {}
        for r in COST_ROWS:
            tot[r] = notional * ctx["side_cost"](b, r, q15)
            cost[r][sd][d] -= tot[r]
        return tot["measured"]

    for d in range(LOOKBACK_SKIP, n_days - 1):
        gap_total = 0.0
        for k in range(n_sym):
            cy = cyc[k]
            od = o.iat[d, k]
            if cy is None:
                if k in pend_entry and not math.isnan(od):
                    sd, L0 = pend_entry.pop(k)
                    cyc[k] = cy = {"k": k, "side": sd, "L0": L0, "units": [], "px": [], "ref": [], "d0": d,
                                   "levels": 0, "pend_add": 0, "pend_exit": None, "pp": 0.0, "fund": 0.0, "cost": 0.0,
                                   "maxnot": 0.0, "ntr": 0, "last": od}
                    cy["units"].append(tr_notional / od)
                    cy["px"].append(od)
                    cy["ref"].append(od)
                    cy["ntr"] = 1
                    cy["cost"] += fill_cost(k, d, tr_notional, sd)
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
                cy["units"].append(tr_notional / od)
                cy["px"].append(od)
                cy["ref"].append(od)
                cy["ntr"] += 1
                cy["last"] = od
                cy["cost"] += fill_cost(k, d, tr_notional, sd)
            n_old = len(cy["units"]) - cy["pend_add"]
            for i in range(n_old):
                pnl = sd * cy["units"][i] * (od - cy["ref"][i])
                gross[sd][d] += pnl
                cy["pp"] += pnl
                gap_total += pnl
                cy["ref"][i] = od
            cy["pend_add"] = 0
        tot_adv = min(0.0, gap_total)
        for k in range(n_sym):
            cy = cyc[k]
            if cy is None:
                continue
            sd = cy["side"]
            U = sum(cy["units"])
            notional_px = sum(u * p for u, p in zip(cy["units"], cy["px"]))
            avg = notional_px / U
            od = o.iat[d, k]
            expo[d] += U * od
            cy["maxnot"] = max(cy["maxnot"], notional_px)
            liq = avg * (1 - 1 / LEV + MMR) if sd > 0 else avg * (1 + 1 / LEV - MMR)
            ext = l.iat[d, k] if sd > 0 else h.iat[d, k]
            cl = c.iat[d, k]
            f = ctx["fnd"][d, k]
            if not math.isnan(f):
                fp = -sd * f * U * od
                gross[sd][d] += fp
                cy["fund"] += fp
            ext_ok = not math.isnan(ext)
            liq_hit = ext_ok and ((sd > 0 and ext <= liq) or (sd < 0 and ext >= liq))
            stop_px = None
            stop_hit = False
            if intra:
                ref = avg if st == "avg_intra" else cy["last"]
                stop_px = ref * (1 - xv) if sd > 0 else ref * (1 + xv)
                stop_hit = ext_ok and ((sd > 0 and ext <= stop_px) or (sd < 0 and ext >= stop_px))
                if stop_hit and liq_hit:
                    stop_first = stop_px > liq if sd > 0 else stop_px < liq
                    gap_liq = (sd > 0 and od <= liq) or (sd < 0 and od >= liq)
                    if gap_liq or not stop_first:
                        stop_hit = False
                    else:
                        liq_hit = False
            if liq_hit:
                margin = notional_px / LEV
                loss_today = -margin - cy["pp"]
                gross[sd][d] += loss_today
                cy["pp"] = -margin
                tot_adv += min(0.0, loss_today)
                cy["exit"] = (d, "LIQUIDATED")
                cycles.append(cy)
                cyc[k] = None
                cool[k] = d + COOLDOWN
                continue
            if stop_hit:
                fillpx = min(stop_px, od) if sd > 0 else max(stop_px, od)
                fillpx = fillpx * (1 - slip) if sd > 0 else fillpx * (1 + slip)
                pnl = sd * sum(u * (fillpx - rf) for u, rf in zip(cy["units"], cy["ref"]))
                gross[sd][d] += pnl
                cy["pp"] += pnl
                tot_adv += min(0.0, pnl)
                cy["cost"] += fill_cost(k, d, U * fillpx, sd)
                cy["exit"] = (d, "STOP")
                cycles.append(cy)
                cyc[k] = None
                cool[k] = d + COOLDOWN
                continue
            pnl_c = sd * sum(u * (cl - rf) for u, rf in zip(cy["units"], cy["ref"]))
            pnl_x = sd * sum(u * (ext - rf) for u, rf in zip(cy["units"], cy["ref"])) if ext_ok else pnl_c
            gross[sd][d] += pnl_c
            cy["pp"] += pnl_c
            tot_adv += min(0.0, pnl_x)
            cy["ref"] = [cl] * len(cy["units"])
        adv[d] = tot_adv
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
                if st == "struct":
                    stop_now = cl <= L0 * (1 - s * (K + 1)) if sd > 0 else cl >= L0 * (1 + s * (K + 1))
                elif st in ("avg_close", "last_close"):
                    ref = avg if st == "avg_close" else cy["last"]
                    stop_now = cl <= ref * (1 - xv) if sd > 0 else cl >= ref * (1 + xv)
                else:
                    stop_now = False
                if stop_now:
                    reason = "STOP"
                elif (sd > 0 and cl >= avg * (1 + TP)) or (sd < 0 and cl <= avg * (1 - TP)):
                    reason = "TARGET"
                if reason is None and held >= TIMEOUT:
                    reason = "TIMEOUT"
                if reason:
                    cy["pend_exit"] = reason
                elif K > 0 and cy["levels"] < K:
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
                pend_entry[k] = (1.0, Lk) if near_lo else (-1.0, Hk)
    open_end = sum(1 for z in cyc if z is not None)
    return {"gross": gross, "cost": cost, "adv": adv, "expo": expo, "cycles": cycles, "open_at_end": open_end}


def stop_stats(cycles: list) -> dict:
    stopped = [cy for cy in cycles if cy["exit"][1] == "STOP"]
    if not cycles:
        return {"n_stop": 0, "stop_share": 0.0, "stopped_mean_net_bp": None}
    net = [(cy["pp"] + cy["fund"] - cy["cost"]) / cy["maxnot"] * 1e4 for cy in stopped]
    return {"n_stop": len(stopped), "stop_share": len(stopped) / len(cycles),
            "stopped_mean_net_bp": float(np.mean(net)) if net else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "dca_stop_dev.json")
    a = ap.parse_args(argv)
    base.simulate = simulate_stop
    counted, refs = make_configs(), make_refs()
    allc = counted + refs
    allc += make_slip(counted)
    costs = {"measured": bu.load_cost_record(base.COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(base.CALIBRATION)}
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "base_script_sha256": bu.sha(HERE / "dca_extreme_dev.py"),
           "helper_sha256": bu.sha(HERE / "broad_universe_dev.py"), "cost_record_sha256": bu.sha(base.COST_RECORD),
           "calibration_sha256": bu.sha(base.CALIBRATION), "cost_profile_sha256": bu.sha(bu.COST_PROFILE),
           "segments": {}, "configs": {}}
    segs = {s: base.run_segment(a.data_dir, s, allc, costs) for s in base.SEGMENTS}
    for s, r in segs.items():
        out["segments"][s] = {"first_day": r["days"][0], "last_day": r["days"][-1], "n_days": len(r["days"]),
                              "n_symbols": r["n_symbols"], "flags": r["flags"],
                              "market_ew": bu.stats(r["mkt"], r["mkt"])}
    s1_years = len(segs["S1"]["days"]) / 365.0
    n_cfg = len(counted)
    prior = sum(json.loads(l).get("dev_evaluated", 0) for l in TRIALS.read_text().splitlines() if l.strip()
                and "ADR 0089" not in json.loads(l).get("search", ""))
    out["null_max_sr_ann"] = {f"N={n_cfg}": expected_max_sr(n_cfg) / math.sqrt(s1_years),
                              f"N={prior + n_cfg} (dev_search_trials.jsonl sum excluding ADR 0089 + this)": expected_max_sr(prior + n_cfg) / math.sqrt(s1_years)}
    null_sr = out["null_max_sr_ann"][f"N={n_cfg}"]
    for cfg in allc:
        row = {"params": {k: v for k, v in cfg.items()}}
        for s, r in segs.items():
            run, mk, sp = r["runs"][cfg["id"]], r["mkt"], r["span"]
            tot = {c: sum(run["gross"][sd] + run["cost"][c][sd] for sd in SIDES)[sp] for c in COST_ROWS}
            d = {c: bu.stats(tot[c], mk) for c in COST_ROWS}
            net = tot["measured"]
            half = len(net) // 2
            d["halves_sr_ann"] = [bu.stats(net[:half], mk[:half])["sr_ann"], bu.stats(net[half:], mk[half:])["sr_ann"]]
            d["long_only_sr"] = bu.stats((run["gross"][1.0] + run["cost"]["measured"][1.0])[sp], mk)["sr_ann"]
            d["short_only_sr"] = bu.stats((run["gross"][-1.0] + run["cost"]["measured"][-1.0])[sp], mk)["sr_ann"]
            d["cycles"] = {**base.cycle_stats(run["cycles"]), **stop_stats(run["cycles"])}
            d["open_at_end"] = run["open_at_end"]
            d["tail"] = base.tail_stats(net, run["adv"][sp], run["expo"][sp])
            row[s] = d
        if cfg.get("counted"):
            row["flag"], row["criteria"] = base.flag(row, null_sr)
        out["configs"][cfg["id"]] = row
    for cfg in allc:     # vs matching no-stop control and ADR 0088 structural stop
        if cfg["stop"] == "none":
            continue
        row = out["configs"][cfg["id"]]
        k0 = cfg["K"] == 0
        n, s_, k = cfg["N"], cfg["s"], cfg["K"]
        ctrl = out["configs"][cid(n, s_, k, "none")]
        row["vs_no_stop"] = {s: {"sr_delta": row[s]["measured"]["sr_ann"] - ctrl[s]["measured"]["sr_ann"],
                                 "env_dd_delta": row[s]["tail"]["envelope_max_dd"] - ctrl[s]["tail"]["envelope_max_dd"],
                                 "liq_share_delta": row[s]["cycles"].get("liquidated_share", 0.0) - ctrl[s]["cycles"].get("liquidated_share", 0.0),
                                 "mean_net_bp_delta": row[s]["cycles"].get("mean_net_bp", 0.0) - ctrl[s]["cycles"].get("mean_net_bp", 0.0)}
                             for s in base.SEGMENTS}
    # reproduction check against ADR 0088 (same engine, same data)
    rep = {}
    if ADR88.exists():
        old = json.loads(ADR88.read_text())["configs"]
        for cfg in refs:
            n, s_, k = cfg["N"], cfg["s"], cfg["K"]
            x88 = "target_time_only" if cfg["stop"] == "none" else "stop_target"
            oid = f"N{n}_s{s_:.2f}_K{k}_{x88}"
            if oid in old:
                rep[cfg["id"]] = {s: abs(old[oid][s]["measured"]["sr_ann"] - out["configs"][cfg["id"]][s]["measured"]["sr_ann"]) for s in base.SEGMENTS}
    out["reproduces_adr0088_max_abs_sr_diff"] = max((v for r in rep.values() for v in r.values()), default=None)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("null max SR", {k: round(v, 2) for k, v in out["null_max_sr_ann"].items()}, "prior trials", prior,
          "ADR0088 reproduction max |dSR|", out["reproduces_adr0088_max_abs_sr_diff"])
    for s in base.SEGMENTS:
        print(s, out["segments"][s]["n_days"], out["segments"][s]["flags"], "market SR", round(out["segments"][s]["market_ew"]["sr_ann"], 2))
    for cfg in allc:
        row = out["configs"][cfg["id"]]
        s1, s2 = row["S1"], row["S2"]
        print(f"{cfg['id']:30s} {row.get('flag', 'ref')[:3]} S1 n={s1['cycles']['n']:3d} net {s1['cycles'].get('mean_net_bp', 0):6.0f}bp "
              f"SR {s1['measured']['sr_ann']:5.2f} | S2 n={s2['cycles']['n']:3d} SR {s2['measured']['sr_ann']:5.2f} | "
              f"envDD {s1['tail']['envelope_max_dd']:.2f}/{s2['tail']['envelope_max_dd']:.2f} liq {s1['cycles'].get('liquidated', 0)}/{s2['cycles'].get('liquidated', 0)} "
              f"stop {s1['cycles'].get('n_stop', 0)}/{s2['cycles'].get('n_stop', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
