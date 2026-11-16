# ADR 0080 — Paper Forward 결론 후 증거 로그 완전성 (evidence-log-final-state-v1)

- 상태: Accepted
- 날짜: 2026-11-16
- 요청: 동동, 2026-11-12 지시문 "ADR 0078 — Paper Forward 결론 후 증거 로그 완전성"(독립 감사 2차 `audit_report_paper_forward_r2_20261112.docx`, Low 결함 F-3 + 권고 P4)
- 번호: 지시문은 ADR 0078로 적었으나 0078·0079는 열린 PR #79(통합 감사·후속)가 쓰고 있어 다음 빈 번호 0080을 쓴다. 지시문 안의 "ADR 0078"은 이 ADR을 가리킨다.
- 관련: ADR 0076(paper-forward-cutoff-v1, 앵커·`verify_conclusion`), ADR 0077(paper-forward-completeness-v1), ADR 0074(paper-forward-integrity-v1, seq 체인 로그)
- 시작 커밋: `2d6616a` (main, PR #78 병합; 2026-11-16 기준 main 최신)

## 1. 문제 (F-3, Low)

결론이 난 계획의 증거 로그 끝에 유효한 체인 행을 덧붙여도 `verify_conclusion()`이 그 로그를 지목하지 못했다.

- 봉 로그(`bars_<sym>.jsonl`): `_record_problems()`는 행 수가 **줄어든** 경우와 기록 시점 행의 해시가 **바뀐** 경우만 봤다. 늘어난 경우는 앵커 검사에서 빠졌다.
- 펀딩 로그(`funding_<sym>.jsonl`)·펀딩 품질 로그(`funding_quality_<sym>.jsonl`): 앵커의 `funding_head`·`quality_head`는 **state의 head**와만 비교됐고 디스크 파일과는 비교되지 않았다. 결론 뒤 파일에 행이 붙어도 state는 그대로이므로 아무것도 보고되지 않았다.

### 1.1 재현 (main `2d6616a`, `TestReproduction0078`, 3개 모두 실패)

| 테스트 | main 결과 |
|---|---|
| `test_repro_funding_row_appended_after_conclusion` | `verify_conclusion()` = `[]` (탐지 안 됨) |
| `test_repro_quality_log_row_appended_after_conclusion` | `verify_conclusion()` = `[]` (탐지 안 됨) |
| `test_repro_bar_row_appended_after_conclusion` | 봉 로그 이름을 지목하는 사유 없음. 다만 `[]`는 아니었다: ADR 0077의 독립 위치 재생이 `STATE_BAR_LOG_MISMATCH: BTCUSDT: bar log ends at … with 6721 bars, the state says … / 6720`을 보고했다 |

지시문은 봉 로그 추가도 `[]`를 반환한다고 적었지만 실제로는 위 재생 검사가 간접적으로 잡았다(재생은 cutoff가 원장에서 계산될 때만 돈다). 앵커 수준에서 봉 로그를 지목하지 못한 것은 사실이므로 재현 테스트는 "봉 로그 이름을 지목하는 사유"를 단언하고, 그 단언이 main에서 실패한다. 펀딩·품질 로그 두 건은 지시문 그대로 재현됐다.

판정(표본·지표·cutoff)은 원장·cutoff·state에서 다시 계산되므로 이 결함으로 판정이 바뀌지는 않는다. 그래서 Low다.

## 2. 결정

### 2.1 행 수 완전 일치 (`EVIDENCE_LOG_POLICY = "evidence-log-final-state-v1"`)

`_record_problems()`가 새 보조 함수 `_log_final_problems()`를 부른다. 심볼마다 세 로그를 디스크에서 읽어 앵커와 비교한다.

- 봉 로그: 앵커 `bar_logs[sym].rows`/`head`.
- 펀딩 로그·품질 로그: 앵커 `funding_head[sym]`/`quality_head[sym]`의 `seq`/`hash`. seq 체인(ADR 0074)에서 레코드 i의 seq는 i이므로 head의 `seq`가 곧 행 수다. 그래서 **앵커 형식은 바꾸지 않는다**(기존 기록과 기록 ID 그대로).
- 행 수가 다르면(늘거나 줄거나) `"<file> has N rows, the verdict was computed from M (rows appended to or removed from a concluded plan)"`. 같으면 그 행의 해시를 앵커 head와 비교해 다르면 `"<file> was rewritten after the verdict"`(기존 문구 유지).
- 읽을 수 없는 로그(체인 손상 등)는 예외로 멈추지 않고 사유로 보고한다.

결론 시점에는 `_conclusion_problems()`가 `_evidence(recover=False)`로 "로그가 state보다 앞섬"을 거부하므로, 정상 결론의 디스크 행 수는 앵커와 항상 같다. 그래서 완전 일치를 요구해도 정상 경로에서 잘못된 사유가 나오지 않는다.

### 2.2 폴백 (호환성)

앵커 head에 정수 `seq`가 없으면(행 수 미기록. 현재 그런 결론 기록은 없다) 행 수 사유를 만들지 않고 head 해시를 로그 마지막 행 해시와 비교한다. 다르면 `"rewritten or extended"`로 보고한다. 이 경로는 테스트로만 고정했다.

### 2.3 읽기 전용 유지

수정은 검사(`_record_problems` → `_log_final_problems`)에만 있다. 복구·재작성·재계산은 새로 하지 않는다. `verify_conclusion()`가 디스크를 바꾸지 않는다는 것을 테스트가 파일 트리 sha256 비교로 확인한다. 같은 검사를 쓰는 ADR 0077 재개 경로(`_resume_conclusion`)는 결론 기록 뒤 로그에 행이 붙으면 마무리하지 않고 `PAPER_ERROR`(EVALUATION_RECORD_CONFLICT)를 낸다(이전에도 봉 로그는 재생 검사로 막혔고, 이제 사유가 해당 로그를 지목한다).

### 2.4 영향 없는 것

- 결론 전(OBSERVING) 계획: `_record_problems`는 결론 기록이 있을 때만 쓰인다. step의 행 추가는 그대로다(테스트로 확인).
- `events.jsonl`(원장)은 이미 `events_sha256`·`events_logged` 전체 대조로 안전하다. 바꾸지 않았다.

## 3. 다중 심볼 전체 경로 픽스처 (감사 P4)

`test_paper_forward_multisymbol_fullpath_20261112.py`. BTCUSDT+ETHUSDT 전략이 **각 심볼마다 다른 합성 이력**으로 등록 → DEV → 검증(VALIDATION_SURVIVOR) → 잠금 캠페인(PAPER_TEST_READY) → replay paper(PAPER_TESTING) → forward `step()`까지 실제로 통과한다.

- 피드: ETH는 15분 그리드. BTC는 다른 합성 봉을 30분 단위로 묶어 보낸다(:15/:45에 닫힌 봉은 다음 :30/:00에 도착, `delay_ms`에 기록). 매 묶음마다 이미 받은 마지막 봉을 다시 보낸다(`duplicates`에 기록).
- 시나리오: ETH가 3일 뒤처짐 → ETH가 day-70 진입 직후 봉까지(포지션 열림) → 따라잡음.
- 단언: (a) 뒤처진 동안 cutoff 없음, `_sample_bound` = min(last_ts) = ETH 시각. (b) 따라잡은 뒤 T = start+70일 00:30이고, 지연 없는 대조 실행과 T·표본·지표가 같다. (c) ETH의 미결 포지션은 `OPEN_POSITIONS_PENDING`으로 대기한 뒤 청산되고 `OPEN_POSITION_MISSING`은 한 번도 나오지 않는다. (d) 두 심볼의 거래가 모두 표본에 있다(각 60건 이상, ETH의 지연 구간 거래 포함).
- F-1 반박(ADR 0076 §3) 재현: 뒤처진 시점에 경계를 max(last_ts)로 두면 규칙이 이미 T에서 성립한다. 그런데 그때 원장에는 (ETH 마지막 봉, T] 구간 ETH 거래가 0건이고, 최종 표본에는 그 구간 거래가 3건 이상 있다. max 경계라면 이 거래들이 빠진 표본으로 결론이 났을 것이다.

시험 설정: 테스트 동안만 ETHUSDT의 잠금 TEST 구간을 BTCUSDT 구간으로 바꿔(`mock.patch.dict`) 두 합성 이력이 같은 달력을 쓰게 했다. 실제 잠금 구간(BTC 2024-03~06, ETH 2026-04~08)을 그대로 쓰면 ETH의 선택 데이터가 BTC 잠금 구간을 덮지 않도록 ETH를 2024-06-19 이후로 시작해야 한다. 그러면 두 심볼의 DEV 일별 시계열 사이에 달력 공백이 생기고 `EvolutionPipeline._dsr()`의 `pooled()`가 `KeyError`로 멈춘다(§5). 실제 원장·잠금 구간·데이터는 건드리지 않았다(임시 디렉터리만).

## 4. 하지 않은 것

- 통계 기준·임계값(`paper_forward_v1.json`), 펀딩·증거 무결성(ADR 0073/0074), 미결 대기·재구성(ADR 0075/0077), cutoff 규칙(ADR 0076) 변경 없음.
- prereg 활성화, 검증/잠금/IW1 구간 개방, 실거래, API 키, 실시장 데이터 다운로드 없음. 합성 데이터·임시 디렉터리만.
- 자동 복구 없음(보고만).

## 5. 남은 한계

1. **`_dsr` 달력 공백 KeyError (범위 밖, 수정 안 함)**: DEV 일별 시계열이 심볼 간에 이어지지 않으면 `pipeline.py` `_dsr.pooled()`가 `acc[d]` KeyError로 검증 배치를 멈춘다. 실패 방향(잘못된 통과 아님)이고, 실제 데이터(모든 심볼 2021년 시작)로는 공백이 생기지 않는다. 다만 BTC+ETH처럼 잠금 구간 독립성 때문에 선택 데이터가 서로 떨어질 수밖에 없는 조합에서는 생긴다. 별도 작업으로 다룰 일이다.
2. 폴백(§2.2)은 행 수가 없는 앵커가 실제로 없어서 테스트로만 고정했다.
3. 체인을 처음부터 다시 계산하고 state·원장·앵커를 함께 위조하면 이 검사로는 막을 수 없다. 기록 ID(`record_hash`)가 앵커를 덮으므로 평가 기록까지 고쳐야 한다. 그 경우는 외부 보관 사본과의 대조 영역이다(ADR 0076 §4.3과 같은 한계).
4. 다중 심볼 픽스처는 합성 피드 하나의 형태(BTC 30분 묶음·ETH 지연)만 다룬다. 실시간 피드(`feed_realtime=True`, late 거래)는 다루지 않았다.

## 6. 테스트

- `run/discovery/tests/test_paper_forward_evidence_final_20261112.py`: 18개(재현 3, 정책 15).
- `run/discovery/tests/test_paper_forward_multisymbol_fullpath_20261112.py`: 3개.
- 기존 스위트는 단언을 바꾸지 않고 전부 통과. 결과는 `run/audit/FINAL_REPORT_20261116_ADR0080.md`.
