#!/usr/bin/env python3
"""High/low line + volume-spike strategy, DEV only, measured alt cost (ADR 0043).

Runs exactly the 17 configs fixed in
``run/audit/evidence_hilo_volume_20261028/prereg_hilo_volume_v1.json``
(committed before any data was downloaded) on the point-in-time top-50
universe of ADR 0035.

Never reads: 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards; BTCUSDT
in S2.  No orders, no private API, no NEW2.

Usage (data from run/broad_universe_fetch.py, steps daily/funding/bar2345)::

    python3 run/hilo_volume_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import datetime as dt
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

VERSION = "hilo-volume-dev-v1"
EVID = HERE / "audit" / "evidence_hilo_volume_20261028"
PREREG = EVID / "prereg_hilo_volume_v1.json"
COST_RECORD = HERE / "audit" / "alt_cost_measured_20261027" / "estimate_v3_alts.json"
CALIBRATION = HERE / "audit" / "lowliq_cost_20261027" / "calibration_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"
SEGMENTS = bu.SEGMENTS
WEIGHT = 0.05
MAX_OPEN = 20
VOL_WINDOW = 20
MIN_TRADES = 100
COST_ROWS = ("measured", "measured_x2", "volume_scaled", "assumed_public_v1", "cost0")


def make_configs() -> list:
    cfgs = [{"id": f"R_N{n}_a{a:.2f}_m{m:.1f}_H{h}", "N": n, "a": a, "m": m, "H": h, "mode": "R"}
            for n, a, m, h in itertools.product((20, 50), (0.10, 0.20), (2.0, 3.0), (3, 7))]
    cfgs.append({"id": "B_N20_a0.10_m2.0_H7", "N": 20, "a": 0.10, "m": 2.0, "H": 7, "mode": "B"})
    return cfgs


def load_ohlcv(data: Path, seg: str) -> dict:
    lo, hi, _ = SEGMENTS[seg]
    t0, t1 = bu.ms(lo), bu.ms(hi)
    bu.assert_allowed(t0, t1)
    recs = []
    for p in sorted((data / "daily").glob("*.csv")):
        sym = p.name.split("-1d-")[0]
        if seg == "S2" and sym == "BTCUSDT":
            continue
        for f in bu.read_rows(p):
            t = int(f[0])
            if t0 <= t <= t1:
                recs.append((sym, t, float(f[1]), float(f[2]), float(f[3]), float(f[4]), float(f[7])))
    df = pd.DataFrame(recs, columns=["sym", "t", "open", "high", "low", "close", "qv"]).drop_duplicates(["sym", "t"])
    assert df["t"].between(t0, t1).all()
    days = np.arange(t0, t1 + 1, bu.DAY_MS)
    return {k: df.pivot(index="t", columns="sym", values=k).reindex(days)
            for k in ("open", "high", "low", "close", "qv")}


def signal_arrays(piv: dict, n: int, a: float, m: float) -> tuple:
    """Boolean (days x symbols) near-high and near-low spike signals at event day t (bars <= t only)."""
    hi = piv["high"].shift(1).rolling(n, min_periods=n).max()
    lo = piv["low"].shift(1).rolling(n, min_periods=n).min()
    rng = hi - lo
    p = (piv["close"] - lo) / rng.where(rng > 0)
    v = piv["qv"] / piv["qv"].shift(1).rolling(VOL_WINDOW, min_periods=VOL_WINDOW).mean()
    spike = v >= m
    return ((p >= 1 - a) & spike).values, ((p <= a) & spike).values, v.values


def universe_mask(piv: dict, first: str) -> tuple:
    """(days x symbols) bool: member of the latest Monday's point-in-time top-50; and the Monday indexes."""
    days, opn, qv = piv["open"].index.values, piv["open"], piv["qv"]
    nb = piv["close"].notna().cumsum()
    mask = np.zeros(opn.shape, bool)
    mondays = []
    cur = None
    for i, t in enumerate(days):
        if t >= bu.ms(first) and dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).weekday() == 0 and i >= 2:
            cut = i - 2
            v, n = qv.iloc[max(0, cut - 29): cut + 1].mean(), nb.iloc[cut]
            el = [s for s in opn.columns if n[s] >= bu.MIN_BARS and not math.isnan(opn.iloc[i][s]) and not math.isnan(v[s])]
            top = sorted(el, key=lambda s: (-v[s], s))[:bu.TOP_N]
            cur = np.array([s in set(top) for s in opn.columns])
            mondays.append(i)
        if cur is not None:
            mask[i] = cur
    return mask, mondays


def simulate(cfg: dict, sig: tuple, mask: np.ndarray, ctx: dict) -> dict:
    """Run one config; returns daily gross, per-row costs, trades and counters."""
    near_hi, near_lo, v = sig
    n_days, n_sym = mask.shape
    start, h = ctx["start"], cfg["H"]
    gross = np.zeros(n_days)
    cost = {r: np.zeros(n_days) for r in COST_ROWS}
    busy = np.zeros(n_sym, int)
    trades, skipped = [], 0
    for e in range(max(start, 1), n_days - h):
        t = e - 1
        long_sig = near_lo[t] if cfg["mode"] == "R" else near_hi[t]
        short_sig = near_hi[t] if cfg["mode"] == "R" else near_lo[t]
        cand = np.where(mask[e] & (long_sig | short_sig) & (busy <= e) & ~(long_sig & short_sig))[0]
        if len(cand) == 0:
            continue
        free = MAX_OPEN - int((busy > e).sum())
        order = sorted(cand, key=lambda k: (-v[t, k], ctx["syms"][k]))
        skipped += max(0, len(order) - max(free, 0))
        for k in order[:max(free, 0)]:
            side = 1.0 if long_sig[k] else -1.0
            busy[k] = e + h
            pnl = 0.0
            for d in range(e, e + h):
                r = ctx["ret"][d, k]
                if math.isnan(r):
                    ctx["flags"]["missing_return_days"] += 1
                    r = 0.0
                f = ctx["fnd"][d, k]
                pnl += side * r - side * (0.0 if math.isnan(f) else f)
                gross[d] += WEIGHT * (side * r - side * (0.0 if math.isnan(f) else f))
            tc = {r: 0.0 for r in COST_ROWS}
            for idx in (e, e + h):
                b, q15 = ctx["impact"](k, idx)
                for r in COST_ROWS:
                    c = WEIGHT * ctx["side_cost"](b, r, q15)
                    cost[r][idx] -= c
                    tc[r] += c / WEIGHT
            trades.append({"sym": ctx["syms"][k], "e": e, "side": side, "gross": pnl, "cost": tc["measured"]})
    return {"gross": gross, "cost": cost, "trades": trades, "skipped": skipped}


def trade_stats(trades: list) -> dict:
    if not trades:
        return {"n": 0}
    g = np.array([t["gross"] for t in trades])
    c = np.array([t["cost"] for t in trades])
    side = np.array([t["side"] for t in trades])
    net = g - c
    return {"n": len(trades), "n_long": int((side > 0).sum()), "n_short": int((side < 0).sum()),
            "mean_gross_bp": float(g.mean() * 1e4), "mean_net_bp": float(net.mean() * 1e4),
            "hit_rate": float((net > 0).mean()),
            "mean_net_bp_long": float(net[side > 0].mean() * 1e4) if (side > 0).any() else None,
            "mean_net_bp_short": float(net[side < 0].mean() * 1e4) if (side < 0).any() else None}


def run_segment(data: Path, seg: str, cfgs: list, costs: dict) -> dict:
    piv = load_ohlcv(data, seg)
    days = piv["open"].index.values
    syms = list(piv["open"].columns)
    t0, t1 = int(days[0]), int(days[-1])
    fund = bu.load_funding(data, syms, t0, t1)
    fund["day"] = ((fund["t"] - 1) // bu.DAY_MS) * bu.DAY_MS
    fnd = fund.groupby(["day", "sym"])["rate"].sum().unstack().reindex(days).reindex(columns=syms).values
    bars = bu.load_bar2345(data, syms, t0, t1)
    opn = piv["open"]
    ret = (opn.shift(-1) / opn - 1.0).values
    q30 = piv["qv"].rolling(30, min_periods=10).mean().values
    qv = piv["qv"].values
    flags = {"missing_return_days": 0, "impact_bar_fallback": 0, "no_volume_cost_fallback": 0,
             "participation_over_max": 0, "no_q15_fallback": 0}
    mask, mondays = universe_mask(piv, SEGMENTS[seg][2])
    start = mondays[0]

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

    ctx = {"start": start, "syms": syms, "ret": ret, "fnd": fnd, "impact": impact, "side_cost": side_cost, "flags": flags}
    mkt = np.full(len(days), np.nan)
    for d in range(start, len(days) - 1):
        r = ret[d, mask[d]]
        mkt[d] = float(np.nanmean(r)) if np.isfinite(r).any() else 0.0
    out = {}
    sigs = {}
    for cfg in cfgs:
        key = (cfg["N"], cfg["a"], cfg["m"])
        if key not in sigs:
            sigs[key] = signal_arrays(piv, *key)
        out[cfg["id"]] = simulate(cfg, sigs[key], mask, ctx)
    span = slice(start, len(days) - 1)
    return {"days": [int(x) for x in days[span]], "mkt": mkt[span], "span": span, "runs": out, "flags": flags}


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
    ap.add_argument("--out", type=Path, default=EVID / "hilo_volume_dev.json")
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
                              "flags": r["flags"], "market_ew": bu.stats(np.nan_to_num(r["mkt"]), r["mkt"])}
    s1_years = len(segs["S1"]["days"]) / 365.0
    n_cfg = len(cfgs)
    prior = prior_trials()
    out["null_max_sr_ann"] = {f"N={n_cfg}": expected_max_sr(n_cfg) / math.sqrt(s1_years),
                              f"N={bu.PRIOR_TRIALS_REF + n_cfg} (reference)": expected_max_sr(bu.PRIOR_TRIALS_REF + n_cfg) / math.sqrt(s1_years),
                              f"N={prior + n_cfg} (dev_search_trials.jsonl sum + this)": expected_max_sr(prior + n_cfg) / math.sqrt(s1_years)}
    null_sr = out["null_max_sr_ann"][f"N={n_cfg}"]
    for cfg in cfgs:
        row = {"params": cfg}
        for s, r in segs.items():
            run, mk = r["runs"][cfg["id"]], r["mkt"]
            sp = r["span"]
            d = {c: bu.stats((run["gross"] + run["cost"][c])[sp], mk) for c in COST_ROWS}
            net = (run["gross"] + run["cost"]["measured"])[sp]
            half = len(net) // 2
            d["halves_sr_ann"] = [bu.stats(net[:half], mk[:half])["sr_ann"], bu.stats(net[half:], mk[half:])["sr_ann"]]
            d["trades"] = trade_stats(run["trades"])
            d["skipped_capacity"] = run["skipped"]
            tot = net.sum()
            d["top10_days_share_of_net"] = float(np.sort(net)[-10:].sum() / tot) if tot > 0 else None
            row[s] = d
        row["flag"] = flag(row, null_sr)
        out["configs"][cfg["id"]] = row
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("null max SR", {k: round(v, 2) for k, v in out["null_max_sr_ann"].items()}, "prior trials", prior)
    for s in SEGMENTS:
        print(s, out["segments"][s]["flags"], "market SR", round(out["segments"][s]["market_ew"]["sr_ann"], 2))
    for cid, row in out["configs"].items():
        s1, s2 = row["S1"], row["S2"]
        print(cid, row["flag"], f"S1 n={s1['trades']['n']} gross {s1['trades'].get('mean_gross_bp', 0):.0f}bp net {s1['trades'].get('mean_net_bp', 0):.0f}bp",
              f"SR {s1['measured']['sr_ann']:.2f} x2 {s1['measured_x2']['sr_ann']:.2f} vs {s1['volume_scaled']['sr_ann']:.2f} c0 {s1['cost0']['sr_ann']:.2f} at {s1['measured']['alpha_t']:.2f}",
              f"halves {[round(x, 2) for x in s1['halves_sr_ann']]} | S2 n={s2['trades']['n']} SR {s2['measured']['sr_ann']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
