# X2 엔진 교차검증 (CandleCostModel)

- fee 5bp + half_spread 2bp + impact 0.1·sqrt(participation), max_part 5% — $10k/$100k 시나리오

- **$10k**: 거래 보유 셀 48/48, 엔진 순익 양수 셀 **14/48**, 최고 셀 BTCUSDT s2 FOMC W1 h16 133.20bp
- **$100k**: 거래 보유 셀 48/48, 엔진 순익 양수 셀 **7/48**, 최고 셀 BTCUSDT s2 FOMC W1 h16 95.88bp

| 셀 | n | $10k 순응(bp) | 양수거래/전체 | $100k 순응(bp) |
|---|---|---|---|---|
| BTCUSDT s2 FOMC W1 h16 | 7 | 133.20 | 7/7 | 95.88 |
| BTCUSDT s2 FOMC W1 h48 | 7 | 129.82 | 7/7 | 86.79 |
| ETHUSDT s2 CPI W4 h48 | 16 | 78.73 | 10/16 | 42.45 |
| ETHUSDT s2 CPI W4 h16 | 16 | 68.21 | 12/16 | 37.49 |
| BTCUSDT s2 FOMC W4 h48 | 7 | 60.55 | 4/7 | 20.48 |
| BTCUSDT s2 FOMC W4 h16 | 7 | 52.26 | 5/7 | 16.31 |
| ETHUSDT s2 CPI W4 h4 | 16 | 42.49 | 11/16 | 18.95 |
| ETHUSDT s2 CPI W1 h48 | 16 | 38.18 | 9/16 | -2.32 |
| ETHUSDT s2 CPI W1 h16 | 16 | 30.64 | 10/16 | -2.76 |
| ETHUSDT s2 FOMC W1 h4 | 11 | 27.63 | 6/11 | -0.33 |
| ETHUSDT s1 FOMC W4 h16 | 10 | 25.26 | 5/10 | -21.78 |
| BTCUSDT s2 FOMC W4 h4 | 7 | 23.67 | 4/7 | -7.13 |
