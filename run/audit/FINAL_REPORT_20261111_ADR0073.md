# 최종 보고서 — Paper Forward 펀딩 데이터 무결성 (ADR 0073)

작성: 2026-11-11. 검증은 모두 합성 데이터와 임시 디렉터리로 했다. 실제 시장 데이터, 기존 아카이브, 잠금 TEST·IW0·IW1·sealed 구간은 열지 않았다. 실계좌·API 키·주문도 쓰지 않았다. REST 피드는 mock opener로만 시험했다.

## A. 작업 정보

| 항목 | 값 |
|---|---|
| 시작 커밋 | `0725f86` (main, PR #73 병합 확인. 그 뒤 main 변경 없음, 작업 트리 깨끗함) |
| 코드 커밋 | `8df6f40` (이 보고서 커밋은 그 다음) |
| 작업 브랜치 | `claude/project-thread-oajva7` |
| PR | #74 |
| 병합 | 메모리 정책(로컬 CI 3.11/3.13 통과 후 스레드가 직접 병합)에 따라 병합. 병합 커밋은 스레드 답변에 적는다 |
| ADR | 0073 (`docs/adr/0073-paper-forward-funding-integrity.md`, README 등록). ADR 0072는 고치지 않음 |

수정·추가 파일과 이유:

| 파일 | 변경 | 이유 |
|---|---|---|
| `run/discovery/paper_forward.py` | `funding_fetch_result`, 피드 `funding_checked`·`funding_source_hashes`, REST 검증·페이지, 등록 순서 변경, `_collect_funding`·`_recover_funding`·`_funding_window_status`·`_state_problems`·`_state_unusable`, `_evaluate`(require_funding, 선언 주기), `_verdict`·`_conclude` 게이트, 해시 체인 펀딩 로그 | 결함 1–4의 본체 |
| `run/discovery/evaluator.py` | `FundingRequired`, `run_event_study(require_funding=False)`, None 의미를 문서화 | None→0bp와 명목 8h 규칙을 Paper Forward에서 금지 |
| `run/discovery/funding_schedule.py` | `coverage_check()` (기존 `settlement_check` 재사용 + 시작·끝 검사) | 관찰 구간 전체의 정산 완전성 |
| `run/discovery/data_quality.py` | `load_funding_files_checked()`, `funding_file_issues()` | 파일 내부·파일 간 검사를 runner와 공용화 |
| `run/discovery/runner.py` | `load_funding_window_checked()`가 공용 함수 호출(보고서 키 그대로, `file_status`만 추가) | 품질 정의가 둘로 갈라지지 않게 |
| `run/discovery/tests/test_paper_forward_funding_20261111.py` | 신규 51개 | 재현·회귀 |
| `run/discovery/tests/data/adr0073_forward_baseline_0725f86.json` | 수정 전 코드의 정상 데이터 이벤트 | 차등 검사 |
| `run/discovery/tests/test_completion.py` | `SynthFeed.funding_checked()` 추가(픽스처가 만드는 8h 주기를 선언). 기대값 변경 없음 | 주기를 선언하지 않은 피드는 이제 등록이 거부됨 |
| `run/discovery/STATISTICS_AND_STATES.md`, `run/discovery/README.md` | 펀딩 상태와 판정 규칙 문서화 | |
| `docs/adr/0073-…md`, `docs/adr/README.md` | ADR | |

## B. 결함 분석 (수정 전 `0725f86`)

- **정확한 위치.** `paper_forward.py:130-139` `FileFeed.funding()`은 파싱 오류·잘린 행·원본 순서 오류가 있는 파일을 `continue`로 건너뛰었다. `:179` REST `funding()`은 응답을 검증하지 않았다. `:297` 수집 시작점이 `start - 8h`로 하드코딩되어 있었다. `:371` 단계 루프가 행을 그대로 로그에 추가했다. `:461,464` `funding or None`, `:463-465` `funding_intervals`를 넘기지 않음, `:466` 제외된 거래를 `excluded`에만 셌다. `:550` `_verdict()`는 펀딩을 보지 않았다. `evaluator.py:238-240,289-297`은 `funding is None`이면 `fp = 0.0`이었다.
- **데이터가 빠지는 과정.** 9월 파일이 손상되면 FileFeed가 그 파일을 건너뛰고 8월·10월 행만 돌려준다. 그 행이 펀딩 로그에 쌓인다. 평가기의 명목 8h 규칙이 빈 9월을 "간격 누락"으로 보면, 그 구간에 걸린 거래만 조용히 제외된다. 나머지 거래는 그대로 통계에 들어간다. 4h 시리즈에서 정산 하나가 빠진 경우는 간격이 8h라 명목 규칙에서는 누락으로도 보이지 않는다. 그 정산만큼 적게 부과된다.
- **None → 0.** 펀딩 행이 하나도 없으면 `sorted(set([]))`가 `[]`이고, `[] or None`이 `None`이다. 평가기에서는 `fidx=None`이 되고 각 거래에 `fp = 0.0`이 부과된다. `funding_schedule`은 "NONE"으로만 표시된다.
- **판정이 막지 못한 이유.** `_verdict()`는 거래 수·기간·수익·p·스트레스·낙폭·봉 누락·청산·지연만 검사했다. 펀딩 상태, 펀딩 때문에 제외된 거래 수, 정산 주기 출처는 판정 입력이 아니었다. 그래서 재현 4건이 모두 `PAPER_PASSED`였다(ADR 0073 표).

## C. 수정 결과

- **Feed 품질 처리.** 두 피드에 `funding_checked()`를 두었다. 반환 항목은 상태 7종(VALID, VALID_ZERO_RATE, DATA_MISSING, DATA_INVALID, COVERAGE_INCOMPLETE, SCHEDULE_UNKNOWN, SOURCE_ERROR), 원본 순서 행, 정산별 선언 주기, 파일·응답별 sha256·상태·오류, 주기 출처, 조회 범위다. 관련 파일 중 하나라도 손상되면 전체 응답이 DATA_INVALID다. 목록 인터페이스는 이때 예외를 낸다.
- **CSV 원본 순서.** ADR 0072의 정렬 전 검사를 공용 `load_funding_files_checked()`로 옮겼다. FileFeed와 runner가 이 함수 하나를 같이 쓴다. 파일 간 오류는 두 파일 모두에 표시한다.
- **월별 파일 완전성.** 파일이 없는 월은 `missing_files`로 기록한다. 정산이 필요했는지는 선언 주기로 계산한 커버리지가 판단한다. 파일이 없다는 것만으로 손상으로도 정상으로도 판정하지 않는다.
- **정산 주기 전달.** CSV의 `funding_interval_hours`, 또는 REST `/fapi/v1/fundingInfo`를 쓴다. warm-up 파일(`funding_intervals`)과 펀딩 로그의 각 행(`interval_ms`)에 주기를 저장하고, `_evaluate`가 `funding_intervals`로 넘긴다. 주기가 없으면 SCHEDULE_UNKNOWN이며 8h를 가정하지 않는다.
- **FundingIndex 연결.** `require_funding=True`와 선언 주기를 넘기므로 `FundingIndex`가 거래마다 `settlement_check`를 쓴다. 결과는 `funding_schedule = "DECLARED"`이며 테스트로 확인했다.
- **run_event_study 전달.** 관찰 구간 커버리지가 VALID/VALID_ZERO_RATE일 때만 호출한다. 그때 펀딩은 항상 비어 있지 않은 리스트다. None을 넘기면 `FundingRequired`가 난다.
- **_evaluate.** 펀딩 때문에 제외된 거래 수와 사유를 결과에 넣는다. 제외가 하나라도 있으면 그 단계는 차단된다.
- **_verdict.** 레거시 계획과 펀딩 미확인 단계는 `PAPER_BLOCKED_DATA`다. non-final이고 상태를 바꾸지 않는다. `_conclude`도 같은 조건을 다시 확인해 거부한다. 평가 기록에 `funding_evidence`를 남긴다.
- **상태 저장·복구.** 품질 로그를 상태보다 먼저 쓴다. 펀딩 로그는 해시 체인이다. 재시작하면 로그에서 위치와 원본을 다시 만든다. 이전에 DATA_INVALID 기록이 있으면 다시 차단한다. 상태 파일이 손상되면 PAPER_ERROR를 내고 파일은 보존한다. 원본 sha256이 바뀌거나 파일이 삭제되면 영구 차단한다.

## D. 테스트 결과

| 항목 | 결과 |
|---|---|
| 신규 테스트 | 51개 (재현 4, FileFeed 14, 커버리지 7, 평가기 4, REST mock 7, 전체 경로 10, 재시작 5) |
| 수정 전 코드에서 재현 | 4/4 실패 (모두 `PAPER_PASSED`가 나옴) |
| 기존 단위 테스트 | 831개 (ADR 0072 직후 기준), 전부 통과 |
| Python 3.11 (로컬) | research-ci.yml 전 단계 통과. 단위 882/882, 루트 152/152, 레거시 1/1 |
| Python 3.13 (로컬) | 전 단계 통과. 단위 882/882, 루트 152/152, 레거시 1/1 |
| 로컬 CI 기타 | compileall, ruff(게이팅·F·run/ 전체), ShellCheck, 설정 스키마, stdlib 의존성, ledger 2개, 스크립트 목록 diff, 소스 감사표, 감사 출처표, TS 변환, manifest. 모두 통과 |
| GitHub Actions | **통과하지 않음 / 실행되지 않음.** 실행 #320(push)·#321(PR #74)이 3초 만에 failure로 끝났다. 두 job(3.11, 3.13) 모두 steps가 없다. 2026-11-09 이후와 같은 계정 수준 문제(runner 미배정)이며 코드 실패가 아니다. 원격 CI는 통과로 기록하지 않는다 |
| 차등 테스트 | 수정 전 코드로 캡처한 정상 데이터 4가지(1h 선언, 4h 선언, 8h 파일 피드, 8h 0%)를 비교했다. 각 75건의 이벤트가 모든 필드에서 같았고 판정도 같았다. 평가기 단계 1h·4h·8h(48봉 보유, 0이 아닌 펀딩)에서도 명목 경로와 선언 경로의 이벤트가 같았다 |

## E. 기존 기능 영향

- **정상 펀딩 데이터:** 이벤트·비용·펀딩·순수익·판정이 바뀌지 않았다(차등 테스트). 바뀐 것은 기록뿐이다. 품질 로그·해시 체인·평가 기록의 `funding_evidence`가 추가되었다.
- **손상·불완전 펀딩:** `PAPER_PASSED`였던 경우가 이제 `PAPER_BLOCKED_DATA`(non-final, 상태 불변)이거나 등록 거부다.
- **기존 PAPER 계획 기록:** 삭제하거나 다시 쓰지 않는다. `plans.jsonl`이 그대로인 것을 테스트로 확인했다. ADR 0073 이전 계획은 평가는 되지만 결론은 나지 않는다. 저장소에 forward 계획이나 `PAPER_PASSED` 기록은 없다.
- **실험 컨텍스트 해시:** 바뀐다. 변경 파일이 `EVO_CODE_FILES`에 포함되어 `code_hash`가 바뀌므로 `context_id`가 새로 생긴다. 기존 설계대로이며, 이전 컨텍스트의 상태는 보존되고 재검증 대기로 보인다.
- **다른 연구 기능:** DEV·검증·locked·IW1·cost_sensitivity의 `run_event_study` 호출은 기본값 그대로다. runner의 펀딩 보고서 키도 같다. 단위 831개와 루트 152개가 모두 통과했다. 통계 기준, 후보 규칙, 잠금 규칙은 바꾸지 않았다.

## F. 남은 위험

- **실데이터 미확인:** 실제 Binance 아카이브와 REST 응답으로 확인하지 않았다(금지 범위). 합성 데이터 검증뿐이다.
- **실계좌 체결 증거 부족:** 그대로다. Forward paper 등록에는 VERIFIED 비용이 필요하고, 실계좌 체결은 아직 0건이다(2026-11-02 OKX는 데모 계정). 그래서 지금 실제로 등록할 수 있는 계획은 없다.
- **정산 주기 출처의 한계:** CSV `funding_interval_hours`의 의미(그 정산으로 끝나는 구간)는 ADR 0071의 해석이며 공식 문서로 확인하지 못했다. REST의 fundingInfo는 조회 시점의 값이다. 이 엔드포인트는 주기를 조정한 심볼만 나열하므로, 대부분의 8h 심볼은 REST 피드에서 SCHEDULE_UNKNOWN으로 평가되지 않는다(안전 쪽이지만 실용성이 낮다).
- **NOMINAL_8H_LEGACY 잔존:** IW1(`independent_window`), runner v1, `cost_sensitivity`, `paper.py` 재생 경로에 남아 있다. Paper Forward 경로는 분리되었다.
- **원격 CI 제한:** GitHub Actions는 계정 수준 문제로 job이 시작되지 않는다. 사용자가 billing·spending limit을 확인해야 한다.
- **추가로 발견한 사항:** 선언 주기보다 짧은 간격(주기 단축)은 빈 곳으로 탐지하지 않는다(기존 규칙). 아카이브 파일이 늦게 올라오면 끝 정산 미기록으로 차단이 이어질 수 있다(의도한 안전 쪽 동작). JSONL의 잘린 마지막 줄 다음에 추가하는 경우는 기존 `append_jsonl` 동작을 따르며 이번 범위 밖이다.

## G. 최종 판정

| 항목 | 판정 |
|---|---|
| Feed 펀딩 품질 결과 구조(FileFeed) | COMPLETE |
| REST 피드 검증(mock) | COMPLETE (mock 기준). 실제 API 동작은 NOT_VERIFIED |
| CSV 원본 순서·파일 간 검사 공용화 | COMPLETE |
| 월별 파일 누락과 정산 누락 구분 | COMPLETE |
| 선언 정산 주기 전달 / 8h 금지 (Paper Forward 경로) | COMPLETE |
| 관찰 구간 정산 완전성 | COMPLETE |
| None·명목 8h 거부 (`require_funding`) | COMPLETE |
| `_evaluate`/`_verdict`/`_conclude` 차단 | COMPLETE |
| 상태 저장·복구·해시 변경 감지 | COMPLETE |
| 정상 데이터 결과 불변 | COMPLETE (합성 차등) |
| 로컬 CI 3.11/3.13 | COMPLETE |
| GitHub Actions 원격 CI | BLOCKED (계정 수준 runner 미배정) |
| 실제 시장 데이터 검증 | NOT_VERIFIED (금지 범위) |
| 아카이브 주기 컬럼 의미 | UNDETERMINED |
| 다른 경로의 NOMINAL_8H_LEGACY | PARTIAL (Paper Forward만 분리) |

## 질문 답변

1. **펀딩 파일 하나라도 손상되면 PAPER 평가가 차단되는가?** 예. 조회 구간과 겹치는 파일 중 하나라도 손상되면 응답 전체가 DATA_INVALID이고 계획은 BLOCKED_DATA로 영구 차단된다(`test_b1`, `test_b7`, `test_d2`, 재현 3·4). 등록 시점이면 등록이 거부된다. 조회 구간 밖의 오래된 파일은 읽지 않는다(`test_corrupt_file_outside_the_queried_period_is_not_read`).
2. **펀딩 자료가 없으면 비용 0으로 처리되지 않는가?** 예. warm-up 펀딩이 없으면 등록이 거부된다. 관찰 중 자료가 없으면 평가기를 호출하지 않는다. 평가기는 `require_funding=True`에서 None을 거부하고, 빈 리스트이면 모든 거래를 0 부과 없이 제외한다(재현 1, `test_d3`, `test_empty_series_…`).
3. **실제 펀딩률 0과 데이터 누락이 구분되는가?** 예. 0% 정산은 `VALID_ZERO_RATE`이고 `paid_bp = 0.0`, 상태 OK다. 정산이 없으면 FUNDING_SETTLEMENT_GAP / COVERAGE_INCOMPLETE이고 `paid_bp = None`이다(`test_c7`, `test_a4`, 차등 "zero" 경우가 PASSED).
4. **펀딩 주기를 알 수 없으면 PAPER 검증이 통과하지 못하는가?** 예. 목록만 주는 피드는 등록이 거부된다. 관찰 중 주기를 알 수 없는 정산이 생기면 PAPER_BLOCKED_DATA다. REST에서 fundingInfo에 심볼이 없으면 SCHEDULE_UNKNOWN이다(`test_d4`, `test_f7`, `test_c4`).
5. **필요한 정산 자료가 불완전하면 PAPER_PASSED가 불가능한가?** 예. 시작·중간·끝 누락, API 페이지 잘림, 펀딩 때문에 제외된 거래가 있으면 모두 PAPER_BLOCKED_DATA다. `_conclude`도 별도로 거부한다(재현 2, `test_d5`, `test_c1_c2_c3`, `test_conclude_refuses_…`).
6. **재시작 이후에도 무결성 오류가 유지되는가?** 예. 품질 로그가 기준이다. 상태 파일을 이전 OBSERVING 사본으로 되돌려도 FUNDING_BLOCK_RESTORED로 다시 차단된다. 파일을 고쳐도 풀리지 않는다. 손상된 상태 파일은 PAPER_ERROR이고 덮어쓰지 않는다(`test_e2_e6`, `test_d2`, `test_e4`).
7. **기존 정상 데이터의 이벤트와 수익률이 불필요하게 바뀌지 않았는가?** 예, 바뀌지 않았다. 수정 전 코드에서 캡처한 기준선과 이벤트 75건 × 4가지가 모든 필드에서 같다(`test_a7_results_equal_the_pre_fix_baseline`, 평가기 단계 차등).
8. **기존 통계 기준과 잠금 데이터가 보호되었는가?** 예. promo-v5·DSR·CI·StepM·PBO·FWER·alpha spending·후보 규칙·DEV/검증 판정·`paper_forward_v1.json` 기준값을 바꾸지 않았다. 잠금·IW·sealed 데이터와 기존 아카이브는 열지 않았다.
9. **GitHub Actions와 로컬 테스트가 각각 실제로 통과했는가?** 로컬은 통과했다(3.11·3.13 전 단계, 단위 882/882). GitHub Actions는 통과하지 않았다. #320·#321이 job 시작 전 3초 만에 실패했다(steps 없음, 계정 수준). 실제 시장 데이터 검증은 하지 않았다.
10. **아직 남아 있는 가장 중요한 문제는?** 실계좌 체결 증거가 0건이라 비용이 VERIFIED가 아니다. 그래서 Paper Forward 자체를 실제로 돌릴 수 없다. 그다음은 GitHub Actions 계정 문제(원격 CI 불가)와, 대부분의 8h 심볼을 REST 피드에서 평가할 수 없다는 점이다. 후자는 fundingInfo 밖 심볼의 주기를 확인할 출처가 따로 필요하다.
