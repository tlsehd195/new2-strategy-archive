"""Research -> NEW2 hand-off artifact (directive §11).

Writes ONE self-describing, hash-sealed JSON file per candidate.  It is a
document for a human reviewer, nothing else:
  * never registers anything in a trading registry, never imports NEW2 code,
    never touches the network, never creates orders;
  * only strategies that the pipeline has taken to PAPER_TEST_READY or beyond
    can be exported, and the evidence is re-read from the ledgers (not from
    a caller-supplied flag);
  * ``live_registration`` is always false and ``requires_human_approval``
    always true.  Deployment is a separate human procedure outside this repo.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Mapping, Optional

from engine_lib import canonical_json, iso_now, iter_jsonl
from strategy_spec import canonicalize_spec

#: v2 (2026-10-16): adds the forward-paper evidence and the cost verification record id
HANDOFF_SCHEMA = "new2_research_handoff_v2"
EXPORTABLE = ("PAPER_TEST_READY", "PAPER_TESTING", "PAPER_PASSED")


class HandoffRefused(RuntimeError):
    pass


def build_handoff(pipeline, sid: str) -> dict:
    ctx = pipeline.context_id()
    st = pipeline.statuses.get(sid, ctx)
    if not st or st["status"] not in EXPORTABLE:
        raise HandoffRefused(f"{sid} is {st and st['status']}: not exportable")
    # the promotion decision is re-derived from the ledgers, not trusted from the status line
    review = [r for r in iter_jsonl(pipeline.review_path)
              if r["strategy_id"] == sid and r["context_id"] == ctx]
    if not review or review[-1]["decision"] != "PAPER_TEST_READY" or review[-1]["failures"]:
        raise HandoffRefused(f"{sid}: no clean promotion review on record")
    evidence = {}
    for stage in pipeline.cfg["promotion"]["required_evidence"]:
        rec = pipeline.evaluation(stage, sid)
        if rec is None or rec["record_id"] != review[-1]["evidence"].get(stage):
            raise HandoffRefused(f"{sid}: {stage} evidence missing or differs from the review")
        evidence[stage] = rec["record_id"]
    paper = pipeline.evaluation("paper", sid)
    forward = pipeline.evaluation("paper_forward", sid)
    comps = next((r["components"] for r in iter_jsonl(pipeline.dir / "contexts.jsonl")
                  if r["context_id"] == ctx), {})
    spec = _spec_or_refuse(pipeline, sid)
    _forward_or_refuse(sid, st, spec, forward)
    cost = pipeline._cost_block()
    body = {
        "schema": HANDOFF_SCHEMA,
        "strategy_id": sid,
        "spec": spec,
        "lineage": pipeline.lineage.paths(sid),
        "context_id": ctx,
        "context_components": comps,
        "status": st["status"],
        "evidence_record_ids": evidence,
        "paper_record_id": paper["record_id"] if paper else None,
        "paper_kind": "isolated replay of stored post-locked data (can fail, never passes)",
        "paper_forward_record_id": forward["record_id"] if forward else None,
        "paper_forward_verdict": forward["verdict"] if forward else "NOT_RUN",
        "cost_model": cost,
        "cost_verification_record": pipeline.cost.identity().get("verification_record"),
        "labels": review[-1].get("labels", []),
        "failure_conditions": [
            "any later cost-model recalibration that turns the net edge <= 0",
            "paper result inconsistent with locked result",
            "data-quality BLOCK on any required symbol",
        ],
        "research_not_profitability_proof": True,
        "live_registration": False,
        "requires_human_approval": True,
        "created_at": iso_now(),
    }
    body["sha256"] = hashlib.sha256(canonical_json(
        {k: v for k, v in body.items() if k != "created_at"}).encode()).hexdigest()
    return body


def _spec_or_refuse(pipeline, sid: str) -> dict:
    """The canonical specification, or HandoffRefused (never a guess)."""
    from strategy_spec import SpecError
    raw = pipeline.specs.get(sid)
    if raw is None:
        raise HandoffRefused(f"{sid}: DATA_MISSING: strategy specification missing")
    try:
        return canonicalize_spec(raw)
    except (SpecError, KeyError, TypeError, ValueError) as e:
        raise HandoffRefused(f"{sid}: strategy specification unreadable: {type(e).__name__}: {e}") from None


def _forward_or_refuse(sid: str, st: Mapping, spec: Mapping, forward: Optional[Mapping]) -> None:
    """ADR 0079 follow-up (C-4): the forward-paper evidence a hand-off carries.

    - A strategy that reads FNG has no forward conclusion that can be verified:
      no plan pins its FNG values (the forward feed has no FNG source), so a
      recorded verdict — PAPER_PASSED included — is refused with the same reason
      ``ForwardPaper`` gives (``_fng_refusal``: FNG_NOT_PINNED) plus
      UNVERIFIED_PRIOR_CONCLUSION.  The record itself is left as it is.
    - A PAPER_PASSED status is only exported with the forward record it came
      from (the status line is not trusted on its own): missing, another
      verdict or another record id than the status evidence is refused.
    Nothing is read from a feed and nothing is written."""
    from paper_forward import ForwardPaper
    fng = ForwardPaper._fng_refusal(spec)
    if fng and forward is not None:
        raise HandoffRefused(
            f"{sid}: {fng}; UNVERIFIED_PRIOR_CONCLUSION: recorded paper_forward verdict "
            f"{forward.get('verdict')} ({forward.get('record_id')}) cannot be verified and is not "
            "exported as evidence")
    if st["status"] == "PAPER_PASSED":
        if forward is None:
            raise HandoffRefused(f"{sid}: DATA_MISSING: status PAPER_PASSED without its paper_forward record")
        if forward.get("verdict") != "PAPER_PASSED":
            raise HandoffRefused(f"{sid}: status PAPER_PASSED but the paper_forward record says "
                                 f"{forward.get('verdict')}")
        ev = (st.get("evidence") or {}).get("paper_forward")
        if ev != forward.get("record_id"):
            raise HandoffRefused(f"{sid}: status PAPER_PASSED points to paper_forward record {ev}, the "
                                 f"ledger holds {forward.get('record_id')}")


def export_handoff(pipeline, sid: str, out_dir: Path) -> Path:
    """Write the artifact; never overwrites an existing file."""
    doc = build_handoff(pipeline, sid)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{sid}.{doc['sha256'][:12]}.handoff.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "x", encoding="utf-8") as f:
        f.write(json.dumps(doc, sort_keys=True, indent=1))
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, path)            # fails if the target already exists: no overwrite
    except FileExistsError:
        pass
    finally:
        os.unlink(tmp)
    return path
