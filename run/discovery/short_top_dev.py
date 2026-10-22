"""Top short-only candidates on DEV (ADR 0026): quality, regimes, bundles, chance level.

Reads the DEV-only evolve state dirs given on the command line (same data, same
cost; strategy ids are content hashes so the pools are merged by id).  From all
DEV-evaluated ``direction == short`` strategies with a positive DEV mean it
takes the top N by capped DEV t (cap 6, the search's own ranking), re-runs their
DEV events (same ``_study`` call as ``dev_evaluate``) and reports per strategy:
trades, trades per year, holding time, net/gross bp per trade, cost share,
sized annual return and Sharpe (primary and stress cost), Sharpe by DEV half,
and performance on "down-trend" days.  Then equal-weight bundles of the top
5 / top 10 shorts, the same for the top longs (DEV-pass, same ranking), their
correlation and a 50/50 long+short mix, and the chance level of the best
Sharpe given the number of DEV trials (expected maximum of N unskilled
trials, deflated Sharpe).

Down-trend day (fixed before looking at any result): the 50/50 BTC/ETH
buy-and-hold market's cumulative return over the 60 UTC days BEFORE the day
is below zero (causal, no look-ahead).  Also reported: calendar 2022 and the
worst 10% market days.

Reads DEV bars only; no validation, locked, paper or IW1 data, no ledger
writes, never a gate.  Members are chosen on these same DEV days, so every
number is selection-biased.

    python3 short_top_dev.py <data_dir> --state STATE_DIR [--state STATE_DIR ...] --out report.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bundle_dev import corr  # noqa: E402
from bundle_stable import DAY_MS, beta, mix, perf, sized_daily  # noqa: E402
from engine_lib import deflated_sharpe, expected_max_sr, iter_jsonl  # noqa: E402

VERSION = "short-top-dev-v1"
T_CAP = 6.0
TREND_DAYS = 60
BEAR_YEAR = 2022
DAYS_PER_YEAR = 365.0


# -- pools ------------------------------------------------------------------------
def load_pool(state_dirs: Sequence[Path]) -> dict:
    """Merge DEV evaluations of several state dirs by strategy id."""
    pool: dict[str, dict] = {}
    trials = 0
    for sd in state_dirs:
        evo = Path(sd) / "evolution"
        ctx = [r["context_id"] for r in iter_jsonl(evo / "contexts.jsonl")][-1]
        specs = {r["strategy_id"]: r["definition"] for r in iter_jsonl(evo / "specs.jsonl")}
        status = {r["strategy_id"]: r["status"] for r in iter_jsonl(evo / "statuses.jsonl")
                  if r.get("context_id") == ctx and r.get("stage") == "dev_screen"}
        series = {r["strategy_id"]: r["series"] for r in iter_jsonl(evo / "dev_series.jsonl")
                  if r["context_id"] == ctx}
        n_ev = 0
        for r in iter_jsonl(evo / "evaluations.jsonl"):
            if r.get("stage") != "dev" or r.get("context_id") != ctx or r.get("status") != "OK":
                continue
            n_ev += 1
            sid = r["strategy_id"]
            if sid in pool or sid not in specs:
                continue
            pool[sid] = {"spec": specs[sid], "metrics": r["metrics"], "status": status.get(sid),
                         "series": series.get(sid), "source": str(sd), "context": ctx}
        trials += n_ev
    return {"pool": pool, "trials_by_run_sum": trials}


def pooled(series: Mapping) -> dict:
    acc: dict[int, float] = {}
    for d0, vals in series.values():
        for i, v in enumerate(vals):
            acc[d0 + i] = acc.get(d0 + i, 0.0) + v
    return acc


def sr_ann(x: Sequence[float]) -> Optional[float]:
    n = len(x)
    if n < 3:
        return None
    mu = sum(x) / n
    sd = math.sqrt(sum((v - mu) ** 2 for v in x) / (n - 1))
    return mu / sd * math.sqrt(DAYS_PER_YEAR) if sd > 0 else None


def capped_t(m: Mapping) -> float:
    return min(T_CAP, m.get("dev_t") or 0.0)


def rank(pool: Mapping, direction: str, *, passing_only: bool, n: int) -> list:
    rows = []
    for sid, p in pool.items():
        if p["spec"]["direction"] != direction:
            continue
        m = p["metrics"]
        if not (m.get("dev_mean_bp") or 0) > 0:
            continue
        if passing_only and p["status"] != "RESEARCH_ONLY":
            continue
        rows.append(sid)
    rows.sort(key=lambda s: (-capped_t(pool[s]["metrics"]), s))
    seen, out = set(), []
    for sid in rows:               # identical DEV day series = the same behaviour: keep the first
        key = json.dumps(pool[sid]["series"], sort_keys=True) if pool[sid]["series"] else sid
        if key in seen:
            continue
        seen.add(key)
        out.append(sid)
    return out[:n]


# -- per strategy -------------------------------------------------------------------
def moments(x: Sequence[float]) -> tuple:
    n = len(x)
    mu = sum(x) / n
    m2 = sum((v - mu) ** 2 for v in x) / n
    if m2 <= 0:
        return 0.0, 3.0
    m3 = sum((v - mu) ** 3 for v in x) / n
    m4 = sum((v - mu) ** 4 for v in x) / n
    return m3 / m2 ** 1.5, m4 / m2 ** 2


def regime_stats(x: Sequence[float], mask: Sequence[bool]) -> dict:
    """Mean daily % and annualised Sharpe on the days where mask is true."""
    sel = [v for v, k in zip(x, mask) if k]
    if len(sel) < 3:
        return {"days": len(sel)}
    mu = sum(sel) / len(sel)
    sd = math.sqrt(sum((v - mu) ** 2 for v in sel) / (len(sel) - 1))
    return {"days": len(sel), "ann_return_pct": mu * DAYS_PER_YEAR, "total_pct": sum(sel),
            "sharpe_ann": (mu / sd * math.sqrt(DAYS_PER_YEAR)) if sd > 0 else None}


def trade_profile(ev_by_sym: Mapping[str, Sequence[Mapping]], years: float) -> dict:
    ev = [e for evs in ev_by_sym.values() for e in evs]
    n = len(ev)
    if not n:
        return {"trades": 0}
    hold_h = sorted((e["exit_ts"] - e["entry_ts"]) / 3_600_000.0 for e in ev)
    net = [e["net_bp"] for e in ev]
    gross = [e["gross_bp"] for e in ev]
    return {"trades": n, "trades_per_year": n / years,
            "trades_by_symbol": {s: len(v) for s, v in sorted(ev_by_sym.items())},
            "hold_hours_median": hold_h[n // 2], "hold_hours_mean": sum(hold_h) / n,
            "net_bp_mean": sum(net) / n, "gross_bp_mean": sum(gross) / n,
            "win_share": sum(1 for v in net if v > 0) / n,
            "top5pct_share_of_profit": _top_share(net)}


def _top_share(net: Sequence[float]) -> Optional[float]:
    tot = sum(v for v in net if v > 0)
    if tot <= 0:
        return None
    k = max(1, int(math.ceil(0.05 * len(net))))
    return sum(sorted(net, reverse=True)[:k]) / tot


# -- regime switching (top-5 long bundle x top-5 short bundle) ------------------------
FIXED_LONG_SHARE = (1.0, 0.7, 0.5)            # the three fixed mixes asked for
GRID = (1.0, 0.7, 0.5, 0.3, 0.0)              # long share a state may choose from
SWITCH_RULES = ("trend60", "ma100", "trend60_3state")   # fixed before any result


def regime_labels(rule: str, mkt: Sequence[float]) -> list:
    """Causal state of each day from market 50/50 returns (%, days before it only)."""
    n = len(mkt)
    out = []
    px = [1.0]
    for v in mkt:
        px.append(px[-1] * (1.0 + v / 100.0))      # px[i] = level before day i
    for i in range(n):
        if rule == "ma100":
            if i < 100:
                out.append(None)
            else:
                out.append("up" if px[i] > sum(px[i - 100:i]) / 100.0 else "down")
        else:
            if i < TREND_DAYS:
                out.append(None)
                continue
            r = px[i] / px[i - TREND_DAYS] - 1.0
            if rule == "trend60":
                out.append("up" if r >= 0 else "down")
            else:
                out.append("up" if r > 0.10 else ("down" if r < -0.10 else "side"))
    return out


def regime_switch(short_y, long_y, mkt, days, halves) -> dict:
    configs = 0
    res = {"rules_fixed_before_results": list(SWITCH_RULES), "grid_long_share": list(GRID),
           "fixed_mix": {}, "switch": {}}

    def mixed(ls):
        return [ls * a + (1.0 - ls) * b for a, b in zip(long_y, short_y)]
    for ls in FIXED_LONG_SHARE:
        y = mixed(ls)
        configs += 1
        res["fixed_mix"][f"long{int(ls*100)}_short{int(round((1-ls)*100))}"] = {
            "all": perf(y, days), "H1": perf(y[halves["H1"]], days[halves["H1"]]),
            "H2": perf(y[halves["H2"]], days[halves["H2"]])}
    sr_trials = [res["fixed_mix"][k]["all"].get("sharpe_ann") for k in res["fixed_mix"]]
    for rule in SWITCH_RULES:
        lab = regime_labels(rule, mkt)
        states = sorted({v for v in lab if v})
        for fit, sc in (("H1", "H2"), ("H2", "H1")):
            fsl, ssl = halves[fit], halves[sc]
            choice = {}
            for st in states:
                best = None
                for ls in GRID:
                    y = mixed(ls)
                    xs = [v for v, l in zip(y[fsl], lab[fsl]) if l == st]
                    mu = sum(xs) / len(xs) if xs else 0.0
                    if best is None or mu > best[0]:
                        best = (mu, ls)
                choice[st] = best[1]
            y = [choice.get(l, 0.5) * a + (1 - choice.get(l, 0.5)) * b if l else 0.5 * a + 0.5 * b
                 for l, a, b in zip(lab, long_y, short_y)]
            configs += 1
            fixed_same = {k: perf(mixed(ls)[ssl], days[ssl]) for k, ls in
                          (("long100", 1.0), ("long70", 0.7), ("long50", 0.5), ("short100", 0.0))}
            res["switch"][f"{rule}_fit{fit}_score{sc}"] = {
                "chosen_long_share_per_state": choice,
                "days_per_state_fit": {st: sum(1 for l in lab[fsl] if l == st) for st in states},
                "scored": perf(y[ssl], days[ssl]),
                "fixed_mixes_same_half": {k: v.get("sharpe_ann") for k, v in fixed_same.items()},
                "ann_return_scored_vs_50_50": [perf(y[ssl], days[ssl]).get("ann_return_pct"),
                                               fixed_same["long50"].get("ann_return_pct")]}
            sr_trials.append(perf(y[ssl], days[ssl]).get("sharpe_ann"))
    res["configurations_counted"] = configs
    srs = [v for v in sr_trials if v is not None]
    # yardstick: best of `configs` unskilled trials, each Sharpe measured over one half (~2.3 y)
    yrs = len(days) / 2 / DAYS_PER_YEAR
    res["chance_max_sharpe_ann_unit_variance"] = {f"N={configs}": expected_max_sr(configs) / math.sqrt(yrs)}
    res["best_observed_sharpe_among_configs"] = max(srs) if srs else None
    res["corr_short_vs_long_all"] = corr(short_y, long_y)
    return res


# -- report -------------------------------------------------------------------------
def build(data_dir: Path, state_dirs: Sequence[Path], *, top_n: int = 10) -> dict:
    from costs_bridge import CostContext
    from pipeline import EvolutionPipeline, RunnerDataProvider
    from runner import cost_from_env
    from strategy_spec import canonicalize_spec
    loaded = load_pool(state_dirs)
    pool = loaded["pool"]
    short_top = rank(pool, "short", passing_only=False, n=top_n)
    short_pass = rank(pool, "short", passing_only=True, n=10 ** 6)   # deduplicated by day series
    long_top = rank(pool, "long", passing_only=True, n=top_n)
    short_pass_top = short_pass[:top_n]
    members = sorted(set(short_top) | set(short_pass_top) | set(long_top))
    provider = RunnerDataProvider(Path(data_dir))
    primary = cost_from_env()
    stress = CostContext.from_assumed_profile("assumed_public_v1")
    pipe = EvolutionPipeline(Path(state_dirs[0]), provider, primary)
    daily = {"primary": {}, "stress": {}, "gross": {}}
    dev_span = {}
    ev_cache = {}
    for sid in members:
        spec = canonicalize_spec(pool[sid]["spec"])
        data, fng, missing = pipe._inputs(spec)
        if missing:
            raise SystemExit(f"{sid}: DEV inputs missing {missing}")
        ev_p, ev_s = {}, {}
        for sym, e in data.items():
            d1 = provider.windows(sym, spec["exit"]["horizon"])["dev"][1]
            ev_p[sym] = pipe._study(spec, sym, e["bars"], e["funding"], fng, 1, d1)
            ev_s[sym] = pipe._study(spec, sym, e["bars"], e["funding"], fng, 1, d1, cost=stress)
            lo, hi = e["bars"][0].ts // DAY_MS, e["bars"][d1].ts // DAY_MS
            sp = dev_span.setdefault(sym, [lo, hi])
            sp[0], sp[1] = min(sp[0], lo), max(sp[1], hi)
        ev_cache[sid] = ev_p
        daily["primary"][sid] = sized_daily(ev_p)
        daily["stress"][sid] = sized_daily(ev_s)
        daily["gross"][sid] = sized_daily(ev_p, "gross_bp")
    d0 = min(v[0] for v in dev_span.values())
    d1 = max(v[1] for v in dev_span.values())
    days = list(range(d0, d1 + 1))
    n = len(days)
    years = n / DAYS_PER_YEAR
    ser = {k: {s: [m.get(d, 0.0) for d in days] for s, m in v.items()} for k, v in daily.items()}

    # market 50/50 (daily close-to-close, %) and the causal down-trend mask
    syms = sorted(dev_span)
    mret = {}
    for sym in syms:
        close = {}
        for b in provider.symbol(sym)["bars"]:
            d = b.ts // DAY_MS
            if dev_span[sym][0] <= d <= dev_span[sym][1]:
                close[d] = b.close
        mret[sym] = {d: close[d] / close[d - 1] - 1.0 for d in close if d - 1 in close}
    mkt = []
    for d in days:
        have = [mret[s][d] for s in syms if d in mret[s]]
        mkt.append(100.0 * sum(have) / len(have) if have else 0.0)
    down = []
    for i in range(n):
        lo = max(0, i - TREND_DAYS)
        w = mkt[lo:i]
        down.append(len(w) >= TREND_DAYS // 2 and sum(w) < 0.0)
    year_of = [dt.datetime.fromtimestamp(d * 86400, tz=dt.timezone.utc).year for d in days]
    y22 = [y == BEAR_YEAR for y in year_of]
    cut = sorted(mkt)[max(0, int(0.10 * n) - 1)]
    worst = [v <= cut for v in mkt]
    half = n // 2
    halves = {"H1": slice(0, half), "H2": slice(half, n)}

    def one(sid: str) -> dict:
        p = pool[sid]
        m = p["metrics"]
        x = ser["primary"][sid]
        out = {"id": sid, "direction": p["spec"]["direction"], "family": m.get("family"),
               "symbols": p["spec"]["symbols"], "horizon_bars": p["spec"]["exit"]["horizon"],
               "dev_status": p["status"], "dev_t": m.get("dev_t"), "dev_mean_bp": m.get("dev_mean_bp"),
               "dev_cost_share": m.get("dev_cost_share"),
               "dev_fold_pos_share": m.get("dev_fold_pos_share"),
               "profile": trade_profile(ev_cache[sid], years),
               "primary": perf(x, days), "stress": perf(ser["stress"][sid], days),
               "gross": perf(ser["gross"][sid], days),
               "sharpe_H1": perf(x[halves["H1"]], days[halves["H1"]]).get("sharpe_ann"),
               "sharpe_H2": perf(x[halves["H2"]], days[halves["H2"]]).get("sharpe_ann"),
               "down_trend": regime_stats(x, down), "up_trend": regime_stats(x, [not k for k in down]),
               "year_2022": regime_stats(x, y22), "worst10pct_market_days": regime_stats(x, worst),
               "beta_to_market": beta(x, mkt), "corr_to_market": corr(x, mkt)}
        return out

    def bundle(ids: Sequence[str]) -> dict:
        if not ids:
            return {"n_members": 0}
        w = {s: 1.0 / len(ids) for s in ids}
        y = mix(w, ser["primary"], n)
        ys = mix(w, ser["stress"], n)
        return {"n_members": len(ids), "members": list(ids),
                "all": perf(y, days), "stress_all": perf(ys, days),
                "H1": perf(y[halves["H1"]], days[halves["H1"]]),
                "H2": perf(y[halves["H2"]], days[halves["H2"]]),
                "down_trend": regime_stats(y, down), "up_trend": regime_stats(y, [not k for k in down]),
                "year_2022": regime_stats(y, y22), "worst10pct_market_days": regime_stats(y, worst),
                "beta_to_market": beta(y, mkt), "corr_to_market": corr(y, mkt), "_y": y}

    sb = {f"short_top{k}": bundle(short_top[:k]) for k in (3, 5, 10) if k <= len(short_top)}
    for k in (3, 5):
        if k <= len(short_pass_top):
            sb[f"short_pass_top{k}"] = bundle(short_pass_top[:k])
    sb["short_all_pass"] = bundle(short_pass_top)
    lb = {f"long_top{k}": bundle(long_top[:k]) for k in (5, 10) if k <= len(long_top)}
    pairs = {}
    for sk, s in sb.items():
        for lk, l in lb.items():
            if not s.get("_y") or not l.get("_y"):
                continue
            c = [0.5 * a + 0.5 * b for a, b in zip(s["_y"], l["_y"])]
            pairs[f"{sk}__{lk}"] = {
                "corr_daily": corr(s["_y"], l["_y"]),
                "corr_down_trend_days": corr([v for v, k in zip(s["_y"], down) if k],
                                             [v for v, k in zip(l["_y"], down) if k]),
                "mix50_all": perf(c, days), "mix50_down_trend": regime_stats(c, down),
                "mix50_stress_all": perf([0.5 * a + 0.5 * b for a, b in
                                          zip(mix({m: 1.0 / len(s["members"]) for m in s["members"]}, ser["stress"], n),
                                              mix({m: 1.0 / len(l["members"]) for m in l["members"]}, ser["stress"], n))], days),
                "long_alone_all": l["all"], "short_alone_all": s["all"]}
    switching = {}
    for sk in ("short_top5", "short_pass_top5"):
        if sk in sb and "long_top5" in lb:
            switching[sk] = regime_switch(sb[sk]["_y"], lb["long_top5"]["_y"], mkt, days, halves)
    for b in list(sb.values()) + list(lb.values()):
        b.pop("_y", None)

    # chance level: Sharpe (pooled bp series, unsized) of every DEV-evaluated trial
    sr_all = {}
    for sid, p in pool.items():
        if p["series"]:
            acc = pooled(p["series"])
            lo_, hi_ = min(acc), max(acc)
            x = [acc.get(d, 0.0) for d in range(lo_, hi_ + 1)]
            s = sr_ann(x)
            if s is not None:
                sr_all[sid] = (s / math.sqrt(DAYS_PER_YEAR), x)
    sr_d = [v[0] for v in sr_all.values()]
    var_sr = (sum((v - sum(sr_d) / len(sr_d)) ** 2 for v in sr_d) / (len(sr_d) - 1)) if len(sr_d) > 1 else 0.0
    n_pool = len(pool)
    trials_list = sorted({n_pool, loaded["trials_by_run_sum"]})
    chance = {"n_pool_unique": n_pool, "n_trials_run_sum": loaded["trials_by_run_sum"],
              "var_sr_daily_across_trials": var_sr,
              "expected_max_sr_ann_if_N_unskilled": {
                  f"N={N}": expected_max_sr(N) * math.sqrt(var_sr * DAYS_PER_YEAR) for N in trials_list},
              "note": "scaled by the actual spread of Sharpe across all trials in the pool; an upper-ish"
                      " yardstick for 'best of N with no skill', not a test"}
    top_rows = []
    for sid in short_top:
        if sid not in sr_all:
            continue
        s_d, x = sr_all[sid]
        sk, ku = moments(x)
        top_rows.append({"id": sid, "dev_sharpe_ann_pooled_bp": s_d * math.sqrt(DAYS_PER_YEAR),
                         "dsr": {f"N={N}": deflated_sharpe(s_d, len(x), sk, ku, N, var_sr) for N in trials_list}})
    chance["short_top_dsr"] = top_rows
    best_short_pooled = max((r["dev_sharpe_ann_pooled_bp"] for r in top_rows), default=None)
    chance["best_short_top_sharpe_pooled"] = best_short_pooled
    chance["best_long_top_sharpe_pooled"] = max(
        (sr_all[s][0] * math.sqrt(DAYS_PER_YEAR) for s in long_top if s in sr_all), default=None)

    def iso(d):
        return dt.datetime.fromtimestamp(d * 86400, tz=dt.timezone.utc).date().isoformat()
    shorts_all = [s for s, p in pool.items() if p["spec"]["direction"] == "short"]
    return {
        "version": VERSION, "data": "DEV only (BTC/ETH engine DEV windows)",
        "use": "diagnostic only (members selected on DEV; never a gate)",
        "contexts": sorted({p["context"] for p in pool.values()}),
        "cost": {"primary": primary.version, "stress": stress.version},
        "dev_days": [iso(days[0]), iso(days[-1])], "dev_years": years,
        "regime_definition": {"down_trend": f"market 50/50 cumulative return over the previous {TREND_DAYS} days < 0",
                              "down_trend_days": sum(down), "year_2022_days": sum(y22),
                              "worst10pct_cut_pct": cut},
        "market_5050": {"all": perf(mkt, days), "down_trend": regime_stats(mkt, down),
                        "up_trend": regime_stats(mkt, [not k for k in down]),
                        "year_2022": regime_stats(mkt, y22)},
        "pool": {"unique_strategies": n_pool, "shorts": len(shorts_all),
                 "shorts_mean_positive": sum(1 for s in shorts_all if (pool[s]["metrics"].get("dev_mean_bp") or 0) > 0),
                 "shorts_pass": len(short_pass),
                 "shorts_underpowered": sum(1 for s in shorts_all
                                            if min((pool[s]["metrics"].get("per_symbol_n") or {"x": 0}).values()) < 15),
                 "longs_pass": len(rank(pool, "long", passing_only=True, n=10 ** 6))},
        "short_top": [one(s) for s in short_top],
        "short_pass_top": [one(s) for s in short_pass_top],
        "long_top": [one(s) for s in long_top],
        "regime_switch_top5": switching,
        "short_bundles": sb, "long_bundles": lb, "short_long_pairs": pairs,
        "chance_level": chance,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--state", type=Path, action="append", required=True)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    rep = build(a.data_dir, a.state, top_n=a.top)
    text = json.dumps(rep, indent=1, sort_keys=True, default=float)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
