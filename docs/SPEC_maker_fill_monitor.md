# SPEC — maker 체결 역학 실측 모니터 (Task 41, ③ 사용자 지시 이행)

작성: 2026-10-14 · 성격: **인프라 문서 (PREREG 아님 — 전략 측정·승격 주장 아님)**
사용자 지시: "3번 포트폴리오/maker 인프라 너가 승격 가능성 높다고 판단되는걸로 알아서 진행해"

## 0. 진행 방향 판정 근거 (포트폴리오 vs maker 인프라)

- **판정: maker 체결 인프라 채택**. 근거 3건:
  1. F2/X6-R 재검증(PREREG_f2x6_reverify_2027.md)의 1차 arm = **maker promo δ0/K16 promotion-basis**로
     이미 고정 — 백테스트 체결률 1.000(상한 가정)이 실전 체결 역학에서 유지되는지가
     2027 승격 심사의 최대 불확실성. 본 인프라가 이 수치를 직접 실측한다.
  2. 포트폴리오 계층은 통계적 승격 가능성을 높이지 못한다(검증 전 추정치의 위험 관리일 뿐 —
     발견 ㊹ 검정력 병목과 무관). maker 체결 실측은 **유일하게 남은 승격 후보(F2/X6-R)의
     핵심 전제를 검증**하고, P3가 통과할 경우의 실행 계층에도 재사용된다.
  3. N2 발견 ㉙("maker는 공짜 절감이 아니라 교환")은 일간 이벤트 백테스트 실측 —
     실시간 체결 확률·시간·어드버스 셀렉션의 분해는 미실측이며 본 모니터의 측정 대상.
- 포트폴리오 계층은 2027-01-11 판정 후 재평가 (판정 결과가 입력이 되므로 선행 설계의 가치가 낮음).

## 1. 서비스 규격 (고정)

- 위치: `mini-services/maker-fill-monitor/` · 포트 3022 · `bun index.ts` 직행(--hot 금지 — Task 38 선례)
- 영속 구조: `src/instrumentation.ts` SERVICES 등록 — 부팅 가드 + 5분 워치독 자동 부활
- 데이터 소스: Bybit v5 public linear — `orderbook.1.{BTCUSDT,ETHUSDT}` + `publicTrade.{...}`
- 셀: {BTC,ETH} × {롱,숏} × δ{0,5} = 8셀 · 배치: 4h UTC 경계(00/04/08/12/16/20) + 부팅 웜업 1회
  - δ0 = 유선가 join(라이브에서 중간가 rest 불가 — best bid/ask join, **δ_effective 실측 기록**)
  - δ5 = mid×(1∓0.0005), 틱 반올림 post-only 안전 방향(롱 내림/숏 올림)
- 체결 판정(N2 §2 준용·보수적): 트레이드 가격이 P_lim **엄격 통과** 시에만 체결(터치=미체결),
  체결가 = P_lim · K16 = 4h 만료(미체결 폐기 — "미체결은 포기" 규격)
- 어드버스 셀렉션(AS): 체결 후 mid 변화 — 롱 (mid_fill−mid_check)/mid_fill,
  숏 (mid_check−mid_fill)/mid_fill (bp·양수=체결자에게 불리) · 체크 +15분/+1시간/+4시간
- 상태: **페이퍼 모니터 — 실 주문·실 자금 없음** /stats에 명시

## 2. 데이터 저장 (사용자 질문 답변 포함 전수)

| 데이터 | 위치 | 형식 |
|---|---|---|
| 청산 캐스케이드(P3) | `mini-services/liq-collector/data/liquidations.db` | SQLite(+WAL) — UNIQUE 인덱스 |
| F&G 일별 장부 | `mini-services/fng-watcher/data/fng_ledger.jsonl` | JSONL append |
| **maker 체결 장부(신설)** | `mini-services/maker-fill-monitor/data/fill_ledger.jsonl` | JSONL append — placed/filled/expired/as |
| 시세 원천(15m kline·funding·프리미엄·CME·DVOL·김치 등) | `NEW2_run/data/` (13폴더·326MB) | csv/json |
| 연구 중간 산출 | `NEW2_run/research/` (124파일) | json/csv |
| 측정 결과·엔진 교차검증 | `NEW2_run/results/` | json+md |
| 사전등록·보고서·활문서 | `NEW2_run/*.md`·`NEW2_전략탐색_종합정리.md`·`worklog.md` | md |

## 3. 실측 이력 (구축 중 발생 — 정직 기록)

1. **프로세스 회수 재실증**: 에이전트 셸에서 기동한 프로세스는 명령 종료 후 회수됨(Task 37 선례 재확인)
   → instrumentation SERVICES 등록(3022) + next.config.ts touch로 부팅 가드 스폰 — 해소.
2. **pkill 사고**: 재시작 중 `pkill -f "bun index.ts"`가 수집기·감시기를 함께 종료(3서비스 동시 다운,
   수집 공백 수 분 발생) → 개별 PID 킬 절차로 복구. 운영 교훈: 서비스 킬은 포트 기반 PID로 한정.
   SQLite WAL 덕에 누적 무손상 확인(2,425→2,488건 재개).
3. **Bun.write append 버그 2연발**: `Bun.file(path,{append:true})`와 `Bun.write(path,data,{append:true})`
   모두 본 bun 빌드에서 트렁케이트/무시됨을 실측 → `node:fs appendFileSync`로 교체해 해소.
   (초기 장부 일부 placed 레코드 소실 — 체결 레코드는 보존됨, 정직 기록)
4. **첫 실측 체결**: BTCUSDT δ0 숏 6.2초·δ5 롱 33.6초·ETHUSDT δ5 숏(δ_eff 5.04bp) —
   체결 엔진·AS 추적·장부 전부 실데이터로 동작 확인. 통계 판단은 표본 누적 후(일 단위)만.

## 4. 2027 판정에서의 사용 방식

- 2027-01-11 F2/X6-R 판정 시: 본 모니터의 δ0 체결률·ttf·AS 실측치를
  promotion-basis 1.000 가정의 **할인 계수 근거**로 병기 (승격 심사 자료 — 게이트 추가 아님).
- 장부 원시(JSONL)가 유일한 원기록 — /stats는 라이브 모니터링 뷰일 뿐.
- 서비스 장기 중단 시에도 JSONL은 보존되며 재기동 시 재생산(replay) 후 계속 누적.
