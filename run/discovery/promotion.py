"""Promotion gate (directive §15) — queue registration ≠ live deployment.

A candidate reaches the promotion queue only through the explicit gate
sequence below.  The gate returns SURVIVOR / SURVIVOR_COMBO / NOT_ELIGIBLE
with the failed-step list; NOTHING here ever announces a live promotion and
no code path connects to order execution (principle 9).

Residual fix P1-9: the gates now take EVIDENCE, not caller-supplied
booleans.  In particular a combo can only become SURVIVOR_COMBO from a real
portfolio-simulation result object whose context matches the candidate's
and whose pass criteria the gate re-evaluates itself; ``portfolio_passed=True``
on its own is rejected.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

from engine_lib import (
    CandidateState,
    iso_now,
    symbols_without_locked_test,
)

#: fields a portfolio result must carry to count as evidence (P1-9)
PORTFOLIO_EVIDENCE_FIELDS = ("result_id", "context_id", "model_version", "config",
                             "equity_curve", "final_equity", "initial_capital",
                             "max_drawdown", "trades", "liquidation_events",
                             "cost_status", "costs_applied", "funding_applied",
                             "margin_model", "skipped_same_symbol_conflict",
                             "skipped_capacity")


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
                   required_symbols: Sequence[str],
                   fdr_survived: Optional[bool] = None,
                   cost_verified: Optional[bool] = None,
                   portfolio_passed: Optional[bool] = None,
                   notes: Sequence[str] = ()) -> PromotionCheck:
    """The §15 gate, in code.

    - PASS + preregistration (membership of a verified generation manifest)
    - the generation's FINAL FDR decision must include the candidate
      (``fdr_survived`` None = not finalized → not eligible)
    - independent validation passed
    - the FINAL lock test must have been run exactly once and passed; a
      missing locked window leaves the candidate NO_LOCKED_TEST, never final
    - the cost model must be verified — an UNVERIFIED_COST_MODEL pass is a
      research result, not a promotable one
    - ``portfolio_passed`` is accepted only to REJECT it: combos go through
      :func:`combo_promotion_gate` with a portfolio result object
    """
    failed: list[str] = []
    notes_l = list(notes)
    if state != CandidateState.PASS.value:
        failed.append(f"candidate state is {state}, PASS required")
    if not preregistered:
        failed.append("preregistration missing")
    if fdr_survived is None:
        failed.append("generation FDR decision not final")
    elif not fdr_survived:
        failed.append("did not survive the generation BH-FDR")
    if not independent_validation_passed:
        failed.append("independent validation not passed")
    if lock_test_passed is None:
        no_lock = symbols_without_locked_test(required_symbols)
        if no_lock:
            notes_l.append(f"NO_LOCKED_TEST for {no_lock} — candidate cannot be final")
            failed.append("NO_LOCKED_TEST: " + ",".join(no_lock))
        else:
            failed.append("locked test not run")
    elif not lock_test_passed:
        failed.append("locked test did not pass (results must NOT be re-used)")
    if lock_test_used > 1:
        failed.append(
            f"locked test used {lock_test_used}x — no longer an independent final test"
        )
    elif lock_test_passed is not None and lock_test_used < 1:
        failed.append("locked test pass claimed but no registry use recorded")
    if cost_verified is not True:
        failed.append("cost model UNVERIFIED — not promotable")
    if portfolio_passed is not None:
        failed.append("boolean portfolio_passed is not evidence — use combo_promotion_gate "
                      "with a portfolio result")
    kind = "SURVIVOR" if not failed else "NOT_ELIGIBLE"
    return PromotionCheck(strategy_id="", eligible=not failed, kind=kind,
                          failed_steps=failed, notes=notes_l)


def validate_portfolio_evidence(result: Optional[Mapping], expected_context_id: Optional[str]) -> list[str]:
    """Failures of a portfolio result as promotion evidence (empty = valid)."""
    from portfolio import portfolio_passes
    if not result:
        return ["portfolio simulation result missing"]
    fails = [f"portfolio result lacks {k}" for k in PORTFOLIO_EVIDENCE_FIELDS if k not in result]
    if fails:
        return fails
    if not expected_context_id or result.get("context_id") != expected_context_id:
        fails.append(f"portfolio context {result.get('context_id')!r} != candidate context "
                     f"{expected_context_id!r}")
    if result.get("costs_applied") is not True:
        fails.append("portfolio ran without costs")
    if result.get("funding_applied") is not True:
        fails.append("portfolio ran without funding")
    if result.get("margin_model") != "isolated":
        fails.append("portfolio margin model unknown")
    cfg = result.get("config") or {}
    if not isinstance(cfg.get("max_concurrent"), int) or cfg.get("max_concurrent", 0) < 1:
        fails.append("portfolio has no concurrent-position limit")
    curve = result.get("equity_curve") or []
    if not curve or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in curve):
        fails.append("portfolio equity curve missing or non-finite")
    ok, why = portfolio_passes(result)
    if not ok:
        fails += [f"portfolio criteria: {w}" for w in why]
    return fails


def combo_promotion_gate(*, portfolio_result: Optional[Mapping] = None,
                         context_id: Optional[str] = None, **kw) -> PromotionCheck:
    """SURVIVOR_COMBO: statistics combination alone is never enough — a
    portfolio simulation with position/conflict/cost/funding/risk handling
    must have RUN under the candidate's context and pass (directive §7-3)."""
    if "portfolio_passed" in kw:
        kw.pop("portfolio_passed")
        bool_note = ["boolean portfolio_passed ignored — evidence required"]
    else:
        bool_note = []
    check = promotion_gate(**kw)
    pf_fails = validate_portfolio_evidence(portfolio_result, context_id)
    if pf_fails:
        check.failed_steps += pf_fails
        check.eligible = False
        check.kind = "NOT_ELIGIBLE"
    elif check.eligible:
        check.kind = "SURVIVOR_COMBO"
    check.notes = list(check.notes) + ["combo"] + bool_note
    if portfolio_result:
        check.notes.append(f"portfolio_result_id={portfolio_result.get('result_id')}")
    return check


def is_combo(defn: Mapping) -> bool:
    return defn.get("family") == "combo" or bool(defn.get("legs"))


class PromotionQueue:
    """Append-only promotion queue file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def register(self, check: PromotionCheck, strategy_id: str,
                 context_id: str, *, test_only: bool = False) -> dict:
        rec = {
            "strategy_id": strategy_id,
            "context_id": context_id,
            "kind": check.kind,
            "eligible": check.eligible,
            "failed_steps": check.failed_steps,
            "notes": check.notes,
            "ts": iso_now(),
            "live_deployed": False,
            "test_only": test_only,
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
