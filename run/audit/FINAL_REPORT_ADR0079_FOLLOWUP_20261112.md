# ADR 0079 후속 감사 보고서 — FNG 완료 상태 반환, G-03 호출 그래프, F-11 잠금 범위

- 지시: 사용자 동동 2026-11-12 23:20Z ("ADR 0079 후속 결함 수정 — FNG 완료 상태 반환 및 G-03 독립성 검사 보완")
- 기준: PR #79 HEAD `3d49e41`. 수정 커밋 `986aaab`.
- 결정 기록: [ADR 0079 §5](../../docs/adr/0079-audit-followup-d01-resolution-20261112.md)
- 테스트: `run/discovery/tests/test_audit_followup_20261112.py` (33개 → 48개)
- 도구: `run/discovery/tests/static_callgraph.py`

## A. 재현한 문제와 원인

1. **완료된 FNG 계획이 `step()`에서 PAPER_PASSED를 돌려줌**
   - 원인: `_step_locked`가 `st["concluded"]`일 때 저장된 판정을 바로 돌려줬다. 이 반환이 `_plan_fng_problem` 검사보다 먼저 실행됐다.
   - 재현: ADR 0079 이전 동작으로 FNG 계획을 등록하고 PAPER_PASSED로 결론냈다(합성 데이터). 그 뒤 새 코드에서 `step()`을 부르면 `{'status': 'CONCLUDED', 'verdict': 'PAPER_PASSED'}`가 나왔다. 같은 계획에 `verify_conclusion()`은 FNG_NOT_PINNED를 냈다. 두 API가 서로 달랐다.
2. **G-03 테스트가 이름만 확인함**
   - `TestG03SharedSurface`는 문서에 적힌 이름이 양쪽 코드에 "나타나는지"만 봤다.
   - 그래서 문서에 없는 공유 호출이 새로 생겨도 통과했다.
   - 함수 이름이 속성 이름이나 변수로만 나와도 통과했다.
3. **F-11: 잠금이 있는 파일에 잠금 없이 추가하는 경로**
   - 원인: `append_jsonl`은 꼬리 확인(`repair_jsonl_tail`)과 기록을 한 번에 하지 않는다.
   - 재현: 쓰기 A가 꼬리를 확인한 뒤 기록하기 전에, 같은 파일의 다른 쓰기가 줄 중간에서 죽으면 A의 기록이 반쪽 줄에 붙는다.
     - 죽은 쓰기는 kill된 프로세스다. 반쪽 바이트가 남고, 그 flock은 커널이 푼다.
     - 결과: 내부 손상 줄이 생겨 엄격 읽기가 실패하고, A의 기록은 사라진다.
   - 같은 파일의 판단 쓰기가 잠금을 잡아도, 단순 추가가 잠금을 잡지 않으면 이 순서를 막지 못한다. 테스트 `_interleaving_writer`가 이 순서를 그대로 만든다.
   - 해당 경로:
     - `CampaignLedger.record_result`, `abort_no_exposure`
     - 배치 RESULT
     - `evaluations.jsonl`
     - `research_feedback.jsonl`. 여기는 읽기·판단·쓰기라서 더 문제다.

## B. 변경한 파일과 함수

| 파일 | 함수 | 변경 |
|---|---|---|
| `run/discovery/paper_forward.py` | `_step_locked`, 신규 `_concluded_result` | 완료 계획을 돌려주기 전에 FNG를 검사한다. FNG면 `PAPER_BLOCKED_DATA`, `recorded_verdict`, `conclusion_verified: False`, 사유 `FNG_NOT_PINNED`·`UNVERIFIED_PRIOR_CONCLUSION`. 읽기만 하고 쓰지 않는다. FNG가 없는 계획은 기존 dict 그대로 |
| `run/discovery/paper_forward.py` | `_feedback_once` | 읽기·판단·추가를 `exclusive_file_lock` 안에서 |
| `run/discovery/campaigns.py` | `record_result`, `abort_no_exposure` | 원장 잠금 안에서 추가 |
| `run/discovery/pipeline.py` | `_evaluate_batch`(RESULT 2곳), `_append_eval` | 배치 원장과 평가 기록 잠금 안에서 추가 |
| `run/discovery/tests/static_callgraph.py` | 신규 | AST Call 노드 기반 호출 그래프와 `shared_surface` |
| `run/discovery/tests/test_audit_followup_20261112.py` | 신규 클래스 | `TestG04ForwardFng`(2개 추가), `TestF11AppendUnderLock`, `TestF11BatchAndEvaluations`, `TestF11ForwardFeedback`, `TestG03CallGraph`, `TestG03Resolver`, `TestG03RuntimeTrace` |
| `docs/adr/0079-…md` | §5 | 후속 결정, 공유 표(노드 ID) |

`runner.py`와 `engine_lib.py`는 바꾸지 않았다. 그래서 두 파일의 FULL 검토 sha256 고정은 유지된다(provenance check 통과).

## C. 수정 전 실패 테스트 (`3d49e41` 코드에 새 테스트 파일만 넣어 실행)

`Ran 48 tests — FAILED (failures=33)`. 이 숫자는 하위 테스트까지 센 것이다.

- `test_concluded_fng_plan_step_does_not_return_old_pass`: `'PAPER_PASSED' == 'PAPER_PASSED'`
- `test_campaign_result_appends_under_the_ledger_lock`, `test_campaign_abort_appends_under_the_ledger_lock`: 잠금 없이 추가. 반쪽 줄에 붙음.
- `test_batch_result_and_evaluations_append_under_lock`: 같음.
- `test_research_feedback_decided_and_appended_under_lock`: 같음.
- `test_documentation_lists_the_entries`(하위 28개): 3d49e41의 ADR에 노드 ID 목록이 없었다.

G-03 호출 그래프 테스트(`TestG03CallGraph`의 나머지, `TestG03Resolver`, `TestG03RuntimeTrace`)는 3d49e41 코드에서도 통과한다. 이번에 공유 범위 자체를 바꾸지 않았으므로 맞는 결과다. 이 테스트들이 앞으로의 변화를 잡는다는 것은 변형 테스트로 확인했다.

## D. 수정 후 통과 테스트

- 새 테스트 15개와 기존 ADR 0079 테스트 33개, 모두 48개가 통과한다.
- ADR 0078 테스트 55개(`test_integrated_audit_20261112.py`)는 전체 실행에 포함되어 통과했다.
- 로컬 전체 CI는 `986aaab`에서 Python 3.11과 3.13 작업 트리 각각에서 실제로 실행했다. 두 버전 모두 `OVERALL fail=0`.
  - discovery unit: 1,119개 OK(이전 1,104개 + 신규 15개). 3.11은 1,716초, 3.13은 1,688초.
  - root 테스트: 152개 OK.
  - legacy, compileall(discovery와 run/·audit/), ruff(E9/F63/F7/F82와 F), `bash -n`과 shellcheck, configs, independence, TS 빌드, manifests: OK.
  - 원장(ledger_check), inventory(diff 없음), source audit, provenance(FULL sha256 일치): OK.

## E. 기존 결론과 원장 변경 여부

- 없음. `step()`은 완료된 FNG 계획에서 아무것도 쓰지 않는다. 테스트가 상태 디렉터리 전체의 바이트를 호출 전후로 비교한다(state.json, evaluations.jsonl, statuses, steps.jsonl, 계획 원장).
- 저장된 결론은 다음과 같이 그대로 남는다.
  - `state["verdict"]`: PAPER_PASSED
  - 평가 기록: PAPER_PASSED
  - 상태 원장: PAPER_PASSED
- 반환값에만 `recorded_verdict`와 `conclusion_verified: False`로 미검증 과거 결론이라고 표시한다.
- 실제 `data/`, `research/`, `results/`, state, 원장, PREREG, `private_fills/`는 읽지도 쓰지도 않았다. 테스트는 합성 데이터와 임시 디렉터리만 쓴다.
  - 변형 테스트는 소스 사본을 임시 디렉터리에 만들어 쓴다.
  - 잠금 TEST, IW0, IW1, sealed 창은 열지 않았다.

## F. G-03 공유 함수 최종 목록 (호출 그래프 계산 결과 = 테스트 고정값)

기준점은 평가기 `ForwardPaper._evaluate`와 재생 `ForwardPaper._reconstruct_positions`다. 아래는 양쪽이 각자 비공유 코드에서 직접 들어가는 공유 함수다. 그 아래 함수는 전부 공유된다.

| 영역 | 공유 진입 |
|---|---|
| 명세 | `strategy_spec.canonicalize_spec`, `evolution.SpecStore.get` |
| 지표·신호 | `strategy_spec.build_spec_aux`, `strategy_spec.build_spec_signal` |
| 청산 | `strategy_spec.stop_exit_rule` |
| FNG | `dyn:self.p.provider.fng` (provider 구현마다 다름) |
| 인과성 | `evaluator.PrefixView` |
| 펀딩 | `evaluator.FundingIndex`, `evaluator.FundingIndex.paid_bp`, `ForwardPaper._funding_series`, `ForwardPaper._funding_policy` |
| 비용 거부 | `costs_bridge.CostContext.round_trip_bp` |
| 입력 | `ForwardPaper._warmup`, `paper_forward.read_bar_log`, `paper_forward._row_bar`, `engine_lib.iter_jsonl` |

- 평가기 전용(직접 호출):
  - `evaluator.run_event_study`
  - `execution_sim.ExecutionConfig`, `execution_sim.apply_execution`
  - `ForwardPaper._series`, `_open_positions`, `_reconcile_trades`
  - `evaluator.EventRecord.to_json`
  - `engine_lib.append_jsonl`, `iso_now`
- 재생 전용:
  - `ForwardPaper._replay_from_disk`, `_replay_positions`
  - `costs_bridge.CostContext.identity`
- 공유 규칙 상수: `GAP_EMBARGO_BARS`, `MS15`

검증 방법:

- 정확 일치: 계산 결과가 문서와 다르면 테스트가 실패한다.
- 변형 테스트: 소스 사본에서 재생 쪽에 `apply_execution` 호출을 넣으면 공유 진입에 들어가 테스트가 실패한다.
- 합성 모듈 해석기 테스트: 다음을 확인한다.
  - 모듈 별칭, `from … import … as`, 함수 안 import, 래퍼를 통한 간접 호출, 메서드, 주석으로 타입이 정해진 매개변수, 생성자 지역 변수, 인자로 넘긴 함수.
  - 반대로 docstring 안의 이름, 대입만 한 참조, 알 수 없는 수신자는 호출로 세지 않는다.
- 런타임 교차 확인: 합성 계획 하나를 `sys.setprofile`로 추적했다. 실행된 저장소 함수는 모두 정적 도달 집합 안에 있다. 예외는 선언된 동적 종류 셋(던더, `FEATURES` 표, `CostContext.model`)뿐이다.
  - 이 확인에서 처음에 `EventRecord.to_json` 누락이 나왔다. 해석기가 반환 주석과 리스트 필드 타입을 따르도록 고쳤다.

한계(과장하지 않음):

- 정적 분석은 실행 때 정해지는 호출을 보지 못한다. 이런 호출은 `untracked`로 기록하고 추정하지 않는다.
  - 콜러블 매개변수 `sig`, `exit_rule`, `refusal`
  - 함수 사전, `getattr`
  - 타입 없는 속성
- 도달 가능한 호출이 매 입력에서 실행되는 것은 아니다.
- 런타임 추적은 그 입력이 지나는 분기만 덮는다.
- 수신자 타입 셋(`self.p.cost`, `self.p.specs`, `self.p.provider`)은 소스에 없어서 테스트가 선언한다.

결론: 재생이 독립인 것은 포지션 순회와 구멍 재도출뿐이다. 위 표의 함수 안에 결함이 있으면 재생으로 잡히지 않는다.

## G. F-11 잠금 범위와 미해결 동시성 위험

| 기록 | 경로 | 분류 | 처리 |
|---|---|---|---|
| `locked_window_campaigns.jsonl` | `record_result`, `abort_no_exposure` | 단순 추가. 같은 파일에 판정 쓰기(`preregister`, 잠금)가 있음 | 잠금 안으로 |
| `validation_batches.jsonl` RESULT | `_evaluate_batch` 2곳 | 단순 추가. 사전등록은 잠금 안 | 잠금 안으로. 이 잠금을 잡은 채 부르는 경로 없음 |
| `evaluations.jsonl` | `_append_eval` | 단순 추가. 다른 계획들이 동시에 결론냄(계획별 `step.lock`만) | 잠금 안으로 |
| `research_feedback.jsonl` | `_feedback_once` | 읽기·판단·쓰기. 여러 계획이 같은 파일에 씀 | 읽기부터 추가까지 잠금 안으로 |

- 교착 검토: 새 잠금은 짧은 잠금이다. 그 안에서 다른 잠금을 기다리지 않는다(상태 원장 잠금은 기존 순서 그대로). 긴 잠금의 순서(이력 → 배치, IW 이력 → 레지스트리)는 바뀌지 않았다.
- 운영 CLI: `runner.py`가 상태 디렉터리당 `run.lock` 하나로 모든 쓰기를 직렬화한다. 이 경우 위 경쟁은 생기지 않는다.
- 라이브러리를 직접 쓸 때 잠금으로 보장되는 것:
  - 위 표의 파일
  - 상태 원장, 사전등록, 계획 원장, contexts, 검증 창 이력, IW 레지스트리, 잠금 TEST 레지스트리
  - 계획별 파일

미해결(기록만):

| ID | 내용 | 판단 |
|---|---|---|
| C-1 | 같은 상태 디렉터리에서 두 인스턴스가 같은 열린 배치를 동시에 평가하면 평가 기록과 RESULT가 중복될 수 있다. `run_validation`의 "열린 배치가 있나" 판단이 잠금 밖에 있다. | 기록이 사라지거나 바뀌지 않는다. 재개 대조가 불일치를 막는다. 다중 인스턴스 지원 여부는 사용자 결정 |
| C-2 | `series`·`review` 기록과 계획 디렉터리 밖의 기타 파이프라인 기록은 잠금 없이 추가한다. | 한 디렉터리 한 쓰기(`run.lock`)가 전제 |
| C-3 | `append_jsonl` 자체에는 잠금을 넣지 않았다. 넣으면 모든 JSONL에 잠금 파일이 생긴다. 또 이미 잠금을 잡은 호출부가 같은 프로세스에서 다시 잡으면 교착이 생긴다. 그래서 FULL 검토된 `engine_lib.py`는 바꾸지 않았다. | 잠금 프로토콜이 있는 파일만 처리 |
| C-4 | 상태 원장에 PAPER_PASSED로 남은 과거 FNG 전략은 `handoff.build_handoff`가 `paper_forward_verdict: PAPER_PASSED`로 내보낸다(사람 검토용 문서, `requires_human_approval`). | 사용자 결정 |

## H. 남은 외부 증거와 사용자 결정

- FNG 전진 원천을 추가할지, 계획별 해시 체인으로 남길지. 지금은 새 FNG 계획을 거부한다.
- 과거 FNG PAPER_PASSED를 handoff 같은 하류 문서에서 어떻게 표시할지(C-4).
- 한 상태 디렉터리의 다중 인스턴스를 지원할지(C-1).
- 잠금 TEST 캠페인 수를 상태 디렉터리끼리 공유할지(L-1). ADR 0078의 E-01, ME-04.
- 비용 VERIFIED: 실계좌 체결 증거가 없다. 변경 없음.

검토 범위:

| 파일 | 수준 |
|---|---|
| `runner.py`, `engine_lib.py` | FULL(이전, sha256 고정 유지. 이번에 바꾸지 않음) |
| `paper_forward.py` | PARTIAL: `_step_locked`, `_concluded_result`, `_feedback_once`, `_evaluate`, `_series`, `_reconstruct_positions`, `_replay_from_disk`, `_replay_positions`, `_warmup`, `_funding_series`, `verify_conclusion`, `_resume_conclusion` |
| `pipeline.py` | PARTIAL: `_evaluate_batch`, `_append_eval`, `_history_guard`, `_preregister_batch`, `run_validation`, 잠금·추가 호출부 |
| `campaigns.py` | PARTIAL: 원장 쓰기 전부 |
| `evaluator.py`, `strategy_spec.py`, `costs_bridge.py`, `handoff.py`, `autoloop.py` | PARTIAL: 호출 그래프에 나온 함수와 `step()` 호출부 |
| 그 밖 | NOT REVIEWED(이번 작업) |

## I. PR 및 Actions 상태

- PR #79(브랜치 `claude/project-thread-vjbdnd`)에 이어서 커밋했다. main 직접 커밋과 병합은 없다.
- 로컬 CI(`986aaab`): §D 참조. 3.11과 3.13 모두 fail=0.
- GitHub Actions `research-ci`:
  - `986aaab`: run #351(push), #352(pull_request).
  - 이 보고서를 넣은 최종 HEAD: run 결과는 PR #79의 체크와 스레드 보고에 적는다(보고서 커밋 자체는 문서만 바꾼다).
