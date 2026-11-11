# ADR 0072 — 펀딩 CSV 원본 순서 검사·창 끝 신호 전수 집계·엔벌로프 낙폭 판정 연결·Cross stale mark 처리 (portfolio-multi-v3)

- 상태: Accepted
- 날짜: 2026-11-11
- 요청: 동동, 2026-11-11 "NEW2 추가 결함 수정 및 최종 정밀 검증 지시문 — ADR 0071 이후 후속 작업"
- 관련: ADR 0071(portfolio-multi-v2, portfolio-validation-v2, funding-schedule-v1), ADR 0068(포트폴리오 계층 v1)
- 시작 커밋: `89fcc6d` (main, PR #72 병합). 그 뒤 main에 추가 커밋 없음. ADR 0071은 고치지 않았다.

## 배경: 재현된 결함

수정 전 코드(`89fcc6d`)에서 새 테스트 `run/discovery/tests/test_followup_defects_20261111.py` 41개를 돌린 결과는 실패 16, 오류 20(새 보고 항목이 없어서 생긴 KeyError/TypeError), 통과 5였다. 통과한 5개는 수정 전후가 같아야 하는 회귀 기준 테스트다.

| # | 결함 | 수정 전 동작 (재현 테스트) |
|---|---|---|
| 1 | `data_quality.load_funding_csv_checked()`가 행을 읽자마자 `out.sort()`로 정렬했다. 그 뒤 `funding_quality()`·`funding_schedule.series_issues()`는 정렬된 배열만 보므로 원본의 시간 역순을 볼 수 없다. `runner.load_funding_window_checked()`도 월별 파일을 합친 뒤 다시 정렬했다. | 00→16→08 CSV가 `ok=True`. 2월 파일이 1월의 빠진 정산을 메워도 정상 처리 (`TestFundingSourceOrderFile.test_reproduction_00_16_08_…`, `TestFundingSourceOrderWindow.test_reproduction_later_file_filling_…`, `TestFundingSourceOrderProvider.test_pipeline_dev_evaluation_is_blocked_data…`) |
| 2 | `evaluator.run_event_study()`가 처음 잘린 신호(`exit_i >= n`, `exit_rule`이 None)에서 `break`했다. 그 뒤 신호는 어느 칸에도 들어가지 않았다. 항등식은 맞았다. 세지 않은 신호는 양쪽 모두에서 빠졌기 때문이다. | 40봉·h3·매 봉 신호: `signals_total` 37(실제 38), truncated 1(실제 2) (`TestWindowEndSignals.test_reproduction_every_bar_signal_horizon_3_…`) |
| 3 | `max_drawdown_equity_peak_to_trough`는 계산만 하고 `judge()`와 `portfolio_validation_v2.json`에는 없었다. | 종가 0, 봉 내부 최악 0.40, 엔벌로프 0.538인 결과가 PASS_PORTFOLIO (`TestEnvelopeDrawdownGate.test_reproduction_envelope_alone_…`) |
| 4 | Cross 시가·봉 내부 계좌 청산 검사가 현재 봉이 없는 심볼을 마지막 종가로 평가(`stale_o`)하고도 청산을 확정했다. 청산으로 포지션이 모두 닫히면 그 봉의 `stale_marks`도 세지 않아 낙폭 상태가 OK로 남을 수 있다. 같은 시점 신규 진입도 stale 자본으로 크기를 정했다. | A 시가 30·B 봉 없음(종가 100 사용): 실제 관측되지 않은 계좌 청산 2건 기록 (`TestCrossStaleMarks.test_reproduction_stale_mark_…`, `test_intrabar_check_with_a_stale_symbol_is_deferred`) |

모든 재현은 합성 데이터와 임시 파일로 했다. 실제 시장 데이터는 이번에 읽지 않았고 실데이터 결과도 다시 계산하지 않았다.

## 결정

### A. 펀딩 CSV 원본 순서 (data_quality, runner)
- **정렬 전에 검사한다.** `load_funding_csv_checked()`는 파싱된 각 행을 같은 파일의 직전 파싱 행과 비교한다. 더 작으면 `SOURCE_TIME_INVERSION`, 같으면 `SOURCE_DUPLICATE_TS`다. 파싱 실패 행은 기준이 되지 않는다(그 행은 따로 `parse_errors`로 차단). 기록 항목은 `{kind, file, line(1부터, 헤더 포함), prev_ts, ts, effect}`이고 보고서 키는 `source_order_errors`다.
- **정렬은 계산용 정규화로만 남긴다.** 반환되는 pairs는 지금처럼 정렬돼 있다(`pairs_sorted_for_computation: true`). 오류 목록은 보고서에 남는다. 정렬은 오류를 지우지 못한다.
- **월별 파일 통합.** `load_funding_window_checked()`는 각 파일의 오류를 모은다. 그다음 월 순서로 이어 붙였을 때 파일 첫 행이 직전(비어 있지 않은) 파일의 마지막 원본 행보다 늦은지 확인한다. 아니면 `CROSS_FILE_TIME_INVERSION` / `CROSS_FILE_DUPLICATE_TS`로 기록하고 `prev_file`을 남긴다. 그 뒤 정렬한다. `files`에 파일명·sha256·행 수를 남긴다(파일 내용이 바뀌면 sha가 바뀐다).
- **품질 상태.** `funding_quality(..., source_order_errors=…)`는 오류가 하나라도 있으면 `SOURCE_ORDER_INVALID` 이슈를 내고 `ok=False`가 된다. `status`는 기존 체계를 따른다. 정상은 `OK`, 이슈가 있으면 `FUNDING_DATA_INVALID`, 행이 없으면 `FUNDING_DATA_MISSING`이다. 펀딩비를 0으로 채우지 않는다.
- **정산 간격이 없거나 잘못된 행**은 `interval_missing_rows`로 보고하고 경고를 남긴다. 그 정산에는 선언된 간격이 없다. 그래서 그 정산을 지나는 보유 구간은 지금처럼 `FUNDING_SCHEDULE_UNKNOWN`이 되고 통과할 수 없다(ADR 0071 규칙, 8h를 가정하지 않음).
- **전달 경로.** `checked_funding_quality()`가 `source_order_errors`를 넘긴다. 그래서 이를 쓰는 모든 경로가 차단된다. 해당 경로는 `RunnerDataProvider.symbol()`·`_range()`·locked 로드, `runner.Runner._sym_data()`·locked, `independent_window`, `liquid_search_dev`·`strong_search_dev`·`evalue_v3_block`이다. 차단된 심볼은 `funding=None`이고 `missing`에 사유가 적힌다. 그러면 DEV가 `BLOCKED_DATA`가 되고 검증·포트폴리오 단계에 가지 않는다(통합 테스트로 확인). `FundingIndex`·`settlement_check`·포트폴리오에는 이 시리즈가 들어가지 않는다. `data_fetch._quality_ok`·`data_manifest`도 같은 인자를 넘긴다. `paper_forward`의 로컬 피드는 순서 오류가 있는 파일을 쓰지 않는다. 제공자 기록에는 `funding_quality`(status·issues·source_order_errors)와 `funding_files`가 남는다.

### B. 신호 집계 (evaluator)
- `exit_i >= n`과 `exit_rule(...) is None`에서 `break` 대신 해당 신호를 `truncated_end_of_window`에 넣고 **다음 신호로 계속**한다.
- 잘린 신호는 거래가 아니다. 그래서 `last_exit_index`를 움직이지 않는다. 그 뒤 신호는 마지막 **채택된** 거래와 겹칠 때만 `skipped_overlapped`가 된다.
- 창 끝 검사는 `exit_rule` 호출 전에 한다(기존 순서 유지). 그래서 끝 근처 신호가 "손절이 마침 일찍 나서" 채택되는 일은 없다. 채택 여부가 결과에 의존하지 않는다.
- 결정 봉 t는 `[warmup, n-2]` 전부다. 마지막 봉의 결정은 진입 봉이 없으므로 이 창의 신호가 아니다(기존과 같음).
- 항등식 `signals_total = accepted + skipped_overlapped + truncated + participation_refusals + cost_data_missing + dropped_missing_funding`은 `diagnostics()["accounting_ok"]`로 검사하고 보고한다. 숫자를 맞추는 보정은 없다. `to_json()`과 `diagnostics()`의 칸 값이 같음을 테스트로 확인했다.
- DEV·검증·locked·IW1·paper_forward·cost_sensitivity가 모두 같은 `run_event_study`를 쓰므로 규칙은 하나다. `liquid_search_dev.py`·`strong_search_dev.py`의 자체 numpy 이벤트 루프에도 `break`가 있다. 하지만 이들은 신호 회계를 내지 않는 DEV 전용 도구다. 채택 이벤트는 같고 집계 칸이 없으므로 이번 범위에서 바꾸지 않았다(남은 위험에 기록).
- **기존 결과와의 차이.** 채택 이벤트·수익률·비용·펀딩·거절 수·거절률은 바뀌지 않는다. 고정 보유 기간에서는 첫 잘린 신호 뒤의 모든 신호도 `entry+h >= n`이기 때문이다. 바뀌는 것은 `signals_total`과 `truncated_end_of_window`뿐이다. 이를 차등 검사로 확인했다. 합성 400건(이전 코드 `89fcc6d`와 새 코드, 손절·익절·펀딩·거래대금 0 포함)에서 이벤트·순수익·거절 수·거절률은 400건 모두 같았다. `signals_total`은 354건에서 늘었고 잘린 신호는 합계 2065개 늘었다. 기존 테스트 하나의 기대값을 truncated 1에서 2로 바꿨다(`test_portfolio_core_defects_20261111.test_every_signal_in_one_bucket`). 이유는 위 재현이며, 기대값만 맞춘 것이 아니라 이전 값이 결함이었다.

### C. 엔벌로프 낙폭 (portfolio_multi.judge, portfolio_validation_v2.json)
- 표현하는 위험: OHLC 봉에서는 고가와 저가 중 무엇이 먼저 났는지 알 수 없다. 엔벌로프는 같은 봉에서 유리한 극값(고점 갱신)이 먼저, 불리한 극값이 나중에 났다고 보는 가장 불리한 순서의 고점→저점 낙폭이다. **관측된 경로가 아니라 봉이 허용하는 스트레스 경계다.** 실현 낙폭(종가)이나 동시 최악(intrabar)보다 항상 크거나 같다.
- 기준: 저장소에 사전 등록된 계좌 손실 한도는 `portfolio.PORTFOLIO_PASS_CRITERIA`의 `max_drawdown_lt 0.5` 하나뿐이다. 그 값을 그대로 쓴다. 새 숫자는 없다. 이 기준은 수익성이나 유의성 증거가 아니라 계좌 위험에 대한 추가 거부 조건이다.
- 연결: `JUDGE_KEYS_V2`에 `max_drawdown_equity_peak_to_trough_lt`를 추가했다. `judge()`는 세 낙폭(종가·봉 내부 최악·엔벌로프)을 같은 방식으로 검사한다. 상태가 OK가 아니거나 값이 None·NaN·Inf·bool이면 `UNDETERMINED_PORTFOLIO`가 된다(0으로 대체하지 않음). 값이 0.5 이상이면 `FAIL_PORTFOLIO`다. 세 기준을 모두 통과해야 PASS가 될 수 있다.
- 설정: `base`·`stress_cross_3x`(gating)와 `stress_partial_latency`(진단, `gating=false` 유지)에 `0.5`를 추가했다. 버전 문자열은 `portfolio-validation-v2`를 유지한다. 지시대로 최소 변경이며 `revisions`에 기록했다. 파일 해시(`portfolio_config_hash`)와 컨텍스트의 코드 해시가 바뀌므로 ADR 0071 파일로 만든 기록과 섞이지 않는다. v1 파일은 바꾸지 않았다.
- 키 누락 정책: `pipeline.load_portfolio_config()`는 v2 스키마의 gating 시나리오에 세 낙폭 키 중 하나라도 없으면 파일을 거부한다(ValueError). v1 스키마는 v1 키 체계 그대로 읽힌다.
- 이 추가는 PASS를 FAIL/UNDETERMINED로만 바꿀 수 있다. 완화가 아니다.

### D. Cross stale mark (portfolio_multi.simulate_multi, portfolio-multi-v3)
- 발생 상황: 여러 심볼을 들고 있는데 그중 한 심볼에 그 시각 봉이 없을 때다(거래소 데이터 공백, 심볼별 검증 창 길이 차이, 상장 전후). `_mark()`는 그 심볼의 마지막 종가를 돌려준다.
- 영향: Cross 청산은 계좌 전체 자본과 유지증거금으로 판정한다. 한 심볼은 이번 시가, 다른 심볼은 과거 종가로 평가하면 실제로 존재한 적 없는 혼합 시점의 계좌가 된다.
- 청산 판정 보류: 시가 단계(②)와 봉 내부 단계(⑥)에서 열린 포지션 중 하나라도 그 시각 봉이 없으면 계좌 청산 검사를 **확정하지 않는다.** 청산 이벤트도 현금 차감도 없다. 대신 `cross_account_valuation.events`에 시각·단계(open/intrabar)·stale 심볼·stale 가격 기준 자본/유지증거금·"stale 가격이면 청산이었을지"를 남긴다. 다음 완전한 시각에 관측된 가격으로 다시 검사한다(테스트: 봉이 돌아온 시각에 청산).
- 신규 진입 차단: Cross에서 열린 포지션의 심볼 중 하나라도 그 시각 봉이 없으면 그 시각의 신규 진입은 모두 `account_valuation_incomplete`(class `data_missing`)로 거절한다. 순서는 전략 자체 규칙(inactive, duplicate) 다음, 크기 계산 전이다. 사유가 `SKIP_REASONS`/`SKIP_CLASS`에 들어 있어 신호 회계 항등식이 유지된다(테스트에서 `identity_ok=True`). v2 설정의 `max_data_missing_signals 0`에 따라 그런 결과는 `INSUFFICIENT_PORTFOLIO_DATA`가 된다.
- 최종 상태: `cross_account_valuation.status`는 `COMPLETE`, `INCOMPLETE`(보류된 검사 또는 차단된 진입이 있음), `NOT_APPLICABLE`(isolated) 중 하나다. `judge()`는 `INCOMPLETE`이면 `UNDETERMINED_PORTFOLIO`를 낸다. 숫자 조건보다 먼저 검사하므로 PASS가 될 수 없다. 열린 포지션이 없으면 검사할 계좌가 없으므로 COMPLETE다.
- 모든 심볼에 봉이 있는 기존 Cross 테스트와 Isolated 동작은 바뀌지 않았다(기존 테스트 전부 통과, 새 테스트로도 확인).
- 계산이 바뀌었으므로 `PORTFOLIO_MULTI_VERSION`을 `portfolio-multi-v3`으로 올렸다. 설정의 `model_version_required` 설명과 기존 테스트 기대값 1곳(`TestPipelineWiring`, v2→v3)을 함께 바꿨다.

## 바꾸지 않은 것
promo-v5(DSR 0.95, 부트스트랩 CI 하한 > 0), StepM·PBO·FWER·alpha spending, portfolio-validation-v1, funding-schedule-v1, 기존 결과·기록 파일은 그대로다. 잠금 TEST·IW0·IW1·sealed 데이터는 열지 않았다. 실거래·주문·자금 이동 기능은 없다.

## 기존 결과 재사용
- 실데이터 포트폴리오 기록은 0건이다(VALIDATION_SURVIVOR 0). 무효가 되는 실데이터 판정은 없다.
- DEV·검증 기록의 이벤트·통계는 B의 차등 검사대로 같은 값이다. 다만 진단(`signals_total`, `truncated`)은 달라진다. 또 `evaluator.py`·`data_quality.py`·`runner.py`·`pipeline.py`·`portfolio_multi.py`와 설정 파일이 코드 해시에 들어 있어 실험 컨텍스트 ID가 바뀐다. 그래서 이전 기록은 새 컨텍스트에서 재사용되지 않고 섞이지 않는다.
- 원본 순서 오류가 있는 펀딩 파일이 실제 아카이브에 있는지는 이번에 확인하지 않았다(실데이터 미사용). 있다면 그 심볼은 이제 BLOCKED_DATA다.

## 한계
- 엔벌로프와 Cross 동시 극값은 모델 가정이다. 실제 체결 경로는 1분봉이나 체결 데이터가 있어야 알 수 있다.
- Cross 보류는 "판정 불가"로 남길 뿐이다. 그 사이 실제로 청산됐을 가능성은 결과를 UNDETERMINED로 만드는 것으로만 반영된다.
- 거래소별 청산 규칙(등급별 유지증거금, 보험기금, ADL)은 여전히 `NOT_MODELLED`다.

## 테스트
`run/discovery/tests/test_followup_defects_20261111.py` 41개(합성 데이터·임시 CSV만)와 기존 테스트 기대값 2곳 변경. 전체 결과는 PR 본문과 `run/audit/FINAL_REPORT_20261111_FOLLOWUP.md`에 있다.
