# ADR 0083 — PBO는 시행들의 관측 달력이 같을 때만 계산한다 (무관측일 0 채움 제거)

- 상태: Accepted
- 날짜: 2026-11-16
- 요청: 동동, 2026-11-16 "너의 추천으로 진행해" (ADR 0081 §5·보고서 §6.1의 범위 밖 발견)
- 관련: ADR 0006(관측하지 않은 날은 0 수익이 아님), ADR 0064(promo-v5, `max_pbo` 0.05), ADR 0081(`pooled_dev_days`, DSR 관측일 계산)
- 시작 커밋: `a441ba7` (main, PR #81 병합). 열린 PR #79가 0078/0079, 열린 PR #82가 0082를 쓰고 있어 0083을 쓴다.

## 1. 문제

`EvolutionPipeline._pbo()`는 CSCV(Bailey et al. 2017) T×N 행렬을 **모든 시행의 DEV 날짜 합집합** 위에 만들고, 시행이 관측하지 않은 날을 `pooled[s].get(d, 0.0)`으로 0 수익으로 채웠다.

```python
days = sorted({d for a in pooled.values() for d in a})
mat = [[pooled[s].get(d, 0.0) for s in sids] for d in days]
```

한 실험 문맥 안에 심볼 집합이 다른 전략(BTC 단독, ETH 단독, BTC+ETH)이 섞이면 DEV 창이 달라지고, 각 시행은 관측하지 않은 날에 0을 받는다. CSCV의 IS/OOS 절반 평균과 순위가 지어낸 값으로 정해지므로, PBO 게이트(`max_pbo`) 판정이 근거 없이 바뀐다. 또 시계열에 NaN이 있으면 `max()`·`sorted()`가 NaN을 순서 없이 다뤄 **유한한 PBO가 나왔다**(사유 없는 잘못된 계산 가능).

## 2. 재현 (수정 전 `a441ba7`)

`run/discovery/tests/test_pbo_calendar_mismatch_20261116.py`를 먼저 작성해 수정 전 코드로 실행했다. 9개 중 7개 실패(실패 5, 오류 2).

| 테스트 | 수정 전 |
|---|---|
| `test_different_calendars_are_not_zero_filled` (A 0~199일 4개, B 100~299일 4개) | 300일(각 시행 100일이 0)로 PBO 계산 |
| `test_zero_fill_can_turn_an_overfit_set_into_a_pass` | 같은 달력 8개 시행 PBO **0.574**(불합격). 0~319일을 관측한 시행 1개를 더하면 나머지 8개가 160일씩 0을 받아 PBO **0.0**(합격) |
| `test_non_finite_value_is_refused` | NaN이 섞인 시행이 있어도 PBO 계산 가능으로 나옴 |
| `TestMixedSymbolSetsFullPath` 2개 (실제 잠금 구간, BTC+ETH 우위 전략 1 + BTC 단독 4 + ETH 단독 4) | 586일 합집합에서 PBO 0.0, 심은 우위 전략이 `VALIDATION_SURVIVOR` |
| 사유 코드 2개 | `reason` 필드 없음(KeyError) |

## 3. 판단: 관측 달력이 다르면 계산 불가로 거부한다

- **CSCV의 정의.** CSCV는 같은 T개 기간에서 N개 시행의 성과를 나란히 놓고 IS 1위의 OOS 순위를 센다. 행렬의 모든 칸이 관측값이어야 한다. 관측 달력이 다른 시행들에는 이 행렬이 없다. 0 채움은 관측하지 않은 날을 "포지션 없음"으로 바꾸는 것이고, ADR 0006·0081과 어긋난다.
- **공통 관측일로 줄이지 않는다.** 교집합으로 잘라 계산할 수는 있지만, 어떤 기간을 PBO 표본으로 쓸지를 결과를 본 뒤 데이터 모양에 따라 바꾸는 것이 되고 측정 대상이 바뀐다(기준 변경). 이 작업 범위(기준·임계값 불변) 밖이므로 하지 않는다. 교집합 크기는 기록해 둔다.
- **fail-closed.** 계산 불가 PBO는 지금도 PBO 검사 불합격, 다른 검사가 모두 통과면 `BLOCKED_STATISTICS`(INSUFFICIENT_DATA)다. 잘못된 통과가 생기지 않는다. 달력은 시행이 늘어도 바뀌지 않으므로 재개(`_blocked_now_computable`)에서도 계속 막힌다.
- **지금의 계산과의 관계.** 모든 시행의 관측일이 같으면(지금까지 실제로 실행된 단일 심볼 집합 문맥) 행렬과 값이 수정 전과 **완전히 같다**(무작위 40개 집합 차등 테스트). 바뀌는 것은 0 채움이 들어가던 경우와 NaN 경우뿐이므로 `PIPELINE_VERSION`·승격 설정 버전은 올리지 않는다(코드 해시는 이전처럼 문맥 ID에 자동 반영된다).

## 4. 결정

1. `pipeline.pooled_dev_day_map(series)`(신규): ADR 0081 `pooled_dev_days`와 같은 합산을 `{day: value}`로 돌려준다. `pooled_dev_days`는 이 함수를 쓰도록 바꿨다(동작 같음).
2. `_pbo()`는 시행마다 이 함수로 관측일 시계열을 만들고, 관측일 집합이 모두 같을 때만 그 날들로 행렬을 만든다. 0 채움은 없다.
3. 계산 불가일 때 `computable: false`, `pbo: None`과 `reason`·`reason_detail`을 기록한다.
   - `TRIAL_CALENDARS_DIFFER`: 관측 달력이 둘 이상. `calendars`, `days_union`, `days_common`, `calendar_groups`(달력별 시행 수·관측일·첫/끝 날·예시 시행, 최대 10개).
   - `TOO_FEW_TRIALS`: 시행 수 < `pbo_min_trials`(이전과 같은 조건, 사유만 추가).
   - `DEV_SERIES_INVALID`: 비유한 값이 있는 시행(`invalid_trials`).
   - `CSCV_NOT_COMPUTABLE`: 공통 일수 < `pbo_partitions` 등 `pbo_cscv`가 NaN을 돌려준 경우.
4. `BLOCKED_STATISTICS` 상태 사유 문자열 끝에 PBO 사유 코드를 붙인다(예: `PBO not computable (9 trials) [TRIAL_CALENDARS_DIFFER]`).
5. `max_pbo`, `pbo_min_trials`, `pbo_partitions`, 시행 집합(`search_pool`), 게이트 여부는 바꾸지 않았다.

## 5. 바꾸지 않은 것과 남은 발견

- `bundle_dev.py`의 `combine()`도 날짜 합집합에서 `m.get(d, 0.0)`을 쓴다. DEV 전용 연구 스크립트(묶음 수익, 게이트 아님)이고 "flat = 0"이라고 문서화돼 있어 이번 범위에서 고치지 않았다. 같은 원칙을 적용할지는 별도 판단이 필요하다.
- PR #79가 `_pbo()` 반환 dict(`trials_searched_all` 추가)와 `why["pbo"]` 줄을 바꾼다. 늦게 병합되는 쪽에서 충돌을 풀어야 하며 두 변경은 독립이다(이쪽: 행렬 구성과 사유, 저쪽: 시행 수 기록).
- 통계 기준·임계값, prereg 활성화, 검증/잠금/IW1 구간 개방, 실거래, 실시장 데이터 다운로드는 하지 않았다.

## 6. 검증

- 신규 `test_pbo_calendar_mismatch_20261116.py` 9개 통과. 전체 경로(실제 잠금 구간, 심볼 집합이 다른 9개 시행): 달력 3개(BTC 단독 4개 88일, ETH 단독 4개 498일, BTC+ETH 1개 586일), 공통 0일 / 합집합 586일 → PBO `TRIAL_CALENDARS_DIFFER`로 불합격, 우위 전략은 `VALIDATION_SURVIVOR`(수정 전) 대신 `BLOCKED_STATISTICS`(사유 `INSUFFICIENT_DATA: PBO not computable (9 trials) [TRIAL_CALENDARS_DIFFER]`).
- 기존 스위트 단언 완화 없이 전부 통과(3.13·3.11), ruff F 및 E9/F63/F7/F82, source audit, provenance, research inventory 통과. 수치는 최종 보고서 `run/audit/FINAL_REPORT_20261116_ADR0083.md`.
