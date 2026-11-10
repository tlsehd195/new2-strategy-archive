"""Read-only consistency check of a state directory (directive 2026-10-20 §7).

``python3 ledger_check.py <state_dir> [--json OUT]`` — exit 0 consistent,
2 inconsistencies found, 1 unreadable ledger.  Nothing is repaired here:
the findings say what is wrong and ``README.md`` ("Recovery") says what to do.

Roles of the files (the check enforces them):

=================================  ==========================================
file                               role
=================================  ==========================================
``evolution/evaluations.jsonl``    SOURCE: hash-sealed evaluation records
``evolution/statuses.jsonl``       SOURCE: append-only status transitions
``evolution/validation_batches``   SOURCE: preregistration + result per batch
``evolution/promotion_reviews``    SOURCE: promotion decisions
``evolution/evolution_cycle_       DERIVED: last cycle summary (may lag; a
summary.json``                     ``CYCLE_RUNNING`` without a live lock is an
                                   interrupted cycle)
``discovery_ledger.jsonl``         SOURCE (legacy engine): evaluation records
``state.json``                     DERIVED cache of the legacy candidate states
``queue.json``                     DERIVED re-verification schedule
``cycles.md``, ``promotion_queue``  DERIVED human-readable logs
=================================  ==========================================

Evolution engine checks: every status transition is allowed by
``pipeline.TRANSITIONS`` and chains from the previous record; evaluation
records match their hash; no (stage, strategy, context) is evaluated twice;
no strategy is preregistered in two validation batches of one context; every
batch has at most one result and every result a preregistration; every
evidence id a status cites exists; every OK DEV record has its DEV daily
series (``MISSING_DEV_SERIES``); at most one PAPER_TEST_READY review per
(strategy, context); the summary's status counts match the status ledger.

Frozen v1 history (``state.json`` with ``engine == "NEW2-discovery-v1"``, the
files committed at ``run/discovery``): ``state.json`` is a cache rebuilt from
the ledger, so ``tested`` = distinct ``cand_hash``, ``tested_rows`` = ledger
rows, ``cursors`` = distinct candidates per family, ``cycle`` / ``last_ts`` =
the last ledger row, and survivors / promotion queue only name candidates
with a survivor verdict (ADR 0034; before it the check called the file
unreadable).

Legacy engine checks: state.json loads with its schema; a final state in
state.json agrees with the latest ledger record of that strategy (or is a
legal re-verification of it); a RUNNING candidate without a live lock is an
interrupted evaluation (``store.reconcile`` re-queues it on the next run).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from engine_lib import JsonlCorruption, iter_jsonl, load_json

FINDING_KINDS = ("IMPOSSIBLE_TRANSITION", "BROKEN_STATUS_CHAIN", "EVIDENCE_TAMPERED",
                 "DUPLICATE_EVALUATION", "DUPLICATE_SUBMISSION", "DUPLICATE_RESULT", "ORPHAN_RESULT",
                 "MISSING_EVIDENCE", "DUPLICATE_PROMOTION", "SUMMARY_MISMATCH", "INTERRUPTED_CYCLE",
                 "STATE_LEDGER_MISMATCH", "STUCK_RUNNING", "STATE_UNREADABLE", "V1_ARCHIVE_MISMATCH")
#: the frozen v1 engine history committed at run/discovery (state.json rebuilt
#: from the ledger on 2026-10-15); it predates the CandidateStore schema
V1_ENGINE = "NEW2-discovery-v1"
#: v1 verdicts that put a candidate on the survivor list
V1_SURVIVOR_VERDICTS = ("SURVIVOR", "SURVIVOR_COMBO", "PASS")


def _lock_alive(state_dir: Path) -> bool:
    lk = Path(state_dir) / "run.lock"
    if not lk.exists():
        return False
    try:
        pid = int(lk.read_text().strip())
    except (ValueError, OSError):
        return False
    from ops import _pid_alive
    return _pid_alive(pid)


def check_evolution(state_dir: Path) -> list[dict]:
    from pipeline import TRANSITIONS, record_hash
    evo = Path(state_dir) / "evolution"
    out: list[dict] = []
    if not evo.exists():
        return out
    # statuses: replay the transition table
    last: dict = {}
    for i, r in enumerate(iter_jsonl(evo / "statuses.jsonl"), 1):
        key = (r["strategy_id"], r["context_id"])
        prev = last.get(key)
        cur_prev = prev["status"] if prev else None
        if r.get("previous") != cur_prev:
            out.append({"kind": "BROKEN_STATUS_CHAIN", "line": i, "strategy_id": r["strategy_id"],
                        "detail": f"record says previous={r.get('previous')}, ledger has {cur_prev}"})
        if r["status"] not in TRANSITIONS.get(cur_prev, set()):
            out.append({"kind": "IMPOSSIBLE_TRANSITION", "line": i, "strategy_id": r["strategy_id"],
                        "detail": f"{cur_prev} -> {r['status']}"})
        last[key] = r
    # evaluations: hash and uniqueness
    seen: Counter = Counter()
    ids = set()
    for i, r in enumerate(iter_jsonl(evo / "evaluations.jsonl"), 1):
        if r.get("record_id") != record_hash(r):
            out.append({"kind": "EVIDENCE_TAMPERED", "line": i, "strategy_id": r.get("strategy_id"),
                        "detail": f"record {r.get('record_id')} does not match its content"})
        seen[(r["stage"], r["strategy_id"], r["context_id"])] += 1
        ids.add(r.get("record_id"))
    # an OK DEV record must have its daily series (DSR / PBO trial set, ADR 0065);
    # before ADR 0064 the record was written first, so a kill between the two
    # writes left the record without its series
    have_series = {(r.get("strategy_id"), r.get("context_id")) for r in iter_jsonl(evo / "dev_series.jsonl")}
    for i, r in enumerate(iter_jsonl(evo / "evaluations.jsonl"), 1):
        if r.get("stage") == "dev" and r.get("status") == "OK" \
                and (r["strategy_id"], r["context_id"]) not in have_series:
            out.append({"kind": "MISSING_DEV_SERIES", "line": i, "strategy_id": r["strategy_id"],
                        "detail": f"OK DEV record under {r['context_id']} has no dev_series row — "
                                  "the strategy is missing from the DSR/PBO trial set"})
    for (stage, sid, ctx), n in sorted(seen.items()):
        if n > 1 and stage != "robustness":
            out.append({"kind": "DUPLICATE_EVALUATION", "strategy_id": sid,
                        "detail": f"{n} {stage} records under context {ctx}"})
    # status evidence must exist
    for (sid, ctx), r in sorted(last.items()):
        for stage, rid in sorted((r.get("evidence") or {}).items()):
            if isinstance(rid, str) and rid.startswith("EV-") and rid not in ids:
                out.append({"kind": "MISSING_EVIDENCE", "strategy_id": sid,
                            "detail": f"{r['status']} cites {stage} record {rid}, not in evaluations"})
    # batches
    pre: dict = {}
    res: Counter = Counter()
    member_batches: dict = defaultdict(list)
    for b in iter_jsonl(evo / "validation_batches.jsonl"):
        if b["kind"] == "PREREGISTERED":
            pre[b["batch_id"]] = b
            for m in b["members"]:
                member_batches[(m, b["context_id"])].append(b["batch_id"])
        elif b["kind"] == "RESULT":
            res[b["batch_id"]] += 1
    for bid, n in sorted(res.items()):
        if bid not in pre:
            out.append({"kind": "ORPHAN_RESULT", "detail": f"result for unknown batch {bid}"})
        if n > 1:
            out.append({"kind": "DUPLICATE_RESULT", "detail": f"batch {bid} has {n} results"})
    for (m, ctx), bs in sorted(member_batches.items()):
        if len(bs) > 1:
            out.append({"kind": "DUPLICATE_SUBMISSION", "strategy_id": m,
                        "detail": f"in {len(bs)} validation batches of context {ctx}: {bs}"})
    # promotions
    ready = Counter((r["strategy_id"], r["context_id"]) for r in iter_jsonl(evo / "promotion_reviews.jsonl")
                    if r.get("decision") == "PAPER_TEST_READY")
    for (sid, ctx), n in sorted(ready.items()):
        if n > 1:
            out.append({"kind": "DUPLICATE_PROMOTION", "strategy_id": sid,
                        "detail": f"{n} PAPER_TEST_READY reviews under {ctx}"})
    # derived summary
    summ = load_json(evo / "evolution_cycle_summary.json", None)
    if isinstance(summ, dict) and summ:
        if summ.get("status") == "CYCLE_RUNNING" and not _lock_alive(state_dir):
            out.append({"kind": "INTERRUPTED_CYCLE",
                        "detail": f"summary says CYCLE_RUNNING since {summ.get('started_at')} and no live "
                                  "process holds run.lock — rerun the same command (it resumes)"})
        ctx = summ.get("context_id")
        if ctx and summ.get("status") not in ("CYCLE_RUNNING", "CYCLE_FAILED"):
            have = Counter(r["status"] for (s, c), r in last.items() if c == ctx)
            if dict(have) != dict(summ.get("status_counts") or {}):
                out.append({"kind": "SUMMARY_MISMATCH",
                            "detail": f"summary status_counts {summ.get('status_counts')} != ledger {dict(have)} "
                                      "(the ledger is authoritative)"})
    return out


def check_legacy(state_dir: Path) -> list[dict]:
    from engine_lib import CandidateState
    from store import CandidateStore, StoreCorruption
    sd = Path(state_dir)
    out: list[dict] = []
    if not (sd / "state.json").exists():
        return out
    try:
        raw = load_json(sd / "state.json")
    except (OSError, ValueError):
        raw = None
    if isinstance(raw, dict) and raw.get("engine") == V1_ENGINE:
        return check_v1_archive(sd, raw)
    try:
        store = CandidateStore(sd / "state.json")
    except StoreCorruption as e:
        return [{"kind": "STATE_UNREADABLE", "detail": str(e)}]
    latest: dict = {}
    for r in iter_jsonl(sd / "discovery_ledger.jsonl"):
        latest[r["strategy_id"]] = r
    final = {s.value for s in (CandidateState.PASS, CandidateState.FAIL_COST, CandidateState.FAIL_STAT,
                               CandidateState.UNDETERMINED_POWER, CandidateState.DATA_MISSING,
                               CandidateState.ERROR, CandidateState.AWAITING_FDR)}
    alive = _lock_alive(sd)
    for sid, st in sorted(store.all_states().items()):
        rec = latest.get(sid)
        if st in final and (rec is None or rec.get("status") != st):
            out.append({"kind": "STATE_LEDGER_MISMATCH", "strategy_id": sid,
                        "detail": f"state.json {st}, latest ledger record "
                                  f"{rec.get('status') if rec else 'none'}"})
        if st == CandidateState.RUNNING.value and not alive:
            out.append({"kind": "STUCK_RUNNING", "strategy_id": sid,
                        "detail": "RUNNING without a live run.lock — the next run's reconcile() re-queues it"})
    for sid, rec in sorted(latest.items()):
        cur = store.all_states().get(sid)
        if rec.get("status") in final and cur not in (rec.get("status"), CandidateState.REVERIFY_PENDING.value,
                                                     CandidateState.PENDING.value, CandidateState.RUNNING.value):
            out.append({"kind": "STATE_LEDGER_MISMATCH", "strategy_id": sid,
                        "detail": f"ledger {rec.get('status')}, state.json {cur}"})
    return out


def check_v1_archive(sd: Path, st: dict) -> list[dict]:
    """Frozen v1 history: the state cache must be exactly what its ledger implies."""
    rows = list(iter_jsonl(sd / "discovery_ledger.jsonl"))
    out: list[dict] = []

    def bad(what: str, have, want):
        out.append({"kind": "V1_ARCHIVE_MISMATCH", "detail": f"state.json {what} = {have!r}, ledger implies {want!r}"})
    fam: dict = defaultdict(set)
    for r in rows:
        fam[r.get("family")].add(r.get("cand_hash"))
    hashes = {r.get("cand_hash") for r in rows}
    want = {"tested": len(hashes), "tested_rows": len(rows),
            "cycle": max((r.get("cycle") or 0 for r in rows), default=0),
            "last_ts": rows[-1].get("ts") if rows else None,
            "cursors": {k: len(v) for k, v in fam.items()}}
    for k, v in want.items():
        if st.get(k) != v:
            bad(k, st.get(k), v)
    surv = {r.get("cand_hash") for r in rows if r.get("verdict") in V1_SURVIVOR_VERDICTS}
    for k in ("survivors", "promotion_queue", "stouffer_done"):
        names = {x if isinstance(x, str) else (x or {}).get("cand_hash") for x in st.get(k) or []}
        if names - surv:
            bad(k, sorted(names - surv), "only candidates with a survivor verdict")
    if set(st.get("survivor_specs") or {}) - surv:
        bad("survivor_specs", sorted(set(st.get("survivor_specs") or {}) - surv),
            "only candidates with a survivor verdict")
    return out


def check(state_dir: Path) -> dict:
    try:
        findings = check_evolution(state_dir) + check_legacy(state_dir)
    except JsonlCorruption as e:
        return {"state_dir": str(state_dir), "status": "UNREADABLE", "error": str(e), "findings": []}
    return {"state_dir": str(state_dir), "status": "INCONSISTENT" if findings else "CONSISTENT",
            "counts": dict(Counter(f["kind"] for f in findings)), "findings": findings}


def main(argv: Optional[list] = None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="read-only state / ledger consistency check")
    ap.add_argument("state_dir")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    rep = check(Path(a.state_dir))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: rep.get(k) for k in ("state_dir", "status", "counts", "error")}, indent=1))
    return {"CONSISTENT": 0, "INCONSISTENT": 2}.get(rep["status"], 1)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
