# ADR 0087 — PBO 탐색 문맥을 심볼 집합별로 나눈다

- 상태: Accepted
- 날짜: 2026-11-17
- 요청: 동동, 2026-11-17 "전부 추천으로" (감사 3차 후속 (3): PBO는 심볼 집합별 탐색 문맥 분리로 간다)
- 관련: ADR 0006(관측하지 않은 날은 0 수익이 아님), ADR 0064(promo-v5), ADR 0078(D-02/D-06 전체 시행 수 DSR 게이트), ADR 0081(DSR 관측일), ADR 0083(PBO 관측 달력이 다르면 계산 불가)
- 시작 커밋: `13faade` (main, PR #79 병합). 작업 중 PR #84가 0084로 병합되고 열린 PR #85·#86이 0085·0086을 써서 0087을 쓴다.

## 1. 문제

ADR 0083 이후 `_pbo()`는 시행 집합(같은 실험 문맥의 `search_pool` DEV 시계열)의 관측 달력이 하나가 아니면 `TRIAL_CALENDARS_DIFFER`로 계산을 거부한다. 실제 잠금 구간은 BTCUSDT 2024, ETHUSDT 2026이라 BTC 단독·ETH 단독·BTC+ETH 전략의 DEV 달력은 서로 겹치지 않는다. 그래서 한 실험 문맥에 다른 심볼 집합 전략이 **하나라도** 섞이면 그 문맥의 **모든** 전략이 PBO 계산 불가(`BLOCKED_STATISTICS`)가 되어, 다중 심볼 전략을 탐색하는 순간 PBO 게이트를 쓸 수 없었다.

## 2. 재현 (수정 전 `13faade`)

`run/discovery/tests/test_pbo_symbol_set_context_20261117.py`를 먼저 작성해 수정 전 코드로 실행했다: 7개 중 6개 실패(오류 5: `_pbo()`가 전략 인자를 받지 않음, 실패 1: 실제 잠금 구간 전체 경로에서 BTC+ETH 9개 시행 그룹이 있어도 우위 전략의 PBO가 계산 불가). 나머지 1개(`test_dsr_gate_count_is_not_reduced_by_the_split`)는 DSR 시행 수를 지키는 보호 테스트로, 수정 전후 모두 통과해야 한다.

## 3. 결정

1. **PBO 시행 집합 = 전략의 탐색 문맥.** `robustness(sid)`와 재개 판단(`_blocked_now_computable`)은 `_pbo(sid)`를 쓴다. 시행 집합은 같은 실험 문맥(`context_id`)의 시행 집합(`_trial_series`, promo-v5에서는 `search_pool`) 중 **심볼 집합이 `sid`와 같은 것**이다.
2. **심볼 집합의 정의.** DEV 시계열 행의 심볼 키를 정렬한 튜플. `dev_evaluate`는 스펙의 모든 심볼에 데이터가 있을 때만 OK 시계열을 쓰므로 이 키는 스펙의 `symbols`와 같다(순서 무관). `sid`의 시계열이 없으면 스펙의 `symbols`를 쓴다.
3. **그룹 안 계산은 ADR 0083 그대로.** 그룹 안에서도 관측 달력이 다르면 `TRIAL_CALENDARS_DIFFER`, 그룹 시행 수가 `pbo_min_trials`(8) 미만이면 `TOO_FEW_TRIALS`로 계산 불가(fail-closed: PBO 검사 불합격, 다른 검사가 모두 통과면 `BLOCKED_STATISTICS`, 재개 가능). 본문은 `_pbo_of()`로 옮겼고 한 줄도 바꾸지 않았다.
4. **기록.** 실험 문맥에 심볼 집합이 둘 이상일 때만 결과에 `search_context` = {`basis`, `symbol_set`, `trials_in_experiment_context`, `symbol_sets_in_experiment_context`}를 붙이고, `BLOCKED_STATISTICS` 사유 끝에 `[symbol set BTCUSDT+ETHUSDT]`를 붙인다. 심볼 집합이 하나면 이 필드가 없고 결과 dict가 이전과 **완전히 같다**.
5. `_pbo()`(인자 없음)는 실험 문맥 전체로 계산하는 ADR 0083 동작 그대로 남겨 진단용으로 쓴다.

## 4. 다중검정 보정에 쓰는 시행 수 (느슨해지지 않게)

분리로 그룹 시행 수가 줄어도 보정이 느슨해지지 않도록 다음과 같이 정했다. 모호한 곳은 전부 보수적인 쪽(전체 시행 수 유지)을 택했다.

| 항목 | 쓰는 시행 집합 / 시행 수 | 분리 영향 |
|---|---|---|
| DSR 게이트 값 `dsr_at_trials_searched` | `trials_searched` = 이 상태 디렉터리의 **모든 실험 문맥·모든 심볼 집합**에서 탐색한 전략 수(ADR 0078 D-02/D-06) | **없음**. 그룹으로 나누지 않는다 |
| DSR `var_sr`, 기록용 `dsr`(`trials`) | 현재 실험 문맥의 계산 가능한 시계열 **전체**(모든 심볼 집합) | **없음**. DSR은 공통 달력이 필요 없어(ADR 0081) 나눌 이유가 없고, 선택은 모든 심볼 집합에 걸쳐 일어났으므로 전체가 맞다 |
| MinBTL, SR floor | DSR 게이트와 같은 전체 시행 수 | **없음** |
| PBO 값 | 같은 심볼 집합 그룹의 CSCV 행렬 | 그룹으로 계산. PBO는 시행 수 N을 보정 인자로 쓰는 통계가 아니라 행렬에서 직접 측정하는 확률이고, 행렬은 같은 달력의 시행끼리만 존재한다(ADR 0083) |
| PBO 최소 시행 수 | `pbo_min_trials`를 **그룹**에 적용 | 그룹이 작으면 계산 불가(통과 아님). 전체 문맥 수로 채우지 않는다 |
| PBO 기록의 `trials_searched_all` | 전체 탐색 수(ADR 0078) 그대로 | 없음 |

정리하면, 심볼 집합 분리는 **PBO 행렬을 만들 수 있게 하는 것**에만 쓰고, 탐색 규모에 대한 벌점(DSR·MinBTL·SR floor의 N)은 줄이지 않는다. 심볼 집합을 가로지르는 선택(예: BTC 단독과 BTC+ETH 중 고르기)에 대한 과적합 벌점은 전체 N의 DSR 게이트가 계속 맡는다. 그룹 PBO는 그룹 안 선택의 과적합만 측정한다는 한계를 여기 적어 둔다.

## 5. 바꾸지 않은 것

- `max_pbo`, `pbo_min_trials`, `pbo_partitions`, `min_dsr`, 게이트 여부, 시행 집합 정의(`search_pool`), `PIPELINE_VERSION`, 승격 설정 버전과 해시. 설정 키를 새로 만들지 않았으므로 설정 해시는 그대로이고, 코드 해시만 이전처럼 문맥 ID에 자동 반영된다.
- `_dsr()` 코드.
- 결과가 달라지는 경우는 실험 문맥에 심볼 집합이 둘 이상일 때뿐이다. 그 경우 수정 전에는 PBO가 언제나 `TRIAL_CALENDARS_DIFFER`로 계산 불가였고, 이제 같은 심볼 집합 시행이 8개 이상이고 달력이 같으면 계산된다. 이전에 이 사유로 `BLOCKED_STATISTICS`가 된 기록은 `_blocked_now_computable`로 재개될 수 있다(그룹 PBO가 계산 가능할 때만, `max_pbo` 그대로).
- 기존 ADR 0083 전체 경로 테스트 `test_robustness_blocks_and_never_passes`는 BTC+ETH 우위 전략이 그 심볼 집합의 유일한 시행이라, 계산 불가 사유가 `TRIAL_CALENDARS_DIFFER`에서 `TOO_FEW_TRIALS`로 바뀐다. 이 테스트의 보안 단언(계산 불가, 불합격, `VALIDATION_SURVIVOR`·`PASS_INDIVIDUAL` 아님, `BLOCKED_STATISTICS`, 재개 불가)은 그대로 두고 사유 기대값만 새 사유로 정확히 바꿨으며 `trials == 1`, `search_context.symbol_set` 단언을 추가했다. 단언 완화는 없다.
- prereg 활성화, 검증/잠금/IW1 구간 개방, 실거래, 실시장 데이터 다운로드는 하지 않았다. 연구 스크립트(`bundle_dev` 등)의 0 채움 통일은 별도 보류 항목 그대로다.

## 6. 검증

- 신규 테스트 7개: 혼합 문맥의 그룹 PBO = 그 그룹만 있는 문맥의 PBO(값·조합 수·시행 수·일수 동일), 그룹 미달 `TOO_FEW_TRIALS`, 그룹 안 달력 차이 `TRIAL_CALENDARS_DIFFER` 유지, 심볼 순서 무관, 심볼 집합 하나인 무작위 30개 집합에서 `_pbo(sid) == _pbo()`(dict 완전 일치, 차등), 실제 잠금 구간 전체 경로(BTC+ETH 9 / BTC 4 / ETH 4)에서 그룹 PBO가 BTC+ETH 9개만 있는 실행과 같고 DSR 게이트 시행 수는 17 그대로, 강건성 기록의 PBO가 그룹(9개)으로 계산됨.
- 전체 스위트(3.11·3.13), ruff, source audit, provenance, research inventory 결과는 PR에 기록한다.
