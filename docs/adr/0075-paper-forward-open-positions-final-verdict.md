# ADR 0075 — Paper Forward 미결 포지션과 최종 판정 무결성: 관찰 끝 미결 포지션 무시, 거래 원장 중복 집계, 결론 단계의 이전 판정 신뢰 (paper-forward-positions-v1)

- 상태: Accepted
- 날짜: 2026-11-11
- 요청: 동동, 2026-11-11 "ADR 0075 — Paper Forward 미결 포지션(Open Position) 및 최종 PASS 판정 무결성 감사·수정"
- 관련: ADR 0074(paper-forward-integrity-v1), ADR 0073(paper-forward-funding-v1), ADR 0072(evaluator 창 끝 신호 분류), ADR 0006(forward paper)
- 시작 커밋: `80a687d` (main, PR #75 병합). 작업 시작 시 main HEAD도 `80a687d`였다.

## 1. 문제 발견 배경

`ForwardPaper._evaluate()`는 `open_positions`(진입했지만 청산 봉을 아직 관찰하지 못한 포지션)를 계산해 결과에 넣지만, `_verdict()`, `_conclusion_problems()`, `_conclude()`는 이 값을 읽지 않았다. 성과 지표(`metrics()`)는 `events.jsonl`(청산 완료 거래 원장)만 읽는다. 따라서 관찰을 멈춘 시점에 열린 포지션은 판정에서 단순히 빠졌다.

## 2. 수정 전 코드에서 실제로 확인된 결함

`run/discovery/tests/test_paper_forward_positions_20261111.py`의 `TestReproduction0075` 4개를 **수정 전 코드(`80a687d`)에 먼저** 돌렸고 4개 모두 실패했다(= 결함 재현).

| # | 결함 (수정 전 위치) | 수정 전 동작 |
|---|---|---|
| 1 | `_verdict()` / `_conclusion_problems()`가 `open_positions`를 보지 않음 | 70일째 00:15 진입 포지션이 열린 채(마지막 봉 00:45) 판정 → **`PAPER_PASSED`** 기록 |
| 2 | 1과 같은 경로 | 열린 포지션이 −30 %(낙폭 한도 2500 bp 초과)인데도 **`PAPER_PASSED`**. 청산 뒤에는 `PAPER_FAILED`(낙폭)가 맞는 결과(수정 후 테스트 `test_1b`로 확인) |
| 3 | `metrics()`는 `events.jsonl`의 줄을 그대로 셈, `_reconcile_trades()`는 event_id로 dict를 만들어 중복을 지움 | 원장 줄을 복제해도 오류 없음, 거래 수가 두 번 집계된 채 **`PAPER_PASSED`** |
| 4 | `_conclude()`가 `_verdict()`가 만든 `result["metrics"]`를 그대로 기록 | `_verdict()` 뒤 원장 기록을 지워도 이전 판정으로 **`PAPER_PASSED`** 기록 |

코드 읽기로 찾은 추가 결함(재현은 수정 후 단위 테스트로만 확인):

- 5. `_evaluate()`의 미결 포지션 탐지 루프가 마지막 청산 뒤 **첫 신호에서 `break`** 했다. 그 신호가 체결 거부(참여율·거래대금 없음)나 펀딩 누락으로 이벤트가 되지 못하면, 그 뒤의 실제 미결 포지션을 놓쳤다(ADR 0072가 evaluator에서 없앤 것과 같은 종류의 `break`).
- 6. `_reconcile_trades()`는 side·entry·exit·net만 비교한다. 원장의 `late`, `exec_status`, `fill_fraction`, `liquidated`, `cost_bp`를 고쳐도 탐지되지 않았다(`late`는 실시간 피드에서 집계 여부를 바꾼다).
- 7. 원장 기록을 지우면 다음 단계의 `_reconcile_trades()`가 조용히 다시 추가했다(삭제 자체가 기록되지 않음).
- 8. `_verdict()`가 `result.setdefault("metrics", ...)`를 써서, 같은 `result`로 다시 부르면 이전 지표가 남았다.

### 포지션 생명주기 (실제 구현 기준)

| 상황 | 수정 전 처리 | 수정 후 |
|---|---|---|
| 생성 | `run_event_study()`가 결정 봉 t의 신호로 t+1 시가 진입. 보유 기간(`entry + horizon`)이 관찰 봉 안에 들어와야 이벤트가 됨. 들어오지 않으면 `truncated_end_of_window`로 세기만 함 | 같음(evaluator 미변경) |
| 보유 중 | 상태 파일에 포지션 기록 없음. 매 단계 전체 재계산으로 `open_positions`만 결과·steps.jsonl에 나옴(수량은 없음, 방향·진입 시각·근사 평가손익) | 같음 + `event_id`, `entry_index`, `planned_exit_ts`, `bars_held` 추가. 평가손익은 정보용이며 판정에 쓰지 않음 |
| 청산 | 청산 봉이 관찰되면 이벤트가 되고 `apply_execution()` 뒤 `events.jsonl`에 한 번 기록 | 같음 + 원장 정합성 검사 |
| 부분 청산 | 모델에 없음. `PARTIAL`은 **진입** 부분 체결(`fill_fraction < 1`)이고 체결된 수량 전체가 한 번에 청산됨 | 같음. 상태와 체결률 조합을 검사(`FILLED`=1, `PARTIAL`=(0,1), `LIQUIDATED`=(0,1], 거부=0) |
| 평가 종료 시 미결 | **무시하고 판정** | 대기(아래 정책) |
| 중복 등록·청산 | 탐지 안 됨(원장 중복은 이중 집계) | `DUPLICATE_CLOSE`, `DUPLICATE_POSITION`(같은 심볼 보유 기간 겹침, 미결 2개 이상) → 오류 |
| 기록 누락·손상 | 누락은 조용히 재추가, 비정상 값 검사 없음 | state의 `events_logged`보다 짧으면 `TRADE_LOG_TRUNCATED`, 값·평가기 불일치는 `POSITION_INTEGRITY` |
| 평가 중 예외 | `EvidenceIntegrityError`, `CausalityViolation` 외의 예외는 `step()` 밖으로 전파, state 미저장 | 같음(테스트로 확인: state 불변, 기록 없음, 다음 단계 정상 재개) |

## 3. 미결 포지션의 정상 상태와 비정상 상태

**정상 미결**: 보유 기간이 아직 끝나지 않은 포지션. 결과에 `status: "OPEN"`으로 나오고, 진입 시각이 관찰 범위 안이며, 마지막 관찰 봉에서 유효한(유한한) 근사 평가가 있고, 원장에 청산 기록이 없고, 마지막 청산 뒤에 진입했고, 심볼당 하나다.

**비정상(무결성 오류)**: 원장 읽기 실패, 심볼·방향·시각 누락 또는 비정수, 15분 격자 밖, 시작 전 진입, 청산 시각이 마지막 관찰 봉 뒤(청산 기록이 있는데 실제로는 열려 있음), 체결 상태와 체결률 불일치(0 이하·음수 포함), 순손익·비용이 유한하지 않음, 청산 플래그 불일치, 같은 event_id 중복, 보유 기간 겹침, 평가기 거래와 원장 불일치(누락·초과·필드 차이), 원장이 state 기록 수보다 짧음, 미결 포지션 평가 가격·시각 누락(`MARK_MISSING`), 같은 포지션이 미결과 청산에 동시에 있음, 평가기가 거래·미결 목록을 내지 않음(`POSITIONS_NOT_EVALUATED`), state의 cutoff가 관찰 범위 밖이거나 그 시점에 중단 규칙이 성립하지 않음.

## 4. 최종 판정 정책 (`POSITION_POLICY = "paper-forward-positions-v1"`)

현재 설계 확인: Paper Forward의 판정은 **청산 완료 거래의 실행 반영 순수익**(비용·펀딩·체결·청산 포함)에 대한 통계다. 근사 평가손익은 비용·펀딩·체결을 모르는 총수익이라 같은 척도가 아니다. 강제 청산은 전략의 보유 기간을 바꾸므로 다른 실험이 된다. 그래서 둘 다 하지 않고 **기다린다**.

1. 중단 규칙(`min_days`+`min_trades`, 또는 `max_days`)이 처음 성립한 단계의 마지막 관찰 봉(모든 심볼 공통 최소 시각)을 **평가 cutoff**로 state에 기록한다(`evaluation_cutoff`, 이후 바꾸지 않음).
2. 판정 표본 = 진입 시각이 `[start, cutoff]`인 청산 완료 거래. cutoff 뒤 진입 거래는 표본 밖(`trades_entered_after_bound`로 기록).
3. cutoff까지 진입한 포지션이 하나라도 열려 있으면 판정은 `PAPER_OBSERVING`(비최종, 상태 `PAPER_TESTING` 유지), 사유 `OPEN_POSITIONS_PENDING`. 모든 미결 포지션에 같은 규칙. 청산 봉과 그 펀딩이 관찰되면 그 거래가 원장에 들어가고 그때 판정한다. 보유 기간은 유한(`horizon` 봉)이라 데이터가 들어오는 한 대기는 끝난다.
4. 새 판정 상태는 만들지 않았다. 기존 `PAPER_OBSERVING`(대기), `PAPER_ERROR`(무결성), `PAPER_BLOCKED_DATA`(데이터·결론 거부), `PAPER_INCONCLUSIVE`, `PAPER_PASSED`, `PAPER_FAILED`를 그대로 쓴다. `INCONCLUSIVE`도 같은 대기 규칙을 따른다.
5. `PAPER_PASSED`가 보장하는 것: `[start, cutoff]`에 진입한 모든 거래가 청산까지 관찰되어 계획의 비용·펀딩·체결 규칙으로 실현되었고, 미실현 손익은 판정에 하나도 들어가지 않았으며, 판정에 쓴 원장은 결론을 기록하는 순간 디스크에 있는 원장과 같고, ADR 0073/0074의 펀딩·증거 검사를 모두 통과했다.

## 5. 수정한 코드 및 함수 (`run/discovery/paper_forward.py`)

- 상수 `POSITION_POLICY`, `_EXEC_FILL`, `_TRADE_KEYS`; 모듈 문서에 정책 추가.
- `_open_positions()` (신규, 정적): evaluator와 같은 규칙으로 미결 포지션을 찾는다. 들어맞는데 이벤트가 아닌 신호는 건너뛰고 계속 찾는다(`break` 제거). 가격이 유효하지 않으면 평가값을 `None`으로 둔다(임의 값으로 대체하지 않음).
- `_trade_log_len()`, `_sample_bound()`, `_pending()` (신규).
- `_position_problems()` (신규): 위 3절의 모든 검사. 매번 디스크에서 원장을 새로 읽는다.
- `step()`: 평가 전 원장 길이 < `events_logged`이면 `TRADE_LOG_TRUNCATED`(ERROR). 평가 후 `events_logged` 갱신, `_position_problems()`가 있으면 `POSITION_INTEGRITY`(ERROR, 고정). 판정이 cutoff를 내면 state에 처음 한 번만 기록.
- `metrics()`: 표본을 `entry_ts <= 표본 경계`로 제한, `days_observed`를 경계 기준으로, `sample_bound_ts`, `evaluation_cutoff_ts`, `trades_entered_after_bound` 추가.
- `_verdict()`: 펀딩·증거 검사 뒤 `_position_problems()` → `PAPER_ERROR`; 지표는 항상 이번 호출 값(`setdefault` 제거); 기록된 cutoff에서 규칙이 성립하지 않으면 `EVALUATION_CUTOFF_INVALID`; 미결 대기 → `PAPER_OBSERVING`; 판정 기준은 `_decide()`로 분리.
- `_decide()` (신규, 정적): 사전 등록 기준 판정. `_verdict()`와 결론 게이트가 같은 함수를 쓴다(기준 자체는 한 글자도 바꾸지 않음).
- `_conclusion_problems(…, verdict=None)`: 기존 ADR 0073/0074 검사 뒤에 `_position_problems()`(디스크 재읽기), cutoff 존재, cutoff까지의 미결 없음, 원장에서 지표 재계산 후 판정 때 지표와 비교, 재계산 지표로 `_decide()`한 결과가 넘겨받은 판정과 같은지.
- `_conclude()`: 위 게이트에 판정을 넘기고, 평가 기록에 `positions`(정책, cutoff, 표본 거래 수, cutoff 뒤 미결) 추가.
- `_state_problems()`: 선택 키 `events_logged`(음이 아닌 정수), `evaluation_cutoff`(시작 이후, 격자 위, 모든 심볼의 마지막 관찰 봉 이하) 검사. ADR 0075 이전 state(두 키 없음)는 유효.

`evaluator.py`, `execution_sim.py`, 전략 생성기, 백테스트 엔진, 통계 기준(`config/paper_forward_v1.json`)은 바꾸지 않았다.

## 6. 변경 전후 판정 흐름

수정 전: `step` → (증거·펀딩) → `_evaluate`(미결 계산만) → `_verdict`(청산 원장 지표 → 기준) → `_conclude`(펀딩·증거만 재검사, 이전 지표 기록).

수정 후: `step` → (증거·펀딩, 변경 없음) → 원장 길이 검사 → `_evaluate`(미결 탐지 수정) → 원장·평가기·미결 정합성(ERROR) → `_verdict`(정합성 재검사 → 표본 경계 지표 → `_decide` → 규칙 성립 시 cutoff, 미결 대기면 `PAPER_OBSERVING`) → cutoff 기록 → `_conclude` → `_conclusion_problems`(펀딩·증거 + 원장 재읽기·미결·지표 재계산·판정 일치) → 기록. 게이트가 거부하면 기존대로 `PAPER_BLOCKED_DATA`(`CONCLUSION_REFUSED`), 아무것도 기록하지 않음.

## 7. 추가한 테스트

신규 파일 `tests/test_paper_forward_positions_20261111.py` 37개(합성 데이터·임시 디렉터리·mock만):

- 재현 4(수정 전 실패).
- 정책 5: 지시서 테스트 1(미결 → 대기 → 청산 뒤 표본 +1로 PASS, cutoff 고정, 원장 1회 기록), 손실 미결이 실현되면 FAIL, 보유 중 여러 단계 미결 유지, 여러 미결 모두 같은 규칙, 거부 신호 뒤 미결 탐지(`break` 결함).
- 비정상 14: 중복 청산, 체결률 0·음수, 비유한 비용, 원장 플래그 불일치, 기록 삭제, 존재하지 않는 거래 기록, 관찰되지 않은 청산, 평가 가격·시각 누락 4종, 미결 중복, 미결이 청산에도 기록, 미평가, cutoff 위조 2종.
- 지시서 테스트 2(결론 직전 상태 변경) 9: 변경 없음 → 결론, 미결 추가·원장 중복·삭제·플래그 변경·지표 변조·판정 불일치·cutoff 없음 → 모두 `ConclusionRefused`, `step()` 경로에서 `_verdict`와 `_conclude` 사이 변조 → `PAPER_BLOCKED_DATA`.
- 예외·호환 5: 평가 중 예외(상태 불변, 기록 없음, 재개 시 정상), 지시서 테스트 3(전부 청산 → 즉시 PASS, 표본 = 전체 원장), 펀딩 공백 + 미결 → `PAPER_BLOCKED_DATA`(cutoff 미기록), 품질 로그 변조 + 미결 → `PAPER_ERROR`, ADR 0075 이전 state 호환.

기존 테스트 2개를 **보강**했다(삭제·완화 없음): `test_d6_source_error_blocks_the_step_and_is_retried`와 `test_full_path_boundary_8h_and_1h`는 재시도 단계의 마지막 봉(00:45)이 75일째 보유 기간 안이라 이제 `PAPER_OBSERVING`(`OPEN_POSITIONS_PENDING`)이 맞다. 그 사실을 단언한 뒤 청산과 다음 정산이 관찰된 시각으로 한 단계 더 진행해 원래 단언(`PAPER_PASSED`)을 그대로 확인한다.

## 8. 테스트 실행 결과

로컬에서 `.github/workflows/research-ci.yml`의 단계를 그대로 실행했다(Python 3.11.x와 3.13.x 가상환경, numpy 2.2.6·pandas 2.2.3·ruff 0.16.8).

| 단계 | 3.11 | 3.13 |
|---|---|---|
| compile, independence, ruff(E9/F63/F7/F82), ruff F 전체, bash -n, config, ledger_check | 통과 | 통과 |
| `discovery/tests` 전체 | 955개 통과(기존 918 + 신규 37), 실패 0 | 955개 통과, 실패 0 |
| 루트 연구 테스트 `run/test_*.py` | 152개 통과 | 152개 통과 |
| legacy funding_cost | 1개 통과 | 1개 통과 |
| run/*.py 컴파일·ruff, 인벤토리 최신, 소스 감사표, 감사 출처표 | 통과 | 통과 |
| bun 트랜스파일, 패키지 manifest | 통과(1회, 버전 무관) | — |
| ShellCheck | 실행 못함(이 환경에 shellcheck 없음; `run_cycle.sh`는 변경 없음) | 같음 |

GitHub Actions 결과는 별도로 기록한다(11-09 이후 계정 수준 실패: 러너 미할당). 로컬 통과를 Actions 통과로 표현하지 않는다.

수정 전 코드(`80a687d`)에서 재현 테스트 4개 실패, 수정 후 통과. 수정 도중 처음 돌린 기존 테스트 14개가 실패했는데, 원인은 (a) 결론 게이트가 판정 때 지표(cutoff 기록 전)와 재계산 지표(cutoff 기록 후)를 `evaluation_cutoff_ts` 필드까지 비교한 내 코드 버그(12개, 필드 제외로 수정), (b) 정책 변경으로 재시도 단계가 미결 대기가 된 기존 테스트 2개(위 보강)였다.

## 9. ADR 0073 / 0074와의 호환성

펀딩 확인(`funding_checked`, 커버리지 경계, 정산 주기, `SCHEDULE_UNKNOWN`, REST 계획 거부), 증거 로그 해시 체인·state head, 워밍업 sha256, legacy 계획 차단, `_conclusion_problems()`의 기존 검사는 코드 변경 없이 그대로 먼저 실행된다. 새 검사는 그 뒤에 덧붙였고 기존 검사를 건너뛰는 경로는 없다. 기존 0073/0074 테스트는 위 2개 보강 외에 수정 없이 통과한다. 미결이 없는 정상 데이터의 판정·지표·이벤트는 단일 심볼에서 변하지 않는다(`test_a7_results_equal_the_pre_fix_baseline` 통과).

## 10. 해결되지 않은 제한 사항

- `events.jsonl`은 해시 체인이 아니다. 삭제·잘림은 `events_logged`로, 내용 변경은 평가기 재계산 비교로 잡지만, state와 원장을 함께 고치는 경우는 막지 못한다(ADR 0074의 외부 앵커 부재와 같은 한계). `evaluation_cutoff`도 state에 있으므로 규칙이 성립하는 다른 시각으로 함께 옮기는 위조는 탐지되지 않는다(규칙이 성립하지 않는 시각·관찰 범위 밖은 탐지).
- 낙폭(`mdd_bp`)은 기존처럼 청산 거래 누적 기준이다. 보유 중 미실현 낙폭은 판정에 들어가지 않는다(대기 후 실현 손익으로만 반영).
- 미결 포지션의 평가손익은 총수익 근사(비용·펀딩·체결 미반영)이며 정보용이다. 진입 체결 거부 여부도 청산 때 결정되므로, 결국 거부될 신호도 그때까지 대기한다(최대 `horizon` 봉).
- 워밍업 중 진입해 시작 시점에 열려 있던 포지션은 관찰 대상이 아니다(기존과 같이 시작 전 진입은 제외).
- 다중 심볼 계획은 합성 픽스처가 단일 심볼(BTCUSDT)이라 전체 경로로 시험하지 못했다. 심볼별 마지막 관찰 시각이 다르면 표본 경계가 심볼별 마지막 관찰 시각 중 가장 이른 시각이 되어, 수정 전보다 앞선 심볼의 거래 일부가 표본에서 빠진다(더 보수적).
- 평가 중 일반 예외는 기존처럼 `step()` 밖으로 전파된다(PAPER_ERROR로 기록하지 않음). state는 저장되지 않고 결론도 나지 않는다.
