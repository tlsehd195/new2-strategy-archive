"""Empirical behaviour overlap of candidates on their DEV trades (ADR 0025).

``evolution.behaviour_key`` / ``near_duplicate`` compare DEFINITIONS: they
fold exact equivalences and flag thresholds within half a mutation step.
They cannot see that two definitions with different parameters (a window of
96 vs 97 bars, a filter that never binds on these data) trade identically.
This module compares what the candidates actually DID on the DEV window:

- ``IDENTICAL_TRADES``: the same set of (symbol, entry time, side, exit
  time) — exact, no threshold.  Such candidates are one hypothesis on the DEV
  evidence; the report groups them.
- otherwise ``DIFFERENT``, with the overlap measures reported (never used
  to merge): entry Jaccard (same symbol, entry time and side), direction
  agreement on shared entry times, exit agreement on shared entries, mean
  holding bars of each side.

Same entries with different exits (another horizon or stop) are NOT
identical: their risk differs, so they are never merged by this module.
The diagnostic changes no candidate list and no gate; a policy that acts on
it is a decision for the user (it is listed as a follow-up in ADR 0025).
"""

from __future__ import annotations

import hashlib
import json
from typing import Mapping, Sequence

from engine_lib import canonical_json


def trade_set(events: Sequence[Mapping]) -> frozenset:
    return frozenset((e["symbol"], int(e["entry_ts"]), int(e["side"]), int(e["exit_ts"])) for e in events)


def trade_hash(events: Sequence[Mapping]) -> str:
    return hashlib.sha256(canonical_json(sorted(trade_set(events))).encode()).hexdigest()[:20]


def overlap(a: Sequence[Mapping], b: Sequence[Mapping]) -> dict:
    ta, tb = trade_set(a), trade_set(b)
    ea = {(s, t, d) for s, t, d, _ in ta}
    eb = {(s, t, d) for s, t, d, _ in tb}
    union = ea | eb
    by_time_a = {(s, t): d for s, t, d, _ in ta}
    by_time_b = {(s, t): d for s, t, d, _ in tb}
    shared_t = set(by_time_a) & set(by_time_b)
    exit_a = {(s, t, d): x for s, t, d, x in ta}
    exit_b = {(s, t, d): x for s, t, d, x in tb}
    shared_e = ea & eb

    def hold(evs):
        return sum(e["exit_index"] - e["entry_index"] for e in evs) / len(evs) if evs else None
    identical = bool(ta) and ta == tb
    return {"kind": "IDENTICAL_TRADES" if identical else "DIFFERENT",
            "n_a": len(ta), "n_b": len(tb),
            "entry_jaccard": len(ea & eb) / len(union) if union else None,
            "direction_agreement": (sum(1 for k in shared_t if by_time_a[k] == by_time_b[k]) / len(shared_t)
                                    if shared_t else None),
            "exit_agreement": (sum(1 for k in shared_e if exit_a[k] == exit_b[k]) / len(shared_e)
                               if shared_e else None),
            "mean_hold_bars_a": hold(a), "mean_hold_bars_b": hold(b)}


def groups(events_by_id: Mapping[str, Sequence[Mapping]]) -> dict:
    """{trade hash: [ids]} for every hash shared by two or more candidates
    with at least one trade (exact behavioural twins on these data)."""
    by: dict = {}
    for sid, ev in sorted(events_by_id.items()):
        if ev:
            by.setdefault(trade_hash(ev), []).append(sid)
    return {h: ids for h, ids in sorted(by.items()) if len(ids) > 1}


def report(events_by_id: Mapping[str, Sequence[Mapping]], *, top: int = 20) -> dict:
    """Twin groups plus the most-overlapping DIFFERENT pairs (for reading)."""
    ids = sorted(events_by_id)
    pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            o = overlap(events_by_id[a], events_by_id[b])
            if o["kind"] == "DIFFERENT" and o["entry_jaccard"]:
                pairs.append({"a": a, "b": b, **o})
    pairs.sort(key=lambda x: (-x["entry_jaccard"], x["a"], x["b"]))
    g = groups(events_by_id)
    return {"schema": "behaviour_overlap_v1", "stage": "dev", "candidates": len(ids),
            "identical_trade_groups": g,
            "candidates_in_twin_groups": sum(len(v) for v in g.values()),
            "distinct_behaviours": len(ids) - sum(len(v) - 1 for v in g.values()),
            "most_overlapping_different_pairs": pairs[:top],
            "note": "diagnostic only: no candidate is removed by this report"}


def dev_events(pipe, sid: str) -> list[dict]:
    """DEV events of a registered candidate (same window and cost as its DEV record)."""
    from strategy_spec import canonicalize_spec
    spec = canonicalize_spec(pipe.specs.get(sid))
    data, fng, missing = pipe._inputs(spec)
    if missing:
        return []
    out = []
    for sym, e in data.items():
        d1 = pipe.provider.windows(sym, spec["exit"]["horizon"])["dev"][1]
        out += pipe._study(spec, sym, e["bars"], e["funding"], fng, 1, d1)
    return out


if __name__ == "__main__":  # pragma: no cover - CLI (real data)
    import argparse
    import sys
    from pathlib import Path
    from pipeline import EvolutionPipeline, RunnerDataProvider
    from runner import cost_from_env
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    prov = RunnerDataProvider(Path(a.data_dir))
    usable = [s for s in ("BTCUSDT", "ETHUSDT") if not prov.symbol(s)["missing"]]
    pipe = EvolutionPipeline(Path(a.state_dir), prov, cost_from_env(), symbols=usable)
    ids = [x.strip() for x in Path(a.ids).read_text().splitlines() if x.strip()]
    rep = report({s: dev_events(pipe, s) for s in ids})
    Path(a.out).write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("candidates", "candidates_in_twin_groups", "distinct_behaviours")}))
    sys.exit(0)
