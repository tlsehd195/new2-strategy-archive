# NEW2 Discovery Engine v2

Rule-based strategy discovery engine (7 families, 779 candidates) with an
explicit honesty layer: state machine, append-only ledger, multiple-testing
gates and single-use locked TEST windows.

This is a REBUILD of the original engine (lost with the old sandbox, F-26).
It is NOT a patch of the old code and starts a NEW ledger; historical
records (v1 ledger, old result JSONs) are preserved untouched.

## Run one cycle

```bash
bash run/discovery/run_cycle.sh [batch]     # default batch 10
```

Exit codes: 0 CYCLE_OK · 1 CYCLE_FAILED/INTERRUPTED · 2 CYCLE_PARTIAL · 3 SKIPPED (lock held).

Environment (all optional, fail-closed when wrong):

- `NEW2_DISCOVERY_ROOT` (default: this directory) — state/, results/ live here
- `NEW2_DATA_DIR` (default `/home/z/my-project/NEW2_run/data`) — must exist
- `COINTRADER_SRC` (default `/home/z/my-project/NEW2_public_clone/src`) — the SINGLE
  cost-model source (`cointrader.backtest.costs.CandleCostModel`)
- `REFRESH=1` is explicitly NOT supported: data updates belong to
  `run/download_missing_data.py`; every evaluation records data staleness honestly.

## Tests

```bash
cd /home/z/my-project/NEW2_run && python3 -m unittest discover -s discovery/tests -v
```

91 tests (unittest, stdlib only), synthetic data + isolated temp dirs —
the real ledger/state and the locked TEST windows are NEVER touched.
Last verified run: **91/91 OK, twice in a row** (see
`run/audit/AUDIT_FINAL_감사보고서.md` §F).

## Honesty rules enforced in code

1. 9-state candidate machine — transitions checked, PASS terminal
2. `strategy_id` (normalized strategy only) / `evaluation_id` (fresh per run) /
   `experiment_context_id` (dataset+cost+code+config)
3. Holm within symbol cells, **BH-FDR across candidates BEFORE the locked
   TEST is consumed**, ledger = authoritative record
4. Multi-symbol candidates pass only if EVERY required symbol passes;
   missing data is UNDETERMINED, never a silent failure
5. Stouffer: z-scores only (no sqrt(n) double weighting), correlation
   estimated on dev data only, estimation failure → UNDETERMINED (no rho grid max)
6. Locked TEST windows are read-only for selection and single-use per
   strategy+symbol (`LockTestRegistry`); locked-window funding is loaded
   separately (A-31) and a symbol without an evaluated lock can never
   yield a final PASS (A-32)
7. Promotion gate: preregistration + independent validation + lock test
   exactly once (+ portfolio pass for combos) → append-only queue;
   registration ≠ live deployment (no order-execution code path exists)
8. Costs are `UNVERIFIED_COST_MODEL` until real fee/execution data exists.

## Layout

- `engine_lib.py` — states, IDs, statistics, verdicts, locked windows, lock
- `generator.py` — 7 rule-based families, semantic dedup, space status
- `evaluator.py` / `portfolio.py` — event study (no lookahead) / portfolio sim
- `store.py` — state.json wrapper, reverify queue, ledger, reconcile()
- `data_quality.py` — fail-closed quality gates + dataset versioning
- `costs_bridge.py` — single cost source + sensitivity scenarios
- `promotion.py` — promotion gate + append-only queue
- `runner.py` — cycle orchestration, path config (env, fail-closed)
- `state/` — state.json, queue.json, discovery_ledger.jsonl (v2 schema),
  cycles.md, preregistration.json, lock_test_usage.json
- `../audit/` — Phase 0 remaining-issues doc + final audit report
