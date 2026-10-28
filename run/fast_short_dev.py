#!/usr/bin/env python3
"""Fast short-horizon strategies vs the cost wall, DEV only, measured alt cost (ADR 0045).

Runs exactly the 20 configs fixed in
``run/audit/evidence_fast_short_20261028/prereg_fast_short_v1.json``
(committed before any signal or P&L was computed) on the 15 full-history
alts of the project 15m archive, plus the descriptive break-even analysis.

Never reads: 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards (rows
are filtered by timestamp the moment a monthly file is parsed).  No orders,
no private API, no NEW2.

Usage (archive unpacked: ``klines/`` and ``funding/`` directories)::

    python3 run/fast_short_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import heapq
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

VERSION = "fast-short-dev-v1"
EVID = HERE / "audit" / "evidence_fast_short_20261028"
PREREG = EVID / "prereg_fast_short_v1.json"
COST_RECORD = HERE / "audit" / "alt_cost_measured_20261027" / "estimate_v3_alts.json"
CALIBRATION = HERE / "audit" / "lowliq_cost_20261027" / "calibration_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"
SEGMENTS = bu.SEGMENTS
SYMBOLS = ["ADAUSDT", "AVAXUSDT", "BCHUSDT", "BNBUSDT", "DOGEUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT",
           "SOLUSDT", "TRXUSDT", "UNIUSDT", "XLMUSDT", "XMRUSDT", "XRPUSDT", "ZECUSDT"]
BAR_MS = 900_000
BARS_DAY = 96
WEIGHT = 0.05
MAX_OPEN = 20
LOOKBACK_H = 168
MIN_TRADES = 300
MAKER_FEE = 0.0002
Q30_BARS = 2880
GAP_FILL_BARS = 96
COST_ROWS = ("measured", "measured_x2", "volume_scaled", "assumed_public_v1", "cost0",
             "maker_both", "maker_entry_taker_exit")
HORIZONS = {"15m": 1, "1h": 4, "4h": 16, "8h": 32, "1d": 96}


def make_configs() -> list:
    cfgs = []
    for d in ("fade", "follow"):
        for k in (2.0, 3.0):
            for h in (4, 16):
                cfgs.append({"id": f"F1_{d}_k{k:.1f}_H{h}", "fam": "F1", "dir": d, "k": k, "H": h})
    for d in ("follow", "fade"):
        for h in (4, 16):
            cfgs.append({"id": f"F2_{d}_H{h}", "fam": "F2", "dir": d, "k": 2.0, "H": h})
    for th in (0.0003, 0.0006):
        for pre, post, h in ((15, 15, 2), (60, 60, 8)):
            cfgs.append({"id": f"F3_th{round(th * 1e4)}_w{pre}", "fam": "F3", "theta": th, "pre": pre, "post": post, "H": h})
    for d in ("reversal", "momentum"):
        for lb in (4, 16):
            cfgs.append({"id": f"F4_{d}_L{lb}", "fam": "F4", "dir": d, "L": lb, "H": 16})
    return cfgs


# ---------------------------------------------------------------- data

def month_range(name: str) -> tuple:
    y, m = int(name.rsplit("-", 2)[-2]), int(name.rsplit("-", 2)[-1].split(".")[0])
    lo = bu.ms(f"{y:04d}-{m:02d}-01")
    hi = bu.ms(f"{y + (m == 12):04d}-{m % 12 + 1:02d}-01")
    return lo, hi


def load_segment(data: Path, seg: str) -> dict:
    lo, hi, first = SEGMENTS[seg]
    t0, t1 = bu.ms(lo), bu.ms(hi) + bu.DAY_MS - 1
    bu.assert_allowed(t0, t1)
    T0 = t0
    n_days = int(round((t1 + 1 - t0) / bu.DAY_MS))
    nb = n_days * BARS_DAY
    ns = len(SYMBOLS)
    arr = {k: np.full((nb, ns), np.nan) for k in ("open", "close", "qv", "tbq")}
    fund = []
    for k, sym in enumerate(SYMBOLS):
        for p in sorted((data / "klines").glob(f"{sym}-15m-*.csv")):
            m0, m1 = month_range(p.stem)
            if m1 <= t0 or m0 > t1:
                continue
            with p.open() as fh:
                has_header = fh.readline().startswith("open_time")
            df = pd.read_csv(p, usecols=[0, 1, 4, 7, 10], header=0 if has_header else None,
                             names=["t", "open", "close", "qv", "tbq"])
            df = df[(df["t"] >= t0) & (df["t"] <= t1)]
            idx = ((df["t"].values - T0) // BAR_MS).astype(int)
            for c in ("open", "close", "qv", "tbq"):
                arr[c][idx, k] = df[c].values
        ts, rate = [], []
        for p in sorted((data / "funding").glob(f"{sym}-fundingRate-*.csv")):
            m0, m1 = month_range(p.stem)
            if m1 <= t0 or m0 > t1:
                continue
            for f in bu.read_rows(p):
                t = int(f[0])
                if t0 <= t <= t1:
                    ts.append(int(round(t / BAR_MS)) * BAR_MS)
                    rate.append(float(f[2]))
        o = np.argsort(ts)
        fund.append((np.array(ts, dtype=np.int64)[o], np.array(rate)[o]))
    return {"T0": T0, "n_days": n_days, "nb": nb, "arr": arr, "fund": fund,
            "start_bar": int(round((bu.ms(first) - T0) / bu.DAY_MS)) * BARS_DAY}


# ---------------------------------------------------------------- signals

def hourly_z(x: np.ndarray, idx_h: np.ndarray, centred: bool) -> np.ndarray:
    """z at hourly decision bars: x / sigma (or (x-mu)/sigma) with mu, sigma from the previous 168 hourly samples."""
    h = pd.DataFrame(x[idx_h])
    sig = h.shift(1).rolling(LOOKBACK_H, min_periods=LOOKBACK_H).std()
    mu = h.shift(1).rolling(LOOKBACK_H, min_periods=LOOKBACK_H).mean() if centred else 0.0
    return ((h - mu) / sig.where(sig > 0)).values


def events_for(cfg: dict, seg: dict) -> list:
    """(decision bar j, symbol k, side, score, hold) for one config; bars <= j only."""
    a, nb, ns = seg["arr"], seg["nb"], len(SYMBOLS)
    ev = []
    if cfg["fam"] in ("F1", "F2"):
        idx_h = np.arange(3, nb, 4)
        if cfg["fam"] == "F1":
            r1 = np.full((nb, ns), np.nan)
            r1[3:] = np.log(a["close"][3:] / a["open"][:-3])
            z = hourly_z(r1, idx_h, centred=False)
        else:
            qs = pd.DataFrame(a["qv"]).rolling(4, min_periods=4).sum().values
            ts = pd.DataFrame(a["tbq"]).rolling(4, min_periods=4).sum().values
            tb = np.where(qs > 0, ts / np.where(qs > 0, qs, 1.0), np.nan)
            z = hourly_z(tb, idx_h, centred=True)
        for m, k in np.argwhere(np.abs(z) >= cfg["k"]):
            s = np.sign(z[m, k])
            side = -s if (cfg["dir"] == "fade") else s
            ev.append((int(idx_h[m]), int(k), float(side), float(abs(z[m, k])), cfg["H"]))
    elif cfg["fam"] == "F3":
        pre, post = cfg["pre"] * 60_000, cfg["post"] * 60_000
        for k, (ts, rate) in enumerate(seg["fund"]):
            for i in range(1, len(ts)):
                fp = rate[i - 1]
                if abs(fp) < cfg["theta"] - 1e-12:
                    continue
                e0 = int((ts[i] - pre - seg["T0"]) // BAR_MS)
                if e0 < 1:
                    continue
                ev.append((e0 - 1, k, -1.0 if fp > 0 else 1.0, abs(fp), cfg["H"]))
    else:
        lb = cfg["L"]
        for j in np.arange(15, nb, 16):
            if j - lb + 1 < 0:
                continue
            sc = np.log(a["close"][j] / a["open"][j - lb + 1])
            ok = np.where(np.isfinite(sc))[0]
            if len(ok) < 8:
                continue
            order = ok[np.argsort(sc[ok], kind="stable")]
            low, high = order[:3], order[-3:]
            lo_side, hi_side = (1.0, -1.0) if cfg["dir"] == "reversal" else (-1.0, 1.0)
            ev += [(int(j), int(k), lo_side, 1.0, cfg["H"]) for k in low]
            ev += [(int(j), int(k), hi_side, 1.0, cfg["H"]) for k in high]
    ev.sort(key=lambda e: (e[0], -e[3], SYMBOLS[e[1]]))
    return ev


# ---------------------------------------------------------------- simulation

def make_cost_fns(costs: dict) -> dict:
    meas, ass, cal = costs["measured"], costs["assumed"], costs["volume_scaled"]

    def one(row: str, b: float, q15: float, entry: bool) -> float:
        if row == "cost0":
            return 0.0
        if row == "volume_scaled":
            return bu.side_cost(b, cal, "", q15)
        if row == "assumed_public_v1":
            return bu.side_cost(b, ass, "")
        if row == "maker_both":
            return MAKER_FEE
        if row == "maker_entry_taker_exit":
            return MAKER_FEE if entry else bu.side_cost(b, meas, "")
        v = bu.side_cost(b, meas, "")
        return 2.0 * v if row == "measured_x2" else v
    return {"one": one, "maxp": meas["maxp"]}


def simulate(events: list, seg: dict, ctx: dict) -> dict:
    ns, nb = len(SYMBOLS), seg["nb"]
    op, qf, q30 = seg["arr"]["open"], ctx["qf"], ctx["q30"]
    busy = np.zeros(ns, int)
    live: list = []
    trades = []
    cnt = {"skipped_capacity": 0, "skipped_invalid": 0, "participation_over_max": 0}
    i = 0
    while i < len(events):
        j = events[i][0]
        grp = []
        while i < len(events) and events[i][0] == j:
            grp.append(events[i])
            i += 1
        e = j + 1
        while live and live[0] <= e:
            heapq.heappop(live)
        for (_, k, side, _, hold) in grp:
            if busy[k] > e:
                continue
            x = e + hold
            if e < seg["start_bar"] or x > nb - 1 or not (op[e, k] > 0 and op[x, k] > 0):
                cnt["skipped_invalid"] += 1
                continue
            be, bx = qf[e - 1, k], qf[x - 1, k]
            if not (be > 0 and bx > 0):
                cnt["skipped_invalid"] += 1
                continue
            if len(live) >= MAX_OPEN:
                cnt["skipped_capacity"] += 1
                continue
            busy[k] = x
            heapq.heappush(live, x)
            ts, cum = ctx["fund_cum"][k]
            te, tx = seg["T0"] + e * BAR_MS, seg["T0"] + x * BAR_MS
            f = cum[np.searchsorted(ts, tx, "left")] - cum[np.searchsorted(ts, te, "right")]
            gross = side * (op[x, k] / op[e, k] - 1.0) - side * f
            if bu.ORDER_VALUE / min(be, bx) > ctx["fns"]["maxp"]:
                cnt["participation_over_max"] += 1
            qe, qx = q30[e - 1, k], q30[x - 1, k]
            qe = qe if qe > 0 else be
            qx = qx if qx > 0 else bx
            ce = {r: ctx["fns"]["one"](r, be, qe, True) for r in COST_ROWS}
            cx = {r: ctx["fns"]["one"](r, bx, qx, False) for r in COST_ROWS}
            trades.append({"k": k, "e": e, "x": x, "side": side, "gross": gross, "cost_e": ce, "cost_x": cx,
                           "cost": {r: ce[r] + cx[r] for r in COST_ROWS}})
    return {"trades": trades, **cnt}


def daily_series(trades: list, seg: dict) -> tuple:
    n = seg["n_days"]
    gross = np.zeros(n)
    cost = {r: np.zeros(n) for r in COST_ROWS}
    for t in trades:
        de, dx = t["e"] // BARS_DAY, t["x"] // BARS_DAY
        gross[dx] += WEIGHT * t["gross"]
        for r in COST_ROWS:
            cost[r][de] -= WEIGHT * t["cost_e"][r]
            cost[r][dx] -= WEIGHT * t["cost_x"][r]
    return gross, cost


def trade_stats(trades: list, row: str = "measured") -> dict:
    if not trades:
        return {"n": 0}
    g = np.array([t["gross"] for t in trades])
    c = np.array([t["cost"][row] for t in trades])
    side = np.array([t["side"] for t in trades])
    net = g - c
    sd = float(net.std(ddof=1)) if len(net) > 1 else float("nan")
    return {"n": len(trades), "n_long": int((side > 0).sum()), "n_short": int((side < 0).sum()),
            "mean_gross_bp": float(g.mean() * 1e4), "mean_cost_bp": float(c.mean() * 1e4),
            "mean_net_bp": float(net.mean() * 1e4), "hit_rate": float((net > 0).mean()),
            "sd_net_bp": sd * 1e4, "t_trade_naive": float(net.mean() / sd * math.sqrt(len(net))) if sd > 0 else 0.0,
            "breakeven_cost_multiple": float(g.mean() / c.mean()) if c.mean() > 0 else None,
            "breakeven_per_side_bp": float(g.mean() / 2 * 1e4),
            "mean_net_bp_by_row": {r: float((g - np.array([t["cost"][r] for t in trades])).mean() * 1e4) for r in COST_ROWS}}


def run_segment(data: Path, seg_name: str, cfgs: list, costs: dict) -> dict:
    seg = load_segment(data, seg_name)
    a = seg["arr"]
    qf = pd.DataFrame(a["qv"]).where(lambda d: d > 0).ffill(limit=GAP_FILL_BARS).values
    q30 = pd.DataFrame(a["qv"]).rolling(Q30_BARS, min_periods=480).mean().values
    fund_cum = []
    for ts, rate in seg["fund"]:
        fund_cum.append((ts, np.concatenate([[0.0], np.cumsum(rate)])))
    ctx = {"qf": qf, "q30": q30, "fund_cum": fund_cum, "fns": make_cost_fns(costs)}
    days = seg["n_days"]
    # equal-weight open-to-open daily market return
    op = a["open"]
    ref = np.array([op[min((d + 1) * BARS_DAY, seg["nb"] - 1)] / op[d * BARS_DAY] - 1.0 for d in range(days)])
    mkt = np.array([np.nanmean(r) if np.isfinite(r).any() else 0.0 for r in ref])
    start_day = seg["start_bar"] // BARS_DAY
    out = {}
    for cfg in cfgs:
        res = simulate(events_for(cfg, seg), seg, ctx)
        res["gross_d"], res["cost_d"] = daily_series(res["trades"], seg)
        out[cfg["id"]] = res
    return {"seg": seg, "mkt": mkt, "start_day": start_day, "runs": out, "ctx": ctx}


# ---------------------------------------------------------------- break-even analysis

def horizon_table(seg: dict, costs: dict) -> dict:
    op, qv = seg["arr"]["open"], seg["arr"]["qv"]
    meas = costs["measured"]
    rt = []
    for k in range(len(SYMBOLS)):
        q = np.nanmedian(qv[:, k])
        if q > 0:
            rt.append(2.0 * bu.side_cost(q, meas, "") * 1e4)
    cost_rt = float(np.median(rt))
    rows = {}
    for name, h in HORIZONS.items():
        step = 1 if h <= 4 else 4
        i0 = np.arange(0, len(op) - h, step)
        r = (op[i0 + h] / op[i0] - 1.0).ravel()
        r = r[np.isfinite(r)] * 1e4
        ma, sd = float(np.abs(r).mean()), float(r.std(ddof=1))
        rows[name] = {"n": int(len(r)), "median_abs_bp": float(np.median(np.abs(r))), "mean_abs_bp": ma, "sd_bp": sd,
                      "cost_over_mean_abs": cost_rt / ma, "hit_rate_to_break_even": 0.5 + cost_rt / (2 * ma),
                      "trades_for_t2_at_net_5bp": (2 * sd / 5.0) ** 2, "trades_for_t2_at_net_10bp": (2 * sd / 10.0) ** 2}
    return {"median_symbol_round_trip_cost_bp": cost_rt, "horizons": rows}


# ---------------------------------------------------------------- driver

def flag(row: dict, null_sr: float) -> str:
    s1 = row["S1"]["measured"]
    ok = (s1["sr_ann"] > null_sr and s1["alpha_t"] >= 2 and min(row["S1"]["halves_sr_ann"]) > 0
          and row["S2"]["measured"]["sr_ann"] > 0.5 and row["S1"]["volume_scaled"]["sr_ann"] > 0
          and row["S1"]["trades"]["n"] >= MIN_TRADES)
    return "DEV_PROMISING" if ok else "NOT_PROMISING"


def prior_trials() -> int:
    return sum(json.loads(l).get("dev_evaluated", 0) for l in TRIALS.read_text().splitlines() if l.strip())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "fast_short_dev.json")
    a = ap.parse_args(argv)
    cfgs = make_configs()
    costs = {"measured": bu.load_cost_record(COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(CALIBRATION)}
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "helper_sha256": bu.sha(HERE / "broad_universe_dev.py"), "cost_record_sha256": bu.sha(COST_RECORD),
           "calibration_sha256": bu.sha(CALIBRATION), "cost_profile_sha256": bu.sha(bu.COST_PROFILE),
           "segments": {}, "configs": {}, "horizon_table": {}}
    segs = {s: run_segment(a.data_dir, s, cfgs, costs) for s in SEGMENTS}
    for s, r in segs.items():
        sd = r["start_day"]
        out["horizon_table"][s] = horizon_table(r["seg"], costs)
        out["segments"][s] = {"n_days": r["seg"]["n_days"], "first_entry_day": sd,
                              "market_ew": bu.stats(r["mkt"][sd:], r["mkt"][sd:])}
    s1_years = (segs["S1"]["seg"]["n_days"] - segs["S1"]["start_day"]) / 365.0
    n_cfg = len(cfgs)
    prior = prior_trials()
    out["null_max_sr_ann"] = {f"N={n_cfg}": expected_max_sr(n_cfg) / math.sqrt(s1_years),
                              f"N={bu.PRIOR_TRIALS_REF + n_cfg} (reference)": expected_max_sr(bu.PRIOR_TRIALS_REF + n_cfg) / math.sqrt(s1_years),
                              f"N={prior + n_cfg} (dev_search_trials.jsonl sum + this)": expected_max_sr(prior + n_cfg) / math.sqrt(s1_years)}
    null_sr = out["null_max_sr_ann"][f"N={n_cfg}"]
    for cfg in cfgs:
        row = {"params": cfg}
        for s, r in segs.items():
            run, sd, mk = r["runs"][cfg["id"]], r["start_day"], r["mkt"]
            sl = slice(sd, None)
            d = {c: bu.stats((run["gross_d"] + run["cost_d"][c])[sl], mk[sl]) for c in COST_ROWS}
            net = (run["gross_d"] + run["cost_d"]["measured"])[sl]
            half = len(net) // 2
            d["halves_sr_ann"] = [bu.stats(net[:half], mk[sl][:half])["sr_ann"], bu.stats(net[half:], mk[sl][half:])["sr_ann"]]
            d["trades"] = trade_stats(run["trades"])
            d["skipped"] = {k: run[k] for k in ("skipped_capacity", "skipped_invalid", "participation_over_max")}
            tot = net.sum()
            d["top10_days_share_of_net"] = float(np.sort(net)[-10:].sum() / tot) if tot > 0 else None
            row[s] = d
        row["flag"] = flag(row, null_sr)
        out["configs"][cfg["id"]] = row
    s1g = [(cid, r["S1"]["trades"]["mean_gross_bp"]) for cid, r in out["configs"].items() if r["S1"]["trades"]["n"] >= MIN_TRADES]
    best = max(s1g, key=lambda x: x[1]) if s1g else None
    out["conditions"] = {"best_s1_gross_bp_per_trade": best,
                         "max_cost0_s1_sr": max(r["S1"]["cost0"]["sr_ann"] for r in out["configs"].values()),
                         "null_max_sr_ann_n20": null_sr}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("null max SR", {k: round(v, 2) for k, v in out["null_max_sr_ann"].items()}, "prior trials", prior)
    for s in SEGMENTS:
        print(s, "market SR", round(out["segments"][s]["market_ew"]["sr_ann"], 2),
              "median RT cost bp", round(out["horizon_table"][s]["median_symbol_round_trip_cost_bp"], 1))
    for cid, row in out["configs"].items():
        s1, s2 = row["S1"], row["S2"]
        t = s1["trades"]
        print(cid, row["flag"], f"S1 n={t.get('n', 0)} gross {t.get('mean_gross_bp', 0):.1f}bp cost {t.get('mean_cost_bp', 0):.1f} net {t.get('mean_net_bp', 0):.1f}",
              f"SR {s1['measured']['sr_ann']:.2f} c0 {s1['cost0']['sr_ann']:.2f} mk {s1['maker_both']['sr_ann']:.2f} vs {s1['volume_scaled']['sr_ann']:.2f}",
              f"| S2 n={s2['trades'].get('n', 0)} SR {s2['measured']['sr_ann']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
