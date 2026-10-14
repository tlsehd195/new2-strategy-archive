# ALT-P1-P2 엔진 교차검증 (CandleCostModel + 캐리 회계 동일 포함)

엔진 파라미터: fee 5bp·half_spread 2bp·impact 0.1·max_part 5% · 주문 $10k/$100k · t=valid_n 표준

- 엔진 net>0 (10000): **4/36** — 최고 ZECUSDT 2024-12-10~2026-10-01 B90 h96 net 309.6bp(비용 104.4bp, p 0.817, FC 95)
- 엔진 net>0 (100000): **3/36** — 최고 ZECUSDT 2024-12-10~2026-10-01 B80 h96 net 153.6bp(비용 219.6bp, p 0.903, FC 136)
- half_spread ×2 감도($10k) net>0: 4/36
- **판정: FAIL 재확증** — diag Holm 0/36, 승격 대상 셀 부재