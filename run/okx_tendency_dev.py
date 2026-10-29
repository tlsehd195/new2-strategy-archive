#!/usr/bin/env python3
"""OKX paper-record tendencies as preregistered DEV rules (ADR 0051).

Runs the 12 configs fixed in
``run/audit/evidence_okx_tendency_20261029/prereg_okx_tendency_v1.json``
(committed before any signal or P&L was computed).  Reuses the ADR 0045
engine (``fast_short_dev``) with BTC/ETH added to the symbol list, 24h
high/low arrays, and one extra reported-only cost row (0.70 x measured).

Never reads 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards.
BTC is not read in S2.

    python3 run/okx_tendency_dev.py --data-dir <dir with klines/ funding/>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import fast_short_dev as fs  # noqa: E402
from broad_universe_dev import expected_max_sr  # noqa: E402

VERSION = "okx-tendency-dev-v1"
EVID = HERE / "audit" / "evidence_okx_tendency_20261029"
PREREG = EVID / "prereg_okx_tendency_v1.json"
SYMBOLS = ["BTCUSDT", "ETHUSDT"] + fs.SYMBOLS
BTC = SYMBOLS.index("BTCUSDT")
BARS_DAY = fs.BARS_DAY
WIN = 96
MIN_VALID = 90
POS_SHORT, POS_LONG_LO, POS_LONG_HI, VOL_LOW = 0.78, 0.30, 0.45, 0.70
LIMIT_RATIO = 0.70
COST_ROWS = fs.COST_ROWS + ("limit_ratio070",)


def make_configs() -> list:
    cfgs = []
    for d in (0, 1):
        for v in (0, 1):
            for h in (16, 48):
                cfgs.append({"id": f"T1_d{d}_v{v}_H{h}", "fam": "T1", "drop": d, "lowvol": v, "H": h})
    for v in (0, 1):
        for h in (16, 48):
            cfgs.append({"id": f"T2_v{v}_H{h}", "fam": "T2", "lowvol": v, "H": h})
    return cfgs


def load_segment(data: Path, seg: str) -> dict:
    """fast_short_dev.load_segment plus high/low; BTC left empty in S2."""
    lo, hi, first = bu.SEGMENTS[seg]
    t0, t1 = bu.ms(lo), bu.ms(hi) + bu.DAY_MS - 1
    bu.assert_allowed(t0, t1)
    n_days = int(round((t1 + 1 - t0) / bu.DAY_MS))
    nb = n_days * BARS_DAY
    ns = len(SYMBOLS)
    arr = {k: np.full((nb, ns), np.nan) for k in ("open", "high", "low", "close", "qv", "tbq")}
    fund = []
    for k, sym in enumerate(SYMBOLS):
        if seg == "S2" and sym == "BTCUSDT":
            fund.append((np.array([], dtype=np.int64), np.array([])))
            continue
        for p in sorted((data / "klines").glob(f"{sym}-15m-*.csv")):
            m0, m1 = fs.month_range(p.stem)
            if m1 <= t0 or m0 > t1:
                continue
            with p.open() as fh:
                has_header = fh.readline().startswith("open_time")
            df = pd.read_csv(p, usecols=[0, 1, 2, 3, 4, 7, 10], header=0 if has_header else None,
                             names=["t", "open", "high", "low", "close", "qv", "tbq"])
            df = df[(df["t"] >= t0) & (df["t"] <= t1)]
            idx = ((df["t"].values - t0) // fs.BAR_MS).astype(int)
            for c in ("open", "high", "low", "close", "qv", "tbq"):
                arr[c][idx, k] = df[c].values
        ts, rate = [], []
        for p in sorted((data / "funding").glob(f"{sym}-fundingRate-*.csv")):
            m0, m1 = fs.month_range(p.stem)
            if m1 <= t0 or m0 > t1:
                continue
            for f in bu.read_rows(p):
                t = int(f[0])
                if t0 <= t <= t1:
                    ts.append(int(round(t / fs.BAR_MS)) * fs.BAR_MS)
                    rate.append(float(f[2]))
        o = np.argsort(ts)
        fund.append((np.array(ts, dtype=np.int64)[o], np.array(rate)[o]))
    return {"T0": t0, "n_days": n_days, "nb": nb, "arr": arr, "fund": fund,
            "start_bar": int(round((bu.ms(first) - t0) / bu.DAY_MS)) * BARS_DAY}


def features(a: dict) -> dict:
    """pos24, ret4h, volratio for every bar (valid only where the lookback is complete); bars <= j only."""
    hi = pd.DataFrame(a["high"]).rolling(WIN, min_periods=MIN_VALID).max().values
    lo = pd.DataFrame(a["low"]).rolling(WIN, min_periods=MIN_VALID).min().values
    rng = hi - lo
    pos = np.where(rng > 0, (a["close"] - lo) / np.where(rng > 0, rng, 1.0), np.nan)
    nb = len(a["close"])
    ret4 = np.full(a["close"].shape, np.nan)
    ret4[15:] = a["close"][15:] / a["open"][:-15] - 1.0
    s4 = pd.DataFrame(a["qv"]).rolling(4, min_periods=4).sum().values
    ref = np.zeros_like(s4)
    for k in range(1, 25):
        sh = np.full_like(s4, np.nan)
        sh[4 * k:] = s4[:-4 * k]
        ref = ref + sh
    ref = ref / 24.0
    vr = np.where(ref > 0, s4 / np.where(ref > 0, ref, 1.0), np.nan)
    return {"pos": pos, "ret4": ret4, "vr": vr, "nb": nb}


def events_for(cfg: dict, seg: dict) -> list:
    f = seg.get("feat") or features(seg["arr"])
    seg["feat"] = f
    pos, ret4, vr = f["pos"], f["ret4"], f["vr"]
    idx_h = np.arange(3, seg["nb"], 4)
    ev = []
    for j in idx_h:
        p = pos[j]
        if cfg["fam"] == "T1":
            ok = p >= POS_SHORT
            if cfg["drop"]:
                ok &= ret4[j] < 0
            side = -1.0
            score = p
        else:
            ok = (p >= POS_LONG_LO) & (p <= POS_LONG_HI)
            side = 1.0
            score = np.ones_like(p)
        if cfg["lowvol"]:
            ok &= vr[j] < VOL_LOW
        for k in np.where(ok)[0]:
            ev.append((int(j), int(k), side, float(score[k]), cfg["H"]))
    ev.sort(key=lambda e: (e[0], -e[3], SYMBOLS[e[1]]))
    return ev


def make_cost_fns(costs: dict) -> dict:
    base = ORIG_COST_FNS(costs)
    one0 = base["one"]

    def one(row: str, b: float, q15: float, entry: bool) -> float:
        if row == "limit_ratio070":
            return LIMIT_RATIO * one0("measured", b, q15, entry)
        return one0(row, b, q15, entry)
    return {"one": one, "maxp": base["maxp"]}


ORIG_COST_FNS = fs.make_cost_fns


def install() -> None:
    """Point the shared engine at this module's symbols, loader, signals and cost rows."""
    fs.SYMBOLS = SYMBOLS
    fs.COST_ROWS = COST_ROWS
    fs.load_segment = load_segment
    fs.events_for = events_for
    fs.make_cost_fns = make_cost_fns


def baseline_gross(cfg: dict, seg: dict) -> float:
    """Mean gross (bp, price only) of the same side/hold at every hourly decision bar after the first-entry day."""
    op = seg["arr"]["open"]
    side = -1.0 if cfg["fam"] == "T1" else 1.0
    j = np.arange(3, seg["nb"], 4)
    e, x = j + 1, j + 1 + cfg["H"]
    keep = (e >= seg["start_bar"]) & (x <= seg["nb"] - 1)
    r = side * (op[x[keep]] / op[e[keep]] - 1.0)
    r = r[np.isfinite(r)]
    return float(r.mean() * 1e4)


def prior_trials() -> int:
    return fs.prior_trials()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "okx_tendency_dev.json")
    a = ap.parse_args(argv)
    install()
    cfgs = make_configs()
    costs = {"measured": bu.load_cost_record(fs.COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(fs.CALIBRATION)}
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "engine_sha256": bu.sha(HERE / "fast_short_dev.py"), "helper_sha256": bu.sha(HERE / "broad_universe_dev.py"),
           "cost_record_sha256": bu.sha(fs.COST_RECORD), "calibration_sha256": bu.sha(fs.CALIBRATION),
           "segments": {}, "configs": {}}
    segs = {s: fs.run_segment(a.data_dir, s, cfgs, costs) for s in bu.SEGMENTS}
    for s, r in segs.items():
        sd = r["start_day"]
        out["segments"][s] = {"n_days": r["seg"]["n_days"], "first_entry_day": sd,
                              "market_ew": bu.stats(r["mkt"][sd:], r["mkt"][sd:])}
    s1_years = (segs["S1"]["seg"]["n_days"] - segs["S1"]["start_day"]) / 365.0
    n_cfg, prior = len(cfgs), prior_trials()
    out["null_max_sr_ann"] = {f"N={n_cfg}": expected_max_sr(n_cfg) / math.sqrt(s1_years),
                              f"N={prior + n_cfg} (cumulative)": expected_max_sr(prior + n_cfg) / math.sqrt(s1_years)}
    null_sr, null_cum = out["null_max_sr_ann"][f"N={n_cfg}"], out["null_max_sr_ann"][f"N={prior + n_cfg} (cumulative)"]
    for cfg in cfgs:
        row = {"params": cfg}
        for s, r in segs.items():
            run, sd, mk = r["runs"][cfg["id"]], r["start_day"], r["mkt"]
            sl = slice(sd, None)
            d = {c: bu.stats((run["gross_d"] + run["cost_d"][c])[sl], mk[sl]) for c in COST_ROWS}
            net = (run["gross_d"] + run["cost_d"]["measured"])[sl]
            half = len(net) // 2
            d["halves_sr_ann"] = [bu.stats(net[:half], mk[sl][:half])["sr_ann"], bu.stats(net[half:], mk[sl][half:])["sr_ann"]]
            d["trades"] = fs.trade_stats(run["trades"])
            d["trades"]["mean_net_bp_by_row"] = {c: float((np.array([t["gross"] - t["cost"][c] for t in run["trades"]])).mean() * 1e4)
                                                 for c in COST_ROWS} if run["trades"] else {}
            d["baseline_gross_bp_every_hour"] = baseline_gross(cfg, r["seg"])
            if run["trades"]:
                d["gross_minus_baseline_bp"] = d["trades"]["mean_gross_bp"] - d["baseline_gross_bp_every_hour"]
            d["skipped"] = {k: run[k] for k in ("skipped_capacity", "skipped_invalid", "participation_over_max")}
            row[s] = d
        row["flag"] = fs.flag(row, null_sr)
        row["clears_cumulative_null"] = bool(row["S1"]["measured"]["sr_ann"] > null_cum)
        out["configs"][cfg["id"]] = row
    best = max(out["configs"].items(), key=lambda kv: kv[1]["S1"]["measured"]["sr_ann"])
    out["best_s1_measured"] = {"id": best[0], "sr_ann": best[1]["S1"]["measured"]["sr_ann"], "flag": best[1]["flag"]}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("null max SR", {k: round(v, 2) for k, v in out["null_max_sr_ann"].items()}, "prior", prior)
    for s in bu.SEGMENTS:
        print(s, "market SR", round(out["segments"][s]["market_ew"]["sr_ann"], 2))
    for cid, row in out["configs"].items():
        s1, s2 = row["S1"], row["S2"]
        t = s1["trades"]
        print(cid, row["flag"], f"S1 n={t.get('n', 0)} L/S {t.get('n_long', 0)}/{t.get('n_short', 0)} gross {t.get('mean_gross_bp', 0):.1f} (base {s1['baseline_gross_bp_every_hour']:.1f}) cost {t.get('mean_cost_bp', 0):.1f} net {t.get('mean_net_bp', 0):.1f}",
              f"SR {s1['measured']['sr_ann']:.2f} c0 {s1['cost0']['sr_ann']:.2f} lim {s1['limit_ratio070']['sr_ann']:.2f} mk {s1['maker_both']['sr_ann']:.2f} vs {s1['volume_scaled']['sr_ann']:.2f}",
              f"| S2 n={s2['trades'].get('n', 0)} gross {s2['trades'].get('mean_gross_bp', 0):.1f} SR {s2['measured']['sr_ann']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
