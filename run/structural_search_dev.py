#!/usr/bin/env python3
"""DEV-only search for market-structure return sources (ADR 0020).

Question: does a return that comes from how the perpetual market is built
(funding payments, relative prices of similar coins, the funding settlement
clock) survive the assumed costs, on development data only?

The 22 configurations are fixed in
``run/audit/evidence_structural_20261021/prereg_structural_v1.json`` (committed
before any result).  This script runs exactly those, records every one, and
writes the evidence next to the preregistration.

Families (see the preregistration for the exact rules):

- CC  cash-and-carry funding harvest (long spot + short perp).  The data has
      no spot or quarterly prices, so the basis P&L is NOT measured: CC rows
      are an upper bound on funding income after costs, not a backtest, and
      are not ranked by Sharpe.
- XC  perp-only cross-sectional carry on 15 symbols, dollar neutral.
- PR  pairs relative value (distance method + OLS hedge), daily.
- FT  funding-settlement timing on 15m bars.

Never reads: 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards, BTC or
ETH.  No orders, no private API, no NEW2.

Usage::

    python3 run/structural_search_dev.py --data-dir <unzipped alts bundle>
"""

from __future__ import annotations

import argparse
import warnings
import datetime as dt
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "discovery"))
from engine_lib import deflated_sharpe, expected_max_sr  # noqa: E402

VERSION = "structural-search-dev-v1"
PREREG = HERE / "audit" / "evidence_structural_20261021" / "prereg_structural_v1.json"
COST_PROFILE = HERE / "discovery" / "config" / "cost_assumed_public_v1.json"
DAY_MS = 86_400_000
BAR_MS = 900_000
MIN_MS = 60_000
DAYS_PER_YEAR = 365.0
ORDER_VALUE = 10_000.0
#: trials already spent on the same days by the strong-candidate search
#: (ADR 0019: Track A 3,857 + Track B 62) — one family with these 22
PRIOR_TRIALS = 3_919

#: assumed spot leg of cash-and-carry (Binance spot base tier taker; not fetched here)
SPOT_FEE = 0.0010
SPOT_HALF_SPREAD_SLIP = 0.0005

FORBIDDEN = [
    ("*", "2000-01-01", "2020-12-31", "IW1 (2020) and earlier"),
    ("*", "2023-09-19", "2024-06-18", "BTC validation + locked TEST (cross-symbol) and embargo"),
    ("*", "2025-10-21", "2099-12-31", "ETH validation, locked TEST and paper window, with embargo"),
    ("BTCUSDT", "2000-01-01", "2099-12-31", "not used by this study"),
    ("ETHUSDT", "2000-01-01", "2099-12-31", "not used by this study"),
]


def assert_allowed(sym: str, lo: str, hi: str) -> None:
    for s, a, b, why in FORBIDDEN:
        if (s == "*" or s == sym) and lo <= b and a <= hi:
            raise SystemExit(f"refused: read {sym} [{lo},{hi}] overlaps {why} [{a},{b}]")


def day_ms(iso: str) -> int:
    d = dt.date.fromisoformat(iso)
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


def months(lo: str, hi: str) -> list:
    d, end, out = dt.date.fromisoformat(lo).replace(day=1), dt.date.fromisoformat(hi), []
    while d <= end:
        out.append(f"{d:%Y-%m}")
        d = (d.replace(day=28) + dt.timedelta(days=6)).replace(day=1)
    return out


# ---------------------------------------------------------------------------
# data: raw monthly CSVs, hard-truncated to the segment, never filled
# ---------------------------------------------------------------------------

class Seg:
    """One segment: 15m open/quote-volume on a full grid (NaN = missing),
    settled funding per symbol, and daily arrays derived from them."""

    def __init__(self, name: str, spec: dict, syms: list, data_dir: Path):
        lo, hi = spec["read"]
        self.name, self.syms = name, syms
        t0, t1 = day_ms(lo), day_ms(hi) + DAY_MS        # [t0, t1)
        self.grid = np.arange(t0, t1, BAR_MS, dtype=np.int64)
        S, B = len(syms), len(self.grid)
        self.o = np.full((S, B), np.nan)
        self.qv = np.full((S, B), np.nan)
        self.f_ts, self.f_rate = [], []
        self.missing_bars, self.files = {}, []
        for i, s in enumerate(syms):
            assert_allowed(s, lo, hi)
            frames = []
            for m in months(lo, hi):
                p = data_dir / "klines" / f"{s}-15m-{m}.csv"
                if p.exists():
                    df = pd.read_csv(p, header=None, usecols=[0, 1, 7], names=["ts", "o", "qv"],
                                     dtype=str)
                    df = df[df.ts.str.isdigit()]
                    frames.append(df.astype({"ts": np.int64, "o": float, "qv": float}))
                    self.files.append(p)
            k = pd.concat(frames) if frames else pd.DataFrame(columns=["ts", "o", "qv"])
            k = k[(k.ts >= t0) & (k.ts < t1)].drop_duplicates("ts")
            idx = ((k.ts.to_numpy() - t0) // BAR_MS).astype(int)
            ok = (k.ts.to_numpy() - t0) % BAR_MS == 0
            self.o[i, idx[ok]] = k.o.to_numpy()[ok]
            self.qv[i, idx[ok]] = k.qv.to_numpy()[ok]
            self.missing_bars[s] = int(np.isnan(self.o[i]).sum())
            fr = []
            for m in months(lo, hi):
                p = data_dir / "funding" / f"{s}-fundingRate-{m}.csv"
                if p.exists():
                    self.files.append(p)
                    for line in p.read_text().splitlines()[1:]:
                        a = line.split(",")
                        if a and a[0].isdigit():
                            fr.append((int(a[0]), float(a[2])))
            fr = sorted(x for x in fr if t0 <= x[0] < t1)
            self.f_ts.append(np.array([x[0] for x in fr], dtype=np.int64))
            self.f_rate.append(np.array([x[1] for x in fr]))
        # daily grid: decision/execution at each day's 00:00 open
        self.days = np.arange(t0 // DAY_MS, t1 // DAY_MS)
        D = len(self.days)
        self.first = int(day_ms(spec["entry_from"]) // DAY_MS - self.days[0])
        bpd = DAY_MS // BAR_MS
        self.dopen = self.o[:, ::bpd][:, :D]                      # open of 00:00 bar
        nxt = np.full((S, D), np.nan)
        nxt[:, :-1] = self.dopen[:, 1:]
        self.dret = nxt / self.dopen - 1.0                        # open d -> open d+1
        prevq = np.full((S, D), np.nan)
        prevq[:, 1:] = self.qv[:, bpd - 1::bpd][:, :D - 1]        # 23:45 bar before the open
        self.dqv_prev = prevq
        # funding credited to holding day d: settlements in [open d + 1min, open d+1 + 1min)
        # (the 00:00 settlement belongs to the day that ends there); the signal
        # for day d uses only settlements strictly before open d.
        starts = self.days * DAY_MS
        self.dfund = np.zeros((S, D))
        self.f_mean3 = np.full((S, D), np.nan)
        self.f_mean21 = np.full((S, D), np.nan)
        for i in range(S):
            ts, r = self.f_ts[i], self.f_rate[i]
            if not len(ts):
                continue
            cum = np.concatenate([[0.0], np.cumsum(r)])
            a = np.searchsorted(ts, starts + MIN_MS)
            b = np.searchsorted(ts, starts + DAY_MS + MIN_MS)
            self.dfund[i] = cum[b] - cum[a]
            kk = np.searchsorted(ts, starts)                       # count strictly before open
            for n, out in ((3, self.f_mean3), (21, self.f_mean21)):
                good = kk >= n
                out[i, good] = (cum[kk[good]] - cum[kk[good] - n]) / n
        self.valid = ~np.isnan(self.dret)


def load_cost() -> dict:
    p = json.loads(COST_PROFILE.read_text())
    return {"fee": p["fee_rate"], "hs": p["base_half_spread"] + p["slippage_margin_per_side"],
            "imp": p["impact_coefficient"], "maxp": p["max_participation"],
            "profile_id": p["profile_id"],
            "sha256": hashlib.sha256(COST_PROFILE.read_bytes()).hexdigest()}


def side_cost(qv: np.ndarray, cost: dict, fee: float | None = None, hs: float | None = None) -> np.ndarray:
    """Per-side cost fraction; a bar with no traded value or participation
    above the cap is priced at 100 bp (never free)."""
    fee = cost["fee"] if fee is None else fee
    hs = cost["hs"] if hs is None else hs
    with np.errstate(divide="ignore", invalid="ignore"):
        part = ORDER_VALUE / qv
        c = fee + hs + cost["imp"] * np.sqrt(part)
    bad = ~(qv > 0) | (part > cost["maxp"])
    return np.where(bad, 0.01, c)


# ---------------------------------------------------------------------------
# daily portfolio engine
# ---------------------------------------------------------------------------

def run_perp(sg: Seg, w: np.ndarray, cost: dict, mult: float = 1.0) -> np.ndarray:
    """w: S x D perp weights decided with info before each open, traded at it.
    A symbol-day without both opens is flat.  Returns daily bp, mean over S."""
    w = np.where(sg.valid, np.nan_to_num(w), 0.0)
    dw = np.abs(np.diff(np.concatenate([np.zeros((w.shape[0], 1)), w], axis=1), axis=1))
    c = side_cost(sg.dqv_prev, cost) * mult
    pnl = w * np.nan_to_num(sg.dret) - dw * c - w * sg.dfund
    return pnl.mean(0)[sg.first:] * 1e4


def run_cc(sg: Seg, w: np.ndarray, cost: dict, mult: float = 1.0) -> tuple:
    """Cash-and-carry: w in [0,1] = long spot + short perp.  Basis P&L = 0
    (not measured).  Funding received = w * funding."""
    w = np.where(sg.valid, np.nan_to_num(w), 0.0)
    dw = np.abs(np.diff(np.concatenate([np.zeros((w.shape[0], 1)), w], axis=1), axis=1))
    c = (side_cost(sg.dqv_prev, cost) + side_cost(sg.dqv_prev, cost, SPOT_FEE, SPOT_HALF_SPREAD_SLIP)) * mult
    pnl = w * sg.dfund - dw * c
    inv = w[:, sg.first:].mean()
    return pnl.mean(0)[sg.first:] * 1e4, float(inv)


def hold(w: np.ndarray, every: int, first: int) -> np.ndarray:
    if every <= 1:
        return w
    out = w.copy()
    for j in range(w.shape[1]):
        if (j - first) % every:
            out[:, j] = out[:, j - 1]
    return out


def rank_weights(sig: np.ndarray, k: int, sign: int) -> np.ndarray:
    """+sign on the k highest, -sign on the k lowest (needs 2k valid)."""
    w = np.zeros_like(sig)
    for j in range(sig.shape[1]):
        col = sig[:, j]
        ok = np.flatnonzero(~np.isnan(col))
        if len(ok) < 2 * k:
            continue
        o = ok[np.argsort(col[ok])]
        w[o[-k:], j] = sign
        w[o[:k], j] = -sign
    return w


# ---------------------------------------------------------------------------
# families
# ---------------------------------------------------------------------------

def cc_weights(sg: Seg, cid: str) -> np.ndarray:
    S, D = sg.dfund.shape
    if cid == "CC1":
        return np.ones((S, D))
    if cid in ("CC2", "CC3"):
        thr = 1e-4 if cid == "CC2" else 3e-4
        w = np.zeros((S, D))
        for i in range(S):
            on = 0.0
            for j in range(D):
                f = sg.f_mean3[i, j]
                if not np.isnan(f):
                    on = 1.0 if f > thr else (0.0 if f < 0 else on)
                w[i, j] = on
        return w
    k = 3 if cid == "CC4" else 5
    sig = sg.f_mean21
    w = np.zeros((S, D))
    for j in range(D):
        col = sig[:, j]
        ok = np.flatnonzero(~np.isnan(col) & (col > 0))
        if len(ok):
            w[ok[np.argsort(col[ok])][-k:], j] = 1.0
    return hold(w, 7, sg.first)


def xc_weights(sg: Seg, n_set: int, every: int) -> np.ndarray:
    sig = sg.f_mean3 if n_set == 3 else sg.f_mean21
    return hold(rank_weights(sig, 5, -1), every, sg.first)


def pr_daily(sg: Seg, F: int, N: int, z_in: float, cost: dict, mult: float = 1.0) -> tuple:
    """Distance-method pairs, re-formed every 30 days from day ``first``."""
    S, D = sg.dopen.shape
    lp = np.log(sg.dopen)
    c = side_cost(sg.dqv_prev, cost) * mult
    port = np.zeros(D)
    trades = 0
    for p0 in range(sg.first, D, 30):
        p1 = min(p0 + 30, D)
        form = lp[:, p0 - F:p0]
        if p0 - F < 0:
            continue
        good = [i for i in range(S) if not np.isnan(form[i]).any() and
                not np.isnan(lp[i, p0:p1]).all()]
        if len(good) < 2:
            continue
        norm = {i: form[i] - form[i, 0] for i in good}
        ssd = []
        for a_ in range(len(good)):
            for b_ in range(a_ + 1, len(good)):
                a, b = good[a_], good[b_]
                ssd.append((float(((norm[a] - norm[b]) ** 2).sum()), a, b))
        ssd.sort()
        for _, a, b in ssd[:N]:
            x, y = form[b], form[a]
            beta = float(np.polyfit(x, y, 1)[0])
            spr = y - beta * x
            mu, sd = float(spr.mean()), float(spr.std(ddof=1))
            if not sd > 0:
                continue
            wa_, wb_ = 1.0 / (1 + abs(beta)), beta / (1 + abs(beta))
            pos = 0.0            # +1 = long spread (long a, short b)
            prev_wa = prev_wb = 0.0
            for j in range(p0, p1):
                # target for day j decided from the open of day j-1 (one day delay)
                tgt = pos
                if j - 1 >= p0 and j < p1 - 1:
                    z = (lp[a, j - 1] - beta * lp[b, j - 1] - mu) / sd
                    if np.isnan(z):
                        tgt = pos
                    elif pos == 0 and abs(z) > z_in:
                        tgt = -float(np.sign(z))
                    elif pos != 0 and np.sign(z) != -pos:
                        tgt = 0.0
                if j == p1 - 1:
                    tgt = 0.0        # flat at period end
                if not (sg.valid[a, j] and sg.valid[b, j]):
                    tgt = 0.0
                pos = tgt
                wa, wb = pos * wa_, -pos * wb_
                cst = abs(wa - prev_wa) * c[a, j] + abs(wb - prev_wb) * c[b, j]
                if pos != 0 and prev_wa == 0:
                    trades += 1
                r = 0.0
                if pos != 0:
                    r = (wa * sg.dret[a, j] + wb * sg.dret[b, j]
                         - wa * sg.dfund[a, j] - wb * sg.dfund[b, j])
                port[j] += (r - cst) / N
                prev_wa, prev_wb = wa, wb
    return port[sg.first:] * 1e4, trades


def ft_daily(sg: Seg, window: str, thr: float, cost: dict, mult: float = 1.0) -> tuple:
    """Settlement-clock events on 15m bars; daily sum of event P&L by exit
    day, mean over symbols."""
    S, B = sg.o.shape
    t0 = int(sg.grid[0])
    D = len(sg.days)
    port = np.zeros(D)
    n_ev = 0
    off = {"pre": (-4, 0), "straddle": (-1, 1), "post": (0, 4)}[window]
    entry_from_bar = (sg.first * DAY_MS) // BAR_MS
    for i in range(S):
        ts, r = sg.f_ts[i], sg.f_rate[i]
        c = side_cost(np.concatenate([[np.nan], sg.qv[i, :-1]]), cost) * mult  # cost at bar k uses bar k-1
        for k in range(1, len(ts)):
            last = r[k - 1]
            if last > thr:
                s = -1.0
            elif last < -thr:
                s = 1.0
            else:
                continue
            tb = int((ts[k] - t0) // BAR_MS)          # bar that opens at the settlement
            e, x = tb + off[0], tb + off[1]
            if e < entry_from_bar or x >= B or e < 1:
                continue
            oe, ox = sg.o[i, e], sg.o[i, x]
            if np.isnan(oe) or np.isnan(ox):
                continue
            pnl = s * (ox / oe - 1.0) - c[e] - c[x]
            if window == "straddle":
                pnl -= s * r[k]                       # held through the settlement
            d = int((t0 + x * BAR_MS) // DAY_MS - sg.days[0])
            port[d] += pnl
            n_ev += 1
    return port[sg.first:] / S * 1e4, n_ev


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

def sr_ann(x: np.ndarray) -> float:
    sd = x.std(ddof=1) if len(x) > 2 else 0.0
    return float(x.mean() / sd * math.sqrt(DAYS_PER_YEAR)) if sd > 0 else float("nan")


def mdd(x: np.ndarray) -> float:
    cum = np.cumsum(x)
    return float(np.max(np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:] - cum)) if len(x) else 0.0


def moments(x: np.ndarray) -> tuple:
    sd = x.std(ddof=0)
    if sd <= 0:
        return 0.0, 3.0
    z = (x - x.mean()) / sd
    return float((z ** 3).mean()), float((z ** 4).mean())


def summ(x: np.ndarray, mkt: np.ndarray) -> dict:
    return {"sr_ann": sr_ann(x), "apr_bp": float(x.mean() * DAYS_PER_YEAR),
            "mdd_bp": mdd(x), "folds_sr": [sr_ann(f) for f in np.array_split(x, 5)],
            "days": int(len(x)), "corr_mkt": float(np.corrcoef(x, mkt)[0, 1]) if x.std() > 0 else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=PREREG.parent / "structural_search_dev.json")
    a = ap.parse_args(argv)
    pre = json.loads(PREREG.read_text())
    syms = pre["universe"]
    cost = load_cost()
    segs = {n: Seg(n, spec, syms, a.data_dir) for n, spec in pre["segments"].items()}
    res: dict = {"version": VERSION, "prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(),
                 "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 "cost_profile": cost["profile_id"], "cost_sha256": cost["sha256"],
                 "spot_leg_assumed": {"fee": SPOT_FEE, "half_spread_plus_slippage": SPOT_HALF_SPREAD_SLIP},
                 "data": {}, "rows": []}
    h = hashlib.sha256()
    for sg in segs.values():
        for p in sorted(set(sg.files)):
            h.update(p.name.encode())
            h.update(hashlib.sha256(p.read_bytes()).digest())
        res["data"][sg.name] = {"missing_15m_bars": sg.missing_bars,
                                "days_scored": int(len(sg.days) - sg.first)}
    res["data"]["files_sha256"] = h.hexdigest()
    for sg in segs.values():
        c = side_cost(sg.dqv_prev, cost)[:, sg.first:] * 1e4
        res["data"][sg.name]["perp_side_cost_bp_at_00utc"] = {
            "median": float(np.nanmedian(c)), "p25": float(np.nanpercentile(c, 25)),
            "p75": float(np.nanpercentile(c, 75))}
        res["data"][sg.name]["funding_apr_pct"] = {
            s: float(np.nansum(sg.dfund[i, sg.first:]) / ((len(sg.days) - sg.first) / DAYS_PER_YEAR) * 100)
            for i, s in enumerate(sg.syms)}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)       # the last day has no next open
        mkt = {n: np.nanmean(np.where(sg.valid, sg.dret, np.nan), axis=0)[sg.first:] * 1e4
               for n, sg in segs.items()}
    res["market_ew_long"] = {n: {"sr_ann": sr_ann(np.nan_to_num(m)), "apr_bp": float(np.nanmean(m) * 365)}
                             for n, m in mkt.items()}

    rows = []

    #: reporting-only sensitivity: the same profile without its impact term
    #: (fee + half spread + slippage = 10 bp a side); never used to choose
    flat = dict(cost, imp=0.0)
    runs = (("", cost, 1.0), ("_cost2x", cost, 2.0), ("_cost0x", cost, 0.0), ("_no_impact", flat, 1.0))

    def add(fam, cid, cfg, fn):
        row = {"family": fam, "id": cid, "config": cfg}
        for n, sg in segs.items():
            for suffix, cst, mult in runs:
                out = fn(sg, mult, cst)
                x, extra = (out if isinstance(out, tuple) else (out, None))
                key = n + suffix
                row[key] = summ(x, np.nan_to_num(mkt[n]))
                if extra is not None:
                    row[key]["extra"] = extra
                if key == "S1":
                    row["_x"] = x
        rows.append(row)
        print(f"{cid:5s} S1 SR {row['S1']['sr_ann']:6.2f} APR {row['S1']['apr_bp']:8.1f}bp "
              f"| S2 SR {row['S2']['sr_ann']:6.2f} APR {row['S2']['apr_bp']:8.1f}bp "
              f"| 2x S1 {row['S1_cost2x']['sr_ann']:6.2f} | no-cost S1 {row['S1_cost0x']['sr_ann']:6.2f} "
              f"S2 {row['S2_cost0x']['sr_ann']:6.2f} | no-impact S1 {row['S1_no_impact']['sr_ann']:6.2f} "
              f"S2 {row['S2_no_impact']['sr_ann']:6.2f} APR S1 {row['S1_no_impact']['apr_bp']:7.1f} "
              f"S2 {row['S2_no_impact']['apr_bp']:7.1f}", flush=True)

    for cid in ("CC1", "CC2", "CC3", "CC4", "CC5"):
        add("CC", cid, {}, lambda sg, m, c, i=cid: run_cc(sg, cc_weights(sg, i), c, m))
    for cid, n, e in (("XC1", 3, 7), ("XC2", 21, 7), ("XC3", 21, 1)):
        add("XC", cid, {"signal_settlements": n, "rebalance_days": e},
            lambda sg, m, c, n=n, e=e: run_perp(sg, xc_weights(sg, n, e), c, m))
    k = 0
    for F in (60, 120):
        for N in (5, 10):
            for z in (1.5, 2.0):
                k += 1
                add("PR", f"PR{k}", {"F": F, "N": N, "z_in": z},
                    lambda sg, m, c, F=F, N=N, z=z: pr_daily(sg, F, N, z, c, m))
    k = 0
    for win in ("pre", "straddle", "post"):
        for thr in (1e-4, 3e-4):
            k += 1
            add("FT", f"FT{k}", {"window": win, "thr": thr},
                lambda sg, m, c, w=win, t=thr: ft_daily(sg, w, t, c, m))

    assert len(rows) == pre["trial_count"], "every preregistered config must run exactly once"
    # DSR over the Sharpe-ranked rows (CC has no measured basis: not ranked)
    ranked = [r for r in rows if r["family"] != "CC"]
    srd = np.array([r["_x"].mean() / r["_x"].std(ddof=1) for r in ranked])
    var_sr = float(np.var(srd, ddof=1))
    n_days = len(ranked[0]["_x"])
    years = n_days / DAYS_PER_YEAR
    for r in rows:
        x = r.pop("_x")
        if r["family"] == "CC" or not x.std() > 0:
            continue
        sk, ku = moments(x)
        s = float(x.mean() / x.std(ddof=1))
        r["dsr"] = {f"N={N}": deflated_sharpe(s, len(x), sk, ku, N, var_sr)
                    for N in (len(rows), len(rows) + PRIOR_TRIALS)}
    res["null"] = {f"N={N}": {"expected_max_sr_ann_unit_variance": expected_max_sr(N) / math.sqrt(years),
                              "expected_max_sr_ann_scaled": expected_max_sr(N) * math.sqrt(var_sr * DAYS_PER_YEAR)}
                   for N in (len(rows), len(rows) + PRIOR_TRIALS)}
    res["var_sr_daily"] = var_sr
    res["trials_this_study"] = len(rows)
    res["trials_family_total"] = len(rows) + PRIOR_TRIALS
    res["rows"] = rows
    a.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
    print(json.dumps({"market": res["market_ew_long"], "null": res["null"]}, indent=1))
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
