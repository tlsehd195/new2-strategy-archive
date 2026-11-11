# ADR 0073 — Paper Forward 펀딩 데이터 무결성: 확인하지 못한 펀딩을 비용 0으로 계산하거나 PAPER 통과로 인정하지 않는다 (paper-forward-funding-v1)

- 상태: Accepted
- 날짜: 2026-11-11
- 요청: 동동, 2026-11-11 "Claude Code 작업 지시문 — Paper Forward 펀딩 데이터 무결성 및 비용 0 오판 문제 해결 (ADR 0073)"
- 관련: ADR 0072(펀딩 CSV 원본 순서 검사), ADR 0071(funding-schedule-v1, 선언된 정산 주기), ADR 0006(forward paper)
- 시작 커밋: `0725f86` (main, PR #73 병합). 그 뒤 main에 추가 커밋 없음. ADR 0072는 고치지 않았다.

## 배경: 재현된 결함

새 테스트 파일 `run/discovery/tests/test_paper_forward_funding_20261111.py`의 재현 테스트 4개(`TestReproduction`)를 **수정 전 코드에 먼저** 돌렸다. 4개 모두 실패했다.

| # | 결함 (수정 전 코드 위치, `0725f86`) | 수정 전 동작 (재현 테스트) |
|---|---|---|
| 1 | `paper_forward.py:461,464` `funding = sorted(set(fund))` 뒤 `run_event_study(..., funding or None, ...)`. `evaluator.py:289-297`은 `funding is None`이면 `fp = 0.0`이다. | 펀딩 자료가 하나도 없는 피드로 75일 관찰 → 모든 거래의 펀딩 0bp, **`PAPER_PASSED`** (`test_repro_no_funding_at_all_is_not_zero_cost_pass`) |
| 2 | `paper_forward.py:463-465`가 `funding_intervals`를 넘기지 않았다 → `NOMINAL_8H_LEGACY`. 4h 시리즈에서 정산 하나가 빠지면 간격 8h가 되는데, 명목 8h 규칙은 이를 정상으로 본다. | 4h 정산 하나 누락 → 거래가 정산 하나만큼 적게 부과된 채 **`PAPER_PASSED`** (`test_repro_missing_4h_settlement_hidden_by_nominal_8h`) |
| 3 | `FileFeed.funding()` (`paper_forward.py:130-139`): 파싱 오류·잘린 행·원본 순서 오류가 있는 파일을 `continue`로 건너뛰고, 나머지 파일의 행만 돌려준다. 건너뛴 사실은 어디에도 남지 않았다. | 8월 정상·9월 역순·10월 정상 → 9월을 빼고 184개 정산을 오류 없이 반환 (`test_repro_corrupt_middle_month_file_is_not_silently_skipped`) |
| 4 | 3의 부분 시리즈가 그대로 `step()`(`:371`)의 펀딩 로그와 `_evaluate()`로 들어갔다. 보유 구간이 빈 곳에 걸린 거래는 평가기에서 조용히 빠졌고(`excluded["no_funding_coverage"]`, `:466`), `_verdict()`(`:550`)는 펀딩 상태를 보지 않았다. | 손상된 9월 파일이 있는 전체 경로 → **`PAPER_PASSED`** (`test_repro_corrupt_month_file_end_to_end`) |

그 밖에 확인한 문제:
- `_new_state()`(`:297`)는 펀딩 수집 시작점을 `start_ts - 8h`로 하드코딩했다. warm-up 펀딩과 겹치는 행은 `sorted(set(...))`가 우연히 없앴다.
- `BinancePublicFeed.funding()`(`:179`)은 API 응답을 확인 없이 그대로 믿었다. 페이지 끝(1000행), 중복·역순, 조회 범위 밖 행, 정산 주기 정보가 없는 경우를 구분하지 않았다.
- 상태 파일이 깨지면 `json.loads` 예외로 멈췄다. 펀딩 수집 도중 중단되면(로그 추가 후 상태 저장 전) 다음 단계에서 같은 정산이 두 번 기록될 수 있었다(`_recover_from_logs`는 봉 로그가 앞설 때만 펀딩 위치를 맞췄다).

모든 재현은 합성 데이터와 임시 디렉터리로 했다. 실제 시장 데이터, 기존 아카이브, 잠금 TEST·IW0·IW1·sealed 구간은 열지 않았다. 네트워크 경로는 가짜 opener로만 시험했다.

## 결정

### A. 펀딩 상태 어휘 (fetch와 관찰 구간 공통)

| 상태 | 의미 | 처리 |
|---|---|---|
| `VALID` | 원본 무결성·선언 주기·필요 정산이 확인됨 | 평가 |
| `VALID_ZERO_RATE` | 정산 기록이 있고 기록된 비율이 정확히 0 | 평가 (0은 실제 관측값) |
| `DATA_MISSING` | 펀딩 원본이 없음 (파일 없음, 시리즈 없음) | 평가 안 함 |
| `DATA_INVALID` | 파싱 오류·잘린 행·파일 내부/파일 간 역순·중복, API 응답 행 손상, **이전에 읽은 파일의 sha256 변경·삭제** | 계획 영구 차단 (`BLOCKED_DATA`) |
| `COVERAGE_INCOMPLETE` | 관찰 구간에 필요한 정산 일부가 없음 (중간·시작·끝), API 페이지가 끝까지 가득 참 | 평가 안 함, 다음 단계에 재확인 |
| `SCHEDULE_UNKNOWN` | 정산의 선언된 주기를 알 수 없음 | 평가 안 함 (8h로 대체하지 않음) |
| `SOURCE_ERROR` | 파일 읽기 오류, API 요청 실패·오류 객체, 피드 예외 | 평가 안 함, 다음 단계에 재시도 |

`None`(펀딩을 부과하지 않음), 빈 리스트(정산 없음 → 모든 거래 제외), 정산 기록이 있는 0% 비율은 서로 다른 것으로 다룬다. Paper Forward는 `None`을 쓰지 않는다.

### B. 피드 (`paper_forward.py`)
- **`funding_checked(sym, after_ts)`** 를 두 피드에 추가했다. 반환은 `funding_fetch_result()` 구조다: `status, rows(원본 순서), intervals({ts: ms}), issues, source_files[{name, sha256, rows, status, errors}], schedule_source, query{after_ts, until_ts, source}, zero_rate_rows, policy`. `FileFeed`는 `missing_files`(조회 구간 중 파일이 없는 월)와 `future_rows`도 남긴다.
- **FileFeed**: 조회 구간과 기간이 겹치는 파일을 모두 읽는다(이름을 해석할 수 없는 파일은 관련 있다고 본다). 검사는 새 공용 함수 `data_quality.load_funding_files_checked()`로 한다. 이 함수는 ADR 0072의 `runner.load_funding_window_checked()` 안에 있던 파일 내부·파일 간 검사를 옮긴 것이다. `runner`는 이제 이 함수를 호출하며, 보고서 키(`parse_errors, torn_tail, intervals, source_order_errors, interval_missing_rows, files`)는 그대로다. 파일별 판정 `file_status`만 추가했다. 그래서 연구 경로와 Paper Forward가 같은 품질 정의를 쓴다. 관련 파일 중 **하나라도** 문제가 있으면 전체 응답이 `DATA_INVALID`다. 파일 간 오류는 두 파일 모두에 표시한다. 정상 파일만 골라 돌려주는 일은 없다.
- **파일이 없는 월**은 그 자체로 손상이나 정상으로 판정하지 않는다. `missing_files`로 기록하고, 정산이 필요했는지는 C의 커버리지 검사(선언 주기)가 판단한다. 같은 누락 파일이라도 보유 구간이 그 달에 걸리지 않으면 영향이 없다(테스트 `test_b9`).
- `FileFeed.funding()`(목록 인터페이스)은 상태가 `VALID`/`VALID_ZERO_RATE`가 아니면 `FundingSourceInvalid`를 낸다. 빈 리스트나 부분 리스트를 돌려주지 않는다.
- `FileFeed.funding_source_hashes(sym)`: 현재 디스크의 파일별 sha256. 계획이 한 번 읽은 파일이 바뀌거나 사라졌는지 매 단계 확인한다.
- **BinancePublicFeed**: `/fapi/v1/fundingInfo`의 `fundingIntervalHours`를 선언 주기로 쓰고, 출처와 관측 시각을 `schedule_source`에 남긴다. 심볼이 목록에 없으면 `SCHEDULE_UNKNOWN`이다(Binance 문서의 "기본 8h"도 가정하지 않는다). `/fapi/v1/fundingRate`는 `startTime/endTime/limit`으로 최대 20페이지까지 받는다. 요청 실패·JSON 아님·오류 객체는 `SOURCE_ERROR`, 파싱 불가·비유한·다른 심볼·조회 범위 밖·중복·역순 행은 `DATA_INVALID`, 마지막 페이지가 가득 차면 `COVERAGE_INCOMPLETE`다. 빈 응답은 오류가 아니다("구간에 정산 없음"). 정산이 필요했는지는 커버리지가 판단한다. 각 응답의 sha256을 남긴다.

### C. 정산 완전성 (`funding_schedule.coverage_check`)
관찰 구간 `[start_ts, 마지막 관찰 봉]` 전체를 검사한다. 내부 검사는 기존 `settlement_check()`를 그대로 호출한다(같은 시리즈 규칙, 같은 정산별 선언 주기, 같은 허용 오차). 거래별 검사로는 보이지 않는 두 끝을 더했다.
- 시작(`HEAD_NOT_COVERED`): `start_ts` 이전 정산이 있어야 시작 직후 정산 누락이 없음을 증명할 수 있다.
- 끝(`TAIL_DUE_MISSING`): 마지막 정산의 선언 주기만큼 뒤가 끝 시각(+허용 오차)보다 이르면, 그 정산이 기록되지 않은 것이다. 마지막 정산의 주기를 모르면 `SCHEDULE_UNKNOWN`이다.
- 기록된 0% 정산 수(`zero_rate_settlements`)를 따로 센다.

`settlement_check()`·`series_issues()`·`FundingIndex` 자체는 바꾸지 않았다. 그래서 DEV·검증 경로의 동작은 같다.

### D. 평가기 (`evaluator.py`)
- `run_event_study(..., require_funding=False)` 인자를 추가했다. `True`이면 `funding=None`(0bp 부과)이나 `funding_intervals=None`(NOMINAL_8H_LEGACY)을 `FundingRequired`로 거부한다. 기본값은 `False`라서 다른 호출자(IW1, runner v1, cost_sensitivity, pipeline)의 동작은 같다.
- 확인한 기존 동작(테스트로 고정): 빈 리스트 → 모든 거래 `FUNDING_DATA_MISSING`으로 제외(0 부과 없음). 첫 정산이 진입보다 늦거나 마지막 정산이 청산보다 이르면 제외. 선언 주기가 있으면 중간 누락은 `FUNDING_SETTLEMENT_GAP`, 주기가 없으면 `FUNDING_SCHEDULE_UNKNOWN`. 0% 정산은 `paid_bp = 0.0`, 상태 OK.

### E. Paper Forward 엔진 (`ForwardPaper`)
- **계획 등록**: warm-up 봉과 펀딩을 먼저 모두 계산한 뒤 쓴다. warm-up 펀딩 상태가 `VALID`/`VALID_ZERO_RATE`가 아니거나 시작 이전 정산이 없으면 `ForwardRefused`다. 이때 계획·디렉터리·상태 변경은 남지 않는다. 계획에 `funding_policy: "paper-forward-funding-v1"`을 기록하고, warm-up 파일에 `funding_intervals`, `funding_fetch`, `funding_sources`를 기록한다. 펀딩 수집은 마지막 warm-up 정산 바로 뒤에서 시작한다(`start - 8h` 제거).
- **수집 `_collect_funding`**: fetch 요약을 `funding_quality_<SYM>.jsonl`에 **상태를 바꾸기 전에** 추가한다. `DATA_INVALID`나 원본 sha256 변경·삭제(`FUNDING_SOURCE_CHANGED`)는 `BLOCKED_DATA`로 영구 차단한다. `SOURCE_ERROR`는 아무것도 추가하지 않고 다음 단계에 재시도한다. 나머지는 정산을 선언 주기와 함께 **해시 체인** `funding_<SYM>.jsonl`에 추가한다. 선언 주기로 보아 빈 곳이 생기는 첫 정산에서 멈추며, 그 뒤는 다음 단계에 다시 받는다(`COVERAGE_INCOMPLETE`).
- **평가 `step` / `_evaluate`**: 평가 전에 심볼별로 `_funding_window_status()`(커버리지 + 이번 fetch 상태)를 계산한다. 모든 심볼이 사용 가능할 때만 평가기를 호출한다. 호출은 `run_event_study(..., funding, funding_intervals=선언 주기, require_funding=True)`이다. 펀딩 때문에 제외된 거래가 하나라도 있으면 그 단계도 차단한다. 예전에는 조용히 `excluded`로 빠졌다.
- **판정 `_verdict`**: `ERROR`/`BLOCKED_DATA` 다음에 검사한다. ADR 0073 이전 계획(정책 표시 없음)은 항상 `PAPER_BLOCKED_DATA`(`LEGACY_PLAN_FUNDING_UNVERIFIED`)이고, 펀딩이 확인되지 않은 단계는 `PAPER_BLOCKED_DATA`(`FUNDING_<상태>: <사유>`)다. 둘 다 non-final이고 전략 상태를 바꾸지 않는다. `PAPER_PASSED`도 `PAPER_FAILED`도 아니다.
- **기록 `_conclude`**: 펀딩이 확인되지 않으면 예외로 거부한다(판정 함수와 별도의 두 번째 게이트). 평가 기록에 `funding_evidence{policy, schedule(DECLARED), symbols{status, coverage, sources(sha256)}}`를 남긴다.
- **재시작·복구**: 상태 파일을 읽을 수 없거나 구조 검증(`_state_problems`: 상태 값, 심볼별 키, 정수 시각, 펀딩 원본 지도, 알려진 펀딩 상태)에 실패하면 `PAPER_ERROR`(`STATE_CORRUPT`)다. 이때 상태 파일을 덮어쓰지 않고 증거로 남긴다. 단계 기록에만 추가한다. `_recover_funding`은 펀딩 로그에서 `last_funding_ts`와 읽은 원본을 다시 만든다(중단 후 중복 기록 없음). 품질 로그에 `DATA_INVALID` 기록이 있는데 상태가 `OBSERVING`이면 다시 `BLOCKED_DATA`로 만든다(`FUNDING_BLOCK_RESTORED`). 그래서 이전 상태 파일로 되돌려도 차단이 풀리지 않는다. 펀딩 로그 체인이 깨지면 `ERROR`다.

### F. 상태 체계
새 전략 상태는 추가하지 않았다. 기존 `PAPER_BLOCKED_DATA`(non-final, 상태 변경 없음)와 `PAPER_ERROR`를 쓴다. 그래서 `STATUSES`/`TRANSITIONS`/상태 저장·복구 규칙은 바뀌지 않는다. 데이터 부족을 `PAPER_FAILED`로 단정하지 않는다.

### G. 바꾸지 않은 것
promo-v5, DSR, Bootstrap CI, StepM, PBO, FWER, alpha spending, 후보 생성·순위, DEV·검증 판정, 잠금 데이터 규칙, `config/paper_forward_v1.json` 기준값은 그대로다. `NOMINAL_8H_LEGACY`를 쓰는 다른 호출자(IW1 `independent_window`, runner v1, `cost_sensitivity`, `paper.py` 재생 — 모두 `funding_intervals`를 넘기지 않음)도 바꾸지 않았다. `paper.py`는 `PAPER_PASSED`를 만들 수 없다. 기존 계획·평가 기록은 지우거나 다시 쓰지 않는다. 저장소에는 forward 계획이나 `PAPER_PASSED` 기록이 없다(검색으로 확인).

## 기존 결과와의 차이 (차등 검사)
- 수정 전 코드(`0725f86`)로 정상 합성 펀딩 5가지(1h·4h·8h 선언, 8h 파일 피드, 8h 0%)의 forward 이벤트를 캡처했다. 결과는 `tests/data/adr0073_forward_baseline_0725f86.json`에 넣었다(8h 선언은 8h 파일과 같아 제외, 4가지 저장). 수정 후 결과는 이벤트 75건씩 **필드 하나까지 같았다**(진입·청산 시각, 방향, gross, 비용, 펀딩, net, 실행 후 net, 체결 상태). 판정도 모두 `PAPER_PASSED`로 같다(`test_a7_results_equal_the_pre_fix_baseline`). 이 전략(2시간 보유)에서는 1h 경우만 펀딩이 실제로 부과된다(합계 15bp). 그래서 평가기 단계에서 48봉 보유·0이 아닌 펀딩으로 1h/4h/8h를 따로 비교했다. 기존 명목 경로와 선언 경로의 이벤트가 같았다(`test_a7_valid_complete_series_declared_equals_legacy`).
- 바뀐 것은 무효·불완전 펀딩의 판정뿐이다. 예전 `PAPER_PASSED`가 이제 `PAPER_BLOCKED_DATA`이거나 등록 거부다.
- 기존 테스트 픽스처 `test_completion.SynthFeed`에 `funding_checked()`를 추가했다. `synth_funding()`이 실제로 8시간마다 만드는 정산 주기를 픽스처가 **선언**한다(`FUNDING_INTERVAL_MS`). 기대값은 하나도 바꾸지 않았다. 선언 주기가 없는 피드는 이제 등록이 거부되기 때문에 필요했다.
- 실험 컨텍스트: `paper_forward.py`, `evaluator.py`, `data_quality.py`, `runner.py`, `funding_schedule.py`가 `EVO_CODE_FILES`에 포함되므로 `code_hash`가 바뀌고 새 `context_id`가 된다. 이것은 기존 설계다. 이전 컨텍스트의 상태는 보존되고 재검증 대기(`reverification_pending`)로 보인다. 계획별 펀딩 출처(sha256)는 상태·품질 로그·평가 기록에 남는다. 원본이 바뀌면 그 계획은 차단되고, 같은 컨텍스트에서 두 번째 계획은 거부된다(기존 규칙). 그래서 데이터 컨텍스트가 섞이지 않는다.

## 테스트
- 신규: `test_paper_forward_funding_20261111.py` **51개**. 재현 4, FileFeed 14(A1·A3·A5, A4, B1–B10, 주기 없음, 구간 밖 손상 파일, runner 보고서 호환), 커버리지 7(C1–C8, 명목 8h 은폐), 평가기 4(require_funding, 빈 리스트, 시작·끝 미포함, 차등), REST mock 7(F1–F8), 전체 경로 10(D1–D7, 차등 기준선, 레거시 계획, `_conclude` 게이트), 재시작 5(E1–E7, 펀딩 로그 변조)이다. 전체 경로 테스트는 Feed → funding_checked → `_collect_funding` → 커버리지 → `run_event_study(require_funding)` → `_verdict` → `_conclude`/상태 파일까지 연결된다.
- 수정 전 코드: 재현 4개 모두 실패(위 표). 나머지는 새 API를 쓰므로 수정 전 코드에서는 실행되지 않는다.
- 로컬 CI(`research-ci.yml`의 모든 단계, Python 3.11.x와 3.13.x 각각): 단위 **882/882**(기존 831 + 신규 51), 루트 152/152, 레거시 1/1, compileall, ruff(게이팅·F 전체·run/ 전체), ShellCheck, 설정 스키마, 표준 라이브러리 의존성, ledger 2개, 스크립트 목록 diff, 소스 감사표, 감사 출처표, TypeScript 변환 검사, 패키지 manifest. 모두 통과했다.
- GitHub Actions: 별도 확인(최종 보고서). 2026-11-09 이후 계정 수준 문제로 runner가 배정되지 않으면 통과로 기록하지 않는다.

## 한계
- 실제 시장 데이터·실계좌 체결로는 확인하지 않았다(금지 범위). 합성 데이터 검증뿐이다.
- 아카이브 CSV의 `funding_interval_hours`가 "그 정산으로 끝나는 구간"의 길이라는 해석은 ADR 0071의 해석을 따른다. Binance가 이 의미를 공식 문서로 밝힌 것은 확인하지 못했다.
- REST 피드는 조회 시점의 `fundingInfo` 주기를 그 조회로 받은 정산에 적용한다. 조회 사이에 주기가 바뀌면 바뀐 시점을 정확히 알 수 없다. 대부분의 8h 심볼은 `fundingInfo`에 나오지 않으므로 이 피드에서는 `SCHEDULE_UNKNOWN`으로 평가되지 않는다(안전 쪽).
- 주기가 짧아진 경우(선언 4h인데 2h 간격으로 기록)는 빈 곳으로 탐지하지 않는다(기존 `settlement_check` 규칙 그대로).
- 아카이브 파일이 늦게 올라오면 끝 정산 미기록으로 `PAPER_BLOCKED_DATA`가 이어질 수 있다. 의도한 안전 쪽 동작이며, 파일이 생기면 다음 단계에서 풀린다.
- `NOMINAL_8H_LEGACY`는 IW1(`independent_window`)·runner v1·`cost_sensitivity`·`paper.py` 경로에 남아 있다(이번 범위 밖, 기록만). DEV·검증 파이프라인(`pipeline` 제공자)은 ADR 0071부터 선언 주기를 넘긴다.
