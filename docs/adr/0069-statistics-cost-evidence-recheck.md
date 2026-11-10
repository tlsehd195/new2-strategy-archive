# ADR 0069 — 통계 게이트·검정력·비용 증거 재검증(판정 기준 불변)

- 상태: Accepted (진단·안전장치 추가, 판정 기준·임계값 불변)
- 날짜: 2026-11-10
- 요청: 동동, 2026-11-10 "잔여 문제 전면 해결" 지시문 §3~§5 (P1)
- 관련: ADR 0063(비용 증거), ADR 0064(promo-v5, 검정력), ADR 0068(포트폴리오 계층)

## 다시 확인한 것(현재 코드 기준)
코드를 직접 읽고 테스트를 실행해 확인했다.

| 항목 | 확인 결과 | 근거 |
|---|---|---|
| promo-v5 기본값 | 맞음. `pipeline.DEFAULT_PROMOTION_CONFIG = promotion_v5.json` | `test_promo_v5_20261110` |
| DSR ≥ 0.95 게이트 | 맞음. 계산 불가는 BLOCKED_STATISTICS(INSUFFICIENT_DATA) | `robustness()`, 테스트 |
| 부트스트랩 CI 하한 > 0 게이트, B = 2000 | 맞음. 정상(stationary) 블록 부트스트랩, 평균 블록 길이 n^(1/3), 퍼센타일 CI. 독립 표본 재추출이 아님 | `robustness()`, `stats_mc.stationary_bootstrap_indices` |
| PBO·DSR 시행 집합 = 탐색 풀 | 맞음. 이웃 전략 제외, 실행 순서와 무관 | `_trial_series()` |
| 시계열 먼저, 평가 기록 나중 | 맞음 | `dev_evaluate()` |
| MISSING_DEV_SERIES | 맞음. `ledger_check`가 탐지 | `test_run_modes_20261110` |
| 검정력 분석 고정 시드·재현 | 맞음. 같은 시드면 같은 숫자 | `test_power_analysis_20261110` |
| 설정 해시 기록 | 맞음. 평가·배치·승격 기록에 `config_hash` | 코드 |
| DSR 공식 | Bailey·López de Prado 2014와 일치. 일 단위 SR, Pearson 첨도(정규 = 3), 시행 간 SR 분산 | `engine_lib.deflated_sharpe` |

## 발견과 조치
1. **DSR 시행 수 과소 집계(진단 추가, 게이트 불변).**
   - `_dsr`의 `trials`는 SR을 계산할 수 있는 전략(30일 이상, 분산 > 0)만 센다.
   - 그러나 탐색 풀에서 평가한 다른 전략도 모두 시행이다. 시행 수가 적으면 기대 최댓값 기준이 낮아져 DSR이 관대해진다.
   - 조치: `trials_searched`(탐색 풀의 DEV 시계열 전체)와 그 수로 계산한 `dsr_at_trials_searched`를 기록에 더한다. 판정에는 쓰지 않는다.
   - 판정에 쓰려면 새 promo 버전과 ADR이 필요하다. promo-v5는 바꾸지 않았다.
   - 테스트: 전체 수로 계산한 DSR이 기존 값보다 크지 않음을 확인한다.
2. **검정력 추정의 시뮬레이션 불확실성이 표시되지 않았다.**
   - `n_sims = 40`이면 비율 하나의 95% 범위가 ±0.15에 이른다.
   - 조치: 단계별 Wilson 95% 구간(`mc_ci95`)을 결과에 더한다. ALL은 구성 요소 경계의 곱으로 표시한다.
   - 시뮬레이션하지 않은 게이트(`gates_not_simulated`)를 결과에 적는다: 비용 스트레스, 장세, 낙폭, 쏠림, 이웃, walk-forward, PBO, 잠금, 포트폴리오. 따라서 보고된 검정력은 **상한**이다.
   - 실제 판정보다 적은 부트스트랩 횟수를 쓴 사실도 기록한다(`bootstrap_B_used`: StepM 200 대 1000, CI 500 대 2000).
   - 기존 숫자(ADR 0064)는 바뀌지 않는다. 같은 시드에서 같은 값이 나오고 필드만 더해진다.
3. **실계좌 체결 증거 안전장치.**
   - `account_kind`는 사용자 신고값일 뿐 증거가 아니다.
   - 조치: `real`로 신고했는데 원본에 모의·페이퍼·테스트넷 표시가 있으면 가져오기를 거부한다.
     - 표시어: demo trading, demo account, simulated trading, paper trading, testnet, mock trading, 모의투자, 모의 거래.
   - `real_fill_evidence_status()`: 실계좌 신고 체결이 0건이면 `NO_REAL_FILL_EVIDENCE`를 돌려준다.
   - 한계: 표시어가 없는 모의 기록은 코드로 구별할 수 없다. VERIFIED에는 계속 `cost_calibration`의 모든 조건이 필요하다(심볼당 200건 이상, 체결 단위, 수수료·펀딩 분리, 결정 시각 기준가).

## 남은 통계적 한계(코드로 해결 불가)
- 검정력: SR 4에서도 80%에 못 미친다(ADR 0064). BTC/ETH 창의 다음 배치(k = 8)는 SR 4에서 18%다. 새 독립 창이 필요하다.
- 다중검정 범위:
  - 확증 검정은 검증 창의 StepM 하나다. 창별 알파 소비 0.05/창 수 × 6/π²/k²를 쓴다.
  - DSR·PBO·CI·포트폴리오는 거부만 더한다. FWER를 늘리지 않지만 독립적인 확신을 더하지도 않는다. 같은 자료를 여러 방법으로 본 것이다.
  - 과거 v2 배치 7개 때문에 이력 전체 FWER 상한은 ≈ 0.096이다(ADR 0013). 새 창을 쓰는 것 말고는 낮출 수 없다.
  - Holm은 evolve 경로에서 쓰지 않는다. legacy 경로(`engine_lib` 후보 안 셀)에서만 쓰며, 평균 > 0을 함께 요구한다.
  - 폐기된 v1 스크립트의 Holm 중에는 양측 검정이라 유의하게 음수인 셀도 통과로 적은 것이 있다(ADR 0070 감사).
- 같은 시장 구간의 반복 선택: DEV 창은 탐색 신호일 뿐 검정이 아니다. 반복 선택 편향은 DSR(진단·게이트)과 PBO로만 다룬다.

## 비용 항목별 증거 등급(ADR 0063 표의 재분류)
| 등급 | 항목 | 출처·계산 |
|---|---|---|
| 1. 실측 검증(실계좌) | 없음 | 실계좌 체결 0건. OKX 기록은 모의(ADR 0058)라 제외 |
| 2. 외부 자료로 측정·보정 | 펀딩(공개 정산 이력, 보유 구간 (entry, exit] 합산), 테이커 수수료(공개 수수료표), 스프레드·충격(estimate_v1: 2026-10 공개 호가 80h + 알트 96h, ESTIMATED_FROM_PUBLIC_DATA) | `public_cost_estimate.py`, ADR 0011·0041 |
| 3. 모델 추정 | 충격의 √참여율 형태, 참여율 상한 거절, 손절 체결 = 봉 극값(`exit_fill_v1`), 저유동 회귀 외삽 | `cost_model.py`, `strategy_spec.stop_exit_rule` |
| 4. 모델링 안 함(NOT_MODELLED) | 부분 체결, 체결 지연, 변동성 의존 비용, 메이커 수수료, 펀딩 외 롱·숏 비대칭, 거래소·심볼별 차이(파라미터 1벌) | 결과의 `cost_components`에 표시, 0으로 채우지 않음 |

중복·누락 점검 결과:
- 진입·청산 비용은 각각 한 번씩 매기고, 펀딩은 별도로 한 번 매긴다(evaluator·portfolio_multi 모두).
- 손절은 봉 극값 체결에 half_spread까지 더해진다. 겹치는 부분이 있어 보수 쪽으로 과대 계산된다.
- 비용 자료가 없으면 거래를 거절하거나(DATA_MISSING) 결과를 UNDETERMINED로 둔다. 0으로 넣지 않는다.

## 결과
- 새 테스트 `tests/test_statistics_evidence_20261110.py`.
- 기존 판정, 기준 파일, 해시는 바뀌지 않는다. 진단 필드가 늘어서 새 기록의 record_id는 달라진다.
