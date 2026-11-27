#!/usr/bin/env python3
"""Daily prior-extreme scale-in with LOW LEVERAGE and LIMIT-ONLY entries, DEV only (ADR 0090).

Same strategy as ADR 0088 (``dca_extreme_dev``): entry band 3% of the prior N-day low/high,
tranche W/(K+1), W=0.05, target avg*1.10 on the close -> next-open market exit, 60-day timeout,
5-day cooldown, 17 alts, S1/S2.  Differences, all fixed in
``run/audit/evidence_dca_limit_20261118/prereg_dca_limit_v1.json`` (committed before any data was read):

* leverage 1/2/3 (isolated liquidation ``avg*(1-1/L+mmr)``; notional per cycle does not change);
* every entry (first tranche and every add) is a resting limit order: the first entry is posted on the
  signal day for the next day at ``close*(1-u)`` (good for one day, then lost), adds rest at
  ``L0*(1-s*k)``.  An order fills when the day's low (short: high) trades through the limit by FILL_MARGIN
  (5bp); the fill price is the limit price itself (no price improvement);
* entry-side cost = measured per-side cost x m (central .70 / optimistic .47 / conservative 1.00,
  ADR 0049); exits stay market with full cost;
* optional stop ``avg_close`` (close <= avg*(1-0.15) -> next-open market exit).

    python3 run/dca_extreme_prepare.py --zip <zip> --out <dir>      # as ADR 0088
    python3 run/dca_limit_dev.py --data-dir <dir>
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

VERSION = "dca-limit-dev-v1"
EVID = HERE / "audit" / "evidence_dca_limit_20261118"
PREREG = EVID / "prereg_dca_limit_v1.json"
TRIALS = base.TRIALS
BASES = {"B1": (30, 0.04, 4), "B2": (30, 0.08, 2), "B3": (90, 0.04, 4), "B4": (90, 0.08, 2)}
LEVS = (1.0, 2.0, 3.0)
STOPS = ("none", "avg_close")
STOP_X = 0.15
US = (0.0, 0.02)
FILL_MARGIN = 0.0005
FM_ROBUST = {"touch": 0.0, "strict": 0.002}
# (row name, base cost row, entry multiplier)
ROWS = (("central", "measured", 0.70), ("optimistic", "measured", 0.47), ("conservative", "measured", 1.00),
        ("volume_scaled", "volume_scaled", 0.70), ("assumed_public_v1", "assumed_public_v1", 0.70), ("cost0", "cost0", 0.0))
ROW_NAMES = tuple(r[0] for r in ROWS)
SIDES = base.SIDES
W, TP, TIMEOUT, COOLDOWN, MMR, BAND = base.W, base.TP, base.TIMEOUT, base.COOLDOWN, base.MMR, base.BAND
LOOKBACK_SKIP = base.LOOKBACK_SKIP
MIN_CYCLES, MAX_ENV_DD, MAX_LIQ_SHARE = base.MIN_CYCLES, base.MAX_ENV_DD, base.MAX_LIQ_SHARE


def cid(n, s, k, lev, stop, u, fm=None):
    return f"N{n}_s{s:.2f}_K{k}_L{lev:.0f}_{stop}_u{u:.2f}" + (f"_{fm}" if fm else "")


def make_configs() -> list:
    out = []
    for b, (n, s, k) in BASES.items():
        for lev in LEVS:
            for st in STOPS:
                for u in US:
                    out.append({"id": cid(n, s, k, lev, st, u), "base": b, "N": n, "s": s, "K": k, "lev": lev,
                                "stop": st, "u": u, "fm": FILL_MARGIN, "counted": True})
    for n in (30, 90):
        for u in US:
            out.append({"id": cid(n, 0.04, 0, 3.0, "none", u), "base": f"K0_N{n}", "N": n, "s": 0.04, "K": 0, "lev": 3.0,
                        "stop": "none", "u": u, "fm": FILL_MARGIN, "counted": True})
    return out


def make_robust(cfgs: list) -> list:
    out = []
    for c in cfgs:
        if c["lev"] == 3.0 and c["stop"] == "none":
            for name, fm in FM_ROBUST.items():
                out.append({**c, "id": c["id"] + f"_{name}", "fm": fm, "counted": False})
    return out


def simulate_limit(cfg: dict, ctx: dict) -> dict:
    n_days, n_sym = ctx["open"].shape
    K, s, lev, stop, u, fm = cfg["K"], cfg["s"], cfg["lev"], cfg["stop"], cfg["u"], cfg["fm"]
    tr_notional = W / (K + 1)
    o, h, l, c = ctx["open"], ctx["high"], ctx["low"], ctx["close"]
    Lref, Href = ctx["lines"][cfg["N"]]
    gross = {sd: np.zeros(n_days) for sd in SIDES}
    cost = {r: {sd: np.zeros(n_days) for sd in SIDES} for r in ROW_NAMES}
    adv = np.zeros(n_days)
    expo = np.zeros(n_days)
    cyc = [None] * n_sym
    cool = np.zeros(n_sym, int)
    cycles = []
    pend_entry = {}                 # k -> (side, L0, limit price)
    counts = {"signals": 0, "first_fills": 0, "add_levels_posted": 0, "add_levels_filled": 0}

    def fill_cost(k: int, d: int, notional: float, sd: float, entry: bool) -> float:
        b, q15 = ctx["impact"](k, d)
        base_c = {}
        for rname, brow, m in ROWS:
            if brow not in base_c:
                base_c[brow] = notional * ctx["side_cost"](b, brow, q15)
            v = base_c[brow] * (m if entry else 1.0) if brow != "cost0" else 0.0
            cost[rname][sd][d] -= v
        return notional * ctx["side_cost"](b, "measured", q15) * (0.70 if entry else 1.0)

    def new_tranche(cy: dict, k: int, d: int, px: float, sd: float) -> None:
        cy["units"].append(tr_notional / px)
        cy["px"].append(px)
        cy["ref"].append(px)
        cy["ntr"] += 1
        cy["cost"] += fill_cost(k, d, tr_notional, sd, True)

    for d in range(LOOKBACK_SKIP, n_days - 1):
        gap_total = 0.0
        # 1) market exits at today's open; re-reference old tranches; first-entry limit fills ----------------
        for k in range(n_sym):
            cy = cyc[k]
            od = o.iat[d, k]
            if cy is None:
                if k in pend_entry:
                    sd, L0, px = pend_entry.pop(k)
                    raw = ctx["close_raw"].iat[d, k]
                    if math.isnan(raw):
                        continue
                    hit = l.iat[d, k] <= px * (1 - fm) if sd > 0 else h.iat[d, k] >= px * (1 + fm)
                    if hit:
                        counts["first_fills"] += 1
                        counts["add_levels_posted"] += K
                        cyc[k] = cy = {"k": k, "side": sd, "L0": L0, "units": [], "px": [], "ref": [], "d0": d,
                                       "levels": 0, "pend_exit": None, "pp": 0.0, "fund": 0.0, "cost": 0.0,
                                       "maxnot": 0.0, "ntr": 0, "first_px": px}
                        new_tranche(cy, k, d, px, sd)
                continue
            sd = cy["side"]
            if cy["pend_exit"] is not None:
                U = sum(cy["units"])
                pnl = sd * sum(u_ * (od - rf) for u_, rf in zip(cy["units"], cy["ref"]))
                gross[sd][d] += pnl
                cy["pp"] += pnl
                gap_total += pnl
                cy["cost"] += fill_cost(k, d, U * od, sd, False)
                cy["exit"] = (d, cy["pend_exit"])
                cycles.append(cy)
                cyc[k] = None
                cool[k] = d + COOLDOWN
                continue
            for i in range(len(cy["units"])):
                pnl = sd * cy["units"][i] * (od - cy["ref"][i])
                gross[sd][d] += pnl
                cy["pp"] += pnl
                gap_total += pnl
                cy["ref"][i] = od
        # 2) intraday: add-limit fills, liquidation, envelope, close mark, funding --------------------------
        tot_adv = min(0.0, gap_total)
        for k in range(n_sym):
            cy = cyc[k]
            if cy is None:
                continue
            sd, L0 = cy["side"], cy["L0"]
            if K > 0 and cy["levels"] < K:
                for kk in range(cy["levels"] + 1, K + 1):
                    px_lvl = L0 * (1 - s * kk) if sd > 0 else L0 * (1 + s * kk)
                    reached = l.iat[d, k] <= px_lvl * (1 - fm) if sd > 0 else h.iat[d, k] >= px_lvl * (1 + fm)
                    if not reached:
                        break           # levels are monotone: deeper ones cannot be reached either
                    new_tranche(cy, k, d, px_lvl, sd)
                    cy["levels"] = kk
                    counts["add_levels_filled"] += 1
            U = sum(cy["units"])
            notional_px = sum(u_ * p for u_, p in zip(cy["units"], cy["px"]))
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
                loss_today = -margin - cy["pp"]
                gross[sd][d] += loss_today
                cy["pp"] = -margin
                tot_adv += min(0.0, loss_today)
                cy["exit"] = (d, "LIQUIDATED")
                cycles.append(cy)
                cyc[k] = None
                cool[k] = d + COOLDOWN
                continue
            pnl_c = sd * sum(u_ * (cl - rf) for u_, rf in zip(cy["units"], cy["ref"]))
            pnl_x = sd * sum(u_ * (ext - rf) for u_, rf in zip(cy["units"], cy["ref"])) if not math.isnan(ext) else pnl_c
            gross[sd][d] += pnl_c
            cy["pp"] += pnl_c
            tot_adv += min(0.0, pnl_x)
            cy["ref"] = [cl] * len(cy["units"])
        adv[d] = tot_adv
        # 3) close signals for tomorrow: exits and new limit orders ---------------------------------------
        for k in range(n_sym):
            cy = cyc[k]
            cl = ctx["close_raw"].iat[d, k]
            if math.isnan(cl):
                continue
            if cy is not None:
                sd = cy["side"]
                U = sum(cy["units"])
                avg = sum(u_ * p for u_, p in zip(cy["units"], cy["px"])) / U
                held = d - cy["d0"]
                reason = None
                if sd > 0:
                    if stop == "avg_close" and cl <= avg * (1 - STOP_X):
                        reason = "STOP"
                    elif cl >= avg * (1 + TP):
                        reason = "TARGET"
                else:
                    if stop == "avg_close" and cl >= avg * (1 + STOP_X):
                        reason = "STOP"
                    elif cl <= avg * (1 - TP):
                        reason = "TARGET"
                if reason is None and held >= TIMEOUT:
                    reason = "TIMEOUT"
                if reason:
                    cy["pend_exit"] = reason
            elif cool[k] <= d:
                Lk, Hk = Lref[d, k], Href[d, k]
                if math.isnan(Lk) or math.isnan(Hk):
                    continue
                near_lo = cl <= Lk * (1 + BAND)
                near_hi = cl >= Hk * (1 - BAND)
                if near_lo == near_hi:
                    continue
                counts["signals"] += 1
                pend_entry[k] = (1.0, Lk, cl * (1 - u)) if near_lo else (-1.0, Hk, cl * (1 + u))
    open_end = sum(1 for z in cyc if z is not None)
    return {"gross": gross, "cost": cost, "adv": adv, "expo": expo, "cycles": cycles, "open_at_end": open_end,
            "counts": counts}


def fill_stats(counts: dict) -> dict:
    sg = counts["signals"]
    ap = counts["add_levels_posted"]
    return {**counts, "first_fill_rate": counts["first_fills"] / sg if sg else None,
            "add_fill_rate": counts["add_levels_filled"] / ap if ap else None}


def flag(row: dict, null_sr: float) -> tuple:
    s1 = row["S1"]
    ok = {"a": s1["central"]["sr_ann"] > null_sr, "b": s1["central"]["alpha_t"] >= 2,
          "c": min(s1["halves_sr_ann"]) > 0, "d": row["S2"]["central"]["sr_ann"] > 0.5,
          "e": s1["volume_scaled"]["sr_ann"] > 0, "f": s1["cycles"]["n"] >= MIN_CYCLES,
          "g_tail": all(row[s]["tail"]["envelope_max_dd"] <= MAX_ENV_DD and
                        row[s]["cycles"].get("liquidated_share", 0.0) <= MAX_LIQ_SHARE for s in ("S1", "S2"))}
    return ("DEV_PROMISING" if all(ok.values()) else "NOT_PROMISING"), ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "dca_limit_dev.json")
    a = ap.parse_args(argv)
    base.simulate = simulate_limit
    counted = make_configs()
    allc = counted + make_robust(counted)
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
                and "ADR 0090" not in json.loads(l).get("search", ""))
    out["null_max_sr_ann"] = {f"N={n_cfg}": expected_max_sr(n_cfg) / math.sqrt(s1_years),
                              f"N={prior + n_cfg} (dev_search_trials.jsonl sum excluding ADR 0090 + this)": expected_max_sr(prior + n_cfg) / math.sqrt(s1_years)}
    null_sr = out["null_max_sr_ann"][f"N={n_cfg}"]
    for cfg in allc:
        row = {"params": dict(cfg)}
        for s, r in segs.items():
            run, mk, sp = r["runs"][cfg["id"]], r["mkt"], r["span"]
            tot = {c: sum(run["gross"][sd] + run["cost"][c][sd] for sd in SIDES)[sp] for c in ROW_NAMES}
            d = {c: bu.stats(tot[c], mk) for c in ROW_NAMES}
            net = tot["central"]
            half = len(net) // 2
            d["halves_sr_ann"] = [bu.stats(net[:half], mk[:half])["sr_ann"], bu.stats(net[half:], mk[half:])["sr_ann"]]
            d["long_only_sr"] = bu.stats((run["gross"][1.0] + run["cost"]["central"][1.0])[sp], mk)["sr_ann"]
            d["short_only_sr"] = bu.stats((run["gross"][-1.0] + run["cost"]["central"][-1.0])[sp], mk)["sr_ann"]
            d["cycles"] = base.cycle_stats(run["cycles"])
            d["open_at_end"] = run["open_at_end"]
            d["tail"] = base.tail_stats(net, run["adv"][sp], run["expo"][sp])
            d["fills"] = fill_stats(run["counts"])
            d["margin_use_peak"] = d["tail"]["max_gross_notional"] / cfg["lev"]
            row[s] = d
        if cfg["counted"]:
            row["flag"], row["criteria"] = flag(row, null_sr)
        out["configs"][cfg["id"]] = row
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("null max SR", {k: round(v, 2) for k, v in out["null_max_sr_ann"].items()}, "prior trials", prior)
    for s in base.SEGMENTS:
        print(s, out["segments"][s]["n_days"], out["segments"][s]["flags"], "market SR", round(out["segments"][s]["market_ew"]["sr_ann"], 2))
    for cfg in allc:
        row = out["configs"][cfg["id"]]
        s1, s2 = row["S1"], row["S2"]
        print(f"{cfg['id']:36s} {row.get('flag', 'rob')[:3]} S1 n={s1['cycles']['n']:3d} fill {s1['fills']['first_fill_rate'] or 0:.2f} "
              f"net {s1['cycles'].get('mean_net_bp', 0):6.0f}bp SR {s1['central']['sr_ann']:5.2f} cons {s1['conservative']['sr_ann']:5.2f} c0 {s1['cost0']['sr_ann']:5.2f} | "
              f"S2 n={s2['cycles']['n']:3d} SR {s2['central']['sr_ann']:5.2f} | envDD {s1['tail']['envelope_max_dd']:.2f}/{s2['tail']['envelope_max_dd']:.2f} "
              f"liq {s1['cycles'].get('liquidated', 0)}/{s2['cycles'].get('liquidated', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
