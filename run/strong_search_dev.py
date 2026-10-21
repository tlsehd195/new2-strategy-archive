#!/usr/bin/env python3
"""DEV-only search for markedly stronger candidates (ADR 0019).

Question (동동, 2026-10-21): can the DEV period produce candidates much
stronger than the R6 DEV-pass set (annual SR 0.7-1.2), strong enough for the
one-shot independent window IW1 (ADR 0016 power check: annual SR ~2.8+)?

What this measures, and how it keeps the search honest:

- The IW1 statistic, not the engine's DEV screen: each rule is applied to the
  six IW1 tranche-A symbols and the daily net series (bp, exit-day booking)
  are averaged with equal weight; cost = ``assumed_public_v1`` (the IW1 cost,
  dearer than the BTC/ETH public estimate).
- Reads only DEV time.  S1 = 2021-01..2023-09-18 for all six symbols (search).
  S2 = 2024-06-19..2025-10-20 without BTC (a second DEV stretch, reported but
  never used to choose).  Never read: 2020 (IW1), 2023-09-19..2024-06-18 (BTC
  validation + locked TEST, all symbols because of ADR 0010), BTC after its
  locked TEST (paper window), anything from 2025-10-21 (ETH validation,
  locked TEST, paper).  ``assert_allowed`` refuses any other read.
- Every configuration evaluated counts as a trial.  The report gives the
  null expectation of the best SR for that many trials (Bailey et al. 2014),
  the deflated Sharpe ratio, PBO by CSCV over all trials, the S1->S2
  transfer of the S1 ranking, time-fold stability and duplicate clusters.
- Track A = rules in the existing spec language (``strategy_spec_v1``): the
  only kind IW1 can test as preregistered.  Track B = ideas outside it
  (daily trend over weeks, cross-sectional momentum/reversal, funding
  carry, bundles); IW1 could only test them after a v2 preregistration.

Nothing here writes a ledger, a status, a validation history row or touches
NEW2.  Research only.

    python3 run/strong_search_dev.py --data-dir <dir with klines/ funding/> --out <dir>
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "discovery"))
import runner  # noqa: E402  (engine loaders and quality gate)
from engine_lib import expected_max_sr, deflated_sharpe, strategy_id  # noqa: E402
from strategy_spec import SPEC_SCHEMA, FEATURES, canonicalize_spec  # noqa: E402

VERSION = "strong-search-dev-v1"
DAY_MS = 86_400_000
BAR_MS = 900_000
DAYS_PER_YEAR = 365.0
TRANCHE_A = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "TRXUSDT", "ZECUSDT"]

SEGMENTS = {
    "S1": {"read_start": "2021-01-01", "entry_start": "2021-04-01", "read_end": "2023-09-18",
           "symbols": TRANCHE_A, "use": "search (all choices are made here)"},
    "S2": {"read_start": "2024-06-19", "entry_start": "2024-07-15", "read_end": "2025-10-20",
           "symbols": [s for s in TRANCHE_A if s != "BTCUSDT"],
           "use": "second DEV stretch, reported only (ETH part already in the engine's DEV)"},
}

#: (symbol or "*", first day, last day, why) — inclusive, never read
FORBIDDEN = [
    ("*", "2000-01-01", "2020-12-31", "IW1 (2020) and earlier"),
    ("*", "2023-09-19", "2024-06-18", "BTC validation + locked TEST (cross-symbol, ADR 0010) and embargo"),
    ("*", "2025-10-21", "2099-12-31", "ETH validation, locked TEST and paper window, with embargo"),
    ("BTCUSDT", "2024-06-19", "2099-12-31", "BTC paper window (after its locked TEST)"),
]

COST_PROFILE = HERE / "discovery" / "config" / "cost_assumed_public_v1.json"
ORDER_VALUE = 10_000.0


def assert_allowed(sym: str, lo: str, hi: str) -> None:
    for s, a, b, why in FORBIDDEN:
        if (s == "*" or s == sym) and lo <= b and a <= hi:
            raise SystemExit(f"refused: read {sym} [{lo},{hi}] overlaps {why} [{a},{b}]")


def day_ms(iso: str) -> int:
    d = dt.date.fromisoformat(iso)
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

WARMUP_DAYS = 30


class SymData:
    """One gap-free piece of one symbol (the engine's quality gate passed)."""

    def __init__(self, sym: str, seg: dict, data_dir: Path):
        assert_allowed(sym, seg["read_start"], seg["read_end"])
        h = runner.merged_bars(data_dir, sym, seg["read_start"], seg["read_end"])
        if not h.bars:
            raise ValueError(f"{sym} [{seg['read_start']},{seg['read_end']}]: no usable bars ({h.missing})")
        fund, rep = runner.load_funding_window_checked(data_dir, sym, seg["read_start"], seg["read_end"])
        end_ms = day_ms(seg["read_end"]) + DAY_MS - 1
        fund = [(t, r) for t, r in fund if day_ms(seg["read_start"]) <= t <= end_ms]
        fq = runner.checked_funding_quality(fund, rep)
        self.sym, self.bars, self.dataset_sha = sym, h.bars, h.sha256
        self.funding_ok = bool(fq.get("ok", True)) if isinstance(fq, dict) else True
        b = h.bars
        self.ts = np.array([x.ts for x in b], dtype=np.int64)
        self.o = np.array([x.open for x in b])
        self.h = np.array([x.high for x in b])
        self.l = np.array([x.low for x in b])
        self.c = np.array([x.close for x in b])
        self.v = np.array([x.volume for x in b])
        self.qv = np.array([x.quote_volume for x in b])
        self.tbv = np.array([x.taker_buy_volume for x in b])
        self.funding = fund
        self.f_ts = np.array([t for t, _ in fund], dtype=np.int64)
        self.f_rate = np.array([r for _, r in fund])
        self.f_cum = np.concatenate([[0.0], np.cumsum(self.f_rate)])
        self.entry_lo = int(np.searchsorted(self.ts, day_ms(seg["entry_start"])))
        self.hi = len(b) - 1
        self.feat: dict = {}


def _gap_days(data_dir: Path, sym: str, lo: str, hi: str) -> list:
    """UTC days with missing 15m bars in the raw monthly files (never filled)."""
    import csv
    have = set()
    d, end = dt.date.fromisoformat(lo).replace(day=1), dt.date.fromisoformat(hi)
    while d <= end:
        p = data_dir / "klines" / f"{sym}-15m-{d:%Y-%m}.csv"
        if p.exists():
            with p.open() as fh:
                for row in csv.reader(fh):
                    if row and row[0].isdigit():
                        have.add(int(row[0]))
        d = (d.replace(day=28) + dt.timedelta(days=6)).replace(day=1)
    out = []
    d = dt.date.fromisoformat(lo)
    while d <= end:
        t0 = day_ms(d.isoformat())
        if any(t0 + i * BAR_MS not in have for i in range(96)):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def load_segment(name: str, data_dir: Path) -> dict:
    """{symbol: [gap-free pieces]}.  A day with a missing bar splits the
    segment (fail-closed, nothing interpolated); each later piece warms its
    features up for WARMUP_DAYS before it may enter."""
    seg = SEGMENTS[name]
    out = {}
    for s in seg["symbols"]:
        gaps = _gap_days(data_dir, s, seg["read_start"], seg["read_end"])
        pieces, start = [], dt.date.fromisoformat(seg["read_start"])
        for g in gaps + [dt.date.fromisoformat(seg["read_end"]) + dt.timedelta(days=1)]:
            if g > start:
                pieces.append((start, g - dt.timedelta(days=1)))
            start = g + dt.timedelta(days=1)
        lst = []
        for a, b in pieces:
            entry = max(dt.date.fromisoformat(seg["entry_start"]), a + dt.timedelta(days=WARMUP_DAYS))
            if entry >= b:
                continue
            lst.append(SymData(s, {"read_start": a.isoformat(), "entry_start": entry.isoformat(),
                                   "read_end": b.isoformat()}, data_dir))
        out[s] = {"pieces": lst, "gap_days": [g.isoformat() for g in gaps]}
    return out


# ---------------------------------------------------------------------------
# features (numpy versions of strategy_spec features; cross-checked against
# the engine in crosscheck())
# ---------------------------------------------------------------------------

def _prior_z(x: np.ndarray, w: int) -> np.ndarray:
    """runner._rolling_z: current value vs the w PRIOR values (>= 90% valid)."""
    s = pd.Series(x).shift(1)
    r = s.rolling(w, min_periods=int(math.ceil(w * 0.9)))
    mu, sd = r.mean().to_numpy(), r.std(ddof=1).to_numpy()
    out = (x - mu) / np.where(sd > 0, sd, np.nan)
    out[:w] = np.nan
    return out


def feature(sd: SymData, name: str, p: dict) -> np.ndarray:
    key = (name, tuple(sorted(p.items())))
    if key in sd.feat:
        return sd.feat[key]
    n = len(sd.c)
    c = sd.c
    if name == "ret_z":
        lb, w, mh = p["lookback"], p["window"], p["min_history"]
        rets = np.full(n, np.nan)
        prev = c[:-lb]
        rets[lb:] = np.where(prev > 0, c[lb:] / np.where(prev > 0, prev, 1) - 1.0, np.nan)
        out = _prior_z(rets, w)
        out[:mh] = np.nan
    elif name == "taker_share_z":
        share = np.where(sd.v > 0, sd.tbv / np.where(sd.v > 0, sd.v, 1), np.nan)
        out = _prior_z(share, p["window"])
    elif name == "volume_z":
        out = _prior_z(sd.qv.astype(float), p["window"])
    elif name == "breakout_k":
        w = p["window"]
        s = pd.Series(c).shift(1)
        r = s.rolling(w, min_periods=w)
        mx, sdv = r.max().to_numpy(), r.std(ddof=1).to_numpy()
        out = (c - mx) / np.where(sdv > 0, sdv, np.nan)
        out[:w + 1] = np.nan
    elif name == "vol_ratio":
        lr = np.full(n, np.nan)
        ok = (c[:-1] > 0) & (c[1:] > 0)
        lr[1:] = np.where(ok, np.log(np.where(ok, c[1:], 1) / np.where(ok, c[:-1], 1)), np.nan)
        s = pd.Series(lr)

        def rsd(w):
            return s.rolling(w, min_periods=max(2, int(math.ceil(w * 0.9)))).std(ddof=1).to_numpy()
        a, b = rsd(p["short"]), rsd(p["long"])
        out = a / np.where(b > 0, b, np.nan)
        out[:p["long"]] = np.nan
    elif name == "rsi":
        per = p["period"]
        d = np.diff(c, prepend=np.nan)
        g = pd.Series(np.where(d > 0, d, 0.0)).rolling(per, min_periods=per).sum().to_numpy()
        lo = pd.Series(np.where(d < 0, -d, 0.0)).rolling(per, min_periods=per).sum().to_numpy()
        lo = np.where(np.abs(lo) < 1e-12 * np.maximum(1.0, np.abs(g)), 0.0, lo)
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where(lo == 0, 100.0, 100.0 - 100.0 / (1.0 + g / lo))
        out[:per] = np.nan
    elif name == "hour_utc":
        out = ((sd.ts // 3_600_000) % 24).astype(float)
    elif name == "funding_z":
        known = runner.funding_known_series(sd.bars, sd.funding)
        z = runner.funding_settlement_z(sd.funding, known, p["window"])
        out = np.array([np.nan if v is None else v for v in z])
    else:
        raise KeyError(name)
    sd.feat[key] = out
    return out


def _holds(x: np.ndarray, op: str, v) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        if op == "gt":
            m = x > v
        elif op == "ge":
            m = x >= v
        elif op == "lt":
            m = x < v
        elif op == "le":
            m = x <= v
        else:
            m = (x >= v[0]) & (x < v[1])
    return m & ~np.isnan(x)


def spec_side(sd: SymData, spec: dict) -> np.ndarray:
    """+1 / -1 / 0 decision per bar, same rule as strategy_spec.build_spec_signal."""
    def side_ok(mirror: bool) -> np.ndarray:
        def m(cnd):
            x = feature(sd, cnd["feature"], cnd["params"])
            if mirror and FEATURES[cnd["feature"]].signed:
                x = -x
            return _holds(x, cnd["op"], cnd["value"])
        ent = [m(cnd) for cnd in spec["entry"]["conditions"]]
        ok = np.logical_and.reduce(ent) if spec["entry"]["logic"] == "all" else np.logical_or.reduce(ent)
        for f in spec["filters"]:
            ok = ok & m(f)
        return ok
    if spec["direction"] == "long":
        return side_ok(False).astype(np.int8)
    if spec["direction"] == "short":
        return -side_ok(False).astype(np.int8)
    a, b = side_ok(False), side_ok(True)
    return np.where(a, 1, np.where(b, -1, 0)).astype(np.int8)


# ---------------------------------------------------------------------------
# event study (same rules as evaluator.run_event_study, no stops)
# ---------------------------------------------------------------------------

def load_cost() -> dict:
    p = json.loads(COST_PROFILE.read_text())
    return {"fee": p["fee_rate"], "hs": p["base_half_spread"] + p["slippage_margin_per_side"],
            "imp": p["impact_coefficient"], "maxp": p["max_participation"],
            "profile_id": p["profile_id"],
            "sha256": hashlib.sha256(COST_PROFILE.read_bytes()).hexdigest()}


def side_cost_bp(qv: float, cost: dict) -> float | None:
    if not qv > 0:
        return None
    part = ORDER_VALUE / qv
    if part > cost["maxp"]:
        return None
    return (cost["fee"] + cost["hs"] + cost["imp"] * math.sqrt(part)) * 1e4


def events(sd: SymData, side: np.ndarray, horizon: int, cost: dict, cost_mult: float = 1.0) -> list:
    """Non-overlapping events: decide at close t, enter open t+1, exit open
    t+1+H; exits never after the segment's last bar."""
    lo_t = max(1, sd.entry_lo - 1)
    cand = np.flatnonzero(side[lo_t:sd.hi]) + lo_t
    out = []
    k = 0
    nf = len(sd.f_ts)
    while k < len(cand):
        t = int(cand[k])
        e, x = t + 1, t + 1 + horizon
        if x > sd.hi:
            break
        ce, cx = side_cost_bp(sd.qv[e - 1], cost), side_cost_bp(sd.qv[x - 1], cost)
        if ce is None or cx is None:
            k += 1
            continue
        s = int(side[t])
        ets, xts = sd.ts[e], sd.ts[x]
        if nf == 0 or sd.f_ts[0] > ets or sd.f_ts[-1] < xts:
            k += 1
            continue
        a = np.searchsorted(sd.f_ts, ets, side="right")
        b = np.searchsorted(sd.f_ts, xts, side="right")
        fp = s * (sd.f_cum[b] - sd.f_cum[a]) * 1e4
        gross = s * (sd.o[x] / sd.o[e] - 1.0) * 1e4
        cst = (ce + cx) * cost_mult
        out.append((e, x, s, int(ets), int(xts), gross, cst, fp, gross - cst - fp))
        k = int(np.searchsorted(cand, x, side="left"))
    return out


def day_grid(seg: dict) -> tuple[int, int]:
    return day_ms(seg["entry_start"]) // DAY_MS, day_ms(seg["read_end"]) // DAY_MS


def daily(evs: list, d0: int, d1: int) -> np.ndarray:
    out = np.zeros(d1 - d0 + 1)
    for ev in evs:
        d = ev[4] // DAY_MS
        if d0 <= d <= d1:
            out[d - d0] += ev[8]
    return out


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

def sr_ann(x: np.ndarray) -> float:
    if len(x) < 3:
        return float("nan")
    sd = x.std(ddof=1)
    return float(x.mean() / sd * math.sqrt(DAYS_PER_YEAR)) if sd > 0 else float("nan")


def mdd(x: np.ndarray) -> float:
    cum = np.cumsum(x)
    return float(np.max(np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:] - cum)) if len(x) else 0.0


def fold_srs(x: np.ndarray, k: int = 5) -> list:
    return [sr_ann(f) for f in np.array_split(x, k)]


def moments(x: np.ndarray) -> tuple[float, float]:
    sd = x.std(ddof=0)
    if sd <= 0:
        return 0.0, 3.0
    z = (x - x.mean()) / sd
    return float((z ** 3).mean()), float((z ** 4).mean())


def pbo_cscv(mat: np.ndarray, parts: int = 16, max_combos: int = 4000, seed: int = 0) -> dict:
    """CSCV PBO on a T x N matrix of daily returns, choosing by Sharpe."""
    T, N = mat.shape
    blocks = np.array_split(np.arange(T), parts)
    s1 = np.stack([mat[b].sum(0) for b in blocks])
    s2 = np.stack([(mat[b] ** 2).sum(0) for b in blocks])
    cnt = np.array([len(b) for b in blocks])
    combos = list(itertools.combinations(range(parts), parts // 2))
    rng = np.random.default_rng(seed)
    if len(combos) > max_combos:
        combos = [combos[i] for i in rng.choice(len(combos), max_combos, replace=False)]
    logits, oos_best = [], []
    allb = set(range(parts))

    def sharpe(idx):
        n = cnt[list(idx)].sum()
        m = s1[list(idx)].sum(0) / n
        v = s2[list(idx)].sum(0) / n - m ** 2
        return m / np.sqrt(np.where(v > 0, v, np.nan))
    for cb in combos:
        tr, te = sharpe(cb), sharpe(tuple(allb - set(cb)))
        best = int(np.nanargmax(tr))
        rank = (np.nan_to_num(te, nan=-np.inf) < te[best]).sum() + 1
        w = rank / (N + 1)
        logits.append(math.log(w / (1 - w)))
        oos_best.append(float(te[best] * math.sqrt(DAYS_PER_YEAR)))
    lg = np.array(logits)
    return {"pbo": float((lg <= 0).mean()), "combinations": len(combos), "trials": N,
            "median_oos_sr_ann_of_is_best": float(np.median(oos_best)),
            "share_oos_sr_of_is_best_positive": float((np.array(oos_best) > 0).mean())}


def clusters(mat: np.ndarray, order: list, rho: float = 0.7, limit: int = 40) -> list:
    """Greedy duplicate clusters in ``order`` (best first): a series joins the
    first representative it correlates with above ``rho``."""
    reps: list = []
    members: dict = {}
    z = (mat - mat.mean(0)) / np.where(mat.std(0) > 0, mat.std(0), np.nan)
    for j in order:
        if np.isnan(z[:, j]).any():
            continue
        hit = None
        for r in reps:
            if float(np.mean(z[:, j] * z[:, r])) > rho:
                hit = r
                break
        if hit is None:
            reps.append(j)
            members[j] = [j]
            if len(reps) >= limit:
                break
        else:
            members[hit].append(j)
    return [(r, members[r]) for r in reps]


def effective_trials(mat: np.ndarray, sample: int = 1500, seed: int = 1) -> float:
    """Participation-ratio estimate of the number of independent trials
    (sum lambda)^2 / sum lambda^2 of the correlation matrix (subsample)."""
    N = mat.shape[1]
    rng = np.random.default_rng(seed)
    idx = rng.choice(N, min(sample, N), replace=False)
    m = mat[:, idx]
    m = m[:, m.std(0) > 0]
    cmat = np.corrcoef(m, rowvar=False)
    ev = np.linalg.eigvalsh(cmat)
    ev = ev[ev > 0]
    pr = float(ev.sum() ** 2 / (ev ** 2).sum())
    return pr * (N / m.shape[1]) if m.shape[1] < N else pr


# ---------------------------------------------------------------------------
# Track A: spec-language grid
# ---------------------------------------------------------------------------

def C(feature_, params, op, value):
    return {"feature": feature_, "params": params, "op": op, "value": value}


def single_conditions() -> list:
    out = []
    for lb in (4, 16, 48, 96, 192, 384):
        for w in sorted({max(64, 2 * lb), 2048}):
            for t in (1.5, 2.0, 2.5, 3.0):
                out.append(C("ret_z", {"lookback": lb, "window": w, "min_history": 0}, "gt", t))
                out.append(C("ret_z", {"lookback": lb, "window": w, "min_history": 0}, "lt", -t))
    for w in (16, 48, 96, 192, 384, 768):
        for k in (0.0, 0.5, 1.0, 1.5):
            out.append(C("breakout_k", {"window": w}, "gt", k))
    for w in (24, 96, 384):
        for t in (2.0, 2.5, 3.0):
            out.append(C("taker_share_z", {"window": w}, "gt", t))
            out.append(C("taker_share_z", {"window": w}, "lt", -t))
    for w in (96, 384, 1536):
        for t in (2.0, 3.0):
            out.append(C("volume_z", {"window": w}, "gt", t))
    for w in (30, 90, 270):
        for t in (1.0, 1.5, 2.0):
            out.append(C("funding_z", {"window": w}, "gt", t))
            out.append(C("funding_z", {"window": w}, "lt", -t))
    for s, l_ in ((16, 192), (48, 768), (96, 1536)):
        for op, t in (("gt", 1.5), ("gt", 2.0), ("lt", 0.6), ("lt", 0.8)):
            out.append(C("vol_ratio", {"short": s, "long": l_}, op, t))
    for per in (14, 48, 192):
        for op, t in (("lt", 20.0), ("lt", 30.0), ("gt", 70.0), ("gt", 80.0)):
            out.append(C("rsi", {"period": per}, op, t))
    for h0 in range(0, 24, 4):
        out.append(C("hour_utc", {}, "between", [float(h0), float(h0 + 4)]))
    return out


FILTERS = [
    C("ret_z", {"lookback": 384, "window": 2048, "min_history": 0}, "gt", 0.0),
    C("ret_z", {"lookback": 384, "window": 2048, "min_history": 0}, "lt", 0.0),
    C("ret_z", {"lookback": 96, "window": 2048, "min_history": 0}, "gt", 0.5),
    C("ret_z", {"lookback": 96, "window": 2048, "min_history": 0}, "lt", -0.5),
    C("vol_ratio", {"short": 96, "long": 1536}, "gt", 1.2),
    C("vol_ratio", {"short": 96, "long": 1536}, "lt", 0.8),
    C("vol_ratio", {"short": 16, "long": 192}, "gt", 1.5),
    C("funding_z", {"window": 90}, "gt", 1.0),
    C("funding_z", {"window": 90}, "lt", -1.0),
    C("funding_z", {"window": 90}, "lt", 1.0),
    C("volume_z", {"window": 384}, "gt", 1.0),
    C("taker_share_z", {"window": 96}, "gt", 1.0),
    C("taker_share_z", {"window": 96}, "lt", -1.0),
    C("rsi", {"period": 192}, "gt", 55.0),
    C("rsi", {"period": 192}, "lt", 45.0),
    C("hour_utc", {}, "between", [0.0, 12.0]),
    C("hour_utc", {}, "between", [12.0, 24.0]),
]
HORIZONS = (16, 48, 96, 192, 384, 768)


def make_spec(direction, conds, horizon, filters=()):
    raw = {"schema": SPEC_SCHEMA, "direction": direction, "symbols": ["BTCUSDT", "ETHUSDT"],
           "entry": {"logic": "all", "conditions": list(conds)}, "filters": list(filters),
           "exit": {"horizon": horizon}}
    s = canonicalize_spec(raw)
    return strategy_id(s), s


def stage1_specs() -> list:
    out = []
    for cnd in single_conditions():
        dirs = ("long", "short", "symmetric") if FEATURES[cnd["feature"]].signed else ("long", "short")
        for d in dirs:
            for h in HORIZONS:
                try:
                    out.append(make_spec(d, [cnd], h))
                except Exception:
                    pass
    return out


# ---------------------------------------------------------------------------
# evaluation drivers
# ---------------------------------------------------------------------------

class Evaluator:
    def __init__(self, seg_name: str, data: dict, cost: dict):
        self.seg_name, self.data, self.cost = seg_name, data, cost
        self.d0, self.d1 = day_grid(SEGMENTS[seg_name])

    def spec(self, spec: dict, cost_mult: float = 1.0, keep_events: bool = False) -> dict:
        per, pooled = {}, np.zeros(self.d1 - self.d0 + 1)
        allev = []
        for s, entry in self.data.items():
            evs = []
            for sd in entry["pieces"]:
                evs += events(sd, spec_side(sd, spec), spec["exit"]["horizon"], self.cost, cost_mult)
            x = daily(evs, self.d0, self.d1)
            pooled += x
            nets = np.array([e[8] for e in evs])
            per[s] = {"n": len(evs), "mean_bp": float(nets.mean()) if len(evs) else None,
                      "sr_ann": sr_ann(x)}
            allev += evs
        pooled /= len(self.data)
        r = {"daily": pooled, "per_symbol": per, "n": len(allev)}
        if keep_events:
            r["events"] = allev
        return r


def summarize(r: dict) -> dict:
    x = r["daily"]
    nets = np.array([e[8] for e in r.get("events", [])]) if r.get("events") else None
    out = {"sr_ann": sr_ann(x), "mean_bp_day": float(x.mean()), "mdd_bp": mdd(x),
           "days": len(x), "active_days": int((x != 0).sum()), "n_events": r["n"],
           "fold_sr_ann": fold_srs(x), "per_symbol": r["per_symbol"]}
    out["fold_positive_share"] = float(np.mean([f > 0 for f in out["fold_sr_ann"] if f == f]))
    if nets is not None and len(nets):
        srt = np.sort(nets)[::-1]
        k = max(1, int(math.ceil(0.05 * len(nets))))
        tot = nets[nets > 0].sum()
        out.update({"mean_net_bp_trade": float(nets.mean()),
                    "mean_cost_bp_trade": float(np.mean([e[6] for e in r["events"]])),
                    "top5pct_profit_share": float(srt[:k].sum() / tot) if tot > 0 else None,
                    "long_share": float(np.mean([e[2] == 1 for e in r["events"]]))})
    return out


def dsr_block(x: np.ndarray, n_trials: float, var_sr_daily: float) -> dict:
    sr_d = x.mean() / x.std(ddof=1)
    sk, ku = moments(x)
    n = len(x)
    years = n / DAYS_PER_YEAR
    e_max = expected_max_sr(int(round(n_trials)))
    return {"trials": n_trials,
            "expected_max_sr_ann_null_unit": e_max / math.sqrt(years),
            "expected_max_sr_ann_null_scaled": e_max * math.sqrt(var_sr_daily) * math.sqrt(DAYS_PER_YEAR),
            "dsr": deflated_sharpe(float(sr_d), n, sk, ku, int(round(n_trials)), var_sr_daily)}


# ---------------------------------------------------------------------------
# Track B: daily portfolios outside the spec language
# ---------------------------------------------------------------------------

class Daily:
    """Per-symbol daily arrays on the segment day grid: open at 00:00 UTC,
    traded value of the bar before it, funding settled in (open d, open d+1]."""

    def __init__(self, data: dict, seg: dict):
        d0, d1 = day_grid(seg)
        # include warm-up days from the read start for look-back signals
        r0 = day_ms(seg["read_start"]) // DAY_MS
        self.days = np.arange(r0, d1 + 1)
        self.first = d0 - r0
        self.syms = list(data)
        S, D = len(self.syms), len(self.days)
        self.o = np.full((S, D + 1), np.nan)
        self.qv = np.full((S, D + 1), np.nan)
        self.fund = np.zeros((S, D))
        self.fund_last = np.full((S, D), np.nan)
        starts = (np.append(self.days, d1 + 1)) * DAY_MS
        for i, s in enumerate(self.syms):
            for sd in data[s]["pieces"]:
                idx = np.searchsorted(sd.ts, starts)
                for j, (ix, st) in enumerate(zip(idx, starts)):
                    if ix < len(sd.ts) and sd.ts[ix] == st:
                        self.o[i, j] = sd.o[ix]
                        self.qv[i, j] = sd.qv[ix - 1] if ix > 0 else np.nan
                inside = (starts[:-1] >= sd.ts[0]) & (starts[1:] <= sd.ts[-1] + BAR_MS)
                if len(sd.f_ts):
                    a = np.searchsorted(sd.f_ts, starts[:-1], side="right")
                    b = np.searchsorted(sd.f_ts, starts[1:], side="right")
                    k = a - 1
                    last3 = np.where(k >= 2, (sd.f_cum[np.maximum(k, 0) + 1]
                                              - sd.f_cum[np.maximum(k - 2, 0)]) / 3.0, np.nan)
                    self.fund[i] = np.where(inside, sd.f_cum[b] - sd.f_cum[a], self.fund[i])
                    self.fund_last[i] = np.where(inside, last3, self.fund_last[i])
        # last bar of the segment: the final day's "next open" is missing; flat that day
        self.ret = self.o[:, 1:] / self.o[:, :-1] - 1.0

    def run(self, w: np.ndarray, cost: dict, cost_mult: float = 1.0) -> np.ndarray:
        """w: S x D target weights decided at each day's open (info < open)."""
        w = np.nan_to_num(w)
        dw = np.abs(np.diff(np.concatenate([np.zeros((w.shape[0], 1)), w], axis=1), axis=1))
        part = ORDER_VALUE / np.where(self.qv[:, :-1] > 0, self.qv[:, :-1], np.nan)
        side_cost = (cost["fee"] + cost["hs"] + cost["imp"] * np.sqrt(part)) * cost_mult
        side_cost = np.nan_to_num(side_cost, nan=0.01)
        pnl = w * np.nan_to_num(self.ret) - dw * side_cost - w * self.fund
        port = pnl.mean(0) * 1e4
        return port[self.first:]


def past_ret(dl: Daily, L: int) -> np.ndarray:
    """Return over the L days before each day's open (known at the open)."""
    o = dl.o[:, :-1]
    out = np.full_like(o, np.nan)
    out[:, L:] = o[:, L:] / o[:, :-L] - 1.0
    return out


def hold(w: np.ndarray, every: int) -> np.ndarray:
    if every <= 1:
        return w
    out = w.copy()
    for j in range(w.shape[1]):
        if j % every:
            out[:, j] = out[:, j - 1]
    return out


def xs_rank_weights(sig: np.ndarray, k: int, sign: int) -> np.ndarray:
    w = np.zeros_like(sig)
    for j in range(sig.shape[1]):
        col = sig[:, j]
        ok = ~np.isnan(col)
        if ok.sum() < 2 * k:
            continue
        order = np.argsort(np.where(ok, col, np.nan))
        order = [i for i in order if ok[i]]
        w[order[-k:], j] = sign
        w[order[:k], j] = -sign
    return w


def track_b_configs() -> list:
    out = []
    for L in (3, 7, 14, 30, 60, 90):
        for mode in ("long_only", "short_only", "long_short"):
            for every in (1, 7):
                out.append({"kind": "ts_mom", "L": L, "mode": mode, "rebalance_days": every})
    for L in (1, 3, 7, 14, 30):
        for sgn, nm in ((1, "xs_mom"), (-1, "xs_rev")):
            for every in (1, 7):
                out.append({"kind": nm, "L": L, "k": 2, "rebalance_days": every})
    for every in (1, 7):
        out.append({"kind": "xs_carry", "k": 2, "rebalance_days": every})
        for thr in (1e-4, 3e-4):
            out.append({"kind": "ts_carry", "thr": thr, "rebalance_days": every})
    return out


def track_b_weights(dl: Daily, cfg: dict) -> np.ndarray:
    k = cfg["kind"]
    if k == "ts_mom":
        r = past_ret(dl, cfg["L"])
        s = np.sign(np.nan_to_num(r))
        if cfg["mode"] == "long_only":
            s = np.maximum(s, 0)
        elif cfg["mode"] == "short_only":
            s = np.minimum(s, 0)
        w = s
    elif k in ("xs_mom", "xs_rev"):
        w = xs_rank_weights(past_ret(dl, cfg["L"]), cfg["k"], 1 if k == "xs_mom" else -1)
    elif k == "xs_carry":
        w = xs_rank_weights(dl.fund_last, cfg["k"], -1)     # short the highest funding
    elif k == "ts_carry":
        f = dl.fund_last
        w = np.where(f > cfg["thr"], -1.0, np.where(f < 0, 1.0, 0.0))
    else:
        raise KeyError(k)
    return hold(w, cfg["rebalance_days"])


# ---------------------------------------------------------------------------
# engine cross-check
# ---------------------------------------------------------------------------

def crosscheck(spec: dict, sd: SymData) -> dict:
    """Run the engine's own feature/signal/event code on the same bars and
    compare events (the fast path must give the same trades)."""
    from costs_bridge import CostContext
    from evaluator import run_event_study
    from strategy_spec import build_spec_aux, build_spec_signal
    cost_ctx = CostContext.from_assumed_profile("assumed_public_v1")
    aux = build_spec_aux(sd.bars, spec, sd.funding, None)
    res = run_event_study(sd.bars, sd.sym, build_spec_signal(spec, aux), spec["exit"]["horizon"],
                          cost_ctx, ORDER_VALUE, sd.funding, warmup=max(1, sd.entry_lo - 1))
    eng = [(e.entry_index, e.side, round(e.net_bp, 6)) for e in res.events]
    fast = [(e[0], e[2], round(e[8], 6)) for e in events(sd, spec_side(sd, spec),
                                                         spec["exit"]["horizon"], load_cost())]
    same = sum(1 for a, b in zip(eng, fast) if a[0] == b[0] and a[1] == b[1] and abs(a[2] - b[2]) < 1e-3)
    return {"symbol": sd.sym, "engine_events": len(eng), "fast_events": len(fast),
            "identical_prefix": same, "match": len(eng) == len(fast) == same,
            "engine_sum_bp": float(sum(x[2] for x in eng)), "fast_sum_bp": float(sum(x[2] for x in fast))}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--stage2-top", type=int, default=40)
    ap.add_argument("--crosscheck", type=int, default=6, help="engine cross-check of the top N")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    cost = load_cost()
    print("loading S1 / S2 ...", flush=True)
    data1, data2 = load_segment("S1", a.data_dir), load_segment("S2", a.data_dir)
    ev1, ev2 = Evaluator("S1", data1, cost), Evaluator("S2", data2, cost)

    # ---- Track A stage 1 ------------------------------------------------------
    specs = stage1_specs()
    print(f"track A stage 1: {len(specs)} specs", flush=True)
    ids, spec_by, mat1, nev = [], {}, [], []
    for i, (sid, s) in enumerate(specs):
        if sid in spec_by:
            continue
        r = ev1.spec(s)
        ids.append(sid)
        spec_by[sid] = s
        mat1.append(r["daily"])
        nev.append(r["n"])
        if i % 500 == 0:
            print(f"  {i} {time.time() - t0:.0f}s", flush=True)
    # ---- Track A stage 2: filters on the best distinct stage-1 rules ---------
    M = np.array(mat1).T
    srs = np.array([sr_ann(M[:, j]) for j in range(M.shape[1])])
    ok_n = np.array(nev) >= 60
    order = [int(j) for j in np.argsort(-np.nan_to_num(srs, nan=-9)) if ok_n[j]]
    base = [r for r, _ in clusters(M, order, rho=0.7, limit=a.stage2_top)]
    n_stage1 = len(ids)
    print(f"track A stage 2 on {len(base)} distinct rules x {len(FILTERS)} filters", flush=True)
    for j in base:
        s = spec_by[ids[j]]
        for f in FILTERS:
            try:
                sid, s2 = make_spec(s["direction"], s["entry"]["conditions"], s["exit"]["horizon"], [f])
            except Exception:
                continue
            if sid in spec_by:
                continue
            r = ev1.spec(s2)
            ids.append(sid)
            spec_by[sid] = s2
            mat1.append(r["daily"])
            nev.append(r["n"])
    M = np.array(mat1).T
    srs = np.array([sr_ann(M[:, j]) for j in range(M.shape[1])])
    nev = np.array(nev)
    N_A = M.shape[1]
    print(f"track A total trials {N_A} ({time.time() - t0:.0f}s)", flush=True)

    # ---- Track B --------------------------------------------------------------
    dl1, dl2 = Daily(data1, SEGMENTS["S1"]), Daily(data2, SEGMENTS["S2"])
    tb = []
    for cfg in track_b_configs():
        x1 = dl1.run(track_b_weights(dl1, cfg), cost)
        x2 = dl2.run(track_b_weights(dl2, cfg), cost)
        x1c = dl1.run(track_b_weights(dl1, cfg), cost, 1.5)
        tb.append({"config": cfg, "S1": {"sr_ann": sr_ann(x1), "mean_bp_day": float(x1.mean()),
                                         "mdd_bp": mdd(x1), "fold_sr_ann": fold_srs(x1)},
                   "S1_cost_x1.5_sr_ann": sr_ann(x1c),
                   "S2": {"sr_ann": sr_ann(x2), "mean_bp_day": float(x2.mean()), "mdd_bp": mdd(x2)},
                   "_x1": x1})
    N_B = len(tb)
    # market exposure reference (not a trial): always long all symbols, held
    bh = {}
    for nm, dl in (("S1", dl1), ("S2", dl2)):
        x = dl.run(np.ones((len(dl.syms), len(dl.days))), cost)
        bh[nm] = {"sr_ann": sr_ann(x), "mean_bp_day": float(x.mean()), "_x": x}

    # ---- honesty checks over all trials --------------------------------------
    valid = (nev >= 60) & np.isfinite(srs)
    var_sr_daily = float(np.nanvar(srs[valid] / math.sqrt(DAYS_PER_YEAR), ddof=1))
    n_eff = effective_trials(M[:, valid])
    pbo = pbo_cscv(M[:, valid])
    order = [int(j) for j in np.argsort(-np.nan_to_num(srs, nan=-9)) if nev[j] >= 60]
    reps = clusters(M, order, rho=0.7, limit=25)
    # S1 -> S2 transfer of the ranking: evaluate S2 for the top 300 and a random 300
    rng = np.random.default_rng(3)
    top300 = order[:300]
    rnd = [int(j) for j in rng.choice(np.flatnonzero(nev >= 60), min(300, int((nev >= 60).sum())),
                                      replace=False)]
    s2 = {}
    for j in sorted(set(top300) | set(rnd)):
        s2[j] = sr_ann(ev2.spec(spec_by[ids[j]])["daily"])
    tr_top = np.array([[srs[j], s2[j]] for j in top300])
    tr_rnd = np.array([[srs[j], s2[j]] for j in rnd])

    def spearman(m):
        m = m[np.isfinite(m).all(1)]
        if len(m) < 5:
            return None
        ra = pd.Series(m[:, 0]).rank().to_numpy()
        rb = pd.Series(m[:, 1]).rank().to_numpy()
        return float(np.corrcoef(ra, rb)[0, 1])

    # ---- candidate cards for the distinct leaders ---------------------------
    cards = []
    for rank, (j, mem) in enumerate(reps):
        s = spec_by[ids[j]]
        r1 = ev1.spec(s, keep_events=True)
        r1c = ev1.spec(s, cost_mult=1.5)
        r2 = ev2.spec(s, keep_events=True)
        card = {"rank": rank + 1, "strategy_id": ids[j], "spec": s, "cluster_size": len(mem),
                "S1": summarize(r1), "S1_cost_x1.5_sr_ann": sr_ann(r1c["daily"]),
                "S2": summarize(r2),
                "deflation": {"all_trials": dsr_block(r1["daily"], N_A + N_B, var_sr_daily),
                              "effective_trials": dsr_block(r1["daily"], max(2.0, n_eff), var_sr_daily)}}
        cards.append(card)
    # ---- bundle of the distinct leaders (Track B, selection-biased) ---------
    bundle = {}
    for k in (5, 10, 25):
        sel = [j for j, _ in reps[:k]]
        x1 = M[:, sel].mean(1)
        x2 = np.mean([ev2.spec(spec_by[ids[j]])["daily"] for j in sel], axis=0)
        bundle[f"top{k}_distinct"] = {"S1_sr_ann": sr_ann(x1), "S2_sr_ann": sr_ann(x2),
                                      "S1_fold_sr_ann": fold_srs(x1),
                                      "corr_with_buy_and_hold_S1": float(np.corrcoef(x1, bh["S1"]["_x"])[0, 1]),
                                      "corr_with_buy_and_hold_S2": float(np.corrcoef(x2, bh["S2"]["_x"])[0, 1])}
    for card in cards:
        x = ev1.spec(card["spec"])["daily"]
        card["S1"]["corr_with_buy_and_hold"] = float(np.corrcoef(x, bh["S1"]["_x"])[0, 1])
    # ---- engine cross-check ---------------------------------------------------
    xc = []
    for card in cards[:a.crosscheck]:
        for sym in ("BTCUSDT", "ZECUSDT"):
            xc.append({"strategy_id": card["strategy_id"], **crosscheck(card["spec"], data1[sym]["pieces"][0])})

    tb_sorted = sorted(tb, key=lambda r: -np.nan_to_num(r["S1"]["sr_ann"], nan=-9))
    for r in tb_sorted:
        r.pop("_x1", None)
    rep = {
        "version": VERSION, "segments": SEGMENTS, "forbidden_reads": FORBIDDEN,
        "cost": {k: cost[k] for k in ("profile_id", "sha256")}, "order_value": ORDER_VALUE,
        "statistic": "equal-weight mean over the segment symbols of daily net bp (exit-day booking for Track A, daily mark-to-market for Track B); annual SR = mean/sd*sqrt(365)",
        "datasets": {seg: {s: {"pieces": [[p.ts[0], p.ts[-1], p.dataset_sha] for p in d["pieces"]],
                               "gap_days": d["gap_days"]} for s, d in dd.items()}
                     for seg, dd in (("S1", data1), ("S2", data2))},
        "trials": {"track_a_stage1": n_stage1, "track_a_stage2": N_A - n_stage1, "track_a": N_A,
                   "track_b": N_B, "total": N_A + N_B,
                   "track_a_with_60plus_events": int((nev >= 60).sum()),
                   "effective_independent_trials_estimate": n_eff},
        "track_a_sr_distribution_S1": {q: float(np.nanpercentile(srs[nev >= 60], q)) for q in (50, 90, 99, 99.9, 100)},
        "null_reference": {
            "expected_max_sr_ann_if_all_trials_unskilled_unit_variance":
                expected_max_sr(N_A + N_B) / math.sqrt(len(M) / DAYS_PER_YEAR),
            "expected_max_sr_ann_effective_trials":
                expected_max_sr(int(max(2, n_eff))) / math.sqrt(len(M) / DAYS_PER_YEAR),
            "years_S1": len(M) / DAYS_PER_YEAR},
        "pbo_cscv_S1_track_a": pbo,
        "s1_to_s2_transfer": {
            "top300_by_S1": {"spearman_S1_vs_S2": spearman(tr_top),
                             "median_S2_sr_ann": float(np.nanmedian(tr_top[:, 1])),
                             "share_S2_positive": float(np.nanmean(tr_top[:, 1] > 0))},
            "random300": {"spearman_S1_vs_S2": spearman(tr_rnd),
                          "median_S2_sr_ann": float(np.nanmedian(tr_rnd[:, 1])),
                          "share_S2_positive": float(np.nanmean(tr_rnd[:, 1] > 0))}},
        "track_a_distinct_leaders": cards,
        "bundles_of_leaders": bundle,
        "buy_and_hold_reference": {k: {kk: vv for kk, vv in v.items() if kk != "_x"} for k, v in bh.items()},
        "track_b": tb_sorted,
        "engine_crosscheck": xc,
        "runtime_seconds": time.time() - t0,
    }
    (a.out / "strong_search_dev.json").write_text(json.dumps(rep, indent=1, default=float) + "\n")
    print(json.dumps({k: rep[k] for k in ("trials", "track_a_sr_distribution_S1", "null_reference",
                                          "pbo_cscv_S1_track_a", "s1_to_s2_transfer")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
