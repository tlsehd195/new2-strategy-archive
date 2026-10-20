# 전체 감사 및 미완성 기능 완성 보고서 — 2026-10-20

- 범위: 전략 생성, 진화, 백테스트, 비용 검증, 독립 검증, 상태 기록. 연구와 백테스트만 다룬다.
- NEW2 매매 시스템(`tlsehd195/NEW2`)과의 연결: 만들지 않았다. 데이터·API·파일 전달·공통 파이프라인 없음. 실제 주문이나 계정 거래 없음.
- 사용자 결정(동동, 2026-10-20 카드 응답 "읽기 전용 허용"): 테스트와 실데이터 실행에 필요한 비용 모델 코드만 NEW2 고정 커밋 `8ec6371`에서 읽기 전용으로 받았다.
- 증거 폴더: `run/audit/evidence_audit_20261020/`. 결정 기록: [ADR 0013](../../docs/adr/0013-audit-search-pool-cost-evidence-alpha-history.md).
- 기능 상태 표기: **완료** / **부분 구현** / **미구현** / **검증 실패** / **검증 대기**.

---

## 1. 저장소 상태와 커밋

| 항목 | 값 |
|---|---|
| 기준 | `main` `900c71c` (PR #11 병합) |
| 작업 브랜치 | `claude/project-thread-vabu8n` |
| 커밋 | `8a22e73` 주요 수정, `b36fabe` 행동 키에서 risk 제외·R4 이력·증거, 그리고 이 보고서와 문서를 담은 마지막 커밋 |
| 변경 규모(`900c71c..b36fabe`) | 35개 파일, +2920 / −65 (대부분 증거 파일) |
| 작업 트리 보존 | 결과·상태·원천 데이터·과거 연구 파일은 삭제하거나 덮어쓰지 않았다. 이전 보고서는 고치지 않고 맨 위에 안내 줄만 붙였다 |
| NEW2 읽기 전용 사본 | `8ec637153b4375143cbb9b34c00eb32053feca5e`, `COINTRADER_SRC`로만 사용 |

## 2. 검토한 파일과 기존 구현 상태

코드가 문서보다 우선한다. 아래는 코드를 직접 읽어 확인한 상태다.

| 영역 | 파일 | 기존 상태(감사 전) |
|---|---|---|
| 실행 진입점 | `run/discovery/runner.py` | legacy / evolve 두 모드. 경로·비용 프로필 검사 있음 |
| 진화 루프 | `autoloop.py`, `pipeline.py` | DEV → 사전등록 검증 배치(StepM) → 강건성 → 잠금 캠페인 → 승격. 동작함 |
| 전략 표현·변이 | `evolution.py`, `search.py` | 스펙 정규화, `strategy_id` 해시, 연산자 14종, 계보 기록 |
| 백테스트·통계 | `engine_lib.py`, `pipeline.py` | 비용·펀딩 반영, HAC t, StepM, PBO/DSR, 잠금 레지스트리 |
| 비용 연결 | `costs_bridge.py` + NEW2 `cointrader.backtest.costs.CandleCostModel` | 편도 비용 = 수수료 + 반스프레드 + 충격·√참여율. NEW2 코드를 읽어 이중 계산이 없음을 확인 |
| 공개 호가 추정 | `public_market_estimate/`, ADR 0011 | `ESTIMATED_FROM_PUBLIC_DATA` 기록 생성 |
| 비용 검증 | `cost_calibration.py`, `config/cost_verification_v1.json` | 보정/검증 분할, 상태 판정. 결함 다수(§3) |
| 운영 | `ops.py`, `lock` | health, 원장 무결성 검사 |
| 전진 페이퍼 | `paper_forward.py` | 공개 REST 피드 있음. 계획 등록 조건(VERIFIED 비용) 때문에 차단 |
| 인계 | `handoff.py` | 사람 검토용 문서만 만든다. 승격 생존자가 없어 도달하지 않음 |
| 테스트 | `run/discovery/tests/` | 기준 `900c71c`에서 407개 OK |
| 문서 | `README.md`, `STATISTICS_AND_STATES.md`, `docs/adr/0001~0012`, `run/audit/STATUS_CURRENT_20261017.md`, `results/measurement_queue_status.md`, `docs/worklog.md` | 2026-10-17 상태까지 반영 |

## 3. 발견한 문제와 심각도

수정 전 코드(`900c71c`)에서 재현했다(`repro_base.py`, `repro_base.txt`). 새 테스트 17개를 기준 코드에 적용하면 4개 실패, 13개 오류가 난다(`new_tests_on_base.txt`).

### 수정한 문제

| # | 문제 | 심각도 | 재현 결과(수정 전) | 수정 후 |
|---|---|---|---|---|
| 1 | 비용 검증이 합친 오차만 봐서 종목 하나의 과소추정을 숨김 | 높음 | 합친 평균 −0.999bp, p90 1.164bp로 `VERIFIED` | 종목별 한도도 적용, `NOT_VERIFIED` |
| 2 | 단위 오류 수용(초 단위 ts, bp 수수료, 100배 가격) | 높음 | 셋 다 유효 체결로 통과 | 셋 다 거부(격리 가능) |
| 3 | 검증 창 알파 소비가 상태 폴더마다 k=1부터 다시 시작 | 높음 | 같은 창을 6번 보면서 매번 첫 배치 알파 0.030 사용 | 창 이력 파일로 k를 이어 셈. R4는 k=7, 알파 0.00062 |
| 4 | 메모리 입력의 중복 체결을 걸러내지 못함 | 중간 | 반복 체결 5건으로 `VERIFIED` | 사유로 기록, `NOT_VERIFIED` |
| 5 | 표본 부족을 실패(`NOT_VERIFIED`)로 기록 | 중간 | 체결 100건에 `NOT_VERIFIED` | `INSUFFICIENT_DATA` |
| 6 | 강건성 이웃(검증 결과로 생긴 전략)이 다음 라운드 검색 피드백에 들어감 | 중간(잠재) | 이웃 3개가 피드백에 나타남 | 검색 풀은 등록된 시드·제안만 |
| 7 | 행동이 같은 전략(`진입[A]+필터[B]` = `진입[A,B]`)이 다른 후보로 등록 | 중간 | 두 id 모두 신규로 수락 | 행동 키로 거름. R4에서 6건 걸러짐 |
| 8 | 중단된 사이클이 이전 `CYCLE_OK`를 남겨 health가 OK | 중간 | 강제 종료 후 OK | `CYCLE_RUNNING` 기록, health는 `CYCLE_INTERRUPTED`(DEGRADED) |
| 9 | 비용 전후 비교와 탈락 원인이 기록에 없음 | 낮음 | 순수익 평균만 기록 | 총수익·비용·펀딩·순수익과 `failure_class` 기록 |
| 10 | risk 설정만 다른 쌍이 같은 배치에 들어감(평가는 risk를 쓰지 않음) | 낮음 | R4에 `S-0d7f17648693c1b9` / `S-202b2284faa1e9ac` 쌍 | 행동 키에서 risk 제외(`b36fabe`) |

### 수정하지 않고 기록한 문제

| 문제 | 심각도 | 이유 |
|---|---|---|
| 계정 체결 기록이 없어 비용 `VERIFIED` 불가 | 차단(외부 증거) | 사용자 계정 체결이 필요하다. 만들 수 없다 |
| 엔진이 비용 계산을 NEW2 코드에 의존 | 중간 | "NEW2와 연결하지 않음" 원칙과 긴장. 이번에는 읽기 전용 사용만 허용받았다. 비용 모델을 이 저장소로 옮길지는 사용자가 정할 일 |
| 종목별 비용 매개변수를 큰 값으로 합침 | 낮음 | 보수적 방향이라 낙관 편향은 없다 |
| legacy 엔진(779 후보)은 실데이터로 끝까지 돌린 적 없음 | 정보 | 세대 FDR에는 779개 전부가 필요하다. 이번 범위에서도 실행하지 않았다 |
| R1~R3 원 기록(상태 폴더)이 보존되지 않음 | 낮음 | 문서와 ADR의 수치만 남아 있다. 이번 R4는 원장 사본을 증거로 남겼다 |
| 포트폴리오 청산 수수료 미반영 | 낮음 | 단일 전략 판정에는 영향 없음 |
| `handoff.py`는 사람 검토 문서만 만든다 | 정보 | 승격 생존자가 없어 도달하지 않는다. NEW2로 넘기는 경로는 없다(의도대로) |

## 4. 수정한 파일

| 파일 | 변경 |
|---|---|
| `run/discovery/cost_calibration.py` | `cost-calib-v3`: 단위·범위 검사, `order_id` 중복 키, `load_observations_checked`와 `--quarantine`, 종목별 한도, 평균 오차 95% 구간, 수집 기간, 커버리지, 상태 4종, exit 4 |
| `run/discovery/pipeline.py` | `search_pool`, 피드백을 풀로 제한, `cost_attribution`, `validation_failure_class`, 창 이력 기반 배치 번호(`external_prior_batches`) |
| `run/discovery/evolution.py` | `behaviour_key`(all 논리에서 진입·필터를 합치고 risk는 뺌) |
| `run/discovery/search.py` | `known_keys`, `semantic_duplicates` 집계 |
| `run/discovery/autoloop.py` | `CYCLE_RUNNING` / `CYCLE_FAILED` 래퍼, 풀 기반 known id·key, 이력 전달 |
| `run/discovery/ops.py` | `CYCLE_INTERRUPTED` 판정 |
| `run/discovery/runner.py` | evolve 모드는 창 이력 파일이 없으면 거부(exit 1) |
| `run/discovery/evolution_report.py` (신규) | 원장에서 단계별 집계 재계산 |
| `run/discovery/validation_window_history.jsonl` (신규) | 실데이터 검증 배치 7건(A1, A2, D1, R1, R2, R3, R4) |
| `run/discovery/tests/test_audit_20261020.py` (신규) | 회귀 테스트 20개 |
| `run/discovery/tests/test_completion.py` | 표본 100건 사례의 기대값을 `INSUFFICIENT_DATA`로 |
| `run/discovery/README.md`, `STATISTICS_AND_STATES.md` | 새 상태·도구·규칙 |
| `docs/adr/0013-…md`, `docs/adr/README.md` | 결정 기록 |
| `run/audit/STATUS_CURRENT_20261020.md` (신규), `STATUS_CURRENT_20261017.md`·`results/measurement_queue_status.md`(안내 줄), `docs/worklog.md` | 현재 상태 |

## 5. 비용 모델과 검증 기준

- 모델: 편도 비용 = 수수료 + 반스프레드 + 충격계수·√참여율 (NEW2 `CandleCostModel`, `costs_bridge.CostContext`). 펀딩은 보유 구간에 따로 더한다.
- R4가 쓴 프로필: `public_estimate:data/public_market_estimate/estimate_v1.json`. half_spread 1.92e-4, impact 1.0013e-3(둘 다 ETH 값, 종목 간 큰 쪽), fee 5e-4(공개 수수료표 가정, 관측 아님), max_participation 0.05. 상태는 **`ESTIMATED_FROM_PUBLIC_DATA`**이며 체결 검증이 아니다.
- 검증 기준(`run/discovery/config/cost_verification_v1.json`, 2026-10-16 사전등록, 바꾸지 않음): 보정 비율 0.5, 종목당 검증 체결 200건 이상, 평균 과소추정 ≤ 0.5bp, p90 과소추정 ≤ 2.0bp, 거부율 ≤ 5%. 이번에 합친 오차와 종목별 오차 모두에 적용하도록 했다.
- 상태 판정 순서: 구성요소(계정 체결 등) 없음 → `BLOCKED_EXTERNAL_EVIDENCE`; 검사 실패 하나라도 → `NOT_VERIFIED`; 표본만 부족 → `INSUFFICIENT_DATA`; 모두 통과 → `VERIFIED`. 격리된 행이 있으면 `VERIFIED`가 될 수 없다.
- 현재 상태: **`BLOCKED_EXTERNAL_EVIDENCE`**. 계정 체결 기록이 없다. `data/fill_ledger.jsonl`은 가상 호가라 증거가 아니다.

## 6. 생성·진화 결과 (R4, 실데이터)

- 실행: 2026-10-20 06:34~07:10 UTC(2156초), exit 0, `CYCLE_OK`. 코드 `8a22e73`, 컨텍스트 `C2-4a5b6fb6c5a3202b6fc8`, dataset sha `5f0101f7…`(업로드 f6379f02). 명령은 `r4_command.txt`.
- 후보: 시드 705 + 진화 72 = 777. 검색 풀 777(강건성 이웃 0). 연산자: crossover 12, random_spec 18, swap_feature 8, tweak_param 5, tweak_threshold 5, change_horizon 5, split_symbol 4, add_filter 4, add_entry_condition 3, resample_param 3, flip_direction 2, change_exit 2, change_risk 1.
- 라운드 3회: 제안 24/24/24, 시도 37/35/24, 무효 11/7/0, 행동 중복 2/4/0, id 중복 0.
- DEV: 777건 모두 OK, 화면 통과 42.
  - 탈락 사유(중복 집계): t 미달 698, 평균 ≤ 0 595, 폴드 불안정 527, 검정력 부족 109, 비용 비중 과다 12.
  - 사건이 있는 765건: 총수익 > 0 413건, 순수익 > 0 182건, 총수익 > 0인데 순수익 ≤ 0 232건. 중앙값은 총수익 1.65bp, 비용 14.10bp, 펀딩 −0.97bp, 순수익 −12.56bp. **비용이 총수익 우위를 대부분 지운다.**
- 최종 상태: REJECTED 679, INCONCLUSIVE 76, RESEARCH_ONLY 22. **생존 0.**
- R4에 risk만 다른 쌍 1건이 있었다. `b36fabe`에서 행동 키를 고쳤다. 고친 뒤 다시 돌리지 않았다. 다시 돌리면 같은 검증 창을 8번째로 보게 되기 때문이다.

## 7. 백테스트 신뢰성 감사

| 항목 | 결과 |
|---|---|
| 미래 정보 | 인과성 테스트(기존 5개) 통과. 검증 결과가 검색으로 새는 경로 1개를 막았다(§3 #6) |
| 비용 이중 계산 | NEW2 `costs.py` 확인, 없음 |
| 중복 시험 | id 중복 0, 행동 중복 6건 차단. 행동 키는 all 논리의 진입·필터 합침과 risk만 다룬다. 임계값이 사실상 같은 경우는 잡지 않는다 |
| 다중검정 | 배치 안 StepM(Romano–Wolf), 배치 간 알파 α·6/π²/k². 이제 창 이력으로 폴더를 넘어 이어진다 |
| 재현성 | 컨텍스트 id가 코드·데이터·비용 해시를 묶는다. 코드가 바뀌면 이전 결과를 재사용하지 않는다 |
| 중단 처리 | 강제 종료 시 `CYCLE_INTERRUPTED`(증거 `killed_cycle_health.txt`) |
| 잠금 TEST | 사용 0, 노출 0 |
| 한계 | 과거 6건 이력은 종목을 알 수 없어 두 창 모두에 적었다(보수적). 공개 추정 비용은 미검증이다 |

## 8. 실행한 테스트와 실제 결과

| 검사 | 결과 | 증거 |
|---|---|---|
| 기준 `900c71c` 전체 테스트(깨끗한 작업 트리) | 407개 OK | `baseline_main_900c71c.txt` |
| 새 테스트 17개를 기준 코드에 적용 | 실패 4, 오류 13(결함 재현) | `new_tests_on_base.txt` |
| 재현 스크립트, 수정 후 | 결함 1~5 모두 해소 | `repro_after_fix.txt` |
| `8a22e73` 전체 테스트 2회 | 427개 OK, 2회 모두 | `full_suite_8a22e73_run1/2.txt` |
| `b36fabe` 코드 전체 테스트 2회(이후 커밋은 문서만) | 427개 OK, 2회 모두 | `full_suite_final_run1/2.txt` |
| 정적 검사: compileall, ruff E9/F63/F7/F82, 변경 파일 ruff F, 이력 JSONL 형식 | 모두 통과 | `static_checks_final.txt` |
| 강제 종료 health | `CYCLE_INTERRUPTED` | `killed_cycle_health.txt` |
| R4 후 health | OK | `r4_health.json` |

## 9. 최신 전략 평가와 독립 검증 통계

- 검증 배치: 사전등록 20명, BTC·ETH 두 창(`BTCUSDT:VAL:1695772800000`, `ETHUSDT:VAL:1761782400000`). 배치 번호 7(외부 6 + 폴더 0 + 1), 알파 0.00062.
- 결과: REJECTED 7, INCONCLUSIVE 13, **생존 0**.
- 탈락 원인: INSUFFICIENT_DATA 13, FAIL_COST 3, NO_EDGE_OUT_OF_SAMPLE 2, FAIL_STAT 2.
- SPA p값: BTC 0.036, ETH 0.645. Reality Check p값: BTC 0.144, ETH 0.794. BH 진단은 비어 있음.
- 알파가 작아진 것이 결과를 바꾸지는 않았다(추론). ETH SPA p가 0.645라 두 종목 모두에서 기각될 멤버가 없었다.
- 강건성·잠금 캠페인·승격: 진입 없음.
- 결론: **실데이터에서 독립 검증을 통과한 전략은 없다.** 기준은 완화하지 않았다. 이 결과는 공개 추정 비용 기준이며, 수익성 근거가 아니다.

## 10. 기능별 상태

| 기능 | 상태 | 근거 |
|---|---|---|
| 전략 생성(시드 번역 705) | 완료 | R4 실데이터 |
| 진화 검색(연산자 14종, 계보) | 완료 | R4 라운드 3회 |
| 행동 중복 제거 | 부분 구현 | all 논리와 risk만. 임계값 동치는 못 잡음 |
| DEV 백테스트(비용·펀딩·HAC) | 완료 | R4 777건 OK |
| 비용 전후 비교 | 완료 | DEV·검증 기록 |
| 검증 배치(StepM)와 창 단위 알파 | 완료 | R4 k=7. 이력은 수동 관리 파일 |
| 탈락 원인 분류 | 완료 | R4 20명 |
| 강건성 단계 | 검증 대기 | 합성 테스트만. StepM 통과자 없음 |
| 잠금 TEST 캠페인·승격 | 검증 대기 | 생존자 0 |
| 비용 보정·검증 도구 | 완료(코드) / 검증 대기(실증) | `BLOCKED_EXTERNAL_EVIDENCE` |
| 공개 호가 비용 추정 | 완료(추정) | `ESTIMATED_FROM_PUBLIC_DATA`. 장기 수집분 업로드 대기 |
| 비용 `VERIFIED` | 검증 대기 | 계정 체결 200건/종목 필요 |
| 전진 페이퍼 | 검증 대기 | `PAPER_TEST_READY` 전략과 VERIFIED 비용 필요 |
| legacy 엔진(779) 실데이터 | 검증 대기 | 실행한 적 없음 |
| 상태 기록·health | 완료 | 중단 감지 포함 |
| 원장 보고 도구 | 완료 | `evolution_report.py` |
| 인계 문서(`handoff.py`) | 부분 구현 | 사람 검토 문서만. 도달 안 함 |
| 포트폴리오 청산 수수료 | 미구현 | 낮음 |
| 수익 전략 발견 | 검증 실패 | 수동 32건 + 자동 R1~R4 모두 생존 0 |

## 11. 미완료 항목과 다음 우선순위

1. **계정 체결 기록**(외부 증거): 종목당 검증 체결 200건 이상이 있어야 `VERIFIED` 판정을 시도할 수 있다. `cost_calibration.py --quarantine`으로 바로 넣을 수 있다.
2. **장기 공개 호가 데이터**: Oracle VM 수집분 업로드 후 추정을 다시 계산한다. 결과는 여전히 `ESTIMATED_FROM_PUBLIC_DATA`다.
3. **새 검증 창**: BTC·ETH 검증 창은 이미 7번 봤고 다음 배치 알파는 더 작아진다. 같은 창을 더 보기보다 새 데이터 구간이 쌓인 뒤 새 창을 사전등록하는 편이 낫다.
4. **비용이 지배적인 문제**: DEV 중앙값 기준으로 비용 14.1bp가 총수익 1.65bp를 크게 넘는다. 보유 기간이 길거나 거래 빈도가 낮은 전략 계열이 다음 탐색 방향이다(제안이며 실행하지 않음).
5. **NEW2 비용 코드 의존**: 이 저장소로 비용 모델을 옮길지 사용자가 정한다.
6. legacy 엔진 전량 실행, 포트폴리오 청산 수수료, 행동 키 확장은 낮은 우선순위다.
