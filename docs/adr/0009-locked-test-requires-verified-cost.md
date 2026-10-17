# ADR 0009 — 비용 모델이 검증되기 전에는 잠금 TEST를 열지 않는다

- 상태: Accepted (PR #8 병합, `a2f63c0`)
- 날짜: 2026-10-17
- 범위: `run/discovery/pipeline.py`(`run_locked_campaign`), `tests/test_locked_cost_gate.py`(신규), `tests/test_pipeline.py`, `tests/test_assumed_cost.py`, `tests/test_public_estimate.py`
- 선행: [ADR 0007](0007-assumed-cost-profile.md), [ADR 0008](0008-residual-audit-cost-estimate-lookahead-forward-integrity.md) "결정 대기"

## 맥락
잠금 TEST 창은 전략·종목마다 한 번만 쓸 수 있다. 이전 동작에서는 비용 모델이 미검증(UNVERIFIED, ASSUMED, ESTIMATED)이어도 검증 생존자가 생기면 잠금 TEST를 열었다. 그 결과는 승격 심사에서 항상 `BLOCKED_COST`가 된다. 이렇게 창을 써 버리면 나중에 비용이 VERIFIED가 되어도 그 전략은 잠금 TEST를 다시 받을 수 없다. ADR 0008에서 이 문제를 사용자 결정으로 남겼다. 사용자(동동)는 2026-10-17에 "닫아 두기"를 골랐다.

## 결정
- `run_locked_campaign`은 생존자마다 먼저 `cost.verified_for(종목, 주문 금액)`를 확인한다. 이 확인은 창 상태 조회, 예약, 데이터 읽기보다 앞선다.
- 확인을 통과하지 못하면 다음과 같이 처리한다.
  - 상태: `BLOCKED_COST`(stage `locked`, resume_to `VALIDATION_SURVIVOR`).
  - 사유: "cost not verified for this use (…) — locked window preserved, not touched".
  - 캠페인 사전등록, 잠금 예약, 잠금 데이터 읽기는 하지 않는다.
- 다음 경우도 VERIFIED지만 범위 밖이므로 같은 처리를 한다.
  - 검증 범위에 없는 종목.
  - 검증된 명목금액 범위 밖의 주문.
- 같은 상태로 기다리는 전략은 비용이 이 용도를 덮게 되면 다시 후보가 된다.
- 기준값을 하나도 바꾸지 않았다. 게이트를 설정 스위치로 두지 않고 코드 규칙으로 고정했다. 승격 비용 게이트가 옛 `allow_unverified_cost_for_paper` 스위치를 무시하는 것과 같은 방식이다.

## 결과
- 미검증 비용으로는 잠금 창이 소모되지 않는다. 지금까지 실데이터 생존자가 0명이었으므로 이미 쓰인 창도 없다.
- 비용 모델이 바뀌면 컨텍스트 ID가 바뀐다. 그래서 VERIFIED 비용을 쓰는 실행은 새 컨텍스트에서 DEV부터 다시 거친 뒤 잠금 TEST로 간다. 이전 컨텍스트의 생존 결과는 재사용하지 않는다(기존 규칙).
- 비용: 비용 검증 전에는 잠금 TEST 결과라는 정보를 얻지 못한다. 그 결과로는 어차피 승격할 수 없으므로, 창을 보존하는 쪽을 택했다.
- 기존 테스트 3개(UNVERIFIED, ASSUMED, ESTIMATED의 승격 차단)를 새 동작에 맞췄다. 이제는 잠금 창 사용 0, 상태 `BLOCKED_COST`(stage `locked`)를 확인하고, 승격 심사의 비용 라벨은 직접 호출해서 확인한다.
