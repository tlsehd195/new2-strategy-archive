# X14 엔진 교차검증 (CandleCostModel 기본 파라미터)

엔진 파라미터: fee 5bp·half_spread 2bp·impact 0.1·max_part 5% · 주문 $10k/$100k · t=valid_n 표준

- 엔진 net>0 (10000): **0/96** — 최고 ETHUSDT 2024-12-10~2026-04-20 L2880 B90 h48 net -6.9bp(비용 29.4bp, p 0.885, FC 0)
- 엔진 net>0 (100000): **0/96** — 최고 ETHUSDT 2024-12-10~2026-04-20 L2880 B90 h48 net -40.3bp(비용 62.8bp, p 0.402, FC 0)
- half_spread ×2 감도($10k) net>0: 0/96
- **판정: FAIL 재확증** — diag Holm 14/96 전원 추종 방향 음수(되돌림), 승격 대상 셀 부재