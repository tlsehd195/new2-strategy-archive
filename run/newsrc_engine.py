#!/usr/bin/env python3
"""Shared cross-sectional DEV engine of the repeated new-source rounds (ADR 0097 onwards).

Same accounting as ``newsrc_r1_dev`` / ADR 0047: universe at cut-off bar i-2,
trade at the open of day i, weights drift with price between rebalances, real
funding, ADR 0041 calibrated cost on every weight change (rows cal, cal_x2,
cal_tail_x2, cost0).  A round module supplies a ``Spec``: trial keys, signal and
weight functions, evaluation starts.  The engine never reads outside the DEV
segments (``broad_universe_dev.assert_allowed`` via the loaders).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))
import broad_universe_dev as bu  # noqa: E402
import cross_section_v2_dev as cs  # noqa: E402
import wide_universe_dev as wu  # noqa: E402
from engine_lib import deflated_sharpe, expected_max_sr, pbo_cscv  # noqa: E402

SEGMENTS = bu.SEGMENTS
COST_ROWS = wu.COST_ROWS
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"


@dataclass
class Spec:
    keys: list                      # [(hypothesis, universe)]
    signals: Callable               # (ctx dict) -> {hypothesis: pd.Series or None}
    weights: Callable               # (hypothesis, signal Series, universe list, ctx) -> {sym: w}
    eval_start: dict                # {"S1": iso, "S2": iso}
    hold: int = 14
    extra: dict = field(default_factory=dict)


def day_iso(t: int) -> str:
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def prior_trials(context_id: str) -> int:
    rows = [json.loads(ln) for ln in TRIALS.read_text().splitlines() if ln.strip()]
    return sum(r.get("dev_evaluated", 0) for r in rows if r.get("context_id") != context_id)


def impact_bar(bars, qv, s, i, bar_t, flags):
    b = bars.get((s, bar_t))
    if b is None or b <= 0:
        flags["impact_bar_fallback"] += 1
        dq = qv.iloc[i - 1][s]
        if not (dq > 0):
            dq = float(np.nanmean(qv[s].iloc[max(0, i - 30): i].values))
        b = dq / 96.0 if dq > 0 else 1.0
    return b


def run_segment(data: Path, seg: str, cal: dict, spec: Spec) -> dict:
    piv = cs.load_daily(data, seg)
    days = piv["open"].index.values
    syms = list(piv["open"].columns)
    t0, t1 = int(days[0]), int(days[-1])
    fund = bu.load_funding(data, syms, t0, t1)
    fund["day"] = ((fund["t"] - 1) // bu.DAY_MS) * bu.DAY_MS
    fund_daily = fund.groupby(["day", "sym"])["rate"].sum().unstack().reindex(days).reindex(columns=syms)
    fnd = fund_daily.values
    bars = bu.load_bar2345(data, syms, t0, t1)
    opn, qv = piv["open"], piv["qv"]
    ret = (opn.shift(-1) / opn - 1.0).values
    cret = piv["close"].pct_change(fill_method=None)
    col = {s: k for k, s in enumerate(syms)}
    n_bars = piv["close"].notna().cumsum()
    keys = spec.keys
    daily = {k: {r: np.zeros(len(days)) for r in COST_ROWS} for k in keys}
    mkt = np.full(len(days), np.nan)
    cost_paid = {k: 0.0 for k in keys}
    turnover = {k: [] for k in keys}
    gross = {k: [] for k in keys}
    n_reb = {k: 0 for k in keys}
    flat_reb = {k: 0 for k in keys}
    names_sig = {k: [] for k in keys}
    flags = {"missing_return_days": 0, "impact_bar_fallback": 0}
    held = {k: {} for k in keys}
    reb = wu.rebalance_days(days, SEGMENTS[seg][2], spec.hold)
    ev0 = bu.ms(spec.eval_start[seg])
    for j, i in enumerate(reb):
        cut = i - 2
        if cut < 0:
            continue
        ranked = wu.eligible(piv, n_bars, i)
        end = reb[j + 1] if j + 1 < len(reb) else len(days) - 1
        bar_t = int(days[i - 1]) + 23 * 3_600_000 + 45 * 60_000
        v30 = qv.iloc[max(0, cut - 29): cut + 1].mean()
        u50 = wu.slice_universe(ranked, "U50")
        if u50:
            uc = [col[s] for s in u50]
            for d in range(i, end):
                r = ret[d, uc]
                mkt[d] = float(np.nanmean(r)) if np.isfinite(r).any() else 0.0
        if int(days[i]) < ev0:
            continue
        ctx = {"piv": piv, "cret": cret, "cut": cut, "i": i, "ranked": ranked, "days": days, "seg": seg,
               "data": data, "fund_daily": fund_daily, "syms": syms}
        sig = spec.signals(ctx)
        for k in keys:
            h, u = k
            universe = wu.slice_universe(ranked, u)
            n_reb[k] += 1
            s = sig.get(k)
            w = spec.weights(h, s, universe, ctx) if universe and s is not None else {}
            names_sig[k].append(int(s.reindex(universe).notna().sum()) if s is not None else 0)
            if not w:
                flat_reb[k] += 1
            gross[k].append(float(sum(abs(x) for x in w.values())))
            prev = held[k]
            to = 0.0
            for name in sorted(set(w) | set(prev)):
                dw = abs(w.get(name, 0.0) - prev.get(name, 0.0))
                if dw == 0:
                    continue
                b = impact_bar(bars, qv, name, i, bar_t, flags)
                q15 = float(v30[name]) / 96.0 if not math.isnan(v30[name]) else 0.0
                for row in COST_ROWS:
                    daily[k][row][i] -= dw * wu.side_cost(row, cal, b, q15)
                cost_paid[k] += dw * wu.side_cost("cal", cal, b, q15)
                to += dw
            turnover[k].append(to)
            cur = dict(w)
            for d in range(i, end):
                pnl = 0.0
                for name, ws in cur.items():
                    r = ret[d, col[name]]
                    if math.isnan(r):
                        flags["missing_return_days"] += 1
                        r = 0.0
                    f = fnd[d, col[name]]
                    pnl += ws * r - ws * (0.0 if math.isnan(f) else f)
                    cur[name] = ws * (1.0 + r)
                for row in COST_ROWS:
                    daily[k][row][d] += pnl
            held[k] = cur
    a = int(np.searchsorted(days, ev0))
    span = slice(a, len(days) - 1)
    return {"days": days, "span": span, "daily": daily, "mkt": mkt, "cost_paid": cost_paid, "turnover": turnover,
            "gross": gross, "n_reb": n_reb, "flat_reb": flat_reb, "names_sig": names_sig, "flags": flags}


def flag(row: dict, null_key: str) -> str:
    s1 = row["S1"]["cal"]
    ok = (s1["sr_ann"] > row[null_key] and s1["alpha_t"] >= 2 and min(row["S1"]["halves_sr_ann"]) > 0
          and row["S2"]["cal"]["sr_ann"] > 0.5 and row["S1"]["cal_x2"]["sr_ann"] > 0
          and row["S1"]["cal_tail_x2"]["sr_ann"] > 0)
    return "DEV_PROMISING" if ok else "NOT_PROMISING"


def moments(x: np.ndarray) -> tuple:
    x = np.asarray(x, float)
    sd = x.std(ddof=1)
    if not sd > 0:
        return 0.0, 0.0, 3.0
    z = (x - x.mean()) / sd
    return float(x.mean() / sd), float(np.mean(z ** 3)), float(np.mean(z ** 4))


def evaluate(data: Path, spec: Spec, context_id: str, header: dict) -> dict:
    cal = bu.load_cost_calibration(wu.CALIBRATION)
    cal = {**cal["calib"], "maxp": cal["maxp"]}
    n = len(spec.keys)
    null_key = f"null_max_sr_ann_N{n}"
    out = dict(header)
    out.update({"helper_sha256": {nm: bu.sha(HERE / nm) for nm in ("broad_universe_dev.py", "wide_universe_dev.py",
                                                                  "cross_section_v2_dev.py", "newsrc_engine.py")},
                "calibration_sha256": bu.sha(wu.CALIBRATION), "segments": {}, "trials": {}})
    segs = {s: run_segment(data, s, cal, spec) for s in SEGMENTS}
    prior = prior_trials(context_id)
    for s, r in segs.items():
        out["segments"][s] = {"first_day": day_iso(int(r["days"][0])), "last_day": day_iso(int(r["days"][-2])),
                              "eval_from": day_iso(int(r["days"][r["span"].start])), "flags": r["flags"]}
    for k in spec.keys:
        h, u = k
        row = {}
        for s, r in segs.items():
            sp = r["span"]
            mk = r["mkt"][sp]
            row[s] = {c: bu.stats(v[sp], mk) for c, v in r["daily"][k].items()}
            x = r["daily"][k]["cal"][sp]
            half = len(x) // 2
            row[s]["halves_sr_ann"] = [bu.stats(x[:half], mk[:half])["sr_ann"], bu.stats(x[half:], mk[half:])["sr_ann"]]
            row[s]["years"] = len(x) / 365.0
            row[s]["cost_paid_pct_of_capital"] = r["cost_paid"][k] * 100
            row[s]["rebalances"] = r["n_reb"][k]
            row[s]["flat_rebalances"] = r["flat_reb"][k]
            row[s]["mean_names_with_signal"] = float(np.mean(r["names_sig"][k])) if r["names_sig"][k] else 0.0
            row[s]["mean_gross"] = float(np.mean(r["gross"][k])) if r["gross"][k] else 0.0
            row[s]["mean_turnover_per_rebalance"] = float(np.mean(r["turnover"][k])) if r["turnover"][k] else 0.0
        y1 = row["S1"]["years"]
        row[null_key] = expected_max_sr(n) / math.sqrt(y1)
        row["null_max_sr_ann_cumulative"] = expected_max_sr(prior + n) / math.sqrt(y1)
        row["flag"] = flag(row, null_key)
        out["trials"][f"{u}:{h}"] = row
    sp1 = segs["S1"]["span"]
    srs = {k: moments(segs["S1"]["daily"][k]["cal"][sp1]) for k in spec.keys}
    var_sr = float(np.var([v[0] for v in srs.values()], ddof=1))
    for k, (sr, sk, ku) in srs.items():
        row = out["trials"][f"{k[1]}:{k[0]}"]
        row[f"dsr_N{n}"] = deflated_sharpe(sr, sp1.stop - sp1.start, sk, ku, n, var_sr)
        row["dsr_cumulative"] = deflated_sharpe(sr, sp1.stop - sp1.start, sk, ku, prior + n, var_sr)
    mat = np.column_stack([segs["S1"]["daily"][k]["cal"][sp1] for k in spec.keys])
    pbo, ncomb = pbo_cscv(mat.tolist(), n_partitions=16)
    z = wu.NormalDist().inv_cdf
    y1 = out["trials"][f"{spec.keys[0][1]}:{spec.keys[0][0]}"]["S1"]["years"]
    out["multiple_testing"] = {"n_trials_here": n, "prior_trials": prior, "cumulative": prior + n,
                               "cross_trial_var_sr_daily": var_sr, "pbo_cscv_16": pbo, "pbo_combinations": ncomb,
                               "min_detectable_annual_sr_80pct_bonf": (z(1 - 0.05 / n) + z(0.8)) / math.sqrt(y1)}
    out["s1_corr"] = {"keys": [f"{u}:{h}" for h, u in spec.keys], "corr": np.corrcoef(mat.T).round(3).tolist()}
    return out


def print_summary(out: dict) -> None:
    mt = out["multiple_testing"]
    print("prior", mt["prior_trials"], "PBO", round(mt["pbo_cscv_16"], 3), "MDE", round(mt["min_detectable_annual_sr_80pct_bonf"], 2))
    for key, row in out["trials"].items():
        nk = [k for k in row if k.startswith("null_max_sr_ann_N")][0]
        line = [key, row["flag"], f"null {row[nk]:.2f} cum {row['null_max_sr_ann_cumulative']:.2f}"]
        for s in SEGMENTS:
            for c in ("cal", "cal_x2", "cost0"):
                st = row[s][c]
                line.append(f"{s}-{c} SR {st['sr_ann']:.2f} ret {st['ann_return_pct']:.1f}% dd {st['max_dd_pct']:.0f}% at {st['alpha_t']:.2f}")
            line.append(f"{s} halves {[round(v, 2) for v in row[s]['halves_sr_ann']]} cost {row[s]['cost_paid_pct_of_capital']:.1f}%"
                        f" names {row[s]['mean_names_with_signal']:.0f} gross {row[s]['mean_gross']:.2f} to {row[s]['mean_turnover_per_rebalance']:.2f}")
        line.append(f"DSRcum {row['dsr_cumulative']:.3f}")
        print(" | ".join(line))
