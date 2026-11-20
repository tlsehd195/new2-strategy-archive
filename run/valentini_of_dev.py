#!/usr/bin/env python3
"""Fabio Valentini full-process replication on Binance aggTrades order flow, DEV only (ADR 0114).

Runs exactly the 3 trials of
``run/audit/evidence_valentini_of_20261120/prereg_valentini_of_v1.json`` on the
1-minute order-flow bars built by ``run/aggtrades_minute_build.py`` (15 alts of
the ADR 0045 engine).  Pre-market map (24h volume profile, daily VWAP, 15m
structure), TREND_NY and MEANREV_LONDON models (location -> validation ->
aggression), A/B tiers, 0.25% risk sizing, one position in the whole book,
daily/weekly loss rules, partial at +1R, breakeven, trail, scratch; journal of
every trade.  Judged with the unchanged ``fast_short_dev.flag``.

    python3 run/valentini_of_dev.py --min-dir <aggtrades minute dir> --funding-dir <dir with funding/>
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))
import broad_universe_dev as bu  # noqa: E402
import fast_short_dev as fs  # noqa: E402
from engine_lib import deflated_sharpe, expected_max_sr  # noqa: E402

VERSION = "valentini-of-dev-v1"
EVID = HERE / "audit" / "evidence_valentini_of_20261120"
PREREG = EVID / "prereg_valentini_of_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"
CONTEXT_ID = "ALT15-1m-aggtrades-valentini-of-v1"
CONFIGS = {"VF1_TREND_NY": ("TREND",), "VF2_MEANREV_LONDON": ("MR",), "VF3_FULL_VALENTINI": ("TREND", "MR")}
SYMBOLS = fs.SYMBOLS
MIN_DAY = 1440
NY, LDN = ZoneInfo("America/New_York"), ZoneInfo("Europe/London")
FIELDS = ("open", "high", "low", "close", "vwap", "qv", "buy_qv", "big_buy_qv", "big_sell_qv",
          "sell_at_low_qv", "buy_at_high_qv")
BIN_BP, VA_SHARE, MIN_PROFILE_MIN = 0.0010, 0.70, 900
RISK_A, W_CAP, STOP_MIN, STOP_MAX, BUF = 0.0025, 3.0, 0.0010, 0.015, 0.0002
COST_ROWS = fs.COST_ROWS


# ---------------------------------------------------------------- data

def load(min_dir: Path, fund_dir: Path, seg: str) -> dict:
    lo, hi, first = bu.SEGMENTS[seg]
    t0, t1 = bu.ms(lo), bu.ms(hi) + bu.DAY_MS - 1
    bu.assert_allowed(t0, t1)
    n_days = int(round((t1 + 1 - t0) / bu.DAY_MS))
    nm, ns = n_days * MIN_DAY, len(SYMBOLS)
    a = {f: np.full((nm, ns), np.nan) for f in FIELDS}
    for k, sym in enumerate(SYMBOLS):
        for p in sorted((min_dir / sym).glob("*.npz")):
            z = np.load(p)
            t = z["t"]
            ok = (t >= t0) & (t <= t1)
            idx = ((t[ok] - t0) // 60_000).astype(int)
            for f in FIELDS:
                a[f][idx, k] = z[f][ok]
    for f in ("qv", "buy_qv", "big_buy_qv", "big_sell_qv", "sell_at_low_qv", "buy_at_high_qv"):
        a[f] = np.nan_to_num(a[f])
    fund = []
    for sym in SYMBOLS:
        ts, rate = [], []
        for p in sorted((fund_dir / "funding").glob(f"{sym}-fundingRate-*.csv")):
            for r in bu.read_rows(p):
                t = int(r[0])
                if t0 <= t <= t1:
                    ts.append(t)
                    rate.append(float(r[2]))
        o = np.argsort(ts)
        ts_a, r_a = np.array(ts, np.int64)[o], np.array(rate)[o]
        fund.append((ts_a, np.concatenate([[0.0], np.cumsum(r_a)])))
    return {"seg": seg, "T0": t0, "n_days": n_days, "nm": nm, "a": a, "fund": fund,
            "start_min": int(round((bu.ms(first) - t0) / bu.DAY_MS)) * MIN_DAY}


def derive(D: dict) -> dict:
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    a = D["a"]
    qv = a["qv"]
    with np.errstate(invalid="ignore", divide="ignore"):
        delta = np.where(qv > 0, (2 * a["buy_qv"] - qv) / qv, np.nan)
        bignet = np.where(qv > 0, (a["big_buy_qv"] - a["big_sell_qv"]) / qv, np.nan)
    m60 = {f: pd.DataFrame(a[f]).shift(1).rolling(60, min_periods=30).mean().values
           for f in ("qv", "sell_at_low_qv", "buy_at_high_qv")}
    # daily VWAP anchored at 00:00 UTC
    pv = np.nan_to_num(a["vwap"]) * qv
    nd = D["n_days"]
    cpv = np.cumsum(pv.reshape(nd, MIN_DAY, -1), axis=1).reshape(D["nm"], -1)
    cq = np.cumsum(qv.reshape(nd, MIN_DAY, -1), axis=1).reshape(D["nm"], -1)
    with np.errstate(invalid="ignore", divide="ignore"):
        dvwap = np.where(cq > 0, cpv / cq, np.nan)
    # 15m structure: low/high of the last completed 15m block and the one before, at each minute
    n15 = D["nm"] // 15
    low15 = np.nanmin(a["low"].reshape(n15, 15, -1), axis=1) if n15 else None
    high15 = np.nanmax(a["high"].reshape(n15, 15, -1), axis=1) if n15 else None
    # cost inputs: quote volume of the previous 15 minutes and its 30-day mean
    q15 = pd.DataFrame(qv).rolling(15, min_periods=15).sum().shift(1).values
    q15m = pd.DataFrame(qv).rolling(43_200, min_periods=7_200).sum().shift(1).values / 2880.0
    return {"delta": delta, "bignet": bignet, "m60": m60, "dvwap": dvwap, "low15": low15, "high15": high15,
            "q15": q15, "q15m": q15m}


def profile(vw: np.ndarray, q: np.ndarray, ref: float):
    ok = np.isfinite(vw) & (q > 0)
    if ok.sum() < MIN_PROFILE_MIN or not ref > 0:
        return None
    p, v = vw[ok], q[ok]
    lo = float(p.min())
    w = BIN_BP * ref
    nb = int((p.max() - lo) / w) + 1
    h = np.bincount(((p - lo) / w).astype(int), weights=v, minlength=nb)
    poc = int(np.argmax(h))
    a_ = z = poc
    tot = h[poc]
    while tot < VA_SHARE * h.sum():
        up = h[z + 1] if z + 1 < nb else -1.0
        dn = h[a_ - 1] if a_ - 1 >= 0 else -1.0
        if up >= dn:
            z += 1
            tot += up
        else:
            a_ -= 1
            tot += dn
    return lo + (poc + 0.5) * w, lo + a_ * w, lo + (z + 1) * w


# ---------------------------------------------------------------- side-mirrored views

class View:
    """Arrays of one symbol seen from one side: a short is the long logic on negated prices."""

    def __init__(self, D, X, k, side):
        a = D["a"]
        s = side
        self.side = s
        if s > 0:
            self.O, self.H, self.L, self.C = a["open"][:, k], a["high"][:, k], a["low"][:, k], a["close"][:, k]
            self.SAL, self.BAH = a["sell_at_low_qv"][:, k], a["buy_at_high_qv"][:, k]
            self.mSAL, self.mBAH = X["m60"]["sell_at_low_qv"][:, k], X["m60"]["buy_at_high_qv"][:, k]
            self.vw = X["dvwap"][:, k]
            self.lo15 = X["low15"][:, k]
        else:
            self.O, self.H, self.L, self.C = -a["open"][:, k], -a["low"][:, k], -a["high"][:, k], -a["close"][:, k]
            self.SAL, self.BAH = a["buy_at_high_qv"][:, k], a["sell_at_low_qv"][:, k]
            self.mSAL, self.mBAH = X["m60"]["buy_at_high_qv"][:, k], X["m60"]["sell_at_low_qv"][:, k]
            self.vw = -X["dvwap"][:, k]
            self.lo15 = -X["high15"][:, k]
        self.delta, self.bignet = s * X["delta"][:, k], s * X["bignet"][:, k]
        self.qv, self.mqv = a["qv"][:, k], X["m60"]["qv"][:, k]

    def upper_half(self, m):
        r = self.H[m] - self.L[m]
        return r > 0 and (self.C[m] - self.L[m]) / r >= 0.5

    def lower_half(self, m):
        r = self.H[m] - self.L[m]
        return r > 0 and (self.C[m] - self.L[m]) / r <= 0.5

    def tier(self, m):
        return "A" if (self.bignet[m] >= 0.20 and self.mqv[m] > 0 and self.qv[m] >= 2 * self.mqv[m]) else "B"


def gt(x, y):
    return np.isfinite(x) and np.isfinite(y) and x > y


# ---------------------------------------------------------------- models

def trend_candidates(v: View, ws: int, we: int, close_min: int, vah: float, val: float) -> list:
    width = vah - val
    out = []
    state, touch, deadline, val_ok = 0, -1, -1, False
    for m in range(ws, we):
        c = v.C[m]
        if not np.isfinite(c):
            continue
        if state == 0:
            if vah < c <= vah + width:
                state = 1
            continue
        if c < vah:  # back inside: breakout cancelled
            state, val_ok = 0, False
            continue
        if state == 1:
            if np.isfinite(v.L[m]) and v.L[m] <= vah + 0.25 * width:
                state, touch, deadline, val_ok = 2, m, m + 3, False
            else:
                continue
        if state == 2:
            if m > deadline:
                state, val_ok = 1, False
                if np.isfinite(v.L[m]) and v.L[m] <= vah + 0.25 * width:
                    state, touch, deadline = 2, m, m + 3
                else:
                    continue
            absorb = v.mSAL[m] > 0 and v.SAL[m] >= 2 * v.mSAL[m] and v.upper_half(m)
            flip = v.delta[m] >= 0.15 and m > 0 and v.delta[m - 1] < 0
            val_ok = val_ok or absorb or flip
            vw30 = v.vw[m - 30] if m >= 30 else np.nan
            b = m // 15 - 1
            direction = gt(c, vah) and gt(v.vw[m], vw30) and b >= 1 and gt(v.lo15[b], v.lo15[b - 1])
            aggr = v.delta[m] > 0 and v.bignet[m] > 0 and gt(c, v.O[m]) and c > vah
            if val_ok and aggr and direction and m + 1 < close_min:
                stop = np.nanmin(v.L[touch: m + 1])
                stop = stop - BUF * abs(stop)
                out.append({"trig": m, "e": m + 1, "side": v.side, "stop_v": stop, "final_v": vah + width,
                            "min_final_R": 2.0, "close_min": close_min, "model": "TREND", "tier": v.tier(m),
                            "vah_v": vah, "val_v": val, "delta": float(v.delta[m]), "bignet": float(v.bignet[m])})
                state, val_ok = 1, False
    return out


def mr_candidates(v: View, ws: int, we: int, close_min: int, vah: float, val: float, poc: float) -> list:
    """Long side: excursion below VAL, absorption of sellers, close back inside, buyers step in."""
    out = []
    exc, absorbed, ret, low_x = -1, False, -1, np.inf
    for m in range(ws, we):
        c, lo = v.C[m], v.L[m]
        if not np.isfinite(c):
            continue
        if exc < 0:
            if np.isfinite(lo) and lo < val:
                exc, absorbed, ret, low_x = m, False, -1, lo
            else:
                continue
        if ret < 0:
            low_x = min(low_x, lo) if np.isfinite(lo) else low_x
            if v.mSAL[m] > 0 and v.SAL[m] >= 2 * v.mSAL[m] and v.upper_half(m) and lo < val:
                absorbed = True
            if c > val:
                if m - exc <= 30:
                    ret = m
                else:
                    exc = -1
                    continue
            elif m - exc > 30:
                exc = -1
                continue
        if ret >= 0:
            if not (val <= c <= vah) or m > ret + 2:
                exc = -1
                continue
            if absorbed and v.delta[m] > 0 and v.bignet[m] > 0 and m + 1 < close_min:
                stop = low_x - BUF * abs(low_x)
                out.append({"trig": m, "e": m + 1, "side": v.side, "stop_v": stop, "final_v": poc,
                            "min_final_R": 1.0, "close_min": close_min, "model": "MR", "tier": v.tier(m),
                            "vah_v": vah, "val_v": val, "delta": float(v.delta[m]), "bignet": float(v.bignet[m])})
                exc = -1
    return out


def local_to_min(D, date: dt.date, hh: int, mm: int, tz) -> int:
    t = dt.datetime(date.year, date.month, date.day, hh, mm, tzinfo=tz).astimezone(dt.timezone.utc)
    return int((t.timestamp() * 1000 - D["T0"]) // 60_000)


def candidates(D: dict, X: dict) -> dict:
    a = D["a"]
    out = {"TREND": [], "MR": []}
    d0 = dt.datetime.fromtimestamp(D["T0"] / 1000, dt.timezone.utc).date()
    for di in range(1, D["n_days"]):
        date = d0 + dt.timedelta(days=di)
        wins = (("MR", local_to_min(D, date, 8, 0, LDN), local_to_min(D, date, 11, 0, LDN),
                 local_to_min(D, date, 12, 0, LDN)),
                ("TREND", local_to_min(D, date, 9, 30, NY), local_to_min(D, date, 12, 30, NY),
                 local_to_min(D, date, 16, 0, NY)))
        for model, ws, we, cm in wins:
            if ws < D["start_min"] or cm >= D["nm"] or ws < MIN_DAY:
                continue
            for k in range(len(SYMBOLS)):
                ref = a["close"][ws - 1, k]
                pr = profile(a["vwap"][ws - MIN_DAY: ws, k], a["qv"][ws - MIN_DAY: ws, k], ref)
                if pr is None:
                    continue
                poc, val, vah = pr
                for side in (1.0, -1.0):
                    v = View(D, X, k, side)
                    if side > 0:
                        L_vah, L_val, L_poc = vah, val, poc
                    else:
                        L_vah, L_val, L_poc = -val, -vah, -poc
                    if model == "TREND":
                        cs = trend_candidates(v, ws, we, cm, L_vah, L_val)
                    else:
                        cs = mr_candidates(v, ws, we, cm, L_vah, L_val, L_poc)
                    for c in cs:
                        c.update({"k": k, "date_ny": date.isoformat(), "poc": poc, "vah": vah, "val": val})
                    out[model] += cs
    return out


# ---------------------------------------------------------------- trade path

def run_trade(D, X, c, fns) -> dict | None:
    k, s, e, cm = c["k"], c["side"], c["e"], c["close_min"]
    v = View(D, X, k, s)
    p0 = v.O[e]
    if not np.isfinite(p0):
        return None
    stop = c["stop_v"]
    if not stop < p0:
        return None
    d = (p0 - stop) / abs(p0)
    if d > STOP_MAX:
        return None
    if d < STOP_MIN:
        d = STOP_MIN
        stop = p0 - d * abs(p0)
    R = p0 - stop
    final = c["final_v"]
    if c["model"] == "TREND":
        final = max(final, p0 + c["min_final_R"] * R)
    elif final - p0 < c["min_final_R"] * R:
        return None
    t1 = p0 + R
    pieces = []  # (fraction, minute, price, reason)
    rem, partial, trail_on = 1.0, False, False
    m = e
    while m < cm and rem > 0:
        o, h, lo = v.O[m], v.H[m], v.L[m]
        if not np.isfinite(h):
            m += 1
            continue
        if lo <= stop:
            px = min(stop, o) if np.isfinite(o) else stop
            pieces.append((rem, m, px, "stop" if not partial or stop < p0 else "breakeven_or_trail"))
            rem = 0.0
            break
        if not partial and h >= t1:
            pieces.append((0.5, m, t1, "partial_1R"))
            rem, partial = 0.5, True
            stop = max(stop, p0)
        if h >= final:
            pieces.append((rem, m, final, "target"))
            rem = 0.0
            break
        if c["model"] == "TREND":
            if h >= p0 + 1.5 * R:
                trail_on = True
            if trail_on:
                stop = max(stop, np.nanmin(v.L[max(e, m - 4): m + 1]))
        if m == e + 2 and not partial:
            dl = v.delta[e: e + 3]
            if np.all(dl < 0) and np.isfinite(v.C[m]) and v.C[m] < p0:
                j = m + 1
                while j < cm and not np.isfinite(v.O[j]):
                    j += 1
                if j < cm:
                    pieces.append((rem, j, v.O[j], "scratch"))
                    rem = 0.0
                    break
        m += 1
    if rem > 0:
        j = cm
        while j < D["nm"] - 1 and not np.isfinite(v.O[j]):
            j += 1
        if not np.isfinite(v.O[j]):
            return None
        pieces.append((rem, j, v.O[j], "time"))
    # unit (unlevered) results; prices in the view are side-mirrored, so ratio px/p0 is the real ratio
    ts, cum = D["fund"][k]
    te = D["T0"] + e * 60_000
    gross = 0.0
    cost = {r: 0.0 for r in COST_ROWS}
    be, qe = X["q15"][e, k], X["q15m"][e, k]
    if not (be > 0):
        return None
    qe = qe if qe > 0 else be
    ce = {r: fns["one"](r, be, qe, True) for r in COST_ROWS}
    cx_all = {r: 0.0 for r in COST_ROWS}
    for frac, mx, px, _ in pieces:
        tx = D["T0"] + mx * 60_000
        f = cum[np.searchsorted(ts, tx, "right")] - cum[np.searchsorted(ts, te, "right")]
        gross += frac * (s * (px / p0 - 1.0) - s * f)
        bx, qx = X["q15"][mx, k], X["q15m"][mx, k]
        bx = bx if bx > 0 else be
        qx = qx if qx > 0 else bx
        for r in COST_ROWS:
            cx_all[r] += frac * fns["one"](r, bx, qx, False)
    for r in COST_ROWS:
        cost[r] = ce[r] + cx_all[r]
    return {"k": k, "e": e, "x": pieces[-1][1], "side": s, "gross": gross, "cost_e": ce, "cost_x": cx_all,
            "cost": cost, "stop_dist": d, "pieces": pieces}


# ---------------------------------------------------------------- book with Valentini's risk rules

def book(D, X, cands: list, fns) -> dict:
    cands = sorted(cands, key=lambda c: (c["e"], c["k"], -c["side"]))
    busy_until = -1
    day_state = {}
    week_half = {}
    trades, journal = [], []
    skipped = {"busy": 0, "daily_losses": 0, "daily_cap": 0, "plus2R_tierB": 0, "invalid": 0}
    for c in cands:
        if c["e"] <= busy_until:
            skipped["busy"] += 1
            continue
        date = c["date_ny"]
        st = day_state.setdefault(date, {"losses": 0, "n": 0, "R": 0.0})
        if st["losses"] >= 3:
            skipped["daily_losses"] += 1
            continue
        if st["n"] >= 12:
            skipped["daily_cap"] += 1
            continue
        risk = RISK_A if c["tier"] == "A" else RISK_A / 2
        if st["R"] >= 2.0:
            if c["tier"] != "A":
                skipped["plus2R_tierB"] += 1
                continue
            risk = RISK_A / 2
        iso = dt.date.fromisoformat(date).isocalendar()[:2]
        done = sorted((d_, s_["R"]) for d_, s_ in day_state.items() if d_ < date and s_["n"] > 0)
        if len(done) >= 2 and done[-1][1] < 0 and done[-2][1] < 0:
            if dt.date.fromisoformat(done[-1][0]).isocalendar()[:2] == iso:
                week_half[iso] = True
        if week_half.get(iso):
            risk /= 2
        t = run_trade(D, X, c, fns)
        if t is None:
            skipped["invalid"] += 1
            continue
        w = min(risk / t["stop_dist"], W_CAP)
        t["w"] = w
        net_meas = w * (t["gross"] - t["cost"]["measured"])
        r_mult = net_meas / RISK_A
        st["n"] += 1
        st["R"] += r_mult
        if net_meas < 0:
            st["losses"] += 1
        busy_until = t["x"]
        trades.append(t)
        journal.append({"time_utc": dt.datetime.fromtimestamp((D["T0"] + t["e"] * 60_000) / 1000, dt.timezone.utc)
                        .strftime("%Y-%m-%d %H:%M"), "date_ny": date, "symbol": SYMBOLS[t["k"]], "model": c["model"],
                        "side": "long" if t["side"] > 0 else "short", "tier": c["tier"], "risk": risk,
                        "weight": round(w, 4), "poc": c["poc"], "vah": c["vah"], "val": c["val"],
                        "trig_delta": round(c["delta"], 3), "trig_bignet": round(c["bignet"], 3),
                        "stop_dist_pct": round(100 * t["stop_dist"], 3), "exit": t["pieces"][-1][3],
                        "hold_min": t["x"] - t["e"], "gross_bp": round(t["gross"] * 1e4, 2),
                        "cost_bp": round(t["cost"]["measured"] * 1e4, 2), "R": round(r_mult, 3)})
    return {"trades": trades, "journal": journal, "skipped": skipped}


def daily(trades, D):
    n = D["n_days"]
    g = np.zeros(n)
    cost = {r: np.zeros(n) for r in COST_ROWS}
    for t in trades:
        de, dx = t["e"] // MIN_DAY, t["x"] // MIN_DAY
        g[min(dx, n - 1)] += t["w"] * t["gross"]
        for r in COST_ROWS:
            cost[r][de] -= t["w"] * t["cost_e"][r]
            cost[r][min(dx, n - 1)] -= t["w"] * t["cost_x"][r]
    return g, cost


def ne_moments(x):
    x = np.asarray(x, float)
    sd = x.std(ddof=1)
    if not sd > 0:
        return 0.0, 0.0, 3.0
    z = (x - x.mean()) / sd
    return float(x.mean() / sd), float(np.mean(z ** 3)), float(np.mean(z ** 4))


def prior_trials() -> int:
    rows = [json.loads(ln) for ln in TRIALS.read_text().splitlines() if ln.strip()]
    return sum(r.get("dev_evaluated", 0) for r in rows if r.get("context_id") != CONTEXT_ID)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-dir", required=True, type=Path)
    ap.add_argument("--funding-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "valentini_of_dev.json")
    a = ap.parse_args(argv)
    costs = {"measured": bu.load_cost_record(fs.COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(fs.CALIBRATION)}
    fns = fs.make_cost_fns(costs)
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "builder_sha256": bu.sha(HERE / "aggtrades_minute_build.py"), "configs": {}, "segments": {}}
    segs = {}
    for s in fs.SEGMENTS:
        D = load(a.min_dir, a.funding_dir, s)
        X = derive(D)
        cand = candidates(D, X)
        op = D["a"]["open"]
        nd = D["n_days"]
        mkt = np.zeros(nd)
        for di in range(nd - 1):
            r = op[(di + 1) * MIN_DAY] / op[di * MIN_DAY] - 1.0
            mkt[di] = np.nanmean(r) if np.isfinite(r).any() else 0.0
        runs = {}
        for cfg, models in CONFIGS.items():
            res = book(D, X, [c for mdl in models for c in cand[mdl]], fns)
            res["gross_d"], res["cost_d"] = daily(res["trades"], D)
            runs[cfg] = res
            jp = EVID / f"journal_{cfg}_{s}.csv"
            if res["journal"]:
                with jp.open("w", newline="") as fh:
                    wr = csv.DictWriter(fh, fieldnames=list(res["journal"][0].keys()))
                    wr.writeheader()
                    wr.writerows(res["journal"])
        sd = D["start_min"] // MIN_DAY
        segs[s] = {"D": D, "mkt": mkt, "start_day": sd, "runs": runs}
        out["segments"][s] = {"n_days": nd, "first_entry_day": sd,
                              "candidates": {m: len(v) for m, v in cand.items()},
                              "minutes_with_data_share": float(np.isfinite(D["a"]["close"]).mean())}
        del X
    y1 = (segs["S1"]["D"]["n_days"] - segs["S1"]["start_day"]) / 365.0
    n = len(CONFIGS)
    prior = prior_trials()
    null = expected_max_sr(n) / math.sqrt(y1)
    out["null_max_sr_ann"] = {f"N={n}": null, f"N={prior + n} cumulative": expected_max_sr(prior + n) / math.sqrt(y1)}
    s1_series = {}
    for cfg in CONFIGS:
        row = {}
        for s, r in segs.items():
            run, sd_, mk = r["runs"][cfg], r["start_day"], r["mkt"]
            sl = slice(sd_, None)
            dd = {c: bu.stats((run["gross_d"] + run["cost_d"][c])[sl], mk[sl]) for c in COST_ROWS}
            net = (run["gross_d"] + run["cost_d"]["measured"])[sl]
            half = len(net) // 2
            dd["halves_sr_ann"] = [bu.stats(net[:half], mk[sl][:half])["sr_ann"],
                                   bu.stats(net[half:], mk[sl][half:])["sr_ann"]]
            dd["trades"] = fs.trade_stats(run["trades"])
            jr = run["journal"]
            if jr:
                R = np.array([j["R"] for j in jr])
                dd["journal_summary"] = {
                    "n": len(jr), "mean_R": float(R.mean()), "win_rate_R": float((R > 0).mean()),
                    "by_model": {m: int(sum(j["model"] == m for j in jr)) for m in ("TREND", "MR")},
                    "by_tier": {t_: int(sum(j["tier"] == t_ for j in jr)) for t_ in ("A", "B")},
                    "by_exit": {x: int(sum(j["exit"] == x for j in jr)) for x in sorted({j["exit"] for j in jr})},
                    "mean_hold_min": float(np.mean([j["hold_min"] for j in jr])),
                    "mean_weight": float(np.mean([j["weight"] for j in jr]))}
            dd["skipped"] = run["skipped"]
            row[s] = dd
            if s == "S1":
                s1_series[cfg] = net
        row["flag"] = fs.flag(row, null)
        out["configs"][cfg] = row
    srs = {c: ne_moments(x) for c, x in s1_series.items()}
    var_sr = float(np.var([v[0] for v in srs.values()], ddof=1))
    for c, (sr, sk, ku) in srs.items():
        L = len(s1_series[c])
        out["configs"][c][f"dsr_N{n}"] = deflated_sharpe(sr, L, sk, ku, n, var_sr)
        out["configs"][c]["dsr_cumulative"] = deflated_sharpe(sr, L, sk, ku, prior + n, var_sr)
    out["multiple_testing"] = {"n_trials_here": n, "prior_trials": prior, "cumulative": prior + n}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("null", {k: round(v, 2) for k, v in out["null_max_sr_ann"].items()})
    for cid, row in out["configs"].items():
        for s in fs.SEGMENTS:
            t = row[s]["trades"]
            print(cid, s, row["flag"], f"n={t.get('n', 0)} gross {t.get('mean_gross_bp', 0):.1f}bp cost "
                  f"{t.get('mean_cost_bp', 0):.1f} net {t.get('mean_net_bp', 0):.1f} hit {t.get('hit_rate', 0):.2f} "
                  f"SR {row[s]['measured']['sr_ann']:.2f} c0 {row[s]['cost0']['sr_ann']:.2f} "
                  f"mk {row[s]['maker_entry_taker_exit']['sr_ann']:.2f} vs {row[s]['volume_scaled']['sr_ann']:.2f} "
                  f"at {row[s]['measured']['alpha_t']:.2f} halves {[round(x, 2) for x in row[s]['halves_sr_ann']]}")
        print(cid, "DSRcum", round(row["dsr_cumulative"], 3))
    return 0


if __name__ == "__main__":
    sys.exit(main())
