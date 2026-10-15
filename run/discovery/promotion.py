"""Promotion gate (directive §15) — queue registration ≠ live deployment.

A candidate reaches the promotion queue only through the explicit gate
sequence below.  The gate returns SURVIVOR / SURVIVOR_COMBO / NOT_ELIGIBLE
with the failed-step list; NOTHING here ever announces a live promotion and
no code path connects to order execution (principle 9).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

from engine_lib import (
    CandidateState,
    LockTestRegistry,
    atomic_write_json,
    iso_now,
    locked_window_for,
    symbols_without_locked_test,
)


@dataclass
class PromotionCheck:
    strategy_id: str
    eligible: bool
    kind: str = "NOT_ELIGIBLE"          # SURVIVOR | SURVIVOR_COMBO | NOT_ELIGIBLE
    failed_steps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return dict(self.__dict__)


def promotion_gate(*, state: str, preregistered: bool,
                   independent_validation_passed: bool,
                   lock_test_passed: Optional[bool],
                   lock_test_used: int,
                   portfolio_passed: Optional[bool] = None,
                   required_symbols: Sequence[str],
                   notes: Sequence[str] = ()) -> PromotionCheck:
    """The §15 gate, in code.

    - queue registration requires PASS + preregistration + independent
      validation
    - the FINAL lock test must have been run exactly once; a missing locked
      window leaves the candidate in the queue as NO_LOCKED_TEST, never as
      deployable
    - combo candidates additionally need a portfolio backtest pass; a
      combined-statistics pass alone (old SURVIVOR_COMBO) is NOT enough
    """
    failed: list[str] = []
    kind = "NOT_ELIGIBLE"
    if state != CandidateState.PASS.value:
        failed.append(f"candidate state is {state}, PASS required")
    if not preregistered:
        failed.append("preregistration missing")
    if not independent_validation_passed:
        failed.append("independent validation not passed")
    if lock_test_passed is None:
        no_lock = symbols_without_locked_test(required_symbols)
        if no_lock:
            notes_local = list(notes) + [
                f"NO_LOCKED_TEST for {no_lock} — candidate cannot be final"
            ]
            return PromotionCheck(
                strategy_id="", eligible=False, kind="NOT_ELIGIBLE",
                failed_steps=failed + ["NO_LOCKED_TEST: " + ",".join(no_lock)],
                notes=notes_local,
            )
        failed.append("locked test not run")
    elif not lock_test_passed:
        failed.append("locked test did not pass (results must NOT be re-used)")
    if lock_test_used > 1:
        failed.append(
            f"locked test used {lock_test_used}x — no longer an independent final test"
        )
    if not failed:
        kind = "SURVIVOR"
    if failed and "combo" in (notes or ()):
        kind = "NOT_ELIGIBLE"
    return PromotionCheck(strategy_id="", eligible=not failed, kind=kind,
                          failed_steps=failed, notes=list(notes))


def combo_promotion_gate(**kw) -> PromotionCheck:
    """SURVIVOR_COMBO: statistics combination alone is never enough — a
    portfolio simulation with position/conflict/cost/funding/risk handling
    must pass too (directive §7-3)."""
    portfolio_passed = kw.pop("portfolio_passed", None)
    check = promotion_gate(**kw)
    if portfolio_passed is not True:
        check.eligible = False
        check.kind = "NOT_ELIGIBLE"
        if "portfolio_passed" not in check.failed_steps:
            check.failed_steps.append(
                "SURVIVOR_COMBO requires a portfolio backtest pass, not a "
                "combined statistic"
            )
    elif check.eligible:
        check.kind = "SURVIVOR_COMBO"
    check.notes = list(check.notes) + ["combo"]
    return check


class PromotionQueue:
    """Append-only promotion queue file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def register(self, check: PromotionCheck, strategy_id: str,
                 context_id: str) -> dict:
        rec = {
            "strategy_id": strategy_id,
            "context_id": context_id,
            "kind": check.kind,
            "eligible": check.eligible,
            "failed_steps": check.failed_steps,
            "notes": check.notes,
            "ts": iso_now(),
            "live_deployed": False,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def duplicates(self, strategy_id: str) -> int:
        return sum(1 for e in self.entries() if e["strategy_id"] == strategy_id)
