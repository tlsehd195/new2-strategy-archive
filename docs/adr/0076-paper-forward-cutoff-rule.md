# ADR 0076 — Paper Forward 평가 cutoff 규칙: step 주기와 무관한 표본, max_days 창 상한, 관찰하지 못한 창의 차단, 결론 앵커 (paper-forward-cutoff-v1)

- 상태: Accepted
- 날짜: 2026-11-11
- 요청: 동동, 2026-11-11 독립 감사 보고서 `audit_report_paper_forward_20261111.docx`(ADR 0075 / main `4fd25d2` 감사, 판정 CHANGES REQUIRED) 첨부
- 관련: ADR 0075(paper-forward-positions-v1), ADR 0074(paper-forward-integrity-v1), ADR 0073(paper-forward-funding-v1), ADR 0006(forward paper)
- 시작 커밋: `4fd25d2` (main, PR #76 병합)

## 1. 감사 지적과 재현 결과

| 항목 | 감사 판단 | 이 ADR의 판단 | 근거 |
|---|---|---|---|
| F-1 다중 심볼 표본 경계 `min(last_ts)` | Medium 결함, 심볼별/최댓값 경계 권고 | **결함 아님** (경계는 유지, 테스트로 고정) | §3 |
| F-2 max_days가 데이터 기준이라 피드가 멈추면 영원히 OBSERVING | Low 결함 | **결함 확인 (재현)** | §2.2 |
| (추가) cutoff가 step을 언제 돌렸는지에 따라 달라짐 | 감사 범위 밖 | **결함 확인 (재현)** — F-2를 재현하다 발견 | §2.1 |
| (추가) 한 번에 늦게 step하면 표본이 max_days 창을 넘음 | 감사 범위 밖 | **결함 확인 (재현)** | §2.3 |
| 7.3 원장·cutoff 외부 앵커 | 보완 권고(결함 아님) | **반영** | §4.3 |

재현 테스트는 `run/discovery/tests/test_paper_forward_cutoff_20261111.py`의 `TestReproduction0076`, `TestReproductionWindow`이며, **수정 전 코드(`4fd25d2`의 `paper_forward.py`)에서 3개 모두 실패**하고 수정 후 통과한다.

### 2.1 cutoff가 step 주기에 따라 달라진다 (Medium)

ADR 0075의 cutoff는 "중단 규칙(60일·30거래)이 처음 성립한 **step**의 마지막 관찰 봉"이었다. 규칙이 처음 성립하는 **봉**이 아니다. 합성 EDGE(하루 1거래)로 같은 데이터에 step 시점만 바꿔 보면:

| 첫 판정 step | 수정 전 표본 | 수정 후 표본 |
|---|---|---|
| 61일째 | 61일, 61거래 | 60일, 60거래 |
| 75일째 | 75일, 75거래 | 60일, 60거래 |
| 120일째 | 120일, 120거래 | 60일, 60거래 |

파일 아카이브 피드는 언제 step을 돌려도 같은 봉이 들어오므로, 운영자가 step 시점을 고르면 표본 길이를 고를 수 있었다(공개 데이터로 미리 계산 가능). 판정 근거가 운영 습관에 좌우되는 것이므로 결함으로 본다.

### 2.2 피드가 멈추면 판정이 영원히 나오지 않는다 (감사 F-2)

봉이 20일째에서 멈추고 시계가 200일(> max_days 180)까지 가도 `days_observed`는 20에 머물러 `PAPER_OBSERVING`이 계속되고, 아무 경고도 없다. 감사 보고서의 프로브(60일에서 멈춤, 기준 충족 → PASS)는 정상 동작이다(60일 표본으로 규칙이 성립). 문제는 거래가 부족할 때 결론도 차단 사유도 없다는 쪽이다.

### 2.3 늦은 step 하나로 표본이 max_days 창을 넘는다

max_days 70, min_trades 80 기준에서 90일째 한 번 step하면, 수정 전 코드는 90일·90거래로 기준을 평가해 **`PAPER_PASSED`** 를 기록했다. 사전등록 창(70일) 안에는 70거래뿐이므로 올바른 결론은 `PAPER_INCONCLUSIVE`다.

## 3. F-1을 결함으로 보지 않는 이유

cutoff 기록 전 경계를 `min(last_ts)`(모든 심볼이 관찰된 시각)로 두는 것은 의도이며 필요하다.

- 경계 이전에는 모든 심볼의 진입이 알려져 있다(청산이면 원장, 아니면 그 심볼의 마지막 봉에서 미결 포지션으로 잡힘). 경계를 앞선 심볼 기준(최댓값)이나 심볼별로 두면, 늦은 심볼 B의 (B 관찰 시각, 경계] 구간 진입은 아직 **보이지 않아** 미결 대기(`_pending`)에도 잡히지 않는다. 그 상태에서 규칙이 성립하면 B의 거래가 빠진 표본으로 결론이 날 수 있다. 감사가 말한 "A의 00:15 진입이 빠지는" 상황보다 이쪽이 실제로 판정을 바꾸는 위험이다.
- `min`은 규칙 성립을 늦출 뿐(일수·거래 수가 작게 잡힘) 통과를 쉽게 만들지 않는다.
- ADR 0076 이후에는 cutoff가 원장에서 결정적으로 계산되므로, 어느 심볼이 늦든 cutoff는 같고 늦은 심볼은 결론 시점만 늦춘다(`TestMultiSymbolRule`).

다만 감사가 지적한 대로 다중 심볼 전체 경로(step 포함) 합성 픽스처는 이 저장소에 없다(검증·잠금 단계를 거친 다중 심볼 전략 픽스처가 필요). 다중 심볼은 `_rule_cutoff`/`_sample_bound` 단위 테스트로만 확인했다.

## 4. 결정

### 4.1 cutoff = 규칙이 성립하는 가장 이른 봉 (`CUTOFF_POLICY = "paper-forward-cutoff-v1"`)

`ForwardPaper._rule_cutoff(plan, st, ledger)`:

- `t_days` = min_days가 채워지는 봉(`start + ceil(min_days)일 - 15분`, 15분 격자 올림)
- `t_trades` = 집계 대상 거래(FILLED/PARTIAL/LIQUIDATED, 실시간 피드면 지연 제외)의 **청산 시각** 중 min_trades번째. 거래는 청산 봉이 관찰돼야 결과를 알 수 있으므로 청산 시각 기준이다.
- `T = min(max(t_days, t_trades), 창 끝 봉)`, 거래가 부족하면 `T = 창 끝 봉`(start + max_days - 15분)
- 모든 심볼이 T까지 관찰됐을 때만 T, 아니면 None(아직 성립 안 함)

T는 원장(매 step 평가기와 대조되는 파일)만으로 정해지며 step 시점과 무관하다. 한 번 T가 정해지면 이후 데이터로 바뀌지 않는다(T 이전에 청산된 거래는 모두 이미 원장에 있다). 표본은 기존대로 "T까지 진입한 청산 거래", T까지 진입해 아직 열린 포지션은 ADR 0075대로 청산까지 대기한다.

`_verdict`, `_conclusion_problems` 모두 기록된 cutoff가 T와 다르면 `EVALUATION_CUTOFF_INVALID`/결론 거부. ADR 0075의 "규칙이 성립하는 다른 시각으로 cutoff를 옮기는 상태 변조"가 이제 탐지된다(`test_cutoff_moved_to_another_bar_where_the_rule_holds`).

### 4.2 max_days는 창이고, 시계가 창을 지났는데 데이터가 없으면 BLOCKED_DATA

- 표본 경계와 `days_observed`는 창 끝(start + max_days)을 넘지 않는다. `missing_bar_share`도 [start, 경계] 구간만 센다(cutoff 뒤 공백이 판정에 들어가지 않음, step 주기와 무관).
- 규칙이 아직 성립하지 않았는데 시계가 창 끝을 지났으면 `PAPER_BLOCKED_DATA`(비최종) + `OBSERVATION_WINDOW_NOT_COVERED`. 관찰하지 못한 봉으로 INCONCLUSIVE를 내지 않는다. 데이터가 나중에 도착하면 그대로 이어서 판정한다(`test_data_arriving_later_resumes_the_plan`).
- 창이 열려 있는 동안 데이터가 시계보다 1일 넘게 늦으면 `PAPER_OBSERVING` 사유에 `DATA_LAG`를 붙인다. 매 step 결과에 `observation_clock`(시계 일수, 데이터 일수, 창 끝)을 남긴다.
- 창을 다 관찰했는데 거래가 부족하면 기존대로 `PAPER_INCONCLUSIVE`.

### 4.3 결론 앵커와 사후 검증 (감사 7.3)

평가 기록(`evaluations.jsonl`)에 `anchors`를 추가했다: cutoff와 규칙 버전, `events.jsonl`의 sha256·기록 수, 심볼별 봉 로그 행 수·마지막 해시, funding/quality 로그 head, last_ts, warm-up sha256. `record_id`가 레코드 전체의 해시이므로 앵커도 그 안에 묶인다.

`ForwardPaper.verify_conclusion(plan_id)`는 읽기 전용으로 디스크 파일을 앵커와 대조하고, 원장에서 cutoff·지표·판정을 다시 계산해 기록과 비교한다. state와 원장을 함께 고친 위조, 다시 체인을 짠 봉 로그 위조를 탐지한다(`TestConclusionAnchors`). 평가 기록 파일 자체를 통째로 다시 쓰는 위조는 여전히 이 저장소 밖의 앵커(예: 커밋된 해시)가 있어야 막을 수 있다.

## 5. 바뀌지 않은 것

통계 기준·임계값(`paper_forward_v1.json`), 펀딩·증거 무결성(ADR 0073/0074), 미결 포지션 대기와 원장 정합성 검사(ADR 0075), PAPER_PASSED 반환 지점(`_decide` 한 곳). prereg 활성화, 검증/잠금/IW1 구간 개방, 실거래·API 키·실시장 데이터 다운로드는 하지 않았다.

## 6. 호환성

- 기록된 cutoff에 `rule` 필드가 붙는다. ADR 0075 규칙으로 기록된 cutoff(step의 마지막 봉)는 대개 T와 달라 `EVALUATION_CUTOFF_INVALID`가 된다(fail-closed). 이 저장소에는 실제 forward 계획이 없다(PAPER_TEST_READY 전략 없음).
- ADR 0075 테스트 중 "cutoff = step의 마지막 봉"을 단언하던 곳은 새 규칙에 맞게 고쳤다: positions 테스트 4곳, 그리고 ADR 0075가 보강한 funding 테스트 2개(`test_d6_…`, `test_full_path_boundary_8h_and_1h`)의 중간 단언 — 75일째 보유 중 포지션은 이제 표본(60일 cutoff) 밖이므로 재시도 step에서 바로 `PAPER_PASSED`이고, cutoff 값과 대기 포지션 없음을 단언한다. 관찰 끝 미결 포지션 시나리오는 기준 `min_days = 70 + 45/1440`(규칙이 70일째 00:30, 보유 중에 성립)으로 같은 상황을 유지한다. 완화한 단언은 없다.

## 7. 테스트

- 신규 `test_paper_forward_cutoff_20261111.py`: 재현 3, 주기 무관 4, 미결+규칙 3, 피드 정지 3, 다중 심볼 규칙 4, 앵커 5.
- 전체 `python -m unittest discover -s discovery/tests`(977개 = 955 + 신규 22)와 루트 테스트(152개), legacy 테스트를 Python 3.11/3.13에서 실행해 통과. ruff(F), source audit / provenance / inventory 검사 통과.
- 재현 3개는 `4fd25d2`의 `paper_forward.py`에서 실패함을 확인(cutoff 1731023100000 ≠ 1729727100000, OBSERVING ≠ BLOCKED_DATA, PASSED ≠ INCONCLUSIVE).
