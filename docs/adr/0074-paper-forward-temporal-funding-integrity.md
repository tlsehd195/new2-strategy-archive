# ADR 0074 — Paper Forward 펀딩의 시간적 유효성과 증거 파일 무결성: 관찰 끝의 정산 누락, 현재 정산 주기의 과거 소급, 품질 로그·워밍업·state 검증 (paper-forward-integrity-v1)

- 상태: Accepted
- 날짜: 2026-11-11
- 요청: 동동, 2026-11-11 "ADR 0074 — Paper Forward 펀딩 커버리지 경계·과거 정산 주기·증거 로그 무결성 전면 수정"
- 관련: ADR 0073(paper-forward-funding-v1, 이 ADR은 0073을 고치지 않고 위에 얹는다), ADR 0071(funding-schedule-v1), ADR 0006(forward paper)
- 시작 커밋: `ead5e17` (main, PR #74 병합). 작업 시작 시 main HEAD도 `ead5e17`이었다.

## 배경: 수정 전 코드에서 재현한 결함

`run/discovery/tests/test_paper_forward_funding_integrity_20261111.py`의 `TestReproduction0074` 8개를 **수정 전 코드(`ead5e17`)에 먼저** 돌렸다. 8개 모두 실패했다.

| # | 결함 (수정 전 위치) | 수정 전 동작 |
|---|---|---|
| 1 | `funding_schedule.coverage_check()` 끝 검사 `if end_ts - times[k1] > iv + tol_ms` | 08:00 정산, 4h 선언, 관찰 끝 12:00, 12:00 정산 없음 → `OK` |
| 2 | 1과 같은 경계를 Paper Forward 전체 경로로 | 마지막 봉이 정산 예정 시각과 같고 그 정산이 없음 → 펀딩 상태 `VALID`, 판정 **`PAPER_PASSED`** |
| 3 | `BinancePublicFeed.funding_checked()` `ivs = {t: iv for t, _ in rows}` | 지금 `fundingInfo`는 4h, 과거는 1h이고 하나 누락(2h 간격) → 모든 행에 4h가 붙어 `VALID`, 커버리지 `OK` |
| 4 | `funding_quality_<SYM>.jsonl`이 해시 체인이 아님 | DATA_INVALID 기록을 VALID로 고쳐도 오류 없음 |
| 5 | 같은 파일 | 마지막 기록(DATA_INVALID)을 지워도 오류 없음 |
| 6 | `warmup_<SYM>.json`에 무결성 검사가 없음 | 등록 뒤 워밍업 펀딩률을 바꾸면 그대로 평가 → **`PAPER_PASSED`** |
| 7 | `_state_problems()`는 `last_funding_ts`의 정수 여부만 봄 | state의 `last_funding_ts`를 3일 미래로 → `PAPER_BLOCKED_DATA`(커버리지 공백)일 뿐 무결성 오류로 잡지 않음 |
| 8 | funding log의 해시 체인을 state가 고정하지 않음 | 펀딩률을 바꾸고 체인을 다시 계산하면 그대로 평가 → **`PAPER_PASSED`** |

모든 재현은 합성 데이터·임시 디렉터리·가짜 opener로만 했다. 실제 시장 데이터, 기존 아카이브, 잠금 TEST·IW0·IW1·sealed 구간은 열지 않았다.

## 공식 문서 확인 (읽기 전용)

Binance USD-M 선물 REST 문서(developers.binance.com, Market Data, 2026-11-11 조회):

- `GET /fapi/v1/fundingInfo`: "Query funding rate info for symbols that had FundingRateCap/FundingRateFloor / fundingIntervalHours adjustment". 응답은 `symbol, adjustedFundingRateCap, adjustedFundingRateFloor, fundingIntervalHours, disclaimer`뿐이다. 유효 시작 시각·변경 이력 필드가 없다. `fundingIntervalHours`의 의미와 유효 시점은 설명되지 않는다. 조정이 없던 심볼은 목록에 없다.
- `GET /fapi/v1/fundingRate`: 행은 `symbol, fundingRate, fundingTime, markPrice, rateType`이다. **정산 주기 필드가 없다.** `startTime`/`endTime`는 포함(INCLUSIVE), 오름차순이다. 범위가 `limit`보다 많으면 `startTime`부터 `limit`개를 준다.
- `GET /fapi/v1/premiumIndex`: `nextFundingTime`(현재)만 있고 주기·이력은 없다.
- 과거 정산별 주기 이력을 주는 공식 엔드포인트나 문서는 찾지 못했다. 공지·FAQ·changelog는 확인하지 않았다.
- `binance-public-data` README는 fundingRate 데이터셋과 `calc_time, funding_interval_hours, last_funding_rate` 열을 설명하지 않는다. `funding_interval_hours`가 그 정산에서 **끝나는** 주기인지 **시작하는** 주기인지 공식 근거가 없다.

## 결정

### A. 관찰 구간 경계 (`coverage_check`, funding-coverage-v2)

시간 정의:
- `start_ts`는 계획의 첫 봉 시가 시각, `end_ts`는 마지막으로 관찰된(마감된) 봉의 시가 시각이다.
- 거래는 `entry < t <= exit`인 정산 t를 보유한다(`FundingIndex`와 같음).
- `tol_ms`(60초)는 기록된 정산 시각의 jitter다. 기록된 시각끼리, 또는 예정 시각과 비교할 때만 쓴다. 예정된 정산을 생략할 근거로 쓰지 않는다.

규칙:
- **끝:** `L`은 `end_ts` 이하의 마지막 정산, `iv`는 L에 선언된 주기다.
  - 다음 정산 예정 시각은 `D = L + iv`이다.
  - `D - tol_ms <= end_ts`이면 그 정산은 구간 안에서 예정된 것이다. 정확히 끝 시각에 예정된 경우와, L의 jitter 때문에 몇 ms 뒤에 예정된 경우를 포함한다.
  - 예정된 정산이 있으면 L 다음에 기록된 정산이 있어야 하고, 그 간격이 아래 규칙을 통과해야 한다. 아니면 `FUNDING_SETTLEMENT_GAP`(`TAIL_DUE_MISSING`)이다.
  - 그 정산의 주기가 선언돼 있지 않으면 `FUNDING_SCHEDULE_UNKNOWN`(`TAIL_SCHEDULE_UNKNOWN`)이다.
- **간격:** CSV 열의 의미("끝나는 주기"인지 "시작하는 주기"인지)는 문서화돼 있지 않다. 그래서 연속한 두 정산 a→b를 **두 해석으로 동시에** 판정한다. lo, hi는 a·b에 선언된 값 중 작은 값과 큰 값이다.
  - `step <= lo + tol`: 정상
  - `lo + tol < step <= hi + tol`: 한 해석에서만 정상(주기 변경의 의미 미확인) → `FUNDING_SCHEDULE_UNKNOWN`(`SCHEDULE_CHANGE_AMBIGUOUS`)
  - `step > hi + tol`: 두 해석 모두에서 누락 → `FUNDING_SETTLEMENT_GAP`
  - a 또는 b의 주기가 선언돼 있지 않으면 `FUNDING_SCHEDULE_UNKNOWN`이다.
- 거래별 검사(`settlement_check`, `FundingIndex`)는 바꾸지 않았다. 파이프라인·포트폴리오가 같이 쓰기 때문이다. 관찰 구간 검사는 같은 보유 정의와 허용 오차를 쓰고 `lo <= iv_b`이므로 거래별 검사보다 약하지 않다(임의 구간 속성 테스트로 확인).
- 남는 가정(`tail_assumption`에 기록): L 바로 뒤에 주기가 **짧아지면** 다음 정산이 도착할 때까지 알 수 없다. 그동안 L 뒤를 지나는 거래는 부과되지 않는다. 거래별 검사가 청산 시각 이후의 기록된 정산을 요구해서 그 거래는 제외되고, 판정은 BLOCKED_DATA가 된다.

### B. REST 정산 주기 (과거 소급 제거)

- `BinancePublicFeed.funding_checked()`는 어떤 정산 행에도 주기를 붙이지 않는다(`intervals = {}`).
- `fundingInfo` 응답은 `schedule_observation`(관찰 시각, 값, 목록 포함 여부, 원본 sha256, "현재 스냅샷이며 과거 정산에 유효하지 않음")으로 따로 기록한다.
- 정상적이고 완전한 응답도 `SCHEDULE_UNKNOWN`이다. 그래서 REST 피드로는 계획 등록이 거부된다. 워밍업 주기를 증명할 수 없기 때문이다(지시문 3-4의 선택지 3). 등록이 거부되면 아무것도 쓰지 않는다.
- 그 밖의 상태는 ADR 0073 그대로다.
  - 요청 실패 또는 오류 객체: SOURCE_ERROR. 스냅샷을 받았으면 보존한다.
  - 파싱 불가·범위 밖·중복·역순·다른 심볼 행: DATA_INVALID
  - 마지막 페이지가 가득 참: COVERAGE_INCOMPLETE
  - 빈 응답은 SOURCE_ERROR가 아니라 SCHEDULE_UNKNOWN이다.
- ADR 0073 테스트 f1·f2·f4·f8은 소급된 주기로 `VALID`를 기대했다. 이번에 `SCHEDULE_UNKNOWN`으로 바꿨다. 결함 자체를 기대값으로 삼던 테스트다.
- FileFeed `funding_interval_hours`는 세 가지를 구분한다.
  - 저장된 값: 정산별 정수 시간.
  - 이 코드의 가정: 두 인접 주기 중 하나.
  - 문서로 입증된 의미: 없음.
  - 그래서 A의 두 해석 규칙을 쓰고, 출처 문자열에 "period ending vs starting at calc_time undocumented"를 남긴다. fetch 결과의 `interval_basis`는 `DECLARED_PER_SETTLEMENT_MEANING_UNDOCUMENTED`다.

### C. 증거 파일 무결성 (paper-forward-integrity-v1)

1. **품질 로그 체인.**
   - `funding_quality_<SYM>.jsonl`의 각 기록은 다음을 담는다: `seq`(1부터 연속), `prev`, `hash`(sha256(prev + 정규 JSON)), `at`, `symbol`, `status`, `source_files`(sha256), `query`, `issues`, `policy`, `integrity`, `interval_basis`, `schedule_observation`.
   - funding log도 같은 형식(`seq`, `quality_seq` 포함)이다.
   - 읽기는 `read_seq_chain`이 한다. 다음은 `*_SEQUENCE` / `*_CHAIN` / `*_CORRUPT` 오류다: 순서 번호 불연속·중복·역순, 이전 해시, 본문 해시, 내부 손상 줄.
2. **state 앵커.**
   - state에 `quality_head`, `funding_head`({seq, hash})를 저장한다. `check_log_head`는 다음과 같이 판정한다.
     - head가 로그보다 앞섬: `*_TRUNCATED` (마지막 기록 삭제, 마지막 줄 잘림, 파일 삭제)
     - head 위치의 해시가 다름: `*_REWRITTEN` (체인을 다시 계산한 변조)
     - 로그가 head보다 앞섬: 정상 중단(로그 기록 뒤 state 저장 전 종료)이거나 state 단독 롤백이다. 이때는 로그가 기록이다. head·`last_funding_ts`·소비한 원본을 로그에서 다시 만들고, 로그 어디든 있는 DATA_INVALID는 OBSERVING state를 다시 BLOCKED_DATA로 만든다(`FUNDING_BLOCK_RESTORED`).
3. **funding log 내용 교차 검증.**
   - 시각은 정수이고, 워밍업 마지막 정산보다 뒤이며 엄격히 증가해야 한다(`FUNDING_LOG_ORDER`).
   - rate는 유한해야 한다(`FUNDING_LOG_INVALID`).
   - state `last_funding_ts`가 로그·워밍업 끝보다 뒤면 `STATE_AHEAD_OF_EVIDENCE`다.
   - head 위치 시각과 다르면 `STATE_FUNDING_TS_MISMATCH`다.
   - 품질 기록의 심볼과 상태가 맞아야 한다(`QUALITY_LOG_INVALID`).
4. **워밍업 SHA256.**
   - 등록 순서는 다음과 같다. 정규 JSON 본문(심볼·start_ts 포함) → 파일 바이트(정규 JSON + 개행) → sha256 → 계획 본문 `warmup_sha256` → 계획 ID → 파일 기록 → 다시 읽어 sha256 확인 → 계획 기록.
   - 해시 기준은 **파일 바이트**다. 내용이 같아도 형식을 바꾸면 불일치로 본다.
   - `_funding_series`, `_series`(`_evaluate`), 단계 시작 검증, 최종 게이트가 모두 `_warmup()`을 거친다.
   - 오류 종류는 `WARMUP_MISSING` / `WARMUP_TAMPERED` / `WARMUP_MISMATCH`다.
   - 계획 기록의 sha를 같이 고치면 계획 ID가 맞지 않아 `PLAN_CONTEXT_CHANGED`가 된다.
5. 무결성 오류는 모두 `EvidenceIntegrityError` → state `ERROR` → `PAPER_ERROR`(비최종)로 간다. 증거 파일은 고치거나 덮어쓰지 않는다. 전략 상태는 바뀌지 않는다.
6. **ADR 0073으로만 등록된 계획**(`funding_integrity` 없음)은 아무것도 수집·평가·결론하지 않는다. 판정은 `PAPER_BLOCKED_DATA`(`LEGACY_PLAN_INTEGRITY_UNVERIFIED`)다. 실제로 그런 계획은 없다(PAPER_TEST_READY 전략이 없음).

### D. PAPER_PASSED 최종 게이트

- `_verdict()`: 기존 ADR 0073 차단(펀딩 미확인, 거래 제외)에 더해, 최종 판정 전에 `_evidence(recover=False)`와 `_plan_drift()`를 다시 검사한다. 문제가 있으면 `PAPER_ERROR`(비최종)이다.
- `_conclude()`: `_conclusion_problems()`가 `_verdict()`와 **독립적으로** 다시 검사한다. 하나라도 실패하면 `ConclusionRefused`이다. 이때 단계는 `PAPER_BLOCKED_DATA`(`CONCLUSION_REFUSED`)가 되고, 평가 기록·상태 변경·research feedback이 생기지 않으며 결론도 나지 않는다. 검사 항목:
  - 정책·무결성 표시
  - `funding_ok`
  - 펀딩 커버리지 제외 거래 0건
  - 디스크에서 다시 계산한 관찰 구간 커버리지
  - 증거 파일과 state head 일치
  - 계획 drift
- 데이터 문제는 `PAPER_FAILED`가 되지 않는다. 통계 실패와 구분된다.

## 보장 범위와 한계

이번 작업이 보장하는 것(테스트 있음):
- 품질 로그·funding log·워밍업 파일 **한 개**의 변조(내용·해시·prev·seq), 중간 행 삭제·삽입·재정렬, 마지막 기록 삭제·잘림(이미 state에 반영된 것), 파일 삭제
- state **단독** 변조: 미래 시각, head 불일치, head가 로그보다 앞섬
- state **단독** 롤백: 더 오래된 OBSERVING state를 되돌려 놓아도 DATA_INVALID가 다시 적용된다
- 정상 중단(로그 기록 뒤 state 저장 전): 그대로 복구된다

보장하지 않는 것(외부 신뢰 앵커 없음):
- state와 로그를 **같은 과거 시점으로 함께** 되돌리는 경우
- 마지막 state 저장 **뒤에** 추가된(아직 state에 반영되지 않은) 로그 꼬리를 지우는 경우. 정상 중단과 구별할 수 없다.
- 해시가 키 없는 sha256이므로, 모든 파일(계획 기록 포함)을 일관되게 다시 쓰는 경우

이를 막으려면 외부(별도 저장소·서명) 앵커가 필요하다. 이번 범위 밖이다.

## 영향

- 정상 합성 데이터: ADR 0073 기준선(`tests/data/adr0073_forward_baseline_0725f86.json`)의 1h·4h·8h·0%·파일 경우가 모두 이벤트·진입·청산·펀딩·비용·순수익·판정까지 그대로다(`test_a7_results_equal_the_pre_fix_baseline`).
- 바뀌는 것:
  - REST 경로는 등록 불가(의도된 결과)
  - 주기가 길어지는 변경(예: 4h→8h)이 있는 CSV 구간은 `SCHEDULE_UNKNOWN`
- `evaluator.run_event_study()`, `settlement_check()`, `FundingIndex`, 파이프라인·IW1·runner·portfolio 호출자는 바뀌지 않았다. `coverage_check`의 호출자는 `paper_forward.py` 하나다. Paper Forward의 정책 계획 경로에는 `NOMINAL_8H_LEGACY`가 없다. 정책 이전 계획 분기에만 남아 있고, 그 판정은 항상 차단이다.
- promo-v5, DSR, Bootstrap, StepM, PBO, FWER, alpha spending, 후보 규칙·해시, DEV·Validation·Locked·IW1 규칙, `paper_forward_v1.json`, 비용 프로필, 진입·청산·체결 시뮬레이션은 바뀌지 않았다.

## 남은 문제

- REST 피드로 Paper Forward를 하려면 정산별 주기 이력에 대한 독립된 근거가 필요하다. 지금은 그런 근거가 없다.
- 아카이브 CSV 주기 열의 의미는 공식 문서로 입증되지 않았다. 두 해석 규칙으로 어느 쪽이든 누락은 잡지만, "두 인접 주기 중 하나"라는 가정 자체는 남는다.
- 실제 시장 데이터에서는 검증하지 않았다(금지 범위).
