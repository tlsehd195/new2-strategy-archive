# 최종 보고서 — ADR 0071 이후 추가 결함 수정 (ADR 0072)

2026-11-11, 요청: 동동. 모든 재현과 검증은 **합성 데이터와 임시 파일**로 했다. 실제 시장 데이터는 읽지 않았다.

## A. 작업 범위

| 항목 | 내용 |
|---|---|
| 시작 커밋 | `89fcc6d` (main, PR #72 병합, ADR 0071). 그 뒤 main 추가 커밋 없음 |
| 종료 커밋 | 이 PR의 병합 커밋 (PR 본문·스레드 보고에 기록) |
| 작업 브랜치 | `claude/project-thread-091ziv` |
| PR | #73 |
| 수정 파일 | `run/discovery/data_quality.py`, `runner.py`, `evaluator.py`, `pipeline.py`, `portfolio_multi.py`, `data_fetch.py`, `data_manifest.py`, `paper_forward.py`, `config/portfolio_validation_v2.json`, `tests/test_portfolio_core_defects_20261111.py`(기대값 2곳), `docs/adr/README.md` |
| 추가 파일 | `run/discovery/tests/test_followup_defects_20261111.py`(41개), `docs/adr/0072-…md`, 이 보고서 |
| 추가 ADR | 0072 (ADR 0071은 수정하지 않음) |
| 버전 | `portfolio-multi-v2` → **`portfolio-multi-v3`**. `portfolio-validation-v2`는 버전 문자열 유지, 엔벌로프 키 추가(`revisions` 기록, 파일 해시 변경). `promo-v5`·`portfolio-validation-v1`·`funding-schedule-v1`은 불변. 코드 해시 대상 파일이 바뀌어 실험 컨텍스트 ID가 새로 생긴다 |

## B. 문제별 수정 결과

| 문제 | 수정 전 | 수정 후 | 재현 테스트 | 상태 |
|---|---|---|---|---|
| 원본 펀딩 CSV 역순 탐지 | `data_quality.load_funding_csv_checked()`가 정렬부터 함. `runner.load_funding_window_checked()`도 합친 뒤 정렬. 00→16→08 CSV는 `ok=True`. 2월 파일이 1월의 빠진 정산을 메워도 정상 | 정렬 전 원본 행 순서 검사(`source_order_errors`: 파일·행·이전/현재 ts·종류·영향). 월별 파일 간 `CROSS_FILE_*`도 검사. `funding_quality()` → `SOURCE_ORDER_INVALID`, `status=FUNDING_DATA_INVALID`. `checked_funding_quality()` → `RunnerDataProvider.symbol()`에서 funding=None → DEV `BLOCKED_DATA`, 검증·포트폴리오에 가지 않음 | `TestFundingSourceOrderFile`, `TestFundingSourceOrderWindow`, `TestFundingSourceOrderProvider` (17개) | FIXED |
| 마지막 창 끝 신호 누락 | `evaluator.run_event_study()`가 처음 잘린 신호(`exit_i >= n`, `exit_rule` None)에서 `break`. 40봉·h3: signals 37, truncated 1 | `continue`로 끝까지 분류. 잘린 신호는 `last_exit_index`를 움직이지 않음. 결과: 38 / truncated 2 | `TestWindowEndSignals` (8개) | FIXED |
| 엔벌로프 낙폭 판정 미연결 | `portfolio_multi.judge()`·v2 설정이 종가·봉 내부 최악만 검사. 종가 0 / 최악 0.40 / 엔벌로프 0.538 → PASS | `JUDGE_KEYS_V2`에 `max_drawdown_equity_peak_to_trough_lt` 추가. `judge()`가 세 낙폭을 같은 규칙으로 검사(상태 비정상·NaN·Inf·None → UNDETERMINED, ≥0.5 → FAIL). v2 base·cross 3x·partial-latency(진단)에 0.5. v2 gating 시나리오에 세 키 중 하나라도 없으면 `pipeline.load_portfolio_config()`가 거부 | `TestEnvelopeDrawdownGate` (8개) | FIXED |
| Cross stale mark | `simulate_multi()` Cross 시가·봉 내부 계좌 청산이 봉 없는 심볼을 과거 종가로 평가하고도 청산을 확정. 같은 시각 신규 진입도 허용 | 열린 포지션 심볼 중 하나라도 봉이 없으면 계좌 청산 검사 보류(장부 변화 없음, `cross_account_valuation.events`에 stale 가격 기준 판정 기록), 신규 진입 `account_valuation_incomplete`(data_missing) 거절, 상태 `INCOMPLETE` → `judge()` UNDETERMINED_PORTFOLIO. Isolated는 그대로 | `TestCrossStaleMarks` (9개) | FIXED |

수정 전 코드에서 새 테스트 41개 결과: 실패 16, 오류 20, 통과 5. 통과한 5개는 수정 전후가 같아야 하는 회귀 기준이다. 수정 뒤에는 41개 모두 통과했다.

## C. 테스트 결과

| 항목 | 값 |
|---|---|
| 신규 테스트 | 41 (`test_followup_defects_20261111.py`) |
| 기존 단위 테스트 | 790 (수정 전 기준선: 3.11·3.13 모두 790 OK) |
| 전체 단위 테스트 | 831 (기존 790 + 신규 41) |
| 루트 연구 테스트 | 152 |
| 실패 수 | 0 |
| Python 3.11 | 로컬 CI 전 단계 통과 (단위 831 OK, 루트 152 OK, 레거시 1 OK) |
| Python 3.13 | 로컬 CI 전 단계 통과 (단위 831 OK, 루트 152 OK, 레거시 1 OK) |
| 로컬 CI | research-ci.yml의 모든 단계를 로컬에서 재현: compileall, 독립성 grep, ruff E9/F63/F7/F82와 F, bash -n + ShellCheck, 설정 스키마, stdlib-only 검사, ledger, 단위·루트·funding_cost 레거시 테스트, run/*.py·run/audit 컴파일·ruff, 인벤토리 최신성, 소스 감사표, 감사 출처표, bun 1.4.4 TS 트랜스파일, 패키지 매니페스트 |
| 차등 검사 | 합성 400건, 이전(`89fcc6d`)·새 evaluator 비교. 이벤트·순수익·거절 수·거절률이 400건 모두 동일. `signals_total`은 354건 증가, truncated 합계 +2065 |
| GitHub Actions | 실패, 확인 불가. 아래 참조 |

GitHub Actions: #313(PR #72, `b586876`)과 #314(main 병합, `89fcc6d`) 모두 `failure`다. 두 job(3.11, 3.13)은 `runner_id 0`, `runner_name` 빈 값, 과금 0 ms, 실행 4초, 로그 404, check run 출력이 비어 있다. runner가 배정되지 않았고 어떤 단계도 실행되지 않았다. 11-09 이후 모든 실행이 같은 형태다. 결제나 사용량 한도가 원인일 가능성이 높지만 API로는 정확한 원인을 확인할 수 없다(**확인 불가**). 이 PR의 실행 #315(push)·#316(pull_request, `7cdd8ca`)도 같은 형태로 실패했다. 2 job 모두 4초에 끝났고, 과금 0 ms, 단계 없음이었다. 로컬 CI 통과가 Actions 통과를 뜻하지는 않는다.

## D. 기존 기능 영향

- DEV 결과: 이벤트·수익률·비용·펀딩·거절은 같다(차등 검사). 진단의 `signals_total`·`truncated_end_of_window`는 커진다. 원본 순서 오류가 있는 펀딩 파일이 있는 심볼은 이제 BLOCKED_DATA다(실제 아카이브에 그런 파일이 있는지는 확인하지 않음).
- validation 결과: DEV와 같은 evaluator를 쓰므로 영향도 같다.
- 포트폴리오 결과: 판정이 엄격해지기만 한다(엔벌로프 FAIL/UNDETERMINED, Cross stale → UNDETERMINED). 모든 심볼에 봉이 있는 기존 Cross·Isolated 테스트 결과는 그대로다. 실데이터 포트폴리오 기록은 0건(VALIDATION_SURVIVOR 0)이다.
- 호환성: v1 설정은 그대로 읽힌다. v2 파일은 해시가 바뀌었다. `simulate_multi` 시그니처는 바뀌지 않았다.
- 기록 분리: 코드 해시와 설정 해시가 바뀌므로 기존 기록은 새 컨텍스트와 섞이지 않는다. 기존 결과 파일은 지우거나 덮어쓰지 않았다.
- 기대값을 바꾼 기존 테스트 2곳: truncated 1→2(결함이었던 값), model_version v2→v3. 이유는 ADR 0072에 있다.

## E. 남은 위험

- 실제 시장 데이터 검증: 안 함. 실제 펀딩 아카이브에 원본 순서 오류가 있는지 모른다.
- 실계좌 체결 증거: 0건(데모 계좌 체결은 증거가 아님). 비용은 VERIFIED가 아니다.
- 독립적인 미래 검증 구간: 없음. 잠금 TEST·IW0·IW1·sealed 데이터는 열지 않았다.
- 파이프라인 밖 레거시 호출자: `liquid_search_dev.py`·`strong_search_dev.py`의 자체 numpy 이벤트 루프에는 아직 `break`가 있다(신호 회계를 내지 않는 DEV 도구라 집계만 다름). runner v1·IW1·paper_forward의 펀딩 정산은 여전히 NOMINAL_8H_LEGACY 규칙이다(ADR 0071).
- 거래소별 청산 규칙: 등급별 유지증거금·보험기금·ADL·청산 수수료는 NOT_MODELLED다.
- 모델 한계: 엔벌로프와 Cross 동시 극값은 OHLC 가정이다. 실제 경로는 더 세밀한 데이터가 있어야 알 수 있다. Cross 보류는 판정 불가로 남길 뿐 실제 청산 여부를 복원하지 않는다.
- GitHub Actions가 계정 수준에서 실패한다. 원격 CI 증거가 없다.

## F. 최종 평가

| 항목 | 등급 | 근거 |
|---|---|---|
| 원본 펀딩 데이터 무결성 | COMPLETE (합성) / NOT_VERIFIED (실데이터) | 파일 내부·파일 간 순서·중복, 파싱 실패, 잘린 행, 간격 누락을 모두 보고 |
| 펀딩 정산 완전성 | PARTIAL | 파이프라인은 선언 주기 기준(ADR 0071). 레거시 호출자는 8h 규칙 |
| 신호 집계 정확성 | COMPLETE | 창 끝까지 전수 분류, 차등 검사로 채택 불변 확인 |
| 신호 회계 | COMPLETE | 새 거절 사유도 SKIP_CLASS에 있음, 항등식 검사·보고(보정 없음) |
| 포트폴리오 자본 관리 | PARTIAL | 합성 테스트만. 실데이터 실행 0건 |
| Cross 청산 정확성 | PARTIAL | stale 가격으로는 확정하지 않음. 거래소 규칙은 모델 가정 |
| 종가 낙폭 | COMPLETE | gating |
| 봉 내부 최악 낙폭 | COMPLETE | gating |
| 엔벌로프 낙폭 | COMPLETE | 이번에 gating 연결 |
| 최종 포트폴리오 판정 | COMPLETE (규칙) / NOT_VERIFIED (실데이터) | 계산 불가 → UNDETERMINED, PASS 불가 |
| 데이터 누수 방지 | COMPLETE | prefix 원칙 유지, 잠금 창 미개방 |
| 재현성 | COMPLETE | 결정적 합성 테스트, 버전·해시 기록 |
| 상태 복구 | COMPLETE | 재시작 후 진단 유지(기존 TestPipelineWiring) |
| 자동화 | BLOCKED | GitHub Actions가 계정 수준에서 실패. 로컬 CI만 통과 |
| 전체 코드 감사 | PARTIAL | 이번 범위 4개 결함만 재감사. 그 밖의 파일은 ADR 0070 감사표 기준 |

## G. 최종 질문

1. **원본 CSV 역순이 실제 파이프라인에서 탐지되는가?** 예. `load_funding_csv_checked`(정렬 전) → `load_funding_window_checked`(파일 간) → `checked_funding_quality` → `RunnerDataProvider.symbol()`(funding=None) → `dev_evaluate` BLOCKED_DATA 경로를 임시 CSV로 끝까지 테스트했다. 실제 아카이브 파일로는 돌리지 않았다.
2. **모든 발생 신호가 정확히 한 번씩 분류되는가?** 예, 진입 봉이 있는 모든 결정 봉에 대해 그렇다. 여섯 칸 합이 `signals_total`이며 테스트와 차등 검사 400건에서 확인했다.
3. **세 가지 낙폭 지표가 모두 최종 포트폴리오 위험 판정에 반영되는가?** 예. base·cross 3x(gating)에서 종가·봉 내부 최악·엔벌로프 모두 < 0.5여야 PASS다. 하나라도 계산 불가이면 UNDETERMINED다.
4. **Cross 계좌가 오래된 가격으로 잘못 청산되지 않는가?** 예. stale mark가 있는 시각에는 청산을 확정하지 않고 진입도 막으며, 결과는 UNDETERMINED가 된다.
5. **기존 백테스트 및 통계 검증 기준이 변경되었는가?** 통계 기준(promo-v5, DSR, CI, StepM, PBO, FWER, alpha spending)은 바뀌지 않았다. 포트폴리오 위험 판정은 엄격해지기만 했다(엔벌로프 0.5, Cross stale → UNDETERMINED). 펀딩 품질 판정도 엄격해졌다.
6. **실제 시장 데이터로 검증한 부분은 무엇인가?** 없다. 전부 합성 데이터와 임시 파일이다.
7. **아직 해결되지 않은 가장 중요한 문제는?** 실계좌 체결 증거(비용 VERIFIED)와 독립적인 미래 검증 구간이 없다. 그래서 어떤 전략도 실제 성과 주장을 할 수 없다. 코드 쪽에서는 GitHub Actions가 계정 수준에서 막혀 원격 CI가 없다는 점과, 레거시 호출자의 8h 펀딩 규칙·자체 이벤트 루프가 남아 있다.
