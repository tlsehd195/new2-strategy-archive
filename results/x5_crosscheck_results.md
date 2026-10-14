# X5 교차검증 — 엔진 CandleCostModel 적용

- 비용 공식: fee 5bp + half_spread 2bp + 10%*sqrt(participation) per side · valid_n 일간 규약

- o10k: Holm 통과 0/36 · 엔진 net>0 17/36
- o100k: Holm 통과 1/36 · 엔진 net>0 12/36
  - BTCUSDT 2021-05-02~2022-08-17 L30 h96 (net=-97.17bp)
