"""Search generations: preregistered candidate manifests and the
generation-wide BH-FDR decision (residual fixes P0-1 and P1-8).

Definitions
-----------
search generation
    One preregistered candidate space: the complete sorted list of
    (strategy_id, normalized definition) pairs plus the generator version
    and the multiple-testing specification.  Its id is the hash of exactly
    that content — the order in which a generator yields candidates, or the
    number of candidates, can never by itself make two spaces "equal".

FDR family
    (generation_id, experiment context_id).  It holds one hypothesis per
    candidate of the generation, m = |generation|.  Each candidate's test
    statistic is the pre-registered primary-cell dev p-value (one-sided
    HAC t-test, primary symbol), taken from the FIRST dev-phase ledger record
    of that candidate under this context — later re-evaluations stay in the
    ledger as history but can never replace the tested value.

Decision rule (pre-specified, fixed-sample, NOT sequential)
-----------------------------------------------------------
The family is decided ONCE, when every candidate of the generation has a
dev outcome PRODUCED UNDER THAT CONTEXT.  A PASS or dev record from another
context is never counted as an evaluation of this one: such candidates are
reported (``prior_context_pass`` / ``prior_context_only``), flagged for
re-evaluation, and keep the family undecided until they are re-tested.  Then Benjamini–Hochberg runs at q = 0.05 over m = |generation|:
hypotheses without a valid statistic (UNDETERMINED_POWER, DATA_MISSING) are
counted in m and never rejected — no p-value is imputed for them.  Before
the family is complete no candidate of the generation can survive, so no
locked TEST can be consumed and the evaluation order cannot change any
decision.  The decision is written append-only and is never recomputed.

Statistical status (stated, not overclaimed): BH controls FDR at q under
independence or positive regression dependence (PRDS) of the test
statistics (Benjamini & Hochberg 1995; Benjamini & Yekutieli 2001).  The
candidates here share data and overlap heavily, so PRDS is an ASSUMPTION,
not a verified property; Benjamini–Yekutieli would give a guarantee under
arbitrary dependence at a much lower power and is not used.  Re-testing
the same generation under a NEW context (new data / code / cost) creates a
new family and is not FDR-controlled across families — the single-use
locked TEST (one exposure per strategy+symbol, ever) is the safeguard
against that repetition.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from engine_lib import (
    append_jsonl,
    atomic_write_json,
    bh_fdr_family,
    canonical_json,
    iso_now,
    iter_jsonl,
    load_json,
    normalize_strategy,
    strategy_id,
)

GENERATOR_VERSION = "rule_based_v2"
FDR_Q = 0.05
FDR_METHOD = "BH_full_generation_fixed_sample_v1"
FDR_SPEC = {
    "method": FDR_METHOD,
    "q": FDR_Q,
    "family": "all candidates of the generation under one experiment context",
    "statistic": "primary-cell dev p-value (first dev record per candidate)",
    "untestable": "counted in m, never rejected, no p-value imputed",
    "finalization": "only when every candidate has a dev outcome",
}

#: dev-phase statuses that count as "this hypothesis was evaluated" for
#: family completeness.  ERROR does not — the hypothesis was not tested.
DEV_OUTCOME_STATUSES = {"AWAITING_FDR", "FAIL_STAT", "FAIL_COST",
                        "UNDETERMINED_POWER", "DATA_MISSING"}


class ManifestError(ValueError):
    pass


class PreregistrationConflict(RuntimeError):
    pass


def build_manifest(cands: Sequence[tuple[str, Mapping]], dedup_log: Sequence[Mapping] = (),
                   *, generator_version: str = GENERATOR_VERSION,
                   settings: Optional[Mapping] = None) -> dict:
    """Manifest of a candidate space.

    Verifies every (sid, definition) pair — the id must be the hash of the
    normalized definition, so a definition edited after registration is
    detected — and that no strategy id appears twice.
    """
    seen: dict[str, str] = {}
    entries: list[list[str]] = []
    for sid, defn in cands:
        canon = normalize_strategy(defn)
        canon_s = canonical_json(canon)
        recomputed = strategy_id(canon)
        if recomputed != sid:
            raise ManifestError(f"{sid}: definition does not hash to its id "
                                f"(recomputed {recomputed}) — edited after registration?")
        if sid in seen:
            if seen[sid] != canon_s:  # pragma: no cover - sha256 collision
                raise ManifestError(f"{sid}: two different definitions share an id")
            raise ManifestError(f"{sid}: duplicate candidate in the space")
        seen[sid] = canon_s
        entries.append([sid, canon_s])
    entries.sort()
    core = {"generator_version": generator_version, "fdr": FDR_SPEC,
            "candidates": entries}
    manifest_hash = hashlib.sha256(canonical_json(core).encode("utf-8")).hexdigest()
    by_family: dict[str, int] = {}
    for _, d in cands:
        by_family[d["family"]] = by_family.get(d["family"], 0) + 1
    return {
        "generation_id": "G-" + manifest_hash[:16],
        "manifest_hash": manifest_hash,
        "generator_version": generator_version,
        "fdr": FDR_SPEC,
        "n_candidates": len(entries),
        "n_families": len(by_family),
        "family_counts": dict(sorted(by_family.items())),
        "strategy_ids": [e[0] for e in entries],
        "candidates": entries,
        "duplicates_collapsed": [
            {"strategy_id": d.get("strategy_id"), "family": d.get("family"),
             "normalized": canonical_json(d.get("collapsed_definition") or {})}
            for d in dedup_log
        ],
        "settings": dict(settings or {}),
    }


def verify_manifest_doc(doc: Mapping) -> None:
    """Recompute the hash of a stored manifest; raise if it was altered."""
    core = {"generator_version": doc["generator_version"], "fdr": doc["fdr"],
            "candidates": doc["candidates"]}
    h = hashlib.sha256(canonical_json(core).encode("utf-8")).hexdigest()
    if h != doc.get("manifest_hash") or doc.get("generation_id") != "G-" + h[:16]:
        raise PreregistrationConflict(
            f"stored manifest {doc.get('generation_id')} does not match its content hash")
    for sid, canon_s in doc["candidates"]:
        if strategy_id(json.loads(canon_s)) != sid:
            raise PreregistrationConflict(f"stored definition of {sid} was altered")


class PreregistrationStore:
    """Write-once manifests under ``state/preregistration/<gen>.json`` plus
    an append-only ``generations.jsonl`` history.  The legacy single-file
    ``preregistration.json`` (no hash) is left untouched and never reused."""

    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir) / "preregistration"
        self.log = Path(state_dir) / "generations.jsonl"
        self.legacy = Path(state_dir) / "preregistration.json"

    def path_for(self, generation_id: str) -> Path:
        return self.dir / f"{generation_id}.json"

    def ensure(self, manifest: Mapping) -> dict:
        gid = manifest["generation_id"]
        p = self.path_for(gid)
        if p.exists():
            doc = load_json(p)
            verify_manifest_doc(doc)
            if doc["manifest_hash"] != manifest["manifest_hash"]:
                raise PreregistrationConflict(f"{gid}: manifest hash mismatch")
            return {"generation_id": gid, "created": False, "doc": doc}
        doc = dict(manifest)
        doc["preregistered_at"] = iso_now()
        doc["legacy_preregistration_present"] = self.legacy.exists()
        doc["legacy_preregistration_reused"] = False
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True),
                       encoding="utf-8")
        try:
            os.link(tmp, p)          # fails if another writer got there first
        except FileExistsError:
            os.unlink(tmp)
            return self.ensure(manifest)
        os.unlink(tmp)
        prev = None
        for rec in iter_jsonl(self.log):
            prev = rec.get("generation_id")
        append_jsonl(self.log, {"generation_id": gid,
                                "manifest_hash": manifest["manifest_hash"],
                                "n_candidates": manifest["n_candidates"],
                                "family_counts": manifest["family_counts"],
                                "generator_version": manifest["generator_version"],
                                "previous_generation": prev,
                                "created_at": doc["preregistered_at"]})
        return {"generation_id": gid, "created": True, "doc": doc}

    def load(self, generation_id: str) -> Optional[dict]:
        p = self.path_for(generation_id)
        if not p.exists():
            return None
        doc = load_json(p)
        verify_manifest_doc(doc)
        return doc

    def is_preregistered(self, sid: str, generation_id: str) -> bool:
        doc = self.load(generation_id)
        return bool(doc) and sid in set(doc["strategy_ids"])

    def generations(self) -> list[dict]:
        return list(iter_jsonl(self.log))


# ---------------------------------------------------------------------------
# FDR family bookkeeping
# ---------------------------------------------------------------------------


def _finite_p(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and 0.0 <= f <= 1.0 else None


FDR_INPUT_SPEC_VERSION = "fdr_input_v1"


class FdrDecisionConflict(RuntimeError):
    """A stored FDR decision exists and the (re)computed inputs disagree with it."""


def _manifest_complete(manifest: Mapping) -> tuple[bool, list[str]]:
    """Is the preregistered candidate list internally complete and hash-consistent?"""
    problems: list[str] = []
    ids = list(manifest.get("strategy_ids") or [])
    if len(ids) != len(set(ids)):
        problems.append("duplicate strategy ids in manifest")
    entries = manifest.get("candidates")
    if entries is not None:
        if sorted(e[0] for e in entries) != sorted(ids):
            problems.append("strategy_ids differ from candidate entries")
        try:
            verify_manifest_doc(manifest)
        except (PreregistrationConflict, KeyError, ValueError) as e:
            problems.append(f"manifest hash check failed: {e}")
    if manifest.get("n_candidates") is not None and manifest["n_candidates"] != len(ids):
        problems.append("n_candidates differs from the id list")
    if not ids:
        problems.append("empty manifest")
    return (not problems), problems


def family_status(records: Iterable[Mapping], manifest: Mapping, context_id: str,
                  *, terminal_pass: Iterable[str] = ()) -> dict:
    """Completeness of the FDR family (generation, context), split into the
    distinct questions the old single ``complete`` flag blurred together.

    * ``manifest_complete``      – the preregistered list is whole, unique and hash-consistent
    * ``current_context_complete`` – EVERY member has a dev outcome produced UNDER THIS
      CONTEXT.  A PASS (or any dev record) from another context never counts.
    * ``fdr_decision_ready``     – both of the above and no inconsistent evidence
    * ``locked_test_eligible``   – NOT a family property: BH survivors + validation PASS are
      ``survivors_validation_pass`` in the decision; whether the window may still be entered
      depends on the lock-TEST consumption history and is decided by the runner
      (``Runner.lock_entry_status`` / ``Runner.locked_test_eligible``)
    * ``complete``               – compatibility alias of ``fdr_decision_ready``

    The tested value of a candidate is the FIRST dev-phase record under this
    context.  Evidence from other contexts (prior-context PASS, prior-context
    dev records) is reported separately and is never used as a statistic.
    """
    gid = manifest["generation_id"]
    member_list = list(manifest["strategy_ids"])
    members = set(member_list)
    manifest_ok, manifest_problems = _manifest_complete(manifest)

    first: dict[str, Mapping] = {}
    n_dev_cur: dict[str, int] = {}
    errored_cur: set[str] = set()
    other_ctx: dict[str, dict] = {}
    prior_final_pass: dict[str, dict] = {}
    unregistered: set[str] = set()
    for r in records:
        sid = r.get("strategy_id")
        ph = r.get("phase")
        if ph == "dev" and r.get("generation_id") == gid and sid not in members:
            unregistered.add(str(sid))
            continue
        if sid not in members:
            continue
        if ph == "final" and r.get("status") == "PASS":
            prior_final_pass[sid] = {"context_id": r.get("context_id"),
                                     "evaluation_id": r.get("evaluation_id"),
                                     "dev_evaluation_id": r.get("dev_evaluation_id")}
            continue
        if ph != "dev":
            continue
        if r.get("context_id") != context_id:
            if r.get("status") in DEV_OUTCOME_STATUSES and sid not in other_ctx:
                other_ctx[sid] = {"context_id": r.get("context_id"),
                                  "evaluation_id": r.get("evaluation_id"),
                                  "status": r.get("status")}
            continue
        if r.get("generation_id") != gid:
            continue
        if r.get("status") in DEV_OUTCOME_STATUSES:
            n_dev_cur[sid] = n_dev_cur.get(sid, 0) + 1
            first.setdefault(sid, r)
        elif r.get("status") == "ERROR":
            errored_cur.add(sid)

    pass_states = set(terminal_pass) & members
    pending = sorted(members - set(first))
    tested = {s: _finite_p(r.get("primary_dev_p")) for s, r in first.items()}
    with_stat = sorted(s for s, p in tested.items() if p is not None)
    without_stat = sorted(s for s, p in tested.items() if p is None)
    prior_pass_needing = {
        s: {**prior_final_pass.get(s, {}), "state": "PASS",
            "current_context_evaluation": None, "reevaluation_required": True}
        for s in sorted(pass_states - set(first))}
    prior_only = {s: other_ctx[s] for s in sorted(set(other_ctx) - set(first))}
    ready_current = not pending
    ready = manifest_ok and ready_current and not unregistered
    return {
        "generation_id": gid,
        "manifest_hash": manifest.get("manifest_hash"),
        "context_id": context_id,
        "m": len(members),
        # --- the four distinct notions -------------------------------------
        "manifest_complete": manifest_ok,
        "manifest_problems": manifest_problems,
        "current_context_complete": ready_current,
        "fdr_decision_ready": ready,
        "complete": ready,           # compat alias = fdr_decision_ready
        # --- population breakdown (every member lands in exactly one of the first three) --
        "evaluated": len(first),
        "n_with_statistic": len(with_stat),
        "n_without_statistic": len(without_stat),
        "without_statistic": without_stat,
        "unevaluated_current": pending,
        "missing": pending,          # compat alias = unevaluated_current
        "errored_current_only": sorted(errored_cur - set(first)),
        "prior_context_only": prior_only,
        "prior_context_pass": prior_pass_needing,
        "reevaluation_required": pending,
        "duplicate_dev_records": {s: n for s, n in sorted(n_dev_cur.items()) if n > 1},
        "unregistered_records": sorted(unregistered),
        "first_records": {s: r.get("evaluation_id") for s, r in first.items()},
        "pvalues": tested,
        "validation_verdicts": {s: r.get("validation_verdict") for s, r in first.items()},
    }


def fdr_input_hash(status: Mapping, q: float) -> str:
    """Hash of the immutable evidence a decision rests on."""
    ev = {"spec": FDR_INPUT_SPEC_VERSION, "method": FDR_METHOD, "q": q,
          "generation_id": status["generation_id"], "manifest_hash": status.get("manifest_hash"),
          "context_id": status["context_id"], "m": status["m"],
          "records": sorted([s, status["first_records"].get(s), status["pvalues"].get(s)]
                            for s in status["first_records"])}
    return hashlib.sha256(canonical_json(ev).encode("utf-8")).hexdigest()


def decide_family(status: Mapping, *, q: float = FDR_Q) -> dict:
    """The BH decision of a decision-ready family (raises if not ready)."""
    ready = status.get("fdr_decision_ready", status.get("complete"))
    if not ready or not status.get("complete", True):
        raise ValueError("FDR family not decision-ready — no decision may be made "
                         f"(manifest_complete={status.get('manifest_complete')}, "
                         f"current_context_complete={status.get('current_context_complete')})")
    sids = sorted(s for s, p in status["pvalues"].items() if p is not None)
    flags = bh_fdr_family([status["pvalues"][s] for s in sids], status["m"], q)
    survivors = sorted(s for s, ok in zip(sids, flags) if ok)
    vv = status.get("validation_verdicts", {})
    return {
        "generation_id": status["generation_id"],
        "manifest_hash": status.get("manifest_hash"),
        "context_id": status["context_id"],
        "method": FDR_METHOD,
        "input_spec": FDR_INPUT_SPEC_VERSION,
        "q": q,
        "m": status["m"],
        "n_with_statistic": len(sids),
        "n_without_statistic": status["m"] - len(sids),
        "without_statistic": sorted(status.get("without_statistic", [])),
        "survivors": survivors,
        "statistical_survivors": survivors,
        "survivors_validation_pass": sorted(s for s in survivors if vv.get(s) == "PASS"),
        "prior_context_pass_retained": sorted(status.get("prior_context_pass", {})),
        "first_records": dict(status["first_records"]),
        "input_hash": fdr_input_hash(status, q),
        "decided_at": iso_now(),
    }


class FdrDecisionStore:
    """Append-only FDR decisions; one per (generation, context), immutable.

    Re-recording the same evidence is idempotent.  Different evidence for an
    existing decision is a CONFLICT: the old decision stays untouched, the
    conflict is appended to ``<path>.conflicts.jsonl`` and ``FdrDecisionConflict``
    is raised.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.conflict_path = self.path.with_name(self.path.stem + ".conflicts.jsonl")

    def get(self, generation_id: str, context_id: str) -> Optional[dict]:
        for r in iter_jsonl(self.path):
            if r.get("generation_id") == generation_id and r.get("context_id") == context_id:
                return r
        return None

    def conflicts(self) -> list[dict]:
        return list(iter_jsonl(self.conflict_path))

    @staticmethod
    def _same_evidence(old: Mapping, new: Mapping) -> tuple[bool, str]:
        """Evidence equality is decided by the evidence hash ONLY.  A stored decision
        without ``input_hash`` (legacy) has no verifiable original evidence: equal
        result values do not prove equal inputs (the p-values may differ), so it is
        never accepted as 'the same' — the caller treats it as a conflict/hold."""
        if not old.get("input_hash"):
            return False, "stored decision has no input_hash: original evidence not verifiable"
        if not new.get("input_hash"):
            return False, "new decision has no input_hash"
        same = (old["input_hash"] == new["input_hash"]
                and old.get("manifest_hash") == new.get("manifest_hash")
                and old.get("method") == new.get("method") and old.get("q") == new.get("q"))
        return same, "" if same else "inputs differ from the stored decision"

    def record(self, decision: Mapping) -> dict:
        existing = self.get(decision["generation_id"], decision["context_id"])
        if existing is not None:
            same, why = self._same_evidence(existing, decision)
            if same:
                return existing
            append_jsonl(self.conflict_path, {"detected_at": iso_now(),
                                              "generation_id": decision["generation_id"],
                                              "context_id": decision["context_id"],
                                              "stored_input_hash": existing.get("input_hash"),
                                              "new_input_hash": decision.get("input_hash"),
                                              "stored_survivors": existing.get("survivors"),
                                              "new_survivors": decision.get("survivors"),
                                              "reason": why})
            raise FdrDecisionConflict(
                f"FDR decision for ({decision['generation_id']}, {decision['context_id']}) "
                "already exists and the new evidence differs — old decision kept, conflict logged")
        append_jsonl(self.path, dict(decision))
        return dict(decision)


class ContextStore:
    """Write-once normalized components of every experiment context."""

    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir) / "contexts"

    def ensure(self, context_id: str, components: Mapping, info: Optional[Mapping] = None) -> None:
        """``info`` is informational (git commit, ...) and is NOT part of the id."""
        p = self.dir / f"{context_id}.json"
        if p.exists():
            return
        atomic_write_json(p, {"context_id": context_id, "components": components,
                              "informational": dict(info or {}),
                              "recorded_at": iso_now()})

    def load(self, context_id: str) -> Optional[dict]:
        return load_json(self.dir / f"{context_id}.json", default=None)
