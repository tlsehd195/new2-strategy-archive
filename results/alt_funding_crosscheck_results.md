# ALT-P1 엔진 교차검증 (CandleCostModel + 캐리 회계 동일 포함)

엔진 파라미터: fee 5bp·half_spread 2bp·impact 0.1·max_part 5% · 주문 $10k/$100k · t=valid_n 표준

- 엔진 net>0 (10000): **0/48** — 최고 BNBUSDT 2021-05-02~2022-08-17 B90 h96 net -0.2bp(비용 92.3bp, p 1.000, FC 0)
- 엔진 net>0 (100000): **0/48** — 최고 TRXUSDT 2021-05-02~2022-08-17 B90 h96 net -125.6bp(비용 284.0bp, p 0.939, FC 96)
- half_spread ×2 감도($10k) net>0: 0/48
- **판정: FAIL 재확증** — diag Holm 0/48, 승격 대상 셀 부재