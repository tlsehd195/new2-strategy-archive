#!/usr/bin/env python3
"""OKX paper-record tendencies as preregistered DEV rules (ADR 0052).

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
PREREG_U = EVID / "prereg_okx_tendency_addendum_user_method_v1.json"
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
    return cfgs + make_u_configs()


def make_u_configs() -> list:
    """The user's own method (addendum prereg): long candle near the N-bar line, mild volume, fade."""
    cfgs = []
    for n in (96, 672):
        for m in (2, 3):
            for h in (4, 16):
                cfgs.append({"id": f"U_N{n}_m{m}_H{h}", "fam": "U", "N": n, "m": m, "H": h, "prox": 1, "vol": 1, "dir": "fade"})
    for h in (4, 16):
        cfgs.append({"id": f"Uv0_N96_m2_H{h}", "fam": "U", "N": 96, "m": 2, "H": h, "prox": 1, "vol": 0, "dir": "fade"})
    for h in (4, 16):
        cfgs.append({"id": f"Up0_N96_m2_H{h}", "fam": "U", "N": 96, "m": 2, "H": h, "prox": 0, "vol": 1, "dir": "fade"})
    for h in (4, 16):
        cfgs.append({"id": f"Uf_N96_m2_H{h}", "fam": "U", "N": 96, "m": 2, "H": h, "prox": 1, "vol": 1, "dir": "follow"})
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


BODY_WIN, BODY_SHARE, NEAR_HI, NEAR_LO, VOL_WIN, VOL_1D = 96, 0.60, 0.90, 0.10, 16, 96


def u_features(a: dict, n: int) -> dict:
    """Candle/line/volume features for rule U; every window ends at bar j-1 except the candle itself (bar j)."""
    hi_n = pd.DataFrame(a["high"]).rolling(n, min_periods=int(n * 0.9)).max().shift(1).values
    lo_n = pd.DataFrame(a["low"]).rolling(n, min_periods=int(n * 0.9)).min().shift(1).values
    rng = hi_n - lo_n
    p = np.where(rng > 0, (a["close"] - lo_n) / np.where(rng > 0, rng, 1.0), np.nan)
    body = np.abs(a["close"] - a["open"])
    mbody = pd.DataFrame(body).rolling(BODY_WIN, min_periods=int(BODY_WIN * 0.9)).mean().shift(1).values
    candle_rng = a["high"] - a["low"]
    dominant = body >= BODY_SHARE * candle_rng
    qv = pd.DataFrame(a["qv"])
    vr = a["qv"] / qv.rolling(VOL_WIN, min_periods=VOL_WIN).mean().shift(1).values
    vr1d = a["qv"] / qv.rolling(VOL_1D, min_periods=int(VOL_1D * 0.9)).mean().shift(1).values
    return {"p": p, "body": body, "mbody": mbody, "dominant": dominant, "vr": vr, "vr1d": vr1d,
            "bull": a["close"] > a["open"], "bear": a["close"] < a["open"]}


def u_events(cfg: dict, seg: dict) -> list:
    cache = seg.setdefault("ufeat", {})
    if cfg["N"] not in cache:
        cache[cfg["N"]] = u_features(seg["arr"], cfg["N"])
    f = cache[cfg["N"]]
    with np.errstate(invalid="ignore"):
        big = (f["body"] >= cfg["m"] * f["mbody"]) & f["dominant"]
        if cfg["vol"]:
            big &= f["vr"] > 1.0
        bull_ok, bear_ok = f["bull"] & big, f["bear"] & big
        if cfg["prox"]:
            bull_ok &= f["p"] >= NEAR_HI
            bear_ok &= f["p"] <= NEAR_LO
    s_bull, s_bear = (-1.0, 1.0) if cfg["dir"] == "fade" else (1.0, -1.0)
    score = f["body"] / np.where(f["mbody"] > 0, f["mbody"], np.nan)
    ev = []
    for ok, side in ((bull_ok, s_bull), (bear_ok, s_bear)):
        ok = ok.copy()
        ok[: cfg["N"] + 1] = False
        for j, k in np.argwhere(ok):
            ev.append((int(j), int(k), side, float(score[j, k]), cfg["H"]))
    ev.sort(key=lambda e: (e[0], -e[3], SYMBOLS[e[1]]))
    seg.setdefault("u_overlap", {})[cfg["id"]] = (
        (len(ev), int(sum(1 for e in ev if f["vr1d"][e[0], e[1]] >= 2.0))))
    return ev


def events_for(cfg: dict, seg: dict) -> list:
    if cfg["fam"] == "U":
        return u_events(cfg, seg)
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


def baseline_gross(cfg: dict, seg: dict, mean_side: float = 0.0) -> float:
    """Mean gross (bp, price only) of the same side/hold at every hourly decision bar after the first-entry day.

    For U (both sides) the baseline is the long drift at every 15m bar times the rule's mean side."""
    op = seg["arr"]["open"]
    side = -1.0 if cfg["fam"] == "T1" else 1.0
    j = np.arange(3, seg["nb"], 4)
    if cfg["fam"] == "U":
        side, j = mean_side, np.arange(0, seg["nb"])
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
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "prereg_addendum_sha256": bu.sha(PREREG_U), "script_sha256": bu.sha(Path(__file__)),
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
            ms = float(np.mean([t["side"] for t in run["trades"]])) if run["trades"] else 0.0
            d["baseline_gross_bp_every_hour"] = baseline_gross(cfg, r["seg"], ms)
            if cfg["fam"] == "U":
                n_ev, n_spike = r["seg"]["u_overlap"][cfg["id"]]
                d["events_total"], d["events_also_1d_volume_spike_ge2"] = n_ev, n_spike
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
