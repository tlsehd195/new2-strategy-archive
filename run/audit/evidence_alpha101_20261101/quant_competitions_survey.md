# 해외 퀀트 대회 공개 전략 조사 (2026-11-01)

질문: 전략을 공개하고 수익률 순위가 나오는 해외 대회에서 고수익 전략을 찾아 우리(코인 USDT-M 선물, 15분~일봉)에 적용할 수 있나?

## 한 줄 결론
코인 선물에 바로 쓸 수 있고, 라이브로 검증된, 비용 반영 고수익 전략을 공개하는 대회는 찾지 못했다. 순위 상위 수익률은 대부분 짧은 기간 + 많은 참가자 중 1등이라 운(선택 편향)이 크게 섞여 있다.

## 대회·플랫폼별 정리

| 대회 | 전략 공개? | 코인 선물 적용 | 검증 방식 | 우리에게 의미 |
|---|---|---|---|---|
| Kaggle G-Research Crypto Forecasting (2021-22) | 상위 해법 글 공개 (주로 LightGBM + 지연 수익률 특징) | 14개 코인, 단기 수익률 예측 → 형태는 맞음 | 최종 순위는 제출 후 약 3개월 실제 시장 데이터(라이브)로 채점. 지표는 가중 상관계수. 주최 측도 "신호 적고 잡음 큰 세계", 순위 급변을 언급 | 상관계수 순위일 뿐 비용 반영 수익률이 아님. 우리 ADR 0045(15분~4시간)에서 총 엣지가 왕복 비용 26.6bp보다 훨씬 작다는 것을 이미 확인 → 비용 벽을 못 넘을 가능성 매우 높음 |
| Numerai Crypto (2024~) | 개인 모델 비공개. 참가자 평균 '메타모델' 신호만 공개 피드 | 유동성 코인, 4주 뒤 순위 예측 → 우리 28일 횡단면 시험과 같은 형태 | 라이브 채점(4주 후 확정) | 과거 신호가 없어 DEV 백테스트 불가. 앞으로 받아 쌓는 전진(forward) 기록용으로만 가능. 주최 측도 "거래 결정용 아님" 명시 |
| QuantConnect Quant League (2024~2025 Q4 종료) | 우승 코드 'Clone'으로 공개(로그인 필요) | 대부분 주식. 2025 Q3 1위만 코인+주식 혼합 | 분기 단위 라이브 모의(종이) 거래 Sharpe. 2025 Q3 1~3위 Sharpe 3.93 / 3.56 / 3.1 | 약 3개월 표본 + 다수 팀 중 최댓값 → 3개월 Sharpe 3.9는 운만으로도 흔히 나옴. 주식 전략이라 이식 가치 낮음 |
| Quantopian (2011~2020 종료) | 대회 알고리즘 비공개 | 주식 | 888개 알고리즘 연구(Wiecki 외 2016): 백테스트 Sharpe가 실전 Sharpe를 설명하는 정도 R² 0.01~0.02, 백테스트를 많이 할수록 격차 커짐 | "공개 고수익 전략을 가져온다"의 위험을 수치로 보여주는 근거. 우리의 사전등록·누적 시도 보정 방식이 맞다는 근거 |
| WorldQuant BRAIN / IQC | 대회 알파 비공개. 다만 "101 Formulaic Alphas"(Kakushadze 2016) 공식은 공개 | 주식 일봉 공식. 코인 적용 논문·블로그 다수(대부분 비용 미반영) | 원 논문은 미국 주식, 실전 성과 비공개 | 공식이 명확해 사전등록하기 쉬운 유일한 '공개 고수익' 후보. 하지만 일 단위 회전이 커서 비용 부담, 우리 반전·모멘텀·거래량 시험과 상당 부분 겹침 |
| Kaggle Jane Street / Optiver / Two Sigma / Ubiquant | 해법 공개 | 특징 익명화 또는 주식 → 이식 불가 | 라이브 일부 | 해당 없음 |
| Freqtrade 공개 전략 (NostalgiaForInfinity 등) | 코드 완전 공개 | 코인 현물 5분봉 위주 | 제3자 모의 추적 사이트가 있으나 기간·조건 제각각, 생존 편향 | 규칙 수백 개를 수년간 실전 손익 보며 조정 → 사실상 거대한 데이터 마이닝. 5분봉 회전은 우리 비용 벽(1시간 보유 시 적중률 67% 필요)에 정면으로 걸림 |
| 거래소 트레이딩 대회(바이낸스·비트겟 등 ROI 순위) | 비공개 | - | 실계좌지만 고레버리지, 짧은 기간, 순위 선택 편향 극심 | 전략 정보 없음 |

## 선택 편향 vs 라이브 검증 구분
- 라이브(제출 후 미래 데이터) 채점: G-Research 최종, Numerai, Quant League. 단, 모두 "많은 참가자 중 상위"라 1등 점수는 운이 포함된 최댓값. 예: 3개월 표본에서 Sharpe의 표준오차는 약 2(연율) 수준이라, 참가자 수십 명이면 실력 0이어도 최댓값 3~4가 나올 수 있음.
- 백테스트만: Freqtrade, 블로그, 101 Alphas 코인 적용 글 대부분. Quantopian 연구상 거의 예측력 없음.

## 시험 가치 판단
- G-Research식 단기 ML: 이미 ADR 0045로 같은 시간대 비용 벽 확인 → 시험 가치 낮음.
- Numerai Crypto 메타모델: 과거 데이터 없음 → 지금은 시험 불가(전진 기록만 가능).
- 101 Alphas 일부: 공식 공개·사전등록 용이 → 유일하게 저비용으로 DEV 시험 가능. 사전 기대치는 낮음(우리 반전·거래량 가설과 겹침, 회전 비용).
- 나머지: 이식 불가 또는 정보 없음.

## 출처
- G-Research 대회 정리: https://www.gresearch.com/news/wrapping-up-the-g-research-crypto-forecasting-competition/
- Numerai Crypto 소개: https://blog.numer.ai/introducing-numerai-crypto-the-future-of-cryptocurrency-market-prediction/
- Quant League 2025 Q3 결과: https://www.quantconnect.com/announcements/19630/quant-league-q3-2025-results-a-close-race-between-lake-forest-and-cuhk/
- Wiecki 외 2016 요약: https://www.cxoadvisory.com/big-ideas/in-sample-vs-out-of-sample-performance-of-888-trading-strategies
- 101 Formulaic Alphas: https://arxiv.org/abs/1601.00991
- NostalgiaForInfinity: https://github.com/iterativv/NostalgiaForInfinity
