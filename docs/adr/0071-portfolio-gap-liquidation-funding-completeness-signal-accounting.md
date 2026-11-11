# ADR 0071 — 포트폴리오 시가 갭 청산·펀딩 정산 완전성·신호 회계·봉 내부 낙폭 (portfolio-multi-v2, portfolio-validation-v2)

- 상태: Accepted
- 날짜: 2026-11-11
- 요청: 동동, 2026-11-11 "NEW2 후속 정밀 감사 및 핵심 결함 수정 지시문 — ADR 0071 이후"
- 관련: ADR 0068(포트폴리오 계층 v1), ADR 0069(통계·비용, 기준 불변), ADR 0070(소스 감사)
- 시작 커밋: `82e93f9` (main, PR #71 병합). 그 뒤 변경 없음.

## 배경: 재현된 결함
| # | 결함 | 재현 테스트 |
|---|---|---|
| 1 | 시가가 청산가를 넘어 시작한 봉에서, 시간 종료(또는 시가로 바뀐 갭 손절)가 청산 검사보다 먼저 처리돼 정상 종료로 기록됨. 5배 롱 100→시가 60: v1은 청산 0건, 손실 10000 기록 | `TestIsolatedOpenGap.test_reproduction_long_gap_below_liq_with_time_exit_at_that_open` |
| 2 | 펀딩 누락 검사가 "첫 기록 ≤ 진입, 마지막 기록 ≥ 종료"만 확인(portfolio_multi). evaluator의 간격 검사도 공칭 8시간 고정이라 4h(HYPEUSDT)·2/4/8h(SOLUSDT) 시리즈에서 정산 1건 누락(8시간 간격)을 못 봄. 파이프라인은 CSV의 `funding_interval_hours`를 읽고도 evaluator에 넘기지 않았음 | `TestFundingSchedule.test_reproduction_one_middle_settlement_missing`, `test_four_hour_series_gap_missed_by_the_legacy_8h_rule` |
| 3 | `pipeline._study()`가 이벤트 목록만 돌려줘 `EventStudyResult`의 신호·거절·누락 수가 버려짐. 창 끝에서 끊긴 신호는 `signals_total`에만 들어가고 어느 칸에도 없었음 | `TestEvaluatorDiagnostics`, `TestPipelineWiring` |
| 4 | 포트폴리오 거절 사유가 판정에 들어가지 않음(`max_margin_skip_share`는 v1 설정에서 쓰지 않음). 비용 계산 오류(CostError) 거절은 전략별 `skipped`에만 세고 사유표에 없었음 | `TestSignalAccounting` |
| 5 | 최대 낙폭이 종가 기준뿐. 시가 100·저가 70·종가 99면 1%로 보임 | `TestDrawdowns.test_intrabar_dip_recovered_by_the_close` |
| 6 | Cross 3x 스트레스에 낙폭 한도가 없어 봉 내부 54% 손실도 통과 | `TestCross3xAndConfig.test_cross_3x_deep_intrabar_drawdown_fails_without_liquidation` |

실제 시장 데이터로는 아무것도 다시 돌리지 않았다. 모든 재현은 합성 데이터다. 실데이터에서 확인한 것은 펀딩 CSV의 열 이름과 `funding_interval_hours` 값뿐이다(아래 3절).

## 결정

### 1. 시가 갭 청산 (portfolio-multi-v2)
봉 처리 순서: ① 그 시가까지의 펀딩 정산 → ② **시가 갭 청산 검사** → ③ 시가 종료 → ④ 신규 진입 → ⑤ 봉 내부 최악·최선 평가 → ⑥ 봉 내부 청산·손절·익절 → ⑦ 종가 평가.
- 격리: 롱은 시가 ≤ 청산가, 숏은 시가 ≥ 청산가이면 청산(`liquidation_open_gap`). 청산가와 정확히 같으면 청산한다(봉 내부 규칙 `low <= liq`과 같음).
- 손실은 ADR 0068 모델 가정대로 게시 증거금이다. **거래소 규칙이라는 뜻이 아니다.** 갭으로 가격 손실이 증거금보다 크면 초과분을 `uncapped_excess_loss`로 기록만 하고 장부에는 넣지 않는다. 청산 수수료·보험기금은 `NOT_MODELLED`(`cost_components.liquidation_fee`).
- 교차: 시가 기준 계좌 자본(현금 + 모든 포지션의 시가 평가)이 유지증거금 합 이하이면 계좌 전체 청산(`account_liquidation_open_gap`). 손실 = 시가까지의 가격 손실 + mmr×명목가(v1의 봉 내부 규칙과 같은 가정).
- 같은 시가에 정산된 펀딩은 ①에서 한 번만 낸다. 청산 손실과 겹치지 않는다.
- 포지션은 한 번만 닫힌다. 두 번째 `_book`은 내부 오류로 시뮬레이션 오류가 된다.
- 청산 기록 `liquidations[]`: 전략, 심볼, 방향, 시각, 조건, 기준 가격, 청산가, 장부 손실, 가격 손실, 초과 손실, 청산 뒤 현금, 증거금 처리, 수수료 처리, 그 전에 낸 펀딩.

### 2. 펀딩 정산 완전성 (`funding_schedule.py`, funding-schedule-v1)
- 출처: data.binance.vision USD-M `fundingRate` 월별 CSV. 열은 `calc_time, funding_interval_hours, last_funding_rate`. `calc_time`은 정산 시각(ms, UTC, 수 ms 지터), `funding_interval_hours`는 그 정산의 간격, 비율은 정산 1회분(연율 아님).
- 확인한 실제 간격(프로젝트 자료 zip의 열 값만 읽음): 대부분 8h, HYPEUSDT 4h, SOLUSDT 2/4/8h 혼재(2021~2026 묶음). 2020 묶음(IW1)은 모두 8h. IW1 자료는 이 열을 확인하려고 파일을 열었을 뿐이고, 가격·비율·결과는 보지 않았다.
- 규칙: 보유 구간 `(진입, 종료]`(evaluator와 같음). 진입 이전 마지막 정산부터 종료 이후 첫 정산까지 이어지는 각 간격이 **그 정산의 선언된 간격 + 60초** 이하여야 한다. 간격은 하드코딩하지 않는다.
- 상태: `FUNDING_DATA_INVALID`(중복·역순·비정수 시각·비유한 비율·심볼/거래소 불일치) > `FUNDING_DATA_MISSING`(시리즈 없음·시작/끝 범위 밖) > `FUNDING_SCHEDULE_UNKNOWN`(경로의 정산에 선언 간격이 없음) > `FUNDING_SETTLEMENT_GAP`(중간 누락) > `OK`. 비율이 0인 정산은 정상 관측값이다. 누락은 0으로 채우지 않는다.
- evaluator: `FundingIndex(intervals=...)`가 주어지면 이 규칙을 쓰고, 이벤트를 버린 이유를 `funding_drop_reasons`에 남긴다. 주어지지 않으면 예전 공칭 8h 규칙이며 `funding_schedule = "NOMINAL_8H_LEGACY"`로 기록된다.
- 파이프라인: `RunnerDataProvider`가 `funding_intervals`·`funding_meta`를 함께 넘기고, `_study`가 자기 데이터 공급자의 펀딩일 때 간격을 evaluator에 전달한다.
- 포트폴리오: 닫힌 거래마다 `funding_status`를 기록한다. 판정: 누락·간격 누락·무효 → `INSUFFICIENT_PORTFOLIO_DATA`, 주기 불명 → `UNDETERMINED_PORTFOLIO`.

### 3. 신호 진단 보존
- `EventStudyResult.diagnostics()`: `signals_total = accepted + skipped_overlapped + truncated_end_of_window + participation_refusals + cost_data_missing + dropped_missing_funding`을 `accounting_ok`로 검사. `rejection_rate`의 분모는 `execution_candidates = signals_total − skipped_overlapped − truncated_end_of_window`(전략 자체의 겹침 규칙과 창 끝은 거절이 아니다).
- `pipeline._study()`는 그대로(이벤트 목록) 두고 `_study_result()`와 `_validation_study()`를 추가했다. 기존 호출부(behaviour_overlap, bundle_stable, short_top_dev, cost_sensitivity, 잠금 캠페인)는 바뀌지 않는다.
- DEV 기록 `per_symbol[*].signal_diagnostics`, 검증 기록 `per_symbol[*].signal_diagnostics`에 저장. 보고용이며 DEV 선별·StepM·robustness 판정 입력은 바뀌지 않았다. promo-v5는 바뀌지 않았다.

### 4. 포트폴리오 신호 회계와 판정
- 사유 분류: strategy_rule(겹침·중복 신호·비활성), window_end(창 끝·지연 체결이 자료 뒤), cost_refused(참여율 상한: evaluator의 연구 주문 크기 + 계좌의 실제 명목가), data_missing(거래대금 없음·비용 계산 오류·펀딩 누락/간격/주기 불명), capital(증거금·총 익스포저·동시 포지션·동일 심볼·배분 0), halted(파산 뒤).
- `signal_accounting`: 사유별 수, 비율(분모 위와 같음), 부분 체결 수·비율, 잔량 취소 수·비율, 미체결 명목가(진입한 거래의 취소 잔량만), 사유별 거절 명목가, 항등식 검사(`identity_ok`). 맞지 않으면 `ACCOUNTING_MISMATCH`.
- portfolio-validation-v2 판정 순서: 자료 문제(펀딩·거래 수·기간·data_missing 신호 > 0) → INSUFFICIENT; 주기 불명·회계 없음/불일치·비용 미산정·낙폭 상태 비정상 → UNDETERMINED; 수치 기준 → FAIL; 한도가 `null`인 거절 종류가 0보다 많으면 → UNDETERMINED; 그 밖 → PASS.
- 거절률 한도는 새로 만들지 않았다. promo-v1~v5, portfolio-validation-v1, ADR 어디에도 사전 등록된 거절률 기준이 없다. 그래서 `max_cost_refusal_share`, `max_capital_refusal_share`를 `null`(미정)으로 두고, 미정이면 거절이 하나라도 있을 때 통과시키지 않는다. 숫자 한도는 근거와 함께 새 버전·ADR로, 결과를 보기 전에만 정할 수 있다.
- `max_data_missing_signals = 0`: 가격이나 펀딩을 확인할 수 없는 신호의 결과는 알 수 없다. 0보다 큰 허용치는 정당화할 근거가 없다.
- 비용 데이터 부족(INSUFFICIENT, data_missing)과 참여율 초과(cost_refused)는 서로 다른 칸이다.

### 5. 낙폭 세 가지
- `max_drawdown_close`: 종가 자본(v1 `max_drawdown`, 키도 그대로 둠).
- `max_drawdown_intrabar_worst_case`: 고점은 종가, 저점은 봉마다 "현금 + 그 봉에 존재하는 포지션을 불리한 극값(롱 저가, 숏 고가)으로 평가". 격리 포지션은 −증거금 아래로 내려가지 않는다(모델 가정). 여러 심볼의 극값을 같은 순간으로 본다. **실제로 동시에 일어났다는 뜻이 아니라 보수적 스트레스 가정이다.**
- `max_drawdown_equity_peak_to_trough`: 위에 더해 봉마다 유리한 극값을 고점으로 쓴다(같은 봉의 최선이 최악보다 먼저라고 가정, 가장 보수적).
- 상태: OK / INSUFFICIENT_DATA(봉 2개 미만) / NOT_COMPUTABLE(비유한) / UNDETERMINED(열린 포지션의 심볼에 그 시각 봉이 없어 마지막 종가로 평가한 경우, `stale_marks`).
- 진단: `min_equity_to_maintenance_ratio`(봉 내부 최악 자본 ÷ 유지증거금 합의 최솟값, 판정 미사용).

### 6. Cross 3x 위험 기준 (portfolio-validation-v2)
- base와 cross 3x 모두 `max_drawdown_close_lt 0.5`, `max_drawdown_intrabar_lt 0.5`.
- 근거: 저장소에 사전 등록된 계좌 손실 한도는 `portfolio.PORTFOLIO_PASS_CRITERIA["max_drawdown_lt"] = 0.5`(자본 기준) 하나뿐이다. 이것은 **계좌 자본의 손실 한도**라서 레버리지와 무관하다. 그래서 3배 스트레스도 같은 계좌 한도로 본다. 레버리지에 맞춰 조정한 숫자나 더 엄격한 cross 전용 숫자는 근거가 저장소에 없어서 만들지 않았다.
- 봉 내부 기준은 종가 기준보다 항상 엄격하다(최악 ≤ 종가). 완화가 아니다.

### 7. 설정·버전
- `config/portfolio_validation_v2.json` 신설, 기본값(`DEFAULT_PORTFOLIO_CONFIG`)으로 지정. v1 파일은 바이트 단위로 그대로이고 계속 불러올 수 있다(v1 판정 키는 v1 의미 그대로).
- 시뮬레이터 `portfolio-multi-v2`. `funding_schedule.py`, v2 설정이 `EVO_CODE_FILES`에 들어가 실험 컨텍스트가 바뀐다. 예전 기록과 섞이지 않는다. 포트폴리오 기록에는 `model_version`, `portfolio_config_version`, `portfolio_config_hash`가 남는다.
- promo-v5, DSR 0.95, 부트스트랩 CI 하한 > 0, PBO, StepM, FWER/알파 지출, 검정력 분석, 시행 수 기록은 바뀌지 않았다.

### 8. 감사 기록 출처표
- `run/audit/audit_provenance_20261111.py`, `AUDIT_PROVENANCE_20261111.{jsonl,md}`: 검토 에이전트의 "전체 읽음" 주장(124행)과 작성자 직접 확인(2026-11-10 SPOT 5행, 2026-11-11 FULL 5·PARTIAL 3)을 분리한다. CI가 표의 최신 여부와 "검토자 행이 작성자 재검토를 주장하지 않는다"를 검사한다.
- 2026-11-10 감사 표에는 핵심 엔진 모듈(pipeline, portfolio_multi, evaluator, costs_bridge, data_quality)이 없었다. 이번에 작성자가 직접 읽은 범위를 처음 기록했다. pipeline.py와 data_quality.py는 일부만 다시 읽었고 그 범위를 줄 번호로 적었다.
- 폐기 스크립트 76개(`LEGACY_SCRIPTS.md` 76 = 분류표 legacy 76, 디스크에 모두 있음). 엔진(run/discovery)이나 다른 run/*.py가 폐기 스크립트를 import하지 않음을 AST로 확인했다. 이전의 "84개"는 근거를 찾지 못했다.

## 결과에 미치는 영향
- 현재 PASS_INDIVIDUAL(VALIDATION_SURVIVOR)인 전략은 0개라 포트폴리오 판정이 바뀐 실제 전략은 없다.
- 새 컨텍스트에서는 DEV·검증도 다시 계산된다(코드 해시 변경, 기존 설계). 공칭 8h가 아닌 심볼(HYPE, SOL 일부 기간)에서 누락 정산이 걸친 이벤트는 이제 버려지고 이유가 기록된다. BTC/ETH는 모두 8h라 같은 결과일 것으로 예상하지만 실데이터로 다시 돌려 확인하지는 않았다.

## 남은 위험
- 파이프라인 밖 호출자(`independent_window.py`, `runner.py`의 v1 경로, `paper_forward.py`, `cost_sensitivity.py`, run/*.py 연구 스크립트)는 여전히 공칭 8h 규칙이다(`NOMINAL_8H_LEGACY`로 기록). IW1 코드는 동결 상태라 건드리지 않았다. IW1 2020 자료는 모두 8h라 영향이 없다고 보지만 코드로 강제하지는 않았다.
- 갭 청산 손실 상한(증거금), 교차 청산 손실식, 청산 수수료 미반영은 모델 가정이다. 실제 거래소 규칙(단계별 유지증거금, 보험기금, ADL)은 모델링하지 않았다.
- evaluator의 참여율 거절은 연구 주문 크기(`order_value`) 기준이다. 포트폴리오 명목가 기준 거절과 다를 수 있으며 둘 다 cost_refused로 센다.
- 거절률 숫자 한도가 없으므로 거절이 하나라도 있는 결합 장부는 UNDETERMINED가 된다. 기준을 정하려면 실제 체결 자료 등 근거가 필요하다.
- 실계좌 체결 0건, 비용 모델 미검증, 새 독립 구간 없음은 그대로다.

## 테스트
- 신규 `run/discovery/tests/test_portfolio_core_defects_20261111.py` 48건(합성 데이터).
- 기존 테스트 변경: `test_portfolio_multi_20261110.py` — 펀딩을 쓰는 3개 테스트에 선언 간격을 주는 `declared()` 도우미(조건 약화 없음), 실패 전략 테스트가 `_validation_study`도 교체, 기본 설정 테스트가 v1 불변 + v2 기본을 확인. `test_pipeline.py`의 `SyntheticProvider`가 8h 선언 간격과 펀딩 메타를 제공.
