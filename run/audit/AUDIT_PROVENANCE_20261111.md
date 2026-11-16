# 감사 증거 출처표 (2026-11-11, ADR 0071)

`SOURCE_AUDIT_20261110`의 `lines_read == lines_total`은 **독립 검토 에이전트의 주장**이다. 작성자가 파일 전체를 읽었다는 증거가 아니다. 이 표는 주장과 작성자 직접 확인을 분리한다.

- 검토 에이전트 행: 124개 (모두 '전체 읽음' 주장, 작성자 미검증)
- 2026-11-10 작성자 부분 재확인(SPOT, 지목된 주장만): 5개 — alt_funding_r2_diag.py, liquid_search_dev.py, run/discovery/newlisting_collect.py, run/discovery/repro_bundle.py, run/discovery/store.py
- 2026-11-11 작성자 직접 재검토: 8개 (아래)
- 2026-11-10 표에는 핵심 엔진 모듈(pipeline, portfolio_multi, evaluator, costs_bridge, data_quality)의 행이 없었다. 아래 작성자 행이 처음이다.

| 파일 | 작성자 재검토 | 범위 | 실행 | 결함 재현 | 수정 | 회귀 테스트 |
|---|---|---|---|---|---|---|
| `run/discovery/costs_bridge.py` | FULL | all 416 lines; no defect found | 예 | 아니오 | - | - |
| `run/discovery/data_quality.py` | PARTIAL | L400-521 funding loader / gaps / quality | 예 | 아니오 | - | - |
| `run/discovery/evaluator.py` | FULL | all 264 lines | 예 | 예 | ADR 0071 | tests/test_portfolio_core_defects_20261111.py |
| `run/discovery/funding_cost.py` | PARTIAL | L1-200 (legacy pandas module, only imported by the DEPRECATED portfolio_sim.py); not in the runtime path | 예 | 아니오 | - | - |
| `run/discovery/funding_schedule.py` | FULL | new module (ADR 0071) | 예 | 아니오 | ADR 0071 | tests/test_portfolio_core_defects_20261111.py |
| `run/discovery/pipeline.py` | PARTIAL | L55-120 constants/transitions, L160-182 config load, L245-300 data provider, L436-720 identity/study/DEV, L839-1000 validation batch, L1175-1810 robustness/portfolio/locked/promotion; not re-read: search/variation helpers, legacy seeding | 예 | 예 | ADR 0071 | tests/test_portfolio_core_defects_20261111.py |
| `run/discovery/portfolio_multi.py` | FULL | all 808 lines before the change; rewritten parts re-read after the change | 예 | 예 | ADR 0071 | tests/test_portfolio_core_defects_20261111.py |
| `run/discovery/tests/test_portfolio_multi_20261110.py` | FULL | all tests read and run | 예 | 아니오 | - | - |

## 2026-11-12 작성자 수정 (ADR 0078 통합 전수 감사)

- 27개 파일. 범위는 **변경한 함수와 그 호출부만** 다시 읽음(PARTIAL). 감사 등급을 올리지 않는다. 같은 날 검토 에이전트 보고는 ADR 0078 보고서에 '검토자 주장'으로 따로 둔다.

| 파일 | 작성자 재검토 | 범위 | 실행 | 결함 재현 | 수정 | 회귀 테스트 |
|---|---|---|---|---|---|---|
| `run/audit/source_audit_20261110.py` | PARTIAL | H-06 reviewed_against sha256 freshness | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/autoloop.py` | PARTIAL | B-03 budget = search pool, B-04 dev on existing, B-05 whole rounds | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/cost_calibration.py` | PARTIAL | C-06 prediction on the filled notional | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/cost_sensitivity.py` | PARTIAL | C-02 declared funding schedule, C-09 per-symbol volatility label | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/data_fetch.py` | PARTIAL | H-09 symbol / month validation | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/data_quality.py` | PARTIAL | C-01 calc_time snapping in load_funding_csv_checked | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/engine_lib.py` | PARTIAL | D-04 pbo_cscv remainder, F-09 ProcessLock / process_lock_status / exclusive_file_lock, B-06 _round_floats | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/evolution.py` | PARTIAL | B-01/B-02 effective computation keys, FROZEN_PARAMS | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/fills_import.py` | PARTIAL | C-05 decision log keyed by (symbol, order_id) | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/funding_schedule.py` | PARTIAL | C-03 settlement_check under both interval readings | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/independent_window.py` | PARTIAL | F-06 open_tranche under lock, resume of a registered-not-started run | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/ledger_check.py` | PARTIAL | F-09 lock liveness, F-10 latest outcome | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/newlisting_collect.py` | PARTIAL | H-05 discover failures are a failure | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/ops.py` | PARTIAL | H-01 loop-owned SIGTERM/SIGINT, health lock status | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/paper.py` | PARTIAL | F-03 recorded replay re-applied before refusal | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/paper_forward.py` | PARTIAL | G-01 unobservable sample trades block, G-02/A-08 FileFeed hold-back, F-03 degraded replay refusal, G-05 docstring | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/pipeline.py` | PARTIAL | _inputs, preregister_batch, _evaluate_batch, _dsr, _pbo, robustness CI, _apply_robustness, _run_campaign, promotion_review, register, neighbours (D-01/02/03/05/06, ME-01, F-02, F-04, B-03, B-07) | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/portfolio.py` | PARTIAL | E-02 funding not zero-filled, E-03 open-gap liquidation | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/portfolio_multi.py` | PARTIAL | E-04 stop beyond liquidation, E-05 remainder of refused primary, E-06 unknown refusal share | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/power_analysis.py` | PARTIAL | D-01 B scaled to alpha | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/promotion.py` | PARTIAL | F-08 PromotionQueue on append_jsonl / iter_jsonl | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/run_cycle.sh` | PARTIAL | H-07 traps before mktemp (bash -n + shellcheck; no unit test) | 예 | 예 | ADR 0078 | - |
| `run/discovery/runner.py` | PARTIAL | A-01 merged_bars cut, A-02 z guards, A-03 window-only gate, A-06 FNG gate, A-09 funding known, F-01 locked crash recovery, H-01/H-02/H-03 signals and exit code | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/stats_mc.py` | PARTIAL | D-01 bootstrap resolution (stepm / step_spa / mcs) | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/store.py` | PARTIAL | F-05 enqueue_reverify in reconcile, F-10 latest_by_strategy | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/strategy_spec.py` | PARTIAL | A-02 nondegenerate_sd, B-06 -0.0 | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |
| `run/discovery/validation_registry.py` | PARTIAL | F-07 torn tail tolerated, interior corruption fails, flock-serialised writes | 예 | 예 | ADR 0078 | run/discovery/tests/test_integrated_audit_20261112.py |

## 2026-11-12 후속 감사 (ADR 0079)

- FULL = 작성자가 이 스레드에서 파일의 모든 줄을 읽음 (`reviewed_sha256`의 판). 파일이 바뀌면 `check`가 실패한다(재검토하거나 PARTIAL로 내릴 것). 검토 에이전트의 주장은 FULL로 기록하지 않는다.

| 파일 | 작성자 재검토 | 범위 | 실행 | 결함 재현 | 수정 | 회귀 테스트 |
|---|---|---|---|---|---|---|
| `run/discovery/campaigns.py` | PARTIAL | F-11 preregister under the ledger lock | 예 | 예 | ADR 0079 | run/discovery/tests/test_audit_followup_20261112.py |
| `run/discovery/engine_lib.py` | FULL | all 1,626 lines: state machine, JSONL, ids, statistics, verdicts, locked registry, locks; findings E-1..E-4 recorded, no fix | 예 | 아니오 | - | run/discovery/tests/test_audit_followup_20261112.py |
| `run/discovery/independent_window.py` | PARTIAL | D-01 resolution refused before opening, in-run block | 예 | 예 | ADR 0079 | run/discovery/tests/test_audit_followup_20261112.py |
| `run/discovery/paper_forward.py` | PARTIAL | G-03 evaluator/replay shared calls, G-04 FNG refusal/block, F-11 register_plan lock | 예 | 예 | ADR 0079 | run/discovery/tests/test_audit_followup_20261112.py |
| `run/discovery/pipeline.py` | PARTIAL | D-01 _evaluate_batch blocked windows, F-11 StatusStore.set, context_id, _history_guard | 예 | 예 | ADR 0079 | run/discovery/tests/test_audit_followup_20261112.py |
| `run/discovery/runner.py` | FULL | all 2,107 lines (ADR 0079 R-1 fix included): input/state/recovery/locks/exit codes/look-ahead; findings R-1 (fixed), R-2..R-5 (recorded) | 예 | 예 | ADR 0079 | run/discovery/tests/test_audit_followup_20261112.py |
| `run/discovery/stats_mc.py` | PARTIAL | D-01 _require_resolution / bootstrap_B_for / alpha-B checks, stepm, step_spa, mcs | 예 | 예 | ADR 0079 | run/discovery/tests/test_audit_followup_20261112.py |
| `run/discovery/validation_registry.py` | PARTIAL | BLOCKED_STATISTICS member outcome | 예 | 아니오 | ADR 0079 | run/discovery/tests/test_audit_followup_20261112.py |

나머지 행은 `AUDIT_PROVENANCE_20261111.jsonl` 참조.
