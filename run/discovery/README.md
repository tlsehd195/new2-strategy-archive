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
[--mode legacy|evolve|full|audit|power] [--rounds R] [--evals E]` (`--help` lists them).
`run_cycle.sh` passes `NEW2_DISC_MODE` as `--mode`.  `--mode audit` (ledger /
state consistency of `NEW2_STATE_DIR`) and `--mode power` (synthetic power of the
promotion gates, `results/power_analysis.json`) are read-only: no lock, no market
data, nothing written to the state directory (ADR 0065).

**The default mode is `legacy`** (the fixed 779-candidate engine with
generation BH-FDR), not the evolution engine.  With `NEW2_DISC_MODE` unset,
`run_cycle.sh` prints `CYCLE_WARNING NEW2_DISC_MODE not set …` on stderr and
every result line names the mode (`CYCLE_OK batch=10 mode=legacy`); the
runner prints `RUN_MODE <mode> state_dir=…`.  The latest real-data runs used
`--mode evolve`.  A `CYCLE_OK` means the cycle ran, never that a strategy
passed: the evolution summary says so in `status_meaning`, and
`strategies_passed` lists the VALIDATION_SURVIVOR / PAPER_TEST_READY counts (empty
= none).  The two engines' procedures differ: `STATISTICS_AND_STATES.md` §9.

### State files: current vs historical (ADR 0010)

| file | engine | status |
|---|---|---|
| `state/state.json`, `state/discovery_ledger.jsonl`, `state/queue.json`, `state/preregistration.json` | legacy v2 | written by `--mode legacy`; the committed copies are the 2026-10-15 runs |
| `results/cycle_summary.json` | legacy v2 | last committed legacy cycle (2026-10-15, 3 evaluated, all DATA_MISSING) — not the latest research run |
| `state/evolution/*`, `results/evolution_cycle_summary.json` | evolution | written by `--mode evolve`; not committed (real-data runs used fresh directories, see `run/audit/`) |
| `state/lock_test_usage.json`, `state/locked_window_campaigns.jsonl` | both | single-use locked-window records; not present in the repository (no window was ever used) |
| `state.json`, `discovery_ledger.jsonl`, `promotion_queue.md`, `cycles.md` (this directory) | v1 (`NEW2-discovery-v1`) | **historical evidence only**; no current code reads them; kept unchanged |
| `cycle_log.txt` (this directory) | v1 lines, then any engine | default log of `run_cycle.sh`; the committed lines are v1 (2026-10-14), new cycles are appended below them |
| `state/cycles.md`, `state/last_stderr.log` | legacy v2 | notes of the 2026-10-15 runs; no code writes them |

The current state of the research (all engines, all measurements) is
summarised in `run/audit/STATUS_CURRENT_20261020.md` (the 2026-10-17 file is
kept as a dated record).

`python3 evolution_report.py <state_dir> --md OUT.md` re-derives every stage
count (candidates, DEV, validation members with failure class, statuses,
locked uses) from the ledgers of an evolution state directory (ADR 0013).
While a cycle runs, `evolution_cycle_summary.json` says `CYCLE_RUNNING`; a
crash rewrites it as `CYCLE_FAILED`; a killed cycle leaves `CYCLE_RUNNING`,
which `ops.py health` reports as `CYCLE_INTERRUPTED` (DEGRADED).

## Evolution mode (`--mode evolve`, ADR 0004)

Cost profile (ADR 0007): `NEW2_COST_PROFILE=assumed_public_v1` prices every
fill at the public base-tier taker fee (5bp) plus half spread 2bp and a
fixed 3bp slippage margin per side (`config/cost_assumed_public_v1.json`).
Records carry `ASSUMED_COST_MODEL` ("비용 미검증"); like the default
(`UNVERIFIED_COST_MODEL`) it can never reach `PAPER_TEST_READY` — only a
sealed `cost_calibration` VERIFIED record can.  Unknown names exit 1.
`NEW2_COST_PROFILE=public_estimate:<record.json>` uses spread/impact
estimated from public order books (`public_market_collect.py` on a machine
that reaches Binance, then `public_cost_estimate.py`; Korean guide:
`docs/공개시세_비용추정_로컬실행_안내.md`) — status
`ESTIMATED_FROM_PUBLIC_DATA`, equally never promotable.

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
are in `config/promotion_v5.json` (the single default; its version and hash are
part of the context and of every robustness record; v1–v4 are kept for the
record — v3 makes the whole search one FWER family per window, v4 tightens PBO,
v5 makes DSR and the bootstrap CI gating, see `STATISTICS_AND_STATES.md` §10).
`PAPER_TEST_READY` means "eligible for an isolated paper replay" — never
"profitable", never live trading (no order code exists).

Market data acquisition is separate and opt-in:
`python3 data_fetch.py --data-dir DIR --symbols BTCUSDT ETHUSDT --allow-network`
(checksum + quality gate + atomic commit; existing files are never overwritten).
Where `data.binance.vision` is blocked, `--base-url` with the same bucket's S3 endpoint
(`data_fetch.S3_MIRROR`) fetches the identical files; the URL used is in the provenance.

Independent window IW1 (ADR 0016): 2020 data, the year before `READ_START`, in
market-cap tranches, preregistered in `config/independent_window_v1.json`.
`independent_window.py check --data-dir D` (overlap + data readiness, no strategy
evaluated) → `freeze-members` (members of one DEV-only state directory) →
`open --tranche A --confirm-single-use` (once; the history row is written first).

Environment (all optional, fail-closed when wrong):

- `NEW2_DISCOVERY_ROOT` (default: this directory) — state/, results/, cycle_log.txt live here
- `NEW2_DATA_DIR` (required, no default since ADR 0029) — must exist
- the cost model is this repository's `cost_model.py` (no other repository, checkout,
  token or path is needed; `COINTRADER_SRC` is no longer read — directive 2026-10-20)
- `NEW2_DISC_LOG` — log file (default `$NEW2_DISCOVERY_ROOT/cycle_log.txt`)
- `REFRESH=1` is explicitly NOT supported: data updates belong to
  `run/download_missing_data.py`; every evaluation records data staleness honestly.

### DEV-only cycles, fills import, consistency check, recovery (directive 2026-10-20)

- `runner.py --mode evolve --dev-only` runs the data check, DEV evaluations and
  search rounds only.  No validation batch is preregistered, no validation or
  locked window is loaded, no alpha is spent.  Use it to re-screen with new code
  when no new independent window exists.
- `--selection map_elites` (only with `--mode evolve --dev-only`, ADR 0019) picks
  parents MAP-Elites style: one champion per cell of direction x holding band
  (<=32, <=128, <=384, <=768 bars) x signal group (feature kinds of the entry), and
  parents are drawn uniformly over the occupied cells.  The default `pareto_niche`
  is unchanged and keeps its search-config hash.  `run/compare_selection_dev.py`
  compares two such runs (same seed and budget) from their ledgers.
- `fills_import.py --fills <export.csv|json> --account-kind real|demo|paper|unknown --klines <dir> [--fee-prices f.json]
  [--verify --decisions d.jsonl --quotes q.jsonl]` imports a Binance trade-history
  export into `private_fills/` (git-ignored; only counts and hashes are printed),
  checks columns, types, duplicates, time order, time zone, missing fees (never
  read as 0), fee-asset conversion (method and price time recorded per fill) and
  per-symbol counts, and can run `cost_calibration` on the result.  Policy:
  `config/fills_import_v1.json`.  `--data-origin synthetic_test` data can never be
  VERIFIED, and neither can fills of a demo / paper / undeclared account
  (`--account-kind` is required; only `real` counts, ADR 0063).
- `--search-config FILE` (`--mode evolve`) reads `search.SearchConfig` fields from a
  JSON object; unknown keys are a usage error and the parent selection stays on
  `--selection` (ADR 0065).
- `--feature-set v2` (`--mode evolve`) registers the opt-in klines features
  `range_z`, `illiq_z`, `ma_dist_z`, `skew_z`, `dow_utc` (causal, prefix-invariance
  tested).  It changes `registry_identity()` and so starts a new experiment
  context; without it nothing changes.  `feature_data_registry.py <data_dir>` lists
  which feature families have their data and which are `DATA_MISSING` (open
  interest, premium index, liquidations, order-book history, DVOL) or
  `NOT_DERIVABLE` (trade count).  `strategy_record.py` is the one record format for
  engine and independent strategies (with the dedup rule); the classification of
  every `run/*.py` is `run/audit/RESEARCH_SCRIPTS_20261110.md` (ADR 0066).
- `ledger_check.py <state_dir>` (or `runner.py --mode audit`) is a read-only
  consistency check of the source ledgers and derived summaries (exit 0 consistent,
  2 findings, 1 unreadable); the file roles are listed in its docstring.  It also
  flags an OK DEV record without its DEV series (`MISSING_DEV_SERIES`).

Recovery after a crash, `kill` or reboot:

1. `python3 ops.py health` — shows a stale lock, `CYCLE_RUNNING` without a live
   process, or `CYCLE_FAILED`.
2. `python3 ledger_check.py state` — if `CONSISTENT` (an `INTERRUPTED_CYCLE`
   finding alone is expected here), rerun the same command: evaluations are keyed
   by (stage, strategy, context) and are never repeated, the lock is re-taken, and
   legacy `RUNNING` candidates are re-queued by `store.reconcile()`.
3. Any other finding (tampered record, impossible transition, duplicate
   submission or promotion) means the source ledgers were edited: stop, keep the
   state directory as evidence, and restore it from git / backup.  Derived files
   (summaries, `state.json`, `queue.json`, reports) are rebuilt from the sources;
   the ledgers are never hand-edited.

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
cd run && python3 -m unittest discover -s discovery/tests -v
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
   and so does a cost model that is not VERIFIED for the strategy's symbols
   and order size (`BLOCKED_COST`, stage `locked`; ADR 0009)
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
- `config/cost_assumed_public_v1.json` — the ASSUMED cost profile (ADR 0007)
- `public_market_collect.py` / `public_cost_estimate.py` + `config/cost_public_estimate_v2.json` (v1 superseded) — key-less public book collector (opt-in network, bounded; `--manifest-only` re-seals files offline) / offline spread+impact estimator with manifest-hash check, worse-side pricing, thin-book exclusion and `--verify` re-check (ADR 0007, ADR 0008)
- `cost_calibration.py` + `config/cost_verification_v1.json` — the ONLY way to a verified cost model: fit on execution reports, verify out-of-sample, sealed `CV-…` record (ADR 0006); statuses VERIFIED / NOT_VERIFIED / INSUFFICIENT_DATA / BLOCKED_EXTERNAL_EVIDENCE, `--quarantine FILE` isolates bad rows (ADR 0013)
- `evolution_report.py` — read-only stage counts and per-member failure classes from an evolution state directory (ADR 0013)
- `data_manifest.py` — dataset manifest (SHA-256 per file, provenance, cross-month/funding checks)
- `execution_sim.py` — partial fill / reject / liquidation / late-observation layer
- `paper_forward.py` + `config/paper_forward_v1.json` — forward paper observation on bars that close after registration; the only source of PAPER_PASSED; funding must be confirmed complete under the declared schedule, else PAPER_BLOCKED_DATA (ADR 0073)
- `ops.py` — `health` (read-only state check) and bounded `loop`
- `portfolio_sim.py`, `funding_cost.py` — legacy v1 diagnostics (NOT used by
  the runner; `portfolio_sim.py` references the v1 `engine_lib.RUN` and does not run against v2)
- `state/` — state.json, queue.json, discovery_ledger.jsonl (v2 schema),
  preregistration/<G-…>.json, generations.jsonl, fdr_decisions.jsonl,
  contexts/<C2-…>.json, lock_test_usage.json, promotion_queue.jsonl, run.lock;
  legacy preregistration.json is kept untouched
- `../audit/` — audit reports
