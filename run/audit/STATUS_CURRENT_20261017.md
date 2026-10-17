# 현재 상태 요약 — 2026-10-17 (PR #8 병합 이후)

이 문서는 연구 전체의 현재 상태를 한곳에 모은 요약이다. 이전 보고서는 고치지 않았다. 각 보고서는 작성 시점의 기록이다.

- 기준: `main` `a2f63c0`(PR #8 병합) + 이 브랜치의 ADR 0010 변경.
- 표기: **구현**(코드 있음), **합성 테스트**(합성 데이터로 확인), **실데이터 검증**(실제 시장 데이터로 실행).

## 1. 판정

| 구분 | 상태 |
|---|---|
| 연구 엔진 코드 | 확인한 결함은 수정했다(ADR 0001~0010). 테스트 결과는 §6 |
| 전략 수익성 | **승격 후보 0건.** 검증을 통과한 전략이 없다 |
| 비용 검증 | `BLOCKED_EXTERNAL_EVIDENCE`. 계정 체결 기록이 없다 |
| 전체 | **PARTIAL.** 코드 측면은 정리됐다. 비용·전진 관측·잠금 TEST는 외부 증거 없이는 끝나지 않는다 |

## 2. 전략 연구 현황

| 묶음 | 건수 | 결과 | 출처 |
|---|---|---|---|
| 수동 측정(사전등록 → 측정 → 엔진 교차검증) | 32건(방향 26, 실행계층 1, 조합 5) | 전부 FAIL 또는 폐기 | `docs/NEW2_전략연구_보고서_v29.md`(Task 39), `docs/NEW2_전략탐색_종합정리.md` |
| 자동 탐색, legacy 엔진(779 후보) | 2026-10-15 커밋 기록 | 생존 0 | `run/discovery/state/`, `results/cycle_summary.json`(당시 마지막 사이클) |
| 자동 탐색, evolution 엔진 실데이터(업로드 f6379f02, dataset sha `5f0101f7…`) | DEV 777, 검증 진출 6 | 검증 생존 0, 잠금 TEST 사용 0 | `run/audit/AUDIT_RESIDUAL_20261017.md` §7 |
| 데이터 부족으로 보류된 재검증 트리거 | F2/X6-R(2027-01), P3(2027-01-11), COMBO-1 데이터 축적 | 아직 관측 전 | v29 보고서 |

- 수익성 실패와 미판정은 구분된다. 예를 들어 COMBO-1은 "경제성 생존 + 통계 미달"이다. 표본이 부족한 경우는 `UNDETERMINED_POWER`이고, 데이터가 없는 경우는 `DATA_MISSING`/`BLOCKED_DATA`다.
- 실패 기록은 모두 연구 증거로 보존했다.

## 3. 외부 증거가 필요한 항목

| 항목 | 필요한 것 | 현재 |
|---|---|---|
| 비용 VERIFIED | 계정 체결 보고서(`exchange_fills`), 종목당 검증 체결 200건 이상(`config/cost_verification_v1.json`), 수수료 등급 | 계정 없음. `data/fill_ledger.jsonl`은 NEW2 가상 호가라 증거가 아니다 |
| 공개 호가 비용 추정 | 사용자 PC 수집 결과(원본 + 매니페스트) | 수집 중, 업로드 전. 추정이 나와도 `ESTIMATED_FROM_PUBLIC_DATA`이고 승격 불가 |
| 잠금 TEST | 검증 생존자, 그 용도에 대한 VERIFIED 비용, 독립적인 잠금 창 | 생존자 0, 비용 미검증. BTC+ETH 후보는 ADR 0010으로 차단 |
| 전진 페이퍼 | VERIFIED 비용(계획 등록 조건), 바이낸스 공개 피드 접속 | 둘 다 없음. `PAPER_PASSED`인 전략 없음 |

## 4. 두 엔진

- 기본 실행 모드는 **legacy**다. 최근 실데이터 실행은 **evolve**였다.
- 통계 절차가 다르므로 두 엔진의 결과를 섞어 비교하지 않는다. 비교표는 `run/discovery/STATISTICS_AND_STATES.md` §9에 있다.
- 상태 파일 중 무엇이 현재 기록이고 무엇이 역사 기록인지는 `run/discovery/README.md`의 "State files" 표에 정리했다.

## 5. 이전 보고서와 다른 점

| 문서 | 작성 시점의 내용 | 지금 |
|---|---|---|
| `run/audit/AUDIT_RESIDUAL_20261017.md` | PR #8 병합 전. ADR 0008·0009 Proposed, "ETH DEV·BTC 잠금 겹침은 단일 자산이라 영향 없음" | PR #8 병합됨, ADR 0008·0009 Accepted. 다종목 후보에는 영향이 있어 ADR 0010으로 차단 |
| `results/measurement_queue_status.md` | Task 21 시점(측정 18건) | 측정 32건(Task 39, v29 보고서) |
| `run/discovery/results/cycle_summary.json` | 2026-10-15 legacy 사이클 | 최신 연구 실행이 아니다(§2) |
| 루트 `run/discovery/state.json`, `discovery_ledger.jsonl`, `promotion_queue.md` | v1 엔진 | 현재 코드는 읽지 않는다. 증거로 보존 |

## 6. 이번 변경 검증 (ADR 0010)

| 검사 | 결과 |
|---|---|
| 기준 main 전체 테스트(`a2f63c0`) | 390개 OK |
| 새 회귀 테스트를 기준 main에 적용 | 17개 중 12개 실패(결함 재현). 인과성 테스트 5개는 기존 코드에서도 통과(회귀 방지용) |
| 변경 후 전체 테스트 | §6 표 아래 "실행 기록" 참조 |

실행 기록은 PR 설명과 `run/audit/evidence_reaudit_20261017/`에 있다.
