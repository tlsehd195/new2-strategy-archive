# 잔여 감사 보고서 — 2026-10-17

- 브랜치: `claude/project-thread-kzcw3o`, PR #8. 기준 main은 `c39578f`(PR #7 병합)이다.
- 결정 기록: [ADR 0008](../../docs/adr/0008-residual-audit-cost-estimate-lookahead-forward-integrity.md)
- 증거 폴더: `run/audit/evidence_residual_20261017/`
- 표기 구분: **구현**(코드가 있음), **합성 테스트**(합성 데이터 단위 테스트로 확인), **실데이터 검증**(실제 시장 데이터로 실행). 공개 호가 수집은 사용자 PC에서 진행 중이라 이 작업에서는 실행하지 않았다. 수집 데이터로 추정을 돌린 적도 없다.

## 1. 저장소 상태와 보고서 불일치
| 항목 | 확인 결과 |
|---|---|
| 기본 브랜치 | `main`, 최신 `c39578f` (Merge PR #7) |
| PR #4~#7 | 모두 병합(merged_at: #4·#5 2026-10-16 13:43, #6 14:27, #7 14:38). 열린 PR 없음(이 PR 제외) |
| 이전 보고서와 다른 점 | `AUDIT_COMPLETION_20261016.md` §0은 "PR #4 열려 있음", 테스트 344개로 적혀 있다. 지금은 PR #4가 병합됐고, main 테스트는 364개다(PR #6·#7에서 추가). 기록 보존을 위해 그 보고서는 고치지 않았다 |
| ADR 상태 | 0002~0007이 병합 후에도 "Proposed"였다. 색인과 각 파일을 Accepted로 고쳤다 |
| 사용자 파일 | 원장·증거·`data/`·이전 감사 폴더는 건드리지 않았다. 실데이터 실행은 임시 폴더에서만 했다 |

## 2. 수정한 결함 (구현 + 합성 테스트)
| # | 영역 | 결함 | 조치 | 회귀 테스트 (`tests/test_residual_20261017.py`) |
|---|---|---|---|---|
| 1 | 평가기·체결 시뮬·포트폴리오 | 비용·참여 상한·체결 비율을 진입/청산 봉 **자신의** 거래대금으로 계산했다(시점 누출). 포지션 크기 변동성에도 진입 봉 종가가 들어갔다 | 체결 직전에 마감된 봉 기준으로 바꿨다. `exec-sim-v2`, `portfolio-mtm-v3` | `TestNoLookaheadPricing` 4개, `TestPortfolioNoLookahead` |
| 2 | 공개 시세 추정 | 매수·매도 평균, 얇은 호가 스냅샷을 조용히 제외, NaN 통과, 오래된 호가·봉, 중복 시각, 폴더와 다른 종목, 매니페스트 미확인 | 기준 v2와 추정기 v2(ADR 0008 §2) | `TestEstimateRefusals` 7개 |
| 3 | 공개 시세 추정 | 결과를 다시 검증할 수단이 없음 | `verify_estimate`, `--verify` CLI, 수집기 `--manifest-only` | `TestEstimateVerification` 3개 |
| 4 | 비용 검증 | 가상·공개 행에 체결가 허용, 중복·NaN 허용, 체결률·범위에 비체결 행 포함, 사유 있는 VERIFIED 통과 | `cost-calib-v2` 증거 규칙 | `TestCalibrationEvidence` 4개 |
| 5 | 전진 페이퍼 | 현재 설정 파일의 체결 규칙 사용, 계획 변경 미감지, 중단 후 봉 중복, 결론 중복 | 계획 고정 규칙, 무결성 검사, 봉 기록으로 복구, 결론 1회 | `TestForwardIntegrity` 4개 |
| 6 | 체결 시뮬 | 봉 안에서 청산한 거래의 청산 봉 강제 청산을 미확인 | 청산 봉 최악 가격까지 확인 | `test_intrabar_exit_checks_the_exit_bar_for_liquidation` |
| 7 | 운영 | `ops.py loop`가 PARTIAL 사이클이 있어도 exit 0 | exit 2와 `partial_cycles` 기록 | `TestOpsLoop` |
| 8 | 정적 분석 | 사용하지 않는 import 17개, 변수 1개 | 제거하고 CI의 pyflakes F 전체를 게이트로 전환(v1 진단 2개 파일 제외) | CI |

바뀐 기존 테스트는 두 곳이다.
- `test_residual_fixes.test_exit_cost_failure_is_not_priced_cheaply`: 거래대금이 낮은 봉을 "청산 직전 마감 봉"(9)으로 옮겼다. 검사 내용(청산 비용 실패를 싼 값으로 대체하지 않음)은 같다.
- `test_public_estimate`의 합성 스냅샷에 호가 시각(`E`)과 매니페스트를 넣었다. 실제 수집기는 이미 둘 다 기록한다.

## 3. 비용 추정·검증 상태
| 항목 | 상태 |
|---|---|
| 엔진 기본 비용 | `UNVERIFIED_COST_MODEL` (NEW2 기본값, 가정) |
| 가정 프로필 `assumed_public_v1` | `ASSUMED_COST_MODEL`, 승격 불가 |
| 공개 시세 추정 | **구현 + 합성 테스트만**. 기준 v2. 실데이터 추정은 아직 없음(수집 진행 중). 결과가 나와도 `ESTIMATED_FROM_PUBLIC_DATA`이고 승격 불가 |
| 체결 검증(`cost_calibration`) | 저장소의 유일한 실행 관련 파일(`data/fill_ledger.jsonl`, NEW2 가상 호가)로 다시 실행: `BLOCKED_EXTERNAL_EVIDENCE`, exit 4, 실주문 0건, 검증 범위 없음 (`cost_calibration_fill_monitor.json`) |
| VERIFIED 조건 | 계정 체결 보고서(`exchange_fills`), 종목당 검증 체결 200건 이상, 수수료 포함. 사용자에게 계정이 없으므로 **차단 상태 유지** |

수식 점검 결과(구현 확인):
- 한쪽 비용 = 수수료 + 반스프레드 + k·√(주문/직전 마감 15분봉 거래대금). 단위는 명목금액 대비 비율이다.
- 매수 걷기 비용은 매도호가 평균가/중간가 − 1, 매도 걷기 비용은 1 − 매수호가 평균가/중간가다. 둘 다 양수다.
- 충격은 걷기 비용에서 그 스냅샷의 반스프레드를 뺀 초과분이다. 그래서 스프레드가 반스프레드 매개변수와 충격에 이중으로 들어가지 않는다.
- 수수료는 공개 데이터로 관측할 수 없다. 가정값이고, `observed=false`가 강제된다.

## 4. 전략·통계 점검 (코드 재검토, 구현 확인)
- **생성·중복 제거·계보**: 정의 해시가 ID다. 조건 순서가 달라도 같은 ID가 나오고 중복 조건은 합쳐진다(확인). 같은 자식이 여러 경로로 생기면 계보 기록만 늘어난다.
- **체크포인트·재개**: `search_state.json`과 평가 원장에서 이어서 실행한다. 실데이터 재개는 이전 감사(A2)에서 확인했다.
- **데이터 격리**:
  - DEV·검증 읽기 창은 잠금 창 전날에 끝난다. 잠금 봉은 캠페인이 예약한 뒤에만 읽는다. 페이퍼는 잠금 창 종료 2일 뒤부터 시작한다.
  - 탐색 피드백은 DEV 기록만 받는다(`FeedbackLeak`).
  - 남은 위험: ETH의 DEV·검증 기간이 BTC 잠금 창과 겹친다(기존 보고, 단일 자산 특징이라 현재 영향 없음).
- **통계 식**:
  - StepM: (1−α) 분위 임계값으로 단계적 기각한다.
  - SPA: l/c/u 재중심화와 √(2 log log n) 문턱을 쓴다.
  - Reality Check: max √n·d̄를 쓴다.
  - Step-SPA: μ̂ 재중심화를 쓴다.
  - MCS: T_max와 누적 최대 p값을 쓴다.
  - BH/BY: family m을 전체로 센다.
  - PBO(CSCV): ω = 순위/(N+1)이고, 동률은 보수적으로 처리한다.
  - DSR: (γ4−1)/4 항과 시험 간 SR 분산을 쓰고, MinBTL을 계산한다.
  - 원문 식과 다시 대조했고 결함은 찾지 못했다.
  - 검증 배치에서 사건이 없거나 변동이 없는 후보는 StepM 행렬에서 빠진다. 하지만 m(사전등록 인원)과 제외 사유는 기록된다. 제외된 후보는 기각될 수 없으므로 가족 오류율을 부풀리지 않는다.
- **룩어헤드**: 신호는 마감 봉 접두 뷰로만 계산한다. 이번에 비용·참여·체결·크기의 기준 봉 누출을 제거했다(§2-1).
- **결과 재사용**: 컨텍스트 ID에 코드 해시, 비용 identity, 설정 해시, 데이터 해시가 들어간다. 이번 변경으로 새 컨텍스트가 생기며, 이전 PASS나 상태는 재사용하지 않는다(재검증 대기로만 보고).
- **기준 완화 없음**: `promotion_v2.json`, `cost_verification_v1.json`, `paper_forward_v1.json`은 바꾸지 않았다.

## 5. 잠금 TEST·전진 페이퍼
- 잠금 TEST는 `VALIDATION_SURVIVOR`가 있을 때만 열린다. 실데이터에서는 생존자 0명이라 열지 않았다(§7).
- 과거 리플레이(`paper.py`)는 저장된 봉을 다시 재생하므로 `PAPER_PASSED`가 될 수 없다(기존 테스트). 전진 관측(`paper_forward.py`)은 계획 등록 뒤 새로 마감된 봉만 평가한다.
- 수익성 기준은 계획 등록 시점에 해시로 고정되고, 평가는 계획의 기준과 체결 규칙만 쓴다(이번 수정).
- 실제 미래 관측은 아직 없다. 이 컨테이너는 바이낸스 접속이 막혀 있고, 비용이 미검증이라 계획 등록 자체가 거부된다. 따라서 어떤 전략도 `PAPER_PASSED`가 아니며, `PAPER_VALIDATED`라는 상태는 존재하지 않는다.

## 6. 운영·테스트·정적 분석
| 검사 | 명령 | 결과 |
|---|---|---|
| 전체 단위 테스트 | `cd run && COINTRADER_SRC=<NEW2@8ec6371>/src python3 -m unittest discover -s discovery/tests` | 388개 OK, 실패 0, exit 0, 301초 (`full_suite_run1.log`) |
| 전체 단위 테스트 2회차 | 같음(실데이터 사이클 뒤) | 388개 OK, 실패 0, exit 0, 302초 (`full_suite_run2.log`) |
| 컴파일 | `python3 -m compileall -q discovery` | exit 0 |
| Ruff 게이트(E9,F63,F7,F82) | `ruff check --isolated --select E9,F63,F7,F82 discovery` | 통과 |
| Ruff F 전체(v1 진단 2개 제외) | `ruff check --isolated --select F --exclude portfolio_sim.py,funding_cost.py discovery` | 통과(이전에는 22건, 보고만 했음) |
| Ruff F 전체(제외 없음) | 같음, 제외 없이 | 4건, 모두 `portfolio_sim.py`(v1 진단, 러너가 쓰지 않음)의 미사용 변수. 기존 경고 |
| bash -n, ShellCheck 0.11.0 | `run_cycle.sh` | 둘 다 exit 0 |
| 설정 스키마 | CI 단계 "Config schemas" | 통과(추정 기준은 v2를 읽음) |
| CI | research-ci (Python 3.11, 3.13), push·PR | 4개 작업 모두 success (run 38999176075, 38999196389) |

운영 경로 재검토 결과:
- `run_cycle.sh`의 종료 코드 표와 로그 실패 정책은 코드와 일치한다.
- `runner.py`는 잠금 보유 시 3, 데이터 없음 4, 실패 1을 낸다.
- 원장 끝이 찢어지면 격리하고, 중간이 손상되면 중단한다.
- 남은 위험: PID 파일 잠금은 PID가 재사용되면 살아 있는 소유자로 오인할 수 있다. 이때는 실행이 멈추는 쪽으로 실패하고, 겹쳐 실행되지는 않는다.

## 7. 실데이터 사이클 (실데이터 검증, 새 코드)
- 데이터: 업로드 f6379f02. Binance USDT-M BTCUSDT·ETHUSDT 15분봉과 펀딩, 2021-01..2026-09, dataset sha `5f0101f7…`. 임시 폴더에 새로 풀었다.
- 명령: `runner.py --mode evolve --rounds 3 --evals 2000`. 빈 상태 폴더에서 기본 비용 프로필(`UNVERIFIED_COST_MODEL`)로 돌렸다(`run_R1_command.txt`).
- 결과: `CYCLE_OK`, exit 0, 27분. 새 컨텍스트 `C2-eb8b580f8c24306b61e0`이고, 이전 `C2-0f3bfb900528ba8c8ee2`와 다르다. 코드 해시가 바뀌었기 때문이며 의도한 결과다.
- 후보: 시드 705개, 변형 제안 72개(무효 20, 중복 1), DEV 평가 777개.
  - DEV 판정: REJECTED 708, INCONCLUSIVE 69, 검증 진출 6.
  - DEV 탈락 사유(중복 포함): t값 미달 738, 평균 ≤0 677, 폴드 불안정 639, 표본 부족 122, 비용 비중 과다 14.
- 검증 배치(StepM, 사전등록 6명): REJECTED 2, INCONCLUSIVE 4, **생존 0**. 강건성·잠금 캠페인·페이퍼 진입은 0이다. 잠금 TEST 사용 기록은 없다.
- 이전 실행(A1, 시점 누출이 있던 코드)과 비교: REJECTED 707 / INCONCLUSIVE 70 / 생존 0이었다.
  - 검증 진출 6명 중 3명(S-391396a9, S-5f888479, S-6d1f375a)이 같다.
  - 비용 비중 과다 탈락이 6건에서 14건으로 늘었다. 원인은 추정이다: 거래대금이 큰 봉에 진입하는 신호가 직전 봉의 작은 거래대금으로 계산되면서 비용이 커졌을 것으로 본다(사건 단위로 확인하지는 않았다).
  - 결론(생존 0)은 같다.
- 상태 점검(`ops.py health`): OK. 잠금 FREE, 원장 정상, 증거 변조 0건, 설정 OK(`health_after_R1.json`).
- 이 결과는 비용 미검증 상태의 연구 결과다. 수익성 근거가 아니다.

## 8. 미완료 항목과 차단 이유
| 항목 | 상태 | 차단 이유 |
|---|---|---|
| 공개 시세 실데이터 추정 | 미실행 | 사용자 PC에서 수집 중. 끝나면 `--manifest-only`(필요 시) → 추정 → `--verify` 순서로 실행(안내서 5단계) |
| 비용 VERIFIED | 차단 | 계정 체결 보고서 없음(사용자 계정 없음). 공개 호가와 가상 주문은 증거로 인정하지 않음 |
| 잠금 TEST | 미실행 | 검증 생존자 0명 |
| 전진 페이퍼(실제 미래 관측) | 미실행 | 비용 미검증으로 계획 등록 거부. 이 컨테이너는 바이낸스 접속 차단 |
| 미검증 비용에서 잠금 TEST 사용 여부 | 사용자 결정 대기 | ADR 0008 "결정 대기" 참조 |
| NEW2 본 저장소 통합·배포, 실거래 | 하지 않음 | 지시 범위 밖 |
