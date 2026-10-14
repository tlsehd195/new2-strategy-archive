# NEW2 전략 연구 보고서 v5 (v4 대체 — 공백 3건 보강 완료판) — 2026-10-12

작성: Z.ai Code · 대상 `github.com/tlsehd195/NEW2` · 방법: 직접 클론·직접 실행·증거 첨부
**작업 등급**: 조사완료/구현완료/테스트완료/백테스트완료/검증완료/미완료 — 각 섹션에 표기

**v4→v5 변경점**: ① §3 전략 전수 인벤토리를 실제 코드 기반 상세판으로 완성(진입/청산/지표, 파일:줄번호 근거) ② §9.5 P1 교차검증 신설 — 저장소 엔진 `CandleCostModel`(costs.py:104-117) 직접 적용, 정액 관행이 과소평가였음을 실측 ③ §6 웹 리서치 2차 — 배제목록 16계열과 비겹침 검증한 신규 후보 9건 발굴(데이터 아카이브 실측 포함)

## 1. Executive Summary

| 항목 | 값 |
|---|---|
| 조사한 전략 수 | 레지스트리 등록 **57건**(swing 11/scalp 4/daytrade 42) + 레지스트리 외 연구 전용 6계열 + 사전등록 가설 20건 + ADR 85건 계열 지도. **57건 전수 상세 인벤토리 완성**(진입/청산/지표, `NEW2_run/inventory_detail.md`, 파일:줄번호 근거) |
| 전략 클래스 실체 | 57 ID = **14개 클래스** × 파라미터 변형(registry.py:35-50 FACTORIES 실측). daytrade 42 = DayTradeVote 38 + FlowVote 4 |
| 중복 제거 후 신규 후보 | P1 펀딩극단(**교차검증까지 완료, FAIL 재확증**), N2 지정가(대기), P3 청산캐스케이드(BLOCKED_DATA) + **웹 리서치 2차 신규 원리 후보 9건**(상위: C1 베이시스 모멘텀, C2 오더플로우 불균형, C3 코인베이스 프리미엄) |
| 실제 구현 | P1 진단 스크립트 2종 + 교차검증 스크립트 1종 + 사전등록 문서 [구현완료] |
| 실제 백테스트 | **P1 실데이터 130파일 실행 + 엔진 비용모델 교차검증 재실행** [백테스트완료][검증완료] |
| validation 완료 | 0건 (PASS 없음 → 결선 단계 없음) |
| 유망 전략 | 0건 (방향 엣지) / 1건 (구조적: N2 — **엔진 비용 실측으로 기대치 상향**) |
| 기각 | P1 (이번 세션 신규, 교차검증 포함) + 기존 22가설·SMC·김치·선행후행·비대칭 등 전 계열 |
| 데이터 부족 판단 불가 | 1건 (P3 청산 — 수집 중) |
| **핵심 신규 발견** | **엔진 비용모델 기준 $10k 소액 주문도 왕복 31~41bp**(정액 12~20bp 관행은 과소평가 소지) — N2 지정가 진입의 가치를 실측으로 뒷받침 |

## 2. Repository Audit [조사완료]

- branch `main`, HEAD `8ec6371`, **working tree 클린**(`git status` 빈 출력 실측)
- 파일 623개, src Python 150개, tests 68개, workflows **25개**(실측 목록: ci, screening, signal_validation, grid_validation, xsec_validation, swing_study, event_backtest, five_min_diagnosis, flow_diagnosis, edge_diagnosis, signal_candidates_diag, indicator_candidates, vote_frequency_diag, volume_profile_diag, positioning_diag, direction_ab_diag, asym_structure_diag, smc_diag, vol_forecast_diag, vol_gate_screen, funding_carry_study, basis_carry_study, carry_paper_sim, data_coverage, oci_create_vm)
- 패키지 구조: accounting, analytics, backtest, carry, data, evolution, execution, features, funding, journal, learning, live, ml, monitoring, notifications, paper, research, risk, strategies, tax, validation (22 서브패키지)
- 장부: preregistration 20행(H-0022 최신, 2026-10-02) / screening 13행(ADR-0089) / candidate_status 42행(BACKTESTED 21·REJECTED 21) / **`scripts/check_ledgers.py` 직접 실행 → `0 error(s), 0 warning(s)` exit 0**
- 예산: 최근 3건 등록(H-0018 09-29, H-0021 09-29, H-0022 10-02 UTC) → **2026-10-29 11:32 KST 1슬롯**, 11-01 3슬롯 해제
- DEAD 패턴(`src/cointrader/research/hypotheses.py:39`): `ts_momentum_*`, `funding_carry_*`, `basis_carry_*`

## 3. Existing Strategy Inventory [조사완료 — configs/strategies.json 57건 전수 파싱]

레지스트리 특성(실제 코드, `strategies/registry.py` 문서 주석 확인): 등록 없이는 페이퍼·라이브 어디서도 실행 불가, ID가 코드와 불일치하면 레지스트리가 거부(무음 드리프트 방지), lifecycle 상태는 후보 원장만 관리.

**[swing] 11건** — 1h: TrendEmaAtr(20/50,ATR14,stop2.5·trail3.0), BreakoutVolume(20,10), BollingerReversion(20,2), RegimeHybrid / 1d: SmaTrend(100), Donchian(55/20), TrendPullback(100/20), IndicatorVote h5·h10(±side 버전) — 시장 BTC/ETH/SOL
**[scalp] 4건** — 1m: VwapReversion(30,2), ShortTermMeanReversion(20,3), MicrostructureMomentum / 5m: RangeBreakoutVolume(30,2) — 시장 BTC/ETH/SOL
**[daytrade] 42건 — 15m 단타군(본 연구 대상)**:
- indicator_vote 본체: h16·h48 × c0.6 (기본), side 투표 버전, t2.5/t4.5(문턱), t1.5a1.5, s1.5/s3.5(손절), m24/m96, c0.55/0.65 — BTC/ETH/SOL
- flow_vote: w16/w48 × z1, vpin_fund 버전 — BTC/ETH
- x-패널 변형(ADR-0070 후속): tod_seasonal, ac_dir, ac_dir+vr_dir, skew96+rv_ratio 조합 등 14건
- rss1/rss2, 게이트 6건(ghar80/gtod/grand20 × h16/h48)

**레지스트리에 없는 연구 전용 전략(§4 요구 항목)**: grid.py, funding_carry.py, basis_carry.py, cross_sectional.py, adaptive_ensemble.py, baselines.py — 스타디 엔진(grid/xsec/carry 엔진)으로만 실행, lifecycle 원장으로 상태 관리됨.

**전략별 상태(원장·ADR 기준)**: daytrade 42건 전부 BACKTESTED→REJECTED 또는 스크리닝 단계 폐쇄. 유일하게 P5/P7 h48(+0.29%/+0.52% 폴드평균)이 DSR 0.12~0.18<0.95로 승격 불가. swing 11건: 1d 사이드투표 H-0022까지 전부 REJECTED. scalp 4건: 1m/5m은 비용 미달로 폐쇄(ADR-0064~66).

**[v5 보강] 전수 상세 인벤토리 완성 [조사완료]**: 실제 구현 코드에서 진입/청산/지표를 전부 추출한 상세판을 `NEW2_run/inventory_detail.md`에 작성. 핵심 실측:
- 57 ID = 14개 클래스 × 파라미터 변형(registry.py:35-50, 85-93 — ID 불일치 시 예외 = 무음 드리프트 방지)
- **IndicatorVote/DayTradeVote(42건 중 38건의 기반)**: 6지표 투표(ema_trend/donchian_pos/roc/rsi/bollinger_b/obv_slope, features/indicator_votes.py:45) → 플랫 캘리브레이션 p_long, 진입 `p_long≥0.60 & 동의도≥60%`(indicator_vote.py:286-289), 청산 = 히스테리시스 반대신호(p<0.52, :262) + 2.5×ATR 스톱 + 48봉 시간청산(12h)
- **FlowVote(4건)**: 테이커 매수불균형 z≥±1.0 진입(flow_vote.py:200), z가 0 회귀 시 청산(:197) + 2.5×ATR + 12h 시간청산
- 신규 발견 2건: `use_side_data` 4건은 러너 주입 없으면 fail-closed flat / 페이퍼 품질게이트 윈도우(config 50) vs 백테스트(전략 192) 불일치
- SmaTrendFilter/DonchianTrend/TrendPullback은 롱 전용(숏 분기 코드 부재)

## 4. 기존 가설·실패 기록 [조사완료]

사전등록 20건(H-0001~0022) **전부 종결·기각** — H-0022(1d SOL 사이드 투표) 2026-10-02 PBO 0.9/DSR 0.60/TEST 초과수익 -0.70%로 REJECTED(`research/candidate_status.jsonl` 실측). 실패 유형별: momentum grid(PBO 초과), funding/basis carry(연 1.6~1.9%로 사망), DSR 미달(H-0018), PBO 초과(H-0021). **교훈**: ①"덜 거래해서 덜 잃는" 효과와 진짜 엣지 구분(거래당 수익 기준) ②2021~22 국면 수익이 최근 국면에서 재현 안 됨(캐리·모멘텀 공통) ③비용 12~20bp가 모든 15m 방향 신호의 벽.

## 5. 연구 파이프라인 실측 [조사완료 + **직접 실행**]

실제 실행한 명령과 결과:
```
$ python3 scripts/check_ledgers.py            → 0 error(s), 0 warning(s), exit 0
$ python3 scripts/run_screening.py --help     → CLI 전체 문서화
$ python3 scripts/run_validation.py --help    → CLI 전체 문서화
$ python3 scripts/run_backtest.py --help      → CLI 전체 문서화
```
- **screening**: `run_screening.py --family daytrade --symbol BTCUSDT --timeframe 15m --start --end --strategies ... --out reports/screen.json` — TEST 미로딩(ADR-0051), screening.jsonl 자동 기록, 후보 수는 결선 DSR 시도에 합산. CI(screening.yml)의 실제 명령 확인: `pip install -e ".[dev]"` 후 동일 스크립트
- **결선**: `run_validation.py --hypothesis-id ... --statement ...` — 사전등록된 3건 이하 결선 → 1회 held-out TEST → 즉시 잠금
- **백테스트**: `run_backtest.py --strategy <id> --symbol --start --end [--entry-order {market,limit}] [--no-funding]` — Binance USD-M 아카이브 기반 이벤트 드리븐. **`--entry-order limit` 옵션 존재 확인 → N2 지정가 진입 후보가 엔진 차원에서 이미 부분 지원됨**
- 검증 인프라 존재 확인(validation/ 20파일): pbo_dsr.py, walk_forward.py, locked_windows.py, lookahead.py, time_alignment.py, reality_check_spa.py, preregistration.py, trial_ledger.py, integrity.py
- 비용 모델(`backtest/costs.py:88` CandleCostModel): `fee_rate + half_spread + impact_coef*sqrt(order/bar_value)` — 진단 계열 관행은 왕복 정액 12/16/20bp(ADR 공통)

## 6~7. 신규 후보 발굴 + 중복성 보고서 [조사완료]

**D0(실질 동일)·D1(파라미터 변형)으로 배제한 검토 후보**: 시간대/세션 방향(ADR-0070 tod_seasonal), 자기상관·분산비율(ac_dir/vr), OI 급변동(oi_dir/oi_chg), 15m 내부 되돌림(ADR-0065·0089 sweep/FVG), 상위TF+15m(ts_momentum_* 사망 패턴), SMC 변형(0089), 거래량 돌파 변형(ADR-0077) — 7계열
**D4(독립 가설) 신규 후보 3건**:

| ID | 전략 | 중복 등급 | 근거 | 우선순위 | 상태 |
|---|---|---|---|---|---|
| P1 | fr_extreme 펀딩 극단 역발상 | D4 | 캐리(dead)·투표구성요소(ADR-0025 선형)와 다른 독립 이벤트+|z| 문턱+역발상. ADR 미측정 | P0 | **백테스트완료 → FAIL 기각** |
| N2 | 지정가 진입 실행 전환 | D4(실행 계층) | 비용 바닥 12~20→6~10bp, ADR-0065 스스로 제안, 엔진에 limit 옵션 존재(실측) | P0 | **사용자 결정 대기** |
| P3 | liq_cascade 청산 캐스케이드 | D4(신규 데이터) | forceOrder 과거 아카이브 부재 → ADR-0085 수집 중 | P2 | **BLOCKED_DATA** (90일 후 측정, 사전 규격 고정 완료) |

**외부 자료 조사 [조사완료, 출처 기록]**: 검색 4건 실행(`/home/z/my-project/NEW2_run/research/ws*.json`). 유효 출처 2건: ①펀딩 극단 역발상 관행 — crypto.com market-updates(2026-04, "펀딩 7일평균 -0.008% 최저"), Binance Square(2025-01, "음수 펀딩=비관 신호 해석"), CoinGlass/gate.com(2026-06) — **실무 관행 확인용이며 검증 아님. 본 세션 P1 실측으로 비용 후 방향 엣지 없음 확인** ②MDPI JRFM "Lagged Momentum and Reversal"(암호화폐 거래일 세션 분할 연구) — 세션 계열은 ADR-0070 tod_seasonal로 이미 측정됨을 재확인. SSRN Zarattini 2024(미국 주식 데이트레이딩)은 대상 아님. 나머지 2검색(maker 실행·청산 캐스케이드)은 검색 엔진 노이즈로 유의미한 출처 미확보 — **미확보 사실 그대로 기록**

**[v5 보강] 웹 리서치 2차 [조사완료 + 데이터 아카이브 실측]**: 16회 검색(성공 8/노이즈 8 — 정직 기록) + arXiv API 6쿼리 + **data.binance.vision HTTP HEAD/GET·S3 listing 응답코드 실측**. 배제목록 16계열과 1:1 비겹침 판정을 거친 **신규 원리 후보 9건** (`NEW2_run/research/web_research_round2.md`, 원문 r2_ws1~16.json):

| 순위 | 원리 | 핵심 출처 | 데이터 실측 | 비겹침 판정 |
|---|---|---|---|---|
| C1 | 스팟-퍼페 **베이시스 모멘텀**(프리미엄 수준 아닌 변화율→방향) | Boons & Prado, JF 2019 | premiumIndexKlines 1m 2019-12~ **200 OK** | 캐리·펀딩극단과 메커니즘 상이 — 통과(패밀리 상관 검사 조건) |
| C2 | **오더플로우·호가 잔량 불균형**(bookTicker/5단 깊이/CVD) | Cartea 2018, hftbacktest | bookDepth 2023-01~ **200 OK**, bookTicker/aggTrades OK | 통과(단 저장소 microstructure.py 기구현 존재 → "미연결 채널 와이어링" 성격, 비용벽 최고위험) |
| C3 | **코인베이스 프리미엄**(미국 현물 수요 프록시) | CryptoQuant/Coinglass 정의 | Coinbase 2021-06 캔들 실측 OK | 김치(배제13)와 패밀리 인접, 베뉴·메커니즘 상이 — 통과 |
| 4~9 | 프리미엄 인덱스 인트라-인터벌 / 분기선물 기간구조 슬로프 / 청산 캐스케이드(기존 P3 사전등록 존재 — 확인용) / 퍼페-퍼페 교차거래소 스프레드 / ETF 일일 순유입 필터 / 옵션 25δ 스크우·DVOL | 각 § 참조 | 분기선물·Bybit·OKX·Deribit DVOL 실측 OK, Farside 403 | 각 항목별 판정 문서 참조 |

제외(정직 기록): 온체인 순유입(검색 전면 노이즈+유료), 펀딩 예측(배제 패밀리), VWAP/돈치안 변형(배제 15·8 경계), 매크로 이벤트 윈도우(배제 1 인접)

## 8~9. P1 백테스트 방법론·결과 [구현완료][테스트완료][백테스트완료]

- **사전등록(실행 전 고정)**: `/home/z/my-project/NEW2_run/PREREG_local_2026-10-12.md` — 가설·버킷·지평·비용·Holm 24셀·PASS 규칙 포함(§10 형식의 가설 정의서 포함: Market Hypothesis/Entry/Exit/Falsification 등)
- **명령**: `python3 fr_extreme_diag.py` / 데이터: data.binance.vision 15m kline 65 + fundingRate 65 (BTC/ETH 2021-04~2026-04)
- **품질**: 구간별 결측 봉 0, 중복 0, 펀딩 결측 0 (예: BTC span1 45,312/45,312)
- **거래 횟수**: 셀당 n 134~297(버킷·지평별), 총 신호 5,378 정산 중 B80/B90 통과분
- **비용**: 왕복 12/16/20bp 정액 적용(순12/순16/순20 모두 산출). 펀딩 자체 미반영(보유 ≤12h, 8h당 ±1bp — 한계 명시)
- **결과(순16bp)**: BTC 2021-22: B80 h48 +42.5bp(유효n=4, p_holm=1.0) 외 음수 / BTC 2023-24: 전 셀 음수 / ETH 2021-22: 전 셀 음수 / ETH 2024-26: 전 셀 음수. **Holm p<0.05 = 0/24 → 사전 고정 규칙상 FAIL. 추종 방향 통제도 전 셀 음수, 무작위 100시드 대조 우위 없음**
- **보조 통계**: z↔선행수익 선형상관 |r|≤0.067(최근 국면 p>0.38), 정산 직후 4봉 드리프트 국간 부호 불일치(-6.67~+2.14bp)
- **MDD/승률/손익비/PF/Sharpe**: 산출 안 함 — Phase A 등급은 이벤트별 선행수익 측정이고, FAIL이 확정된 이상 포지션 단위 백테스트는 시도 수만 늘림(§20 파라미터 제한 준수). 이 사실을 N/A로 명시

## 9.5 [v5 신설] P1 교차검증 — 엔진 CandleCostModel 직접 적용 [검증완료]

§8의 P1 결과를 **독립 구현 비용(정액 12/16/20bp 왕복)이 아니라 저장소 엔진의 비용모델**(`src/cointrader/backtest/costs.py:104-117`, `fee + half_spread + 10%·sqrt(participation)` per side)으로 재계산해 교차검증. 신호/거래 생성은 사전등록 규격과 동일 로직, 명령/결과: `python3 fr_crosscheck_engine_cost.py` → `results/fr_crosscheck_results.json/md`.

| 시나리오 | 주문금액 | 왕복 비용 실측 | net>0 셀(사전규칙) | Holm p<0.05 | 해석 |
|---|---|---|---|---|---|
| o10k | $10k | **31~41bp**(fee 10+spread 4+impact 17~27) | **0/24** | 3셀 — **전부 음수 방향 유의미**(-31~-37bp, t -3.3~-5.0) | 정액 16bp보다 2배 비관적 → FAIL 강화 |
| o100k | $100k | **69~99bp** | **0/24** | 8셀 — **전부 음수 방향 유의미**(-61~-102bp) | 대형 주문은 집행 비용이 원리 소멸 수준 |

핵심 발견:
1. **정액 12~20bp 왕복 관행은 과소평가 소지** — $10k 주문조차 15m 바 거래대금의 1~3%를 차지해 impact가 지배(모델 기본 계수 0.1은 costs.py:15-16의 "보수적 플레이스홀더" 명시 — 실측 캘리브레이션 필요)
2. Holm 통과 셀은 o10k/o100k 모두 **손실 방향 유의미**뿐 — 사전등록 판정 규칙(net>0 요구)상 PASS 0 → **P1 폐기 권고 재확증**(독립 비용 가정과 무관하게 로버스트)
3. fail-closed(participation>5%) 제외 0건 — 선택편향 아티팩트 아님
4. **N2 지정가 진입의 기대치를 실측으로 뒷받침**: taker fee 5bp + impact 17~27bp를 maker 수수료(~2bp, rebate 가능)로 대체하면 비용 바닥이 구조적으로 낮아짐 — 진입 체결률 손실과의 트레이드오프 측정 필요

## 10. Walk-Forward / PBO / DSR [미완료 — 사유 명시]

P1이 Phase A에서 FAIL → 결선(run_validation.py) 진입 조건 미충족 → WF/PBO/DSR 미실행. **미완료의 이유는 우회가 아니라 파이프라인의 정상 분기**(실패 후보는 결선에 못 들어감). 기존 도구 구현은 존재 확인(validation/pbo_dsr.py 등) 및 ADR-0083 실행 이력(screening.jsonl 실측)으로 검증됨.

## 11. OOS/Locked 보호 [준수 확인]

locked/reserved 구간 일절 미사용(P1 측정 구간 = ADR-0088/0089와 동일한 잠금 해제 구간). 예약 윈도우(BTC 2024-03-25~06-18, ETH 2026-04-28~08-31) 미접촉. `check_ledgers.py` 0 오류 재확인. 저장소 장부·설정 수정 없음.

## 12. 실패 원인 분석 (P1)

- **거래 비용 과다**: 펀딩 극단 이벤트의 평균 반동(총 8~60bp)이 비용 12~20bp를 극복하지 못함(특히 h4)
- **국면 의존**: 2021~22 강세장에서만 역발상 힌트(h48, 유효n 3~4), 최근 2국면 전멸 — 캐리 사망(ADR-0067~69)과 동일한 "최근 국면 소멸" 패턴
- **표본 부족**: h48 셀 유효n 3~6 — §16 표본 기준 미달, 후보화 불가
- **가설 자체**: 선형 상관 |r|<0.07 → 극단 버킷이 아니라 원리 자체에 방향 정보 부족
- 재설계 금지 준수: 결과를 본 뒤 임계값·지평 재조정 안 함(사전등록 위반 방지)
- **[v5 추가] 비용 구조 문제**: 엔진 비용모델 교차검증(§9.5)에서 taker 시장가 체결 기준 $10k 주문도 왕복 31~41bp — P1의 실패는 방향 엣지 부재(1차)와 집행 비용(2차)의 합성. 방향 엣지 부재 자체는 |r|≤0.067로 독립 확인됨

## 13. 최종 결과 표 (§33 형식 — N/A는 사유 기록)

| Rank | Strategy ID | Strategy | Novelty | Symbols | Trades(셀당) | Net Return | Max DD | PF | Sharpe | PBO | DSR | WF | OOS | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| - | fr_extreme(B80/B90,h4/16/48) | 펀딩 극단 역발상 | D4 | BTC,ETH | 134~297 | 셀별 -35~+42bp(순16) | N/A¹ | N/A¹ | N/A¹ | N/A² | N/A² | N/A² | 미사용³ | **REJECTED** |
| 1 | N2 maker_entry | 지정가 진입 실행 전환 | D4 | BTC,ETH | N/A⁴ | 기대: 비용 바닥 -6~-10bp(**§9.5 실측으로 기대치 상향 — taker impact 17~27bp 회피**) | N/A⁴ | N/A⁴ | N/A⁴ | N/A⁴ | N/A⁴ | N/A⁴ | 미사용³ | **READY(사용자 결정 대기)** |
| 2 | liq_cascade | 청산 캐스케이드 되돌림 | D4 | BTC,ETH | N/A⁵ | N/A⁵ | N/A⁵ | N/A⁵ | N/A⁵ | N/A⁵ | N/A⁵ | N/A⁵ | 미사용³ | **BLOCKED_DATA** |
| 3 | C1 basis_momentum | 스팟-퍼페 베이시스 모멘텀 | D4(웹 R2) | BTC,ETH | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미사용³ | **CANDIDATE(데이터 200 OK 실측)** |
| 4 | C2 orderflow_imbalance | 호가 잔량·오더플로우 불균형 | D4(웹 R2) | BTC,ETH | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미사용³ | **CANDIDATE(측정 이력 확인 필요 — microstructure.py 기구현)** |
| 5 | C3 coinbase_premium | 코인베이스 프리미엄 필터 | D4(웹 R2) | BTC | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미사용³ | **CANDIDATE(외부 거래소 데이터 의존)** |

¹ FAIL 확정이라 포지션 단위 지표 미산출(시도 수 관리) ² 결선 미진입(Phase A 탈락) ³ locked/reserved 구간 접근 자체 없음 ⁴ 측정 미착수(설계 필요: aggTrade 체결 관통 모델) ⁵ 데이터 누적 중(90일 필요)

기존 42건 daytrade·11건 swing·4건 scalp 전부 REJECTED(ADR·원장 근거, §3~4). 유일한 생존 계열 없음.

## 14. Final Recommendations

1. **N2 결정(사용자)** — `run_backtest.py --entry-order limit`이 이미 존재하므로, aggTrade 체결 관통 모델만 보강하면 측정 가능. **§9.5 실측으로 우선순위 상향**: taker 시장가는 왕복 31~41bp($10k)이므로 maker 전환의 기대 개선폭이 기존 추정(6~14bp)보다 큼
2. **차기 방향 신호 측정 우선순위(웹 리서치 2차 기반)**: ① C1 베이시스 모멘텀 — premiumIndexKlines 1m 아카이브 200 OK 실측, 단독 데이터로 구성 가능, 캐리·펀딩극단과 신호 상관 검사 조건 ② C2 오더플로우 불균형 — bookDepth 2023-01~ 200 OK, 단 저장소 microstructure.py와의 측정 이력 확인 후 진행, 비용벽 최고위험 ③ C3 코인베이스 프리미엄 — 외부 거래소 데이터 의존(샌드박스 403 제약 → 사용자 PC 측정 검토)
3. **P3 청산 데이터 감시** — 사용자 PC `var/data/liquidations/` + `_connection.jsonl` connected 이벤트 확인. 2027-01경 90일 누적 → P3 사전 규격(고정 완료) 그대로 측정
4. **예산 해제 후(10-29/11-01)** — 등록할 검증 통과 후보가 현재 0건. 무리한 신규 등록 금지, N2/C1 완성 우선. C1 측정 시 P1과 동일한 사전등록→Holm→교차검증 절차 적용 권장
5. V1 har80 게이트는 손실 억제 필터로 보류 목록 유지(방향 엣지 부재 상태에서는 실익 없음)
6. 클로드코드 인계 항목: 본 보고서 + `/home/z/my-project/NEW2_run/`(사전등록·스크립트·결과·인벤토리·리서치) 전달 → P1 결과를 저장소 장부 양식으로 통합 여부는 사용자·클로드코드 결정

## 15. 남은 미완료·한계 (정직 기록)

- aggTrade 기반 체결 모델(N2), 청산 측정(P3), Upbit/스트림 접근(샌드박스 403·451 제약) — 구조적 미완료
- 웹 리서치 2차도 16회 중 8회는 노이즈 폴백(긴 쿼리) — 출처를 꾸미지 않고 그대로 기록. arXiv "Order Book Filtration"(2025-07)는 ID 미확보 상태
- 이번 세션에서 저장소 자체 단위테스트(`pytest`)는 미실행 — 저장소 코드를 수정하지 않았고, 신규 스크립트는 재실행 재현으로 검증
- **[v5 해소] P1의 독립 비용 가정 문제는 §9.5 교차검증으로 해소** — 엔진 CandleCostModel 적용 결과 FAIL 재확증
- CandleCostModel의 impact 계수 0.1은 저장소 문서 스스로 "보수적 플레이스홀더"(costs.py:15-16) — 절대값보다 **정액 관행 대비 상대 왜곡 방향**(과소평가 소지)에 의미. 실제 캘리브레이션은 recorded book 필요(live/paper 수집 후)
- C1~C3는 문헌+데이터 가용성 실측까지만 완료한 후보 — 실측 전이며, 측정 시 사전등록 필수

## 16. 산출물 목록 (v5 전체)

| 파일 | 내용 |
|---|---|
| `NEW2_전략연구_보고서_v5.md` | 본 보고서(v4 대체) |
| `NEW2_run/PREREG_local_2026-10-12.md` | P1 사전등록(실행 전 고정) + P3 청산 캐스케이드 규격 |
| `NEW2_run/fr_extreme_diag.py` / `fr_supplementary.py` | P1 진단·보조 스크립트 |
| `NEW2_run/results/fr_extreme_results.json/md` | P1 24셀 결과(정액 비용) — FAIL |
| **`NEW2_run/fr_crosscheck_engine_cost.py`** | **[v5 신설] 엔진 CandleCostModel 교차검증 스크립트** |
| **`NEW2_run/results/fr_crosscheck_results.json/md`** | **[v5 신설] 교차검증 결과 — FAIL 재확증 + 비용 31~41/69~99bp 실측** |
| **`NEW2_run/inventory_detail.md`** | **[v5 신설] 57전략 전수 인벤토리(진입/청산/지표, 파일:줄번호 근거)** |
| `NEW2_run/research/ws1~4.json` | 웹 리서치 1차 원문 |
| **`NEW2_run/research/r2_ws1~16.json`** | **[v5 신설] 웹 리서치 2차 원문(16검색)** |
| **`NEW2_run/research/web_research_round2.md`** | **[v5 신설] 신규 원리 후보 9건 + 데이터 아카이브 실측 로그** |
| `NEW2_run/data/` | 15m kline 65 + fundingRate 65 (BTC/ETH 2021-04~2026-04) |
