# 웹 리서치 3차 (Task 9-c) — 크로스 마켓·외부 변수·구조적 원리

- 작성: Task 9-c 에이전트 (general-purpose)
- 기준일: 2026-10-12 (UTC 세션)
- 전제: 15m봉 BTC/ETH USDT-M 선물, 최대 보유 12시간, 왕복 거래비용 12~20bp
- 상위 문서: `web_research_round2.md`(8-b)의 기존 9건 C1~C9와 배제목록 16번을 승계·대조
- 저장소 실측: `/tmp/NEW2_probe`(main 얕은 클론, HEAD 확인용) — 워크플로우 25개 yml + `strategies/cross_sectional.py` 원문 + ADR 89건 제목 전수 대조

---

## 0. 검색 실행 요약 (정직 기록)

- **web_search (z-ai CLI) 26회 실행** → `r3_ws1.json ~ r3_ws26.json` (429 레이트리밋 4회 발생→재시도 성공, 미기록 없음)
  - 유의한 결과 10회: ws2(시변상관·risk-on/off), ws6(Boido 2026 스테이블코인-크립토 관계), ws7(Griffin-Shams 티더 연구 경로), ws11(상장 이벤트스터디 3건), ws18(F&G 학술연구), ws19(상폐 공지 가격효과), ws20(거래소 다운타임·플래시크래시 논문), ws22(페어트레이딩 문헌 4건), ws25(BTC-Nasdaq 상관 관행), ws26(베이시스 차익 언와인드 디레버리징)
  - 부분 유의 4회: ws8(유료 소스 확인), ws9(지표 정의), ws15(노이즈이지만 Amihud 유동성 문헌 부수 확인), ws17(드리프트 버스트 논문 — 본 축과 무관)
  - 전면 노이즈 12회: ws1, ws3, ws4, ws5, ws10, ws12, ws13, ws14, ws16, ws21, ws23, ws24 — 긴 쿼리에서 일반 단어로 폴백. 유의 출처로 꾸미지 않고 그대로 기록
- **arxiv API 4회 시도 → 전부 실패** (HTTP 301→https 전환 후 타임아웃, "Rate exceeded" 응답). 라운드 2와 달리 이번 세션에서 arxiv 원문 확보 불가 — 논문 ID 확보 못한 항목은 스니펫 기반으로만 기재
- **데이터 가용성 실측 (curl HTTP 실측, 오늘)**: §8 표. 핵심 신규 실측 6건:
  - alternative.me F&G API **무료 전체 히스토리 3,172일 실측 확보** (2018-02-01~)
  - DefiLlama 스테이블코인 API **USDT 시가총액 일간 3,240레코드 실측 확보** (2017-11-29~)
  - Yahoo Finance chart API **ES=F/NQ=F 1h × 2.4년, GC=F 1h, DXY 일봉 5y 200 OK** / **15m은 60일 한정**
  - 바이낸스 공지 bapi **catalogId=48 "New Cryptocurrency Listing" 2,279건 + releaseDate 필드 200 OK(페이지네이션 실측)**
  - CoinMetrics 커뮤니티 플랜 **거래소 플로우 지표 401 unauthorized 실측** (유료 확인)
  - DefiLlama CEX 준비금 **과거 시계열 엔드포인트 404 실측** (현시점 스냅샷+24h 유입만 제공)

---

## 1. 축별 조사 결과

### 축 1. 전통 시장 연동 (Nasdaq/S&P 야간선물 동조, 금/달러, risk-on/risk-off 전이) — 판정: **유망(조건부, 필터 성격)**

- **원리**: 크립토-주식 상관은 장기 0.2~0.3 수준이지만 위기/리스크오프 국면에서 급등하는 시변 구조가 반복 보고됨. 야간(아시아 시간) ES/NQ 선물 움직임과 달러/금 움직임을 "외부 시장 상태 변수"로 사용해 15m 퍼페 방향에 조건부 바이어스를 부여하는 원리. 핵심은 **시계(시간대)가 아니라 외부 자산의 상태가 조건**이라는 점.
- **출처**:
  - 시변상관 학술: ResearchGate "Time-varying correlation between bitcoin returns and macro-financial variables"(스니펫), SSRN Marti 2025 "Bitcoin and global stock portfolio: Dynamics across short horizons"(ARDL-GARCH) — https://papers.ssrn.com
  - 실무 관행(상관 진동): beincrypto 2022-04-20 "30-day correlation with tech stocks highest since Jul 2020", Yahoo Finance 2025-04-26 "long-term correlation ~0.2-0.3", seekingalpha 2026-02-06, OKX research 2025-03-03 "BTC jumped 13% while tech stocks unmoved"(디커플링 사례)
- **검색 노이즈 정직 기록**: "야간 선물 → 아시아 시간 크립토 선행"에 특화된 문헌은 미확보(ws1/ws5 전면 노이즈). 문헌은 일반 spillover/상관 수준 — 방향성 실측은 직접 진단 필요.
- **비겹침 판정**:
  - 배제 1(tod_seasonal, ADR-0070)과 구분: 배제 1은 무조건부 시간대 더미. 본건은 **외부 자산 수익률을 조건으로 하는 조건부 동조** — 변수 클래스가 다름. 단, "미국 증시 개장 직후" 등 고정 시각 요소를 무조건부로 재검정하면 배제 1 위반이므로 조건부 정의 강제.
  - C1~C9·배제 2~16·워크플로우 19개(ci~basis_carry_study) 어디에도 없음 — **비겹침 통과**
- **데이터 가용성(실측)**: Yahoo `ES=F`/`NQ=F` 1h 2024-05-19~현재 17,389레코드(200), `GC=F` 1h, `DX-Y.NYB` 일봉 5y(200) — 전부 무료·키 불필요. 한계: **15m 캔들은 60일 한정(실측: 2026-08-02~)** → 15m 해상도 교차상관 진단은 단기표본, 1h/일간 해상도면 수년. Binance 15m 아카이브는 로컬 보유(130파일). 장기 15m ES 데이터 무료 경로 미확보(Dukascopy 등 불확실 — 미실측).
- **비용 관점**: 필터/게이트 용도는 추가 왕복 거래 없음. 단독 진입 신호로 쓰면 신호 빈도↑ → 비용벽 재현 위험.
- **결론**: 1h 동조·risk-off 국면 조건부 게이트로 **최우선 진단 대상**. 15m 단독 신호로는 문헌 근거 약함(정직).

### 축 2. Stablecoin 플로우 (USDT/USDC 발행·소각) — 판정: **조건부 통과(주파수 제약 명시)**

- **원리**: 테더 발행(민팅)은 대규모 달러 유입 신호, 시총 증감이 수요 압력을 선행한다는 가설. Griffin & Shams "Is Bitcoin Really Un-tethered?"(경유: Wikipedia 스니펫 — "bitcoin prices increased after Tether minted new USD₮ during market downturns")가 원전. 최신 문헌: Boido 2026(MDPI) "The Relationship Between Stablecoin and Cryptocurrency" — **"stablecoin depegs predict cryptocurrency returns는 증거 약함"** (https://www.mdpi.com, https://www.preprints.org) — 기대치를 낮추는 방향의 증거도 함께 기재.
- **비겹침 판정**: 배제 1~16·C1~C9 어디에도 없음(C8 ETF 플로우는 다른 플로우 변수). 워크플로우 19개와도 무관 — **비겹침 통과**
- **데이터 가용성(실측)**:
  - DefiLlama `stablecoins.llama.fi/stablecoincharts/all?stablecoin=1` → USDT 유통액 **일간 2017-11-29~ 3,240레코드 200 OK, 무료·키 불필요**. USDC 등 다른 ID도 동일 포맷.
  - 한계: **일간 주파수**. 인트라데이 민팅 이벤트의 과거 아카이브는 무료 미확보(Whale Alert API는 유료/제한). 12h 보유 전략에 일간 발행증감은 "느린 바이어스 필터"로만 기능.
- **비용 관점**: 필터 용도라 비용 추가 없음. 다만 Boido 2026의 약한 증거 + 일간 주파수 → 기대효과 제한적.
- **결론**: 무료 데이터는 존재(실측 완료). **차순위 필터 후보**. 민팅 이벤트 단위(인트라데이) 테스트는 데이터가 없어 불가 — 정직 표기.

### 축 3. 온체인 실시간 지표 (거래소 순유입/출발) — 판정: **불가(무료 과거 데이터 없음 — 실측으로 재확정)**

- **지시 축**: "무료로 얻을 수 있는 과거 데이터가 있는지 엄격히 재확인" → 결과: **없음**.
- **실측 증거**:
  - CoinMetrics 커뮤니티 API `FlowInBFX` 타임시리즈 요청 → **HTTP 401 unauthorized**(거래소 플로우는 PRO 전용 실측)
  - DefiLlama CEX 준비금: `/cexs`(88개 거래소, inflows_24h 포함)는 200이나 **과거 시계열 엔드포인트(`/cex/2269/tokens`, `/cex/binance` 등 3종) 전부 404** → 순유입 근사 불가
  - CryptoQuant/Glassnode Studio: 유료(검색 결과 ws8에서 페이지만 확인, 무료 히스토리 API 없음)
  - Upbit 공지 API `/v1/notices` → 404 (축 6 관련 부수 실측)
- **검색 노이즈**: ws8, ws24 전면/부분 노이즈 — 무료 온체인 플로우 데이터셋 발견 못함. 라운드 2의 "유료/노이즈로 제외" 결론이 **HTTP 실측으로 강화됨**.
- **비겹침 판정**: 겹침 문제 이전에 데이터 없음 → **후보 제외 확정**. (배제 3 OI·배제 12 포지셔닝과는 별개 변수였음에도 불가 판정)

### 축 4. 공적분/Pair 트레이딩 (BTC-ETH 스프레드 평균회귀) — 판정: **조건부 통과(비용벽·S3 인접성 플래그)**

- **원리**: BTC-ETH 가격 스프레드(또는 log 스프레드·β헤지 잔차)의 수준이 과도 괴리 시 평균으로 회귀한다는 공적분 가설. 선행-후행(배제 4, ADR-0087 direction_ab_diag로 측정·폐쇄)과 **다른 가설**: 배제 4는 "r_ETH(t+k) 예측에 r_BTC(t) 선행", 본건은 "스프레드 수준의 정상성+에러보정". 공적분 검정에서 동적 페어 문헌 존재.
- **출처**:
  - Tadi 2021, "Evaluation of Dynamic Cointegration-Based Pairs Trading" arXiv(cited ~24) — https://arxiv.org (스니펫 확인, ID 미확보 — arxiv API 장애로 정직 기록)
  - Dann et al. 2024, "Profiting Off the High Correlation of Cryptocurrency Pairs"(BTC/ETH) — https://ideas.repec.org
  - Setzer 2022(포르투갈 UCP 학위논문), Jung 2025 SSRN "Statistical Arbitrage within Crypto Markets using PCA"
- **저장소 대조(실측)**: `strategies/cross_sectional.py` 원문 확인 — **일간 횡단 모멘텀/저거래량 랭킹 포트폴리오(Liu-Tsyvinski-Wu 2022, ADR-0017/0018)로 페어 트레이딩·공적분 전무**. `xsec_validation` 워크플로우는 이 랭킹 계열 검증용. → 페어 스프레드 회귀는 저장소 어디에도 없는 **미측정 원리**. 워크플로우 19개에도 없음.
- **비겹침 판정**:
  - 배제 4(lead-lag)와 메커니즘 상이 → 통과. 배제 7(15m 단일자산 되돌림, S3=ADR-0079 폐쇄)과의 인접성: **1레그 방향 변형(ETH 괴리 → ETH 방향)**은 사실상 "BTC 조건부 ETH 평균회귀"라 S3-인접 리스크 존재 — 명시 필수. 2레그 델타중립 변형은 이 인접성 없음.
  - C7(교차거래소 동일자산 스프레드)과도 상이(동일자산-교차베뉴 vs 교차자산-동일베뉴) — 패밀리 인접성만 명시.
- **데이터 가용성**: **이미 충족** — 로컬 `NEW2_run/data/klines`에 BTC/ETH 15m 2021-04~2026-04 보유(130파일). 추가 다운로드 불필요.
- **비용 관점(핵심 리스크)**: 델타중립 2레그는 왕복 **24~40bp** — 15m 주기 수렴 폭이 이를 넘을 가능성 낮음(라운드 2 C7과 동일 비용 논리). 현실적 형태는 ①저빈도(12h 스탯아브 대기) ②1레그 방향 변형(비용 12~20bp이나 S3-인접) 셋 중 하나. 기각 아닌 "비용 조건부".
- **결론**: 원리·문헌·데이터 충족, **비용 구조가 채택 여부를 결정**. 12h 스탯아브로 극단 괴리만 잡는 1레그 방향 변형을 최소 진단 권고.

### 축 5. Fear & Greed Index / 소셜 지표 — 판정: **조건부 통과(우선순위 낮음, 배제 12와 메커니즘 상이 판정)**

- **원리**: F&G는 변동성 25%·모멘텀 25%·소셜 15%·지배력 15%·트렌드 10%의 복합 센티먼트 지수(cryptoSlate 2024-08-12 정의). 극단 공포(≤25) 매수/극단 탐욕(≥75) 매도 역발상 관행 + 학술 검증.
- **출처**: Szeberényi et al. 2026, "Fear, greed and bitcoin return distributions", *Digital Finance*(Springer) — https://link.springer.com (스니펫: "단순 트레이딩 시그널이라기보다 [분포 특성] 식별에 유용할 수 있다" — 강한 엣지 주장 아님). whaleportal 분석, TradingView 인덱스 구성 설명.
- **배제 12(크라우드 포지셔닝 비율, N1=ADR-0080 폐기)와의 구분**: N1은 **선물 계좌 롱/숏 비율**(metrics 아카이브, 포지션 데이터). F&G는 **가격·변동성·소셜의 복합 센티먼트 합성**(포지션 데이터 아님) → 매커니즘 상이로 비겹침 판정. **단 정직한 반론**: 인덱스 가중치의 50%(변동성+모멘텀)는 이미 보유한 가격 데이터에서 계산 가능 → 증분 정보는 나머지 50%(소셜·지배력·트렌드)뿐. ADR-0080 폐기와 마찬가지로 "정보 중복" 위험이 실질 리스크.
- **데이터 가용성(실측)**: `api.alternative.me/fng/?limit=0` → **3,172일 일간 히스토리 200 OK, 무료·키 불필요(2018-02-01~)**. 로컬 저장 완료(`r3_fng_hist.json`). 한계: **일간만 존재, 인트라데이 히스토리 무료 미확보**. 소셜 지표(LunarCrush/Santiment)는 키 필요+무료 히스토리 제한 — 미실측, 불확실 표기.
- **비용 관점**: 일간 필터라 추가 거래 없음. 그러나 라운드 2 C3(코인베이스 프리미엄)와 동일한 "일간 바이어스 필터" 슬롯 경쟁 — C3가 더 원시적 수요 변수.
- **결론**: 데이터는 즉시 사용 가능하나 **증분 정보 의문 + 일간 주파수** → C3 대체 불가, 부가 필터 차순위.

### 축 6. 거래소 사건·구조 (상장/상폐 공지, 다운타임, 프리미엄 붕괴) — 판정: **조건부 통과(리스크 필터·이벤트 백테스트 적합, 방향 엣지는 약함)**

- **원리**: ①상장 공지 = +5.7% 일평균 이상수익(Ante 2019, SSRN/blockchainresearchlab, cited ~30), Joo 2020 공지 효과, Mehdian 2025 ETF 승인 반응(econstor). ②상폐 공지 = 즉각 두자릿수 하락 관행(beincrypto 2026-04, Binance square 2026-04 "역사적 상폐 사례 24h 내 평균 -50%" 스니펫). ③거래소 다운타임 = 플래시크래시 증폭(Baumgartner et al. 2022, AUT ACFR — 2021-05-19 바이낸스 다운타임 분석; Reuters 2020-03, thehill 2024-02 Coinbase 장애). ④프리미엄 붕괴는 C3/C7이 이미 커버(인접).
- **비겹침 판정**: 배제 1~16·C1~C9에 없음(이벤트 조건부). 워크플로우 중 `event_backtest`가 정확히 이 형태의 검증 인프라 — **비겹침 통과, 저장소 도구와 적합**.
- **데이터 가용성(실측)**:
  - 바이낸스 공지 bapi `catalogId=48`(New Cryptocurrency Listing) **2,279건, `releaseDate`(ms) 필드 포함, 페이지네이션 실측 200 OK** → 상장 이벤트 타임스탬프 무료 전수 확보 가능
  - **한계(정직)**: 상폐 공지는 별도 카탈로그 미발견(title 검색 파라미터 400 실측, catalogId 49~56 프로브 결과 49=Latest News/51=API Updates뿐) → 스크래핑 필요성 남음. 다운타임 이력은 무료 구조화 아카이브 없음(뉴스 수작업 큐레이션만 가능). 업비트 공지 API 404 실측.
- **비용 관점 + 적합성**: **직접 문제** — 상장/상폐 이벤트의 주 효과는 해당 알트코인에 발생. BTC/ETH 15m 시스템에는 ①전반 변동성 증가 윈도우(리스크 필터) ②BTC/ETH가 직접 상장되는 대형 이벤트(ETF 승인 등, 표본 수 년간 소수)만 해당. 방향 엣지로는 표본 부족이 구조적 한계.
- **결론**: 데이터 확보는 가능(상장 2,279건 실측), **본 매매 대상과의 거리 때문에 방향 후보로는 약함 — 리스크/변동성 게이트 재료로 조건부**.

### 축 7. 기타 크로스 자산 (매크로 이벤트 윈도우 재검토 + 차익거래 마진 붕괴)

#### 7-a. 매크로 이벤트 윈도우 (CPI/FOMC/NFP 발표 ±N시간) — 판정: **조건부 통과(라운드 2 "유보" 재검토 후 통과)**
- **원리**: 예정 발표 직전/직후 변동성·방향 캐스케이드. 라운드 2에서 "배제 1 인접 + 표본수"로 유보했으나, 본 재검토에서 구조 분리 가능: 배제 1(tod)은 **무조건부 시각 효과**, 이벤트 윈도우는 **발표 날짜 조건부** — 날짜가 매년 바뀌므로 시계 더미로 흡수되지 않음. 표본수도 재계산: CPI 12+ FOMC 8+ NFP 12 ≈ 연 32회 × 5년 ≈ **160 이벤트** × 이벤트 내 다수 15m 바 = 측정 가능한 규모(셀당 유효 표본은 낮아 Holm 보정 엄격 필요 — 유보 사유의 절반만 유효).
- **출처**: 검색 ws13/16/21/23 전부 노이즈로 전용 문헌 미확보(정직). 단 Axis 1 문헌(시변상관·risk-off)과 FT 2024-08-09 "yen carry unwind+기술주 급락 → 크립토 동반 급락"(ws26)이 간접 지지. 관행적으로 크립토-CPI 반응은 업계 상식 수준이나 학술 출처 확보 실패.
- **데이터 가용성(실측)**: Binance 15m 아카이브 로컬 보유. 이벤트 날짜: federalreserve.gov FOMC 캘린더 **200 OK 실측**; BLS 스케줄 페이지 **403(봇차단) 실측**; FRED API는 무료 키 등록 필요(미실측). 현실적으로 **연 32회 수작업/반자동 큐레이션 ≈ 160행 CSV** — 1회성 비용으로 가능.
- **비겹침**: 배제 1과 조건부/무조건부로 상이(위 근거). 워크플로우 `event_backtest`가 인프라. **통과(표본수 경고 부착)**
- **비용 관점**: 이벤트 게이트는 거래 빈도를 줄임 → 비용에 유리한 구조.

#### 7-b. 차익거래 마진 붕괴 (베이시스-펀딩 동시 압축 → 디레버리징 스트레스) — 판정: **조건부 통과(C1·C6 디코릴레이션 조건)**
- **원리**: 캐리 차익(스팟 롱+퍼페 숏) 마진이 극단으로 좁아지거나 급락하면 레버리지 차익 포지션 언와인드 → 현물 매도+숏 청산 캐스케이드 위험 신호. 시장 서사 확인: Binance square 2026-05-20 "Bitcoin Might Be Getting Dragged Down by Basis Arbitrage"(스니펫), CryptoQuant 분석 2025-06-23 "sharp decline signals a wave of deleveraging"(cryptopotato 경유), FT 2024-08.
- **비겹침 판정(가장 민감한 축)**:
  - C1(베이시스 모멘텀, 방향 신호)과 **동일 변수(프리미엄 인덱스)의 다른 변환·다른 가설**(모멘텀 vs 마진 스트레스/위험 신호) → 채택 시 C1과의 신호 상관 사전 검증 조건 부여 필수
  - C6(청산 캐스케이드, P3 사전등록)과는 보완 관계(마진 붕괴가 흔히 청산 캐스케이드 선행) — P3 측정 시 공변량으로 병기 가능
  - 배제 10(캐리 수확)/11(펀딩 극단 역발상)과는 방향·목적 상이(위험 감시 vs 수확/역발상 진입) — 통과
  - 워크플로우 `basis_carry_study`는 캐리 수확 측정용으로 목적 상이, `funding_carry_study` 동일 — **통과(패밀리 인접 명시)**
- **데이터 가용성**: **모두 로컬/무료 실측 완료** — premiumIndexKlines 1m(라운드 2 실측 200, 2019-12~) + 펀딩레이트(로컬 130파일). 추가 데이터 불필요 → 이 축 중 실행 장벽 최저.
- **비용 관점**: 진입 빈도 낮은 위험 신호 → 필터/청산 가속 용도 시 비용 중립.

---

## 2. 비겹침 통과 후보 종합 (X1~X8)

| # | 후보 | 축 | 원리 한 줄 | 판정 | 데이터(실측) | 비용 관점 | 우선순위 |
|---|------|----|-----------|------|--------------|-----------|----------|
| X1 | ES/NQ 야간 동조 조건부 게이트 | 1 | 외부 자산 1h 수익률 상태 → 15m 방향 바이어스 (risk-off 국면 가중) | **유망** — 배제1과 조건부/무조건부 구분 | Yahoo ES/NQ/GC 1h 2.4y + DXY 5y (200), Binance 15m 로컬 | 필터=비용 중립 | **1** |
| X2 | 매크로 이벤트 윈도우(CPI/FOMC) | 7-a | 발표 조건부 변동성·방향 캐스케이드 | **조건부 통과** — 배제1 비침범, 표본수 경고 | 이벤트 날짜 수작업 160행 + 로컬 15m | 이벤트 게이트=빈도↓ 유리 | **2** |
| X3 | 베이시스 마진 붕괴 디레버리징 스트레스 | 7-b | 캐리 마진 급락 → 언와인드 위험 신호 | **조건부 통과** — C1 상관 사전검증 조건 | premiumIndexKlines(기실측)+로컬 펀딩 | 위험 필터=비용 중립 | **3** |
| X4 | BTC-ETH 스프레드 평균회귀(1레그 방향) | 4 | 스프레드 극단 괘리 → 소외 자산 방향 | **조건부 통과** — S3·C7 인접, 2레그는 비용벽 | BTC/ETH 15m 로컬 보유 | 1레그만 현실적(12~20bp), 12h 스탯아브 전제 | 4 |
| X5 | 스테이블코인 공급 증감 필터 | 2 | USDT 유통액 증감 → 유동성 바이어스 | **조건부 통과** — 일간 주파수 한계 | DefiLlama USDT 일간 2017-11~ (200 실측) | 필터=비용 중립, 문헌 증거 약함(Boido 2026) | 5 |
| X6 | F&G 극단 필터 | 5 | 센티먼트 극단 역발 | **조건부 통과** — 배제12와 메커니즘 상이, 증분 정보 의문 | alternative.me 일간 3,172일 (실측, 로컬 저장) | 필터=비용 중립, C3와 슬롯 경쟁 | 6 |
| X7 | 거래소 사건 리스크 게이트 | 6 | 상장/장애 공지 → 변동성·위험 윈도우 | **조건부 통과** — 방향 엣지는 약함(표본·거리 문제) | Binance 상장 공지 2,279건 실측 확보 / 상폐·장애 이력 미확보 | 리스크 필터=비용 중립 | 7 |
| — | 온체인 거래소 순유입 | 3 | 지갑 순유입 → 방향 | **불가 확정** — 무료 과거 데이터 없음(CoinMetrics 401, DefiLlama CEX 404, CQ/GN 유료) | 없음 | — | 제외 |

**우선순위 논리**: X1~X3는 "필터/게이트"로 기존 투표에 비용 중립적으로 얹을 수 있고 데이터 장벽 0(로컬+무료 실측). X4는 유일한 새 진입 원리이나 비용 구조 리스크. X5~X7은 증분 정보 의문이 실증적 리스크. X8(온체인)은 데이터 불가로 제외.

---

## 3. 축별 결론 요약 (최종 응답용)

| 축 | 결론 | 비겹침 | 데이터 |
|----|------|--------|--------|
| 1 전통 시장 연동 | **유망(필터)** | 통과(배제1과 조건부 정의로 구분) | 무료 실측 완료(1h 2.4년) |
| 2 스테이블코인 플로우 | 조건부(일간 한계) | 통과 | DefiLlama 무료 일간 실측 |
| 3 온체인 순유입 | **불가 확정** | — | 무료 과거 데이터 없음(401/404 실측) |
| 4 BTC-ETH 페어 | 조건부(비용벽) | 통과(lead-lag·S3와 구분) | 로컬 데이터 이미 보유 |
| 5 F&G/소셜 | 조건부(우선순위 낮음) | 통과(배제12와 메커니즘 상이) | alternative.me 무료 일간 실측 |
| 6 거래소 사건 | 조건부(리스크 게이트) | 통과 | 상장 공지 2,279건 실측 확보 |
| 7-a 매크로 이벤트 | 조건부 통과(재검토) | 통과(배제1 비침범) | 이벤트 날짜 수작업 + 로컬 |
| 7-b 차익 마진 붕괴 | 조건부 통과 | 통과(C1·C6 상관 조건) | 전부 로컬/기실측 |

---

## 4. 데이터 가용성 실측 로그 (2026-10-12, curl 실측)

| 데이터 | URL/엔드포인트 | 결과 |
|---|---|---|
| F&G 최신 3건 | `https://api.alternative.me/fng/?limit=3` | **200**, value+classification |
| F&G 전체 히스토리 | `https://api.alternative.me/fng/?limit=0` | **200, 3,172레코드** (2018-02-01~) → 로컬 `r3_fng_hist.json` |
| USDT 유통액 히스토리 | `https://stablecoins.llama.fi/stablecoincharts/all?stablecoin=1` | **200, 3,240레코드** (2017-11-29~, totalCirculatingUSD/totalUnreleased) |
| 스테이블코인 마스터 리스트 | `https://stablecoins.llama.fi/stablecoins?includePrices=false` | **200** (559KB) |
| DefiLlama CEX 리스트 | `https://api.llama.fi/cexs` | **200** (88개, Binance id=2269, inflows_24h 포함, 과거 시계열 없음) |
| DefiLlama CEX 과거 시계열 | `/cex/2269/tokens`, `/cex/binance`, `/cexs/2269` | **모두 404** — 순유입 근사 불가 |
| CoinMetrics 커뮤니티 플로우 | `api.coinmetrics.io/v4/timeseries/asset-metrics?metrics=FlowInBFX...` | **401 unauthorized** (PRO 전용 실측) |
| Yahoo ES=F 1h | `query1.finance.yahoo.com/v8/finance/chart/ES=F?interval=1h&range=730d` | **200, 17,389레코드** (2024-05-19~) |
| Yahoo NQ=F 1h | 동일 패턴 | **200, 17,389레코드** |
| Yahoo ES=F 15m | `interval=15m&range=60d` | **200이나 60일 한정** (2026-08-02~, 5,688레코드) |
| Yahoo GC=F 1h / DX-Y.NYB 1d | 동일 패턴 | **200** / **200** |
| Binance 공지(상장) | `www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query?type=1&catalogId=48&pageNo=N` | **200**, catalogId=48 total **2,279**, `releaseDate` 필드 확인, pageNo=50 페이지네이션 실측 |
| Binance 공지(제목검색 Delist) | 동일 + `&title=Delist` | **400** — 상폐 검색 미확보 |
| Binance 공지 카탈로그 프로브 | catalogId 49~56 | 49=Latest Binance News(4,442), 51=API Updates(81), 나머지 N/A |
| CoinMetrics 카탈로그 | `/v4/catalog/asset-metrics?metrics=FlowInBFX&assets=btc` | 400 (unsupported parameter) |
| Upbit 공지 API | `api.upbit.com/v1/notices` | **404** |
| BLS CPI 스케줄 | `www.bls.gov/schedule/news_release/cpi.htm` | **403** (봇차단) |
| FRED releases JSON(키 없이) | `fred.stlouisfed.org/releases/json...` | 404 (FRED API는 무료 키 필요) |
| 연준 FOMC 캘린더 | `federalreserve.gov/monetarypolicy/fomccalendars.htm` | **200** |
| arxiv API | `export.arxiv.org/api/query` 4회 | **실패** (301→https 타임아웃, "Rate exceeded") |

---

## 5. 저장소 대조 근거 (실측)

- 워크플로우 25개 yml 전수 목록(ci, screening, grid_validation, xsec_validation, event_backtest, five_min_diagnosis, flow_diagnosis, edge_diagnosis, indicator_candidates, vote_frequency_diag, volume_profile_diag, positioning_diag, direction_ab_diag, asym_structure_diag, smc_diag, vol_forecast_diag, vol_gate_screen, funding_carry_study, basis_carry_study, carry_paper_sim, data_coverage, signal_candidates_diag, signal_validation, swing_study, oci_create_vm) — **X1~X7 어느 축도 기존 워크플로우와 동일 원리 아님**. X2·X7은 `event_backtest` 인프라 재사용 적합.
- `strategies/cross_sectional.py` 원문(197행) 확인: 일간 랭킹 모멘텀/저거래량(Liu-Tsyvinski-Wu 2022) — **공적분·페어 없음** → 축 4는 저장소 미측정 원리로 확정.
- ADR 89건 제목 전수 대조: fear/greed, stablecoin, nasdaq/dxy, onchain netflow, listing/delisting, CPI/FOMC 관련 ADR 0건(ADR-0008 일봉 SOL 모멘텀, ADR-0017/18 xsec만 부분 인접).

## 6. 권고 다음 단계 (저장소 규칙 준수 전제)

1. **X1 최우선 진단(저비용)**: Yahoo ES/NQ 1h 2.4년 + Binance 15m(1h로 리샘플) → 시간대 불문 조건부 동조(ΔES 1h z-score 분위수 × 향후 1h/4h/12h BTC 수익) 순위상관 진단. 사전등록에 "무조건부 시각 더미 금지(배제 1 방호)" 명문화.
2. **X3 동시 진단 가능**: 로컬 premiumIndexKlines+펀딩으로 마진 스트레스 지표(연율화 베이시스+펀딩 합의 급락 분위수) → C1 신호와의 상관 매트릭스 동시 산출.
3. **X2**: FOMC/CPI 날짜 160행 큐레이션 후 event_backtest 규격으로 사전등록(이벤트 ±8바).
4. **X4**: 12h 대기 스탯아브 1레그 변형 최소 진단(2레그는 비용 규격 24~40bp로 자동 탈락 예상 — 기각 사유 예고).
5. X5/X6은 일간 필터 후보 풀로 보류 — C3(Coinbase 프리미엄) 대비 증분 정보가 입증될 때만 승격.
6. 온체인(축 3)은 재제안 금지 — 본 리포트의 401/404 실측이 종결 증거.

## 7. 출처 파일 인덱스

- 검색 원문 JSON: `NEW2_run/research/r3_ws1.json ~ r3_ws26.json`(429 재시도 4회 포함, 노이즈 포함 전량 보존)
- 실측 원문: `r3_fng_hist.json`(3,172일), `r3_llama_usdt_hist.json`, `r3_llama_cexs.json`, `r3_yahoo_es/nq/gc/dxy.json`, `r3_bin_bapi.json`, `r3_cm_flow.json`(401), `r3_llama_cex_probe*.json`(404), `r3_upbit_notice.json`(404)
- 저장소 실측: `/tmp/NEW2_probe`(main, 워크플로우 25 yml + cross_sectional.py + docs/decisions 89 ADR)
- 이전 라운드 승계(재검증 안 함): `web_research_round2.md` C1~C9, ADR-0070/0079/0080/0086/0087 원문은 Task 4~8 세션에서 확인됨
