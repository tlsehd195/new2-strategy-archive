# ADR 0012 — 공개 호가 추정 비용(estimate_v1)으로 실데이터 evolve 사이클 R3

- 상태: Accepted
- 날짜: 2026-10-20
- 범위: 기록만(코드·기준 변경 없음)
- 선행: [ADR 0011](0011-public-market-estimate-first-run.md), [ADR 0009](0009-locked-test-requires-verified-cost.md), [ADR 0010](0010-legacy-lock-gates-cross-symbol-independence-mode.md)

## 맥락
PR #10 병합 후, `data/public_market_estimate/estimate_v1.json`(`PE-371c80b76eeba61b6dd27843`)을 비용 모델로 써서 실데이터 evolve를 한 번 돌렸다. 기준은 완화하지 않았고 잠금 TEST는 열지 않았다.

## 실행
- 데이터: 업로드 f6379f02(dataset sha `5f0101f7…`), 빈 상태 폴더.
- 명령: `NEW2_DISC_MODE=evolve NEW2_COST_PROFILE=public_estimate:data/public_market_estimate/estimate_v1.json python3 run/discovery/runner.py --mode evolve --rounds 3 --evals 2000`
- 컨텍스트 `C2-8ff5fb6b72ec804c0e1b`, 비용 상태 `ESTIMATED_FROM_PUBLIC_DATA`, 단일 실행 exit 0 `CYCLE_OK`(2,154초, 중단 없음). `ops.py health` 정상.

## 결과
| 항목 | R1(PR #8 코드, 기본 비용) | R2(PR #9 코드, 기본 비용) | R3(이번, 공개 호가 추정 비용) |
|---|---|---|---|
| DEV 평가 | 777 | 849(5라운드) | 777(3라운드) |
| 검증 배치 진입 | 6 | 7 | 20 |
| 검증 생존 | 0 | 0 | 0 |
| 잠금 TEST 사용 | 0 | 0 | 0 |

- 상태 분포: REJECTED 678, INCONCLUSIVE 76, RESEARCH_ONLY 23.
- 검증 배치(StepM, 사전등록 20명): REJECTED 7, INCONCLUSIVE 13, 생존 0.
- 검증 진입이 7에서 20으로 늘었다. 이번 비용 모델이 기본값과 달라 DEV 단계 통과가 늘어난 것으로 보이며, 전략이 좋아졌다는 증거가 아니다. 통과 수는 통계 검증에서 모두 걸러졌다.
- R1과 DEV 건수는 같지만 비용 모델과 코드가 다르므로 개별 결과를 이어 쓰지 않았다.

## 한계
공개 호가 추정은 체결이 아니며 약 3.3일 시장 상황만 반영한다. 수수료는 가정값이다. 승격과 잠금 TEST에는 VERIFIED 비용이 필요하다(ADR 0009). 승격 후보는 여전히 0건이고 전진 페이퍼 대상 전략도 없다.
