#!/usr/bin/env python3
"""Wide-universe cross-sectional DEV check with effective independent names (ADR 0044).

Runs exactly the five hypotheses x three universes (U50 control, W200, T150)
fixed in ``run/audit/evidence_wide_universe_20261028/prereg_wide_universe_v1.json``
(committed before any data of ranks 51+ was downloaded), DEV segments only.

Never reads: 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards; BTCUSDT
in S2.  No orders, no private API, no NEW2.

Usage (data from run/broad_universe_fetch.py steps daily/funding/bar2345)::

    python3 run/broad_universe_fetch.py --out <dir> --step daily
    python3 run/wide_universe_dev.py --data-dir <dir> --write-universe
    python3 run/broad_universe_fetch.py --out <dir> --step funding --symbols <dir>/ever_w200.json
    python3 run/broad_universe_fetch.py --out <dir> --step bar2345 --symbols <dir>/ever_w200.json
    python3 run/wide_universe_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu
import cross_section_v2_dev as cs
import lowliq_cost_calibration as lc
from broad_universe_dev import expected_max_sr

VERSION = "wide-universe-dev-v1"
EVID = HERE / "audit" / "evidence_wide_universe_20261028"
PREREG = EVID / "prereg_wide_universe_v1.json"
CALIBRATION = HERE / "audit" / "lowliq_cost_20261027" / "calibration_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"
SEGMENTS = bu.SEGMENTS
FLOOR = 2_000_000.0
MIN_UNIVERSE = 30
Q_THIN = 410_000.0  # thinnest measured 15m bar volume of ADR 0041 (the line is extrapolated below it)
NEFF_DAYS = 90
NEFF_MIN_OBS = 60
#: universe id -> (first rank index, last rank index exclusive) in the eligible list
UNIVERSES = {"U50": (0, 50), "W200": (0, 200), "T150": (50, 200)}
#: hypothesis -> (True when the HIGH end of the signal is the long side, hold days)
HYPS = {"H1_MOM28": (True, 28), "H2_REV7": (False, 7), "H3_CARRY7": (False, 7),
        "H4_LOWVOL30": (False, 28), "H5_TAKERFLOW": (True, 28)}
COST_ROWS = ("cal", "cal_x2", "cal_tail_x2", "cost0")
N_TRIALS = len(HYPS) * len(UNIVERSES)


def rebalance_days(days: np.ndarray, first: str, hold: int) -> list:
    first_ms = bu.ms(first)
    mondays = [i for i, t in enumerate(days)
               if t >= first_ms and dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).weekday() == 0]
    return list(range(mondays[0], len(days) - 1, hold)) if mondays else []


def eligible(piv: dict, n_bars: pd.DataFrame, i: int) -> list:
    """Symbols tradeable at the open of day i, ranked by 30-day mean quote volume (cut-off = bar i-2)."""
    cut = i - 2
    if cut < 0:
        return []
    opn, qv = piv["open"], piv["qv"]
    v30 = qv.iloc[max(0, cut - 29): cut + 1].mean()
    nb = n_bars.iloc[cut]
    el = [s for s in opn.columns if nb[s] >= bu.MIN_BARS and not math.isnan(opn.iloc[i][s])
          and not math.isnan(v30[s]) and v30[s] >= FLOOR]
    return sorted(el, key=lambda s: (-v30[s], s))


def slice_universe(ranked: list, uid: str) -> list:
    lo, hi = UNIVERSES[uid]
    u = ranked[lo:hi]
    return u if len(u) >= MIN_UNIVERSE else []


def pick(sig: pd.Series, universe: list, long_top: bool) -> dict:
    """Quintile long-short: K = floor(n_universe / 5) names per side (prereg), equal weight 1/(2K)."""
    k = len(universe) // 5
    s = sig.reindex(universe).dropna()
    if k < 1 or len(s) < 2 * k:
        return {}
    order = sorted(s.index, key=lambda n: (-s[n], n))
    top, bot = order[:k], order[-k:]
    longs, shorts = (top, bot) if long_top else (bot, top)
    w = {n: 1.0 / (2 * k) for n in longs}
    w.update({n: -1.0 / (2 * k) for n in shorts})
    return w


def taker_flow(piv: dict, cut: int) -> pd.Series:
    q28, t28 = piv["qv"].iloc[max(0, cut - 27): cut + 1], piv["tbq"].iloc[max(0, cut - 27): cut + 1]
    flow = t28.sum() / q28.sum()
    flow[(q28.notna().sum() < 28) | (t28.notna().sum() < 28) | ~(q28.sum() > 0)] = np.nan
    return flow


def hyp_signals(piv: dict, fund_daily: pd.DataFrame, cut: int) -> dict:
    s = bu.signals(piv, fund_daily, cut)
    return {"H1_MOM28": s["H1_MOM28"], "H2_REV7": s["H2_REV7"], "H3_CARRY7": s["H3_CARRY7"],
            "H4_LOWVOL30": s["H4_LOWVOL30"], "H5_TAKERFLOW": taker_flow(piv, cut)}


def side_cost(row: str, cal: dict, b: float, q15: float) -> float:
    if row == "cost0":
        return 0.0
    fixed, imp = lc.cost_params(cal, q15)
    c = fixed + imp * math.sqrt(bu.ORDER_VALUE / b)
    if row == "cal_x2":
        return 2.0 * c
    if row == "cal_tail_x2":
        return 2.0 * c if q15 < Q_THIN else c
    return c


def effective_names(close: pd.DataFrame, cut: int, universe: list) -> dict | None:
    """Participation-ratio N_eff and mean pairwise correlation of the last 90 daily returns."""
    r = close[universe].iloc[max(0, cut - NEFF_DAYS): cut + 1].pct_change(fill_method=None).iloc[1:]
    r = r.loc[:, r.notna().sum() >= NEFF_MIN_OBS].fillna(0.0)
    r = r.loc[:, r.std() > 0]
    n = r.shape[1]
    if n < 10:
        return None
    c = np.corrcoef(r.values.T)
    lam = np.linalg.eigvalsh(c)
    n_eff = float(n * n / float(np.sum(lam ** 2)))
    rho = float((c.sum() - n) / (n * (n - 1)))
    n_rho = float(n / (1.0 + (n - 1) * rho)) if 1.0 + (n - 1) * rho > 0 else float("nan")
    return {"n": n, "n_eff": n_eff, "rho": rho, "n_rho": n_rho}


def run_segment(data: Path, seg: str, cal: dict) -> dict:
    piv = cs.load_daily(data, seg)
    days = piv["open"].index.values
    syms = list(piv["open"].columns)
    t0, t1 = int(days[0]), int(days[-1])
    fund = bu.load_funding(data, syms, t0, t1)
    fund["day"] = ((fund["t"] - 1) // bu.DAY_MS) * bu.DAY_MS
    fund_daily = fund.groupby(["day", "sym"])["rate"].sum().unstack().reindex(days).reindex(columns=syms)
    bars = bu.load_bar2345(data, syms, t0, t1)
    opn, qv = piv["open"], piv["qv"]
    ret = (opn.shift(-1) / opn - 1.0).values
    fnd = fund_daily.values
    col = {s: k for k, s in enumerate(syms)}
    n_bars = piv["close"].notna().cumsum()
    last_bar = {s: piv["close"][s].last_valid_index() for s in syms}
    keys = [(h, u) for h in HYPS for u in UNIVERSES]
    daily = {k: {r: np.zeros(len(days)) for r in COST_ROWS} for k in keys}
    mkt = {k: np.full(len(days), np.nan) for k in keys}
    cost_paid = {k: 0.0 for k in keys}
    turnover = {k: [] for k in keys}
    sizes = {k: [] for k in keys}
    legs = {k: [0, 0] for k in keys}  # traded legs, legs over max participation
    flags = {"missing_return_days": 0, "impact_bar_fallback": 0, "no_volume_cost_fallback": 0,
             "skipped_small_universe": {u: 0 for u in UNIVERSES},
             "data_end_while_held": {f"{h}:{u}": {"long": 0, "short": 0} for h, u in keys}}
    held = {k: {} for k in keys}
    first = SEGMENTS[seg][2]
    reb_by_hold = {hd: rebalance_days(days, first, hd) for hd in {v[1] for v in HYPS.values()}}
    rank_cache: dict = {}
    sig_cache: dict = {}
    spans = {}
    for h, (long_top, hold) in HYPS.items():
        reb = reb_by_hold[hold]
        for j, i in enumerate(reb):
            cut = i - 2
            if cut < 0:
                continue
            if i not in rank_cache:
                rank_cache[i] = eligible(piv, n_bars, i)
            if cut not in sig_cache:
                sig_cache[cut] = hyp_signals(piv, fund_daily, cut)
            sig = sig_cache[cut][h]
            end = reb[j + 1] if j + 1 < len(reb) else len(days) - 1
            bar_t = int(days[i - 1]) + 23 * 3_600_000 + 45 * 60_000
            v30 = qv.iloc[max(0, cut - 29): cut + 1].mean()
            for u in UNIVERSES:
                k = (h, u)
                universe = slice_universe(rank_cache[i], u)
                if not universe:
                    flags["skipped_small_universe"][u] += 1
                    w = {}
                else:
                    sizes[k].append(len(universe))
                    w = pick(sig, universe, long_top)
                    uc = [col[s] for s in universe]
                    for d in range(i, end):
                        r = ret[d, uc]
                        mkt[k][d] = float(np.nanmean(r)) if np.isfinite(r).any() else 0.0
                prev = held[k]
                to = 0.0
                for s in sorted(set(w) | set(prev)):
                    dw = abs(w.get(s, 0.0) - prev.get(s, 0.0))
                    if dw == 0:
                        continue
                    b = bars.get((s, bar_t))
                    if b is None or b <= 0:
                        flags["impact_bar_fallback"] += 1
                        dq = qv.iloc[i - 1][s]
                        if not (dq > 0):
                            dq = float(np.nanmean(qv[s].iloc[max(0, i - 30): i].values))
                            flags["no_volume_cost_fallback"] += 1
                        b = dq / 96.0 if dq > 0 else 1.0
                    q15 = float(v30[s]) / 96.0 if not math.isnan(v30[s]) else 0.0
                    legs[k][0] += 1
                    if bu.ORDER_VALUE / b > cal["maxp"]:
                        legs[k][1] += 1
                    for row in COST_ROWS:
                        daily[k][row][i] -= dw * side_cost(row, cal, b, q15)
                    cost_paid[k] += dw * side_cost("cal", cal, b, q15)
                    to += dw
                turnover[k].append(to)
                for s, ws in w.items():
                    lb = last_bar[s]
                    if lb is not None and days[i] <= lb < days[end]:
                        flags["data_end_while_held"][f"{h}:{u}"]["long" if ws > 0 else "short"] += 1
                cur = dict(w)
                for d in range(i, end):
                    pnl = 0.0
                    for s, ws in cur.items():
                        r = ret[d, col[s]]
                        if math.isnan(r):
                            flags["missing_return_days"] += 1
                            r = 0.0
                        f = fnd[d, col[s]]
                        pnl += ws * r - ws * (0.0 if math.isnan(f) else f)
                        cur[s] = ws * (1.0 + r)
                    for row in COST_ROWS:
                        daily[k][row][d] += pnl
                held[k] = cur
        spans[h] = reb[0] if reb else 0
    # common comparison span: from the first rebalance of the shortest hold on, to the last day
    start = min(spans.values())
    span = slice(start, len(days) - 1)
    # effective independent names on the Monday grid (all universes)
    neff = {u: [] for u in UNIVERSES}
    for i in reb_by_hold[7]:
        if i - 2 not in range(len(days)) or i - 2 < NEFF_DAYS:
            continue
        ranked = rank_cache.get(i) or eligible(piv, n_bars, i)
        for u in UNIVERSES:
            uni = slice_universe(ranked, u)
            if uni:
                e = effective_names(piv["close"], i - 2, uni)
                if e:
                    neff[u].append(e)
    return {"days": [int(x) for x in days[span]], "span": span,
            "daily": {k: {r: v[span] for r, v in daily[k].items()} for k in keys},
            "mkt": {k: v[span] for k, v in mkt.items()},
            "turnover": {k: float(np.mean(v)) if v else 0.0 for k, v in turnover.items()},
            "cost_paid": cost_paid, "legs": legs, "sizes": sizes, "neff": neff, "flags": flags}


def neff_summary(rows: list) -> dict:
    if not rows:
        return {"rebalances": 0}
    out = {"rebalances": len(rows)}
    for key in ("n", "n_eff", "rho", "n_rho"):
        v = np.array([r[key] for r in rows], float)
        out[key] = {"mean": float(np.nanmean(v)), "min": float(np.nanmin(v)), "max": float(np.nanmax(v))}
    return out


def flag(row: dict, null_sr: float) -> str:
    s1 = row["S1"]["cal"]
    ok = (s1["sr_ann"] > null_sr and s1["alpha_t"] >= 2 and min(row["S1"]["halves_sr_ann"]) > 0
          and row["S2"]["cal"]["sr_ann"] > 0.5 and row["S1"]["cal_x2"]["sr_ann"] > 0
          and row["S1"]["cal_tail_x2"]["sr_ann"] > 0)
    return "DEV_PROMISING" if ok else "NOT_PROMISING"


def min_detectable_sr(years: float, n_tests: int) -> float:
    z = NormalDist().inv_cdf
    return (z(1.0 - 0.05 / n_tests) + z(0.80)) / math.sqrt(years)


def prior_trials() -> int:
    return sum(json.loads(ln).get("dev_evaluated", 0) for ln in TRIALS.read_text().splitlines() if ln.strip())


def ever_w200(data: Path) -> list:
    """Symbols that are in the W200 universe at any rebalance of either grid (for the funding/15m download)."""
    ever = set()
    for seg in SEGMENTS:
        piv = cs.load_daily(data, seg)
        days = piv["open"].index.values
        n_bars = piv["close"].notna().cumsum()
        for hold in {v[1] for v in HYPS.values()}:
            for i in rebalance_days(days, SEGMENTS[seg][2], hold):
                ever |= set(slice_universe(eligible(piv, n_bars, i), "W200"))
    return sorted(ever)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--write-universe", action="store_true", help="only write <data-dir>/ever_w200.json and exit")
    ap.add_argument("--out", type=Path, default=EVID / "wide_universe_dev.json")
    a = ap.parse_args(argv)
    if a.write_universe:
        syms = ever_w200(a.data_dir)
        (a.data_dir / "ever_w200.json").write_text(json.dumps(syms))
        print(len(syms), "symbols")
        return 0
    cal = bu.load_cost_calibration(CALIBRATION)
    cal = {**cal["calib"], "maxp": cal["maxp"]}
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "helper_sha256": bu.sha(HERE / "broad_universe_dev.py"), "calibration_sha256": bu.sha(CALIBRATION),
           "floor_usdt_per_day": FLOOR, "segments": {}, "hypotheses": {}}
    segs = {s: run_segment(a.data_dir, s, cal) for s in SEGMENTS}
    for s, r in segs.items():
        out["segments"][s] = {"first_day": r["days"][0], "last_day": r["days"][-1], "n_days": len(r["days"]),
                              "flags": r["flags"],
                              "effective_names": {u: neff_summary(r["neff"][u]) for u in UNIVERSES}}
    s1_years = len(segs["S1"]["days"]) / 365.0
    prior = prior_trials()
    out["null_max_sr_ann"] = {f"N={N_TRIALS}": expected_max_sr(N_TRIALS) / math.sqrt(s1_years),
                              f"N={prior + N_TRIALS} (dev_search_trials.jsonl sum + this)":
                              expected_max_sr(prior + N_TRIALS) / math.sqrt(s1_years)}
    null_sr = out["null_max_sr_ann"][f"N={N_TRIALS}"]
    null_cum = out["null_max_sr_ann"][f"N={prior + N_TRIALS} (dev_search_trials.jsonl sum + this)"]
    out["power"] = {"s1_years": s1_years, "min_detectable_annual_sr_80pct": min_detectable_sr(s1_years, N_TRIALS),
                    "note": "normal approximation on the portfolio series, Bonferroni a = 0.05/15; independent of the number of names"}
    ne = {u: out["segments"]["S1"]["effective_names"][u] for u in UNIVERSES}
    ratio = (math.sqrt(ne["W200"]["n_eff"]["mean"] / ne["U50"]["n_eff"]["mean"])
             if ne["W200"].get("n_eff") and ne["U50"].get("n_eff") else None)
    out["breadth"] = {"s1_sr_ratio_W200_over_U50_heuristic": ratio, "per_hypothesis": {}}
    for h, (_, hold) in HYPS.items():
        p = 365.0 / hold
        d = {}
        for u in ("U50", "W200"):
            n_eff = ne[u]["n_eff"]["mean"]
            d[u] = {"n_eff_S1": n_eff, "periods_per_year": p, "ic_needed_for_null_max": null_sr / math.sqrt(n_eff * p),
                    "ic_needed_for_cumulative_null": null_cum / math.sqrt(n_eff * p)}
        out["breadth"]["per_hypothesis"][h] = d
    for (h, u) in [(h, u) for h in HYPS for u in UNIVERSES]:
        row = {}
        for s, r in segs.items():
            k = (h, u)
            mk = r["mkt"][k]
            row[s] = {c: bu.stats(v, mk) for c, v in r["daily"][k].items()}
            row[s]["mean_turnover_per_rebalance"] = r["turnover"][k]
            row[s]["cost_paid_pct_of_capital"] = r["cost_paid"][k] * 100
            legs, over = r["legs"][k]
            row[s]["participation_over_max_share"] = over / legs if legs else None
            row[s]["universe_size_mean_min_max"] = ([float(np.mean(r["sizes"][k])), min(r["sizes"][k]), max(r["sizes"][k])]
                                                    if r["sizes"][k] else None)
            x = r["daily"][k]["cal"]
            half = len(x) // 2
            row[s]["halves_sr_ann"] = [bu.stats(x[:half], mk[:half])["sr_ann"], bu.stats(x[half:], mk[half:])["sr_ann"]]
        row["flag"] = flag(row, null_sr)
        row["s1_sr_above_cumulative_null"] = row["S1"]["cal"]["sr_ann"] > null_cum
        out["hypotheses"][f"{u}:{h}"] = row
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("null max SR", {k: round(v, 2) for k, v in out["null_max_sr_ann"].items()}, "prior trials", prior,
          "MDE", round(out["power"]["min_detectable_annual_sr_80pct"], 2))
    for s in SEGMENTS:
        print(s, {u: (round(v["n_eff"]["mean"], 1), round(v["rho"]["mean"], 2), round(v["n"]["mean"], 1))
                  for u, v in out["segments"][s]["effective_names"].items() if v.get("rebalances")})
    for key, row in out["hypotheses"].items():
        line = [key, row["flag"]]
        for s in SEGMENTS:
            for c in ("cal", "cal_x2", "cal_tail_x2", "cost0"):
                st = row[s][c]
                line.append(f"{s}-{c} SR {st['sr_ann']:.2f} ret {st['ann_return_pct']:.1f}% dd {st['max_dd_pct']:.0f}% at {st['alpha_t']:.2f}")
        line.append(f"S1 halves {[round(v, 2) for v in row['S1']['halves_sr_ann']]}")
        line.append(f"to {row['S1']['mean_turnover_per_rebalance']:.2f} cost {row['S1']['cost_paid_pct_of_capital']:.0f}%"
                    f" over5% {row['S1']['participation_over_max_share']}")
        print(" | ".join(str(x) for x in line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
