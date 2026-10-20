# ADR 0010 — legacy 잠금 TEST 비용 게이트, 다종목 잠금 창 독립성, 실행 모드 표시

- 상태: Accepted (PR #9 병합, 2026-10-17)
- 날짜: 2026-10-17
- 범위: `run/discovery/runner.py`, `run/discovery/pipeline.py`, `run/discovery/run_cycle.sh`, `run/discovery/tests/test_reaudit_20261017.py`(신규), `tests/test_residual_fixes.py`, `tests/test_final_fdr_context.py`, `run/discovery/README.md`, `run/discovery/STATISTICS_AND_STATES.md` §9, `run/audit/STATUS_CURRENT_20261017.md`(신규)
- 선행: [ADR 0009](0009-locked-test-requires-verified-cost.md)
- 사용자 결정: 동동, 2026-10-17. 사전 확인 보고서의 권장안대로 진행하기로 했다. 기본 모드는 legacy로 두고 경고만 추가한다. 겹치는 다종목 후보는 잠금 단계에서 차단한다.

## 맥락
1. ADR 0009의 비용 게이트는 evolution 경로(`pipeline.run_locked_campaign`)에만 들어갔다. legacy 경로(`runner._locked_test`)는 비용 검증을 확인하지 않고 잠금 창을 예약하고, 데이터를 읽고, 소비했다. 그런데 기본 실행 모드는 legacy다. 새 테스트 `test_unverified_cost_never_reserves_or_reads_a_window`가 `a2f63c0`에서 실패하는 것으로 확인했다. 실데이터에서는 legacy 생존자가 0명이었으므로, 실제로 쓰인 창은 없다.
2. 선택 단계(DEV·검증)가 읽는 구간은 종목마다 그 종목의 잠금 창 전날에 끝난다. ETH 선택 구간은 2021-01-01부터 2026-04-27까지이므로, BTC 잠금 창(2024-03-25~2024-06-18)을 포함한다. BTC와 ETH는 시장 전체 충격을 공유한다. 따라서 BTC+ETH 후보는 BTC 잠금 창 기간의 정보를 보고 선택된 셈이고, 그 BTC 잠금 TEST는 독립적인 검정이 아니다. 이전 감사(2026-10-17 §4)는 "단일 자산 특징이라 영향 없음"이라고 적었다. 하지만 legacy 후보는 전부, evolution 명세는 약 70%가 두 종목 후보다.
3. `run_cycle.sh`는 모드를 지정하지 않으면 조용히 legacy를 실행한다. 최근 실데이터 실행은 evolve였다. 결과 줄(`CYCLE_OK batch=N`)만으로는 어느 엔진이 돌았는지 알 수 없었다.

## 결정
- **legacy 비용 게이트.** `Runner.lock_entry_status`가 창 기록을 보기 전에 `cost.verified_for(종목, 주문 금액)`를 먼저 확인한다.
  - 통과하지 못하면 사유는 `COST_NOT_VERIFIED`다. 후보는 `AWAITING_FDR`로 남고, final 기록·예약·잠금 데이터 읽기는 하지 않는다.
  - `_locked_test`도 같은 확인을 한 번 더 한다(이중 방어).
  - 비용이 VERIFIED가 되면 비용 identity가 바뀌므로 컨텍스트도 바뀐다. 그러면 새 컨텍스트에서 DEV부터 다시 거친다(ADR 0009와 같음).
- **다종목 독립성.** `runner.locked_independence_issues(종목들)`를 새로 두었다. 한 종목의 잠금 창이 같은 후보의 다른 종목 선택 구간과 겹치면 그 후보는 잠금 TEST에 들어가지 않는다. 이 검사는 비용 게이트 바로 다음, 창 조회보다 먼저 한다.
  - legacy: final `DATA_MISSING`, 메모 `LOCKED_WINDOW_NOT_INDEPENDENT`. 데이터를 새로 받아도 해결되지 않으므로 재검증 큐에 넣지 않는다.
  - evolution: `BLOCKED_STATISTICS`, stage `locked`.
  - 단일 종목 후보는 영향을 받지 않는다.
- **잠금 창은 바꾸지 않는다.** `LOCKED_TEST_PERIODS`를 다시 설계하면 사전등록과 기존 사용 기록의 의미가 바뀐다. 그래서 창을 고치지 않고 해당 후보를 차단한다.
- **모드 표시.** 기본값(legacy)은 유지한다.
  - `NEW2_DISC_MODE`를 지정하지 않으면 stderr에 `CYCLE_WARNING`을 남긴다.
  - 모든 결과 줄과 감사 로그에 `mode=`를 붙인다.
  - `runner.py`는 `RUN_MODE`를 출력하고 `--help`를 지원한다.
  - README에 현재 상태 파일과 역사 기록을 구분한 표를 두었다. v1 파일은 지우거나 옮기지 않았다.
- 기준값(`promotion_v2.json`, `cost_verification_v1.json`, `paper_forward_v1.json`, 추정 기준 v2)은 하나도 바꾸지 않았다.

## 결과
- 어느 모드로 돌려도 미검증 비용으로는 잠금 창이 소모되지 않는다.
- BTC+ETH 후보는 현재 잠금 창 배치로는 최종 판정을 받을 수 없다. 독립적인 판정이 필요하면 단일 종목 후보로 다시 사전등록하거나, 새 잠금 데이터에 대한 별도 결정이 필요하다.
- 코드 해시가 바뀌므로 다음 실행은 새 컨텍스트가 된다. 이전 결과는 재사용하지 않는다(기존 규칙).
- 기존 테스트 11개(`TestLockLifecycle` 4개, `TestLockEntryPolicy` 6개, `TestRunnerFinalization` 1개)는 창 기록 처리를 확인하는 테스트다. 이 테스트들은 이제 test-only VERIFIED 비용과 독립성 검사 해제(`_fake_runner_cls(open_lock_gates=True)`)로 돌린다. 게이트가 닫힌 동작은 새 테스트가 확인한다.
- 남은 위험: evolution 탐색은 두 종목의 DEV 결과로 변형을 고른다. 그래서 ETH DEV 결과(BTC 잠금 기간 포함)가 단일 BTC 명세의 선택에 간접적으로 영향을 줄 수 있다. 이번 검사는 이 경로를 막지 않는다. `STATISTICS_AND_STATES.md` §9.1에 남은 위험으로 적었다.
