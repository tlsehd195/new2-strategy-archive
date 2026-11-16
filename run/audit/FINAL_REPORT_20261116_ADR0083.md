# 최종 보고서 — ADR 0083: PBO 무관측일 0 채움 제거 (2026-11-16)

- 요청: 동동 "너의 추천으로 진행해" (ADR 0081 보고서 §6.1 범위 밖 발견)
- 시작 커밋: `a441ba7` (main). 브랜치 `claude/project-thread-p4eb3a`.
- 결정 기록: `docs/adr/0083-pbo-trial-calendar-no-zero-fill.md`

## 1. 결론

`_pbo()`가 시행들의 DEV 날짜 합집합에서 관측하지 않은 날을 0 수익으로 채워 CSCV 행렬을 만들던 결함을 재현하고 고쳤다. 이제 모든 시행의 관측일이 같을 때만 PBO를 계산하고, 다르면 `TRIAL_CALENDARS_DIFFER` 사유로 계산 불가로 거부한다(fail-closed). 공통 관측일로 줄여 계산하지 않는다. 관측일이 같은 경우의 값은 수정 전과 완전히 같다.

## 2. 재현 결과 (수정 전 a441ba7)

| 경우 | 수정 전 | 수정 후 |
|---|---|---|
| 같은 달력 8개 시행 | PBO 0.574 (불합격) | 0.574 (같음) |
| 위 8개 + 0~319일 관측 시행 1개 | 320일 중 160일을 0으로 채워 PBO **0.0 (합격)** | 계산 불가 `TRIAL_CALENDARS_DIFFER` (공통 160 / 합집합 320일) |
| A 0~199일 4개 + B 100~299일 4개 | 300일 행렬로 PBO 계산 | 계산 불가 (공통 100 / 300일) |
| NaN이 섞인 시행 | 유한한 PBO (사유 없음) | 계산 불가 `DEV_SERIES_INVALID` |
| 전체 경로: 실제 잠금 구간, BTC+ETH 우위 전략 1 + BTC 단독 4 + ETH 단독 4 | 586일 합집합에서 PBO 0.0, 우위 전략 `VALIDATION_SURVIVOR` | 달력 3개(88일·498일·586일), 공통 0일 → PBO 불합격, `BLOCKED_STATISTICS` (`INSUFFICIENT_DATA: PBO not computable (9 trials) [TRIAL_CALENDARS_DIFFER]`) |

신규 테스트 9개 중 7개가 수정 전 코드에서 실패(실패 5, 오류 2), 수정 후 모두 통과.

## 3. 바꾼 것

- `run/discovery/pipeline.py`
  - `pooled_dev_day_map(series)` 신규: ADR 0081 합산을 `{day: value}`로. `pooled_dev_days`가 이를 사용(동작 같음).
  - `_pbo()`: 시행별 관측일 시계열, 관측일 집합이 모두 같을 때만 행렬 구성. 계산 불가 사유 `TRIAL_CALENDARS_DIFFER`(달력 수, 합집합·공통 일수, 달력별 요약), `TOO_FEW_TRIALS`, `DEV_SERIES_INVALID`, `CSCV_NOT_COMPUTABLE`.
  - `BLOCKED_STATISTICS` 사유 문자열 끝에 PBO 사유 코드.
- `run/discovery/tests/test_pbo_calendar_mismatch_20261116.py` 신규(9개).
- `run/discovery/STATISTICS_AND_STATES.md`, `docs/adr/README.md`, ADR 0083.

## 4. 바꾸지 않은 것

`max_pbo`·`pbo_min_trials`·`pbo_partitions`, 시행 집합, 게이트 여부, `PIPELINE_VERSION`, 승격 설정 버전. prereg 활성화, 검증/잠금/IW1 구간 개방, 실거래, 실시장 데이터 다운로드 없음. 기존 테스트의 단언은 하나도 바꾸지 않았다.

## 5. 검증

- 단위 테스트 `run/discovery/tests` 전체: Python 3.13 **1059개 OK**(1854초), Python 3.11 **1059개 OK**(skipped 1, 1899초). 이전 1050개 + ADR 0083 9개.
- 신규 `test_pbo_calendar_mismatch_20261116.py` 9개: 수정 전 7개 실패 → 수정 후 전부 통과. 같은 달력의 무작위 40개 시행 집합에서 PBO·조합 수·일수가 수정 전 구현(그대로 복사)과 정확히 같음.
- ruff `E9,F63,F7,F82` 및 `--select F`(CI와 같은 설정) 통과, source audit table OK, audit provenance OK, research inventory 재생성 시 변경 없음.
- GitHub Actions는 계정 단위 문제와 30분 작업 제한(PR #82가 60분으로 늘리는 중)으로 로컬 CI를 기준으로 한다(병합 정책 2026-10-22).

## 6. 남은 한계·발견

1. `bundle_dev.py`의 `combine()`도 날짜 합집합에서 `m.get(d, 0.0)`을 쓴다. DEV 전용 연구 스크립트이고 "flat = 0"으로 문서화돼 있어 고치지 않았다. 같은 원칙을 적용할지는 별도 판단.
2. 열린 PR #79(ADR 0078/0079)가 `_pbo()` 반환 dict와 `why["pbo"]` 줄을 바꾼다. 늦게 병합되는 쪽에서 충돌을 풀어야 하며 두 변경은 독립이다.
3. 열린 PR #82가 ADR 0082를 쓰고 있어 0083을 썼다.
4. 심볼 집합이 다른 전략을 한 문맥에서 함께 탐색하면 이제 PBO가 항상 계산 불가가 된다. 그런 탐색에서 PBO를 쓰려면 심볼 집합별로 문맥을 나누거나 공통 구간 규칙을 미리 정해야 하며, 이는 기준 변경이라 사용자 결정이 필요하다.
