"""Independent-validation run registry (ADR 0028).

One append-only, hash-chained ledger (``validation_registry.jsonl``) that
links a frozen candidate set to an independent validation run and to its
result, so the flow

    candidates -> DEV -> dedup -> freeze -> REGISTERED -> STARTED -> COMPLETED
                                                                 \\-> ABORTED / INVALIDATED

is recorded in one place and replayable.  The DEV statuses of
``pipeline.StatusStore`` are never touched: the outcome of an independent run
is stored here, under ``stage = "independent"``, and reports keep the two
apart (a DEV pass is never counted as an independent pass).

Rules (each one enforced, each one tested):

- a run id is registered once; registering the same body again is a no-op,
  a different body under the same id is refused (no silent overwrite);
- ``start`` needs a registration, ``complete`` needs a start; a second
  ``complete`` is refused — a result is never replaced;
- a run that was started and never completed stays ``RUNNING`` and is
  reported as incomplete; it never counts as a completed validation;
- the registration pins the members (id + sha256 of the canonical spec),
  the preregistration, the code identity and the configuration; if any of
  them differs at start or completion the call is refused, and
  :func:`member_drift` lets a caller invalidate a run whose candidates
  changed after registration;
- a line that is not JSON, a record whose hash does not match its content
  or a broken chain makes every read fail (``RegistryCorrupt``): the state
  is not guessed around a damaged ledger.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Optional, Sequence

from engine_lib import append_jsonl, canonical_json, iso_now

SCHEMA = "validation_registry_v1"
EVENTS = ("REGISTERED", "STARTED", "COMPLETED", "ABORTED", "INVALIDATED")
#: replayed state of a run
STATES = ("REGISTERED", "RUNNING", "COMPLETED", "ABORTED", "INVALIDATED")
#: outcome vocabulary of one member in one completed run
MEMBER_OUTCOMES = ("PASSED_INDEPENDENT_WINDOW", "REJECTED", "INCONCLUSIVE")
#: the fields a registration must carry (all of them pin what is tested)
REGISTRATION_FIELDS = ("window_key", "prereg_sha256", "members_sha256", "members",
                       "code_hash", "config", "cost")
GENESIS = "0" * 64


class RegistryCorrupt(RuntimeError):
    """The ledger cannot be trusted (bad JSON, tampered record, broken chain)."""


class RegistryRefused(RuntimeError):
    """The requested transition is not allowed."""


def spec_sha256(spec: Mapping) -> str:
    from strategy_spec import canonicalize_spec
    return hashlib.sha256(canonical_json(canonicalize_spec(spec)).encode()).hexdigest()


def members_pin(members: Sequence[Mapping]) -> dict:
    """{strategy id: sha256 of the canonical spec} of a members list."""
    return {m["strategy_id"]: spec_sha256(m["spec"]) for m in members}


def _rec_sha(rec: Mapping) -> str:
    body = {k: v for k, v in rec.items() if k != "record_sha256"}
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


class ValidationRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)

    # -- reading -------------------------------------------------------------
    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out, prev = [], GENESIS
        for i, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError as e:
                raise RegistryCorrupt(f"{self.path}:{i}: not JSON ({e})") from e
            if not isinstance(r, dict) or r.get("schema") != SCHEMA or r.get("event") not in EVENTS:
                raise RegistryCorrupt(f"{self.path}:{i}: not a {SCHEMA} record")
            if r.get("record_sha256") != _rec_sha(r):
                raise RegistryCorrupt(f"{self.path}:{i}: record hash does not match its content")
            if r.get("prev_sha256") != prev:
                raise RegistryCorrupt(f"{self.path}:{i}: hash chain broken")
            prev = r["record_sha256"]
            out.append(r)
        return out

    def runs(self) -> dict:
        """{run id: {state, registration, started, completed, aborted, invalidated}}."""
        runs: dict = {}
        for r in self.records():
            run = runs.setdefault(r["run_id"], {"state": None, "events": []})
            run["events"].append(r["event"])
            ev = r["event"]
            if ev == "REGISTERED":
                run.update(state="REGISTERED", registration=r)
            elif ev == "STARTED":
                run.update(state="RUNNING", started=r)
            elif ev == "COMPLETED":
                run.update(state="COMPLETED", completed=r)
            elif ev == "ABORTED":
                run.update(state="ABORTED", aborted=r)
            elif ev == "INVALIDATED":
                run.update(state="INVALIDATED", invalidated=r)
        return runs

    def state(self, run_id: str) -> Optional[str]:
        return (self.runs().get(run_id) or {}).get("state")

    # -- writing -------------------------------------------------------------
    def _append(self, run_id: str, event: str, body: Mapping) -> dict:
        recs = self.records()
        rec = {"schema": SCHEMA, "run_id": run_id, "event": event, **dict(body),
               "at": iso_now(), "prev_sha256": recs[-1]["record_sha256"] if recs else GENESIS}
        rec["record_sha256"] = _rec_sha(rec)
        append_jsonl(self.path, rec)
        return rec

    def register(self, run_id: str, **reg) -> dict:
        missing = [k for k in REGISTRATION_FIELDS if reg.get(k) in (None, "", {}, [])]
        if missing:
            raise RegistryRefused(f"{run_id}: registration lacks {missing}")
        body = {k: reg[k] for k in sorted(reg)}
        run = self.runs().get(run_id)
        if run is not None:
            old = {k: v for k, v in run["registration"].items()
                   if k not in ("schema", "run_id", "event", "at", "prev_sha256", "record_sha256")}
            if canonical_json(old) != canonical_json(body):
                raise RegistryRefused(f"{run_id}: already registered with a different body — "
                                      "a registration is never replaced")
            return run["registration"]
        return self._append(run_id, "REGISTERED", body)

    def _need(self, run_id: str, allowed: Sequence[str]) -> dict:
        run = self.runs().get(run_id)
        st = (run or {}).get("state")
        if st not in allowed:
            raise RegistryRefused(f"{run_id}: state {st} (needs one of {list(allowed)})")
        return run

    def _pin_check(self, run: Mapping, *, members_sha256: str, code_hash: str) -> None:
        reg = run["registration"]
        why = []
        if members_sha256 != reg["members_sha256"]:
            why.append("members changed after registration")
        if code_hash != reg["code_hash"]:
            why.append("evaluation code changed after registration")
        if why:
            raise RegistryRefused(f"{reg['run_id']}: " + "; ".join(why) + " — invalidate and re-register")

    def start(self, run_id: str, *, members_sha256: str, code_hash: str) -> dict:
        run = self._need(run_id, ("REGISTERED",))
        self._pin_check(run, members_sha256=members_sha256, code_hash=code_hash)
        return self._append(run_id, "STARTED", {"members_sha256": members_sha256,
                                                "code_hash": code_hash})

    def complete(self, run_id: str, *, members_sha256: str, code_hash: str, result_sha256: str,
                 outcomes: Mapping[str, str], result_path: str = "") -> dict:
        run = self._need(run_id, ("RUNNING",))
        self._pin_check(run, members_sha256=members_sha256, code_hash=code_hash)
        pinned = set(run["registration"]["members"])
        if set(outcomes) != pinned:
            raise RegistryRefused(f"{run_id}: outcomes for {sorted(set(outcomes) ^ pinned)} do not "
                                  "match the registered members")
        bad = sorted(o for o in outcomes.values() if o not in MEMBER_OUTCOMES)
        if bad:
            raise RegistryRefused(f"{run_id}: unknown outcome(s) {bad}")
        return self._append(run_id, "COMPLETED", {"result_sha256": result_sha256,
                                                  "outcomes": dict(sorted(outcomes.items())),
                                                  "result_path": result_path,
                                                  "stage": "independent"})

    def abort(self, run_id: str, reason: str) -> dict:
        self._need(run_id, ("REGISTERED", "RUNNING"))
        return self._append(run_id, "ABORTED", {"reason": reason})

    def invalidate(self, run_id: str, reason: str) -> dict:
        """A run whose candidates or code changed: its result (if any) is no
        longer evidence for the current definitions (re-validation needed)."""
        self._need(run_id, ("REGISTERED", "RUNNING", "COMPLETED"))
        return self._append(run_id, "INVALIDATED", {"reason": reason})

    # -- derived views -------------------------------------------------------
    def member_drift(self, run_id: str, members: Sequence[Mapping]) -> list[str]:
        """Differences between the registered pins and ``members`` now."""
        run = self.runs().get(run_id)
        if run is None:
            return [f"{run_id} not registered"]
        pinned = run["registration"]["members"]
        now = members_pin(members)
        out = [f"{s}: removed" for s in sorted(set(pinned) - set(now))]
        out += [f"{s}: added" for s in sorted(set(now) - set(pinned))]
        out += [f"{s}: definition changed" for s in sorted(set(now) & set(pinned)) if now[s] != pinned[s]]
        return out

    def independent_outcomes(self) -> dict:
        """{strategy id: [{run_id, window_key, outcome}]} of COMPLETED runs only
        (invalidated, aborted and running runs contribute nothing)."""
        out: dict = {}
        for rid, run in sorted(self.runs().items()):
            if run["state"] != "COMPLETED":
                continue
            for sid, o in run["completed"]["outcomes"].items():
                out.setdefault(sid, []).append({"run_id": rid, "outcome": o,
                                                "window_key": run["registration"]["window_key"]})
        return out

    def summary(self) -> dict:
        runs = self.runs()
        by_state: dict = {}
        for r in runs.values():
            by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        outcomes: dict = {}
        for lst in self.independent_outcomes().values():
            for x in lst:
                outcomes[x["outcome"]] = outcomes.get(x["outcome"], 0) + 1
        return {"runs": len(runs), "by_state": by_state,
                "completed_members": sum(outcomes.values()), "outcomes": outcomes,
                "incomplete_runs": sorted(k for k, r in runs.items() if r["state"] == "RUNNING"),
                "independent_passes": sorted(s for s, lst in self.independent_outcomes().items()
                                             if any(x["outcome"] == "PASSED_INDEPENDENT_WINDOW" for x in lst))}
