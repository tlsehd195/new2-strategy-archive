# ADR 0081 — DSR은 관측된 DEV 일만으로 계산한다 (심볼 간 달력 공백의 KeyError 수정)

- 상태: Accepted
- 날짜: 2026-11-16
- 요청: 동동, 2026-11-16 "처리해" (ADR 0080 보고서 §6.1의 범위 밖 발견)
- 관련: ADR 0006(DM 정렬: 결측일을 0으로 채우지 않음), ADR 0064(promo-v5 DSR 게이트), ADR 0069(DSR 시행 수 진단), ADR 0080(발견)
- 시작 커밋: `2d6616a` (main, PR #78 병합). 열린 PR #79(ADR 0078/0079), #80(ADR 0080)이 번호를 쓰고 있어 0081을 쓴다.

## 1. 문제

`EvolutionPipeline._dsr()`는 전략의 심볼별 DEV 일별 시계열(`{symbol: [d0, values]}`)을 날짜별로 더한 뒤 **첫 날부터 마지막 날까지 모든 달력일**을 `acc[d]`로 읽었다.

```python
days = sorted(acc)
return [acc[d] for d in range(days[0], days[-1] + 1)]
```

심볼마다 DEV 창은 하루도 빠짐없이 이어지지만(`stats_mc.daily_series`: 창 안에서 청산이 없는 날은 관측된 0), 심볼끼리는 창이 겹치지 않을 수 있다. 실제 잠금 구간은 BTCUSDT 2024-03-25~06-18, ETHUSDT 2026-04-28~08-31이다. BTC+ETH 전략에서 ETH 선택 데이터가 BTC 잠금 구간을 덮지 않으려면 ETH를 2024-06-19 이후에 시작해야 하고, 그러면 BTC DEV 끝과 ETH DEV 시작 사이에 어느 심볼도 관측하지 않은 날들이 생긴다. 그 날의 `acc[d]`가 없어 `KeyError`가 나고 `run_validation()` → `robustness()` → `_dsr()`에서 검증 배치 전체가 멈췄다. promo-v5에서는 DSR이 게이트(`dsr_gating`)라 이 경로는 판정에 직접 쓰인다.

멈추는 방향이므로 잘못된 통과는 아니었다. 실제 데이터(모든 심볼 2021년 시작)에서는 ETH 선택 데이터가 BTC 잠금 구간을 덮어 `LOCKED_WINDOW_NOT_INDEPENDENT`로 먼저 막히므로 지금까지 실행에서는 드러나지 않았다.

## 2. 재현 (수정 전 `2d6616a`)

`run/discovery/tests/test_dsr_calendar_gap_20261116.py`를 먼저 작성해 수정 전 코드로 실행했다. 잠금 구간은 **실제 값 그대로**(패치 없음), 합성 데이터의 DEV 시작일만 BTC 2023-07-01, ETH 2024-06-19로 다르게 했다(`SyntheticProvider(dev_start={...})`, 시험 도구에 심볼별 시작일 지정만 추가).

| 테스트 | 수정 전 |
|---|---|
| `test_fixture_has_a_calendar_gap_between_the_symbols` | 통과(픽스처에 공백이 있음을 확인) |
| `test_dsr_is_computed_on_the_observed_days_only` | `KeyError: 19627` |
| `test_validation_batch_runs_through_robustness` | `KeyError: 19627` (`run_validation` → `robustness` → `_dsr`) |
| `TestNotComputableReasons.test_too_few_observed_days_across_a_gap` | `KeyError: 10` |
| 나머지(사유 코드, `pooled_dev_days`) | 사유 필드·함수 없음 |

12개 중 11개 실패.

## 3. 판단: 계산 불가가 아니라, 관측일만으로 계산해야 한다

- **DSR의 정의.** Bailey & López de Prado(2014)의 DSR은 수익률 표본 T개의 Sharpe 추정치, T, 왜도, 첨도와 시행 집합의 SR 분산으로 정해진다. 식(`engine_lib.deflated_sharpe`)에 달력 연속성은 들어가지 않는다. 표본이 날짜 순으로 이어질 필요가 없고, 관측된 날만 있으면 T·평균·표준편차·고차 적률이 모두 정의된다. 그러므로 공백이 있다는 이유만으로 "계산 불가"로 거부하는 것은 정의에 맞지 않는다.
- **0으로 채우면 틀린다.** 공백 일은 어느 심볼도 DEV로 보지 않은 날이다. 0으로 채우면 T가 늘고(예: 재현 픽스처 586 관측일 → 852일) 표준편차와 평균이 함께 줄어 SR·DSR이 바뀐다. 관측하지 않은 날은 0 수익이 아니라는 원칙은 ADR 0006(DM 정렬)과 `variation_report`에 이미 있다.
- **지금의 계산과의 관계.** 심볼별 창이 겹치거나 맞닿는 경우(지금까지 계산된 모든 경우)는 관측일 = 달력 구간이므로 결과가 **완전히 같다**. 바뀌는 것은 지금까지 KeyError로 멈추던 경우뿐이다. 그래서 문맥 ID나 통계 기준 버전은 바꾸지 않는다.
- **정말 계산할 수 없는 경우는 거부한다(fail-closed).** 관측일이 `dsr_min_days`(30)보다 적음, 변동 없음, 비유한 값, DEV 시계열 없음, 계산 가능한 시행이 2개 미만. 이때 `computable: false`와 명시적 `reason`을 기록하며, promo-v5에서 `INSUFFICIENT_DATA`(BLOCKED_STATISTICS)로 처리되고 통과하지 않는다. 비유한 값은 이전에는 표준편차가 NaN이 되어 사유 없이 계산 가능 집합에서 빠졌는데, 이제 `DEV_SERIES_INVALID` 사유로 기록한다.

## 4. 결정

1. `pipeline.pooled_dev_days(series)`(신규, 모듈 함수): 심볼별 DEV 일 시계열을 **적어도 한 심볼이 관측한 날**에만 합산해 날짜 순 목록으로 돌려준다. 공백 일은 넣지 않는다. 달력 기록 `dev_calendar = {observed_days, span_days, unobserved_days, segments, first_day, last_day}`를 함께 돌려준다. 비유한 값이면 `DevSeriesError`.
2. `_dsr()`는 이 함수로 모든 시행의 시계열을 만든다. `n_days` = 관측일 수. 결과에 `dev_calendar`를 기록한다.
3. 계산 불가일 때 결과에 `reason`(`DEV_SERIES_TOO_SHORT`, `DEV_SERIES_NO_VARIATION`, `DEV_SERIES_INVALID`, `NO_DEV_SERIES`, `TOO_FEW_COMPUTABLE_TRIALS`), `reason_detail`, `n_days`, `dev_calendar`를 기록한다. 다른 시행의 계산 불가 시계열은 이전처럼 계산 가능 집합에서 빠지고 `trials_searched`(ADR 0069)에는 남는다.
4. 임계값(`min_dsr` 0.95, `dsr_min_days` 30), 시행 집합(`search_pool`), 게이트 여부, MinBTL 계산은 바꾸지 않았다. MinBTL의 연 환산은 관측일 수 `n`을 쓴다(이전과 같은 식).

## 5. 바꾸지 않은 것과 남은 발견

- PR #80의 다중 심볼 테스트가 넣은 `LOCKED_TEST_PERIODS` 패치는 PR #80에 있으므로 여기서 건드리지 않았다. 이 수정 뒤에는 그 패치 없이도 `_dsr`이 멈추지 않는다.
- **범위 밖 발견(수정 안 함): `_pbo()`의 0 채움.** `_pbo()`는 시행들의 날짜 합집합 위에서 `pooled[s].get(d, 0.0)`으로 CSCV 행렬을 만든다. 같은 문맥에 심볼 집합이 다른 전략(예: BTC 단독과 BTC+ETH)이 섞이면, 한 전략이 관측하지 않은 날이 0 수익으로 들어간다. ADR 0006 원칙과 어긋나며 PBO 게이트(`max_pbo`) 값에 영향을 줄 수 있다. 코드 읽기로 확인했고 재현 테스트는 만들지 않았다. CSCV는 공통 T×N 행렬이 필요하므로 고치는 방법(공통 관측일로 제한 / 계산 불가로 거부)은 별도 결정이 필요하다.
- 통계 기준·임계값, prereg 활성화, 검증/잠금/IW1 구간 개방, 실거래, 실시장 데이터 다운로드는 하지 않았다.

## 6. 검증

- 신규 `test_dsr_calendar_gap_20261116.py` 12개 통과(전체 경로: 실제 잠금 구간, BTC+ETH, `run_validation`에서 심어 둔 우위 전략이 robustness까지 가서 DSR 계산됨: 관측 586일, 공백 266일, 2구간).
- 기존 스위트 단언 완화 없이 전부 통과(3.13·3.11), ruff F 및 E9/F63/F7/F82, source audit, provenance, research inventory 통과. 수치는 최종 보고서 `run/audit/FINAL_REPORT_20261116_ADR0081.md`.
