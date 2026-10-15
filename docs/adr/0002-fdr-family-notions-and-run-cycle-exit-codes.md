# ADR 0002 — FDR 가족 개념 분리, 과거 컨텍스트 PASS 처리, run_cycle 종료 코드 정책

- 상태: Proposed (PR #2, 머지 시 Accepted)
- 날짜: 2026-10-15
- 범위: `run/discovery/{generation.py,runner.py,engine_lib.py,run_cycle.sh}`
- 선행: [ADR 0001](0001-discovery-engine-residual-defect-fixes.md)

## 맥락
0001 이후 후속 감사에서 다음이 확인되었다.
- `family_status().complete`가 하나의 불리언으로 여러 의미를 가졌고, **다른 컨텍스트에서 얻은 terminal PASS를 현재 컨텍스트의 평가 완료로 계산**했다. 그 후보는 현재 환경의 통계값이 없는 채 m에 들어갔다.
- 저장된 FDR 결정은 입력 증거 확인 없이 그대로 반환되었다.
- 환경 ID에 전략 공간이 없었고, 펀딩 시리즈 해시가 행 순서에 의존했다.
- `run_cycle.sh`는 "runner 성공 + 로그 실패"만 처리했고, 로그 디렉터리 생성 실패와 빈 문자열 인자가 걸러지지 않았다.

## 결정
1. **완료 개념 4분리**: `manifest_complete`(목록·해시 무결), `current_context_complete`(모든 후보가 *현재 컨텍스트*의 dev 결과를 가짐), `fdr_decision_ready`(앞 둘 + 미등록 후보 기록 없음), `locked_test_eligible`(가족 속성이 아님: 결정은 `statistical_survivors`/`survivors_validation_pass`만 저장하고, 실제 TEST 진입 가능 여부는 실행 시점에 잠금 레지스트리의 소비 이력으로 판정 — 이미 소비한 후보는 심볼 하나만 소비했어도 진입 불가). `complete`는 `fdr_decision_ready`의 호환 별칭으로 유지하되 내부 로직은 구체 필드를 쓴다.
2. **다른 컨텍스트의 증거는 현재 평가가 아니다.** 과거 PASS·dev 기록은 `prior_context_pass`/`prior_context_only`로 원래 context_id·평가 ID와 함께 보존하고 `reevaluation_required`로 표시하며 가족을 미결정으로 둔다. 통계값이 없는 후보는 `without_statistic`으로 따로 기록한다(1.0 대체 없음).
   - *대안 검토*: (a) 과거 PASS를 그대로 인정 — 다른 환경 결과를 현재 결과로 쓰는 것이라 기각. (b) PASS를 되돌려 전체 재평가 — 잠금 TEST가 이미 소비되어 되돌릴 수 없고 PASS 불변 원칙에 반해 기각. (c) **채택**: PASS 상태와 잠금 TEST 결과는 그대로 두고 dev 단계만 현재 컨텍스트에서 재실행(`prior_pass_reeval` 원장 기록); 그 새 p-값이 BH에 들어간다.
3. **FDR 결정 불변성**: 결정에 `manifest_hash`, 방법·버전, q, m, `input_hash`(후보별 첫 평가 ID와 p)를 저장. 같은 증거(해시 일치)는 멱등, 다른 증거는 `FdrDecisionConflict`. `input_hash`가 없는 구형 결정은 결과값이 같아도 원래 입력을 확인할 수 없으므로 동일 증거로 인정하지 않고 충돌/보류로 처리(기존 결정은 덮어쓰지 않음)하며, 새 결정도 해시가 없으면 신뢰하지 않는다. — — 기존 결정은 유지하고 `fdr_decisions.conflicts.jsonl`에 기록하며 러너는 아무 후보도 해결하지 않는다.
4. **환경 ID 보강**: 전략 공간(generation id, manifest hash, 생성기 버전) 포함, 시리즈 해시를 행 순서 무관하게 변경. git commit은 ID에 넣지 않고 `contexts/<ID>.json`의 `informational`에만 기록(문서 커밋이 새 실험을 만들지 않도록; 실제 코드 변경은 소스 해시가 식별). `CODE_VERSION`은 라벨만 `discovery-v2.2.0`으로 변경.
5. **run_cycle 종료 코드 정책**: runner·로그 기록기·출력 축약기의 코드를 `PIPESTATUS`로 각각 보존하고 stderr와 감사 로그(`NEW2_DISC_AUDIT`)에 기록.

   | runner | 로그 | 종료 | 이유 |
   |---|---|---|---|
   | 0 | 실패 | 1 | 기록이 남지 않은 성공은 성공이 아님 |
   | ≥1 | 실패 | 1 | 둘 다 기록, runner 코드는 덮어쓰지 않음 |
   | 2 | 실패 | 1 | 부분 처리 기록 유실 → FAILED로 승격 |
   | 3 | 실패 | 3 | 아무것도 실행되지 않음, 상태 유지·로그 오류만 기록 |

   로그 디렉터리를 만들 수 없으면 runner를 시작하지 않는다. 빈 문자열 인자는 거부한다. 이 표(특히 2+로그 실패→1)는 사용자 규약 확인 없이 정한 정책이므로 변경 요청이 있으면 이 ADR을 대체하는 새 ADR로 바꾼다.

## 결과
- 테스트 212개(+44) 순차 2회 통과. 옛 동작으로 되돌리면 해당 테스트가 실제로 실패함을 변이 검증으로 확인.
- 환경 ID 값이 이전 판과 달라진다(이 저장소에는 실제 실행 기록이 없어 마이그레이션 불필요). 클라인은 파일 바이트 해시라 줄바꿈만 다른 동일 데이터도 다른 ID가 된다(보수적).
- 남은 위험: 과거 PASS 재평가 경로는 가짜 평가기로만 검증, 실데이터 미실행(`BLOCKED_DATA_MISSING`); PRDS 미검증; 비용 모델 미검증; shellcheck 미실행; NEW2 본 저장소 미통합. 판정 `PARTIAL`, 수익성 `NOT_VALIDATED`.
- 근거: `run/audit/AUDIT_FINAL_RESIDUAL_20261015.md`, `run/audit/evidence_final_20261015/`.
