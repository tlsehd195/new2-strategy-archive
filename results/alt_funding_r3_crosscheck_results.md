# ALT-P1-R3 엔진 교차검증 — ZEC·DOGE (CandleCostModel + 캐리 회계 동일 포함)

엔진 파라미터: fee 5bp·half_spread 2bp·impact 0.1·max_part 5% · 주문 $10k/$100k · t=valid_n 표준

- 엔진 net>0 (10000): **0/24** — 최고 ADAUSDT 2021-05-02~2022-08-17 B90 h96 net -72.1bp(비용 84.3bp, p 0.916, FC 0)
- 엔진 net>0 (100000): **0/24** — 최고 ADAUSDT 2021-05-02~2022-08-17 B90 h96 net -216.3bp(비용 229.0bp, p 0.766, FC 6)
- half_spread ×2 감도($10k) net>0: 0/24
- **판정: FAIL 재확증** — diag Holm 0/24, 승격 대상 셀 부재