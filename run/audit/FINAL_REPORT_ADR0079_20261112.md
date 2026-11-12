# ADR 0078 보완 감사 보고서 (ADR 0079, 2026-11-12)

기준: PR #79 HEAD `757114f`. 지시: 사용자 동동 2026-11-12 14:00Z. 결정 기록: [ADR 0079](../../docs/adr/0079-audit-followup-d01-resolution-20261112.md).
테스트: `run/discovery/tests/test_audit_followup_20261112.py` (33개).

## A. 수정 전 발견한 문제

| ID | 심각도 | 내용 |
|---|---|---|
| D-01b | Critical | `_require_resolution`이 `alpha*(B+1) < 1`만 거부. ADR 0078·`bootstrap_B_for`·방법 태그는 5. 1 이상 5 미만이 통과해 이전 버전 배치(B=164, alpha 0.0152)가 PASSED_STEPM |
| D-01c | High | 해상도 부족 배치에서 StepM 차단 후 `rejected=[]` → 검정력 있는 멤버가 REJECTED로 기록(통계적 기각처럼 보임) |
| D-01d | Medium | `step_spa`·`mcs`의 해상도 거부가 `spa_error`·`mcs_error`에 섞여 보이지 않음 |
| D-01e | Medium | alpha·B 형식 검사 없음(B=99.5, "1000", True, NaN 등이 그대로 진행하거나 TypeError) |
| D-01f | Medium | IW: 해상도 부족이면 등록·이력 기록 뒤 실행 중 예외 → tranche만 소모 |
| G-04 | High | FNG 전략 Paper Forward 계획이 FNG를 고정하지 않음. 등록 후 원본 FNG가 바뀌어도 감지 없이 PAPER_PASSED(합성 provider) |
| G-03 | 한계(결함 아님) | 재생과 평가기가 신호·지표·펀딩·청산·비용·입력 함수를 공유. 그 안의 결함은 재생으로 못 잡음 |
| F-11a | High | `StatusStore.set`이 메모리 캐시로 전이 판단 → 종결(REJECTED) 뒤 VALIDATION_SURVIVOR 기록 가능(인스턴스 2개) |
| F-11b | High | `CampaignLedger.preregister` 잠금 없음 → 같은 잠금 창 캠페인 번호 1 두 번(알파 두 번) |
| F-11c | Medium | `register_plan` 잠금 없음 → 한 전략 계획 두 개 |
| F-11d | Low | 이력 파일이 없을 때 배치 원장 잠금 없음, `contexts.jsonl` 중복 행 |
| R-1 | Medium | runner: `KeyError`·`IndexError`(미래 봉 읽기 포함)를 DATA_MISSING(재검증 가능)으로 기록 |

### F-11 호출부 분류

| 파일 (경로) | 쓰는 쪽 | 상태 디렉터리 간 공유 | 동시 쓰기 | 읽기-판단-쓰기 잠금 | 조치 |
|---|---|---|---|---|---|
| 검증 창 이력 `validation_window_history.jsonl` | pipeline 사전등록, IW open | 예 | 예 | 예(`exclusive_file_lock`, ADR 0078) | 유지 |
| IW 레지스트리 `validation_registry` | IW | 예 | 예 | 예(`_serialized` flock) | 유지 |
| 잠금 TEST 레지스트리 `lock_test_usage.json` | runner, pipeline | 디렉터리별 | 같은 디렉터리 내 | 예(`_txn` flock + 원자 쓰기) | 유지. 디렉터리 간 공유 안 됨(§F) |
| 캠페인 원장 `locked_window_campaigns.jsonl` | runner, pipeline | 디렉터리별 | 같은 디렉터리 내 | **아니오** | 수정(F-11b) |
| 상태 `evolution/statuses.jsonl` | pipeline 전체 | 아니오 | run.lock 밖 라이브러리 사용 시 | **아니오(캐시)** | 수정(F-11a) |
| 평가 기록 `evolution/evaluations.jsonl` | pipeline | 아니오 | run.lock으로 직렬 | 추가만(판단은 배치 재개 대조) | 유지(§F R-6) |
| 배치 원장 `validation_batches.jsonl` | pipeline | 아니오 | run.lock | 이력 있을 때만 | 수정(F-11d) |
| 맥락 `contexts.jsonl` | pipeline | 아니오 | 연구 스크립트도 인스턴스 생성 | 아니오 | 수정(F-11d) |
| 원장 `discovery_ledger.jsonl`, `state.json`, `queue.json` | runner | 아니오 | run.lock | 추가·원자 쓰기 | 유지 |
| 승격 대기열 `promotion_queue.jsonl` | runner `_promote` | 아니오 | run.lock | 중복 검사 후 추가(run.lock 안) | 유지 |
| 사전등록·FDR `preregistration.json`, `fdr_decisions.jsonl` | runner | 아니오 | run.lock | 충돌 감지 후 추가 | 유지 |
| 연구 피드백 `research_feedback.jsonl` | paper, paper_forward | 아니오 | run.lock / 계획별 step 잠금 | 계획 단위 판단 | 유지 |
| Paper Forward 계획 `forward/plans.jsonl` | `register_plan` | 아니오 | autoloop(run.lock), 라이브러리 | **아니오** | 수정(F-11c) |
| Paper Forward state·로그·원장 (계획 디렉터리) | step | 아니오 | 계획별 step.lock + CAS(ADR 0077) | 예 | 유지 |
| 비용 보정 원장, fills 가져오기 | CLI | 경로별 | 사용자가 한 번 실행 | 추가 / sha 이름 파일 | 유지 |
| 검색 상태, 사이클 요약 | autoloop, runner | 아니오 | run.lock | 원자 쓰기 | 유지 |

## B. 원인

- D-01b: 경계 기준이 세 곳(문서·`bootstrap_B_for`·검사)에 따로 적혀 있었고, 검사에만 1.0이 남았다. 경계 구간(1~5)을 직접 호출하는 테스트가 없었다(ADR 0078 테스트는 alpha*(B+1)<1 사례만).
- D-01c: 차단을 "기각 없음"으로 표현해 기존 판정 경로(검정력 있음 + 기각 없음 = REJECTED)가 그대로 탔다.
- G-04: Paper Forward의 불변 증거(워밍업 sha256, 해시 체인 로그)가 가격·펀딩만 다뤘고, FNG는 연구용 provider에서 매번 읽었다. 전진 피드에 FNG 원천이 없다.
- F-11: 동시성 보호가 "상태 디렉터리 하나에 프로세스 하나(run.lock)"라는 운영 전제에 기대어, 저장소 객체 자체는 캐시로 판단하거나 잠금 없이 읽고 썼다.
- R-1: `except (FileNotFoundError, LookupError)`가 의도한 "데이터 없음" 예외와 함께 그 하위 클래스(`KeyError`, `IndexError`)를 잡았다.

## C. 바꾼 파일과 함수

- `run/discovery/stats_mc.py`: `MIN_TAIL_DRAWS`(단일 기준), `_check_alpha`, `_check_B`, `_resolved`(Fraction), `_min_resolving_B`, `bootstrap_B_for`(입력 검사), `_require_resolution`(5 기준), `stepm`·`step_spa`·`mcs`(검사 통합), `MC_METHOD_VERSION` mc-v5
- `run/discovery/pipeline.py`: `_evaluate_batch`(BLOCKED_STATISTICS 결과·상태·기록, `step_spa_blocked`, `mcs_blocked`, 재개 대조), `FAILURE_CLASSES`, `outcome_label`, `StatusStore`(`_disk_size`, `set` 잠금, `_set_locked`), `context_id`(잠금), `_history_guard`(배치 원장 잠금)
- `run/discovery/independent_window.py`: `_open_tranche`(개시 전 해상도 거부, 실행 중 차단 기록)
- `run/discovery/validation_registry.py`: `MEMBER_OUTCOMES`에 BLOCKED_STATISTICS
- `run/discovery/paper_forward.py`: `FNG_NOT_PINNED`, `register_plan`(잠금) / `_register_plan`(FNG 거부), `_fng_refusal`, `_plan_fng_problem`, `_verdict`, `_conclusion_problems`, `_record_problems`
- `run/discovery/campaigns.py`: `preregister`(잠금) / `_preregister`
- `run/discovery/runner.py`: `run_one`, `reevaluate_prior_pass`, `_logic_error`
- 테스트: `tests/test_audit_followup_20261112.py`(신규), `tests/test_pdf_check_20261022.py`(B 49→99, 메시지 확인)
- 감사: `run/audit/audit_provenance_20261111.py`(`AUTHOR_2026_11_12B`, FULL sha256 고정 검사), 출처표 재생성
- 문서: ADR 0079, ADR 목록, worklog, 이 보고서, `STATUS_CURRENT_20261112.md`

## D. 수정 전 재현 결과 (`757114f`에 새 테스트 파일만 복사해 실행)

`Ran 33 tests — FAILED (failures=59, errors=31)` (하위 테스트 포함 수). 테스트 단위로 22개 실패, 11개 통과. 통과한 11개는 대조(현재 배치·FNG 없는 계획·의도된 LookupError·허용 구간·p값 검정)와 문서 고정(G-03 공유 목록, 잠금 도구 동작, power_analysis B) 테스트다. 주요 재현:

- `test_one_to_five_band_is_blocked`: stepm·step_spa·mcs × 6조합 모두 거부되지 않음(18 실패)
- `test_legacy_small_B_batch_is_blocked_not_rejected`: `'PASSED_STEPM' != 'BLOCKED_STATISTICS'`
- `test_fng_changed_mid_plan_never_passes`: `'PAPER_PASSED' == 'PAPER_PASSED'`
- `test_status_transition_decided_on_the_file_not_a_stale_cache`, `test_campaign_index_is_taken_once`(인덱스 [1, 1]), `test_one_plan_per_strategy_under_concurrent_registration`
- `test_lookahead_index_error_is_a_logic_error`: `'DATA_MISSING' != 'ERROR'`

## E. 수정 후 결과

- 새 테스트 33개: 3.13 로컬 통과(아래 전체 실행에 포함)
- 전체 결과(로컬, 이 브랜치): §J 참조. 기존 테스트를 실제로 실행하지 못한 부분이 있으면 그렇게 적는다.

## F. 남은 위험

| ID | 내용 | 판단 |
|---|---|---|
| R-2 | `split_windows`: 데이터가 검증 시작 뒤에 시작하면 dev 창 (0, 2)가 검증 창과 겹침. 봉 2개로는 사건이 없어 판정 불가 | POSSIBLE, 저영향. 창 규칙 변경은 데이터 정책 버전 변경이라 미수정 |
| R-3 | `trigger_data_driven_reverify`: 잠금 심볼은 최신 파일이 상한을 넘으면 건너뜀 → 상한 전 데이터가 채워져도 재검증 예약 누락 | 운영 문제, 거짓 PASS 아님 |
| R-4 | `load_fng_series_checked`: 날짜만 있고 값이 없는 행은 BLOCKED가 아니라 구멍(gap)으로 처리 | 저영향 |
| R-5 | 잠금 TEST 실행 중 예외 → 시도 FAILED(노출됨) → 다음 실행에서 "이미 사용" FAIL_STAT | 보수적(거짓 PASS 없음) |
| R-6 | `evaluations.jsonl` 등 pipeline 기록은 run.lock 밖 동시 사용 시 중복 기록 가능(배치 재개 대조로 불일치는 막힘) | run.lock 전제 |
| E-1 | 잠금 TEST 레지스트리의 죽은 소유자 복구가 같은 호스트만 → 컨테이너 호스트명이 바뀌면 IN_PROGRESS가 영구히 남음 | 막히는 쪽(fail-closed) |
| E-2 | `append_jsonl`의 꼬리 복구는 잠금 밖 → 동시 추가 중인 다른 쓰기를 찢긴 꼬리로 볼 수 있음 | 공유 파일은 잠금 안에서 씀 |
| E-3 | 잠금 레지스트리에서 schema 키가 없는 dict는 v1로 감싸 다음 거래 때 다시 씀 | 손상 파일에서만 |
| E-4 | `estimate_corr_dev`는 길이가 다른 입력을 잘라 맞춤. 호출부 없음(사용 안 됨) | 기록 |
| L-1 | 잠금 TEST 캠페인 수·사용 기록이 상태 디렉터리마다 따로 → 두 디렉터리가 같은 창에서 각자 캠페인 1 | 사용자 결정(§H) |
| G-03 | 재생과 평가기 공유 함수 안의 결함은 재생으로 검출 불가. 금액(순손익·비용·펀딩)과 체결 시뮬레이션은 재생이 확인하지 않음 | 한계로 기록, 테스트로 목록 고정 |

ADR 0078 보고서의 POSSIBLE 13건 중 runner/engine_lib 관련 항목은 이번 전수 검토로 위 표로 대체한다. 다른 모듈 항목은 그대로 미확인.

## G. 검토 범위

| 파일 | 수준 | 근거 |
|---|---|---|
| `run/discovery/runner.py` | **FULL** (작성자, 2,107줄 전부) | 출처표 `AUTHOR_2026_11_12B`, sha256 고정 |
| `run/discovery/engine_lib.py` | **FULL** (작성자, 1,626줄 전부) | 같음 |
| `stats_mc.py`, `pipeline.py`, `independent_window.py`, `validation_registry.py`, `paper_forward.py`, `campaigns.py` | PARTIAL (바꾼 함수와 호출부) | 같음 |
| 위 F-11 표의 저장소 클래스(`store.py`, `promotion.py`, `generation.py`, `search.py`, `autoloop.py`, `paper.py`, `ops.py`, `fills_import.py`, `cost_calibration.py`) | PARTIAL (쓰기 호출부와 경로만) | 이 보고서 §A |
| 그 밖의 파일 | NOT REVIEWED (이번 작업) | 2026-11-10 검토자 "전체 읽음"은 주장으로만 남김 |

검토 에이전트는 이번 작업에 쓰지 않았다. FULL은 작성자가 직접 읽은 파일에만 기록했다.

## H. 외부 증거가 필요한 것

- 비용 VERIFIED: 실계좌 체결 200건/심볼(OKX 기록은 DEMO). 변경 없음.
- FNG 전진 관측: FNG 원천(외부 API)을 전진 피드에 추가할지 사용자 결정. 이번 작업은 네트워크를 쓰지 않았다.
- 잠금 TEST 캠페인 수를 상태 디렉터리 간 공유할지(L-1), ADR 0078 E-01·ME-04: 사용자 결정.

## I. 기존 데이터·기록 변경 여부

없음. `data/`, `research/`, `results/`, state, 원장, `validation_window_history.jsonl`, PREREG, `private_fills/`를 쓰거나 지우지 않았다. 테스트는 합성 데이터·mock·임시 디렉터리만 썼다(IW 테스트는 실제 이력 파일을 읽어 임시 파일로 복사만 한다). 잠금 TEST·IW0·IW1·봉인 데이터·사전등록 창을 열지 않았다. 과거 기록을 새 규칙으로 재평가하지 않았다.

## J. PR·Actions 상태

PR #79에 이어서 커밋(main 직접 커밋·병합 없음). 로컬·Actions 결과는 아래에 실행 후 기록한다.

## K. 판정

§J 결과 기록 후 확정.
