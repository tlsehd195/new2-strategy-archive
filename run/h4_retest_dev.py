#!/usr/bin/env python3
"""4h-bar retest of trend / mean-reversion / hi-lo + volume / cross-section rules, DEV only (ADR 0095).

Runs exactly the 20 configs fixed in
``run/audit/evidence_h4_retest_20261120/prereg_h4_retest_v1.json``
(committed before any 4h bar or result was computed) on the 15 full-history alts
of the project 15m archive.  Signals are decided on 4h bars resampled from 15m
bars; fills, impact cost and funding use the 15m bars through the ADR 0045 engine
(``fast_short_dev.simulate``), unchanged.

Never reads: 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards (rows are
filtered by timestamp the moment a monthly file is parsed).  No orders, no
private API, no NEW2.

    python3 run/h4_retest_dev.py --data-dir <dir with klines/ and funding/>
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))
import broad_universe_dev as bu  # noqa: E402
import fast_short_dev as fs  # noqa: E402
from engine_lib import deflated_sharpe, expected_max_sr, pbo_cscv  # noqa: E402

VERSION = "h4-retest-dev-v1"
EVID = HERE / "audit" / "evidence_h4_retest_20261120"
PREREG = EVID / "prereg_h4_retest_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"
SEGMENTS = bu.SEGMENTS
SYMBOLS = fs.SYMBOLS
BAR_MS = fs.BAR_MS
BARS_DAY = fs.BARS_DAY
SUB = 16                      # 15m bars per 4h bar
LIMIT_RATIO = 0.70            # ADR 0049 R_B_matched (limit + chase of unfilled)
COST_ROWS = ("measured", "measured_x2", "volume_scaled", "assumed_public_v1", "cost0",
             "limit_chase", "maker_both")
N_TRIALS = 20
MIN_TRADES = 300


def make_configs() -> list:
    c = []
    for n in (20, 60):
        for h in (6, 18):
            c.append({"id": f"T1_N{n}_H{h}", "fam": "T1", "N": n, "H": h})
    for f, s in ((10, 40), (20, 80)):
        for h in (6, 18):
            c.append({"id": f"T2_f{f}s{s}_H{h}", "fam": "T2", "fast": f, "slow": s, "H": h})
    for k in (2.0, 3.0):
        for h in (1, 6):
            c.append({"id": f"T3_k{k:.1f}_H{h}", "fam": "T3", "k": k, "H": h})
    for n in (42, 180):
        for h in (6, 18):
            c.append({"id": f"T4_N{n}_H{h}", "fam": "T4", "N": n, "H": h})
    for lb in (6, 42):
        for d in ("reversal", "momentum"):
            c.append({"id": f"T5_{d}_L{lb}", "fam": "T5", "L": lb, "dir": d, "H": 6})
    return c


# ---------------------------------------------------------------- data

def load_segment(data: Path, seg: str) -> dict:
    """fast_short_dev.load_segment plus high/low columns."""
    lo, hi, first = SEGMENTS[seg]
    t0, t1 = bu.ms(lo), bu.ms(hi) + bu.DAY_MS - 1
    bu.assert_allowed(t0, t1)
    n_days = int(round((t1 + 1 - t0) / bu.DAY_MS))
    nb = n_days * BARS_DAY
    ns = len(SYMBOLS)
    cols = ("open", "high", "low", "close", "qv", "tbq")
    arr = {k: np.full((nb, ns), np.nan) for k in cols}
    fund = []
    for k, sym in enumerate(SYMBOLS):
        for p in sorted((data / "klines").glob(f"{sym}-15m-*.csv")):
            m0, m1 = fs.month_range(p.stem)
            if m1 <= t0 or m0 > t1:
                continue
            with p.open() as fh:
                has_header = fh.readline().startswith("open_time")
            df = pd.read_csv(p, usecols=[0, 1, 2, 3, 4, 7, 10], header=0 if has_header else None,
                             names=["t", *cols])
            df = df[(df["t"] >= t0) & (df["t"] <= t1)]
            idx = ((df["t"].values - t0) // BAR_MS).astype(int)
            for c in cols:
                arr[c][idx, k] = df[c].values
        ts, rate = [], []
        for p in sorted((data / "funding").glob(f"{sym}-fundingRate-*.csv")):
            m0, m1 = fs.month_range(p.stem)
            if m1 <= t0 or m0 > t1:
                continue
            for f in bu.read_rows(p):
                t = int(f[0])
                if t0 <= t <= t1:
                    ts.append(int(round(t / BAR_MS)) * BAR_MS)
                    rate.append(float(f[2]))
        o = np.argsort(ts)
        fund.append((np.array(ts, dtype=np.int64)[o], np.array(rate)[o]))
    return {"T0": t0, "n_days": n_days, "nb": nb, "arr": arr, "fund": fund,
            "start_bar": int(round((bu.ms(first) - t0) / bu.DAY_MS)) * BARS_DAY}


def to_h4(arr: dict) -> dict:
    """4h bars from 15m bars; a 4h bar with any 15m bar missing is NaN."""
    nb, ns = arr["open"].shape
    m = nb // SUB

    def blk(x):
        return x[: m * SUB].reshape(m, SUB, ns)
    o, h, l, c, q = (blk(arr[k]) for k in ("open", "high", "low", "close", "qv"))
    full = np.isfinite(o).all(1) & np.isfinite(c).all(1) & np.isfinite(h).all(1) & np.isfinite(l).all(1)
    nan = np.full((m, ns), np.nan)
    return {"open": np.where(full, o[:, 0], nan), "close": np.where(full, c[:, -1], nan),
            "high": np.where(full, np.nanmax(h, 1) if full.any() else nan, nan),
            "low": np.where(full, np.nanmin(l, 1) if full.any() else nan, nan),
            "qv": np.where(full, np.nansum(q, 1), nan)}


# ---------------------------------------------------------------- signals (4h bar m -> 15m decision bar 16m+15)

def h4_events(cfg: dict, b: dict) -> list:
    o, h, l, c, q = b["open"], b["high"], b["low"], b["close"], b["qv"]
    m, ns = c.shape
    sig = np.zeros((m, ns))
    score = np.zeros((m, ns))
    fam = cfg["fam"]
    if fam in ("T1", "T4"):
        hi = pd.DataFrame(h).shift(1).rolling(cfg["N"], min_periods=cfg["N"]).max().values
        lo = pd.DataFrame(l).shift(1).rolling(cfg["N"], min_periods=cfg["N"]).min().values
        if fam == "T1":
            sig[c > hi] = 1.0
            sig[c < lo] = -1.0
            rng = np.where(hi - lo > 0, hi - lo, np.nan)
            score = np.abs(np.where(sig > 0, c - hi, lo - c)) / rng
        else:
            vm = pd.DataFrame(q).shift(1).rolling(42, min_periods=42).mean().values
            v = np.where(vm > 0, q / np.where(vm > 0, vm, 1.0), np.nan)
            rng = np.where(hi - lo > 0, hi - lo, np.nan)
            p = (c - lo) / rng
            sig[(v >= 2.0) & (p >= 0.9)] = -1.0
            sig[(v >= 2.0) & (p <= 0.1)] = 1.0
            score = v
    elif fam == "T2":
        cd = pd.DataFrame(c)
        ef = cd.ewm(span=cfg["fast"], adjust=False, min_periods=cfg["slow"]).mean().values
        es = cd.ewm(span=cfg["slow"], adjust=False, min_periods=cfg["slow"]).mean().values
        s = np.sign(ef - es)
        prev = np.vstack([np.full((1, ns), np.nan), s[:-1]])
        cross = np.isfinite(s) & np.isfinite(prev) & (s != 0) & (s != prev)
        sig[cross] = s[cross]
        score = np.abs(ef - es) / np.where(es > 0, es, np.nan)
    elif fam == "T3":
        r = np.log(c / o)
        sd = pd.DataFrame(r).shift(1).rolling(180, min_periods=180).std().values
        z = r / np.where(sd > 0, sd, np.nan)
        sig[np.abs(z) >= cfg["k"]] = -np.sign(z[np.abs(z) >= cfg["k"]])
        score = np.abs(z)
    hold = SUB * cfg["H"]
    ev = []
    if fam == "T5":
        lb = cfg["L"]
        for mm in range(5, m, 6):
            if mm - lb + 1 < 0:
                continue
            sc = np.log(c[mm] / o[mm - lb + 1])
            ok = np.where(np.isfinite(sc))[0]
            if len(ok) < 8:
                continue
            order = ok[np.argsort(sc[ok], kind="stable")]
            lo_side, hi_side = (1.0, -1.0) if cfg["dir"] == "reversal" else (-1.0, 1.0)
            j = SUB * mm + SUB - 1
            ev += [(j, int(k), lo_side, 1.0, hold) for k in order[:3]]
            ev += [(j, int(k), hi_side, 1.0, hold) for k in order[-3:]]
    else:
        score = np.nan_to_num(score, nan=0.0)
        for mm, k in np.argwhere(sig != 0):
            ev.append((SUB * int(mm) + SUB - 1, int(k), float(sig[mm, k]), float(score[mm, k]), hold))
    ev.sort(key=lambda e: (e[0], -e[3], SYMBOLS[e[1]]))
    return ev


# ---------------------------------------------------------------- cost

def make_cost_fns(costs: dict) -> dict:
    base = fs.make_cost_fns(costs)["one"]

    def one(row: str, b: float, q15: float, entry: bool) -> float:
        if row == "limit_chase":
            return LIMIT_RATIO * base("measured", b, q15, entry)
        return base(row, b, q15, entry)
    return {"one": one, "maxp": costs["measured"]["maxp"]}


# ---------------------------------------------------------------- driver

def moments(x: np.ndarray) -> tuple:
    x = np.asarray(x, float)
    sd = x.std(ddof=1)
    if not sd > 0:
        return 0.0, 0.0, 3.0
    z = (x - x.mean()) / sd
    return float(x.mean() / sd), float((z ** 3).mean()), float((z ** 4).mean())


def flag(row: dict, null_sr: float, cost_row: str = "measured") -> str:
    s1 = row["S1"][cost_row]
    ok = (s1["sr_ann"] > null_sr and s1["alpha_t"] >= 2 and min(row["S1"]["halves_sr_ann"][cost_row]) > 0
          and row["S2"][cost_row]["sr_ann"] > 0.5 and row["S1"]["volume_scaled"]["sr_ann"] > 0
          and row["S1"]["trades"]["n"] >= MIN_TRADES)
    return "DEV_PROMISING" if ok else "NOT_PROMISING"


def run_segment(data: Path, seg_name: str, cfgs: list, costs: dict) -> dict:
    seg = load_segment(data, seg_name)
    a = seg["arr"]
    b4 = to_h4(a)
    qf = pd.DataFrame(a["qv"]).where(lambda d: d > 0).ffill(limit=fs.GAP_FILL_BARS).values
    q30 = pd.DataFrame(a["qv"]).rolling(fs.Q30_BARS, min_periods=480).mean().values
    fund_cum = [(ts, np.concatenate([[0.0], np.cumsum(rate)])) for ts, rate in seg["fund"]]
    ctx = {"qf": qf, "q30": q30, "fund_cum": fund_cum, "fns": make_cost_fns(costs)}
    op = a["open"]
    days = seg["n_days"]
    ref = np.array([op[min((d + 1) * BARS_DAY, seg["nb"] - 1)] / op[d * BARS_DAY] - 1.0 for d in range(days)])
    mkt = np.array([np.nanmean(r) if np.isfinite(r).any() else 0.0 for r in ref])
    runs = {}
    for cfg in cfgs:
        res = fs.simulate(h4_events(cfg, b4), seg, ctx)
        res["gross_d"], res["cost_d"] = fs.daily_series(res["trades"], seg)
        runs[cfg["id"]] = res
    return {"seg": seg, "mkt": mkt, "start_day": seg["start_bar"] // BARS_DAY, "runs": runs,
            "h4_missing_share": float(np.isnan(b4["close"]).mean())}


def prior_trials() -> int:
    return sum(json.loads(l).get("dev_evaluated", 0) for l in TRIALS.read_text().splitlines() if l.strip())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "h4_retest_dev.json")
    ap.add_argument("--prior-trials", type=int, default=None,
                    help="cumulative DEV trials before this run (default: sum of dev_search_trials.jsonl)")
    a = ap.parse_args(argv)
    fs.COST_ROWS = COST_ROWS          # the ADR 0045 engine books every row named here
    cfgs = make_configs()
    assert len(cfgs) == N_TRIALS
    costs = {"measured": bu.load_cost_record(fs.COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(fs.CALIBRATION)}
    prior = a.prior_trials if a.prior_trials is not None else prior_trials()
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "engine_sha256": bu.sha(HERE / "fast_short_dev.py"), "helper_sha256": bu.sha(HERE / "broad_universe_dev.py"),
           "cost_record_sha256": bu.sha(fs.COST_RECORD), "calibration_sha256": bu.sha(fs.CALIBRATION),
           "segments": {}, "configs": {}}
    segs = {s: run_segment(a.data_dir, s, cfgs, costs) for s in SEGMENTS}
    for s, r in segs.items():
        sd = r["start_day"]
        out["segments"][s] = {"n_days": r["seg"]["n_days"], "first_entry_day": sd, "h4_missing_share": r["h4_missing_share"],
                              "market_ew": bu.stats(r["mkt"][sd:], r["mkt"][sd:])}
    y1 = (segs["S1"]["seg"]["n_days"] - segs["S1"]["start_day"]) / 365.0
    null20 = expected_max_sr(N_TRIALS) / math.sqrt(y1)
    nullc = expected_max_sr(prior + N_TRIALS) / math.sqrt(y1)
    z = statistics.NormalDist().inv_cdf
    out["null_max_sr_ann"] = {"N=20": null20, f"N={prior + N_TRIALS} cumulative": nullc}
    out["power"] = {"s1_years": y1, "min_detectable_annual_sr_80pct_bonf20": (z(1 - 0.05 / N_TRIALS) + z(0.8)) / math.sqrt(y1)}
    s1_net = {}
    for cfg in cfgs:
        row = {"params": cfg}
        for s, r in segs.items():
            run, sd, mk = r["runs"][cfg["id"]], r["start_day"], r["mkt"]
            sl = slice(sd, None)
            d = {c: bu.stats((run["gross_d"] + run["cost_d"][c])[sl], mk[sl]) for c in COST_ROWS}
            d["halves_sr_ann"] = {}
            for c in ("measured", "limit_chase", "cost0"):
                net = (run["gross_d"] + run["cost_d"][c])[sl]
                hf = len(net) // 2
                d["halves_sr_ann"][c] = [bu.stats(net[:hf], mk[sl][:hf])["sr_ann"], bu.stats(net[hf:], mk[sl][hf:])["sr_ann"]]
            d["trades"] = fs.trade_stats(run["trades"])
            d["trades_per_day"] = d["trades"]["n"] / max(1, r["seg"]["n_days"] - sd)
            d["skipped"] = {k: run[k] for k in ("skipped_capacity", "skipped_invalid", "participation_over_max")}
            d["cost_paid_pct_of_capital_per_year"] = float(-run["cost_d"]["measured"][sl].sum() / ((len(mk) - sd) / 365.0) * 100)
            row[s] = d
            if s == "S1":
                s1_net[cfg["id"]] = (run["gross_d"] + run["cost_d"]["measured"])[sl]
        row["flag"] = flag(row, null20)
        row["flag_limit_chase_info_only"] = flag(row, null20, "limit_chase")
        out["configs"][cfg["id"]] = row
    mom = {k: moments(v) for k, v in s1_net.items()}
    var_sr = float(np.var([m[0] for m in mom.values()], ddof=1))
    for k, (sr, sk, ku) in mom.items():
        n = len(s1_net[k])
        out["configs"][k]["dsr_N20"] = deflated_sharpe(sr, n, sk, ku, N_TRIALS, var_sr)
        out["configs"][k]["dsr_cumulative"] = deflated_sharpe(sr, n, sk, ku, prior + N_TRIALS, var_sr)
    mat = np.column_stack([s1_net[c["id"]] for c in cfgs])
    pbo, ncomb = pbo_cscv(mat.tolist(), n_partitions=16)
    out["multiple_testing"] = {"n_trials_here": N_TRIALS, "prior_trials": prior, "cumulative": prior + N_TRIALS,
                               "cross_trial_var_sr_daily": var_sr, "pbo_cscv_16": pbo, "pbo_combinations": ncomb}
    out["counts"] = {"DEV_PROMISING": sum(r["flag"] == "DEV_PROMISING" for r in out["configs"].values()),
                     "limit_chase_info_DEV_PROMISING": sum(r["flag_limit_chase_info_only"] == "DEV_PROMISING" for r in out["configs"].values())}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("prior", prior, "null20", round(null20, 2), "nullcum", round(nullc, 2), "PBO", round(pbo, 3),
          "MDE", round(out["power"]["min_detectable_annual_sr_80pct_bonf20"], 2), out["counts"])
    for s in SEGMENTS:
        print(s, "mkt SR", round(out["segments"][s]["market_ew"]["sr_ann"], 2), "h4 missing", round(out["segments"][s]["h4_missing_share"], 4))
    for cid, row in out["configs"].items():
        s1, s2, t = row["S1"], row["S2"], row["S1"]["trades"]
        print(f"{cid:16s} {row['flag']:13s} n={t.get('n', 0):5d} g {t.get('mean_gross_bp', 0):6.1f} c {t.get('mean_cost_bp', 0):5.1f}"
              f" SR {s1['measured']['sr_ann']:6.2f} lim {s1['limit_chase']['sr_ann']:6.2f} c0 {s1['cost0']['sr_ann']:5.2f}"
              f" vs {s1['volume_scaled']['sr_ann']:6.2f} at {s1['measured']['alpha_t']:5.2f} hv {s1['halves_sr_ann']['measured'][0]:5.2f}/{s1['halves_sr_ann']['measured'][1]:5.2f}"
              f" | S2 n={s2['trades'].get('n', 0)} SR {s2['measured']['sr_ann']:5.2f} c0 {s2['cost0']['sr_ann']:5.2f} DSR {row['dsr_N20']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
