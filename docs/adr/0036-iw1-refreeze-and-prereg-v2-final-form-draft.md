# ADR 0036 — IW1 멤버 재동결 초안과 사전등록 v2 최종 양식 초안, 후보 동결 리허설

- 상태: Accepted (초안·도구·측정만. 사전등록 활성화 없음, 통계 기준·비용 가정·기본 설정 그대로)
- 날짜: 2026-10-24
- 요청: 동동 (프로젝트 조정자 경유, 10-31 최종 확인 준비)
- 선행: [ADR 0023](0023-forward-window-prereg-v2-draft.md)(v2 초안), [ADR 0025](0025-walkforward-evalue-prereg-v3-draft.md)(α 7:3 분할),
  [ADR 0029](0029-dev-to-independent-flow-prereg-gate-repro.md)(게이트·코드 해시), [ADR 0030](0030-adopt-prpl-eb-bootstrap-t-pbo005-as-drafts.md)(IW1 v2 초안),
  [ADR 0033](0033-pr30-review-near-entry-duplicates.md)(후보 선택 v2), [ADR 0034](0034-ci-billing-v1-ledger-check-draft-members-vol-spread.md)(DRAFT 멤버 점검)
- 범위: `run/discovery/forward_window_freeze.py`(신규), `run/forward_v2_entry_bar_dev.py`(신규),
  `run/discovery/tests/test_refreeze_v2_20261024.py`(신규), 증거 `run/audit/evidence_refreeze_v2_20261024/`.
  평가 코드(`EVO_CODE_FILES`, `independent_window.py`)는 바꾸지 않았다 → 코드 해시·문맥 id 그대로.
- 열지 않은 것: 검증·잠금 TEST·IW1·종이 구간, 활성화 기록. 읽은 시장 데이터는 DEV 구간뿐(`strong_search_dev`의 금지 구간 검사 그대로).

## 맥락
- IW1 멤버 파일(2026-10-21)에는 평가 코드 해시가 없어 게이트가 "재동결 필요"로 보고했다(ADR 0029/0034).
- v2 초안은 `_DRAFT` 스키마라 영원히 ACTIVE가 될 수 없고, 후보 규칙이 두 군데 다르게 적혀 있었으며(members.rule vs D3의 ADR 0024 상위 5개), 동결 도구가 없었다.
- 10-31에 사용자가 최종 확인한다. 알트 호가 수집은 10-27에 끝나고 비용을 다시 추정한다.

## 한 일
1. **R9 DEV 전용 재실행** (main 491d073, R7/R8과 같은 명령, 62분): 894 평가 / DEV 통과 63 / 배치 후보 33,
   문맥 `C2-ccfa2ef38101803483e1`. 후보 목록이 R8과 순서까지 같고 등록 후보 목록 sha256 `ea04d733…`는 R6와 같다.
   재현 번들 `r9_dev_repro_bundle.json`.
2. **IW1 재동결 초안**: `independent_window.py freeze-members`를 IW1 v2 초안(bootstrap-t, ADR 0030)에 대해 실행 →
   `IW1_members_refreeze_DRAFT.json`(20개, members_sha256 `8e03e0cd…`, 코드 해시 고정). 예전 20개와 7개 겹침
   (후보 선택 v1/v2로 같은 진입의 쌍둥이가 빠진 결과). `prereg_gate`: DRAFT, 멤버 점검 문제 없음.
3. **IW1 DEV 점검**: 20개를 IW1 통계량(묶음 A 6종목 평균, `assumed_public_v1`)으로 DEV S1/S2에서 채점
   (`iw1_statistic_dev.py`). S1 연 샤프 **20개 모두 음수**(−0.52 ~ −3.80), S2 최고 0.71. 검정력(최선 경계, α 0.0304)
   최대 11.6%. 예전 사전 점검의 0.7~1.2는 BTC/ETH만, 엔진 DEV 비용(공개 시세 추정)으로 잰 값이었다.
4. **v2 최종 양식 초안** `forward_window_v2_FINALFORM_DRAFT.json`: 스키마 `forward_window_prereg_v2`, 필수 항목 누락 0,
   상태 줄의 DRAFT 표시로 게이트는 DRAFT. 결정된 숫자(창, 검정, α 0.0213, B 10000, D1~D5, 7:3 분할)는 그대로.
   바꾼 것: C1 후보 규칙을 기계적으로(검증 후보 순서 + 진입 기준, 처음 5개), C2 진입 기준 = S1 연 샤프 ≥ 1.0,
   멤버 파일 스키마 `forward_window_members_v2`, α 0.0213 기준 검정력 표(15종목·9종목), 동결 절차 8단계,
   10-31 질문 Q1·Q2(둘 다 `ALT_COST_DEPENDENT` 표시).
5. **동결 도구** `forward_window_freeze.py`: 검증 후보 순서를 유지하며 진입 기준을 넘는 처음 `members.max`개를 고른다.
   점수 파일의 종목·비용이 사전등록과 다르면 거부, 점수 없는 후보는 숨기지 않고 `registrable=false`.
   결과 파일은 `prereg_gate.check_members_file`로 검사된다. 5개 미만이면 `registrable=false`(D5).
6. **v2 리허설** (가정 비용, R9 후보 33개, `forward_v2_entry_bar_dev.py`): 15종목 바구니 **0/33 통과**
   (S1 최고 −0.40), 유동성 9종목 **0/33**(S1 최고 +0.17, S2>0은 19개). 지금 규칙으로는 v2가 등록되지 않는다.
7. **알트 비용 경로 확인**: 사용자 PC 수집 종목(SOL XRP DOGE BNB ADA AVAX LINK SUI LTC DOT)에 v2 바구니의
   TRX ZEC XMR NEAR XLM BCH가 없다. `cost_public_estimate_v2_alts.json`은 15종목 모두를 요구하므로 추정은
   INSUFFICIENT가 되고 D2(측정 비용으로 교체)는 그대로는 작동할 수 없다. 대안 기준 초안
   `cost_public_estimate_v3_alts_DRAFT.json`(필수 종목만 유동성 9종목의 알트 7개로, 나머지 기준 동일)을 만들었다.
   효력 없음. 쓰려면 수집 자료를 보기 전에 설정 파일로 커밋해야 한다.

## 10-31 결정 사항 (사용자)
- Q1 〔알트 비용 의존〕 바구니·비용: A 15종목+가정 비용 / **B 유동성 9종목+v3 알트 기준(추천)** / C 혼합(비추천).
  15→9종목에서 필요한 연 샤프는 2.27→2.30(ρ 0.6)으로 거의 같다.
- Q2 〔알트 비용 의존〕 등록 여부: 규칙상 진입 기준 통과가 5개 이상일 때만. 현재 0개. 비용이 정해지면 다시 센다.
- IW1: 닫아 두기 유지 추천(10-21 결정 조건 "연 샤프 ≳2.8 후보" 미충족).
- 쉬운 요약: `run/audit/evidence_refreeze_v2_20261024/10-31_판단요약.md`.

## 한계
- 모든 비용 의존 수치는 가정 비용(`assumed_public_v1`) 위의 값이다.
- DEV 수치는 같은 시대 자료라 검증 증거가 아니다.
- IW1 재동결 파일은 초안 사전등록에 묶여 있다. 실제로 열려면 최종 파일 작성 → 재실행 → 재동결이 다시 필요하다.
- 진입 기준 채점은 후보당 몇 분 걸린다(33개, 4코어 병렬로 약 1시간 반).
