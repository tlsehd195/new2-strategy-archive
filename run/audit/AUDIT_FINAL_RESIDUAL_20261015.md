# NEW2 자율 전략 탐색 엔진 — 최종 잔여 결함 해결 및 완료 검증 보고서

작성일: 2026-10-15 · 대상: `tlsehd195/new2-strategy-archive` (`run/discovery/`) · 증거: `run/audit/evidence_final_20261015/`

**최종 판정: `PARTIAL`** — 지시문의 코드 수정 항목(run_cycle.sh 종료 코드 조합, FDR 완료 조건 분리, 과거 PASS 처리, 결정 충돌 감지, 환경 ID 보강)은 모두 실제 코드로 구현했고 신규 테스트 34개를 포함한 전체 202개 테스트가 2회 연속 통과했다. 그러나 실데이터 사이클은 `BLOCKED_DATA_MISSING`이고, 비용 모델·BH의 PRDS 가정은 검증되지 않았으며, 본 프로젝트(NEW2)에는 통합·배포하지 않았다. 따라서 `COMPLETE`로 표시하지 않는다.

## A. 저장소 상태

| 항목 | 값 |
|---|---|
| 저장소 | `tlsehd195/new2-strategy-archive` (NEW2 본 저장소는 비용 모델 공급원으로 읽기만 함, 수정 없음) |
| 작업 브랜치 | `claude/final-residual-fixes` (`origin/main` = `66646cdda5216ce385c459d02feff2c66673834e`에서 분기) |
| 시작 커밋 | `66646cdda5216ce385c459d02feff2c66673834e` (직전 작업 PR #1의 머지 커밋, 지시문의 기준 커밋과 동일) |
| 최종 커밋 | 이 보고서를 포함한 브랜치 HEAD (PR에서 SHA 확인) |
| 시작 시 작업 트리 | clean (사용자 미커밋 변경 없음) |
| 상태 파일 보호 | 테스트는 임시 디렉터리에서만 실행. `git status`에서 `state/`·원장·잠금 TEST 파일 변경 없음 확인 |
| PR | 이 작업의 draft PR (이전 PR #1은 **병합 완료**, 열려 있지 않음) |
| NEW2 본 프로젝트 통합 | **미통합** (이 저장소 안에서만 수정) |

## B. 잔여 결함별 결과

### B-1. run_cycle.sh 종료 코드·로그 파이프라인 — **FIXED**
- **기존 문제**: runner 성공 + 로그 실패만 처리. runner 실패/부분/건너뜀 + 로그 실패 조합은 로그 오류를 기록하지 않았고, 로그 디렉터리 생성 실패(`mkdir -p`는 루트만)와 빈 문자열 인자(`""`)가 기본값으로 처리되었다.
- **변경**: `run/discovery/run_cycle.sh`. runner·로그 기록기·출력 축약기의 종료 코드를 `PIPESTATUS`로 파이프라인 직후 각각 보존, 감사 로그(`NEW2_DISC_AUDIT`, 기본 `cycle_audit.log`)와 stderr에 실패한 프로세스·코드·로그 경로를 기록, 로그 디렉터리 생성 실패 시 runner를 시작하지 않음, 빈 인자 거부, `NEW2_DISC_TEE`로 로그 기록기 교체 가능(테스트용).
- **정책(문서화: README, 스크립트 헤더)**: runner 0+로그 실패→1 · runner ≥1+로그 실패→1(둘 다 기록, runner 코드는 덮어쓰지 않음) · runner 2+로그 실패→1(부분 처리 기록 유실) · runner 3+로그 실패→3(아무것도 실행되지 않아 상태 유지, 로그 오류는 기록). 2·3의 기존 의미는 로그가 정상이면 그대로.
- **테스트**: `tests/test_run_cycle_combinations.py` 10개 (지시문 8개 조합 전부 + 실제 `tee` 실패 + 감사 로그 쓰기 실패). 반환 코드·stderr·감사 로그를 모두 검증.
- **제한**: `shellcheck` 미설치로 실행하지 못했다. `bash -n` 문법 검사만 통과.

### B-2. FDR 세대 완료 조건 / 과거 PASS 오용 — **FIXED**
- **기존 문제**: `family_status()`의 `complete`가 "다른 컨텍스트에서 얻은 terminal PASS"를 현재 컨텍스트의 평가 완료로 취급했다. 그 후보는 현재 환경의 통계값이 없는 채로 m에 들어가 "통계값 없음"으로 처리되었다.
- **변경**: `generation.py::family_status`, `runner.py` (`finalize_fdr`, `prior_pass_needing_reevaluation`, `reevaluate_prior_pass`, 사이클 1b단계).
  - 개념 분리: `manifest_complete`, `current_context_complete`, `fdr_decision_ready`, `locked_test_eligible`(결정 시점에 계산). 기존 `complete`는 `fdr_decision_ready`의 호환 별칭으로 유지하고 의미를 문서화.
  - 모집단 분류: `unevaluated_current`, `without_statistic`(별도 상태, 1.0 대체 없음), `prior_context_pass`(과거 `context_id`·평가 ID·dev 평가 ID·재평가 필요 보존), `prior_context_only`, `errored_current_only`, `duplicate_dev_records`, `unregistered_records`, `reevaluation_required`.
  - 과거 컨텍스트 PASS는 PASS 상태와 잠금 TEST 결과를 유지한 채, **dev 단계만** 현재 컨텍스트에서 재실행(`prior_pass_reeval` 원장 기록). 잠금 TEST는 건드리지 않는다. 같은 컨텍스트의 불변 결과는 재계산 없이 사용.
- **테스트**: `tests/test_final_fdr_context.py` — 지시문 12개 시나리오 전부(전체 평가, 일부 미평가, 통계값 없음, 과거 PASS, 컨텍스트 변경, manifest 변경, 동일 환경 재실행, 후보 순서 변경, 중복 기록, 충돌, 후보 누락, 중복 등록) + 러너 수준 3개. 각 테스트가 결정 가능 여부·m·생존자·미평가 수·재평가 필요를 검증. **변이 검증**: 옛 동작(과거 PASS를 완료로 계산)으로 되돌리면 `test_4`가 실제로 실패함을 확인한 뒤 원복.
- **제한**: 과거 PASS 재평가 경로는 고정 p-값을 주입하는 가짜 평가기로 검증했다. 실데이터 경로는 실행하지 못했다.

### B-3. FDR 결정 불변성·충돌 — **FIXED**
- **변경**: `FdrDecisionStore`에 `input_hash`(generation, manifest hash, context, 방법·버전, q, m, 후보별 첫 평가 ID와 p)를 저장. 동일 증거 재기록은 멱등. 증거가 다르면 `FdrDecisionConflict`를 발생시키고 기존 결정은 그대로 두며 `state/fdr_decisions.conflicts.jsonl`에 기록. 러너는 충돌 시 가족을 "결정 불가"로 보고하고 어떤 후보도 해결하지 않는다. 해시가 없는 옛 형식 결정은 m·생존자·첫 기록·q·방법으로 비교.
- **테스트**: 위 시나리오 9·10, 러너 수준 충돌 테스트(저장된 결정을 훼손해 확인).
- **제한**: 결정의 `input_hash`는 새 필드다. 기존 실제 `fdr_decisions.jsonl`은 이 저장소에 없어 마이그레이션이 필요하지 않았고, 옛 형식 비교 경로만 두었다.

### B-4. 실험 환경 ID와 결과 재사용 — **FIXED (보강)**
- **점검 결과**: 클라인·펀딩·FNG 내용 해시, 비용 전체 파라미터와 모델 소스 sha256, 데이터 정책, 검증 설정(FDR 스펙·창·포트폴리오 기준), 소스 해시(`CODE_FILES`), 런타임 버전은 이미 반영되어 있었다.
- **보강**: (1) 전략 공간(generation id, manifest hash, 생성기 버전)을 환경 ID에 포함 — 전략 정의가 바뀌면 ID가 바뀐다. (2) 펀딩·FNG 시리즈 해시를 행 순서와 무관하게 만듦. (3) git commit은 ID에 넣지 않고 `state/contexts/<ID>.json`의 `informational`에만 기록(문서만 바꾼 커밋이 새 실험을 만들지 않도록; 실제 코드 변경은 소스 해시가 식별). (4) `CODE_VERSION` 라벨을 `discovery-v2.2.0`으로 변경(식별은 소스 해시).
- **테스트**: 동일 입력 동일 ID, 후보 순서 불변, 전략 공간 변경, 검증 구간 변경, 시리즈 순서 불변, JSON 키 순서 불변, git commit 비포함, 컨텍스트 변경 시 재평가 큐 등록(기존 `TestContextId`의 펀딩/FNG/비용/코드/런타임/정책 변경 테스트와 함께).
- **제한**: 이 변경으로 환경 ID 값이 이전 판과 달라진다(이 저장소에는 실제 실행 기록이 없음). 클라인은 파일 바이트 해시를 쓰므로 줄바꿈만 다른 동일 데이터도 다른 ID가 된다(보수적).

### B-5. 통계 전제·상태 라벨 문서화 — **FIXED (문서)**
- `STATISTICS_AND_STATES.md` §2를 재작성: p-값 정의, 가설 모집단과 m, BH 적용 조건, 후보 간 의존성, **PRDS 미검증 → 오류율 "보장되지 않음"**, 다른 컨텍스트 재검정은 FDR 통제 밖, 남은 선택 편향·데이터 누출 위험. §3에 `PASS / FAIL_STAT / FAIL_COST / DATA_MISSING / UNDETERMINED_POWER / ERROR` 비중첩 규칙과 "미검증 비용 모델은 라벨이 아니라 claim scope" 명시. 검정 방법은 교체하지 않았다.

### B-6. 보고서·저장소 상태 일치 — **FIXED**
- 이 보고서와 `README.md`를 현재 코드·로그·PR 상태 기준으로 다시 작성. 이전 보고서 `AUDIT_RESIDUAL_FIXES_20261015.md`에는 "PR #1 병합, 168개는 병합 시점 기록" 상태 갱신 주석을 추가(본문은 보존). 이전 보고서 값을 복사해 최신값으로 쓰지 않았다.

## C. 테스트 결과 (`evidence_final_20261015/`)

| 명령 | 종료 코드 | 결과 | 로그 |
|---|---|---|---|
| `cd run && COINTRADER_SRC=/home/claude/NEW2_public/src python3 -m unittest discover -s discovery/tests -v` (1회차, 08:20:43Z–08:21:55Z) | 0 | Ran **202**, OK, 72.6s | `full_suite_run1.log` |
| 같은 명령 (2회차, 08:21:55Z–08:23:06Z) | 0 | Ran **202**, OK, 70.8s | `full_suite_run2.log` |
| `python3 -m compileall -q discovery` | 0 | 오류 없음 | `compile.log` |
| `bash -n discovery/run_cycle.sh` | 0 | 문법 오류 없음 | `shell_syntax.log` |
| `shellcheck` | — | **미설치, 실행하지 못함** | `shell_syntax.log` |
| `ruff check discovery --select F` | 1 | 20건(전부 이번에 건드리지 않은 기존 코드의 미사용 import/변수; 이전 판과 동일, 신규 0) | `static.log` |
| `NEW2_DISCOVERY_ROOT=<tmp> bash run/discovery/run_cycle.sh 1` (실데이터 시도) | 1 | `NEW2_DATA_DIR` 없음 → fail-closed | `real_data_cycle_attempt.log` |

- 전체 202 = 기존 168 + run_cycle 조합 10 + FDR/환경 24. 통과 202, 실패 0, 건너뜀 0.
- 지시문 §7 항목 대응: 신규 잔여 결함 테스트(위 두 파일), FDR 결정 재현성(시나리오 7·8·9), 환경 ID 변경(`TestContextBehaviour`, `TestContextId`), 잠금 TEST 상태 전이·비용 오류·강제 청산 회계·승격 게이트(기존 `test_residual_fixes.py`, 회귀로 재실행되어 통과).
- 처음 병렬로 한 번 돌린 결과는 증거로 채택하지 않고, 위 순차 2회로 다시 만들었다.

## D. FDR 검증 (고정 p-값 6개 후보 합성 시나리오)

| 항목 | 값 |
|---|---|
| 후보 모집단 m | 6 (실제 생성기 정의 6개; 실제 전체 공간은 779) |
| 현재 환경에서 평가된 후보 | 6 (전체 평가 시) / 5 (일부 미평가 시나리오) |
| 미평가 후보 | 0 / 1 |
| 통계값 없는 후보 | 1 (`UNDETERMINED_POWER`, m에 포함, 생존 불가) |
| FDR 결정 가능 여부 | 전체 평가 시 가능, 일부 미평가·과거 PASS 미재평가·manifest 훼손 시 불가 |
| 생존 후보 | 3 (손계산 BH: p=.001,.008,.02 생존, .035 탈락) |
| 환경 변경 시 | 6개 모두 `reevaluation_required`, 과거 결정 재사용 안 됨 |

실제 779개 후보 공간의 평가는 실행하지 않았다(데이터 없음).

## E. 남은 위험

- **통계 가정**: BH의 PRDS 가정 미검증 → FDR "보장되지 않음". 대안(BY 등)은 연구/사전등록 없이 교체하지 않았다.
- **실데이터**: 이 환경에 klines·펀딩이 없어 `BLOCKED_DATA_MISSING`. 779개 전체 평가에는 batch 10 기준 최소 78사이클이 필요하다.
- **비용 모델**: `UNVERIFIED_COST_MODEL` 그대로(주문서·실측 스프레드 없음). 미검증 PASS는 승격 불가.
- **본 프로젝트 통합**: 미통합·미배포. 런타임(`/home/z/...`)에 반영하지 않았다.
- **기타 기존 한계 유지**: 다중 심볼 포트폴리오 그리드 불일치(`portfolio.error`로 fail-closed 기록), 조합 후보 생성기 없음, 청산은 엔진 가정(거래소 규칙 아님), 타 호스트 잠금 시도 자동 복구 없음, 분산 0은 `UNDETERMINED_POWER`, `shellcheck` 미실행.
- **실전 배포 가능 여부**: **불가/미검증**. 실거래 주문·자금 이체·실계좌 API 호출은 실행하지 않았다.

## F. 최종 판정: `PARTIAL`

- [x] 셸 실행기·로그 파이프라인 종료 코드가 지시문의 모든 조합에서 기대값과 일치(테스트 검증)
- [x] FDR 세대 완료 조건이 manifest 완전성·현재 환경 평가 완료와 구분됨
- [x] 과거 PASS 기록이 현재 환경의 평가 결과로 사용되지 않음(변이 검증 포함)
- [x] 미평가·통계값 없는 후보가 명시적으로 기록됨
- [x] FDR 결정이 불변 입력 증거와 연결되고 충돌 시 오류 발생
- [x] 환경 ID가 데이터·설정·코드·전략 공간 변경을 식별
- [x] 기존 잠금 TEST·원장 데이터 보존(상태 파일 변경 없음, 삭제 없음)
- [x] 신규 테스트와 전체 회귀 테스트 통과(202/202, 2회)
- [x] 문서가 현재 코드·로그·PR 상태와 일치
- [x] 실데이터·비용 모델 미검증 공개
- [x] 실거래 주문·자금 이동 없음
- [ ] 실데이터 사이클 실행 — **BLOCKED_DATA_MISSING**
- [ ] `shellcheck` 정적 검사 — 미설치
- [ ] 본 프로젝트 통합·배포 — 미실행

1. **엔진 구조적 신뢰성**: 합성·고정 데이터 및 격리된 가짜 실행기 범위에서 지시문의 결함 시나리오가 의도대로 처리됨을 202개 테스트로 확인했다 → **조건부 개선(실데이터·PRDS·shellcheck 미검증)**.
2. **전략 수익성**: 실데이터와 현실적 거래 비용으로 독립 검증하지 않았으므로 **NOT_VALIDATED**.
