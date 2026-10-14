# NEW2 전략 전수 인벤토리 — 실제 코드 기반 (HEAD 8ec6371)

- 저장소: `/home/z/tlsehd195-NEW2` (main, HEAD `8ec637153b4375143cbb9b34c00eb32053feca5e`, working tree clean)
- 작성일: 2026-10-12 / 작성: Task 8-a (Explore)
- 근거 원칙: 모든 진입/청산/지표 서술은 실제 코드 파일과 줄번호로 근거를 표기. 코드에서 확인 불가한 것은 "추정"으로 명시.
- 참고: `configs/strategies.json`(2077행)의 57개 레코드는 파라미터만 담고 있고 로직은 없음 → 본 문서가 `src/cointrader/strategies/` 구현 코드를 전수 대조해 채움.

---

## 0. 요약 — 클래스 총수와 57개 ID 매핑 방식

**매핑 구조** (`src/cointrader/strategies/registry.py`):

1. `configs/strategies.json`의 각 레코드는 `name`(클래스명) 필드를 가지고, 이 이름이 `FACTORIES` 딕셔너리(`registry.py:35-50`)의 키와 대응. FACTORIES는 **14개 클래스**를 등록:
   `TrendEmaAtr, BreakoutVolume, BollingerReversion, RegimeHybrid, SmaTrendFilter, DonchianTrend, TrendPullback` (swing.py), `VwapReversion, RangeBreakoutVolume, ShortTermMeanReversion, MicrostructureMomentum` (scalp.py), `IndicatorVote` (indicator_vote.py), `DayTradeVote` (daytrade.py — IndicatorVote의 서브클래스), `FlowVote` (flow_vote.py).
2. `build()`(`registry.py:80-94`)가 파라미터로 인스턴스를 만든 뒤 **코드가 만든 `strategy_id`/`family`/`warmup`이 레지스트리 기대값과 일치하지 않으면 즉시 예외**(`registry.py:85-93`). 즉 strategy_id는 코드가 자기 서술로 생성 (`IndicatorVote.strategy_id` = `indicator_vote.py:116-129`, `FlowVote.strategy_id` = `flow_vote.py:78-81`, 스윙/스캘프는 각 클래스 내 property).
3. 따라서 57개 ID = 14개 클래스 × 파라미터 변형. 실제 분포:

| 클래스 | 등록 ID 수 | family | TF |
|---|---|---|---|
| DayTradeVote | 38 | daytrade | 15m |
| IndicatorVote | 4 | swing | 1d |
| FlowVote | 4 | daytrade | 15m |
| TrendEmaAtr / BreakoutVolume / BollingerReversion / RegimeHybrid / SmaTrendFilter / DonchianTrend / TrendPullback | 각 1 (계 7) | swing | 1h/4h/1h/1h/1d/1d/1d |
| VwapReversion / RangeBreakoutVolume / ShortTermMeanReversion / MicrostructureMomentum | 각 1 (계 4) | scalp | 1m/5m/1m/1m |

- 합계: swing 11 + scalp 4 + daytrade 42 = 57 (daytrade 42 = DayTradeVote 38 + FlowVote 4).
- 등록 안 된 연구 전용 모듈 6개(`grid.py, funding_carry.py, basis_carry.py, cross_sectional.py, adaptive_ensemble.py, baselines.py`)는 FACTORIES에 없어 어떤 러너로도 기동 불가(`registry.py:76-77`에서 클래스명 검증) — 본 인벤토리에서 제외하고 §8에 한 줄씩만 기록.
- 전부 `risk_profile: "default"`, `hypothesis_id: null` (configs/strategies.json 전 레코드 실측).
- 마켓 등록: 4종 전체(BTC/ETH/SOL/XRP USDT) 21건 — 클래식 스윙 7 + 스캘프 4 + DayTradeVote 기본/사이드/트레일 10(#16-25). BTC+ETH만 32건 — FlowVote 4 + DayTradeVote 변형 28(#26, 30-57). BTC+SOL만 4건 — 스윙 IndicatorVote 4(#12-15). (configs/strategies.json 실측)

---

## 1. 로직군 상세 (대표 구현 의사코드)

### G2. 지표 투표 계열 — IndicatorVote / DayTradeVote (57건 중 42건 = 74%)

파일: `src/cointrader/strategies/indicator_vote.py` (기반), `src/cointrader/strategies/daytrade.py` (15m 프리셋).

**(1) 투표 패널 — 6개 기본 지표** (`features/indicator_votes.py:45` DEFAULT_PANEL = `ema_trend, donchian_pos, roc, rsi, bollinger_b, obv_slope`; 산출식은 `indicator_votes.py:104-151 raw_scores`, 최근 140봉(MIN_BARS) 고정 윈도우):

| 지표 | 산출식 (근거: indicator_votes.py) |
|---|---|
| ema_trend | `tanh(close/EMA20 − 1)` — 포화상수 `0.03×score_scale` (:135) |
| roc | `tanh(ROC14)` — 포화상수 `0.05×score_scale` (:136) |
| rsi | `(RSI14 − 50)/50` (:137) |
| donchian_pos | `(close−low20)/(high20−low20)×2−1` 클립 [−1,1] (:140) |
| bollinger_b | `−tanh(z20)` — **평균회귀 투표: %B 높으면 약세** (:144, 주석 "vote AGAINST stretch") |
| obv_slope | `tanh(OBV 기울기)` — 포화상수 0.3 (:146) |

각 스코어는 (-1,1) 부호 스코어이며, 15m에서는 `score_scale=0.1`로 수익률형 스코어 상수가 1/10로 축소됨 (`indicator_vote.py:70`, `daytrade.py:29`, 근거 주석 `daytrade.py:6-8`).

**(2) 캘리브레이션** — `PlattCalibrator`(`indicator_votes.py:204-245`): 각 지표별로 **이미 실현된 (score_t, t+horizon 수익률 부호) 쌍만** 사용해 L2-릿지 로지스틱 회귀로 score→P(long) 학습 (`indicator_vote.py:218-231` verdict, 결과 바가 아직 안 닫힌 t는 제외 `last = n-1-horizon`). 30행 미만이면 무정보 캘리브레이터(p=0.5) 반환(`indicator_votes.py:222-224`).
**결합** — `combine_votes`(`indicator_votes.py:265-278`): 로그오즈 **동일가중 평균** → 결합 p_long + 지표별 p + 동의 개수(agree_long/short = p>0.5/<0.5 개수).

**(3) 진입 조건** (`indicator_vote.py:286-289`):

```
IF p_long >= enter_confidence(0.60)  AND agree_long/패널수 >= min_agree(0.6)  → 롱 진입
IF allow_short AND p_long <= 1-0.60  AND agree_short/패널수 >= 0.6            → 숏 진입
strength = min(1, (confidence-0.5)*4)                                          (:276)
```

- 진입 전 게이트 (순서대로, 게이트는 **진입만 차단하고 청산은 안 막음** — `indicator_vote.py:35-36, 262, 279, 285`):
  - 변동성 게이트: `vol_gate_lo(0.5) ≤ 단기/장기 실현변동 비율 ≤ vol_gate_hi(2.0)` 아니면 진입 차단 (`indicator_vote.py:277-279`, 비율 계산은 `side_indicators.py:60-75 volatility_ratio`, 윈도우는 daily 10/60, 15m 96/960 봉 — `daytrade.py:30-31`).
  - regime_switch(rs1/rs2): 레짐별 follow(그대로)/fade(투표 반전, `p_long→1−p_long`, 동의도 교체)/flat(신규 진입 금지) 스위치 (`indicator_vote.py:43-48 REGIME_SWITCHES`, 적용 `:268-275`).
  - entry_gate(g*/ghar80/gtod/grand20): `har80`=HAR 변동성 예측이 자기 14일 분위 q80 초과 시 차단(`features/har_vol.py:29-80 HarGate`, 계수 상수 `har_vol.py:19-22`, warmup +4232봉 `har_vol.py:26`), `tod`=마감시각 UTC 11-14시 차단(`indicator_vote.py:39 TOD_BLOCKED_HOURS, :238-239`), `rand20`=고정 의사난수로 시간의 20% 차단(`:240-241`) — 적용 `:280-285`.
  - 레짐 UNDEFINED 진입은 risk engine이 최종 차단 (`risk/engine.py:222-223`).
- 사이드 데이터 투표(use_side_data=True, 4건: #14,15,17,19): 기본 6표에 `funding_crowding`(직전 9회 펀딩비 평균 → `−tanh(mean/0.0005)`, 역발상 — `side_indicators.py:21,34-41`)과 `oi_confirm`(5일 가격등락 × max(0, tanh(OI 등락/0.05)) — OI 하락 시 기권, `side_indicators.py:44-57`)을 추가 (`indicator_vote.py:163-176`). 주입은 러너가 `attach_side_data()`로 수행(`scripts/run_screening.py:80-88`, `scripts/run_validation.py:110-119`); **src 내부에서는 주입 호출이 없어서 미주입 상태로는 fail-closed(전 시간대 flat)** — `indicator_vote.py:174-175` fund/oi None → None 반환.

**(4) 청산 조건**:

- 신호 청산(히스테리시스): `exit_long = p_long < exit_confidence(0.52)`, `exit_short = p_long > 0.48` (`indicator_vote.py:262`; rs 사용 시 반전된 p 기준 `:274`). 엔진에서 반대신호는 "청산만"이고 재진입은 다음 의사결정(`strategies/base.py:16-18` 주석).
- 보호 청산(엔진이 가격 거리로 처리): `stop_distance = stop_atr(2.5)×ATR14` (`indicator_vote.py:265`), 트레일링 변형은 `trail_atr×ATR`(+ 활성화 `trail_activate_atr×ATR`) (`indicator_vote.py:263-264`). TP 없음.
- 시간 청산: `max_hold_bars=48`(15m→12시간) 경과 시 엔진이 `time_stop` 청산 (엔진 `backtest/event_engine.py:584-585`, 페이퍼 `paper/engine.py:495-501`). daily 스윙 IndicatorVote는 None(무제한).
- 기타 엔진 한도: `max_entries_per_day=100`(UTC일당 진입 상한, `event_engine.py:593-594`), `quality_window_bars=192`(직근 2일 내 캔들 이슈 있으면 진입 거절, `event_engine.py:596-598`).
- 15m 프리셋(DayTradeVote)이 기본값에서 바꾸는 것: horizon 16/48, fit_lookback 1000, score_scale 0.1, vol_short 96/vol_long 960, bars_per_day 96(OI 투표 정렬용), max_hold_bars 48, max_entries_per_day 100, quality_window_bars 192 (`daytrade.py:23-35`, 근거 주석 `:5-12`).

**(5) warmup**: `MIN_BARS(140) + fit_lookback + horizon + 1 (+ MIN_INDEX(481) if extra_panel) (+ HAR 4232 if gate=har80)` (`indicator_vote.py:131-135`) → 실측 일치: h16=1157, h48=1189, 패널변형=1638/1670, ghar80=5389/5421 (configs/strategies.json).

### G3. FlowVote — 15m 테이커 플로우 (4건: #26-29)

파일: `src/cointrader/strategies/flow_vote.py`.

**(1) 신호 하나** (`flow_vote.py:6-10` 모듈 독스트링, 구현 `:104-136`):

```
flow_t  = (2·Σtaker_buy_vol − Σvol) / Σvol   최근 flow_window(16 또는 48)봉
z_t     = (flow_t − mean(flow))/std(flow)     최근 z_window(480봉=5일) 기준
score_t = tanh(z_t / 2)
p_long  = PlattCalibrator(score → P(flow_window봉 후 수익 > 0), fit_lookback 480쌍)   (:163-184)
```

**(2) 진입** (`flow_vote.py:200`): `z ≥ z_in(1.0)` → 롱, `z ≤ −1.0` (allow_short=True) → 숏.
진입 필터(투표 아님, 진입만 차단):
- vol gate: 0.5 ≤ 96/960봉 변동비율 ≤ 2.0 아니면 차단 (`:198-199`).
- vpin_gate(#27,29): 바-타임 VPIN 근사 `mean|2·taker_buy/vol − 1|` (48봉)이 자기 480봉 궤적 상위 10%(vpin_block_pct 0.9)면 차단 (`:138-154`).
- funding_gate(#27,29): 펀딩 크라우딩 스코어 |score| ≥ 0.5(`flow_vote.py:41 CROWDED`)이고 그 방향이 진입 쪽이면 차단(롱 차단 = score ≤ −0.5, `:209-215`). 펀딩 데이터 없으면 fail-closed flat (`:212-213`).

**(3) 청산** (`flow_vote.py:197`):

```
exit_long  = z < 0.0   exit_short = z > 0.0   (z가 0을 되돌아가면 반대신호 청산)
stop_distance = stop_atr(2.5) × ATR14                                                     (:218)
```

트레일링·TP 없음. 엔진 시간청산 `max_hold_bars=48`(12h), `max_entries_per_day=100`, `quality_window_bars=192` (`:64-66`).
**(4) warmup** = `z_window + flow_window + fit_lookback + flow_window + 1` (`:83-85`) = w16: 993, w48: 1057 (실측 일치).

### G1. 클래식 스윙 7종 (각 1건: #1-4, 9-11)

공통: 결정론적 레짐 분류기(`features/regime.py:88-113` — 20봉 실현변동의 100봉 분위 >0.9→HIGH_VOLATILITY, |close−EMA50|/ATR14 ≥1.0 & EMA 순서 일치→TREND_UP/DOWN, 분위 <0.1→LOW_VOLATILITY, else RANGE; warmup 201봉 `regime.py:56-58`)를 진입 조건에 직접 사용, 모든 진입에 ATR 스톱 필수(`strategies/base.py:21-24`, `swing.py:11-13`).

| 클래스 | 진입 | 청산 | 근거 |
|---|---|---|---|
| **TrendEmaAtr** (#1, 1h) | TREND_UP 레짐 & EMA20>EMA50 → 롱 / TREND_DOWN & EMA20<EMA50 → 숏(allow_short) | exit_long=fast<slow, exit_short=fast>slow(EMA 반전 신호청산) + stop 2.5×ATR14 + **trail 3.0×ATR**(무조건 활성) | swing.py:64-82 (진입 :77-80, 청산 :74-76) |
| **BreakoutVolume** (#2, 1h) | 레짐이 HIGH_VOLATILITY/UNDEFINED 아니고 & 상대거래량(20봉 평균 대비) ≥1.5 & close > 직전 20봉 고가 → 롱 / < 20봉 저가 → 숏 | exit_long=close<직전 10봉 저가, exit_short=close>10봉 고가 + stop 2.0×ATR | swing.py:115-138 (진입 :129-136, 청산 :128) |
| **BollingerReversion** (#3, 1h) | RANGE/LOW_VOLATILITY 레짐 & BB20 z ≤ −2.0 & RSI14 ≤ 30 → 롱 / z ≥ 2.0 & RSI ≥ 70 → 숏 | exit z-score ≥0(롱)/≤0(숏) 또는 레짐 추세화 + TP=\|close−BB중심\| + stop 1.5×ATR14 | swing.py:172-194 (진입 :189-192, 청산 :182-187) |
| **RegimeHybrid** (#4, 1h) | TREND_*면 TrendEmaAtr 신호, RANGE/LOW_VOL이면 BollingerReversion 신호(고정 인스턴스 위임) | 소유 레짐 규칙의 exit 사용, HIGH_VOLATILITY면 **양방향 즉시 청산** | swing.py:222-244 (위임 :225-240, HV 청산 :241-242) |
| **SmaTrendFilter** (#9, 1d) | **롱 전용**: close > SMA100 & SMA100이 20봉 전보다 상승 | exit_long=close<SMA100 + stop 3.0×ATR20(카타스트로피 스톱, 숏 없음, 트레일 없음) | swing.py:277-292 (진입 :289-291, 청산 :288) |
| **DonchianTrend** (#10, 1d) | **롱 전용**: close > 직전 55일 고가 (터틀 55/20, 볼륨·레짐 필터 없음) | exit_long=close<직전 20일 저가 + stop 2.0×ATR20 | swing.py:324-337 (진입 :334-336, 청산 :337) |
| **TrendPullback** (#11, 1d) | **롱 전용**: close > SMA100(장기 상승 추세) & BB20 z ≤ −1.5(일시 눌림) | exit_long=z ≥0 또는 추세 이탈 + TP=(BB중심−close) + stop 2.0×ATR20 | swing.py:372-389 (진입 :383-388, 청산 :384) |

### G4. 스캘프 4종 (각 1건: #5-8)

공통: 진입에 레짐 게이트(HIGH_VOLATILITY/UNDEFINED 차단), 1 ATR 언더 스톱, TP는 대부분 평균/VWAP 회귀 목표.

| 클래스 | 진입 | 청산 | 근거 |
|---|---|---|---|
| **VwapReversion** (#5, 1m) | RANGE/LOW_VOL 레짐 & (close−VWAP30)/ATR14 ≤ −2.0 & 되돌림 봉(close>open) → 롱 / ≥ +2.0 & close<open → 숏 | exit close가 VWAP 위(롱)/아래(숏) 교차 + TP=\|close−VWAP\| + stop 1.0×ATR | scalp.py:66-87 (진입 :82-85, 청산 :78-81) |
| **RangeBreakoutVolume** (#6, 5m) | HIGH_VOL/UNDEF 아님 & 상대거래량 ≥2.0 & close > 직전 30봉 고가 → 롱 / < 30봉 저가 → 숏 | **신호 청산 없음 — 오직 stop 1.0×ATR / TP 1.5×ATR(엔진 보호 주문)** | scalp.py:119-138 (진입 :133-136, 보호 :131) |
| **ShortTermMeanReversion** (#7, 1m) | HIGH_VOL/UNDEF 아님 & BB20 z ≤ −3.0 → 롱 / z ≥ 3.0 → 숏 | exit z ≥0(롱)/≤0(숏) + TP=\|close−20봉 평균\| + stop 1.0×ATR | scalp.py:168-187 (진입 :182-185, 청산 :179-181) |
| **MicrostructureMomentum** (#8, 1m) | **BOOK-CONFIRMED**: MarketContext 필요(없으면 절대 진입 안 함, `scalp.py:224-226`), trade_imbalance ≥ 0.3 & book_imbalance ≥ 0.3 & microprice > mid → 롱 / 전부 반대 → 숏 | exit trade_imbalance 부호 반전(롱은 <0) + stop 0.75×ATR14 + TP 1.0×ATR | scalp.py:220-242 (진입 :231-240, 청산 :236) |

---

## 2. 전수 표 — 57건 (configs/strategies.json 순서)

약어: **G2**=지표투표(§1.G2), **G3**=플로우투표(§1.G3), **G1**=클래식 스윙, **G4**=스캘프. "호"는 §1 대표 로직과 동일 + 괄호 안 변형만 기술. TF는 레지스트리 timeframes. warmup·마켓은 §0/§1 참조.

| # | strategy_id | family | TF | 진입 조건 | 청산 조건 | 지표 | 비고 |
|---|---|---|---|---|---|---|---|
| 1 | swing_trend_ema_atr_20_50_v1 | swing | 1h | G1 TrendEmaAtr | EMA 반전 + 2.5ATR 스톱 + 3ATR 트레일 | EMA20/50, ATR14, 레짐 | allow_short |
| 2 | swing_breakout_volume_20_10_v1 | swing | 1h | G1 BreakoutVolume | 반대 10봉 돈치안 돌파 + 2ATR 스톱 | Donchian20/10, 상대거래량, ATR14, 레짐 | allow_short |
| 3 | swing_bollinger_reversion_20_2_v1 | swing | 1h | G1 BollingerReversion | z≥0 복귀·레짐 전환 + TP(중심) + 1.5ATR 스톱 | BB20, RSI14, ATR14, 레짐 | 숫쇼트 파라미터 없음(양방향) |
| 4 | swing_regime_hybrid_v1 | swing | 1h | G1 RegimeHybrid | 소유 레짐 규칙 청산, HV 전량 청산 | 위 두 전략 조합 | parameters {} (고정 인스턴스) |
| 5 | scalp_vwap_reversion_30_2_v1 | scalp | 1m | G4 VwapReversion | VWAP 교차 + TP(VWAP) + 1ATR 스톱 | VWAP30, ATR14, 레짐 | 캔들 기반 |
| 6 | scalp_breakout_volume_30_2_v1 | scalp | 5m | G4 RangeBreakoutVolume | 신호청산 없음: 1ATR 스톱 / 1.5ATR TP | Donchian30, 상대거래량, ATR14 | TP 비율 고정 |
| 7 | scalp_short_mean_reversion_20_3_v1 | scalp | 1m | G4 ShortTermMeanReversion | z 복귀 + TP(평균) + 1ATR 스톱 | BB20 z-score, ATR14, 레짐 | z±3 진입 |
| 8 | scalp_microstructure_momentum_v1 | scalp | 1m | G4 MicrostructureMomentum(북 확인) | 플로우 부호 반전 + 0.75ATR 스톱 / 1ATR TP | trade/book imbalance, microprice, ATR14 | 북 데이터 필요, warmup 58 |
| 9 | swing_daily_sma_trend_100_v1 | swing | 1d | G1 SmaTrendFilter | close<SMA 청산 + 3ATR 카타스토피 스톱 | SMA100(+20봉 기울기), ATR20 | 롱 전용 |
| 10 | swing_daily_donchian_55_20_v1 | swing | 1d | G1 DonchianTrend | 20일 저가 이탈 + 2ATR 스톱 | Donchian55/20, ATR20 | 롱 전용, 필터 없음 |
| 11 | swing_daily_trend_pullback_100_20_v1 | swing | 1d | G1 TrendPullback | z 복귀·추세 이탈 + TP(중심) + 2ATR 스톱 | SMA100, BB20 z, ATR20 | 롱 전용 눌림목 |
| 12 | swing_indicator_vote_h5_c0.6_v2 | swing | 1d | G2 (h=5, c=0.60) | p<0.52 반대신호 + 2.5ATR 스톱 | 6지표 패널(일간 스케일) | vol_short 10/60 |
| 13 | swing_indicator_vote_h10_c0.6_v2 | swing | 1d | G2 (h=10) | 동일 | 동일 | — |
| 14 | swing_indicator_vote_side_h5_c0.6_v2 | swing | 1d | G2 + 사이드 투표 2개 | 동일 | 6지표 + funding_crowding + oi_confirm | 마켓 BTC+SOL, 사이드 데이터 주입 필요 |
| 15 | swing_indicator_vote_side_h10_c0.6_v2 | swing | 1d | G2 + 사이드 (h=10) | 동일 | 동일 | — |
| 16 | daytrade_indicator_vote_h16_c0.6_v1 | daytrade | 15m | G2 DayTradeVote 기본 (h=16) | p<0.52 + 2.5ATR + 48봉(12h) 시간청산 | 6지표 × score_scale 0.1, vol 96/960 | 4마켓, warmup 1157 |
| 17 | daytrade_indicator_vote_side_h16_c0.6_v1 | daytrade | 15m | G2 + 사이드 투표 | 동일 | + funding_crowding, oi_confirm | 사이드 주입 필요 |
| 18 | daytrade_indicator_vote_h48_c0.6_v1 | daytrade | 15m | G2 (h=48=12h) | 동일 | 동일 | warmup 1189 |
| 19 | daytrade_indicator_vote_side_h48_c0.6_v1 | daytrade | 15m | G2 + 사이드 (h=48) | 동일 | 동일 | — |
| 20 | daytrade_indicator_vote_h16_c0.6_t2.5_v1 | daytrade | 15m | G2 (h=16) | + **트레일 2.5ATR** 즉시 활성 | 동일 | 소유자 아이디어 2026-10-09, 스크리닝 전용 |
| 21 | daytrade_indicator_vote_h48_c0.6_t2.5_v1 | daytrade | 15m | G2 (h=48) | + 트레일 2.5ATR | 동일 | — |
| 22 | daytrade_indicator_vote_h16_c0.6_t4.5_v1 | daytrade | 15m | G2 (h=16) | + 트레일 4.5ATR(진입부터) | 동일 | ADR-0053 |
| 23 | daytrade_indicator_vote_h16_c0.6_t1.5a1.5_v1 | daytrade | 15m | G2 (h=16) | + 트레일 1.5ATR(±1.5ATR 진행 후 활성=브레이크이븐 시작) | 동일 | ADR-0053 |
| 24 | daytrade_indicator_vote_h48_c0.6_t4.5_v1 | daytrade | 15m | G2 (h=48) | + 트레일 4.5ATR | 동일 | — |
| 25 | daytrade_indicator_vote_h48_c0.6_t1.5a1.5_v1 | daytrade | 15m | G2 (h=48) | + 트레일 1.5a1.5 | 동일 | — |
| 26 | daytrade_flow_vote_w16_z1_v1 | daytrade | 15m | G3 (W=16=4h) | z<0 교차 청산 + 2.5ATR + 12h 시간청산 | 테이커 플로우 z480, ATR14, vol 96/960 | ADR-0064 |
| 27 | daytrade_flow_vote_vpin_fund_w16_z1_v1 | daytrade | 15m | G3 + VPIN·펀딩 게이트 | 동일 | + VPIN48 근사, 펀딩 크라우딩 | 게이트는 진입만 차단 |
| 28 | daytrade_flow_vote_w48_z1_v1 | daytrade | 15m | G3 (W=48=12h) | 동일(26) | 동일 | — |
| 29 | daytrade_flow_vote_vpin_fund_w48_z1_v1 | daytrade | 15m | G3 + 게이트 (W=48) | 동일 | 동일(27) | — |
| 30 | daytrade_indicator_vote_h16_c0.6_m24_v1 | daytrade | 15m | G2 (h=16) | **max_hold 24봉(6h)** 시간청산 | 동일(16) | ADR-0071 그리드 |
| 31 | daytrade_indicator_vote_h16_c0.6_m96_v1 | daytrade | 15m | G2 (h=16) | max_hold 96봉(24h) | 동일 | ADR-0071 |
| 32 | daytrade_indicator_vote_h16_c0.55_v1 | daytrade | 15m | G2, **enter_confidence 0.55**(문턱 하향→진입 빈도↑) | 동일(16) | 동일 | ADR-0071 |
| 33 | daytrade_indicator_vote_h16_c0.65_v1 | daytrade | 15m | G2, enter 0.65(문턱 상향) | 동일 | 동일 | ADR-0071 |
| 34 | daytrade_indicator_vote_h16_c0.6_s1.5_v1 | daytrade | 15m | G2 (h=16) | **스톱 1.5ATR**(사이징도 축소) | 동일 | ADR-0071 |
| 35 | daytrade_indicator_vote_h16_c0.6_s3.5_v1 | daytrade | 15m | G2 (h=16) | 스톱 3.5ATR | 동일 | ADR-0071 |
| 36 | daytrade_indicator_vote_h16_c0.6_xtod_seasonal_v1 | daytrade | 15m | G2 + 후보투표 **tod_seasonal** 추가(7표) | 동일(16) | + 4일 전 같은 15분 슬롯 수익률 패턴(candidate_indicators.py:145-147) | ADR-0070, warmup 1638 |
| 37 | daytrade_indicator_vote_h16_c0.6_xac_dir_v1 | daytrade | 15m | G2 + **ac_dir** 추가 | 동일 | + 480봉 자기상관 × 4h 방향(:130-134) | ADR-0070 |
| 38 | daytrade_indicator_vote_h16_c0.6_xtod_seasonal+ac_dir_v1 | daytrade | 15m | G2 + tod_seasonal + ac_dir | 동일 | 위 2개 | — |
| 39 | daytrade_indicator_vote_h16_c0.6_xac_dir_dbollinger_b_v1 | daytrade | 15m | G2 + ac_dir − **bollinger_b 제거**(6표) | 동일 | 패널 편집 | ADR-0070 |
| 40 | daytrade_indicator_vote_h48_c0.6_xac_dir_v1 | daytrade | 15m | G2 (h=48) + ac_dir | 동일(18) | 동일(37) | — |
| 41 | daytrade_indicator_vote_h48_c0.6_xac_dir+vr_dir_v1 | daytrade | 15m | G2 + ac_dir + **vr_dir**(8표) | 동일 | + 분산비율 × 방향(:135-138) | — |
| 42 | daytrade_indicator_vote_h16_c0.6_xtod_seasonal+ac_dir_ddonchian_pos+roc+rsi+bollinger_b_v1 | daytrade | 15m | G2, 패널 P1 = ema_trend+obv_slope+tod_seasonal+ac_dir(4표) | 동일 | 역할 중복 제거 패널 | ADR-0070 P1, \|corr\|≤0.5 |
| 43 | ..._h48_... (동일 구성) | daytrade | 15m | 동일(42) | 동일(18) | 동일 | — |
| 44 | ..._xtod_seasonal+ac_dir+skew96+rv_ratio_d(...5개 제거)_v1 | daytrade | 15m | G2, 패널 P5 = ema_trend+tod_seasonal+ac_dir+skew96+rv_ratio(5표) | 동일 | + 96봉 왜도, 변동성 비율(×방향) | ADR-0070 P5 |
| 45 | ..._h48_... (동일 구성) | daytrade | 15m | 동일(44) | 동일(18) | 동일 | — |
| 46 | ..._xtod_seasonal+vr_dir+rv_ratio_d(...4개 제거)_v1 | daytrade | 15m | G2, 패널 P6 = ema_trend+obv_slope+tod_seasonal+vr_dir+rv_ratio | 동일 | 위 조합 | ADR-0070 P6 |
| 47 | ..._h48_... (동일 구성) | daytrade | 15m | 동일(46) | 동일(18) | 동일 | — |
| 48 | ..._xtod_seasonal+ac_dir+rv_ratio_dema_trend+donchian_pos+roc+rsi_v1 | daytrade | 15m | G2, 패널 P7 = bollinger_b+obv_slope+tod_seasonal+ac_dir+rv_ratio | 동일 | 위 조합 | ADR-0070 P7 |
| 49 | ..._h48_... (동일 구성) | daytrade | 15m | 동일(48) | 동일(18) | 동일 | — |
| 50 | daytrade_indicator_vote_h48_c0.6_rss1_v1 | daytrade | 15m | G2 + **레짐 스위치 s1**: TREND_*=follow, RANGE/LOW_VOL=fade, HIGH_VOL/UNDEF=flat | fade 시 반전된 p 기준 청산 | + 레짐 분류기 | ADR-0079 |
| 51 | daytrade_indicator_vote_h48_c0.6_rss2_v1 | daytrade | 15m | G2 + 스위치 s2: LOW_VOL도 flat(양방향 추세만) | 동일 | 동일 | ADR-0079 |
| 52 | daytrade_indicator_vote_h16_c0.6_ghar80_v1 | daytrade | 15m | G2 + **게이트 har80**(HAR 예측 상위 20% 차단) | 동일(16) | + HAR 변동성 예측(고정 계수) | ADR-0083, warmup 5389 |
| 53 | daytrade_indicator_vote_h16_c0.6_gtod_v1 | daytrade | 15m | G2 + 게이트 tod(UTC 11-14시 마감분 차단) | 동일 | UTC 시각 | ADR-0083 |
| 54 | daytrade_indicator_vote_h16_c0.6_grand20_v1 | daytrade | 15m | G2 + 게이트 rand20(대조군: 무작위 20% 차단) | 동일 | 없음(의사난수) | ADR-0083 |
| 55 | daytrade_indicator_vote_h48_c0.6_ghar80_v1 | daytrade | 15m | 동일(52), h=48 | 동일(18) | 동일 | warmup 5421 |
| 56 | daytrade_indicator_vote_h48_c0.6_gtod_v1 | daytrade | 15m | 동일(53), h=48 | 동일 | 동일 | — |
| 57 | daytrade_indicator_vote_h48_c0.6_grand20_v1 | daytrade | 15m | 동일(54), h=48 | 동일 | 동일 | — |

(행 42-49의 strategy_id는 가독성을 위해 공통 접두 `daytrade_indicator_vote_h16/h48_c0.6_`를 생략해 표기 — 전체 ID는 configs/strategies.json 참조.)

---

## 3. 코드상 확인된 공통 구조 (요약)

1. **신호 계약 / 무상태 전략** — 전략은 `signal(history, context) -> Signal` 순수함수이며 포지션 상태는 전혀 갖지 않음(엔진이 보유). 진입(entry ±1)·청산 플래그(exit_long/exit_short)·스톱거리만 반환. 엔진 공통 의미론: "flat + entry≠0 → 진입, long + exit_long/반대entry → 청산(한 스텝에서 반전 없음), short + exit_short/반대entry → 청산" (`strategies/base.py:14-24`). 진입 신호는 **양의 유한 stop_distance가 없으면 예외로 금지**(fail-closed, `base.py:68-70`).
2. **레짐 게이트는 risk engine이 최종 집행** — 레짐 UNDEFINED면 어느 전략이든 risk engine이 진입을 거절(`risk/engine.py:222-223`), 레짐 규칙 자체는 고정 판별식(`features/regime.py:88-113`).
3. **공통 리스크 프로파일** — 57건 전부 `risk_profile: "default"`. RiskConfig 기본: risk_per_trade 0.5%, max_leverage 2.0, max_daily_loss 3%, max_drawdown 15%, 스톱 기준 사이징 `수량 = equity×risk% / stop_distance`(`risk/engine.py:81-94, 279-282`). p_long 기반 신뢰도가 있으면 risk_per_trade_max 선형 스케일링 옵션(`risk/engine.py:97-99, 125-129`, 신뢰도는 `base.py:102-107 entry_confidence`가 Signal.features["p_long"]에서 추출 — 즉 G2/G3 계열만 스케일링 대상).
4. **allow_short 처리** — 전략 레벨 플래그일 뿐 엔진에 개념 없음. G2(IndicatorVote :288, FlowVote :200)와 TrendEmaAtr/BreakoutVolume에서 분기(swing.py:79, 135). BollingerReversion/스캘프 4종은 allow_short 파라미터 자체가 없고 코드상 양방향 진입. SmaTrendFilter/DonchianTrend/TrendPullback은 **숏 분기 코드가 아예 없는 롱 전용**. 등록된 57건 중 allow_short=true는 47건, 나머지 10건은 파라미터 미기재(#3-11 등, 실측).
5. **청산 우선순위(이벤트 백테스트 엔진)** — ① 이전 바 신호청산을 다음 시가에서 체결(`event_engine.py:471-474`) ② 지정가/시장가 진입 체결(`:476-522`) ③ 바 내 보호청산: **청산(liquidation) > 갭스톱 > 스톱 > TP** 순, 같은 바에 스톱·TP 동시 닿으면 스톱 우선 가정(`:529-544`, 가정 명시 `:314`) ④ 트레일링 스톱 갱신(최고가 − trail_distance, activation 미달 시 미작동, `:556-561`) ⑤ 시간청산(max_hold) → 신호청산 순 판정, 체결은 latency 1바 뒤(`:584-588`, `latency_bars=1` `:94`). 페이퍼 엔진도 동일 개념(max_hold `paper/engine.py:495-501`, 스톱/TP/트레일 `:696-733`).
6. **엔진 소비 파라미터** — `max_hold_bars`, `max_entries_per_day`, `quality_window_bars`는 전략 속성을 getattr로 읽는 엔진 규칙(`event_engine.py:303-305`). 백테스트는 전략 속성을 쓰지만 페이퍼의 데이터품질 게이트는 config값(`quality_window` 기본 50, `paper/engine.py:80, 547`)을 써서 이 값만 백테스트(192)·페이퍼(50) 간 불일치 — 개선 후보(추정: 의도 차이인지 확인 필요).
7. **warmup 규칙** — 전략별 `warmup` 프로퍼티가 있고, 레지스트리 `build()`가 등록값과 일치 검증(`registry.py:92-93`). 전형값: 레짐 warmup 201(=max(200,57,121)+1, `regime.py:56-58`)이 클래식 스윙/스캘프의 하한, G2는 캘리브레이션 필요분(140+lookback+horizon+1), G3는 480+2W+481, har80 게이트만 +4232.
8. **fail-closed 관례** — 지표 1개라도 None이면 신호 없음(예: `indicator_vote.py:251-260`, `swing.py:72-73`), 사이드 투표/게이트 데이터 누락도 진입 없음(`indicator_vote.py:174-175`, `flow_vote.py:212-213`).
9. **비용·체결 가정(백테스트)** — taker 0.05%/maker 0.02%, half_spread 1bp, 변동성 스케일 임팩트, 최대 참여 5%, 진입 시가 체결 + 1바 지연(`event_engine.py:80-113`); 캔들 단순 비용모델은 `backtest/costs.py:88-117`.
10. **로직 중복 없음 확인** — 같은 DayTradeVote 38건은 전부 하나의 `signal()` 코드 경로를 공유하고 파라미터만 다름(데이터클래스 동등성 기반 strategy_id, `indicator_vote.py:116-129`). 패널/게이트/스위치 변형도 동일 경로의 분기(`:190-206, 268-285`).

## 4. 레지스트리 외 연구 전용 모듈 (참고, 등록 0건)

| 파일 | 내용 | 등록 불가 사유 |
|---|---|---|
| grid.py | 그리드 매매 시뮬레이터 | FACTORIES에 없음, 별도 grid_engine 사용 |
| funding_carry.py / basis_carry.py | 펀딩/베이시스 캐리 연구 | DEAD_CANDIDATE 패턴(H-0011/12 계열), 워크로그 Task 6 참조 |
| cross_sectional.py / adaptive_ensemble.py / baselines.py | 크로스섹션/앙상블/베이스라인 연구용 | FACTORIES에 없음 |

## 5. 미해결/확인 필요 사항

- 페이퍼 데이터품질 윈도우(config 50봉) vs 백테스트(전략 quality_window_bars 192봉) 불일치 — §3.6. 의도적 차이인지 확인 필요(추정: ADR-0081에서 백테스트 쪽만 정비).
- `use_side_data=True` 4건(#14,15,17,19)은 러너(scripts/run_screening.py, run_validation.py)가 펀딩/OI를 주입할 때만 작동 — 페이퍼/라이브 경로에는 주입 배선이 없어 현재 그대로 구동 시 전 시간대 flat(fail-closed).

## 6. 근거 파일 목록

- `src/cointrader/strategies/registry.py` (14 FACTORIES, build 검증)
- `src/cointrader/strategies/indicator_vote.py`, `daytrade.py`, `flow_vote.py`, `swing.py`, `scalp.py`, `base.py`
- `src/cointrader/features/indicator_votes.py` (패널/플랫/결합), `candidate_indicators.py`(후보 투표), `side_indicators.py`(펀딩/OI/vol gate), `regime.py`, `har_vol.py`, `indicators.py`
- `src/cointrader/risk/engine.py` (진입 게이트·사이징)
- `src/cointrader/backtest/event_engine.py` (청산 우선순위/엔진 소비 파라미터), `costs.py`
- `src/cointrader/paper/engine.py` (페이퍼 청산/품질 게이트)
- `configs/strategies.json` (57건 파라미터·warmup·마켓 실측)
- `scripts/run_screening.py`, `scripts/run_validation.py` (사이드 데이터 주입)
