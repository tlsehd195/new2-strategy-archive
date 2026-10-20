# Statistics, states and accounting — discovery engine v2.1

This document states what the engine's numbers mean and, just as
importantly, what they do NOT guarantee.  It reflects the code as of the
2026-10-15 residual-defect fixes; section 9 was added on 2026-10-17
(ADR 0010) and covers both engines as of that date.

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
| `locked_test_eligible` | not a family property. The decision stores `statistical_survivors` and `survivors_validation_pass`; whether the window may still be entered is read from the lock registry at run time (`Runner.lock_entry_status`): a window consumed earlier — any context, any symbol of the candidate — is never enterable again |
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
runner reports the family as not decision-ready (no candidate is resolved).  A stored
decision WITHOUT `input_hash` (legacy) is never accepted as "the same": equal result values
do not prove equal p-values, so it is handled as a conflict/hold and left untouched.

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

## 8. Adaptive generation (evolution mode, ADR 0004)

- **DEV is never a test.**  The search reads only DEV metrics through
  `search.dev_feedback` (any other stage raises `FeedbackLeak`); DEV results
  rank and screen, they never confirm.
- **Validation = preregistered batches.**  Members, alpha, method and seed
  are written to `evolution/validation_batches.jsonl` before evaluation.
  Inside a batch, per symbol: Romano–Wolf studentized StepM on daily net
  returns (events booked on their exit UTC day; flat days 0) — strong FWER
  under arbitrary dependence between strategies.  Batch k on one validation
  window gets `alpha_total·6/π²/k²` (sum ≤ alpha_total: valid however the
  later batches were chosen).  Multi-symbol strategies must be rejected on
  every symbol.  SPA / Reality Check / BH / BY are diagnostics only.
- **Robustness filters** (cost ×1.5/×2, regimes, MDD, concentration, DEV
  parameter neighbours, walk-forward, sample size, PBO/CSCV over all DEV
  trials) only remove candidates; an uncomputable PBO is `BLOCKED_STATISTICS`.
- **Locked windows = campaigns** (`state/locked_window_campaigns.jsonl`,
  shared with the legacy path): members and alpha fixed before access;
  campaign c on a window gets `alpha_total·6/π²/c²`, Bonferroni inside; at
  most 3 campaigns per window, then `WINDOW_RETIRED`.  Campaigns aborted
  before any exposure do not count.  A legacy locked PASS additionally needs
  `p ≤` its campaign member alpha.
- **Statuses** (per strategy and context, append-only, transition table):
  RESEARCH_ONLY → VALIDATION_SURVIVOR → PAPER_TEST_READY; terminal REJECTED /
  INCONCLUSIVE; BLOCKED_DATA / BLOCKED_COST / BLOCKED_STATISTICS resume only
  to the status recorded in `resume_to`.  Promotion links the record ids of
  the dev, validation, robustness and locked evidence; any missing evidence
  blocks it.
- **Search pool** (ADR 0013): the search sees only strategies registered
  through the search itself (seeds and proposals, i.e. with a lineage
  record).  Robustness neighbours are DEV-evaluated because their centre
  passed VALIDATION, so they are kept out of the search feedback and out of
  the known-id set; otherwise a validation outcome would steer the search.
- **Behavioural duplicates** (ADR 0013): `evolution.behaviour_key` pools
  entry and filter conditions under `all` logic (entry A + filter B trades
  exactly like entry A AND B).  A proposal with a known key is counted as
  `semantic_duplicates` and not registered.  `strategy_id` is unchanged.
- **Failure class** of every validation member (reporting only, never a
  gate; the worst symbol decides): `DATA_MISSING` (no event on a symbol),
  `INSUFFICIENT_DATA` (fewer than the preregistered minimum),
  `NO_EDGE_OUT_OF_SAMPLE` (mean gross move ≤ 0: the DEV edge did not carry
  over), `FAIL_COST` (gross > 0, net ≤ 0), `FAIL_STAT` (net > 0 but StepM did
  not reject).  DEV and validation records carry gross / cost / funding /
  net means of the same events (before vs after cost, same rule and window).
- Assumptions stated, not proven: stationarity / weak dependence of daily
  strategy returns (bootstrap validity), daily aggregation of 15m events,
  `q = n^(-1/3)` mean block length.

## 9. Two engines, two procedures (ADR 0010)

`run_cycle.sh` / `runner.py` run **legacy** by default (`NEW2_DISC_MODE` /
`--mode`; unset prints `CYCLE_WARNING` and every result line carries
`mode=`).  The engines share only the single-use locked-window records.
A `CYCLE_OK` line means the cycle ran; it is never a strategy verdict.

| | legacy (`--mode legacy`, default) | evolution (`--mode evolve`) |
|---|---|---|
| candidates | fixed 779-candidate space, one generation | declarative specs, seeded from the 779, then mutated |
| cross-candidate test | generation BH-FDR, q = 0.05, m = whole manifest (§2) | Romano–Wolf StepM per preregistered validation batch, alpha spending `alpha_total·6/π²/k²` per window (§8) |
| BY / SPA / RC | not used | diagnostics only |
| locked TEST entry | BH survivor + validation PASS → one campaign per BH decision | VALIDATION_SURVIVOR after robustness → one campaign |
| locked PASS | cell test AND `p ≤` campaign member alpha | one-sided HAC t per symbol, Bonferroni in the campaign |
| verdict labels | §3 (PASS, FAIL_STAT, …, AWAITING_FDR) | §8 statuses (VALIDATION_SURVIVOR, BLOCKED_*, PAPER_TEST_READY, …) |
| state | `state/state.json`, `state/discovery_ledger.jsonl`, `state/queue.json`, `results/cycle_summary.json` | `state/evolution/*`, `results/evolution_cycle_summary.json` |
| shared | `state/lock_test_usage.json`, `state/locked_window_campaigns.jsonl` | same files |

The two procedures are not one common procedure and their numbers are not
comparable.  BH-FDR assumes PRDS between candidates; that is **not**
verified (§2.5).

### 9.1 Locked TEST gates, both engines

Checked in this order, before any window status lookup, reservation or
locked-data read.  A blocked candidate spends nothing.

1. **Cost covers the use** (ADR 0009, legacy since ADR 0010):
   `cost.verified_for(symbols, order_value)`.  Not covered → legacy:
   `lock_blocked = COST_NOT_VERIFIED`, the candidate stays `AWAITING_FDR`
   with no final record; evolution: `BLOCKED_COST`, stage `locked`,
   `resume_to = VALIDATION_SURVIVOR`.
2. **Independence** (ADR 0010): no other symbol of the candidate may have
   selection (DEV/validation) data inside a symbol's locked window.  ETH
   selection reads 2021-01-01..2026-04-27, which covers the BTC window
   2024-03-25..2024-06-18, so **every BTC+ETH candidate is blocked**.
   Legacy: final `DATA_MISSING` with note `LOCKED_WINDOW_NOT_INDEPENDENT`
   (not re-queued: no data refresh can change it); evolution:
   `BLOCKED_STATISTICS`, stage `locked`.  Single-symbol candidates are
   unaffected.  Not covered: information that reaches a single-symbol
   candidate through the search itself (evolution mutates using DEV results
   of both symbols) — a stated residual risk.
3. Window history (consumed / in progress / retired) and data presence (§4).

### 9.2 Cost status labels

| label | source | promotable |
|---|---|---|
| `UNVERIFIED_COST_MODEL` | NEW2 candle-model defaults (production default) | no |
| `ASSUMED_COST_MODEL` | `assumed_public_v1`: public fee schedule + fixed margins | no |
| `ESTIMATED_FROM_PUBLIC_DATA` | `public_estimate:<record>`: spread/impact from public order books; fee still assumed | no |
| `VERIFIED` | sealed `cost_calibration` record from account execution reports, ≥ 200 verification fills per symbol, mean / p90 underestimate limits met pooled AND per symbol (ADR 0013), no quarantined or duplicate rows, inside its symbol and notional scope | yes, inside the scope only |
| `NOT_VERIFIED` | `cost_calibration` result: enough evidence, but a check failed (underestimate, reject rate, quarantined / duplicate rows) | no |
| `INSUFFICIENT_DATA` | `cost_calibration` result: genuine execution evidence, but fewer verification fills than required or the order size outside the observed range — collect more, nothing failed (ADR 0013) | no |
| `BLOCKED_EXTERNAL_EVIDENCE` | `cost_calibration` result when the evidence does not exist (no account fills) | — |

A calibration record also carries the 95 % interval of the mean prediction
error (normal approximation, independent fills assumed — clustered fills
make the true interval wider), the collection period, and which volatility /
participation buckets the verification evidence covers.  Those are
informational: the preregistered criteria file (`cost_verification_v1.json`)
was not changed.  Plausibility checks on every fill (ts in ms between
2017-01-01 and now + 1 day, fee ≤ 1 % of filled notional, fill price within
5 % of the reference, no overfill) catch unit errors; `order_id`, when
present, is a duplicate key.

### 9.3 Paper labels

`PAPER_TEST_READY` = eligible for an isolated replay; `paper.py` replays
stored bars and can never produce `PAPER_PASSED`.  `PAPER_PASSED` comes only
from `paper_forward.py` on bars that closed after the plan was registered,
and plan registration requires a VERIFIED cost.  No strategy is
`PAPER_PASSED`.
