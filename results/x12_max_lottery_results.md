# X12 MAX/복권 효과 — 측정 결과 (2026-10-13)

- 규격: PREREG_x12_max_lottery_2026-10-13.md | 패밀리 48 cell-span (B80/B90 × L{90,288} × h{16,48,96} × 4스팬)
- 1차 방향(사전 고정): z≥+thr → 숏(복권 과열 정상화). 통제: 추속 롱 / 음의 꼬리 MIN 숏 / 무작위 100시드.
- **PASS = False** | Holm 통과 셀: 0/48

## 품질
- BTCUSDT_dup_kline: 0
- BTCUSDT_days_total: 2020
- BTCUSDT_days_complete: 1959
- BTCUSDT_days_incomplete: 61
- BTCUSDT_r_finite: 1957
- BTCUSDT_z_valid: {"90": 1754, "288": 1358}
- ETHUSDT_dup_kline: 0
- ETHUSDT_days_total: 2020
- ETHUSDT_days_complete: 1744
- ETHUSDT_days_incomplete: 276
- ETHUSDT_r_finite: 1741
- ETHUSDT_z_valid: {"90": 1519, "288": 1123}
- BTCUSDT_span1_2021-05-02~2022-08-17: {"kline_bars_in_span": 45312, "expected_bars": 45312, "kline_missing": 0, "sig_days": {"B80_L90": 26, "B80_L288": 16, "B90_L90": 13, "B90_L288": 7}}
- BTCUSDT_span1_2021-05-02~2022-08-17_min_episode_spacing_days: 2
- BTCUSDT_span2_2023-04-07~2024-03-18: {"kline_bars_in_span": 33216, "expected_bars": 33216, "kline_missing": 0, "sig_days": {"B80_L90": 25, "B80_L288": 27, "B90_L90": 17, "B90_L288": 16}}
- BTCUSDT_span2_2023-04-07~2024-03-18_min_episode_spacing_days: 2
- ETHUSDT_span1_2021-05-02~2022-08-17: {"kline_bars_in_span": 45312, "expected_bars": 45312, "kline_missing": 0, "sig_days": {"B80_L90": 32, "B80_L288": 15, "B90_L90": 14, "B90_L288": 11}}
- ETHUSDT_span1_2021-05-02~2022-08-17_min_episode_spacing_days: 2
- ETHUSDT_span2_2024-12-10~2026-04-20: {"kline_bars_in_span": 47616, "expected_bars": 47616, "kline_missing": 0, "sig_days": {"B80_L90": 35, "B80_L288": 16, "B90_L90": 22, "B90_L288": 10}}
- ETHUSDT_span2_2024-12-10~2026-04-20_min_episode_spacing_days: 2

## Holm 통과 셀: 0 — 전원 FAIL

## 최고 셀 (숏 net16 기준 top10)
- BTCUSDT 2021-05-02~2022-08-17 B80 L288h48: total=121.81bp net16=105.81bp n=16 p=0.1259 p_holm=1.0000 p_ctrl=0.0 추속롱=-137.81 MIN숏=-54.10817611218632 OOS16=110.83353719513703(n=15)
- BTCUSDT 2021-05-02~2022-08-17 B90 L288h16: total=113.60bp net16=97.60bp n=7 p=0.2404 p_holm=1.0000 p_ctrl=0.19 추속롱=-129.60 MIN숏=47.44143151262838 OOS16=109.16298815558513(n=6)
- BTCUSDT 2021-05-02~2022-08-17 B90 L288h48: total=96.11bp net16=80.11bp n=7 p=0.5166 p_holm=1.0000 p_ctrl=0.32 추속롱=-112.11 MIN숏=-46.78017940949213 OOS16=88.39210984892891(n=6)
- BTCUSDT 2021-05-02~2022-08-17 B80 L288h16: total=83.21bp net16=67.21bp n=16 p=0.2119 p_holm=1.0000 p_ctrl=0.07 추속롱=-99.21 MIN숏=32.31592855619152 OOS16=69.80892775963159(n=15)
- ETHUSDT 2021-05-02~2022-08-17 B80 L90h48: total=72.80bp net16=56.80bp n=32 p=0.2868 p_holm=1.0000 p_ctrl=0.12 추속롱=-88.80 MIN숏=-62.556271256620896 OOS16=12.05454928131815(n=18)
- BTCUSDT 2021-05-02~2022-08-17 B90 L90h48: total=70.29bp net16=54.29bp n=13 p=0.3982 p_holm=1.0000 p_ctrl=0.17 추속롱=-86.29 MIN숏=-64.76328750648608 OOS16=88.39210984892891(n=6)
- ETHUSDT 2021-05-02~2022-08-17 B80 L288h16: total=67.38bp net16=51.38bp n=15 p=0.3217 p_holm=1.0000 p_ctrl=0.1 추속롱=-83.38 MIN숏=7.621692506939359 OOS16=60.66900889880668(n=14)
- ETHUSDT 2021-05-02~2022-08-17 B90 L288h16: total=67.37bp net16=51.37bp n=11 p=0.4690 p_holm=1.0000 p_ctrl=0.24 추속롱=-83.37 MIN숏=67.60925398041489 OOS16=64.3763923224749(n=10)
- BTCUSDT 2021-05-02~2022-08-17 B80 L90h48: total=61.09bp net16=45.09bp n=26 p=0.4280 p_holm=1.0000 p_ctrl=0.18 추속롱=-77.09 MIN숏=2.441506306880678 OOS16=99.74127423643188(n=17)
- ETHUSDT 2021-05-02~2022-08-17 B80 L90h16: total=54.49bp net16=38.49bp n=32 p=0.1868 p_holm=1.0000 p_ctrl=0.08 추속롱=-70.49 MIN숏=-1.1832412807750514 OOS16=42.68053939089835(n=18)
