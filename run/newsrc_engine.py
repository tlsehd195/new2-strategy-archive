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
import research_data_integrity as rdi  # noqa: E402
from engine_lib import deflated_sharpe, expected_max_sr, pbo_cscv  # noqa: E402

#: v2 (ADR 0116): unknown days are NaN + ``invalid`` (v1 used 0.0 return / 0 funding)
ENGINE_VERSION = "newsrc-engine-v2"

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


def impact_bar(bars, qv, s, i, bar_t, flags, vol=None):
    """Impact volume of a trade at the open of day i (ADR 0116: observed vs
    estimated is recorded; no volume at all is None, never 1.0)."""
    b, kind = rdi.impact_volume(bars, qv, s, i, bar_t)
    if kind != "OBSERVED_BAR2345":
        flags["impact_bar_fallback"] += 1
    if vol is not None:
        vol.add(kind)
    return b


def run_segment(data: Path, seg: str, cal: dict, spec: Spec) -> dict:
    """Daily series of every (hypothesis, universe) key.

    ADR 0116 (newsrc-engine-v2): a day whose PnL cannot be computed from
    observed data is NaN in ``daily`` (every cost row) and True in ``invalid``
    — a missing return of a held name (the day and, because the weight then
    drifts with an unknown return, the rest of the holding period and the next
    rebalance day), funding of a held name that is missing / gapped /
    schedule-less under the declared schedule, or a trade with no volume for
    its cost.  Nothing is replaced by 0.  Every event is in ``issues`` with
    symbol, day and kind; ``volume`` separates observed from estimated impact
    volume.  On complete data the series are identical to v1."""
    piv = cs.load_daily(data, seg)
    days = piv["open"].index.values
    syms = list(piv["open"].columns)
    t0, t1 = int(days[0]), int(days[-1])
    fund = bu.load_funding(data, syms, t0, t1)
    fund["day"] = rdi.funding_day(fund["t"].values)
    fund_daily = fund.groupby(["day", "sym"])["rate"].sum().unstack().reindex(days).reindex(columns=syms)
    fcal = rdi.FundingCalendar(fund, syms)
    bars = bu.load_bar2345(data, syms, t0, t1)
    opn, qv = piv["open"], piv["qv"]
    ret = (opn.shift(-1) / opn - 1.0).values
    cret = piv["close"].pct_change(fill_method=None)
    col = {s: k for k, s in enumerate(syms)}
    n_bars = piv["close"].notna().cumsum()
    keys = spec.keys
    daily = {k: {r: np.zeros(len(days)) for r in COST_ROWS} for k in keys}
    state = {k: rdi.SeriesState(len(days), f"{k[1]}:{k[0]}") for k in keys}
    mkt = np.full(len(days), np.nan)
    cost_paid = {k: 0.0 for k in keys}
    turnover = {k: [] for k in keys}
    gross = {k: [] for k in keys}
    n_reb = {k: 0 for k in keys}
    flat_reb = {k: 0 for k in keys}
    names_sig = {k: [] for k in keys}
    flags = {"missing_return_days": 0, "impact_bar_fallback": 0, "funding_invalid_name_days": 0,
             "impact_volume_missing": 0, "invalid_days": {}}
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
                # market proxy for beta only: no observed return -> NaN (stats skip it), not 0
                mkt[d] = float(np.nanmean(r)) if np.isfinite(r).any() else float("nan")
        if int(days[i]) < ev0:
            continue
        ctx = {"piv": piv, "cret": cret, "cut": cut, "i": i, "ranked": ranked, "days": days, "seg": seg,
               "data": data, "fund_daily": fund_daily, "syms": syms}
        sig = spec.signals(ctx)
        for k in keys:
            h, u = k
            ss = state[k]
            universe = wu.slice_universe(ranked, u)
            n_reb[k] += 1
            s = sig.get(k)
            w = spec.weights(h, s, universe, ctx) if universe and s is not None else {}
            names_sig[k].append(int(s.reindex(universe).notna().sum()) if s is not None else 0)
            if not w:
                flat_reb[k] += 1
            gross[k].append(float(sum(abs(x) for x in w.values())))
            prev = held[k]
            # weights carried in after a missing return are unknown: so are this day's turnover and cost
            ss.rebalance(i, prev, days)
            to = 0.0
            for name in sorted(set(w) | set(prev)):
                dw = abs(w.get(name, 0.0) - prev.get(name, 0.0))
                if dw == 0:
                    continue
                b, kind = rdi.impact_volume(bars, qv, name, i, bar_t)
                if kind != "OBSERVED_BAR2345":
                    flags["impact_bar_fallback"] += 1
                v30n = float(v30[name]) if name in v30.index else float("nan")
                if not ss.cost_volume(i, name, days, b, kind, v30n):
                    # no volume for the cost (old: b = 1.0 / q15 = 0.0): the cost is unknown
                    flags["impact_volume_missing"] += 1
                    to += dw
                    continue
                q15 = v30n / 96.0
                for row in COST_ROWS:
                    daily[k][row][i] -= dw * wu.side_cost(row, cal, b, q15)
                cost_paid[k] += dw * wu.side_cost("cal", cal, b, q15)
                to += dw
            turnover[k].append(to)
            cur = dict(w)

            def add(d, pnl, k=k):
                for row in COST_ROWS:
                    daily[k][row][d] += pnl
            ss.hold(cur, i, end, ret, col, days, fcal, add, drift=True, flags=flags)
            held[k] = cur
    for k in keys:
        state[k].finish(daily[k].values())
    invalid = {k: state[k].invalid for k in keys}
    a = int(np.searchsorted(days, ev0))
    span = slice(a, len(days) - 1)
    for k in keys:
        flags["invalid_days"][f"{k[1]}:{k[0]}"] = int(invalid[k][span].sum())
    return {"days": days, "span": span, "daily": daily, "invalid": invalid, "mkt": mkt, "cost_paid": cost_paid,
            "turnover": turnover, "gross": gross, "n_reb": n_reb, "flat_reb": flat_reb, "names_sig": names_sig,
            "flags": flags, "issues": {f"{k[1]}:{k[0]}": state[k].log.to_json() for k in keys},
            "volume": {f"{k[1]}:{k[0]}": state[k].volume.to_json() for k in keys},
            "panel_issues": rdi.panel_issues(data, seg), "data_policy": rdi.POLICY,
            "cost_evidence": rdi.COST_EVIDENCE, "engine_version": ENGINE_VERSION}


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
                              "eval_from": day_iso(int(r["days"][r["span"].start])), "flags": r["flags"],
                              "issues": r["issues"], "volume_provenance": r["volume"],
                              "panel_issues": r["panel_issues"]}
    out["data_policy"] = rdi.POLICY
    out["cost_evidence"] = rdi.COST_EVIDENCE
    out["engine_version"] = ENGINE_VERSION
    valid = {s: {k: ~r["invalid"][k][r["span"]] for k in spec.keys} for s, r in segs.items()}
    for k in spec.keys:
        h, u = k
        key = f"{u}:{h}"
        row = {}
        dstat = {}
        for s, r in segs.items():
            sp = r["span"]
            vm = valid[s][k]
            mk = r["mkt"][sp][vm]
            # ADR 0116: statistics on observed days only; the exclusion is reported
            row[s] = {c: bu.stats(v[sp][vm], mk) for c, v in r["daily"][k].items()}
            x = r["daily"][k]["cal"][sp][vm]
            half = len(x) // 2
            row[s]["halves_sr_ann"] = [bu.stats(x[:half], mk[:half])["sr_ann"], bu.stats(x[half:], mk[half:])["sr_ann"]]
            row[s]["years"] = len(x) / 365.0
            dstat[s] = rdi.series_status(int(vm.size), int((~vm).sum()), r["volume"][key])
            row[s]["data_status"] = dstat[s]
            row[s]["cost_paid_pct_of_capital"] = r["cost_paid"][k] * 100
            row[s]["rebalances"] = r["n_reb"][k]
            row[s]["flat_rebalances"] = r["flat_reb"][k]
            row[s]["mean_names_with_signal"] = float(np.mean(r["names_sig"][k])) if r["names_sig"][k] else 0.0
            row[s]["mean_gross"] = float(np.mean(r["gross"][k])) if r["gross"][k] else 0.0
            row[s]["mean_turnover_per_rebalance"] = float(np.mean(r["turnover"][k])) if r["turnover"][k] else 0.0
        y1 = max(row["S1"]["years"], 1 / 365.0)
        row[null_key] = expected_max_sr(n) / math.sqrt(y1)
        row["null_max_sr_ann_cumulative"] = expected_max_sr(prior + n) / math.sqrt(y1)
        worst = [d["status"] for d in dstat.values()]
        if "INVALID_DATA" in worst or "INSUFFICIENT_DATA" in worst:
            row["flag"] = "INVALID_DATA"        # never DEV_PROMISING on unobserved days
        elif "UNDETERMINED" in worst:
            row["flag"] = "UNDETERMINED"
        else:
            row["flag"] = flag(row, null_key)
        out["trials"][key] = row
    sp1 = segs["S1"]["span"]
    common = np.logical_and.reduce([valid["S1"][k] for k in spec.keys])
    srs = {k: moments(segs["S1"]["daily"][k]["cal"][sp1][valid["S1"][k]]) for k in spec.keys}
    var_sr = float(np.var([v[0] for v in srs.values()], ddof=1))
    for k, (sr, sk, ku) in srs.items():
        row = out["trials"][f"{k[1]}:{k[0]}"]
        nv = int(valid["S1"][k].sum())
        row[f"dsr_N{n}"] = deflated_sharpe(sr, nv, sk, ku, n, var_sr)
        row["dsr_cumulative"] = deflated_sharpe(sr, nv, sk, ku, prior + n, var_sr)
    # PBO needs one calendar for all trials: days invalid in any trial are dropped (counted)
    mat = np.column_stack([segs["S1"]["daily"][k]["cal"][sp1][common] for k in spec.keys])
    pbo, ncomb = pbo_cscv(mat.tolist(), n_partitions=16)
    z = wu.NormalDist().inv_cdf
    y1 = max(out["trials"][f"{spec.keys[0][1]}:{spec.keys[0][0]}"]["S1"]["years"], 1 / 365.0)
    out["multiple_testing"] = {"n_trials_here": n, "prior_trials": prior, "cumulative": prior + n,
                               "cross_trial_var_sr_daily": var_sr, "pbo_cscv_16": pbo, "pbo_combinations": ncomb,
                               "pbo_days_used": int(common.sum()), "pbo_days_dropped_invalid": int((~common).sum()),
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
