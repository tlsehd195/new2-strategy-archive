#!/usr/bin/env python3
"""Five new cross-sectional hypotheses, DEV only, measured alt cost (ADR 0039).

Runs exactly the hypotheses fixed in
``run/audit/evidence_cross_section_v2_20261027/prereg_cross_section_v2.json``
(committed before any data was downloaded) on the point-in-time top-50
universe of ADR 0035, rebalancing every 28 days with drifting weights.

Never reads: 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards; BTCUSDT
in S2.  No orders, no private API, no NEW2.

Usage (data from run/broad_universe_fetch.py, steps daily/funding/bar2345)::

    python3 run/cross_section_v2_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import datetime as dt
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

VERSION = "cross-section-dev-v2"
EVID = HERE / "audit" / "evidence_cross_section_v2_20261027"
PREREG = EVID / "prereg_cross_section_v2.json"
COST_RECORD = HERE / "audit" / "alt_cost_measured_20261027" / "estimate_v3_alts.json"
HOLD_DAYS = 28
SEGMENTS = bu.SEGMENTS
#: signal -> True when the HIGH end of the signal is the long side
LONG_TOP = {"N1_SIZE": False, "N2_VOLSHOCK": True, "N3_MAX": False, "N4_TAKERFLOW": True, "N5_COMPOSITE": True}
COST_ROWS = ("measured", "measured_x2", "assumed_public_v1", "cost0")


def load_daily(data: Path, seg: str) -> dict:
    """Daily open/close/quote volume/taker-buy quote volume of every symbol in the segment."""
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
                recs.append((sym, t, float(f[1]), float(f[4]), float(f[7]), float(f[10])))
    df = pd.DataFrame(recs, columns=["sym", "t", "open", "close", "qv", "tbq"]).drop_duplicates(["sym", "t"])
    assert df["t"].between(t0, t1).all()
    days = np.arange(t0, t1 + 1, bu.DAY_MS)
    return {k: df.pivot(index="t", columns="sym", values=k).reindex(days) for k in ("open", "close", "qv", "tbq")}


def signals(piv: dict, cut: int, universe: list) -> dict:
    """Signals from bars with index <= cut, restricted to the universe."""
    close, qv, tbq = (piv[k][universe] for k in ("close", "qv", "tbq"))
    out = {}
    w30 = qv.iloc[max(0, cut - 29): cut + 1]
    out["N1_SIZE"] = w30.mean()
    rec, old = qv.iloc[max(0, cut - 6): cut + 1], qv.iloc[max(0, cut - 59): max(0, cut - 6)]
    shock = np.log(rec.mean() / old.mean())
    shock[(rec.notna().sum() < 7) | (old.notna().sum() < 53) | ~np.isfinite(shock)] = np.nan
    out["N2_VOLSHOCK"] = shock
    r = close.iloc[max(0, cut - 28): cut + 1].pct_change(fill_method=None).iloc[1:]
    mx = r.max()
    mx[r.notna().sum() < 28] = np.nan
    out["N3_MAX"] = mx
    q28, t28 = qv.iloc[max(0, cut - 27): cut + 1], tbq.iloc[max(0, cut - 27): cut + 1]
    flow = t28.sum() / q28.sum()
    flow[(q28.notna().sum() < 28) | (t28.notna().sum() < 28) | ~(q28.sum() > 0)] = np.nan
    out["N4_TAKERFLOW"] = flow
    ranks = []
    for h in ("N1_SIZE", "N2_VOLSHOCK", "N3_MAX", "N4_TAKERFLOW"):
        s = out[h] if LONG_TOP[h] else -out[h]
        ranks.append(s.rank(pct=True))
    out["N5_COMPOSITE"] = pd.concat(ranks, axis=1).mean(axis=1, skipna=False)
    return out


def rebalance_days(days: np.ndarray, first: str) -> list:
    first_ms = bu.ms(first)
    mondays = [i for i, t in enumerate(days)
               if t >= first_ms and dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).weekday() == 0]
    return list(range(mondays[0], len(days) - 1, HOLD_DAYS)) if mondays else []


def side_cost(b: float, row: str, measured: dict, assumed: dict) -> float:
    if row == "cost0":
        return 0.0
    c = assumed if row == "assumed_public_v1" else measured
    v = c["fixed"] + c["imp"] * math.sqrt(bu.ORDER_VALUE / b)
    return 2.0 * v if row == "measured_x2" else v


def run_segment(data: Path, seg: str, measured: dict, assumed: dict) -> dict:
    piv = load_daily(data, seg)
    days = piv["open"].index.values
    syms = list(piv["open"].columns)
    t0, t1 = int(days[0]), int(days[-1])
    fund = bu.load_funding(data, syms, t0, t1)
    fund["day"] = ((fund["t"] - 1) // bu.DAY_MS) * bu.DAY_MS
    fund_daily = fund.groupby(["day", "sym"])["rate"].sum().unstack().reindex(days).reindex(columns=syms)
    bars = bu.load_bar2345(data, syms, t0, t1)
    opn, qv = piv["open"], piv["qv"]
    ret = (opn.shift(-1) / opn - 1.0).values
    fnd = fund_daily.values
    col = {s: k for k, s in enumerate(syms)}
    n_bars = piv["close"].notna().cumsum()
    last_bar = {s: (piv["close"][s].last_valid_index()) for s in syms}
    reb = rebalance_days(days, SEGMENTS[seg][2])
    hyps = list(LONG_TOP)
    daily = {h: {r: np.zeros(len(days)) for r in COST_ROWS} for h in hyps}
    mkt = np.full(len(days), np.nan)
    flags = {"missing_return_days": 0, "impact_bar_fallback": 0, "no_volume_cost_fallback": 0,
             "participation_over_max": 0, "rebalances": 0, "universe_sizes": [],
             "data_end_while_held": {h: {"long": 0, "short": 0} for h in hyps}}
    turnover = {h: [] for h in hyps}
    cost_paid = {h: 0.0 for h in hyps}
    held = {h: {} for h in hyps}  # drifted weights
    for j, i in enumerate(reb):
        cut = i - 2
        if cut < 0:
            continue
        nb = n_bars.iloc[cut]
        vol30 = qv.iloc[max(0, cut - 29): cut + 1].mean()
        elig = [s for s in syms if nb[s] >= bu.MIN_BARS and not math.isnan(opn.iloc[i][s]) and not math.isnan(vol30[s])]
        universe = sorted(elig, key=lambda s: (-vol30[s], s))[:bu.TOP_N]
        if len(universe) < 2 * bu.K_SIDE:
            continue
        flags["rebalances"] += 1
        flags["universe_sizes"].append(len(universe))
        sig = signals(piv, cut, universe)
        end = reb[j + 1] if j + 1 < len(reb) else len(days) - 1
        uc = [col[s] for s in universe]
        for d in range(i, end):
            r = ret[d, uc]
            mkt[d] = float(np.nanmean(r)) if np.isfinite(r).any() else 0.0
        bar_t = int(days[i - 1]) + 23 * 3_600_000 + 45 * 60_000
        for h in hyps:
            w = bu.pick(sig[h], universe, LONG_TOP[h])
            prev = held[h]
            to = 0.0
            for s in sorted(set(w) | set(prev)):
                dw = abs(w.get(s, 0.0) - prev.get(s, 0.0))
                if dw == 0:
                    continue
                b = bars.get((s, bar_t))
                if b is None or b <= 0:
                    flags["impact_bar_fallback"] += 1
                    dq = qv.iloc[i - 1][s]
                    if not (dq > 0):
                        dq = float(np.nanmean(qv[s].iloc[max(0, i - 30): i].values))
                        flags["no_volume_cost_fallback"] += 1
                    b = dq / 96.0 if dq > 0 else 1.0
                if bu.ORDER_VALUE / b > measured["maxp"]:
                    flags["participation_over_max"] += 1
                for row in COST_ROWS:
                    daily[h][row][i] -= dw * side_cost(b, row, measured, assumed)
                cost_paid[h] += dw * side_cost(b, "measured", measured, assumed)
                to += dw
            turnover[h].append(to)
            for s, ws in w.items():
                lb = last_bar[s]
                if lb is not None and lb < days[end] and lb >= days[i]:
                    flags["data_end_while_held"][h]["long" if ws > 0 else "short"] += 1
            cur = dict(w)
            for d in range(i, end):
                pnl = 0.0
                for s, ws in cur.items():
                    r = ret[d, col[s]]
                    if math.isnan(r):
                        flags["missing_return_days"] += 1
                        r = 0.0
                    f = fnd[d, col[s]]
                    pnl += ws * r - ws * (0.0 if math.isnan(f) else f)
                    cur[s] = ws * (1.0 + r)
                for row in COST_ROWS:
                    daily[h][row][d] += pnl
            held[h] = cur
    span = slice(reb[0], len(days) - 1)
    us = flags.pop("universe_sizes")
    flags["universe_size_min_mean"] = [min(us) if us else 0, float(np.mean(us)) if us else 0.0]
    return {"days": [int(x) for x in days[span]], "mkt": mkt[span],
            "daily": {h: {r: daily[h][r][span] for r in COST_ROWS} for h in hyps},
            "turnover": {h: float(np.mean(turnover[h])) if turnover[h] else 0.0 for h in hyps},
            "cost_paid": cost_paid, "flags": flags}


def flag(row: dict, null_sr: float) -> str:
    s1 = row["S1"]["measured"]
    ok = (s1["sr_ann"] > null_sr and s1["alpha_t"] >= 2 and min(row["S1"]["halves_sr_ann"]) > 0
          and row["S2"]["measured"]["sr_ann"] > 0.5 and row["S1"]["measured_x2"]["sr_ann"] > 0)
    return "DEV_PROMISING" if ok else "NOT_PROMISING"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "cross_section_v2_dev.json")
    a = ap.parse_args(argv)
    measured, assumed = bu.load_cost_record(COST_RECORD), bu.load_cost()
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "helper_sha256": bu.sha(HERE / "broad_universe_dev.py"), "cost_record_sha256": bu.sha(COST_RECORD),
           "cost_profile_sha256": bu.sha(bu.COST_PROFILE), "cost": {"measured": measured, "assumed": assumed},
           "segments": {}, "hypotheses": {}}
    segs = {s: run_segment(a.data_dir, s, measured, assumed) for s in SEGMENTS}
    for s, r in segs.items():
        out["segments"][s] = {"first_day": r["days"][0], "last_day": r["days"][-1], "n_days": len(r["days"]),
                              "flags": r["flags"], "market_ew": bu.stats(np.nan_to_num(r["mkt"]), r["mkt"])}
    s1_years = len(segs["S1"]["days"]) / 365.0
    null_sr = expected_max_sr(len(LONG_TOP)) / math.sqrt(s1_years)
    out["null_max_sr_ann"] = {f"N={len(LONG_TOP)}": null_sr}
    for h in LONG_TOP:
        row = {}
        for s, r in segs.items():
            mk = r["mkt"]
            row[s] = {c: bu.stats(v, mk) for c, v in r["daily"][h].items()}
            row[s]["mean_turnover_per_rebalance"] = r["turnover"][h]
            row[s]["cost_paid_pct_of_capital"] = r["cost_paid"][h] * 100
            x = r["daily"][h]["measured"]
            half = len(x) // 2
            row[s]["halves_sr_ann"] = [bu.stats(x[:half], mk[:half])["sr_ann"], bu.stats(x[half:], mk[half:])["sr_ann"]]
        row["flag"] = flag(row, null_sr)
        out["hypotheses"][h] = row
    s1 = pd.DataFrame({h: segs["S1"]["daily"][h]["measured"] for h in LONG_TOP})
    out["s1_correlation_measured"] = s1.corr().round(3).to_dict()
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print(f"null max SR (N={len(LONG_TOP)}) {null_sr:.2f}")
    for s in SEGMENTS:
        print(s, out["segments"][s]["flags"], "market SR", round(out["segments"][s]["market_ew"]["sr_ann"], 2))
    for h, row in out["hypotheses"].items():
        line = [h, row["flag"]]
        for s in SEGMENTS:
            for c in COST_ROWS:
                st = row[s][c]
                line.append(f"{s}-{c} SR {st['sr_ann']:.2f} ret {st['ann_return_pct']:.1f}% dd {st['max_dd_pct']:.1f}% b {st['beta']:.2f} at {st['alpha_t']:.2f}")
        line.append(f"S1 halves {[round(v, 2) for v in row['S1']['halves_sr_ann']]}")
        line.append(f"turnover {row['S1']['mean_turnover_per_rebalance']:.2f} cost {row['S1']['cost_paid_pct_of_capital']:.1f}%")
        print(" | ".join(str(x) for x in line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
