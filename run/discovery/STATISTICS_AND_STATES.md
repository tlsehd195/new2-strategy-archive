# Statistics, states and accounting — discovery engine v2.1

This document states what the engine's numbers mean and, just as
importantly, what they do NOT guarantee.  It reflects the code as of the
2026-10-15 residual-defect fixes.

## 1. Test units

| unit | definition | code |
|---|---|---|
| hypothesis | one candidate strategy: "the primary cell (primary symbol, preregistered horizon) has positive mean net return on dev data" | `runner._evaluate` |
| cell test | one-sided Newey–West HAC t-test on NON-overlapping event net returns (costs + funding included); n < 30 → `UNDETERMINED_POWER` | `evaluator.cell_stats` |
| symbol verdict | Holm within the symbol's cell family | `engine_lib._symbol_status` |
| candidate dev verdict | PASS only if EVERY required symbol is PASS | `engine_lib.verdict_from_cells` |
| search generation | the complete preregistered candidate space; id = hash(sorted ids + normalized definitions + generator version + FDR spec) | `generation.build_manifest` |
| FDR family | (generation, experiment context); m = number of candidates in the generation | `generation.family_status` |

## 2. Cross-candidate multiple testing (P0-1, final-residual §3/§5)

### 2.1 What is tested

- **p-value**: the one-sided Newey–West HAC t-test p-value of the candidate's
  *primary cell* (primary symbol, pre-registered horizon) on dev data, from
  non-overlapping event net returns (costs and funding included).  The normal
  approximation is used; n < 30 events gives no p-value (`UNDETERMINED_POWER`).
- **Hypothesis**: "the primary cell has positive mean net return on dev data".
- **m** = the number of candidates in the preregistered generation manifest
  (all of them, evaluated or not).  Nobody is removed from m after the fact.
- **Tested value** = the first dev-phase ledger record of the candidate *under
  the current experiment context*.  Re-evaluations stay as history; they cannot
  replace it.

### 2.2 Four different questions (the old single `complete` flag mixed them)

`generation.family_status()` returns them separately:

| field | question |
|---|---|
| `manifest_complete` | is the preregistered list whole, free of duplicate ids and consistent with its stored hash? |
| `current_context_complete` | does EVERY member have a dev outcome produced **under this context**? |
| `fdr_decision_ready` | both of the above, and no dev record for an unregistered id |
| `locked_test_eligible` | (in the decision) BH survivors whose independent validation also passed |
| `complete` | compatibility alias of `fdr_decision_ready`; internal logic uses the specific fields |

Population breakdown reported next to them: `unevaluated_current`,
`without_statistic` (evaluated, but no valid p — recorded as such, never 1.0,
never 0), `prior_context_pass`, `prior_context_only`, `errored_current_only`,
`duplicate_dev_records`, `unregistered_records`, `reevaluation_required`.

### 2.3 Evidence from other contexts

- A PASS (or any dev record) produced under another experiment context is **not**
  an evaluation of the current context.  It is reported with its original
  context id / evaluation ids (`prior_context_pass`, `prior_context_only`), flagged
  `reevaluation_required`, and it keeps the family undecided.
- Prior-context PASS candidates stay `PASS` (their locked TEST is consumed and
  immutable).  The runner re-runs only their **dev phase** under the current
  context (`prior_pass_reeval` ledger record); the locked TEST is never touched
  and the state is not changed.  Their new p-value takes part in BH like any other.
- Same-context results are reused without recomputation: once a current-context
  dev record exists it is the tested value.
- Decision rule: BH at q = 0.05 over m, run ONCE when `fdr_decision_ready`;
  candidates without a valid statistic are counted in m and never rejected.
  No sequential / alpha-spending procedure exists, so no interim decision is made.

### 2.4 Decision immutability

A decision is keyed by (generation id, context id) and carries `manifest_hash`,
method + version, q, m and `input_hash` (a hash of the exact first-record ids and
p-values).  Recording identical evidence again is idempotent.  Different evidence
for an existing decision raises `FdrDecisionConflict`, leaves the stored decision
untouched, appends the conflict to `state/fdr_decisions.conflicts.jsonl`, and the
runner reports the family as not decision-ready (no candidate is resolved).

### 2.5 What is and is not guaranteed

- BH controls the FDR at q under independence or PRDS of the test statistics
  (Benjamini & Hochberg 1995; Benjamini & Yekutieli 2001).  The candidates share
  the same price series and overlap heavily.  **PRDS has NOT been verified**, so
  the FDR level is *not guaranteed* for this family.  Benjamini–Yekutieli (valid
  under arbitrary dependence, lower power) is not used; swapping the method
  would need its own pre-registration and was deliberately not done here.
- The FDR statement covers the primary-cell hypotheses only.  The additional
  "all symbols PASS", independent-validation and locked-TEST requirements only
  remove rejections.
- Re-testing a generation under a NEW context is a new family; FDR is NOT
  controlled across families.  The single-use locked TEST (one exposure per
  strategy+symbol, ever) is the only protection against that repetition.
- Selection bias that remains: the candidate space itself was designed by people
  who have seen earlier data and results (the generator, horizons, thresholds);
  m counts the registered space, not the ideas discarded before registration.
- Leakage risks that remain: dev and validation windows come from the same
  history as the data the rules were conceived on; rolling z-score and
  alignment of auxiliary data (funding, FNG) depend on the publication-timing
  assumptions in `DATA_POLICY`; none of this has been independently audited
  with real data.

## 3. Candidate states

`PENDING → RUNNING → {AWAITING_FDR, PASS, FAIL_COST, FAIL_STAT,
UNDETERMINED_POWER, DATA_MISSING, ERROR, REVERIFY_PENDING}`;
`AWAITING_FDR → {final states, REVERIFY_PENDING}`; non-final states →
`REVERIFY_PENDING → PENDING`; `PASS` is terminal.

| label | meaning | never means |
|---|---|---|
| PASS | dev + validation + FDR survival + locked TEST passed, lock used exactly once | "profitable" or "promotable" — promotion also needs a VERIFIED cost model |
| AWAITING_FDR | dev-PASS, generation FDR decision not final | any kind of pass |
| FAIL_STAT | evaluated, effect not shown (or not an FDR survivor, or lock re-used) | data problem |
| FAIL_COST | cost model refused the trade / result | priced with a cheaper fallback |
| UNDETERMINED_POWER | too few events (n < 30) or degenerate variance | fail or pass |
| DATA_MISSING | required data absent / quality-rejected / no locked window | statistical failure |
| ERROR | computation error (NaN/inf, p outside [0,1], cost model numeric error, crash) | p = 0 or p = 1 |

Non-overlap check (one cause → one label): `FAIL_STAT` = data and cost model were
fine, the effect is not shown; `DATA_MISSING` = data absent/rejected, nothing was
tested; `UNDETERMINED_POWER` = tested but too few events or degenerate variance,
no judgement; `FAIL_COST` = the cost model refused a trade or the result fails after
costs; an *unverified* cost model is not a label but a claim scope
(`UNVERIFIED_COST_MODEL`: such a PASS is never promotable); `ERROR` = the program failed to complete the evaluation.
`AWAITING_FDR` is not a verdict.

Verdict precedence when symbols disagree:
`ERROR > DATA_MISSING > UNDETERMINED_POWER > FAIL_COST > FAIL_STAT > PASS`;
an unknown label is treated as ERROR.

Re-verification: the central policy `engine_lib.reverify_eligible`.  A changed
experiment context (`context_changed`) is a new experiment and re-queues any
non-PASS candidate regardless of the per-experiment attempt counter.

## 4. Locked TEST lifecycle (P1-6)

`AVAILABLE → RESERVED → EVALUATION_STARTED → CONSUMED`;
`RESERVED → BLOCKED_DATA_MISSING | FAILED`; `EVALUATION_STARTED → FAILED`.

- All symbols' locked klines + funding are loaded and quality-checked
  while RESERVED; if any is missing every reservation is BLOCKED and
  nothing is consumed.
- `EVALUATION_STARTED` marks exposure; from then on the window counts as
  used, even if the process dies (recovery: dead owner → FAILED, exposed).
  A dead owner in RESERVED → FAILED, not exposed.
- `CONSUMED` results are immutable; a second `complete` raises.
- A window already exposed once refuses new reservations (`ALREADY_USED`).
- Every read-modify-write holds `fcntl.flock(<registry>.lock)`; v1 records
  are kept under `legacy_v1` and count as uses.

## 5. Experiment context (P1-7)

`C2-` + sha256 of the canonical JSON of: per-symbol klines content hash and
range, per-symbol funding series hash, FNG series hash (or explicit
"missing"), `DATA_POLICY` (timezone, truncation, gap/outlier rules, publication
timing), full cost identity (all four parameters, cost-model source sha256,
verified/test flags), engine source hash over `CODE_FILES`, validation config
(windows, MIN_VALID_N, alpha, embargo, locked windows, order value,
portfolio config and pass criteria, FDR spec, strategy space = generation id +
manifest hash + generator version), Python implementation +
version (the engine is stdlib-only, so this is the only library version that
can change numbers).  No clock, uuid, host or path enters it; `run_id` and
`evaluation_id` identify runs.  The git commit is recorded informationally
only (stored beside the context, never hashed, so a doc-only commit is not a new
experiment; the source hash is what detects real code changes).  Series hashes are
independent of row order; candidate order never changes the id.  Legacy `C-…` ids of old ledger rows are left as they are; those rows
are re-evaluated under the new context, never rewritten.

## 6. Portfolio accounting (P0-2, P0-4)

```
cash        = initial + realized PnL − fees − funding      (margin is reserved, not spent)
unrealized  = Σ side·(mark − entry)·qty
equity      = cash + unrealized        → equity_curve (at each close), MDD, sizing
used_margin = Σ qty·entry/leverage ;  entries must fit in equity − used_margin
```

Per bar: funding settles into cash at its timestamp (side·rate·qty·open) →
exits/reductions at open (exit fee once) → entries at open (entry fee once)
→ intrabar liquidation → mark at close.  The last bar force-closes every
open position at its open with normal costs, so `final_equity ==
equity_curve[-1]`.  MDD = max over t of (running peak − equity)/running peak
with the initial capital as first peak; a non-finite point → NaN (fails).
Equity ≤ 0 halts new entries (`bankrupt`).

Liquidation — **engine assumption, not an exchange rule**: isolated margin,
liq price `entry·(1 − 1/L + mmr)` (long) / `entry·(1 + 1/L − mmr)` (short);
on touch the position's posted margin is lost in full (maintenance margin =
liquidation penalty), no separate exit fee, fees/funding already paid stay
paid.  Real exchange liquidation (mark price, tiered MMR, insurance fund,
partial liquidation) is NOT modelled.

## 7. Costs (P0-3)

`CostContext.quote` classifies before pricing: participation > cap →
`CostRefused` (FAIL_COST); traded value missing/0/NaN → `CostDataMissing`;
bad input or non-finite/negative output → `CostComputationError` (ERROR);
incomplete parameters → `CostConfigError` at construction (CandleCostModel's
Upbit-spot defaults can never leak in).  Event study: refused / data-missing
events are skipped and counted, numeric errors propagate.  Portfolio: entries
are refused; an unpriceable EXIT still happens without an invented cost and
flags `cost_status`, which makes `portfolio_passes` false.  The candle model
uses no order book and no measured spread (`COST_MODEL_SCOPE`); it is
`UNVERIFIED_COST_MODEL` until calibrated, and an UNVERIFIED pass is never
promotable.
