# ADR 0006 — 잔여 작업 완료: 비용 검증 절차, 전진 페이퍼, 데이터 매니페스트, 체결 시뮬, 운영·CI

- 상태: Proposed (PR #5, 머지 시 Accepted)
- 날짜: 2026-10-16
- 범위: `run/discovery/{cost_calibration,data_manifest,execution_sim,paper_forward,ops}.py`(신규), `{costs_bridge,data_fetch,pipeline,paper,autoloop,runner,handoff,engine_lib}.py`, `config/{cost_verification_v1,paper_forward_v1}.json`(신규), `tests/test_completion.py`(신규), `.github/workflows/research-ci.yml`(신규)
- 선행: [ADR 0005](0005-project-wide-audit-cost-gate-evidence-integrity-handoff.md), PR #4(Step-SPA·DM·MinBTL·비용 손익분기)

## 맥락
2026-10-16 지시문은 남은 단계 전체를 증거와 함께 끝내라고 요구한다. 대상은 PR #4 검토·통합, 데이터 수집·출처, 비용 검증, 통계 재검토, 잠금 TEST, 전진 페이퍼, 운영, CI다. ADR 0005 시점에 남아 있던 문제는 다음과 같다.
- 비용 모델을 검증된 것으로 만드는 절차가 없었다. `verified=True` 플래그만 있었다.
- 페이퍼는 과거 데이터 리플레이로 `PAPER_PASSED`를 줄 수 있었다.
- 데이터 다운로드에 재시도와 실패 분류가 없었다.
- 부분 체결, 거부, 청산 모델이 없었다.
- 운영 점검 도구와 CI가 없었다.

## PR #4 검토 결과
- **Step-SPA**: Hansen식 재중심화와 일치한다. **MinBTL**: 식 4와 6을 확인했다. 진단 전용이며, 극단값으로 패치해도 결과가 변하지 않음을 테스트가 증명한다. StepM 게이트는 그대로 유지된다.
- **결함 — DM 정렬**: 자식과 부모의 결측일을 0으로 채웠고, 심볼 집합이 서로 달랐다. 이제 공통 심볼·공통 날짜에서만 비교한다. 최적 부모도 그 격자에서 고르고, 비교 범위를 기록한다. 겹치는 구간이 없으면 `NOT_COMPUTED`다.
- **손익분기**: 손익분기 배수가 검증으로 오인되지 않도록 `breakeven_basis`에 비용 상태(`UNVERIFIED_COST_MODEL` 등)를 함께 기록한다.

## 결정
1. **비용 검증은 기록으로만 가능** (`cost_calibration.py`):
   - 운영 경로의 `CostContext(verified=True)`는 봉인된 `CV-…` 검증 기록이 있어야 생성된다. 이 기록은 `check_record`를 통과하고, 매개변수가 동일해야 한다. 플래그만 바꾸면 `CostConfigError`가 난다.
   - 기록 절차: 체결 보고서를 시간순으로 나눠 앞부분으로 수수료·스프레드·충격 계수를 적합한다. 뒷부분에서 과소추정 오차(평균·p90)와 거부율을 검증한다.
   - 이 기록은 `exchange_fills` 출처에서만 VERIFIED가 될 수 있다. 호가 스냅샷이나 페이퍼 감시 로그로는 스프레드만 볼 수 있으므로 `BLOCKED_EXTERNAL_EVIDENCE`다.
   - 검증 범위(심볼, 명목금액 구간) 밖의 주문 규모는 `verified_for`가 거부한다.
   - 기록 해시는 실험 컨텍스트 ID에 들어간다. 비용 모델이 바뀌면 새 컨텍스트가 되고, 이전 진행 전략은 `REVERIFY_PENDING`으로 보고된다.
2. **페이퍼 판정 분리**:
   - 과거 리플레이(`paper.py`)는 더 이상 `PAPER_PASSED`를 주지 않는다. 일치하면 `PAPER_TESTING`에 머물고, 악화되면 `PAPER_FAILED`다.
   - `PAPER_PASSED`는 `paper_forward.py`에서만 나온다. 이 모듈은 등록 시점 이후에 마감된 봉으로만 관측한다. 판정 기준은 사전 등록된 `paper_forward_v1.json`이다(최소 60일·30거래, 최대 180일, 1.5배 비용 스트레스, MDD 2500bp, 결측 1%, 지연 10%, 청산 0).
   - 봉 로그는 해시 체인이다. 기록된 거래가 바뀌면 `CausalityViolation`이 나고, 변조되면 ERROR다.
   - 기한 안에 결론이 나지 않으면 `INCONCLUSIVE`다. 이를 위해 전이표에 `PAPER_TESTING → INCONCLUSIVE`를 추가했다.
   - 파일 피드는 `OBSERVED_FROM_ARCHIVE_NOT_REALTIME` 라벨이 붙는다. 네트워크 피드는 명시적으로 허용해야 하며, 공개 시세만 받는다.
   - `PAPER_PASSED`도 실거래 승인이 아니다.
3. **체결 시뮬** (`execution_sim.py`): 참여율 상한을 넘는 주문은 거부(`refuse`, 기본)하거나 부분 체결(`cap`)한다. 봉 안의 최악 가격으로 청산을 판정하고, 관측 지연을 기록한다. 레버리지 기본값은 1이다.
4. **데이터**:
   - 다운로드는 일시적 실패만 2·4·8초 간격으로 재시도한다. 실패는 `NOT_PUBLISHED`, `PROXY_ERROR`, `EXCHANGE_ERROR`, `TRANSIENT`로 분류한다.
   - 출처 기록은 `provenance_v2`(소스, 코드 버전, 품질 요약)다.
   - `data_manifest.py`는 파일별 SHA-256, 월 경계·마감시각·미래 펀딩·이전 매니페스트 대비 변경을 점검한다. 차단 항목이 있으면 종료 코드 4다.
5. **운영·CI**:
   - `ops.py health`는 읽기 전용이다. 잠금, 원장 손상, 증거 변조, 설정 스키마, 디스크를 점검한다.
   - `ops.py loop`는 최대 횟수가 필수다. 실패, 잠금 보유, 데이터 없음, 무진전 시 멈춘다. 알림은 설정하지 않았고, 로그 파일이 기록이다.
   - CI(`research-ci.yml`)는 컴파일, ruff(구문 오류·미정의 이름 게이트), shellcheck, 설정 스키마, stdlib 전용 의존성 검사, 전체 단위 테스트를 Python 3.11·3.13에서 돌린다. NEW2는 고정 커밋 `8ec6371`으로 읽기만 한다.
6. **인계** `new2_research_handoff_v2`: 페이퍼 종류, 전진 관측 기록 ID와 판정(없으면 `NOT_RUN`), 비용 검증 기록을 싣는다.

## 설정·기준 변경 기록
| 항목 | 이전 값 | 새 값 | 사유 | 컨텍스트 영향 |
|---|---|---|---|---|
| `promotion_v2.json` | — | 변경 없음 | — | 없음 |
| `cost_verification_v1.json` | 없음 | 신규. 검증 비율 0.5, 심볼당 200건 이상, 평균 과소추정 ≤0.5bp, p90 ≤2.0bp, 거부율 ≤5% | 검증 절차 신설 | CODE_FILES 포함 → 새 컨텍스트 |
| 평균 과소추정 한도 | 0.0bp (초안) | 0.5bp | 표본 잡음만으로 불통과(합성 0.003bp). 실데이터 관측 **전에** 정했다. | 위와 같음 |
| `paper_forward_v1.json` | 없음 | 신규(위 2) | 전진 판정 사전 등록 | EVO_CODE_FILES 포함 → 새 컨텍스트 |
| 리플레이 `PAPER_CONSISTENT` | `PAPER_PASSED` | `PAPER_TESTING` 유지 | 과거 리플레이는 전진 증거가 아님 | 코드 해시 변경 → 새 컨텍스트 |
| 인계 스키마 | v1 | v2 | 전진·비용 증거 필드 | 인계 파일만 |

이전 컨텍스트의 PASS·상태는 재사용하지 않는다. 기존 원장과 잠금 TEST 기록은 건드리지 않았다.

## 결과
- 비용: 실제 체결 데이터가 없다. 저장소의 유일한 실행 관련 데이터(`data/fill_ledger.jsonl`, NEW2 가상 호가 16건)로 돌리면 `BLOCKED_EXTERNAL_EVIDENCE`다. 따라서 실데이터 경로에서는 여전히 어떤 전략도 `PAPER_TEST_READY`가 될 수 없다. 의도된 동작이다.
- 페이퍼: 전진 엔진은 합성 테스트로만 검증했다(`UNIT_TESTED`). 실제 미래 데이터로 관측한 적은 없다.
- 잠금 TEST: 실데이터 실행에서 검증 생존자가 없어 열지 않았다.
- 남은 위험: 교차 자산 누출(ETH 개발·검증 기간이 BTC 잠금 창과 겹친다), BH/PRDS 가정, 정상성 가정, PBO 0.5 기준이 관례 0.05보다 느슨함(변경하지 않음).
- 근거와 실행 로그: `run/audit/AUDIT_COMPLETION_20261016.md`, `run/audit/evidence_completion_20261016/`.
