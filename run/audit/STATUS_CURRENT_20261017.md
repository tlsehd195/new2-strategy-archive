> **2026-10-20 안내:** 이 문서는 2026-10-17 시점의 기록이다. 현재 상태는 `run/audit/STATUS_CURRENT_20261020.md`를 본다.

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

증거 폴더: `run/audit/evidence_reaudit_20261017/`

| 검사 | 결과 |
|---|---|
| 기준 main 전체 테스트(`a2f63c0`) | 390개 OK |
| 새 회귀 테스트(17개)를 기준 main에 적용 | 12개 실패(결함 재현). 인과성 테스트 5개는 기존 코드에서도 통과(회귀 방지용) |
| 변경 후 전체 테스트 2회 | 407개 OK, 2회 모두(`full_suite_run1.txt`, `full_suite_run2.txt`) |
| 정적 분석 | compileall, ruff E9/F63/F7/F82, ruff F 전체(v1 진단 2개 파일 제외), bash -n, ShellCheck 0.11.0, 설정 스키마: 모두 통과 |
| CI(research-ci, Python 3.11·3.13) | 4개 작업 모두 success |

### 실데이터 사이클 R2 (실데이터 검증, 새 코드)
- 데이터: 업로드 f6379f02를 새 폴더에 풀었다. dataset sha `5f0101f7c707…`이고, 이전 R1과 같다.
- 실행 조건: 빈 상태 폴더, 기본 비용(`UNVERIFIED_COST_MODEL`), `runner.py --mode evolve --rounds 3 --evals 2000`.
- 실행 경과:
  - 1차 실행은 시드 705개와 라운드 1·2까지 마친 뒤, 세션의 백그라운드 시간 제한(30분)으로 중단됐다. 요약 파일은 남지 않았다.
  - 같은 명령을 같은 상태 폴더에서 다시 실행했더니 체크포인트에서 이어서 라운드 3~5와 검증 배치를 돌았다. exit 0, `CYCLE_OK`.
  - 이 중단과 재개는 실데이터 재개 검증이 됐다. DEV 평가 849건 중 중복 평가는 0건이다.
- 컨텍스트: `C2-d02d2b0b6b60075dd192`. R1의 `C2-eb8b580f8c24306b61e0`과 다르다(코드 해시가 바뀜). 이전 결과는 재사용하지 않았다.
- 결과:
  - DEV 849건: REJECTED 747, INCONCLUSIVE 102.
  - 검증 배치(StepM, 사전등록 7명): REJECTED 2, INCONCLUSIVE 5, **생존 0**.
  - 잠금 캠페인 없음, 잠금 TEST 사용 기록 없음.
  - `ops.py health` OK(원장 8개 정상, 증거 변조 0).
- R1과의 비교: R1은 3라운드에 DEV 777건이었고, R2는 중단 때문에 총 5라운드에 DEV 849건이다. 예산이 같지 않아 건수를 직접 비교할 수 없다. 결론(생존 0)은 같다.
- 이 결과는 비용 미검증 상태의 연구 결과이며, 수익성 근거가 아니다.
- legacy 엔진은 실데이터로 돌리지 않았다. 세대 FDR 결정에는 779개 전부를 평가해야 하는데, 이번 작업 범위에서는 실행하지 않았다. legacy 게이트는 합성 테스트로만 확인했다.
