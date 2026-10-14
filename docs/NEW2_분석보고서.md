# NEW2 프로젝트 분석 보고서 및 작업 지시서

> **생성일**: 2025-10-09
> **분석 대상**: github.com/tlsehd195/NEW2 (main 브랜치, HEAD `3e5c9cd`)
> **분석 범위**: 508개 파일 전수 조사 (Python 215개 / 약 29,000 라인)
> **용도**: Claude Code에 전달하여 우선순위대로 작업을 진행하기 위한 문서

---

## 1. 프로젝트 현황 요약

**"코인 단타 프로그램"이라기보다, 매우 훌륭한 '퀀트 연구·검증 플랫폼 + 페이퍼 트레이딩 시스템'이다.**
코드 품질과 안전장치(AST 가드, 킬 스위치, 잠긴 TEST 구간)는 상용 트레이딩 시스템 수준이다.
다만 아래 3가지가 프로젝트 완성도를 제한하고 있다:

1. **단타 전략 자체의 엣지(수익 우위)가 아직 하나도 검증되지 않음** — 15분봉 투표 후보 4종 전부 CANDIDATE 상태, 가설 등록 안 됨, BTC 확률 보정은 기저율보다 못했음(ADR-0042)
2. **페이퍼 엔진과 백테스트 엔진의 동작 불일치 버그 2건** (아래 §4 참조)
3. **라이브 실거래 경로는 의도적으로 미연결** — 이것은 설계대로이며(헌법상 검증 전 실거래 금지), 버그가 아님

---

## 2. 완성도 평가

### 영역별 점수

| 영역 | 완성도 | 근거 |
|---|---|---|
| 데이터 인프라 (`src/cointrader/data/`) | **90%** | 견고함. 단, WS 파서가 실전 메시지로 미검증(런북만 준비됨), depth 스트림 미연결 |
| 백테스트·검증 시스템 (`backtest/`, `validation/`) | **95%** | 과최적화 방지 장치(사전등록→PBO/DSR→잠긴 TEST)는 학술 수준. 검증 결과 수동 커밋백이 약점 |
| 리스크 관리 (`risk/`) | **85%** | fail-closed 철저. `markets.json`/`margin_policy.json`이 "verified: false" 자리표시자 |
| 페이퍼 실행 루프 (`paper/`, `execution/`) | **80%** | 대부분 완성. 시간손절 버그, depth 미사용 |
| 전략(엣지) 자체 (`strategies/`) | **40%** | 후보 4종 있으나 전부 미검증. 페이퍼가 24시간 도는 전략이 검증을 통과한 적 없음 |
| 라이브 실거래 (`live/`, `funding/`) | **15%** | LiveBroker 스캐폴드+안전게이트만. 지오패치(451), 실거래소 클라이언트 없음 — **의도된 설계** |
| 대시보드 (`dashboard-ui/`, `dashboard-next/`) | **85%** | 2종 완성. 자산곡선 차트 없음, next 버전 설정 저장 없음 |
| 문서·테스트·거버넌스 | **95%** | ADR 52편, 524 테스트, AST 안전장치 — 개인 프로젝트 수준을 넘음 |

### 종합

- **연구 + 페이퍼 트레이딩 시스템 기준: 약 75%**
- **실전 매매까지 포함한 완전한 단타 프로그램 기준: 약 55~60%**

부족한 부분은 "설계했지만 못 만든 것"이 아니라 **"의도적으로 아직 열지 않은 문(라이브 게이트)"과 "아직 증명 안 된 엣지"**이다. PROJECT_MASTER_PLAN 헌법이 "검증 없이 실거래 기능을 만들지 않는다"고 스스로 금지했기 때문에 라이브 미완성은 설계대로다.

---

## 3. 잘 연결된 파이프라인 (수정 불필요)

다음 핵심 데이터 흐름은 실제 코드로 완전히 연결되어 있으며 테스트로 보호되고 있다:

```
WebSocket 스트림 → ResilientEventFeed → PaperTrader.process
  → 캔들 특징추출(candle_features) + 레짐 분류(classify_regime)
  → 전략 투표 signal() → 데이터품질 게이트(evaluate_data_quality) + 킬스위치 확인
  → RiskEngine.evaluate_entry (fail-closed 사이징)
  → OrderIntent → ExecutionEngine → PaperBroker 체결
  → 보호손절/TP/트레일링 관리 → 청산
  → 저널 10계층 기록(LayeredStore) → 디스코드 알림(Notifier)
  → 학습사이클(DailyLearningCycle, 관측전용) + 대시보드(read_snapshot, 읽기전용) + KRW 회계
```

- 모든 기록이 `decision_id` / `client_order_id` / `risk_decision_id`로 조인 가능
- 연구 루프(사전등록 → 백테스트 → PBO/DSR → TEST 락 → 후보 원장)도 닫힌 고리로 완성
- AST 테스트(`tests/test_safety_boundaries.py`)가 킬스위치 해제·라이브 승인·APPROVED/DEPLOYED 전이·LiveBroker 생성을 자동 코드 경로에서 구조적으로 차단

---

## 4. 발견된 실제 버그 (직접 코드 검증 완료)

### 🔴 BUG-1: 페이퍼 엔진에 시간손절·일일진입상한 없음 (백테스트/페이퍼 불일치)

- **증상**: 12시간 시간손절(`max_hold_bars=48`)과 일일 진입상한(`max_entries_per_day`)이 백테스트 엔진에만 존재. 페이퍼 엔진은 `bars_held`를 증가만 하고 아무것도 안 함.
- **위치**:
  - 백테스트(정상): `src/cointrader/backtest/event_engine.py:299-301, 563-569` — `max_hold = getattr(strategy, "max_hold_bars", None)` 강제
  - 전략 정의: `src/cointrader/strategies/daytrade.py:33-34` — `max_hold_bars: int = 48`, `max_entries_per_day: int = 100`
  - 페이퍼(버그): `src/cointrader/paper/engine.py` — `bars_held`는 126행(정의), 433행(증가), 705행(저널 기록)에만 등장. **`max_hold_bars` 참조 전무**
- **영향**: 지금 페이퍼로 도는 게 정확히 `DayTradeVote`(48봉 보유)인데, 12시간이 지나도 청산 안 됨 → 백테스트 결과를 페이퍼에서 재현 불가. 검증된 수익률과 실제 페이퍼 성과가 어긋나는 원인.
- **수정 지시**: `paper/engine.py`의 `_advance` 또는 `_manage_open_trade`에서 `trade.bars_held >= strategy.max_hold_bars` 도달 시 시간손절 청산(청산 사유 기록: 한국어 "시간손절(12시간)"). 진입 경로에서 `max_entries_per_day` 카운트도 적용. 백테스트 `event_engine.py`의 구현과 의미를 일치시킬 것. 관련 테스트 추가(`tests/test_paper_trader.py`에 시간손절 케이스).

### 🟠 BUG-2: depth 스트림 구독만 하고 버림

- **증상**: `@depth@100ms` 스트림을 구독하고 `ResilientEventFeed`가 `DepthDelta`로 파싱하지만, `PaperTrader.process()`에 `DepthDelta` 분기가 없어 `PaperBroker.on_depth`/`LocalOrderBook`이 한 번도 호출되지 않음 → 시장가 체결이 항상 최우선호가 IOC 폴백만 사용(문서상 "풀 뎁스 체결"과 불일치).
- **위치**:
  - 구독: `src/cointrader/data/binance_ws.py:64` (`depth_stream`), `standard_streams`에 포함
  - 파싱: `src/cointrader/data/realtime.py` (DepthDelta 이벤트 생성됨)
  - 미소비: `src/cointrader/paper/engine.py:310-333` — `process()`가 BookTicker/TradeTick/MarkPriceUpdate/Candle만 분기. DepthDelta 없음
- **수정 지시**: `process()`에 `DepthDelta` 분기 추가 → `LocalOrderBook` 업데이트 → `PaperBroker.on_depth` 전달. 페이퍼 체결이 전체 호가 깊이를 사용하도록 개선. 네트워크 부하 고려해 100ms 딥을 전부 저널에 남기지 말 것(기존 `book_stats_1m` 집계와 일관되게).

### 🟡 BUG-3: GitHub Actions 브랜치 필터 오타

- **위치**: `.github/workflows/screening.yml:6`, `.github/workflows/signal_validation.yml:6`, `.github/workflows/vote_frequency_diag.yml:6` — `branches: ain]` (`[m` 유실)
- **참고**: `ci.yml:5`는 정상(`branches: [main]`). 세 파일 모두 push 잡이 `if:` 조건으로 스킵되므로 실질 피해는 없으나 오타 수정 필요.
- **수정 지시**: 3개 파일 모두 `branches: [main]`으로 수정.

### 🟡 BUG-4: 문서 오류 (주석이 코드와 불일치)

- **위치**: `src/cointrader/risk/margin_policy.py:16-17` — "아직 페이퍼/라이브 진입 경로에 연결 안 됨"이라고 적혀 있으나 실제로는 `paper/engine.py:494-502`에 연결되어 있음(라이브는 미연결 상태 맞음).
- **수정 지시**: 주석을 "페이퍼 경로에는 연결됨(ADR-0037), 라이브는 미연결"로 갱신.

### 정리 대상 (죽은 코드)

- `src/cointrader/journal/trade_journal.py` — 레거시, 테스트에서만 사용 → 제거 또는 deprecated 표시
- `src/cointrader/risk/sizing.py`, `risk/futures_sizing.py` — 페이퍼 경로에서 미사용
- `src/cointrader/data/orderbook.py` `LocalOrderBook` — BUG-2 수정 전까지 사실상 미사용

---

## 5. 무엇이 부족한가 (프로젝트 관점)

1. **검증된 단타 전략 0개**: 가설 20건 이상 전부 REJECTED/INCONCLUSIVE. 15분봉 투표 후보는 등록 자체를 안 함. "단타 프로그램"이라는 이름이 실제로 성립하려면 후보 하나라도 정식 가설 등록 → `run_validation.py` 완주 → OOS_TESTED까지 가야 함.
2. **실전 스트림 검증 미실행**: `scripts/check_ws_quality.py`를 사용자 PC에서 돌려 `malformed: 0 + decisions > 0` 확인하는 런북(`docs/` first-paper-run-ws-check.md)이 준비만 되어 있음.
3. **거래소 실제값 미확인**: `configs/markets.json`(`verified: false`, 틱사이즈·최소주문량을 "기억으로 타이핑한" 상태), `configs/margin_policy.json`(MMR 티어 ASSUMED) → `/fapi/v1/exchangeInfo`와 실제 마진 브래킷으로 갱신 필요. **단, 안전 파일 보호 훅(`.claude/hooks/protect-safety-files.sh`) 대상은 아니므로 AI가 수정 가능한지 먼저 확인 후 진행.**
4. **학습 사이클 무인 스케줄러 없음** (MASTER_PLAN 2·4단계): 드리프트 체크·섀도 챌린저가 수동 실행.
5. **검증 결과 커밋백 자동 검증 없음**: Actions 결과를 사람이 손으로 `configs/locked_windows.json`/`research/`에 복사하는데, ledger↔locks 일관성 CI 체크가 없음.
6. **대시보드 부족분**: 자산곡선(equity curve) 패널 없음(outcome 레이어에 데이터는 이미 있음), dashboard-next에 설정 저장 없음, 킬스위치/재조정 상태 표시 없음(`show_status.py`만).

---

## 6. Claude Code 작업 목록 (우선순위 순)

> 각 항목 완료 시 체크. ADR이 필요한 변경(아키텍처/안전장치)은 `scripts/adr_number.py new`로 번호 발급 후 진행할 것.

### Phase 1 — 신뢰성 버그 수정 (지금 당장)

- [ ] **T1** BUG-1 수정: 페이퍼 엔진에 `max_hold_bars` 시간손절 + `max_entries_per_day` 적용, 백테스트와 의미 일치, 테스트 추가
- [ ] **T2** BUG-2 수정: `process()`에 DepthDelta 분기 추가 → LocalOrderBook → PaperBroker.on_depth 연결, 테스트 추가
- [ ] **T3** BUG-3 수정: 3개 워크플로우 `branches: [main]` 오타 수정
- [ ] **T4** BUG-4 수정: `margin_policy.py` 주석 갱신
- [ ] **T5** 전체 테스트: `python3 -m pytest -q` 통과 확인 → PR → main 병합까지 세션 안에서 완료(CLAUDE.md 운영 규칙)

### Phase 2 — 검증 파이프라인 완주 (전략 완성도)

- [ ] **T6** 실전 스트림 검증: 사용자 PC에서 `python3 scripts/check_ws_quality.py` 실행, `malformed: 0 + decisions > 0` 확인 후 결과 기록(런북 문서 갱신)
- [ ] **T7** `configs/markets.json` 실제값 갱신: `/fapi/v1/exchangeInfo`에서 BTC/ETH(+SOL/XRP)의 tickSize/stepSize/minNotional 확인 후 `verified: true`, 안전 파일 보호 훅 해당 여부 먼저 확인
- [ ] **T8** 단타 후보 1종(예: `daytrade_indicator_vote_h16_c0.6_v1` 또는 ADR-0053 트레일링 스톱 변형) 정식 가설 등록 → `signal_validation.yml` workflow_dispatch로 검증 → 결과를 `research/` + `configs/locked_windows.json`에 커밋백
- [ ] **T9** ledger↔locked_windows 일관성 검증 스크립트/CI 추가 (수동 커밋백 누락 방지)

### Phase 3 — 자동화·기능 확장

- [ ] **T10** 학습 사이클 스케줄러: `run_paper_trader.py`에 DailyLearningCycle 정기 트리거 통합(ADR-0044 관측전용 유지) 또는 별도 스케줄 스크립트
- [ ] **T11** 대시보드 자산곡선 패널: `outcome` 레이어에서 누적 net_pnl/equity_return 시계열 그래프(dashboard-ui와 dashboard-next 모두)
- [ ] **T12** 대시보드 킬스위치·재조정 상태 표시 (읽기전용 유지)
- [ ] **T13** dashboard-next에 설정 저장 기능 포팅(패널 배치·지표 주기·차트 높이, dashboard-ui의 localStorage 방식 참고)
- [ ] **T14** 주간/월간 페이퍼 성과 리포트 자동 디스코드 전송 (`show_performance.py` 결과를 웹훅으로)
- [ ] **T15** 멀티 심볼 페이퍼 확장 검토 (SOL/XRP — ADR-0031 combined cap 활용)
- [ ] **T16** 확률비례 사이징 구현 검토 (ADR-0050에서 보류된 것 — 검증 통과한 후보가 생긴 후에)

### 참고: 손대면 안 되는 것 (AI 변경 금지)

- `src/cointrader/live/kill_switch.py`, `safety_gate.py`, `approval.py`
- `src/cointrader/funding/bridge.py`, `funding/approval.py`
- `configs/live/`, `.env`
- 킬스위치 해제·라이브 승인·APPROVED/DEPLOYED 전이·`execute_transfer` 호출 코드 추가 — AST 테스트가 막음
- `src/`는 표준 라이브러리만 사용 (`test_safety_boundaries.py`가 검사)

---

## 7. 프로젝트의 진짜 강점 (유지할 것)

- **95개 잠긴 TEST 구간** — 한 번 쓴 held-out 구간을 영구 은퇴시키는 다중검정 방지 장치
- **20건 가설의 정직한 기록** — 자기 전략도 REJECTED로 남긴 학문적 성실성
- **AST 안전장치** — 거버넌스 규칙을 코드 구조로 강제 (킬스위치 인간전용, 자동 승격 불가능)
- **10계층 추가전용 저널** — 모든 결정·근거·결과가 조인 가능한 ID로 남음
- **ADR 52편 + PROJECT_STATUS.md** — 세션이 바뀌어도 파일이 기억하는 프로젝트

이 강점들을 깨지 않는 선에서 위 작업 목록을 진행할 것. 특히 Phase 1(버그 수정)과 Phase 2(검증 완주)가 끝나기 전까지 라이브 관련 기능 추가는 하지 않는다 — 그것이 이 프로젝트의 헌법이다.
