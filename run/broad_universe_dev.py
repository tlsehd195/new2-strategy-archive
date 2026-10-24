#!/usr/bin/env python3
"""Broad-universe cross-sectional DEV check (ADR 0035).

Runs exactly the four hypotheses fixed in
``run/audit/evidence_broad_universe_20261023/prereg_broad_universe_v1.json``
(committed before any data was downloaded) on a point-in-time top-50 universe
of Binance USDT-M perpetuals, DEV segments only, and writes the evidence next
to the preregistration.

Never reads: 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards; BTCUSDT
in S2.  No orders, no private API, no NEW2.

Usage::

    python3 run/broad_universe_fetch.py --out <dir> --step daily
    python3 run/broad_universe_dev.py --data-dir <dir> --write-universe
    python3 run/broad_universe_fetch.py --out <dir> --step funding --symbols <dir>/ever_top50.json
    python3 run/broad_universe_fetch.py --out <dir> --step bar2345 --symbols <dir>/ever_top50.json
    python3 run/broad_universe_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
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
from engine_lib import expected_max_sr  # noqa: E402

VERSION = "broad-universe-dev-v1"
EVID = HERE / "audit" / "evidence_broad_universe_20261023"
PREREG = EVID / "prereg_broad_universe_v1.json"
COST_PROFILE = HERE / "discovery" / "config" / "cost_assumed_public_v1.json"
DAY_MS = 86_400_000
ORDER_VALUE = 10_000.0
TOP_N = 50
K_SIDE = 10
MIN_BARS = 60
VOL_DAYS = 30
#: DEV evaluations already spent on 2021-2025 data by earlier searches
#: (dev_search_trials.jsonl + R3-R8 evolve runs, ADR 0012-0028); reference only
PRIOR_TRIALS_REF = 10_000

SEGMENTS = {
    "S1": ("2021-01-01", "2023-09-18", "2021-03-01"),
    "S2": ("2024-06-19", "2025-10-20", "2024-08-19"),
}
NEVER = (("2000-01-01", "2020-12-31"), ("2023-09-19", "2024-06-18"), ("2025-10-21", "2099-12-31"))


def ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def assert_allowed(t0: int, t1: int) -> None:
    for lo, hi in NEVER:
        if t0 <= ms(hi) + DAY_MS - 1 and t1 >= ms(lo):
            raise SystemExit(f"refusing to read a never-read range {lo}..{hi}")


def read_rows(path: Path) -> list:
    out = []
    for line in path.read_text().splitlines():
        f = line.split(",")
        if f and f[0].isdigit():
            out.append(f)
    return out


def load_daily(data: Path, seg: str) -> dict:
    lo, hi, _ = SEGMENTS[seg]
    t0, t1 = ms(lo), ms(hi)
    assert_allowed(t0, t1)
    recs = []
    for p in sorted((data / "daily").glob("*.csv")):
        sym = p.name.split("-1d-")[0]
        if seg == "S2" and sym == "BTCUSDT":
            continue
        for f in read_rows(p):
            t = int(f[0])
            if t0 <= t <= t1:
                recs.append((sym, t, float(f[1]), float(f[4]), float(f[7])))
    df = pd.DataFrame(recs, columns=["sym", "t", "open", "close", "qv"]).drop_duplicates(["sym", "t"])
    assert df["t"].between(t0, t1).all()
    days = np.arange(t0, t1 + 1, DAY_MS)
    piv = {k: df.pivot(index="t", columns="sym", values=k).reindex(days) for k in ("open", "close", "qv")}
    return piv


def load_funding(data: Path, syms: list, t0: int, t1: int) -> pd.DataFrame:
    recs = []
    for s in syms:
        for p in sorted((data / "funding").glob(f"{s}-fundingRate-*.csv")):
            for f in read_rows(p):
                t = int(f[0])
                if t0 <= t <= t1 + DAY_MS:
                    recs.append((s, t, float(f[2])))
    return pd.DataFrame(recs, columns=["sym", "t", "rate"])


def load_bar2345(data: Path, syms: list, t0: int, t1: int) -> dict:
    out = {}
    for s in syms:
        for p in sorted((data / "bar2345").glob(f"{s}-2345-*.csv")):
            for f in read_rows(p):
                t = int(f[0])
                if t0 <= t <= t1:
                    out[(s, t)] = float(f[1])
    return out


def load_cost() -> dict:
    p = json.loads(COST_PROFILE.read_text())
    return {"fixed": p["fee_rate"] + p["base_half_spread"] + p["slippage_margin_per_side"],
            "imp": p["impact_coefficient"], "maxp": p["max_participation"]}


def side_cost(qv: float, cost: dict, mode: str) -> float:
    if mode == "no_impact":
        return 0.0010
    c = cost["fixed"] + cost["imp"] * math.sqrt(ORDER_VALUE / qv)
    return c


def signals(piv: dict, fund_daily: pd.DataFrame, cut_idx: int) -> dict:
    """Signals from bars with index <= cut_idx (bars closed by the cut-off)."""
    close = piv["close"]
    c = close.iloc[cut_idx]
    out = {
        "H1_MOM28": c / close.iloc[cut_idx - 28] - 1.0 if cut_idx >= 28 else c * np.nan,
        "H2_REV7": c / close.iloc[cut_idx - 7] - 1.0 if cut_idx >= 7 else c * np.nan,
    }
    rets = close.iloc[max(0, cut_idx - VOL_DAYS): cut_idx + 1].pct_change(fill_method=None).iloc[1:]
    vol = rets.std()
    vol[rets.notna().sum() < VOL_DAYS] = np.nan
    out["H4_LOWVOL30"] = vol
    f7 = fund_daily.iloc[max(0, cut_idx - 6): cut_idx + 1]
    carry = f7.sum() / 21.0
    carry[f7.notna().sum() < 7] = np.nan
    out["H3_CARRY7"] = carry.reindex(close.columns)
    return out


#: which end of the ranking is long; signal ranks descending
LONG_TOP = {"H1_MOM28": True, "H2_REV7": False, "H3_CARRY7": False, "H4_LOWVOL30": False}


def pick(sig: pd.Series, universe: list, long_top: bool) -> dict:
    s = sig.reindex(universe).dropna()
    if len(s) < 2 * K_SIDE:
        return {}
    order = sorted(s.index, key=lambda k: (-s[k], k))  # descending, ties by name
    top, bot = order[:K_SIDE], order[-K_SIDE:]
    longs, shorts = (top, bot) if long_top else (bot, top)
    w = {k: 1.0 / (2 * K_SIDE) for k in longs}
    w.update({k: -1.0 / (2 * K_SIDE) for k in shorts})
    return w


def run_segment(data: Path, seg: str, cost: dict) -> dict:
    lo, hi, first = SEGMENTS[seg]
    piv = load_daily(data, seg)
    days = piv["open"].index.values
    syms = list(piv["open"].columns)
    t0, t1 = int(days[0]), int(days[-1])
    fund = load_funding(data, syms, t0, t1)
    # funding settled in (d 00:00, d+1 00:00] belongs to day d
    fund["day"] = ((fund["t"] - 1) // DAY_MS) * DAY_MS
    fund_daily = fund.groupby(["day", "sym"])["rate"].sum().unstack().reindex(days)
    fund_daily = fund_daily.reindex(columns=syms)
    bars = load_bar2345(data, syms, t0, t1)
    opn, qv = piv["open"], piv["qv"]
    # open-to-open return of day d (held from d 00:00 to d+1 00:00)
    ret = opn.shift(-1) / opn - 1.0
    n_bars = piv["close"].notna().cumsum()
    first_ms = ms(first)
    reb = [i for i, t in enumerate(days) if t >= first_ms and dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).weekday() == 0]
    hyps = list(LONG_TOP)
    modes = {"assumed": 1.0, "cost2x": 2.0, "cost0x": 0.0, "no_impact": 1.0}
    daily = {h: {m: np.zeros(len(days)) for m in modes} for h in hyps}
    gross_daily = {h: np.zeros(len(days)) for h in hyps}
    mkt = np.full(len(days), np.nan)
    flags = {"missing_return_days": 0, "impact_bar_fallback": 0, "participation_over_max": 0,
             "no_volume_cost_fallback": 0, "rebalances": 0, "universe_sizes": []}
    turnover = {h: [] for h in hyps}
    cost_paid = {h: 0.0 for h in hyps}
    prev = {h: {} for h in hyps}
    active = [i for i in reb]
    for j, i in enumerate(active):
        cut = i - 2  # Saturday's bar closes at Sunday 00:00; one day skipped
        if cut < 0:
            continue
        nb = n_bars.iloc[cut]
        vol30 = qv.iloc[max(0, cut - 29): cut + 1].mean()
        elig = [s for s in syms if nb[s] >= MIN_BARS and not math.isnan(opn.iloc[i][s]) and not math.isnan(vol30[s])]
        universe = sorted(elig, key=lambda s: (-vol30[s], s))[:TOP_N]
        if len(universe) < 2 * K_SIDE:
            continue
        flags["rebalances"] += 1
        flags["universe_sizes"].append(len(universe))
        sig = signals(piv, fund_daily, cut)
        end = active[j + 1] if j + 1 < len(active) else len(days) - 1
        hold = range(i, end)
        # market: equal weight of the universe
        for d in hold:
            r = ret.iloc[d][universe]
            mkt[d] = float(r.mean()) if r.notna().any() else 0.0
        for h in hyps:
            w = pick(sig[h], universe, LONG_TOP[h])
            if not w:
                w = {}
            names = set(w) | set(prev[h])
            to = 0.0
            for m, mult in modes.items():
                cst = 0.0
                for s in names:
                    dw = abs(w.get(s, 0.0) - prev[h].get(s, 0.0))
                    if dw == 0:
                        continue
                    b = bars.get((s, int(days[i - 1]) + 23 * 3_600_000 + 45 * 60_000))
                    if b is None or b <= 0:
                        dq = qv.iloc[i - 1][s] if not math.isnan(qv.iloc[i - 1][s]) else np.nan
                        if m == "assumed":
                            flags["impact_bar_fallback"] += 1
                        if not (dq > 0):
                            dq = float(np.nanmean(qv[s].iloc[max(0, i - 30): i].values)) if s in qv else np.nan
                            if m == "assumed":
                                flags["no_volume_cost_fallback"] += 1
                        b = dq / 96.0 if dq > 0 else 1.0
                    if m == "assumed" and ORDER_VALUE / b > cost["maxp"]:
                        flags["participation_over_max"] += 1
                    c = side_cost(b, cost, m)
                    cst += dw * c * mult
                    if m == "assumed":
                        to += dw
                daily[h][m][i] -= cst
                if m == "assumed":
                    cost_paid[h] += cst
            turnover[h].append(to)
            for d in hold:
                pnl = 0.0
                for s, ws in w.items():
                    r = ret.iloc[d][s]
                    if math.isnan(r):
                        flags["missing_return_days"] += 1
                        r = 0.0
                    f = fund_daily.iloc[d][s] if s in fund_daily.columns else np.nan
                    pnl += ws * r - ws * (0.0 if math.isnan(f) else f)
                gross_daily[h][d] += pnl
                for m in modes:
                    daily[h][m][d] += pnl
            prev[h] = w
    lo_i = active[0] if active else 0
    span = slice(lo_i, len(days) - 1)
    res = {"days": [int(x) for x in days[span]], "mkt": mkt[span]}
    res["daily"] = {h: {m: daily[h][m][span] for m in modes} for h in hyps}
    res["gross"] = {h: gross_daily[h][span] for h in hyps}
    res["turnover"] = {h: float(np.mean(turnover[h])) if turnover[h] else 0.0 for h in hyps}
    res["cost_paid"] = cost_paid
    us = flags.pop("universe_sizes")
    flags["universe_size_min_mean"] = [min(us) if us else 0, float(np.mean(us)) if us else 0.0]
    res["flags"] = flags
    return res


def ever_top50(data: Path) -> list:
    """Symbols that enter the top-50 universe at any rebalance (for the funding/15m download)."""
    ever = set()
    for seg in SEGMENTS:
        piv = load_daily(data, seg)
        days, opn, qv = piv["open"].index.values, piv["open"], piv["qv"]
        nb = piv["close"].notna().cumsum()
        for i, t in enumerate(days):
            if t < ms(SEGMENTS[seg][2]) or dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).weekday() != 0:
                continue
            cut = i - 2
            v, n = qv.iloc[max(0, cut - 29): cut + 1].mean(), nb.iloc[cut]
            el = [s for s in opn.columns if n[s] >= MIN_BARS and not math.isnan(opn.iloc[i][s]) and not math.isnan(v[s])]
            ever |= set(sorted(el, key=lambda s: (-v[s], s))[:TOP_N])
    return sorted(ever)


def stats(x: np.ndarray, mkt: np.ndarray) -> dict:
    x = np.asarray(x, float)
    n = len(x)
    sd = x.std(ddof=1) if n > 1 else float("nan")
    m = x.mean()
    cum = np.cumsum(x)
    mdd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:] - cum)) if n else 0.0
    ok = ~np.isnan(mkt)
    beta = corr = a_mean = a_t = float("nan")
    if ok.sum() > 30 and np.std(mkt[ok]) > 0:
        beta = float(np.cov(x[ok], mkt[ok])[0, 1] / np.var(mkt[ok], ddof=1))
        corr = float(np.corrcoef(x[ok], mkt[ok])[0, 1])
        res = x[ok] - beta * mkt[ok]
        a_mean = float(res.mean())
        a_t = float(res.mean() / res.std(ddof=1) * math.sqrt(len(res)))
    return {"n_days": n, "sr_ann": float(m / sd * math.sqrt(365)) if sd > 0 else 0.0,
            "ann_return_pct": float(m * 365 * 100), "max_dd_pct": mdd * 100,
            "t": float(m / sd * math.sqrt(n)) if sd > 0 else 0.0,
            "beta": beta, "corr_mkt": corr, "alpha_bp_day": a_mean * 1e4, "alpha_t": a_t}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--write-universe", action="store_true",
                    help="only write <data-dir>/ever_top50.json and exit")
    a = ap.parse_args(argv)
    if a.write_universe:
        syms = ever_top50(a.data_dir)
        (a.data_dir / "ever_top50.json").write_text(json.dumps(syms))
        print(len(syms), "symbols")
        return 0
    cost = load_cost()
    out = {"version": VERSION, "prereg_sha256": sha(PREREG), "script_sha256": sha(Path(__file__)),
           "cost_profile_sha256": sha(COST_PROFILE), "segments": {}, "hypotheses": {}}
    segs = {s: run_segment(a.data_dir, s, cost) for s in SEGMENTS}
    for s, r in segs.items():
        out["segments"][s] = {"first_day": r["days"][0], "last_day": r["days"][-1], "n_days": len(r["days"]),
                              "flags": r["flags"], "market_ew": stats(np.nan_to_num(r["mkt"]), r["mkt"])}
    s1_years = len(segs["S1"]["days"]) / 365.0
    null4 = expected_max_sr(4) / math.sqrt(s1_years)
    nullref = expected_max_sr(PRIOR_TRIALS_REF + 4) / math.sqrt(s1_years)
    out["null_max_sr_ann"] = {"N=4": null4, f"N={PRIOR_TRIALS_REF + 4} (reference)": nullref}
    for h in LONG_TOP:
        row = {}
        for s, r in segs.items():
            mk = r["mkt"]
            row[s] = {m: stats(v, mk) for m, v in r["daily"][h].items()}
            row[s]["before_cost_and_funding_sign"] = stats(r["gross"][h], mk)
            row[s]["mean_turnover_per_rebalance"] = r["turnover"][h]
            row[s]["cost_paid_pct_of_capital"] = r["cost_paid"][h] * 100
            if s == "S1":
                x = r["daily"][h]["assumed"]
                half = len(x) // 2
                row[s]["halves_sr_ann"] = [stats(x[:half], mk[:half])["sr_ann"], stats(x[half:], mk[half:])["sr_ann"]]
            # reporting only: halves under every cost row
            row[s]["halves_sr_ann_by_cost"] = {
                m: [stats(v[: len(v) // 2], mk[: len(v) // 2])["sr_ann"], stats(v[len(v) // 2:], mk[len(v) // 2:])["sr_ann"]]
                for m, v in r["daily"][h].items()}
        s1, s2 = row["S1"]["assumed"], row["S2"]["assumed"]
        promising = (s1["sr_ann"] > null4 and s1["alpha_t"] >= 2 and min(row["S1"]["halves_sr_ann"]) > 0
                     and s2["sr_ann"] > 0.5)
        row["flag"] = "DEV_PROMISING" if promising else "NOT_PROMISING"
        out["hypotheses"][h] = row
    (EVID / "broad_universe_dev.json").write_text(json.dumps(out, indent=1, default=float))
    print(f"null max SR (N=4) {null4:.2f}; reference N={PRIOR_TRIALS_REF + 4}: {nullref:.2f}")
    for s in SEGMENTS:
        print(s, out["segments"][s]["flags"], "market SR", round(out["segments"][s]["market_ew"]["sr_ann"], 2))
    for h, row in out["hypotheses"].items():
        line = [h, row["flag"]]
        for s in SEGMENTS:
            for m in ("assumed", "no_impact", "cost0x"):
                st = row[s][m]
                line.append(f"{s}-{m} SR {st['sr_ann']:.2f} ret {st['ann_return_pct']:.1f}% dd {st['max_dd_pct']:.1f}% b {st['beta']:.2f} at {st['alpha_t']:.2f}")
        line.append(f"S1 halves {row['S1']['halves_sr_ann']}")
        line.append(f"turnover {row['S1']['mean_turnover_per_rebalance']:.2f}")
        print(" | ".join(str(x) for x in line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
