# ADR 0116 — 누락 데이터 0 대체 제거, 수집기·체결 감시기 복구, 해시 기반 감사 레지스트리

- 상태: Accepted (코드·테스트·CI; 사전등록 활성화·검증 구간 개방 없음)
- 날짜: 2026-11-27
- 요청: 동동, 2026-11-27 "NEW2 Strategy Archive — 전체 문제 해결 및 코드 무결성 강화 지시문"(§1~§17)
- 기준 커밋: `main` `d564456a7df8adf02c64796cec1a3784ea3d0cf3`
- 표준 문서: [`docs/data_integrity_standard.md`](../data_integrity_standard.md) (데이터 품질 표준, 전진 판정 규칙, 실행 방법, 모듈 의존 관계)
- 최종 보고: [`run/audit/FINAL_REPORT_20261127_ADR0116.md`](../../run/audit/FINAL_REPORT_20261127_ADR0116.md)

## 맥락
지시문은 전진 검증의 누락 데이터 0 대체(P1), 포트폴리오 입력 검증, 공통 데이터 품질 규칙, 수집기 경로·복구, 메이커 체결 감시기 원장 복구, 페이퍼 펀딩 오류 분류, 레거시 진입점, git 트리 기반 감사 기록, CI 강화를 요구했다. 실거래·키·주문 기능은 추가하지 않는다. 통계 기준과 승격 조건은 바꾸지 않는다.

## 발견 (기준 커밋에서 재현)
| ID | 위치 | 문제 | 영향 |
|---|---|---|---|
| F-01 | `newsrc_engine.run_segment` | 보유 종목의 수익률이 없으면 0%, 펀딩이 없으면 0으로 계산 | 전진 기록(CL1·FIP1·AF1) 포함 모든 일봉 엔진 결과가 "누락=평범한 날"로 섞임 |
| F-02 | `forward_nearmiss.verdicts` | 상태가 PASS/FAIL 계열뿐, 누락·대체 비율을 판정에 반영하지 않음 | 자료가 빠져도 FORWARD_PASS 가능 |
| F-03 | `impact_bar` 계열 | 거래량이 없으면 1.0을 대체값으로 써 충격 비용을 계산 | 비용이 근거 없이 정해짐 |
| F-04 | 일봉 엔진의 펀딩 귀속 `(t-1)//DAY` | 00:00:00.002 같은 아카이브 지터 정산을 다음 날로 귀속 | 경계마다 정산 1건이 하루 밀림 |
| F-05 | `load_daily` | 같은 (종목, 일자) 중복 캔들을 값이 달라도 첫 줄만 조용히 유지 | 데이터 충돌이 드러나지 않음 |
| F-06 | `portfolio.simulate_portfolio` | `decision_index=-1`이면 첫 봉에 진입하고 비용을 마지막 봉 거래량으로 읽음; 정수 아님·범위 밖도 통과 | 잘못된 신호가 결과로 해석됨 |
| F-07 | `src/instrumentation.ts` | 특정 PC 절대경로 하드코딩, 폴더·실행 파일 확인 없음 | 다른 PC·Windows에서 조용히 스폰 실패 |
| F-08 | `src/*_index.ts` | `URL.pathname` 경로(Windows·공백 오류), 폴더 생성·쓰기 확인 없음, 청산 시각 누락 시 `Date.now()` 대입 | 저장 실패가 숨거나 시각이 조작됨 |
| F-09 | `src/fng_watcher_index.ts` | 고정 요청량, 장기 중단 뒤 빈 날짜 복구 없음, 재시도 없음, 같은 날짜 다른 값 덮어쓰기 가능 | 지표 공백·덮어쓰기 |
| F-10 | `src/maker_fill_monitor_index.ts` | 재시작 시 원장 일부만 복원(미완료 주문·AS 체크 유실), 이벤트 중복·순서 역전·손상 줄 처리 없음, 체결률 분모 불명확 | 재시작마다 체결률 왜곡 |
| F-11 | `paper_forward` 펀딩 조회 | 실패가 모두 SOURCE_ERROR 하나 — 네트워크/미지원 종목/스키마 변경을 구분 불가 | 운영 진단 불가 |
| F-12 | `portfolio_sim.py` | DEPRECATED인데 그냥 실행됨 | 결함 있는 숫자가 다시 쓰일 위험 |
| F-13 | 감사 기록 | 파일 해시에 묶인 기록이 없음(검토 후 바뀐 파일을 구분 못 함), `research_inventory`의 TESTED = "테스트 파일이 있음" | 검토 상태 과대 표시 |
| F-14 | CI | TS는 문법 변환만, 타입 검사·테스트 없음; 새 0 대체 코드를 막는 검사 없음 | 회귀 탐지 불가 |
| F-15 | 테스트 격리 | `run/discovery/tests`를 개별 실행하면 `test_00_isolate_shared_lock_dir`가 로드되지 않아 저장소의 `locked_shared/`에 기록됨. 그 모듈의 안내(`discover -p <file>`)도 틀렸다(그 방식도 로드하지 않음). 이번에 재현했고 생성된 파일은 삭제 | 개별 실행 시 운영 기록 오염 위험 |

## 결정
1. **공통 모듈 `run/research_data_integrity.py`** — 펀딩 귀속일, 보유일별 펀딩 완결성(`funding_schedule.settlement_check` 재사용), 가격 누락과 포지션 상태 불명, 충격 거래량 출처, 패널 정리, 시계열 상태를 한곳에 둔다. 무효일은 NaN으로 표시하고 통계에서 빼며 개수·비율을 결과에 남긴다(F-01, F-03, F-04, F-05).
2. **일봉 엔진 전부에 적용**: `newsrc_engine` v2, `broad_universe_dev`, `wide_universe_dev`, `cross_section_v2_dev`, `lowfreq_nonprice_dev`, `newsrc_r1/r11/r14_dev`, `hilo_volume_dev`, `alpha101_dev`, `dca_extreme_dev`; 펀딩 귀속일만 `basis_harvest_dev`, `ml_combo_r15b_dev`. 시장 평균 벤치마크도 0 대신 NaN(`dca_*`, `hilo_intraday_dev`).
3. **전진 기록 6상태 판정**(F-02): `final_status` 한 곳에서 PASS/FAIL/INVALID_DATA/INSUFFICIENT_DATA/UNDETERMINED/ERROR. PASS는 무효일 0, 추정 거래량 ≤ 10%, 보유 종목 패널 문제 없음, 통계 유한일 때만. Holm m=3 고정, 무효 후보 p=1. 사전등록 내용은 그대로이고, 바뀐 코드 해시는 첫 평가(2026-12-03) 전에 부속 문서에 기록했다. 자료가 완전하면 옛 코드와 결과가 같다(골든 테스트).
4. **포트폴리오 신호 검증**(F-06): `validate_signals`가 거부하고 `SignalInputError`(ValueError)를 던진다. runner는 이미 ValueError를 `passed False`로 처리한다. 모델 버전(`portfolio-mtm-v4`)은 그대로 두어 검증 컨텍스트 ID가 바뀌지 않게 했다(올바른 입력의 결과는 같다).
5. **수집기**(F-07~F-09): 설정 기반 경로(환경 변수, 상대경로는 서비스 폴더 기준, `fileURLToPath`, `node:path`), 시작 시 폴더 생성·쓰기 확인 후 실패하면 종료 코드 1. 청산 기록은 시각·가격·수량이 유효해야 저장(`Date.now()` 대입 제거). F&G는 마지막 정상 일자 기준 요청량, 재시도·백오프, 같은 응답 안의 충돌까지 포함한 비덮어쓰기 upsert, 복구·충돌 기록. instrumentation은 폴더·파일·실행 파일을 확인하고 `missing`/`failed`를 돌려준다.
6. **메이커 체결 감시기**(F-10): 원장 이벤트 재생으로 전체 복원, `eid` 중복 제거, 이상 이벤트는 SUSPECT로 분리, 꺼져 있던 동안의 미완료 주문은 `canceled(monitor_downtime)`, 놓친 AS 체크는 `as_missed`, 쓰기에 성공한 이벤트만 상태에 반영. 체결률 = `filled / (filled + expired)`, 종목별·일자별 통계.
7. **페이퍼 펀딩 실패 원인**(F-11): `cause`(NETWORK_ERROR, API_ERROR, SYMBOL_UNSUPPORTED, SCHEMA_CHANGED, NOT_COLLECTED, LOCAL_READ_ERROR, DATA_INVALID, COVERAGE_INCOMPLETE, SCHEDULE_UNKNOWN)와 `cache`(캐시 없음)를 기록한다. HTTP 오류 본문 앞 200바이트를 `FetchFailure`에 붙여 `-1121`을 구분한다. 판정(`status`)은 바뀌지 않는다.
8. **레거시 진입점**(F-12): `portfolio_sim.py`는 `--legacy-diagnostic` 없이 실행을 거부(종료 코드 2, pandas import 전)하고 출력에 `deprecated`, `verdict_use: FORBIDDEN`을 찍는다. `run_cycle.sh`의 기본값(legacy + 경고)은 바꾸지 않았고 잘못된 모드는 거부된다(테스트 추가).
9. **감사 레지스트리**(F-13): `run/audit/code_audit_registry.py` + `CODE_AUDIT_REGISTRY.json/.md`. git 트리의 362개 코드 파일, sha256, 상태 8종, 검토 해시·검토자·메모. 바뀐 파일은 자동으로 CHANGED_SINCE_REVIEW, 상태를 올리는 것은 `review` 명령뿐. `source_audit check`의 "INVENTORIED 금지" 규칙은 레지스트리가 현재 해시에서 읽음 상태를 가진 파일을 통과시킨다.
10. **테스트 격리**(F-15): 단일 실행 안내를 고쳤고(격리 모듈을 먼저 지정), CI가 테스트 뒤 `locked_shared/`가 생겼으면 실패한다.
11. **CI**(F-14): bun 테스트, TypeScript strict 타입 검사(`src/tsconfig.json`), `src/` 절대경로 검사, 레지스트리 검사, 0 대체 증가 차단(`zero_fill_scan.py`, 기준 39줄은 "옛 코드, 맞다고 주장하지 않음").

## 결과
- 테스트: 루트 323개(Python 3.11 + numpy 2.2.6/pandas 2.2.3, Python 3.13 모두 통과), bun 35개, 엔진 단위 테스트 전체(최종 보고서에 수치), 새 엔진 테스트 25개.
- 전진 기록: 2026-12-03 첫 자동 평가는 새 코드로 돈다. CLI와 출력 첫 줄 형식은 그대로라 워크플로(ADR 0115)는 바뀌지 않는다.

## 남은 위험 (UNRESOLVED)
- 연구 스크립트 204개 중 이번에 다시 읽은 것은 바꾼 파일뿐이다. 레지스트리에서 174개는 NOT_REVIEWED, 18개는 CHANGED_SINCE_REVIEW다. 지시문 §11의 전수 정밀 검토는 끝나지 않았다.
- 옛 DEV 스크립트의 0 대체 39줄(19개 파일)은 고치지 않고 묶어 두었다. 결론이 이미 NOT_PROMISING인 탐색이라 판정에는 영향이 없지만, 다시 쓰려면 먼저 고쳐야 한다.
- `confirm_iw0.py`(15분봉, 이미 소진된 IW0)는 바꾸지 않았다.
- REST 펀딩은 과거 정산 간격을 증명할 수 없다(ADR 0074 그대로). `SYMBOL_UNSUPPORTED`는 오류 본문을 읽을 수 있을 때만 구분된다.
- 쓰기 권한 검사는 access(W_OK)가 아니라 실제 쓰기 시험이다(bun 1.1.38이 0555 폴더를 쓰기 가능으로 답했음, CI에서 발견). TS 서비스는 실제 네트워크·장기 실행으로 확인하지 않았다(클라우드 장기 실행 금지 규칙).
- F-15: 격리 모듈을 빼고 개별 실행하면 여전히 기록이 생긴다(구조는 그대로, 안내와 CI 감시만 추가). 전체 `discover` 실행은 안전하다.
