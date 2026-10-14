# 웹 리서치 2차 (Task 8-b) — 미측정 방향 신호 원리 후보 확대

- 작성: Task 8-b 에이전트 (general-purpose)
- 기준일: 2026-10-12 (UTC 기준 세션)
- 전제: 15m봉 BTC/ETH USDT-M 선물, 최대 보유 12시간, 왕복 거래비용 12~20bp
- 배제 기준: 아래 16개 "이미 측정 완료(전멸/폐쇄)" 원리와 겹치지 않을 것
  1) 시간대/세션(tod) 2) 자기상관/VR 3) OI 방향/급변 4) BTC→ETH lead_lag 5) 비대칭(왜도/첨도/변동성 비대칭)
  6) SMC(OB/FVG/BOS) 7) 15m 내부 되돌림(sweep/FVG) 8) 거래량 돌파 변형 9) 모멘텀 그리드/상위TF 모멘텀
  10) 캐리(펀딩/베이시스 수집) 11) 펀딩 극단 역발상 12) 크라우드 포지셔닝 비율(metrics) 13) 김치프리미엄
  14) 변동성 예측 진단(har80 등) 15) 표준 지표(RSI/BB 반전 등) 16) 1m/5m 스캘핑

---

## 0. 검색 실행 요약 (정직 기록)

- **web_search (z-ai CLI) 16회 실행** → 결과 파일 `NEW2_run/research/r2_ws1.json ~ r2_ws16.json`
  - 유의한 결과 8회: ws2(오더북 불균형), ws7(베이시스 트레이드 맥락), ws8(코인베이스 프리미엄), ws9(청산 스냅샷 아카이브 복원 공지), ws11(옵션 스크우 센티먼트), ws12(ETF 플로우), ws13(교차거래소 차익 기계론), ws15(Boons-Prado basis-momentum 문헌)
  - 노이즈/실패 8회: ws1, ws3, ws4, ws5, ws6, ws10, ws14, ws16 — 긴 쿼리에서 검색 엔진이 일반 단어(사전/기업 홈페이지)로 폴백. 노이즈를 유의 출처로 꾸미지 않고 그대로 기록함
- **arxiv API 6쿼리**: "basis momentum"+bitcoin(0건), "order book imbalance"+bitcoin(1건 유효), "liquidation cascade"+bitcoin(0건), term structure+futures+bitcoin(0건), "coinbase premium"(0건), abs 필드 4쿼리(1건 유효: 2102.04591)
- **데이터 가용성 실측 (HTTP HEAD/GET + S3 listing, 오늘 실측)**: §3 표 참조. 아카이브 존재 여부를 문서 추측이 아니라 응답 코드로 확인함

---

## 1. 상위 3건 (우선순위)

### 🥇 C1. 스팟-퍼페추얼 베이시스 "모멘텀" (베이시스 변화율 → 퍼페 방향)
- **원리**: (퍼페 − 스팟) 프리미엄의 *수준*이 아니라 *최근 변화/모멘텀*이 선물 가격 방향을 예측한다는 원리. 상품선물 문헌의 basis-momentum(근월물 모멘텀 − 월물 모멘텀이 스팟·기간 프리미엄을 예측)의 암호화폐 인트라데이 적용.
- **출처**:
  - Boons & Prado, "Basis-Momentum", Journal of Finance 2019 — https://onlinelibrary.wiley.com/doi/10.1111/jofi.12796 (검색 스니펫: "Basis-momentum strongly outperforms benchmark characteristics in predicting commodity spot and term premiums in the time series and cross section") · SSRN: https://papers.ssrn.com (2015, cited ~194)
  - Kwon(2021) 후속: https://www.sciencedirect.com — "difference between the past 12 months' momentum in first- and second-nearby futures"
- **데이터 가용성 (실측 200 OK)**: UM 퍼페 1m kline + 스팟 1m kline 아카이브(양쪽 다 200 확인). 더 직접적으로 `futures/um/daily/premiumIndexKlines/BTCUSDT/1m/BTCUSDT-1m-*.zip` 1m 프리미엄 인덱스 OHLC가 2019-12~현재 존재(200 실측) → 스팟 데이터 없이도 베이시스 프록시 구성 가능.
- **비용 관점**: 신호 주기 15m~4h로 빈도가 낮아 왕복 12~20bp를 감수할 목표 변동폭을 선별할 여지. 다만 엣지 크기 자체는 미검증 — P1과 동일한 비용 장벽 검증 필수.
- **중복성 자가 체크**: 배제 10(캐리=베이시스 수집, 시장중립 수확)과 메커니즘이 다름(방향 트레이드). 배제 11(펀딩 레이트 수준 극단 역발상)과는 변수가 다르나(스팟-퍼페 프리미엄 vs 8h 펀딩율) 개념 인접 → 채택 시 기존 funding 신호와의 신호 상관 검사 조건 부여 권고. 배제 9(상위TF 모멘텀)는 가격 모멘텀이며 본건은 베이시스 모멘텀으로 상이. **비겹침 판정: 통과(단, 10·11 패밀리 상관 모니터링)**

### 🥈 C2. 오더플로우·호가 잔량 불균형 (bookTicker / bookDepth / aggTrades 통합)
- **원리**: 최우선 호가 잔량 비대칭(bookTicker), 5단 호가 깊이 비대칭(bookDepth), 테이커 방향성 체결 흐름(aggTrades → CVD)이 단기 방향을 이끈다는 미시구조 원리. 극단 분위수에서만 진입하는 형태.
- **출처**:
  - Cartea et al. (2018, Oxford ORA): https://ora.ox.ac.uk — "volume imbalance in the limit order book … is a good [predictor]" (Nasdaq 고빈도 데이터)
  - arXiv 2025-07-30 게시 "Order Book Filtration and Directional Signal Extraction" — 검색 스니펫: "days with excess buying (positive imbalance)…" (정확 ID 미확보, 제목 검색 0건 — 정직 기록)
  - 실무: hftbacktest 문서 "Market Making with Alpha - Order Book Imbalance" https://hftbacktest.readthedocs.io , dm13450 블로그(2022-02-02) https://dm13450.github.io
  - 기존 검증(Task 2): Kim & Hansen "Quarter-Hour Effect"(arXiv 2607.09426) — 15m 경계 시작 오더 불균형이 4~12h 수익 예측(당시 세션에서 원문 확인됨)
- **데이터 가용성 (실측 200 OK)**: `bookTicker` 일별 zip(장기), `bookDepth` 5단 호가 2023-01-01~현재(2023-01-01, 2024-01-15, 2026-09-03 모두 200 실측), `aggTrades` 일별 zip. 아카이브만으로 수년 백테스트 가능.
- **비용 관점**: 미시구조 알파는 거래당 기대값이 작아 비용 벽(12~20bp) 위험이 가장 큰 후보. 완화책: 극단 분위수 진입 + N2 지정가(maker) 진입 병행. 그래도 실패 확률 높음을 전제로 최소 비용으로 선별 측정 권고.
- **중복성 자가 체크**: 배제 7(SMC sweep/FVG·되돌림)은 가격액션 규칙, 본건은 통계적 잔량/체결 비대칭으로 상이. 배제 15(표준 지표) 아님. 배제 16(1m/5m 스캘핑) 아님 — 15m 신호·12h 보유 전제. 단, **저장소에 microstructure.py(imbalance/microprice/TradeFlowWindow) 기구현이 존재**하며 투표에 미연결 상태(ADR 계열 배제목록에는 없음) → "신규 원리"라기보다 "미측정 채널 와이어링" 성격. 측정 이력 재확인 후 진행. **비겹침 판정: 통과(구현 이력 확인 조건)**

### 🥉 C3. 코인베이스 프리미엄 (미국 현물 수요 프록시 → 방향 필터)
- **원리**: Coinbase(BTC-USD, 미국 규제 현물)와 Binance(BTC-USDT)의 가격 차이가 미국 기관/현물 수요 압력을 반영한다는 원리. 수준과 변화율을 방향 바이어스로 사용.
- **출처**:
  - CryptoQuant 지표 정의: https://cryptoquant.com — "percentage price difference between Coinbase Pro (USD) and Binance (USDT)"
  - Coinglass: https://www.coinglass.com — "Reflects the price difference between Coinbase (BTC-USD) and Binance (BTC-USDT)"
  - 언론 실황(지표 관행 확인용): CoinDesk 2026-08-28 "premium flipped positive for the first time since May", Yahoo Finance 2026-08-02 "negative 76 consecutive days", bitbo charts https://charts.bitbo.io
- **데이터 가용성 (실측)**: 유료 제공(CryptoQuant/Coinglass API) 대신 **자체 계산 가능**: Coinbase Exchange candles 15m — 2021-06-01 실데이터 응답 확인(무료, 페이지네이션 백필 필요) + Binance 스팟/퍼페 아카이브(200 실측). Coinglass/CryptoQuant 유료 API 없이도 재현 가능.
- **비용 관점**: 문헌상 예측력은 주로 일간 지평에서 보고됨 → 15m 단독 신호로는 약할 가능성. 국면/바이어스 필터로 사용하면 추가 왕복 거래가 거의 없어 비용 부담 최소.
- **중복성 자가 체크**: 배제 13(김치프리미엄 = KRW 현물 프리미엄, ADR-0086 폐기)과 같은 "교차 베뉴 프리미엄" 패밀리이지만 베뉴·메커니즘이 다름(미국 USD 현물 수요 vs 한국 KRW 프리미엄). 시간대/세션 효과(배제 1)와 구분 위해 조건부(시간대 무관) 정의 필요. **비겹침 판정: 조건부 통과(교차 베뉴 프리미엄 패밀리 인접성 명시)**

---

## 2. 전체 후보 목록 (9건)

| # | 후보 | 원리 요약 | 데이터(실측) | 비용 관점 | 배제목록 비겹침 판정 | 우선순위 |
|---|------|-----------|--------------|-----------|----------------------|----------|
| C1 | 스팟-퍼페 베이시스 모멘텀 | 베이시스 변화율이 선물 방향 예측 (Boons-Prado JF 2019) | premiumIndexKlines 1m 2019-12~ (200), 스팟+퍼페 kline (200) | 신호 저빈도, 선별 진입 여지 | 10·11과 메커니즘 상이, 상관 검사 조건 | **1** |
| C2 | 오더플로우·호가 불균형 | LOB 잔량 비대칭 + 테이커 흐름(CVD) → 단기 방향 (Cartea 2018, QHE 2607.09426) | bookTicker(200), bookDepth 2023-01~(200), aggTrades(200) | 거래당 알파 작음 → 비용벽 최고위험, maker 진입 전제 | 7·15·16 비겹침, 저장소 기구현 존재(미측정 채널) | **2** |
| C3 | 코인베이스 프리미엄 | 미국 현물 수요 프록시 (CryptoQuant/Coinglass 정의) | Coinbase 15m candles 2021-06~ 실측 + Binance 아카이브 | 필터 용도 시 비용 추가 소 | 13(김치)과 패밀리 인접, 베뉴·메커니즘 상이 | **3** |
| C4 | 프리미엄 인덱스 인트라-인터벌 다이내믹스 | 8h 펀딩율이 아닌 1m 프리미엄 인덱스의 순간 다이내믹스(스파이크/회귀) | premiumIndexKlines·markPriceKlines·indexPriceKlines 1m 2019-12~ (모두 200) | 신호 고빈도 → 비용벽 위험, 극단 분위수만 | 10(캐리)·11(8h 펀딩 극단)과 변수·지평 상이하나 펀딩 패밀리 인접 최대 → 장식용 제안 아님, 명시적 디코릴레이션 조건 | 4 |
| C5 | 분기선물 기간구조(커브) 슬로프 | 근월-원월 분기선물 스프레드(캘린더 스프레드)의 수준/변화가 방향 예측 | 분기선물 kline 아카이브 BTCUSDT_240329 등 (200) | 분기물 유동성 낮음 → 신호로만 사용, 실행은 퍼페 | 10(퍼페-스팟 캐리)과 다른 커브 원리 | 5 |
| C6 | 청산 캐스케이드 반전 | 강제청산 집중 후 반전(이미 저장소 P3 사전등록됨 — 신규 아님, 확인용 기재) | **아카이브 UM 폴더 비어있음(실측)**, CM BTCUSD_PERP만 2023-06-25~2024-10-14 존재 → 이후는 자체 수집기(ADR-0084) 필수 | 극단 이벤트 후 진입이라 빈도 낮음 | 배제목록 16번 어디에도 없음, 기존 P3 규격 존재 | — (파이프라인 기존) |
| C7 | 교차거래소 퍼페-퍼페 스프레드 회귀 | Binance↔OKX/Bybit 동일자산 스프레드 정상화 | Bybit v5 kline 15m 2021-06~ 실측, OKX history-candles 실측 | 스프레드 자체 차익실행은 비용 미달 가능성 높음 → 방향 필터로만 | 4(BTC→ETH lead-lag)·13(김치)과 상이(동일 자산 교차 베뉴) | 6 |
| C8 | 스팟 BTC ETF 일일 순유입 → 바이어스 필터 | 일일 ETF 플로우가 익일/당일 바이어스 형성 | bitbo/Glassnode 차트 존재(스니펫), **Farside curl 403(봇차단) → 자동 수집 방법 불확실** | 일간 필터라 추가 왕복 없음 | 1(시간대 계절성)과 상이한 외부 플로우 변수 | 7 |
| C9 | 옵션 25δ 스크우/리스크 리버설·DVOL 국면 | 파츠케 콜/풋 IV 스크우가 센티먼트 방향 형성 | DVOL 시간봉 2022-01~ 무료 실측(200), **25δ 스크우 역사는 무료 미확보(불확실)** | 신호 저빈도, 필터 용도 | 14(실현변동성 예측 진단)와 목적 상이(방향 센티먼트), 인접성 명시 | 8 |

### 검토 후 유보/제외 (정직 기록)
- **온체인 거래소 순유입**: ws14 검색이 전면 노이즈 + 무료 과거 데이터 소스 미확보(Glassnode/CryptoQuant 유료) → 후보에서 제외.
- **펀딩율 예측(front-run settlement)**: Task 2에서 이미 Presto Research(2024) 확인했고, 배제 10·11과 같은 펀딩 패밀리 + 동일 비용 장벽이 재현될 것으로 판단 → 제외.
- **VWAP 회귀 / 돈치안 브레이크아웃 계열**: 배제 15(표준 지표 "등") 및 8·9(돌파/모멘텀 계열 반복 전멸)와의 경계가 불분명해 제안하지 않음.
- **매크로 발표(CPI/FOMC) 이벤트 윈도우 모멘텀**: 원리 자체는 배제 1(tod_seasonal, 무조건부 시간대)과 달라 이벤트 조건부이지만, "특정 시각 효과"와의 인접성 + 15m 전략에서 표본수 문제로 유보(원하면 별도 사전등록 가능).

---

## 3. 데이터 가용성 실측 로그 (2026-10-12, curl HEAD/GET + S3 listing)

| 데이터 | URL | 결과 |
|---|---|---|
| UM 퍼페 1m kline | `https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01-15.zip` | **200** |
| 스팟 1m kline | `https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01-15.zip` | **200** |
| 프리미엄 인덱스 kline 1m | `https://data.binance.vision/data/futures/um/daily/premiumIndexKlines/BTCUSDT/1m/BTCUSDT-1m-2024-01-15.zip` | **200** (2019-12-23~) |
| 마크 프라이스 kline 1m | `https://data.binance.vision/data/futures/um/daily/markPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2024-01-15.zip` | **200** (파일명 주의: `BTCUSDT-1m-…`, 폴더가 유형 구분) |
| 인덱스 프라이스 kline 1m | `.../indexPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2024-01-15.zip` | **200** |
| bookTicker | `.../daily/bookTicker/BTCUSDT/BTCUSDT-bookTicker-2024-01-15.zip` | **200** |
| bookDepth(5단 호가) | `.../daily/bookDepth/BTCUSDT/BTCUSDT-bookDepth-2023-01-01.zip` / `-2024-01-15` / `-2026-09-03` | **200** (2023-01-01~현재) |
| aggTrades | `.../daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2024-01-15.zip` | **200** |
| 분기선물 kline | `.../daily/klines/BTCUSDT_240329/1m/BTCUSDT_240329-1m-2024-01-15.zip` | **200** |
| liquidationSnapshot UM | S3 listing `prefix=data/futures/um/daily/liquidationSnapshot/` | **빈 폴더 (BTCUSDT 데이터 없음 실측)** |
| liquidationSnapshot CM | `prefix=data/futures/cm/daily/liquidationSnapshot/BTCUSD_PERP/` | **200, 2023-06-25 ~ 2024-10-14만 존재** (GitHub 이슈 "restored at data.binance.vision … cm/daily/liquidationSnapshot" 스니펫과 일치) |
| Coinbase 현물 캔들 | `https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=900&start=2021-06-01T00:00:00Z&end=...` | **200, 2021-06-01 실데이터 응답** |
| Bybit 선물 kline | `https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=15&start=1622509200000...` | **200, 2021-06-01 실데이터 응답** |
| OKX 스왑 캔들 | `https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP&bar=15m&after=...` | **200, 2024-01-01 실데이터 응답** |
| Deribit DVOL | `https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&start_timestamp=1640995200000...` | **200, 2022-01-01 시간봉 실데이터** |
| Farside ETF 플로우 | `https://www.farside.co.uk/btc/` | **403 (curl 봇차단)** — 브라우저 접근은 가능해 보이나 자동 수집 방법 불확실 |

참고: metrics 아카이브(크라우드 포지셔닝 비율 등)는 Task 4에서 이미 실측·사용됨(ADR-0080으로 폐기된 N1과 연결) → 재제안 없음.

---

## 4. 권고 다음 단계 (저장소 규칙 준수 전제)

1. **C1 최우선 진단(저비용)**: premiumIndexKlines 1m 다운로드 → 베이시스 변화율(예: Δ1h/4h z-score)과 향후 4h/8h/12h 퍼페 수익의 순위상관·분위수별 기대수익 진단. P1과 동일한 Holm·비용 게이트 규격 재사용, 사전등록 먼저.
2. **C2 선별 측정**: bookTicker 기반 최우선 잔량 불균형 + aggTrades 기반 CVD를 15m 경계 스냅샷으로 집약(QHE 규격) → 극단 분위수 셀만 측정. N2 지정가 진입 확정 여부와 함께 결정.
3. **C3 필터 성격으로 병행**: Coinbase 프리미엄은 단독 신호 대신 기존 투표의 승산 필터 후보로 사전등록.
4. 공통: 배제 10·11(펀딩/베이시스 패밀리), 13(교차 베뉴 프리미엄)과의 **신호 상관 매트릭스**를 1차 진단에 포함해 패밀리 재탕 방지.

---

## 5. 출처 파일 인덱스

- 검색 원문 JSON: `NEW2_run/research/r2_ws1.json ~ r2_ws16.json`
- 이전 세션 참조(재검증 안 함, 정직 표기): Kim & Hansen "Quarter-Hour Effect" arXiv 2607.09426(Task 2 원문 확인), Presto Research 펀딩비 예측(Task 2)
