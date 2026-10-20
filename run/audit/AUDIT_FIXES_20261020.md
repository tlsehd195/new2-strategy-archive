# 아카이브 미해결 문제 수정·검증 보고서 (2026-10-20)

대상: `tlsehd195/new2-strategy-archive`. 기준 HEAD는 `9eefcf3`(main, PR #12 병합)이고, 작업 브랜치는 `claude/project-thread-xtayok`입니다. 지시에 따라 **커밋·푸시하지 않았습니다**. 변경분은 `archive_fixes_20261020.patch`에 있습니다.

> 연구 파이프라인이 정상 실행됐다고 해서 전략의 수익성이 증명된 것은 아닙니다. 이번 작업으로 통과 전략은 생기지 않았습니다(0개).

## 0. 기준선 (§2)

| 항목 | 확인 결과 |
|---|---|
| 브랜치 / HEAD / status | `claude/project-thread-xtayok` / `9eefcf3` / 작업 전 깨끗함 |
| 기준 테스트 | 427개 OK (498초). 단, NEW2 체크아웃(`COINTRADER_SRC`)이 필요했음 |
| CI (`9eefcf3`) | research-ci run 38 **success**. 이 성공은 `NEW2_READ_TOKEN`으로 비공개 NEW2를 체크아웃한 결과이며, 재시도(attempt 2)에서 났음. 같은 브랜치의 이전 run 34~37은 NEW2 체크아웃 단계에서 failure |
| R4 재집계 | 아래 표 참고. `evidence_audit_20261020/r4_statuses.jsonl`과 `r4_validation_batches.jsonl`에서 다시 계산함 |

R4 재집계 결과(문서 숫자를 쓰지 않고 원장에서 다시 계산):

| 항목 | 값 |
|---|---|
| 전략 수 | 777 |
| 상태 행 수 | 797 |
| DEV 기각 | 672 |
| DEV 미결 | 63 |
| DEV 통과 후 검증 대기(RESEARCH_ONLY) | 22 |
| 검증 배치 | 1개, 20개 전략 |
| 검증 결과 | 기각 7, 미결 13 |
| 미결 13의 원인 | 모두 INSUFFICIENT_DATA(검정력 부족) |
| 기각 7의 원인 | FAIL_COST 3, NO_EDGE_OUT_OF_SAMPLE 2, FAIL_STAT 2 |
| 통과 | **0** |
| 사용한 alpha | k=7 창별 alpha 0.00062 (promo-v2) |

## 1. 근본 원인과 변경 파일 (§9-1)

| # | 문제 | 근본 원인 | 변경 파일 |
|---|---|---|---|
| A | NEW2 의존 | 비용 공식이 NEW2의 `cointrader.backtest.costs`를 `COINTRADER_SRC`로 import했고, CI도 비공개 NEW2를 토큰으로 체크아웃함 | 신규 `cost_model.py`. `costs_bridge.py`, `engine_lib.py`(코드 해시 대상), `.github/workflows/research-ci.yml`, `run_cycle.sh`, `README.md` |
| B | 실제 체결로 비용을 검증할 경로 없음 | 거래내역 내보내기 파일을 검사하고 가져오는 도구가 없었음. 수수료 누락이나 수수료 자산 환산 규칙도 없었음 | 신규 `fills_import.py`, `config/fills_import_v1.json`. `cost_calibration.py`(cost-calib-v4), `.gitignore`(`private_fills/`) |
| C | 중복 탐지가 표면적 | 행동 키가 동치 형태(구간 교집합, 정수 피처, 조건 순서)를 정규화하지 않았음. 근사 중복 개념, 지문 버전, 계보 정보도 없었음 | `evolution.py`(fp-v2), `search.py`, `pipeline.py`, `autoloop.py`, `evolution_report.py` |
| D | 다중검정 family가 창 단위 | promo-v2는 BTC 창과 ETH 창에 각각 α=0.05를 써서, 탐색 전체의 FWER가 최대 0.10이었음 | 신규 `config/promotion_v3.json`. `pipeline.py`(`window_alpha_total`, `alpha_family`), `runner.py`(legacy 잠금 캠페인), `ops.py` |
| E | 상태·원장 불일치를 탐지할 도구 없음, 신호 처리 부족 | 원장 간 교차 검사 도구가 없었음. `run_cycle.sh`가 SIGTERM을 runner에 전달하지 않았음. evolve 실행 중 SIGTERM을 받으면 정리 없이 종료됐음 | 신규 `ledger_check.py`. `run_cycle.sh`, `runner.py`(`CycleInterrupted`, `--dev-only`), `autoloop.py`(`confirmatory`) |
| F | 상태 어휘 혼재 | 저장 상태(RESEARCH_ONLY, BLOCKED_* 등)를 지시문 어휘로 옮기는 매핑이 없었음 | `pipeline.outcome_label`, `cost_calibration.evidence_status`, `STATISTICS_AND_STATES.md` §10 |

## 2. 구현 내용 (§9-2)

### A. NEW2 독립 (§3)

- `run/discovery/cost_model.py`의 `CandleCostModel`이 비용을 계산합니다. 편도 비용 공식은 `fee_rate + half_spread + impact·sqrt(order/bar_quote_volume)`이고, `max_participation`을 넘으면 거부합니다. 파라미터에는 기본값이 없습니다.
- 출처와 가정은 docstring에 기록했습니다.
  - 원본: NEW2@`8ec6371` `src/cointrader/backtest/costs.py`, sha256 `27ec57bb…`, git blob `ca95b9c5`
  - 같은 소유자의 저장소이고 라이선스 파일은 없습니다. 공식은 코드를 복사하지 않고 재구현했습니다.
- 동등성 확인: 무작위 200,000건에서 결과가 원본과 완전히 같았습니다(불일치 0, 거부 73,984건도 동일). 증거는 `evidence_independence_20261020/cost_model_crosscheck.txt`입니다. 비교에 쓴 NEW2 사본은 세션 임시 폴더에만 두었고 저장소에는 넣지 않았습니다.
- 비용 모델의 정체성은 `archive.cost_model.CandleCostModel@sha256:<파일 해시>`로 바꿨습니다. 그래서 **실험 context id가 바뀌며**, 이전 결과는 재검증 대상(REVERIFY)이 됩니다.
- CI 변경:
  - NEW2 체크아웃 단계, `NEW2_READ_TOKEN`, `COINTRADER_SRC`를 제거했습니다.
  - 의존성 스캔에서 `cointrader` 허용을 없앴습니다.
  - 설정 검사에 `fills_import` 정책을 추가했습니다.
  - 이제 `NEW2_READ_TOKEN` 시크릿은 쓰이지 않으니 삭제해도 됩니다(사용자 판단).

### B. 체결 가져오기와 비용 상태 (§4)

`fills_import.py`는 Binance 거래내역 내보내기 파일(CSV/JSON, 열 이름 매핑 가능)을 받아 다음을 검사합니다.

- 열과 타입, 결측값, 중복(Trade ID, 없으면 내용 해시), 비정상 값(가격·수량·금액 일관성, 주변 체결 대비 가격 이탈, 캔들 범위 밖 가격, 수수료율 1% 초과)
- 시간 순서와 시간대(명시하지 않으면 UTC로 간주하고 기록. 미래 시각과 2019-09 이전 시각은 거부)
- **수수료 누락은 0으로 처리하지 않습니다.** 누락된 체결은 격리됩니다.
- 수수료 자산 환산: quote 자산이면 그대로 씁니다. 그 밖의 자산은 체결 시각 이전에 마감된 15분봉 종가나 사용자가 준 가격으로 환산하고, 방법·가격·시각을 체결마다 기록합니다. 가격이 없으면 미환산으로 남깁니다.
- 심볼별 표본 수. VERIFIED에는 심볼마다 200개 이상이 필요하며, 이 기준은 그대로 유지했습니다.
- 개인 데이터 보호:
  - 결과는 git에서 제외되는 `private_fills/`에 저장합니다.
  - 화면에는 개수와 해시만 출력하고 행 내용은 출력하지 않습니다.
  - API 키나 계정 호출은 쓰지 않습니다.

`cost_calibration`(v4)은 다음처럼 바뀌었습니다.

- 중복 행, 격리 행, 가져오기 실패가 있으면 `DATA_INVALID`
- 주문 결과(거부·부분 체결)를 데이터로 관찰할 수 없으면 `INSUFFICIENT_DATA`
- `synthetic_test` 데이터로는 절대 `VERIFIED`가 될 수 없음

지시문의 상태 어휘는 저장 상태를 늘리지 않고 `evidence_status()`라는 보기(view)로 연결했습니다.

| 지시문 어휘 | 대응하는 저장 상태 |
|---|---|
| VERIFIED_FROM_ACTUAL_FILLS | VERIFIED |
| ESTIMATED_FROM_PUBLIC_QUOTES | ESTIMATED_FROM_PUBLIC_DATA |
| INSUFFICIENT_DATA | INSUFFICIENT_DATA, BLOCKED_EXTERNAL_EVIDENCE |
| DATA_INVALID | DATA_INVALID, 봉인 불일치 |
| NOT_CALIBRATED | 기본값·가정 비용, 합성 데이터 |
| REVERIFY_REQUIRED | 다른 비용 모델이나 기준으로 만든 VERIFIED |

### C. 중복 탐지 fp-v2 (§6)

- 정체성 세 가지:
  - `definition_fingerprint`(DF-): 정의가 완전히 같은지
  - `behaviour_key`(BK-): 신호가 같은지. 같은 피처의 AND 구간 교집합, 정수 피처(`hour_utc`)의 정수 구간화, 연산자 정규화, 조건 순서 무시를 적용하고, 진입 신호를 바꾸지 않는 리스크·사이징은 제외합니다.
  - `near_signature`(NS-): 임계값을 변이 보폭의 절반으로 반올림합니다(z 척도 0.125, 0–100 척도 2.5).
- 처리 방식:
  - 정확한 중복은 대표 하나만 남기고, 버린 이유를 dedup_log에 기록합니다.
  - 근사 중복은 남겨 두되 표시합니다. 검증 배치에는 근사 그룹마다 하나만 들어갑니다. 같은 context의 이전 배치에 들어간 것도 셉니다.
- 계보 기록: 후보 ID, 부모, 세대, 연산자, 바뀐 파라미터, 시드, 지문 버전을 남깁니다.
- 결과는 같은 입력이면 같게 재현됩니다.
- 보고서에는 결정마다 대표, 중복, 이유가 표로 나옵니다.

### D. 검증 독립성과 다중검정 (§5)

- 추적 결과:
  - lookahead: DEV 지표와 검증 지표는 모두 이미 마감된 봉만 씁니다.
  - purge/embargo: `max(horizon, 96봉)`입니다.
  - walk-forward 접힘 안정성은 DEV 선별에서 확인합니다.
- 이번 변경에서 위 규칙은 바꾸지 않았습니다. 확인만 했고 결함은 찾지 못했습니다.
- promo-v3: 탐색 전체를 하나의 FWER family로 묶었습니다. 0.05를 지원 심볼 창 2개에 0.025씩 나누고, 창마다 6/π²/k² 비율로 alpha를 소진합니다. 배치 기록에 `alpha_family`가 남고, 잠금 캠페인(legacy 엔진 포함)도 같은 분할을 씁니다.
  - v2보다 엄격해지기만 했고 다른 값은 바꾸지 않았습니다.
  - 이미 v2로 소진한 7개 배치 때문에 **전체 이력 기준 FWER 상한은 약 0.096**입니다. 0.05 이하가 보장되는 것은 v3로 새로 시작하는 탐색뿐입니다.
- 심볼별 결과와 전체 결과는 분리해서 보고합니다. 데이터 부족(`INSUFFICIENT_DATA`→UNDETERMINED_POWER, `DATA_MISSING`)은 통계 실패(FAIL_STAT)와 구분합니다.
- `--dev-only` 실행 모드를 추가했습니다. 검증·잠금 창을 전혀 읽지 않고 alpha도 쓰지 않습니다.

### E. 상태·원장·실행 안전 (§7)

- `ledger_check.py`는 읽기 전용입니다. 종료 코드는 0(일치), 2(이상 발견), 1(읽기 불가)입니다.
  - 검사 항목:
    - 상태 전이표 위반, 상태 체인 끊김, 평가 기록 해시 위조
    - 같은 평가의 중복, 같은 context 안에서 같은 전략의 이중 배치 등록
    - 배치 결과 중복이나 고아 결과, 근거 기록 누락, PAPER_TEST_READY 중복
    - 요약과 원장의 불일치, 중단된 사이클
    - legacy 엔진의 state.json과 원장 불일치, 멈춘 RUNNING
  - 원본(source)과 파생(derived) 파일의 역할은 모듈 docstring에 표로 정리했습니다.
- `run_cycle.sh`:
  - 파이프라인을 백그라운드 서브셸로 돌리고 PIPESTATUS를 보존합니다.
  - TERM/INT/HUP을 받으면 runner에 SIGTERM을 전달하고, runner가 끝날 때까지 기다린 뒤 `CYCLE_FAILED reason=signal`(종료 코드 1)로 끝냅니다. 신호로 끊긴 사이클은 어떤 경우에도 성공으로 보고되지 않습니다.
  - 임시 파일은 EXIT trap에서 지웁니다.
  - 잠금은 runner의 `state/run.lock`(PID+flock) 하나로 관리합니다.
- `runner.py`: evolve 실행 중 SIGTERM/SIGINT를 받으면 `CycleInterrupted`가 발생합니다. 이때 요약에 CYCLE_FAILED가 기록되고 잠금이 풀린 뒤 종료 코드 1로 끝납니다. 같은 명령으로 다시 실행하면 이어서 진행하고, 중복 평가는 생기지 않습니다(테스트로 확인).
- 복구 절차(README): ① `ops.py health` → ② `ledger_check.py state` → ③ 일치하면 같은 명령으로 재실행하고, 그 밖의 이상이 나오면 중단하고 백업에서 복원합니다. 원장은 손으로 고치지 않습니다.

## 3. 테스트와 명령 (§9-3)

| 명령 | 결과 |
|---|---|
| `cd run && env -u COINTRADER_SRC python3 -m unittest discover -s discovery/tests` (NEW2가 없는 깨끗한 복사본) | **489개 OK** (561초, exit 0). 증거: `evidence_independence_20261020/full_suite_clean_copy.txt` |
| `python -m compileall -q run/discovery` | 통과 |
| `ruff check --isolated --select E9,F63,F7,F82 run/discovery` | 통과 |
| `ruff check --isolated --select F --exclude portfolio_sim.py,funding_cost.py run/discovery` | 통과 |
| `bash -n` + `shellcheck run/discovery/run_cycle.sh` | 통과 |
| `mypy` (신규 모듈 `cost_model`, `fills_import`, `ledger_check`) | 통과. CI에는 없는 추가 검사 |
| `ledger_check.py run/discovery/state` (저장소의 legacy 상태) | CONSISTENT |

신규 테스트는 62개입니다.

| 파일 | 개수 |
|---|---|
| `test_independence_20261020` | 8 |
| `test_fills_import_20261020` | 26 |
| `test_dedup_20261020` | 13 |
| `test_search_family_20261020` | 4 |
| `test_state_safety_20261020` | 11 |

기존 테스트 중 기대값을 바꾼 것은 3곳입니다. 모두 더 엄격해진 방향입니다.

- `test_audit_20261020`: 중복 행이 있는 체결의 결과를 NOT_VERIFIED에서 DATA_INVALID로 바꿈
- `test_audit_20261020`, `test_pipeline`: alpha 기대값을 0.05에서 0.05/2(v3)로 바꿈

## 4. 통과 / 실패 / 미실행 (§9-4)

- **통과**: 위 표의 항목들. 첫 전체 실행(489개)에서 legacy 잠금 캠페인 경로의 오류 11개를 찾았습니다. v3 설정에 `alpha_total_per_window` 키가 없어서 생긴 오류이며, 이를 고친 뒤 다시 실행했습니다. 관련 파일 3개(45개 테스트)는 OK였고, 전체 결과는 위 표에 적었습니다.
- **실패**: 최종 실행 기준 0개.
- **미실행**:
  - 이 변경분에 대한 GitHub Actions(푸시하지 않았으므로 CI 결과 없음. 성공을 추정하지 않음)
  - Python 3.11 매트릭스(로컬은 Python 3.13 하나)
  - 실제 계정 체결로 한 비용 검증(데이터 없음)

## 5. 독립성 결과 (§9-5)

- 엔진·테스트·CI 코드에는 NEW2 import, 체크아웃, 경로 가정이 없습니다.
  - AST 스캔과, docstring을 제외한 문자열 스캔으로 확인했습니다.
  - `cointrader`를 막은 하위 프로세스에서도 엔진이 import됩니다.
  - CI 파일에 `repository:`, `NEW2_READ_TOKEN`, `COINTRADER_SRC`가 없습니다.
  - 전체 테스트를 `COINTRADER_SRC`가 없는 깨끗한 복사본에서 실행했습니다.
- **남은 것(UNRESOLVED, 영향 없음)**:
  - `run/*.py`의 과거 1회성 스크립트들이 옛 작업공간 경로 `/home/z/my-project/NEW2_run`을 참조합니다. 엔진이나 CI 코드가 아니어서 그대로 두었습니다.
  - `NEW2_DATA_DIR`의 기본값도 옛 경로입니다. 엔진은 값이 없으면 fail-closed로 멈추므로 동작상 문제는 없습니다.

## 6. 실제 체결 데이터 존재 여부 (§9-6)

**없습니다.** 프로젝트 업로드에는 계정 체결 내보내기 파일이 없습니다.

- `fill_ledger.jsonl`(61줄)은 NEW2 maker 호가 모니터의 가상 호가 기록입니다. 실제 체결이나 수수료가 아니어서 쓰지 않았습니다.
- 가져오기 도구는 합성 데이터(`synthetic_test`로 표시, VERIFIED 불가)로만 시험했습니다.

## 7. 비용 상태 (§9-7)

**ESTIMATED_FROM_PUBLIC_QUOTES**입니다(저장 상태는 `ESTIMATED_FROM_PUBLIC_DATA`, 공개 호가 80시간으로 만든 estimate_v1).

- 실제 체결 검증 상태는 INSUFFICIENT_DATA(`BLOCKED_EXTERNAL_EVIDENCE`, 체결 0건)입니다.
- 이 상태로는 승격, 잠금 TEST, 포워드 페이퍼가 모두 막혀 있습니다.

## 8. 최신 코드로 재평가한 후보와 상태별 개수 (§9-8)

**새로운 독립 검증 창이 없습니다.** ETH 잠금 구간은 2026-08에 끝나는데 데이터는 2026-09까지라 남는 구간이 너무 짧습니다. BTC 잠금 이후 구간은 ETH의 DEV 구간과 겹칩니다. 그래서 검증 단계는 다시 돌리지 않고 **DEV만 재평가**했습니다(`--dev-only`).

실행 조건:

- 데이터: 업로드 f6379f02 (dataset sha256 5f0101f7…)
- 비용 프로필: public_estimate
- 탐색: 3라운드
- 깨끗한 복사본, NEW2 없음
- 09:31~10:20Z, CYCLE_OK
- 증거: `evidence_independence_20261020/dev_only_r5_*`

| 항목 | 값 |
|---|---|
| 등록 / DEV 평가 | 777 / 777 (시드 705 + 탐색 72) |
| 행동 동일 중복으로 제외 | 6 (교차 5, 리스크만 다른 것 1) |
| 근사 중복 | 0 |
| 저장 상태 | REJECTED 672 · INCONCLUSIVE 64 · RESEARCH_ONLY 41 |
| 지시문 어휘 (DEV 선별 기준이며 검정이 아님) | FAIL_STAT 440 · FAIL_COST 232 · UNDETERMINED_POWER 64 · PENDING 41 · **PASS 0** |
| 검증 배치 / 잠금 사용 / alpha 소진 | 0 / 0 / 0 (`validation_window_history.jsonl` 그대로) |
| `ledger_check` | CONSISTENT |

- 새 context(`C2-8ffa…`)에서 R4의 검증 결과(기각 7, 미결 13)는 **REVERIFY_REQUIRED**입니다. 비용 코드 정체성과 promo-v3가 바뀌었기 때문입니다. 새 독립 창이 생기기 전까지는 다시 검증할 수 없습니다(INSUFFICIENT_DATA).
- DEV 통과 41개는 검증 대기 상태일 뿐이며 성공이 아닙니다.
- DEV 실행 뒤에 legacy 엔진 경로를 한 곳 수정했습니다(항목 D의 잠금 alpha). evolve의 DEV 경로는 바뀌지 않았습니다.

## 9. 미해결 문제와 분류 (§9-9)

| 문제 | 분류 |
|---|---|
| 비용 공식 NEW2 의존 제거, CI 자급 | FIXED (CI 실행 결과는 TEST_REQUIRED) |
| 체결 가져오기·검사·수수료 환산 | FIXED (합성 데이터로만 검증) |
| 실제 체결 기반 비용 VERIFIED | BLOCKED — 사용자 계정 체결이 필요(심볼당 200개 이상) |
| 행동 기반 중복 탐지, 계보, 재현성 | FIXED |
| 탐색 전체 단위 다중검정(v3) | FIXED (앞으로의 탐색). 이력 전체 상한 약 0.096은 되돌릴 수 없으며, 기록만 해 둠 |
| 상태 전이 차단, RUNNING 고착, 중복 평가·승격, 원장·요약 불일치 | FIXED |
| run_cycle.sh pipefail·잠금·신호 정리 | FIXED |
| R4 검증 결과의 최신 코드 재검증 | INSUFFICIENT_DATA — 새 독립 창이 없음. REVERIFY_REQUIRED로 표시 |
| 이 변경분의 GitHub Actions | TEST_REQUIRED — 푸시 전이라 미실행 |
| Python 3.11에서 실행 | TEST_REQUIRED |
| 과거 run/*.py의 옛 경로, `NEW2_DATA_DIR` 기본값 | UNRESOLVED (엔진 밖이고 fail-closed라 영향 없음) |
| 포워드 페이퍼 | BLOCKED — PAPER_TEST_READY 전략과 VERIFIED 비용이 모두 없음 |

## 10. diff 검토

- 35개 파일이 바뀌었습니다(신규 파일, 이 보고서와 증거 파일 포함).
- API 키, 시크릿, 인증 정보, 개인 체결 데이터, 이메일은 없습니다. `NEW2_READ_TOKEN`은 삭제된 줄과, 그 이름이 없음을 확인하는 테스트에만 나옵니다.
- 관계없는 변경은 없습니다. 데이터 zip과 NEW2 사본은 저장소 밖에 두었습니다.
- 기존 사용자 변경 사항은 없었습니다(작업 시작 시 status 깨끗함).
- PR로 올릴 경우 ADR 0014를 추가합니다. README와 코드의 "directive 2026-10-20" 표기가 그 ADR을 가리키게 됩니다.
