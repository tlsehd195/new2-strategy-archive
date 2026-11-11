# 최종 보고서 — ADR 0074 Paper Forward 펀딩 커버리지 경계·과거 정산 주기·증거 로그 무결성 (2026-11-11)

요청: 동동, 2026-11-11. 모든 검증은 합성 데이터·임시 디렉터리·가짜 opener만 썼다. 실제 시장 데이터, 기존 아카이브, 잠금 TEST·IW0·IW1·sealed 구간, API 키, 주문·계좌 API는 쓰지 않았다. Binance 문서는 읽기 전용으로 조회만 했다.

## A. 작업 정보

| 항목 | 값 |
|---|---|
| 시작 main HEAD | `ead5e178e3a85dc72c0c43e41176683d2805e04a` (PR #74 ADR 0073 병합 커밋, 작업 시작 시 main HEAD와 동일, 추가 커밋 없음) |
| 작업 브랜치 | `claude/project-thread-09brl0` |
| 코드 커밋 | `bbe0541` (코드·테스트·ADR), 이 보고서 갱신 커밋은 그 뒤에 있다 |
| PR | #75 https://github.com/tlsehd195/new2-strategy-archive/pull/75 |
| ADR | 0074 (`docs/adr/0074-paper-forward-temporal-funding-integrity.md`), ADR 0073은 수정하지 않음 |
| 병합 | 메모리 정책(로컬 CI 3.11/3.13 통과 시 스레드가 직접 병합)에 따라 병합한다. 병합 커밋은 PR #75에 기록된다. 이 보고서는 병합 전에 작성했다. |

변경 파일:
- `run/discovery/funding_schedule.py`: `coverage_check` 재작성(funding-coverage-v2), `_step_status` 추가, `COVERAGE_VERSION`. `settlement_check`·`series_issues`는 바뀌지 않음.
- `run/discovery/paper_forward.py`:
  - `BinancePublicFeed.funding_checked` 소급 제거
  - `funding_fetch_result`·`_fetch_summary`에 `interval_basis`·`schedule_observation` 추가
  - 새 함수·클래스: `read_seq_chain` / `append_seq_chain` / `check_log_head` / `_warmup_bytes` / `_atomic_write_bytes` / `EvidenceIntegrityError` / `ConclusionRefused`
  - `register_plan`(워밍업 sha256), `_new_state`(head), `step`, `_collect_funding`, `_recover_funding` → `_evidence`, `_warmup`, `_funding_series`, `_series`, `_state_problems`, `_recover_from_logs`, `_verdict`, `_conclusion_problems` / `_conclude`
- `run/discovery/tests/test_paper_forward_funding_integrity_20261111.py` (신규, 36개 테스트)
- `run/discovery/tests/test_paper_forward_funding_20261111.py`: f1·f2·f4·f8의 REST 기대값 4곳. 소급된 주기로 VALID를 기대하던 부분이다.
- `docs/adr/0074-…md`, `docs/adr/README.md`, `run/discovery/README.md`, 이 보고서

## B. 수정 전 결함 (`ead5e17`에서 재현, `TestReproduction0074` 8개 모두 실패)

1. **관찰 끝 경계.**
   - 위치: `funding_schedule.coverage_check()`의 `if end_ts - times[k1] > iv + tol_ms`.
   - 조건: 마지막 정산 L, 선언 주기 iv, 관찰 끝 = L + iv, 그 정산 없음. 차이가 정확히 iv여서 `>`가 거짓이 된다.
   - 경로: Feed → `_collect_funding` → `_funding_window_status`(VALID) → `_evaluate` → `_verdict`.
   - 재현:
     - 함수 단위: 08:00/4h/끝 12:00 → `OK`
     - 전체 경로: 4h 계획, 75일 뒤 마지막 봉 = 정산 예정 시각, 그 정산 누락 → 펀딩 `VALID`, 제외 0건, **`PAPER_PASSED`**
2. **REST 과거 주기 소급.**
   - 위치: `BinancePublicFeed.funding_checked()`의 `ivs = {t: iv for t, _ in rows}`.
   - 조건: 현재 `fundingInfo` 4h, 과거 1h에서 하나 누락.
   - 재현: 모든 행에 4h → `VALID`, 커버리지 `OK`.
3. **품질 로그 비무결성.**
   - 위치: `_collect_funding()`이 품질 로그를 체인 없이 추가하고, `_recover_funding()`은 그 상태만 읽었다.
   - 재현: DATA_INVALID 기록을 VALID로 수정 → 오류 없음(BLOCKED만 유지). 마지막 기록 삭제 → 오류 없음.
4. **워밍업 비무결성.**
   - 위치: `_funding_series()` / `_series()`가 `warmup_<SYM>.json`을 검사 없이 읽었다.
   - 재현: 워밍업 펀딩률을 0.05로 바꿈 → 그대로 평가, **`PAPER_PASSED`**.
5. **state↔funding log.**
   - 위치: `_state_problems()`는 정수 여부만 확인했고, `_recover_funding()`은 로그가 앞설 때만 맞췄다.
   - 재현:
     - `last_funding_ts` 3일 미래 → 무결성 오류가 아니라 커버리지 공백 BLOCKED
     - 펀딩률 변경 후 체인 재계산 → **`PAPER_PASSED`**

재현 출력: 수정 전 코드에서 `python -m unittest -v discovery.tests.test_paper_forward_funding_integrity_20261111` → `Ran 8 tests … FAILED (failures=8)`.

## C. 수정 내용

자세한 규칙은 ADR 0074에 있다.

- **경계:**
  - 다음 예정 시각은 `D = L + iv`이고, `D - tol <= end`이면 예정된 정산이다.
  - 예정된 정산은 기록돼 있어야 한다. jitter로 끝 뒤에 찍힌 행도 찾는다.
  - 간격 a→b는 CSV 열의 두 해석(끝/시작)으로 동시에 판정한다. 정상 / 모호(SCHEDULE_UNKNOWN) / 누락(GAP).
- **REST:**
  - 어떤 행에도 주기를 붙이지 않는다.
  - `fundingInfo`는 시각·값·sha256과 함께 "현재 스냅샷"으로만 기록한다.
  - 완전한 응답도 SCHEDULE_UNKNOWN → 등록 거부(아무것도 쓰지 않음).
- **품질 로그·funding log:** seq + prev + hash 체인. state의 `quality_head` / `funding_head`로 교차 검증한다(잘림, 재작성, 앞섬, 뒤처짐 복구).
- **워밍업:** 파일 바이트 sha256을 계획 본문(계획 ID)에 넣고, 기록 후 다시 읽어 확인한다. 모든 읽기 경로에서 검증한다.
- **state↔log:** 시각 증가, 워밍업 이후, 유한 rate, `last_funding_ts`가 head 시각과 같아야 한다. 앞서면 STATE_AHEAD_OF_EVIDENCE다.
- **최종 게이트:**
  - `_verdict`가 증거와 drift를 재검사한다.
  - `_conclude`(`_conclusion_problems`)가 독립적으로 다음을 재검사한다: 정책·무결성, `funding_ok`, 제외 0건, 디스크 기준 커버리지, 증거, drift.
  - 하나라도 실패하면 `CONCLUSION_REFUSED` → PAPER_BLOCKED_DATA이고, 기록과 상태 변경이 없다.
- **ADR 0073 전용 계획:** LEGACY_PLAN_INTEGRITY_UNVERIFIED(수집·평가·결론 없음).

## D. 신규 테스트

`run/discovery/tests/test_paper_forward_funding_integrity_20261111.py`: 테스트 함수 36개(하위 subTest 포함).

| 클래스 | 내용 | 수정 전 / 후 |
|---|---|---|
| `TestReproduction0074` (8) | 결함 B-1~5 재현 | 8 실패 / 8 통과 |
| `TestBoundary` (6) | 1h·4h·8h 예정 시각 직전·일치·경과·존재; 허용 오차 경계(−tol, −tol−1, L jitter, 끝 뒤 jitter 행, tol 초과 지연); 예정 행 주기 없음; 주기 변경 직후 누락; 4h→8h 모호; 관찰 구간 ⊇ 거래 검사 속성 테스트 | 새 규칙 |
| `TestBoundaryForward` (1) | 8h·1h 전체 경로 경계 누락 → BLOCKED, 늦게 도착하면 PASSED | — |
| `TestRestTemporal` (4) | 소급 없음, 8→4·4→1·1→4 조회 사이 변경, fundingInfo 심볼 없음, 주기 성공·정산 실패, 빈 응답 vs 오류, 페이지 잘림·중복·역순·심볼 불일치·0%, 외부 URL 없음 | — |
| `TestRestForward` (1) | REST 등록 거부, 아무것도 쓰지 않음 | — |
| `TestQualityLogIntegrity` (5) | 정상 체인·state 앵커, 변조 13종(내용·중간·중간 삭제·마지막 삭제·재정렬·잘림·파일 삭제·hash·prev·seq 중복·seq 역전·재해시·손상 줄), DATA_INVALID 삭제, OBSERVING 롤백, head hash 불일치, 기록 후 state 저장 전 종료 | — |
| `TestWarmupIntegrity` (3) | sha256이 계획 ID에 포함, 변조 12종(펀딩률·시각·주기·행 추가·삭제·순서·봉·재포맷·잘림·JSON 손상·삭제·다른 심볼), 계획 기록 sha 동시 수정 | — |
| `TestStateFundingLog` (4) | 뒤처짐 복구, 미래 시각·head 시각 불일치·head 초과, 중간 삭제·rate 재해시·중복·역전·워밍업 겹침(state도 함께 수정), 체인 단절·로그 삭제, 재시작 후 DATA_INVALID 유지 | — |
| `TestFinalGate` (4) | 판정 뒤·결론 전 변조 → CONCLUSION_REFUSED(기록·상태·feedback 없음), `_conclude` 단독 차단(제외·drop·미확인·디스크 커버리지), 0074 이전 계획, 재시작 후에도 비통과 | — |

모든 무결성 오류 테스트는 다음을 함께 확인한다:
- 판정 PAPER_ERROR
- 전략 상태 PAPER_TESTING 유지
- `paper_forward` 평가 기록 없음
- 변조된 파일 바이트 불변(복구·덮어쓰기 없음)

## E. 기존 기능 영향

- **정상 데이터 이벤트·비용:** ADR 0073 기준선(`tests/data/adr0073_forward_baseline_0725f86.json`)의 1h·4h·8h 선언, 4h 파일, 0% 경우가 모두 같다. 비교 대상은 이벤트·진입·청산·펀딩·비용·순수익·판정이다(`test_a7_results_equal_the_pre_fix_baseline` 통과). `test_completion`의 forward 경로 테스트도 그대로 통과한다.
- **바뀐 결과:**
  - REST 피드: VALID → SCHEDULE_UNKNOWN, 등록 거부. 소급된 주기는 정상 기준선이 아니다.
  - CSV에서 주기가 길어지는 변경 지점: SCHEDULE_UNKNOWN
- **기존 상태·계획 기록:** 새 필드만 추가했다. ADR 0073 계획은 결론하지 않는다. 실제로 그런 계획은 없다.
- **evaluator 호출자:** `run_event_study`, `FundingIndex`, `settlement_check`는 바뀌지 않았다. 따라서 pipeline·IW1·runner·portfolio_multi·paper.py·cost_sensitivity에 영향이 없다. `coverage_check`의 호출자는 `paper_forward.py`뿐이다. Paper Forward 정책 계획 경로에는 `NOMINAL_8H_LEGACY`가 없다.
- **통계 기준:** 변경 없음(promo-v5, DSR, Bootstrap, StepM, PBO, FWER, alpha spending, `paper_forward_v1.json`, 비용 프로필, 진입·청산·체결).
- **잠금 데이터:** 읽거나 쓰지 않았다(ledger 검사 통과).

## F. CI 결과

로컬 CI는 `.github/workflows/research-ci.yml`과 같은 단계를 uv 가상환경에서 실행했다. Python 3.11.x와 3.13.x에 각각 numpy 2.2.6, pandas 2.2.3, ruff 0.16.8, shellcheck-py를 설치했고, TS 검사는 bun으로 했다. 숫자는 이번 실행의 집계다.

| 단계 | Python 3.11 | Python 3.13 |
|---|---|---|
| compileall run/discovery | 통과 | 통과 |
| 독립성 grep | 통과 | 통과 |
| ruff E9,F63,F7,F82 (게이팅) | All checks passed | All checks passed |
| ruff F 전체 (legacy 2개 제외) | 위반 0 | 위반 0 |
| bash -n + ShellCheck | 통과 | 통과 |
| 설정 스키마 | configs ok | configs ok |
| 의존성 검사 | stdlib-only: ok | stdlib-only: ok |
| Ledger (`.`, `state`) | CONSISTENT | CONSISTENT |
| discovery 단위 테스트 | **918 OK** (= ADR 0073의 882 + 신규 36) | **918 OK** |
| 루트 research 테스트 | 152 OK | 152 OK |
| Legacy 테스트 | OK | OK |
| run/*.py·run/audit/*.py compile + ruff | 통과 | 통과 |
| 연구 스크립트 인벤토리 | 차이 없음 | 차이 없음 |
| 소스 감사표 / 감사 출처표 | OK / OK | OK / OK |
| TypeScript transpile / package manifest | ok / ok | ok / ok |

중간에 실패한 테스트와 원인(모두 해결):
- ADR 0073 REST 테스트 4개(f1·f2·f4·f8): 소급된 주기로 VALID를 기대했다. 이 결함 자체를 기대값으로 삼던 것이라 기대값을 바꿨다.
- `test_legacy_plan_is_never_passed`: `_integrity()`가 정책 없는 계획에도 참이 되어 레거시 펀딩 로그를 seq 체인으로 읽었다. 코드를 고쳤다(`_integrity`가 `funding_policy`도 요구).
- 신규 테스트 2개의 시나리오 오류: 끝 경계 기대값, 롤백 시나리오의 역순 위치. 테스트를 고쳤다.

GitHub Actions: run #325 (push, id 42969744062)와 #326 (pull_request, id 42969792110)는 커밋 `bbe0541`에서 실행됐다. 두 run 모두 결과는 `failure`다. Job `test (3.11)` / `test (3.13)`은 3~4초 만에 끝났다. `runner_id` 0, runner 이름이 비어 있고, step 목록이 없다. 즉 runner가 배정되지 않아 **테스트 단계가 하나도 실행되지 않았다**. 로그는 확보할 수 없었다. 2026-11-09 이후 #287~#324와 같은 양상이다. 원인(계정 단위 결제·한도 추정)은 API로 확정할 수 없다. 코드 통과로 보고하지 않는다.

## G. 남은 문제

- **REST 피드 Paper Forward 불가:** 정산별 과거 주기 이력에 대한 공식 근거가 없다(`fundingInfo`는 현재 스냅샷, `fundingRate`에는 주기가 없다). 공지·FAQ·changelog는 확인하지 않았다.
- **아카이브 CSV `funding_interval_hours`의 의미가 공식 문서로 입증되지 않았다.** 두 해석을 동시에 판정해서 어느 쪽이든 누락은 잡는다. "두 인접 주기 중 하나"라는 가정은 남는다.
- **끝 가정:** 마지막 정산 직후 주기가 짧아지면 다음 정산이 올 때까지 끝 누락을 알 수 없다. 그동안 그 뒤를 지나는 거래는 부과되지 않고 제외되며, 판정은 BLOCKED다.
- **외부 앵커 없는 한계:**
  - state와 로그를 함께 과거로 되돌리는 경우
  - 아직 state에 반영되지 않은 로그 꼬리를 삭제하는 경우(정상 중단과 구별 불가)
  - 모든 파일과 계획 기록을 일관되게 다시 쓰는 경우(키 없는 sha256)
  - 이 세 경우는 감지하지 못한다.
- **실제 시장 데이터로는 검증하지 않았다**(금지 범위).
- **GitHub Actions:** run #325/#326은 runner가 배정되지 않아 step이 없다(F 참조). 원격 CI 통과는 확인되지 않았다.

## H. 최종 판정

| 항목 | 판정 |
|---|---|
| 정산 예정 시각 = 관찰 끝 누락 탐지 | COMPLETE |
| 허용 오차 경계 일관성 | COMPLETE |
| REST 현재 주기 과거 소급 제거 | COMPLETE |
| 주기 변경의 시간적 유효성 미확인 구간 차단 | COMPLETE (REST 전면 차단, CSV 모호 지점 SCHEDULE_UNKNOWN) |
| REST로 과거 주기 증명 | BLOCKED (공식 근거 없음) |
| CSV 주기 열 의미의 공식 입증 | NOT_VERIFIED |
| 품질 로그 무결성 | COMPLETE (단일 파일·state 단독 범위) |
| 워밍업 SHA256 | COMPLETE |
| state↔funding log 교차 검증 | COMPLETE |
| 재시작 후 오류 상태 보존 | COMPLETE |
| PAPER_PASSED 최종 게이트 | COMPLETE |
| 정상 합성 데이터 결과 유지 | COMPLETE |
| 다중 파일 동시 롤백 방어 | UNDETERMINED (외부 앵커 없음, 범위 밖) |
| 실제 시장 데이터 검증 | NOT_VERIFIED (금지 범위) |
| Python 3.11 로컬 CI | COMPLETE (918 + 152 + legacy 통과) |
| Python 3.13 로컬 CI | COMPLETE (918 + 152 + legacy 통과) |
| GitHub Actions | NOT_VERIFIED (runner 미배정, step 없음) |
