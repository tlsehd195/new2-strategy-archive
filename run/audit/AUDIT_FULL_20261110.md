# 전수 감사 보고서 — 2026-11-10 (지시문 §19 형식 A~G)

- 요청: 동동, 2026-11-10 06:33Z 「NEW2 전략 아카이브 저장소 전체 정밀 감사 및 문제 전면 개선 지시문」
- 감사 기준 커밋: `ae17753`. 이 커밋은 감사 시작 시점 main에 PR #64가 병합된 상태다. 실제 시작 시점의 main은 `a732ed6`이었다.
- 변경은 주제별 PR 다섯 개로 나눴다. ADR 0063~0067이 하나씩 붙어 있다.

| PR | ADR | 주제 |
|---|---|---|
| #64 | 0063 | 비용 증거 |
| #65 | 0064 | 통계·승격·검정력 |
| #66 | 0065 | 실행 모드·상태 무결성 |
| #67 | 0066 | 독립 연구 통합·피처·데이터 보호 |
| #68 | 0067 | CI·문서·이 보고서 |

- 실거래 주문, 실계좌 연결, 주문 실행 코드는 추가하지 않았다. 검증·잠금·IW0·IW1 구간은 열지 않았고, 사전등록도 활성화하지 않았다.

## A. 전체 조사 결과

| 항목 | 값 |
|---|---|
| 최신 커밋 | 감사 시작 `a732ed6`; 이 보고서 작성 시 main = PR #64·#65·#66·#67 병합 후 |
| 조사한 디렉터리 | `run/`(136 + 새 1개 .py), `run/discovery/`(엔진 48 .py, 19,505줄), `run/discovery/tests/`(41개, 테스트 669개), `run/discovery/config/`(19개), `run/audit/`, `src/`(TS 4개 + package 3개 + prisma), `docs/adr/`, `.github/workflows/`(2개), `data/`, `research/` 목록 |
| 전체 소스 파일 수 | 저장소 추적 파일 1,883개. 그중 Python: run/*.py 137, run/discovery 48, 테스트 41 + run/test_*.py 25. TypeScript 4 |
| 본문을 읽은 파일 수 | 아래 "읽은 범위" 참고. 엔진 14개 모듈의 해당 부분, run/*.py 5개를 추적했다 |
| 로직 추적을 끝낸 파일 수 | 엔진 14개(부분), run/*.py 5개(위험 줄) |
| 테스트한 파일·모듈 | 엔진 전체 테스트 스위트(41개 파일) 실행. run/*.py 중 20개는 전용 테스트가 통과(TESTED) |
| 조사하지 못한 파일과 이유 | 아래 참고 |

읽은 범위:
- `pipeline.py`: dev_evaluate, robustness, _pbo/_dsr, resume_pending_robustness, promotion_review, search_pool, RunnerDataProvider
- `cost_calibration.py`, `fills_import.py`, `costs_bridge.py`: 계정·증거 경로
- `runner.py`: CLI, main, PathConfig
- `ledger_check.py`: 전체
- `autoloop.py`: 요약·사이클 래퍼
- `strategy_spec.py`: 피처 등록부, 인과 도우미, canonicalize
- `evolution.py`: 조건·임계값 생성, 지문
- `search.py`: SearchConfig
- `evaluator.py`: 진입 시점(t+1 시가)
- `data_quality.Bar`
- `portfolio.py`: 공개 API·통과 기준
- `stats_mc`, `engine_lib`: 검정력 분석에서 직접 호출

조사하지 못한 파일과 이유:
- run/*.py 112개는 INVENTORIED 수준이다. 머리말, import, 경로, 위험 패턴(음수 shift, center=True, bfill, 미래 인덱스, 네트워크, v1 경로)을 확인했고 본문 전체는 읽지 않았다.
- 그중 84개는 `LEGACY_SCRIPTS.md`의 폐기 v1 스크립트다. 은퇴한 v1 머신 경로와 본 저장소 밖 `cointrader`가 필요해서 여기서 실행할 수 없다. 이들 파일에 대한 판정은 머리말 규칙 수준이다.
- 엔진 모듈 중 `portfolio_sim.py`, `funding_cost.py`(v1 진단, CI 예외), `generation.py`, `handoff.py`, `repro_bundle.py`, `public_market_collect.py`, `newlisting_collect.py`는 이번에 본문을 다시 읽지 않았다. 수집기는 클라우드에서 실행하지 않는다는 규칙이 있다.
- 분류표 `RESEARCH_SCRIPTS_20261110.md`의 조사 수준 열에 파일마다 같은 내용이 적혀 있다. 본문을 다 읽지 않은 파일은 CODE_READ로 적지 않았다.

## B. 발견한 문제

| ID | 심각도 | 파일 | 문제 | 근본 원인 | 해결 결과 |
|---|---|---|---|---|---|
| C-01 | Critical | `cost_calibration.py`, `fills_import.py` | 모의(데모) 계정 체결 내보내기를 가져오면 체결 수만 채워도 비용 모델이 VERIFIED가 될 수 있었다 | 자료 출처가 user_export/synthetic_test 둘뿐이고 계좌 종류 칸이 없었다 | **해결**(ADR 0063, PR #64): `--account-kind` 필수, real만 VERIFIED, 기존 VERIFIED 기록도 real이 아니면 거부 |
| C-02 | Medium | `costs_bridge.py`, `pipeline._cost_block` | 비용 결과에 구성요소별 처리·증거가 없었다 | 상태 라벨 하나로 모델 전체를 표시했다 | **해결**(ADR 0063): 구성요소 표(부분 체결·지연·변동성 의존은 NOT_MODELLED) |
| T-01 | Critical | `pipeline.robustness` | DSR이 판정에 쓰이지 않았다(`gating: False`). DSR 0.59 후보도 생존할 수 있었다 | 정보용으로만 도입된 뒤 게이트로 바뀌지 않았다 | **해결**(ADR 0064, PR #65): promo-v5 DSR ≥ 0.95 게이트 |
| T-02 | High | `pipeline.robustness` | 부트스트랩 CI도 판정에 쓰이지 않았다. CI가 0을 포함해도 통과했다 | 같은 원인 | **해결**(ADR 0064): CI 하한 > 0 게이트, B=2000 |
| T-03 | Medium | `config/promotion_v3/v4`, 문서 | 기본은 v3인데 v4가 따로 있고 문서마다 다른 버전을 가리켰다 | 설정을 버전업할 때 기본값과 문서를 함께 정리하지 않았다 | **해결**(ADR 0064): v5 단일 기본값, 기록에 버전·해시 |
| T-04 | Medium | `pipeline._pbo/_dsr` | 시행 집합에 robustness 이웃이 들어가서 평가 순서에 따라 결과가 달랐다 | "컨텍스트의 모든 DEV 시계열"을 그대로 썼다 | **해결**(ADR 0064): 시행 집합 = search_pool |
| T-05 | Medium | (없음) | 현재 게이트의 재현 가능한 검정력 분석이 없었다 | 이전 측정은 v3 게이트 복사본이었다 | **해결**(ADR 0064): `power_analysis.py`, 고정 시드, 실제 함수 사용 |
| S-01 | Medium | `pipeline.dev_evaluate` | OK 기록을 먼저 쓰고 시계열을 나중에 써서, 중단되면 전략이 시행 집합에서 조용히 빠졌다 | 쓰기 순서 | **해결**(ADR 0064): 시계열 먼저 기록, 테스트 |
| S-02 | Medium | `ledger_check.py` | 위 상태를 찾지 못했다 | 점검 항목이 없었다 | **해결**(ADR 0065): MISSING_DEV_SERIES |
| S-03 | Low | `autoloop.EvolutionCycleSummary` | 요약만 보면 CYCLE_OK가 전략 성공으로 읽힐 수 있었다 | 사이클 상태와 전략 결과가 한 파일에 섞여 있었다 | **해결**(ADR 0065): status_meaning, strategies_passed |
| M-01 | Medium | `runner.py` | Audit/Power 실행 모드가 없었다 | 별도 스크립트로만 실행할 수 있었다 | **해결**(ADR 0065): `--mode audit/power`, 읽기 전용, 잠금·자료 불필요 |
| M-02 | Medium | `runner.py`, `search.SearchConfig.from_json` | 검색 범위를 설정 파일로 바꿀 수 없었다. from_json은 모르는 키를 버린다 | CLI가 `--selection`만 받았다 | **해결**(ADR 0065): `--search-config`, 모르는 키는 오류 |
| L-01 | High | `run/broad_universe_dev.py`, `run/newlisting_short_dev.py` | 공용 펀딩·2345봉 로더에 읽기 금지 구간 검사가 없었다. 신규상장 숏 도구가 검증·잠금 구간(2023-09-19~2024-06-18) 펀딩까지 읽었다 | `assert_allowed`를 `load_daily`에만 넣었다 | **해결**(ADR 0066, PR #67): 로더 보호, 구간별 로드. 결과에 쓰인 값은 DEV 안이라 발표 수치는 그대로 |
| I-01 | Medium | `run/*.py` | 136개 스크립트의 통합·중복·폐기 분류가 없었다 | LEGACY 목록만 있었다 | **해결**(ADR 0066): 분류표 생성기(CI에서 최신 여부 확인) |
| I-02 | Medium | (없음) | 엔진·독립 전략을 같은 필드로 적는 형식과 중복 규칙이 없었다 | 독립 도구가 저마다 다른 결과 형식을 썼다 | **해결**(ADR 0066): `strategy_record.py`, 13개 독립 가설 기록 |
| F-01 | Medium | `strategy_spec.py` | klines만으로 만들 수 있는 v1 가설(A1 비유동성, X12 왜도 등)이 엔진에 없었다 | 등록부 확장이 없었다 | **해결**(ADR 0066): 선택형 피처 세트 v2 5개, 기본 컨텍스트 불변, `--feature-set v2` |
| F-02 | Medium | (없음) | OI·베이시스·청산·호가·DVOL·체결 건수 자료가 없다는 사실이 기록돼 있지 않았다 | — | **해결**(ADR 0066): `feature_data_registry.py`, DATA_MISSING/NOT_DERIVABLE |
| Q-01 | Medium | `.github/workflows/research-ci.yml` | run/ 스크립트 중 현재 도구만 컴파일하고, 폐기 스크립트·audit 도구는 컴파일·정적 검사하지 않았다 | 범위 | **해결**(ADR 0067, PR #68): 모든 run/*.py와 run/audit/*.py 컴파일·ruff E9/F63/F7/F82 |
| Q-02 | Medium | `src/*.ts` | TS 서비스가 CI에 없었다 | 범위 | **해결**(ADR 0067): bun 트랜스파일 검사, package JSON 파싱 |
| P-01 | High | `pipeline.py` (evolve) | evolve 경로에는 포트폴리오 시뮬레이션(사이징·레버리지·청산·동시 보유)과 전략 간 포트폴리오 검사가 없다(legacy 경로는 `portfolio.simulate_portfolio` 사용) | evolve 승격 심사가 단일 전략 이벤트 수익만 본다 | **미해결**(F절): 잠금 창 이후 게이트를 추가하면 사후 판정이 되므로 검증 창 robustness 단계 설계가 먼저 필요하다 |
| E-01 | Critical(외부) | 비용 | 실계좌 체결 0건 | 외부 증거 | **외부 증거 필요**(F절) |
| E-02 | High(외부) | 검정력 | 현재 창 길이로는 연 SR 4에서도 80% 검정력이 안 된다 | 표본 부족 | **외부 증거 필요**: 새 독립 창·전향 자료(F절) |
| G-01 | Medium(환경) | GitHub Actions | 결제 실패로 Actions가 돌지 않는다 | 계정 결제 | **환경 문제**: 로컬 CI(3.11·3.13)로 대신함 |

## C. 실제 수정 사항

| PR / ADR | 수정 파일 | 변경 함수·기능 | 이유 | 기대 효과 | 기존 기능 영향 |
|---|---|---|---|---|---|
| #64 / 0063 | `cost_calibration.py`, `fills_import.py`, `costs_bridge.py`, `pipeline.py`, 테스트 4개 + 새 1개 | `calibrate_and_verify(account_kind=)`, `check_record`, `evidence_tier`, `import_fills(account_kind=)`, CLI `--account-kind`, `cost_component_evidence` | C-01, C-02 | 모의·미신고 체결로는 VERIFIED 불가, 구성요소별 증거 표시 | 기준 숫자 불변. 새 평가 기록의 record_id가 바뀜 |
| #65 / 0064 | `pipeline.py`, `engine_lib.py`, `ops.py`, `config/promotion_v5.json`, `power_analysis.py`, `config/power_analysis_v1.json`, 테스트 | `robustness`(게이트·not_computable), `_trial_series`, `_blocked_now_computable`, `dev_evaluate` 쓰기 순서, `power_analysis.run/simulate` | T-01~T-05, S-01 | 거짓 생존 차단, 계산 불가와 실패 구분, 순서 무관 | 기본 설정 해시 변경 → 새 컨텍스트. 잠금 캠페인 메커니즘 테스트 2개는 v3 명시 |
| #66 / 0065 | `runner.py`, `ledger_check.py`, `autoloop.py`, 새 테스트 | `parse_options`(audit/power/search-config), `load_search_config`, `run_read_only_mode`, `check_evolution`(MISSING_DEV_SERIES), `EvolutionCycleSummary.to_json` | S-02, S-03, M-01, M-02 | 읽기 전용 점검·검정력 실행, 설정 오타 차단 | 기존 모드·해시 불변. 요약 JSON에 필드 2개 추가 |
| #67 / 0066 | `run/broad_universe_dev.py`, `run/newlisting_short_dev.py`, `strategy_spec.py`, `evolution.py`, `pipeline.py`, `runner.py`, 새 `strategy_record.py`·`feature_data_registry.py`·`run/audit/research_inventory_20261110.py`, 테스트 4개 | `load_funding/load_bar2345`(assert_allowed), `fund_sum`(구간별), `FEATURE_SETS/enable_feature_set`, `INTEGER_FEATURES`, `--feature-set`, `from_engine_spec/research_record/dedup`, `availability/family_report` | L-01, I-01, I-02, F-01, F-02 | 금지 구간 읽기 차단, 분류·중복·자료 결손을 한곳에서 관리, 탐색 범위 확장(선택형) | 기본 registry_identity·컨텍스트·난수 순서 불변. 발표 수치 불변 |
| #68 / 0067 | `.github/workflows/research-ci.yml`, 이 보고서 | CI 단계 3개 + TS 작업 | Q-01, Q-02 | 폐기 스크립트 문법 회귀와 TS 문법 오류 감지 | 없음 |

## D. 독립 연구 통합 현황

파일별 분류와 사유는 생성된 표 `run/audit/RESEARCH_SCRIPTS_20261110.md`(137행)에 있다. 생성기는 `research_inventory_20261110.py`이고, CI가 표의 최신 여부를 확인한다. 요약은 다음과 같다.

| 분류 | 파일 수 | 대표 사유 |
|---|---|---|
| 핵심 엔진 통합 완료 (INTEGRATED_CORE) | 13 | 아래 목록 참고 |
| 독립 표준 인터페이스 연결 완료 (STANDARD_INTERFACE) | 11 | strong/liquid/structural 탐색, maker_entry_reeval, iw1_statistic, evalue_v3_block 등이 엔진의 spec·CostContext·evaluator를 그대로 사용 |
| 독립 유지 (KEPT_INDEPENDENT) | 23 | 횡단면·다자산 패널(엔진은 종목별 15분 단일 전략), 합성 시뮬레이션, 비용 추정 도구. 결과는 각 ADR에 있고 13개 가설은 표준 기록으로 등록(`RESEARCH_STRATEGIES_20261110.jsonl`) |
| 중복 연구 (DUPLICATE_RESEARCH) | 1 | x7 상장 이벤트 → newlisting_short_dev가 대체 |
| 폐기 또는 보류 (RETIRED_OR_ON_HOLD) | 43 | v1 cointrader 교차검증·v1 다운로드(실행 불가), alt 펀딩 v1 라운드, X4/F3 2자산 스프레드(엔진에 2자산 유형 없음) |
| 추가 데이터 필요 (NEEDS_MORE_DATA) | 21 | 베이시스·프리미엄(c1,c4,x3), Coinbase(c3), 분기선물(c5,l1), 타 거래소(c7), DVOL(c9), CME(f1,x1), 김프(kp), 매크로 일정(x2), 스테이블 공급(x5), 체결 건수(x9), 호가 큐(n2), 청산(p3) |
| 조사 미완료 (NOT_SURVEYED) | 0 | — |
| (테스트 파일) | 25 | run/test_*.py |

INTEGRATED_CORE 13개의 연결:
- A1 비유동성 → illiq_z(v2)
- X12 왜도 → skew_z(v2)
- F2X6·fng → fng 계열과 fng_max/min
- fr → fund_z와 funding_z
- x8 → flow_z와 taker_share_z
- x10 → tod와 hour_utc/dow_utc
- l3 → vol_ratio와 range_z(부분 일치)

조사 수준:
- TESTED 20개, LOGIC_TRACED 5개, INVENTORIED 112개.
- 폐기 84개는 대부분 INVENTORIED다. 중복·폐기 판정의 근거는 머리말에 적힌 사전등록 규칙과 v1 경로다(지시문 금지 7에 따라 근거를 표에 적음).

## E. 테스트 결과

로컬 CI는 `research-ci.yml`의 run 단계를 Python 3.11과 3.13 가상환경(numpy 2.2.6, pandas 2.2.3)에서 그대로 실행했다. 실행 명령은 `python3 local_ci.py <venv> <repo>`다. GitHub Actions는 결제 실패로 돌지 않았다.

| 검사 항목 | 결과 | 실행 명령 | 비고 |
|---|---|---|---|
| 엔진 컴파일 | 성공 (3.11·3.13) | `python -m compileall -q run/discovery` | PR마다 |
| 독립성(타 저장소·폐기 경로 없음) | 성공 | grep 단계 | |
| 정적 분석 E9/F63/F7/F82, F 전체 | 성공 | `ruff check --isolated ...` | 로컬 ruff 0.16.9, CI는 0.16.8 고정 |
| 셸 검사 | 부분 | `bash -n run_cycle.sh` | **실행 환경 문제**: 로컬에 shellcheck 없음(bash -n만 실행) |
| 설정 스키마 | 성공 | config schemas 단계 | promotion_v5 포함 |
| 의존성(엔진 표준 라이브러리만) | 성공 | dependency scan | |
| 원장 일관성(v1 기록, state/) | 성공 | `python ledger_check.py . / state` | |
| 엔진 단위 테스트(회귀 + 새 테스트) | 성공 (3.11·3.13) | `python -m unittest discover -s discovery/tests` | 약 370초, PR마다 |
| 루트 연구 테스트 run/test_*.py | 성공 | `python -m unittest test_*` | 새 test_dev_window_guard 포함 |
| 모든 run/ 스크립트 컴파일·ruff | 성공 | PR #68 새 단계 | 폐기 스크립트 포함 |
| 분류표 최신 여부 | 성공 | `python run/audit/research_inventory_20261110.py && git diff --exit-code` | |
| TypeScript 트랜스파일 | 성공 (4/4) | `bun build --no-bundle src/*.ts` | 로컬 bun 1.4.4, CI는 1.1.38 고정. 형식 검사(tsc)는 의존 형식이 없어 하지 않음 |
| 검정력 분석 | 성공(재현) | `python3 power_analysis.py --out ...` | 1,930초, 시드 고정; 같은 시드 → 같은 수치(테스트) |
| 실제 자료 회귀(시장 자료) | 실행 안 함 | — | **데이터 부족/규칙**: 클라우드 세션에 시장 자료 없음, 검증·잠금 창 열지 않음 |
| 실계좌 비용 검증 | 실행 불가 | — | **외부 증거 필요**: 실계좌 체결 0건 |

## F. 해결되지 않은 문제

| 남은 문제 | 해결하지 못한 이유 | 필요한 데이터·환경 | 필요한 추가 작업 | 시스템 영향 |
|---|---|---|---|---|
| E-01 실계좌 체결 비용 검증 | 실계좌 체결이 0건이다. OKX 기록은 모의 계정이다 | 종목별 검증 체결 200건 이상(보정 분 포함 약 400건/종목), 미체결을 포함한 전체 주문 로그 | 체결을 모아 `fills_import.py --account-kind real` | PAPER_TEST_READY 불가(비용 미검증 라벨). 모든 결과가 추정 비용 기준 |
| E-02 검정력 부족 | 창 길이와 알파 소진 때문이다. 기준을 낮추는 것은 금지다 | 새 독립 창 또는 12개월 이상 전향 자료 | 사전등록 v2/v3 설계(ADR 0022·0023), 배치 축소, 바스켓 단일 가설 | 진짜 SR 2 전략도 대부분 놓친다(전체 검정력 ≤ 1%) |
| P-01 evolve 포트폴리오 검사 없음 | 잠금 창 이후에 게이트를 두면 사후 판정이 된다. 검증 창 robustness 단계에 넣는 설계와 사전 기준이 필요하다 | 없음(코드 설계) | `simulate_portfolio`를 검증 창 robustness에 연결(promo-v6), 기준 사전 고정 | evolve 생존자의 사이징·레버리지·청산 위험이 확인되지 않는다. 현재 생존자는 0건이라 실제 영향은 아직 없다 |
| DATA_MISSING 계열 | 자료가 없다 | OI, 프리미엄 지수, 청산(VM 수집분), 호가 이력, DVOL, 체결 건수 | 수집(로컬 PC·VM, 클라우드 금지) 후 레지스트리 갱신 | 해당 가설 시험 불가 |
| 2자산 전략 유형(X4/F3) | 엔진 spec이 단일 종목 신호다 | 없음 | spec 스키마 확장(새 레지스트리 버전) | 페어 전략 탐색 불가 |
| v2 피처 탐색 미실행 | 이번 작업은 기능 추가까지다 | 시장 자료(로컬) | `runner.py --mode evolve --dev-only --feature-set v2` | 성과 주장 없음 |
| 폐기 스크립트 84개 본문 미정독 | 실행할 수 없는 v1 기록이고 수정하면 출처 기록이 바뀐다 | — | 필요하면 가설별로 엔진 피처화(이번에 2개 완료) | 판정은 머리말 규칙 수준 |
| 과거 검증 창 알파 | v2 시절 7배치 소진 | — | 새 창 사용 | 전체 이력 FWER 상한 ≈ 0.096(문서 §10.1) |
| GitHub Actions 결제 | 계정 결제 실패 | 결제 수단 갱신(사용자) | 결제 복구 뒤 CI 재실행 | 로컬 CI에 의존 |
| shellcheck 로컬 미설치 | 환경 | — | Actions 복구 시 확인 | 셸 정적 검사 일부 미확인 |

## G. 최종 시스템 평가

| 항목 | 평가 | 근거 |
|---|---|---|
| 전략 탐색 능력 | 부분 완료 | 진화 탐색, MAP-Elites, `--search-config`, 선택형 v2 피처 5개가 있다. 2자산 유형이 없고 DATA_MISSING 계열 6개는 시험할 수 없다 |
| 전략 다양성 | 부분 완료 | 엔진 피처 그룹 8개 + v2 4개 그룹, 니치 상한, 지문 중복 제거, 독립 13개 가설 기록이 있다. 자료 없는 계열은 빠져 있다 |
| 진화 및 조합 능력 | 확인 완료 | 변이·교차·탐색 연산자와 계보 기록을 기존 테스트로 확인했다. 지문·behaviour_key 중복 제거와 표준 기록 dedup도 테스트했다 |
| 데이터 품질 | 부분 완료 | 품질 게이트, 인과성(접두 불변) 테스트, 로더 금지 구간 보호(L-01 수정)가 있다. 실제 시장 자료 재검사는 이번 세션에서 하지 않았다(자료 없음) |
| 백테스트 정확성 | 부분 완료 | 진입은 t+1 시가이고 펀딩·비용은 반영했다(코드 확인, 테스트). 부분 체결·지연은 NOT_MODELLED. 실계좌 대조는 없다 |
| 통계적 신뢰성 | 부분 완료 | StepM + 알파 소진, DSR·CI 게이트, 계산 불가 구분, 1종 오류 ≤ 0.025를 합성 측정으로 확인했다. 검정력은 낮다(E-02) |
| 실제 비용 검증 | 외부 증거 필요 | 검증 체계(계좌 종류, 증거 등급, 봉인)는 완성됐다. 실계좌 체결은 0건이다 |
| 포트폴리오 검증 | 미완료 | legacy 경로에만 있고 evolve 경로에는 없다(P-01) |
| 재현성 | 확인 완료 | 컨텍스트 해시(코드·설정·자료·비용·레지스트리), 설정 버전 기록, 검정력 분석 고정 시드 재현 테스트, 분류표 CI 확인 |
| 오류 복구 | 확인 완료 | 중단 시 CYCLE_RUNNING/FAILED, 재개, 시계열 먼저 기록(S-01), MISSING_DEV_SERIES 점검, audit 모드 |
| 자동 테스트 수준 | 부분 완료 | 로컬 CI 두 버전에서 모두 통과했고 TS 트랜스파일 검사가 생겼다. GitHub Actions는 결제 문제로 멈춰 있고, TS 형식 검사와 shellcheck는 로컬 미확인이다 |
