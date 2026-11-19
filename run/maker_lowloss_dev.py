#!/usr/bin/env python3
"""Limit-order (maker) bundle of small-profit / low-loss DEV members, take-profit only when net > 0 (ADR 0093).

Everything is fixed in ``run/audit/evidence_maker_lowloss_20261119/prereg_maker_lowloss_v1.json`` (commit 5670555,
before any series of this study was computed).  DEV S1/S2 only.  No orders, no NEW2.

Members (27, read from the ADR 0092 catalog by the preregistered rule):
* 13 daily scale-in + intraday stop configs (ADR 0089 engine semantics, 17 alts, 5x isolated);
* 14 R9 entry-bar candidates (ADR 0044 spec signal, 15m, 7 liquid alts in the project archive).

Execution model (prereg ``execution_model``):
* every entry is a resting limit order; it fills only if price trades THROUGH the limit by 5 bp, at the limit;
  an entry that does not fill is lost;
* every profit-taking exit is a resting limit at a price where the trade's net (gross + funding - entry costs
  - maker exit cost, conservative row) is > 0;
* stops, liquidation and the hard time cap are market orders with full (taker) cost;
* CTRL keeps the original timeout (market); RULE replaces it by a breakeven-plus resting limit and a hard cap
  at 2x the original holding time.

Stages:
    python3 run/dca_extreme_prepare.py --zip <alts zip> --out <dca dir>
    python3 run/maker_lowloss_dev.py --stage scalein --dca-data <dca dir> --work <w>
    python3 run/maker_lowloss_dev.py --stage r9 --r9-data <dir with klines/ funding/> --work <w>
    python3 run/maker_lowloss_dev.py --stage analyze --work <w>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))

VERSION = "maker-lowloss-dev-v1"
EVID = HERE / "audit" / "evidence_maker_lowloss_20261119"
PREREG = EVID / "prereg_maker_lowloss_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"
R9_CAND = HERE / "audit" / "alt_cost_measured_20261027" / "r9_candidates_with_specs.json"
R9_COST = HERE / "audit" / "alt_cost_measured_20261027" / "estimate_v3_alts.json"
R9_SYMBOLS = ["BNBUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT", "LTCUSDT", "LINKUSDT"]
DAY_MS = 86_400_000
FILL_MARGIN = 0.0005
BE_MARGIN = 0.0001
MAKER_ROWS = {"optimistic": 0.47, "central": 0.70, "conservative": 1.00}
ROW_NAMES = tuple(MAKER_ROWS) + ("cost0",)
DECISION_ROW = "conservative"
VARIANTS = ("CTRL", "RULE")
CONTEXT_ID = "ALT-maker-lowloss-bundle-v1"
# not counted: decomposition of the scale-in CTRL difference vs the market original (entry / adds / take-profit)
DIAG_MODES = {"D0_market_all": {"entry": "market", "adds": "market", "tp": "close"},
              "D1_limit_entry": {"entry": "limit", "adds": "market", "tp": "close"},
              "D2_limit_entry_adds": {"entry": "limit", "adds": "limit", "tp": "close"}}
N_COUNTED = 60
TARGET_VOL_ANN = 0.10


def prereg() -> dict:
    return json.loads(PREREG.read_text())


CATALOG = HERE / "audit" / "evidence_winloss_combo_20261118" / "winloss_catalog_rows.csv"
NO_DATA_FAMILIES = ("MAP-Elites top (BTC/ETH)",)          # BTC/ETH not in the project archive
DEDUPE = {"S-bdae1ff0b24876cc": "S-3f825ccdd41fb32d"}       # identical S1/S2 numbers (same rule)


def passes_selection(r: dict) -> bool:
    """Prereg ``selection.rule_on_S1_rows`` on one catalog row (values may be None)."""
    def f(k):
        v = r.get(k)
        return None if v in ("", None) else float(v)
    n, m, sr, w, dd, lq = (f(k) for k in ("n", "mean_net_bp", "sr_ann", "worst_bp", "max_dd_pct", "liq_share"))
    if n is None or n < 30 or m is None or m <= 0 or sr is None or sr <= 0:
        return False
    if w is None and dd is None and lq is None:
        return False
    return not ((w is not None and w < -2500) or (dd is not None and dd > 20) or (lq is not None and lq > 0.05))


def select_members(rows: list) -> dict:
    """Apply the prereg rule to the ADR 0092 catalog rows -> {'passing': n, 'R9': [...], 'scalein': [...], 'no_data': [...]}."""
    passing = [r for r in rows if r["seg"] == "S1" and passes_selection(r)]
    out = {"passing": len(passing), "R9": [], "scalein": [], "no_data": []}
    for r in passing:
        cid = r["config"]
        if cid in DEDUPE:
            continue
        if r["family"] in NO_DATA_FAMILIES:
            out["no_data"].append(cid)
        elif r["family"].startswith("R9"):
            out["R9"].append(cid)
        elif r["family"] == "scale-in + stop":
            out["scalein"].append(cid)
        else:
            raise ValueError(f"no engine for family {r['family']!r}")
    return out


def parse_scalein_id(cid: str) -> dict:
    """'N90_s0.04_K4_avg_intra0.06' -> config dict (ADR 0089 naming)."""
    n, s, k, kind, rest = cid.split("_")
    st = kind + "_intra"
    return {"id": cid, "N": int(n[1:]), "s": float(s[1:]), "K": int(k[1:]), "stop": st,
            "x": float(rest[len("intra"):])}


# ============================================================================ breakeven price

def breakeven_px(side: float, units: list, px: list, fund: float, entry_cost: float, c_exit: float) -> float:
    """Price at which closing the whole position nets exactly 0 (gross + funding - entry_cost - exit cost).

    long : sum u (P - px) + fund - Ce - U P c = 0  ->  P = (sum u px - fund + Ce) / (U (1 - c))
    short: sum u (px - P) + fund - Ce - U P c = 0  ->  P = (sum u px + fund - Ce) / (U (1 + c))"""
    U = sum(units)
    upx = sum(u * p for u, p in zip(units, px))
    if side > 0:
        return (upx - fund + entry_cost) / (U * (1.0 - c_exit))
    return (upx + fund - entry_cost) / (U * (1.0 + c_exit))


def net_at(side: float, units: list, px: list, fund: float, entry_cost: float, c_exit: float, P: float) -> float:
    U = sum(units)
    return side * sum(u * (P - p) for u, p in zip(units, px)) + fund - entry_cost - U * P * c_exit


# ============================================================================ scale-in (daily)

def simulate_maker(cfg: dict, ctx: dict, variant: str) -> dict:
    """ADR 0089 scale-in with intraday stop, ADR 0090 limit entries, resting net>0 take-profit (see prereg)."""
    import dca_extreme_dev as base
    W, TP, TIMEOUT, COOLDOWN, LEV, MMR, BAND = base.W, base.TP, base.TIMEOUT, base.COOLDOWN, base.LEV, base.MMR, base.BAND
    n_days, n_sym = ctx["open"].shape
    K, s, st, xv = cfg["K"], cfg["s"], cfg["stop"], cfg["x"]
    fm = FILL_MARGIN
    tr_notional = W / (K + 1)
    o, h, l, c = ctx["open"], ctx["high"], ctx["low"], ctx["close"]
    Lref, Href = ctx["lines"][cfg["N"]]
    gross = np.zeros(n_days)
    cost = {r: np.zeros(n_days) for r in ROW_NAMES}
    expo = np.zeros(n_days)
    cyc = [None] * n_sym
    cool = np.zeros(n_sym, int)
    cycles = []
    pend_entry = {}
    counts = {"signals": 0, "first_fills": 0, "add_levels_filled": 0}
    cap_days = 2 * TIMEOUT
    mode = {"entry": "limit", "adds": "limit", "tp": "limit", **cfg.get("mode", {})}   # diagnostics only

    def side_frac(k: int, d: int) -> float:
        b, q15 = ctx["impact"](k, d)
        return ctx["side_cost"](b, "measured", q15)

    def pay(cy: dict, k: int, d: int, notional: float, maker: bool) -> None:
        f = side_frac(k, d)
        for r, m in MAKER_ROWS.items():
            v = notional * f * (m if maker else 1.0)
            cost[r][d] -= v
            cy["cost"][r] += v

    def close_cycle(cy: dict, k: int, d: int, reason: str) -> None:
        cy["exit"] = (d, reason)
        cycles.append(cy)
        cyc[k] = None
        cool[k] = d + COOLDOWN

    def new_tranche(cy: dict, k: int, d: int, px: float, maker: bool = True) -> None:
        cy["units"].append(tr_notional / px)
        cy["px"].append(px)
        cy["ref"].append(px)
        cy["ntr"] += 1
        cy["last"] = px
        pay(cy, k, d, tr_notional, maker)

    for d in range(base.LOOKBACK_SKIP, n_days - 1):
        # 1) open: pending market exits, re-reference, first-entry limit fills
        for k in range(n_sym):
            cy = cyc[k]
            od = o.iat[d, k]
            if cy is None:
                if k in pend_entry:
                    sd, L0, px = pend_entry.pop(k)
                    if math.isnan(ctx["close_raw"].iat[d, k]):
                        continue
                    if mode["entry"] == "market":
                        px = od
                        hit = not math.isnan(od)
                    else:
                        hit = l.iat[d, k] <= px * (1 - fm) if sd > 0 else h.iat[d, k] >= px * (1 + fm)
                    if hit:
                        counts["first_fills"] += 1
                        cyc[k] = cy = {"k": k, "side": sd, "L0": L0, "units": [], "px": [], "ref": [], "d0": d,
                                       "levels": 0, "pend_exit": None, "pp": 0.0, "fund": 0.0,
                                       "cost": {r: 0.0 for r in MAKER_ROWS}, "maxnot": 0.0, "ntr": 0, "last": px,
                                       "be_mode": False, "fill_day": d,
                                       "lim_day": d if mode["entry"] == "limit" else -1}
                        new_tranche(cy, k, d, px, mode["entry"] == "limit")
                continue
            sd = cy["side"]
            if cy["pend_exit"] is not None:
                U = sum(cy["units"])
                pnl = sd * sum(u * (od - rf) for u, rf in zip(cy["units"], cy["ref"]))
                gross[d] += pnl
                cy["pp"] += pnl
                pay(cy, k, d, U * od, False)
                close_cycle(cy, k, d, cy["pend_exit"])
                continue
            for i in range(len(cy["units"])):
                pnl = sd * cy["units"][i] * (od - cy["ref"][i])
                gross[d] += pnl
                cy["pp"] += pnl
                cy["ref"][i] = od
            for _ in range(cy.get("pend_add", 0)):
                new_tranche(cy, k, d, od, False)
                cy["fill_day"] = d
            cy["pend_add"] = 0
        # 2) intraday: add limits, funding, liquidation / stop (market), take-profit limit, close mark
        for k in range(n_sym):
            cy = cyc[k]
            if cy is None:
                continue
            sd, L0 = cy["side"], cy["L0"]
            od = o.iat[d, k]
            if K > 0 and cy["levels"] < K and mode["adds"] == "limit":
                for kk in range(cy["levels"] + 1, K + 1):
                    px_lvl = L0 * (1 - s * kk) if sd > 0 else L0 * (1 + s * kk)
                    reached = l.iat[d, k] <= px_lvl * (1 - fm) if sd > 0 else h.iat[d, k] >= px_lvl * (1 + fm)
                    if not reached:
                        break
                    new_tranche(cy, k, d, px_lvl)
                    cy["levels"] = kk
                    cy["fill_day"] = cy["lim_day"] = d
                    counts["add_levels_filled"] += 1
            U = sum(cy["units"])
            notional_px = sum(u * p for u, p in zip(cy["units"], cy["px"]))
            avg = notional_px / U
            expo[d] += U * od
            cy["maxnot"] = max(cy["maxnot"], notional_px)
            f = ctx["fnd"][d, k]
            if not math.isnan(f):
                fp = -sd * f * U * od
                gross[d] += fp
                cy["fund"] += fp
            liq = avg * (1 - 1 / LEV + MMR) if sd > 0 else avg * (1 + 1 / LEV - MMR)
            ext = l.iat[d, k] if sd > 0 else h.iat[d, k]
            fav = h.iat[d, k] if sd > 0 else l.iat[d, k]
            cl = c.iat[d, k]
            ext_ok = not math.isnan(ext)
            liq_hit = ext_ok and ((sd > 0 and ext <= liq) or (sd < 0 and ext >= liq))
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
                gross[d] += -margin - cy["pp"]
                cy["pp"] = -margin
                close_cycle(cy, k, d, "LIQUIDATED")
                continue
            if stop_hit:
                fillpx = min(stop_px, od) if sd > 0 else max(stop_px, od)
                if cy["lim_day"] == d:      # limit-filled today below the open: the stop cannot fill above the stop
                    fillpx = stop_px
                pnl = sd * sum(u * (fillpx - rf) for u, rf in zip(cy["units"], cy["ref"]))
                gross[d] += pnl
                cy["pp"] += pnl
                pay(cy, k, d, U * fillpx, False)
                close_cycle(cy, k, d, "STOP")
                continue
            if cy["fill_day"] < d and not math.isnan(fav) and (mode["tp"] == "limit" or cy["be_mode"]):
                c_exit = side_frac(k, d) * MAKER_ROWS[DECISION_ROW]
                pbe = breakeven_px(sd, cy["units"], cy["px"], cy["fund"], cy["cost"][DECISION_ROW], c_exit)
                pbe = pbe * (1 + BE_MARGIN) if sd > 0 else pbe * (1 - BE_MARGIN)
                if cy["be_mode"]:
                    ptp = pbe
                else:
                    ptp = max(avg * (1 + TP), pbe) if sd > 0 else min(avg * (1 - TP), pbe)
                tp_hit = fav >= ptp * (1 + fm) if sd > 0 else fav <= ptp * (1 - fm)
                if tp_hit:
                    pnl = sd * sum(u * (ptp - rf) for u, rf in zip(cy["units"], cy["ref"]))
                    gross[d] += pnl
                    cy["pp"] += pnl
                    pay(cy, k, d, U * ptp, True)
                    close_cycle(cy, k, d, "TARGET_BE" if cy["be_mode"] else "TARGET")
                    continue
            pnl_c = sd * sum(u * (cl - rf) for u, rf in zip(cy["units"], cy["ref"]))
            gross[d] += pnl_c
            cy["pp"] += pnl_c
            cy["ref"] = [cl] * len(cy["units"])
        # 3) close: time rules for tomorrow, new entry limits
        for k in range(n_sym):
            cy = cyc[k]
            cl = ctx["close_raw"].iat[d, k]
            if math.isnan(cl):
                continue
            if cy is not None:
                held = d - cy["d0"]
                sd = cy["side"]
                if mode["tp"] == "close" and not cy["be_mode"]:
                    avg = sum(u * p for u, p in zip(cy["units"], cy["px"])) / sum(cy["units"])
                    if (sd > 0 and cl >= avg * (1 + TP)) or (sd < 0 and cl <= avg * (1 - TP)):
                        cy["pend_exit"] = "TARGET"
                        continue
                if mode["adds"] == "market" and K > 0 and cy["levels"] < K:
                    crossed = 0
                    for kk in range(cy["levels"] + 1, K + 1):
                        px_lvl = cy["L0"] * (1 - s * kk) if sd > 0 else cy["L0"] * (1 + s * kk)
                        if (sd > 0 and cl <= px_lvl) or (sd < 0 and cl >= px_lvl):
                            crossed = kk
                    if crossed > cy["levels"]:
                        cy["pend_add"] = crossed - cy["levels"]
                        cy["levels"] = crossed
                if variant == "CTRL":
                    if held >= TIMEOUT:
                        cy["pend_exit"] = "TIMEOUT"
                else:
                    if held >= cap_days:
                        cy["pend_exit"] = "CAP"
                    elif held >= TIMEOUT:
                        cy["be_mode"] = True
            elif cool[k] <= d:
                Lk, Hk = Lref[d, k], Href[d, k]
                if math.isnan(Lk) or math.isnan(Hk):
                    continue
                near_lo = cl <= Lk * (1 + BAND)
                near_hi = cl >= Hk * (1 - BAND)
                if near_lo == near_hi:
                    continue
                counts["signals"] += 1
                pend_entry[k] = (1.0, Lk, cl) if near_lo else (-1.0, Hk, cl)
    return {"gross": gross, "cost": cost, "expo": expo, "cycles": cycles,
            "open_at_end": sum(1 for z in cyc if z is not None), "counts": counts}


def cycle_summary(cycles: list, row: str = DECISION_ROW) -> dict:
    if not cycles:
        return {"n": 0}
    net = np.array([(cy["pp"] + cy["fund"] - cy["cost"][row]) / cy["maxnot"] for cy in cycles]) * 1e4
    reasons = [cy["exit"][1] for cy in cycles]
    n = len(cycles)
    return {"n": n, "hit_rate": float((net > 0).mean()), "mean_net_bp": float(net.mean()),
            "worst_bp": float(net.min()), "avg_win_bp": float(net[net > 0].mean()) if (net > 0).any() else None,
            "avg_loss_bp": float(net[net <= 0].mean()) if (net <= 0).any() else None,
            "liq_share": reasons.count("LIQUIDATED") / n, "cap_share": reasons.count("CAP") / n,
            "exit_reasons": {r: reasons.count(r) for r in sorted(set(reasons))},
            "mean_days_held": float(np.mean([cy["exit"][0] - cy["d0"] for cy in cycles])),
            "profit_exits_net_le_0": int(sum(1 for cy, x in zip(cycles, net)
                                             if cy["exit"][1].startswith("TARGET") and x <= 0))}


def stage_scalein(data: Path, work: Path) -> None:
    import broad_universe_dev as bu
    import dca_extreme_dev as base
    import dca_stop_dev as ds
    pre = prereg()
    cfgs = [parse_scalein_id(x) for x in pre["selection"]["members_scalein"]]
    costs = {"measured": bu.load_cost_record(base.COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(base.CALIBRATION)}
    sims = []
    for cfg in cfgs:
        for v in VARIANTS:
            sims.append({**cfg, "variant": v, "run_id": f"{cfg['id']}|{v}"})
        sims.append({**cfg, "variant": "REF", "run_id": f"{cfg['id']}|REF"})   # ADR 0089 reproduction (market)
        for dname, mode in DIAG_MODES.items():                                  # decomposition, not counted
            sims.append({**cfg, "variant": "CTRL", "mode": mode, "run_id": f"{cfg['id']}|{dname}"})

    def sim(cfg, ctx):
        if cfg["variant"] == "REF":
            return ds.simulate_stop({**cfg, "base": cfg["id"]}, ctx)
        return simulate_maker(cfg, ctx, cfg["variant"])

    base.simulate = sim
    out = {"version": VERSION, "stage": "scalein", "segments": {}}
    for seg in base.SEGMENTS:
        r = base.run_segment(data, seg, [{**c, "id": c["run_id"]} for c in sims], costs)
        sp = r["span"]
        segd = {"days": r["days"], "mkt": [float(x) for x in np.nan_to_num(r["mkt"])], "flags": r["flags"], "runs": {}}
        for c in sims:
            run = r["runs"][c["run_id"]]
            if c["variant"] == "REF" or "mode" in c:
                if "mode" in c:
                    net = (run["gross"] + run["cost"][DECISION_ROW])[sp]
                    segd["runs"][c["run_id"]] = {"net": {"measured": net.tolist()},
                                                 "cycles": cycle_summary(run["cycles"], DECISION_ROW)}
                    continue
                net = sum(run["gross"][sd] + run["cost"]["measured"][sd] for sd in ds.SIDES)[sp]
                segd["runs"][c["run_id"]] = {"net": {"measured": net.tolist()}, "cycles": base.cycle_stats(run["cycles"])}
                continue
            nets = {row: (run["gross"] + run["cost"][row])[sp].tolist() for row in ROW_NAMES}
            segd["runs"][c["run_id"]] = {"net": nets, "counts": run["counts"], "open_at_end": run["open_at_end"],
                                         "cycles": {row: cycle_summary(run["cycles"], row) for row in MAKER_ROWS}}
        out["segments"][seg] = segd
    (work / "scalein_series.json").write_text(json.dumps(out))


# ============================================================================ R9 (15m)

def r9_trades(spec: dict, bars, funding, sym: str, entry_lo: int, cost_ctx, variant: str) -> tuple[list, dict]:
    """Event loop with limit entries and CTRL / RULE exits (prereg r9).  Returns (trades, counts)."""
    import strong_search_dev as T
    from costs_bridge import CostDataMissing, CostRefused
    from evaluator import FundingIndex, PrefixView
    from strategy_spec import build_spec_aux, build_spec_signal
    aux = build_spec_aux(bars, spec, funding, None)
    sig = build_spec_signal(spec, aux)
    H = int(spec["exit"]["horizon"])
    fidx = FundingIndex(funding)
    n = len(bars)
    cnt = {"signals": 0, "not_filled": 0, "overlapped": 0, "truncated": 0, "cost_refused": 0, "funding_missing": 0}
    trades = []
    last_exit = -1
    m_dec = MAKER_ROWS[DECISION_ROW]
    for t in range(max(1, entry_lo - 1), n - 1):
        side = sig(PrefixView(bars, t + 1))
        if side not in (1, -1):
            continue
        cnt["signals"] += 1
        ei = t + 1
        if ei <= last_exit:
            cnt["overlapped"] += 1
            continue
        cap_i = ei + (2 * H if variant == "RULE" else H)
        if ei + H >= n or (variant == "RULE" and cap_i >= n):
            cnt["truncated"] += 1
            continue
        b = bars[ei]
        px_in = b.open
        filled = b.low <= px_in * (1 - FILL_MARGIN) if side == 1 else b.high >= px_in * (1 + FILL_MARGIN)
        if not filled:
            cnt["not_filled"] += 1
            continue
        try:
            c_in = cost_ctx.quote(T.ORDER_VALUE, bars[ei - 1].quote_volume)
        except (CostRefused, CostDataMissing):
            cnt["cost_refused"] += 1
            continue
        hi = ei + H
        if variant == "CTRL":
            xi, px_out, maker_out, reason = hi, bars[hi].open, False, "TIME"
        else:
            xi = px_out = None
            for j in range(hi, cap_i):
                fp = fidx.paid_bp(b.ts, bars[j].ts, side)
                if fp is None:
                    break
                try:
                    cq = cost_ctx.quote(T.ORDER_VALUE, bars[j - 1].quote_volume)
                except (CostRefused, CostDataMissing):
                    cq = c_in
                # breakeven: side*(P/px_in - 1) - fp/1e4 - c_in*m - cq*m = 0 (per unit notional at entry)
                pbe = px_in * (1 + side * (fp / 1e4 + (c_in + cq) * m_dec))
                pbe = pbe * (1 + side * BE_MARGIN)
                plim = max(bars[hi].open, pbe) if side == 1 else min(bars[hi].open, pbe)
                bj = bars[j]
                if (side == 1 and bj.high >= plim * (1 + FILL_MARGIN)) or (side == -1 and bj.low <= plim * (1 - FILL_MARGIN)):
                    # a fill inside bar j settles after bar j's open: funding to the NEXT bar's open is charged below
                    xi, px_out, maker_out, reason = j, plim, True, "TARGET_BE"
                    break
            if xi is None:
                xi, px_out, maker_out, reason = cap_i, bars[cap_i].open, False, "CAP"
        # funding: settlements in (entry open, exit time]; an intrabar limit fill counts the bar's whole span
        f_end = bars[xi + 1].ts if (maker_out and xi + 1 < n) else bars[xi].ts
        fp = fidx.paid_bp(b.ts, f_end, side)
        if fp is None:
            cnt["funding_missing"] += 1
            continue
        try:
            c_out = cost_ctx.quote(T.ORDER_VALUE, bars[xi - 1].quote_volume)
        except (CostRefused, CostDataMissing):
            c_out = c_in
        gross = side * (px_out / px_in - 1.0) * 1e4
        costs = {r: (c_in * m + c_out * (m if maker_out else 1.0)) * 1e4 for r, m in MAKER_ROWS.items()}
        costs["cost0"] = 0.0
        trades.append({"entry_ts": int(b.ts), "exit_ts": int(bars[xi].ts), "side": side, "gross_bp": gross,
                       "funding_bp": fp, "cost_bp": costs, "reason": reason, "bars_held": xi - ei})
        last_exit = xi
    return trades, cnt


def _r9_one(args) -> dict:
    cid, spec, data_dir = args
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
    out = {"strategy_id": cid, "direction": spec["direction"], "horizon": spec["exit"]["horizon"]}
    for seg, seg_data in data.items():
        d0, d1 = T.day_grid(T.SEGMENTS[seg])
        out[seg] = {"day0": int(d0), "n_days": int(d1 - d0 + 1), "n_symbols": len(seg_data)}
        for v in VARIANTS:
            ser = {r: np.zeros(d1 - d0 + 1) for r in ROW_NAMES}
            trades, counts = [], {}
            for sym, entry in seg_data.items():
                for sd in entry["pieces"]:
                    tr, cn = r9_trades(spec, sd.bars, sd.funding, sym, sd.entry_lo, cost_ctx, v)
                    for k2, x in cn.items():
                        counts[k2] = counts.get(k2, 0) + x
                    for e in tr:
                        dx = e["exit_ts"] // DAY_MS
                        if d0 <= dx <= d1:
                            for r in ROW_NAMES:
                                ser[r][dx - d0] += e["gross_bp"] - e["funding_bp"] - e["cost_bp"][r]
                            trades.append({"net_bp": {r: e["gross_bp"] - e["funding_bp"] - e["cost_bp"][r] for r in MAKER_ROWS},
                                           "reason": e["reason"], "bars_held": e["bars_held"], "exit_day": int(dx)})
            out[seg][v] = {"net_bp": {r: (ser[r] / len(seg_data)).tolist() for r in ROW_NAMES},
                           "trades": trades, "counts": counts}
    return out


def stage_r9(data: Path, work: Path, workers: int) -> None:
    pre = prereg()
    want = pre["selection"]["members_R9"]
    cands = {c["strategy_id"]: c for c in json.loads(R9_CAND.read_text())}
    jobs = [(cid, cands[cid]["spec"], str(data)) for cid in want]
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(_r9_one, jobs))
    (work / "r9_series.json").write_text(json.dumps({"version": VERSION, "stage": "r9", "symbols": R9_SYMBOLS,
                                                     "candidates": res}))


# ============================================================================ analysis

def sr_ann(x: np.ndarray) -> float:
    x = np.asarray(x, float)
    sd = x.std(ddof=1) if len(x) > 2 else 0.0
    return float(x.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0


def max_dd(x: np.ndarray) -> float:
    cum = np.cumsum(x)
    return float(np.max(np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:] - cum)) if len(x) else 0.0


def expected_max_sr_ann(n: int, years: float) -> float:
    from broad_universe_dev import expected_max_sr
    return expected_max_sr(n) / math.sqrt(years)


def prior_trials() -> int:
    return sum(json.loads(l).get("dev_evaluated", 0) for l in TRIALS.read_text().splitlines() if l.strip())


def r9_trade_summary(trades: list, row: str = DECISION_ROW) -> dict:
    if not trades:
        return {"n": 0}
    net = np.array([t["net_bp"][row] for t in trades])
    reasons = [t["reason"] for t in trades]
    n = len(net)
    return {"n": n, "hit_rate": float((net > 0).mean()), "mean_net_bp": float(net.mean()), "worst_bp": float(net.min()),
            "avg_win_bp": float(net[net > 0].mean()) if (net > 0).any() else None,
            "avg_loss_bp": float(net[net <= 0].mean()) if (net <= 0).any() else None,
            "cap_share": reasons.count("CAP") / n, "exit_reasons": {r: reasons.count(r) for r in sorted(set(reasons))},
            "mean_bars_held": float(np.mean([t["bars_held"] for t in trades])),
            "profit_exits_net_le_0": int(sum(1 for t, x in zip(trades, net) if t["reason"] == "TARGET_BE" and x <= 0))}


def analyze(work: Path, out_path: Path) -> dict:
    pre = prereg()
    sc = json.loads((work / "scalein_series.json").read_text())
    r9 = json.loads((work / "r9_series.json").read_text())
    res = {"version": VERSION, "prereg_sha256": __import__("hashlib").sha256(PREREG.read_bytes()).hexdigest(),
           "decision_row": DECISION_ROW, "members": {}, "reference": {}, "bundles": {}, "segments": {}}
    # common day grid: scale-in span days covered by the R9 grid
    grids = {}
    for seg in ("S1", "S2"):
        days = [d // DAY_MS for d in sc["segments"][seg]["days"]]
        c0 = r9["candidates"][0][seg]
        r0, rn = c0["day0"], c0["n_days"]
        keep = [i for i, d in enumerate(days) if r0 <= d < r0 + rn]
        grids[seg] = {"sc_idx": np.array(keep), "r9_idx": np.array([days[i] - r0 for i in keep]),
                      "first_day": int(days[keep[0]]), "last_day": int(days[keep[-1]])}
        res["segments"][seg] = {"n_days": len(keep), "first_day_ms": grids[seg]["first_day"] * DAY_MS,
                                "last_day_ms": grids[seg]["last_day"] * DAY_MS}
    series = {}   # (member, variant, seg, row) -> np.array on the common grid (fraction of equity / bp->frac)
    for cid in pre["selection"]["members_scalein"]:
        for v in VARIANTS:
            m = {}
            for seg in ("S1", "S2"):
                run = sc["segments"][seg]["runs"][f"{cid}|{v}"]
                for row in ROW_NAMES:
                    series[(cid, v, seg, row)] = np.asarray(run["net"][row])[grids[seg]["sc_idx"]]
                m[seg] = {"cycles": run["cycles"][DECISION_ROW], "cycles_central": run["cycles"]["central"],
                          "counts": run["counts"], "open_at_end": run["open_at_end"],
                          **{f"sr_{row}": sr_ann(series[(cid, v, seg, row)]) for row in ROW_NAMES}}
            res["members"][f"{cid}|{v}"] = {"family": "scale-in + intra stop (ADR 0089)", **m}
        ref = {}
        for seg in ("S1", "S2"):
            run = sc["segments"][seg]["runs"][f"{cid}|REF"]
            ref[seg] = {"sr_measured_full_span": sr_ann(np.asarray(run["net"]["measured"])), "cycles_n": run["cycles"]["n"],
                        "hit_rate": run["cycles"].get("hit_rate"), "liq_share": run["cycles"].get("liquidated_share")}
        res["reference"][cid] = ref
        res.setdefault("scalein_decomposition", {})[cid] = {
            seg: {name: sr_ann(np.asarray(sc["segments"][seg]["runs"][f"{cid}|{name}"]["net"].get("measured")
                                          or sc["segments"][seg]["runs"][f"{cid}|{name}"]["net"][DECISION_ROW]))
                  for name in ("REF", *DIAG_MODES, "CTRL", "RULE")} for seg in ("S1", "S2")}
    for c in r9["candidates"]:
        cid = c["strategy_id"]
        for v in VARIANTS:
            m = {}
            for seg in ("S1", "S2"):
                d = c[seg][v]
                for row in ROW_NAMES:
                    series[(cid, v, seg, row)] = np.asarray(d["net_bp"][row])[grids[seg]["r9_idx"]] / 1e4
                m[seg] = {"trades": r9_trade_summary(d["trades"]), "trades_central": r9_trade_summary(d["trades"], "central"),
                          "counts": d["counts"],
                          "entry_fill_rate": (1 - d["counts"].get("not_filled", 0) /
                                              max(1, d["counts"].get("signals", 0) - d["counts"].get("overlapped", 0)
                                                  - d["counts"].get("truncated", 0))),
                          **{f"sr_{row}": sr_ann(series[(cid, v, seg, row)]) for row in ROW_NAMES}}
            res["members"][f"{cid}|{v}"] = {"family": "R9 entry-bar (7 symbols)", "direction": c["direction"],
                                            "horizon": c["horizon"], **m}
    groups = {"ALL": pre["selection"]["members_scalein"] + pre["selection"]["members_R9"],
              "R9": pre["selection"]["members_R9"], "SCALEIN": pre["selection"]["members_scalein"]}
    s1_years = res["segments"]["S1"]["n_days"] / 365.0
    prior = prior_trials()
    null = {"N=60": expected_max_sr_ann(N_COUNTED, s1_years),
            f"N={prior + N_COUNTED} (cumulative)": expected_max_sr_ann(prior + N_COUNTED, s1_years)}
    res["null_max_sr_ann"] = null
    res["prior_trials"] = prior
    for g, mem in groups.items():
        for v in VARIANTS:
            row_out = {}
            scales = {}
            for cid in mem:
                sd1 = series[(cid, v, "S1", DECISION_ROW)].std(ddof=1)
                scales[cid] = 1.0 / sd1 if sd1 > 0 else 0.0
            raw = {seg: {row: np.mean([series[(cid, v, seg, row)] * scales[cid] for cid in mem], axis=0)
                         for row in ROW_NAMES} for seg in ("S1", "S2")}
            sd_b = raw["S1"][DECISION_ROW].std(ddof=1)
            k = (TARGET_VOL_ANN / math.sqrt(365)) / sd_b if sd_b > 0 else 0.0
            for seg in ("S1", "S2"):
                x = {row: raw[seg][row] * k for row in ROW_NAMES}
                row_out[seg] = {**{f"sr_{row}": sr_ann(x[row]) for row in ROW_NAMES},
                                "max_dd_pct": max_dd(x[DECISION_ROW]) * 100, "worst_day_pct": float(x[DECISION_ROW].min() * 100),
                                "ann_return_pct": float(x[DECISION_ROW].mean() * 365 * 100)}
                sc_mem = [cid for cid in mem if cid in groups["SCALEIN"]]
                r9_mem = [cid for cid in mem if cid in groups["R9"]]
                cyc_n = sum(res["members"][f"{cid}|{v}"][seg]["cycles"]["n"] for cid in sc_mem)
                liq_n = sum(res["members"][f"{cid}|{v}"][seg]["cycles"]["liq_share"] * res["members"][f"{cid}|{v}"][seg]["cycles"]["n"]
                            for cid in sc_mem)
                row_out[seg]["scalein_liq_share"] = liq_n / cyc_n if cyc_n else None
                row_out[seg]["r9_worst_trade_bp"] = (min(res["members"][f"{cid}|{v}"][seg]["trades"]["worst_bp"] for cid in r9_mem)
                                                     if r9_mem else None)
            row_out["vol_scale_k"] = k
            s1, s2 = row_out["S1"], row_out["S2"]
            crit = {"a": s1[f"sr_{DECISION_ROW}"] > null["N=60"], "b": s2[f"sr_{DECISION_ROW}"] > 0.5,
                    "c": s1["max_dd_pct"] <= 20 and s2["max_dd_pct"] <= 20,
                    "d": all((row_out[sg]["scalein_liq_share"] is None or row_out[sg]["scalein_liq_share"] <= 0.05) and
                             (row_out[sg]["r9_worst_trade_bp"] is None or row_out[sg]["r9_worst_trade_bp"] >= -2500)
                             for sg in ("S1", "S2")),
                    "e": s1["sr_central"] > 0 and s2["sr_central"] > 0}
            row_out["criteria"] = crit
            row_out["flag"] = "DEV_PROMISING" if all(crit.values()) else "NOT_PROMISING"
            res["bundles"][f"{g}|{v}"] = row_out
    # correlation of member series (decision row, RULE) on S1: mean pairwise
    for v in VARIANTS:
        for seg in ("S1", "S2"):
            mats = np.array([series[(cid, v, seg, DECISION_ROW)] for cid in groups["ALL"]])
            cm = np.corrcoef(mats)
            iu = np.triu_indices(len(mats), 1)
            res.setdefault("mean_pairwise_corr", {})[f"{v}|{seg}"] = float(np.nanmean(cm[iu]))
    out_path.write_text(json.dumps(res, indent=1, default=float))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("scalein", "r9", "analyze"), required=True)
    ap.add_argument("--dca-data", type=Path)
    ap.add_argument("--r9-data", type=Path)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=EVID / "maker_lowloss_dev.json")
    a = ap.parse_args(argv)
    a.work.mkdir(parents=True, exist_ok=True)
    if a.stage == "scalein":
        stage_scalein(a.dca_data, a.work)
    elif a.stage == "r9":
        stage_r9(a.r9_data, a.work, a.workers)
    else:
        res = analyze(a.work, a.out)
        print("null", {k: round(x, 2) for k, x in res["null_max_sr_ann"].items()})
        for k, b in res["bundles"].items():
            print(f"{k:14s} {b['flag'][:3]} S1 SR {b['S1']['sr_conservative']:5.2f} (c {b['S1']['sr_central']:5.2f}) "
                  f"S2 SR {b['S2']['sr_conservative']:5.2f} (c {b['S2']['sr_central']:5.2f}) DD {b['S1']['max_dd_pct']:.1f}/{b['S2']['max_dd_pct']:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
