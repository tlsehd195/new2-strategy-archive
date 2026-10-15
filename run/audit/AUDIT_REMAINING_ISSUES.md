# AUDIT_REMAINING_ISSUES — Phase 0 조사 (지시문 §2)

- 작성: 2026-10-15, 후속 감사 프로젝트 Phase 0
- 조사 대상: 런타임 `/home/z/my-project/NEW2_run` (라이브) + `tlsehd195/new2-strategy-archive` @ `85325ca` + `tlsehd195/NEW2` (공개, 비용모델 출처)
- 기준 커밋: archive `main` = `85325ca` (브랜치 단일, main), 공개 NEW2 = ADR 데이트 트레이딩 프로젝트
- 원칙 선언: 본 조사 시점까지 **기존 원장 0행 수정, 실패 기록 0건 은폐**. 상태 스냅샷은 `audit/evidence_phase0/`에 보존.

## 0. 전제가 된 환경 사실

1. 이전 감사(Task 55)에서 v2-audit2(16건 수정+14종 테스트)가 **push 없이 소실**됨(F-26) → 현재 `discovery/` v2는 그 **재구현**임 (engine_lib.py 헤더에 REBUILD notice 명시).
2. 현재 v2 재구축 코드는 2026-10-15T02:40~04:04Z 작성, 04:04 사이클 1회 실행됨(`SUCCESS_WITH_WARNINGS`, 779 후보 등록, evaluated 4, errors 0).
3. 공개 저장소의 `src/cointrader/backtest/costs.py`는 로컬 클론 `/home/z/my-project/NEW2_public_clone`에서 실제 임포트 확인됨(COSTS_SOURCE_OK).

## 1. 16개 지적사항 × 현재 코드 매핑 (전수 판독 결과)

| # | 지시문 항목 | 현재 상태 | 근거 위치 | 남은 갭 |
|---|---|---|---|---|
| 1 | 재검증 큐/상태 전이 | **구현됨** | engine_lib `CandidateState` 9-state + `ALLOWED_TRANSITIONS` + `reverify_eligible()`(§3-2 규칙 유일 구현부), store `ReverifyQueue` 백오프, `reconcile()` 재시작 복구, RetryRecord 8필드 | 테스트 없음 → R-1 |
| 2 | strategy_id/evaluation_id/실험환경 ID 분리 | **구현됨** | engine_lib `strategy_id`(정규화 전략만), `evaluation_id`(실행마다 신규), `experiment_context_id`(dataset+cost+code+cfg) | 테스트 없음 → R-1 |
| 3 | 의미적 중복 제거 | **구현됨** | generator `enumerate_candidates()` 정규화 키 기반 병합 + collapse 로그, `normalize_strategy` 기본값/float 정규화 | 테스트 없음 → R-1 |
| 4 | 다중 검정/선택 편향 | **부분 구현** | 후보 내 Holm(`_symbol_status`), HAC(NW), 블록부트스트랩, DSR, PBO-CSCV, 겹침 방지 `select_non_overlapping` | **후보 간 BH-FDR이 어디에도 적용 안 됨**(구현만 존재), 시행(trial) 장부·사전등록 기록 부재 → R-2, R-3 |
| 5 | 복수 심볼 합격 | **구현됨** | engine_lib `verdict_from_cells`: 필수 심볼 전부 PASS여야 합격, DATA_MISSING은 불확정, `single_symbol_pass_hint` 분리 힌트, `split_single_symbol_candidate` | 테스트 없음 → R-1 |
| 6 | Stouffer | **재설계 완료** | `stouffer_independent`(z-score 입력, sqrt(n) 이중가중 제거), `stouffer_correlated`(추정 상관행렬, ρ-그리드 max 제거), `estimate_corr_dev`(dev 전용, CI 폭 검사, 실패→None→UNDETERMINED) | 테스트 없음 → R-1 |
| 7 | 독립 검증 기간/잠금 TEST | **구현됨** | `LOCKED_TEST_PERIODS` 읽기전용, `assert_no_locked_overlap`(선택 단계 차단), `split_windows` embargo=horizon, `LockTestRegistry` 1회성 사용기록, 심볼별 독립 창, NO_LOCKED_TEST 표시 | 테스트 없음 → R-1 |
| 8 | 데이터 품질/버전 | **구현됨** | data_quality `check_bars`(중복/역전/결측/OHLC/음수/급변), `funding_quality`, `staleness`, sha256 dataset_id, 품질 게이트 fail-closed, FNG 발표시점 분리(`build_aux` fng_hist=d-1..), 펀딩 calc_time≤봉마감 | 테스트 없음, REFRESH=1 미처리 → R-1, R-4 |
| 9 | 보유 중 펀딩비 | **구현됨** | evaluator `funding_paid_bp`(보유구간 (start,end] 실제 정산, 미커버→None→이벤트 폐기, 추정 금지), 모든 패밀리에 적용(A-17) | 테스트 없음 → R-1 |
| 10 | 포트폴리오 시뮬레이션 | **구현됨** | portfolio.py: 자본/사이징/동시보유제한/동일심볼 충돌/체결시점/비용/펀딩/강제청산(고립증거금)/MDD/자산곡선, 그리드 불일치 하드에러. runner가 dev-PASS에 실행 | 테스트 없음 → R-1 |
| 11 | 비용 모델 일관성 | **구현됨** | costs_bridge: 공개 repo `CandleCostModel` 단일 구현 임포트, `UNVERIFIED_COST_MODEL` 명시, 민감도 시나리오 5종 정의 | 시나리오 미실행(테스트 대상), 테스트 없음 → R-1 |
| 12 | 버전 관리 | **구현됨** | CODE_VERSION/VALIDATION_CFG_VERSION/LEDGER_SCHEMA/STATE_SCHEMA, 레코드에 dataset/cost/code/validation 버전 전부 기록 | 테스트 없음 → R-1 |
| 13 | 테스트 재현성 | **미해결 — 최대 갭** | **저장소에 테스트 파일이 0개** (이전 14종 테스트는 소실 F-26) → R-1 (최우선) |
| 14 | 문서 불일치 | **미해결** | README/핸드오버가 v1 설명 또는 부재, audit 문서가 실코드와 불일치 → R-5 |
| 15 | 승격 절차 | **부분 구현** | promotion.py 게이트(사전등록/독립검증/잠금 1회성/포트폴리오) + PromotionQueue append-only 존재 | **runner가 게이트/큐를 호출하지 않음**(연결 누락) → R-6 |
| 16 | 최종 감사 보고서 | 미작성 | Phase 14에서 `run/audit/AUDIT_FINAL_감사보고서.md` 작성 예정 → R-7 |

## 2. 이력 코드 의심점 15항목 × 재구축 버전 판정

| 이력 의심점 (v1) | v2 판정 |
|---|---|
| load_tested_hashes()가 UNDETERMINED/ERROR 영구 스킵 | **해소** — 상태 머신에서 UNDETERMINED/ERROR는 `reverify_eligible` 경로로 재검증 가능 |
| verdict_from_cells 단일 셀 통과=전체 통과 | **해소** — required 전부 PASS 요구 |
| combo 변형 해시가 원파라미터 사용 | **해소** — `strategy_id`는 정규화된 실제 정의 기준, `_defaults` 드롭 |
| run_cycle.sh pipefail 부재 | **해소** — `set -uo pipefail` 확인 |
| Stouffer ρ 그리드 max | **해소** — ρ 추정(dev 전용)+CI 검사, 그리드 제거 |
| REFRESH=1 무효 | **미해소** — v2는 REFRESH를 아예 처리하지 않음. 명시적 거부 경고 필요 → R-4 |
| 원장 중복 9쌍(62행 vs 53 해시) | **해소** — v2 장부는 strategy_id 기반, `Ledger.duplicate_strategy_records()`로 중복 이력 조회 가능(재평가는 신규 evaluation_id로 정상 다중 행) |
| 하드코딩 경로 | **부분 해소** — PathConfig 환경변수화(fail-closed). 단 data_dir 기본값에 절대경로 1곳 존재(환경변수로 오버라이드 가능, 문서화) |
| last_status="ok" 은폐 | **해소** — 5단계 실행상태 + errors 카운트 + PARTIAL_FAILURE 임계(30%) |

## 3. 잔여 문제 목록 (수정 대상)

| ID | 심각도 | 내용 | 수정 계획 | 필요 테스트 |
|---|---|---|---|---|
| R-1 | **치명** | 자동화 테스트 0개 — 재구축 코드 전체가 무증명 | `discovery/tests/`에 unittest 스위트 작성(합성 데이터+임시 디렉터리), A~G 7영역 커버 | 전 스위트 |
| R-2 | **높음** | 후보 간 BH-FDR 미적용 — 779 후보 탐색의 선택편향이 잠금 TEST 소비 전에 차단되지 않음 | `_evaluate`에서 잠금 소비 **전** 장부 내 동일 데이터셋 컨텍스트 dev p-value 모집단에 BH-FDR 적용, 미생존→FAIL_STAT(잠금 미소비) + 시행 수 기록 | 합성 다중 후보로 FDR 게이트 동작 검증 |
| R-3 | 높음 | 사전등록/시행 장부 부재 (§5-2) | `register_all()` 시 preregistration.json 기록(생성시각/생성규칙버전/후보수/패밀리 그리드), 레코드에 n_trials 포함 | 사전등록 파일 생성/내용 검증 |
| R-4 | 중간 | REFRESH=1 명시적 처리 부재 (§9-1) | runner main에서 REFRESH=1 감지 시 경고 출력+요약에 기록(미지원 명시) | REFRESH 경고 검증 |
| R-5 | 중간 | 문서-코드 불일치 | Phase 14에서 README/핸드오버/정책 문서 갱신 | 수동 검토 |
| R-6 | 높음 | 승격 게이트 미연결 | `_evaluate` PASS 경로에서 promotion_gate 실행, eligible→PromotionQueue.register | 게이트 통과/실패/중복 등록 테스트 |
| R-7 | 중간 | 최종 보고서 부재 | Phase 14 작성 | — |

## 4. 현황 통계 (읽기 전용 재계산, 2026-10-15T04:04Z 기준)

- 후보 공간: **779** (flow_z 360 · ret_rev 120 · ts_mom 60 · vol_break 60 · tod 60 · fund_z 45 · fng 74) — `enumerate_candidates()` 실측, 중복 병합 0
- v2 원장(`state/discovery_ledger.jsonl`): 30행 (schema discovery_ledger_v2) — 판정 분포는 §C에서 최종 재계산
- 구버전 원장(archive `run/discovery/discovery_ledger.jsonl`, v1 사이클 로그 160행): **보존, 미병합**
- 재검증 큐: 8건, 승격 큐: 0건, 검색공간 상태: REVERIFY_PENDING
- 잠금 TEST 구간: BTC 2024-03-25~06-18 / ETH 2026-04-28~08-31 — **건드리지 않음**

## 5. 수정 순서와 보존 약속

R-3 → R-2 → R-6 → R-4 → R-1(테스트, 최대 작업) → 회귀(실사이클 1회) → R-5/R-7(문서) → push.
모든 테스트는 임시 디렉터리+합성 데이터만 사용. 실원장/상태/잠금구간은 테스트가 절대 건드리지 않음. 코드 수정 전 현재 상태 스냅샷을 `audit/evidence_phase0/`에 보존.

## 6. 후속 — 잔여 핵심 결함 수정 (2026-10-15)

후속 감사에서 지적된 잔여 결함(후보 간 BH-FDR이 평가 순서·시점에 의존, 포트폴리오 곡선의 MTM 누락, 비용 실패 시 유리한 fallback, 강제청산 회계, 잠금 TEST 사용 기록 선행, 환경 ID 불완전, 사전등록 재사용, 조합 승격 미연결, run_cycle.sh 인자·종료 코드)의 수정·검증 결과는
`AUDIT_RESIDUAL_FIXES_20261015.md` 와 `evidence_residual_20261015/` 에 있다. 이 문서의 위 표는 작성 당시 기록으로 그대로 보존한다.
