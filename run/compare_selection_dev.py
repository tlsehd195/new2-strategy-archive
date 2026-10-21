#!/usr/bin/env python3
"""Compare DEV-only evolve runs that differ only in parent selection (ADR 0019).

Reads the evolution ledgers of each state dir (specs, lineage, evaluations,
statuses, dev_series, rounds) and reports, per run: trials (every DEV
evaluation and every proposal, invalid and duplicate ones included), DEV
screen passes split into seeds and evolution children, direction mix, map
cells covered, best capped DEV t and the annualised DEV Sharpe of the pooled
daily series.  DEV data only; nothing is written to the ledgers.

    python3 run/compare_selection_dev.py NAME=<state_dir>/evolution ... [--out f.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "discovery"))
from engine_lib import iter_jsonl  # noqa: E402
from evolution import behaviour_key  # noqa: E402
from search import map_cell  # noqa: E402


def sr_ann(series: dict) -> float | None:
    acc: dict = {}
    for d0, vals in series.values():
        for i, v in enumerate(vals):
            acc[d0 + i] = acc.get(d0 + i, 0.0) + v
    if not acc:
        return None
    x = [acc.get(d, 0.0) for d in range(min(acc), max(acc) + 1)]
    n = len(x)
    mu = sum(x) / n
    sd = math.sqrt(sum((v - mu) ** 2 for v in x) / (n - 1)) if n > 1 else 0.0
    return mu / sd * math.sqrt(365.0) if sd > 0 else None


def summarize(evo: Path) -> dict:
    ctx = [r["context_id"] for r in iter_jsonl(evo / "contexts.jsonl")][-1]
    specs = {r["strategy_id"]: r["definition"] for r in iter_jsonl(evo / "specs.jsonl")}
    child = {r["child_id"] for r in iter_jsonl(evo / "lineage.jsonl")
             if r["operator"] not in ("legacy_translation", "seed")}
    evals = [r for r in iter_jsonl(evo / "evaluations.jsonl")
             if r.get("stage") == "dev" and r.get("context_id") == ctx]
    metrics = {r["strategy_id"]: r.get("metrics") or {} for r in evals if r.get("status") == "OK"}
    status = {}
    for r in iter_jsonl(evo / "statuses.jsonl"):
        if r.get("context_id") == ctx and r.get("stage") == "dev_screen":
            status[r["strategy_id"]] = r["status"]
    sr = {r["strategy_id"]: sr_ann(r["series"]) for r in iter_jsonl(evo / "dev_series.jsonl")
          if r["context_id"] == ctx}
    rounds = list(iter_jsonl(evo / "rounds.jsonl")) if (evo / "rounds.jsonl").exists() else []
    passers = sorted(s for s, st in status.items() if st == "RESEARCH_ONLY")

    def block(ids):
        ids = list(ids)
        t = sorted((min(6.0, metrics[s].get("dev_t") or 0.0) for s in ids if s in metrics), reverse=True)
        srs = sorted((sr[s] for s in ids if sr.get(s) is not None), reverse=True)
        return {"n": len(ids),
                "direction": dict(Counter(specs[s]["direction"] for s in ids)),
                "map_cells": len({map_cell(specs[s]) for s in ids}),
                "distinct_behaviour": len({behaviour_key(specs[s]) for s in ids}),
                "best_capped_dev_t": t[0] if t else None,
                "top5_capped_dev_t": t[:5],
                "best_dev_sr_ann": srs[0] if srs else None,
                "top5_dev_sr_ann": [round(v, 3) for v in srs[:5]]}
    kids = [s for s in specs if s in child and s in status]
    return {
        "context_id": ctx,
        "selection": (rounds[-1].get("selection") if rounds else None) or "pareto_niche",
        "rounds": len(rounds),
        "trials": {"dev_evaluated": len(evals),
                   "proposed": sum(r.get("proposed", 0) for r in rounds),
                   "attempts": sum(r.get("attempts", 0) for r in rounds),
                   "invalid": sum(r.get("invalid", 0) for r in rounds),
                   "duplicates": sum(r.get("duplicates", 0) for r in rounds),
                   "semantic_duplicates": sum(r.get("semantic_duplicates", 0) for r in rounds)},
        "status_counts": dict(Counter(status.values())),
        "children_evaluated": block(kids),
        "children_dev_pass": block([s for s in kids if status[s] == "RESEARCH_ONLY"]),
        "all_dev_pass": block(passers),
        "elite_size_last_round": len(rounds[-1]["elite"]) if rounds else None,
        "map_cells_last_round": len(rounds[-1].get("map_cells") or {}) if rounds else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("runs", nargs="+", help="NAME=<state_dir>/evolution")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    rep = {"use": "DEV only, same seed and budget per pair; a diagnostic of the search, not a verdict",
           "runs": {n: summarize(Path(p)) for n, p in (x.split("=", 1) for x in a.runs)}}
    text = json.dumps(rep, indent=1, sort_keys=True)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
