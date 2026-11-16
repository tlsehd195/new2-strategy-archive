# 최종 보고 — 독립 감사 2차 F-3 대응, ADR 0080 (evidence-log-final-state-v1)

- 날짜: 2026-11-16, 기준 커밋 `2d6616a` (main, PR #78 병합. 2026-11-16 현재 main 최신)
- 입력: 2026-11-12 지시문 "ADR 0078 — Paper Forward 결론 후 증거 로그 완전성"(독립 감사 2차 `audit_report_paper_forward_r2_20261112.docx`, 판정 APPROVED WITH LIMITATIONS, Low F-3 + 권고 P4)
- 번호: 0078·0079는 열린 PR #79(통합 감사·후속)가 쓰고 있어 **ADR 0080**을 쓴다. 지시문의 파일명 `FINAL_REPORT_20261112_ADR0078.md`도 이 파일로 바꿨다.
- 브랜치 `claude/project-thread-q1dse4`, PR #80

## 1. 사전 확인

| 확인 | 결과 |
|---|---|
| main 최신 커밋 | `2d6616a` (지시문 base와 같음) |
| docs/adr 최대 번호 | main 0077. 열린 PR #79가 0078·0079 사용 → 0080 |
| F-3 수정 여부 | main·PR #79 브랜치 모두 미수정. `_record_problems` 봉 로그 검사는 main 2323-2330행 그대로(PR #79 브랜치에서는 2457-2464행) |

## 2. 재현 결과 (`TestReproduction0078`, main `2d6616a`에서 3개 모두 실패)

| 테스트 | main에서 `verify_conclusion()` | 판정 |
|---|---|---|
| `test_repro_funding_row_appended_after_conclusion` | `[]` | 지시문대로 재현 |
| `test_repro_quality_log_row_appended_after_conclusion` | `[]` | 지시문대로 재현 |
| `test_repro_bar_row_appended_after_conclusion` | `['STATE_BAR_LOG_MISMATCH: BTCUSDT: bar log ends at 1730592000000 with 6721 bars, the state says 1730591100000 / 6720']` | **지시문과 다름**: `[]`가 아니다. ADR 0077의 독립 위치 재생이 state와 봉 로그 길이를 비교해 간접적으로 잡았다. 앵커 검사는 봉 로그를 지목하지 못했으므로, 테스트는 "봉 로그 이름을 지목하는 사유"를 단언하고 그 단언이 실패한다 |

원인: 앵커의 `funding_head`·`quality_head`는 state의 head와만 비교됐고 디스크 파일과는 비교되지 않았다. 봉 로그 앵커는 "더 짧음"과 "기록 행 해시 변경"만 봤다.

## 3. 수정 내용

- `paper_forward.py`
  - `EVIDENCE_LOG_POLICY = "evidence-log-final-state-v1"`.
  - `_record_problems()`의 봉 로그 루프를 새 `_log_final_problems()` 호출로 바꿨다. 심볼마다 봉 로그·펀딩 로그·품질 로그를 디스크에서 읽어 다음을 검사한다. 행 수가 앵커와 다르면(추가·삭제 모두) `"<file> has N rows, the verdict was computed from M (rows appended to or removed from a concluded plan)"`. 같으면 마지막 행 해시가 앵커 head와 다를 때 `"<file> was rewritten after the verdict"`. 로그를 읽지 못하면 예외 대신 사유를 낸다.
  - 펀딩·품질 로그의 행 수는 seq 체인 head의 `seq`(레코드 i의 seq = i)를 쓴다. 그래서 **앵커 형식과 기록 ID는 바뀌지 않는다**. 지시문 §4.1의 "앵커에 rows 추가"는 필요 없었다.
  - 정수 `seq`가 없는 앵커는 해시 폴백(마지막 행 해시와 비교, `"rewritten or extended"`). 이 경우는 실제로 없어서 테스트로만 고정했다.
  - `verify_conclusion()` docstring 갱신. 읽기 전용은 그대로다(복구·재작성·재계산 없음).
- 같은 검사를 쓰는 재개 경로(`_resume_conclusion`): 결론 기록 뒤 로그에 행이 붙으면 마무리하지 않고 `PAPER_ERROR`(EVALUATION_RECORD_CONFLICT)를 내며, 사유가 해당 로그를 지목한다.
- 정상 경로 영향 없음: 결론 시점에는 `_evidence(recover=False)`가 "로그가 state보다 앞섬"을 거부하므로 정상 결론의 디스크 행 수는 앵커와 항상 같다.

## 4. 테스트

### 4.1 `test_paper_forward_evidence_final_20261112.py` (18개)

- 재현 3(§2).
- 정책 15: 정책 상수, 손대지 않은 계획은 `[]`이고 파일 트리 sha256이 그대로(읽기 전용), 세 로그 각각 행 추가 2개(두 수를 모두 적은 사유 1개), 세 로그 각각 마지막 행 삭제, 세 로그 각각 같은 길이로 다시 체인한 위조(`rewritten`), 체인 손상은 예외 없이 사유, seq 없는 앵커 폴백, 재개 경로에서 행 추가 시 PAPER_ERROR, OBSERVING 계획은 영향 없음(step마다 96행 추가, 결론 기록 없음).

### 4.2 `test_paper_forward_multisymbol_fullpath_20261112.py` (3개, 감사 P4)

BTCUSDT+ETHUSDT 전략이 **심볼마다 다른 합성 이력**으로 DEV → 검증(VALIDATION_SURVIVOR) → 잠금 캠페인(PAPER_TEST_READY) → replay paper(PAPER_TESTING) → forward `step()`을 실제로 통과한다.

- 피드: ETH는 15분 그리드. BTC는 다른 합성 봉을 30분 묶음으로 보낸다(:15/:45에 닫힌 봉은 다음 :30/:00 도착, `delay_ms`). 묶음마다 마지막 봉을 다시 보낸다(`duplicates` > 0, ETH는 0).
- `test_lagging_symbol_end_to_end`: ETH 3일 지연 → day-70 진입 직후 봉(00:45)까지 → 따라잡음.
  - (a) 지연 중 cutoff 없음, `_rule_cutoff` None, `_sample_bound` = min(last_ts) = ETH 시각.
  - F-1 반박 재현: 같은 시점에 경계를 max(last_ts)로 두면 규칙이 이미 T에서 성립하는데, 원장에는 (ETH 마지막 봉, T] 구간 ETH 거래가 0건이다. 최종 표본에는 그 구간 거래가 3건 이상 있다. max 경계였다면 이 거래들이 빠진 표본으로 결론이 났다.
  - (c) ETH day-70 포지션은 `OPEN_POSITIONS_PENDING`으로 대기한 뒤 02:15에 청산된다. steps.jsonl 전체에 `OPEN_POSITION_MISSING`이 없고 `open_replayed` = [].
  - (b) 대기 중 기록된 T = 결론의 T = 기록 앵커의 T = start+70일 00:30.
  - (d) 표본 = 두 심볼 각 60건 이상, `n_trades`와 일치, ETH 지연 구간 거래 포함. `verify_conclusion()` = [].
- `test_catch_up_gives_the_same_T_and_sample_as_no_lag`: 지연 없는 대조 실행과 지연 실행이 T, 표본 event_id 목록, `n_trades`·`mean_net_bp`·`hac_p_one_sided`·`stressed_mean_bp`·`mdd_bp`가 모두 같다.
- `test_appended_row_of_one_symbol_is_found`: 두 심볼 계획에서 ETH 펀딩 로그에만 행을 붙이면 그 로그만 지목된다.

시험 설정: 테스트 동안만 ETHUSDT 잠금 TEST 구간을 BTCUSDT 구간으로 바꿨다(`mock.patch.dict`, 임시). 이유는 §6-1.

## 5. 실행 결과

| 항목 | Python 3.13.18 | Python 3.11.18 |
|---|---|---|
| discovery 전체 (`unittest discover -s discovery/tests`) | **1,037개 OK** (1,016 + 21), 1,550.8초 (실시간 25분 52초) | **1,037개 OK** (skipped 1: pandas 없음), 1,647.6초 (27분 28초) |
| 루트 연구 테스트 (`run/test_*.py`) | 152개 OK (로컬 numpy 2.5.3/pandas 3.0.5. CI 고정 버전과 다름) | 미실행(로컬 3.11에 numpy 없음) |
| legacy pandas 테스트 | OK | skipped (pandas 없음) |

- 기존 테스트 단언 변경: 없음. 기존 `verify_conclusion` 테스트(TestConclusionAnchors의 `test_bar_log_rewritten` 등)는 문구를 그대로 둬서 충돌 없음.
- ruff 0.16.9(로컬. CI는 0.16.8): `--select E9,F63,F7,F82`, `--select F --exclude portfolio_sim.py,funding_cost.py`, `run/*.py run/audit/*.py` 모두 통과. compileall 통과.
- `source_audit_20261110.py check` OK, `audit_provenance_20261111.py check` OK, research inventory diff 없음, `ledger_check.py` (`.`, `state`) OK.
- GitHub Actions(PR #80): 결과는 PR 페이지 참조(작성 시점 실행 중).

## 6. 남은 한계

1. **범위 밖 발견: `pipeline.py` `_dsr()`의 `pooled()` KeyError.** DEV 일별 시계열이 심볼 사이에서 달력상 이어지지 않으면 `acc[d]` KeyError로 검증 배치가 멈춘다. 실제 잠금 구간(BTC 2024-03-25~06-18, ETH 2026-04-28~08-31)으로 BTC+ETH 합성 전략을 만들면, ETH 선택 데이터가 BTC 잠금 구간을 덮지 않도록 ETH를 2024-06-19 이후로 시작해야 하고, 그러면 공백이 생겨 이 오류가 난다. 멈추는 방향이라 잘못된 통과는 아니다. 실제 데이터(모든 심볼 2021년 시작)에서는 ETH 선택 데이터가 BTC 잠금 구간을 덮어 `LOCKED_WINDOW_NOT_INDEPENDENT`로 먼저 막힌다. 이번에는 수정하지 않았다(변경 범위를 `_record_problems`로 제한). 별도 작업으로 다룰 일이다.
2. 단일 step이 거래 청산 직후·다음 펀딩 정산 전(예: 03:00)에 돌면 그 step은 `PAPER_BLOCKED_DATA`(FUNDING_COVERAGE_INCOMPLETE)다. 상태는 OBSERVING으로 남고 다음 step에서 풀린다(결함 아님, ADR 0073 설계). 픽스처는 정산 뒤(04:15)에 step한다.
3. 폴백(seq 없는 앵커)은 해당 기록이 실제로 없어 테스트로만 고정했다.
4. 체인을 처음부터 다시 계산하고 state·원장·평가 기록(`record_hash`가 앵커를 덮음)까지 함께 위조하면 이 검사로는 막을 수 없다. 저장소 밖 사본과의 대조 영역이다.
5. 다중 심볼 픽스처는 비실시간 합성 피드(`feed_realtime=False`) 한 형태만 다룬다. 실시간 late 거래 경로는 다루지 않았다.
6. 실제 forward 계획은 없다(PAPER_TEST_READY 전략 없음). 통계 기준, prereg, 검증/잠금/IW1 구간, 실거래, API 키, 실시장 데이터는 건드리지 않았다.
