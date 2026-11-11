# 최종 보고서 — 포트폴리오 엔진 핵심 결함 후속 수정 (2026-11-11, ADR 0071)

전체 근거는 `docs/adr/0071-portfolio-gap-liquidation-funding-completeness-signal-accounting.md`에 있다. 모든 테스트는 합성 데이터로 했다. 실제 시장 데이터로 검증하거나 탐색한 것은 없다.

## A. 작업 범위
- 시작 커밋: `82e93f9` (main, PR #71 병합). 작업 전 브랜치는 `claude/project-thread-zbxv5q`였고 작업 트리는 깨끗했다. #71 뒤의 변경은 없었다.
- 종료 커밋: 이 브랜치의 ADR 0071 커밋 (PR에 기록)
- 브랜치: `claude/project-thread-zbxv5q`
- 변경한 파일:
  - `run/discovery/portfolio_multi.py`
  - `run/discovery/evaluator.py`
  - `run/discovery/pipeline.py`
  - `run/discovery/tests/test_pipeline.py` (SyntheticProvider가 선언 간격을 제공)
  - `run/discovery/tests/test_portfolio_multi_20261110.py`
  - `.github/workflows/research-ci.yml` (출처표 검사 단계)
  - `docs/adr/README.md`
- 추가한 파일:
  - `run/discovery/funding_schedule.py`
  - `run/discovery/config/portfolio_validation_v2.json`
  - `run/discovery/tests/test_portfolio_core_defects_20261111.py` (48건)
  - `run/audit/audit_provenance_20261111.py`, `run/audit/AUDIT_PROVENANCE_20261111.{jsonl,md}`
  - ADR 0071, 이 보고서
- 삭제한 파일: 없음
- 설정 버전:
  - portfolio-validation-v2를 새로 만들고 기본값으로 정했다. v1 파일은 바이트 단위로 그대로다.
  - 시뮬레이터는 portfolio-multi-v2, 펀딩 검사는 funding-schedule-v1이다.
  - promo-v5는 바꾸지 않았다.

## B. 핵심 문제별 수정 결과
| 문제 | 수정 전 | 수정 후 | 테스트 | 최종 상태 |
|---|---|---|---|---|
| 시가 갭 청산 | 시가 종료와 시가로 바뀐 갭 손절이 청산 검사보다 먼저 처리됐다. 5배 롱 100이 시가 60으로 열리면 청산 0건, 정상 종료로 기록 | 펀딩 정산 다음, 시가 종료 전에 갭 청산을 검사한다. 격리는 포지션 단위, 교차는 계좌 단위(시가 기준 자본과 유지증거금 비교). 청산 기록을 상세히 남기고, 증거금을 넘는 손실은 기록만 한다. 포지션은 한 번만 닫힌다 | 격리 8건(롱·숏, 청산가와 같은 시가, 극단 갭, 갭 손절, 정상 종료 유지, 같은 시각 펀딩, 중복 종료), 교차 5건 | FIXED |
| 펀딩 중간 누락 | 처음과 끝 범위만 봤다. evaluator는 공칭 8h로 고정돼 있어 4h나 2h 시리즈의 누락을 못 봤다. 파이프라인은 간격 열을 버렸다 | 정산마다 선언된 간격으로 (진입, 종료] 전체를 검사한다. 상태는 OK, SETTLEMENT_GAP, SCHEDULE_UNKNOWN, DATA_MISSING, DATA_INVALID로 나눈다. 비율 0은 정상 관측값으로 본다. 파이프라인이 간격을 evaluator와 포트폴리오에 넘긴다 | 13건(중간 1건·여러 건 누락, 처음·끝 누락, 경계, 비율 0, 중복·역순, 4h, 8h→4h 변경, 주기 불명, 심볼 불일치, evaluator 롱·숏, 포트폴리오 판정) | FIXED (파이프라인 경로). 파이프라인 밖 호출자는 E절 참고 |
| 신호 진단 유실 | `_study()`가 이벤트만 돌려줬다. 창 끝에서 끊긴 신호는 어느 칸에도 들어가지 않았다 | `diagnostics()`가 항등식을 검사한다. `_study_result`와 `_validation_study`를 추가했고, 진단은 DEV·검증 기록과 포트폴리오 입력에 저장된다. `_study()`의 인터페이스는 그대로다 | evaluator 3건, 파이프라인 재시작 후 복구 1건 | FIXED |
| 거래 거절률 | 거절 사유가 판정에 들어가지 않았다. CostError 거절은 사유표에서 빠졌다 | 신호부터 실제 진입까지 한 가지 사유로만 분류하고 분모를 명시한다. 부분 체결, 취소, 미체결 명목가, 거절 명목가도 기록한다. 판정 규칙: 자료 부족 신호는 0건만 허용하고 넘으면 INSUFFICIENT, 거절률 한도는 null(미정)이라 거절이 있으면 UNDETERMINED, 회계가 없거나 불일치하면 UNDETERMINED | 8건 | FIXED. 숫자 한도는 근거가 없어 정하지 않았다(E절) |
| 봉 내부 낙폭 | 종가 기준만 있었다 | 종가, 봉 내부 최악, 엔벌로프 세 가지로 나누고 상태값을 붙였다. 갱신 안 된 가격으로 평가한 경우는 UNDETERMINED다 | 6건 | FIXED |
| Cross 3x 위험 기준 | 낙폭 한도가 없어 봉 내부 54% 손실도 통과했다 | 계좌 낙폭 한도 0.5를 종가와 봉 내부 모두에 적용한다(기존 위험 기준, 레버리지와 무관). 유지증거금 대비 자본 비율은 진단값으로만 기록한다 | 3건 | FIXED |

## C. 코드 검토 (함수와 위치)
### `portfolio_multi.py`
- `simulate_multi` (L171): 아래 봉 처리 단계를 다시 짰다.
  - ② 시가 갭 청산 (L449): 이번 수정의 핵심이다.
  - ⑤a 봉 내부 최악·최선 평가 (L585)
- 처리 함수:
  - `_book` (L350): 포지션 이중 종료를 막고, 거래별 펀딩 완전성(L375)과 청산 기록을 남긴다.
  - `_skip` (L345): 거절 사유를 진입 구간별로 기록한다.
- 집계 함수:
  - `signal_accounting` (L904)
  - `drawdown_metrics` (L998)
  - `judge` (L1071)와 `JUDGE_KEYS_V2` (L1065)
  - `SKIP_CLASS` (L74)
- `cost_completeness`에 청산 수수료를 NOT_MODELLED로 추가했다 (L756).
- 영향: 결과 키가 늘었다. v1 키(`max_drawdown`, `skipped`, `funding_uncovered_trades`)는 그대로 유지했다. `funding_uncovered_trades`는 이제 중간 누락도 센다.

### `funding_schedule.py`
- `series_issues` (L56), `settlement_check` (L89)

### `evaluator.py`
- `EventStudyResult.diagnostics` (L89)
- `FundingIndex.paid_bp` (L191): 간격을 선언하면 새 규칙, 선언하지 않으면 예전 규칙과 함께 `last_status`를 남긴다.
- `run_event_study(funding_intervals=)` (L224)
- 창 끝 신호 카운트 (L248, L255)

### `pipeline.py`
- `DEFAULT_PORTFOLIO_CONFIG` (L92): v2로 바꿨다.
- `load_portfolio_config` (L165): v1과 v2 스키마를 모두 받고, 거절 한도는 null이거나 [0, 1] 범위여야 한다.
- 공급자 (L293): `funding_intervals`와 `funding_meta`를 넘긴다.
- `_study` (L563), `_funding_schedule_of` (L573), `_study_result` (L585)
- DEV 진단 저장 (L686), `_validation_study` (L903), 검증 진단 저장 (L945)
- `_portfolio_inputs` (L1402): 반환값에 `extra`를 추가했다. 호출부는 `_portfolio_run` 한 곳뿐이다.
- `EVO_CODE_FILES`에 새 파일 두 개를 넣었다. 실험 컨텍스트가 바뀌므로 예전 기록과 섞이지 않는다.

## D. 테스트 결과
아래는 모두 로컬에서 실행한 결과다. 단계 정의는 `.github/workflows/research-ci.yml`의 모든 단계를 그대로 재현한 스크립트에서 가져왔다.

| 단계 | Python 3.11 | Python 3.13 |
|---|---|---|
| `python -m compileall -q run/discovery` | 통과 | 통과 |
| 독립성 grep | 통과 | 통과 |
| ruff E9,F63,F7,F82 / ruff F (0.16.8) | 통과 | 통과 |
| `bash -n` + shellcheck (run_cycle.sh) | 통과 | 통과 |
| 설정 스키마 (v2 기본 포함) | 통과 | 통과 |
| 의존성 검사 (stdlib 전용) | 통과 | 통과 |
| ledger_check (`.`, `state`) | 통과 | 통과 |
| `python -m unittest discover -s discovery/tests` | 790건 통과, 실패 0 | 790건 통과, 실패 0 |
| 루트 연구 테스트 `run/test_*.py` (numpy 2.2.6 / pandas 2.2.3 on 3.11) | 152건 통과 | 152건 통과 |
| funding_cost 레거시 테스트 | 1건 통과 | 1건 통과 |
| run/*.py와 run/audit 컴파일, ruff | 통과 | 통과 |
| 분류표 최신 여부, 소스 감사 표, 출처표(신규) | 통과 | 통과 |
| bun 1.4.4 src/*.ts 트랜스파일, package JSON | 통과 (CI의 bun 버전은 1.1.38. 같은 버전으로 돌리지는 않았다) | 같음 |

- 단위 테스트 790건은 기존 742건에 신규 48건을 더한 것이다. 기존 테스트를 삭제하거나 조건을 약화하지 않았다. 바꾼 세 가지는 ADR 0071의 "테스트" 절에 적었다.
- GitHub Actions는 실행되지 않았다(아래 참고). 로컬 CI 통과는 GitHub Actions 통과가 아니다.
- 실제 시장 데이터 검증은 하지 않았다. 실데이터에서 읽은 것은 펀딩 CSV의 열 이름과 `funding_interval_hours` 값뿐이다.

### GitHub Actions 확인 결과
- 실행 #287~#311(2026-11-09부터)의 모든 job이 3~5초 만에 실패했다. 해당 job들의 상태:
  - `runner_id` 0, 러너가 배정되지 않았다.
  - 과금 시간 0ms다.
  - step이 하나도 없다.
  - 로그를 받으려 하면 HTTP 404가 나고, check-run 출력도 비어 있다.
- #306, #307, #308, #310(PR), #311(main)은 모두 같은 모양이다.
- 워크플로 파일은 10-22 이후 #287 전까지 바뀌지 않았고, 같은 파일로 #249~#286(11-01~11-02)은 성공했다.
- 그래서 워크플로 설정, 의존성 설치, 코드 문제는 아니다. 실행 전 단계에서 막힌 계정 수준 제한(결제나 사용 한도일 가능성이 높음)으로 분류한다.
- 정확한 사유 문구는 API로 얻지 못해 확인할 수 없었다. 결제나 한도는 사용자가 GitHub Billing 화면에서 확인해야 한다.

## E. 남은 위험
1. 파이프라인 밖 호출자는 아직 공칭 8h 펀딩 규칙을 쓴다(`NOMINAL_8H_LEGACY`로 기록됨): `independent_window.py`, `runner.py`의 v1 경로, `paper_forward.py`, `cost_sensitivity.py`, run/*.py 연구 스크립트. IW1 코드는 동결 상태라 수정하지 않았다. 2020 자료는 모두 8h라 영향이 없을 것으로 보지만 강제하는 장치는 없다.
2. 청산 손실식은 모델 가정이다. 갭 손실은 증거금까지만 장부에 넣고 초과분은 기록만 하며, 청산 수수료와 보험기금, ADL은 반영하지 않는다.
3. 거절률 숫자 한도가 없다. 그래서 거절이 하나라도 있는 장부는 UNDETERMINED가 된다. 한도를 정하려면 실제 체결 자료 같은 근거가 필요하다.
4. 감사 기록:
   - pipeline.py와 data_quality.py는 일부만 다시 읽었고, 범위를 출처표에 남겼다.
   - 124개 스크립트 표는 여전히 검토 에이전트의 주장이다. 작성자가 확인한 것은 5행뿐이다.
5. 코드 수정과 별개로 외부 증거가 필요한 일:
   - 실계좌 체결 0건이라 비용 모델을 VERIFIED로 만들 수 없다.
   - 새 독립 검증 구간이 없다.
   - 개별 생존 전략이 0개라 포트폴리오 계층은 아직 실제 데이터로 한 번도 돌지 않았다.
6. GitHub Actions가 계정 수준에서 막혀 있다. 사용자 조치가 필요하다.

## F. 최종 평가
| 항목 | 등급 | 근거 |
|---|---|---|
| 청산 계산 정확성 | COMPLETE (모델 가정 범위 안에서) | 갭과 봉 내부, 격리와 교차, 중복 종료 테스트. 거래소 규칙은 모델링하지 않음 |
| 펀딩 정산 완전성 | PARTIAL | 파이프라인과 포트폴리오는 완료. 파이프라인 밖 호출자는 레거시 규칙 |
| 거래비용 계산 | PARTIAL | 구조는 맞지만 비용 모델이 미검증이고 실체결이 0건 |
| 신호 거절 추적 | COMPLETE | 항등식 검사와 판정 연결 |
| 포트폴리오 자본 관리 | COMPLETE (합성) | 거절 사유와 명목가 기록 |
| 봉 내부 위험 측정 | COMPLETE (합성) | 세 가지 낙폭과 상태값 |
| 개별 전략 검증 | PARTIAL | 기준은 바뀌지 않았다. 생존 전략 0개, 검정력 낮음(ADR 0064) |
| 포트폴리오 검증 | PARTIAL | 엔진과 기준은 정비했지만 실데이터로 실행한 적이 없다 |
| 통계적 검증 | PARTIAL | promo-v5는 바뀌지 않았고 검정력 한계는 그대로다 |
| 데이터 무결성 | PARTIAL | 펀딩 주기 검사 추가. 레거시 호출자는 남아 있다 |
| 재현성 | COMPLETE | 결정적 시뮬레이션, 설정 해시와 모델 버전 기록 |
| 상태 복구 | COMPLETE (합성) | 재시작 뒤 진단 복구, 포트폴리오 기록 1회 |
| 자동화 | BLOCKED | GitHub Actions가 계정 수준에서 막힘. 로컬 CI만 통과 |
| 전체 코드 감사 | PARTIAL | 핵심 모듈을 직접 다시 읽었지만 pipeline은 일부. 124행은 검토자 주장 |

전체 작업은 COMPLETE가 아니다. 코드 수정(P0, P1)은 완료했지만, 위 PARTIAL과 BLOCKED 항목이 남아 있다.

## G. 최종 질문
1. **세 가지 핵심 결함이 모두 수정되었는가?** 그렇다. 시가 갭 청산 우회, 펀딩 중간 누락 미탐지, 신호와 거절 진단 유실을 각각 재현 테스트로 확인하고 수정했다. 펀딩은 파이프라인 경로 기준이고, 파이프라인 밖 호출자는 E-1에 적었다.
2. **정상 상황과 극단 상황 모두에서 올바르게 작동하는가?** 합성 데이터에서는 그렇다. 정상 종료, 청산가와 같은 시가, 극단 갭, 여러 심볼 동시 손실, 교차 상쇄, 정산 주기 변경, 비율 0, 중복과 역순을 확인했다. 실제 시장 데이터로는 확인하지 않았다.
3. **거절과 데이터 부족을 숨기지 않고 보고하는가?** 그렇다. 모든 신호가 한 가지 사유로만 분류되고 항등식이 검사된다. 자료 부족은 INSUFFICIENT로, 한도가 정해지지 않은 거절과 회계 불일치는 UNDETERMINED로 판정되며 PASS가 나오지 않는다.
4. **NEW2는 실제 시장 데이터로 어디까지 검증되었는가?** DEV 탐색과 검증 창 StepM까지다. 모든 컨텍스트에서 생존 전략은 0개다. 포트폴리오 계층, 잠금 TEST, 페이퍼, 실계좌는 실데이터로 실행된 적이 없다. 비용 모델은 미검증이고 실계좌 체결은 0건이다.
5. **가장 중요한 남은 문제는?** 실제 증거가 없다는 점이다. 비용 검증용 실계좌 체결, 새 독립 검증 구간, 검정력을 높일 더 긴 자료가 필요하다. 코드 쪽에서는 파이프라인 밖 호출자의 펀딩 주기 규칙 통일, 그리고 GitHub Actions 복구(사용자 조치)가 남았다.
