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

Exit codes: 0 CYCLE_OK · 1 CYCLE_FAILED (runner error, usage error, log
pipeline failure) · 2 CYCLE_PARTIAL · 3 CYCLE_SKIPPED (another process holds
`state/run.lock`).  The runner's exit code is propagated unchanged; a
failing `tee` to the log file also fails the cycle.

`runner.py` can be called directly: `python3 run/discovery/runner.py --batch N`.

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
6. when EVERY candidate of the generation has a dev outcome under the
   context, runs Benjamini–Hochberg once over m = |generation| (decision
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

168 tests (unittest, stdlib only): `test_engine.py` (91, original suite) and
`test_residual_fixes.py` (77, residual-defect regressions).  Synthetic data +
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
- `runner.py` — cycle orchestration, path config (env, fail-closed), CLI
- `portfolio_sim.py`, `funding_cost.py` — legacy v1 diagnostics (NOT used by
  the runner; `portfolio_sim.py` references the v1 `engine_lib.RUN` and does not run against v2)
- `state/` — state.json, queue.json, discovery_ledger.jsonl (v2 schema),
  preregistration/<G-…>.json, generations.jsonl, fdr_decisions.jsonl,
  contexts/<C2-…>.json, lock_test_usage.json, promotion_queue.jsonl, run.lock;
  legacy preregistration.json is kept untouched
- `../audit/` — audit reports
