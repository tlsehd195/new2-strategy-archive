# NEW2 Discovery Engine v2

Rule-based strategy discovery engine (7 families, 779 candidates) with an
explicit honesty layer: state machine, append-only ledger, multiple-testing
gates and single-use locked TEST windows.

This is a REBUILD of the original engine (lost with the old sandbox, F-26).
It is NOT a patch of the old code and starts a NEW ledger; historical
records (v1 ledger, old result JSONs) are preserved untouched.

Statistical method, state vocabulary and accounting rules are documented in
[`STATISTICS_AND_STATES.md`](STATISTICS_AND_STATES.md).  The 2026-10-15
residual-defect fixes are reported in
[`../audit/AUDIT_RESIDUAL_FIXES_20261015.md`](../audit/AUDIT_RESIDUAL_FIXES_20261015.md).

## Run one cycle

```bash
bash run/discovery/run_cycle.sh [BATCH]     # BATCH: positive integer, default $NEW2_DISC_BATCH or 10
```

`BATCH` is passed to `runner.py --batch BATCH` (the number of candidate
evaluations in this cycle).  The script can be run from any working
directory.  Invalid input (`0`, `-3`, `abc`, more than one argument) is
rejected before Python starts.

Exit codes: 0 CYCLE_OK · 1 CYCLE_FAILED · 2 CYCLE_PARTIAL · 3 CYCLE_SKIPPED
(another process holds `state/run.lock`) · 4 CYCLE_BLOCKED_DATA (evolution mode:
no usable market data, nothing evaluated; with a log failure it becomes 1).  The runner's exit code, the log
writer's exit code and the output filter's exit code are captured separately
(`PIPESTATUS`) and recorded in `$NEW2_DISC_AUDIT` (default
`$NEW2_DISCOVERY_ROOT/cycle_audit.log`) and on stderr.  A log-writer failure never
hides the runner's status:

| runner | log writer | exit | meaning |
|---|---|---|---|
| 0 | ok | 0 | CYCLE_OK |
| 0 | fail | 1 | success without a record is not success |
| ≥1 | ok / fail | 1 | CYCLE_FAILED; with a log failure both are recorded |
| 2 | ok | 2 | CYCLE_PARTIAL |
| 2 | fail | 1 | partial result whose record was lost → FAILED |
| 3 | ok / fail | 3 | CYCLE_SKIPPED (nothing ran); a log error is still recorded |

If the log directory cannot be created the runner is not started (exit 1).
`NEW2_DISC_TEE` replaces the log writer command (used by the tests).

`runner.py` can be called directly: `python3 run/discovery/runner.py --batch N
[--mode legacy|evolve|full] [--rounds R] [--evals E]`.  `run_cycle.sh` passes
`NEW2_DISC_MODE` (default `legacy`) as `--mode`.

## Evolution mode (`--mode evolve`, ADR 0004)

One automatic research cycle (`autoloop.py`): data check (no usable data →
`CYCLE_BLOCKED_DATA`, nothing synthesised) → seed the search once with the
779 legacy candidates translated to declarative specs → pending DEV
evaluations → `R` search rounds (Pareto + niche-capped elite from DEV results
only; typed mutation / crossover / exploration; every child registered with
its lineage) → one preregistered validation batch (StepM, alpha spending per
window) → robustness filters → one locked-window campaign → promotion review
→ isolated paper replay.  State lives in `state/evolution/` (specs, lineage,
evaluations, statuses, batches, rounds, search state, reviews, paper runs) and
`state/locked_window_campaigns.jsonl` (shared with the legacy path).  Gates
are in `config/promotion_v1.json` (its hash is part of the context).
`PAPER_TEST_READY` means "eligible for an isolated paper replay" — never
"profitable", never live trading (no order code exists).

Market data acquisition is separate and opt-in:
`python3 data_fetch.py --data-dir DIR --symbols BTCUSDT ETHUSDT --allow-network`
(checksum + quality gate + atomic commit; existing files are never overwritten).

Environment (all optional, fail-closed when wrong):

- `NEW2_DISCOVERY_ROOT` (default: this directory) — state/, results/, cycle_log.txt live here
- `NEW2_DATA_DIR` (default `/home/z/my-project/NEW2_run/data`) — must exist
- `COINTRADER_SRC` (default `/home/z/my-project/NEW2_public_clone/src`) — the SINGLE
  cost-model source (`cointrader.backtest.costs.CandleCostModel`)
- `NEW2_DISC_LOG` — log file (default `$NEW2_DISCOVERY_ROOT/cycle_log.txt`)
- `REFRESH=1` is explicitly NOT supported: data updates belong to
  `run/download_missing_data.py`; every evaluation records data staleness honestly.

## What one cycle does

1. builds the candidate space and its **generation manifest** (sorted ids +
   normalized definitions → `G-…` hash); a new space creates a NEW write-once
   file `state/preregistration/<G-…>.json`, existing ones are verified, never rewritten
2. recovers crashed state (`reconcile`, dead lock-test owners)
3. computes the **experiment context** `C2-…` from every input that changes a
   result (klines/funding/FNG content, data policy, full cost parameters +
   cost-model source hash, engine source hash, validation config, Python version)
4. re-queues candidates whose last evaluation ran under another context
5. evaluates up to BATCH candidates (dev + independent validation); dev-PASS
   candidates wait in `AWAITING_FDR` — no final verdict, no locked TEST yet
6. prior-context PASS candidates get a fresh dev-phase statistic under the
   current context (their PASS and locked TEST are untouched); when EVERY
   candidate of the generation has a dev outcome **under the current context**, runs Benjamini–Hochberg once over m = |generation| (decision
   stored append-only in `state/fdr_decisions.jsonl`); survivors with a
   validation PASS get their locked TEST (reserve → data checks → start →
   complete), everyone else is resolved without touching the lock
7. promotion gate on evidence (manifest membership, final FDR decision,
   registry lock results, verified cost model, portfolio result for combos)

Consequence: no candidate of a generation can reach a final PASS before the
whole generation has been evaluated (779 candidates ≈ 78 cycles at batch 10).

## Tests

```bash
cd run && COINTRADER_SRC=/path/to/NEW2/src python3 -m unittest discover -s discovery/tests -v
```

unittest, stdlib only: `test_engine.py` (original suite), `test_residual_fixes.py`
(residual-defect regressions), `test_run_cycle_combinations.py` (run_cycle.sh exit-code
matrix), `test_final_fdr_context.py` (FDR family notions, prior-context evidence,
decision immutability, context behaviour), `test_core_defects.py` (15m integrity, defaults,
look-ahead, ledger recovery), `test_evolution_spec.py` (specs, operators, lineage,
RC/SPA/StepM), `test_pipeline.py` (staged pipeline, campaigns, promotion, paper, search
budget/resume, cycle), `test_legacy_campaigns.py`, `test_data_fetch.py` and `test_completion.py` (cost verification
gate, fetch retry, data manifest, execution sim, forward paper, PR #4 diagnostics, scenario
matrix, ops; ADR 0006).  CI: `.github/workflows/research-ci.yml`.  The exact counts of the last verification
are in the final audit report.  Synthetic data +
isolated temp dirs — the real ledger/state and the locked TEST windows are
NEVER touched.  The run log of the last verification is in the residual-fix
audit report.

## Honesty rules enforced in code

1. 10-state candidate machine (incl. `AWAITING_FDR`) — transitions checked, PASS terminal
2. `strategy_id` (normalized strategy only) / `evaluation_id` (fresh per run) /
   `experiment_context_id` (`C2-…`, conditions only — no clock, no uuid) / `run_id`
3. Holm within symbol cells; **BH-FDR over the whole preregistered
   generation**, decided once when complete; untestable hypotheses counted in
   m, never imputed; ledger = authoritative record (first dev record per
   candidate is the tested value)
4. Multi-symbol candidates pass only if EVERY required symbol passes;
   missing data is UNDETERMINED, never a silent failure; ERROR outranks every label
5. Stouffer: z-scores only (no sqrt(n) double weighting), correlation
   estimated on dev data only, estimation failure → UNDETERMINED (no rho grid max)
6. Locked TEST windows are read-only for selection and single-use per
   strategy+symbol (`LockTestRegistry` v2: RESERVED → EVALUATION_STARTED →
   CONSUMED / BLOCKED_DATA_MISSING / FAILED, flock-protected, immutable results);
   missing locked data blocks the attempt WITHOUT consuming the window
7. Promotion gate: preregistration + final FDR survival + independent
   validation + lock test exactly once + VERIFIED cost model (+ a real
   portfolio result under the same context for combos) → append-only queue;
   registration ≠ live deployment (no order-execution code path exists)
8. Costs are `UNVERIFIED_COST_MODEL` until real fee/execution data exists —
   such a PASS is labelled `claim_scope: UNVERIFIED_COST_MODEL` and is not
   promotable.  Cost failures are classified (FAIL_COST / DATA_MISSING /
   ERROR) and never replaced by a cheaper fallback.
9. Portfolio equity is mark-to-market (cash + unrealized); MDD uses that
   same series; fees/funding are charged exactly once; the last bar
   force-closes at its open with normal costs.

## Layout

- `engine_lib.py` — states, IDs, context/code identity, statistics, verdicts, locked windows, lock registry, process lock
- `generation.py` — generation manifest, preregistration store, FDR family + decisions, context store
- `generator.py` — 7 rule-based families, semantic dedup, space status
- `evaluator.py` / `portfolio.py` — event study (no lookahead) / MTM portfolio sim
- `store.py` — state.json wrapper, reverify queue, ledger, reconcile()
- `data_quality.py` — fail-closed quality gates + dataset versioning
- `costs_bridge.py` — single cost source, failure classification, sensitivity scenarios
- `promotion.py` — evidence-based promotion gates + append-only queue
- `runner.py` — cycle orchestration, path config (env, fail-closed), CLI (`--mode`)
- `strategy_spec.py` — declarative strategy schema, feature registry, canonical form, legacy translation
- `evolution.py` / `search.py` — variation operators, spec + lineage stores / DEV-only Pareto search, budgets, resume
- `stats_mc.py` — stationary bootstrap, Reality Check, SPA, StepM, BY, alpha spending
- `pipeline.py` — staged evaluation, statuses, validation batches, robustness, locked campaigns, promotion review
- `campaigns.py` — locked-window campaign ledger (group-level locked TEST control)
- `paper.py` / `autoloop.py` / `data_fetch.py` — isolated paper replay (never PAPER_PASSED) / one evolution cycle / opt-in data acquisition (retry + failure classes)
- `cost_calibration.py` + `config/cost_verification_v1.json` — the ONLY way to a verified cost model: fit on execution reports, verify out-of-sample, sealed `CV-…` record (ADR 0006)
- `data_manifest.py` — dataset manifest (SHA-256 per file, provenance, cross-month/funding checks)
- `execution_sim.py` — partial fill / reject / liquidation / late-observation layer
- `paper_forward.py` + `config/paper_forward_v1.json` — forward paper observation on bars that close after registration; the only source of PAPER_PASSED
- `ops.py` — `health` (read-only state check) and bounded `loop`
- `portfolio_sim.py`, `funding_cost.py` — legacy v1 diagnostics (NOT used by
  the runner; `portfolio_sim.py` references the v1 `engine_lib.RUN` and does not run against v2)
- `state/` — state.json, queue.json, discovery_ledger.jsonl (v2 schema),
  preregistration/<G-…>.json, generations.jsonl, fdr_decisions.jsonl,
  contexts/<C2-…>.json, lock_test_usage.json, promotion_queue.jsonl, run.lock;
  legacy preregistration.json is kept untouched
- `../audit/` — audit reports
