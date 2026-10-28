#!/usr/bin/env python3
"""High/low line + volume-spike strategy on 15m/1h bars, DEV only (ADR 0044).

Runs exactly the 48 configs fixed in
``run/audit/evidence_hilo_intraday_20261028/prereg_hilo_intraday_v1.json``
(committed before any 15m/1h data was downloaded) on the point-in-time top-50
universe of ADR 0035.  Line families: rolling N-bar extremes (ROLL) and
confirmed swing points (SWING); rules: fade (F) and breakout (BO).

Never reads: 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards; BTCUSDT
in S2.  No orders, no private API, no NEW2.

Usage (data from run/broad_universe_fetch.py, steps daily/funding/m15)::

    python3 run/hilo_intraday_dev.py --data-dir <dir>
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
import hilo_volume_dev as hv  # noqa: E402
from broad_universe_dev import expected_max_sr  # noqa: E402

VERSION = "hilo-intraday-dev-v1"
EVID = HERE / "audit" / "evidence_hilo_intraday_20261028"
PREREG = EVID / "prereg_hilo_intraday_v1.json"
COST_RECORD = hv.COST_RECORD
CALIBRATION = hv.CALIBRATION
TRIALS = hv.TRIALS
SEGMENTS = bu.SEGMENTS
WEIGHT = hv.WEIGHT
MAX_OPEN = hv.MAX_OPEN
MIN_TRADES = 200
BAR_MS = 15 * 60_000
PER_DAY = 96
A_BAND = 0.10
COST_ROWS = hv.COST_ROWS
#: timeframe -> (15m bars per bar, volume baseline bars, line lengths N, swing k, hold bars (short, long), swing age cap)
TF = {
    "15m": {"r": 1, "w": 96, "n": (96, 672), "k": (4, 16), "h": (4, 16), "cap": 672},
    "1h": {"r": 4, "w": 24, "n": (168, 480), "k": (4, 12), "h": (4, 24), "cap": 168},
}
M_GRID = (2.0, 3.0)


def make_configs() -> list:
    cfgs = []
    for fam, mode, key in (("FR", "F", "n"), ("FS", "F", "k"), ("BR", "BO", "n"), ("BS", "BO", "k")):
        for tf, d in TF.items():
            holds = d["h"] if mode == "F" else (d["h"][1],)
            for par, m, h in itertools.product(d[key], M_GRID, holds):
                cfgs.append({"id": f"{fam}_{tf}_{'N' if key == 'n' else 'k'}{par}_m{m:.1f}_H{h}", "fam": fam, "tf": tf,
                             "line": "ROLL" if key == "n" else "SWING", "par": par, "m": m, "H": h, "mode": mode})
    return cfgs


def load_m15(data: Path, seg: str, syms: list) -> dict:
    """Aligned 15m matrices (bars x symbols) for the segment; missing bars stay NaN."""
    lo, hi, _ = SEGMENTS[seg]
    t0, t1 = bu.ms(lo), bu.ms(hi) + bu.DAY_MS - BAR_MS
    bu.assert_allowed(t0, t1)
    n = (t1 - t0) // BAR_MS + 1
    out = {k: np.full((n, len(syms)), np.nan) for k in ("open", "high", "low", "close", "qv")}
    for j, s in enumerate(syms):
        if seg == "S2" and s == "BTCUSDT":
            continue
        for p in sorted((data / "m15").glob(f"{s}-15m-*.npy")):
            a = np.load(p)
            a = a[(a[:, 0] >= t0) & (a[:, 0] <= t1)]
            if len(a) == 0:
                continue
            i = ((a[:, 0] - t0) // BAR_MS).astype(int)
            for c, name in enumerate(("open", "high", "low", "close", "qv"), start=1):
                out[name][i, j] = a[:, c]
    return out


def to_tf(m15: dict, r: int) -> dict:
    """tf bars from 15m bars: an hour with any missing 15m bar is NaN."""
    if r == 1:
        return {k: pd.DataFrame(v) for k, v in m15.items()}
    n = (m15["open"].shape[0] // r) * r
    g = {k: v[:n].reshape(n // r, r, -1) for k, v in m15.items()}
    return {"open": pd.DataFrame(g["open"][:, 0]), "high": pd.DataFrame(g["high"].max(axis=1)),
            "low": pd.DataFrame(g["low"].min(axis=1)), "close": pd.DataFrame(g["close"][:, -1]),
            "qv": pd.DataFrame(g["qv"].sum(axis=1))}


def roll_lines(bars: dict, n: int) -> tuple:
    """H/L of the N bars t-N..t-1 (the event bar is not part of its own lines)."""
    return (bars["high"].shift(1).rolling(n, min_periods=n).max(),
            bars["low"].shift(1).rolling(n, min_periods=n).min())


def swing_lines(bars: dict, k: int, cap: int) -> tuple:
    """Latest confirmed swing high/low known at t (swing bar j known from bar j+k closing), at most cap bars old."""
    hi, lo = bars["high"], bars["low"]
    sh = (hi >= hi.shift(1).rolling(k, min_periods=k).max()) & \
         (hi > hi.shift(-1).rolling(k, min_periods=k).max().shift(-(k - 1)))
    sl = (lo <= lo.shift(1).rolling(k, min_periods=k).min()) & \
         (lo < lo.shift(-1).rolling(k, min_periods=k).min().shift(-(k - 1)))
    h_val = hi.where(sh).shift(k)
    l_val = lo.where(sl).shift(k)
    return h_val.ffill(limit=cap - k), l_val.ffill(limit=cap - k)


def volume_ratio(bars: dict, w: int) -> pd.DataFrame:
    return bars["qv"] / bars["qv"].shift(1).rolling(w, min_periods=w).mean()


def signal_arrays(close: pd.DataFrame, lines: tuple, v: pd.DataFrame, m: float, mode: str) -> tuple:
    """(long_sig, short_sig, v) boolean/ratio arrays at event bar t, bars <= t only."""
    h, lo = lines
    rng = h - lo
    p = (close - lo) / rng.where(rng > 0)
    spike = v >= m
    if mode == "F":
        near_hi, near_lo = (p >= 1 - A_BAND) & spike, (p <= A_BAND) & spike
        return near_lo.values, near_hi.values, v.values
    return ((p >= 1) & spike).values, ((p <= 0) & spike).values, v.values


def simulate(cfg: dict, sig: tuple, mask_d: np.ndarray, ctx: dict) -> dict:
    """Run one config; sig is on tf bars, everything else on 15m intervals."""
    long_s, short_s, v = sig
    r, h15 = TF[cfg["tf"]]["r"], cfg["H"] * TF[cfg["tf"]]["r"]
    n15, n_sym = ctx["n15"], v.shape[1]
    n_days = ctx["n_days"]
    gross, cost = np.zeros(n_days), {c: np.zeros(n_days) for c in COST_ROWS}
    busy = np.zeros(n_sym, int)
    trades, skipped = [], 0
    ts, ks = np.nonzero(long_s | short_s)
    first = ctx["start"] * PER_DAY
    i0 = 0
    while i0 < len(ts):
        t = ts[i0]
        i1 = i0
        while i1 < len(ts) and ts[i1] == t:
            i1 += 1
        cols = ks[i0:i1]
        i0 = i1
        e = (t + 1) * r
        if e < first or e + h15 > n15 - 1 or e < 1:
            continue
        cand = [k for k in cols if mask_d[e // PER_DAY, k] and busy[k] <= e and not (long_s[t, k] and short_s[t, k])]
        if not cand:
            continue
        free = MAX_OPEN - int((busy > e).sum())
        order = sorted(cand, key=lambda k: (-v[t, k], ctx["syms"][k]))
        skipped += max(0, len(order) - max(free, 0))
        for k in order[:max(free, 0)]:
            side = 1.0 if long_s[t, k] else -1.0
            busy[k] = e + h15
            rr, ff = ctx["ret"][e:e + h15, k], ctx["fnd"][e:e + h15, k]
            nm = int(np.isnan(rr).sum())
            ctx["flags"]["missing_return_intervals"] += nm
            x = side * np.nan_to_num(rr) - side * np.nan_to_num(ff)
            np.add.at(gross, ctx["day"][e:e + h15], WEIGHT * x)
            tc = {c: 0.0 for c in COST_ROWS}
            for idx in (e, e + h15):
                b, q15 = ctx["impact"](k, idx)
                d = idx // PER_DAY
                for c in COST_ROWS:
                    cc = WEIGHT * ctx["side_cost"](b, c, q15)
                    cost[c][d] -= cc
                    tc[c] += cc / WEIGHT
            trades.append({"sym": ctx["syms"][k], "e": e, "side": side, "gross": float(x.sum()), "cost": tc["measured"]})
    return {"gross": gross, "cost": cost, "trades": trades, "skipped": skipped}


def run_segment(data: Path, seg: str, cfgs: list, costs: dict, ever: list) -> dict:
    daily = hv.load_ohlcv(data, seg)
    days = daily["open"].index.values
    mask_full, mondays = hv.universe_mask(daily, SEGMENTS[seg][2])
    cols = list(daily["open"].columns)
    sel = [cols.index(s) for s in ever if s in cols]
    syms = [cols[i] for i in sel]
    mask_d = mask_full[:, sel]
    start = mondays[0]
    t0, t1 = int(days[0]), int(days[-1])
    m15 = load_m15(data, seg, syms)
    n15 = m15["open"].shape[0]
    assert n15 == len(days) * PER_DAY
    fund = bu.load_funding(data, syms, t0, t1 + bu.DAY_MS - BAR_MS)
    fnd = np.zeros((n15, len(syms)))
    col_of = {s: j for j, s in enumerate(syms)}
    for s, t, rate in fund.itertuples(index=False):
        i = (t - 1 - t0) // BAR_MS  # funding at f belongs to the interval (open_i, open_i+1] containing f
        if 0 <= i < n15:
            fnd[i, col_of[s]] += rate
    opn = m15["open"]
    ret = np.full_like(opn, np.nan)
    ret[:-1] = opn[1:] / opn[:-1] - 1.0
    qv15 = m15["qv"]
    q30 = daily["qv"][syms].rolling(30, min_periods=10).mean().values
    day = np.arange(n15) // PER_DAY
    flags = {"missing_return_intervals": 0, "impact_bar_fallback": 0, "no_volume_cost_fallback": 0,
             "participation_over_max": 0, "no_q15_fallback": 0}
    dqv = daily["qv"][syms].values

    def impact(k: int, idx: int) -> tuple:
        b = qv15[idx - 1, k]
        if not b > 0:
            flags["impact_bar_fallback"] += 1
            seg_ = qv15[max(0, idx - 1 - PER_DAY): idx - 1, k]
            b = float(np.nanmean(seg_)) if np.isfinite(seg_).any() else float("nan")
            if not b > 0:
                dq = dqv[idx // PER_DAY - 1, k] if idx >= PER_DAY else float("nan")
                flags["no_volume_cost_fallback"] += 1
                b = dq / 96.0 if dq > 0 else 1.0
        if bu.ORDER_VALUE / b > costs["measured"]["maxp"]:
            flags["participation_over_max"] += 1
        q15 = q30[max(idx // PER_DAY - 1, 0), k] / 96.0
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

    ctx = {"start": start, "syms": syms, "ret": ret, "fnd": fnd, "impact": impact, "side_cost": side_cost,
           "flags": flags, "n15": n15, "n_days": len(days), "day": day}
    mkt_open = daily["open"][syms]
    mret = (mkt_open.shift(-1) / mkt_open - 1.0).values
    mkt = np.full(len(days), np.nan)
    for d in range(start, len(days) - 1):
        x = mret[d, mask_d[d]]
        mkt[d] = float(np.nanmean(x)) if np.isfinite(x).any() else 0.0
    out, cache = {}, {}
    for cfg in cfgs:
        tf = cfg["tf"]
        if tf not in cache:
            cache[tf] = {"bars": to_tf(m15, TF[tf]["r"]), "lines": {}}
            cache[tf]["v"] = volume_ratio(cache[tf]["bars"], TF[tf]["w"])
        c = cache[tf]
        lk = (cfg["line"], cfg["par"])
        if lk not in c["lines"]:
            c["lines"][lk] = roll_lines(c["bars"], cfg["par"]) if cfg["line"] == "ROLL" else \
                swing_lines(c["bars"], cfg["par"], TF[tf]["cap"])
        sig = signal_arrays(c["bars"]["close"], c["lines"][lk], c["v"], cfg["m"], cfg["mode"])
        out[cfg["id"]] = simulate(cfg, sig, mask_d, ctx)
        print(seg, cfg["id"], len(out[cfg["id"]]["trades"]), "trades", flush=True)
    span = slice(start, len(days))
    return {"days": [int(x) for x in days[span]], "mkt": mkt[span], "span": span, "runs": out, "flags": flags}


def flag(row: dict, null_sr: float) -> str:
    s1 = row["S1"]["measured"]
    ok = (s1["sr_ann"] > null_sr and s1["alpha_t"] >= 2 and min(row["S1"]["halves_sr_ann"]) > 0
          and row["S2"]["measured"]["sr_ann"] > 0.5 and row["S1"]["volume_scaled"]["sr_ann"] > 0
          and row["S1"]["trades"]["n"] >= MIN_TRADES)
    return "DEV_PROMISING" if ok else "NOT_PROMISING"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "hilo_intraday_dev.json")
    a = ap.parse_args(argv)
    cfgs = make_configs()
    assert len(cfgs) == 48
    ever = json.loads((a.data_dir / "ever_top50.json").read_text())
    costs = {"measured": bu.load_cost_record(COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(CALIBRATION)}
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "helper_sha256": [bu.sha(HERE / "broad_universe_dev.py"), bu.sha(HERE / "hilo_volume_dev.py")],
           "cost_record_sha256": bu.sha(COST_RECORD), "calibration_sha256": bu.sha(CALIBRATION),
           "cost_profile_sha256": bu.sha(bu.COST_PROFILE), "segments": {}, "configs": {}}
    segs = {s: run_segment(a.data_dir, s, cfgs, costs, ever) for s in SEGMENTS}
    for s, r in segs.items():
        out["segments"][s] = {"first_day": r["days"][0], "last_day": r["days"][-1], "n_days": len(r["days"]),
                              "flags": r["flags"], "market_ew": bu.stats(np.nan_to_num(r["mkt"]), r["mkt"])}
    s1_years = len(segs["S1"]["days"]) / 365.0
    n_cfg = len(cfgs)
    prior = hv.prior_trials()
    out["null_max_sr_ann"] = {f"N={n_cfg}": expected_max_sr(n_cfg) / math.sqrt(s1_years),
                              f"N={bu.PRIOR_TRIALS_REF + n_cfg} (reference)": expected_max_sr(bu.PRIOR_TRIALS_REF + n_cfg) / math.sqrt(s1_years),
                              f"N={prior + n_cfg} (dev_search_trials.jsonl sum + this)": expected_max_sr(prior + n_cfg) / math.sqrt(s1_years)}
    null_sr = out["null_max_sr_ann"][f"N={n_cfg}"]
    for cfg in cfgs:
        row = {"params": cfg}
        for s, r in segs.items():
            run, mk, sp = r["runs"][cfg["id"]], r["mkt"], r["span"]
            d = {c: bu.stats((run["gross"] + run["cost"][c])[sp], mk) for c in COST_ROWS}
            net = (run["gross"] + run["cost"]["measured"])[sp]
            half = len(net) // 2
            d["halves_sr_ann"] = [bu.stats(net[:half], mk[:half])["sr_ann"], bu.stats(net[half:], mk[half:])["sr_ann"]]
            d["trades"] = hv.trade_stats(run["trades"])
            d["skipped_capacity"] = run["skipped"]
            nd = len(net)
            d["trades_per_day"] = len(run["trades"]) / nd
            d["cost_drag_ann_pct"] = float(-run["cost"]["measured"][sp].sum() / nd * 365 * 100)
            d["mean_cost_bp_per_trade"] = float(np.mean([t["cost"] for t in run["trades"]]) * 1e4) if run["trades"] else None
            tot = net.sum()
            d["top10_days_share_of_net"] = float(np.sort(net)[-10:].sum() / tot) if tot > 0 else None
            row[s] = d
        row["flag"] = flag(row, null_sr)
        out["configs"][cfg["id"]] = row
    out["by_timeframe_s1_median"] = {}
    for tf in TF:
        rows = [r["S1"] for r in out["configs"].values() if r["params"]["tf"] == tf]
        out["by_timeframe_s1_median"][tf] = {
            "sr_cost0": float(np.median([x["cost0"]["sr_ann"] for x in rows])),
            "sr_measured": float(np.median([x["measured"]["sr_ann"] for x in rows])),
            "sr_measured_x2": float(np.median([x["measured_x2"]["sr_ann"] for x in rows])),
            "cost_drag_ann_pct": float(np.median([x["cost_drag_ann_pct"] for x in rows])),
            "trades_per_day": float(np.median([x["trades_per_day"] for x in rows])),
            "mean_cost_bp_per_trade": float(np.median([x["mean_cost_bp_per_trade"] or 0 for x in rows]))}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("null max SR", {k: round(v, 2) for k, v in out["null_max_sr_ann"].items()}, "prior trials", prior)
    for s in SEGMENTS:
        print(s, out["segments"][s]["flags"], "market SR", round(out["segments"][s]["market_ew"]["sr_ann"], 2))
    for cid, row in out["configs"].items():
        s1, s2 = row["S1"], row["S2"]
        print(cid, row["flag"], f"S1 n={s1['trades']['n']} gross {s1['trades'].get('mean_gross_bp', 0):.0f}bp net {s1['trades'].get('mean_net_bp', 0):.0f}bp",
              f"SR {s1['measured']['sr_ann']:.2f} c0 {s1['cost0']['sr_ann']:.2f} x2 {s1['measured_x2']['sr_ann']:.2f} vs {s1['volume_scaled']['sr_ann']:.2f} at {s1['measured']['alpha_t']:.2f}",
              f"halves {[round(x, 2) for x in s1['halves_sr_ann']]} drag {s1['cost_drag_ann_pct']:.0f}%/y | S2 n={s2['trades']['n']} SR {s2['measured']['sr_ann']:.2f}")
    print("by timeframe", json.dumps(out["by_timeframe_s1_median"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
