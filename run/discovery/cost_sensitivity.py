"""Cost sensitivity of a strategy on its DEV window (ADR 0029).

The cost model (``cost_model.CandleCostModel``) and its assumptions
(``config/cost_assumed_public_v1.json`` or a sealed estimate / verification)
stay separate from this module: here the SAME events are re-priced with each
cost component scaled up, and the order size is scaled (which raises impact
and can push orders past the participation cap, so they are refused, not
priced).  Every scenario re-runs the event study; nothing is approximated
from the base result.

Scenarios (multipliers of the base parameters; never below 1 except the one
row labelled ``optimistic_bound``, which is reported, never used as a gate):

    fee x1.5 / x2, half spread x2 / x3, impact x2, order size x5 / x25,
    all components x2, maker fee (optimistic bound), and (ADR 0034) the half
    spread scaled with entry volatility (``spread_vol_scaled``, analytic)

Each row: events, refused orders, mean gross / cost / funding / net bp,
HAC t of the net, and the net split by entry volatility (above / below the
median realised volatility of the base events).  ``monotone_issues`` checks
that a costlier scenario never shows a better net mean on the same events
(a pricing bug would show up here).

This is a DEV diagnostic.  It never reads a validation, locked or paper
window, never changes a gate and never turns an assumed cost into an
observed one: ``basis`` carries the cost status of the base context.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

SCHEMA = "cost_sensitivity_v1"
#: (name, parameter multipliers, order-size multiplier)
SCENARIOS = (
    ("base", {}, 1.0),
    ("fee_x1.5", {"fee_rate": 1.5}, 1.0),
    ("fee_x2", {"fee_rate": 2.0}, 1.0),
    ("spread_x2", {"half_spread": 2.0}, 1.0),
    ("spread_x3", {"half_spread": 3.0}, 1.0),
    ("impact_x2", {"impact_coefficient": 2.0}, 1.0),
    ("size_x5", {}, 5.0),
    ("size_x25", {}, 25.0),
    ("all_x2", {"fee_rate": 2.0, "half_spread": 2.0, "impact_coefficient": 2.0}, 1.0),
)
#: maker fee of the public Binance USD-M base tier — an OPTIMISTIC bound only
MAKER_FEE_RATE = 0.0002
#: half spread scaled with entry volatility (analytic, on the base events; ADR 0034)
VOL_SCENARIO = "spread_vol_scaled"
#: scenarios that only raise per-order cost on the same order size (monotonicity check)
COSTLIER = ("fee_x1.5", "fee_x2", "spread_x2", "spread_x3", "impact_x2", "all_x2", VOL_SCENARIO)


def scenario_cost(base, mult: Mapping[str, float], *, name: str):
    from costs_bridge import CostContext
    p = dict(base.params)
    for k, m in mult.items():
        p[k] = p[k] * m
    return CostContext.from_params(p, version=f"{base.version}#sens:{name}", verified=False,
                                   test_only=base.test_only)


def _events(spec: Mapping, sym: str, bars, funding, fng, lo: int, hi: int, cost, order_value: float):
    from evaluator import run_event_study
    from strategy_spec import build_spec_aux, build_spec_signal, stop_exit_rule
    aux = build_spec_aux(bars, spec, funding, fng)
    es = run_event_study(bars[:hi + 1], sym, build_spec_signal(spec, aux), spec["exit"]["horizon"], cost,
                         order_value, funding, warmup=max(1, lo), exit_rule=stop_exit_rule(spec))
    return [e.to_json() for e in es.events], es.participation_refusals


def _vol_at(bars, i: int, w: int = 96) -> float:
    r = [math.log(bars[j].close / bars[j - 1].close) for j in range(max(1, i - w + 1), i + 1)]
    return (sum(x * x for x in r) / max(1, len(r))) ** 0.5


def _row(ev: Sequence[Mapping], refused: int, vol_split: Optional[Mapping]) -> dict:
    from pipeline import _hac
    n = len(ev)

    def mean(k):
        return sum(x[k] for x in ev) / n if n else None
    t = _hac([x["net_bp"] for x in ev])[0] if n >= 3 else float("nan")
    row = {"n": n, "refused": refused, "gross_bp": mean("gross_bp"), "cost_bp": mean("cost_bp"),
           "funding_bp": mean("funding_bp"), "net_bp": mean("net_bp"),
           "net_t": t if math.isfinite(t) else None}
    if vol_split is not None:
        for part in ("high_vol", "low_vol"):
            xs = [x["net_bp"] for x in ev if vol_split.get(x["entry_ts"]) == part]
            row[f"net_bp_{part}"] = sum(xs) / len(xs) if xs else None
            row[f"n_{part}"] = len(xs)
    return row


def sensitivity(spec: Mapping, inputs: Mapping[str, Mapping], base_cost, order_value: float) -> dict:
    """``inputs``: {symbol: {bars, funding, fng, lo, hi}} — the DEV range per symbol."""
    from strategy_spec import canonicalize_spec
    spec = canonicalize_spec(spec)
    plan = list(SCENARIOS) + [("maker_fee_optimistic_bound", None, 1.0)]
    rows, vol_split, vol_ratio, base_events = {}, {}, {}, []
    for name, mult, size in plan:
        if mult is None:
            from costs_bridge import CostContext
            p = {**base_cost.params, "fee_rate": min(MAKER_FEE_RATE, base_cost.params["fee_rate"])}
            cost = CostContext.from_params(p, version=f"{base_cost.version}#sens:{name}",
                                           verified=False, test_only=base_cost.test_only)
        else:
            cost = base_cost if name == "base" else scenario_cost(base_cost, mult, name=name)
        evs, refused = [], 0
        for sym, d in sorted(inputs.items()):
            ev, r = _events(spec, sym, d["bars"], d.get("funding"), d.get("fng"), d["lo"], d["hi"],
                            cost, order_value * size)
            if name == "base":
                idx = {b.ts: i for i, b in enumerate(d["bars"])}
                vols = {x["entry_ts"]: _vol_at(d["bars"], idx[x["entry_ts"]] - 1) for x in ev}
                med = sorted(vols.values())[len(vols) // 2] if vols else 0.0
                vol_split.update({ts: ("high_vol" if v > med else "low_vol") for ts, v in vols.items()})
                vol_ratio.update({(sym, ts): (v / med if med > 0 else 1.0) for ts, v in vols.items()})
                base_events += [dict(x, symbol=sym) for x in ev]
            evs += ev
            refused += r
        rows[name] = {"params": dict(cost.params), "order_value": order_value * size,
                      **_row(evs, refused, vol_split)}
    rows[VOL_SCENARIO] = vol_scaled_spread_row(base_events, vol_ratio, base_cost.params["half_spread"],
                                               rows["base"]["refused"], vol_split)
    base = rows["base"]
    from pipeline import cost_breakeven
    return {"schema": SCHEMA, "strategy_family": spec["family"], "symbols": sorted(inputs),
            "basis": {"cost_version": base_cost.version, "cost_status": base_cost.status,
                      "claim": ("modelled cost under an assumption — not observed execution cost"
                                if base_cost.status != "VERIFIED" else "verified cost model")},
            "scenarios": rows, "monotone_issues": monotone_issues(rows),
            "first_scenario_net_not_positive": next((k for k, v in rows.items()
                                                     if k != "maker_fee_optimistic_bound"
                                                     and (v["net_bp"] is None or v["net_bp"] <= 0)), None),
            "base_net_bp": base["net_bp"], "stage": "dev", "gate": False,
            "note": "DEV diagnostic only; optimistic_bound row is never a pass criterion",
            "breakeven_cost_multiplier": None if base["n"] == 0 else cost_breakeven(
                [{"gross_bp": base["gross_bp"], "funding_bp": base["funding_bp"], "cost_bp": base["cost_bp"]}])}


def vol_scaled_spread_row(events: Sequence[Mapping], vol_ratio: Mapping, half_spread: float,
                          refused: int, vol_split: Mapping) -> dict:
    """The base events with the half spread of BOTH sides scaled by the entry
    volatility relative to the median of the base events (never below 1x):
    ``extra = 2 * half_spread * max(0, vol / median - 1)``.  A diagnostic of
    how a volatility-dependent spread would change the result; the cost model
    itself has a constant half spread (stated in ``cost_model.py``).  The
    entry volatility stands in for the exit bar too (an approximation)."""
    adj = []
    for e in events:
        k = max(0.0, vol_ratio.get((e["symbol"], e["entry_ts"]), 1.0) - 1.0)
        extra = 2.0 * half_spread * 1e4 * k
        adj.append(dict(e, cost_bp=e["cost_bp"] + extra, net_bp=e["net_bp"] - extra))
    row = {"params": {"half_spread": "x max(1, entry vol / median vol)"}, "order_value": None,
           **_row(adj, refused, vol_split)}
    row["approximation"] = "analytic re-pricing of the base events (not a re-run)"
    return row


def monotone_issues(rows: Mapping[str, Mapping]) -> list[str]:
    """A costlier scenario must not show a better mean net on the same events."""
    b = rows.get("base") or {}
    out = []
    for k in COSTLIER:
        r = rows.get(k)
        if not r or r["n"] != b.get("n") or b.get("net_bp") is None:
            if r and r["n"] != b.get("n"):
                out.append(f"{k}: event count {r['n']} != base {b.get('n')} (only order size may refuse orders)")
            continue
        if r["net_bp"] > b["net_bp"] + 1e-9 or r["cost_bp"] < b["cost_bp"] - 1e-9:
            out.append(f"{k}: costlier scenario shows a better result than base")
    for a, c in (("size_x5", "base"), ("size_x25", "size_x5")):
        if rows.get(a) and rows.get(c) and rows[a]["n"] and rows[c]["n"] and \
                rows[a]["cost_bp"] < rows[c]["cost_bp"] - 1e-9 and rows[a]["n"] == rows[c]["n"]:
            out.append(f"{a}: larger orders priced cheaper than {c}")
    return out


def dev_inputs(pipe, spec: Mapping) -> dict:
    """DEV range of every symbol of ``spec`` from an EvolutionPipeline provider."""
    out = {}
    fng = pipe.provider.fng() if "fng" in spec.get("data", []) else None
    for sym in spec["symbols"]:
        e = pipe.provider.symbol(sym)
        if e["missing"]:
            continue
        d1 = pipe.provider.windows(sym, spec["exit"]["horizon"])["dev"][1]
        out[sym] = {"bars": e["bars"], "funding": e["funding"], "fng": fng, "lo": 1, "hi": d1}
    return out


def to_markdown(reps: Mapping[str, Mapping]) -> str:
    lines = ["# Cost sensitivity (DEV only, not a verdict)", "",
             "Same events re-priced; `refused` = orders above the participation cap. "
             "Basis: modelled cost, not observed fills.", ""]
    for sid, r in reps.items():
        lines += [f"## `{sid}` ({r['basis']['cost_status']})", "",
                  "| scenario | n | refused | gross | cost | net | net t | net hi-vol | net lo-vol |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for k, v in r["scenarios"].items():
            f = (lambda x: "-" if x is None else f"{x:.2f}")
            lines.append(f"| {k} | {v['n']} | {v['refused']} | {f(v['gross_bp'])} | {f(v['cost_bp'])} | "
                         f"{f(v['net_bp'])} | {f(v['net_t'])} | {f(v.get('net_bp_high_vol'))} | "
                         f"{f(v.get('net_bp_low_vol'))} |")
        lines += ["", f"first scenario with net <= 0: `{r['first_scenario_net_not_positive']}`; "
                      f"monotonicity issues: {r['monotone_issues'] or 'none'}", ""]
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI (real data)
    from pipeline import EvolutionPipeline, RunnerDataProvider
    from runner import cost_from_env
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--ids", required=True, help="file with one strategy id per line")
    ap.add_argument("--out", required=True, help="output prefix (.json and .md are written)")
    a = ap.parse_args(argv)
    prov = RunnerDataProvider(Path(a.data_dir))
    usable = [s for s in ("BTCUSDT", "ETHUSDT") if not prov.symbol(s)["missing"]]
    pipe = EvolutionPipeline(Path(a.state_dir), prov, cost_from_env(), symbols=usable)
    reps = {}
    for sid in [x.strip() for x in Path(a.ids).read_text().splitlines() if x.strip()]:
        spec = pipe.specs.get(sid)
        reps[sid] = sensitivity(spec, dev_inputs(pipe, spec), pipe.cost, pipe.order_value)
    Path(a.out + ".json").write_text(json.dumps(reps, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    Path(a.out + ".md").write_text(to_markdown(reps), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
