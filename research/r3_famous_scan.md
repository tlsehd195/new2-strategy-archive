# 웹 리서치 3차 (Task 9-a) — "유명 전략" 전수 스캔

- 작성: Task 9-a 에이전트 (general-purpose)
- 기준일: 2026-10-13 (UTC 기준 세션)
- 전제: 15m봉 BTC/ETH USDT-M 선물, 최대 보유 12시간, 왕복 거래비용 12~20bp
- 목적: 사용자 지시 "겹치지 않게 유명한 것들로 최대한 찾아보고, 유명하지는 않지만 테스트했을 때 유능하다고 판단되는 것들 전부" — **유명 전략 카탈로그 관점**의 3차 전수 스캔
- 상위 문서: 2차 리서치 `web_research_round2.md` (C1~C9 후보 9건 존재 — 이번엔 그와 중복되는 것은 재발굴하지 않음)
- 배제목록: 16개 "이미 측정 완료(전멸)" 원리 (문서 하단 §6 참조)

---

## 0. 검색 실행 요약 (정직 기록)

- **web_search (z-ai CLI) 26회 실행** → 결과 파일 `NEW2_run/research/r3_ws1.json ~ r3_ws26.json`
  - 유의한 결과 **19회 (73%)**, 노이즈/실패 **7회 (27%)**
  - 노이즈 7회 중 5회는 짧은 변형 쿼리로 재검색하여 회복 성공(ORB·TTM·페어트레이딩·VWAP·Wyckoff), 1회는 2번째 변형(이치모쿠)에서 회복, 1회는 회복 불필요(카탈로그 쿼리 "best known..." — 대체 출처 확보됨)
  - 노이즈 패턴(2차 실측과 동일 재현): **엔진이 쿼리의 한 단어를 일반 단어로 해석**해 전혀 다른 주제로 폴백. 실측 예: `best known...`→사전·CISA, `cointegration`→"Connectedness"(사회학 논문), `TTM squeeze breakout crypto`→고래(Whale), `Ichimoku ... backtest`→Tether(회사), `wyckoff` 단어 탈락→범용 백테스트 툴. 쿼리를 짧게+고유명사 유지로 회복됨.
- **무료 데이터 소스 2건 라이브 실측 (오늘, curl)**:
  1. alternative.me 공포·탐욕 지수 API — **200 OK, 일별 값 반환** (value/classification 정상)
  2. Yahoo Finance `BTC=F`(CME 비트코인 선물) 차트 API — **200 OK, 일봉 OHLC** (`firstTradeDate: 1513573200` = 2017-12-18, CME 상장일부터)
- **백테스트 좋다고 "주장"하는 것과 검증된 것의 구분 원칙 준수**: 블로그/아카데미 글의 수익 주장은 모두 "주장"으로만 기록. 학술 논문은 피어리뷰 여부 표기. 저장소 측정 이력과 충돌하는 주장은 배제 판정 우선.

---

## 1. 요약 표 — 신규 비겹침 후보 (조건부 통과 4건)

> 정직 전제: **완전 무결 overlap-free는 0건**. 4건 모두 기존 배제/기존 후보와 어느 정도 패밀리 인접성이 있어 "조건부 통과(인접성 명시)"로 판정함. 인접성 없는 완전 신규는 없었다는 것이 이번 전수의 결론이기도 함.

| # | 전략명 | 유명도 | 비겹침 판정 | 데이터 (실측) | 비용 리스크 |
|---|--------|--------|------------|---------------|-------------|
| F1 | **CME 갭 (비트코인 선물 주말 갭 필)** | 높음 — OKX/Bitget/BloFin 아카데미 전부 전용 글 + Cointelegraph 뉴스 빈도 | **조건부 통과** — 배제1(tod_seasonal)과 달리 갭 존재라는 이벤트 조건부. 단, 진입이 CME 재개장(주중 특정 시각)에 몰림 → 시간 앵커 인접성 명시 | CME 일봉 외부 필요 — **Yahoo `BTC=F` 실측 200 (2017-12~)**. Binance kline만으론 불가 | 주당 1~2회 이벤트, 보유 수 시간~수일 → 빈도 최저 수준이라 비용 친화적. 리스크: 갭 메움까지 >12h 걸리는 비율 미지수 |
| F2 | **공포·탐욕 지수 (F&G) 극단 역발/바이어스 필터** | 매우 높음 — CMC "thoroughly backtested" 주장, Binance 지표 제공, CNN 원조 | **조건부 통과** — 배제12(크라우드 포지셔닝 비율, ADR-0080 폐기)와 같은 "군중 상태" 패밀리이나 변수 상이(복합 센티먼트 vs 거래소 포지션 비율) → 신호 디코릴레이션 검사 조건. 구성 성분 중 변동성 25%·모멘텀 25%는 배제 9·14와 성분 수준 인접 (정직 표기) | **alternative.me API 실측 200 (일별, 무료)**. 전체 역사 길이는 미검증(통상 2019-02~로 알려짐) | 일간 필터/오버레이 → 추가 왕복 ~0. 리스크: 지수 성분이 이미 배제된 가격 파생 변수 포함 |
| F3 | **BTC-ETH 페어 트레이딩 (공적분/스프레드 평균회귀)** | 학술 중상 — Kristoufek 2023(SD, cited 32), Dann 2024, IJSRA 2026 학위논문 | **조건부 통과** — 배제 16번 어디에도 없음, C1~C9와도 불일치. 단 저장소 `cross_sectional` 연구 모듈 + Task 4 N4(XS 단기 되돌림)와 **패밀리 인접** 명시. C7(교차거래소 동일자산)·C1(베이시스)과는 메커니즘 상이 | **Binance kline(BTC+ETH 15m)만으로 가능 — 신규 데이터 0** (저장소 이미 보유) | **2레그 → 왕복 비용 24~40bp로 비용벽 2배** — 최고위험. N2 지정가 진입이 사실상 전제. 리스크: 공적분 관계 시간가변성 |
| F4 | **구글 트렌드 관심도 급등 → 방향 (attention)** | 중간 — Aslanidis et al. 2022(cited ~144), Quantpedia 2024 | **조건부 통과** — 배제 16번·C1~C9 어디에도 없음. F2와 같은 "센티먼트/관심" 패밀리이나 변수 상이(검색량 vs 복합지수) | **불확실** — pytrends(구글 비공식 API) 알려진 불안정, Coinglass 차트 존재하나 자동 수집 방법 미확인 | 일간/주간 필터 → 비용 추가 소. 리스크: 데이터 수집 안정성 + 트렌드 값의 상대치/개정 문제 |

---

## 2. 신규 후보 상세

### F1. CME 갭 (CME Bitcoin Futures Gap)
- **원리**: CME 비트코인 선물은 주중 ~23시간만 거래하고 BTC 현물은 24/7이라, 금요일 마감가와 일요/월요 개장가 사이에 갭이 생긴다. 유명 실무 주장: 갭은 "메워지는(fill)" 경향이 있다 → 갭 반대방향 진입(되돌림) 또는 갭 방향 모멘텀 확인용. 주의: 이는 **실무 주장**이며 체계적 검증 논문은 이번 스캔에서 확인 못함(정직 기록).
- **출처** (검색 스니펫 요지, r3_ws13):
  - OKX Learn, "What's the Bitcoin CME gap and how to trade it" (2024-02-25) — https://www.okx.com/Learn/Trading — "The 'CME gap' is when the opening price of a Bitcoin futures contract is significantly different from its closing price."
  - Bitget Academy (2024-06-19), BloFin Academy (2025-05-21) — 갭 원인(거래시간 차)·트레이딩 가이드
  - Cointelegraph via TradingView News (2025-10-28) — "The latest CME futures gap gains popularity as a downside target" — **뉴스 수준에서 지속적으로 회자됨 = 유명도 증거**
  - Binance Square (2025-09-14) — 갭을 타깃/진입점으로 사용하는 방법 안내
- **배제목록 비겹침 판정**:
  - 배제1(tod_seasonal, 무조건부 시간대 수익 패턴)과 다름: **갭이라는 이벤트 조건이 먼저** 존재해야 트레이드됨. 다만 진입 집중 시각(재개장 직후)이 생기므로 인접성 명시.
  - 배제 8·9(돌파/모멘텀 반복 전멸)와 다름: 갭 필은 되돌림 메커니즘.
  - C1~C9와 불일치: C3(코인베이스 프리미엄)은 동시점 교차 베뉴 스프레드, F1은 **동일 베뉴의 시간 갭**으로 상이. C5(분기선물 커브)는 만기구조 슬로프, F1은 커브 무관.
  - **판정: 조건부 통과 (배제1 인접성 명시)**
- **데이터 가용성**: CME 일봉 OHLC는 Binance 아카이브에 없음(외부 필수). **Yahoo Finance `BTC=F` 실측 200 OK — 2017-12-18(CME 상장)부터 일봉**. 갭 정의에 필요한 최소 데이터(금요 정산가/월요 개장가) 확보 가능. 대안: CME Data Portal(회원가입 필요), Investing.com 스크레이핑(Farside처럼 403 가능성 — 미검증). ETH도 CME 이더선물(`ETH=F`, 2021-02~)이 있으나 이번 세션 미실측 — 불확실 표기.
- **비용 관점**: 이벤트가 주당 1~2회 → 신호 빈도 최저 수준, 목표 변동폭(갭 크기)이 통상 수백~수천 달러로 왕복 12~20bp를 흡수할 여지가 구조적으로 있음. 최대 보유 12시간 제약과 갭 소요시간 분포의 정합성은 측정 필요.
- **리스크/정직 노트**: 갭 메움 주장의 체계적 검증 부재, 표본 수(연 ~50개 갭), 2020년 이후 CME 거래시간 확장으로 갭 자체가 줄었다는 실무 지적 존재(이번 스캔에서 원문 미확인).

### F2. 공포·탐욕 지수(F&G) 극단 → 역발상/바이어스 필터
- **원리**: 변동성(25%)·모멘텀(25%)·소셜(15%)·지배력(10%)·트렌드(10%)·설문(15%) 합성 센티먼트 지수. 극단 공포(<20) 매수 / 극단 탐욕(>80) 경계라는 역발상, 또는 국면 바이어스 필터 용도.
- **출처** (r3_ws14):
  - QuantifiedStrategies, "Fear And Greed Trading Strategy: Can It Be Quantified?" (2025-05-08) — https://www.quantifiedstrategies.com — CNN 지수 데이터 미보유로 타 연구 인용 형태의 백테스트(검증 강도 제한적 — 정직 표기)
  - CoinMarketCap 지수 페이지 — "The index has been backtested thoroughly" — **제공자 자체 주장, 제3자 검증 아님**
  - Binance 지수 페이지 — "high value warns of a potential market correction" (관행 확인)
  - levelup.gitconnected (2025-03-25) — SPY 대상 F&G 백테스트 글(주식 대상, 참고)
- **배제목록 비겹침 판정**:
  - 배제12(크라우드 포지셔닝 비율 — metrics toptrader 비율, ADR-0080 폐기): 같은 "군중 상태 → 반대" 가설 패밀리. 변수는 다르나(포지션 비율 vs 복합 센티먼트) **신호 상관 매트릭스 검사를 채택 조건으로 부여**.
  - 배제9(상위TF 모멘텀)·14(변동성 예측): F&G 성분 중 50%가 모멘텀+변동성 → **성분 수준 인접**. 지수 전체를 쓰는 것과 성분을 따로 쓰는 것은 다르지만, 엣지가 성분에서 온다면 배제 계열 재탕일 수 있음 — 진단 시 성분 기여 분해 권고.
  - C8(ETF 플로우)과는 "외부 일간 오버레이"라는 사용 패턴만 같고 변수 상이. C9(옵션 스크우)와는 변동성 성분에서 약한 인접.
  - **판정: 조건부 통과 (12·9·14 성분 인접성 명시)**
- **데이터 가용성**: **alternative.me API 실측 200 OK** (`https://api.alternative.me/fng/?limit=N&format=json`, 일별 value/classification 반환, 무료·키 불필요). CMC/Binance 값과 동일 소스 계열로 알려짐(미검증). 역사 시작점(통상 2019-02)은 이번 세션에서 직접 확인 안 함 — 표기.
- **비용 관점**: 일간 1회 갱신 → 필터/게이트 용도 시 추가 왕복 없음. 단독 15m 신호로는 빈도가 맞지 않음(일 1점) → 보조 변수 성격.
- **리스크/정직 노트**: "백테스트됐다"는 주장의 대부분이 제공자/블로그 수준. 학술 검증은 이번 스캔에서 미확보.

### F3. BTC-ETH 페어 트레이딩 (공적분 기반 스프레드 평균회귀)
- **원리**: BTC와 ETH 로그가격의 공적분 관계(장기 균형)를 이용, 스프레드가 평균에서 이탈하면 고가 자산 매도/저가 자산 매수 후 수렴 수확. 시장중립 스프레드 트레이드(USDT-M 퍼페 양레그로 실행 가능).
- **출처** (r3_ws21, r3_ws12):
  - Kristoufek (2023), "Exploring sources of statistical arbitrage opportunities in the Bitcoin market", ScienceDirect — https://www.sciencedirect.com/science/article/abs/pii — 5개 거래소(Binance 등) 대상 통계적 차익 기회의 발생 조건 연구 (피어리뷰, cited 32)
  - Dann (2024), "Profiting Off the High Correlation of Cryptocurrency Pairs", RePEc/Springer chapter — BTC/ETH/LTC/BCH 대상 스탯아비트라지 제안
  - IJSRA (2026-02-12) 학위논문 — "statistical arbitrage methods in the cryptocurrency markets using cointegration analysis on Bitcoin, ethereum…" https://ijsra.net/fulltext_pdf/IJSRA-2026-0283
  - dYdX Learning — 스탯아비트라지 개요 (실무)
  - 보조(r3_ws12): Quantpedia "Trend-following and Mean-reversion in Bitcoin"(2022-03-15) + SSRN 후속(2015-2024 재검증) — 페어/평균회귀 계열 맥락
- **배제목록 비겹침 판정**:
  - 배제 16번 어디에도 없음. C1(스팟-퍼페 베이시스)·C7(교차거래소 동일자산 스프레드)과 메커니즘 상이(크로스 자산, 동일 베뉴).
  - **저장소 `cross_sectional` 연구 전용 모듈 + Task 4 N4(XS 단기 되돌림, 차순위)와 패밀리 인접** — XS는 자산 간 상대 강도 랭킹, 페어는 스프레드 시계열 회귀로 상이하나 상관 검사 조건 부여.
  - 배제15(표준 지표 반전)와 다름: 스프레드 통계 회귀는 지표 기반이 아님.
  - **판정: 조건부 통과 (cross_sectional/N4 인접성 명시)**
- **데이터 가용성**: **Binance kline BTC+ETH만으로 완결 — 신규 데이터 다운로드 0** (저장소가 이미 보유). 스프레드 계산·롤링 공적분 추정 모두 15m kline으로 가능.
- **비용 관점**: **레그 2개 = 왕복 24~40bp** — 본 플랫폼 비용 전제에서 가장 불리한 구조. 수렴 보유가 수 시간 이상이고 스프레드 이동폭이 비용을 초과하는 극단 이탈만 잡는 설계 + N2 지정가(maker) 진입이 사실상 필수. 그래도 통과 확률은 4건 중 최하로 정직하게 판단.
- **리스크/정직 노트**: 공적분 관계의 시간가변성(구조적 붕괴)이 문헌의 공통 비판점. "유능하다"는 주장 대부분이 소수 자산·특정 기간 백테스트.

### F4. 구글 트렌드 관심도 (attention spike → 방향)
- **원리**: 검색 관심도 급등(소매 참여 유입)이 단기 방향 압력(상승 후 되돌림)과 관련된다는 attention 문헌의 암호화폐 적용.
- **출처** (r3_ws15):
  - Aslanidis et al. (2022), "The link between cryptocurrencies and Google Trends", ScienceDirect — https://www.sciencedirect.com/science/article/pii — "bidirectional flow of information between Google Trends attention and cryptocurrency returns up to six days" (피어리뷰, cited ~144)
  - Zelieska (2024), SSRN/Quantpedia — "Can Google Trends Sentiment Be Useful as a Predictor for Bitcoin?" — 혼합 결과(예측기로의 유용성 제한적)
  - ResearchGate (2026-03-03) — 트렌드 기반 트레이딩 시스템 긍정 주장 (검증 강도 낮음 — 표기)
  - Coinglass — Bitcoin Google Trends 차트 제공
- **배제목록 비겹침 판정**: 16번·C1~C9 어디에도 없음. F2와 같은 심리 패밀리이나 변수(검색량)가 상이. **판정: 조건부 통과 (F2 패밀리 인접, 우선순위 최하)**
- **데이터 가용성**: **불확실 — 최대 약점**. pytrends는 알려진 rate-limit/차단 이슈, Coinglass 차트의 자동 수집 가능성 미확인. 자동 백필이 안 되면 후보로서 실격 가능성 높음.
- **비용 관점**: 일간~주간 변수 → 필터 용도, 추가 왕복 ~0.
- **리스크/정직 노트**: 트렌드 수치는 상대치이며 과거 재계산(개정)되는 점이 백테스트 재현성을 해침.

---

## 3. "유명하지만 이미 배제/중복" — 전수 확인 증거 표

> 사용자 지시대로 **유명한데 배제목록 1~16 또는 기존 9건(C1~C9)·저장소 기존 항목과 겹치는 것**을 별도로 전수 기록. 각 행의 출처는 이번 스캔에서 실제로 회수된 검색 결과(r3_ws*.json) 기준.

| # | 전략 (유명도 근거) | 유명한 이유/출처 | 겹침 판정 (해당 항목) |
|---|---|---|---|
| X1 | Opening Range Breakout (ORB) | buildalpha 가이드, quantifiedstrategies 백테스트 글, LiteFinance, LuxAlgo (r3_ws19) | 배제 8(돌파 계열 반복 전멸) + 배제 1(개장 앵커 시간 효과 인접) — 크립토는 24/7이라 "개장" 정의 자체가 시간 앵커 |
| X2 | TTM Squeeze (BB⊂Keltner 스퀴즈 돌파) | thinkorswim 공식 지표 문서, TrendSpider, StockCharts, Schwab (r3_ws20) | 배제 15(표준 지표: BB/Keltner) + 배제 8(돌파) |
| X3 | VWAP 회귀/필터 | ThinkMarkets, altfins, TradingView, "15m VWAP 전략" 문서, Binance Square (r3_ws22) | 배제 15 + 배제 1(일 단위 앵커) 인접 — **2차 리서치에서 이미 검토 후 "제안하지 않음" 결정 기록 존재** |
| X4 | Ichimoku Cloud | Investopedia, Swissquote, Changelly 크립토 가이드, Wikipedia (r3_ws25) | 배제 15(표준 지표 시스템) — 저장소 G1/G2 지표 투표 패널과 동일 패밀리 |
| X5 | Heikin Ashi 추세추종 | quantifiedstrategies 백테스트 글, pyquantlab, BJF Trading Group (r3_ws8) | 배제 9(추세/모멘텀) + 15(캔들 스무딩 변형). 백테스트 함정(HA 신호를 실가격 체결)도 출처 스스로 경고 |
| X6 | Donchian/Turtle | trendfollowing.com, Altrady, crosstrade, lizardindicators (r3_ws10) | 배제 8·9 — **Task 2에서 이미 리서치됨** |
| X7 | Larry Williams 변동성 돌파 | TradingView 전략, tradersmastermind, Binance Square (r3_ws11) | 배제 8·15. 참고: 제3자 백테스트에서 **음의 결과**(coinquant: BB 변동성 돌파 BTC 2022-26 총수익 −14.2%, 승률 25.6% 주장) |
| X8 | 평균회귀 일반형(BB/RSI/z-score) | Quantpedia Bitcoin trend/mean-reversion, Changelly, bit.com (r3_ws12) | 배제 15 직격 |
| X9 | 피벗 포인트(플로어/카마리야) | BingX, TradingSim, paperswithbacktest, Binance Square (r3_ws26) | 배제 1(세션 앵커) + 15(레벨 되돌림) |
| X10 | Wyckoff(축적/분배, spring/upthrust) | Binance Academy, CoinMarketCap, Phemex, TradeSanta (r3_ws24) | 배제 6·7(SMC 가격액션 패밀리, ADR-0088 48셀 폐쇄) — 규칙이 주관적이라 정량화 정의 자체가 불유일 |
| X11 | 캔들스틱 패턴(상승장애형 등) | MDPI Uzun 2024, Moser 2026(SD, 인트라데이 캔들패턴 수익성), ResearchGate (r3_ws16) | 배제 15·2 인접(패턴 = 단기 반전/연속 신호). Moser 2026이 일부 유의성 주장하나 미검증 + 패밀리 인접으로 제안 안 함 |
| X12 | 듀얼 모멘텀/BTC-ETH 로테이션 | cryptonews(MA 모멘텀), rotation 가이드 (r3_ws9 — 결과 약함, 정직 기록) | 배제 9 + 저장소 `cross_sectional` 모듈 중복 |
| X13 | 온체인 MVRV/SOPR 극단 | lookintobitcoin, CryptoQuant, Glassnode Research, bitbo (r3_ws18) | 데이터 유료/지연 + 지평 일간 이상 + 배제 12·14(군중/변동성) 인접 — **2차에서 온체인 계열 이미 제외** |
| X14 | 그리드 트레이딩 | 범용 실무 | 저장소에 `grid` 연구 전용 모듈 존재(Task 7 실측) + 방향 알파가 아닌 구간 수확 |
| X15 | 1m/5m 스캘핑 | LiteFinance/CMC Markets 목록글 (r3_ws2) | 배제 16 직격 |
| X16 | 인트라데이 RSI 시스템 | ResearchGate 2023 — "RSI 시스템이 최고" 주장 (r3_ws2) | 배제 15 직격. 출처 주장이 긍정적이어도 배제 판정 유지(저장소 측정 전멸 우선) |
| X17 | 알트시즌/도미넌스 로테이션 | 커뮤니티 지표(blockchaincenter식) | 배제 9 + cross_sectional 중복 |
| X18 | DCA/홀딩/반감기 사이클 | 범용 | 지평 불일치(개월~년) — 15m/12h 전제와 무관 |
| X19 | 차익거래(펀딩/베이시스/삼각/DEX-CEX) | dYdX, CoinAPI 용어집 (r3_ws21 보조) | 배제 10·11 + 실행 계층(15m 방향 신호 아님) |
| X20 | 유동성 존 스나이핑(스톱 헌팅존) | OSL 아카데미 목록글 (r3_ws2) | 배제 6·7(sweep/FVG 패밀리) |

**"유명하나 기존 9건(C1~C9)과 중복" 확인**: 이번 스캔에서 접한 유명 전략 중 C계열과 동일한 것 — 청산 캐스케이드(C6, X계열 아닌 기존 확인용), 펀딩 극단(X19), 옵션 변동성 지표(C9), ETF 플로우(C8) — 전부 2차 산출물에 존재하므로 재발굴하지 않았음(지시 준수).

---

## 4. 검토 후 미제안 (정직 기록)

- **스테이블코인 디페그 → 수익 예측**: r3_ws6(노이즈 검색)에서 우연히 회수한 Boido (2026, MDPI) — "evidence that stablecoin depegs predict cryptocurrency returns is weak and does not cohere into a systematic pattern". **출처 스스로 약한 증거** → 제안하지 않음. (데이터는 무료로 가능: Binance USDC/USDT kline)
- **CPI/FOMC 매크로 이벤트 윈도우 모멘텀**: 2차 리서치 §"검토 후 유보"에 이미 기록됨 → 재발굴 안 함.
- **고빈도 기술지표 ML(예: Visharad 2025, r3_ws6 부수 회수)**: 배제 15의 ML 래퍼 꼴 → 제안 안 함.
- **거래소 순유입/고래 지갑 추적**: 2차 제외 사유(유료 데이터) 불변 + C2(오더플로우) 패밀리 인접 → 제외.

---

## 5. 검색 로그 (전수, 정직)

| 파일 | 쿼리 | 결과 판정 | 비고 |
|---|---|---|---|
| r3_ws1 | best known crypto trading strategies list | **노이즈** | 사전·CISA 폴백 — 카탈로그 출처는 ws2/ws11/ws12로 대체 확보 |
| r3_ws2 | crypto intraday strategies famous | 성공 | LiteFinance/ThinkMarkets 12법/CMC/OSL 목록 + ResearchGate RSI 논문 |
| r3_ws3 | opening range breakout crypto backtest | **노이즈** | Crypto.com·CDC 폴백 → ws19로 회복 |
| r3_ws4 | TTM squeeze breakout crypto | **노이즈** | 고래(Whale) 폴백 → ws20으로 회복 |
| r3_ws5 | pairs trading cointegration bitcoin ethereum | **노이즈** | "Connectedness" 사회학 폴백 → ws21로 회복 |
| r3_ws6 | VWAP strategy crypto 15 minute | 부분 | 스테이블코인 폴백, 부수 출처 2건(Visharad/Boido)만 회수 → ws22로 회복 |
| r3_ws7 | Ichimoku cloud crypto backtest | **노이즈** | Tether 폴백 → ws23도 노이즈 → ws25로 회복 |
| r3_ws8 | Heikin Ashi strategy crypto backtest | 성공 | quantifiedstrategies·pyquantlab·BJF |
| r3_ws9 | dual momentum crypto rotation | 약함 | 4건, 직접 관련 낮음 — X12 판정에 부족하지만 충분한 수준 |
| r3_ws10 | donchian turtle crypto futures | 성공 | trendfollowing.com 등 5건 |
| r3_ws11 | volatility breakout strategy crypto | 성공 | coinquant 음의 백테스트 포함 |
| r3_ws12 | mean reversion crypto strategy research | 성공 | Quantpedia·SSRN·changelly |
| r3_ws13 | CME bitcoin futures gap trading | 성공 | OKX/Bitget/BloFin/Cointelegraph — F1 |
| r3_ws14 | fear and greed index crypto backtest | 성공 | quantifiedstrategies/CMC — F2 |
| r3_ws15 | google trends bitcoin returns strategy | 성공 | Aslanidis 2022/Quantpedia — F4 |
| r3_ws16 | candlestick patterns crypto profitability study | 성공 | MDPI Uzun 2024/Moser 2026 — X11 |
| r3_ws17 | wyckoff method crypto backtest | **노이즈** | 범용 백테스트 툴 폴백 → ws24로 회복 |
| r3_ws18 | MVRV SOPR bitcoin strategy | 성공 | CryptoQuant/lookintobitcoin/Glassnode — X13 |
| r3_ws19 | opening range breakout bitcoin | 성공 | buildalpha/quantifiedstrategies — X1 |
| r3_ws20 | TTM squeeze indicator strategy | 성공 | thinkorswim/TrendSpider — X2 |
| r3_ws21 | statistical arbitrage bitcoin ethereum | 성공 | Kristoufek 2023/Dann 2024/IJSRA — F3 |
| r3_ws22 | VWAP trading strategy bitcoin | 성공 | ThinkMarkets/altfins — X3 |
| r3_ws23 | Ichimoku bitcoin trading strategy | **노이즈** | 사전 폴백 → ws25로 회복 |
| r3_ws24 | Wyckoff method bitcoin trading | 성공 | Binance Academy/CMC — X10 |
| r3_ws25 | Ichimoku kinko hyo cryptocurrency | 성공 | Investopedia/Changelly — X4 |
| r3_ws26 | pivot points bitcoin trading strategy | 성공 | BingX/TradingSim — X9 |

- 합계 26회: 성공 19 / 부분 0(부분은 노이즈로 계산 안 함 — 위 표 기준 성공 19, 약함 1 포함 시 유의 19~20) / 노이즈 7 (ws1, 3, 4, 5, 7, 17, 23). 재검색 회복 6회.
- 추가 실측(curl): alternative.me F&G API 200, Yahoo `BTC=F` 200.

---

## 6. 배제목록 원문 (대조용, 2차 문서에서 인용)

1) 시간대/세션(tod) 2) 자기상관/VR 3) OI 방향/급변 4) BTC→ETH lead_lag 5) 비대칭(왜도/첨도) 6) SMC(OB/FVG/BOS) 7) 15m 내부 되돌림(sweep) 8) 거래량 돌파 변형 9) 모멘텀 그리드/상위TF 모멘텀 10) 캐리(펀딩/베이시스 수집) 11) 펀딩 극단 역발상 12) 크라우드 포지셔닝(metrics) 13) 김치프리미엄 14) 변동성 예측(har80) 15) 표준 지표 반전(RSI/BB/MACD 등) 16) 1m/5m 스캘핑

기존 9건(2차, 재발굴 금지): C1 베이시스 모멘텀 / C2 오더플로우 불균형 / C3 코인베이스 프리미엄 / C4 프리미엄 인덱스 인트라-인터벌 / C5 분기선물 커브 / C6 청산 캐스케이드 / C7 교차거래소 퍼페 스프레드 / C8 ETF 플로우 / C9 옵션 스크우·DVOL. 저장소 기존: volume_profile(측정 완료), regime 판별식, har_vol, grid/cross_sectional(연구 전용 모듈).

---

## 7. 권고 다음 단계 (저장소 규칙 준수 전제)

1. **F2(공포·탐욕)가 최저 비용 진입점**: alternative.me 무료 API 확보됨 → 일간 값과 15m 수익의 조건부 진단(극단 분위수)만으로 사전등록 가능. 단 ADR-0080(N1) 신호와의 상관 매트릭스를 1차 진단에 포함(패밀리 재탕 방지 조건).
2. **F1(CME 갭)**: Yahoo `BTC=F` 일봉으로 갭 분포·메움 비율·소요시간 분포를 먼저 기술 통계로 진단(비용 게이트 이전). 메움 비율이 낮거나 소요 >12h 비중이 크면 즉시 폐기.
3. **F3(페어)**: 비용벽 2배 때문에 극단 분위수(예: 스프레드 z>3)만의 조건부 진단으로 한정. cross_sectional 모듈과의 신호 상관 확인 후.
4. **F4(트렌드)**: 데이터 백필 가용성이 확보될 때까지 보류.
5. 공통: 본 문서는 어디까지나 "후보 발굴"이며, 실제 채택은 저장소 사전등록 → Holm·비용 게이트 규격대로만.

---

## 8. 출처 파일 인덱스

- 검색 원문 JSON: `NEW2_run/research/r3_ws1.json ~ r3_ws26.json` (26개)
- 2차 리서치(중복 대조 기준): `NEW2_run/research/web_research_round2.md`
- 이번 세션 라이브 실측: alternative.me F&G API, Yahoo Finance BTC=F (본 문서 §0)
