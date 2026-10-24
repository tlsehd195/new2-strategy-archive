#!/usr/bin/env python3
"""Prereg v2 entry bar on DEV time: basket-average annual SR of candidates (ADR 0036).

The v2 draft (ADR 0023, D5) freezes a member only if its DEV annual SR on the
v2 statistic (equal-weight daily net bp over the basket symbols) reaches the
entry bar.  This scores candidates exactly like ``iw1_statistic_dev.score``
(the engine's own signal, stop and event code) over the DEV stretches of
``strong_search_dev`` (same refused reads), only with the basket as the symbol
list.  S2 never reads BTC (its paper window), as in ADR 0019.

Cost: ``--cost assumed_public_v1`` (default, an assumed profile) or a sealed
``public_market_estimate_v1`` record path (``--cost-record``).  DEV only:
nothing here reads a validation, locked TEST, IW1 or paper window.

    python3 run/forward_v2_entry_bar_dev.py --data-dir <dir> --candidates c.json \\
        --symbols BTCUSDT ETHUSDT ... --out o.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))
import iw1_statistic_dev as I  # noqa: E402
import strong_search_dev as T  # noqa: E402
from costs_bridge import CostContext  # noqa: E402
from strategy_spec import canonicalize_spec  # noqa: E402

ENTRY_BAR = 1.0      # DEV annual SR on S1 (the selection stretch), ADR 0023 D5


def segments_for(symbols: list[str]) -> dict:
    """The DEV stretches of strong_search_dev with the basket as symbol list."""
    out = {}
    for name, seg in T.SEGMENTS.items():
        syms = list(symbols) if name == "S1" else [s for s in symbols if s != "BTCUSDT"]
        out[name] = {**seg, "symbols": syms}
    return out


def entry_bar_pass(row: dict, bar: float = ENTRY_BAR) -> bool:
    sr = (row.get("S1") or {}).get("sr_ann")
    return sr is not None and sr == sr and sr >= bar


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--candidates", type=Path, required=True, help='[{"strategy_id", "spec"}, ...]')
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--cost", default="assumed_public_v1")
    ap.add_argument("--cost-record", type=Path, help="sealed public estimate record (replaces --cost)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cost_record:
        cost_ctx = CostContext.from_public_estimate(json.loads(a.cost_record.read_text()))
        cost_name = f"public_estimate:{a.cost_record.name}"
    else:
        cost_ctx = CostContext.from_assumed_profile(a.cost)
        cost_name = a.cost
    T.SEGMENTS = segments_for(a.symbols)
    for seg in T.SEGMENTS.values():
        for s in seg["symbols"]:
            T.assert_allowed(s, seg["read_start"], seg["read_end"])
    data = {s: T.load_segment(s, a.data_dir) for s in ("S1", "S2")}
    out = []
    for c in json.loads(a.candidates.read_text()):
        spec = canonicalize_spec(c["spec"])
        row = {"strategy_id": c["strategy_id"], "rank": c.get("rank"),
               **{s: I.score(spec, data[s], s, cost_ctx) for s in ("S1", "S2")}}
        row["entry_bar_pass"] = entry_bar_pass(row)
        out.append(row)
        print(row["strategy_id"], round(row["S1"]["sr_ann"], 2), round(row["S2"]["sr_ann"], 2),
              row["entry_bar_pass"], flush=True)
    a.out.write_text(json.dumps({"cost": cost_name, "cost_identity": cost_ctx.identity(),
                                 "symbols": a.symbols, "segments": T.SEGMENTS, "entry_bar_s1_sr": ENTRY_BAR,
                                 "candidates": out}, indent=1, default=float) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
