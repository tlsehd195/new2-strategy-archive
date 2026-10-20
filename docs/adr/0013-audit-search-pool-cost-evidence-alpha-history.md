# ADR 0013 — 전체 감사(2026-10-20): 검색 풀 분리, 행동 중복, 비용 전후 비교, 비용 검증 상태 세분, 창 단위 알파 소비

- 상태: Proposed (이 브랜치 `claude/project-thread-vabu8n`)
- 날짜: 2026-10-20
- 범위: `run/discovery/pipeline.py`, `evolution.py`, `search.py`, `autoloop.py`, `runner.py`, `ops.py`, `cost_calibration.py`, `evolution_report.py`(신규), `validation_window_history.jsonl`(신규), `tests/test_audit_20261020.py`(신규), `tests/test_completion.py`(기대값 1건), `README.md`, `STATISTICS_AND_STATES.md`
- 선행: [ADR 0004](0004-auto-evolution-staged-validation-and-paper-review.md), [ADR 0006](0006-cost-verification-forward-paper-data-manifest-ops-ci.md), [ADR 0012](0012-evolve-real-data-run-public-estimate-cost.md)
- 사용자 결정(동동, 2026-10-20): 테스트 실행을 위해 NEW2 비용 모델 코드를 고정 커밋 `8ec6371`로 읽기 전용으로 받는 것을 허용했다. 데이터·API·파이프라인 연결은 만들지 않는다.

## 맥락
사용자 지시문(전체 감사 및 미완성 기능 완성)에 따라 코드를 직접 읽고 수정 전 코드(`main` `900c71c`)에서 문제를 재현했다. 재현 기록은 `run/audit/evidence_audit_20261020/repro_base.txt`와 `new_tests_on_base.txt`에 있다.

1. **검증 결과가 검색에 새어 들어감.** 강건성 단계는 StepM을 통과한 전략의 이웃을 DEV로 평가한다. 이 이웃은 검증 결과 때문에 생긴 것인데 `dev_feedback_all()`이 이웃까지 돌려주어, 다음 라운드의 엘리트 부모가 될 수 있었다. 실데이터에서는 StepM 통과자가 없어 실제로 일어나지는 않았다.
2. **행동이 같은 전략이 서로 다른 후보로 등록됨.** `all` 논리에서는 진입 조건과 필터가 모두 AND이므로 `진입[A]+필터[B]`와 `진입[A,B]`는 같은 규칙이다. `strategy_id`는 서로 달라 중복으로 걸러지지 않았다. 예산과 다중검정 인원을 낭비한다.
3. **비용 검증이 종목 하나의 과소추정을 놓침.** 판정은 합친 오차만 보았다. BTC를 1bp 과소추정하고 ETH를 3bp 과대추정한 합성 체결로 `VERIFIED`가 나왔고, 범위에는 두 종목이 모두 들어갔다.
4. **단위 오류를 받아들임.** 초 단위 타임스탬프, bp로 적은 수수료, 100배 가격이 모두 유효한 체결로 통과했다. 메모리에서 합친 관측치의 중복도 걸러지지 않아 `VERIFIED`가 나왔다.
5. **표본 부족을 실패로 기록함.** 체결 수가 기준(종목당 검증 체결 200건)보다 적으면 `NOT_VERIFIED`였다. 모델이 틀렸다는 뜻인지 증거가 적다는 뜻인지 구분되지 않았다.
6. **검증 창 알파 소비가 상태 폴더마다 처음부터 다시 시작됨.** 실데이터 실행(A1, A2, D1, R1, R2, R3)은 모두 새 상태 폴더에서 돌았다. 그래서 같은 검증 창을 볼 때마다 첫 배치 알파(0.05·6/π² ≈ 0.030)를 다시 썼다. 창 단위 FWER 보장은 폴더 단위로만 성립했다.
7. **중단된 사이클이 성공처럼 보임.** 사이클 요약 파일은 끝날 때만 쓰였다. 프로세스가 죽으면 이전 사이클의 `CYCLE_OK`가 그대로 남아 `ops.py health`가 OK를 보고했다.
8. **비용 전후 비교와 탈락 원인 구분이 없음.** DEV·검증 기록에는 비용 차감 후 평균만 있었다. 탈락이 비용 때문인지, 표본외 우위가 없어서인지, 통계적으로 약해서인지 기록에서 알 수 없었다.

## 결정
- **검색 풀.** 검색은 `register()`로 등록된 전략(시드와 검색 제안, 계보 기록이 있는 것)만 본다(`EvolutionPipeline.search_pool`). 강건성 이웃은 피드백과 알려진 id 집합에서 빠진다.
- **행동 키.** `evolution.behaviour_key`는 `all` 논리에서 진입 조건과 필터를 합쳐 해시한다. 제안 단계에서 알려진 키와 같으면 등록하지 않고 `semantic_duplicates`로 센다. `strategy_id`는 바꾸지 않았다(기존 id 안정성 유지).
- **비용 전후 비교.** DEV·검증 기록에 같은 사건의 총수익·비용·펀딩·순수익 평균(bp)을 넣는다(`pipeline.cost_attribution`). 검색은 이 값을 읽지 않는다(`DEV_FEEDBACK_KEYS` 그대로). 파라미터 재최적화는 하지 않는다.
- **탈락 원인.** 검증 기록과 상태 사유에 `failure_class`를 남긴다. 가장 나쁜 종목이 결정한다. `DATA_MISSING` → `INSUFFICIENT_DATA` → `NO_EDGE_OUT_OF_SAMPLE`(총수익 ≤ 0) → `FAIL_COST`(총수익 > 0, 순수익 ≤ 0) → `FAIL_STAT`(순수익 > 0, StepM 미기각) 순서다. 보고용이며 판정 기준이 아니다.
- **비용 검증 기록(`cost-calib-v3`).**
  - 상태를 `VERIFIED` / `NOT_VERIFIED` / `INSUFFICIENT_DATA` / `BLOCKED_EXTERNAL_EVIDENCE`로 나눈다. 검사 실패가 하나라도 있으면 `NOT_VERIFIED`, 실패는 없고 표본만 부족하면 `INSUFFICIENT_DATA`다.
  - 사전등록 기준(`cost_verification_v1.json`)의 평균·p90 과소추정 한도를 합친 오차와 **종목별** 오차에 모두 적용한다. 기준값은 바꾸지 않았다.
  - 단위 점검: ts는 ms이고 2017-01-01 ~ 현재+1일, 수수료 ≤ 체결 금액의 1%, 체결가는 기준가 ±5% 안, 과체결 없음, PARTIAL은 주문보다 작아야 한다.
  - `order_id`(선택)를 중복 키로 쓴다. 메모리 입력의 중복도 사유로 남긴다.
  - `load_observations_checked`와 CLI `--quarantine FILE`은 잘못된 행을 격리하고 나머지를 읽는다. 격리된 행이 있으면 `VERIFIED`가 될 수 없다.
  - 평균 오차의 95% 구간(정규 근사, 독립 가정), 수집 기간, 변동성·참여율 구간 커버리지를 기록한다(정보용).
- **창 단위 알파 소비.** `run/discovery/validation_window_history.jsonl`에 실데이터 검증 배치 6건(A1, A2, D1, R1, R2, R3)을 BTC·ETH 두 창 모두에 보수적으로 적었다. `runner.py --mode evolve`는 이 파일(또는 `NEW2_VALIDATION_HISTORY`)을 읽어 배치 번호를 `1 + 폴더 안 배치 + 외부 배치`로 매긴다. 파일이 없으면 실행을 거부한다(exit 1). 같은 폴더의 배치가 기록에 있으면 두 번 세지 않는다. 배치 기록에 `external_prior_batches`가 남는다. 기준은 완화되지 않고 더 엄격해진다.
- **사이클 상태 파일.** 시작할 때 `CYCLE_RUNNING`을 쓰고, 예외가 나면 `CYCLE_FAILED`로 덮어쓴 뒤 예외를 다시 올린다. 잠금이 풀린 상태에서 `CYCLE_RUNNING`이 남아 있으면 `ops.py health`는 `CYCLE_INTERRUPTED`(DEGRADED)로 보고한다.
- **보고 도구.** `evolution_report.py <state_dir>`는 원장에서 단계별 집계와 검증 멤버별 탈락 원인을 다시 계산한다. 상태 파일에는 쓰지 않는다.

## 결과
- 장점: 검증 창이 검색 데이터가 되는 경로 하나를 막았다. 비용 검증의 거짓 `VERIFIED` 경로 3개(종목 은닉, 단위 오류, 중복)를 막았다. 표본 부족과 실패를 구분한다. 실행 폴더가 바뀌어도 검증 창 알파 소비가 이어진다. 중단된 실행이 성공으로 보이지 않는다. 탈락 원인을 기록에서 셀 수 있다.
- 비용: 코드 해시가 바뀌어 새 컨텍스트가 된다(이전 결과는 재사용하지 않음). 다음 실데이터 검증 배치의 알파는 첫 배치보다 작다(R4는 7번째 배치, 알파 ≈ 0.05·6/π²/49).
- 남은 위험:
  - 히스토리의 과거 6건은 배치별 종목을 알 수 없어 두 창에 모두 적었다(보수적).
  - 행동 키는 `all` 논리의 진입·필터 중복만 잡는다. 임계값이 사실상 같은 조건 등 더 넓은 동치는 잡지 않는다.
  - 공개 호가 추정과 기본 비용은 여전히 미검증이다. `VERIFIED`에는 계정 체결 기록이 필요하다(외부 증거).
  - 엔진은 비용 계산을 NEW2 저장소 코드(`cointrader.backtest.costs`, 고정 커밋)에 의존한다. 사용자 원칙(NEW2와 연결하지 않음)과 긴장이 있으며, 이번에는 테스트 전용 읽기 전용 사용만 허용받았다.
