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

## 2. Cross-candidate multiple testing (P0-1)

Rule (pre-specified, fixed sample): Benjamini–Hochberg at q = 0.05 over
m = |generation|, run ONCE when every candidate has a dev outcome under the
context.  The tested value is the first dev-phase record of each candidate
under that context; re-evaluations are kept as history and cannot replace it.
The decision is stored append-only (`state/fdr_decisions.jsonl`).

- Candidates without a valid statistic (`UNDETERMINED_POWER`,
  `DATA_MISSING`) are counted in m and never rejected.  No p-value is
  imputed; this is BH on the full family with those hypotheses retained,
  and it can only be stricter than BH on the testable subset.
- `ERROR` is not an outcome: a family with an erroring candidate stays
  incomplete until the error is fixed (fixing code changes the context).
- Before completion nobody survives, so no locked TEST is reserved and the
  evaluation order cannot change any decision (tested:
  `TestGenerationFdr.test_order_invariance_and_no_early_decision`).
- A terminal PASS from an earlier context is retained (counted, not re-tested).

What is guaranteed and what is not:

- BH controls the FDR at q when the test statistics are independent or
  PRDS (Benjamini & Hochberg 1995; Benjamini & Yekutieli 2001).  Candidates
  here share data and overlap; **PRDS is an assumption, not verified**.
  Benjamini–Yekutieli (valid under arbitrary dependence) is not used.
- The FDR statement covers the primary-cell hypotheses.  The additional
  "all symbols PASS" and independent-validation requirements only remove
  rejections, so they cannot raise the FDR of the primary hypotheses.
- Re-testing the same generation under a NEW context (new data / code /
  cost) is a new family; FDR is NOT controlled across families.  The
  protection against that repetition is the single-use locked TEST (one
  exposure per strategy+symbol, ever).
- No sequential / alpha-spending procedure is implemented; therefore no
  interim decision is made.

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
portfolio config and pass criteria, FDR spec), Python implementation +
version (the engine is stdlib-only, so this is the only library version that
can change numbers).  No clock, uuid, host or path enters it; `run_id` and
`evaluation_id` identify runs.  The git commit is recorded informationally
only.  Legacy `C-…` ids of old ledger rows are left as they are; those rows
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
