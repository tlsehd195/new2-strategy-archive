# ADR 0084 — 과거 FNG 전략의 PAPER_PASSED를 집계에서 PASS·생존자로 세지 않는다 (라벨 UNVERIFIED)

- 상태: Accepted
- 날짜: 2026-11-17
- 요청: 동동, 2026-11-17 "전부 추천으로" (ADR 0079 후속 C-5, C-4 연관)
- 관련: ADR 0079(G-04 FNG_NOT_PINNED, C-4 handoff 거부)
- 시작 커밋: `13faade` (main). ADR 번호 0084는 열린 PR·main에서 미사용 확인.

## 1. 문제

ADR 0079 이후 새 FNG 전략의 Paper Forward 계획은 등록이 거부되고(FNG_NOT_PINNED), 기존 계획은 결론을 내지 못하며, handoff도 FNG 전략의 기록된 forward 판정을 내보내지 않는다(C-4). 그러나 **집계 보고** 쪽은 그대로였다.

- `pipeline.outcome_label`: 상태 `PAPER_PASSED` → `PASS`
- `evolution_report`: `SURVIVOR_STATUSES`에 `PAPER_PASSED`가 있어 `survivors`에 센다

따라서 FNG 원천이 고정되지 않은 채 과거에 기록된 `PAPER_PASSED`가 보고서에서 합격·생존자로 보였다.

## 2. 결정 (보고용 집계만 수정)

- 새 라벨 `UNVERIFIED`를 `OUTCOME_LABELS`에 추가한다(뜻: 저장된 상태는 PAPER_PASSED이나 forward 결론을 검증할 수 없음).
- `pipeline.forward_conclusion_verifiable(spec)`: 사양이 `data`에 `fng`를 읽으면, 또는 사양이 없거나 읽을 수 없으면(fail-closed) False. 판정은 `ForwardPaper._fng_refusal`과 같은 기준(`"fng" in spec["data"]`)이다.
- `outcome_label(..., spec=None)`: 상태가 `PAPER_PASSED`이고 결론을 검증할 수 없으면 `UNVERIFIED`. 다른 상태·라벨은 불변(`REVERIFY_PENDING`이 여전히 우선).
- `evolution_report`: 사양(`specs.jsonl`)을 읽어 `outcome_label`에 넘기고, `survivors`는 라벨이 `PASS`인 것만, 새 필드 `unverified_prior_conclusions`에 UNVERIFIED id를 담는다. 마크다운에도 한 줄 추가. 저장된 상태(`statuses`)는 그대로 보인다.
- handoff(`build_handoff`)는 C-4에서 이미 FNG 전략의 forward 판정과 근거 없는 PAPER_PASSED를 거부하므로 `paper_forward_verdict`에는 FNG 판정이 도달하지 않는다. 같은 기준(FNG)이라 추가 변경 없음.
- 판정 로직·게이트·임계값·저장 상태는 바꾸지 않는다. 기록을 지우거나 고치지 않는다.

## 3. 테스트 (먼저 작성, 수정 전 실패 확인)

`run/discovery/tests/test_fng_outcome_label_20261117.py` (6개; 수정 전 오류 7·실패 1): FNG PAPER_PASSED → UNVERIFIED, 일반 PAPER_PASSED → PASS, 사양 없음/읽기 불가 → UNVERIFIED, 다른 상태 불변, reverify 우선, 보고서의 survivors/unverified/statuses. 기존 단언은 바꾸지 않았다.

## 4. 한계

- `survivors` 수가 줄 수 있다(FNG PAPER_PASSED가 있던 상태 디렉터리). 의도한 변화이며 저장 상태는 불변.
- FNG 원천 고정(forward 피드의 FNG 소스)은 별도 사용자 결정 사항으로 남아 있다.
