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

나머지 행은 `AUDIT_PROVENANCE_20261111.jsonl` 참조.
