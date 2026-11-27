# 데이터 품질 표준 · 전진 판정 규칙 · 실행 방법 (ADR 0116)

이 문서는 연구 코드(`run/*.py`, `run/discovery/`)와 사용자 PC에서 도는 수집기(`src/*.ts`)가 따르는 공통 규칙이다. 근거 코드와 테스트를 함께 적었다. 규칙을 바꿀 때는 새 ADR을 쓴다.

## 1. 데이터 품질 표준

### 1-1. 원칙
- **없는 값은 0이 아니다.** 가격·펀딩·거래량이 없으면 그 날(또는 그 거래)은 "관측 불가"로 표시하고 통계에서 뺀 뒤, 뺀 개수를 결과에 남긴다. 0은 실제로 관측된 0(예: 공개 아카이브의 펀딩률 0, 포지션이 없는 날의 손익 0)만 뜻한다.
- **대체값은 출처를 남긴다.** 추정으로 바꾼 값은 종류와 비율을 기록하고, 비율이 한도를 넘으면 정상 판정을 하지 않는다.
- **판정은 한곳에서 한다.** 기록만 하고 판정에 반영하지 않는 플래그를 두지 않는다.

### 1-2. 공통 모듈 `run/research_data_integrity.py` (`research-data-integrity-v1`)
| 항목 | 규칙 | 코드 |
|---|---|---|
| 펀딩 귀속일 | 정산 시각 `D+1일`(+아카이브 지터 최대 60초)은 `D`일 보유분이다. 옛 규칙 `(t-1)//DAY`는 00:00:00.002 정산을 다음 날로 보냈다(F-04). | `funding_day` |
| 펀딩 완결성 | 보유일 `(D, D+1일]`마다 `funding_schedule.settlement_check`로, 아카이브가 선언한 `funding_interval_hours` 기준 예정 정산이 모두 있는지 본다. 없으면 `FUNDING_DATA_MISSING` / `FUNDING_SETTLEMENT_GAP`, 간격 미선언이면 `FUNDING_SCHEDULE_UNKNOWN`, 행 오류면 `FUNDING_DATA_INVALID`. | `FundingCalendar` |
| 가격 누락 | 보유 중 수익률이 없으면 `PRICE_RETURN_MISSING`. 가중치가 움직이는 보유(드리프트)에서는 그 뒤 포지션 상태를 알 수 없으므로 남은 보유일과 다음 리밸런스까지 `POSITION_STATE_UNKNOWN`. | `SeriesState.hold` |
| 충격 비용 거래량 | `OBSERVED_BAR2345`(진입 직후 봉 실측) → `ESTIMATED_DAILY_96`(일 거래량/96) → `ESTIMATED_MEAN30_96`(30일 평균/96, 보정 비용일 때만) → 없으면 `MISSING`(그날 무효). 옛 대체값 1.0은 없앴다. | `impact_volume`, `VolumeProvenance` |
| 패널(일봉) 정리 | 같은 (종목, 시각) 중복이 값까지 같으면 하나만 남기고, 값이 다르면 둘 다 버리고 `PANEL_DUPLICATE_CONFLICT`. 비정상 값(음수·0 가격, 음수 거래량, 비유한)은 `PANEL_INVALID_VALUE`, 자정이 아닌 시각은 `PANEL_OFF_GRID_TIMESTAMP`. | `clean_panel` |
| 시계열 상태 | `COMPLETE` / `COMPLETE_WITH_EXCLUSIONS`(무효일 ≤ 2%) / `INVALID_DATA`(무효일 > 2%) / `UNDETERMINED`(추정 거래량 비율 > 10%) / `INSUFFICIENT_DATA`(평가일 0). | `series_status`, `POLICY` |

적용 범위: `newsrc_engine.py`(v2), `broad_universe_dev.py`, `wide_universe_dev.py`, `cross_section_v2_dev.py`, `lowfreq_nonprice_dev.py`, `newsrc_r1_dev.py`, `newsrc_r11_dev.py`, `newsrc_r14_dev.py`, `hilo_volume_dev.py`, `alpha101_dev.py`, `dca_extreme_dev.py`, 펀딩 귀속일만: `basis_harvest_dev.py`, `ml_combo_r15b_dev.py`. 나머지 옛 DEV 스크립트의 0 대체 39줄은 `run/audit/ZERO_FILL_BASELINE.json`에 "옛 코드, 맞다고 주장하지 않음"으로 묶어 두었고, CI가 늘어나는 것을 막는다(§4).

테스트: `run/test_research_data_integrity.py`, `run/test_forward_nearmiss.py`(`ForwardDataIntegrityTest`, `ImpactVolumeProvenanceTest`), `run/test_cross_section_v2.py`.

### 1-3. 포트폴리오 입력 (`run/discovery/portfolio.py`, `portfolio-signals-v1`)
`decision_index`는 0 이상 정수이고 진입 봉(`decision_index+1`)이 데이터 안에 있어야 한다. 음수·범위 밖·정수 아님(`True`, `3.7`, `"3"`, NaN)·알 수 없는 종목·방향 0/2·horizon ≤ 0·`reduce`가 (0,1) 밖이면 `SignalInputError`(ValueError)로 거부한다. 봉은 시각이 엄격히 증가하고 가격이 유한한 양수, OHLC가 일관되고 거래대금이 0 이상이어야 한다. 마지막 봉에서 진입해야 하는 신호는 실행하지 않고 `signals_unexecuted_at_data_end`로 센다. 테스트: `run/discovery/tests/test_adr0116_portfolio_inputs.py`.

### 1-4. 페이퍼 펀딩 조회 실패 원인 (`run/discovery/paper_forward.py`)
판정은 그대로 `status`만 쓴다. `cause`는 운영자가 원인을 구분하도록 추가로 남긴다. 추측하지 않으며, 구분할 수 없으면 넓은 분류를 쓴다.

| cause | 뜻 |
|---|---|
| `NONE` | 사용 가능(`VALID`, `VALID_ZERO_RATE`) |
| `NETWORK_ERROR` | 시간 초과·연결 끊김·DNS·프록시·HTTP 429/5xx (재시도 후에도 실패) |
| `API_ERROR` | 그 밖의 HTTP 4xx, 거래소 오류 객체 |
| `SYMBOL_UNSUPPORTED` | 거래소가 `-1121 Invalid symbol`로 답함 (본문을 읽을 수 있을 때만) |
| `SCHEMA_CHANGED` | 응답이 JSON이 아님, 목록이 아님, 행 필드를 읽을 수 없음 |
| `NOT_COLLECTED` | 로컬 아카이브에 그 기간 파일이 없음 |
| `LOCAL_READ_ERROR` | 로컬 파일을 읽지 못함 |
| `DATA_INVALID` | 중복·역순·범위 밖·다른 종목 행 |
| `COVERAGE_INCOMPLETE` | 페이지 한도까지 꽉 찬 응답(뒤가 잘림) |
| `SCHEDULE_UNKNOWN` | 과거 정산 간격을 증명할 수 없음(REST, ADR 0074) |

캐시: 어느 피드도 펀딩 응답을 캐시하지 않는다(`cache` 필드에 기록). 테스트: `run/discovery/tests/test_adr0116_funding_causes.py`.

## 2. 전진 기록(CL1·FIP1·AF1, ADR 0112) 판정 규칙

결과 상태는 `PASS`, `FAIL`, `INVALID_DATA`, `INSUFFICIENT_DATA`, `UNDETERMINED`, `ERROR` 여섯 가지이고, `forward_nearmiss.final_status` 한 곳에서만 정한다.

1. 통계가 유한하지 않으면(관측 단계 제외) `ERROR`.
2. 데이터 상태가 `INVALID_DATA`(무효일 > 2%, 또는 보유 종목에 패널 문제)이면 `INVALID_DATA`.
3. 평가일이 없으면 `INSUFFICIENT_DATA`.
4. 사전등록 규칙이 `FORWARD_PASS`여도 무효일이 하루라도 있거나 추정 거래량 비율이 10%를 넘으면 `UNDETERMINED`. 무효일 0이고 모든 검사가 통과해야 `PASS`.
5. 사전등록 규칙이 실패(`FORWARD_FAIL`, 182일 중간 `FORWARD_FAIL_FUTILITY`)면 `FAIL`. 단 추정 비율 초과로 비용 자체가 불확실하면 `UNDETERMINED`.
6. 그 밖(관측 중, 중간 점검에서 중단 조건 아님)은 `INSUFFICIENT_DATA`.

Holm 보정의 m=3은 고정이다. 무효·자료 부족 후보는 p=1로 넣는다(빼서 m을 줄이지 않는다). 사전등록의 후보·파라미터·유니버스·비용·통계 기준은 바뀌지 않았다. 바뀐 코드 해시는 사전등록 부속 문서 `run/audit/evidence_forward_nearmiss_20261120/prereg_forward_nearmiss_v1_addendum_data_integrity.json`에 첫 평가(2026-12-03) 전에 기록했고, 자료가 완전할 때 옛 코드와 결과가 같다는 것을 골든 테스트가 보인다.

## 3. 수집기·감시기 실행 방법 (사용자 PC, 클라우드에서 장기 실행 금지)

| 서비스 | 파일 | 포트 | 저장 위치 설정 | 기본값 |
|---|---|---|---|---|
| 청산 수집기 | `src/liq_collector_index.ts` | 3020 | `LIQ_DATA_DIR` | `<서비스 폴더>/data` |
| F&G 감시기 | `src/fng_watcher_index.ts` | 3021 | `FNG_DATA_DIR` | `<서비스 폴더>/data` |
| 메이커 체결 감시기 | `src/maker_fill_monitor_index.ts` | 3022 | `MAKER_DATA_DIR` | `<서비스 폴더>/data` |
| 스폰 감독 | `src/instrumentation.ts` | – | `NEW2_MINI_SERVICES_DIR`, `NEW2_<NAME>_DIR`, `NEW2_BUN_BIN` | `<작업 디렉터리>/mini-services/<폴더>` |

- 상대경로는 서비스 파일 위치 기준이다. 경로는 `node:path`와 `fileURLToPath`로 만들어 Windows(`C:\...`)와 공백 경로에서도 맞다.
- 시작할 때 저장 폴더를 만들고 쓰기 권한을 확인한다. 실패하면 이유를 출력하고 종료 코드 1로 끝난다(조용히 실패하지 않음).
- F&G 감시기: 장부의 마지막 정상 일자부터 빠진 날짜를 계산해 요청량을 정하고(10~4000), 3회 재시도(2/4/8초, 429는 `Retry-After` 최대 300초)한다. 같은 날짜·같은 값은 건너뛰고, 다른 값은 덮어쓰지 않고 `fng_conflicts.jsonl`에 남긴다. 복구·실패 기록은 `fng_recovery.jsonl`, 손상된 장부 줄은 `/stats`의 `corrupt_ledger_lines`.
- 메이커 감시기: 원장 `fill_ledger.jsonl` 하나로 재시작 시 상태를 전부 복원한다. 이벤트는 `placed`, `partial`, `filled`, `expired`, `canceled`, `as`, `as_missed`이고 `eid`로 중복을 거른다. 내용이 다른 중복, 순서 역전, 종료 후 전이, 잘못된 주문은 그 주문을 SUSPECT로 표시해 통계에서 따로 센다. 감시기가 꺼져 있던 동안의 미완료 주문은 `canceled(monitor_downtime)`로 닫는다(체결도 만료도 아님). 체결률 분모는 4시간 창 끝까지 관측된 주문(`filled + expired`)뿐이다.
- 실행 예: `cd mini-services/fng-watcher && FNG_DATA_DIR=D:\new2\fng bun run dev`.
- 테스트: `cd src && bun test` (임시 폴더, 주입한 fetch·시계, 네트워크 없음). 타입 검사: `tsc -p src/tsconfig.json`(CI가 typescript 5.7.3, bun-types 1.1.38로 실행).

## 4. 감사 기록과 CI

- `run/audit/code_audit_registry.py`: git 트리의 모든 코드 파일(362개)을 sha256과 함께 등록한다. 상태는 `FULL_SOURCE_REVIEW`, `LOGIC_TRACED`, `TESTED`, `PARTIALLY_REVIEWED`, `INVENTORIED`, `NOT_REVIEWED`, `CHANGED_SINCE_REVIEW`, `EXEMPTED_WITH_REASON`. 상태는 검토 당시 해시에 묶이고, 파일이 바뀌면 자동으로 `CHANGED_SINCE_REVIEW`가 된다. 상태를 올리는 것은 `review` 명령(검토자·메모 필수)뿐이다.
- CI(`research-ci.yml`)가 막는 것: 등록 안 된 새 파일, 사라지거나 옮겨진 파일, 해시 불일치, 다른 버전에 대한 검토 주장, 메모 없는 미검토·예외, 테스트가 없는 새 소스 파일, 새 0 대체 줄, `src/`의 특정 PC 절대경로, TypeScript 타입 오류, bun 테스트 실패.

## 5. 모듈 의존 관계 (요약)

```
funding_schedule (run/discovery) ──> research_data_integrity ──> broad_universe_dev ──> cross_section_v2_dev ──> newsrc_engine ──> forward_nearmiss
                                                         │                        ├─> wide_universe_dev        └─> newsrc_r*_dev
                                                         └─> (각 DEV 스크립트)     └─> dca_* / hilo_* / basis_harvest_dev / ml_combo_r15b_dev
data_fetch ──> paper_forward (BinancePublicFeed, FileFeed) ──> runner / pipeline
portfolio (signals-v1) ──> runner (except ValueError → passed False)
src/instrumentation.ts ──spawn──> mini-services/{liq-collector, fng-watcher, maker-fill-monitor}/index.ts
```
