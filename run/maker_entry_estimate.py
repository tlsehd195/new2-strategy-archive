#!/usr/bin/env python3
"""Limit-order (maker) entry cost estimate from public books + public aggTrades (ADR 0049).

Method fixed before any result in
``run/audit/evidence_maker_entry_20261028/prereg_maker_entry_v1.json``.  A simulated resting
order on a public tape is not a fill: the result is ESTIMATED_FROM_PUBLIC_DATA, never VERIFIED.

    python3 run/maker_entry_estimate.py --book-dir <orderbook_data> --tape-dir <aggTrades zips> --out o.json

Per symbol, at every valid 30 s snapshot (t0, decision mid m0), a buy (sell) limit order joins the best
bid (ask) at t0 + latency and rests up to the timeout.  Fill rules QUEUE / STRICT / TOUCH decide against
the full public tape.  Costs are in bp of m0, positive = worse for us.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PREREG = HERE / "audit" / "evidence_maker_entry_20261028" / "prereg_maker_entry_v1.json"
VERSION = "maker-entry-estimate-v1"
SYMBOLS = ["ADAUSDT", "BNBUSDT", "DOGEUSDT", "LINKUSDT", "LTCUSDT", "SOLUSDT", "XRPUSDT", "AVAXUSDT", "DOTUSDT", "SUIUSDT"]
NOTIONAL = 10_000.0
TAKER_FEE_BP = 5.0
LATENCIES_S = (0.0, 0.5, 2.0)
BASE_LATENCY_S = 0.5
TIMEOUTS_S = (30, 120, 600)
PRIMARY_TIMEOUT_S = 120
HORIZONS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
RULES = ("QUEUE", "STRICT", "TOUCH")
MAX_BOOK_AGE_MS = 5000
MAX_MID_GAP_MS = 60_000
BLOCK_MS = 2 * 3600 * 1000
STRESS_WINDOW_MS = 300_000
FEES_MAKER_BP = (0.0, 1.0, 2.0, 4.0)
BASE_FEE_MAKER_BP = 2.0
TAPE_MATCH_MIN = 0.99
N_BOOT = 400


# ------------------------------------------------------------------ loading

def vwap_side(levels: list, notional: float) -> float:
    """Average price of consuming ``notional`` USDT from (price, qty) levels; nan if the book is too thin."""
    rest, cost, qty = notional, 0.0, 0.0
    for p, q in levels:
        p, q = float(p), float(q)
        take = min(rest, p * q)
        cost += take
        qty += take / p
        rest -= take
        if rest <= 1e-9:
            return cost / qty
    return float("nan")


def load_book(sym_dir: Path) -> dict:
    """Valid snapshots of a symbol as arrays plus the in-snapshot trades kept only for the tape check."""
    rows, trades = [], set()
    for f in sorted(sym_dir.glob("public_*.jsonl")):
        for line in f.read_text().splitlines():
            r = json.loads(line)
            for t in r.get("agg_trades") or []:
                trades.add((int(t["T"]), round(float(t["p"]), 8), round(float(t["q"]), 8)))
            d = r.get("depth")
            if r.get("status") != "OK" or not d or not d.get("bids") or not d.get("asks"):
                continue
            if int(r["fetched_at_ms"]) - int(d["T"]) > MAX_BOOK_AGE_MS:
                continue
            bb, ba = float(d["bids"][0][0]), float(d["asks"][0][0])
            if not (0 < bb < ba):
                continue
            vb, vs = vwap_side(d["asks"], NOTIONAL), vwap_side(d["bids"], NOTIONAL)
            if not (vb == vb and vs == vs):
                continue
            rows.append((int(d["T"]), bb, float(d["bids"][0][1]), ba, float(d["asks"][0][1]), vb, vs))
    rows.sort()
    a = np.array(rows, float)
    return {"t": a[:, 0], "bb": a[:, 1], "bbq": a[:, 2], "ba": a[:, 3], "baq": a[:, 4], "vwb": a[:, 5], "vws": a[:, 6],
            "mid": (a[:, 1] + a[:, 3]) / 2.0, "snap_trades": trades}


def load_tape(tape_dir: Path, sym: str, t_lo: float, t_hi: float) -> dict:
    """Public aggTrades daily zips of the symbol, restricted to [t_lo - 1h, t_hi + 4h + 1h]."""
    tt, pp, qq, mm = [], [], [], []
    for z in sorted(tape_dir.glob(f"{sym}-aggTrades-*.zip")):
        with zipfile.ZipFile(z) as zf:
            with zf.open(zf.namelist()[0]) as fh:
                rd = csv.reader(io.TextIOWrapper(fh))
                next(rd)
                for r in rd:
                    t = int(r[5])
                    if t_lo - 3_600_000 <= t <= t_hi + 18_000_000:
                        tt.append(t)
                        pp.append(float(r[1]))
                        qq.append(float(r[2]))
                        mm.append(r[6] == "true")
    o = np.argsort(np.array(tt), kind="stable")
    return {"t": np.array(tt, float)[o], "p": np.array(pp)[o], "q": np.array(qq)[o], "m": np.array(mm)[o]}


def tape_match(book: dict, tape: dict) -> float:
    """Share of the in-snapshot trades (last 100 per snapshot) that the public tape contains."""
    have = set(zip(tape["t"].astype(np.int64).tolist(), np.round(tape["p"], 8).tolist(), np.round(tape["q"], 8).tolist()))
    snap = book["snap_trades"]
    lo, hi = max(book["t"].min(), tape["t"].min() + 3_600_000), min(book["t"].max(), tape["t"].max() - 18_000_000)
    inside = [x for x in snap if lo <= x[0] <= hi]
    return sum(1 for x in inside if x in have) / max(1, len(inside))


# ------------------------------------------------------------------ simulation

def mid_at(book: dict, t: np.ndarray) -> np.ndarray:
    """Mid by linear interpolation between the neighbouring snapshots (nan if either is > 60 s away)."""
    bt, bm = book["t"], book["mid"]
    i = np.searchsorted(bt, t, side="right") - 1
    ok = (i >= 0) & (i + 1 < len(bt))
    i = np.clip(i, 0, len(bt) - 2)
    gap = bt[i + 1] - bt[i]
    w = np.where(gap > 0, (t - bt[i]) / np.where(gap > 0, gap, 1), 0.0)
    out = bm[i] + w * (bm[i + 1] - bm[i])
    return np.where(ok & (gap <= MAX_MID_GAP_MS), out, np.nan)


def snap_before(book: dict, t: np.ndarray) -> tuple:
    """Index of the last snapshot at or before t and whether it is fresh enough (<= 60 s)."""
    i = np.searchsorted(book["t"], t, side="right") - 1
    ok = i >= 0
    i = np.clip(i, 0, len(book["t"]) - 1)
    return i, ok & (t - book["t"][i] <= MAX_MID_GAP_MS)


def first_fills(pp: np.ndarray, qq: np.ndarray, sell_side: np.ndarray, price: float, queue: float) -> tuple:
    """Index of the first fill under (QUEUE, STRICT, TOUCH) for a BUY resting at ``price``; -1 if none.

    ``sell_side`` marks the aggressor-sell prints (they hit bids).  The caller mirrors prices for a SELL order.
    """
    le = sell_side & (pp <= price)
    strict = np.flatnonzero(sell_side & (pp < price))
    s = int(strict[0]) if strict.size else -1
    touch_i = np.flatnonzero(le)
    t = int(touch_i[0]) if touch_i.size else -1
    cum = np.cumsum(np.where(le, qq, 0.0))
    hit = np.flatnonzero(cum >= queue)
    h = int(hit[0]) if hit.size else -1
    cands = [x for x in (s, h) if x >= 0]
    return (min(cands) if cands else -1), s, t


def simulate_symbol(book: dict, tape: dict, latency_s: float) -> dict:
    """One row per (snapshot, side): decision mid, join price, slip, fill times by rule, stress flag, block."""
    n = len(book["t"])
    lat = latency_s * 1000.0
    horizon = max(TIMEOUTS_S) * 1000.0
    tt, tp, tq, tm = tape["t"], tape["p"], tape["q"], tape["m"]
    mid = book["mid"]
    # trailing 5 minute range for the stress slice
    lo_i = np.searchsorted(book["t"], book["t"] - STRESS_WINDOW_MS, side="left")
    rng = np.array([(mid[lo_i[i]:i + 1].max() - mid[lo_i[i]:i + 1].min()) / mid[i] if book["t"][i] - book["t"][lo_i[i]] >= STRESS_WINDOW_MS * 0.8 else np.nan
                    for i in range(n)])
    cut = np.nanquantile(rng, 2 / 3)
    rows = {k: [] for k in ("i", "side", "t0", "m0", "slip", "off", "fill_QUEUE", "fill_STRICT", "fill_TOUCH", "stress", "block")}
    for i in range(n):
        t0 = book["t"][i]
        a = np.searchsorted(tt, t0 + lat, side="right")
        b = np.searchsorted(tt, t0 + lat + horizon, side="right")
        if b <= a or b >= len(tt) or tt[a] > t0 + lat + 60_000:
            continue    # the tape must cover the whole 600 s window (the 10-27 daily file is not yet published)
        pp, qq, mm = tp[a:b], tq[a:b], tm[a:b]
        for side in (1, -1):
            if side == 1:
                price, queue = book["bb"][i], book["bbq"][i]
                f_q, f_s, f_t = first_fills(pp, qq, mm, price, queue)
                off = (book["vwb"][i] - mid[i]) / mid[i] * 1e4
            else:
                price, queue = book["ba"][i], book["baq"][i]
                f_q, f_s, f_t = first_fills(-pp, qq, ~mm, -price, queue)
                off = (mid[i] - book["vws"][i]) / mid[i] * 1e4
            rows["i"].append(i)
            rows["side"].append(side)
            rows["t0"].append(t0)
            rows["m0"].append(mid[i])
            rows["slip"].append(side * (price - mid[i]) / mid[i] * 1e4)
            rows["off"].append(off)
            for name, f in (("QUEUE", f_q), ("STRICT", f_s), ("TOUCH", f_t)):
                rows[f"fill_{name}"].append(tt[a + f] if f >= 0 else np.nan)
            rows["stress"].append(bool(rng[i] >= cut) if rng[i] == rng[i] else False)
            rows["block"].append(int(t0 // BLOCK_MS))
    return {k: np.array(v) for k, v in rows.items()} | {"lat_ms": lat}


def order_outcomes(book: dict, sim: dict, rule: str, timeout_s: int) -> dict:
    """Per-order vectors for one (rule, timeout): filled flag, slip, AS_h, chase slip of the non-fills; nan = excluded."""
    lat, t0, side, m0 = sim["lat_ms"], sim["t0"], sim["side"], sim["m0"]
    tf = sim[f"fill_{rule}"]
    deadline = t0 + lat + timeout_s * 1000.0
    filled = tf <= deadline
    out = {"filled": filled}
    # adverse selection of the filled: -side * (mid(t_fill + h) - m0) / m0
    for name, h in HORIZONS.items():
        x = np.full(len(t0), np.nan)
        if filled.any():
            x[filled] = -side[filled] * (mid_at(book, tf[filled] + h * 1000.0) - m0[filled]) / m0[filled] * 1e4
        out[f"as_{name}"] = x
    # chase of the non-filled: cross at deadline + latency against the snapshot book at or before it
    tc = deadline + lat
    idx, ok = snap_before(book, tc)
    off = np.where(side == 1, (book["vwb"][idx] - book["mid"][idx]) / book["mid"][idx] * 1e4,
                   (book["mid"][idx] - book["vws"][idx]) / book["mid"][idx] * 1e4)
    drift = side * (mid_at(book, tc) - m0) / m0 * 1e4
    out["chase"] = np.where(~filled & ok, drift + off, np.nan)
    out["chase_ok"] = ok
    return out


def taker_baseline(book: dict, sim: dict) -> np.ndarray:
    """Market order at t0 + latency: drift since m0 plus the 10000 USDT vwap offset of the t0 book (no fee)."""
    drift = sim["side"] * (mid_at(book, sim["t0"] + sim["lat_ms"]) - sim["m0"]) / sim["m0"] * 1e4
    return drift + sim["off"]


# ------------------------------------------------------------------ statistics

def stat_block(sim: dict, oc: dict, base: np.ndarray, sel: np.ndarray, fee_m: float) -> dict:
    """Aggregate for the orders in ``sel`` (a boolean mask): fill rate, C_f (abandon), chase per side, taker baseline."""
    f = oc["filled"] & sel
    nf = (~oc["filled"]) & sel & oc["chase_ok"] & ~np.isnan(oc["chase"])
    usable = sel & (oc["filled"] | (oc["chase_ok"] & ~np.isnan(oc["chase"])))
    n, nfill = int(usable.sum()), int((oc["filled"] & usable).sum())
    if n == 0 or nfill == 0:
        return {"n": n, "fill_rate": None}
    p = nfill / n
    fill = oc["filled"] & usable
    slip_fill = float(np.nanmean(sim["slip"][fill]))
    res = {"n": n, "fill_rate": p, "slip_fill_bp": slip_fill}
    for name in HORIZONS:
        v = oc[f"as_{name}"][fill]
        res[f"as_{name}_bp"] = float(np.nanmean(v)) if np.isfinite(v).any() else None
    as4 = res["as_4h_bp"] if res["as_4h_bp"] is not None else 0.0
    res["C_f_abandon_bp"] = slip_fill + as4 + fee_m
    chase_slip = float(np.nanmean(oc["chase"][nf])) if nf.any() else 0.0
    res["chase_slip_nonfill_bp"] = chase_slip
    res["C_side_chase_bp"] = p * (slip_fill + fee_m) + (1 - p) * (chase_slip + TAKER_FEE_BP)
    tk = base[usable]
    res["taker_side_bp"] = float(np.nanmean(tk)) + TAKER_FEE_BP
    res["lost_trade_share_abandon"] = 1 - p
    return res


def block_boot(sim: dict, oc: dict, base: np.ndarray, sel: np.ndarray, fee_m: float, seed: int = 0) -> dict:
    """95% interval of C_side_chase and C_f over 2 hour blocks (only ~48 blocks per symbol exist)."""
    blocks = np.unique(sim["block"])
    idx = {b: np.flatnonzero((sim["block"] == b) & sel) for b in blocks}
    rng = np.random.default_rng(seed)
    cs, cf = [], []
    for _ in range(N_BOOT):
        pick = rng.choice(blocks, len(blocks))
        sel2 = np.zeros(len(sim["t0"]), bool)
        w = np.zeros(len(sim["t0"]))
        for b in pick:
            w[idx[b]] += 1
        # resample by weights: replicate counts through weighted means
        r = stat_weighted(sim, oc, w, fee_m)
        if r is not None:
            cs.append(r[0])
            cf.append(r[1])
    if not cs:
        return {}
    return {"C_side_chase_bp_ci95": [float(np.percentile(cs, 2.5)), float(np.percentile(cs, 97.5))],
            "C_f_abandon_bp_ci95": [float(np.percentile(cf, 2.5)), float(np.percentile(cf, 97.5))]}


def stat_weighted(sim: dict, oc: dict, w: np.ndarray, fee_m: float):
    usable = (w > 0) & (oc["filled"] | (oc["chase_ok"] & ~np.isnan(oc["chase"])))
    fill = oc["filled"] & usable
    nf = ~oc["filled"] & usable
    wn = w[usable].sum()
    if wn == 0 or w[fill].sum() == 0:
        return None
    p = w[fill].sum() / wn
    slip = np.average(sim["slip"][fill], weights=w[fill])
    a = oc["as_4h"][fill]
    okm = np.isfinite(a)
    as4 = np.average(a[okm], weights=w[fill][okm]) if okm.any() else 0.0
    cs = np.average(oc["chase"][nf], weights=w[nf]) if w[nf].sum() > 0 else 0.0
    return p * (slip + fee_m) + (1 - p) * (cs + TAKER_FEE_BP), slip + as4 + fee_m


def analyse(books: dict, tapes: dict, latency_s: float) -> dict:
    out = {}
    for sym in books:
        book, tape = books[sym], tapes[sym]
        sim = simulate_symbol(book, tape, latency_s)
        base = taker_baseline(book, sim)
        sym_out = {"n_orders": int(len(sim["t0"])), "n_buy_sell_each": int((sim["side"] == 1).sum())}
        for rule in RULES:
            for to in TIMEOUTS_S:
                oc = order_outcomes(book, sim, rule, to)
                cell = {}
                for sl_name, sel in (("pooled", np.ones(len(sim["t0"]), bool)), ("stress", sim["stress"])):
                    for fee in FEES_MAKER_BP:
                        if fee != BASE_FEE_MAKER_BP and not (rule == "QUEUE" and to == PRIMARY_TIMEOUT_S):
                            continue
                        cell[f"{sl_name}|fee{fee:g}"] = stat_block(sim, oc, base, sel, fee)
                    if rule == "QUEUE" and to == PRIMARY_TIMEOUT_S:
                        cell[f"{sl_name}|fee{BASE_FEE_MAKER_BP:g}"].update(
                            block_boot(sim, oc, base, sel, BASE_FEE_MAKER_BP))
                sym_out[f"{rule}|{to}s"] = cell
        out[sym] = sym_out
    return out


def worst_symbol(per_sym: dict, rule: str, to: int, sl: str, fee: float, key: str):
    vals = {s: v[f"{rule}|{to}s"].get(f"{sl}|fee{fee:g}", {}).get(key) for s, v in per_sym.items()}
    vals = {s: x for s, x in vals.items() if x is not None}
    if not vals:
        return None
    w = max(vals, key=vals.get)
    return {"symbol": w, "value_bp": vals[w], "median_bp": float(np.median(list(vals.values()))), "by_symbol": vals}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--book-dir", type=Path, required=True)
    ap.add_argument("--tape-dir", type=Path, required=True)
    ap.add_argument("--symbols", nargs="+", default=SYMBOLS)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    books, tapes, check, dropped = {}, {}, {}, {}
    for s in a.symbols:
        b = load_book(a.book_dir / s)
        t = load_tape(a.tape_dir, s, b["t"].min(), b["t"].max())
        m = tape_match(b, t)
        check[s] = {"snapshots_ok": int(len(b["t"])), "tape_trades": int(len(t["t"])), "snapshot_trade_match": m}
        if m < TAPE_MATCH_MIN:
            dropped[s] = f"tape match {m:.4f} < {TAPE_MATCH_MIN}"
            continue
        b.pop("snap_trades")
        books[s], tapes[s] = b, t
        print(s, check[s], flush=True)
    res = {"version": VERSION, "prereg_sha256": sha(PREREG), "script_sha256": sha(Path(__file__)),
           "grade": "ESTIMATED_FROM_PUBLIC_DATA (not VERIFIED; a simulated resting order is not a fill)",
           "tape_check": check, "dropped": dropped, "by_latency": {}}
    for lat in LATENCIES_S:
        per = analyse(books, tapes, lat)
        summary = {}
        for rule in RULES:
            for to in TIMEOUTS_S:
                for sl in ("pooled", "stress"):
                    key = f"{rule}|{to}s|{sl}"
                    fees = FEES_MAKER_BP if (rule == "QUEUE" and to == PRIMARY_TIMEOUT_S) else (BASE_FEE_MAKER_BP,)
                    for fee in fees:
                        summary[f"{key}|fee{fee:g}"] = {
                            "C_side_chase_worst": worst_symbol(per, rule, to, sl, fee, "C_side_chase_bp"),
                            "C_f_abandon_worst": worst_symbol(per, rule, to, sl, fee, "C_f_abandon_bp"),
                            "fill_rate_min": worst_symbol(per, rule, to, sl, fee, "lost_trade_share_abandon"),
                            "taker_side_worst": worst_symbol(per, rule, to, sl, fee, "taker_side_bp")}
        res["by_latency"][f"{lat:g}s"] = {"per_symbol": per, "summary": summary}
        print("latency", lat, "done", flush=True)
    a.out.write_text(json.dumps(res, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
