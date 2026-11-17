#!/usr/bin/env python3
"""Daily prior-extreme scale-in (DCA / "물타기") strategy, DEV only (ADR 0088).

Runs exactly the 24 configs fixed in
``run/audit/evidence_dca_extreme_20261117/prereg_dca_extreme_v1.json``
(committed before any data was read) on the 17-alt archive of the project upload.

Accounting (dedicated daily engine; ``portfolio_multi`` has no add-to-position):
* every tranche has its own fill price; PnL is marked per unit against the previous
  close, so the cycle's loss is ``sum(units * (price - fill))`` i.e. average-price based;
* each cycle is an isolated-margin position (leverage 5, mmr 0.005): if the day's low
  (short: high) reaches ``avg*(1-1/L+mmr)`` the cycle loses its full posted margin;
* intraday envelope drawdown: every open cycle marked at the day's adverse extreme at
  the same time (worst case, ADR 0072 style);
* actually settled funding; measured alt cost per tranche fill and per exit.

Never reads: 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards.  No orders, no
private API, no NEW2.

    python3 run/dca_extreme_prepare.py --zip <zip> --out <dir>
    python3 run/dca_extreme_dev.py --data-dir <dir>
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
from broad_universe_dev import expected_max_sr  # noqa: E402

VERSION = "dca-extreme-dev-v1"
EVID = HERE / "audit" / "evidence_dca_extreme_20261117"
PREREG = EVID / "prereg_dca_extreme_v1.json"
COST_RECORD = HERE / "audit" / "alt_cost_measured_20261027" / "estimate_v3_alts.json"
CALIBRATION = HERE / "audit" / "lowliq_cost_20261027" / "calibration_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"
SEGMENTS = bu.SEGMENTS
W = 0.05
BAND = 0.03
TP = 0.10
TIMEOUT = 60
COOLDOWN = 5
LEV = 5.0
MMR = 0.005
LOOKBACK_SKIP = 90          # first 90 days of each segment: lookback only, no trades, not in statistics
MIN_CYCLES = 100
MAX_ENV_DD = 0.20
MAX_LIQ_SHARE = 0.05
COST_ROWS = ("measured", "measured_x2", "volume_scaled", "assumed_public_v1", "cost0")
SIDES = (1.0, -1.0)


def make_configs() -> list:
    return [{"id": f"N{n}_s{s:.2f}_K{k}_{x}", "N": n, "s": s, "K": k, "exit": x}
            for n, s, k, x in itertools.product((30, 90), (0.04, 0.08), (0, 2, 4), ("stop_target", "target_time_only"))]


def load_ohlcv(data: Path, seg: str) -> dict:
    lo, hi, _ = SEGMENTS[seg]
    t0, t1 = bu.ms(lo), bu.ms(hi)
    bu.assert_allowed(t0, t1)
    recs = []
    for p in sorted((data / "daily").glob("*.csv")):
        sym = p.name.split("-1d-")[0]
        for f in bu.read_rows(p):
            t = int(f[0])
            if t0 <= t <= t1:
                recs.append((sym, t, float(f[1]), float(f[2]), float(f[3]), float(f[4]), float(f[7])))
    df = pd.DataFrame(recs, columns=["sym", "t", "open", "high", "low", "close", "qv"]).drop_duplicates(["sym", "t"])
    assert df["t"].between(t0, t1).all()
    days = np.arange(t0, t1 + 1, bu.DAY_MS)
    return {k: df.pivot(index="t", columns="sym", values=k).reindex(days)
            for k in ("open", "high", "low", "close", "qv")}


def simulate(cfg: dict, ctx: dict) -> dict:
    """One config over one segment; returns daily series per cost row, envelope path and cycle records."""
    n_days, n_sym = ctx["open"].shape
    K, s, x = cfg["K"], cfg["s"], cfg["exit"]
    tr_notional = W / (K + 1)
    o, h, l, c = ctx["open"], ctx["high"], ctx["low"], ctx["close"]
    Lref, Href = ctx["lines"][cfg["N"]]
    gross = {sd: np.zeros(n_days) for sd in SIDES}
    cost = {r: {sd: np.zeros(n_days) for sd in SIDES} for r in COST_ROWS}
    adv = np.zeros(n_days)          # worst same-day adverse extreme of all open cycles vs previous close (<=0)
    expo = np.zeros(n_days)         # gross notional held at the open
    cyc = [None] * n_sym
    cool = np.zeros(n_sym, int)
    cycles = []
    pend_entry = {}                 # k -> (side, L0)

    def fill_cost(k: int, d: int, notional: float, sd: float) -> float:
        b, q15 = ctx["impact"](k, d)
        tot = {}
        for r in COST_ROWS:
            tot[r] = notional * ctx["side_cost"](b, r, q15)
            cost[r][sd][d] -= tot[r]
        return tot["measured"]

    for d in range(LOOKBACK_SKIP, n_days - 1):
        gap_total = 0.0
        # 1) fills at today's open from yesterday's close signals ------------------------------
        for k in range(n_sym):
            cy = cyc[k]
            od = o.iat[d, k]
            if cy is None:
                if k in pend_entry and not math.isnan(od):
                    sd, L0 = pend_entry.pop(k)
                    cyc[k] = cy = {"k": k, "side": sd, "L0": L0, "units": [], "px": [], "ref": [], "d0": d,
                                   "levels": 0, "pend_add": 0, "pend_exit": None, "pp": 0.0, "fund": 0.0, "cost": 0.0,
                                   "maxnot": 0.0, "ntr": 0}
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
                cy["ref"].append(od)       # gap from previous close is not this tranche's PnL
                cy["ntr"] += 1
                cy["cost"] += fill_cost(k, d, tr_notional, sd)
            # re-reference old tranches to today's open (their overnight gap booked now)
            n_old = len(cy["units"]) - cy["pend_add"]
            for i in range(n_old):
                pnl = sd * cy["units"][i] * (od - cy["ref"][i])
                gross[sd][d] += pnl
                cy["pp"] += pnl
                gap_total += pnl
                cy["ref"][i] = od
            cy["pend_add"] = 0
        # 2) intraday: liquidation, envelope, close mark, funding ------------------------------
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
            liq = avg * (1 - 1 / LEV + MMR) if sd > 0 else avg * (1 + 1 / LEV - MMR)
            ext = l.iat[d, k] if sd > 0 else h.iat[d, k]
            cl = c.iat[d, k]
            f = ctx["fnd"][d, k]
            if not math.isnan(f):
                fp = -sd * f * U * o.iat[d, k]
                gross[sd][d] += fp
                cy["fund"] += fp
            hit = (not math.isnan(ext)) and ((sd > 0 and ext <= liq) or (sd < 0 and ext >= liq))
            if hit:
                margin = notional_px / LEV
                loss_today = -margin - cy["pp"]     # price PnL of the cycle ends at exactly -margin
                gross[sd][d] += loss_today
                cy["pp"] = -margin
                tot_adv += min(0.0, loss_today)
                cy["exit"] = (d, "LIQUIDATED")
                cycles.append(cy)
                cyc[k] = None
                cool[k] = d + COOLDOWN
                continue
            # normal day: mark to close and to the adverse extreme
            pnl_c = sd * sum(u * (cl - rf) for u, rf in zip(cy["units"], cy["ref"]))
            pnl_x = sd * sum(u * (ext - rf) for u, rf in zip(cy["units"], cy["ref"])) if not math.isnan(ext) else pnl_c
            gross[sd][d] += pnl_c
            cy["pp"] += pnl_c
            tot_adv += min(0.0, pnl_x)
            cy["ref"] = [cl] * len(cy["units"])
        adv[d] = tot_adv
        # 3) close signals for tomorrow's open ---------------------------------------------------
        n_open = sum(1 for z in cyc if z is not None)
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
                if sd > 0:
                    if x == "stop_target" and cl <= L0 * (1 - s * (K + 1)):
                        reason = "STOP"
                    elif cl >= avg * (1 + TP):
                        reason = "TARGET"
                else:
                    if x == "stop_target" and cl >= L0 * (1 + s * (K + 1)):
                        reason = "STOP"
                    elif cl <= avg * (1 - TP):
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
    # cycles still open at the last day are marked, not closed (reported)
    open_end = sum(1 for z in cyc if z is not None)
    return {"gross": gross, "cost": cost, "adv": adv, "expo": expo, "cycles": cycles, "open_at_end": open_end}


def cycle_stats(cycles: list) -> dict:
    if not cycles:
        return {"n": 0}
    net = np.array([(cy["pp"] + cy["fund"] - cy["cost"]) / cy["maxnot"] for cy in cycles])
    gr = np.array([(cy["pp"] + cy["fund"]) / cy["maxnot"] for cy in cycles])
    sides = np.array([cy["side"] for cy in cycles])
    reasons = [cy["exit"][1] for cy in cycles]
    liq = sum(1 for r in reasons if r == "LIQUIDATED")
    out = {"n": len(cycles), "n_long": int((sides > 0).sum()), "n_short": int((sides < 0).sum()),
           "mean_gross_bp": float(gr.mean() * 1e4), "mean_net_bp": float(net.mean() * 1e4),
           "median_net_bp": float(np.median(net) * 1e4), "hit_rate": float((net > 0).mean()),
           "worst_cycle_net_pct": float(net.min() * 100), "liquidated": liq,
           "liquidated_share": liq / len(cycles),
           "exit_reasons": {r: reasons.count(r) for r in sorted(set(reasons))},
           "mean_tranches": float(np.mean([cy["ntr"] for cy in cycles])),
           "mean_days_held": float(np.mean([cy["exit"][0] - cy["d0"] for cy in cycles])),
           "mean_net_bp_long": float(net[sides > 0].mean() * 1e4) if (sides > 0).any() else None,
           "mean_net_bp_short": float(net[sides < 0].mean() * 1e4) if (sides < 0).any() else None}
    return out


def tail_stats(net: np.ndarray, adv: np.ndarray, expo: np.ndarray) -> dict:
    cum = np.cumsum(net)
    peak_prev = np.maximum.accumulate(np.concatenate([[0.0], cum]))[:-1]
    prev = np.concatenate([[0.0], cum[:-1]])
    env = float(np.max(peak_prev - (prev + adv)))
    close_dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:] - cum))
    roll20 = np.convolve(net, np.ones(20), "valid") if len(net) >= 20 else net
    pk = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    under, best = 0, 0
    for a, b in zip(cum, pk):
        under = under + 1 if a < b else 0
        best = max(best, under)
    return {"close_max_dd": close_dd, "envelope_max_dd": env, "worst_day": float(net.min()),
            "worst_20d_sum": float(roll20.min()), "longest_underwater_days": int(best),
            "max_gross_notional": float(expo.max()), "mean_gross_notional": float(expo.mean())}


def run_segment(data: Path, seg: str, cfgs: list, costs: dict) -> dict:
    piv = load_ohlcv(data, seg)
    days = piv["open"].index.values
    syms = list(piv["open"].columns)
    t0, t1 = int(days[0]), int(days[-1])
    fund = bu.load_funding(data, syms, t0, t1)
    fund["day"] = ((fund["t"] - 1) // bu.DAY_MS) * bu.DAY_MS
    fnd = fund.groupby(["day", "sym"])["rate"].sum().unstack().reindex(days).reindex(columns=syms).values
    bars = bu.load_bar2345(data, syms, t0, t1)
    raw_close = piv["close"]
    filled = {k: piv[k].ffill() for k in ("open", "high", "low", "close")}   # for marking held positions only
    qv = piv["qv"].values
    q30 = piv["qv"].rolling(30, min_periods=10).mean().values
    flags = {"impact_bar_fallback": 0, "no_volume_cost_fallback": 0, "participation_over_max": 0, "no_q15_fallback": 0}
    lines = {}
    for n in (30, 90):
        lo = piv["low"].shift(1).rolling(n, min_periods=n).min().values
        hi = piv["high"].shift(1).rolling(n, min_periods=n).max().values
        lines[n] = (lo, hi)

    def impact(k: int, idx: int) -> tuple:
        b = bars.get((syms[k], int(days[idx - 1]) + 23 * 3_600_000 + 45 * 60_000))
        if b is None or b <= 0:
            flags["impact_bar_fallback"] += 1
            dq = qv[idx - 1, k]
            if not (dq > 0):
                dq = float(np.nanmean(qv[max(0, idx - 30): idx, k])) if idx else float("nan")
                flags["no_volume_cost_fallback"] += 1
            b = dq / 96.0 if dq > 0 else 1.0
        if bu.ORDER_VALUE / b > costs["measured"]["maxp"]:
            flags["participation_over_max"] += 1
        q15 = q30[idx - 1, k] / 96.0
        if not q15 > 0:
            flags["no_q15_fallback"] += 1
            q15 = b
        return b, q15

    def side_cost(b: float, row: str, q15: float) -> float:
        if row == "cost0":
            return 0.0
        if row == "volume_scaled":
            return bu.side_cost(b, costs["volume_scaled"], "", q15)
        if row == "assumed_public_v1":
            return bu.side_cost(b, costs["assumed"], "")
        v = bu.side_cost(b, costs["measured"], "")
        return 2.0 * v if row == "measured_x2" else v

    ctx = {"open": filled["open"], "high": filled["high"], "low": filled["low"], "close": filled["close"],
           "fnd": fnd, "impact": impact, "side_cost": side_cost, "lines": lines}
    # signals use real (unfilled) closes: NaN close -> no signal; marks use ffilled prices
    ctx["close_raw"] = raw_close
    opn = piv["open"]
    ret = (opn.shift(-1) / opn - 1.0).values
    mkt = np.array([float(np.nanmean(r)) if np.isfinite(r).any() else 0.0 for r in ret])
    span = slice(LOOKBACK_SKIP, len(days) - 1)
    out = {}
    for cfg in cfgs:
        out[cfg["id"]] = simulate(cfg, ctx)
    return {"days": [int(x) for x in days[span]], "mkt": mkt[span], "span": span, "runs": out, "flags": flags,
            "n_symbols": len(syms)}


def flag(row: dict, null_sr: float) -> tuple:
    s1 = row["S1"]
    ok = {"a": s1["measured"]["sr_ann"] > null_sr, "b": s1["measured"]["alpha_t"] >= 2,
          "c": min(s1["halves_sr_ann"]) > 0, "d": row["S2"]["measured"]["sr_ann"] > 0.5,
          "e": s1["volume_scaled"]["sr_ann"] > 0, "f": s1["cycles"]["n"] >= MIN_CYCLES,
          "g_tail": all(row[s]["tail"]["envelope_max_dd"] <= MAX_ENV_DD and
                        row[s]["cycles"].get("liquidated_share", 0.0) <= MAX_LIQ_SHARE for s in ("S1", "S2"))}
    return ("DEV_PROMISING" if all(ok.values()) else "NOT_PROMISING"), ok


def prior_trials() -> int:
    return sum(json.loads(l).get("dev_evaluated", 0) for l in TRIALS.read_text().splitlines() if l.strip())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "dca_extreme_dev.json")
    a = ap.parse_args(argv)
    cfgs = make_configs()
    costs = {"measured": bu.load_cost_record(COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(CALIBRATION)}
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "helper_sha256": bu.sha(HERE / "broad_universe_dev.py"), "cost_record_sha256": bu.sha(COST_RECORD),
           "calibration_sha256": bu.sha(CALIBRATION), "cost_profile_sha256": bu.sha(bu.COST_PROFILE),
           "segments": {}, "configs": {}}
    segs = {s: run_segment(a.data_dir, s, cfgs, costs) for s in SEGMENTS}
    for s, r in segs.items():
        out["segments"][s] = {"first_day": r["days"][0], "last_day": r["days"][-1], "n_days": len(r["days"]),
                              "n_symbols": r["n_symbols"], "flags": r["flags"],
                              "market_ew": bu.stats(np.nan_to_num(r["mkt"]), r["mkt"])}
    s1_years = len(segs["S1"]["days"]) / 365.0
    n_cfg, prior = len(cfgs), prior_trials()
    out["null_max_sr_ann"] = {f"N={n_cfg}": expected_max_sr(n_cfg) / math.sqrt(s1_years),
                              f"N={prior + n_cfg} (dev_search_trials.jsonl sum + this)": expected_max_sr(prior + n_cfg) / math.sqrt(s1_years)}
    null_sr = out["null_max_sr_ann"][f"N={n_cfg}"]
    for cfg in cfgs:
        row = {"params": cfg}
        for s, r in segs.items():
            run, mk, sp = r["runs"][cfg["id"]], r["mkt"], r["span"]
            tot = {c: sum(run["gross"][sd] + run["cost"][c][sd] for sd in SIDES)[sp] for c in COST_ROWS}
            d = {c: bu.stats(tot[c], mk) for c in COST_ROWS}
            net = tot["measured"]
            half = len(net) // 2
            d["halves_sr_ann"] = [bu.stats(net[:half], mk[:half])["sr_ann"], bu.stats(net[half:], mk[half:])["sr_ann"]]
            d["long_only_sr"] = bu.stats((run["gross"][1.0] + run["cost"]["measured"][1.0])[sp], mk)["sr_ann"]
            d["short_only_sr"] = bu.stats((run["gross"][-1.0] + run["cost"]["measured"][-1.0])[sp], mk)["sr_ann"]
            d["cycles"] = cycle_stats(run["cycles"])
            d["open_at_end"] = run["open_at_end"]
            d["tail"] = tail_stats(net, run["adv"][sp], run["expo"][sp])
            row[s] = d
        row["flag"], row["criteria"] = flag(row, null_sr)
        out["configs"][cfg["id"]] = row
    for cfg in cfgs:   # scale-in vs no-scale-in control (same N, s, exit)
        row = out["configs"][cfg["id"]]
        ctrl = out["configs"][f"N{cfg['N']}_s{cfg['s']:.2f}_K0_{cfg['exit']}"]
        row["vs_K0_control"] = {s: {"sr_delta": row[s]["measured"]["sr_ann"] - ctrl[s]["measured"]["sr_ann"],
                                    "env_dd_delta": row[s]["tail"]["envelope_max_dd"] - ctrl[s]["tail"]["envelope_max_dd"]}
                                for s in SEGMENTS}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("null max SR", {k: round(v, 2) for k, v in out["null_max_sr_ann"].items()}, "prior trials", prior)
    for s in SEGMENTS:
        print(s, out["segments"][s]["n_days"], out["segments"][s]["flags"], "market SR", round(out["segments"][s]["market_ew"]["sr_ann"], 2))
    for cid, row in out["configs"].items():
        s1, s2 = row["S1"], row["S2"]
        print(f"{cid:34s} {row['flag'][:3]} S1 n={s1['cycles']['n']:3d} gross {s1['cycles'].get('mean_gross_bp', 0):6.0f}bp net {s1['cycles'].get('mean_net_bp', 0):6.0f}bp "
              f"SR {s1['measured']['sr_ann']:5.2f} c0 {s1['cost0']['sr_ann']:5.2f} | S2 n={s2['cycles']['n']:3d} SR {s2['measured']['sr_ann']:5.2f} | "
              f"envDD {s1['tail']['envelope_max_dd']:.2f}/{s2['tail']['envelope_max_dd']:.2f} liq {s1['cycles'].get('liquidated', 0)}/{s2['cycles'].get('liquidated', 0)} "
              f"L/S SR {s1['long_only_sr']:.2f}/{s1['short_only_sr']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
