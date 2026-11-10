# 잔여 문제 전면 해결 — 최종 보고서 (2026-11-10, 지시문 형식 A~H)

연구·백테스트 전용이다.
- 실주문, 실계좌 API, 키 요청은 없었다.
- 검증·잠금·IW0·IW1 창은 열지 않았다. prereg도 활성화하지 않았다.
- 수집기는 클라우드에서 실행하지 않았다.

## A. 작업 범위
- **기준 커밋:** `510304e`(PR #68 병합, 2026-11-10 감사 직후).
- **변경 커밋:**
  - `517454c` → 병합 `91f2024`(PR #69, ADR 0068)
  - `6e0e7ac` → 병합 `56164d4`(PR #70, ADR 0069·0070)
  - 이 보고서 PR
- **변경 규모(510304e..56164d4):** 27개 파일, +3054 / −245. 추가 12개, 수정 15개, **삭제 0개**.
- **수정한 파일:**
  - `run/discovery/`: `pipeline.py`, `autoloop.py`, `power_analysis.py`, `fills_import.py`, `store.py`, `repro_bundle.py`, `newlisting_collect.py`, `funding_cost.py`, `portfolio_sim.py`(머리말만), `tests/test_pipeline.py`
  - `run/audit/research_inventory_20261110.py`(및 생성물 2개)
  - `.github/workflows/research-ci.yml`, `docs/adr/README.md`
- **추가한 파일:**
  - 코드: `portfolio_multi.py`, `config/portfolio_validation_v1.json`
  - 테스트: `tests/test_portfolio_multi_20261110.py`, `tests/test_statistics_evidence_20261110.py`, `tests/test_state_recovery_20261110.py`, `tests/test_funding_cost_legacy_20261110.py`
  - 감사: `run/audit/source_audit_20261110.py`, `SOURCE_AUDIT_20261110.jsonl`·`.md`
  - ADR 0068·0069·0070, 이 보고서
- **직접 읽고 확인한 핵심 모듈:** `pipeline.py`(robustness, `_dsr`, `_pbo`, `_trial_series`, dev_evaluate, 잠금·승격), `engine_lib.deflated_sharpe`, `stats_mc`, `evaluator`(FundingIndex, 비용), `cost_model`, `strategy_spec.stop_exit_rule`, `store`, `repro_bundle`, `fills_import`, `power_analysis`
- **감사 수준(전체 137개 run/*.py, 분류표 기준):** FULL_SOURCE_REVIEW 33, LOGIC_TRACED 56, TESTED 48, INVENTORIED 0. 핵심 모듈 12개는 E절에 따로 적었다.

## B. 기존 문제별 처리 결과
| ID | 상태 | 수정 파일·함수 | 이유 | 테스트 근거 |
|---|---|---|---|---|
| P-01 evolve 포트폴리오 검사 없음 | **FIXED** | `portfolio_multi.simulate_multi`, `pipeline.portfolio_validation` / `_portfolio_gate` / `run_locked_campaign` / `promotion_review` | 단일 전략 수익만 보고 승격했다 | `test_portfolio_multi_20261110` 47개 |
| T-01·T-02 DSR·CI 게이트 | **NOT_REPRODUCED**(이미 수정됨, 재확인) | `pipeline.robustness` | 현재 코드에서 게이트 동작 확인 | `test_promo_v5_20261110` |
| DSR 시행 수 과소 집계(신규) | **PARTIALLY_FIXED** | `pipeline._dsr` | 진단 필드만 추가했다. 판정에 쓰려면 새 promo 버전이 필요하다 | `TestDsrTrialCount` |
| 검정력 불확실성 미표시(신규) | **FIXED** | `power_analysis.wilson`, `simulate` | ±0.15 오차를 숨겼다 | `TestWilson` |
| C-01 모의 체결이 실계좌로 들어갈 위험 | **FIXED**(추가 방어) | `fills_import.import_fills`, `real_fill_evidence_status` | 신고값만 믿었다 | `TestRealFillGuards` |
| 재검증 큐 충돌 창(신규) | **FIXED** | `store.ReverifyQueue.pop_ready`, `reconcile` | 중단되면 후보가 영영 평가되지 않았다 | `TestQueueCrashWindow` |
| 재현 검증이 개수만 비교(신규) | **FIXED** | `repro_bundle.verify_rerun` | 다른 기록도 "재현됨"이었다 | `TestReproVerify` |
| 신규상장 상태 손상 시 기준선 재설정(신규) | **FIXED** | `newlisting_collect.load_state` | 기존 심볼이 신규 상장으로 보였다 | `TestNewlistingState`. 사용자 이식본도 교체했고 `.bak_20261110`으로 백업했다 |
| 펀딩 정산 누락 미탐지(신규) | **FIXED**(레거시 모듈) | `funding_cost.funding_cost_detail` | 8~12h 보유의 누락을 놓쳤다. 현재 판정 경로는 쓰지 않는다 | `test_funding_cost_legacy_20261110`. 수정 전 실패, 수정 후 통과 |
| `portfolio_sim.py` 결함(신규) | **NOT_FIXED**(DEPRECATED 표시만) | 머리말 | 쓰이지 않고 import도 실패한다. 대체는 portfolio_multi | — |
| 폐기 v1 진단의 가짜 OOS·양측 Holm·겹침·펀딩 누락 | **NOT_FIXED**(의도적) | — | 판정에 쓰이지 않는다. 출처 기록을 보존한다 | 감사 표 |
| 폐기 스크립트 본문 미정독 | **FIXED** | 감사 표 | 76개 전부 전체 읽음. 이전 보고서의 84개는 현재 목록(76)과 맞지 않는다 | `source_audit check`(CI) |
| shellcheck 로컬 미설치 | **FIXED** | — | shellcheck 0.11.0을 설치하고 `.sh` 4개를 검사했다 | F절 |
| bun 버전 차이 | **FIXED**(확인) | — | 1.1.38과 1.4.4 모두 트랜스파일 OK. 두 버전 모두 문법 오류에서 rc = 1 | F절 |
| E-01 실계좌 체결 0건 | **BLOCKED** | — | 외부 증거 | — |
| E-02 검정력 부족 | **BLOCKED** | — | 표본(창) 부족 | — |
| G-01 GitHub Actions | **BLOCKED** | — | 이번 실행 #306~#308도 작업이 2~3초 만에 단계 없이 실패했다. 결제 차단으로 보인다 | — |

## C. 포트폴리오 시뮬레이터(ADR 0068)
- **기존 구조:**
  - evolve: 없음. 단일 전략 이벤트 수익만 봤다.
  - legacy: `portfolio.simulate_portfolio`.
  - `portfolio_sim.py`: 미사용이고 결함이 있다.
- **신규 구조:**
  - `portfolio_multi.simulate_multi`. 봉마다 펀딩 → 시가 청산 → 진입(고정 우선순위) → 봉 내부 청산·손절 비교 → 평가 순서로 처리한다.
  - 기준 `portfolio-validation-v1`은 promo-v5와 별도다.
- **자본 관리:**
  - 초기 자본에 비중 × position_fraction을 곱해 크기를 정한다.
  - 증거금은 진입 시 예약한다.
  - 총 노출 상한은 `gross > max_gross_exposure × equity`로 판정한다.
- **포지션 관리:**
  - 동시 보유 상한(max_concurrent)을 둔다.
  - 같은 심볼은 hedge 또는 one_per_symbol 규칙을 따른다.
  - 신호 중복은 uid로 막는다.
  - 멤버 spec에 없는 심볼은 거부한다.
- **레버리지·증거금:**
  - 레버리지와 유지증거금률(mmr)을 쓴다.
  - isolated는 포지션별로 청산한다. cross는 모든 불리한 극값으로 계좌 전체를 한 번에 청산한다.
- **청산 처리:**
  - 봉 내부에서 청산가와 손절가를 비교해 손실이 큰 쪽을 택한다.
  - 비교는 비용을 매기기 전 가격 손실로 한다.
  - 파산을 기록한다.
- **거래비용:**
  - 실제 명목가로 `CostContext.quote`를 쓴다(수수료 + 반 스프레드 + 충격).
  - 펀딩은 (entry, exit]에 매긴다.
  - CostRefused, DataMissing, 계산 오류를 구분한다.
  - 부분 체결 시 잔량은 다음 시가에 별도 leg로 체결한다.
  - 지연 체결이면 청산을 전략 규칙으로 다시 계산한다.
- **전략 동시 운용:** 단독 실행과, 현재 장부와 결합한 실행(동일 가중, 최대 20개)을 모두 돌린다.
- **검증 기준:**
  - base(isolated 1x): 최종 자본 > 초기, MDD < 0.5, 청산 0, 파산 없음, 거래 30건 이상, 30일 이상.
  - stress_cross_3x: 청산 0, 파산 없음.
  - stress_partial_latency: 판정에 쓰지 않는다.
  - 상태: PASS / FAIL / INSUFFICIENT_PORTFOLIO_DATA / UNDETERMINED_PORTFOLIO / PORTFOLIO_SIMULATION_ERROR. 개별(PASS_INDIVIDUAL)과 분리해 저장한다.
  - PASS_PORTFOLIO만 잠금 캠페인과 승격으로 간다.
- **테스트:** 47개 통과(3.11·3.13). 독립 검토가 찾은 결함 7건을 수정하고 회귀 테스트를 붙였다.
- **한계:**
  - 검증 창 자료만 쓰고 거부 전용이다.
  - 현재 생존자가 0건이라 실제 시장 자료로 돌린 적이 없다.
  - 체결 지연과 부분 체결은 단순 모델이다.
  - 펀딩 외 롱·숏 비대칭은 모델링하지 않았다.

## D. 통계 및 거래비용 검증(ADR 0069)
- **DSR:** Bailey·López de Prado 공식과 일치한다(일 단위 SR, Pearson 첨도, 시행 간 SR 분산). ≥ 0.95 게이트다. 시행 수 과소 집계는 진단만 추가했다.
- **Bootstrap:** 정상 블록 부트스트랩, 평균 블록 n^(1/3), B = 2000, 퍼센타일 CI. 하한 > 0 게이트다.
- **PBO:** CSCV를 쓴다. 시행 집합은 탐색 풀이며 실행 순서와 무관하다.
- **다중검정:**
  - 확증은 검증 창 StepM 하나이고, 창별 알파를 소비한다.
  - Holm은 legacy 경로에서만 쓰며 평균 > 0을 함께 요구한다.
  - 이력 전체 FWER 상한은 ≈ 0.096이다.
- **Power analysis:**
  - 고정 시드로 재현된다.
  - Wilson 구간을 추가했다.
  - 시뮬레이션하지 않은 게이트 9종과 실제보다 적은 B를 기록한다. 보고된 검정력은 **상한**이다.
- **비용 4등급:**
  1. 실측(실계좌): 없음.
  2. 공개 자료로 측정·보정: 펀딩, 테이커 수수료, 스프레드·충격 estimate_v1.
  3. 모델 추정: √참여율, 상한 거절, 손절 봉 극값 체결.
  4. NOT_MODELLED: 부분 체결, 지연, 변동성 의존, 메이커, 거래소별 차이.
- **미해결 통계 한계:** SR 4에서도 검정력 80%에 못 미친다. 같은 시장 구간을 반복 선택한 편향은 DSR·PBO로만 다룬다.

## E. 전체 코드 감사(ADR 0070)
- **이번 감사 표:** 124개 파일(INVENTORIED였던 run/*.py 112개 + 핵심 모듈 12개). 모두 lines_read = lines_total이다.

| 수준 | 이번 표(124개) | 분류표 run/*.py(137개) |
|---|---|---|
| FULL_SOURCE_REVIEW | 34 | 33 |
| LOGIC_TRACED | 52 | 56 |
| TESTED | 38 | 48 |
| INVENTORIED | 0 | 0 |
| NOT_REVIEWED | 0 | 0 |
| BLOCKED | 0 | 0 |

- 두 표의 수가 다른 이유: 분류표의 TESTED는 `run/test_*.py` 통과를 뜻하고, 이전부터 LOGIC_TRACED였던 5개가 포함된다.
- 핵심 모듈 12개: TESTED 10, LOGIC_TRACED 1, FULL_SOURCE_REVIEW 1.
- **누가 읽었나:** 읽기 전용 독립 검토 에이전트가 읽었다. 각 행은 그 에이전트의 주장이다. 작성자가 직접 다시 확인한 것은 5행이다(`spot_checked`).
- **"실행해서 확인"한 것이 아니다.** v1 스크립트 대부분은 퇴역 경로와 자료가 필요해 여기서 실행할 수 없다.
- 확정 위험(검토자 주장): 누수 5, 비용 6, 백테스트 오류 18. 모두 폐기 v1 진단이거나, 수정한 모듈 또는 미사용 모듈이다. 전체 목록은 `SOURCE_AUDIT_20261110.md`에 있다.

## F. 테스트 결과(실제 실행)
| 항목 | 실행 | 결과 |
|---|---|---|
| Python 3.11 로컬 CI | 실행 | 17단계 전부 통과. 단위 742개 OK, 루트 152개 OK |
| Python 3.13 로컬 CI | 실행 | 17단계 전부 통과. 단위 742개 OK, 루트 152개 OK |
| 포트폴리오 테스트 | 실행 | 47개 OK |
| 데이터 누수 테스트 | 실행(기존 스위트에 포함) | 인과성·누수·금지 구간 관련 테스트(키워드 기준 11개 파일)를 포함한 전체 스위트 OK. 실제 시장 자료로 재검사하지는 않았다 |
| 재현성 테스트 | 실행 | 고정 시드 검정력, 재현 번들 sha256 비교 포함 OK |
| 상태·복구 테스트 | 실행 | 4개 + 레거시 펀딩 1개 OK |
| TypeScript | 실행 | bun 1.1.38(CI 고정)과 1.4.4 모두 `src/*.ts` 4개 트랜스파일 OK, 문법 오류 시 rc = 1. 1.1.38은 `--no-bundle --outdir`에서 파일을 쓰지 않지만 검사 목적에는 영향이 없다. 형식 검사(tsc)는 하지 않았다 |
| ShellCheck | 실행(0.11.0) | `.sh` 4개 중 3개는 경고 없음. `run/backup_snapshot.sh`는 SC2012 note 3건(ls 대신 find 권장, 심각도 note). CI는 run_cycle.sh만 검사한다 |
| GitHub Actions | 확인만 | 실행 #306·#307·#308 모두 작업이 2~3초 만에 단계 없이 실패. 결제 차단 추정(이전과 같은 양상) |

- 로컬 CI는 workflow의 run 단계를 그대로 실행한다.
- 한계: 로컬 venv에는 pandas가 미리 설치돼 있다. 그래서 "stdlib 전용 단위 테스트" 조건은 의존성 검사 단계로만 보장된다.

## G. 외부 의존성 및 미해결 문제(코드로 해결 불가)
- **실계좌 체결 자료:** 0건. OKX는 모의 계정이다. 심볼당 200건 이상(보정 포함 약 400건)이 필요하다. 그때까지 비용은 ESTIMATED이고 PAPER_TEST_READY는 불가다.
- **독립적인 미래 검증 기간:** 새 독립 창 또는 12개월 이상의 전향 자료가 필요하다. 열려면 동동의 최종 확인이 필요하다.
- **부족한 시장 데이터(§7 분류, `feature_data_registry.py`):**

| 자료 | 저장소에 존재 | 수집 가능 | 과거 이력 | 파생 |
|---|---|---|---|---|
| Open Interest | 아니오(프로브뿐) | 공개 API로 가능(로컬·VM) | 공개 API는 최근 구간만. 장기 이력은 유료 또는 별도 아카이브 필요 | 불가 |
| 선물 베이시스(프리미엄 지수) | 아니오(일별 현물 지수만) | 가능 | 공개 클라인으로 받을 수 있으나 아직 안 받음 | 펀딩으로 근사하면 안 됨(채우지 않음) |
| 청산 | 1.7일(`liquidations.db`) | VM에서 수집 중 | 부족. 과거분은 공개 API 없음 | 불가 |
| 오더북 | 96h + 80h(비용 추정용) | 가능 | 백테스트 창으로는 부족 | 불가 |
| DVOL | 아니오(v1 머신에만 있었음) | 공개 API로 가능 | 미수집 | 불가 |
| 체결 횟수 | 아니오 | 클라인 8열에 있으나 `data_quality.Bar`가 버림 | 재다운로드 필요 | **NOT_DERIVABLE**(현재 자료) |
| 신규 상장 | 수집기 이식본(사용자 PC) | 가능 | 수집 시작 이후만 | — |

- 위 표의 "수집 가능 / 과거 이력" 칸은 이번에 네트워크로 확인한 것이 아니라 레지스트리 메모와 이전 기록에 따른 것이다.
- v2 피처 세트(range·illiq·ma_dist·skew·dow)는 klines만 쓰고 실제 로더와 연결돼 있다. 다만 **실제 시장 자료로 탐색을 돌린 적은 없다.** 성과 주장은 없다.
- **GitHub Actions:** 계정 결제를 복구해야 한다(사용자).
- **기타:** 2자산(페어) 전략 유형은 spec 확장이 필요하다. 수집기는 로컬 PC 또는 Oracle VM에서만 실행한다.

## H. 최종 평가
| 항목 | 평가 | 근거 |
|---|---|---|
| 전략 발굴 | PARTIAL | 진화 탐색·MAP-Elites·v2 피처가 있다. DATA_MISSING 계열 6개와 페어 유형은 없다 |
| 전략 진화 | COMPLETE | 변이·교차·계보·중복 제거 테스트 |
| 개별 전략 백테스트 | PARTIAL | t+1 시가, 비용·펀딩, 보수적 손절은 반영했다. 부분 체결·지연은 NOT_MODELLED이고 실계좌 대조가 없다 |
| 포트폴리오 백테스트 | PARTIAL | 계층과 테스트는 완성됐다. 실제 시장 자료 실행 0회(생존자 0) |
| 데이터 품질 | PARTIAL | 품질 게이트와 금지 구간 로더가 있다. 이번에 시장 자료 재검사는 하지 않았다 |
| 데이터 누수 방지 | PARTIAL | 현재 엔진은 테스트됐다. 폐기 v1 진단에는 가짜 OOS가 확정됐다(판정 미사용) |
| 거래비용 정확성 | BLOCKED | 실계좌 체결 0건. 공개 자료 추정까지만 했다 |
| 통계적 검증 | COMPLETE | StepM·DSR·CI·PBO 게이트를 코드와 테스트로 확인했다 |
| 검정력 | BLOCKED | SR 4에서도 80% 미만. 새 창이 필요하다 |
| 다중검정 관리 | PARTIAL | 알파 소비는 정상이다. 이력 FWER ≈ 0.096이다 |
| 재현성 | COMPLETE | 컨텍스트 해시, 고정 시드, 재현 검증 sha256 비교 |
| 오류 복구 | COMPLETE | 큐 충돌 창 수정, 고아 재투입, 상태 손상은 오류 처리 |
| 자동화 | PARTIAL | 로컬 CI 두 버전 통과. GitHub Actions가 멈춰 있다 |
| 전체 코드 감사 | PARTIAL | 137개 모두 전체를 읽었다(INVENTORIED 0). 확인은 검토자 주장 위주이고 작성자 직접 확인은 5행이다. v1 대부분은 실행 불가 |

### 네 가지 질문
1. **어디까지 완성되었나?**
   - 탐색 → DEV → 검증 StepM → robustness(DSR·CI·PBO) → **포트폴리오 검증** → 잠금 → 승격의 전체 파이프라인이 코드로 완성됐다.
   - 각 단계는 로컬 CI 두 버전에서 테스트로 확인했다.
   - 상태 복구와 재현 검증의 빈틈도 메웠다.
2. **실제 시장 자료로 검증된 부분은?**
   - 과거 DEV·검증 창 평가(R3 등)에서 전략이 0개 생존했다. 실패 원인이 계산 오류가 아님은 확인했다(ADR 0057).
   - 공개 호가 80h + 알트 96h로 비용을 추정했다(ESTIMATED).
   - 이번 작업은 새 시장 자료 실행 없이 코드·합성 테스트로만 확인했다.
3. **검증되지 않았거나 외부 증거가 필요한 부분은?**
   - 실계좌 체결 비용(0건)
   - 독립 미래 창 또는 전향 자료(검정력)
   - OI·베이시스·청산·DVOL·체결 수 이력
   - 포트폴리오 계층과 v2 피처의 실제 자료 실행
   - GitHub Actions
4. **가장 먼저 해결할 문제는?**
   - **새 독립 검증 기간의 확보**(검정력)와 **실계좌 체결 증거**가 핵심이다.
   - 그중 먼저 할 수 있는 것은 v2 피처로 DEV 탐색을 돌려 후보를 찾는 일이다. 다만 생존 후보가 나와도 비용이 VERIFIED가 될 때까지 페이퍼로 넘어갈 수 없다.
