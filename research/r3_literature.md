# r3_literature.md — 학술 문헌 재실행(축소판), Task ID: 9-b
- 목적: 실측(백테스트/실증) 결과가 좋다고 보고된 "덜 알려진 신호" 발굴 (문헌 고유 신호만)
- 제약: web_search ≤ 8회(사용 8회), arXiv API ≤ 3쿼리(사용 3회, 전부 실패)
- 배제: 16번 배제목록 + 기존 후보 C1~C9 / F1~F4 / X1~X7 (겹치면 제안 금지)
- 실행: 15분봉 BTC/ETH USDT-M 선물, 최대 보유 12h, 왕복 비용 12~20bp 기준으로 평가

## 1. 검색 로그 (정직 기록 — 노이즈도 그대로)
| # | 도구 | 쿼리 | 결과 |
|---|------|------|------|
| 1 | web | bitcoin jump risk premium realized volatility bipower paper returns predictability | 유효 — RSJ(실현 부호점프)/실현변동 요인 문헌 다수(단 상당수가 배제5 인접) |
| 2 | web | cryptocurrency liquidity premium Amihud illiquidity measure returns empirical | 부분 — realized Amihud 개념 + 크립토 옵션 일유동성 프리미엄(C8 인접). 현물 시계열 프리미엄 실증 직접 히트 없음 |
| 3 | web | price clustering round numbers bitcoin trading returns study | 노이즈 — Holý(2021) 가격 클러스터링 모델링(스타일라이즈드 팩트)만. 수익률 신호 없음 |
| 4 | web | hash ribbon miner capitulation bitcoin returns backtest | 유효 — 실무 백테스트 다수(lookintobitcoin, bitbo, TradingView 보도). 학술 논문 부재 |
| 5 | web | bitcoin futures expiry effect spot returns pinning study | 유효 — Blasco et al.(2023) 만기 효과 실증 + Quantpedia(2024) 만기 주변 동학 |
| 6 | web | bitcoin drawdown recovery pattern duration returns study paper | 노이즈 — SSRN "triple penance"(전략 드로다운 이론)만, 가격 회복 신호 아님 |
| 7 | web | whale wallet large on-chain transfers bitcoin price impact paper | 노이즈 — 고래(해양생물) 폴백. 0 히트 |
| 8 | web | bitcoin round number magnet effect 10000 returns study (3번 재시도) | 노이즈 — 0 히트. 1회 재시도 후 포기 원칙 적용(클러스터링 축 누적 2회) |
| a1 | arXiv | all:"bitcoin" AND all:"jump risk" | 실패 — 빈 응답(연결 불가, 9-c와 동일 재현) |
| a2 | arXiv | all:"bitcoin" AND all:"intraday volatility" | 실패 — 빈 응답 |
| a3 | arXiv | all:"cryptocurrency" AND all:"cross-section returns" (https+리다이렉트 재시도) | 실패 — 14바이트 오류 응답 |

- web_search: 8/8 사용(유효 3, 부분 1, 노이즈 4), arXiv API: 3/3 사용(유효 0) — 9-c에서도 arXiv API 4회 전부 실패했으므로 샌드박스 네트워크 문제로 판단, ID 확보는 웹검색 출처로 대체

## 2. 발굴 후보 (문헌 고유 신호)

### L1. 선물 만기(expiration) 이벤트 효과 — 최우선
- 원리: 정규 시장(특히 CME) 비트코인 선물 만기일 주변에 헤지 롤/언와인딩으로 유동성·수익률 이상이 발생. 만기 직전/직후 윈도우의 체계적 수익률 패턴.
- 출처: Blasco, Corredor 등(2023) "Is there an expiration effect in the bitcoin market?" (ScienceDirect, 피인용 5) https://www.sciencedirect.com/science/article/abs/pii — **저자 보고**: "비트코인 선물 관련 명확한 만기 효과(expiration effect)가 존재". Quantpedia(2024-03-27) "Cryptocurrency Market Dynamics Around Bitcoin Futures Expiration" https://quantpedia.com — **보고**: 만기 주변 현물 수익률 동학 변화 가설 검증.
- 배제목록 비겹침: 배제1(시간대/세션)은 일중 시간대 효과, 본 신호는 캘린더 이벤트 조건 → 비겹침(단, 설계 시 만기일 vs 비만기일 동일 시간대 비교로 세션 혼입 통제 필요). 기존 후보 'CME 갭'은 주말 갭(금종가→일개시)으로 별개 메커니즘이나 '선물 캘린더' 패밀리 인접성 플래그.
- 검증 가능성: 높음 — Binance 분기선물 kline 아카이브(8-b에서 HTTP 200 실측) + CME/Deribit/Binance 만기일 캘린더(공개)로 15m 이벤트스터디 즉시 가능. 추가 데이터 0원.
- 비용 관점: 월 2~4회 이벤트로 거래 빈도 낮음, 왕복 12~20bp에 민감하지만 만기 전후 1~12h 홀딩 설계 가능.
- 과최적화 위험: 중간 — 표본 약 80~90개 월간 만기(2019~2025). 근월물 스펙 변경(CME 마이크로 선물 등) 구조적 변화 리스크.

### L2. 해시리본 / 채굴자 자포자상 종료 게이트
- 원리: 가격 하락 → 한계 채굴자 네트워크 이탈 → 해시레이트 급락(자포자상) → 해시레이트 30DMA가 60DMA 상향 돌파 시 매도 공급 압력 해소 → 회복 국면. 가격이 아닌 공급자 지표.
- 출처: Charles Edwards의 Hash Ribbons(실무 문헌) https://www.lookintobitcoin.com/charts/bitcoin-hash-ribbons/ , bitbo https://charts.bitbo.io/miner-capitulation/ , CoinDesk(2026-02) "Historic mining capitulation nears end" — **보고**: "자포자상 종료 신호가 통상 바닥 이후 진입점을 제시", 실무 백테스트에서 좋은 성과 주장(lookintobitcoin/tradingdigits). 학술 검증 논문 부재는 명시(실무 문헌 유일).
- 배제목록 비겹침: 배제9(상위TF 모멘텀)는 가격 모멘텀, 본 신호는 해시레이트 파생 → 비겹침. 크라우드 포지셔닝(12)·온체인 순유입(9-c 종결)과도 별개(네트워크 공급자 물량).
- 검증 가능성: 중간 — 일간 지표이므로 15m 단타(12h 홀딩)엔 직접 신호 불가, '롱 레짐 게이트' 용도만. 해시레이트 일간 시계열은 CoinMetrics community CSV(무료) 후보 — 9-c에서 401을 확인한 것은 유료 Flow 커넥터이므로 community CSV 별도 확인 필요(플래그).
- 비용 관점: 신호 빈도 연 수 회 → 자체 수익원이 아니라 게이트(필터)로만 기여 가능.
- 과최적화 위험: 높음 — 30/60 파라미터가 사후 선택된 실무 지표, 신호 표본 적음. 사전등록 없이 사용 금지 권고.

### L3. 실현변동 수준 → 미래 수익률 (변동성 스케일링 게이트)
- 원리: 실현변동(15m kline에서 산출)이 높은 시기/자산의 미래 수익률이 낮다는 실증 → 고변동 국면에서 익스포저 축소(변동성 관리형 스케일링).
- 출처: S. S. Lee(2024) "Variance Decomposition and Cryptocurrency Return Predictability" (Georgia Tech WP) https://www.scheller.gatech.edu/lee/pdf — **저자 보고**: "실현분산이 크립토 수익률을 예측하며, 실현분산이 높을수록 미래 수익률이 낮다". 보조: "Risk Premia in the Bitcoin Market"(arXiv 2025-08, 옵션+실현 기반 1·2차 모멘트 프리미엄, arXiv ID 미확보 — API 장애).
- 배제목록 비겹침: 배제14(HAR 변동성)는 '변동성 예측 모델', 본 신호는 '변동 수준 기반 익스포저 스케일링'으로 용도 상이. 배제9(모멘텀)·5(왜도/첨도) 비겹침. BTC/ETH 2자산이라 크로스섹션 불가 → 시계열 게이트 변형이 전제(조건부 통과).
- 검증 가능성: 높음 — 15m kline만으로 RV 계산, 저장소 har_vol 모듈과 결합 가능. 추가 데이터 0원.
- 비용 관점: 게이트/스케일링이라 추가 거래 없음(비용 중립). 파라미터 1개로 과최적화 위험 최소.
- 과최적화 위험: 낮음 — 단, 자체 백테스트에서 증분 기여가 미미할 수 있음(필터는 방향 엣지를 만들지 못함).

### 제안 보류(인접성으로 기각 — 기록용)
- Zhang(2023) "Good volatility, bad volatility, and the cross section" — 실현 부호점프(RSJ) 요인. **저자 보고**: RVM/RSJ가 미래 수익률 예측. 그러나 배제5(왜도/첨도) 패밀리 인접 → 제안 안 함.
- 크립토 옵션 일유동성 프리미엄(Deribit 2020~2024) — 기존 C8(옵션 스크우/DVOL) 인접 → 제안 안 함.
- Bollerslev 등 꼬리위험 프리미엄 — 첨도 인접 + 주식시장 중심 → 제안 안 함.

## 3. 미확보 축 (정직 기록)
- **라운드넘버/가격 클러스터링**: 2회 시도 전부 노이즈. Holý(2021, Quantitative Finance) 가격 클러스터링 모델링 논문만 — 스타일라이즈드 팩트 연구이고 수익률 신호 백테스트 부재 → 미확보.
- **고래 지갑 이동 가격충격**: 쿼리가 해양생물로 폴백(0 히트). 9-c에서 온체인 플로우 무료 과거데이터 401/404 실측과 동일한 데이터 벽 → 미확보(데이터 불가).
- **인트라데이 드로다운 회복 패턴**: 노이즈. SSRN 트리플 페넌스 논문은 전략 드로다운 이론이지 가격 회복 신호 아님 → 미확보.
- **반감기(halving) 이벤트 스터디**: 검색 예산 소진으로 미실시. 전략적 판단: 표본 4회(2012/2016/2020/2024)로 15m 검증 불가 + X2(매크로 이벤트 윈도우) 인접 → 미확보로 종결.
- **점프 리스크 프리미엄(독립 신호)**: arXiv API 장애로 원문 ID 미확보. 웹 히트 대부분이 배제5/옵션 인접 → 독립 후보 없음, 미확보.

## 4. 비겹침 대조 요약
| 후보 | 배제1 | 배제5 | 배제9 | 배제14 | 기존후보 인접 | 판정 |
|------|-------|-------|-------|--------|----------------|------|
| L1 만기 효과 | 세션 혼입 통제 필요 | 무관 | 무관 | 무관 | CME갭(선물캘린더) | 조건부 통과 |
| L2 해시리본 | 무관 | 무관 | 가격모멘텀 아님 | 무관 | 없음(온체인순유입 종결과 별개) | 조건부 통과(게이트 전용) |
| L3 RV 스케일링 | 무관 | 무관 | 무관 | 용도 상이 명시 | HAR모듈 재사용 가능 | 조건부 통과(시계열 변형) |

## 5. 결론
- 문헌 고유 신호 3건(L1 만기 효과/L2 해시리본/L3 RV 스케일링) 발굴 — 전원 조건부 통과(패밀리 인접성 명시). L1이 유일하게 '일중 12h 홀딩 규모의 이벤트 신호'로 직결되며 표본도 충분 → 다기 측정 1순위.
- 미확보 4축(라운드넘버/고래/드로다운 회복/반감기) + arXiv API 0/3 실패는 노이즈·장애 포함 정직 기록.
- 예산: web_search 8/8, arXiv 3/3 사용.
