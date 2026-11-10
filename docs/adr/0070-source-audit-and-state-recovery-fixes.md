# ADR 0070 — 미정독 스크립트 전체 소스 감사와 상태·복구·펀딩 결함 수정

- 상태: Accepted
- 날짜: 2026-11-10
- 요청: 동동, 2026-11-10 "잔여 문제 전면 해결" 지시문 §6(P2), §9
- 관련: ADR 0066(분류표), ADR 0067(전수 감사 보고서), ADR 0068·0069

## 무엇을 했나
`RESEARCH_SCRIPTS_20261110.json`에서 INVENTORIED였던 `run/*.py` 112개와 핵심 모듈 12개, 모두 124개 파일을 처음부터 끝까지 읽었다.
- 검토는 읽기 전용 독립 검토 에이전트가 했다. 각 행은 그 에이전트의 주장이다.
- 작성자가 직접 다시 읽어 확인한 행은 `spot_checked = true`로 표시했다. 5개 행이다.
- 모든 행에서 `lines_read = lines_total`이다. 일부만 읽은 파일은 없다.
- 결과 표: `run/audit/SOURCE_AUDIT_20261110.jsonl`, 요약은 `run/audit/SOURCE_AUDIT_20261110.md`.
- 생성·검사 스크립트: `run/audit/source_audit_20261110.py`. CI에서 `check`를 실행한다. 다음 경우 실패한다.
  - 분류표에 INVENTORIED로만 남은 스크립트가 있다.
  - 없는 파일을 가리키는 행이 있다.
  - 읽은 줄 수가 모자란다.
- 분류표(`RESEARCH_SCRIPTS_20261110.*`)의 읽기 수준은 이 감사 표를 반영한다. 결과는 INVENTORIED 112 → 0, LOGIC_TRACED 56, TESTED 48, FULL_SOURCE_REVIEW 33(137개 기준)이다.

### 감사 수준(124개)
| 수준 | 개수 | 의미 |
|---|---|---|
| FULL_SOURCE_REVIEW | 34 | 전체를 읽고 구조·의존성·오류 처리를 확인 |
| LOGIC_TRACED | 52 | 전체를 읽고 신호·라벨·비용·판정 경로를 줄 번호로 추적 |
| TESTED | 38 | 위에 더해 관련 테스트를 실행해 통과 |
| INVENTORIED / NOT_REVIEWED / BLOCKED | 0 | — |

- 폐기 v1 스크립트는 `run/LEGACY_SCRIPTS.md`에 76개가 있다. 이전 보고서의 "84개"는 현재 목록과 맞지 않는다.
- 76개 중 73개는 이번에, 3개는 이전에 LOGIC_TRACED로 읽었다.
- v1 스크립트 대부분은 여기서 실행할 수 없다. 퇴역한 `/home/z/my-project/NEW2_run` 경로, v1 자료, 저장소 밖 `cointrader`가 필요하다. 그래서 "실행해서 확인"한 것이 아니라 "읽어서 추적"한 것이다.

### 위험 판정(검토자 주장, 124개)
| 항목 | 없음 | 가능 | 확정 |
|---|---|---|---|
| 누수 | 98 | 21 | 5 |
| 비용 | 63 | 55 | 6 |
| 백테스트 오류 | 38 | 68 | 18 |

반복해서 나온 확정 결함(폐기 v1 진단 스크립트):
1. **가짜 OOS.** "OOS"가 셀을 고른 같은 구간의 마지막 40%다. 표본 밖 검정이 아니다.
   - 해당: c4, c5, c9, f1, f2x6_fng, x1, x1r, x5_stable, x8, x10, alt_funding_r2, x4f3_pair, x7, x9 등.
   - 일부는 PASS 판정에 OOS를 아예 쓰지 않는다.
2. **양측 Holm.** 유의하게 음수인 셀도 통과로 센다.
3. **겹치는 거래.** 봉마다 진입해 보유 구간이 겹친다. `valid_n = n/h` 보정만 한다.
   - 예: 일 단위 신호 하나를 15분봉마다 최대 96번 거래한다(f2x6).
4. **펀딩 누락.** 1~7일 보유인데 정액 12/16/20bp만 빼고 펀딩은 빼지 않는다(c9, f2x6_fng, x5).
5. **개별 결함.**
   - `l1_engine_crosscheck`: p_holm이 만들어질 때부터 모두 1.0이다.
   - `kp_diag`: Holm 집합을 n ≥ 5 셀로 줄여 보정이 느슨하다.
   - `liquid_search_dev` L881: TRANCHE_A에 없는 ZECUSDT로 교차검증한다. **작성자 확인.**
   - `x4f3_engine_crosscheck`: 체결 불가 거래를 조용히 버린다.

**영향:** 모두 폐기된 v1 진단이다. 현재 evolve 판정(DEV → 검증 StepM → robustness → 포트폴리오 → 잠금)에는 쓰이지 않는다.
- 이 스크립트들의 과거 "PASS"는 근거가 될 수 없다. 대부분 이미 NOT_PROMISING 또는 폐기로 끝났다.
- 출처 기록을 보존하려고 파일 자체는 고치지 않았다.

## 고친 코드 결함(현재 쓰이는 모듈)
1. **`store.ReverifyQueue.pop_ready` 충돌 창.**
   - 이전에는 큐 항목을 지우고 저장한 뒤에 PENDING으로 바꿨다. 그 사이에 멈추면 후보가 REVERIFY_PENDING에 큐 항목 없이 남았다.
   - `reconcile`은 이런 후보를 다시 넣지 않았다.
   - 수정: 먼저 PENDING으로 바꾸고 나서 항목을 지운다. `reconcile`은 항목 없는 REVERIFY_PENDING 후보를 다시 넣는다(`orphans_requeued`).
2. **`repro_bundle.verify_rerun`(dev).** 개수만 비교해서, 기록이 달라도 개수가 같으면 "재현됨"이었다. 수정: `evaluations.jsonl`·`statuses.jsonl`의 sha256도 비교한다.
3. **`newlisting_collect.load_state`.**
   - 이전에는 깨진 state.json을 빈 상태로 읽었다. 그래서 기준선을 다시 잡았고, 기존 심볼을 신규 상장으로 볼 수 있었다.
   - 수정: 파일이 없을 때만 `{}`를 돌려준다. 깨졌거나 dict가 아니면 `StateCorrupt`를 낸다. main은 오류로 끝난다.
4. **`funding_cost.funding_cost_detail` 정산 누락 미탐지.**
   - 구간 안에 정산 기록이 0건일 때, 보유가 정산 간격의 1.5배(8h 기준 12h)를 넘어야만 불완전으로 봤다.
   - 그래서 8~12h 보유에서 빠진 정산을 놓쳤다.
   - 수정: 간격의 1배를 넘으면 정산이 최소 한 번 있어야 한다. 없으면 `complete = False`다.
   - 이 함수는 현재 `portfolio_sim.py`만 쓴다. evolve 평가기는 자체 `FundingIndex`를 쓴다. 따라서 현재 판정 결과는 바뀌지 않는다.

## 문서 표시만 한 것(코드 수정 아님)
- `portfolio_sim.py`: 어디서도 쓰지 않는다. 현재 `engine_lib`에 `RUN`이 없어서 import도 실패한다.
- 알려진 결함:
  - `complete` 플래그를 버린다.
  - 청산이 있는 날만 sqrt(365)로 연환산한다.
  - 동시 보유 bp를 그냥 합산한다.
- 파일 머리말에 DEPRECATED와 결함을 적었다. 고치지 않았다. 포트폴리오 검증은 `portfolio_multi.py`(ADR 0068)가 맡는다.

## 남은 "가능" 항목(미수정, 표에 기록)
- `public_market_collect`: 한 주기의 모든 심볼에 주기 시작 시각을 찍는다. 수집 시각 오차는 최대 수 초이며 15분봉 판정에는 영향이 없다.
- `data_fetch`: 체크섬을 같은 호스트에서 받는다. 위변조를 막지 못하고 전송 오류만 막는다.
- `paper`: 컨텍스트당 1회 실행이 전제다.
- 나머지 POSSIBLE 항목은 `SOURCE_AUDIT_20261110.md`에 모두 적었다.

## 테스트
`tests/test_state_recovery_20261110.py`, `tests/test_funding_cost_legacy_20261110.py`(pandas가 필요한 레거시 모듈이라 의존성 검사 예외 목록에 추가):
- 고아 후보 재투입, 승격 후 삭제 순서, 기록이 다른 재현 거부, 깨진 신규상장 상태 오류, 9h 보유의 누락 정산을 테스트한다.
- 누락 정산 테스트는 수정 전 코드에서 실패하고 수정 후 통과하는 것을 확인했다.
