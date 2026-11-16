# 최종 보고 — `_dsr()` 심볼 간 DEV 달력 공백 KeyError, ADR 0081

- 날짜: 2026-11-16, 기준 커밋 `2d6616a` (main, PR #78 병합. 작업 시점 main 최신)
- 입력: ADR 0080 보고서 §6.1 범위 밖 발견, 동동 2026-11-16 "처리해"
- 번호: 열린 PR #79(0078·0079), #80(0080)이 병합 전이라 **ADR 0081**
- 브랜치 `claude/project-thread-j05292`

## 1. 결론

공백이 있는 DEV 시계열의 DSR은 **계산 가능**하다고 판단해 관측일만으로 올바르게 계산하도록 고쳤다. 공백 일은 0으로 채우지 않는다. 정말 계산할 수 없는 경우는 명시 사유와 함께 거부한다(통과 없음).

근거: DSR(Bailey & López de Prado 2014)은 T개 관측 수익률의 SR·T·왜도·첨도와 시행 간 SR 분산의 함수이고 달력 연속성을 요구하지 않는다. 어느 심볼도 관측하지 않은 날은 0 수익이 아니라는 원칙은 ADR 0006(DM 정렬)에 이미 있다. 0으로 채우면 재현 픽스처에서 T가 586 → 852로 늘고 평균·표준편차가 함께 바뀐다.

## 2. 재현 (main `2d6616a`)

`run/discovery/tests/test_dsr_calendar_gap_20261116.py`, 실제 잠금 구간 그대로(패치 없음), BTC DEV 2023-07-01 시작, ETH DEV 2024-06-19 시작(BTC 잠금 끝 다음 날).

- 12개 중 11개 실패. 전체 경로 `run_validation()` → `robustness()` → `_dsr()` → `pooled()`에서 `KeyError: 19627`.
- 원인: `[acc[d] for d in range(days[0], days[-1] + 1)]`가 어느 심볼의 창에도 없는 날을 읽었다.

## 3. 수정

| 위치 | 내용 |
|---|---|
| `pipeline.pooled_dev_days` (신규) | 관측일에만 합산, 날짜 순. `dev_calendar`(관측·span·공백 일, 구간 수, 처음·끝 날) 반환. 비유한 값은 `DevSeriesError` |
| `EvolutionPipeline._dsr` | 위 함수 사용. `n_days`=관측일. 결과에 `dev_calendar`. 계산 불가 시 `reason`·`reason_detail`·`n_days`·`dev_calendar` |
| 계산 불가 사유 | `DEV_SERIES_TOO_SHORT`, `DEV_SERIES_NO_VARIATION`, `DEV_SERIES_INVALID`, `NO_DEV_SERIES`, `TOO_FEW_COMPUTABLE_TRIALS` |
| `tests/test_pipeline.py` | `SyntheticProvider(dev_start=...)`가 심볼별 날짜 dict도 받음(기존 문자열 그대로 동작) |
| `STATISTICS_AND_STATES.md` §10.4 | 관측 DEV 일 규칙 문단 |

바꾸지 않음: `min_dsr` 0.95, `dsr_min_days` 30, `dsr_trial_set`, 게이트 여부, MinBTL 식, 문맥 ID.
연속·겹치는 창은 수정 전과 같은 시계열(무작위 200건 차등 테스트로 고정).

## 4. 수정 후 결과

전체 경로 픽스처: 심어 둔 우위 전략이 `VALIDATION_SURVIVOR`, DSR 계산됨(관측 586일, span 852일, 공백 266일, 2구간, DSR 1.0, 시행 8). 노이즈 7개는 REJECTED(이전 단계).

## 5. 검증

| 항목 | 결과 |
|---|---|
| 신규 테스트 | 13개 OK |
| discovery 스위트 3.13 | 1028 tests OK (1836 s) — 차등 테스트 추가 전 실행, 추가분은 개별 실행 OK |
| discovery 스위트 3.11 | 1028 tests OK (skipped=1, 1885 s) |
| run/test_*.py | 152 OK |
| legacy funding test | OK |
| ruff F(제외 2개)·E9/F63/F7/F82 | 통과 (로컬 ruff 0.16.9, CI 고정 0.16.8) |
| source audit / provenance / research inventory / ledger_check / compileall | 통과 |

단언 완화 없음. 기존 테스트 수정은 `SyntheticProvider` 인자 확장 1곳뿐.

## 6. 남은 한계·발견

1. **`_pbo()` 0 채움(범위 밖, 수정 안 함).** CSCV 행렬을 시행들의 날짜 합집합에서 `.get(d, 0.0)`으로 만든다. 심볼 집합이 다른 전략이 한 문맥에 섞이면 관측하지 않은 날이 0으로 들어가 PBO 게이트 값에 영향을 줄 수 있다. 코드 읽기로만 확인했다. 공통 관측일로 제한할지, 계산 불가로 거부할지 별도 결정이 필요하다.
2. PR #80 테스트의 `LOCKED_TEST_PERIODS` 패치는 그대로 두었다. 이 수정 뒤에는 필요 없지만 그 PR의 범위다.
3. PR #79가 같은 `_dsr()`의 다른 줄(시행 수·반환 dict)을 바꾼다. 늦게 병합되는 쪽에서 충돌을 풀어야 하며, 두 변경은 서로 독립이다(이쪽: 시계열 구성과 사유, 저쪽: 시행 수와 게이트 값).
4. 통계 기준·임계값, prereg, 검증/잠금/IW1 구간, 실거래, 실시장 데이터는 건드리지 않았다.
