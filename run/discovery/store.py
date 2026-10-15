"""State store, reverify queue and ledger reconciliation (directive §3)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional

from engine_lib import (
    CandidateState,
    InvalidTransition,
    LEDGER_SCHEMA,
    RetryRecord,
    STATE_SCHEMA,
    assert_transition,
    atomic_write_json,
    iso_now,
    iter_jsonl,
    load_json,
    reverify_eligible,
)


class StoreCorruption(RuntimeError):
    pass


@dataclass
class CandidateEntry:
    state: str = CandidateState.PENDING.value
    updated_at: str = ""
    retry: RetryRecord = field(default_factory=RetryRecord)

    def to_json(self) -> dict:
        return {"state": self.state, "updated_at": self.updated_at,
                "retry": self.retry.to_json()}


class CandidateStore:
    """state.json wrapper — every write is atomic, every transition checked."""

    def __init__(self, path: Path):
        self.path = Path(path)
        import json as _json
        try:
            raw = load_json(self.path, default=None)
        except _json.JSONDecodeError as e:
            raise StoreCorruption(f"{self.path} is not valid JSON: {e}") from e
        if raw is None:
            self._data: dict = {"schema": STATE_SCHEMA, "candidates": {}}
        else:
            if not isinstance(raw, dict) or "candidates" not in raw:
                raise StoreCorruption(f"{self.path} is not a valid state file")
            if raw.get("schema") != STATE_SCHEMA:
                raise StoreCorruption(
                    f"{self.path} schema {raw.get('schema')!r} != {STATE_SCHEMA}"
                )
            self._data = raw

    # -- basic access ------------------------------------------------------
    def entry(self, strategy_id: str) -> CandidateEntry:
        raw = self._data["candidates"].get(strategy_id)
        if raw is None:
            return CandidateEntry(updated_at=iso_now())
        retry = RetryRecord(**raw.get("retry", {}))
        return CandidateEntry(state=raw["state"], updated_at=raw.get("updated_at", ""),
                              retry=retry)

    def set(self, strategy_id: str, nxt: CandidateState, **retry_kw) -> None:
        cur = self.entry(strategy_id)
        nxt_state = nxt if isinstance(nxt, CandidateState) else CandidateState(nxt)
        assert_transition(CandidateState(cur.state), nxt_state)
        e = CandidateEntry(state=nxt_state.value, updated_at=iso_now(), retry=cur.retry)
        for k, v in retry_kw.items():
            setattr(e.retry, k, v)
        if nxt_state is CandidateState.RUNNING:
            e.retry.attempt_count += 1
            e.retry.last_attempt_at = iso_now()
        self._data["candidates"][strategy_id] = e.to_json()
        self.save()

    def save(self) -> None:
        atomic_write_json(self.path, self._data)

    def all_states(self) -> dict[str, str]:
        return {k: v["state"] for k, v in self._data["candidates"].items()}

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for st in self.all_states().values():
            out[st] = out.get(st, 0) + 1
        return out


class ReverifyQueue:
    """JSON-file queue for re-verifications (directive §3-2/§3-3).

    The queue NEVER invents past evaluation results — it only schedules
    work; the ledger stays the authoritative record.  Every candidate that
    enters the queue must already exist in the store.
    """

    def __init__(self, path: Path, store: CandidateStore):
        self.path = Path(path)
        self.store = store
        raw = load_json(self.path, default=None)
        self._items: list[dict] = raw.get("items", []) if isinstance(raw, dict) else []

    def save(self) -> None:
        atomic_write_json(self.path, {"schema": STATE_SCHEMA, "items": self._items})

    def enqueue(self, strategy_id: str, reason: str) -> bool:
        """Queue a re-verification after the central policy approves it."""
        e = self.store.entry(strategy_id)
        eligible, why = reverify_eligible(
            CandidateState(e.state),
            error_code=e.retry.error_code,
            attempt_count=e.retry.attempt_count,
            max_attempts=e.retry.max_attempts,
            cost_model_version_changed=reason == "cost_model_version_changed",
            new_data_beyond_dev=reason == "new_data_beyond_dev_window",
            validation_design_changed=reason == "validation_design_changed",
            data_now_available=reason == "data_refresh_check",
            power_condition_met=reason == "power_condition_met",
        )
        if not eligible:
            return False
        e.retry.reverify_reason = why
        self.store.set(strategy_id, CandidateState.REVERIFY_PENDING,
                       reverify_reason=why)
        if not any(i["strategy_id"] == strategy_id for i in self._items):
            self._items.append({
                "strategy_id": strategy_id,
                "reason": why,
                "queued_at": iso_now(),
            })
            self.save()
        return True

    def pop_ready(self, now_ts: float | None = None) -> Optional[dict]:
        """Pop the next item whose backoff window has elapsed, promoting it
        to PENDING so the runner may pick it up."""
        now = now_ts or __import__("time").time()
        for i, item in enumerate(self._items):
            nxt = item.get("next_retry_after_ts", 0)
            if now >= float(nxt or 0):
                self._items.pop(i)
                self.save()
                # REVERIFY_PENDING -> PENDING is the only legal promotion
                cur = self.store.entry(item["strategy_id"])
                if CandidateState(cur.state) is CandidateState.REVERIFY_PENDING:
                    self.store.set(item["strategy_id"], CandidateState.PENDING)
                return item
        return None

    def requeue_with_backoff(self, strategy_id: str, seconds: float) -> None:
        """Attach/refresh the backoff timestamp on the queued item."""
        for item in self._items:
            if item["strategy_id"] == strategy_id:
                item["next_retry_after_ts"] = __import__("time").time() + seconds
                item["reason"] = item.get("reason") or "retry_backoff"
                self.save()
                return
        self._items.append({
            "strategy_id": strategy_id,
            "reason": "retry_backoff",
            "queued_at": iso_now(),
            "next_retry_after_ts": __import__("time").time() + seconds,
        })
        self.save()

    def size(self) -> int:
        return len(self._items)

    def items(self) -> list[dict]:
        return list(self._items)


class Ledger:
    """Append-only evaluation ledger (JSONL) + read-side reconciliation."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, record: Mapping) -> None:
        if "evaluation_id" not in record or "strategy_id" not in record:
            raise ValueError("ledger record requires evaluation_id and strategy_id")
        rec = dict(record)
        rec.setdefault("schema", LEDGER_SCHEMA)
        rec.setdefault("ts", iso_now())
        from engine_lib import append_jsonl
        append_jsonl(self.path, rec)

    def records(self) -> Iterable[dict]:
        return iter_jsonl(self.path)

    def latest_by_strategy(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for rec in self.records():
            out[rec["strategy_id"]] = rec  # later lines win
        return out

    def evaluation_count(self) -> int:
        return sum(1 for _ in self.records())

    def distinct_strategies(self) -> int:
        return len(self.latest_by_strategy())

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rec in self.latest_by_strategy().values():
            st = rec.get("status", "UNKNOWN")
            counts[st] = counts.get(st, 0) + 1
        return counts

    def duplicate_strategy_records(self) -> list[str]:
        """Strategy ids with more than one evaluation record (history kept)."""
        seen: dict[str, int] = {}
        for rec in self.records():
            seen[rec["strategy_id"]] = seen.get(rec["strategy_id"], 0) + 1
        return sorted(s for s, c in seen.items() if c > 1)


LEDGER_TO_STATE = {
    "PASS": CandidateState.PASS,
    "FAIL_COST": CandidateState.FAIL_COST,
    "FAIL_STAT": CandidateState.FAIL_STAT,
    "UNDETERMINED_POWER": CandidateState.UNDETERMINED_POWER,
    "DATA_MISSING": CandidateState.DATA_MISSING,
    "ERROR": CandidateState.ERROR,
}


def reconcile(store: CandidateStore, queue: ReverifyQueue, ledger: Ledger,
              *, now_ts: float | None = None) -> dict:
    """Crash/restart recovery (directive §3-3, §13).

    Rules:
    - a candidate RUNNING in state.json with NO matching RUNNING ledger
      record was interrupted → transient → REVERIFY_PENDING (backoff)
    - ledger is authoritative for the final state of finished evaluations;
      state.json is only corrected *forward* via legal transitions
    - queue items whose candidate no longer exists are dropped
    - queue items whose candidate sits in a re-verifiable non-final state
      are REALIGNED: the candidate moves to REVERIFY_PENDING so the queue
      pop can actually promote it (A-35: stale entries otherwise pop into
      nothing and are re-counted forever)
    - queue items whose candidate is final (PASS) or already in flight
      (RUNNING) are dropped — the queue must never resurrect them
    """
    now = now_ts or __import__("time").time()
    report = {"requeued_interrupted": [], "dropped_queue_items": [],
              "state_repaired": [], "corrupt_ledger_lines": 0,
              "queue_realigned": []}

    running_in_ledger: set[str] = set()
    for rec in ledger.records():
        if rec.get("status") == "RUNNING":
            running_in_ledger.add(rec["strategy_id"])

    re_verifiable = {
        CandidateState.DATA_MISSING,
        CandidateState.UNDETERMINED_POWER,
        CandidateState.ERROR,
        CandidateState.FAIL_COST,
        CandidateState.FAIL_STAT,
    }

    for sid, st in list(store.all_states().items()):
        e = store.entry(sid)
        if e.state == CandidateState.RUNNING.value and sid not in running_in_ledger:
            # interrupted before any ledger record — transient retry
            e.retry.error_code = e.retry.error_code or "PROCESS_INTERRUPTED"
            store.set(sid, CandidateState.REVERIFY_PENDING,
                      error_code=e.retry.error_code,
                      error_message="interrupted with no ledger record")
            queue.requeue_with_backoff(sid, 60)
            report["requeued_interrupted"].append(sid)
        elif e.state == CandidateState.RUNNING.value and sid in running_in_ledger:
            report["state_repaired"].append(sid)  # legit running — leave as is

    keep = []
    for item in queue.items():
        sid = item.get("strategy_id")
        if sid not in store.all_states():
            report["dropped_queue_items"].append(sid)
            continue
        cur = CandidateState(store.entry(sid).state)
        if cur in re_verifiable:
            # A-35: align the store forward into REVERIFY_PENDING so the
            # queue pop can promote this candidate to PENDING and re-run it
            if cur is not CandidateState.REVERIFY_PENDING:
                store.set(sid, CandidateState.REVERIFY_PENDING,
                          reverify_reason=item.get("reason") or "queue_realign")
                report["queue_realigned"].append(sid)
            keep.append(item)
        elif cur is CandidateState.REVERIFY_PENDING or cur is CandidateState.PENDING:
            keep.append(item)
        else:
            # PASS (final) or RUNNING (in flight): queue must not resurrect
            report["dropped_queue_items"].append(sid)
            continue
    if len(keep) != len(queue.items()):
        queue._items = keep
        queue.save()
    return report
