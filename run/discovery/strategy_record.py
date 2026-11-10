"""One standard record for every strategy, engine or independent (ADR 0066).

Directive 2026-11-10 §3: strategies found by the evolve engine and by the
independent ``run/*.py`` research tools are described with the same fields, so
they can be listed, compared and de-duplicated in one place.  The record is a
description, never evidence: the evaluation and validation statuses are copied
from the source (status ledger or ADR verdict) and an independent tool's
verdict never becomes an engine status.

Fields (all present, ``None`` when not applicable):

``strategy_id, family, version, spec, symbols, timeframe, entry, exit,
filters, risk, required_features, data_requirements, cost_model,
source_script, lineage, evaluation_status, validation_status, dedup_key``

Dedup rule: two records are the same strategy when their ``dedup_key`` is
equal.  Engine specs use ``evolution.behaviour_key`` (exact behavioural
equivalence); records without an engine spec hash family, timeframe,
universe, entry and exit text.  The first record (engine before independent,
then by source order) is the representative; the others are listed with the
reason.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping, Optional

RECORD_VERSION = "strategy-record-v1"
FIELDS = ("strategy_id", "family", "version", "spec", "symbols", "timeframe", "entry", "exit",
          "filters", "risk", "required_features", "data_requirements", "cost_model",
          "source_script", "lineage", "evaluation_status", "validation_status", "dedup_key")
#: evaluation statuses an independent tool may report (its ADR verdict)
RESEARCH_VERDICTS = ("NOT_PROMISING", "NOT_USEFUL", "NOT_STABLE", "PROMISING_DEV_ONLY",
                     "DATA_MISSING", "NOT_EVALUATED", "UNDETERMINED_POWER")
#: validation statuses of a record; only the engine's ledgers can say more than NOT_VALIDATED
VALIDATION_STATUSES = ("NOT_VALIDATED", "VALIDATION_SURVIVOR", "PAPER_TEST_READY", "REJECTED",
                       "INCONCLUSIVE", "BLOCKED_DATA", "BLOCKED_COST", "BLOCKED_STATISTICS",
                       "RESEARCH_ONLY", "WINDOW_RETIRED")


class RecordError(ValueError):
    pass


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def from_engine_spec(spec: Mapping, *, strategy_id: str, source_script: str = "run/discovery/pipeline.py",
                     lineage: Optional[Mapping] = None, evaluation_status: Optional[str] = None,
                     validation_status: Optional[str] = None, cost_model: Optional[Mapping] = None) -> dict:
    """Record of a registered engine strategy (canonical spec)."""
    from evolution import behaviour_key
    from strategy_spec import FEATURES, SPEC_REGISTRY_VERSION, canonicalize_spec
    s = canonicalize_spec(spec)
    conds = s["entry"]["conditions"] + s["filters"]
    feats = sorted({c["feature"] for c in conds})
    data = sorted({d for f in feats for d in FEATURES[f].data})
    rec = {"strategy_id": strategy_id, "family": s.get("family") or "+".join(
               sorted({FEATURES[f].kind for f in feats})),
           "version": SPEC_REGISTRY_VERSION, "spec": s, "symbols": list(s["symbols"]),
           "timeframe": s.get("timeframe", "15m"),
           "entry": {"direction": s["direction"], "conditions": s["entry"]["conditions"]},
           "exit": s["exit"], "filters": s["filters"], "risk": s["risk"],
           "required_features": feats, "data_requirements": data, "cost_model": cost_model,
           "source_script": source_script, "lineage": dict(lineage) if lineage else None,
           "evaluation_status": evaluation_status, "validation_status": validation_status or "NOT_VALIDATED",
           "dedup_key": "bk:" + behaviour_key(s)}
    return validate(rec)


def research_record(*, family: str, source_script: str, symbols: Iterable[str] | str, timeframe: str,
                    entry: str, exit: str, data_requirements: Iterable[str], evaluation_status: str,
                    adr: str, version: str = "1", filters: Optional[str] = None,
                    risk: Optional[str] = None, required_features: Iterable[str] = (),
                    cost_model: Optional[Mapping] = None) -> dict:
    """Record of a strategy studied by an independent tool (no engine spec)."""
    syms = symbols if isinstance(symbols, str) else sorted(symbols)
    body = {"family": family, "timeframe": timeframe, "symbols": syms,
            "entry": " ".join(entry.lower().split()), "exit": " ".join(exit.lower().split())}
    key = "rk:" + _h(body)
    rec = {"strategy_id": "R-" + _h({**body, "source": source_script})[:12], "family": family,
           "version": version, "spec": None, "symbols": syms, "timeframe": timeframe,
           "entry": {"text": entry}, "exit": {"text": exit}, "filters": filters, "risk": risk,
           "required_features": list(required_features), "data_requirements": sorted(data_requirements),
           "cost_model": cost_model, "source_script": source_script, "lineage": {"adr": adr},
           "evaluation_status": evaluation_status, "validation_status": "NOT_VALIDATED", "dedup_key": key}
    return validate(rec)


def validate(rec: Mapping) -> dict:
    missing = [f for f in FIELDS if f not in rec]
    extra = [k for k in rec if k not in FIELDS and k != "record_version"]
    if missing or extra:
        raise RecordError(f"record fields: missing {missing}, unknown {extra}")
    if rec["spec"] is None:
        if rec["evaluation_status"] not in RESEARCH_VERDICTS:
            raise RecordError(f"independent record: evaluation_status {rec['evaluation_status']!r} "
                              f"not in {RESEARCH_VERDICTS}")
        if rec["validation_status"] != "NOT_VALIDATED":
            # an independent tool never opens a validation window
            raise RecordError("independent record cannot carry a validation status")
    elif rec["validation_status"] not in VALIDATION_STATUSES:
        raise RecordError(f"validation_status {rec['validation_status']!r} unknown")
    return {**{f: rec[f] for f in FIELDS}, "record_version": rec.get("record_version", RECORD_VERSION)}


def dedup(records: Iterable[Mapping]) -> tuple[list, list]:
    """(representatives, duplicates); engine records first, then source order."""
    recs = list(records)
    order = sorted(range(len(recs)), key=lambda i: (recs[i]["spec"] is None, i))
    seen: dict = {}
    reps, dups = [], []
    for i in order:
        r = recs[i]
        k = r["dedup_key"]
        if k in seen:
            dups.append({"strategy_id": r["strategy_id"], "duplicate_of": seen[k],
                         "source_script": r["source_script"], "reason": f"same dedup_key {k}"})
        else:
            seen[k] = r["strategy_id"]
            reps.append(r)
    return reps, dups
