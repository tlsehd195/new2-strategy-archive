# ADR 0030 — 논문 그대로의 세 가지를 초안·새 버전으로 채택 (e-값 PrPl-EB, StepM bootstrap-t, PBO 0.05)

- 상태: Accepted (초안·새 버전 파일만. 효력 없음. 기본 설정과 동결된 사전등록은 그대로)
- 날짜: 2026-10-22
- 사용자 결정: 동동, 2026-10-22 ("일단 너가 추천하는 걸로 진행하는데 추천하는 이유도 설명")
- 선행: [ADR 0027](0027-paper-faithful-recheck-of-estimated-parts.md)(원문 대조와 합성 측정), [ADR 0025](0025-walkforward-evalue-prereg-v3-draft.md)(e-값 v3 초안)
- 범위: `run/audit/evidence_evalue_v3_20261022/walkforward_evalue_v3_DRAFT.json`, `run/discovery/config/promotion_v4.json`(신규), `run/audit/evidence_paper_faithful_20261022/independent_window_v2_DRAFT.json`(신규), `run/discovery/pipeline.py`·`independent_window.py`(설정에서 `studentize`를 읽음, 없으면 "fixed"), `engine_lib.py`·`ops.py`(새 설정 파일 등록), `run/discovery/tests/test_promo_v4_20261022.py`(신규). 시장 데이터·검증·잠금 구간·IW1은 읽지 않았다.

## 결정과 추천 이유
1. **e-값 v3 초안의 베팅 = Waudby-Smith–Ramdas PrPl-EB** (2010.09686 v7 식 (15), α = 0.0091, λ 상한 0.75).
   - 논문 식 그대로다. 우리가 정한 정규화 상수(k0 = 3)가 없어진다.
   - 효과 크기의 사전값을 쓰지 않는다. 지난 구간의 샤프는 3~5배 부풀어 있었다(ADR 0021).
   - 1종 오류는 같은 틀(명제 3)로 지켜지고 합성에서 최대 0.03%(v3 α 0.0091)다. 검정력은 GARCH t4, 36블록에서 샤프 2 37.6% → 52.0%, 샤프 3 87.5% → 94.0%.
   - 초안의 V3를 DECIDED로 바꾸고 이전 규칙은 `bet_rule_previous`에 남겼다. 초안은 여전히 효력 없음.
2. **검증 게이트 StepM = bootstrap-t (새 버전 옵션)**.
   - RW2005 식 (11)·RSW2008 알고리즘 A.3 그대로 σ*를 재표본마다 다시 계산한다.
   - 합성 FWER(α 5%)가 모든 과정에서 명목에 가장 가깝다: 현행 5.25/6.0/5.6/11.4% → 4.1/5.5/4.75/7.1%. 엄격해지는 쪽이다. 대가는 대립 검정력이 조금 낮고(예: 37.3% → 34.6%) 계산이 느린 것이다.
   - `promotion_v4.json`의 `validation.studentize = "bootstrap_t"`. 기본 설정은 `promotion_v3.json` 그대로이고, 설정에 키가 없으면 "fixed"라서 기존 배치·동결 사전등록의 동작은 바뀌지 않는다.
   - IW1: 아직 열지 않았으므로 재동결 때 반영한다. 그때 쓸 `independent_window_v2_DRAFT.json`을 만들었다(v1과 다른 곳은 `test.studentize`·`test.method`뿐, v1의 sha256을 `supersedes`에 기록). 효력 없음.
3. **PBO 기준 0.5 → 0.05 (새 버전)**.
   - Bailey 외 PBO 논문: "PBO가 0.05보다 크면 기각하는 것이 관례". 우리 0.5는 그보다 열 배 느슨했다.
   - `promotion_v4.json`의 `robustness.max_pbo = 0.05`. 엄격해지는 쪽이다.

세 가지 모두 α를 느슨하게 하지 않는다(1은 같은 α, 2·3은 엄격해짐).

## 효력
- `DEFAULT_PROMOTION_CONFIG`는 여전히 `promotion_v3.json`이다. v4는 `status: DRAFT_NOT_DEFAULT`이며 누가 명시적으로 지정해야만 쓰인다.
- v2/v3 사전등록 초안, IW1 v1 동결본은 효력·내용이 그대로다(v3 초안은 베팅 문구만 바뀜).
- 테스트(`test_promo_v4_20261022.py`): v3가 기본값인지, v4와 v3의 차이가 위 두 키뿐인지, IW1 v2 초안이 v1과 `studentize`·`method`만 다르고 v1 sha256과 맞는지 확인한다.

## 한계
- 근거는 모두 합성 시뮬레이션이다(ADR 0027). 실제 암호화폐 손익에서의 오류율은 확인하지 못했다.
- PBO 0.05는 지금까지의 후보 대부분을 탈락시킬 수 있다. 이는 의도된 엄격화이다.
