# 최종 보고 — 저장소 전체 전수 감사 및 통합 결함 수정 (ADR 0078)

- 날짜: 2026-11-12
- 기준 커밋: `main` `2d6616a` (PR #78 병합)
- 지시: 동동, 2026-11-11 "저장소 전체 전수 감사 및 통합 결함 해결 프로젝트" §1–22
- ADR: [docs/adr/0078-integrated-full-audit-20261112.md](../../docs/adr/0078-integrated-full-audit-20261112.md)

용어:
- **검토자 주장**은 영역별 독립 검토 에이전트 8개(A–H)가 보고한 내용이다.
- **작성자 확인**은 이 스레드 작성자가 직접 읽고 실행한 내용이다.
- 둘은 `AUDIT_PROVENANCE_20261111`에서 행을 나눠 기록했다.

## A. 전체 감사 결과

- **저장소 기준:** `2d6616a`. 작업 브랜치는 `claude/project-thread-vjbdnd`이다.
- **디렉터리 구조 (추적 파일 1,946개)**

  | 디렉터리 | 파일 수 | 내용 |
  |---|---|---|
  | `run/` | 1,137 | `run/discovery` 연구 엔진 52개, 엔진 테스트 56개, `run/*.py` 연구 스크립트 137개, `run/audit` 17개 |
  | `results/` | 509 | |
  | `research/` | 167 | |
  | `docs/` | 111 | ADR 포함 |
  | `src/` | 8 | TS 수집기·모니터 |
  | `data/` | 5 | |
  | `.github/workflows/` | 2 | |

- **조사한 소스 파일:** 코드 파일 272개(`.py .sh .ts .yml`)의 위치와 역할을 확인했다.
  - 근거는 inventory(`RESEARCH_SCRIPTS_20261110`, 137개 `run/*.py`)와 import 그래프다(H 검토자, `run/discovery` 런타임 52개).
  - `data/`, `research/`, `results/`는 경로만 확인했다. 내용은 읽지 않았다(보호 데이터).
- **전체 검토 파일 (검토자 주장, 전체 읽음):**
  - `stats_mc.py`, `pipeline.py`(D 검토자), `independent_window.py`, `paper_forward.py`, `data_quality.py`, `portfolio_multi.py`, `generator.py`, `run_cycle.sh`
  - TS 4개, 패키지 매니페스트 3개
  - 2026-11-11 작성자가 전체를 읽은 파일: `evaluator.py`, `costs_bridge.py`, `funding_schedule.py`
- **부분 검토 파일:** 나머지 핵심 모듈은 줄 범위를 지정해 읽었다(각 검토자 보고 §1).
  - `runner.py`(2,107줄)
  - `engine_lib.py`(1,626줄)
  - `evolution.py`
  - `strategy_spec.py`
  - `autoloop.py`
  - `store.py`
  - `fills_import.py`
  - `cost_calibration.py`
  - `portfolio.py`
  - 작성자는 수정한 함수와 그 호출부를 다시 읽었다(provenance `AUTHOR_2026_11_12` 27행, 모두 PARTIAL).
- **미검토 파일**
  - `run/*.py` 137개 중 대부분: 2026-11-10 검토 표(검토자 주장)만 있고, 이번에 새로 읽지 않았다. 현재 실행 경로가 아닌 v1 스크립트다(ADR 0067, 컴파일과 정적 검사만 한다).
  - `run/audit/measure_coverage_56b.py`
  - `public_cost_estimate.py`, `short_top_dev.py`, `bundle_*`, `evalue_v3.py`, `data_manifest.py`, `repro_bundle.py`의 본문
  - 테스트 파일 대부분
- **실제 실행한 테스트**
  - 기존 1,016개(기준선, 3.11/3.13)
  - 신규 55개
  - 재현 스크립트 약 40개(합성 데이터)
  - 전체 실행 결과는 §E에 있다.
- **감사 범위의 한계**
  - "모든 핵심 모듈의 전체 코드 검토" 기준은 **충족하지 못했다**. `runner.py`와 `engine_lib.py`는 부분 검토다.
  - 검토자의 "전체 읽음"은 주장이고, 작성자가 검증하지 않았다.
  - 실제 시장 데이터·실계좌 체결·독립 검증 창은 쓰지 않았다(금지 사항).

## B. 발견한 문제 전체 목록

재현 열의 `T:`는 `run/discovery/tests/test_integrated_audit_20261112.py`의 테스트 이름이다. 모두 `2d6616a`에서 실패하고 수정 후 통과한다. 상태는 모두 FIXED이며, 아래 표의 예외는 따로 적었다.

| ID | 심각도 | 파일/함수 | 문제 | 근본 원인 | 재현 | 수정 |
|---|---|---|---|---|---|---|
| A-01 | Critical | runner.py `merged_bars` | 잠금 TEST·paper replay 창이 월초부터 시작해 선택·검증 데이터를 포함(BTC 잠금 봉 22%, paper 18일이 잠금 창) | 파일 선택용 월초 `d0`를 날짜 컷에도 사용 | T: test_a01_… | 컷은 `start_iso`, 월초는 파일 선택만 |
| G-01 | Critical | paper_forward.py `_evaluate`/`_replay_positions`/`_verdict` | cutoff 뒤 데이터 구멍이 손실 중인 미결 포지션을 표본에서 빼서 PAPER_PASSED | 갭과 겹친 거래를 "제외"로 처리(관찰 불가 ≠ 없음) | T: test_g01_gap_after_cutoff_never_passes | 표본 거래가 관찰 불가면 PAPER_BLOCKED_DATA (`UNOBSERVABLE_SAMPLE_TRADES`), `verify_conclusion`도 검사 |
| ME-01 / C-02 | High | pipeline.py `_run_campaign`, paper 경로, `_study`; cost_sensitivity; IW; runner | 잠금·paper·민감도 연구가 선언 주기 없이 NOMINAL_8H_LEGACY로 계산, 4h 누락 정산 은폐 | 펀딩 목록을 새로 만들며 주기 정보 유실 | T: test_me01_… | 주기 전달 + `merge_funding`, 주기 없으면 `require_funding`으로 차단 |
| D-01 | High | stats_mc.py `stepm`/`step_spa`/`mcs`, power_analysis | `alpha*(B+1)<1`이면 임계값이 최대 뽑기로 고정, 실제 수준 ~1/(B+1)(k≥4 배치에서 명목의 4배 이상) | 분위수 인덱스 clamp | T: test_d01_… (2개) | `alpha*(B+1)<5` 거부, 창별 B 사전등록, 부족하면 BLOCKED_STATISTICS(기각 없음) |
| F-01 | High | runner.py `_resolve`/`_locked_test` | 잠금 TEST 완료 후 최종 기록 전 크래시 → 재시작 시 "이미 소비됨" FAIL_STAT, 실제 결과 유실 | 복구가 레지스트리 결과를 다시 쓰지 않음 | T: test_f01_… | `_recover_consumed`로 저장된 결과에서 최종 기록 재구성, 캠페인 RESULT 1회 |
| F-02 | High | pipeline.py `_run_campaign` | 캠페인 재개 시 잠금 기록·리뷰 중복, `InvalidStatusTransition` 무한 반복 | 단계별 멱등성 없음 | T: test_f02_… | 같은 campaign_id 기록·리뷰 재사용, 상태는 다를 때만 |
| F-03 | High | paper.py `run_paper`, paper_forward `register_plan` | DEGRADED replay 기록 후 크래시 → PAPER_FAILED 유실, forward 등록 가능 | 기록 있으면 거절만 함 | T: test_f03_… | `_apply_run` 재적용 후 거절, DEGRADED면 forward 거절 |
| G-02 | High | paper_forward.py `FileFeed.closed_bars` | 손상 파일을 건너뛰고 뒤 파일 전달 → 영구 갭 | 파일 단위 skip | T: test_g02_… | 막힌 파일 시작부터 전달 중지(`held_back_from_ts`) |
| A-02 | Medium | runner/strategy_spec `_rolling_z`, `funding_settlement_z` 등 | 상수 구간 sd≈1e-20로 큰 z → 가짜 신호 | 0 판정이 정확한 0만 | T: test_a02_… | `nondegenerate_sd` 공용 |
| A-06 | Medium | runner.py `load_fng_series` | 범위 밖·숫자 아님·충돌 값 통과 | 품질 게이트 없음 | T: test_a06_… (3개) | `load_fng_series_checked` 차단 |
| B-01 | Medium | evolution.py identity keys, 변이 연산 | `ret_z.min_history`(≤window)가 효과 없는 쌍둥이 가설 생성 | 키가 파라미터 표기 기준 | T: test_b01_… | `_effective_computation`, `FROZEN_PARAMS` |
| B-03 | Medium | autoloop.py 예산 | robustness 이웃이 `max_specs_total`을 소모(검증 결과가 탐색에 누출) | 예산을 전체 spec 수로 셈 | T: test_b03_… | 탐색 풀 기준 |
| C-01 | Medium | data_quality.py `load_funding_csv_checked` | calc_time 수 ms 지터로 (entry, exit] 경계가 바뀜 | 원시 시각 비교 | T: test_c01_calc_time_jitter_… | 정시 ±60s 이내면 정시로 정렬, 원값 보고 |
| D-02 / D-06 | Medium/Low | pipeline.py `_dsr`, `_pbo` | DSR 시행 수가 현재 맥락·계산 가능 후보로 축소 | `_trial_series`가 맥락 필터 | T: test_d02_d06_… | 탐색 풀 전체, 게이트 = `dsr_at_trials_searched` |
| D-03 | Medium | pipeline.py `preregister_batch` | 상태 디렉터리 간 알파 지출 비공유 | 창 이력을 읽기만 함 | T: test_d03_… | 평가 전 잠금 하에 이력 행 기록 |
| E-02 | Medium | portfolio.py `simulate_portfolio`, `portfolio_passes` | 누락 펀딩을 0으로 계산, 통과 가능 | 기본값 0 | T: test_e02_… | 펀딩 누락 시 거절(mtm-v4) |
| F-04 | Medium | pipeline.py robustness | 기록 후 상태 전 크래시 → RESEARCH_ONLY 고착 | 재개가 기록 존재 시 skip | T: test_f04_… | `_apply_robustness` 재적용 |
| F-05 | Medium | store.py `reconcile` | 원장에서 복구한 재검증 결과를 큐에 넣지 않음 | run_one과 별도 구현 | T: test_f05_… | `enqueue_reverify` 공용 |
| F-06 | Medium | independent_window.py `open_tranche` | 이력 행 후 start 전 크래시 → tranche 소각 | 잠금·재개 경로 없음 | T: test_f06_… | 잠금, `--resume`(드리프트 검사) |
| F-07 | Medium | validation_registry.py | 꼬리 손상 시 영구 사용 불가, 동시 쓰기 시 체인 깨짐 | 원시 읽기, 잠금 없음 | T: test_f07_… (3개) | `scan_jsonl`, flock 직렬화 |
| F-08 | Medium | promotion.py `PromotionQueue` | 꼬리 손상 시 예외 | 원시 append/read | T: test_f08_… | `append_jsonl`/`iter_jsonl` |
| H-01 | Medium | ops.py `run_loop`, runner `Runner.__init__` | 사이클 사이 SIGTERM 무시 | 처리기 미복원 | T: test_h01_… (2개) | 루프가 신호 소유, `restore_signal_handlers` |
| H-02 | Medium | runner.py `main` | full 모드에서 legacy 중단 후에도 evolve 실행 | 중단 상태 미전파 | T: test_h02_… | rc 1, evolve 생략 |
| A-03 | Low | runner.py `merged_bars` | 창 밖 불량 봉이 창 전체를 막음(잠금 창 1비트 누출) | 월 파일 전체 게이트 | T: test_a03_… | 자른 창만 게이트(파싱 불가는 차단 유지) |
| A-04 | Low | runner.py funding 창 | 펀딩이 end_iso에서 잘리지 않음 | 월 단위 | 재현 스크립트(rerunA) | 창 끝 컷 |
| A-08 | Low | paper_forward.py FileFeed | 파일 간 충돌 봉을 조용히 첫 값으로 | 중복만 셈 | T: test_a08_… | 충돌부터 보류, SOURCE_CONFLICT |
| A-09 | Low | runner.py `funding_known_series` | 다음 시가 정산을 결정 시점에 앎 | `<=` 비교 | T: test_a09_… | `<` |
| B-02 | Low | evolution.py | fng_max/fng_min(days=1) 쌍둥이 | 동일 계산 | T: test_b02_… | 효과 계산 키 |
| B-04 | Low | autoloop.py | 이웃과 같은 자식이 DEV 없이 탐색 풀에 | created일 때만 DEV | T: test_b04_… | 상태 없으면 DEV |
| B-05 | Low | autoloop.py | 예산 도달 시 라운드 절반 유실 | 라운드 중간 break | T: test_b05_… | 라운드 단위 |
| B-06 | Low | strategy_spec/engine_lib `_round` | -0.0 표기 | 부호 0 | T: test_b06_… | +0.0 (legacy 순서는 §F) |
| B-07 | Low | pipeline.py `register` | 재시드 시 계보 중복 | 무조건 기록 | T: test_b07_… | 같은 간선 생략 |
| C-03 | Low | funding_schedule.py `settlement_check` | 주기 열 한 해석만 | 두 함수 따로 구현 | T: test_c03_… | `_step_status` 공용(funding-schedule-v2) |
| C-05 | Low | fills_import.py | 결정 로그가 order_id만으로 매칭 | 심볼 범위 누락 | T: test_c05_… | (SYMBOL, order_id), 구형 키 재키잉 |
| C-06 | Low | cost_calibration.py `predicted_bp` | 부분 체결 예측을 요청 금액으로 | 기준 불일치 | T: test_c06_… | 체결 금액 |
| C-09 | Low | cost_sensitivity.py `_row` | 두 심볼 같은 봉 진입 시 변동성 라벨 덮어씀 | 키가 시각만 | T: test_c09_… | (symbol, ts) |
| D-04 | Low | engine_lib.py `pbo_cscv` | T mod S 행 버림 | 나머지 병합 dead code | T: test_d04_… | 마지막 블록에 병합 |
| D-05 | Low | pipeline.py robustness CI | 첫~마지막 청산일만 | 범위 계산 | T: test_d05_… | 검증 창 전체(무거래일 0) |
| E-03 | Low | portfolio.py `simulate_portfolio` | 청산가를 넘어 시작한 봉의 시가에 시간 청산(증거금보다 큰 손실, 청산 0건) | 시가 갭 단계 없음 | T: test_e03_… | 1b 단계(시가 갭 청산) |
| E-04 | Low | portfolio_multi.py | 청산가 넘는 손절이 청산 0건 | 분기 누락 | T: test_e04_… | 청산 이벤트로 셈(손실은 큰 쪽) |
| E-05 | Low | portfolio_multi.py | 거절된 주문의 나머지 다리가 진입 | 다리 독립 처리 | T: test_e05_… | `primary_not_entered` |
| E-06 | Low | portfolio_multi.py `judge` | 거절 비율 없을 때 한도 검사 생략 → PASS | `continue` | T: test_e06_… | UNDETERMINED |
| F-09 | Low | engine_lib.py `ProcessLock` | 같은 PID 잔여 잠금을 살아 있다고 봄(재사용 PID) | pid만 기록 | T: test_f09_… | flock + "pid 시작시각" |
| F-10 | Low | store.py `latest_by_strategy`, ledger_check | prior_pass_reeval이 최신 결과로 | 필터 없음 | T: test_f10_… | 유일할 때만 |
| G-05 | Low | paper_forward.py 문서 | 잠금 설명이 사실과 다름 | 문서 | — | 문서 수정 |
| H-03 | Low | runner.py `main` | rc max가 실패(1)를 4로 덮음 | max 사용 | T: test_h03_… | 1 우선 |
| H-04 | Possible→수정 | research-ci.yml | 30분 타임아웃(기준 실행 ~28분) | | 기준선 1,726s | 75분, 빠른 검사 먼저 |
| H-05 | Low | newlisting_collect.py | discover 계속 실패해도 exit 0 | 실패 미집계 | T: test_h05_… | DISCOVER_FAILING / NO_SUCCESSFUL_DISCOVER |
| H-06 | Low | source_audit_20261110.py `check` | 바뀐 파일이 옛 등급 유지 | 줄 수만 비교 | T: test_h06_… | sha256 고정·변경 표시 |
| H-07 | Low | run_cycle.sh | trap이 mktemp 뒤 | 순서 | shellcheck + 검토 | trap 먼저 |
| H-08 | Low | research-ci.yml | 워크플로·.gitignore 변경 시 CI 안 돎 | paths | — | paths 추가 |
| H-09 | Low | fetch-data.yml, data_fetch.py | 입력 주입, data/ 전체 업로드 | 검증 없음 | T: test_h09_… | 정규식 검증, 업로드 범위 축소 |
| E-01 | Medium | portfolio_multi base 시나리오 | 두 번째 겹친 다리가 증거금으로 거절 → UNDETERMINED | 설정(1x, fraction 1.0) | review/E repro | **ACCEPTED_LIMITATION**(실패를 닫는 방향, 설정 새 버전은 사용자 결정) |
| ME-04 | — | ADR 0010 | 교차 심볼 탐색 영향 | 설계 | — | **ACCEPTED_LIMITATION**(사용자 결정) |
| A-05 | Low | data_quality taker 열 | 열 없으면 0 | 기본값 | — | **ACCEPTED_LIMITATION**(PASS 불가, 실파일엔 열 있음) |
| B-06(일부) | Low | engine_lib legacy id | 심볼 순서·정수/실수 | 표기 | — | **ACCEPTED_LIMITATION**(바꾸면 legacy id 전부 변경) |
| C-04 | Low | cost_calibration VERIFIED 기록 | 손으로 쓴 기록 | 재계산 없음 | review/C repro | **ACCEPTED_LIMITATION**(호출자 없음, 실체결 후) |
| C-07 | Low | public_cost_estimate | 손으로 쓴 추정 | | review/C repro | **ACCEPTED_LIMITATION**(연구 전용) |
| H-10 | — | git_backup_push | 구형 | | — | **INTENTIONALLY_RETIRED**(LEGACY_SCRIPTS DEPRECATED) |

**확인하지 못한 위험 (POSSIBLE, 재현 안 됨)**

| ID | 위험 |
|---|---|
| A-10 | IW 종목군 생존 편향. 사전등록 설계 문제이고, 여는 것이 금지라 검증하지 못했다 |
| A-11 | 롤링 특징의 갭 처리 |
| B-08 | 근접 중복 판정의 경계 |
| C-08 | 비용 프로필 fee_rate 교차 검사 부재 |
| E-07 | 포트폴리오 무작위 불변식. 300건은 정상이었다 |
| E-08 | 다중 심볼 cross 마진 경계 |
| F-11 | 잠금 밖의 다른 기록자 |
| G-03 | replay와 평가기가 원시 함수를 공유한다(ADR 0077 알려진 한계) |
| G-04 | FNG 스냅샷 없음 |
| H-11 | TS 모니터 편향 |
| H-12 | `liquid_search_dev` import 경고 |
| — | 공개 수집기의 심볼별 실패 카운터 |
| — | PBO 행렬이 현재 맥락만 씀. 시행 수는 기록한다 |

**NOT_REPRODUCED (검토자 확인):**
- engine JSONL 꼬리 손상 처리
- 봉 품질 게이트(중복·역전·NaN·inf·누락·off-grid·음수)
- 특징 prefix 불변성(look-ahead 없음)
- 포트폴리오 불변식 300건

## C. 영역별 감사 결과

| # | 영역 | 결과 | 근거 |
|---|---|---|---|
| 1 | 아키텍처 | 정상(수정 후) | 진입점: `runner.main`의 legacy·evolve·full, `ops`, `run_cycle.sh`. 상태 디렉터리·원장 구분은 H 검토자의 import 그래프로 확인. full 모드의 중단 전파 결함(H-02)은 수정 |
| 2 | 데이터 품질 | 수정 | A-03, A-06, A-08, G-02, C-01. 봉 품질 게이트는 NOT_REPRODUCED(정상) |
| 3 | Look-ahead·인과성 | 수정 | A-01(Critical), A-09. prefix 불변성 검사에서 미래 데이터 누출 없음 |
| 4 | 전략 생성 | 정상(제한) | generator·legacy seed는 결정적. B-06 legacy 표기는 수용 |
| 5 | 전략 진화 | 수정 | B-01~B-05, B-07 |
| 6 | 백테스트 | 수정 | A-02, ME-01. 진입은 다음 봉 시가, PrefixView 강제 확인 |
| 7 | 체결 시뮬레이션 | 정상(제한) | 손절 보수 체결(ADR 0031) 유지. E-04·E-05 수정 |
| 8 | Funding | 수정 | ME-01/C-02, C-01, C-03, A-04, A-09 |
| 9 | 거래비용 | 수정(외부 제한) | C-05, C-06, C-09. VERIFIED는 실계좌 체결 필요(§F) |
| 10 | 통계 검증 | 수정 | D-01(High), D-04, D-05 |
| 11 | 다중검정 | 수정 | D-02, D-03, D-06. StepM 알파 지출 공유 |
| 12 | 포트폴리오 | 수정(제한) | E-02~E-06. E-01은 수용 |
| 13 | 상태 관리 | 수정 | F-02, F-04, F-05, F-10 |
| 14 | 원장 무결성 | 수정 | F-07, F-08. 꼬리 손상은 격리, 내부 손상은 실패로 닫음 |
| 15 | 오류 복구 | 수정 | F-01, F-03, F-06. 크래시 지점별 재현 테스트 |
| 16 | 동시 실행 | 수정(POSSIBLE 남음) | F-07, F-09, D-03 잠금. F-11은 미확인 |
| 17 | Paper Forward | 수정 | G-01(Critical), G-02, F-03, G-05. ADR 0073~0077 검사 유지 |
| 18 | 운영 스크립트 | 수정 | H-01, H-02, H-03, H-05, H-07 |
| 19 | 보안 | 수정 | H-09(입력 주입·업로드 범위). 실거래·키·주문 코드 없음(H 검토자 grep) |
| 20 | CI 및 테스트 | 수정 | H-04, H-08. 결과는 §E·§G |
| 21 | 문서·감사표 | 수정 | H-06 신선도 검사. provenance 행 분리. 등급 상향 없음 |

## D. 실제 수정 내용

- **수정한 파일:** 런타임 27개, 감사 스크립트 3개, 워크플로 2개, 테스트 4개(픽스처), 신규 테스트 1개, 생성 표 5개, 문서. 목록은 PR 파일 목록에 있다.
- **수정한 함수:** §B의 "파일/함수" 열. 주요 함수:
  - runner: `merged_bars`, `load_fng_series_checked`, `funding_known_series`, `_resolve`, `_recover_consumed`, `_locked_verdict`, `main`
  - pipeline: `_inputs`, `preregister_batch`, `_evaluate_batch`, `_dsr`, `_pbo`, `robustness`, `_apply_robustness`, `_run_campaign`, `promotion_review`, `register`
  - paper_forward: `FileFeed.closed_bars`, `_replay_positions`, `_unobservable_in_sample`, `_verdict`, `_conclusion_problems`, `register_plan`
  - engine_lib: `ProcessLock`, `process_lock_status`, `exclusive_file_lock`, `pbo_cscv`
- **공통 구조 개선:** ADR 0078 §3에 정리했다. 공통 함수는 다음과 같다.
  - `nondegenerate_sd`
  - `merge_funding`
  - `bootstrap_B_for` / `_require_resolution`
  - `exclusive_file_lock` / `process_lock_status`
  - `enqueue_reverify`
  - `_apply_robustness` / `_apply_review` / `_apply_run`
  - `_step_status` 공용(settlement_check)
- **기존 동작에 미치는 영향:**
  - 펀딩 주기가 없는 잠금·paper 연구는 이제 막힌다. 전에는 8h로 가정했다.
  - 작은 알파 창은 B가 커져서 실행 시간이 늘어난다.
  - 관찰할 수 없는 표본 거래가 있으면 forward 판정을 보류한다.
  - 버전 문자열이 바뀌어 맥락 ID가 새로 생긴다.
- **호환성:**
  - 기존 원장·상태 파일은 그대로 읽힌다.
  - ProcessLock 옛 형식도 처리한다.
  - `fills_import`는 구형 결정 dict도 받는다.
  - 저장된 결과 파일(`data/`, `research/`, `results/`, `state`)은 수정하지 않았다.
- **기존 기능 회귀 여부:**
  - 기존 테스트 1,016개는 지우거나 완화하지 않았다.
  - 픽스처 4곳은 선언 주기를 넣도록 바꿨다.
  - 4h→8h 테스트는 두 해석 판정에 따라 SCHEDULE_UNKNOWN을 기대한다. 단언을 강화한 것이고 완화하지 않았다.
  - 첫 Actions 실행에서 기존 테스트 3개가 실패했다(로컬 전체 실행 전에 푸시함). 세 곳을 고쳤다(ADR 0078 §4).
    - 손절>청산가: 청산 0→1건(E-04, 더 엄격).
    - 30분→2시간 펀딩 픽스처를 규칙적인 30분 주기로 바꿨다(C-03). 단언은 같다.
    - 변동성 라벨 키를 (symbol, ts)로 바꿨다(C-09).

## E. 테스트 결과

로컬은 GitHub Actions `research-ci.yml`과 같은 단계를 `local_ci.sh`로 돌렸다. 결과는 아래 표에 채운다. 실행하지 못한 항목은 "미실행"으로 적는다.

| 항목 | Python 3.11 | Python 3.13 |
|---|---|---|
| 기존 테스트 | (CI_311_EXISTING) | (CI_313_EXISTING) |
| 신규 테스트(55) | (CI_311_NEW) | (CI_313_NEW) |
| 전체 unit(discovery) | (CI_311_UNIT) | (CI_313_UNIT) |
| 루트 연구 테스트 / legacy | (CI_311_ROOT) | (CI_313_ROOT) |
| 정적 검사(ruff E9/F63/F7/F82, F 전체) | (CI_311_RUFF) | (CI_313_RUFF) |
| Shell(bash -n, shellcheck) | (CI_311_SHELL) | (CI_313_SHELL) |
| TypeScript(bun transpile) | (CI_311_TS) | (CI_313_TS) |
| 원장 검사(ledger_check . / state) | (CI_311_LEDGER) | (CI_313_LEDGER) |
| source audit check | (CI_311_SA) | (CI_313_SA) |
| inventory 최신성 | (CI_311_INV) | (CI_313_INV) |
| provenance check | (CI_311_PROV) | (CI_313_PROV) |
| GitHub Actions | §G 참조 | §G 참조 |

수정 전 `2d6616a`에서 신규 테스트를 돌리면 55개 중 52개가 실패한다. 나머지 3개는 대조군이다.

## F. 미해결 문제 및 외부 제한

- **해결하지 못한 코드 문제:**
  - E-01: 설정 새 버전이 필요하다(사용자 결정).
  - ME-04: 사용자 결정이 필요하다.
  - legacy id 표기(B-06 일부).
  - POSSIBLE 13건(§B).
- **자료가 없어 검증하지 못한 항목:**
  - 실제 시장 데이터로 다시 돌린 결과. 금지 사항이라 하지 않았고, 이번 수정 뒤 실제 DEV 결과가 어떻게 바뀌는지는 모른다.
  - 다중 심볼 Paper Forward 전체 경로(합성 픽스처가 없다).
- **실제 계좌 증거가 필요한 항목:**
  - 비용 VERIFIED: 심볼마다 실계좌 체결 200건 이상이 필요하다. OKX 기록은 DEMO라 쓸 수 없다.
  - C-04 재계산.
- **통계적 검정력이 부족한 항목:** SR 4에서도 80%에 미치지 못한다(ADR 0064). 현재 생존 전략은 0이다. D-01 수정으로 작은 알파 창의 판정이 더 보수적으로 바뀐다(BLOCKED_STATISTICS 가능).
- **독립 검증 창이 필요한 항목:** IW1, 잠금 TEST, 전진 관측. 열지 않았고 사전등록도 활성화하지 않았다.
- **보류한 폐기 코드 문제:**
  - H-10.
  - `portfolio_sim.py`·`funding_cost.py`의 v1 진단은 실행 경로 밖이다(ADR 0070).
  - `run/*.py` v1 스크립트는 컴파일과 정적 검사만 한다.
- **향후 해결 조건:**
  - 실계좌 체결 수집.
  - 포트폴리오 설정 v3 결정(E-01).
  - `runner.py`·`engine_lib.py` 전체 검토(작성자 FULL).
  - F-11 기록자 전수 조사.

## G. GitHub 반영 결과

| 항목 | 값 |
|---|---|
| 작업 브랜치 | `claude/project-thread-vjbdnd` |
| 커밋 ID | (GH_COMMITS) |
| PR | (GH_PR) |
| 변경 파일 수 / 추가·삭제 줄 | (GH_DIFFSTAT) |
| GitHub Actions | (GH_ACTIONS) |
| main 병합 | (GH_MERGE) |

## H. 최종 판정

(VERDICT)
