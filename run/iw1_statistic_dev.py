#!/usr/bin/env python3
"""Re-score given spec candidates with the IW1 statistic on DEV time (ADR 0019).

Uses the engine's own signal, stop/take-profit and event code
(strategy_spec.build_spec_signal, evaluator.run_event_study) on the six
IW1 tranche-A symbols, cost ``assumed_public_v1``, equal-weight daily net bp,
over the DEV stretches S1/S2 of ``strong_search_dev`` (same refused reads).
Reports annual SR, folds, events, hit rate and loss tail.  Diagnostic only.

    python3 run/iw1_statistic_dev.py --data-dir <dir> --candidates c.json --out o.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))
import strong_search_dev as T  # noqa: E402
from costs_bridge import CostContext  # noqa: E402
from evaluator import run_event_study  # noqa: E402
from strategy_spec import build_spec_aux, build_spec_signal, canonicalize_spec, stop_exit_rule  # noqa: E402


def score(spec: dict, data: dict, seg: str, cost_ctx) -> dict:
    d0, d1 = T.day_grid(T.SEGMENTS[seg])
    pooled = np.zeros(d1 - d0 + 1)
    nets, per = [], {}
    for sym, entry in data.items():
        evs = []
        for sd in entry["pieces"]:
            aux = build_spec_aux(sd.bars, spec, sd.funding, None)
            res = run_event_study(sd.bars, sym, build_spec_signal(spec, aux), spec["exit"]["horizon"],
                                  cost_ctx, T.ORDER_VALUE, sd.funding, warmup=max(1, sd.entry_lo - 1),
                                  exit_rule=stop_exit_rule(spec))
            evs += [(e.entry_index, e.exit_index, e.side, e.entry_ts, e.exit_ts, e.gross_bp,
                     e.cost_bp, e.funding_bp, e.net_bp) for e in res.events]
        x = T.daily(evs, d0, d1)
        pooled += x
        per[sym] = {"n": len(evs), "sr_ann": T.sr_ann(x),
                    "mean_net_bp": float(np.mean([e[8] for e in evs])) if evs else None}
        nets += [e[8] for e in evs]
    pooled /= len(data)
    a = np.array(nets)
    return {"sr_ann": T.sr_ann(pooled), "fold_sr_ann": T.fold_srs(pooled), "n_events": len(a),
            "mean_net_bp": float(a.mean()) if len(a) else None,
            "hit_rate": float((a > 0).mean()) if len(a) else None,
            "worst_net_bp": float(a.min()) if len(a) else None,
            "skew": T.moments(a)[0] if len(a) > 2 else None, "per_symbol": per}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--candidates", type=Path, required=True, help='[{"strategy_id", "spec"}, ...]')
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    cost_ctx = CostContext.from_assumed_profile("assumed_public_v1")
    data = {s: T.load_segment(s, a.data_dir) for s in ("S1", "S2")}
    out = []
    for c in json.loads(a.candidates.read_text()):
        spec = canonicalize_spec(c["spec"])
        row = {"strategy_id": c["strategy_id"], "source": c.get("source"),
               **{s: score(spec, data[s], s, cost_ctx) for s in ("S1", "S2")}}
        out.append(row)
        print(row["strategy_id"], round(row["S1"]["sr_ann"], 2), round(row["S2"]["sr_ann"], 2), flush=True)
    a.out.write_text(json.dumps({"cost": "assumed_public_v1", "segments": T.SEGMENTS,
                                 "candidates": out}, indent=1, default=float) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
