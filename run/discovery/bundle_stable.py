"""Stable small-profit bundle check on DEV (preregistered rules, ADR 0020).

Takes the DEV-pass strategies of ONE DEV-only evolve context, re-runs their
DEV events (the same ``_study`` call as ``dev_evaluate``) under the primary
cost and the stress cost, sizes them so no strategy or bundle ever holds more
than 1x capital, and scores the bundles fixed in
``config/bundle_stable_v1.json``.  Fitted bundles (cluster, inverse vol) are
fitted on one half of DEV and scored on the other.  Reads DEV bars only (the
provider's DEV window per symbol); no validation, locked, paper or IW1 data,
no ledger writes, never a gate.

    python3 bundle_stable.py <state_dir> <data_dir> --context C2-... --out report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bundle_dev import corr  # noqa: E402
from engine_lib import iter_jsonl  # noqa: E402

PREREG = Path(__file__).resolve().parent / "config" / "bundle_stable_v1.json"
DAY_MS = 86_400_000
DAYS_PER_YEAR = 365.0
CLUSTER_CUT = 0.5          # distance 1 - corr
STABLE = {"min_sharpe": 0.5, "max_mdd_pct": 15.0}


# -- returns --------------------------------------------------------------------
def max_concurrent(events: Sequence[Mapping]) -> int:
    """Most positions open at the same time (entry and exit timestamps)."""
    pts = sorted([(e["entry_ts"], 1) for e in events] + [(e["exit_ts"], -1) for e in events],
                 key=lambda x: (x[0], x[1]))
    cur = best = 0
    for _, d in pts:
        cur += d
        best = max(best, cur)
    return best


def sized_daily(events_by_sym: Mapping[str, Sequence[Mapping]], key: str = "net_bp") -> dict:
    """{utc_day: % of capital}: capital split equally over symbols, 1/K per
    event on a symbol (K = max concurrent positions there), exit-day booking."""
    out: dict[int, float] = {}
    n_sym = len(events_by_sym)
    for evs in events_by_sym.values():
        k = max(1, max_concurrent(evs))
        w = 1.0 / (n_sym * k)
        for e in evs:
            d = int(e["exit_ts"]) // DAY_MS
            out[d] = out.get(d, 0.0) + w * float(e[key]) / 100.0   # bp -> %
    return out


def gross_daily(events_by_sym):
    return sized_daily(events_by_sym, "gross_bp")


# -- statistics -------------------------------------------------------------------
def _mean_sd(x):
    n = len(x)
    mu = sum(x) / n
    sd = math.sqrt(sum((v - mu) ** 2 for v in x) / (n - 1)) if n > 1 else 0.0
    return mu, sd


def perf(x: Sequence[float], days: Sequence[int]) -> dict:
    """Simple-sum equity in % of capital (no compounding)."""
    n = len(x)
    if n < 2:
        return {"days": n}
    mu, sd = _mean_sd(x)
    peak = cum = mdd = 0.0
    for v in x:
        cum += v
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    months: dict[int, float] = {}
    for d, v in zip(days, x):
        import datetime as dt
        t = dt.datetime.fromtimestamp(d * 86400, tz=dt.timezone.utc)
        months[t.year * 100 + t.month] = months.get(t.year * 100 + t.month, 0.0) + v
    mv = list(months.values())
    return {"days": n, "ann_return_pct": mu * DAYS_PER_YEAR, "ann_vol_pct": sd * math.sqrt(DAYS_PER_YEAR),
            "sharpe_ann": (mu / sd * math.sqrt(DAYS_PER_YEAR)) if sd > 0 else None,
            "mdd_pct": mdd, "total_pct": cum, "months": len(mv),
            "pos_month_share": sum(1 for v in mv if v > 0) / len(mv) if mv else None,
            "worst_month_pct": min(mv) if mv else None}


def beta(y: Sequence[float], m: Sequence[float]) -> Optional[float]:
    mm, sm = _mean_sd(m)
    if sm <= 0:
        return None
    my = sum(y) / len(y)
    cov = sum((a - my) * (b - mm) for a, b in zip(y, m)) / (len(y) - 1)
    return cov / (sm * sm)


# -- bundle rules --------------------------------------------------------------------
def cluster_pick(series: Mapping[str, Sequence[float]], cut: float = CLUSTER_CUT) -> list:
    """Average-linkage clustering on 1 - corr, stop when the closest pair is
    farther than ``cut``; best-Sharpe member per cluster (ties: smaller id)."""
    ids = sorted(series)
    c = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            r = corr(series[a], series[b])
            c[(a, b)] = c[(b, a)] = 1.0 - (r if r is not None else 0.0)
    cl = [[s] for s in ids]

    def dist(x, y):
        return sum(c[(a, b)] for a in x for b in y) / (len(x) * len(y))
    while len(cl) > 1:
        best = None
        for i in range(len(cl)):
            for j in range(i + 1, len(cl)):
                d = dist(cl[i], cl[j])
                if best is None or d < best[0]:
                    best = (d, i, j)
        if best[0] > cut:
            break
        _, i, j = best
        cl[i] = sorted(cl[i] + cl[j])
        del cl[j]

    def sr(s):
        mu, sd = _mean_sd(series[s])
        return mu / sd if sd > 0 else -1e9
    return sorted(min(g, key=lambda s: (-sr(s), s)) for g in cl)


def invvol_weights(series: Mapping[str, Sequence[float]]) -> dict:
    inv = {}
    for s, x in series.items():
        sd = _mean_sd(x)[1]
        inv[s] = 1.0 / sd if sd > 0 else 0.0
    tot = sum(inv.values())
    return {s: (v / tot if tot > 0 else 0.0) for s, v in inv.items()}


def dir_thirds(direction: Mapping[str, str]) -> dict:
    groups = {d: [s for s in sorted(direction) if direction[s] == d] for d in ("long", "short", "symmetric")}
    live = [g for g in groups.values() if g]
    w = {}
    for g in live:
        for s in g:
            w[s] = 1.0 / len(live) / len(g)
    return w


def mix(weights: Mapping[str, float], series: Mapping[str, Sequence[float]], n: int) -> list:
    out = [0.0] * n
    for s, w in weights.items():
        for i, v in enumerate(series[s]):
            out[i] += w * v
    return out


def stable_label(p_primary: dict, p_stress: dict, hedged: dict) -> str:
    ok = (p_primary.get("sharpe_ann") is not None and p_primary["sharpe_ann"] >= STABLE["min_sharpe"]
          and p_primary["ann_return_pct"] > 0 and p_primary["mdd_pct"] <= STABLE["max_mdd_pct"]
          and p_stress["ann_return_pct"] > 0 and hedged["ann_return_pct"] > 0)
    return "DEV_STABLE" if ok else "NOT_STABLE"


# -- report --------------------------------------------------------------------------
def score(name: str, w: Mapping[str, float], ser: Mapping[str, Mapping[str, list]], mkt: list,
          days: list, halves: Mapping[str, slice], scored: Sequence[str], fit_beta_on: Mapping[str, str]) -> dict:
    n = len(days)
    res = {"weights": {s: round(v, 6) for s, v in sorted(w.items()) if v}, "n_members": sum(1 for v in w.values() if v),
           "halves": {}}
    for h in scored:
        sl = halves[h]
        out = {}
        for ck in ("primary", "stress", "gross"):
            out[ck] = perf(mix(w, ser[ck], n)[sl], days[sl])
        y = mix(w, ser["primary"], n)
        fb = halves[fit_beta_on[h]]
        b = beta(y[fb], mkt[fb])
        out["beta_to_market"] = b
        out["corr_to_market"] = corr(y[sl], mkt[sl])
        out["hedged_primary"] = perf([a - (b or 0.0) * m for a, m in zip(y[sl], mkt[sl])], days[sl])
        out["label"] = stable_label(out["primary"], out["stress"], out["hedged_primary"])
        res["halves"][h] = out
    return res


def build(state_dir: Path, data_dir: Path, context: str, *, stress_profile: str = "assumed_public_v1",
          primary_cost=None, dump: Optional[Path] = None) -> dict:
    from costs_bridge import CostContext
    from pipeline import EvolutionPipeline, RunnerDataProvider
    from runner import cost_from_env
    evo = Path(state_dir) / "evolution"
    ctxs = [r["context_id"] for r in iter_jsonl(evo / "contexts.jsonl")]
    if context not in ctxs:
        raise SystemExit(f"context {context} not in {evo}/contexts.jsonl ({ctxs})")
    status = {r["strategy_id"]: r["status"] for r in iter_jsonl(evo / "statuses.jsonl")
              if r.get("context_id") == context and r.get("stage") == "dev_screen"}
    members = sorted(s for s, st in status.items() if st == "RESEARCH_ONLY")
    specs = {}
    for r in iter_jsonl(evo / "specs.jsonl"):
        specs[r["strategy_id"]] = r["definition"]
    provider = RunnerDataProvider(Path(data_dir))
    primary = primary_cost or cost_from_env()
    stress = CostContext.from_assumed_profile(stress_profile)
    pipe = EvolutionPipeline(Path(state_dir), provider, primary)
    from strategy_spec import canonicalize_spec
    daily = {"primary": {}, "stress": {}, "gross": {}}
    direction, dev_span = {}, {}
    for sid in members:
        spec = canonicalize_spec(specs[sid])
        direction[sid] = spec["direction"]
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
        daily["primary"][sid] = sized_daily(ev_p)
        daily["stress"][sid] = sized_daily(ev_s)
        daily["gross"][sid] = gross_daily(ev_p)
    d0 = min(v[0] for v in dev_span.values())
    d1 = max(v[1] for v in dev_span.values())
    days = list(range(d0, d1 + 1))
    ser = {k: {s: [m.get(d, 0.0) for d in days] for s, m in v.items()} for k, v in daily.items()}
    # market: 50/50 buy-and-hold of the DEV symbols, close-to-close of the last bar per UTC day
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
    if dump is not None:
        Path(dump).write_text(json.dumps({"days": days, "mkt": mkt, "ser": ser, "direction": direction}))
    half = len(days) // 2
    halves = {"H1": slice(0, half), "H2": slice(half, len(days)), "ALL": slice(0, len(days))}
    fixed_scored = ("H1", "H2", "ALL")
    bundles = {
        "B1_EW_ALL": score("B1", {s: 1.0 / len(members) for s in members}, ser, mkt, days, halves,
                           fixed_scored, {h: h for h in fixed_scored}),
        "B2_DIR_THIRDS": score("B2", dir_thirds(direction), ser, mkt, days, halves,
                               fixed_scored, {h: h for h in fixed_scored}),
    }
    for fit, sc in (("H1", "H2"), ("H2", "H1")):
        fit_ser = {s: ser["primary"][s][halves[fit]] for s in members}
        pick = cluster_pick(fit_ser)
        bundles[f"B3_CLUSTER_fit{fit}"] = score("B3", {s: 1.0 / len(pick) for s in pick}, ser, mkt, days,
                                                halves, (sc,), {sc: fit})
        bundles[f"B4_INVVOL_fit{fit}"] = score("B4", invvol_weights(fit_ser), ser, mkt, days, halves,
                                               (sc,), {sc: fit})
    import datetime as dt

    def iso(d):
        return dt.datetime.fromtimestamp(d * 86400, tz=dt.timezone.utc).date().isoformat()
    single = {}
    for s in members:
        for h in ("H1", "H2"):
            single.setdefault(h, []).append(perf(ser["primary"][s][halves[h]], days[halves[h]]).get("sharpe_ann") or 0.0)
    # diagnostic, not a bundle: market exposure of each fixed direction group (equal weight)
    group_beta = {}
    for g in ("long", "short", "symmetric"):
        ids = [s for s in members if direction[s] == g]
        if ids:
            y = mix({s: 1.0 / len(ids) for s in ids}, ser["primary"], len(days))
            group_beta[g] = {h: {"beta": beta(y[halves[h]], mkt[halves[h]]),
                                 "corr": corr(y[halves[h]], mkt[halves[h]]),
                                 "ann_return_pct": perf(y[halves[h]], days[halves[h]])["ann_return_pct"]}
                             for h in ("H1", "H2", "ALL")}
    prereg_sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    return {
        "version": "bundle-stable-v1", "prereg": "run/discovery/config/bundle_stable_v1.json",
        "prereg_sha256": prereg_sha, "context_id": context, "data": "dev only",
        "use": "diagnostic only (DEV, members selected on DEV; never a gate)",
        "n_members": len(members), "directions": {d: sum(1 for v in direction.values() if v == d)
                                                  for d in ("long", "short", "symmetric")},
        "dev_span": {s: [iso(a), iso(b)] for s, (a, b) in dev_span.items()},
        "halves": {"H1": [iso(days[0]), iso(days[half - 1])], "H2": [iso(days[half]), iso(days[-1])]},
        "cost": {"primary": primary.version, "stress": stress.version},
        "configurations_counted": 6,
        "market_5050": {h: perf(mkt[halves[h]], days[halves[h]]) for h in ("H1", "H2", "ALL")},
        "member_sharpe_median": {h: sorted(v)[len(v) // 2] for h, v in single.items()},
        "member_sharpe_rank_corr_H1_H2": _rank_corr(single["H1"], single["H2"]),
        "group_market_exposure": group_beta,
        "bundles": bundles,
    }


def _rank_corr(a, b):
    def rk(x):
        o = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        for k, i in enumerate(o):
            r[i] = float(k)
        return r
    return corr(rk(a), rk(b))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("state_dir", type=Path)
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--context", required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--dump", type=Path, help="also write the daily series (JSON) here")
    a = ap.parse_args(argv)
    rep = build(a.state_dir, a.data_dir, a.context, dump=a.dump)
    text = json.dumps(rep, indent=1, sort_keys=True, default=float)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
