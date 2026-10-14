# X13 극단 일간 수익률 반전 — 유니버스 확장 측정 결과 (2026-10-13)

- 규격: PREREG_x13_universe_extreme_reversal_2026-10-13.md | 패밀리 12셀 (B80/B90 × L{90,288} × h{16,48,96} — 코인 풀링)
- 유니버스: BTCUSDT, ETHUSDT, SOLUSDT, ZECUSDT, NEARUSDT, XRPUSDT, HYPEUSDT, WUSDT, RSRUSDT, STRKUSDT (선정: research/x13_universe_selection.json — 24h qv≥$500M 상위 + 토큰화 전통자산 제외)
- 1차 방향(사전 고정): 극단 양의 일간 수익률 → 숏. 통제: 추속 롱 / 음의 꼬리 MIN 숏 / 무작위 100시드.
- t-표준: 진입일 군집 t (동일 진입일 = 1군집, valid_n=군집 수 — 교훈 ⑧ 다중코인 확장)
- **PASS = False** | Holm 통과 셀: 5/12

## 규칙 탈락 심볼
- HYPEUSDT: coverage 488 < 540 complete days
- WUSDT: monthly qv recheck fail: $8,059,654/day < $500,000,000
- RSRUSDT: monthly qv recheck fail: $2,445,617/day < $500,000,000
- STRKUSDT: monthly qv recheck fail: $25,628,671/day < $500,000,000

## 활성 심볼 품질
- BTCUSDT_coverage: {"dup_kline": 0, "days_total": 2020, "days_complete": 1959, "days_incomplete": 61, "r_finite": 1957, "z_valid": {"90": 1754, "288": 1358}, "last_complete_month": "2026-09-01", "daily_avg_qv_usd": 12514129379.33005, "liquidity_recheck_pass": true}
- ETHUSDT_coverage: {"dup_kline": 0, "days_total": 2020, "days_complete": 1744, "days_incomplete": 276, "r_finite": 1741, "z_valid": {"90": 1519, "288": 1123}, "last_complete_month": "2026-09-01", "daily_avg_qv_usd": 9713350374.835066, "liquidity_recheck_pass": true}
- SOLUSDT_coverage: {"dup_kline": 0, "days_total": 2009, "days_complete": 2004, "days_incomplete": 5, "r_finite": 2001, "z_valid": {"90": 1791, "288": 1395}, "last_complete_month": "2026-09-01", "daily_avg_qv_usd": 2096094086.4071476, "liquidity_recheck_pass": true}
- ZECUSDT_coverage: {"dup_kline": 0, "days_total": 2009, "days_complete": 2004, "days_incomplete": 5, "r_finite": 2001, "z_valid": {"90": 1791, "288": 1395}, "last_complete_month": "2026-09-01", "daily_avg_qv_usd": 2409006446.5285425, "liquidity_recheck_pass": true}
- NEARUSDT_coverage: {"dup_kline": 0, "days_total": 2009, "days_complete": 2004, "days_incomplete": 5, "r_finite": 2001, "z_valid": {"90": 1791, "288": 1395}, "last_complete_month": "2026-09-01", "daily_avg_qv_usd": 575538014.2682332, "liquidity_recheck_pass": true}
- XRPUSDT_coverage: {"dup_kline": 0, "days_total": 2009, "days_complete": 2004, "days_incomplete": 5, "r_finite": 2001, "z_valid": {"90": 1791, "288": 1395}, "last_complete_month": "2026-09-01", "daily_avg_qv_usd": 1272519403.832379, "liquidity_recheck_pass": true}
- HYPEUSDT_coverage: {"dup_kline": 0, "days_total": 489, "days_complete": 488, "days_incomplete": 1, "r_finite": 488, "z_valid": {"90": 398, "288": 200}, "last_complete_month": "2026-09-01", "daily_avg_qv_usd": 708917041.3773857, "liquidity_recheck_pass": true}
- WUSDT_coverage: {"dup_kline": 0, "days_total": 911, "days_complete": 910, "days_incomplete": 1, "r_finite": 910, "z_valid": {"90": 820, "288": 622}, "last_complete_month": "2026-09-01", "daily_avg_qv_usd": 8059653.620587284, "liquidity_recheck_pass": false}
- RSRUSDT_coverage: {"dup_kline": 0, "days_total": 2009, "days_complete": 2004, "days_incomplete": 5, "r_finite": 2001, "z_valid": {"90": 1791, "288": 1395}, "last_complete_month": "2026-09-01", "daily_avg_qv_usd": 2445616.8538257005, "liquidity_recheck_pass": false}
- STRKUSDT_coverage: {"dup_kline": 0, "days_total": 954, "days_complete": 953, "days_incomplete": 1, "r_finite": 953, "z_valid": {"90": 863, "288": 665}, "last_complete_month": "2026-09-01", "daily_avg_qv_usd": 25628670.8904241, "liquidity_recheck_pass": false}
- BTCUSDT_sig_days: {"B80_L90": 51, "B80_L288": 43, "B90_L90": 30, "B90_L288": 23}
- BTCUSDT_min_episode_spacing_days: {"B80_L90": 2, "B80_L288": 2, "B90_L90": 2, "B90_L288": 2}
- ETHUSDT_sig_days: {"B80_L90": 67, "B80_L288": 31, "B90_L90": 36, "B90_L288": 21}
- ETHUSDT_min_episode_spacing_days: {"B80_L90": 2, "B80_L288": 2, "B90_L90": 2, "B90_L288": 2}
- SOLUSDT_sig_days: {"B80_L90": 138, "B80_L288": 91, "B90_L90": 80, "B90_L288": 56}
- SOLUSDT_min_episode_spacing_days: {"B80_L90": 2, "B80_L288": 2, "B90_L90": 2, "B90_L288": 2}
- ZECUSDT_sig_days: {"B80_L90": 127, "B80_L288": 104, "B90_L90": 83, "B90_L288": 71}
- ZECUSDT_min_episode_spacing_days: {"B80_L90": 2, "B80_L288": 2, "B90_L90": 2, "B90_L288": 2}
- NEARUSDT_sig_days: {"B80_L90": 142, "B80_L288": 106, "B90_L90": 88, "B90_L288": 65}
- NEARUSDT_min_episode_spacing_days: {"B80_L90": 2, "B80_L288": 2, "B90_L90": 2, "B90_L288": 2}
- XRPUSDT_sig_days: {"B80_L90": 111, "B80_L288": 66, "B90_L90": 72, "B90_L288": 38}
- XRPUSDT_min_episode_spacing_days: {"B80_L90": 2, "B80_L288": 2, "B90_L90": 2, "B90_L288": 2}
- global_min_episode_spacing_days: {"B80_L90": 2, "B80_L288": 2, "B90_L90": 2, "B90_L288": 2}

## Holm 통과 셀 상세
- B80 L90h16: p_holm=0.0219 net16=-43.10bp n_eps=635 clusters=389 oos_net16=-42.25582316353127 gates={'clusters_ge30': True, 'net16_pos': False, 'oos_pos': False, 'era1_pos': False, 'era2_pos': False, 'breadth_ge3': True}
- B80 L90h48: p_holm=0.0068 net16=-70.14bp n_eps=635 clusters=389 oos_net16=-47.39371126510413 gates={'clusters_ge30': True, 'net16_pos': False, 'oos_pos': False, 'era1_pos': False, 'era2_pos': False, 'breadth_ge3': True}
- B80 L90h96: p_holm=0.0018 net16=-108.44bp n_eps=635 clusters=389 oos_net16=-96.85964529623095 gates={'clusters_ge30': True, 'net16_pos': False, 'oos_pos': False, 'era1_pos': False, 'era2_pos': False, 'breadth_ge3': True}
- B80 L288h16: p_holm=0.0106 net16=-54.68bp n_eps=440 clusters=288 oos_net16=-58.615431899084875 gates={'clusters_ge30': True, 'net16_pos': False, 'oos_pos': False, 'era1_pos': True, 'era2_pos': False, 'breadth_ge3': True}
- B80 L288h48: p_holm=0.0163 net16=-69.36bp n_eps=440 clusters=288 oos_net16=-59.448755678360634 gates={'clusters_ge30': True, 'net16_pos': False, 'oos_pos': False, 'era1_pos': True, 'era2_pos': False, 'breadth_ge3': True}

## 최고 셀 (숏 net16 클러스터 기준 top12)
- B90 L90h16: net16=-42.61bp gross=-21.87bp eps=388 clusters=257 p=0.0263 p_holm=0.1029 p_ctrl=0.93 폭=6코인 OOS=-35.226646878511986 era1=-57.7662642292335 era2=-38.09251518301087 추속롱=10.609056792844076 MIN숏=5.352145347991106
- B80 L90h16: net16=-43.10bp gross=-19.31bp eps=635 clusters=389 p=0.0027 p_holm=0.0219 p_ctrl=0.98 폭=6코인 OOS=-42.25582316353127 era1=-31.69897109545609 era2=-46.84070924595698 추속롱=11.10393067925239 MIN숏=-2.8145413726243427
- B90 L288h16: net16=-54.46bp gross=-33.37bp eps=273 clusters=189 p=0.0163 p_holm=0.0813 p_ctrl=0.97 폭=6코인 OOS=-52.18446444560195 era1=-11.128504820506759 era2=-57.65678739630787 추속롱=22.45642933553848 MIN숏=37.421732748002086
- B80 L288h16: net16=-54.68bp gross=-34.56bp eps=440 clusters=288 p=0.0011 p_holm=0.0106 p_ctrl=0.99 폭=6코인 OOS=-58.615431899084875 era1=24.018075070112182 era2=-61.83158050527924 추속롱=22.677442540663282 MIN숏=6.547447084836479
- B80 L288h96: net16=-67.81bp gross=-42.00bp eps=440 clusters=288 p=0.0267 p_holm=0.1029 p_ctrl=0.89 폭=6코인 OOS=-86.3894373589441 era1=35.402379786155876 era2=-77.19466837972554 추속롱=35.81158103256876 MIN숏=-27.095862139134606
- B80 L288h48: net16=-69.36bp gross=-49.34bp eps=440 clusters=288 p=0.0018 p_holm=0.0163 p_ctrl=0.99 폭=6코인 OOS=-59.448755678360634 era1=37.767443338397364 era2=-79.09867307847178 추속롱=37.35983004373269 MIN숏=9.592279456064128
- B80 L90h48: net16=-70.14bp gross=-42.27bp eps=635 clusters=389 p=0.0006 p_holm=0.0068 p_ctrl=1.0 폭=6코인 OOS=-47.39371126510413 era1=-82.42729505572794 era2=-66.10856493079805 추속롱=38.13580938322291 MIN숏=5.000971579407806
- B90 L90h48: net16=-71.99bp gross=-56.04bp eps=388 clusters=257 p=0.0119 p_holm=0.0712 p_ctrl=0.98 폭=6코인 OOS=-59.014645709642245 era1=-114.99662921402626 era2=-59.17289893197159 추속롱=39.9884634714316 MIN숏=-3.9311222545949347
- B90 L288h48: net16=-81.32bp gross=-53.57bp eps=273 clusters=189 p=0.0091 p_holm=0.0636 p_ctrl=0.98 폭=6코인 OOS=-74.82487317035097 era1=-18.40287720312779 era2=-85.96944167122511 추속롱=49.32200601998031 MIN숏=43.235883433855555
- B90 L90h96: net16=-86.50bp gross=-65.11bp eps=388 clusters=257 p=0.0257 p_holm=0.1029 p_ctrl=0.98 폭=6코인 OOS=-97.18098778450386 era1=-175.07422220408606 era2=-60.10018139303471 추속롱=54.49500010063015 MIN숏=-28.71449800327968
- B90 L288h96: net16=-93.26bp gross=-64.95bp eps=273 clusters=189 p=0.0258 p_holm=0.1029 p_ctrl=0.99 폭=6코인 OOS=-117.29595885097078 era1=-122.07586678882268 era2=-91.13446569905751 추속롱=61.262710218459354 MIN숏=-7.058345228341586
- B80 L90h96: net16=-108.44bp gross=-76.71bp eps=635 clusters=389 p=0.0001 p_holm=0.0018 p_ctrl=1.0 폭=6코인 OOS=-96.85964529623095 era1=-145.6537223560991 era2=-96.24312451241639 추속롱=76.43699955867228 MIN숏=-23.597004548925714

## 코인별 에피소드 기여 (B80 L90 h16 예시 — 최다 빈도 셀)
- {"BTCUSDT": 51, "ETHUSDT": 67, "SOLUSDT": 138, "ZECUSDT": 127, "NEARUSDT": 142, "XRPUSDT": 110}
