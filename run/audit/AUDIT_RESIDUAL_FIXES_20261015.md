# NEW2 자율 전략 탐색 엔진 — 잔여 핵심 결함 수정 및 독립 검증 보고서

- 작성: 2026-10-15 (UTC)
- 대상: `tlsehd195/new2-strategy-archive` · `run/discovery/`
- 증거 파일: `run/audit/evidence_residual_20261015/` (재현 로그, 수치 검증, E2E, 전체 테스트 로그 2회)
- 통계·상태·회계 규칙 문서: `run/discovery/STATISTICS_AND_STATES.md`

**최종 판정: `PARTIAL`** — P0·P1·P2 결함은 모두 실제 코드에서 수정되었고 168개 테스트가 2회 연속 통과했다. 그러나 (1) 이 환경에 실제 캔들·펀딩 데이터가 없어 실데이터 사이클은 `BLOCKED_DATA_MISSING`, (2) 수정 코드는 런타임(`/home/z/my-project/NEW2_run`)에 배포되지 않았고 PR은 draft 상태, (3) 아래 "남은 위험"의 미해결 항목이 있다.

---

## A. 저장소 상태

| 항목 | 값 |
|---|---|
| 저장소 | https://github.com/tlsehd195/new2-strategy-archive |
| 브랜치 | `claude/discovery-residual-fixes-q7ddq4` (main에서 분기) |
| 시작 커밋 | `cc91f2d` (main HEAD, 작업 트리 clean) |
| 기능 수정 커밋 | `7923bed` |
| 보고서·증거 커밋 | 이 문서를 포함한 다음 커밋 (PR에서 확인) |
| 작업 트리 | 커밋 후 clean, `__pycache__` 미포함 |
| 비용모델 출처 | `tlsehd195/NEW2` @ `8ec6371` 의 `src/cointrader/backtest/costs.py` (읽기 전용 clone, 수정 없음) |

책임 범위: `new2-strategy-archive`는 탐색 엔진·원장·감사 문서, `NEW2`는 공유 비용모델(`CandleCostModel`)만 제공한다. 이번 수정은 `NEW2`를 변경하지 않았다(비용 실패 분류는 archive의 `costs_bridge.py`에서 수행).

지시문의 경로는 모두 실제로 존재했다(`runner.py`, `engine_lib.py`, `portfolio.py`, `promotion.py`, `evaluator.py`, `costs_bridge.py`, `run_cycle.sh`, `README.md`, `tests/`, `AUDIT_FINAL_감사보고서.md`, `AUDIT_REMAINING_ISSUES.md`). `generator.py`도 존재. 신규: `generation.py`, `STATISTICS_AND_STATES.md`, `tests/test_residual_fixes.py`.

### 시작 시점 테스트 (`evidence/baseline_before.log`)

`Ran 91 tests — FAILED (failures=1)`: `test_run_cycle_sh_duplicate_prevention` (1 != 3). 원인: `run_cycle.sh`가 `/home/z/...` 경로와 `/tmp` mkdir 락을 하드코딩 → 기존 결함이며 P1-10에서 수정. 이번 수정으로 생긴 실패와 무관.

### 결함 재현 (수정 전 코드에서 실행, `evidence/repro_before.log`)

| 재현 | 수정 전 결과 |
|---|---|
| D1 BH-FDR 순서 의존 | p=0.02 후보가 첫 평가면 trials=1로 생존, 마지막 평가면 trials=5로 탈락 |
| D2 MTM 누락 | 가격이 50% 하락 후 회복하는 동안 equity_curve가 10000 고정, MDD 0.0015 (실제 0.5) |
| D3 비용 fallback | 참여율 500%인 청산이 fee+half_spread(7bp)로 가격 책정, 경고 없음 |
| D4 잠금 기록 선행 | 데이터 로딩 전에 `record_use` → 데이터 없어도 사용 1회 |
| D5 환경 ID | 시그니처에 펀딩·FNG·비용 파라미터 없음 |
| D6 사전등록 | 다른 공간(3개 후보) 파일이 존재만으로 재사용, flag True |
| D7 run_cycle | `python3 "$DISC/runner.py" 2>&1 | tee ...` — 인자 `[batch]` 미전달 |

---

## B. 결함별 결과

### P0-1 BH-FDR 다중 검정과 후보 탐색 편향 — **FIXED**
- 기존 문제/원인: `_bh_fdr_survival()`이 "이미 원장에 있는 동일 context 후보 + 현재 후보"만으로 BH를 계산. 첫 후보는 m=1로 p≤0.05면 생존 → 평가 순서가 판정을 바꾸고, 779 후보 공간의 다중성이 반영되지 않음.
- 수정: `generation.py` 신설(`build_manifest`, `family_status`, `decide_family`, `FdrDecisionStore`), `engine_lib.bh_fdr_family`(m = 사전등록 전체 크기), `CandidateState.AWAITING_FDR` 추가, `runner.finalize_fdr`/`_resolve`.
- 핵심 변경: dev-PASS 후보는 `AWAITING_FDR`로 대기. (generation, context) 가족의 모든 후보가 dev 결과를 가질 때만 BH를 1회 실행, 결정은 append-only로 저장되고 재계산·덮어쓰기 없음. 검정값은 후보별 **첫** dev 기록. 검정 통계가 없는 후보(UNDETERMINED_POWER, DATA_MISSING)는 m에 포함되고 기각되지 않으며 p-value를 만들지 않는다. 전체 수로 p를 나누거나 보정 상수를 쓰지 않았다.
- 잠금 TEST 연결: 가족 미완료 → 생존자 없음 → 잠금 예약 자체가 일어나지 않음. FDR 탈락 → `FAIL_STAT`, 잠금 미소비. 생존 + 검증 PASS만 잠금으로 진행.
- 테스트: `TestGenerationFdr` 6개(순서 불변·조기 결정 없음·세대 해시·m 증가 시 느슨해지지 않음(200회 무작위)·재평가 비덮어쓰기·p 대체 없음), `test_engine.test_bh_fdr_gate_blocks_locked_consumption`(신규 규칙으로 재작성).
- 남은 위험: BH의 FDR 보장은 독립 또는 PRDS 가정하에서만 성립하며 후보 간 데이터 공유로 **PRDS는 미검증 가정**이다. 새 context(데이터·코드·비용 변경)에서의 재검정은 별도 가족이며 가족 간 FDR은 통제되지 않는다(1회용 잠금 TEST가 안전장치). 생성 공간 779개를 batch 10으로 돌리면 최소 78사이클 후에야 어떤 후보든 최종 판정이 가능하다. 잠금 없는 심볼의 데이터가 매일 늘면 context가 계속 바뀌어 가족이 완료되지 않을 수 있다(현재 BTC/ETH는 잠금 전날로 데이터가 고정되어 해당 없음).

### P0-2 포트폴리오 자산곡선·미실현 손익·MDD — **FIXED**
- 원인: `mtm`은 계산했지만 `equity_curve`에 실현 `equity`만 기록. 마지막 강제 청산이 곡선 기록 이후 실행되어 `final_equity ≠ equity_curve[-1]` 가능. 청산 우선 순서 오류(같은 봉 시가에 만기 청산될 포지션을 그 봉 저가로 강제청산).
- 수정: `portfolio.py` 재작성. `cash`(실현) / `unrealized` / `equity = cash + unrealized` / `used_margin` 분리. 봉 순서: 펀딩 정산 → 시가 청산·부분청산 → 시가 진입(진입 수수료 즉시) → 봉중 청산 → 종가 MTM 기록. 마지막 봉은 시가에 일반 비용으로 강제 종료(유리한 체결 가정 없음). 증거금 초과 진입 거절, equity ≤ 0이면 신규 진입 중단(`bankrupt`). `max_drawdown`은 같은 MTM 곡선, 초기자본을 첫 고점으로.
- 테스트: `TestPortfolioAccounting` 9개(상승·하락·미실현 MDD·부분청산·다중심볼·마지막 봉 보유·마지막 봉 강제종료·수수료+펀딩·회계 항등식).
- 남은 위험: 멀티심볼 포트폴리오는 심볼별 dev 구간이 다르면(BTC는 2023-09, ETH는 2025-10까지) 그리드 불일치로 실행되지 않고 `portfolio.error`로 기록된다(이전에는 평가 전체가 LOGIC_ERROR로 크래시). 단일 전략 승격에는 포트폴리오가 필수가 아니므로 영향이 제한적이고, 조합 승격은 증거 부재로 fail-closed.

### P0-3 거래 비용 실패 시 유리한 fallback — **FIXED**
- 원인: `close_position`이 `ValueError`에서 `fee_rate + half_spread`로 대체 → 충격비용과 참여율 거절을 무시한 더 싼 비용.
- 수정: `costs_bridge.CostContext.quote` — 사전 분류: 참여율 초과 `CostRefused`(FAIL_COST), 체결대금 없음/0/NaN `CostDataMissing`(DATA_MISSING), 입력·출력 비정상 `CostComputationError`(ERROR), 파라미터 누락·음수 `CostConfigError`(CandleCostModel의 Upbit 현물 기본값 누수 차단). 진입은 거절, 청산은 비용 없이 실행하되 `cost_status`로 표시해 `portfolio_passes`가 항상 실패. 이벤트 스터디: 거절·데이터 누락은 건너뛰고 별도 집계, 수치 오류는 전파(ERROR). 비용모델 적용 범위 `COST_MODEL_SCOPE` 기록, `test_only` 가짜 비용은 운영 러너가 거부. UNVERIFIED 비용으로는 승격 불가.
- 테스트: `TestCostFailures` 9개.
- 남은 위험: 캔들 모델은 호가창·실측 스프레드를 쓰지 않는다. 지시문의 "호가 데이터 누락"은 이 엔진에 호가 경로가 없어 체결대금 누락으로 대체 검증했다(N/A로 명시). 비용은 여전히 `UNVERIFIED_COST_MODEL`.

### P0-4 강제 청산 회계 — **FIXED**
- 원인: 강제청산은 `equity -= margin` 후 포지션만 삭제, 펀딩은 청산 시점에 일괄 차감하던 구조(청산 경로에서는 누적 펀딩이 사라짐), 진입 수수료 미반영.
- 수정: 청산 시 해당 포지션 증거금을 1회 손실, 별도 청산 수수료 없음(유지증거금이 패널티 역할), 이미 낸 진입 수수료·펀딩은 재차감 없음, 포지션 제거 후 현금·곡선 불변.
- 테스트: `TestLiquidation` 6개(전후 일관성, 수수료·펀딩 중복 방지, 포지션 제거, 유지증거금 경계 50.5/50.6 및 숏 149.5, 정상 청산 6000 vs 강제 청산 5000 = 4950 + mmr·명목 50).
- 남은 위험: **거래소 규칙이 아닌 엔진 가정**이다(마크가격, 단계별 MMR, 보험기금, 부분 청산 미모델).

### P1-6 잠금 TEST 상태 전이 — **FIXED**
- 원인: `record_use`가 잠금 구간 데이터 로딩·품질 검사 이전에 실행 → 데이터 없음도 사용 1회로 영구 기록. 메모리 기반 읽기-수정-쓰기라 동시 실행 경합 가능.
- 수정: `LockTestRegistry` v2 — `RESERVED → EVALUATION_STARTED → CONSUMED`, `BLOCKED_DATA_MISSING`, `FAILED`. 모든 심볼의 잠금 데이터를 RESERVED 상태에서 로딩·검사 후에만 `start`. 결과는 불변(재완료 시 예외). 사망한 소유자 복구: RESERVED → FAILED(비노출), EVALUATION_STARTED → FAILED(사용으로 계산). 모든 쓰기는 `fcntl.flock` + 원자적 저장. v1 기록은 `legacy_v1`에 원본 보존·사용으로 계산. `ProcessLock`도 flock 보호로 경합 제거.
- 테스트: `TestLockLifecycle` 10개(펀딩 누락, 캔들 누락, 품질 불량, 시작 전 사망, 평가 중 사망, 중복 실행·완료 후 재실행, 6개 프로세스 동시 예약 → 정확히 1개 성공, 결과 덮어쓰기 거부, v1 보존).
- 남은 위험: 다른 호스트 소유의 활성 시도는 생존 확인이 불가능해 자동 복구하지 않는다(수동 판단 필요).

### P1-7 실험 환경 ID와 데이터 무결성 — **FIXED**
- 원인: `experiment_context_id`가 캔들 해시·비용 버전 문자열·고정 `CODE_VERSION`만 사용. 비용 버전 문자열에 설치 경로가 들어가 경로 이동은 ID를 바꾸고 실제 소스 변경은 못 바꿈.
- 수정: `engine_lib.experiment_context(components)` → `C2-…`. 구성: 심볼별 캔들 해시·범위, 펀딩 시계열 해시, FNG 해시(없으면 명시적 missing), `DATA_POLICY`(UTC, 절단, 결측·이상치 규칙, 공개 시점), 비용 전체 파라미터 + 비용 소스 sha256 + verified/test 플래그, 엔진 소스 해시(`CODE_FILES`), 검증 설정, Python 구현·버전. 시각·uuid·호스트·경로 미포함, 실행 식별은 `run_id`/`evaluation_id`. git 커밋은 정보용 기록만. 정규화 구성요소는 `state/contexts/<C2>.json`에 1회 기록. 생성 세대의 심볼 합집합 전체로 계산해 FDR 가족이 쪼개지지 않게 함.
- 과거 호환: 구 `C-…` 기록은 그대로 두고, 다른 context에서 평가된 후보는 `context_changed`로 재평가 큐에 넣는다(덮어쓰기 없음).
- 테스트: `TestContextId` 6개(결정성, 펀딩·FNG·비용·코드·검증·정제규칙·런타임 변경, 구성요소 누락 거부, 실제 러너 입력 추적, 소스 해시, 구 기록 재평가).
- 남은 위험: 엔진은 stdlib 전용이라 Python 버전만 반영한다. 외부 라이브러리를 추가하면 `runtime_identity`도 갱신해야 한다.

### P1-8 사전등록 후보 공간 완전성·불변성 — **FIXED**
- 원인: `preregistration.json`이 존재하면 내용과 무관하게 재사용, 해시·ID 목록 없음.
- 수정: `build_manifest` — 모든 (sid, 정규화 정의)를 정렬, sid 재계산으로 사후 수정 탐지, 중복 거부, 생성기 버전·FDR 명세 포함 해시 → `G-…`. `PreregistrationStore`: `state/preregistration/<G>.json` 1회 기록(`os.link`로 경합 시 덮어쓰기 불가), 재로딩 시 해시 검증, 변조 시 `PreregistrationConflict`, `generations.jsonl`에 이전 세대 연결. 구 `preregistration.json`은 바이트 그대로 보존·재사용 안 함.
- 테스트: `TestManifest` 10개(후보 하나 변경, 순서만 변경, 중복, 수는 같고 ID 다름, 기본값 정규화, 사후 정의 수정, 규칙 변경 시 새 세대·구 파일 불변, 변조 거부, 레거시 미재사용, 실제 779 공간 반복 동일).
- 실제 공간: 779 후보, 7 패밀리, `G-b4b7ab1922a370f8` (현재 코드 기준, 두 번 생성 동일).

### P1-9 승격 게이트와 포트폴리오 검증 연결 — **FIXED** (조합 후보 생성기는 없음)
- 원인: `combo_promotion_gate`가 어디서도 호출되지 않았고 `portfolio_passed=True` 불리언만으로 SURVIVOR_COMBO 가능.
- 수정: 게이트가 증거를 받는다 — 매니페스트 소속, 최종 FDR 생존(`fdr_survived`, None이면 미확정), 독립검증, 레지스트리의 잠금 결과·사용 횟수(정확히 1), 검증된 비용모델. 조합은 실제 포트폴리오 결과 객체(결과 ID, context 일치, 비용·펀딩 적용, 격리마진, 동시보유 제한, 유한 곡선)를 검증하고 통과 기준을 게이트가 직접 재계산. 불리언 `portfolio_passed`는 거부. `runner._promote`가 정의(`family == "combo"` 또는 `legs`)에 따라 분기하고, 같은 후보는 큐에 두 번 등록하지 않음. 큐 등록 ≠ 실거래(주문 경로 없음).
- 테스트: `TestPromotionEvidence` 7개 + 기존 게이트 테스트 갱신.
- 남은 위험: 현재 생성기에는 조합 패밀리가 없어 조합 경로는 테스트의 조합 정의로만 실행된다.

### P1-10 run_cycle.sh 인자·종료 코드 — **FIXED**
- 원인: `[batch]` 미전달, `/home/z` 경로·`/tmp` 락 하드코딩, tee 실패 무시.
- 수정: 스크립트 위치 기준 경로, `[BATCH]`(양의 정수, 기본 `$NEW2_DISC_BATCH` 또는 10) 검증 후 `--batch`로 전달, `pipefail` + `PIPESTATUS`로 runner와 tee 종료 코드 보존, 실패 시 `CYCLE_OK` 미출력, 락은 runner의 `ProcessLock(state/run.lock)` 단일 구현. `runner.parse_args`도 잘못된 인자를 거부(argparse의 exit 2가 PARTIAL과 겹치지 않도록 1 반환). README 일치.
- 테스트: `TestRunCycle` 8개(1, 10, 기본값, 잘못된 값 5종, Python 오류 → 1, 로그 파이프라인 오류 → 1, 다른 작업 디렉터리, runner 인자 직접) + 기존 중복 실행 테스트(이제 통과).
- 남은 위험: shellcheck가 환경에 없어 `bash -n`만 실행했다.

### P2 상태 일관성 — **FIXED** (일부 의도적 유지)
- 수정: `ERROR`가 판정 우선순위 최상위, 알 수 없는 라벨은 ERROR. `cell_stats`: NaN/inf 수익 또는 [0,1] 밖 p → ERROR(0/1 대체 없음). `bh_fdr_family`는 비유한 p 거부. `portfolio_passes`는 비유한 지표 실패. UNVERIFIED 비용 PASS는 `claim_scope: UNVERIFIED_COST_MODEL`로 표시, 승격 불가. 큐에 남은 비세대 후보는 요약 notes와 errors에 기록(조용히 생략 안 함).
- 테스트: `TestStatusConsistency` 6개.
- 의도적 유지: 분산 0(n≥30)은 기존대로 `UNDETERMINED_POWER`. 통과로 이어질 수 없어 안전하나 엄밀히는 "검정 불가"와 "검정력 부족"이 같은 라벨이다.

### 재시작 복구 — 검증
`TestRecovery`: 사이클 중 RUNNING 상태로 남은 후보는 재시작 시 REVERIFY_PENDING으로 복구되고, 그 사이 가족은 미완료로 유지되며, 재평가 후 FDR 결정은 기대값과 동일.

---

## C. 테스트 결과

| 명령 | 종료 코드 | 결과 |
|---|---|---|
| `python3 -m unittest discover -s discovery/tests -v` (1회차) | 0 | Ran 168, OK (74.9s) |
| 같은 명령 (2회차) | 0 | Ran 168, OK (73.8s) |
| `python3 -m unittest discovery.tests.test_residual_fixes` ×3 | 0 | 77 OK ×3 |
| `python3 -m py_compile discovery/*.py discovery/tests/*.py` | 0 | — |
| `bash -n discovery/run_cycle.sh` | 0 | — |
| `ruff check --select E9,F discovery/` | 1 | 20건, 모두 손대지 않은 기존 코드(시작 시점 30건). 신규·수정 모듈의 신규 경고 0 |

- 전체 168 = 기존 91 + 신규 77. 통과 168, 실패 0, 차단 0, 건너뜀 0.
- 수정된 기존 테스트 6개(이전 동작을 그대로 고정하고 있었기 때문): `test_cycle_data_missing_flow`(구 사전등록 파일 → 해시 검증 매니페스트), `test_bh_fdr_gate_blocks_locked_consumption`(결함 동작이던 m=1 생존 → 가족 미완료 시 결정 없음), `test_gate_happy_path`·`test_combo_requires_portfolio`(기준 **강화**: FDR 확정·검증 비용·포트폴리오 증거 필요), `test_full_pass_registers_promotion`·`test_failing_locked_test_not_promoted`(사전등록 세대 + FDR 확정 경로로 실행, 승격 단계는 `test_only` 검증 비용 사용). 기준을 낮추거나 테스트를 삭제·비활성화한 것은 없다. (커밋 메시지의 "5 updated"는 6의 오기)
- 차단: 실제 데이터 사이클(`BLOCKED_DATA_MISSING` — 이 컨테이너에 `klines/`, `funding/` 데이터 없음). 테스트 수에 포함하지 않음.
- 로그: `evidence/full_suite_run1.log`, `full_suite_run2.log`.

---

## D. 수치 검증 (`evidence/verify_numbers.log`, `evidence/e2e.log`)

- 자산곡선: 가격 `[100,100,100,80,50,50,100,100,100,100]`, 100계약 롱 → 곡선 `[10000,10000,10000,8000,5000,5000,10000,…]`. MDD 손계산 (10000−5000)/10000 = 0.5, 프로그램 0.5 (수정 전 0.0015).
- 수수료+펀딩: 10000 − 10 − 1 − 10 = 9979, 프로그램 9979.0, `curve[-1]` 9979.0.
- 강제청산: 레버리지 2, 저가 50 ≤ 청산가 50.5 → 10000 − 10 − 5000 = 4990, 프로그램 4990.0, 수수료 10(1회).
- 비용 실패: 10,000 vs 1e8 → 0.0017; vs 1e5 → `CostRefused FAIL_COST`(참여율 10%); vs 0 → `CostDataMissing`. 청산 불가 포트폴리오: `cost_status FAIL_COST`, 진입 비용만 14.07bp(수정 전 7bp fallback, 상태 OK).
- 환경 ID: 기본 `C2-a00da5df857c090010fd`, 펀딩 해시 변경 `C2-b74e1e51efb05eb1f585`, 반복 동일.
- 후보 공간 해시: 6후보 `G-27ca3788a4b4cba1`, 역순 동일, 하나 제거 `G-75a52068e998d666`. 실제 779 → `G-b4b7ab1922a370f8`.
- 잠금 상태 전이: 데이터 없음 → `DATA_MISSING`, `[BLOCKED_DATA_MISSING]`, 사용 0 → 데이터 추가 → `PASS`, `[BLOCKED_DATA_MISSING, CONSUMED]`, 사용 1 → 재시도 → `FAIL_STAT`(이미 사용), 사용 1.
- E2E(합성 BTC 42개월, 1후보 세대, 운영 비용모델): dev `AWAITING_FDR` → FDR 가족 완료(m=1) → 잠금 1회 소비 → `PASS`, `claim_scope UNVERIFIED_COST_MODEL`, 승격 `['cost model UNVERIFIED — not promotable']`, 승격 큐 0건.

---

## E. Git 변경 사항

- 변경: `costs_bridge.py`, `engine_lib.py`, `evaluator.py`, `generator.py`, `portfolio.py`, `promotion.py`, `run_cycle.sh`, `runner.py`, `store.py`, `README.md`, `tests/test_engine.py`
- 신규: `generation.py`, `STATISTICS_AND_STATES.md`, `tests/test_residual_fixes.py`, `run/audit/AUDIT_RESIDUAL_FIXES_20261015.md`, `run/audit/evidence_residual_20261015/*`
- 규모(기능 커밋): 14 files, +3390 / −655
- 원장·상태 파일(`run/discovery/state/*`, 구 원장)은 변경·삭제하지 않았다. 잠금 TEST 데이터·실험 원장 삭제 없음.
- 커밋 `7923bed` 푸시 완료(`origin/claude/discovery-residual-fixes-q7ddq4`), 보고서 커밋 후 draft PR.

---

## F. 최종 판정: `PARTIAL`

완료 조건 점검:
- [x] P0 결함 코드 수정 · [x] P1 결함 코드 수정 · [x] P2 수정(분산 0 라벨은 사유 기록)
- [x] 신규 회귀 테스트 · [x] 기존+신규 실제 실행 · [x] 로그와 종료 코드
- [x] 미실현 손익·MDD 고정 사례 · [x] 비용 실패가 유리한 fallback이 아님 · [x] 잠금 예약/사용 구분
- [x] 환경 ID가 데이터·설정 변경 식별 · [x] 사전등록 완전성 · [x] 조합 승격 조건이 실행 경로에서 강제
- [x] 셸 인자·종료 코드 · [x] 문서와 코드 일치 · [x] 실거래·자금 이체 없음 · [x] 미해결 공개
- [ ] 실데이터 사이클 — `BLOCKED_DATA_MISSING` (데이터가 이 환경에 없음)
- [ ] 런타임 배포·실사이클 회귀 — 미실행 (draft PR, 사용자 검토 필요)

1. **엔진의 구조적 신뢰성**: 합성·고정 데이터 기준으로 통계 검정 순서(FDR), 비용 회계, 잠금 관리, 데이터·실험 식별이 의도대로 작동함을 168개 테스트와 수치 검증으로 확인했다. 실데이터에서의 동작과 BH의 PRDS 가정은 검증되지 않았다 → **조건부 개선(실데이터 미검증)**.
2. **전략의 실제 수익성**: **NOT_VALIDATED**. 비용모델은 미보정(`UNVERIFIED_COST_MODEL`), 실데이터·독립 검증 결과가 이번 작업에 없다. 엔진 테스트 통과는 수익성의 근거가 아니다.
