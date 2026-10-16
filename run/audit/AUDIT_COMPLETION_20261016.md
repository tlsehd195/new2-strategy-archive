# 완료 검증 감사 보고서 — 2026-10-16

결론부터: **엔진 구현과 합성·실데이터 통합 검증은 끝났다. 하지만 연구 목표는 완료되지 않았다.** 비용 모델은 실측 체결 데이터가 없어 검증할 수 없었다(`BLOCKED_EXTERNAL_EVIDENCE`). 그 때문에 어떤 전략도 `PAPER_TEST_READY`에 도달하지 못했다. 전진 페이퍼는 실제 미래 데이터로 한 번도 돌리지 않았다. 잠금 TEST는 검증 생존자가 없어 열지 않았다. 수익성은 `NOT_VALIDATED`다.

## 1. 저장소와 브랜치
- 기준: `origin/main` = `2cb09dc` (PR #2 머지). PR #3은 PR #2 브랜치로 머지된 뒤(`ac3b799`) PR #2와 함께 main에 들어갔다. PR #1·#2·#3 모두 main에 있다.
- PR #4: 열려 있다. 이 작업 브랜치에 merge commit `c9fb482`로 들여와 검토했다. PR #5를 머지하면 PR #4 내용도 main에 들어간다.
- 작업 브랜치 `claude/project-thread-0yqbke`, PR #5(draft).
- 커밋: `c9fb482`(PR #4 통합) → `ad6c54b`(구현) → `a0601df`(인계 v2) → `fa7b325`(ADR·README) → 증거 커밋(이 보고서 포함).
- 기본 브랜치에 직접 쓰지 않았다. 강제 푸시, reset, 기존 원장·잠금 기록 삭제는 없었다. NEW2는 읽기 전용(공개 클론 `8ec6371`)이고 수정하지 않았다.

## 2. PR #4 처리
| 항목 | 판단 | 조치 |
|---|---|---|
| Step-SPA (Hsu–Hsu–Kuan) | Hansen식 재중심화와 일치 | 유지, 진단 전용 |
| MinBTL | 식 4·6 확인 | 유지, 진단 전용 |
| DM 부모·자식 비교 | **결함**: 결측일을 0으로 채웠고 심볼 집합이 달랐음 | 공통 심볼·공통일만 사용, 비교 범위 기록, 겹침 없으면 `NOT_COMPUTED` |
| 비용 손익분기 | 검증으로 오인될 수 있음 | `breakeven_basis`에 비용 상태 표기 |
| 게이트 우회 여부 | 없음 | 진단값을 극단으로 패치해도 StepM 결과 불변임을 테스트로 증명 |

## 3. 변경 파일과 이유
- `cost_calibration.py`, `config/cost_verification_v1.json`(신규): 비용 검증의 유일한 경로. `costs_bridge.py`는 검증 기록 없이는 `verified=True`를 거부한다.
- `data_fetch.py`: 재시도·실패 분류·provenance v2. `data_manifest.py`(신규): 파일별 SHA-256, 교차 월·펀딩 점검.
- `execution_sim.py`(신규): 부분 체결, 거부, 청산, 지연.
- `paper_forward.py`, `config/paper_forward_v1.json`(신규): 전진 관측. `paper.py`: 리플레이로는 통과를 주지 않는다.
- `pipeline.py`: DM 정렬, 비용 범위 검사, `REVERIFY_PENDING`, `PAPER_TESTING → INCONCLUSIVE`.
- `autoloop.py`, `runner.py`: 전진 관측 연결. `handoff.py`: 인계 v2.
- `ops.py`(신규): 상태 점검, 제한 루프. `.github/workflows/research-ci.yml`(신규).
- 설정·기준 변경과 컨텍스트 영향은 [ADR 0006](../../docs/adr/0006-cost-verification-forward-paper-data-manifest-ops-ci.md) 표에 있다. 승격 기준 `promotion_v2.json`은 바꾸지 않았다.

## 4. 테스트와 정적 분석 (증거: `evidence_completion_20261016/`)
| 검사 | 명령 | 결과 |
|---|---|---|
| 전체 단위 테스트 1회차 | `cd run && python3 -m unittest discover -s discovery/tests` | 344개 OK, exit 0, 171초 (`full_suite_run1.log`) |
| 전체 단위 테스트 2회차 | 같음 | 344개 OK, exit 0, 167초 (`full_suite_run2.log`) |
| CI (GitHub Actions, 3.11·3.13) | `research-ci.yml` | 커밋 `ad6c54b`, `a0601df`, `fa7b325`, `93ca2a0` 모두 success |
| compileall | `python3 -m compileall -q run/discovery` | exit 0 |
| ruff 게이트 (E9,F63,F7,F82) | `ruff check --isolated --select …` | 통과 |
| ruff F (보고용) | `ruff check --isolated --select F` | 22건(미사용 import 17, 미사용 변수 5). main은 27건. 남은 22건은 이번에 손대지 않은 기존 파일에 있다. |
| bash -n, ShellCheck 0.11.0 | `run_cycle.sh` | 둘 다 exit 0 |

## 5. 데이터
- 출처: Binance USDT-M 공개 아카이브. GitHub Actions `fetch-market-data`로 받아 업로드됐다(이 컨테이너에서 Binance는 접근 불가).
- 범위: BTCUSDT·ETHUSDT 15분봉과 펀딩, 2021-01..2026-09. 심볼당 69개월, 파일 276개.
- 매니페스트(`manifest_real_data.json`): ok, 차단 0건, 경고 7건(거래량 0인 봉 3–5개짜리 월), dataset_sha256 `5f0101f7c707f2036a19796e77d04b26f9edbd4d788cacf1ad0cf790baad1789`. provenance 해시 276개 모두 일치.
- 손상 사본 시험: 한 파일의 한 줄을 깨뜨렸다. 매니페스트는 `PARSE_ERROR`, `MISSING_BARS`, `PROVENANCE_MISMATCH`로 차단했고, 공급자는 BTCUSDT만 막고 ETHUSDT로 사이클을 진행했다.
- 데이터 없음: exit 4(`CYCLE_BLOCKED_DATA`). 데이터 폴더 없음: exit 1.

## 6. 실데이터 사이클과 재현성
- 명령: `runner.py --mode evolve --rounds 3 --evals 2000`. A1·B1을 서로 다른 빈 상태 폴더에서 동시에 실행했다.
- 결과: 둘 다 `CYCLE_OK`, exit 0, 약 27분. 컨텍스트 `C2-0f3bfb900528ba8c8ee2`.
- 재현성: 평가 기록 783개의 record_id 집합, 상태 집계, 검증 결과가 완전히 같다(`integration_checks.log` 13항).
- 후보 통계:
  - 시드 705개, 변형 제안 72개(무효 18, 중복 3), DEV 평가 777개
  - DEV 판정: REJECTED 706, INCONCLUSIVE 65, 검증 진출 6
  - DEV 탈락 사유(중복 포함): t값 미달 739, 평균 ≤0 676, 폴드 불안정 639, 표본 부족 114, 비용 비중 과다 6
  - 검증 배치(StepM, 사전등록): INCONCLUSIVE 5, REJECTED 1, 생존 0
  - 강건성·잠금 캠페인·페이퍼 진입: 0
- 재개(A2): A1 상태에서 `NEW2_DISC_MODE=evolve bash run_cycle.sh`로 이어서 실행했다. `CYCLE_OK`, exit 0, 5.6분. 시드는 다시 넣지 않았고(0), 새 변형 48개를 평가했다. 새 검증 배치는 INCONCLUSIVE 1개다. 기존 기록 783개는 B1과 순서까지 같은 채로 남았고, 중복 record_id는 0개다(`run_A2_resume_*.txt`).
- 상태 점검(`ops.py health`, A1): OK. 원장 8개 정상, 증거 변조 0건, 잠금 FREE, 잠금 사용 기록 없음.

## 7. 비용 모델
- 현재: NEW2 `CandleCostModel` 기본값(수수료 5bp, 반스프레드 2bp, 충격 0.1·√참여율, 참여 상한 5%). 모두 가정이다. 상태 `UNVERIFIED_COST_MODEL`.
- 보정 실행: `cost_calibration.py --fill-monitor data/fill_ledger.jsonl` → `BLOCKED_EXTERNAL_EVIDENCE`, exit 4(`cost_calibration_fill_monitor.json`).
  - 이 파일은 NEW2 가상 호가 16건뿐이다. 수수료와 충격은 확인할 수 없고, 검증용 체결 0건, 명목금액 1달러뿐이다.
  - 관측된 반스프레드(중앙값 0.013bp)는 가정값 2bp보다 훨씬 작았다. 다만 표본이 8건이고 가상 호가라서 근거로 쓰지 않았다.
- 검증에 필요한 것: 사용자 계정의 체결 보고서(체결가, 수수료, 거부·부분 체결 포함). 심볼당 검증 체결 200건 이상이 필요하고, 실제 수수료 등급 정보도 있어야 한다. 사용자 수수료 등급은 가정하지 않았다.

## 8. 통계 가정과 위험
- StepM과 부트스트랩은 정상성과 약한 의존성을 가정한다. 레짐 전환에는 취약하다.
- 세대 BH는 PRDS 가정을 깔고 있다. BY는 진단으로만 제공한다.
- PBO 기준 0.5는 관례(0.05 수준)보다 느슨하다. 관측 후 기준을 바꾸지 않는다는 원칙에 따라 그대로 두었다.
- 교차 자산 누출: ETH의 개발·검증 기간이 BTC 잠금 창(2024-03-25..06-18)과 겹친다. BTC 페이퍼 기간은 ETH 개발 기간과 겹친다. 교차 자산 특징을 쓰는 전략이 생기면 잠금 결과가 오염될 수 있다. 현재 특징은 단일 자산 기준이다.
- 합성 시나리오 매트릭스(우위 있음, 귀무, 누출, 비용 취약)는 각각 의도한 대로 판정됐다(`test_completion.py`).

## 9. 운영과 복구
- 잠금 보유 시 exit 3, 데이터 없음 4, 실패 1. ENOSPC와 PermissionError는 실패로 처리되고 잠금이 풀린다(테스트).
- JSONL 끝부분이 찢어지면 격리 후 복구하고, 중간이 손상되면 중단한다. 증거 변조는 `EvidenceTampered`로 막는다.
- `ops.py loop`는 횟수 제한이 필수이고, 실패·잠금·데이터 없음·무진전 시 멈춘다.
- 알림은 설정하지 않았다. 알림이 전달된 적은 없고, 로그 파일이 유일한 기록이다.
- A2 재개 후 상태 점검: OK(`health_after_A2.json`). run_cycle 감사 줄은 runner/기록기/tail 종료 코드가 모두 0이다.

## 10. 잠금 TEST, 페이퍼, NEW2
- 잠금 TEST: 사용 0회. 잠금 사용 기록 파일도 생성되지 않았다(A1). 검증 생존자가 없어 억지로 열지 않았다.
- 전진 페이퍼: 구현하고 합성 데이터로 단위 테스트만 했다. 실제 미래 봉으로 관측한 적은 없다. 대상 전략도 없다.
- NEW2: 통합하지 않았고 레지스트리도 바꾸지 않았다. 인계 v2 형식만 테스트로 확인했고, 실제 인계 파일은 생성되지 않았다(대상 없음).

## 11. 상태 라벨
| 항목 | 라벨 |
|---|---|
| PR #4 진단(Step-SPA, DM, MinBTL, 손익분기) | IMPLEMENTED, UNIT_TESTED, REAL_DATA_TESTED(진단만) |
| 데이터 수집 재시도·분류, 매니페스트 | IMPLEMENTED, UNIT_TESTED, REAL_DATA_TESTED |
| 비용 검증 절차·게이트 | IMPLEMENTED, UNIT_TESTED, INTEGRATION_TESTED |
| 비용 모델 자체 | NOT_VALIDATED, BLOCKED_EXTERNAL_EVIDENCE (COST_CALIBRATED 아님) |
| 탐색·검증 파이프라인 | IMPLEMENTED, UNIT_TESTED, INTEGRATION_TESTED, REAL_DATA_TESTED |
| 잠금 TEST 통제 | IMPLEMENTED, UNIT_TESTED (실데이터 사용 0회) |
| 체결 시뮬 | IMPLEMENTED, UNIT_TESTED |
| 전진 페이퍼 | IMPLEMENTED, UNIT_TESTED (PAPER_VALIDATED 아님) |
| 운영 도구·CI | IMPLEMENTED, UNIT_TESTED, INTEGRATION_TESTED, CI 통과 |
| 전략 수익성 | NOT_VALIDATED |

## 12. 남은 차단 요인과 필요한 것
1. **체결 보고서**: 비용 검증에 필요하다. 사용자 계정 체결 내역과 수수료 등급이 있어야 한다. 없으면 승격 경로는 영구히 막힌다(의도된 동작).
2. **실시간 공개 시세 접근**: 전진 페이퍼에 필요하다. 이 컨테이너에서는 Binance가 막혀 있다. 허용된 환경에서 `BinancePublicFeed(allow_network=True)`로 최소 60일 관측해야 한다.
3. **검증 생존자**: 현재 탐색 공간에서는 0개다. 탐색 공간을 넓히는 것은 연구 판단이며, 기준을 낮추는 것으로 해결하지 않는다.
4. PR #5 리뷰와 머지(PR #4 포함).

## 부록 A — 가정 비용(ASSUMED) 실데이터 사이클 (ADR 0007, PR #6)
- 설정: `NEW2_COST_PROFILE=assumed_public_v1`(수수료 5bp, 반스프레드 2bp, 슬리피지 여유 3bp, 한쪽당). 같은 명령(`--rounds 3 --evals 2000`)을 서로 다른 빈 상태 폴더에서 동시에 두 번 실행했다(D1, D2).
- 결과: 둘 다 `CYCLE_OK`, exit 0, 약 27분. 컨텍스트 `C2-d1649d032643094490b6`(비용 프로필과 코드가 바뀌어 5절의 컨텍스트와 다르다). 사이클 요약의 `cost_status`는 `ASSUMED_COST_MODEL`이다.
- 재현성: 평가 기록 779개의 record_id가 순서까지 완전히 같다.
- 후보: DEV 평가 777, REJECTED 709, INCONCLUSIVE 66, 검증 진출 2(둘 다 INCONCLUSIVE). 생존 0, 잠금 TEST 사용 0, 잠금 사용 기록 파일도 없다.
- 해석: 기본 비용에서는 6개가 검증에 올라갔는데(6절), 비용이 더 보수적인 가정 프로필에서는 2개만 올라갔다. 비용을 올리면 DEV 우위가 더 줄어든다는 방향의 관측이다. 어느 쪽도 수익성 증거가 아니다.
- 표시: 검증 단계 기록의 `cost_model.status`는 `ASSUMED_COST_MODEL`, 문구는 "비용 미검증(ASSUMED): research only, never promotable"이다. 이 상태의 전략은 승격 심사에서 `BLOCKED_COST`가 된다(단위 테스트).
- 증거: `run_assumed_D1_summary.json`, `run_assumed_D2_summary.json`, 종료 코드 파일.
- 공개 시세 추정(`ESTIMATED_FROM_PUBLIC_DATA`) 도구는 구현하고 합성 호가로 테스트했다. 이 환경에서는 바이낸스 접속이 막혀 있어 실제 공개 데이터는 모으지 못했다. 로컬 실행 방법은 `docs/공개시세_비용추정_로컬실행_안내.md`에 있다.
