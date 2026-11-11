# ADR 0077 — Paper Forward 미결 포지션 완전성과 최종 결론 상태의 독립 검증 (paper-forward-completeness-v1)

- 상태: Accepted
- 날짜: 2026-11-11
- 요청: 동동, 2026-11-11 지시문 "ADR 0077 — Paper Forward 미결 포지션 완전성 및 최종 상태 독립 검증 보강"
- 관련: ADR 0076(paper-forward-cutoff-v1), ADR 0075(paper-forward-positions-v1), ADR 0074(paper-forward-integrity-v1), ADR 0073(paper-forward-funding-v1)
- 시작 커밋: `34f0e9f` (main, PR #77 병합)

## 1. 발견된 문제

### 1.1 문제 A — 미결 포지션 목록의 완전성을 아무도 확인하지 않았다

`_evaluate()`는 `run_event_study` 결과로 청산 거래를, `_open_positions()`로 미결 포지션을 만든다. 그 뒤 판정 경로(`_position_problems` → `_pending` → `_verdict` → `_conclusion_problems` → `_conclude`)는 모두 **같은** `result["open_positions"]` 목록만 읽었다.

- `_position_problems()`는 목록 **안의 항목**만 검사했다(심볼·방향·마크·원장 중복). 목록이 비어 있으면 검사할 항목이 없다.
- `_pending()`은 빈 목록이면 빈 목록을 돌려준다.
- `_conclusion_problems()`도 같은 목록을 다시 썼다. 이 "재검사"는 독립 검증이 아니었다.

그래서 평가기(또는 그 이후 경로)에서 미결 포지션이 빠지면 cutoff 안에 열린 포지션이 있어도 `PAPER_PASSED`가 기록됐다. 진입 시각을 cutoff 뒤로 바꾼 포지션(event_id도 맞춰 바꿈)도 같은 이유로 "표본 밖"이 되어 통과했다.

### 1.2 문제 B — 결론이 메모리 state만 믿었다

`step()`은 state를 읽어 메모리에서 고치고, `_verdict` → `_conclude`를 거친 뒤 **마지막에** state를 저장한다. `_conclude(…, st)`는 넘겨받은 메모리 state로 검사·앵커를 만들었다. 그 사이에 디스크의 `state.json`이 바뀌면(외부 수정, 겹친 실행) 변경을 보지 못한 채 결론을 기록하고, 이어지는 `_save_state`가 바뀐 파일을 메모리 사본으로 **덮어써** 흔적도 사라졌다. step에는 계획 단위 잠금이 없었다(`runner.py`의 `state/run.lock`은 연구 사이클용이고 `ForwardPaper.step`을 부르는 상위 실행 단계는 저장소에 없다).

## 2. 재현 (수정 전 코드 `34f0e9f`)

`run/discovery/tests/test_paper_forward_completeness_20261111.py`의 `TestReproduction0077` 4개를 먼저 작성해 `34f0e9f`의 `paper_forward.py`로 실행했다. **4개 모두 실패**(=결함 재현):

| 테스트 | 수정 전 결과 |
|---|---|
| `test_repro_A_empty_open_positions_concludes_passed` — 평가기의 `_open_positions`가 `[]`를 반환(결함 주입), 70일 00:15 진입 포지션이 00:45에 열려 있고 cutoff 00:30 | step이 `PAPER_PASSED` 기록, 사유 `[]` |
| `test_repro_A_direct_verdict_and_conclude` — 같은 상황에서 `_verdict`, `_conclude` 직접 호출 | `_verdict`가 PASS |
| `test_repro_A_entry_moved_past_the_cutoff` — 미결 포지션 진입 시각을 30분 뒤로(이름도 맞춤) | `_verdict` = `PAPER_PASSED` |
| `test_repro_B_state_changed_before_conclude_is_overwritten` — `_verdict`가 final을 낸 직후 디스크 `state.json`의 funding head seq 변경 | `PAPER_PASSED` 기록, 변경된 파일은 메모리 사본으로 덮어씀 |

수정 후 4개 모두 통과한다.

## 3. 결정

### 3.1 독립 미결 포지션 재구성 (`COMPLETENESS_POLICY = "paper-forward-completeness-v1"`)

`ForwardPaper._reconstruct_positions(plan, st)` → `_replay_from_disk` → `_replay_positions`(순수 함수):

- **입력은 모두 디스크와 등록 정보**: 등록 스펙(현재 스펙의 sha256이 계획의 `spec_sha256`과 다르면 재구성 거부), 계획의 비용 모델·`order_value`·horizon·청산 규칙(`stop_exit_rule`), 워밍업 파일(sha256 검사), 해시 체인 봉 로그, 해시 체인 펀딩 로그, 거래 원장. **평가기 결과(`result`)는 읽지 않는다.** `run_event_study`, `_open_positions`, `_evaluate`를 쓰지 않는다(이 셋을 막아도 재구성이 같은 포지션을 찾는 테스트로 고정).
- 신호마다 하나로 분류한다: 청산 거래(원장에 있어야 함 — 방향·청산 시각·인덱스 비교), 규칙상 거부(참여율/비용 데이터/펀딩 미커버 — 비용 모델과 `FundingIndex`로 직접 판정), 데이터 공백 제외(포지션은 점유하지만 원장 밖), 겹침, **미결**(마지막 채택 청산 뒤 첫 신호 중 보유 기간이 관찰 봉을 넘는 것).
- 데이터 공백은 state가 아니라 봉 로그에서 다시 계산하고, state의 `gaps`·`last_ts`·`bars`와 다르면 문제로 보고한다.
- 탐지 종류: `OPEN_POSITION_MISSING`(누락), `OPEN_POSITION_UNEXPECTED`(추가), `OPEN_POSITION_DUPLICATE`(중복), `OPEN_POSITION_MISMATCH`(event_id·symbol·side·entry_ts·decision_ts·planned_exit_ts·status 중 불일치), `OPEN_POSITION_RECORDED_CLOSED`(열린 포지션이 원장에 청산으로), `CLOSED_TRADE_MISSING`(규칙상 청산된 거래가 원장에 없음 — 평가기와 원장에서 함께 빠져도 잡힘), `LEDGER_TRADE_NOT_REPLAYED`/`…_REFUSED_BY_RULES`/`…_EXCLUDED_BY_RULES`/`…_MISMATCH`, `STATE_BAR_LOG_MISMATCH`, `STATE_GAPS_MISMATCH`.
- 비교는 `_position_problems()` 끝에 붙었으므로 기존 세 지점(step의 평가 직후 — ERROR 고정, `_verdict`, 결론 게이트)에서 모두 돈다. 같은 입력 파일(sha256 키)이면 한 step 안에서 재구성은 한 번만 계산한다(메모). 파일이 바뀌면 다시 계산한다.

### 3.2 cutoff 이전·이후 구분

- 판정 대기(`_pending_all`)는 평가기 목록과 **재구성 목록의 합집합**에서 cutoff 이하 진입을 고른다. 빈 평가기 목록이 재구성된 포지션을 가릴 수 없다.
- cutoff 이후 진입 포지션은 기존대로 표본 밖(`open_after_bound`)이지만 **정합성 비교에서는 빠지지 않는다**: 평가기가 cutoff 이후 포지션만 빠뜨려도 `PAPER_ERROR`.

### 3.3 최종 결론 검증

`_conclusion_problems()`는 이제 디스크 state를 읽어 쓴다(아래 3.4). 그 위에서 원장 청산 거래, 재구성 미결 포지션, 평가기 미결 포지션, cutoff 이하 미결 존재 여부, 지표·표본 경계 재계산, 펀딩·증거 무결성(ADR 0073/0074 그대로)을 다시 확인한다. 하나라도 어긋나면 기록을 거부하고, step에서는 기존과 같이 `PAPER_BLOCKED_DATA`(`CONCLUSION_REFUSED: …`, 비최종)가 된다. 평가 기록의 `positions`에 `completeness`와 재구성 결과 `open_replayed`, 앵커에 `state_fields_sha256`이 추가됐다. `verify_conclusion()`도 앵커된 state 필드와 재구성(표본 안 미결 없음)을 다시 검사한다.

### 3.4 state 최신성·원자성

조사한 저장 순서(수정 전): 읽기 → 메모리 수정(봉·펀딩 로그 append는 즉시 디스크) → `_verdict` → cutoff 메모리 기록 → `_conclude`(평가 기록 append, 상태 변경, 피드백 append) → `concluded=True` → `_save_state` → steps.jsonl. 단순히 결론 때 디스크 state를 읽으면 이번 step의 수정(새 `last_ts`, cutoff, head)이 빠진 이전 state를 쓰게 된다. 그래서:

1. **계획 단위 잠금**: `step()`은 `forward/<plan>/step.lock`에 비차단 `flock`을 잡는다. 이미 잡혀 있으면 아무것도 읽거나 쓰지 않고 `STEP_IN_PROGRESS`(verdict `PAPER_BLOCKED_DATA`, 비최종)를 돌려준다. 프로세스가 죽으면 커널이 풀어 주므로 낡은 잠금이 남지 않는다. 상위 잠금이 없음을 확인하고 추가했다.
2. **로드 시 해시**: state 바이트의 sha256(`base_sha`)을 기억한다.
3. **결론 전 스냅샷(CAS)**: final 판정이면 먼저 이번 step의 state를 저장한다. 디스크가 `base_sha`와 다르면 저장하지 않고 거부한다(`STATE_CHANGED_DURING_STEP`, 바뀐 파일은 증거로 그대로 둔다). 저장 후 다시 읽어 같은지 확인하고 그 sha256(`snapshot_sha`)을 넘긴다.
4. **결론 게이트**: `_conclude`는 디스크 state를 다시 읽어 (a) sha256이 `snapshot_sha`와 같은지, (b) 메모리 state와 앵커 필드(`status, last_ts, bars, gaps, missing_bars, last_funding_ts, quality_head, funding_head, funding_status, events_logged, evaluation_cutoff.ts`)가 같은지 확인하고, 이후 모든 검사를 **디스크 state**로 한다. 평가 기록 append 직전에 sha256을 한 번 더 확인한다.
5. **마지막 저장도 CAS**: 결론이 없는 step도 마지막 저장 때 디스크가 바뀌었으면 덮어쓰지 않고 `PAPER_ERROR`(`STATE_CHANGED_DURING_STEP`)를 돌려준다.
6. **중단·재개**:
   - 스냅샷 전 중단: 이전과 같음(로그가 state보다 앞서면 기존 복구).
   - 스냅샷 후·기록 전 중단: 디스크에 cutoff가 있는 미결론 state가 남고, 다음 step이 다시 판정해 한 번 기록한다.
   - 기록 후·state 저장 전 중단(또는 상태 변경·피드백 전): 다음 step은 **새 봉을 관찰하기 전에** 기존 평가 기록을 디스크 증거와 대조(`_record_problems`: 앵커·지표·판정·재구성)해 맞으면 상태·피드백·`concluded`만 마무리하고(`resumed_conclusion`), 두 번째 기록은 만들지 않는다. 맞지 않으면 `PAPER_ERROR`(`EVALUATION_RECORD_CONFLICT`), 아무것도 바꾸지 않는다. 피드백 행에 `plan_id`를 넣어 한 계획당 한 번만 쓴다.
   - `record_id`/`record_hash`/평가 기록 체인은 그대로 `pipeline._append_eval`이 만든다.

## 4. 추가 테스트 (`test_paper_forward_completeness_20261111.py`)

- 재현 4개: `TestReproduction0077`(2절).
- 미결 포지션 완전성 `TestOpenPositionCompleteness`: 빈 목록(step 경로 — ERROR 고정·기록 없음, 직접 `_verdict`/`_conclusion_problems`/`_conclude`), event_id 변경, 진입 시각 ±변경, decision/planned_exit/side/status 변경, 원장에 청산으로 잘못 기록, 추가 포지션, 아무것도 안 열렸는데 열린 것으로 보고, 원장·평가기 양쪽에서 청산 거래 누락, 정상 청산 시 PASS 유지(+기록 필드, `verify_conclusion` = []), 대기 후 정상 결론, 평가기 경로를 막아도 재구성 동일, 입력 파일 변경 시 재계산, 순수 replay(거부 신호 뒤 미결, 원장 누락/초과), 비교 함수 종류별.
- 두 심볼 `TestTwoSymbols`(저장소 최초의 다중 심볼 전체 경로 픽스처: 같은 합성 봉, ETH만 70일 00:30까지 전달): **재현 테스트 B** 서로 다른 시각(70일 00:15 ETH, 71일 00:15 BTC) 미결 2개 중 하나만 남기면 나머지를 `OPEN_POSITION_MISSING`으로 지목(양방향, `_verdict`와 결론 게이트), cutoff 이전(ETH, 대기)·이후(BTC, 표본 밖이지만 비교 대상) 동시 존재, 둘 다 누락 시 step `PAPER_ERROR`.
- 최종 상태 일관성 `TestFinalStateConsistency`: `_verdict` 이후 state 변경(임의 필드, cutoff, last_ts, events_logged, funding head) → `CONCLUSION_REFUSED: STATE_CHANGED_DURING_STEP`, 기록 없음, 변경 파일 보존; 직접 `_conclude`에서 메모리/디스크 불일치 4종; 잘못된 snapshot sha; 정상 저장 후 결론(+결론 후 앵커 필드 변경을 `verify_conclusion`이 탐지); 비최종 step 중 state 변경은 덮어쓰지 않음; 기록 직전 중단 → 재개 시 1회 기록; 기록 후 state 저장 전 중단 → 재개 시 마무리, 기록 1개·피드백 1개; 기록과 상태 변경 사이 중단; 재개 시 원장이 바뀌었으면 `EVALUATION_RECORD_CONFLICT`; 잠금이 잡힌 동안의 step; 두 엔진·두 스레드 동시 step → 하나만 진행, 기록 1개.

## 5. ADR 0073~0076 회귀

기존 테스트 파일은 수정하지 않았다(기대값 변경·삭제 없음). 펀딩 coverage boundary, settlement schedule, `SCHEDULE_UNKNOWN`, 불완전 펀딩 차단, 펀딩·품질 해시 체인, 워밍업 sha256, state head, 원장–평가기 정합성, 미결 대기, cutoff 주기 독립성, max_days 상한, `OBSERVATION_WINDOW_NOT_COVERED`, `DATA_LAG`, `verify_conclusion`은 모두 그대로 돌며, 이 ADR의 검사는 그 뒤에 **추가**된다. 통계 기준·PASS 기준 불변. 실행 결과는 PR 본문과 최종 보고서에 기록한다.

## 6. 확인되지 않은 제한 사항

- **공유 부품**: 재구성은 평가기와 다른 걷기(walk)·장부 대조 코드이지만, 신호 계산(`build_spec_aux`/`build_spec_signal`), 청산 규칙(`stop_exit_rule`), 비용 모델(`round_trip_bp`), `FundingIndex`는 같은 모듈을 쓴다(등록된 "규칙" 자체이므로). 이 부품의 결함은 양쪽이 공유한다.
- 재구성은 평가기와 같은 수준의 계산을 한 번 더 하므로 step 비용이 대략 두 배가 된다(같은 입력이면 step당 1회로 메모).
- 잠금은 POSIX `flock`이다. 같은 계획 디렉터리를 NFS 등 flock이 보장되지 않는 파일시스템에서 여러 호스트가 함께 쓰는 경우는 보장하지 않는다(CAS가 마지막 방어선).
- 디스크의 모든 파일(state, 로그, 원장, 평가 기록)을 일관되게 다시 쓰는 공격은 여전히 외부 앵커 없이는 막을 수 없다(ADR 0074/0076과 같은 한계).
- ADR 0076 이전(앵커 없는) 평가 기록이 남은 미결론 state는 재개 시 `EVALUATION_RECORD_CONFLICT`로 막힌다(fail-closed). 실제로 그런 계획은 없다(PAPER_TEST_READY 전략 없음).
- 다중 심볼 픽스처는 같은 합성 봉을 두 심볼에 쓰고 스펙의 심볼 목록을 테스트에서 바꾼 것이다(검증·잠금 단계를 다중 심볼로 거친 픽스처는 아님).
