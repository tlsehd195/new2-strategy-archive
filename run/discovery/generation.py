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
dev outcome under the context (or is terminal PASS from an earlier
context).  Then Benjamini–Hochberg runs at q = 0.05 over m = |generation|:
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
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"generation_id": gid,
                                "manifest_hash": manifest["manifest_hash"],
                                "n_candidates": manifest["n_candidates"],
                                "family_counts": manifest["family_counts"],
                                "generator_version": manifest["generator_version"],
                                "previous_generation": prev,
                                "created_at": doc["preregistered_at"]},
                               sort_keys=True) + "\n")
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


def family_status(records: Iterable[Mapping], manifest: Mapping, context_id: str,
                  *, terminal_pass: Iterable[str] = ()) -> dict:
    """Completeness of the FDR family (generation, context).

    Uses the FIRST dev-phase record of each candidate under the context.
    """
    gid = manifest["generation_id"]
    members = set(manifest["strategy_ids"])
    first: dict[str, Mapping] = {}
    for r in records:
        if r.get("phase") != "dev" or r.get("generation_id") != gid \
                or r.get("context_id") != context_id:
            continue
        sid = r.get("strategy_id")
        if sid in members and sid not in first and r.get("status") in DEV_OUTCOME_STATUSES:
            first[sid] = r
    retained_pass = sorted(set(terminal_pass) & members - set(first))
    missing = sorted(members - set(first) - set(retained_pass))
    tested = {s: _finite_p(r.get("primary_dev_p")) for s, r in first.items()}
    return {
        "generation_id": gid,
        "context_id": context_id,
        "m": len(members),
        "evaluated": len(first),
        "retained_terminal_pass": retained_pass,
        "missing": missing,
        "complete": not missing,
        "first_records": {s: r.get("evaluation_id") for s, r in first.items()},
        "pvalues": tested,
    }


def decide_family(status: Mapping, *, q: float = FDR_Q) -> dict:
    """The BH decision of a COMPLETE family (raises if incomplete)."""
    if not status["complete"]:
        raise ValueError("FDR family incomplete — no decision may be made")
    sids = sorted(s for s, p in status["pvalues"].items() if p is not None)
    flags = bh_fdr_family([status["pvalues"][s] for s in sids], status["m"], q)
    survivors = sorted(s for s, ok in zip(sids, flags) if ok)
    return {
        "generation_id": status["generation_id"],
        "context_id": status["context_id"],
        "method": FDR_METHOD,
        "q": q,
        "m": status["m"],
        "n_with_statistic": len(sids),
        "n_without_statistic": status["m"] - len(sids),
        "survivors": survivors,
        "first_records": status["first_records"],
        "decided_at": iso_now(),
    }


class FdrDecisionStore:
    """Append-only FDR decisions; one per (generation, context), immutable."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def get(self, generation_id: str, context_id: str) -> Optional[dict]:
        for r in iter_jsonl(self.path):
            if r.get("generation_id") == generation_id and r.get("context_id") == context_id:
                return r
        return None

    def record(self, decision: Mapping) -> dict:
        existing = self.get(decision["generation_id"], decision["context_id"])
        if existing is not None:
            return existing   # never recomputed, never overwritten
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(decision), sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return dict(decision)


class ContextStore:
    """Write-once normalized components of every experiment context."""

    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir) / "contexts"

    def ensure(self, context_id: str, components: Mapping) -> None:
        p = self.dir / f"{context_id}.json"
        if p.exists():
            return
        atomic_write_json(p, {"context_id": context_id, "components": components,
                              "recorded_at": iso_now()})

    def load(self, context_id: str) -> Optional[dict]:
        return load_json(self.dir / f"{context_id}.json", default=None)
