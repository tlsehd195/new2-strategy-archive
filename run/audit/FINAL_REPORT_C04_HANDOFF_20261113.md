# C-4 감사 보고서 — Handoff가 과거 FNG PAPER_PASSED를 내보내던 문제

- 지시: 사용자 동동, 2026-11-13 04:21Z
- 작업 기준: 브랜치 `claude/project-thread-vjbdnd`, HEAD `66bf37c`. 작업 시작 시 PR #79 HEAD와 같은지 확인했다.
- 수정 커밋: `3e080c8`
- 결정 기록: [ADR 0079 §6](../../docs/adr/0079-audit-followup-d01-resolution-20261112.md)
- 이전 보고서: [FINAL_REPORT_ADR0079_FOLLOWUP_20261112.md](FINAL_REPORT_ADR0079_FOLLOWUP_20261112.md). 그 보고서 §G의 C-4를 이번에 해결했다.

## A. 해결 결과

- C-4 해결: 예.
- 수정 전 재현: 예.
  - 방법: 합성 데이터로 FNG 전략(EDGE + `fng_max ≤ 100` 필터)을 실제 흐름으로 승격시켰다(검증 StepM PASSED, 잠금 캠페인, 격리 재생, 승격 검토 → PAPER_TESTING).
  - 그 뒤 ADR 0079 이전 동작으로 Forward 계획을 PAPER_PASSED로 결론냈다.
  - 이 상태에서 `66bf37c`의 `build_handoff`는 `status: PAPER_PASSED`, `paper_forward_verdict: PAPER_PASSED`, `paper_forward_record_id: EV-f92b5affa8ffb9f1cb09`를 돌려줬다.
- 수정 후 차단: 예. `HandoffRefused`가 나고 문서는 만들어지지 않는다.

## B. 코드 변경

| 파일 | 함수 | 내용 |
|---|---|---|
| `run/discovery/handoff.py` | `build_handoff` | 명세 읽기를 `_spec_or_refuse`로 바꾸고, `_forward_or_refuse` 검사를 추가 |
| `run/discovery/handoff.py` | 신규 `_spec_or_refuse` | 명세가 없으면 거부(DATA_MISSING). 읽을 수 없으면 거부 |
| `run/discovery/handoff.py` | 신규 `_forward_or_refuse` | 아래 규칙 적용 |
| `run/discovery/tests/test_audit_followup_20261112.py` | 신규 `TestC04HandoffFng` | 테스트 4개 |

`_forward_or_refuse` 규칙:

1. 전략이 FNG를 읽고 Paper Forward 기록이 있으면 판정과 관계없이 거부한다. 사유는 `FNG_NOT_PINNED`와 `UNVERIFIED_PRIOR_CONCLUSION`이다. 기록된 판정과 기록 ID는 거부 메시지에만 남고, 문서로 나가지 않는다.
2. 상태가 PAPER_PASSED이면 다음을 모두 만족해야 내보낸다. 하나라도 어긋나면 거부한다.
   - 그 Paper Forward 기록이 있다(없으면 DATA_MISSING).
   - 기록의 판정이 PAPER_PASSED다.
   - 상태 원장의 증거 ID(`evidence.paper_forward`)가 기록 ID와 같다.

기존 검증 재사용:

- FNG 판정은 `ForwardPaper._fng_refusal(spec)`을 그대로 쓴다. 등록 거부와 같은 함수, 같은 사유 문구다.
- 판정 근거는 정규화 명세의 `data`다. 이 값은 `canonicalize_spec`이 특징 정의(`FEATURES[...].data`)에서 다시 계산한다. 그래서 실제 데이터 의존성을 기준으로 판정한다.
- `ForwardPaper._plan_fng_problem`과 `verify_conclusion`은 쓰지 않았다. 둘 다 Forward 피드 객체와 계획 디렉터리가 필요하기 때문이다. 같은 판정 함수를 직접 썼으므로 결과는 같다. 테스트가 같은 기록에서 `step()`과 `verify_conclusion()`도 FNG_NOT_PINNED를 내는지 함께 확인한다.

### 차단 방식 선택 이유

1순위인 거부를 택했다. 2순위인 미검증 표시는 택하지 않았다.

- `build_handoff`는 원래 증거 불일치를 `HandoffRefused`로 처리한다. 승격 검토가 없거나, 검토 증거와 기록이 다르면 거부한다. 같은 계약을 따랐다.
- 미검증 표시 방식에는 두 가지 문제가 있다.
  - 봉인 문서의 `status` 필드는 상태 원장 값인 PAPER_PASSED를 그대로 담는다. 그래서 통과로 읽힐 여지가 남는다.
  - 스키마(`new2_research_handoff_v2`)를 바꿔야 하고, 문서를 읽는 쪽도 바꿔야 한다.
- 검증 가능한 FNG 증거 구조가 없다.
  - 계획에 FNG 해시·스냅샷이 없다.
  - 전진 피드에 FNG 원천이 없다.
  - 그래서 "검증된 FNG 결론"이라는 경우가 없다. 이 경우를 새로 만들지 않았다.

### FNG 전략과 비FNG 전략의 차이

| 경우 | 수정 전 | 수정 후 |
|---|---|---|
| FNG 전략, Forward 기록 있음(어떤 판정이든) | 판정을 그대로 내보냄 | 거부 |
| FNG 전략, Forward 기록 없음(PAPER_TEST_READY·PAPER_TESTING) | `NOT_RUN` | 같음(노출할 판정 없음) |
| 비FNG 전략, PAPER_PASSED와 일치하는 기록 | 내보냄 | 같음 |
| 비FNG 전략, PAPER_PASSED인데 기록 없음·불일치 | `NOT_RUN` 또는 다른 판정으로 내보냄 | 거부 |
| 명세 없음·읽을 수 없음 | 예외(`canonicalize_spec`) | `HandoffRefused` |

## C. 테스트 결과

신규 테스트 `TestC04HandoffFng` 4개:

| 테스트 | 지시 항목 | 수정 전(`66bf37c`) | 수정 후 |
|---|---|---|---|
| `test_old_fng_paper_passed_is_not_handed_off` | 1, 2 | FAIL(HandoffRefused not raised) | PASS |
| `test_existing_handoff_documents_are_kept` | 2 | FAIL(HandoffRefused not raised) | PASS |
| `test_non_fng_strategy_hands_off_as_before` | 3 | PASS(대조) | PASS |
| `test_unclear_evidence_is_refused` | 4 | ERROR(함수 없음) | PASS |

테스트 5(기존 정상 Handoff 회귀)는 아래 기존 테스트로 확인했다. 테스트 6(우회 경로 조사)은 §E에 적었다.

| 항목 | 결과 |
|---|---|
| Handoff 기존 테스트(`test_pipeline.TestHandoff` 2개, `test_completion`의 forward → handoff 테스트) | PASS |
| FNG 테스트(`TestG04ForwardFng` 6개, 후속 48개 전체) | PASS |
| 지정 묶음: `test_pipeline.TestHandoff`, `test_completion`, `test_audit_followup_20261112` | PASS (3.13 로컬, 89개) |
| 전체 discovery unit | PASS: 3.11·3.13 모두 1,123개 OK(이전 1,119개 + 신규 4개) |
| 루트 테스트 | PASS: 3.11·3.13 모두 152개 OK |
| Python 3.11 / 3.13 | PASS: 로컬 작업 트리 2개와 GitHub Actions 모두 |
| 정적 검사 | PASS: compileall, ruff(E9/F63/F7/F82와 F), `bash -n`·shellcheck, configs, independence, 원장(ledger_check), inventory, source audit, provenance, TS, manifests |

- 처음 Actions(#355 push, #356 pull_request, `3e080c8`)는 source audit 단계에서 실패했다. `handoff.py`가 2026-11-10 검토 판과 달라졌는데 `changed_since_review` 표시가 없었기 때문이다.
  - `source_audit_20261110.py refresh`로 표시를 넣었다(`02306d2`). 감사 표의 해당 행에 바뀐 판의 sha256이 기록된다. 검토 등급은 올리지 않았다.
  - 이 단계를 처음 수정 전에 로컬에서 돌리지 않아서 놓쳤다.

## D. 기록 보존 검증

다음은 문장이 아니라 테스트 assertion으로 확인했다.

- `test_old_fng_paper_passed_is_not_handed_off`
  - `build_handoff`와 `export_handoff`를 호출하기 전후에 상태 디렉터리 전체 파일의 바이트를 비교했다. 같다.
  - 대상: `evaluations.jsonl`, `statuses.jsonl`, 계획별 `state.json`, `steps.jsonl`, 봉 로그, 펀딩 로그, 계획 원장, 검토 원장, 캠페인 원장 등 `.lock`을 뺀 모든 파일.
  - 이 세 파일이 비교 대상에 들어 있는지도 확인한다: `evaluations.jsonl`, `statuses.jsonl`, `state.json`.
  - 호출 뒤에도 상태 원장은 PAPER_PASSED, 평가 기록의 판정은 PAPER_PASSED다.
  - 거부된 export는 출력 디렉터리에 파일을 남기지 않는다.
- `test_existing_handoff_documents_are_kept`: 같은 전략이 PAPER_TESTING이던 때 내보낸 handoff 파일이 있다. 나중에 거부가 나도 그 파일의 바이트는 그대로이고, 새 파일은 생기지 않는다.
- 저장소의 실제 `data/`, `research/`, `results/`, state, 원장, PREREG, `private_fills/`는 읽지도 쓰지도 않았다. 테스트는 임시 디렉터리와 합성 데이터만 쓴다.

## E. 남은 문제

- **우회 경로 조사(테스트 6).** `run/` 전체에서 Paper Forward 기록을 읽는 곳과 `PAPER_PASSED`를 쓰는 곳을 찾았다.
  - 증거 문서는 `handoff.build_handoff`·`export_handoff` 하나뿐이고, 저장소 안에 다른 호출부는 없다.
  - **C-5 (별도 문제, 미수정):** `pipeline.outcome_label`은 상태 PAPER_PASSED를 집계 라벨 PASS로 바꾼다. `evolution_report`는 그것을 생존자로 센다. 둘 다 상태 원장만 읽는 보고용 집계라서, 과거 FNG PAPER_PASSED도 PASS로 집계된다. 고치려면 집계 쪽에서 명세를 읽어 라벨을 바꿔야 한다. 이번 범위(Handoff)를 넘으므로 기록만 했다. 사용자 결정 사항이다.
- **비FNG 결론의 앵커 재검증.** Handoff는 비FNG Forward 결론에 `verify_conclusion`(파일 앵커·재생)을 다시 돌리지 않는다. 그러려면 피드 객체가 필요하다. 기존 동작 그대로다.
- **이전 보고서의 미해결 항목:** C-1(같은 상태 디렉터리 다중 인스턴스), C-2, C-3. FNG 전진 원천, L-1, E-01, ME-04는 사용자 결정 대기다.
- **외부 증거:** 비용 VERIFIED(실계좌 체결). 변경 없음.

검토 범위:

| 파일 | 수준 |
|---|---|
| `handoff.py` | FULL(작성자, 이번 판 전체) |
| `paper_forward.py` | PARTIAL: `_fng_refusal`, `_plan_fng_problem`, `_concluded_result`, `verify_conclusion`, `_conclude`, `_resume_conclusion` |
| `pipeline.py` | PARTIAL: `evaluation`, `outcome_label`, `reverification_pending` |
| `evolution_report.py` | PARTIAL: 상태 집계 |
| 그 밖 | NOT REVIEWED(이번 작업) |

## F. GitHub 작업 상태

- 브랜치 `claude/project-thread-vjbdnd`(PR #79). main 직접 커밋, 병합, 강제 푸시는 하지 않았다.
- 커밋:
  - `3e080c8`: 코드와 테스트. `handoff.py`, `tests/test_audit_followup_20261112.py`.
  - `02306d2`: 감사 표, ADR, worklog, 이 보고서. `SOURCE_AUDIT_20261110.jsonl`·`.md`, `docs/adr/0079-…md`, `docs/worklog.md`, `FINAL_REPORT_C04_HANDOFF_20261113.md`.
  - 이 보고서를 마감한 커밋은 문서만 바꾼다.
- C-4로 바뀐 파일은 모두 7개다(코드 1, 테스트 1, 감사 표 2, 문서 3).
- 로컬 전체 CI: `02306d2`에서 3.11·3.13 모두 `OVERALL fail=0`.
- GitHub Actions `research-ci`:
  - `3e080c8`: #355, #356 실패(source audit, 위 설명).
  - `02306d2`: #357(push)과 #358(pull_request) 모두 test (3.11)·test (3.13) success.
  - 보고서 마감 커밋의 결과는 스레드 최종 보고에 적는다.
