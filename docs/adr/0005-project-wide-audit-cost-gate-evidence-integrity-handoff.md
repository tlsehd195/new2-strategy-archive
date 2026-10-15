# ADR 0005 — 전체 감사: 비용 검증 게이트 강화, 증거 무결성, 페이퍼 상태, NEW2 인계 아티팩트

- 상태: Proposed (PR #3, 머지 시 Accepted)
- 날짜: 2026-10-15
- 범위: `run/discovery/{pipeline,paper,autoloop,handoff}.py`, `config/promotion_v2.json`, `tests/test_pipeline.py`, `tests/test_ops_failures.py`
- 선행: [ADR 0003](0003-core-defects-data-defaults-lookahead-ledger.md), [ADR 0004](0004-auto-evolution-staged-validation-and-paper-review.md)

## 감사 결과 (Phase A)
- **PR 대조**: PR #3 브랜치는 PR #2(`final-residual-fixes`) 위에 쌓였고, PR #2는 PR #1(머지됨)을 포함한다. #2 이후 변경된 기존 파일의 삭제 줄을 확인한 결과 모두 의도된 대체였다(`iter_jsonl`의 조용한 건너뛰기 → `JsonlCorruption`, 단순 append → 격리/복구 경로). #1·#2의 테스트(202개)는 전체 스위트에서 계속 통과한다. 되돌려진 수정은 찾지 못했다.
- **run_cycle.sh**: runner/tee/tail 종료 코드를 따로 보존하며, 하위 실패를 성공으로 바꾸는 경로는 없다. 이번 점검에서 변경하지 않았다.
- **P0 발견 1 — 비용 게이트 약화**: `promotion_v1.json`의 `allow_unverified_cost_for_paper=true`는 미검증 비용 모델로도 `PAPER_TEST_READY`가 되게 했다. 새 지시문 7장(검증된 비용 모델 없이는 연구 후보로만)과 충돌한다. 레거시 `promotion.py`는 이미 막고 있었으나 진화 파이프라인은 아니었다.
- **P0 발견 2 — 증거 레코드 무결성**: 평가 원장은 읽을 때 `record_id`가 내용과 맞는지 확인하지 않았다. 변조된 줄이 승격 증거가 될 수 있었다.
- **P1 발견 — 페이퍼 결과가 상태에 반영되지 않음**: 페이퍼 판정은 주석일 뿐 상태 전이가 없었다.
- **P2 발견 — 인계 형식 부재**: 연구 후보를 NEW2로 넘기는 명시적 아티팩트가 없었다.

## 결정
1. **비용 게이트**: `PAPER_TEST_READY`는 검증된 비용 모델에서만 가능하다. 설정 스위치는 코드가 무시하고, `promotion_v2.json`(`promo-v2`)에서 키를 제거했다. 새 버전이므로 새 실험 컨텍스트가 만들어진다(과거 컨텍스트 결과는 재사용되지 않는다). 미검증이면 `BLOCKED_COST`와 `UNVERIFIED_COST_MODEL` 라벨이 붙고, 이는 연구 후보로만 남는다. 현재 `CostContext.futures_default()`는 `verified=False`이므로, **실데이터·실측 보정이 없는 한 실제 실행 경로에서는 어떤 전략도 `PAPER_TEST_READY`가 되지 않는다.** 이는 의도된 동작이다.
   - 테스트용 검증 비용(`verified=True, test_only=True`)은 합성 테스트에서만 쓰고, 운영 runner는 `test_only` 비용을 거부한다.
2. **증거 무결성**: 평가 원장을 읽을 때 `record_hash`를 다시 계산해 다르면 `EvidenceTampered`로 중단한다(fail-closed). 승격과 인계 모두 이 경로를 쓴다.
3. **페이퍼 상태**: `PAPER_TEST_READY → PAPER_TESTING → PAPER_PASSED | PAPER_FAILED`를 전이표에 추가했다. 이벤트가 부족하면 `PAPER_TESTING`에 남고 창은 소비된다. 데이터가 없으면 상태를 바꾸지 않아 창을 소비하지 않는다. 중단된 리플레이(상태 `PAPER_TESTING`, 실행 기록 없음)는 재개할 수 있다. `PAPER_PASSED`는 실거래 승인이 아니다.
4. **NEW2 인계** (`handoff.py`): `new2_research_handoff_v1` JSON 하나를 해시로 봉인해 쓴다. 명세, 계보, 컨텍스트 구성요소, 증거 ID, 비용 상태, 라벨, 실패 조건을 담는다. 승격 심사 기록과 평가 원장에서 증거를 다시 읽어 대조하며, 호출자가 넘긴 불리언은 믿지 않는다. 기존 파일은 덮어쓰지 않는다. `live_registration=false`, `requires_human_approval=true`로 고정이며 NEW2 코드를 import하거나 레지스트리에 등록하지 않는다.
5. **운영 실패 테스트**: 디스크 가득 참(ENOSPC)은 `FAILED`·종료 코드 1이고 잠금이 풀린다. 잠금 보유 중에는 종료 코드 3, 데이터가 없으면 4이며, 어느 경우에도 잠금 TEST 사용 기록이 생기지 않는다.

## 결과
- 새 테스트: 비용 게이트, 증거 변조, 페이퍼 상태, 인계 2개, 운영 실패 4개.
- 실데이터가 없어(`BLOCKED_DATA`) 실측 비용 보정과 실증 검증은 여전히 하지 못했다. 수익성은 `NOT_VALIDATED`이고, 비용 모델은 `UNVERIFIED_COST_MODEL`이다.
- 지시문 15장의 완료 조건 중 3(실데이터 품질 검증), 8의 실측 부분, 14(전체 테스트는 합성 기준)는 아직 충족되지 않았다. 완료로 표시하지 않는다.
- 미착수: 호가·체결 기반 비용 보정(데이터 필요), 부분 체결·주문 실패·강제 청산 모델, 페이퍼 지연·잔고 기록, 병렬 실행.
