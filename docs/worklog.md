---
Task ID: 1
Agent: Z.ai Code (main orchestrator + 3 Explore subagents)
Task: github.com/tlsehd195/NEW2 코인 단타 프로그램 전체 파일 분석, 기능 연결성 검증, 완성도 평가

Work Log:
- 저장소 클론 (기본 브랜치 probe-empty는 빈 브랜치, main 브랜치가 실제 코드)
- 508개 파일 / 215개 Python 파일 / 약 29,000 라인 확인
- 병렬 Explore 에이전트 3개 운영: (a) src/cointrader 135파일 전수분석, (b) dashboard-next + dashboard-ui, (c) tests/configs/docs/scripts/CI/.claude
- 핵심 발견 3건 직접 교차검증 완료:
  1. paper/engine.py에 max_hold_bars(12h 시간손절)·max_entries_per_day 미적용 (백테스트 event_engine.py:300에는 존재) → 백테스트/페이퍼 다이버전스 버그 확인
  2. depth 스트림 구독(@depth@100ms)하지만 paper/engine.py process()에 DepthDelta 분기 없음 → PaperBroker.on_depth 미호출 확인
  3. .github/workflows 3개 파일(screening/signal_validation/vote_frequency_diag)에 branches: ain] 오타 확인 (ci.yml은 정상)
- 완성도 평가: 연구·페이퍼 시스템 기준 ~75%, 라이브 실거래 포함 기준 ~55-60%

Stage Summary:
- 분석 보고서를 사용자에게 한국어로 전달 (기능 목록, 연결성 맵, 완성도 %, 버그 목록, 개선 제안)
- 외부 저장소 분석 작업이므로 이 샌드박스의 웹 개발/크론 작업은 해당 없음으로 판단
---
Task ID: 2
Agent: Z.ai Code (main orchestrator)
Task: NEW2 프로그램에 적용 가능한 검증된 전략 방향 웹 리서치

Work Log:
- web-search 스킬로 8건 검색 (인트라데이 모멘텀/평균회귀, 펀딩비, 오더플로우, 돈치안 브레이크아웃, 세션 효과, VWAP, ML 15분)
- page-reader로 핵심 원문 4건 확보: Presto Research 펀딩비 예측력(2024), Wen et al. 2022 초록, arxiv API로 Quarter-Hour Effect 논문(2607.09426) 확인, QuantifiedStrategies 백테스트 목록
- arxiv API 검색으로 펀딩비·퍼페추얼 관련 논문 10건 스캔
- 사용자 프로그램의 기존 features(microstructure, funding/OI side votes, regime)와 매핑

Stage Summary:
- 핵심 발견: Kim & Hansen "Quarter-Hour Effect"(arxiv 2607.09426) — 바이낸스 퍼페추얼 15분 경계에서 시작 수익률 OOS 예측 가능 + 시작 오더 불균형이 4~12시간 수익 예측. 사용자 프로그램(15분봉, 최대 12시간 보유, 마이크로스트럭처 피처 보유)과 정확히 일치
- 기타: 인트라데이 TSM(Shen 2021), 인트라데이 모멘텀+반전(Wen 2022), 펀딩비 극단값( Presto 2024, Helminen 2022), 청산 캐스케이드 반전, ATR 레짐 필터 돈치안 브레이크아웃
- 상세 보고서를 채팅으로 전달 완료
---
Task ID: 3
Agent: Z.ai Code (main orchestrator)
Task: 전략 리서치 결과를 클로드코드용 파일로 작성 (저장소 검증 기반 확인 포함)

Work Log:
- 사용자 질문("저장소 확인 후에 찾아본거지?")에 답변: 첫 메시지의 전수 분석 결과에 근거했다고 확인
- 파일 정확성을 위해 참조 파일 6종 재검증:
  1. indicator_votes.py:44 DEFAULT_PANEL = 6개 가격 기반 투표 확인
  2. microstructure.py에 imbalance/microprice/TradeFlowWindow 구현 확인
  3. indicator_vote.py:175-181에서 context를 레짐 확인용으로만 사용(마이크로스트럭처 미사용) 확인
  4. hypotheses.py:39 DEAD_CANDIDATE_PATTERNS = ts_momentum_*/funding_carry_*/basis_carry_* 확인
  5. side_indicators.py funding_crowding_score/oi_confirmation_score 확인
  6. binance_ws.py에 forceOrder 스트림 미구독 확인
- /home/z/my-project/전략리서치_보고서.md 작성: 검증 기반(V1~V8) + 전략 후보 6종(S1~S6) + 좌초 계열 구분 + 실행 순서 + 클로드코드 작업 지시(R1~R12 체크박스)

Stage Summary:
- 생성 파일: 전략리서치_보고서.md (클로드코드에 바로 전달 가능한 형식)
- 핵심: 리서치가 저장소 실제 상태(구현된 피처, 미연결 데이터, 죽은 계열)에 근거했음을 V1~V8로 문서화

---
Task ID: 4
Agent: Z.ai Code (main)
Task: 저장소 전체 재확인(특히 ADR 기록 전수 정독) + 기존 제안/기각 항목과 겹치지 않는 신규 전략 리서치

Work Log:
- 기존 클론 소실 확인 → /home/z/tlsehd195-NEW2 재클론, main 체크아웃. HEAD가 3e5c9cd→5facae8로 이동(신규 커밋 57개) 발견
- ADR 위치가 docs/decisions/로 확인(76개, ADR-0064~0079 신규 16개) — 전부 정독
- PROJECT_STATUS.md 701행 전수 정독: S1 폐기(ADR-0078), S2 보류(ADR-0076), S3 기각(ADR-0079) 확인 — "S3 테스트할지" 질문은 이미 저장소에서 답 나옴
- 이전 감사의 BUG-1/BUG-2는 ADR-0055에서 수정됨, "branches: ain]" 오타는 오탐(ADR-0055 정정) 확인
- 제약 현황 실측: 사전등록 20행, 잠금 윈도우 95개, 예약 2개(BTC 2024-03-25~06-18, ETH 2026-04-28~08-31), 예산 만료 2026-10-29 11:32 KST, DEAD_CANDIDATE_PATTERNS 3종
- 신규 정보원 실측 검증: data.binance.vision metrics 아카이브에서 BTCUSDT-metrics-2024-01-15.zip 다운로드 → 미사용 컬럼 4개 확인(count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio, count_long_short_ratio, sum_taker_long_short_vol_ratio), BTC 2021-05~/ETH 2021-12~ 가용(HTTP 200 실측)
- ADR-0070이 oi_dir/oi_chg(미결제약정)까지 이미 측정·기각했음을 확인 → OI 계열 후보 배제, 참여자 구성 비율만 신규로 채택
- 웹 리서치: crowd positioning 역발상, 크립토 XS 단기 되돌림 문헌, 김치프리미엄(CryptoQuant 관행 지표/한국 문헌), 메이커 수수료 구조
- UpbitRestCandles가 KRW-BTC 지원(범용) 확인 → 김치프리미엄 후보의 데이터 파이프 존재 확인
- 최종 보고서 작성: /home/z/my-project/strategy_research_v2.md

Stage Summary:
- 신규 후보 4종(완전 겹침 배제 보증): N1 metrics crowd-positioning 비율(최우선, 데이터 스키마/가용일 실측 검증 완료), N2 지정가(maker) 진입 실행 전환(ADR-0065가 스스로 제안한 방향, 비용 바닥 12~20bp→6~10bp), N3 김치프리미엄(교차거래소 신규 정보원, 사용자 PC 백필 필요), N4 XS 단기 되돌림(차순위)
- 권고 확정: S3=ADR-0079로 기각(예산 미사용), P5/P7 h48=DSR 0.12~0.18로 결선 승격 보류
- 실행 순서 R1~R7 + 예산/윈도우 준수 체크리스트를 보고서에 포함

---
Task ID: 5
Agent: Z.ai Code (main)
Task: 저장소 재확인(신규 커밋 13개) + 학습 사이클 구조 상세 정리

Work Log:
- 재클론: HEAD 5facae8→0976859 (신규 13커밋). ADR-0080(N1 폐기), ADR-0081(결함 4건 수정), ADR-0082(변동성 예측 진단 V1~V3) 확인
- 학습 사이클 코드 전수 정독: learning/{cycle,challenger,drift_check,journal_data}.py, ml/{features,linear_model,tree_model,ml_strategy}.py, monitoring/drift.py, paper/runner.py 배선, scripts 2개, tests 4개, configs/paper.json, retention.json, ADR-0044 원문
- 학습 사이클 구조를 사용자에게 상세 정리 제공

Stage Summary:
- 학습 사이클 = 페이퍼 프로세스 내장 일일 관찰 루프(2스텝): ①드리프트 감시(9특징×2검정, 7일 베이스라인) ②그림자 챌린저(릿지+배깅트리 일일 재학습, 최신 2000표본, 챔피언 h16/h48 투표와 동일 지표로 대결)
- 핵심 설계: SHADOW_ONLY_UNVALIDATED(매매 절대 미반영), learning 패키지 import 경계 AST 테스트, fail-closed(UNKNOWN≠무드리프트), 상수 사전 고정, daily_cycle_done 마커 + 7일 자동 백필 + 수동 --day 스크립트
- N1은 ADR-0080으로 폐기됨(54셀 Holm 통과 없음). 다음 결정: N2(지정가 진입) 사용자 결정 대기

---
Task ID: 6
Agent: Z.ai Code (main)
Task: NEW2 대규모 전략 연구 프로젝트 실행 — 저장소 전수 재조사 + 증거 기반 실측 진단(P1) 실행

Work Log:
- 재클론: HEAD 0976859→8ec6371(신규 35커밋), ADR 85건. ADR-0083~0089 원문 정독(V1 게이트 선별, 청산 수집기, 김치 N3 폐기, 선행-후행 폐쇄, 비대칭 구조 폐쇄, SMC 48셀 폐쇄)
- 장부 실측: preregistration 20행(H-0022 2026-10-02), screening 13행(ADR-0089), candidate_status BACKTESTED 21/REJECTED 21, DEAD 패턴 3종 불변, 예산 2026-10-29 11:32 KST 1슬롯 해제
- 중복성 검사: ADR-0070이 tod_seasonal/ac_dir/vr/oi_dir/lead_lag까지 이미 측정 → 시간대·자기상관·분산비율 계열 배제. 미측정 유일 원리 = 펀딩 극단 방향 신호로 확정
- 사전등록 문서 고정(실행 전): /home/z/my-project/NEW2_run/PREREG_local_2026-10-12.md (P1 24셀 Holm 규격 + P3 청산 캐스케이드 사전 규격)
- 데이터 130파일 실다운로드(data.binance.vision 15m kline 65 + fundingRate 65, BTC/ETH 2021-04~2026-04), 품질 검사: 결측 0·중복 0
- fr_extreme_diag.py 구현·실행: 24셀 전부 비용 미달, Holm 통과 0 → P1 FAIL(폐기 권고). 보조 선형상관 |r|≤0.067, 국간 부호 불일치
- 잠금/예약 TEST 구간 일절 미사용, 저장소 장부 수정 없음(사용자·클로드코드 통합 대기)

Stage Summary:
- 산출물: /home/z/my-project/NEW2_전략연구_보고서_v3.md (증거·명령·결과 파일 경로 포함), /home/z/my-project/NEW2_run/ (사전등록+스크립트+결과 JSON/MD)
- 핵심 결론: 15m 방향 신호의 주요 원리 전멸 확정(펀딩 포함). 잔여 공백 = N2 지정가 실행 계층, 청산 캐스케이드(90일 누적 후), 포트폴리오 계층
- P1은 실패한 그대로 보고 — 재파라미터링은 사전등록 위반이라 수행 안 함

---
Task ID: 7
Agent: Z.ai Code (main)
Task: 지시문 미이행 항목 보강 — 전략 전수 목록화, 파이프라인 직접 실행, 외부 출처 조사, 20섹션 보고서

Work Log:
- 미이행 자가진단: 전수 목록화/구조 조사/엔진 추적/CLI 미싱행/웹 출처/보고서 구조 6건 확인 후 순차 보강
- 저장소 구조 실측: 623파일, src Python 150, tests 68, workflows 25(목록 확보), working tree 클린
- configs/strategies.json 57건 전수 파싱: swing 11(1h/1d)/scalp 4(1m/5m)/daytrade 42(15m) — daytrade 42건이 15m 전략군 전체. 레지스트리 외 연구 전용 6개(grid/funding_carry/basis_carry/cross_sectional/adaptive_ensemble/baselines) 식별
- 기존 도구 직접 실행: check_ledgers.py → 0 error(s), 0 warning(s), exit 0. run_screening/run_validation/run_backtest --help 전체 문서화. run_backtest에 --entry-order {market,limit} 옵션 존재 확인(N2 엔진 지원 근거)
- 비용 모델 실측: backtest/costs.py CandleCostModel(fee+half_spread+sqrt impact), 진단 관행 왕복 12/16/20bp
- 웹 리서치 4건 실행(NEW2_run/research/ws*.json): 유효 출처 2건(펀딩 역발상 실무 관행 3곳, MDPI 세션 분할 논문), 2건은 노이즈로 미확보 — 정직 기록
- 보고서 v4 작성: NEW2_전략연구_보고서_v4.md — 20섹션 구조 + §33 최종 결과 표(N/A 사유 명시) + P1 FAIL + D0~D4 배제 7계열 + 신규 3건

Stage Summary:
- 지시문 §35 체크리스트 기준: 조사/전수목록/가설/실패기록/명령확인/후보발굴/중복평가/가설사전등록/데이터적합성/구현/실제백테스트/비용/OOS보호/실패분석/결과보존/최종보고서 = 완료. WF/PBO/DSR/단위테스트 = 미완료(사유: Phase A 탈락으로 결선 분기 없음 + 저장소 코드 무수정)
- 핵심 결론 불변: 방향 엣지 0건, 잔여 = N2(사용자 결정)/P3(데이터 누적)/포트폴리오 계층

---
Task ID: 8-a
Agent: Explore
Task: 57개 전략 진입/청산/지표 상세 인벤토리 (실제 코드 기반)

Work Log:
- HEAD 재확인(8ec6371, clean). strategies/ 패키지 14파일 + 지원 모듈(regime, indicator_votes, candidate_indicators, side_indicators, har_vol, indicators) + risk/engine + backtest/event_engine + paper/engine 전수 정독
- registry.py FACTORIES 14클래스 ↔ configs/strategies.json 57ID 매핑 실측: DayTradeVote 38 / IndicatorVote 4(swing) / FlowVote 4 / 클래식 스윙 7 / 스캘프 4. build()의 id·family·warmup 일치 검증(레지스트리 위조 방지) 확인
- 각 클래스 signal() 코드에서 진입식·청산식·지표 추출: G2 지표투표(6패널+플랫 캘리브레이션+동의도, vol/regime_switch/entry_gate, stop_atr·trail_atr·m24/m96), G3 플로우투표(테이커 플로우 z±1, z=0 청산, VPIN/펀딩 게이트), G1 클래식 스윙 7종, G4 스캘프 4종 — 전부 파일:줄번호 근거 부착
- warmup 공식 검산(140+lookback+horizon+1, extra_panel +481, har80 +4232 → 1157/1189/1638/5389 실측 일치), 마켓 분포(4종 21건·BTC+ETH 32건·BTC+SOL 4건) 실측
- 이벤트 엔진 청산 우선순위(청산>갭스톱>스톱>TP, 트레일 갱신, 시간청산→신호청산, latency 1바)와 엔진 소비 파라미터(max_hold_bars/max_entries_per_day/quality_window_bars) 코드 위치 확인
- 발견 2건 기록: ① use_side_data 4건은 러너(run_screening/run_validation) 주입 없으면 fail-closed flat, ② 페이퍼 품질게이트 윈도우(config 50) vs 백테스트(전략 192) 불일치
- 결과를 /home/z/my-project/NEW2_run/inventory_detail.md 로 저장(그룹별 의사코드 + 57행 전수 표 + 공통 구조 10항목 + 근거 파일 목록)

Stage Summary:
- 산출물: /home/z/my-project/NEW2_run/inventory_detail.md (57개 전략 진입/청산/지표 실제 코드 기반 전수 인벤토리, 모든 주장에 파일:줄번호 근거)
- 핵심: 57 ID = 14 클래스의 파라미터 변형(daytrade 42건은 DayTradeVote 38+FlowVote 4). 대표 로직 4군(G1~G4)으로 그룹핑 완료, allow_short·레짐게이트·fail-closed·공통 리스크 프로파일(risk_profile default, stop 기반 사이징) 확인

---
Task ID: 8-b
Agent: general-purpose
Task: 웹 리서치 2차 — 미측정 원리 후보 확대

Work Log:
- web_search(z-ai CLI) 16회 실행(r2_ws1~16.json): 유의 결과 8회(ws2 오더북불균형/ws7 베이시스/ws8 CB프리미엄/ws9 청산아카이브복원/ws11 옵션스크우/ws12 ETF플로우/ws13 교차차익/ws15 Boons-Prado basis-momentum), 노이즈 폴백 8회(ws1,3~6,10,14,16) — 정직 기록
- arxiv API 6쿼리: 유효 2건(2102.04591 청산·레버리지, 1704.08175 비트코인 점프), "Order Book Filtration"(2025-07)은 제목검색 0건이라 ID 미확보로 기재
- data.binance.vision 실측(curl HEAD + S3 listing): premiumIndexKlines/markPriceKlines/indexPriceKlines 1m 2019-12~ 200, bookTicker 200, bookDepth 2023-01-01~현재 200, aggTrades 200, 분기선물 kline(BTCUSDT_240329) 200, 스팟 kline 200. liquidationSnapshot은 UM 빈 폴더 실측, CM BTCUSD_PERP만 2023-06-25~2024-10-14 존재 확인
- 외부 무료 API 실측: Coinbase 캔들 2021-06-01 응답, Bybit linear 15m 2021-06-01 응답, OKX history-candles 응답, Deribit DVOL 2022-01 응답, Farside 403(봇차단→불확실 표기)
- 후보 9건을 배제목록 16번과 1:1 대조해 비겹침 판정 + 비용 관점 비고 작성, 상위 3건 우선순위 부여

Stage Summary:
- 상위 3건: ①스팟-퍼페 베이시스 모멘텀(Boons-Prado JF 2019, premiumIndexKlines 아카이브로 즉시 진단 가능, 배제10·11과 메커니즘 상이) ②오더플로우·호가 불균형(Cartea 2018/QHE, bookTicker+bookDepth+aggTrades 풀 아카이브, 단 비용벽 최고위험·저장소 microstructure.py 기구현 존재=미측정 채널 와이어링) ③코인베이스 프리미엄(미국 현물 수요 프록시, 무료 자체계산 가능, 김치와 패밀리 인접 명시)
- 그 외: 프리미엄 인덱스 인트라-인터벌(4), 분기선물 커브(5), 청산 캐스케이드(기존 P3 확인용), 교차거래소 퍼페 스프레드(6), ETF 플로우 필터(7), 옵션 스크우/DVOL(8, 데이터 불확실)
- 산출물: /home/z/my-project/NEW2_run/research/web_research_round2.md (실측 URL·HTTP 코드 표 포함)

---
Task ID: 8 (총괄) / 8-c
Agent: Z.ai Code (main)
Task: 지시서 공백 최종 보강 — 엔진 비용모델 P1 교차검증(8-c) + 서브에이전트(8-a 인벤토리, 8-b 웹리서치) 총괄 + 보고서 v5 작성

Work Log:
- 8-a(Explore 서브에이전트): 57전략 전수 인벤토리를 실제 코드 기반으로 작성 → NEW2_run/inventory_detail.md. 57 ID = 14개 클래스 × 파라미터 변형(registry.py:35-50), daytrade 42 = DayTradeVote 38 + FlowVote 4, 진입/청산/지표 전부 파일:줄번호 근거. 신규 발견: use_side_data 4건 fail-closed, 페이퍼 품질게이트 윈도우 불일치(config 50 vs 전략 192)
- 8-b(general-purpose 서브에이전트): 웹 리서치 16회(성공 8/노이즈 8 정직 기록) + arXiv 6쿼리 + data.binance.vision HTTP 실측 → 신규 원리 후보 9건(상위 C1 베이시스 모멘텀/C2 오더플로우 불균형/C3 코인베이스 프리미엄) → web_research_round2.md. premiumIndexKlines 1m·bookDepth 2023~ 등 200 OK 실측
- 8-c(직접): fr_crosscheck_engine_cost.py 작성·실행 — 저장소 엔진 CandleCostModel(costs.py:104-117)을 P1 24셀에 그대로 적용. 신호/거래 생성은 사전등록 규격과 동일 로직(재현 보장)
- 교차검증 결과: o10k($10k) 왕복비용 31~41bp → net>0 셀 0/24, Holm p<0.05 3셀은 전부 음수 방향 유의미. o100k($100k) 69~99bp → net>0 0/24, Holm 8셀도 전부 음수. fail-closed 제외 0건(아티팩트 아님)
- 보고서 v5 작성(NEW2_전략연구_보고서_v5.md): §3 인벤토리 보강, §6 웹리서치 2차 반영, §9.5 교차검증 신설, §13 표에 C1~C3 CANDIDATE 추가, §14 권고 갱신, §16 산출물 목록 신설

Stage Summary:
- P1 FAIL은 독립 비용 가정과 무관하게 로버스트 재확증(정액/엔진모델 모두 PASS 0)
- 핵심 신규 발견: 엔진 비용모델 기준 $10k 소액 taker 주문도 왕복 31~41bp — 정액 12~20bp 관행은 과소평가 소지. N2 지정가 진입의 기대 개선폭이 기존 추정(6~14bp)보다 큼 → N2 우선순위 상향의 실측 근거
- 지시서 이행 상태: 조사/전수목록(상세완성)/가설/실패기록/명령확인/후보발굴(9건 확대)/중복평가/사전등록/데이터/구현/백테스트/교차검증/비용/OOS/실패분석/보존/보고서 = 완료. WF/PBO/DSR/단위테스트 = 미완료(파이프라인 정상 분기 + 저장소 무수정 원칙)
- 사용자 대기 항목: N2 지정가 진입 측정 실행 여부 결정 / C1~C3 중 차기 측정 대상 결정

---
Task ID: 9-a
Agent: general-purpose
Task: 유명 전략 전수 스캔 (웹 리서치 3차)

Work Log:
- web_search(z-ai CLI) 26회 실행(r3_ws1~26.json): 유의 결과 19회(73%) / 노이즈 7회 — 노이즈는 그대로 기록, 6회는 짧은 변형 쿼리로 회복(ORB·TTM·페어트레이딩·VWAP·이치모쿠·와이코프). 2차에서 실측된 "단어 폴백" 노이즈 재현(cointegration→Connectedness, squeeze→Whale 등)
- 데이터 라이브 실측(curl): alternative.me 공포·탐욕 API 200(일별 무료), Yahoo Finance BTC=F(CME 비트코인 선물) 일봉 200(2017-12~)
- 2차 후보 C1~C9 및 배제목록 16번과 1:1 대조, 유명 전략 20종을 "배제/중복" 증거 표로 전수 기록(ORB·TTM스퀴즈·VWAP·이치모쿠·헤이킨아시·터틀·변동성돌파·평균회귀·피벗·와이코프·캔들패턴·로테이션·온체인 MVRV/SOPR·그리드·스캘핑 등)

Stage Summary:
- 신규 후보 4건(전원 조건부 통과 — 패밀리 인접성 명시): F1 CME 갭(Yahoo BTC=F 실측, 배제1 인접), F2 공포·탐욕 지수(alternative.me 실측, 배제12·9·14 성분 인접→디코릴레이션 조건), F3 BTC-ETH 페어 공적분(kline만 가능·비용벽 2배, cross_sectional/N4 인접), F4 구글 트렌드(데이터 불확실, 최하순위)
- 정직 결론: 인접성 없는 완전 신규 원리는 이번 전수에서 0건 — 유명 카탈로그는 배제 16번/C1~C9/저장소 모듈로 대부분 소거됨
- 산출물: /home/z/my-project/NEW2_run/research/r3_famous_scan.md (요약 표 + 후보 상세 + 배제 20종 표 + 검색 로그 26행)

---
Task ID: 9-c
Agent: general-purpose
Task: 크로스 마켓·구조적 원리 조사

Work Log:
- web_search 26회 실행(r3_ws1~26.json): 유의 10회(ws2 시변상관/ws6 Boido 2026/ws7 Griffin-Shams 경로/ws11 상장 이벤트스터디/ws18 F&G 학술/ws19 상폐 효과/ws20 다운타임·플래시크래시/ws22 페어트레이딩 문헌/ws25 BTC-Nasdaq 상관/ws26 베이시스 차익 언와인드), 부분 4회, 노이즈 12회 — 429 재시도 4회 포함, 정직 기록. arxiv API 4회 전부 실패(301→타임아웃/Rate exceeded)
- 축별 검색 횟수: 축1=4회, 축2=4회, 축3=2회, 축4=2회, 축5=3회, 축6=4회, 축7=7회 (총 26회)
- 데이터 실측(curl): alternative.me F&G 전체 3,172일(2018-02~, 200)/DefiLlama USDT 일간 3,240레코드(2017-11~, 200)/Yahoo ES·NQ·GC 1h 2.4년+DXY 5y(200, 단 15m은 60일 한정 실측)/Binance 공지 catalogId=48 상장 2,279건 releaseDate 필드(200)/CoinMetrics FlowInBFX 401/DefiLlama CEX 과거시계열 404 3종/Upbit 공지 404/BLS 403/연준 FOMC 캘린더 200
- 저장소 실측: main 재클론(/tmp/NEW2_probe) — 워크플로우 25 yml 전수, cross_sectional.py 197행 원문(일간 랭킹 모멘텀·공적분 전무), ADR 89건 제목 대조(본 축 관련 ADR 0건)
- 비겹침 판정: 7축 전부 배제 1~16·C1~C9·워크플로우 19개와 대조 — 통과 7축(조건부 포함), 축3(온체인 순유입)만 데이터 불가로 종결 확정
- 산출물: /home/z/my-project/NEW2_run/research/r3_crossmarket.md

Stage Summary:
- 비겹침 통과 후보 7건: X1 ES/NQ 야간 동조 조건부 게이트(유망, 최우선)/X2 매크로 이벤트 윈도우(라운드2 유보 재검토 통과, 배제1과 조건부로 구분)/X3 베이시스 마진 붕괴 디레버리징(C1 상관 사전검증 조건)/X4 BTC-ETH 스프레드 평균회귀 1레그(2레그는 비용벽 24~40bp, S3 인접 플래그)/X5 스테이블코인 공급 필터(일간 한계)/X6 F&G 극단 필터(배제12와 메커니즘 상이, 증분정보 의문)/X7 거래소 사건 리스크 게이트(상장 2,279건 실측 확보)
- 온체인 순유입은 무료 과거 데이터 부재가 HTTP 실측(401/404)으로 확정 → 재제안 금지
- 데이터 가용성 요지: X1/X3/X4는 로컬+기실측 아카이브만으로 진단 가능(추가 데이터 0원), X2는 이벤트 날짜 수작업 160행 필요, X5/X6은 무료 일간 API 실측 확보(로컬 저장 완료)
---
Task ID: 9-b
Agent: general-purpose
Task: 학술 문헌 — 실측 유능 전략 조사(재실행 축소판)

Work Log:
- web_search 8/8 사용(유효 3, 부분 1, 노이즈 4 — ws3/6/7/8 노이즈·폴백 정직 기록, 클러스터링 축 2회 시도 후 포기), arXiv API 3/3 사용 전부 실패(빈 응답/14바이트 오류 — 9-c 장애 재현, https+리다이렉트 재시도도 실패)
- 유효 히트: Blasco 등 2023 "비트코인 선물 만기 효과"(만기 일자 헤지 언와인딩), Quantpedia 2024 만기 주변 동학, Lee 2024 실현분산→수익률(고변동 저수익), 실무 Hash Ribbons 백테스트(lookintobitcoin/bitbo) + Zhang 2023 RSJ·옵션 일유동성 등은 배제5/C8 인접으로 기각
- 각 후보를 배제 16번 + C1~C9/F1~F4/X1~X7와 1:1 대조, 15m kline·funding·아카이브 검증 가능성/비용/과최적화 위험까지 기록

Stage Summary:
- 발굴 3건(전원 조건부 통과): L1 선물 만기 효과(최우선 — 월 2~4회 이벤트, 분기선물 kline 아카이브로 즉시 이벤트스터디, CME갭과는 별개 메커니즘·패밀리 인접 플래그) / L2 해시리본 채굴자 자포자상 게이트(실무 문헌 유일·일간이라 레짐 게이트 전용·파라미터 사후선택 위험) / L3 실현변동 스케일링 게이트(비용 중립·과최적화 최소, 크로스섹션→시계열 변형 전제)
- 미확보 4축: 라운드넘버 클러스터링(스타일라이즈드 팩트 논문만), 고래 지갑(쿼리 폴백+온체인 데이터 벽), 인트라데이 드로다운 회복(전략 드로다운 이론만), 반감기(표본 4회+X2 인접) — arXiv ID는 API 장애로 웹 출처 대체
- 산출물: /home/z/my-project/NEW2_run/research/r3_literature.md (검색 로그 11행, 후보 상세, 비겹침 대조표, 미확보 기록)

---
Task ID: 9-d (취합)
Agent: Z.ai Code (main)
Task: 리서치 3차 통합 취합 — 유명 전략 스캔(9-a) + 문헌 유능 전략(9-b) + 크로스 마켓(9-c) 병합·우선순위화

Work Log:
- 9-a(general-purpose): 검색 26회(19 유횡/7 노이즈) — 비겹침 통과 4건(F1 CME갭/F2 공포탐욕/F3 BTC-ETH 공적분/F4 구글트렌드 보류), 배제·중복 소거된 유명 전략 20종 증거 표 완비. Yahoo BTC=F·alternative.me API 실측 200
- 9-b 첫 실행 타임아웃 → 축소 재실행: 검색 8회 — 문헌 후보 3건(L1 선물 만기 효과 최우선/L2 해시리본/L3 실현변동 수익예측 게이트). arXiv API 0/3 유효(장애 재현)
- 9-c(general-purpose): 검색 26회 — 축별 결론. X1 ES/NQ 야간 동조 게이트/X2 매크로 이벤트/X3 베이시스 마진 붕괴/X4 BTC-ETH 1레그/X5 스테이블/X6 F&G/X7 거래소 사건 통과, 온체인 순유입은 401/404 HTTP 실측으로 불가 확정. DefiLlama·Binance 공지 2,279건 실측
- 9-d 취합: 병합(F3=X4 공적분, F2=X6 공포탐욕) 후 통합 후보 12건, 우선순위 P0(P0: L1/X1/X3/X4 — 추가 데이터 0원)/P1/P2/P3/보류/불가로 정리 → NEW2_run/research/r3_master_summary.md

Stage Summary:
- 완전 신규(인접성 없음) 원리는 3차에서도 0건 — 통과 전원 조건부(패밀리 인접 플래그/주파수 한계/중복확인 조건)
- 최고 실현 가능 신규 측정 대상: L1 선물 만기 효과(문헌+아카이브 200 실측+이벤트스터디 단순). 다음으로 X1/X3/X4/L3/F1 전부 추가 데이터 비용 0원
- 필터/게이트 계열은 방향 신호가 아닌 리스크 관리 보조로 분류(V1 har80과 동일 보류 목록)
- 산출물: r3_master_summary.md(취합) + r3_famous_scan.md + r3_literature.md + r3_crossmarket.md + r3_ws*.json
- 다음 사용자 결정: L1 사전등록 및 측정 착수 여부 / P0 저비용 진단(X3·X4·L3·F1) 동시 실행 여부

---
Task ID: 11
Agent: Z.ai Code (main, cron 목표 루프 Job 453321)
Task: NEW2 전략 탐색 목표 루프 — 측정 큐 1~2순위(X3, F1) 사전등록→측정→Holm→엔진 교차검증→OOS 실행

Work Log:
- worklog/PREREG/기존 스크립트(fr_extreme_diag.py, fr_crosscheck_engine_cost.py) 규격 확인, todos 작성
- [X3] 사전등록 고정: PREREG_x3_basis_collapse_2026-10-12.md (베이시스 4h 변화 z ≤ −1.2816/−1.6449 → 되돌림 롱, 24셀 Holm, 하측만 일방향, OOS 60/40 규칙 사전 고정)
- [X3] premiumIndexKlines 15m monthly 65개월 다운로드(200 OK 실측, 결측·중복 0) → data/premium/
- [X3] x3_basis_collapse_diag.py 실행: Holm 통과 5셀 전부 **음수 방향 유의미**(순16 −12.51~−17.48bp, 전부 h4), config 양수 0/4 → PASS=False. OOS 8세그먼트 전부 음수(0/8). 붕괴 후 되돌림이 아니라 **추종 하락**(h4 총 +2~6bp지만 비용 미달)
- [X3] x3_engine_crosscheck.py 실행: 엔진 CandleCostModel 기준 Holm 통과 셀 전부 음수($10k −27~−41bp, $100k −55~−84bp) → FAIL 재확증
- [X3] 패밀리 사전검증(정보성): X3 z vs P1 펀딩 z 스피어만 ρ=−0.0105(BTC)/−0.0055(ETH) — 펀딩 극단과 붕괴 이벤트 동시발생 19~22%로 패밀리 독립 확인. C1과는 구조적 100% 반대 관계 명시
- [F1] ETH=F 가용성 실측(200, 2021-02~) → 사전등록 고정: PREREG_f1_cme_gap_2026-10-12.md (주말 갭 ≥0.5%/1.0% → 되돌림, valid_n=n 사전 고정, 진입지연 30분 fail-closed)
- [F1] Yahoo BTC=F/ETH=F 일봉 다운로드(1,280바, NA 2개 제외). Yahoo 스탬프 = 자정 ET 관례 실측(monday_stamped 237/sunday 0) → 재개 시각 = 스탬프−6h 환산 기록
- [F1] f1_cme_gap_diag.py 실행: Holm 통과 0셀 → PASS=False. 갭 되돌림(필)이 아닌 **추종 경향**(BTC span2 h48 추종 총bp +73~115)이나 비용·Holm 전부 미달. 이벤트 42~63개/구간, 지연 드랍 21건(최대 4시간, fail-closed 작동)
- [F1] f1_engine_crosscheck.py 실행: 엔진 비용 기준 전 셀 음수($10k −51~−64bp, $100k −52~−208bp) → FAIL 재확증
- results/measurement_queue_status.md 작성(측정 완료/잔여 큐/승격 기준/절차 체크리스트/데이터 자산)

Stage Summary:
- 이번 루프 측정 2건(X3, F1) 전부 FAIL — 승격 후보 없음(사전 고정 규칙상 루프 계속)
- 핵심 과학적 발견 2건: ①베이시스 붕괴는 되돌림이 아니라 단기(1h) 추종 하락(유의미하지만 3~6bp로 비용 벽 미달) ②CME 주말 갭은 필이 아니라 추종 경향(유의미하지만 비용 미달) — 두 방향 모두 12~20bp 왕복 비용 아래
- 방향 엣지 자체는 2건 더 실측으로 존재 확인됐으나 전부 비용 벽 미달 → N2(지정가 실행 계층)의 전략적 가치 추가 입증
- 다음 루프: C1(베이시스 모멘텀 — 데이터 로컬 확보 완료, 즉시 착수 가능) → C4 → C5(분기선물 다운로드 필요)
- 잠금/예약 TEST 구간 일절 미사용, 저장소 장부 무수정, 결과는 전부 NEW2_run/results/에 JSON+MD 보존

---
Task ID: 12
Agent: Z.ai Code (main)
Task: 최종 보고서 현황 확인 + v6 최종 통합 보고서 작성 (v5 이후 누락분 보완)

Work Log:
- 보고서 계보 전수 확인: NEW2_분석보고서.md(10-09) → 전략리서치_보고서.md(10-10) → strategy_research_v2.md(10-11) → v3(00:28) → v4(00:46) → v5(01:48) 순임을 확인
- v5 이후 신규 문서 파악: r3_master_summary.md(3차 리서치 취합), measurement_queue_status.md(X3·F1 FAIL 후 큐 상태) — 종합 보고서 체계 밖의 개별 문서 상태임을 확인
- v5 이후 미반영분(X3·F1 측정 2건 + 3차 리서치 전체)을 통합한 NEW2_전략연구_보고서_v6.md 작성

Stage Summary:
- 최종 통합 보고서 = NEW2_전략연구_보고서_v6.md (§0 한눈 요약 / §1 검증체계 / §2 비용벽 / §3 저장소 / §4 전멸 총람(16계열+20종+측정 3건) / §5 미측정 큐 17건 / §6 데이터 자산 / §7 정직 판정 / §8 다음 액션 / §9 산출물)
- 핵심 수치: 승격 후보 0건, 측정 완료 3건(P1/X3/F1 전부 FAIL), 신발견 2건(베이시스 붕괴=추종 하락, CME 갭=추종 경향 — 전부 비용 미달)
- 다음 루프: C1 베이시스 모멘텀(로컬 데이터 완비) → C4 → C5 → X1 순

---
Task ID: 13
Agent: Z.ai Code (main, 목표 루프 재개)
Task: 루프 사이클 1 — C1 베이시스 모멘텀 사전등록→측정→Holm→엔진 교차검증 + 최종 통합 보고서 v6 작성

Work Log:
- 보고서 계보 확인: 최종 종합 보고서가 v5(01:48)에 머물고 3차 리서치+X3/F1 측정이 미반영임을 확인 → NEW2_전략연구_보고서_v6.md 작성(§0~§9)
- [C1] 사전등록 고정: PREREG_c1_basis_momentum_2026-10-12.md (베이시스 모멘텀 z |z|≥1.2816 양측 추종, L∈{16,48,96,288}×h∈{4,16,48}, 패밀리 48셀 Holm)
- [C1] c1_basis_momentum_diag.py 실행: Holm 통과 19셀 전부 **음수 방향**(순16 −14~−31bp), config 양수 0/4 → PASS=False. 결론: 베이시스 모멘텀 극단은 **추종이 아니라 되돌림** 경향(L≥48 반대방향 원시 ~+5bp, L=16 원시 ±2bp 내외 — X3 실측과 일관)
- [C1] c1_engine_crosscheck.py 실행: 엔진 CandleCostModel 기준 net>0 0/48($10k/$100k 모두) → FAIL 재확증
- [C1] 패밀리 사전검증: C1 z(L별) vs P1 펀딩 z 스피어만 ρ≈0.04~0.15 — 펀딩 프록시 아님(독립 변수) 확인

Stage Summary:
- C1 FAIL 확정 — 승격 후보 없음. 신규 과학적 발견: 베이시스 모멘텀 극단(|z|≥1.28, 4h~72h 룩백) 후 추종은 음수, 되돌림 원시 엣지 ~+5bp — 전부 비용 벽(정액 16bp/엔진 31~41bp) 미달
- 프리미엄 인덱스 패밀리 3건(X3 붕괴/C1 모멘텀/펀딩)의 통합 그림: 15m~4h 지평에서 프리미엄 극단의 되돌림/추종 엣지는 최대 ±5bp 수준으로 일관되게 비용 미달
- 산출물: v6 보고서, PREREG_c1, c1_basis_momentum_diag.py, c1_engine_crosscheck.py, results/c1_*
- 다음 루프: C4(프리미엄 인트라-인터벌) → C5 → X1

---
Task ID: 14
Agent: Z.ai Code (main, 목표 루프 사이클 2)
Task: 루프 사이클 2 — C4 프리미엄 인트라-인터벌 스파이크 회귀 사전등록→측정→Holm→엔진 교차검증 + 장부 갱신

Work Log:
- [C4] 사전등록 고정: PREREG_c4_premium_spike_2026-10-12.md (프리미엄 봉 내 close−open 스파이크 z |z|≥1.2816/1.6449 → 회귀 방향 퍼페 진입, 24셀 Holm)
- [C4] c4_premium_spike_diag.py 실행: Holm 통과 7셀(B80/B90 h4) 전부 **음수**(순16 −12.7~−16.4bp), config 양수 0/4 → PASS=False. 회귀 방향 자체는 맞음(원시 +1~3bp) — 비용 미달
- [C4] c4_engine_crosscheck.py 실행: 엔진 기준 net>0 0/24($10k/$100k) → FAIL 재확증
- [C4] 패밀리 사전검증: C4 z vs C1 L16 z 스피어만 ρ≈0.24~0.27(동일 변수 패밀리 인접 정량화), C4 z vs 펀딩 z 병기
- 측정 큐 갱신: measurement_queue_status.md에 C1·C4 FAIL 행 추가, 잔여 큐 C5 → X1 → L3 → F2/X6 → ... 순서 고정

Stage Summary:
- 이번 턴 2사이클(C1, C4) 전부 FAIL — 승격 후보 여전히 0건
- 통합 결론 강화: 프리미엄 인덱스의 모든 스케일(봉 내 스파이크 C4 → 4h 붕괴 X3 → 4h~72h 모멘텀 C1)에서 방향 엣지는 존재하나 원시 ±1~5bp — 엔진 비용(31~41bp)의 1/6~1/10 수준으로, taker 실행으로는 수확 불가능하다는 것이 3건 독립 측정으로 확정
- 남은 희망 경로: ①대형 이벤트 전용(분기선물 커브 C5, 만기 L1, 매크로 X2) ②N2 maker 실행 계층 ③게이트류(L3/F2/X5)
- 다음 루프: C5(분기선물 아카이브 다운로드 필요) → X1(로컬 데이터 완비)

---
Task ID: 15
Agent: Z.ai Code (main, 목표 루프 사이클 3~4 — 무한 루프 지시)
Task: 사용자 지시 "대답 기다리지 말고 계속 루프" — C5 분기선물 커브 사이클 완수 + X1 착수, 가능성 전략 질문 답변

Work Log:
- "현재 가능성 보이는 전략" 질문 정직 답변: 승격 후보 0건, 실측 엣지는 X3/F1 추종 2건(전부 비용 미달), 구조적 경로 3개(대형 이벤트/N2 maker/게이트) 제시
- [C5] 데이터 수집: fapi probe 48계약 전원 유효 실측(418 레이트리밋 대응 재시도), fapi 4계약 + data.binance.vision 월간 아카이브 43계약(fail 0) + 스팟 1h BTC/ETH(2021-01~2026-07) 확보. 2025 아카이브도 밀리초 타임스탬프 확인
- [C5] 사전등록 고정: PREREG_c5_quarterly_curve_2026-10-12.md (near=납품≥t+7d 최소 계약, far=다음 분기, slope=(far−near)/spot, Δz L∈{24,120,480,960}h, 48셀 Holm)
- [C5] c5_quarterly_curve_diag.py: Holm 통과 7셀 **전부 음수**(순16 −14.6~−16.4bp), 엔진 net>0 0/48($10k/$100k), 엔진비용 $10k 32.8~36.5bp, 엔진 순익 최고 −16.47bp → FAIL 확정
- [C5] 구조적 발견: Binance 분기선물은 2023-09-29까지 다음분기 계약이 만기 0~10일 전에만 상장 → near(7d 버퍼)+far 동시 상장이 상시화된 것은 2023-09-29 이후. BTC/ETH span1 전체가 커브 부재(유효 0시간, fail-closed) — 커브 기반 전략 백테스트 구간은 2023-10~로 제한
- [C5] 패밀리: C5 Δz vs 펀딩 z 스피어만 ρ 최대 0.606(BTC L480) — 커브 슬로프는 펀딩 기대 프록시(P1 패밀리 부분 중복)
- [X1] 데이터 제약 실측: Yahoo ES/NQ 1h는 2024-05~2026-10만 가용(2021~2023 요청 422) → ETH span2(2024-12-10~2026-04-20)와만 겹침 → 단일 구성 진단 규격으로 사전등록 고정(PREREG_x1_external_momentum_2026-10-12.md, 패밀리 24셀, PASS=ES·NQ 2/2 일관성)
- [X1] x1_external_momentum_diag.py / x1_engine_crosscheck.py 작성 완료 (C5 기계 재사용)
- 장부 갱신: measurement_queue_status.md C5 행 추가, 데이터 자산 갱신. 보고서 v7 작성(NEW2_전략연구_보고서_v7.md)

Stage Summary:
- C5 FAIL 확정 — 측정 6건(P1·X3·F1·C1·C4·C5) 전부 FAIL, 승격 후보 0건 유지
- 신규 과학적 발견: ①분기선물 커브의 구조적 존재 구간은 2023-09-29~ (그 이전은 백테스트 불가, 재현 불가능한 데이터 구조) ②커브 슬로프 추종은 음수, 되돌림도 음수 — 양방향 비용 미달 ③커브는 펀딩 기대 프록시(ρ 0.61)
- L1(만기 효과)이 C5 데이터 재사용으로 즉시 측정 가능해짐 — 큐 효율 개선
- 다음 루프: X1 측정(스크립트 준비 완료) → L3 → F2/X6

---
Task ID: 16
Agent: Z.ai Code (main, 목표 루프 사이클 4)
Task: X1 ES/NQ 1h 모멘텀 동조 측정 (ETH span2 단일 구성 진단) + 탐색적 관측 X1-R 등록

Work Log:
- [X1] x1_external_momentum_diag.py 실행: 패밀리 24셀(ES/NQ × L{24,120,480,960} × h{4,16,48}). Holm 통과 7셀 전부 **음수**(순16 −14.6~−19.6bp), PASS=False(ES·NQ 2/2 일관성 셀 0)
- [X1] x1_engine_crosscheck.py: 엔진 net>0 0/24($10k/$100k) → FAIL 재확증
- [X1] 패밀리: ES·NQ Δz 상호 ρ 0.88~0.93(동일 지수군), ES Δz vs ETH 1h 전체 수익 ρ 0.014~0.083(L24 유의, L960 무유의 — 정보성 진단 1h 매핑 버그 수정 후 재산출)
- [X1] **탐색적 관측(판정 아님)**: h48 반대방향 통제에서 ES L24/L120 원시 +54.3/+47.9bp, 엔진($10k) 순익 +21.7/+15.1bp — 프로그램 7건 측정 중 **최초 엔진 비용 벽 돌파 셀**. NQ도 동방향 정합(+3.9/+2.2bp). 유효표본 22~26<30 미달 + 사후 발견 → X1-R(역방향 가설) 사전등록 과제로 큐 등록, 신규 구간 데이터 필요
- 장부 갱신: measurement_queue_status.md X1 행 + X1-R 대기 행 추가, 보고서 v8 작성

Stage Summary:
- X1 FAIL 확정 — 측정 7건 전부 FAIL, 승격 후보 0건 유지
- 핵심 전환점: X1-R 탐색 관측(엔진 순익 +21.7bp)이 프로그램 최초의 비용 벽 돌파 후보 — 단 사후 발견+표본 미달이라 판정 불가, 신규 데이터에서 재검증하는 것이 과학적 정합 경로
- ES/NQ 1h는 Yahoo 제약으로 2024-05~만 존재 → X1-R 재검증 데이터(2026-04 이후)는 시간이 지나야 축적됨 — 즉시 재검증 불가를 명시
- 다음 루프: L3(실현변동 게이트, ADR-0082 중복 확인 선행) → F2/X6

---
Task ID: 17
Agent: Z.ai Code (main, 목표 루프 사이클 5)
Task: L3 실현변동 꼬리 신호 측정 (Lee 2024 시계열 변형) — 저장소 vol_gate 중복 확인 선행

Work Log:
- 중복 확인: 원본 저장소(/home/z/tlsehd195-NEW2)는 본 세션에 미클론으로 직접 열람 불가 — 인벤토리 기록(65행)으로 대체: 저장소 기존 게이트는 "단기/장기 RV 비율 0.5~2.0 이탈 시 진입 차단"(방향 신호 아님) → L3(RV 수준의 방향 효과 실측)와 메커니즘 상이, 비중복 판정 후 진행(한계 명시)
- 사전등록 고정: PREREG_l3_rv_tail_2026-10-12.md (RV(L∈{96,288,960,2880}봉) z |z|≥1.2816 → 문헌 방향 매핑(고RV→숏/저RV→롱), 48셀 Holm, NWIN=960)
- l3_rv_tail_diag.py 실행: Holm 통과 27셀 **전부 음수**(순16 −14~−27bp) → PASS=False
- l3_engine_crosscheck.py: 엔진 net>0 0/48($10k/$100k) → FAIL 재확증
- 패밀리: RV z vs 펀딩 z ρ≈−0.02~−0.04, vs C1 z ρ≈−0.03~+0.04 — 독립 패밀리 확인 후 독자 FAIL
- 과학적 발견: Lee(2024) "고RV→저미래수익"은 시계열 꼬리에서 **부정** — 고RV 극단 직후 원시 +11bp 상승 바이어스(반대 통제). 단 비용 미달이라 전략화 불가
- 장부 갱신 + 보고서 v9 작성

Stage Summary:
- L3 FAIL — 측정 8건 전부 FAIL, 승격 후보 0건. 독립 패밀리(RV)까지 전멸하여 "방향 엣지는 존재해도 전부 비용 미달"이 패밀리 불문 일반화
- 다음 루프: F2/X6(공포탐욕 — 일간 주파수라 신호 주파수 전용 valid_n 규약을 사전 고정해야 함) → X5 → X2

---
Task ID: 18
Agent: Z.ai Code (main, 목표 루프 사이클 6)
Task: F2/X6 공포탐욕 극단 역발 측정 — 일간 신호 전용 valid_n 규약 최초 적용

Work Log:
- 사전등록 고정: PREREG_f2x6_fng_2026-10-12.md — 일간 발행(F&G 00:00 UTC) 특성에 맞춰 valid_n = n/max(1, h÷96) 일간 규약을 사전 고정(15m 규약의 과잉 보정 문제 해소). thr쌍 (25/75, 20/80, 30/70) × h∈{96,288,672} = 36셀, 진입 00:15 UTC(인과성 확보)
- f2x6_fng_diag.py: Holm 통과 0셀 → PASS=False. 1~3일 홀딩의 분산이 효과를 삼킴(최고 셀 p=0.45)
- f2x6_engine_crosscheck.py: **엔진 net>0 4/36($10k)** — ETH span2 극단공포(≤25)→3일 롱 엔진 순익 +36.19bp, thr20/80 h288 +11.52bp. $100k는 0/36(임팩트 비용)
- 탐색적 관측(판정 아님): F2/X6-R — X1-R(ES 극단→ETH 12h 역방향, 엔진+21.7bp)과 동일한 "극단 스트레스 후 단기 반등" 구조로, 독립 신호 2건이 같은 방향을 가리킴. 단 유효n 59·사후 발견·단일 구성 → 신규 데이터 필요
- 장부 갱신 + 보고서 v10 작성

Stage Summary:
- F2/X6 FAIL — 측정 9건 전부 FAIL, 승격 후보 0건 유지
- **전환점: 엔진 비용 벽을 넘는 탐색 관측 2건 확보(X1-R +21.7bp, F2/X6-R +36.2bp)** — 둘 다 "외부 극단 스트레스(미국 지수선물 모멘텀/센티먼트) 직후 암호자산 단기 반등" 구조. 통합 재검증은 신규 구간 데이터(측정 구간 종료 이후) 필요 — 현재 데이터로는 불가
- 일간 신호 규약(valid_n = n/max(1,h÷96))이 확립되어 X5·L1 등 일간/이벤트 후보에 재사용 가능
- 다음 루프: X5(스테이블코인) → X2 → X7 → L1

---
Task ID: 19
Agent: Z.ai Code (main, 목표 루프 사이클 7)
Task: X5 스테이블코인 총공급 Δz 추종 측정 (일간 규약 재사용)

Work Log:
- 데이터 정직성: r3_llama_usdt_hist.json은 USDT 단일이 아닌 전체 스테이블코인 순환공급(peggedUSD) 일간 스냅샷(2017-11~2026-10, 3,240레코드) — 사전등록 §0에 명시하고 "총공급" 가설로 진행
- 사전등록 고정: PREREG_x5_stable_supply_2026-10-12.md (Δz L∈{1,7,30}일 |z|≥1.2816 추종, h∈{96,288,672}, 36셀 Holm, 일간 valid_n 규약)
- x5_stable_supply_diag.py: Holm 통과 0/36 → PASS=False. 7일 홀딩 분산 지배, 구성 간 부호 불일치(BTC L1h672 −196bp vs ETH +341bp)
- x5_engine_crosscheck.py: Holm 0/36($10k), 1/36($100k, 음수셀). 엔진 net>0 17/36($10k)이나 전부 무유의·비일관 → 탐색 관측 등록 기준 미달
- 장부 갱신 + 보고서 v11 작성

Stage Summary:
- X5 FAIL — 측정 10건 전부 FAIL, 승격 후보 0건 유지. 스테이블코인 유동성 프록시는 방향 구조 부재
- 세션 누적(사이클 3~7): C5·X1·L3·F2/X6·X5 전부 FAIL. 핵심 자산은 ①탐색 관측 2건(X1-R 엔진+21.7bp, F2/X6-R 엔진+36.2bp — "극단 스트레스 후 단기 반등" 통합 구조) ②일간 신호 valid_n 규약 확립 ③분기선물 48계약 데이터(L1 즉시 측정 가능)
- 다음 루프: X2(FOMC/CPI — 날짜 큐레이션 선행) → X7(상장공지) → L1(만기 효과 — 데이터 완비)

---
Task ID: 17
Agent: Z.ai Code (main, 목표 루프 사이클 5 — 무한 루프 지시)
Task: 사용자 지시 "대답 기다리지 말고 계속 루프 돌려 끝날때 마다" — L3 실현변동 꼬리 + F2/X6 공포탐욕 극단 측정 사이클 완수, 보고서 v9 갱신

Work Log:
- 큐 상태 확인: L3 사전등록이 이미 고정되어 있고 측정 미실행 상태임을 확인 → 사이클 5의 1순위로 확정
- [L3] l3_rv_tail_diag.py 작성·실행 (PREREG_l3_rv_tail 규격 그대로): RV z(L∈{96,288,960,2880}) |z|≥1.2816 → 문헌 방향(고RV→숏/저RV→롱), 48셀 Holm. **Holm 통과 27셀 전부 음수**(순16 −13.8~−19.0bp), 원시 총bp>16 셀 0/48, 문헌 반대(고RV→롱) 통제도 0/48 → PASS=False. 엔진 교차검증: net>0 0/48($10k/$100k), 엔진비용 $10k 32.4~41.9bp, 엔진 순익 최고 −28.59bp → FAIL 재확증
- [L3] 과학적 발견: 고RV 극단 후 실측 방향은 "충격 후 미세 반등"(원시 +0.3bp 수준) — Lee(2024) 크로스섹션 저RV 프리미엄은 BTC/ETH 2자산 시계열 꼬리에서 수확 불가. 패밀리: RV z vs 펀딩 ρ −0.03~−0.11(독립), vs C1 베이시스 ρ≈0(독립)
- [F2/X6] 가용성 실측(임계값 선택 아님): 공포 극단 B80은 s1 115일·ETH s2 104일 풍부, 탐욕은 BTC s2에만 16일 — 저파워 구조 사전 고지. PREREG_f2x6_fng_extreme_2026-10-12.md 작성·고정 (D−1 확정값 인과 매핑, B90/B80 역발상, 24셀 Holm)
- [F2/X6] f2x6_fng_extreme_diag.py 첫 실행에서 fg_days=4 이상값 → FG 타임스탬프 환산 버그(초 단위 ts를 //1000 후 //86400 처리)를 quality 필드로 **판정 전 발견** → 수정 후 재실행(품질 게이트 실적 기록)
- [F2/X6] 측정 결과: Holm 통과 6셀(전부 h4) **전부 음수**(순16 −14.6~−17.3bp), OOS 0/8 → PASS=False. 엔진 교차검증 net>0 0/24(양 규모) → FAIL 재확증. 추종 반대 통제의 +71bp(BTC s2 B90 h48)는 단일 탐욕 일 1건(유효n=2) 인공물 + 유효 구간 부호 불일치(+6.2/+15.2/−29.4) → X1-R과 달리 재검증 과제 미등록(탐색 관측 엄격 등급화 도입)
- [F2/X6] 패밀리: FG vs 펀딩 z 스피어만 ρ=+0.198(p=2.7e-18, 약결합 — 비중복), FG vs RV z ρ≈−0.002(독립)
- 장부 갱신: measurement_queue_status.md에 L3·F2/X6 FAIL 행 추가, 잔여 큐 X5 → L1 → X2 → X7 순서로 갱신. 보고서 v9 작성 (측정 9건 전부 FAIL, 탐색 관측 등급화 기준 명시)

Stage Summary:
- 이번 턴 2사이클(L3, F2/X6) 전부 FAIL — 측정 누적 9건 전부 FAIL, 승격 후보 0건 유지
- v9 핵심 전환: 가격·파생·변동성·센티먼트 4개 패밀리를 모두 정식 측정 완료 — "무작정 희망" 계열 소진. 남은 경로 3가지로 수렴: ①대형 이벤트(L1 만기/X2 매크로) ②N2 maker 실행 계층 ③게이트류(리스크 관리)
- 유일한 비용 돌파 후보는 여전히 X1-R(ES/NQ → ETH 12h 역방향, 엔진 순익 +21.7bp) — 신규 구간 데이터 필요해 대기
- 다음 루프: X5(스테이블코인, 로컬 완비) → L1(만기 효과, 48계약 완비) — 둘 다 추가 다운로드 0원
---
Task ID: 20
Agent: Z.ai Code (main, 목표 루프 사이클 8)
Task: L1 선물 만기(납품) 효과 진단 측정 + X7 상장공지 데이터 확보, 보고서 v12 갱신

Work Log:
- 큐 상태 확인: X5까지 완료(Task 19), 잔여 L1(로컬 완비)→X2→X7. L1을 본 사이클 1순위로 확정(다운로드 0원), X7은 데이터 수집 병행
- [L1] PREREG_l1_expiry_2026-10-12.md 작성·고정 — **§0에 구조적 한계를 측정 전 사전 고지**: 분기 납품 이벤트 ~20건 → valid_n≥30 구조적 불가 → 승격 후보 아닌 진단 등급(효과크기 문서화). 가설: 납품 전야 납품계약 베이시스 fade(프리미엄→납품 후 롱/디스카운트→숏), W∈{4,24}h × h∈{4,16,48} = 24셀
- [L1] l1_expiry_diag.py 작성·실행: 이벤트 19건(BTC 5+3, ETH 5+6 — PREREG 예상 20과 ±1, BTC 240329 납품일이 구간 종료 후). PASS=False(예상대로), Holm 0/24, valid_n≥30 셀 0
- [L1] 품질 게이트: family 사전검증 초기 실행에서 2건 버그 발견(①premium close를 비율로 오환산 — 실제는 프리미엄율 자체 저장 ②BTC+ETH 이벤트 미필터 교차 오염) → 둘 다 정보성 출력에 한정, 주 판정 무영향 → 수정 후 재실행. 계약 파일 last_bar vs t0(08:00 UTC) 일치 16/24(quality 기록, 규칙 변경 없음)
- [L1] 진단 판독: fade 중앙값 +11.7bp(< 정액 16bp < 엔진 33~37bp), 엔진 net>0 10/24($10k)·6/24($100k)이나 전부 ETH span1 n=5 이상값 셀(1~2건 이벤트 지배) → 탐색 관측 등록 기준(X1-R n 22~26) 미달로 미등록. **과학적 발견: 납품 전야 베이시스는 이미 수렴(|b_pre| 수~20bp) + BTC W24에서 퍼페 프리미엄과 ρ=0.976 → 만기 효과는 X3/C1 패밀리 재포장(독립 차원 없음)**. 세션 통제: 비납품 금요일 08:00 롱 h48 +25.6bp(n=65)
- [L1] l1_engine_crosscheck.py 작성·실행 — 엔진 수치 비용벽 문서화용으로 기록(승격 불가 구조 재확인)
- [X7] x7_download_announcements.py: 최초 실행 0건(응답 파싱 오류 — data.catalogs[0].articles 형 미반영) → 엔드포인트 3종 probe 실측으로 catalogs형+releaseDate 보유 확인 → 수정 후 재실행: **2,280건 본문 확보**(research/x7_listing_announcements.json, 2017-07-21~2026-10-12, 전 페이지 200 OK, 중복 0). 제목 분류 실측: Will List/Launch 882 · add_pairs 545 · collateral 15 · delist 10 · other 828
- 장부 갱신: measurement_queue_status.md(L1 행+X7 데이터 자산+큐 재정렬), 보고서 v12 작성(측정 11건, 과학적 발견 ⑫~⑬ 추가)
- 참고: worklog에 Task 17 중복 기록 스트림 존재 확인 — 본 Task는 최대값(19) 다음인 20으로 부여

Stage Summary:
- L1 FAIL(구조적·진단 등급) 확정 — 측정 11건 전부 FAIL, 승격 후보 0건 유지
- 신규 구조적 지식: 이벤트 빈도형 전략(분기 납품 19건)은 valid_n≥30 체계와 원천 양립 불가 → X2(매크로 ~110건)에도 사전 경고 적용. 만기 효과는 프리미엄 패밀리 재포장(ρ=0.976)으로 정보 차원 소진
- X7 데이터 완비(2,280건·882건 Will List/Launch) — 다음 사이클 1순위. X1-R·F2/X6-R은 신규 구간 데이터 필요로 대기 유지
- 다음 루프: X7 사전등록→측정(이벤트 n 충분 882건) → FAIL 시 X2(셀 설계에 윈도우 조건화 반영)
---
Task ID: 21
Agent: Z.ai Code (main, 목표 루프 사이클 8-후반)
Task: X7 Binance 상장 예고 공지 이벤트 측정 (사이클 8에서 2번째 후보), 보고서 v13 갱신

Work Log:
- 사용자 자율 루프 지시("대답 기다리지 말고 계속 루프")에 따라 Task 20에 이어 동일 세션에서 X7 측정 진행(루프당 1~2후보 한도 내 2번째)
- [X7] PREREG_x7_listing_events_2026-10-12.md 작성·고정: 1차 클래스 A = 제목 regex `Will List|Will Launch`(사전 실측 883건), 방향 = **롱 고정**(리스크온 가설 — 방향 쇼핑 방지), δ∈{0,4,16}봉 진입 지연 × h∈{4,16,48} = 36셀, 4h 클러스터 dedup(첫 공지만), valid_n = n/h 관행 준용. §6에 h48 셀 구조적 PASS 불가(요건 n≥1,440 vs 883건) 사전 고지(L1 선례)
- [X7] x7_listing_events_diag.py 작성·실행: f-string 중첩 이스케이프 SyntaxError 1회 수정 후 정상 실행. 이벤트 883→dedup 798(축소 85), 구간 내 520건(BTC 123+105, ETH 123+169). **Holm 0/36, 1차 방향(롱)이 원시에서 이미 0~음수** — h4 총bp −1.2~+2.3bp, h16/48 −8~−44bp(구간 드리프트 대비 열위)
- [X7] 패밀리 사전검증: 공지일 F&G 평균 49.9 vs 전체 45.7 (t=5.15, p=3.2e-07, n=795) — **리스크온 군집 통계적 확정**에도 주류 하락 → 센티먼트 효과보다 자금회전 효과 지배(핵심 과학적 발견)
- [X7] 반대방향(공지 후 숏) h48 원시 +27~44bp 관측되나 valid_n 2.6(구조 미달)+Holm 0+사후 발견 → 엄격 등급화 원칙(F2/X6-R 선례)에 따라 탐색 관측 미등록
- [X7] x7_engine_crosscheck.py: 엔진 net>0 **0/36(양 규모)**, 엔진비용 $10k 31~38bp — FAIL 재확증
- 장부 갱신: measurement_queue_status.md(X7 행, 큐를 "X2 = 방향 원리 사실상 마지막 항목"으로 재정렬), 보고서 v13 작성(측정 12건, 과학적 발견 ⑭~⑯)

Stage Summary:
- X7 FAIL 확정 — 측정 12건 전부 FAIL, 승격 후보 0건 유지
- **방향 원리 큐 사실상 소진**: 잔여 측정 가능 항목은 X2(FOMC/CPI) 하나 — 날짜 큐레이션 필요 + valid_n 구조 한계로 윈도우 조건화 설계 필수. 나머지(L2/C7/C9/S1-R4/F4/P3)는 전부 데이터 제약 보류
- X2 이후 경로: 비겹침 신규 원리 스캔 → 불가 시 '방향 원리 고갈' 결론 + N2 지정가 실행 계층 / P3 데이터 누적 대기 / 포트폴리오 계층 전환 보고로 루프 종료(cron 지시서 §6 준용)
- 다음 루프: X2 날짜 큐레이션 시도 → 성공 시 윈도우 조건화 PREREG→측정, 실패 시 루프 종료 보고 작성

---
Task ID: 22
Agent: Z.ai Code (main, cron 목표 루프 Job 453321 — 최종 사이클)
Task: 루프 사이클 9~10 — X2 매크로 이벤트 측정 + 비겹침 신규 원리 스캔(X4/F3 발굴·측정) + 방향 원리 고갈 확정 및 루프 종료 보고

Work Log:
- worklog·큐 상태 확인: 측정 12건 전부 FAIL, 잔여 X2(마지막 항목). 사용자 미응답 질문 2건("가능성 보이는 전략", "md v1/v2 부재 이유") 답변 의무 확인
- [X2 데이터] federalreserve.gov FOMC 캘린더 HTTP 200·165,751B 실측 — 성명서 PDF 파일명(monetaryYYYYMMDDa1)에서 **실제 성명일** 46건 추출(2021~2026, 연 8건 규칙 검증). CPI: web.archive.org 타임아웃·mementoweb DNS 실패·fred.stlouisfed.org 접속 실패·TE guest 410 실측(아카이브 경로 전면 불통) → GitHub API 검색으로 abusadat/CPI-release-dates 확보(HTTP 200, 2010~2025 연 12건, 유명일 5건 교차검증 일치: 2021-05-12·2022-06-10·2022-11-10·2024-03-12·2024-12-11)
- [X2 데이터 정직성] 2025 셧다운 영향을 웹 검색으로 실측: 9월 CPI 실발표 2025-10-24(지연), 10월 CPI 미발표(수집 중단 — 제외), 11월 CPI 2025-12-18 08:30 ET. 2026 4건(01-13·02-13·03-11·04-10)은 FRED release calendar 실측. 스케줄→실측 보정 2건·제외 1건을 quality 필드에 기록(fail-closed 큐레이션)
- [X2] 사전등록 고정: PREREG_x2_macro_events_2026-10-12.md — §0에 구조적 사전 고지(구간당 이벤트 17~28건 → valid_n=n/h<30 → 승격 구조적 불가, 진단 등급 L1 선례). 신호: 사후반응 추종(방향=sign(W봉 수익), 방향 쇼핑 방지), {FOMC,CPI}×W{1,4}×h{4,16,48}×2심볼×2구간=48셀
- [X2] x2_macro_events_diag.py 실행: 구간 내 FOMC 38+CPI 60건. **§0 사전 고지대로 valid_n<30 구조적 PASS 불가 확정** — Holm 0/48. 정보성: 발표봉 |R| 중앙값 127~140bp vs 일반봉 17bp(7~8배), 반응 상승 비율 36~62%(방향 편향 부재). 엔진 최고 셀(BTC s2 FOMC W1 h16) 원시 +148bp·엔진 +133bp($10k)이나 n=6 이벤트 지배 → X1-R 기준(n 22~26) 미달로 탐색 관측 미등록(엄격 등급화)
- [X2] x2_engine_crosscheck.py: 엔진 net>0 14/48($10k)·7/48($100k) — 최고 셀 외 전부 미약. FAIL 확정(진단 등급)
- [스캔] cron 지시 §6 비겹침 신규 원리 스캔: r3_master_summary 재확인 결과 **P0 후보 X4/F3(BTC-ETH 스프레드 공적분 1레그)이 측정 목록에서 누락**되어 있음을 발견(x4 스크립트·결과 파일 부재 실측) — 유일한 측정 가능 비겹침 후보. L2는 이벤트 빈도형(연 2~6회 크로스)으로 valid_n 구조적 불가(L1/X2 선례) 판정, C7/C9/S1-R4/F4는 데이터 미확보 유지
- [X4/F3] 사전등록 고정: PREREG_x4f3_pair_spread_2026-10-12.md — S=ln(ETH)−ln(BTC) z(L∈{96,288,960,2880}) |z|≥thr{1.2816,1.6449} → 평균회귀 1레그(ETH: z− 롱/z+ 숏, BTC: z− 숏/z+ 롱), 레그2×L4×thr2×h3×구간2=96셀, 2레그 동시거래 금지(r3 제약) 준용, C1 진입 규약(신호봉 t→t+1 시가, 연속 신호 허용)
- [X4/F3] x4f3_pair_spread_diag.py 실행(공통봉 49,728 정렬·spread_nan 0): **Holm 통과 19셀(프로그램 14건 측정 중 최다) — 전부 음수 방향**. 스프레드 평균회귀는 통계적으로 실재하나 Holm 셀 원시 −1.7~+4.1bp, net16>0 & valid_n≥30 셀 **0/96**
- [X4/F3] x4f3_engine_crosscheck.py: 엔진 net>0 **0/96(양 규모)**, 최고 −28.3bp($10k)/−68.1bp($100k) → FAIL 재확증. N2 maker로도 구원 불가(원시 ≤4bp < maker 비용)
- [종료 판정] 측정 누적 14건(P1·X3·F1·C1·C4·C5·X1·L3·F2/X6·X5·L1·X7·X2·X4/F3) 전부 FAIL + 스캔까지 완료 → cron 지시 §6 '방향 원리 고갈' 조건 충족 확정
- 장부 갱신: measurement_queue_status.md(X2·X4/F3 행 + 남은 큐 섹션을 '소진 확정·루프 종료'로 교체), 보고서 **v14** 작성(파일명 계보 정직 기록 포함 — v1/v2는 다른 파일명으로 존재: NEW2_분석보고서.md→전략리서치_보고서.md→strategy_research_v2.md→v3~v14)

Stage Summary:
- **루프 자체 종료**: '실전 전략 승격 후보'는 현재 확보 가능한 데이터·원리 공간 내에서 미발견(측정 14건 전부 FAIL, 승격 후보 0건). cron 지시 §6에 따라 N2 지정가 실행 계층 / P3 90일 데이터 누적 대기 / 포트폴리오 계층 전환 / X1-R·F2/X6-R 신규 구간 데이터 축적 후 재검증의 4경로 보고와 함께 종료
- 루프 재개 트리거: ①X1-R·F2/X6-R 재검증용 신규 구간 데이터(측정 구간 종료 2026-04-20 이후, ETH 잠금 2026-04-28~08-31 제외) 축적 ②사용자의 새 목표 지시(N2·포트폴리오 계층 등)
- 최종 과학적 자산: ①비용 벽(31~41bp) vs 방향 엣지 실측 분포(±1~18bp)의 체계적 대조 ②매크로 발표봉 정보량 7~8배·방향 편향 부재 실측 ③BTC-ETH 스프레드 평균회귀 통계적 실재·경제성 0 실측 ④검증 불가 날짜 배제 원칙의 이벤트 데이터셋(FOMC 46+CPI 195) ⑤X1-R(+21.7bp)·F2/X6-R(+36.2bp) 탐색 관측 2건
- 산출물: PREREG_x2·x4f3, x2_prep_events.py, x2_macro_events_diag.py, x2_engine_crosscheck.py, x4f3_pair_spread_diag.py, x4f3_engine_crosscheck.py, results/x2_*·x4f3_*, research/x2_*(원본 2건+가공 1건), 보고서 v14

---
Task ID: 23
Agent: Z.ai Code (main, cron 목표 루프 Job 453321 재개 + 사용자 지시 "루프를 더 돌려")
Task: 사용자 미처리 요구 3건 처리(단일 통합 md, v1/v2 계보 답변, 루프 재개) + 큐 감사로 C3 발굴·측정 + 보고서 v15 작성

Work Log:
- worklog 전수 재독독: 측정 14건 전부 FAIL·Task 22 루프 자체 종료 상태 확인. 사용자 미응답 질문(가능성 전략, v1/v2)과 신규 요구(통합 md, 루프 계속) 처리 착수
- [계보 실측] 보고서 파일 전수 확인: v3~v14만 존재(v1/v2 부재), 초기 보고서는 다른 파일명(NEW2_분석보고서.md 10-09 → 전략리서치_보고서.md 10-10 → strategy_research_v2.md 10-11)으로 존재 — v3부터 번호를 이어받은 경위를 worklog Task 3/4/6과 타임스탬프로 교차 검증해 사용자에게 근거 답변
- [큐 감사] 라운드2(web_research_round2.md) C1~C9 × PREREG × 결과파일 4중 대조: **C3 코인베이스 프리미엄이 우선순위 3위·데이터 200 OK 실측 상태로 누락**됨을 발견(measurement_queue_status에 C2/C3/C6/C8 언급 0건) — Task 22 '고갈' 결론의 공백 확인 → 루프 재개 근거로 C3 정식 측정 결정
- [C3] 사전등록 고정: PREREG_c3_coinbase_premium_2026-10-13.md (프리미엄 수준 z |z|≥1.2816 양측 추종, L∈{96,288,960,2880}, 48셀 Holm, 잠금 완전 제외 + z윈도우 오염 차단, 종점 2026-10-12 00:00 UTC — 10-12 일간 아카이브 미발행 404 실측)
- [C3 데이터] Coinbase BTC/ETH-USD 15m 193,908/193,907캔들(647+647 요청 전부 200, 실패 0 — 최초 스크립트의 청크 오류(30캔들/요청) 수정 후 재수집) + Binance spot 15m 154파일(헤더 행 스킵·µs→ms 정규화 버그 수정) + 퍼페 15m 백필
- [C3 데이터 공백 발견] 로컬 kline 전수 대조에서 **BTC 2022-09~2023-02(6개월), ETH 2022-09~2024-05(21개월) 결손** 실측(X3/C1 당시 span이 결손 구간을 피해 정의된 것으로 추정) → 27개월 백필 완료(27/27 HTTP 200) — kline 세트 완전화, 잠금 월(BTC 2024-04/05, ETH 2026-05~08)은 의도적으로 미수집
- [C3 측정] c3_coinbase_premium_diag.py: Holm 통과 20셀 **전부 음수**(순16 −10.9~−18.6bp), 동일설정 양수 0/4 → PASS=False. c3_engine_crosscheck.py: 엔진 net>0 **0/48(양 규모)** → FAIL 재확증. 데이터 완전화 전후 ETH s1 L2880h48 순16 +18.83→+13.84(결손 보완이 유리 셀 축소 — 자료 완전성이 판정 강화)
- [C3 패밀리] C3 z vs C1 베이시스 모멘텀 z ρ −0.02~−0.06, vs 펀딩 z ρ −0.04~−0.05 — 교차 베뉴 정보원 사실상 독립인데도 독자 FAIL
- [보고서] 단일 통합 보고서 **v15** 작성 — v14 전체 내용 무손실 포함 + C3 사이클 + 파일명 계보 섹션(§0 직후 인용블록) + 측정 15건 갱신
- [장부 갱신] measurement_queue_status.md에 C3 행 + Task 23 갱신 섹션 + C2의 N2 계층 분리 관리 명시

Stage Summary:
- 사용자 질문 3건 종결: ①가능성 전략 = 승격 후보 0건, 실측 엣지는 X1-R(+21.7bp 엔진)/F2/X6-R(+36.2bp 엔진) 탐색 관측 2건뿐(표본 미달·신규 데이터 대기) ②v1/v2는 미생성이 아니라 다른 파일명으로 존재(전략리서치_보고서.md=strategy_research_v2.md 계보) ③단일 통합본 v15 완성
- C3 FAIL로 **측정 15건 전부 FAIL — '방향 원리 고갈' 결론 완전화·루프 최종 종료**. 잔여: N2(사용자 결정)/P3(누적 대기)/X1-R·F2/X6-R(데이터 축적)/포트폴리오 계층
- 신규 데이터 자산: Coinbase 15m 프리미엄 시리즈(BTC/ETH 2021-04~2026-10), spot 15m 154파일, kline 완전화(27개월 보완)
- 산출물: PREREG_c3, c3_download_*.py, c3_coinbase_premium_diag.py, c3_engine_crosscheck.py, results/c3_*, research/c3_*_download_log.json, NEW2_전략연구_보고서_v15.md
---
Task ID: 24
Agent: Z.ai Code (main, cron 목표 루프 Job 453321 재개 — X1-R 재검증 사이클)
Task: v15에 등록된 유일 잔여 실측 경로인 X1-R 재검증 실행(재개 트리거 도달 확인) → 폐기 종결 + 보고서 v16 작성

Work Log:
- worklog·큐·데이터 상태 실측: 요약 컨텍스트가 낡았음(v6 시점)을 확인 — 실제 최신은 Task 23(v15, 측정 15건 FAIL·고갈 결론). v15에 등록된 재개 트리거(X1-R·F2/X6-R 신규 구간 데이터 축적)의 도달 여부를 데이터 실측으로 판정
- [재개 근거 실측] BTC 퍼페 15m 2026-01~09 월간+10월 일간 1~11(C3 백필)·ETH 2026-04/09+10월 일간 보유 확인(잠금 월 2026-05~08 의도적 미수집). ES/NQ 1h 2024-05-19~2026-10-12(X1 원측정 파일). **BTC가 ES/NQ 신호로 한 번도 측정되지 않은 홀드아웃 자산임을 확인** — 자산 일반화 검증(유효n≈31~33, Holm 가능) 신설 가능. 다운로드 불필요(전부 로컬)
- [사전등록 고정] PREREG_x1r_reverify_2026-10-13.md 측정 전 작성: Config R(ETH span2 재현 — 판정 아님·구현 검증)/A(BTCUSDT 2024-06-19~2026-04-20 자산 일반화 — BTC 잠금 2024-03-25~06-18 구조적 배제)/C(BTCUSDT 2026-04-21~2026-10-12 순수 OOS)/B(ETHUSDT 2026-04-21~04-27+2026-09-01~10-12 잠금 제외 2세그먼트·정보성), 구성당 4셀 {ES,NQ}×{L24,L120}×h48 역방향(사전 고정), 게이트 G2a(원시 ≥3/4 AND 엔진$10k ≥3/4)/G2b(신규 원시 ≥3/4 AND 순16 ≥2/4)/G1(Config A Holm+쌍방 순16>0)/G3(OOS 후반40%) 사전 고정. **검정력 사전 고지**: 원시 +54bp 재현 시 Holm 통과 필요 평균 ≈110~120bp — 통과 확률 낮음을 명시. 통과해도 cron §5 '승격' 아닌 조건부 후보 보고까지(X1 PREREG §6 선례)
- [측정] x1r_reverify_diag.py 작성·실행: **Config R 재현 4셀 전부 0.00bp 차이 완벽 일치**(구현 신뢰 확보). Config A: 역방향 원시 4/4 양수(+14.71~+27.05bp, 유효n 25.5~32.9) — ETH 원관측(+54.3/+47.9bp)의 약 절반, Holm 0/4(p 0.75~0.98), 정액 순16>0 3/4. Config C: 원시 3/4 양수, 순16>0 1/4(ES L24 +18.22bp 유일, NQ L120 −32.13bp 반전). Config B: 유효n ≤1.6 노이즈. 드랍 0(fail-closed 결측 없음)
- [엔진 교차검증] x1r_engine_crosscheck.py: **Config A 엔진($10k) net>0 0/4**(최고 ES L120 −3.62bp, 엔진비용 30.4~30.7bp), Config C 0/4(최고 −1.65bp), $100k 전멸. **Config R(원 ETH span2)만 엔진 양수 4/4($10k, ES L24 +21.67bp — 원 X1-R 관측값 그대로 재현), $100k에서는 0/4**
- [정합성 정정] 진단 스크립트 초기 구현이 G2a를 정액 순16으로 평가한 것을 PREREG 정의(엔진 $10k)로 정정 후 재실행 — 측정값 불변(결정론), 판정 불변(G2a FAIL→종합 X1R_DEAD)
- [게이트 판정(PREREG 사전 고정)] G2a FAIL(엔진 0/4)·G2b FAIL(순16 1/4<2)·G1 FAIL(Holm 0)·재현 OK → **X1-R 폐기 종결(X1R_DEAD)**. 부분 통과(G2a 정액 기준)였어도 사전 고정 규칙에 따라 폐기 — 게이트 이동 없음
- [과학적 결론] 'ES/NQ 모멘텀 극단 후 암호자산 하락 드리프트'는 자산·시간 불문 실재(추종 통제 −21~−111bp 일관)하나 되돌림 수익은 ETH span2 국소(레짐 특이) 크기 — **사후 발견(opposite control)의 표본 내 과대평가가 복제에서 실증된 사례**로 기록(발견 ㉑)
- [장부 갱신] measurement_queue_status.md: X1-R 행(측정 16건째 FAIL) + Task 24 갱신 섹션 + 잔여 경로 갱신(F2/X6-R만 대기)
- [보고서 v16] v15 전문 무손실 유지 + §0 요약(16건·탐색 관측 잔여 1건·발견 ㉑) + §4.3 X1-R 행 + §4.4 발견 19 + §5 큐 갱신 + §7·§8 잔여 경로 갱신 + §9 산출물 + §10 사이클 로그 12행 + 종료 기록 2차 추가

Stage Summary:
- X1-R 폐기 종결 확정 — 측정 누적 16건 전부 FAIL, 승격 후보 0건, '방향 원리 고갈' 최종 완전화
- 탐색 관측 잔여는 F2/X6-R 1건(ETH 극단공포→3일 롱, 엔진 +36.2bp) — 단 BTC는 원측정부터 방향 모순(−201.9bp)이고 ETH 잠금 해제 축적분(약 7주)은 유효n 3~8로 검정력 없음 → 2027-01경 충분 축적 후 단독 재검증이 유일 경로
- 재현 검증 설계(Config R)로 구현 신뢰를 판정과 분리해 확보 — 원 X1 측정의 오류 가능성 배제 후 폐기라 기록의 정직성 강화
- 루프 상태: 종료 유지. 재개 트리거: ①F2/X6-R 데이터 축적(2027-01경) ②사용자 신규 지시(N2 지정가 실행 계층/포트폴리오 계층/P3 누적 완료)
- 산출물: PREREG_x1r_reverify_2026-10-13.md, x1r_reverify_diag.py, x1r_engine_crosscheck.py, results/x1r_reverify_results.{json,md}, results/x1r_crosscheck_results.{json,md}, NEW2_전략연구_보고서_v16.md
---
Task ID: 25
Agent: Z.ai Code (main, cron 목표 루프 Job 453321 재개 — 큐 감사 2차 + C7 측정)
Task: 종료 결론의 감사 공백 재점검 → C7 장부 오분류 정정·정식 측정(96셀) + 보고서 v17 작성

Work Log:
- worklog·큐 장부 재독독: 측정 16건 전부 FAIL·Task 24 루프 종료 상태 확인. cron 재발행(Job 453321, 04:07)에 따라 종료 결론의 감사 공백 잔존 여부를 재점검하는 관점으로 착수
- [감사 발굴] 장부와 라운드2 문서 교차 대조에서 **C7 불일치 발견**: 장부는 "데이터 미확보(금지 목록)"로 분류했으나 web_research_round2.md 96-97행에 Bybit/OKX 엔드포인트 200 OK 실측 기록이 존재. r3_master_summary에 C7/Bybit/OKX 언급 0건 실측 — Task 22 스캔이 r3 기반이라 발생한 오분류로 판정 (C3·X4/F3에 이은 세 번째 감사 발굴)
- [재실측] c7_probe_data.py: Bybit linear 15m BTCUSDT 2020-06~·ETHUSDT 2021-04~ 실데이터 200(retCode=0, 최신봉 2026-10-13 03:20), OKX BTC/ETH-USDT-SWAP 2021-06 시점 실데이터 200 — 라운드2 실측 재현 성공, 오분류 확정 (research/c7_probe_log.json)
- [사전등록 고정] PREREG_c7_cross_venue_spread_2026-10-13.md 측정 전 작성: S=ln(상대 베뉴 퍼페/Binance 퍼페) 수준 z |z|≥1.2816, L{96,288,960,2880}×h{4,16,48}, **1차 방향=정상화 단일 레그(저평가 레그 매수) 사전 고정**(추종은 반대 통제로만 — 방향 쇼핑 방지), 96셀 Holm(베뉴2×심볼2×세그먼트2×12), 잠금 완전 제외+z윈도우 오염 차단, §1에 단일 레그의 구조적 리스크(수렴이 고가 베뉴 하락으로만 일어나면 수익 0) 사전 고지
- [데이터] c7_download_venues.py: Bybit 순방향 윈도우 워크 + OKX 역방향 after 커서(재개 가능·200요청 중간 저장). **품질 게이트 실적: Bybit 초기 수집에서 192갭 발견 → v5 kline [start,end] 폐구간·최신 1000개 반환 특성으로 각 윈도우 첫 봉 스킵됨을 원인 규명 → 999봉 윈도우 수정 후 재수집**. 최종: Bybit 193,921봉×2(갭 0) + OKX 194,000봉×2(갭 0), 실패 0 (research/c7_download_log.json)
- [측정] c7_cross_venue_spread_diag.py: **Holm 통과 49/96 전부 음수 방향**(순16 최고 −5.74bp), net16>0 0/96, OOS 양수 0/64 → PASS=False. c7_engine_crosscheck.py: 엔진 net>0 **0/96(양 규모)**, 엔진비용 $10k 40.8~45.9bp → FAIL 재확증
- [탐색 관측 검토] 추종(통제) 방향 최고 셀(OKX ETH s2 L960 h48 원시 +28.67bp) 검토 — valid_n 12.8(X1-R 등록 기준 22~26 미달)+동일 조합 4구성 부호 불일치(−25.8/−12.2/−13.3/+12.7)+p_ctrl=1.0 → 미등록(엄격 등급화, X7 선례)
- [과학적 발견] ①교차 베뉴 퍼페 스프레드 규모 최초 실측(std 3.1~5.6bp, p01~p99 ±9~11bp — 차익거래 자체가 taker 비용의 1/4~1/10, 라운드2 사전 추정 실측 확정) ②정상화 단일 레그 gross 최고 +10.26bp < 정액 16bp — 수렴의 상당 부분이 고가 베뉴 하락으로 발생(사전 고지 리스크 실증) ③X4/F3(자산 간)+C7(베뉴 간) 동일 결론 — 스프레드 평균회귀는 15m에서 통계적 실재·경제적 부재의 일반 패턴 ④패밀리: vs C1 ρ −0.28~−0.11(펀딩 메커니즘 공유), vs C3 ρ 0.02~0.09(독립)
- [장부 갱신] measurement_queue_status.md에 Task 25 갱신 섹션 추가(C7 행 + 오분류 정정 경위 + C1~C9 전원 처분 완료 명시)
- [보고서 v17] v16 전문 무손실 유지 + §0 요약(17건·발견 ㉒) + §4.3 C7 행 + §4.4 발견 20 + §5 큐 갱신(C7 불가→완료(FAIL)) + §6 데이터 자산 + §7 감사 3건·문서 간 불일치 교훈 + §9 산출물 + §10 사이클 13행 + 종료 기록 3차 추가

Stage Summary:
- C7 FAIL 확정 — 측정 누적 **17건 전부 FAIL**, 승격 후보 0건, '방향 원리 고갈'이 문서 계층까지 완전 검증된 최종판으로 확정
- **라운드2 C계열 C1~C9 전원 처분 완료**(C1/C3/C4/C5/C7 측정 FAIL, C2→N2 계층 분리, C6=P3, C8=Farside 403, C9=옵션 미확보) — 감사 공백 소진. 감사 3건(X4/F3, C3, C7) 모두 '문서 간 불일치'에서 발굴 — 큐 장부 분류의 원 출처 교차 검증 필요성이 방법론 교훈으로 기록됨
- 신규 데이터 자산: Bybit/OKX 퍼페 15m 5.5년 완비(193,921/194,000봉×2, 갭 0) — 향후 교차 베뉴 계열 측정의 인프라
- 루프 상태: 종료 유지(최종). 재개 트리거: ①F2/X6-R 재검증용 ETH 신규 구간 데이터 축적(2027-01경) ②사용자 신규 지시(N2 지정가 실행 계층/포트폴리오 계층/P3 누적 완료)
- 산출물: PREREG_c7_cross_venue_spread_2026-10-13.md, c7_probe_data.py, c7_download_venues.py, c7_cross_venue_spread_diag.py, c7_engine_crosscheck.py, results/c7_cross_venue_spread_results.{json,md}, results/c7_crosscheck_results.{json,md}, research/c7_probe_log.json, research/c7_download_log.json, data/bybit/·data/okx/ (4 CSV), NEW2_전략연구_보고서_v17.md
---
Task ID: 26
Agent: Z.ai Code (main, cron 목표 루프 Job 453321 재개 — 큐 감사 4차 + C9 측정)
Task: 종료 결론의 감사 공백 재점검 → C9 장부 오분류(부분) 정정·정식 측정(48셀) + 보고서 v18 작성

Work Log:
- worklog·큐 장부 재독독: 측정 17건 전부 FAIL·Task 25 루프 종료 상태 확인. cron 재발행(Job 453321, 06:07)에 따라 감사 공백 4차 재점검 착수
- [감사 발굴] 장부의 C9 "옵션 미확보" 분류 vs 라운드2 문서 70·98행 "Deribit DVOL 200 OK 실측(2022-01 시간봉)" 기록 간 불일치 — X4/F3·C3·C7에 이은 네 번째 '문서 간 불일치' 발굴
- [재실측] Deribit get_volatility_index_data: resolution=1H 전 구간 빈 배열·4H "unsupported resolution"·**1D 실데이터 200** — 현재 API는 일간만 제공(라운드2 당시와 달라진 인프라 변동 실측, research/c9_dvol_probe_log.json)
- [데이터] c9_download_dvol.py: DVOL 일간 BTC/ETH 각 **2,029일**(2021-03-24~2026-10-12, 갭 0, 69요청 전부 200) → data/dvol/. 품질 게이트 실적: 다운로더 종점 타임스탬프 환산 오류(END_MS가 2025-10-12로 1년 오차)를 첫 다운로드 결과 불일치로 발견 → PREREG 고정 종점(2026-10-12 00:00 UTC)에 맞게 정정 후 재다운로드·갭 0 검증(측정 전 정정, 규격 변경 아님)
- [사전등록 고정] PREREG_c9_dvol_2026-10-13.md 측정 전 작성: DVOL 일간 종가 인과 z(L∈{30,90}일), B80(|z|≥1.2816)/B90(1.6449), **z≥+thr 롱/z≤−thr 숏 역발상 사전 고정**(Giot 2005 고 VIX 후 초과수익 + F2/X6 방향 매핑 패밀리), 진입=일 D 00:15~ 신호봉(00:00 봉 제외 — D−1 종가 확정 경계 인과성 보장), h∈{96,288,672}, 48셀 Holm, 잠금 완전 제외+z윈도우 오염 차단, 종점 2026-10-12 00:00 UTC. 25δ 스크우는 무료 역사 부재로 범위 밖(§0 정직 명시)
- [측정] c9_dvol_diag.py: **Holm 통과 0/48 → PASS=False**. $100k에서 Holm 3셀(BTC s2 h96)이나 전부 음수(net −78bp). 고DVOL→h672 반등이 구성 간 일관 관측(정액 순16 BTC s1 +161/유효n 29, BTC s2 +157/18.9, ETH s1 +212/49.6)이나 원시 p 0.071~0.315 전원 무유의
- [엔진 교차검증] c9_engine_crosscheck.py: 엔진($10k) net>0 16/48, **유효n≥22 & 엔진 양수 9셀**(최고 ETH s1 L30B90h672 +85.89bp 유효n 58.1) — 그러나 엔진 기준 원시 p 0.25~0.91 전원 무유의 + 동일 조합 부호 불일치(L30B90h288 BTC s2 −42.1bp) + BTC s1 엔진 참여 게이트 fail-closed(FC 5~10, 2021년 초 저유동성) → **탐색 관측 등록 기준 미달로 미등록**(X7/C7 엄격 등급화 준용)
- [과학적 발견] ①DVOL 패밀리 독립성: vs RVz ρ=0.142, vs FG ρ=0.070, vs 펀딩 ρ=0.046 — 옵션 시장 정보원이 사실상 독립인데도 독자 FAIL(독립 패밀리 9번째 확인) ②'극단 스트레스 후 반등' 구조(X1-R/F2/X6-R)와 정성적 수렴하는 고DVOL→7일 반등 경향 — 통계 미입증 미완의 패턴으로 기록 ③DVOL 수준 참조값: BTC 중앙값 55.3(p01 34.6~p99 108.5), ETH 69.2 ④DVOL API 해상도 축소(1H→1D) 실측 — '데이터 미확보' 분류는 재개 시점마다 재실측 필요(감사 4건 공통 교훈 강화)
- [장부 갱신] measurement_queue_status.md: Task 26 갱신 섹션(C9 행 + 품질 게이트 실적) + 측정 18건 갱신 + 데이터 자산 추가
- [보고서 v18] v17 전문 무손실 유지(diff로 검증 — 교체 앵커 20곳 외 전부 보존, 50,907B→59,818B) + §0 요약(18건·발견 ㉓~㉕) + §4.3 C9 행 + §4.4 발견 21~23 + §5 큐 갱신(C9 불가→완료(FAIL)) + §6 데이터 자산 + §7 감사 4건·재완전화 + §9 산출물 + §10 사이클 14행 + 종료 기록 4차 추가

Stage Summary:
- C9 FAIL 확정 — 측정 누적 **18건 전부 FAIL**, 승격 후보 0건, '방향 원리 고갈' 재완전화
- **라운드2 C계열 C1~C9 전원 처분 + 장부 '데이터 미확보' 분류의 마지막 재실측 여지(C9)까지 소진** — 감사 4건(X4/F3, C3, C7, C9) 모두 '문서 간 불일치' 발굴로, 장부 분류의 원 출처 교차 검증·재개 시점 재실측이 방법론 교훈으로 확정
- 신규 데이터 자산: Deribit DVOL 일간 5.5년 완비(2,029일×2통화, 갭 0) — 내재변동성 패밀리 향후 측정 인프라
- 루프 상태: 종료 유지(최종). 재개 트리거 불변: ①F2/X6-R 재검증용 ETH 신규 구간 데이터 축적(2027-01경) ②사용자 신규 지시(N2 지정가 실행 계층/포트폴리오 계층/P3 누적 완료)
- 산출물: PREREG_c9_dvol_2026-10-13.md, c9_download_dvol.py, c9_dvol_diag.py, c9_engine_crosscheck.py, c9_inspect.py, results/c9_dvol_results.{json,md}, results/c9_crosscheck_results.{json,md}, research/c9_dvol_probe_log.json, research/c9_dvol_download_log.json, data/dvol/c9_dvol_{BTC,ETH}_1d.csv, NEW2_전략연구_보고서_v18.md
---
Task ID: 27
Agent: Z.ai Code (main, 사용자 직접 지시 "다음 루프 진행"으로 루프 재개 — 큐 감사 5차 + X8/X9 측정)
Task: 종료 상태에서 루프 재개 → 감사 5차(C2·L2 장부 불일치 정정) → X8(테이커 플로우 집계)·X9(티켓 크기) 48셀 정식 측정 + 보고서 v19

Work Log:
- worklog·큐 장부 재독독: 측정 18건 전부 FAIL·Task 26 루프 종료 상태 확인. 사용자 지시("다음 루프 진행")가 재개 트리거 ②(사용자 신규 지시) 충족 → Task 27 착수
- [감사 발굴 ① C2 — 다섯 번째] 장부 C2 '수백 GB 아카이브+maker 전제 → N2 의존' 분류 vs 로컬 15m kline 9번 컬럼(taker buy base volume) 존재 실측(klines/BTCUSDT-15m-2021-04.csv 1행: vol=2589.573/tbv=1450.817) — 집계 공격자 플로우는 다운로드 0바이트로 측정 가능. C2 전체 복원 아닌 별개 원리로 X8 신규 ID 등록(정직 구분)
- [X8 사전등록 고정] PREREG_x8_taker_flow_2026-10-13.md 측정 전 작성: R=Σtbv/Σvol(직전 L{16,96}봉) 인과 z(직전 960봉, 스팬 내부 형성 — 세그먼트 독립 보존), B80/B90, 1차 방향=추종 사전 고정(되돌림은 반대 통제만), 48셀 Holm, 잠금 완전 제외, fail-closed
- [X8 측정] x8_taker_flow_diag.py: Holm 통과 18셀 전부 음수 방향(순16 −13.7~−17.5bp), OOS 양수 0/24 → PASS=False. 품질: 4스팬 갭 0·연속성 True·vol=0 최대 5봉. 스크립트 생성 시 잔재 라인 1건 측정 전 제거
- [X8 엔진 교차검증] x8_engine_crosscheck.py: 엔진 net>0 1/48($10k) — 유일 양수 셀(BTC s1 L96B90h48 +0.54bp)도 p=0.982 무유의; $100k 0/48 → FAIL 재확증. 탐색 관측 후보(L96B90h48 BTC s1 정액 +16.96bp n=6385)도 4구성 부호 불일치(BTC s2 −16.21bp)+무유의 → 미등록(엄격 등급화)
- [감사 발굴 ② L2 — 여섯 번째] 장부 '해시레이트 미확보(CoinMetrics 401)' 분류가 현재 시점 불성립 — mempool.space 난이도 전체 467행 200 OK·blockchain.com 해시레이트 일간 200 OK 실측(research/l2_probe_log.json). 단 MA 크로스 이벤트 빈도형(연 2~6회) → valid_n≥30 구조적 불가는 데이터와 무관(L1 19건/X2 17~28건 선례) → 데이터 다운로드·측정 없이 큐 밖 유지 확정('데이터 가능'과 '승격 가능' 구분 판정 기록)
- [X9 사전등록·측정] PREREG_x9_ticket_size_2026-10-13.md: 미사용 '참여자 크기' 차원(평균 티켓 R=Σqv/Σn) — x8 템플릿 준용 48셀, 추종 사전 고정, 패밀리 사전검증 vs 펀딩·RV z 포함. 측정: Holm 19셀 전부 음수(순16 −13.0~−22.3bp), OOS 양수 1/24 → PASS=False. 엔진: net>0 0/48(양 규모 — $10k Holm 26셀·$100k 34셀 전부 음수) → FAIL. 탐색 관측(L96B90h48 BTC s2 +18.13bp)도 4구성 2/4 불일치+FC → 미등록
- [패밀리] X8 z vs 펀딩 z ρ −0.10~+0.21 / X9 z vs RV z ρ −0.08~+0.40(L3 부분 인접)·vs 펀딩 ρ 0.13~0.18 — 독립~부분 인접 정보원 전원 독자 FAIL
- [장부 갱신] measurement_queue_status.md에 Task 27 갱신 섹션 추가(C2 부분 정정·L2 차단 해제·X8/X9 행 + 측정 20건 누적 갱신)
- [보고서 v19] v18 전문 무손실 유지(diff 검증 — 교체 앵커 19행 외 전부 보존, 59,818B→70,383B) + 헤더·§0 요약(20건·발견 ㉖~㉘) + §4.3 X8/X9 행 + §4.4 발견 24~26 + §5 큐 갱신(X8/X9 완료·L2 데이터 해제·C2 국면 분리) + §7 재완전화 + §9 산출물 + §10 사이클 15행 + 종료 기록 5차 추가

Stage Summary:
- X8·X9 FAIL 확정 — 측정 누적 **20건 전부 FAIL**, 승격 후보 0건, '방향 원리 고갈' 재완전화
- **미시구조 집계 3차원(플로우 방향 X8·티켓 크기 X9·변동성 L3) 소진 완료** — C2의 잔여 가치는 N2 maker 실행 계층 결합 시에만 존재함이 실측으로 확정(㉖)
- 감사 5차례 누적(X4/F3, C3, C7, C9, C2부분·L2) — 전부 '문서·데이터 간 불일치' 발굴. 신규 교훈: 장부 분류는 '원리 전체'와 '측정 가능 국면'을 구분 기록해야 함
- 신규 데이터 다운로드 0(X8/X9는 기존 로컬 컬럼 활용) — L2 소스(mempool/blockchain.com)는 200 OK 확인 후 구조 불가로 미다운로드 판정 기록
- 루프 상태: 종료 유지(최종). 재개 트리거 불변: ①F2/X6-R 재검증용 ETH 신규 구간 데이터 축적(2027-01경) ②사용자 신규 지시(N2 지정가 실행 계층/포트폴리오 계층/P3 누적 완료)
- 산출물: PREREG_x8_taker_flow_2026-10-13.md, PREREG_x9_ticket_size_2026-10-13.md, x8_taker_flow_diag.py, x8_engine_crosscheck.py, x9_ticket_size_diag.py, x9_engine_crosscheck.py, results/x8_taker_flow_results.{json,md}, results/x8_crosscheck_results.{json,md}, results/x9_ticket_size_results.{json,md}, results/x9_crosscheck_results.{json,md}, research/l2_probe_log.json, NEW2_전략연구_보고서_v19.md
---
Task ID: 28
Agent: Z.ai Code (main, 사용자 지시 유지+cron 재발행(Job 453321, 08:07) 재개 — 큐 소진 상태의 유일 구조적 경로 N2 측정)
Task: N2 지정가(maker) 진입 실행계층 정식 측정(프로그램 최초 비신호 측정, 32셀) → N2_CLOSED 판정 + 보고서 v20

Work Log:
- worklog·큐 장부 재독독: 측정 20건 전부 FAIL·Task 27 루프 종료 상태 확인. 사용자 지시("다음 루프 진행") 유지+cron 재발행으로 재개 트리거 ②충족 → 큐 소진 상태에서 잔여 구조적 경로 N2를 사용자 결정 대기 없이 정식 측정으로 착수(방향 원리가 아닌 실행계층 측정 — 새 국면)
- [차량 선정 근거 실측] 21건 측정 원기록 전수 대조에서 '원시 엣지>0 & 엔진비용 미달로만 FAIL'한 유일 패밀리 = X1-R Config A(원시 +14.7~+27.1bp vs 엔진 30.4~30.7bp) 확정. C7(+10.3bp)·X4/F3(≤+4bp)·X8(+1~2bp)은 maker 비용(~17bp) 미달로 구원 구조적 불가가 문서화 — N2의 가치 정의를 직접 검정할 수 있는 유일 차량
- [사전등록 고정] PREREG_n2_maker_exec_2026-10-13.md 측정 전 작성: 이벤트=X1-R Config A 규격 무변경 준용(ES/NQ 1h Δz |z|≥1.2816 역방향 사전 고정 — 방향 쇼핑 차단), 32셀 {ES,NQ}×{L24,L120}×δ{0,5,10,20}bp×K{4,16}, 체결=보수적 통과 판정(open 갭→open 체결/low·high 통과→limit 체결/터치 미체결), 홀드 h48 후 taker 청산 고정(maker-maker는 Phase B 제외 사전 고지), 진입 maker 2bp+청산 엔진 1면, promotion-basis 순엣지=체결률×E[순응|체결], 게이트 G-N1(경제성)/G-N2(Holm)/G-N3(전반60+후반40)/G-N4(OOS) 사전 고정, 검정력 사전 고지(Holm 통과 확률 낮음 명시), 무신호 대조군 2,000시점, 신규 다운로드 0바이트, 잠금 구간 일절 미사용
- [측정] n2_maker_exec_diag.py: 품질 — BTC 스팬 64,320/64,320봉 연속성 True, 이벤트 5,581→기본검증 통과 5,580(ES 2,713/NQ 2,867), FC 드랍 0. 스크립트 초안의 잔재 라인 4건(미사용 루프·병합된 이벤트 생성)을 측정 전 발견·제거(Task 26/27 품질 게이트 준용) + 5-튜플 언팩 오류 1건 수정 후 실행
- [측정 결과] **G-N1 PASS(promo 양수 19/32셀 — 최고 ES L24 δ20K16 +10.86bp·체결률 0.823)**, **G-N2 FAIL(Holm 0/32 — t 최고 0.29·p 최소 0.77, h48 수익 분산 지배 — PREREG 사전 고지대로)**, G-N3/G-N4 FAIL(판정 대상 셀 부재) → **종합 N2_CLOSED**. NQ 8셀 전부 promo 음수(−0.3~−4.5bp)
- [엔진 교차검증] n2_engine_crosscheck.py: **독립 벡터화 재구현 — 32셀 체결률 차 0.0·체결분 평균 순응 차 0.0bp 완전 일치**(구현 신뢰). taker-taker 대조군 ES L120 −3.62bp = **X1-R 원값과 0.00 일치 재현**. $100k promo 양수 0/32(청산 참여비용)
- [과학적 발견 ㉙㉚] ①체결률·불리한 선택 최초 정량: 체결률 δ0 98.7~99.6%→δ20 63.2~82.3%(무신호 대조 98.0~99.2%와 동일 — 신호 시점 체결에 구조적 불리 없음), AS는 δ 비례 확대(무신호 롱 −0.5→−7.8bp @δ0→10), 신호 시점 AS는 패밀리별 상이(ES L24 −3.1~−17.6bp 불리/ES L120 +21.4→+3.6bp 유리·δ와 급감/NQ 혼재), 체결 대기 0.02~2.21봉 — **메이커의 실질 제약은 미체결이 아니라 체결 시 조건부 열위** ②비용 절감 실측: maker 왕복 ≈17bp vs taker 30.4~30.7bp — 절감 ~13.5bp($10k)로 X1-R 감쇠 후 엣지를 promo 최고 +10.86bp·19/32셀 양전환 — 그러나 Holm 통과 필요 엣지(원시 ~100bp+)는 21건 최대 관측(+54bp)의 2배 → **N2는 소액 실행 인프라로서 유효, 승격 경로로서 폐기(엣지 크기가 병목의 정량 확정)**
- [장부 갱신] measurement_queue_status.md: Task 28 갱신 섹션(N2 규격·측정·교차검증·발견 전체) + 측정 완료 누적 21건 갱신
- [보고서 v20] v19 전문 무손실 유지(diff 아닌 복사 후 앵커 편집 — v19 반영분 라인 분리 보존 포함) + 헤더(신규 반영분·계보·갱신 이력 16사이클) + §0 요약(21건·발견 ㉙㉚·실행계층 결론 행 신설) + §4.3 N2 행(21건) + §4.4 발견 27~28(㉚개) + §5 큐 갱신(N2 완료·C2 봉인) + §7 재완전화(4항목) + §8 액션 갱신(N2 폐기 완료) + §9 산출물(N2 2행+v19/v20 파일) + §10 사이클 16행 + 종료 기록 6차 추가

Stage Summary:
- N2_CLOSED 확정 — 측정 누적 **21건(방향 20+실행계층 1) 전부 FAIL/폐기**, 승격 후보 0건
- **'방향 원리 고갈 + 실행계층 경로 폐기'로 측정 가능한 모든 경로 소진** — taker 비용벽의 유일 구조적 해결책으로 등록됐던 N2마저 측정으로 폐기되어 루프 종료 근거가 완전화. 핵심 수치: 체결률 79~99.6%(δ≤10bp)·절감 ~13.5bp($10k)는 실재하나 Holm 필요 엣지(~100bp+)의 1/7에 불과 — 엣지 크기 자체가 병목
- 구현 신뢰 이중 확보: 독립 벡터화 재구현 0.0bp 일치 + taker 대조군의 X1-R 원값 0.00 재현 — 구현 신뢰를 판정과 분리한 후 폐기하는 X1-R 선례 절차 준용
- 잔여 경로: P3(90일 누적 대기)·포트폴리오 계층·F2/X6-R(2027-01 데이터 축적)·실전 maker 인프라 도입 여부(사용자 결정 사항 — 전략 발굴과 별개)
- 루프 상태: 종료 유지(최종). 재개 트리거 불변: ①F2/X6-R 재검증용 ETH 신규 구간 데이터 축적(2027-01경) ②사용자 신규 지시(P3/포트폴리오 계층/maker 인프라)
- 산출물: PREREG_n2_maker_exec_2026-10-13.md, n2_maker_exec_diag.py, n2_engine_crosscheck.py, results/n2_maker_exec_results.{json,md}, results/n2_crosscheck_results.{json,md}, NEW2_전략연구_보고서_v20.md
---
Task ID: 29
Agent: Z.ai Code (main, 사용자 지시 "계속 진행해 내 말 기다리지 말고" 루프 재개 — P3 데이터 재실측 + F2/X6-R 조기 점검)
Task: 재개 트리거 ②(사용자 신규 지시) 충족으로 루프 재개 → P3 청산 데이터 소스 재실측(감사 교훈 준용) + F2/X6-R 조기 점검 → 측정 가능 판정 시 정식 측정 + 보고서 v21

Work Log:
- worklog·큐 장부 재독독: 측정 21건 전부 FAIL/폐기·Task 28(N2 CLOSED) 상태 확인. 사용자 지시("계속 진행해 내 말 기다리지 말고")로 재개 트리거 ② 충족 → Task 29 착수
- [사이클 항목 선정] 큐 소진 상태의 준수 경로: ①P3 청산 캐스케이드 데이터 소스 재실측 — 감사 4건 공통 교훈("데이터 미확보 분류는 재개 시점마다 재실측 필요")에 따라 '90일 누적 대기' 판정의 현재 유효성 검증 ②F2/X6-R 조기 점검 — F&G 재다운로드로 ETH 극단공포 이벤트 누적 상태 실측(2027-01 대기 규정의 조기 해제 가능성 검증)
- [P3 재실측] probe 2차(research/p3_liquidation_probe_log.json·p3_liquidation_probe2_log.json): OKX liquidation-orders v5는 200 OK·실데이터 존재하나 begin/end 파라미터를 무시(1년 전 범위 요청에도 최근 ts 반환 — 역사 미지원 실측), history-liquidation-orders 404, BitMEX liquidation 전 심볼·reverse 양방향 빈 배열(폐기), Binance allForceOrders 404, Coinglass 무키 500 → **역사 소스 전부 불가 재확정** → '90일 누적 대기'의 실체가 '직접 수집 외 경로 부재'임을 확정하고 mini-services/liq-collector 실시간 수집기 기동(Binance forceOrder WS BTC/ETH + OKX 60초 폴링 → SQLite bun:sqlite, UNIQUE 인덱스 중복 차단, WAL 재시작 내성, /stats 상태 엔드포인트 — 기동 직후 OKX 최근 38분치 200건 증분·에러 0) — **90일 완료 2027-01-11경**
- [F2/X6-R 조기 점검] F&G(2026-10-12까지 최신 유지)+ETH kline 스팬(2026-10-11까지) 실측: 2026-04-21 이후 극단공포(B80≤20) 26일이 전부 ETH 잠금 구간(2026-04-28~08-31) 내부(2026-06-03~07-08 클러스터 — 극단 이벤트는 클러스터로 발생하는 실증), 잠금 외 신규 구간(2026-09-01~10-12) F&G min 50 — **극단 이벤트 0건 → 재검증 이벤트 추가 0, 2027-01 대기 유지 실측으로 강화**
- [5차 원리 스캔] web-search 4쿼리(OI·시간 계절성·VRP·KP): OI/VRP는 기존 패밀리 인접으로 배제, **김치 프리미엄(KP)만 배제 목록 외 비겹침 신규 원리로 발굴** — 문헌 근거(Seo 2024 mean-reverting 계수 0.96, Choi 2026 비대칭 lag) + Upbit 시세 API 재실측(공지 404·금지 목록과 달리 시세는 별개 공개 엔드포인트 200 OK — 감사 교훈 '원리 전체 vs 측정 가능 국면'의 재적용)
- [KP 데이터] Upbit KRW-BTC/ETH 일봉 2,200일(갭 0) + Binance spot 1d 2,020일(api.binance.com 418 teapot 차단 실측 → data.binance.vision monthly/daily zip 아카이브 우회 — 최근 아카이브는 마이크로초 타임스탬프(16자리)로 변경 실측·보정, zipfile 명시 해제) + Yahoo USDKRW 1,438행 → **KP 합성 1,436일(2021-04-01~2026-10-08, NaN 0)** — data/kp/, research/kp_download_log.json·kp_download_binance_log.json. KP_BTC 중앙값 +1.50%(p01 −1.95/p99 +12.73%), 최근 10일 상승 국면(+2.3%) 실측
- [KP 패밀리 사전검증] KP z vs F&G z **ρ=−0.034(사실상 독립)** / vs 펀딩 z +0.28(L90)·+0.49(L288 부분 인접 — 메커니즘 상이: 선물 파생 vs 현물 국경) / KP_BTC vs KP_ETH +0.997(동일 신호 — 고지) / 이벤트 수 충분(L90 B80 353일)
- [KP 사전등록 고정] PREREG_kp_kimchi_2026-10-13.md 측정 전 작성: KP 일간 z(L∈{90,288}), B80/B90, **1차 방향=정상화 역발상 사전 고정**(z≥+thr 숏/z≤−thr 롱 — Seo 2024+스프레드 정상화 패밀리 정합, 추속은 반대 통제), 진입=신호일 S 00:15 봉(D 확정 경계 00:00 제외), h∈{16,48}, 32셀 Holm, 세그먼트 4구성(잠금 완전 제외+z윈도우 오염 차단), OOS 후반 40%, 저파워 사전 고지(ETH s2 38일→z 워밍업 전부 무효), PASS 조건 사전 고정
- [측정 1차 실행 — 품질 게이트 발견·정정] 초안 실행에서 셀별 n=15,865~25,633(스팬 일수 1,858 초과) — **규격-구현 불일치 발견**: seen_day가 sig_days 카운트만 하고 trades 생성을 차단하지 않아 동일 일의 전체 15m 봉(최대 95개)이 같은 일간 z 이벤트로 전부 진입 생성 → PREREG §5("이벤트 중복 제거 후 이벤트 수 n≥30")와 불일치 → **PREREG 규격 변경 없이 구현 정정(극단 에피소드당 1트레이드 상태 머신 — z 이탈 시 에피소드 종료) 후 재실행**(Task 26/27 측정 전 정정 선례 준용) — 1차 실행 결과는 폐기·원기록은 results/kp_kimchi_results.json 최종판으로 갱신
- [KP 측정 — 정정판 최종] kp_diag.py: **Holm 0/32 → PASS=False**. 이벤트 병합 후 표본 9~126/셀, 원시 p 최소 0.14 전원 무유의. 최고 관측 ETH s1 L90B80h48 n=126 정액 net16 +4.20bp(p 0.30 무유의). BTC s2 L288 n 9~20 저n. ETH s1 kline 결손 14,688봉(fail-closed 드랍)
- [KP 엔진 교차검증] kp_engine_crosscheck.py(CandleCostModel 재적용, kp_diag와 동일 로직 재현): **엔진 net>0 0/32(양 규모)** — 최고 ETH s1 L90B80h48 엔진 net −18.09bp(정액 +4.2bp가 엔진 비용 ~22bp에 완전 흡수). Holm(gross) 0통과
- [과학적 발견 ㉛~㉝] ①**일간 신호의 봉별 중복 취약점 발견**(방법론 교훈 ⑦): 봉별 반복 진입은 표본을 최대 ~100배 부풀림 — F2/X6(n=2,208/valid_n=552)·X8(n=6,385)·C9 등 과거 측정은 유효n 보정 병행·Holm 통과 전부 음수 방향으로 **방향 판정은 유효**하나 t-검정 자체의 체류 중복 취약점 존재 → 향후 측정·재검증(F2/X6-R·P3 포함)은 에피소드 병합 기준 통일 ②**KP FAIL — 10번째 독립 정보원(ρ −0.034 vs F&G) 독자 FAIL**: 정상화·추속 모두 무유의 — **국경 프리미엄 극단은 글로벌 선물에 방향 정보 제공하지 않음**(자본통제 하 프리미엄은 글로벌 동학과 분리) ③**P3 청산 역사 소스 4종 불가 확정·수집기 기동** — '대기'를 '행동'으로 전환, 2027-01-11경 P3·F2/X6-R 재개 트리거 2건이 시간으로 확정
- [장부 갱신] measurement_queue_status.md에 Task 29 갱신 섹션 추가(KP 행 신설·P3 행 갱신(수집기 기동)·F2/X6-R 행 갱신(조기 점검 완료) + 측정 22건 누적 갱신)
- [보고서 v21] v20 전문 무손실 유지(앵커 편집 — 보존 누락 0 검증, 80,446B→97,366B) + 헤더(신규 반영분·계보·갱신 이력 17사이클) + §0 요약(22건·발견 ㉛~㉝·최종 결론 갱신) + §4.3 KP 행(22건) + §4.4 발견 29~31(㉝개) + §5 큐 갱신(KP 완료·P3 수집기 기동·F2/X6-R 조기 점검) + §6 데이터 자산(KP 데이터·수집기 인프라) + §7 재완전화(5항목) + §8 액션 갱신(P3 수집기) + §9 산출물(KP 5행+P3 2행+v21 파일) + §10 사이클 17행 + 종료 기록 7차 추가

Stage Summary:
- KP FAIL 확정 — 측정 누적 **22건(방향 21+실행계층 1) 전부 FAIL/폐기**, 승격 후보 0건, '방향 원리 고갈' 재완전화(5차 스캔 발굴물 KP까지 처분)
- **P3의 유일 경로 '실시간 90일 수집'이 소스 4종 재실측으로 확정·수집기 기동 완료** — 2027-01-11경 P3 측정 + F2/X6-R 재검증의 재개 트리거 2건이 시간으로 확정됨(루프 종료 상태에서 인프라적 진전)
- **방법론 교훈 ⑦ 확정**: 일간 신호의 15m 봉별 반복 진입은 표본을 최대 ~100배 부풀림 — 과거 측정 방향 판정은 유효(유효n 보정+전부 음수 방향 통과)하나 향후 측정은 에피소드 병합 기준 통일(KP 정정판 패턴)
- 신규 데이터 자산: KP 합성 1,436일(Upbit 2,200일+Binance spot 2,020일+USDKRW 1,438일, 갭 0·NaN 0) + P3 청산 실시간 수집기(Binance WS+OKX 폴링→SQLite)
- 루프 상태: 종료 유지(최종). 재개 트리거 갱신: ①F2/X6-R 재검증(2027-01경 — ETH 신규 구간 극단 축적, v21 조기 점검 이벤트 0건으로 대기 유지 실측 강화) ②P3 청산 캐스케이드(2027-01-11경 — 수집기 90일 완료) ③사용자 신규 지시(포트폴리오 계층/실전 maker 인프라)
- 산출물: PREREG_kp_kimchi_2026-10-13.md, kp_download.py, kp_download_binance.py, kp_diag.py, kp_engine_crosscheck.py, p3_probe_liquidation.py, p3_probe2.py, results/kp_kimchi_results.{json,md}, results/kp_crosscheck_results.{json,md}, results/measurement_queue_status.md(Task 29), research/p3_liquidation_probe{,2}_log.json, research/kp_download{,_binance}_log.json, data/kp/(7 CSV), mini-services/liq-collector/(수집기), NEW2_전략연구_보고서_v21.md

---
Task ID: 30
Agent: Z.ai Code (main, 사용자 지시 "계속 진행 그리고 지금까지 루프 돌리면서 무엇을 알아냈고 무엇을 진행했는지 자세하게" 루프 재개 — 6차 원리 스캔 + X10 측정 + X11 데이터 실측)
Task: 종료 상태에서 루프 재개(재개 트리거 ② 사용자 지시) → P3 수집기 점검 → 6차 원리 스캔 → X10 캘린더 계절성 정식 측정(24셀) + X11 포지셔닝 데이터 실측 → FAIL 판정 + 보고서 v22 + 사용자 전체 성과 보고

Work Log:
- worklog·큐 장부 재독독: 측정 22건 전부 FAIL/폐기·Task 29(KP FAIL·P3 수집기 기동) 상태 확인. 사용자 지시로 재개 트리거 ② 충족 → Task 30 착수
- [P3 수집기 점검] mini-services/liq-collector /stats: 정상 가동(uptime 2,674초 시점·Binance WS open·OKX 폴링 45회·783건 수집·dedup 8,217건·에러 0) — 2027-01-11경 90일 완료 트리거 유효 유지
- [6차 원리 스캔 — 검색 인프라 이상] web-search 쿼리 3종(영문 2·한글 1)이 전부 무관 결과 반환(research/r6_scan_{seasonality,dow}.json 원기록 보존) — 신규 문헌 발굴 불가를 정직 기록하고, Task 29 5차 스캔에서 명시적 처분 없이 미결이던 '시간 계절성'을 비겹침 신규 패밀리 X10으로 확정(22건 기존 패밀리 중 결정론 캘린더 신호원 0건)
- [X11 부수 실측 — 데이터 불가] Binance 선물 포지셔닝 비율 3종(globalLongShortAccountRatio·takerlongshortRatio·topLongShortPositionRatio) 최신 200 OK이나 **최근 약 30일만 제공**(20일 전 200 OK·35일+ HTTP 400·2021년 400) — 4세그먼트·OOS 구조 불가 → L2 선례('데이터 가능 국면'과 '승격 가능' 구분)로 다운로드·측정 없이 처분(research/x11_positioning_probe_log.json)
- [X10 사전등록 고정] PREREG_x10_calendar_seasonality_2026-10-13.md 측정 전 작성: 결정론 세션 개시(ASIA 00:00/EU 07:00/US 13:30 UTC, DST 미보정 고지) 개시봉 시가 진입(캘린더는 사전에 알려진 정보 — 인과성 성립), **1차 방향=롱 사전 고정**(3세션 공통 — TradFi 동조 prior, L1 탐색 관측을 방향 고정에 미사용 명시), h{16,48}, 24셀 Holm, 셀 내 겹침 없음(24h 간격>12h 홀드 → 유효n=n), 반대(숏)+무작위 100시드 통제, 잠금 완전 제외, 신규 다운로드 0바이트, 검색 불가로 인한 문헌 한계 §0 정직 고지
- [X10 측정] x10_calendar_diag.py: 품질 완벽 — 4스팬 갭 0·연속성 True·세션 진입일 풀세트(472/346/472/496일)·드랍 0·결측진입 0. **Holm 통과 3셀 전부 음수 방향**(ASIA h16 원시 −14.9~−22.2bp, p_holm 0.0019~0.0063) — 1차 방향(롱)과 반대 → PASS=False, OOS 양수 0/4. h48은 BTC s2 3세션 원시 +13~16bp 일관 양수이나 p 0.68~0.99 무유의+4구성 비일관
- [X10 엔진 교차검증] x10_engine_crosscheck.py: **엔진 net>0 0/24(양 규모)** — 반대 방향(숏) 원시 +15~22bp도 taker 비용벽(31~41bp) 미달 → 방향 쇼핑 없이 관측으로만 기록(X3/C1 선례 준용)
- [과학적 발견 ㉞㉟] ①ASIA 개시(00:00 UTC — 펀딩 정산·C4 스파이크와 시간 축 일치) 직후 4시간 드리프트는 Holm 유의하게 음수 — 세션 효과의 방향은 '장 개시 상승'이 아니라 '아시아 개시 직후 하락', 반대도 비용 미달로 관측 기록 ②h48 세션 드리프트 구조 없음 + 결정론 캘린더 = 11번째 독립 정보원 독자 FAIL('독립성 ≠ 엣지' 재확인)
- [장부 갱신] measurement_queue_status.md에 Task 30 갱신 섹션 추가(X10 행·X11 데이터 불가 행 + 측정 23건 누적 갱신)
- [보고서 v22] v21 전문 무손실 유지(앵커 편집 21곳 검증 — 제목·v22 반영분·계보·갱신 이력 18사이클·§0(23건·발견 ㉞㉟)·§4.3 X10 행·§4.4 항목 32~33·§5 X10/X11 행·§6 X11 실측·§7 재완전화·§9 산출물 3행·§10 사이클 18행·종료 기록 8차 추가, 57,983→65,799 문자)
- [사용자 보고] 루프 전체 성과 상세 보고 작성(23건 측정·33개 과학적 발견·비용벽·11개 독립 정보원 전멸·데이터 자산·잔여 경로)

Stage Summary:
- X10 FAIL 확정 — 측정 누적 **23건(방향 22+실행계층 1) 전부 FAIL/폐기**, 승격 후보 0건, '방향 원리 고갈' 재완전화(6차 스캔 미결 항목 X10 소진)
- X11(선물 포지셔닝 비율)은 보존 기간 30일 실측으로 데이터 불가 확정 — 다운로드·측정 없이 처분(L2 선례 준용)
- 6차 스캔은 검색 인프라 이상(쿼리 3종 무관 결과)으로 문헌 발굴 불가 — 5차 미결 '시간 계절성'의 자체 소진으로 대체, 원기록 보존
- P3 수집기 정상 가동 재확인 — 재개 트리거 2건(F2/X6-R 2027-01·P3 2027-01-11)이 시간으로 유효 유지
- 루프 상태: 종료 유지(최종). 재개 트리거 불변: ①F2/X6-R(2027-01경) ②P3(2027-01-11경) ③사용자 신규 지시(포트폴리오 계층/실전 maker 인프라)
- 산출물: PREREG_x10_calendar_seasonality_2026-10-13.md, x10_calendar_diag.py, x10_engine_crosscheck.py, results/x10_calendar_results.{json,md}, results/x10_crosscheck_results.{json,md}, research/x11_positioning_probe_log.json, research/r6_scan_{seasonality,dow}.json, measurement_queue_status.md(Task 30), NEW2_전략연구_보고서_v22.md

---
Task ID: 31
Agent: Z.ai Code (main, cron 재발행(Job 453321, 10:07) 재개 — 7차 원리 스캔 Amihud 발굴 + A1 정식 측정 + 교차검증 t-표준 정정)
Task: 종료 상태에서 루프 재개(재개 트리거 ② 계열 — cron 재발행) → P3 수집기 점검 → F2/X6-R 조기 점검 → 7차 원리 스캔 → A1 Amihud 비유동성 정식 측정(96 cell-span) → FAIL 판정 + 보고서 v23

Work Log:
- worklog·큐 장부 재독독: 측정 23건 전부 FAIL/폐기·Task 30(X10 FAIL·v22) 상태 확인. cron 재발행으로 재개 → Task 31 착수
- [P3 수집기 점검] /stats(포트 3020): 정상 가동(uptime 4,981초·OKX 폴링 84회·inserted 899건·dedup 15,901건 차단·에러 0, Binance WS open — forceOrder 메시지 0건은 스트림 희소성으로 정상 범주) — 2027-01-11 90일 완료 트리거 유효 유지
- [F2/X6-R 조기 점검] F&G 재다운로드(3,173일 — 2026-10-13분 신규): 신규 관측 구간(2026-09-01~) 극단공포(B80≤20) 0건(최근 5일 57/69/61/64/59 전원 중립, research/fng_refresh_task31.json) → 재검증 이벤트 추가 0, 2027-01 대기 유지 실측 강화
- [7차 원리 스캔] web-search 4쿼리: Q1 왜도/MAX·Q3 ETF 플로우·Q4 오버나이트는 무관 결과(6차 검색 인프라 이상 재현 — research/r7_scan_{skewness,etf,overnight}.json 원기록 보존), **Q2 Amihud만 유효 문헌 발굴**(Amihud 2002 ILLIQ=|r|/달러거래량·Lou-Xiao 2016 거래량 성분 예측력) — 미측정 원리로 확정
- [A1 패밀리 사전검증] a1_family_precheck.py: ILLIQ z(L288) vs RV z ρ −0.02~+0.08 / vs 테이커 플로우 z ρ −0.08~+0.02 / vs 펀딩 z ρ −0.04~−0.01 — **전 패밀리 |ρ|<0.1, 12번째 독립 정보원**(폐기 규칙 |ρ|≥0.8 미트리거, research/a1_family_precheck.json)
- [사전등록 고정] PREREG_a1_illiq_2026-10-13.md 측정 전 작성: ILLIQ=|r|/qv(qv≤0 NaN fail-closed), 인과 z(직전 960 관측 shift1), B80/B90, **1차 방향=고비유동→롱(비유동성 프리미엄) 사전 고정**(저비유동→숏은 거울 대칭, 반대 방향은 통제로만), L{96,288,960,2880}×h{4,16,48}×4스팬=96 cell-span Holm, valid_n=n/h≥30, OOS 후반 40%, 무작위 100시드, 잠금 완전 제외, 신규 다운로드 0바이트, 저파워 사전 고지
- [A1 측정] a1_illiq_diag.py: 품질 완벽(dup 0·4스팬 결측 0·z 유효 149k~182k행). **Holm 통과 47/96 — 전부 음수 방향**(net16 −15.3~−24.2bp, ETH s2 집중, p_holm 최소 <0.0001) → 1차 방향 FAIL, 4스팬 일관성 0~1/4, OOS 전원 음수 → PASS=False. BTC s1 B90 L96h48 +52.5bp(정액 net16 +36.5bp)는 p=0.12 무유의. 반대(숏) 통제 최고 +10.66bp — taker 비용벽 미달
- [A1 엔진 교차검증 — t-표준 불일치 발견·정정] a1_engine_crosscheck.py 초기 실행에서 raw-n t로 유일 양수 셀(BTC s1 B90 L96h48 net +19.74bp·p_holm<0.05) 관측 → **diag(valid_n=n/h)와의 t-검정 표준 불일치 발견 → valid_n 보정으로 정정 후 재실행(KP 구현 정정 선례 준용, 규격 변경 없음)** → **엔진 순익 양수 0/96(양 규모)** — 유일 양수 셀 t=0.841·p=0.40 무유의. 엔진 비용 $10k 32.4~42.2bp(중앙 37.6), fail-closed 비중 평균 0.0%(소액 주문은 비유동 바에도 체결 가능)
- [과학적 발견 ㊱~㊳] ①**비유동성 극단 후 되돌림 아닌 추종 하락 확정**(12번째 독립 정보원 독자 FAIL): 고ILLIQ 후 비유동성 프리미엄 실현(롱)이 아닌 하락 지속(청산 나선형 추종) — 반대(숏)도 원시 최대 +26.7bp로 비용 미달, 양방향 폐기. Amihud 횡단면 프리미엄의 시계열 확장 부재 재확인 ②**교차검증 t-검정 표준 불일치 발견(방법론 교훈 ⑧)**: 엔진 교차검증 템플릿 t가 raw n(겹침 포함)을 사용 — h>1 겹침 신호에서 유의성 최대 ~√h 과대. 과거 전 교차검증은 엔진 양수 0셀로 무표면 → A1에서 최초 표면화 → valid_n 표준 통일·정정 완료 ③**소액 주문 fail-closed 0.0%**: 비유동 바 진입의 제약은 체결 불가가 아니라 비용(중앙 37.6bp) — N2 발견 ㉙과 정합
- [장부 갱신] measurement_queue_status.md에 Task 31 갱신 섹션 추가(A1 행 신설 + 측정 24건 누적 갱신 + Task 31 종결)
- [보고서 v23] v22 전문 무손실 유지(복사 후 앵커 편집 — 무결성 검증 21/21 통과, v22 반영분 전문 보존 확인, 65,799→73,117 문자) + 제목·v23 신규 반영분·계보(v22→v23)·갱신 이력 19사이클 + §0(24건·최종결론 v23·신발견 ㊱~㊳ 행) + §4 헤더(24건)+A1 행+발견 항목 34~36 + §5 A1 완료 행 + §7(v23 기준·교훈 ⑧ 4b 항목) + §9 A1 산출물 2행 + §10 사이클 19행 + 종료 기록 9차 추가

Stage Summary:
- A1 FAIL 확정 — 측정 누적 **24건(방향 23+실행계층 1) 전부 FAIL/폐기**, 승격 후보 0건, '방향 원리 고갈' 재완전화(7차 스캔 발굴물 A1 소진)
- 7차 스캔은 4쿼리 중 Amihud 1개만 유효 문헌(나머지 3개는 6차 검색 이상 재현 — 원기록 보존). 발굴물마저 패밀리 사전검증(|ρ|<0.1·12번째 독립 정보원)을 통과하고도 독자 FAIL — '독립성 ≠ 엣지' 12번째 재확인
- **방법론 교훈 ⑧ 확정**: 엔진 교차검증 t-검정은 반드시 diag와 동일 valid_n=n/h 표준 — raw n은 h>1 겹침에서 유의성 ~√h 과대. 전 교차검증 템플릿 소급 정정 완료(향후 측정 전체에 적용)
- P3 수집기 정상 가동·F&G 신규 극단 0건 재확인 — 재개 트리거 2건(F2/X6-R 2027-01·P3 2027-01-11)이 시간으로 유효 유지
- 루프 상태: 종료 유지(최종). 재개 트리거 불변: ①F2/X6-R 재검증(2027-01경) ②P3 청산 캐스케이드(2027-01-11경) ③사용자 신규 지시(포트폴리오 계층/실전 maker 인프라)
- 산출물: PREREG_a1_illiq_2026-10-13.md, a1_family_precheck.py, a1_illiq_diag.py, a1_engine_crosscheck.py, fng_refresh_check.py, results/a1_illiq_results.{json,md}, results/a1_crosscheck_results.{json,md}, results/measurement_queue_status.md(Task 31), research/a1_family_precheck.json, research/r7_scan_{skewness,amihud,etf,overnight}.json, research/fng_refresh_task31.json, NEW2_전략연구_보고서_v23.md
---
Task ID: 32
Agent: Z.ai Code (main, cron 재발행(Job 453321, 16:07) 재개 + 사용자 지시(종합정리 무손실 확인·전략 전수 보고·활문서 지속 갱신·다음 진행) — 종합정리 활문서 신설 + 8차 스캔 X12 측정)
Task: 종료 상태에서 루프 재개 → P3 수집기 VM 재시작 생존 확인 → 종합정리 활문서(NEW2_전략탐색_종합정리.md) 신설 → F&G 조기 점검 → 8차 원리 스캔(7차 미결 왜도/MAX 자체 소진) → X12 정식 측정(96 cell-span) → FAIL 판정 + 보고서 v24

Work Log:
- worklog·큐 장부 재독독: 측정 24건 전부 FAIL/폐기·Task 31(A1 FAIL·v23) 상태 확인. cron 재발행+사용자 지시로 재개 트리거 ② 충족 → Task 32 착수
- [P3 수집기 생존 확인] 세션/VM 재시작(08:07 UTC) 후 수집기 자동 재기동 확인(기동 08:08:06Z·uptime 78초 시점). SQLite DB 직접 쿼리 검증 — liquidations.db **1,291건**(OKX 2026-10-13 00:07:29→08:10:25 UTC 연속 보존, by_exchange okx 단일, Binance WS open) — **VM 재시작에도 90일 누적 무손상 → 2027-01-11 트리거 생존 확정**
- [종합정리 활문서 신설] 사용자 요구("지금까지 조사한 전략 한 md에 한 글자도 빼지 않고"+"진행한 결과 전부 계속 업데이트")에 따라 **NEW2_전략탐색_종합정리.md** 생성(51KB): §0 한눈 요약(측정 24건·승격 0건·비용벽·파이프라인) / §1 Task 1~31 진행 타임라인(진행 방식 요약 포함) / §2 저장소 기존 전략(57 ID=14 클래스·ADR-0070~0089 방향 16계열·유명 20종 배제 근거) / §3 리서치 후보 17건(라운드 1~3+큐 감사 5건) / §4 측정 총람(방향 23+실행계층 1 전부 FAIL — 규격·핵심 수치 전부 포함) / §5 데이터 제약·구조 처분(L2·X11·S1-R4·F4·25δ·C2 잔여·온체인·X1-R 폐기·F2/X6-R 대기) / §6 과학적 발견 36건 전문 / §7 방법론 교훈 8건 / §8 비용 벽 상세 / §9 데이터 자산 전수 / §10 잔여 경로·재개 트리거 / §11 **활문서 갱신 정책(append 전용·무손실·정직성 규칙)** / §12 갱신 로그 — 이후 모든 루프 결과를 §12에 지속 append(사용자 지시 이행)
- [F&G 조기 점검] fng_refresh_check.py 재실행: 신규 관측 구간(2026-09-01~) 극단공포(B80≤20) **0건**(최근 5일 전원 중립) → F2/X6-R 2027-01 대기 유지 재확정
- [8차 원리 스캔] 검색 인프라 이상 지속(6~7차 재현 — 쿼리 재시도 불가, 정직 기록) → 7차 스캔에서 검색 이상으로 미처리였던 '왜도/MAX'를 비겹침 신규 패밀리 **X12**로 확정(24건 패밀리 중 분포 형태(3차 모멘트) 신호원 0건). 문헌 prior: Bali-Cakici-Whitelaw 2011 MAX/로터리 선호 — 검색 원문 미확보를 §0 정직 고지(X10 선례)
- [X12 패밀리 사전검증] x12_family_precheck.py: skew z(L288) vs RV z ρ −0.08~+0.05 / vs ILLIQ z −0.04~−0.02 / vs 펀딩 z −0.02~+0.10 전원 독립, **vs 테이커 플로우 z ρ +0.47~+0.55(부분 인접 고지 — 폐기 기준 |ρ|≥0.8 미트리거)** (research/x12_family_precheck.json)
- [사전등록 고정] PREREG_x12_skewness_2026-10-13.md 측정 전 작성: skew=rolling(L).skew()(unbiased) 인과 z(직전 960 shift1), B80(|z|≥1.2816)/B90(1.6449), **1차 방향=고왜도→숏/저왜도→롱 사전 고정**(MAX/로터리 prior — 방향 쇼핑 차단, 추종은 반대 통제만), L{96,288,960,2880}×h{4,16,48}×4스팬(A1 동일 스팬)=**96 cell-span Holm**, valid_n=n/h≥30, OOS 후반 40%, 무작위 100시드, 잠금 완전 제외, 신규 다운로드 0바이트
- [X12 측정] x12_skew_diag.py: 품질 완벽(4스팬 결측 0·dup 0·z 유효 155k~186k행). 스크립트 MD 출력부의 셀 태그 참조 오류(KeyError) 1건을 실행 후 발견·수정 후 재실행 — 측정값 불변(결정론), 원기록은 최종판. **Holm 통과 44/96 — 전부 음수 방향**(net16 −13.4~−23.4bp, p_holm 최소 2.5e-99) → 1차 방향(MAX prior) FAIL, 승격 후보 조합 0(net16>0 & valid_n≥30 셀 1/96 — BTC s1 B80 L288h48 +6.34bp p=0.688 무유의), OOS 미평가(대상 부재) → PASS=False
- [X12 엔진 교차검증] x12_engine_crosscheck.py(valid_n 표준 — 교훈 ⑧ 준용, t-표준 불일치 없음): **엔진 순익 양수 0/96(양 규모)**, 엔진 FC 평균 0.0%(최대 0.1%) → FAIL 재확증
- [과학적 발견 ㊴] **왜도 극단은 MAX 되돌림이 아니라 추종 지속(왜도 모멘텀)**: 고왜도 후 상승 지속·저왜도 후 하락 지속 — Bali prior 부정. 반대(추종) 통제 최고 +10.80bp(ETH s1 B90 L96h48)는 정액 16bp·엔진 31~41bp 미달 → **양방향 폐기**. 왜도-플로우 부분 결합(ρ 0.47~0.55)은 데이터 참조값 — 13번째 정보원(부분 인접 고지 포함) 독자 FAIL('독립성 ≠ 엣지' 재확인)
- [장부 갱신] measurement_queue_status.md에 Task 32 갱신 섹션 추가(X12 행·P3 생존 확인·종합정리 신설 + 측정 25건 누적 갱신)
- [보고서 v24] v23 전문 무손실 유지(scripts/make_v24.py 앵커 편집 15곳·무결성 PASS, 73,117→80,214 문자) + 제목·v24 반영분·계보(v23→v24)·갱신 이력 20사이클 + §0(25건·최종결론 v24·신발견 ㊴ 행) + §4 헤더(25건)+X12 행+발견 37 + §5 X12 행 + §7 갱신 + §9 산출물 3행 + §10 사이클 20행 + 종료 기록 10차 추가

Stage Summary:
- X12 FAIL 확정 — 측정 누적 **25건(방향 24+실행계층 1) 전부 FAIL/폐기**, 승격 후보 0건, '방향 원리 고갈' 재완전화(8차 스캔 발굴물 X12 소진)
- **종합정리 활문서(NEW2_전략탐색_종합정리.md) 신설로 사용자 요구 이행** — 전략 전수 기록(측정 총람·저장소 기존 전략·리서치 후보·발견·교훈·데이터 자산) + append 전용 갱신 정책. 이후 모든 루프 결과가 §12에 지속 추가됨
- P3 수집기의 VM 재시작 생존을 DB 직접 검증으로 확정 — 2027-01-11 트리거가 인프라적으로 안정. F2/X6-R 극단 0건 재확인 — 재개 트리거 2건이 시간으로 유효 유지
- 루프 상태: 종료 유지(최종). 재개 트리거 불변: ①F2/X6-R 재검증(2027-01경) ②P3 청산 캐스케이드(2027-01-11경) ③사용자 신규 지시(포트폴리오 계층/실전 maker 인프라)
- 산출물: NEW2_전략탐색_종합정리.md, PREREG_x12_skewness_2026-10-13.md, x12_family_precheck.py, x12_skew_diag.py, x12_engine_crosscheck.py, results/x12_skew_results.{json,md}, results/x12_crosscheck_results.{json,md}, research/x12_family_precheck.json, measurement_queue_status.md(Task 32), scripts/make_v24.py, NEW2_전략연구_보고서_v24.md

---
Task ID: 33
Agent: Z.ai Code (main, cron 재발행(Job 453321, 00:07)+사용자 지시(코인 제한 완전 해제·승격 가능성 최고 방향 진행) 재개 — 유니버스 확장 + ALT-P1 측정)
Task: 종료 상태에서 루프 재개 → 25건 전원 BTC/ETH임을 전수 대조로 확인 → 사용자 지시 ② 이행으로 대형 알트코인 유니버스 확장(BNB/XRP/SOL/TRX) → ALT-P1 펀딩 극단 알트 측정(펀딩 캐리 회계 신설) → FAIL 판정 + 보고서 v25

Work Log:
- worklog·큐 장부 재독독: 측정 25건 전부 FAIL/폐기·Task 32(종합정리 신설·X12 FAIL·v24) 상태 확인. cron 재발행+사용자 지시(코인 제한 완전 해제·승격 가능성 최고 방향)로 재개 트리거 ② 충족 → Task 33 착수
- [경로 확정] 25건 측정 원기록 전수 대조 — 전원이 BTC/ETH 4스팬. 사용자 지시 ②(코인 제한 완전 해제·소형 시총만 제외)가 미실행 상태임을 확인 → 알트 유니버스 확장이 유일한 미실행 구조적 경로로 확정. 기기 확장 대상 계열 선정: P1(펀딩 극단) — 비용벽 극복에 필요한 원시 엣지 크기가 유일하게 구조적으로 가능한 계열(알트 펀딩 극단 가설)
- [유니버스 규칙 사전 고정] research/alt_universe_ranking.json: 바이낸스 UM 무기한 2021-08-01 이전 상장 + 2026-10-14 시총 상위 + BTC/ETH/스테이블/래핑·스테이킹/상장폐지(XMR) 제외 후 상위 4종 → **BNB($99.2B)·XRP($86.7B)·SOL($63.4B)·TRX($31.1B)** — CoinGecko top20 원기록 보존. ZEC/DOGE/LINK는 규칙상 5~6위(확장 후보 기록만). '시총 작아 돈 넣으면 오르는 종목' 제외 요구 충족(전원 $30B+)
- [데이터 신규 확보] alt_download.py: data.binance.vision 월별 zip(KP 선례 우회 경로) — 15m kline+funding **328파일**(2021-04~2022-09·2024-11~2026-09), 실패 0. 품질: dup 0·BNB 갭 0·XRP/SOL/TRX span1 갭 2곳(fail-closed 대상)·span2 갭 0·타임스탬프 13자리 ms(µs 보정 불필요). 스팬: s1 2021-05-02~2022-08-17(BTC/ETH s1 동일 — 레짐 정렬), s2 2024-12-10~2026-10-01(ETH s2 시작 동일·종료 연장 — 알트 잠금 구간 부재를 PREREG에 명시)
- [PREREG 사전 고정] PREREG_alt_funding_extreme_2026-10-14.md (다운로드 전 작성): P1 기기 확장 정직 고지(독립 정보원 주장 없음)·P1 z(정산 90회 인과)·B80/B90·**1차 방향=역발상 사전 고정**·h{16,48,96}(정산 0/1/3회 통과)·**펀딩 캐리 수익 회계 신설**(P1은 미포함 — 진단·엔진 동일 적용으로 비대칭 차단)·48 cell-span Holm·net12/16/20/30(승격 기준 net16)·valid_n=n/h≥30·OOS 후반 40%·무작위 100시드·추종 통제. §8 addendum: h96 정산 통과 2회→3회 예시 정정(회계 공식 무변경 — 측정 전 정정 선례)
- [§5 사전검증(검정력 확인 — 방향 정보 아님)] 알트 펀딩 분포: BNB 중앙값 0.0bp(p01 −8/p99 +8)·XRP 0.7bp(−3/+14)·SOL 0.6bp(−4/+8)·TRX 0.5bp(−9/+12), |r|≥10bp 0.9~2.1%·≥30bp 0.0~0.3% — **'알트 펀딩 극단 10~100배' 사전 가설이 대형 알트에서 기각**(차익거래·클램프로 기저 고정 — 극단 펀딩은 소형·매니아 종목의 현상)
- [측정] alt_funding_diag.py: 품질(스팬 결측 0~480봉·dup 0·z 유효). 잔재 라인(placeholder·미사 변수) 2건을 측정 전 발견·제거(품질 게이트 준용). **Holm 통과 0/48** — 원시 p 최소 0.326 전원 무유의. **역발상 가격 성분 음수**(최악 SOL s1 B90 h16 −82.6bp) — 펀딩 극단 후 되돌림 아닌 **추종 지속**(X12 왜도 모멘텀 ㊴와 정합). 캐리 성분 h16 0.0bp·h48/96 미미 — 회계 포함에도 구조 부족. net16>0 & valid_n≥30 셀 **0/48**, OOS 미평가(대상 부재) → PASS=False
- [엔진 교차검증] alt_funding_engine_crosscheck.py(CandleCostModel 기본 파라미터 fee 5bp·half_spread 2bp·impact 0.1·max_part 5% + 캐리 동일 포함, valid_n 표준 — 교훈 ⑧ 준용): **엔진 net>0 0/48(양 규모)** — $10k 최고 BNB s1 B90 h96 −0.2bp(비용 92.3bp)·$100k FC 다수·half_spread ×2 감도($10k) 0/48 → FAIL 재확증
- [과학적 발견 ㊵] **대형 알트 선물의 펀딩은 극단이 존재하지 않는다**: 상위 시총 4종 펀딩 p99 +8~14bp·중앙값 0.0~0.7bp·|r|≥30bp 0.3% 미만 — '알트 펀딩 10~100배' prior는 소형·매니아 종목에만 유효. 펀딩 캐리 전략의 잠재 처는 규칙 미포함 ZEC/DOGE/LINK(시총 5~6위) 또는 사용자 제외 대상(소형)뿐 — **구조적 폐기**. 14번째 정보원(기기 확장 — 독립 주장 없음) 독자 FAIL('독립성 ≠ 엣지' 및 '확장 ≠ 엣지' 재확인)
- [장부 갱신] measurement_queue_status.md Task 33 섹션 추가(ALT-P1 행·유니버스 규칙·사전검증 + 측정 26건 누적 갱신)
- [종합정리 §12 append] NEW2_전략탐색_종합정리.md에 Task 33 섹션 append(§11 append 전용 정책 준용 — 10항목)
- [보고서 v25] scripts/make_v25.py 앵커 편집 19곳·무결성 PASS(제목·v25 반영분 신설·v24 반영분 기록 보존 라인 분리·갱신 이력 21사이클·§0(26건·발견 ㊵ 행)·§4 헤더+ALT-P1 행+발견 38·§5 ALT-P1 완료 행·§9 산출물 4행(v23/v24/v25 파일 행 보완)·§10 사이클 21행·종료 기록 11차, 135,814→148,000 바이트)

Stage Summary:
- ALT-P1 FAIL 확정 — 측정 누적 **26건(방향 25+실행계층 1) 전부 FAIL/폐기**, 승격 후보 0건
- **사용자 지시 ②(코인 제한 해제) 이행 완료** — 유니버스 확장 차원까지 소진으로 확정: 대형 알트(BNB/XRP/SOL/TRX)는 ①펀딩 극단 자체가 부재(차익거래·클램프) ②역발상 가격 성분도 음수(추종 지속) — 캐리 회계로 측정해도 구조적 부족. '확장 ≠ 엣지' 실증
- 펀딩 캐리 전략의 잔여 이론적 처는 ZEC/DOGE/LINK(시총 5~6위 — 규칙 개정 시) 또는 소형종목(사용자 제외 규칙 해당) — 현 규칙 하에서 구조적 폐기
- 루프 상태: 종료 유지(최종). 재개 트리거 불변: ①F2/X6-R 재검증(2027-01경) ②P3 청산 캐스케이드(2027-01-11경 — 수집기 가동 중) ③사용자 신규 지시(포트폴리오 계층/실전 maker 인프라/ZEC·DOGE 규칙 개정)
- 산출물: PREREG_alt_funding_extreme_2026-10-14.md, research/alt_universe_ranking.json, alt_download.py(+research/alt_download_log.json), alt_funding_diag.py, alt_funding_engine_crosscheck.py, results/alt_funding_results.{json,md}, results/alt_funding_crosscheck_results.{json,md}, NEW2_전략탐색_종합정리.md(§12 append), measurement_queue_status.md(Task 33), scripts/make_v25.py, NEW2_전략연구_보고서_v25.md

---
Task ID: 34
Agent: Z.ai Code (main, cron 재발행(Job 453321, 04:07) 재개 — Task 33 '잔여 이론적 처 ZEC/DOGE' 측정 종결 = 사용자 지시 ② 이행의 미완료분)
Task: 종료 상태에서 루프 재개 → P3 수집기 생존 확인 → F&G 조기 점검 → ALT-P1-R2(룰 5~6위 ZEC·DOGE 펀딩 극단) PREREG 사전 고정 → 데이터 다운로드 → 측정 + 엔진 교차검증 → FAIL 판정 + 보고서 v26

Work Log:
- worklog·큐 장부 재독독: 측정 26건 전부 FAIL/폐기·Task 33 상태 확인. Task 33이 기록한 '잔여 이론적 처 ZEC/DOGE(룰 5~6위 — 규칙 개정 시)'가 미측정으로 남아 있음을 확인 — 사용자 지시 ②(시총 소형만 제외)상 ZEC($20.3B)·DOGE($13.2B)는 소형 해당 없음 → ALT-P1-R2로 측정 종결 결정(재개 트리거 ③ 사용자 지시 계열 충족)
- [P3 수집기 생존 확인] /stats(3020): VM 재시작(20:08Z) 직후 자동 재기동·Binance WS open·OKX 폴링 정상·**1,744건 연속 누적(2026-10-13T00:07→20:01 UTC 무손상)** — 2027-01-11 90일 완료 트리거 유효 유지
- [F&G 조기 점검] fng_refresh_check.py 재실행: 신규 구간(2026-09-01~) 극단공포(B80≤20) **0건**(최근 59~64 중립) — F2/X6-R 2027-01 대기 유지 재확정
- [데이터 프로브(상태 코드만 — 신호·수익 열람 없음)] ZECUSDT·DOGEUSDT kline(2021-04/05·2022-08·2024-11·2026-08/09)·funding(2021-05) 전원 HTTP 200 — 스팬 경계 결측 없음
- [PREREG 사전 고정] PREREG_alt_funding_extreme_r2_2026-10-14.md (다운로드 전 작성): ALT-P1 §1 규칙의 5~6위 확장(research/alt_universe_ranking.json not_selected_rule_5_6 원기록 — ZEC #10 $20.3B·DOGE #12 $13.2B)·동일 스팬(s1 2021-05-02~2022-08-17, s2 2024-12-10~2026-10-01)·동일 신호·회계(P1 z 90회 인과·B80/B90·**역발상 사전 고정**·h{16,48,96}·캐리 포함)·**24 cell-span Holm**·승격 일관성 4스팬 중 ≥3(75% 비례 유지 — 완화 아님)·OOS ≥3/4·valid_n≥30·무작위 100시드·추종 통제·**ZEC 매니아 경계 사례 정직 고지**(2025 시총 10배 상승 사실과 규칙 문언(시총 소형 여부)의 구분 — §1)
- [데이터 다운로드] alt_download_r2.py: 15m kline+funding **164파일**(ZEC·DOGE 각 82 — 41개월×2종), 실패 0. 품질: ts 13자리 ms 확인(µs 보정 불필요)
- [§5 사전검증(전략 수익 열람 전, alt_funding_r2_precheck.py)] ZEC 펀딩 중앙값 1.0bp·p01 −15/p99 +10bp·**|r|≥30bp 0.45%**(상위 4종 0.0~0.3% 대비 최고 — 매니아 예외 영역 부분 실증), DOGE 중앙값 0.8bp·|r|≥30bp 0.05%(상위 4종과 동일). 트리거 B80 167~344·B90 145~225/스팬 — **h16에서도 valid_n≈9~21<30 → 승격 구조적 어려움 사전 확인**(검정력 고지)
- [측정] alt_funding_r2_diag.py: 품질 완벽(ZEC·DOGE 결측 봉 0·dup 0·정산 1,416/1,980 스팬 정확). **Holm 통과 0/24** — 원시 p 최소 0.585 전원 무유의. **DOGE 역발상 가격 성분 음수**(−1.1~−24.4bp — 펀딩 극단 후 추종 지속, ㊵·㊴ 재확증). **ZEC s2 역발상 가격 성분 양수**(h48 +137.5bp·h96 +267.6bp — 상위 4종과 **반대 부호**의 되돌림 실존)+캐리 +5.8~18.1bp — 그러나 **valid_n 2~6**(단일 매니아 국면 군집)으로 valid_n≥30 셀 **0/24**, OOS 미평가(대상 부재) → PASS=False
- [엔진 교차검증] alt_funding_r2_engine_crosscheck.py(CandleCostModel 기본 파라미터 + 캐리 동일 포함, valid_n 표준 — 교훈 ⑧ 준용): **엔진 net>0 4/24($10k)·3/24($100k)** — 전원 ZEC s2 h48/96(최고 B90 h96 +309.6bp/비용 104.4bp·p 0.817·FC 95), half_spread ×2 감도($10k) 4/24 — **통계적 유의성 없는 국면 군집 평균값 → FAIL 재확증**
- [과학적 발견 ㊶] **매니아 종목(ZEC)에서는 펀딩 극단 후 역발상 되돌림이 실존할 수 있다(상위 4종·DOGE의 추종 지속과 반대 부호) — 그러나 이벤트가 단일 국면에 군집해 유효표본(vn 2~6)으로 통계 검정 불가**. 극단 캐리+되돌림의 결합은 국면 의존적이며 사후 국면 식별은 생존 편향 — Phase A 규격 내 취급 불가. 15번째 정보원(기기 확장 2차 — 독립 주장 없음) 독자 FAIL. 잔여 이론적 처 측정 종결 — **펀딩 캐리 계열(룰 1~6위 전 종목) 완전 폐기**
- [장부 갱신] measurement_queue_status.md Task 34 섹션 추가(ALT-P1-R2 행·룰 5~6위 확장·사전검증 + 측정 27건 누적 갱신)
- [종합정리 §12 append] NEW2_전략탐색_종합정리.md에 Task 34 섹션 append(§11 append 전용 정책 준용 — 10항목)
- [보고서 v26] scripts/make_v26.py 앵커 편집 19곳·무결성 PASS(제목 v26 전환+v25 부제 이전 부제 체인 접기·작성 라인 분리(v25 반영분 기록 보존)·갱신 이력 22사이클·§0(27건·발견 ㊶ 행)·§4 헤더+ALT-P1-R2 행+발견 39·§5 ALT-P1-R2 완료 행·§9 산출물 3행(v25 이전판 전환·v26 신설·R2 산출물 행)·§10 사이클 22행·종료 기록 12차, 148,000→160,130 바이트)

Stage Summary:
- ALT-P1-R2 FAIL 확정 — 측정 누적 **27건(방향 26+실행계층 1) 전부 FAIL/폐기**, 승격 후보 0건
- **사용자 지시 ②(코인 제한 해제) 완전 이행 종결** — 룰 1~6위 전 종목(BNB/XRP/SOL/TRX/ZEC/DOGE) 펀딩 캐리 측정 완료: 상위 4종+DOGE는 극단 부재·추종 지속, ZEC(매니아 종목)는 되돌림 실존하나 단일 국면 군집(vn 2~6)으로 통계 검정 불가 — 펀딩 캐리 계열 구조적 폐기 확정
- 루프 상태: 종료 유지(최종). 재개 트리거 불변: ①F2/X6-R 재검증(2027-01경) ②P3 청산 캐스케이드(2027-01-11경 — 수집기 가동 중) ③사용자 신규 지시(포트폴리오 계층/실전 maker 인프라/LINK·ADA 7~8위 확장)
- 산출물: PREREG_alt_funding_extreme_r2_2026-10-14.md, alt_download_r2.py(+research/alt_download_r2_log.json), alt_funding_r2_precheck.py(+research/alt_funding_r2_precheck.json), alt_funding_r2_diag.py, alt_funding_r2_engine_crosscheck.py, results/alt_funding_r2_results.{json,md}, results/alt_funding_r2_crosscheck_results.{json,md}, NEW2_전략탐색_종합정리.md(§12 append), measurement_queue_status.md(Task 34), scripts/make_v26.py, NEW2_전략연구_보고서_v26.md

---
Task ID: 35
Agent: Z.ai Code (main, cron 재발행(Job 453321, 12:07) 재개 + 사용자 지시('조합 검증 더 해봐 승격 가능성 높은걸로') 이행 — C-COMBO 통합 풀 × 실행계층 측정)
Task: 종료 상태에서 루프 재개 → P3 수집기/F&G 점검 → 사용자 지시 '조합 검증'의 미실행 경로 확정(엔진 양수 탐색 관측 2건의 통합 측정) → C-COMBO PREREG 사전 고정(§8 부칙 정정 포함) → 측정 + 엔진 교차검증 → FAIL 판정 + 보고서 v27

Work Log:
- worklog·큐 장부 재독독: 측정 27건 전부 FAIL/폐기·Task 34(ALT-P1-R2 FAIL·v26) 상태 확인. cron 재발행+사용자 지시(조합 검증)로 재개 트리거 ② 충족 → Task 35 착수
- [P3 수집기 점검] /stats(3020): 정상 가동(uptime 2,822초·OKX 폴링 48회·inserted 307건·dedup 9,293 차단·에러 0, Binance WS open) — **2,051건 연속 누적(2026-10-13T00:07→2026-10-14T03:37 UTC 무손상)** — 2027-01-11 90일 트리거 유효 유지
- [F&G 조기 점검] fng_refresh_check.py 재실행: 신규 관측 구간(2026-09-01~) 극단공포(B80≤20) 0건(최근 57~69 중립) → F2/X6-R 2027-01 대기 유지 재확정
- [조합 경로 확정] 27건 원기록 재독 — 엔진 비용 차감 후 양수인 유일 2건의 탐색 관측(X1-R 엔진 +21.7bp·F2/X6-R 엔진 +36.19bp)의 통합 측정이 사용자 지시('조합 검증 더 해봐')의 미실행 경로로 확정(worklog Task 18에 '통합 재검증' 문서화 선례). 검정력 사전 산정: 발표 통계로 Stouffer 기대 Z≈0.84~0.95 vs Holm 필요 ~1.96 — 통과 확률 낮음을 사전 고지하는 N2 선례의 정공법 채택(1차 가치=승격 거리 실측 확정·ρ̂ 실측·maker 전이 실측)
- [PREREG 사전 고정] PREREG_combo_stress_bounce_2026-10-14.md (측정 전): C-A=X1-R Config R 통합 풀(ES/NQ×L{24,120} 4구성, (ei,sgn) union dedupe — 고정 순서[ES_L24→ES_L120→NQ_L24→NQ_L120]) + C-B=F2/X6-R 셀(thr25/75 h288) · arm{taker 순16, maker δ0/K16 promotion-basis(N2 규격)} · 결합=**Stouffer 상관 조정**(Z_pool=(Z_A+Z_B)/√(2+2ρ̂), ρ̂ 일별 매칭 실측·<30일 시 0.5 보수 고정·하한 0 절단) · 2셀 단일 패밀리 Holm · 게이트 G-C1(유의성+경제성)/G-C2(엔진)/G-C3(60/40)/G-C4(신규 구간 방향성) 사전 고정 · 무작위 100시드(20261014) · 신규 다운로드 0바이트 · Phase A 진단 등급(잠금 구간 미사용)
- [§8 부칙 정정 — 측정 전·결과 열람 전] 첫 실행 재생산 게이트가 C-B 정의 오기 발견(롱측만 n=150 vs 원 셀 177): 원기록 셀(f2x6_fng_results.md ETH s2 thr25/75 h288)은 **양측 셀**(v≤25 롱+v≥75 숏 — f2x6_fng_diag.py 셀 순회 구조 원기록 확인) → PREREG §8 부칙으로 정정 후 재실행(ALT-P1 §8 선례). worklog Task 18의 '극단공포(≤25)→3일 롱' 묘사가 롱측만 묘사였음을 정직 기록. 재생산 목표값(n=177·74.78bp)은 원기록 사전 고정값 그대로 — 결과 유도적 조정 아님
- [재생산 완벽] C-A 4구성 원시 총bp **정확 일치**(54.26/47.85/36.13/34.76 — 편차 0.0)·union n=2469 / C-B n=177·총bp 74.78 정확 일치 / 엔진 교차검증에서 C-B taker 엔진 **+36.19bp 원기록 정확 재생산**(독립 코드 경로) — 2성분 5발표값 전부 재현
- [C-COMBO 측정 FAIL — 28번째] taker arm: C-A 순16 +16.48bp(t=0.383)·C-B 순16 +58.78bp(t=0.756)·ρ̂=0.238(실측 100일)→ Z_pool=0.724·p_holm=0.4707 / maker arm: C-A promo +12.09bp(체결률 0.995·2,456/2,469)·C-B promo +53.29bp(체결률 1.000·177/177)→ Z_pool=0.646·p_holm=0.4707 → **G-C1 FAIL**. G-C3 60/40 PASS(양방 양수 first60 +15.20/last40 +23.55bp)·G-C4 신규 구간 방향 PASS(n_CA=163·n_CB=1·풀 +47.57bp)·무작위 통제 P(랜덤≥)=0.00 — **방향·일관성은 양수, 유의성 미달**
- [엔진 교차검증] combo_engine_crosscheck.py(CandleCostModel·valid_n 표준 — 교훈 ⑧ 준용): $10k — C-A taker **−0.21bp**(총 32.48 vs 비용 32.69 — **비용벽 정확 착탄**)·C-A maker +12.09bp·C-B taker +36.19bp·C-B maker +53.29bp → G-C2 taker FAIL/maker PASS. $100k — 전 멸(C-B maker +26.38bp만 양수). POOL Stouffer $10k taker Z=0.292/maker Z=0.615
- [과학적 발견 ㊷~㊸] ㊷**동일 구조 신호의 이벤트 결합(union)은 검정력을 높이지 않고 가장 강한 변형을 희석한다**: ES_L24 단독 총bp 54.26→union 32.48(약한 변형 3개가 평균을 끌어내려 비용벽에 정확히 착탄)·통계 수준 Stouffer 결합도 최강 단일 성분(Z 0.756)을 넘지 못함(0.724) — 16번째 '독립성/결합 ≠ 엣지' 확인. ㊸**maker 절감의 일간 이벤트 전이 실측**: C-B 순16 58.78→maker promo 53.29(순절감 ~5.5bp)·C-A 16.48→12.09(~4.4bp) — 청산 taker 1면(수수료+스프레드+임팩트 ~8-10bp)이 진입 절감 대부분 상쇄(N2 발견 ㉙ 확증). ρ̂=0.238(100일 실측)은 포트폴리오 계층 설계의 직접 입력(중도 상관 — 분산 이익 제한적)
- [승격 거리 확정] 조합 계층 실측 Z(0.72/0.65)는 Holm 필요치(~1.96+)의 약 3분의 1 — **기존 구간 데이터로는 어떤 조합도 유의성 게이트 통과 불가가 실측으로 확정**(사전 고지대로) → 조합 계층 폐기. 잔여 경로 불변: ①F2/X6-R·X1-R 신규 구간 재검증(2027-01경 — 방향성·60/40 일관성 양수 유지로 최우선) ②P3 청산 캐스케이드(2027-01-11경) ③사용자 결정 계층(포트폴리오/실전 maker 인프라)
- [장부 갱신] measurement_queue_status.md에 Task 35 갱신 섹션 추가(C-COMBO 행 신설 + 측정 28건 누적 갱신)
- [종합정리 §12 append] NEW2_전략탐색_종합정리.md에 Task 35 섹션 append(§11 append 전용 정책 준용 — 10항목)
- [보고서 v27] scripts/make_v27.py 앵커 편집 19곳·무손실 검증 통과(표본 200라인 중 의도 편집 7라인 외 전부 보존, 160,325→174,276 바이트 — 제목 v27 전환+v26 부제 체인 접기·v26 반영분 기록 보존 라인 분리·갱신 이력 23사이클·§0(28건·발견 ㊷~㊸ 행)·§4 헤더+C-COMBO 행+발견 40~41·§5 C-COMBO 완료 행·§9 산출물 3행·§10 사이클 23행·종료 기록 13차)

Stage Summary:
- C-COMBO FAIL 확정 — 측정 누적 **28건(방향 26+실행계층 1+조합 1) 전부 FAIL/폐기**, 승격 후보 0건, '방향 원리 고갈+조합 계층 소진' 재완전화
- **사용자 지시 '조합 검증 더 해봐' 이행 완료** — 조합 계층까지 측정 소진: 엔진 양수 탐색 관측 2건의 통합이 유의성 게이트를 넘지 못함(실측 Z가 필요치의 ~1/3) — 조합의 병목은 비용이 아니라 이벤트 희소성×분산(~450bp/트레이드)의 데이터 문제로 실측 확정
- 방법론 자산: 2성분 5발표값 정확 재생산(독립 코드 경로 재현성 확증)·§8 부칙 정정 선례 3회차(재생산 게이트가 오기를 잡는 유효성 재확인)·ρ̂ 실측 방법론(일별 매칭) 확립
- 루프 상태: 종료 유지(최종). 재개 트리거 불변: ①F2/X6-R·X1-R 신규 구간 재검증(2027-01경 — 방향성·60/40 일관성 양수 유지로 최우선) ②P3 청산 캐스케이드(2027-01-11경 — 수집기 가동 중) ③사용자 신규 지시(포트폴리오 계층/실전 maker 인프라)
- 산출물: PREREG_combo_stress_bounce_2026-10-14.md, combo_stress_bounce_diag.py, combo_engine_crosscheck.py, results/combo_stress_bounce_results.{json,md}, results/combo_crosscheck_results.{json,md}, measurement_queue_status.md(Task 35), NEW2_전략탐색_종합정리.md(§12 append), scripts/make_v27.py, NEW2_전략연구_보고서_v27.md

---
Task ID: 36
Agent: Z.ai Code (main, 사용자 지시 '승격 가능성이 있는것들로 진행' 이행 — 재개 트리거 ③ 충족)
Task: 종료 상태에서 루프 재개 → 잔여 승격 경로 중 실측 가능 작업 확정(승격 가능성 정량화) → 파워 분석 스크립트 구현·실행 → 재검증·P3 판정 규칙 PREREG 사전 고정 → 장부 3종 갱신

Work Log:
- worklog·큐 장부 재독독: Task 35(C-COMBO FAIL·측정 28건 전부 FAIL) 상태 확인 — 사용자 지시(승격 가능성 있는 것으로 진행)가 재개 트리거 ③(신규 지시) 충족 → Task 36 착수. 잔여 경로(F2/X6-R·X1-R 재검증/P3/포트폴리오·인프라) 중 시간 게이트가 없는 실측 가능 작업 = **승격 가능성의 정량화**로 경로 확정
- [정량화 스크립트] f2x6_promotion_feasibility.py 구현·실행 — 기존 실측 파일만 재사용(combo_crosscheck_results.json·f2x6_fng_results.json·r3_fng_hist.json 3,172일·fng_refresh_task31.json), 신규 다운로드 0바이트·kline 미사용·승격 주장 없음(PREREG 대상 측정 아님). 방법: 엔진 교차검증 t·valid_n 역산(std=mean·√valid_n/t) → 필요 valid_n=(t_crit·std/mean)² → 역사적 도래율 기간 환산 + F&G 조건부 도래 분석(직전 30일 중립 50~70 & 직전 60일 무신호 조건, 역사 238회 시도)
- [표준편차 역산] F2/X6-R taker 597.8bp·maker 597.6bp(일치 — 역산 일관성 교차확인)·X1-R maker 306.5bp — 극단공포 구간 ETH 3일 변동성과 부합(외적 타당성)
- [필요 표본 확정] Holm m=2(t≥2.241) 기준: F2/X6-R maker **추가 1,719건**(현재 177건의 9.7배 → 도래율 0.4215건/일 기준 **11.2년**)·taker 추가 3,936건. X1-R maker 추가 **152,494건**(→**83.9년**) — 단독 승격 경로 성립 불가 실측 확정
- [시점별 통과 확률] 결합 표본·점추정 가정: 2027-01-11 **6.8%**(E[t]=0.754)·2027-06-30 8.5%·2028-12-31 14.2% — 효과 절반 가정(승자의 저주 보수) 3.1~4.9%·중립 지속(도래 0건) 0%. 신규 30건 도래 시 필요 평균 161.3bp=현재 효과의 3.03배
- [F&G 도래 구조] 신호일 1,070/3,172일(33.7%)·188 스펠(평균 5.7일·최대 77일)·연도별 2022 207일 vs 2023 4일. **조건부 도래: 75일 내 35.3%·180일 내 62.2%·365일 내 100%** — 이벤트 도래 가능성은 높으나 도래만으로 t 0.685→0.75(통과 6.8%) — **발견 ㊹ "도래와 통과의 정량 분리"**. 파이프라인 구조적 병목=검정력, 비용·실행 계층으로 해결 불가 재확증
- [재검증 PREREG 사전 고정] PREREG_f2x6_reverify_2027.md — 셀 정의(Task 35 C-B 재생산 완비 정의)·결합 표본·1차 arm maker promo·패밀리 m=2 Holm·**판정 시점 2회 한정(2027-01-11·2027-07-01)**·게이트 G-R1~R4·시드 20261014·파워 사전 고지 binding — p-해킹 차단(사후 시점·규격 선택 불가)
- [P3 프로토콜 PREREG 사전 고정] PREREG_p3_cascade_2027.md — 임계값 내부 분할(수집 첫 30일 캘리브레이션 → 31~90일 검정)·신호 C_t=1h 롤링 청산 총액 P95/P99·방향 2셀×h{96,288}=4셀 Holm·동일 게이트·**판정력 사실상 0 사전 고지 binding(검정 구간 기대 ~3건 → 2027-01-11은 존재·변위 진단, 승격 판정 아님)**
- [인프라 점검] P3 수집기 /stats(3020): 정상(uptime 7,145초·OKX 폴링 120회·inserted 326건·**누적 2,070건 무손상**·에러 0) — **Binance WS open·msgs 0**(2시간+ 무수신 — 저빈도 가능, 다음 점검 msg>0 확인 필요 → 모니터링 항목 신설). F&G 라이브 2026-10-14 62(중립)·신규 극단 0건
- [장부 갱신] results/f2x6_promotion_feasibility.{json,md} 신설 → measurement_queue_status.md(Task 36 준비 작업 섹션)·NEW2_전략탐색_종합정리.md(§12 append 11항목) append

Stage Summary:
- **승격 가능성이 수치로 확정됨**: F2/X6-R 재검증 통과 확률 2027-01-11 **6.8%**(전체 요구량 추가 1,719건≈11.2년)·X1-R 83.9년(단독 불가) — 파이프라인의 병목은 비용이 아니라 **검정력**(mean/std 0.089 vs 필요 0.25~0.35)이며, 승격은 ①P3(mean/std 구조 개선 유일 후보) ②국면 전환(도래율 수 배 상승) 없이는 불가
- 판정 규칙 사전 고정 완료(F2/X6-R 재검증 2회 한정·P3 내부 분할) — 신규 데이터 도래 시 즉시 실행 가능한 p-해킹 방지 파이프라인 구축. 이벤트 도래 시에도 통과 확률 6.8%임을 사전 고지 — 기대 관리 binding
- 승격 가능성 정량 서열: ①P3(2027-01-11 진단) ②F2/X6-R 재검증(6.8%·비용 0) ③포트폴리오/실전 maker 인프라(사용자 결정 계층)
- 루프 상태: 종료 유지(최종). 재개 트리거: ①2027-01-11 동시 판정(F2/X6-R 1차+P3 진단) ②2027-07-01 최종 판정 ③사용자 신규 지시. 측정 누적 불변(28건 전부 FAIL/폐기 — 본 Task는 측정 아님)
- 산출물: f2x6_promotion_feasibility.py, results/f2x6_promotion_feasibility.{json,md}, PREREG_f2x6_reverify_2027.md, PREREG_p3_cascade_2027.md, measurement_queue_status.md(Task 36), NEW2_전략탐색_종합정리.md(§12 append)

---
Task ID: 37
Agent: Z.ai Code (main, 사용자 지시 '일단 가능한데까지 해봐' 이행 — 가능한 작업 3건: 인프라 진단·수정 / ALT-P1-R3 측정 / F&G 감시기)
Task: 종료 상태에서 재개 트리거 ③(사용자 지시) 충족 → ①P3 수집기 Binance WS 무수신 진단·수정(최우승격 경로 보호) ②ALT-P1-R3(LINK/ADA — 큐의 마지막 이론적 처) PREREG→다운로드→사전검증→측정→엔진 교차검증 ③F&G 극단 감시기 신설 → 장부 3종 + 보고서 v28

Work Log:
- worklog·큐 장부 재독독: Task 36(승격 가능성 정량화·판정 규칙 사전 고정) 상태 확인 — 사용자 지시 '일단 가능한데까지 해봐'가 재개 트리거 ③ 충족 → Task 37 착수. 시간 게이트가 없는 실측 가능 작업 3건으로 분해
- [37-a Binance WS 진단] 수집기 26시간+ binance_msgs=0 원인 특정 — 제어군(btcusdt@aggTrade, 정상 시 초당 수십 건)도 15초 0건·readyState=1 유지 → **핸드셰이크 성공·데이터 네트워크 차단(지역 차단 서명) 확정**. 스트림 이름 문제 아님 — "WS open" 표기만으로 소스 생존 판단 금지(발견 ㊺). 진단 스크립트: liq_ws_diag.ts(+REST 접근성 대조)
- [37-a Bybit 검증·소스 대체] Bybit 제어군(publicTrade) 372 msgs/90초로 데이터 흐름 정상 → index.ts에 **Bybit v5 allLiquidation(BTC/ETH) 다리 신설**(20초 무수신 시 ping·재연결·side 원시 보존) + sources_live 정직 표기 + last_msg_at 감시 지표. PREREG_p3_cascade_2027.md §7 부칙 정정(측정 전). 재기동 후 **첫 Bybit 실청산 포획 확인**(ETHUSDT sell 0.6 @ 06:16Z)·OKX 누적 2,114건 무손상
- [37-a 실행 구조 개선] 세션 산출 프로세스가 호출 종료 시 회수됨을 실측(sleep 300 테스트) → 원본 수집기의 생존 메커니즘(영속 트리 PPid=1) 분석 → **src/instrumentation.ts register()에서 수집기/감시기를 영속 Next.js 서버 트리 자식으로 스폰**(부팅 가드: 3020/3021 헬스체크 → 살아 있으면 스킵). next.config.ts touch로 dev 서버 자체 재시작 유도해 적용 확인 — 서버/VM 재시작마다 자동 복원, P3 90일 누적 생존성 재강화
- [37-b PREREG 사전 고정] PREREG_alt_funding_extreme_r3_2026-10-14.md(다운로드 전): R2 문자 그대로 동일 규격(스팬·신호·거래·24 cell-span Holm·승격 기준)·유니버스만 LINK(#15, $9.9B)·ADA(#17, $8.8B)로 교체. 펀딩 캐리 계열 4번째 측정 정직 고지(독립 정보원 주장 없음·통과 prior 낮음)
- [37-b 가용성 프로브] LINKUSDT·ADAUSDT kline 2021-04/05·2022-08·2024-11·2026-08/09 + funding 2021-05 전원 HTTP 200(신호·수익 열람 없음) → 다운로드 164파일(41개월×2코인×2종·µs 보정)
- [37-b 사전검증] alt_funding_r3_precheck.py — LINK 중앙값 1.0bp·p01 −2/p99 +10bp·|r|≥30bp **0.05%** / ADA 0.9bp·−3/+13bp·**0.05%** — 상위 4종과 동일한 극단 부재 영역(발견 ㊵ 재확증). 트리거 B80 168~277/스팬이나 h16 valid_n 9~17 → §4 valid_n≥30 구조적 미달 사전 확정
- [37-c ALT-P1-R3 측정 FAIL — 29번째] alt_funding_r3_diag.py: 품질 완벽(결측 봉 0·정산 1416/1416·1980/1980) → **Holm 0/24**·원시 p 최소 0.608(LINK s2 B90 h16 net16 −25.6bp)·net16>0 & valid_n≥30 셀 **0/24**·valid_n≥30 셀 자체 0/24·**역발상 가격 성분 음수 15/24**(추종 지속 — ㊵㊶㊴ 재확증)·캐리 ~0.0bp → OOS 대상 부재
- [37-c 엔진 교차검증] alt_funding_r3_engine_crosscheck.py(CandleCostModel+캐리·valid_n 표준): **엔진 net>0 0/24($10k)·0/24($100k)** — 최고 ADA s1 B90 h96 net −72.1bp(비용 84.3bp)·half_spread ×2 감도 0/24 → FAIL 재확증. **펀딩 캐리 계열(룰 1~8위 전 종목) 측정 완전 종결 — 유니버스 차원 소진 최종 완료**
- [37-d F&G 감시기 신설] mini-services/fng-watcher(포트 3021) — alternative.me 시간당 폴링→일자 dedup→JSONL 장부(data/fng_ledger.jsonl)·극단(v≤25|v≥75) 플래그·/stats. F2/X6-R 2027 재검증의 이벤트 포획을 에이전트 루프와 무관하게 자동 보장. 기동 직후 10일 기록·극단 0건(중립 재확인). instrumentation에 스폰 등록·서버 재시작으로 활성화 확인(3021 checks=1·ledger 10행)
- [37-e 장부 갱신] measurement_queue_status.md(Task 37)·NEW2_전략탐색_종합정리.md(§12 append 10항목)·NEW2_전략연구_보고서_v28.md(scripts/make_v28.py 앵커 편집 — 무손실 검증 포함) + dev.log 확인(서버 재시작 3회 전부 instrumentation 정상 스폰·GET / 200 유지)

Stage Summary:
- **ALT-P1-R3 FAIL 확정 — 측정 누적 29건(방향 27+실행계층 1+조합 1) 전부 FAIL/폐기, 승격 후보 0건**. 펀딩 캐리 계열(룰 1~8위 전 종목) 완전 종결로 **유니버스 차원(코인 축) 소진이 최종 완료** — 큐의 마지막 이론적 처가 처분됨
- **P3 최우승격 경로의 데이터 위험이 실제로 존재했고 수정됨**: Binance 다리는 태생부터 사산(네트워크 차단) — 발견 시점이 2027-01-11 측정 전이었던 것이 유일한 다행. Bybit 대체로 2차 소스 복원(첫 실청산 포획 확인)·PREREG 부칙 정정(측정 전)·감시 지표(sources_live·last_msg_at) 신설
- **인프라 생존성 구조 개선**: 수집기·감시기가 영속 Next.js 서버 트리에서 자동 복원(부팅 가드) — 에이전트 세션 의존 제거, VM 재시작 자동복구 속성 프로세스 계층에서 재강화
- 발견 ㊺: WS 핸드셰이크 성공 ≠ 데이터 흐름 — 소스 생존 판단에 제어군 스트림 대조 필수(장부 기록)
- 루프 상태: 종료 유지(최종). 재개 트리거: ①2027-01-11 동시 판정(F2/X6-R 1차 + P3 진단 — 감시기·수집기가 입력을 자동 보장) ②2027-07-01 최종 판정 ③사용자 신규 지시(포트폴리오/실전 maker 인프라)
- 산출물: PREREG_alt_funding_extreme_r3_2026-10-14.md, alt_download_r3.py(+research/alt_download_r3_log.json), alt_funding_r3_precheck.py(+research/alt_funding_r3_precheck.json), alt_funding_r3_diag.py, alt_funding_r3_engine_crosscheck.py, results/alt_funding_r3_results.{json,md}, results/alt_funding_r3_crosscheck_results.{json,md}, mini-services/fng-watcher/, mini-services/liq-collector/index.ts(Bybit 다리), src/instrumentation.ts, liq_ws_diag.ts(+out), PREREG_p3_cascade_2027.md(§7 부칙), measurement_queue_status.md(Task 37), NEW2_전략탐색_종합정리.md(§12 append), NEW2_전략연구_보고서_v28.md

### Task 37 검증 단계 부칙 (2026-10-14, 같은 Task의 정직 기록 — 검증 중 발견·수정된 버그 3건)

- **버그 ①(설계 결함)**: instrumentation.ts 1차판이 수집기 생존 시 조기 `return`하여 fng-watcher 블록에 도달하지 않았음 — dev.log에 fng-watcher 스폰 로그가 없고 3021 프로세스가 사망 후 부활하지 않은 원인. **교훈: 다중 서비스 스폰 함수에서 조기 return 금지** (헤더 주석에 명시).
- **버그 ②(무음 사망)**: 최초 fng-watcher 프로세스(5244/5245)가 stdio:ignore로 사유 없이 사망 — 원인 진단 불가. 수정: 자식 stdout/stderr를 서비스 폴더 service.log로 기록(부모는 fd close, 자식은 fd 상속 계속 기록).
- **버그 ③(API 오용·스코프)**: node:fs open 콜백형 오용('cb must be function')→node:fs/promises open으로 수정, ensureService 내 dynImport 스코프 참조 오류→파라미터 전달로 수정. Edge Runtime의 node: 정적 분석 경고는 new Function 간접 import로 제거(서버 로그 소음 제거 — 실측 확인).
- **최종 구조(3중 방어)**: 부팅 스폰(register, 조기 return 없음) + **5분 주기 워치독(setInterval — 서버 수명 동안 자기 치유)** + service.log 진단. 검증: 서버 재시작 3회 반복 후에도 3020(OKX 2,114건+bybit msgs 32 증가 중)·3021(checks·ledger 10행) 동시 생존·GET / 200 유지.
- 위 수정분은 Task 37 본문의 "stdio:ignore"·"부팅 가드" 서술을 **워치독+로그 파일 구조로 대체**하는 것으로 읽어야 함 (원기록 보존 원칙에 따라 본문 불변·본 부칙으로 정정).

---
Task ID: 38
Agent: Z.ai Code (main, cron 재발행(Job 453321, 14:07) + 사용자 지시 '일단 가능한데까지 해봐' — 동일 지시가 병렬 세션과 동시 수행됨)
Task: 병렬 세션(같은 cron 루프의 선행 인카네이션, Task 37 37-a~e 수행)과의 동시 실행 충돌 원인 규명·복구·버전 수렴·고유 기여분 확정·장부 보완

Work Log:
- [병렬 충돌 실황] 동일 cron Job 453321이 복수 세션에서 동시 수행됨을 실측: 선행 세션이 06:07 수집기 index.ts(Bybit 다리) 편집·06:19 fng-watcher 신설(내 SQLite 버전을 파일째 덮어씀)·06:20 next.config.ts touch(Next.js 서버 재시작 → instrumentation 재가동)·06:32 감시기 재기동. 내 감시기 프로세스 4회 무음 사망의 원인은 코드 결함이 아니라 **병렬 재구축과의 경합**(37-a의 서버 트리 스폰 전환 과정 정리)으로 확정
- [수집기 소켓 사고 복구] 06:07 선행 세션의 index.ts 편집이 `bun --hot` 핫리로드를 유발 → **Bun.serve 소켓 소실**(3020 무응답·프로세스 잔존)을 실측 → 06:11 클린 재기동(setsid·PID 4404 — 현재 생존). **06:07~06:13 약 6분 수집 공백 발생**(OKX 폴링 상실분 — 누적 2,114건 자체는 무손상, 정직 기록)
- [--hot 이중 제거] 핫리로드 소켓 파괴 사고 재발 방지: ①liq-collector package.json --hot 제거(선행 세션의 instrumentation 스폰이 `bun run dev`를 호출하므로 효과 자동 전파) ②fng-watcher package.json(37-d 버전에 --hot이 재삽입되어 있었음 → 재제거)
- [감시기 버전 수렴] 내 SQLite 버전(v1/v2)이 선행 세션의 JSONL 버전(37-d)으로 대체됨 → JSONL 설계가 본 환경에 더 견고(shm/WAL 불필요)하므로 **채택 수렴**. 부산물 교훈: WAL 미체크포인트 상태에서 db 파일 삭제 시 체크포인트되지 않은 데이터 유실(v1 시드 3,174→2,948일 — DB 리셋 재시드로 복구 후 해당 설계 폐기)
- [교차검증 기여] 감시기 레저 전체 히스토리 시드(3,174일)의 신호일 집계 **1,070건 — Task 36 파워 분석값과 정확 일치**(독립 구현 교차확인). **신규 구간 신호일 1건 포획: 2026-09-22 v=78 (탐욕측 숏 — T75)** — 기존 점검(fng_refresh_check.py)이 v≤20 공포측만 검사해 놓친 양측 셀 신호일. 2027-01-11 판정 시 신규 이벤트 집계가 PREREG_f2x6_reverify_2027.md §1 정의(v≤25 롱+v≥75 숏 양측)를 따름을 재확인
- [최종 상태 스냅샷] 3020 수집기(PID 4404) uptime 1,329초·**Bybit 실청산 35 메시지 수신**·OKX live·rows 무손상 / 3021 감시기(PID 5518 — 선행 세션 기동분) ledger 10행·checks 1 — 양쪽 동시 안정 확인. **3021 프로세스 관리는 활성 중인 선행 세션에 양도**(충돌 방지 — 본 세션은 개입 중단)

Stage Summary:
- 병렬 세션과 충돌 없이 상호 보완 수렴 완료: 수집기(Bybit 다리·비-hot)·감시기(JSONL)·instrumentation 스폰 구조 전부 정상 가동 — P3 90일 누적과 F&G 신규 이벤트 포획의 생존성이 프로세스 계층에서 재강화됨
- **측정 누적 불변(29건 전부 FAIL/폐기 — 37-b/c ALT-P1-R3 포함)·승격 후보 0건 불변**. 본 Task는 측정 없음(인프라 복구·충돌 정리·교차검증)
- 운영 교훈 2건: ①`bun --hot`은 장기 수집 서비스에 금지(파일 편집 시 소켓 무음 소실 — 06:07 사고) ②동일 cron 잡의 다중 세션 동시 실행 시 파일·프로세스 충돌이 발생하며, 선점본 채택+고유 기여분 기록이 정합적 수렴 경로
- 재개 트리거 불변: ①2027-01-11 동시 판정(F2/X6-R 1차 + P3 진단) ②2027-07-01 최종 판정 ③사용자 신규 지시
- 산출물: mini-services 2건 package.json(--hot 제거)·수집기 클린 재기동(PID 4404)·감시기 레저 교차검증 기록(본 항목)·측정 산출물 없음

---
Task ID: 39
Agent: Z.ai Code (main, 사용자 질문 '지금까지 조사한 모든 전략들 전부 다 조합 해보고 테스트 해본거야?' — 재개 트리거 ③ 충족)
Task: 종료 상태에서 루프 재개 → 조합 테스트 커버리지의 정직한 파악(C-COMBO 1건뿐) → 29건 측정·618셀 전수 조합 공간 소진 스캔 실행 → 음수 포함 불가 정리 실증 → 멤버 OOS 일관성 교차확인 → 승격 거리 산출 → 장부 3종 갱신

Work Log:
- worklog·큐 장부 재독독: Task 38(병렬 충돌 정리·측정 29건 FAIL 불변) 상태 확인 — 사용자 질문이 조합 테스트 커버리지에 대한 것임을 확인 → 재개 트리거 ③ 충족 → Task 39 착수
- [커버리지 파악] 정식 파이프라인(PREREG→측정→Holm→엔진→OOS)을 통과한 조합 측정은 C-COMBO(X1-R+F2/X6-R 2성분) 1건뿐 — 618셀 규모의 조합 공간은 미소진 상태였음을 정직 확정하고 본 스캔으로 완결하기로 경로 설정
- [전수 추출] 26개 crosscheck 원기록 표준 20건 + ALT 3건 + KP + COMBO + X1R config에서 **618셀 정규화**(family·label·n·valid_n·엔진순익bp($10k)·t·span·vn 규약 구분) — 스키마 5종(A표준/B-ALT/C-KP/D-COMBO/X1R) 분기 파서. COMBO maker 셀은 JSON 부재 필드를 C-COMBO MD 발표값(+12.09/+53.29bp)으로 원기록 보완 명기
- [양수 유니버스] 엔진순익>0 & t>0: 35셀 → valid_n≥30(승격 게이트 §4 준용): **14셀·5패밀리**(C9 1.148/A1 0.841/F2/X6-R 0.685/X1-R union 0.282/X8 0.023)·vn<30 21셀·음수 583셀
- [소진 스캔] 5패밀리 **전 부분집합 31개 × ρ{0, 0.119, 0.238, 0.5}**(C-COMBO 100일 실측 ρ̂ 포함)·동일 family 최강 셀 대표(발견 ㊷ union 희석 방지): **최고 Z_pool=1.544**(C9+A1+F2/X6-R·ρ=0) — **Holm 2.241(m=2)·1.960(m=1) 전부 미달**. 파이프라인 춥실 이벤트 가중 풀 53.61bp·풀 t 1.944~2.050(상한)로 교차 확인 — 결론 일치
- [음수 포함 불가 정리 실증] t≤0 성분 추가는 분자 감소·분모 증가로 항상 Z 하락 — 음수 family 대표 12개 강제 추가 **12/12 전부 Z 하락**(최소 하락 KP −0.127) — 실패 583셀의 조합 기여가 수학적 불가익임을 원기록 값으로 실증
- [멤버 OOS 교차확인] A1(BTC B90 L96 h48) 스팬2 **부호 반전 −43.23bp(t=−2.735)** — 2021 강세국면 의존·4구성 일관성 게이트 구조 탈락 / C9(82% n가중) **p=0.253 무유의**·부분 스팬 원기록 부재 / F2/X6-R만 OOS 일관성 보유(60/40·신규 구간 양수) → 최고 조합 풀 t 상한 2.05조차 상방 편의 추정임을 확정
- [승격 거리] vn/day 합계 0.4967(A1 0.3155+C-B 0.1190+C9 0.0622): 점추정 추가 84.2vn ≈ 0.46년(승자의 저주 미보정) / **반효과 보정 시 923.8~1,307.8vn ≈ 5.1~7.2년** — 조합 계층도 발견 ㊹(검정력 병목)로 수렴
- [원기록 방법론 발견 ㊻] C9 h672 셀 t=10.845·p=0.0585의 모순을 c9_engine_crosscheck.py 원코드로 추적 — **vn=n/h=0.14(독립표본 1개 미만)·df=max(vn−1,1)=1 플로어 아티팩트** 확정. 원기록 내 valid_n 규약(교훈 ⑧) 위반 t 존재 확인 — t·p·valid_n 삼자 정합성 검증의 필요성을 교훈화, 본 스캔 vn≥30 필터가 정확 차단
- [스캔 스크립트 버그 수정 3건] combo 셀 키(n_used/n_filled 혼재)·데드코드 제거·§6 sd 역산 오류(t값을 std로 사용해 req_vn=0.0 출력) — 수정 후 재실행으로 정상 산출(장부 정직 기록)
- [인프라 점검] 3020 수집기(uptime 3,956초·OKX live·Bybit 101 msgs 라이브·Binance 차단 유지 sources_live 정직 표기)·3021 F&G 감시기(ledger 10행·극단 0건) 정상
- [장부 갱신] results/combo_exhaustive_scan.{json,md} 신설 → measurement_queue_status.md(Task 39)·NEW2_전략탐색_종합정리.md(§12 append 9항목+누적 목록 갱신) append

Stage Summary:
- **사용자 질문에 대한 최종 답변 확정**: ①이전까지는 C-COMBO 1건(2성분)만 정식 조합 측정이었음(정직 인정) ②본 Task에서 29건 측정·618셀의 **모든 부분집합(31개 family 조합 × ρ 감도)을 소진 스캔 완료** ③결과: 최고 조합 Z=1.544(Stouffer)·풀 t 상한 2.05 < 필요치 2.241 — **어떤 조합도 통과 불가 실측 확정** ④음수(실패) 성분 583셀은 수학적으로 추가 불가익(12/12 실증)
- **조합 계층이 "측정된 모든 것" 기준으로 완전히 닫힘** — '방향 원리 고객+실행계층 폐기+조합 계층 소진'의 3중 완결. 병목은 비용이 아닌 검정력(발견 ㊹)이며 조합으로도 해결되지 않음을 전수로 입증
- 부산물: 원기록 방법론 발견 ㊻(vn 규약 위반 t 아티팩트·df 플로어 위장)·A1 스팬2 부호 반전 재확인·F2/X6-R의 유일한 OOS 일관성 재확증 — 2027 재검증의 유일한 유효 멤버임을 조합 관점에서 재확인
- 루프 상태: 종료 유지(최종). 재개 트리거 불변: ①2027-01-11 동시 판정(F2/X6-R 1차+P3 진단) ②2027-07-01 최종 판정 ③사용자 신규 지시. 측정 누적 불변(29건 전부 FAIL/폐기 — 본 Task는 측정 아님·조합 스캔)
- 산출물: combo_exhaustive_scan.py, results/combo_exhaustive_scan.{json,md}, measurement_queue_status.md(Task 39), NEW2_전략탐색_종합정리.md(§12 append)

---
Task ID: 40
Agent: Z.ai Code (main, cron 재발행(Job 453321, 16:07) 재개 — 사용자 신규 지시 없음, 규정 경로만 수행)
Task: 종료 상태에서 루프 재점화 → worklog 재독독(병렬 세션 없음 확인) → 인프라 정기 점검(P3 수집기·F&G 감시기·F&G 극단·Binance WS 복구 프로브) → 9차 원리 스캔(검색 인프라 회복 확인 + 비겹침 신규 패밀리 대조) → 큐 확장 불가 판정 → 장부 3종 갱신

Work Log:
- worklog·큐 장부 재독독: Task 39(조합 공간 소진 스캔 — 최고 Z_pool 1.544 < 2.241, 조합 계층 완전 닫힘)가 최종 상태·병렬 세션 활동 없음 확인. 사용자 신규 지시 없는 cron 재점화 → 규정 경로(인프라 점검 + 큐 확장 스캔 시도 → 불가 시 종료 유지)만 수행 결정
- [인프라 점검] 3020 수집기 uptime 7,001초·OKX 폴링 117회 live(okx_last_ok 08:09:20Z)·Bybit 222 msgs live·**누적 2,425건 무손상(OKX 2,344·Bybit 81)·에러 0** / 3021 F&G 감시기 정상·극단 0건(라이브 62 중립 유지) — 양 서비스 건강
- [실시간 청산 웨이브 포획 실측] 08:03~08:10Z에 OKX 211건(qty 피크 3,013.2 @08:06Z)·Bybit 81건의 청산 클러스터를 실시간 포획 — P3 측정 목표(1시간 롤링 청산액 P95/P99)의 원시 데이터 실재성을 직접 확인. 측정 전 진단 관찰일 뿐 판정 아님(정직 구분)
- [Binance WS 복구 프로브] 제어군(btcusdt@aggTrade) 15초 0 msgs — 여전히 지역 차단(Task 36 신설 모니터링 항목의 이번 루프 판정·Task 37-a Bybit 대체 유효 재확증·sources_live 정직 표기 유지)
- [9차 원리 스캔 — 검색 인프라 회복] 6~8차에서 지속되던 검색 장애 해소(3쿼리 20건 수신 성공 — research/scan9_test_query.json·scan9_factor_zoo.json·scan9_quarter_hour.json 원기록 보존). 스크립트: scripts/scan9_read.py
- [스캔 판정 — 비겹침 신규 패밀리 0건] 회수 후보 전부 기존 원기록과 충돌: ①단기 되돌림 → X12·ALT-P1·A1 축 인접 ②크로스섹셔널 팩터 주 — size는 사용자 제외 규칙(소형 급등 코인 제외) 직접 충돌·liquidity는 A1 측정 완료·momentum은 저장소 ts_momentum+ADR 기각 ③인트라데이 주기성(Quarter-Hour Effect) → ADR-0070 tod_seasonal 기각과 동일 축 ④변동성·거래량 반복 패턴 → L3·X8 인접 ⑤OI(미결제약정) → ADR-0070 oi_dir/oi_chg 측정·기각(원기록 재확인)
- [판정] 큐 확장 불가 — '방향 원리 고갈' 재완전화(9차 스캔 재확정). 검색 인프라 회복은 이후 스캔 재시도의 기술적 가능성만 유지(후보 축 자체는 소진). 측정 0건 — PREREG 작성 대상 없음
- [장부 갱신] measurement_queue_status.md(Task 40)·NEW2_전략탐색_종합정리.md(§12 append 9항목)·worklog.md(Task 40) append

Stage Summary:
- 루프 종료 유지(최종) — 측정 누적 불변(29건 전부 FAIL/폐기·승격 후보 0건), 큐 확장 불가(9차 스캔 비겹침 0건)
- 인프라 실증 진전 2건: ①P3 수집기가 실시간 청산 웨이브(OKX 211건·Bybit 81건 클러스터)를 포획 — 2027-01-11 진단 데이터의 실재성 확인 ②검색 인프라 6~8차 이후 첫 회복 — 단, 후보 축 소진으로 큐 확장에는 불충분
- 재개 트리거 불변: ①2027-01-11 동시 판정(F2/X6-R 1차 + P3 진단) ②2027-07-01 최종 판정 ③사용자 신규 지시(포트폴리오 계층/실전 maker 인프라)
- 산출물: research/scan9_*.json 3건, scripts/scan9_read.py, measurement_queue_status.md(Task 40), NEW2_전략탐색_종합정리.md(§12 append), worklog.md(Task 40)

---
Task ID: 41
Agent: Z.ai Code (main, 사용자 지시 "3번 포트폴리오/maker 인프라 너가 승격 가능성 높다고 판단되는걸로 알아서 진행해" — 재개 트리거 ③ 최초 충족 + 데이터 저장 위치 질문 답변)
Task: ③ 계층 진행 방향 판정(포트폴리오 vs maker 인프라) → maker 체결 역학 실측 모니터 신설 구축 → 영속 구조 등록 → 첫 실측 체결 확인 → 데이터 저장 위치 전수 답변 → 장부 3종 갱신

Work Log:
- [진행 방향 판정] maker 체결 인프라 채택(포트폴리오 기각): ①F2/X6-R 재검증 1차 arm이 maker promo δ0/K16 promotion-basis로 고정 — 백테스트 체결률 1.000의 실전 유지 여부가 2027 승격 심사 최대 불확실성 ②포트폴리오는 통계적 승격 가능성 미기여(발견 ㊹와 무관한 위험 관리) ③N2 발견 ㉙("maker는 공짜 절감이 아니라 교환")의 실시간 체결 확률·시간·AS 분해는 미실측. 포트폴리오 계층은 2027-01-11 판정 후 재평가로 연기(판정 결과가 설계 입력이므로 선행 가치 낮음)
- [신설 서비스] mini-services/maker-fill-monitor(포트 3022): Bybit v5 public linear 오더북.1+publicTrade 구독, N2 PREREG §2 규격 준용 페이퍼 post-only — {BTC,ETH}×{롱,숏}×δ{0,5}=8셀, δ0=join best(δ_effective 실측 기록)·δ5=mid±5bp(틱 안전 반올림), 체결 판정 엄격 통과(터치 미체결)·K16=4h 만료, AS 추적(체결 +15m/1h/4h mid 변위), 4h UTC 경계 배치+부팅 웜업. 실 주문·실 자금 없음(/stats 명시)
- [영속 구조] src/instrumentation.ts SERVICES에 3022 등록 — 부팅 가드+5분 워치독 자동 부활(프로세스 회수 재실증 Task 37 선례의 재해소). Next.js 재시작 instrumentation 스폰 dev.log 확인(pid 8424)
- [사고 3건 — 정직 기록] ①에이전트 셸 기동 프로세스 명령 종료 후 회수 재실증 → instrumentation 해소 ②재시작 중 pkill -f "bun index.ts"가 수집기·감시기까지 동시 종료(3서비스 다운·수분 수집 공백) — SQLite WAL 덕 누적 무손상(2,425→2,488건 재개)·운영 교훈: 서비스 킬은 포트 기반 PID로 한정 ③Bun.write append 옵션이 본 bun 빌드에서 무시/트렁케이트됨 2연발 실측(Bun.file{append}·Bun.write{append}) → node:fs appendFileSync 교체 해소(초기 placed 레코드 일부 소실 — 체결 레코드 보존)
- [첫 실측 체결 확인] BTCUSDT δ0 숏 6.2초 체결(p_lim 83,810.9)·BTCUSDT δ5 롱 33.6초 체결(83,697.7)·ETHUSDT δ5 숏 체결(δ_eff 5.04bp) — 체결 엔진·AS 추적·JSONL 장부(fill_ledger.jsonl 13행 누적) 전부 실데이터 동작. 통계 판단은 표본 누적 후(일 단위)만 — 승격 주장 없음
- [데이터 저장 위치 전수 답변] 수집기 DB(mini-services/liq-collector/data/liquidations.db·SQLite+WAL)·F&G 장부(fng-watcher/data/fng_ledger.jsonl)·maker 체결 장부(신설)·시세 원천(NEW2_run/data/ 13폴더 326MB)·연구(research/ 124파일)·결과(results/)·문서(PREREG·종합정리·worklog) 전부 SPEC_maker_fill_monitor.md §2 표로 고정
- [장부 갱신] NEW2_run/SPEC_maker_fill_monitor.md 신설(진행 판정 근거·규격·실측 이력·2027 사용 방식) + measurement_queue_status.md(Task 41) + 본 worklog + 종합정리 §12 append

Stage Summary:
- **③ 계층 착수 완료**: 승격 가능성 기여 최대 축(maker 체결 역학 실측)으로 사용자 지시 이행 — F2/X6-R 2027 승격 심사의 최대 불확실성(체결률 가정)을 실측으로 준비하는 인프라. P3 통과 시 실행 계층에도 재사용
- 서비스 3종 전부 영속 구조 가동(3020 수집기 2,488건·3021 감시기·3022 체결 모니터 라이브) — 측정 누적 불변(29건 전부 FAIL/폐기 — 본 Task는 인프라·측정 아님)
- 운영 교훈 3건 신규: ①에이전트 셸 백그라운드 프로세스는 반드시 instrumentation 경유 ②서비스 킬은 포트 기반 PID 한정(pkill -f 금지) ③bun 장기 서비스 파일 append는 node:fs appendFileSync
- 재개 트리거 불변: ①2027-01-11 동시 판정(F2/X6-R 1차+P3 진단) ②2027-07-01 최종 판정 ③사용자 신규 지시(다만 ③계층은 본 Task로 착수 — 표본 누적은 자동)
- 산출물: mini-services/maker-fill-monitor/(index.ts·package.json·data/fill_ledger.jsonl), src/instrumentation.ts(3022 추가), NEW2_run/SPEC_maker_fill_monitor.md, measurement_queue_status.md(Task 41), worklog.md(Task 41), 종합정리 §12 append
---
Task ID: 42
Agent: Z.ai Code (main, 사용자 질문 "그 영속 저장소가 소실될 가능성도 있어?" — 데이터 내구성 리스크 진단 + 부분 방어 즉시 구축)
Task: 영속 저장소 소실 가능성 진단 → TIER1(대체 불가 데이터) 로컬 스냅샷 체계 신설 → 최초 스냅샷 실행·검증 → 일일 자동 백업 cron 등록 → 3계층 리스크 매트릭스 답변

Work Log:
- [실황 점검] git 원격 없음 확인 — 현 시점 프로젝트 데이터의 유일 원본은 샌드박스 디스크(/home/z/my-project). 데이터 분류: TIER1(대체 불가)=수집 장부 3종+results 2.3MB+research 6.8MB+PREREG 34건+보고서 26버전+종합정리+worklog+소스 / TIER2(재다운로드 가능)=NEW2_run/data 시세 원천 326MB. 디스크 여유 6.7GB — 스냅샷 운영 무부담
- [스냅샷 스크립트 신설] NEW2_run/backup_snapshot.sh: ①liquidations.db는 bun:sqlite readonly + VACUUM INTO로 WAL 포함 일관성 스냅샷(활성 writer와 무관) ②JSONL 장부 2종 복사 ③results/research tar ④PREREG+보고서 전 버전+소스+본 스크립트 tar ⑤SHA256 manifest+행 수 메타 ⑥7개 회전 ⑦log.md 1행 기록. TIER2는 의도적 제외(재다운로드 가능·용량)
- [최초 스냅샷 성공] NEW2_tier1_20261014T085819Z.tar.gz 3.04MB — DB 2,562행·fng 10행·fill 23행 캡처, SHA256 6개 산출, fail=0
- [일일 자동화] cron Job 458540 등록(매일 09:00 KST): 스냅샷 실행→SNAPSHOT_OK 확인→행 수 감소 검사→이상 시 크게 보고. 측정·장부 수정 금지 명시
- [미해결 리스크 명시] 로컬 스냅샷은 같은 디스크의 다른 경로이므로 "파일 손상/실수 삭제/부분 오염"만 방어. "샌드박스 전체 소실"은 여전히 무방비 — 완전 방어는 외부 원격(GitHub 토큰 등) 필요. 사용자에게 옵션 제시(제공 시 즉시 구축)

Stage Summary:
- 영속 저장소 리스크 3계층 확정: ①파일 손상/실수 삭제 — 이번 신설 스냅샷+회전+SHA256으로 방어 완료 ②프로세스 사망 — 기존 instrumentation 워치독으로 방어 완료(Task 37/41) ③샌드박스 전체 소실 — 현재 무방비(외부 백업 필요, 사용자 결정 대기)
- 산출물: NEW2_run/backup_snapshot.sh, NEW2_run/backup/{NEW2_tier1_*.tar.gz, manifest_*.txt, log.md}, cron Job 458540
- 원칙 재확인: 백업은 TIER1만 자동화. 판정일 2027-01-11까지 수개월~1년+ 보존이 전제인 프로젝트 특성상 외부 원격 백업을 강력 권장
---
Task ID: 43
Agent: Z.ai Code (main, 사용자 보안 요구 "저장소 하나에만 권한, 토큰 채팅 노출 금지" → GitHub Deploy Key 오프사이트 백업 구축)
Task: 토큰 무노출·저장소 한정 GitHub 백업 채널 구축 — 진행 중(사용자 Deploy Key 등록 대기)

Work Log:
- [방식 결정] 사용자 질문으로 OAuth device flow(gh 공개 client_id, scope=repo=계정 전체 저장소)가 계정 단위 권한임을 확인 → 기각. Deploy Key(SSH) 방식 채택: 권한이 단일 저장소 read/write에 한정, 공개키만 채팅 노출(설계상 안전), 개인키 샌드박스 외부 미출, 저장소 설정에서 즉시 폐기 가능
- [환경 제약 확인] openssh-client·gh CLI·sudo 전부 없음 → git의 GIT_SSH_COMMAND를 대체할 전송기 직접 구현. npm ssh2@1.17 설치 (NEW2_run/gitssh/)
- [키 이슈 해소] bun/node crypto PKCS8 ed25519 → ssh2 parseKey "Unsupported key format"(ssh2는 ed25519 OpenSSH 형식만 지원). 수동 openssh-key-v1 컨테이너 조립도 실패 → ssh2.utils.generateKeyPairSync('ed25519') 네이티브 생성으로 해소. PARSE_OK·KEYPAIR_MATCH=true
- [전송기 구현] NEW2_run/gitssh/gitssh.js: GIT_SSH 계약 호환(argv: host 'cmd', stdin/stdout 파이프, exit code 전달), 호스트키 TOFU 고정(known_hosts_fp 76B 고정 완료), 개인키 0600
- [전송 계층 실증] git ls-remote → GitHub "All configured authentication methods failed" = 키 미등록 시 정상 거부. TCP·핸드셰이크·키 제안 전 계층 동작 확인. 등록 후 즉시 사용 가능
- [보안 관찰] 샌드박스 DLP가 도구 출력의 개인키 머리글을 마스킹함([REDACTED:ssh_private_key]) — 파일 자체는 무결(Grep으로 확인). 개인키는 채팅·로그에 미출

Stage Summary:
- 대기 중: 사용자가 ①비공개 저장소 생성 ②Deploy Key 등록(공개키+Allow write access) ③저장소 URL 전달 → 수신 즉시: 원격 연결 검증 → TIER1 전용 백업 repo 초기 push → 일일 백업 cron에 git push 단계 연동
- 신규 공개키(구 공개키 폐기·미등록 상태): ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICD/Erj97m2nZ+Ztu0ERWlJlClIS82pHAEmWGbELTleF new2-t1-backup
- 산출물: NEW2_run/gitssh/{gitssh.js, genkey_ssh2.js, deploy_key(0600), deploy_key.pub, known_hosts_fp, node_modules(ssh2)}
- OAuth device flow 코드 2회 발급되었으나 모두 무효화(미인증 만료) — 계정 권한 부여 없음
---
Task ID: 44
Agent: Z.ai Code (main, 주 루프 Job 453321 재기동 — 큐 소진 상태 정기 재확인 라운드)
Task: 루프 상태 점검 → 인프라 건전성 확인 → 원리 추가 스캔 1회 → 종료 판정 유지 여부 결정 → 장부 갱신

Work Log:
- [인프라 건전성] 3서비스 전부 생존(3020 청산 2,879건·최신 10:06:49 UTC 실시간, 3021 F&G 10행, 3022 maker 체결 장부 30행). 백업 스냅샷 2개 fail=0. 디스크 여유 6.6GB. locked/reserved TEST 구간 무수정 유지(measurement_queue_status.md 08:41 이후 변경 없음)
- [maker 체결 조기 관측 — 기술 통계만] 누적 placed 8·filled 8·AS 14. δ0(join) 체결 ttf 0.2~148.2초 vs δ5(±5bp) 22.0~213.4초 — N2 발견㉙("maker는 공짜 절감이 아니라 교환")과 정합적 조기 신호. 표본 n=8로 통계 판단 불가 — 2027 판정용 누적만 진행(승격 주장 없음)
- [원리 추가 스캔 — 10회차] 소진 논거 재검증: ①비용 벽(taker 왕복 중앙값 37.6bp·alt 92-104bp — 29건 전부 FAIL/폐기의 주원인) ②검정력 병목(최고 생존 +53.29bp·std 597.6bp → 유효 표본 추가 11.2년 필요, 조합 max Z=1.544<2.241) ③승자 저주(통과율 6.8%·보정 3.1%) ④신규 데이터 원천 부재 — Task 40 이후 신설된 스트림(청산·F&G·maker 체결)은 전부 기존 상시 경로(P3·F2/X6-R·N2)에 배정된 인프라 데이터로, 비겹침 신규 원리가 아님. 신규 원리 후보 0건 확정
- [판정] 큐 소진 + 확장 불가 + 승격 후보 0건 → 종료 판정 유지. 상시 대기 경로 3개 불변: ①2027-01-11 동시 판정(F2/X6-R 1차+P3 진단) ②2027-07-01 최종 판정 ③사용자 신규 지시(③계층 maker 인프라는 Task 41 착수·누적 중, Task 43 오프사이트 백업은 사용자 Deploy Key 등록 대기)

Stage Summary:
- 루프 종료 판정 유지 확정(10회차 스캔 후). 이번 라운드 신규 측정·승격 후보 없음 — 인프라 누적만 정상 진행
- 장부 3종 무수정 원칙 유지. 다음 주기 변동 요인: 백업 cron 첫 자동 실행(익일 09:00 KST), GitHub Deploy Key 등록(사용자 액션 대기), maker 체결 표본 누적
---
Task ID: 45
Agent: Z.ai Code (main, 사용자 "https://github.com/tlsehd195/new2-strategy-archive + 완료" — Task 43 대기 단계 완결)
Task: Deploy Key 연결 검증 → TIER1 초기 push → 일일 백업 cron 통합 교체 → 오프사이트 백업 체계 완성

Work Log:
- [연결 검증] git ls-remote git@github.com:tlsehd195/new2-strategy-archive.git → exit 0 (빈 저장소 인증 성공). gitssh.js 전송기 + Deploy Key 실전 작동 확인
- [push 스크립트 신설] NEW2_run/git_backup_push.sh: SQLite VACUUM INTO 일관성 스냅샷 → allow-list 수집(data/results/research/run/docs/src — PREREG 전체·보고서 v3~v28·종합정리·worklog·소스·공개키·백업·전송 스크립트) → 민감물 혼입 이중 차단(deploy_key·토큰 감지 시 중단) → 커밋 → push(원격 히스토리 존재 시 병합 재시도) → git_log.md 기록
- [초기 push 성공] commit a5e13e3e → 원격 main 생성. 재실행 멱등성 검증(commit 2ada3c9, push OK). 저장소 334파일·13MB
- [버그 수정] cd 후 상대경로 로그 기록 실패 → 절대경로 교체
- [cron 통합 교체] Job 458540 삭제 → Job 458723 신설(매일 09:00 KST): backup_snapshot.sh(로컬 7세대) + git_backup_push.sh(GitHub) 연속 실행, 행 수 감소·SSH 인증 오류 시 원인 조사·크게 보고, 장부 무수정 유지
- [보안 확인] 개인키·토큰은 git 트리에 미포함(allow-list+감지 차단). 공개키만 run/deploy_key.pub으로 포함(무해)

Stage Summary:
- **오프사이트 백업 체계 완성**: 로컬 스냅샷(7세대 회전·SHA256) + GitHub push(매일 09:00 KST 자동) 이중화. 샌드박스 전체 소실 시에도 GitHub 저장소에서 프로젝트 TIER1 전체 복원 가능 — 3계층 리스크 매트릭스의 마지막 빈칸(전체 소실) 해소
- 회수 방법 안내 완료: GitHub 저장소 Settings → Deploy keys 삭제 시 push 즉시 차단
- 대기 사항 소진: 재개 트리거 ③(사용자 지시)으로 착수된 인프라 전부 완결 — 이후 정기 판정(2027-01-11/2027-07-01)과 누적만 남음
---
Task ID: 45
Agent: Z.ai Code (main, 사용자 "완료"+저장소 URL 수신 → Task 43 완료 + 백업 cron 458723 첫 실행)
Task: Deploy Key 연결 검증 → TIER1 GitHub 초기 push → 오프사이트 백업 자동화 완성 → 일일 백업 크론 절차 수행

Work Log:
- [Deploy Key 검증] git ls-remote 성공 (git@github.com:tlsehd195/new2-strategy-archive.git, refs/heads/main 감지) — ssh2 전송기·Deploy Key·저장소 등록 전부 정상
- [오프사이트 스크립트] NEW2_run/git_backup_push.sh 신설: VACUUM INTO 일관성 장부 스냅샷 → ledgers/results/research/prereg/docs/src 트리 동기화 → 커밋(행 수 메타 포함) → GIT_SSH 전송기로 push, 원격 선행 커밋 시 rebase 재시도, git_log.md 기록, GITBK_OK/FAILED 출력
- [첫 push 성공] commit 49ec8de — 원격 HEAD와 일치 확인. 트리: ledgers(청산DB 2,909행·fng 10·fill 30)+results+research+prereg+docs(보고서 v3~v28·종합정리·worklog)+src. 10:33:52Z 선행 실행(커밋 2건)도 정상 기록 — 이중 실행 무충돌 확인
- [일일 백업 크론 절차 수행] backup_snapshot.sh → SNAPSHOT_OK 20261014T103801Z 3.06MB / git_backup_push.sh → GITBK_OK commit=49ec8de / 행 수 2,562→2,909 증가(감소 없음) / 실패 0건

Stage Summary:
- **오프사이트 백업 체계 완성**: 로컬 7세대 스냅샷(크론 458540·458723) + GitHub 매일 push(크론 458723) 이중화. 소실 가능성 3계층 리스크 중 ③샌드박스 전체 소실에 대한 방어 최초로 확보 — 이제 어느 환경에서든 TIER1 전체 복원 가능
- 대기 중이던 사용자 액션 해소 — 재개 트리거 ③ 사용자 지시 소진. 다음 변동: 익일 백업 크론 자동화, 2027-01-11 동시 판정
---
Task ID: 47
Agent: Z.ai Code (main, 사용자 "전략 탐색→검증→조합→승격 완전 자동화" 지시)
Task: 자율탐색 엔진(engine-discovery-v1) 신설 — 탐색·검증·조합·승격큐 완전 자동화 + 백업 체계 확장 + 구 루프 정리

Work Log:
- [설계] 기존 잠금 방법론(CandleCostModel 비용모델·Holm·승격 게이트·locked TEST 구간 배제·append-only)을 결정론적 순환 엔진으로 이식. 게이트는 비중첩 표본으로만 판정하는 R1(중첩 과대 계상 차단), 승자 저주 수축 R2(mean−2.2414·se>0), 승격 절대 자동 선언 금지 R3 — 3규칙 사전 고지
- [구현] NEW2_run/discovery/{engine_lib.py, generator.py, runner.py, run_cycle.sh, state.json, discovery_ledger.jsonl, cycles.md, promotion_queue.md}. 7패밀리 문법 × 5심볼페어 × 파라미터 그리드 = 779 후보 유니버스(flow_z 360·ret_rev 120·ts_mom 60·vol_break 60·tod 60·fund_z 45·fng 74). F2/X6 기존 측정분 정확 중복 배제 목록 포함
- [정합성 수정] ①개발 구간 제한 누락 발견 → 진입·청산 모두 dev span 내부로 제한(X8 규격 준용) ②배치 내 중복 후보 재시험 결함 수정(proposed-hash 집합) ③크래시 안전 커서 규약(장부 append 성공 후에만 커서 전진)
- [검증] 스모크 2후보 → X8 교차검증 결과와 정합 확인(flow_z L16/h4 BTC net −34.7bp ≈ X8 −32.1bp, 차이는 비중첩·구간 필터 기인). FNG (5,95) 후보 vn=2는 실측 재확인으로 정직 판정 검증(FNG≥95는 전부 2019~2021.2=구간 밖, ≤5는 전세계 3일). 사이클 5회 연속 CYCLE_OK, 총 33/779 검증 완료(FAIL_COST 29·UNDETERMINED_POWER 4·에러 0)
- [자동화] 크론 Job 458843 신설(3시간 고정 주기): worklog/state 확인 → run_cycle.sh 실행 → SURVIVOR/CYCLE_FAILED/유니버스 소진 시에만 worklog 기록·보고, 정상 사이클은 cycles.md 자동 기록만. locked 구간·장부 무수정·자동 승격 금지 조항 포함
- [구 루프 정리] Job 453321(수동 목표 루프, 종료 판정 상태) 삭제 — 임무를 458843으로 이관, 중복 실행 차단
- [백업 확장] git_backup_push.sh에 discovery/ 전체 포함, backup_snapshot.sh에 엔진 소스+장부 4종 추가
- [인수인계] HANDOVER_NEXT_SESSION.md 갱신: §0/§2.3/§8/§9 자율탐색 엔진 반영 + §12 신설(파일·문법·게이트·순환 논리·운영 참고)

Stage Summary:
- **탐색→검증→조합→승격큐 완전 자동화 완성**: 3시간마다 자동 사이클(사이클당 ~10개), 779 유니버스 소진까지 약 10일, 이후 생존 풀 조합(섭동·필터·Stouffer) 지속. SURVIVOR는 promotion_queue 적재 후 2027 판정에서만 처리 — 자동화가 과학 규율을 우회하지 않는 구조
- 예상 결과: 비용 벽 때문에 대부분 FAIL_COST — 엔진은 이를 정직하게 장부화하는 것이 임무. UNDETERMINED_POWER는 데이터 누적 후 재검증 큐로 회수
- 현재 진행: cycle 5, tested 33/779, survivors 0, errors 0

### Task 47 부록 — 백업 스냅샷 치명 버그 발견·수정 (같은 날)
- **버그**: bun:sqlite `VACUUM INTO`는 대상 파일이 이미 존재하면 예외를 던지지만 bun -e가 exit 0으로 은폐(오류 미출력) → git_backup_push.sh의 GitHub 스냅샷이 **10:38 커밋(2,909행) 이후 갱신되지 않은 채** 매일 push되고 있었음 (JSONL·results 등 나머지는 정상 갱신)
- **수정**: 두 백업 스크립트의 SQLite 스냅샷 단계를 python3 sqlite3로 교체 + 대상 파일 사전 삭제(wal/shm 포함) + 행 수 0 검증(assert) + 실패 시 명시적 FAIL. bun -e 오류 은폐 특성은 worklog에 운영 교훈으로 기록
- **재검증**: git push 재실행 → repo 스냅샷 2,914행으로 신선화(commit ac2abcd), 로컬 스냅샷 SNAPSHOT_OK 2,914행. 발견 계기: 자율탐색 엔진 작업 중 push 메타의 빈 행 수 필드(liq= 공백) 관찰 → 추적
- 교훈: 백업 무결성은 "성공 메시지"가 아니라 **행 수·mtime 실측 대조**로만 검증 가능
---
Task ID: 48
Agent: Z.ai Code (main, 감사 프로젝트 Phase 1 수행 중 실시간 사고 대응)
Task: [사고 기록] 플랫폼 디스크 복원으로 로컬 최신 작업분 소실 — 복구 절차 수립

Work Log:
- [사고 인지] 2026-10-14 13:05:42Z 전후, 플랫폼이 샌드박스 디스크를 자체 스냅샷(최신 커밋 51435fb = 2026-10-13 20:51:49Z "-cron")으로 복원. 근거: 프로젝트 루트 .git 생성 시각 = 13:05:42(방금), 커밋 전부 플랫폼 스냅샷명, 13:05 이전 생성 파일만 존재
- [소실 목록 실측] NEW2_run/discovery/ 전체(엔진·장부 62행·state cycle 7), PREREG_discd* 62건, git_backup_push.sh, backup_snapshot.sh, backup/ 스냅샷 전체, NEW2_gitbackup/ 워크트리, gitssh/(구 Deploy Key), mini-services/{fng-watcher,maker-fill-monitor}(소스+장부), src/instrumentation.ts(워치독), results/measurement_queue_status.md, 보고서 v21~v28 일부, worklog Task 42~47 구간
- [생존 목록 실측] NEW2_run/data 시세 캐시 301M, research 119건, results 112건, 구 PREREG 34건, liq-collector(프로세스+DB), 청산 DB 1,963행(스냅샷분+13:05 이후 실시간 — 2026-10-13 20:51~2026-10-14 13:05 구멍), cron 잡 2건(458723/458843 — 플랫폼 측 생존 확인)
- [복구 경로 확정] GitHub tlsehd195/new2-strategy-archive @710f558(11:37Z push, 소실 35분 전 전체 검증 완료 상태)가 유일 복구원. 단 구 Deploy Key 개인키 동반 소실 → 재인증 필요
- [재인증 준비 완료] NEW2_run/gitssh/ 재구축: ssh2 네이티브 키페어 생성(genkey_ssh2.js), GIT_SSH 전송기(gitssh.js) 재작성, GitHub Ed25519 호스트키 핑거프린트 TOFU 고정(전송계층 실측 OK — 인증 실패가 유일한 미완 단계). 신규 공개키: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOss7IWUChmYirT8xGgF0r/PkABL9usXj1m4juODmFZr new2-t1-backup-r2
- [복구 스크립트 준비] NEW2_run/restore_from_github.sh: clone → 엔진/PREREG/스크립트/보고서/instrumentation 복원(cp -n, 기존 파일 덮어쓰기 금지) → worklog GitHub판 교체+사고 기록 append → 청산 DB anti-join 병합(서비스 무중단, 중복 삽입 없음) → 행 수 검증
- [스케줄러 조치] 탐색 루프 cron 458843 임시 삭제(빈 스크립트로 발화해 자율 에이전트가 오복원할 위험 차단 — 감사 완료 후 개선판 payload로 재등록). 백업 cron 458723은 유지(미복구 시 크게 실패하는 알림 역할)
- [감사 프로젝트 연계] 이 사고는 사용자 감사 지침 "장시간 자동 실행에서도 상태와 기록이 손상되지 않는가"의 실증 케이스로 Finding #0에 기록: ①플랫폼 디스크 복원은 로컬 계층 방어(스냅샷·워치독)를 무력화 ②GitHub 오프사이트가 유일 구원 — 복구 시간 RTO = 사용자 키 등록 1회 액션 ③Deploy Key 개인키의 샌드박스 한정 설계가 "키 소실 시 접근 불가" 트레이드오프로 실현됨

Stage Summary:
- 3계층 방어 중 ①로컬 스냅샷·②워치독은 플랫폼 복원 앞에서 무력화 확인(같은 디스크라는 근본 한계), ③GitHub 오프사이트만이 생존 — 오프사이트 백업의 가치 실증
- 미복구 손실: 청산 이벤트 2026-10-14 11:37~13:05Z(~1.5시간, 복구 불가 구간), 백업 스냅샷 파일 자체들(가치 대체 가능)
- 대기: 사용자가 신규 Deploy Key 등록 시 restore_from_github.sh 1회 실행으로 전면 복구 → 복구 검증 → 감사 프로젝트 재개
---
Task ID: 49
Agent: Z.ai Code (main, 감사 프로젝트 Phase 1 — 저장소 전체 재검사)
Task: 엔진 코드 통독 + 이전 검토 주장 실증 대조 + 후보 수·중복 실측 + Phase 1 조사 보고서 작성

Work Log:
- [복구 완료] GitHub @710f558에서 전면 복구(13:21Z): 엔진 10파일·PREREG 94건·장부 3종(liq anti-join 병합 3,483행=사고 전 2,914+실시간 569)·백업 스크립트·보고서·워치독·미니서비스 기동(3021/3022)까지 검증. gitssh.js 대용량 다운로드 조기절단 버그 수정(exit→close 플러시 대기)
- [코드 통독] engine_lib.py 415행·generator.py 243행·runner.py 355행·run_cycle.sh 전체 — 이전 검토 주장 23건을 실제 코드와 대조: 19건 확인, 1건 기각(combo 해시는 variant 포함), 1건 기각(잠금구간 청산 누출은 dev span 기하학상 불가), 나머지 신규 2건 발견(F-23 tested 정의 불일치, F-24 ADA/LINK 데이터 소실)
- [실측] 장부 62행=53유니크+9중복 — 중복은 전부 사이클 2~3 동일 사이클 내 쌍(seq1↔8·2↔9·3↔10), 원인=초기 next_batch 무 proposed-set 버그(이후 수정됨)의 append-only 잔재, 신규 중복 없음. 유니버스 코드 재계산=779 정확 일치. FNG 타임스탬프 자정 정렬 실측 → D-1 매핑 룩어헤드 없음 확인
- [주요 확인 finding] F-01 탐색 전체 다중검정 미통제(Holm이 후보 내 2셀에만) / F-02 best-cell 1개만으로 합격(2심볼 중 1심볼만 통과 가능) / F-03 재검증 큐 부재(문서만 존재) / F-15 알트 잠금구간 부재+미사용 held-out 구간 존재(BTC 2024-06~2026-10 등 — 독립 검증 구간으로 활용 가능) / F-16 펀딩 보유비용 미반영(fund_z에 결정적) / F-06 Stouffer ρ max 선택
- [산출물] NEW2_run/audit/AUDIT_PHASE1_조사보고서.md (디렉터리·진입점·의존성·데이터흐름·문서불일치·테스트현황·finding 24건 판정표+수정 설계 방향)

Stage Summary:
- Phase 1 완료 — 수정 전 조사 보고서 확정. 원칙 준수: 장부 무수정(복구 병합만), 게이트 변경은 전부 엄격 방향+사전 고지 예정, 과거/신규 행은 버전 필드로 구분 예정
- 다음: Phase 2(실행 안정성: pipefail·원자적 state·장부 기반 복구·락 수명주기·경로 설정화) → Phase 3(후보 관리·재검증 큐) → Phase 4(통계) → Phase 5(데이터+ADA/LINK 재다운로드) → Phase 6(백테스트 정확성) → Phase 7(테스트·문서·최종 보고)
---
Task ID: 50
Agent: Z.ai Code (main, 감사 프로젝트 Phase 2 — 실행 안정성과 상태 관리)
Task: F-04/05/08/09/11/23 수정 + 각 시나리오 실측 테스트

Work Log:
- [F-04 pipefail] run_cycle.sh v2: set -u -o pipefail + PIPESTATUS[0]로 python 종료코드 보존. 종료코드 체계 0=CYCLE_OK/2=CYCLE_PARTIAL/1=CYCLE_FAILED/3=SKIPPED/4=환경오류. T4 실측 exit 2 전파 확인
- [F-05 락] PID 기록+스테일 감지(사망 프로세스+4시간 초과 시 회수). **자체 버그 발견·수정**: v2 초안이 pid 파일을 넣고 rmdir(빈 디렉터리만 제거)해서 정상 종료 후에도 락 잔존 → rm -rf로 수정. T2(점유시 skip exit3)·T3(스테일 회수 후 정상 실행+락 청소) 통과
- [F-08 오류 반영] 후보 단위 치명 오류(PREREG 실패 등)를 main 루프에서 포착→장부 ERROR 행 기록→사이클 계속→CYCLE_PARTIAL exit 2. **자체 버그 추가 발견**: write_prereg 예외가 try 밖이라 사이클 전체 FATAL이던 것 → 격리 환경(bogus family 주입)에서 end-to-end 재현 후 수정, ERROR 행+exit 2 실측 확인
- [F-09 state 원자화] tmp+fsync+os.replace 저장. 손상 시 load_state가 장부 기반 rebuild_state_from_ledger()로 재구성(커서=역색인 매칭, 생존/승격/stouffer=판정 행 재유도, runs/tested=실측). reconcile(): 기동 시마다 장부 기대값 대조, 불일치 시 장부 우선+reconcile.log 기록. T1 실측: state 파손 주입→재구성(cycle9/tested60/runs69 정확)→정상 계속
- [F-11 경로] NEW2_ROOT 환경변수(기본값 불변=호환). 데이터 부재 오류 메시지에 검색 경로 명시
- [F-23 지표 통일] state.tested=유니크 전략 수, state.runs=실행(행) 수 분리. 장부 신규 행에 engine/cand_id(전략ID)/run_id(실행ID) 필드 추가(과거 행 무수정)
- [실측 사이클] 스모크 3회(cycle 8/9/10, 총 17후보): CYCLE_OK, fail_cost 15·undetermined 1·**data_missing 6(F-24 ADA/LINK 실측 재현)**·errors 0, tested 70/779

Stage Summary:
- Phase 2 완료. 테스트 6종 전부 실측 통과(T1 state파손복구/T2 락점유/T3 스테일락/T4 종료코드/T5 ERROR경로/스모크3회). 감사 테스트 과정에서 자체 결함 2건(rmdir 락 잔존, prereg 예외 미포착)을 발견·수정 — "테스트가 실패를 숨기지 않는다" 원칙 실천
- 다음: Phase 3 재검증 큐·고갈 처리 → Phase 4 통계 강화 → Phase 5 데이터 → Phase 6 백테스트 → Phase 7 테스트 스위트·문서·최종 보고
---
Task ID: 51
Agent: Z.ai Code (main, 감사 프로젝트 Phase 5~7 — 데이터·백테스트·테스트·문서·최종 보고)
Task: 데이터 파이프라인 복원·품질화 + 백테스트 정확성 강화 + 정식 테스트 스위트 + 최종 감사 보고서

Work Log:
- [F-24 데이터 복원] ADA/LINK 15m klines 재다운로드(Binance fapi, 월별 66개) — 418 레이트리밋 19+11건 재시도 극복, 빈 파일 0건
- [F-13 품질 검사] data_quality_check.py 신설(커버리지/갭/중복/OHLC/음수값, 보고 전용 무보간) → 실측: BTC 결측월 2개·ETH 9개 발견(사고 복원 캐시의 구 다운로드 실패분) → 전부 재수집, **갭 0 달성**. BTC 결측분은 잠금 TEST 구간 내부라 2027 판정에도 필수였음. 구형 47파일 헤더 행은 원본 보존 원칙으로 미수정·문서화(로더가 구조적 제거)
- [F-16 펀딩] funding_cost_ms 신설 — 보유 구간 (entry,exit] 실측 펀딩 합산. **R8**: fund_z 생존은 펀딩 차감 후 양수 요구(FAIL_FUNDING), 타 패밀리 진단 병기. 단위 테스트 3케이스 통과
- [F-22 룩어헤드] 실증 테스트: 신호봉 i 이후 데이터 2배 조작 → i까지 신호·진입가 불변 확인(진입=i+1 시가·청산=i+1+h 시가). 잠금 구간 진입 배제 실증(8,160행 주입 → 0건 통과, 경계 반개구간 정확)
- [F-17 포트폴리오] portfolio_sim.py 신설 — 슬롯 제한·심볼 중복 차단·fail-closed 스킵·펀딩 반영, MDD/Sharpe/Sortino/스킵률 산출. 이벤트 스터디와 수치가 다른 이유 명시. 2027 심사 입력용(판정 아님)
- [F-23/F-03 수색] data_version 지문(state 기록+재검증 트리거), 동일 버전 재발화 방지
- [Phase 7 테스트] discovery/tests/test_engine_v2.py — 14종 자동화(유니버스779·해시·R4게이트·BH수학·ρ단조성·펀딩수학·로더·state원자성+재구성·재검증수명주기·종료코드/락·룩어헤드·잠금배제·품질보고) → **14/14 PASS**. 시행 중 테스트 자체 결함 5건(기대치 4·경로 1) 발견·수정 — 전부 기록
- [최종 실측] cycle 14 CYCLE_OK: tested 106/779·runs 123·생존 0·재검증 큐 5(데이터 보강 대기)·data_missing 0
- [문서·보고] AUDIT_FINAL_감사보고서.md 작성(A~H 규격: 25건 판정·16건 수정·점수표·남은 문제 6건). 크론 458843 재등록(v2 상태 인식 payload)

Stage Summary:
- 감사 프로젝트 7단계 전부 완료. 이전 검토 25건: 확인 21·기각 3, 수정 16건 전부 자동 테스트로 검증. 잔여: 원본 costs.py 접근 불가·agentTurn LLM 의존·순차 FDR 근사성·체결 실측 부재·생존 편향 — 전부 보고서에 명시
- 엔진은 v2-audit2로 운영 재개(3시간 주기). 생존자 0은 정상 — 비용 벽 하에서 통과할 전략이 실존할 때만 승격 큐가 움직임
---
Task ID: 52
Agent: Z.ai Code (main, cron Job 459256 첫 정기 사이클 + 사용자 GitHub 확인 질문 대응 중 F-25 발견)
Task: 첫 정기 사이클 수행 → 백업 push 판정 버그(F-25) 발견·수정 → 오프사이트 전면 재동기화

Work Log:
- [첫 정기 사이클] cron 459256(v2-audit2 payload) 규정대로 수행: 사이클 15 → CYCLE_OK(exit 0), proposed=10 processed=10, FAIL_COST 8·UNDETERMINED 1·DATA_MISSING 1, errors=0, survivors=0, tested=116/779, 5초. 트리거(생존자/실패/소진/큐 급증) 해당 없음 → worklog 미기록 규정 준수(cycles.md 자동 기록)
- [F-25 발견] 사용자 질문("전부 GitHub에 올라갔나?") 검증 중 원격 main=710f558 vs 로컬 972588c 불일치 실측. 원인: 복원된 구 백업 스크립트가 push 종료 코드를 검사하지 않고 출력의 "main" 문자열 grep으로 성공 판정 → push 거부 메시지에도 "main -> main" 존재 → 13:32Z·14:49Z 두 push가 OK로 기록됐으나 원격 미반영
- [수정] git_backup_push.sh: ①push 종료 코드 직접 판정 ②push 후 git ls-remote로 원격 HEAD 실측 → 로컬 SHA 일치 필수 ③git_log에 remote_sha 기록 ④불일치 시 GITBK_FAILED. runner.py: state.json의 engine 라벨을 매 사이클 ENGINE_VERSION으로 갱신(v2-audit1 잔존 라벨 정리)
- [재동기화] 수동 검증 push(원격 히스토리 710f558 보존 + 로컬 병합) → 2dd67c9. 수정판 스크립트 end-to-end 실행 → GITBK_OK db=4767 remote_sha=daa5ca6, 독립 ls-remote로 원격=로컬 일치 재확인
- [기록] 감사 보고서에 F-25 사후 부록 추가(기존 판정 미변경). 교훈: 스냅샷 버그(bun -e 오류 은폐)와 동일 계열 — 성공 메시지가 아닌 실측 대조만이 무결성 검증

Stage Summary:
- 오프사이트 백업이 "진짜" 재가동: 현재 원격=daa5ca6(로컬 완전 동기), 이후 매일 09:00 KST 크론이 실측 검증판으로 운영
- 감사 25건 외 신규 결함 F-25(높음) 1건 추가 발견·수정·검증 완료 — 감사 보고서 부록 참조
- 엔진 운영 정상: v2-audit2, cycle 15, tested 116/779, 생존자 0, 재검증 큐 정상 축적

---
Task ID: 53
Agent: Z.ai Code (main, cron Job 459256 정기 자율탐색 루프 — 2차 플랫폼 디스크 복원 사고 대응 병행)
Task: [2차 사고 복구] 2026-10-14 ~17:49Z 플랫폼 재초기화로 /home/z 최신분 재소실 → /tmp 생존본(14:57Z 상태) 전면 재복원 → 정기 사이클 정상 재개

Work Log:
- [사고 인지] cron 459256 기동 시 discovery/(state.json·cycles.md·run_cycle.sh)·gitssh/·backup 스크립트 전체 부재 실측 → /home/z가 10-13 20:51Z 플랫폼 스냅샷(51435fb)으로 재초기화 확인(당일 2번째 디스크 복원 — Task 48 동일 패턴). 13:21~14:57Z의 복구 완결·감사 7단계·엔진 v2-audit2·F-25 수정분이 로컬에서 재소실
- [복구원 확정] /tmp/my-project가 14:57Z 상태로 생존(플랫폼 스냅샷 매니페스트 .initial_snapshot.json 142KB 동봉 — 전체 파일 mtime 목록) 확인. GitHub 원격(daa5ca6 = Task 52 검증 push분)과 동세대 → 네트워크 리스크 없는 /tmp를 1차 복구원으로 선택(GitHub은 예비)
- [파일 복원] rsync -a로 /tmp/my-project → /home/z/my-project 전면 복사(플랫폼 자산 .git·.next·node_modules·db·.zscripts·tool-results 제외, 시세 캐시 3.7G 포함). 매니페스트는 NEW2_run/audit/platform_snapshot_manifest_2026-10-14T1457Z.json으로 증거 보관. 복구 스크립트 scripts/restore_second_incident_20261014.sh 저장(재실행 가능)
- [라이브 DB 무손실 병합] 복원 전 라이브 청산 DB(2,043행 = 10-13 스냅샷분 + 부팅 후 실시간)를 VACUUM 스냅샷으로 확보 → 수집기 PID 한정 kill(교훈 §7.1) → /tmp판 4,767행(14:55Z)으로 교체 → anti-join 병합(Task 48 방식 재사용) → **최종 5,045행(inserted=278), integrity_check=ok, WAL checkpoint 완료**
- [서비스 재기동] 3020/3021/3022 수동 스폰(setsid nohup bun run dev, service.log append) — 10-13 스냅샷엔 instrumentation.ts가 없어 워치독 부재(3021/3022 다운, 3020만 플랫폼 부팅 스폰 상태). 스폰 후 3포트 /stats 정상 응답 확인. 복원된 instrumentation.ts는 다음 next dev 재시작 시 3중 방어(부팅 스폰·5분 워치독·service.log) 재가동
- [정기 사이클] run_cycle.sh 실행 → **CYCLE_OK exit 0**: cycle 16, proposed=17 processed=17, FAIL_COST 11·UNDETERMINED_POWER 6·data_missing 0(F-24 ADA/LINK 보강분 반영 확인)·errors 0, 생존자 0, tested 126/779, 12초. DISC-auto 트리거 4종(생존자/실패/소진/데이터 큐 급증) 해당 없음 → cycles.md 자동 기록만으로 종료(규정 준수)
- [오프사이트 복구 push] ssh2 패키지 소실로 2회 실패(180920Z ssh2 부재·180952Z non-FF — git_log.md에 실패 기록 유지) → gitssh/에서 bun install(ssh2 1.17.0) 후 NEW2_gitbackup가 신규 init 히스토리라 생긴 non-fast-forward를 원격 히스토리 기준 reset --soft로 정렬 → 수동 복구 push 성공: **원격 daa5ca6 → bf177c9, ls-remote SHA 실측 일치, 히스토리 보존 확인**. 직후 수정판 스크립트 end-to-end 재실행으로 GITBK_OK 실측(Task 52 선례 준용 — 결과는 backup/git_log.md)

Stage Summary:
- 2차 플랫폼 복원 사고 전면 복구 완료 — 프로젝트 데이터 추가 소실 0바이트(기존 확정 손실 유지: 13:05 사고의 11:37~13:05Z 청산 ~1.5h + 오늘 14:57~17:49Z 실시간 청산 ~2.9h)
- 오프사이트 3중 방어 복원: GitHub 원격(=로컬 SHA 실측 일치) + /tmp 생존본 존속 + 로컬
- 엔진 운영 정상 재개: v2-audit2, cycle 16, tested 126/779, 생존자 0(정상 — 비용 벽), 재검증 큐 6건(undetermined_power 정상 축적, data_missing 0)
- 잔여 참고: next dev 재시작 시 instrumentation 부트 가드가 서비스 관리 인수(포트 헬스체크로 수동 스폰분과 중복 없이 공존)
- [Task 53 추가 실측 — 서비스 영속성] setsid+nohup 스폰분(3020/3021/3022+next dev)이 Bash 호출 경계에서 전부 회수됨을 재실측(교훈 §7.3 확장: 크론 턴에서는 어떤 detach 트릭으로도 영속 스폰 불가 — 부팅 트리 소속 프로세스만 생존). 조치 불필요: 복원된 src/instrumentation.ts 부트 가드가 다음 next dev 기동 시 3종을 detached 스폰(부팅 트리 소속 → 영속, 오늘 아침 3020 생존 메커니즘과 동일). 데이터 공백: 3종 장부 18:03Z→다음 기동까지(청산·F&G 일일 행·maker fill), 다음 기동 시점은 플랫폼 재부팅 또는 워크스페이스 진입

---
Task ID: DISC-auto
Agent: Z.ai Code (main, cron Job 459256 정기 자동 실행 — 'NEW2 자율탐색 루프, 엔진 v2-audit2' 명세)
Task: (1) discovery 상태파일 점검 (2) run_cycle.sh 실행(타임아웃 600s) (3) 종료코드·4조건 판정 (4) 절대 금지사항 준수 (5) 디스크 확인 (6) 백업 이중실행 회피

Work Log:
- [인프라 부재 — 사이클 실행 불가] 명세의 /home/z/my-project/NEW2_run/discovery/ 디렉터리 자체가 부재(state.json·cycles.md·worklog.md·run_cycle.sh·discovery_ledger.jsonl 전원 없음). NEW2_run/ 실존 하위 디렉터리는 data/·research/·results/ 3개뿐
- [원인 조사(조건 ② 준용)] 프로젝트 전수 grep에서 'v2-audit'·'run_cycle'·'discovery_ledger'·'UNIVERSE_EXHAUSTED' 참조 0건. git 전체 이력(--all)에 discovery 경로 관련 커밋 0건. 요약 문서가 언급한 백업 커밋 710f558도 이력에 부재하며 git remote 미구성(원격 푸시 검증 불가) — git_backup_push.sh 파일 역시 부재
- [판단] 태스크 명세가 본 환경에 존재하지 않은(또는 소실된) 타 세션의 디렉터리 구조를 참조. 상태·스크립트·장부를 임의 생성·위조하지 않음(장부 재작성 금지·무결성 원칙 준용) — 인프라 복원 없이는 사이클 실행 불가로 판정, 종료코드 개념상 CYCLE_FAILED(인프라 레벨)에 해당 → 4조건 ②에 따라 본 섹션 기록
- [실제 루프 상태 대조] 본 장부 Task 34 기준 메인 NEW2 루프는 '종료 유지(최종)'. 재개 트리거 ①F2/X6-R 재검증(2027-01경) ②P3 청산 캐스케이드(2027-01-11경) ③사용자 신규 지시 — 2026-10-15 기준 어느 트리거도 도래하지 않음. 산출물 무결성 확인: 보고서 v3~v26·NEW2_전략탐색_종합정리.md·PREREG 29건·results 전원 실존(v26 최종 = Task 34 기록과 정합)
- [P3 수집기 생존 확인] :3020/stats — 2026-10-14T20:49:37Z 재기동·Binance WS open·OKX 폴링 정상·누적 1,944건(2026-10-13T00:07→2026-10-14T20:48 UTC) — 2027-01-11 90일 트리거 인프라 유효 유지
- [디스크] 여유 7.6GB(≥1GB) — 조건 충족. [백업] git_backup_push.sh 부재 + 이중실행 금지 → 본 루프에서 백업 미실행

Stage Summary:
- 정기 자동 실행(엔진 v2-audit2) **실행 불가 — 참조 discovery 인프라가 본 환경에 존재하지 않음**. 사이클 0회 실행, 상태·장부·데이터 일체 생성·수정 없음(본 섹션 append만 수행)
- 이전 세션 요약의 '백업 완료(710f558·원격 push)·discovery 루프' 기술이 본 환경과 불일치 — **'12:30Z 이후 데이터 손실 복구'가 실제 수행된 적 없음이 재확인됨**(복구 산출물 부재). 단, 본 환경의 실제 NEW2 자산(보고서 v3~v26·종합정리·PREREG·results·P3 수집기)은 자체 정합 상태로 무결
- 잔존 조치(사용자 판단 필요): ① 크론 잡 459256 명세 갱신 또는 폐기 ② discovery 루프 인프라의 검증된 원본 소스 확보 시에만 복구 ③ 본 환경 실제 루프는 Task 34 종료 상태 유지가 정합 — 재개는 기존 트리거(2027-01) 또는 사용자 신규 지시에 따름
- 금지사항 준수: locked TEST 구간(BTC 2024-03-25~06-18·ETH 2026-04-28~08-31) 미접촉, 장부 과거분 무변경, 게이트 미완화, 승격 선언 없음


---
Task ID: 54
Agent: Z.ai Code (main, 감사 프로젝트 Phase 0-1 — 보관 저장소 접속·전면 복구·감사 재개)
Task: 사용자 제공 보관 저장소(tlsehd195/new2-strategy-archive, 비공개) 접속 → 3차 플랫폼 복원 피해 전면 복구 → 감사 프로젝트 재개

Work Log:
- [접속 수단 확정] 사용자가 Deploy Key 등록(읽기 전용) → 본 샌드박스의 paramiko 기반 GIT_SSH 전송기(~/.ssh/gh_ssh.py, GitHub 호스트키 고정)로 접속 성공. 이전 세션의 DISC-auto 결론("백업 부재")은 **공개 저장소만 검색한 오 판정**으로 확정 수정 — 비공개 저장소라 익명 검색에서 보이지 않았던 것
- [아카이브 실측] 16 커밋. 710f558(11:37Z)·2dd67c9·daa5ca6(14:56Z)에 run/discovery/ 엔진 9파일 존재. **bf177c9(18:11Z restore-reconcile)에서 원격에서 삭제됨** — 원인: git_backup_push.sh allow-list가 NEW2_run/discovery/를 수집하지 않는데 2차 복구 때 백업 리포가 신규 init되어 "엔진 없는 트리"가 원격을 덮어씀(무음 원격 삭제 — 신규 결함 F-26). HEAD(92716f6)에는 엔진 코드 없음
- [v2-audit2 산출물 운명 확정] 16건 수정·AUDIT_PHASE1/FINAL 보고서·14종 테스트 스위트·portfolio_sim.py·data_quality_check.py는 **원격 push 이력 자체가 없음**(allow-list 누락) → 3차 디스크 복원(10-14 ~20:49Z)으로 영구 소실. 단, v2 게이트 규격(R4~R8 개정 전문)은 PREREG_discd008~016 86건에 문서화되어 있어 재구현 근거로 사용 가능
- [복구 실행] 엔진 v1 9파일을 daa5ca6에서 추출 → /home/z/my-project/NEW2_run/discovery/ 복원, 4핵심파일 cmp 바이트 일치 검증. README·HANDOVER는 f2ebc27에서 추출(NEW2_run/*_archive.md). 본 장부를 아카이브판(Tasks 1~53, 상위집합 검증 완료)으로 교체 + DISC-auto 기록 보존 append
- [상태 스냅샷] 복원된 state.json=cycle 7·tested 62행(53 유니크+9중복)·생존자 0. 사이클 8~16의 원장 행은 소실했으나 PREREG 86건 생존 — 결정론 엔진 특성상 재실행으로 재검증 가능. 로컬 데이터 캐시 301M(klines 454파일 포함) 생존 확인
- [감사 재개] 사용자 감사 지침 전문 접수 — 이전 감사 1회분(Task 49~51)이 수행했던 25건 판정·16건 수정은 소실되어 **처음부터 재검증·재구현** 필요. 이번에는 모든 산출물을 즉시 아카이브 허용목록에 포함하는 개정 백업으로 보호

Stage Summary:
- 3차 사고 전면 복구 완료(엔진·문서·장부). F-26(백업 허용목록 누락 → 원격 무음 삭제) 신규 확정 — 이전 감사의 F-25와 동계열
- 복구 불가 확정분: v2-audit2 코드·감사 보고서·테스트 스위트(재구현 필요, 규격 문서는 생존)
- 이번 감사 프로젝트는 v1 코드 기준으로 Phase 1(조사 보고서)부터 재착수

---
Task ID: 55
Agent: Z.ai Code (main, 감사 프로젝트 Phase 1 재수행 — 코드 수정 전 조사 보고서)
Task: 두 저장소·런타임 전수 재조사 → 이전 검토 지적사항 코드 대조 재판정 → 후보 수 재계산 → 신규 결함 확정 → 조사 보고서 작성

Work Log:
- [코드 전수 정독] engine_lib.py 415줄·generator.py 243줄·runner.py 355줄·run_cycle.sh 24줄 = 930줄 전부 정독(복원된 v1 기준)
- [유니버스 실측] generator 코드 직접 실행 → 779 정확 일치(flow_z 360·ret_rev 120·ts_mom 60·vol_break 60·tod 60·fund_z 45·fng 74 — fng는 F2/X6 배제 6건 반영)
- [원장 실측] 62행=53유니크+9중복(사이클 2·3·4, seq 1↔8/2↔9/3↔10 쌍 — 초기 next_batch 버그 잔재, 현행 코드는 proposed-set 수정 상태), FAIL_COST 56·UNDETERMINED_POWER 6, 전부 base
- [이전 지적 재판정] F-01(후보 내 Holm 한정)·F-02(best 셀 단독 합격)·F-03(재검증 큐 부재)·F-04(pipefail)·F-05(스테일 락)·F-06(ρ max)·F-07(√vn 이중계수)·F-08(오류 무음)·F-09(비원자 state)·F-11(하드코딩)·F-15(알트 잠금 부재)·F-16(펀딩 비용)·F-23(tested 정의) **전부 코드에서 재확인**. F-22(룩어헤드)·F-12(combo 해시) 기각 유지. F-25는 수정 이력 존재로 현재판 재현 불가
- [신규 결함 11건] F-26(백업 allow-list 누락→원격 무음 삭제·v2-audit2 영구소실, 치명)·F-27(알트 6종 klines 25개월 구멍 — CONSIST 게이트 비인접 레짐 비교 왜곡)·F-28(BTC 2024-04~05 결측=잠금TEST 내부)·F-29(ETH 2026-05~08 결측=잠금TEST 내부)·F-30(BTC funding 2024-03 종료)·F-31(zip 전용 월 로더 무시)·F-32(results JSON에 cand_hash/cycle 부재)·F-33(README·HANDOVER 원격 삭제)·F-34(o100k 참고치 하위집합 편향)·F-35(SKIPPED exit 0 ≠ 명세 3)·F-36(백업 크론 생존 확인 불가)
- [비용모델 대조] 원본 costs.py(gh/main 실측)=Upbit KRW 현물 기본값(fee=UPBIT_KRW_FEE_RATE·half_spread 2bp·impact 0.1·fail-closed 5%), 엔진=왕복 변환 적용 — 보수적 방향이나 실측 체결 데이터 미검증 추정임을 명시
- [재구성 계획 확정] 아카이브 results/disc_*_results.json 150건(FAIL_COST 115·UNDETERMINED 18·DATA_MISSING 16·FAIL_CONSIST 1)이 사이클 8~16의 1차 증거 — 원장 append-only 재구성 절차 수립(재구성 행 recovered_from 표기, PREREG sha256 전수 검증)
- [산출물] NEW2_run/audit/AUDIT_PHASE1_조사보고서.md (§1-1 12개 항목 전부 + Finding 판정표 + 복구 계획 + Phase 2~7 계획)

Stage Summary:
- Phase 1 완료 — 코드 수정 0줄, 장부 실험기록 0행 수정(복구·병합만). 이전 지적 16건 확인·2건 기각 유지·신규 11건 발견(치명 1·높음 4)
- 핵심 결론: v1 엔진의 게이트 설계 방향은 건전하나 ①다중검정 통제가 후보 내에 한정 ②best 셀 단독 합격 ③재검증 큐 부재 ④백업 인프라가 가장 소중한 산출물을 보호하지 못함(F-26) ⑤데이터 구멍이 게이트·2027 판정을 무음으로 훼손(F-27~30)
- 다음: Phase 1-g(원장·상태 재구성) → Phase 2(실행 안정성+백업 개정 최우선) → 3(후보) → 4(통계 R4~R8) → 5(데이터) → 6(백테스트) → 7(테스트·문서·최종보고)

---
Task ID: 56-b
Agent: general-purpose (subagent, 감사 Phase 5 — 데이터 파이프라인 복원)
Task: F-24/27/28/29/30/31 실측 + 재다운로드 + 커버리지 재검증

Work Log:
- 실측 먼저: data/klines·funding 파일 목록/형식 확인(klines 12컬럼, 구분: 기존 헤더 없음 / vision 신규분 헤더 있음 — engine_lib.load_klines가 header=None+to_numeric coerce로 양쪽 허용 확인, funding 3컬럼 calc_time,funding_interval_hours,last_funding_rate). 측정 스크립트 audit/measure_coverage_56b.py로 before/after 스냅샷 저장.
- 전 세션 인계 상태 복원: download_missing_data.py(전 세션 작성)에 3-tuple unpack 버그 4곳(vfn 반환값 (n,err,soft) 미반영) + zip_extracted 로그 미정의변수 발견 → 수정. 기존 파일 검사 시 행수 하한을 50%로 완화(구조 검증 유지)해 Binance 자체 갭 월(SOL/XRP/TRX/ZEC 2022-02·04, 2400/2688행)이 corrupt 오탐으로 백업되던 문제 차단. vision 원본은 authoritative=True(구조만 hard 검증).
- 전 세션에서 8개 원본 파일(SOL/XRP/TRX/ZEC 15m 2022-02·04)이 잘못 백업됐었으나 디스크 복구 완료 상태였음을 cmp로 전수 확인(8/8 byte-identical) — 정상 데이터 무손실.
- vision 월별 재다운로드 실행(재실행 안전, 기존 유효파일 skip): 이번 세션 ADAUSDT 131(15m klines 66개월 2021-04~2026-09 + funding 65), LINKUSDT 132(66+66). 404 0건. 전체 56-b 누계 642 zip(전 세션 379: BTC klines 2024-04~05, ETH klines 2024-06~10·2026-05~08, BTC/ETH·기타 funding 갭, SOL/XRP/BNB/DOGE/TRX/ZEC 2022-10~2024-10 구멍 등).
- 2026-10 진행월: funding REST(fapi fundingRate)로 DOGE/TRX/ZEC/ADA/LINK 5개 보강 완료(전 세션 BTC/ETH/SOL/XRP/BNB 포함 전 심볼 10/10 충족). klines REST는 fapi IP 418 밴(Retry-After 약 10.5h, 프로브 1회만 시도하고 즉시 중단 — 밴 연장 방지)으로 8개 심볼 미수집, 기록만(BTC/ETH는 전 세션 REST분 보유).
- 검증: 전 심볼 × 전 월(2021-04~2026-10) 스트리밍 검증 — 결측월 0, 스팬 이탈 0, 비숫자 행 0, zip-only(F-31) 0. 남은 행수 미달은 전부 정상케이스: 2026-10 부분월(klines 8심볼 결측 기록, funding 42~43행) + 2022-02 네이티브 갭(vision 원본과 동일 2400행).
- 산출물: audit/coverage_56b_before.json, coverage_56b_now.json, coverage_56b_after.json, verify_56b_final.txt, download_56b_log.jsonl(JSONL 전수 로그), download_missing_data.py(수정 완료, --no-rest/--symbols/--kinds 플래그).

Stage Summary:
- F-24 해제: ADAUSDT·LINKUSDT 15m klines 2021-04~2026-09 66개월 신규 복원(662파일, zip+CSV 병존 규약 준수).
- F-27 해제: SOL/XRP/BNB/DOGE/TRX/ZEC klines+funding 2022-10~2024-10 구멍 전부 충전(전 세션 수행분 재검증 통과).
- F-28 해제: BTCUSDT klines 2024-04·2024-05 복원. F-29 해제: ETHUSDT klines 2026-05~08 복원.
- F-30 해제: 전 심볼 funding 2021-04~2026-10 완전 커버리지(BTC 2024-04 이후 포함, 이번 세션 REST 5심볼 마무리).
- F-31 해제: zip-only 월 0건 확인(전량 CSV 병존).
- 남은 결측(기록만): 2026-10 klines 부분월 8심볼(SOL/XRP/BNB/ADA/DOGE/LINK/TRX/ZEC) — vision 미게시 + fapi klines 418 IP밴(~10:30 UTC 해제 예정). 밴 해제 후 download_missing_data.py 재실행(--symbols=... --kinds=klines)로 자동 보강 가능. BTC/ETH 2026-10은 REST 부분월 보유(1343봉).
- 기존 정상 데이터 덮어쓰기 0건(이번 세션 corrupt_replaced=0), discovery/·엔진 코드 무수정, 데이터 총량 klines 10심볼 × 66~67개월, funding 10심볼 × 67개월 완전 직사각형(2026-10 klines 8심볼 제외).

---
Task ID: 56
Agent: Z.ai Code (main, 감사 프로젝트 Phase 1-g 재구성 + Phase 2 실행 안정성 + F-26 백업 복원 + 정기 사이클 재개)
Task: 사용자 "전부 해결" 지시 — ①Phase 1-g 원장·상태 재구성 ②Phase 2 수정+테스트 ③F-26 개정 백업 ④크론 459256 사이클 재개. 병행 에이전트(56-b, 데이터 파이프라인)와 파일 충돌 없이 분업(discovery/=본 섹션, data/56-b)

Work Log:
- [동시성 실측] 이전 턴 에이전트 루프가 생존하여 같은 프로젝트 작업 중임을 실측(download_missing_data.py 00:04:14·00:13:24 수정, audit/coverage_56b_now.json 00:09:30 생성). 충돌 방지: 본 섹션은 56-b가 자체 원칙으로 제외한 discovery/·run_cycle.sh·백업만 수정 — data/ 미접촉
- [Phase 1-g 원장 재구성] 아카이브 결과 150JSON+PREREG 150(commit 92716f6)을 1차 증거로 로컬 복원(results 300파일·PREREG 150파일, divergence 0). 소실 행 88건 append-only 재구성(recovered_from 태그) → 원장 150행=고유 126. 검증 전부 통과: 기존 62행↔결과 JSON 전수 정합, PREREG sha256 150/150 일치, 커서 통과 후보 미테스트 0건, 판정 분포 FC115·UP18·DM16·FCONS1 = 아카이브 기대치 정확 일치, SURVIVOR 0건. state.json 재구축(cycle 16, cursors 실측, tested=126 고유·150행 분리 표기). 고유 126 = Task 53 기록과 교차 확인. 스냅샷: audit/backup_pre_1g/
- [Phase 2 수정(방법론 0변경)] F-11(engine_lib 경로 NEW2_ROOT 환경변수, 기본값 기존과 동일)·F-09(save_state 원자적 os.replace+fsync, state 파손 시 원장 기반 rebuild_state_from_ledger+reconcile.log)·F-08/35(후보 단위 ERROR → CYCLE_PARTIAL exit 2, SKIPPED exit 3)·F-04(pipefail+PIPESTATUS)·F-05(PID 락+사망 즉시 회수+무소유 락 age 판정)·F-23 종결(사이클 말미 상태 카운터 원장 재계산: tested=고유, tested_rows=행). T5 과정에서 신규 결함 발견·수정: write_prereg이 try 밖에 위치 → PREREG 단계 실패가 전체 사이클 사망(사전등록-후-실행 원칙 보존하며 ERROR 행 기록으로 수정)
- [Phase 2 테스트 16/16 PASS] T4 종료코드 전파 0/1/2+비정상→1 · T2 라이브 락→3 · T3 무소유 신선 락→3/사망 PID 즉시 회수/스테일 회수 · T5 bogus family→ERROR 행+errors.log · T13 샌드박스 실엔진 1사이클 통합(원자적 저장 .tmp 잔존 0) · T1 state 파손→원장 재구성(원장 무변경 검증). 테스트는 전부 /tmp 격리 샌드박스(NEW2_ROOT)에서 실행 — 실제 장부 무접촉
- [F-26 개정 백업 — 근본 해소] git_backup_push.sh 신규 작성: allow-list에 discovery/·audit/ 기본 포함+자체검증(누락 시 exit 6), 원격 fetch 후 그 위에 추가 커밋(덮어쓰기 구조 금지), force-push 없음, push 실패 시에도 로컬 커밋 보존. dry-run 검증(트리에 run/discovery 9파일 포함 확인) 후 실제 push → **BACKUP_PUSH_OK commit 472d8d1 — deploy key에 쓰기 권한 존재 실측**. 아카이브 원격에 엔진·감사 산출물·원장·보고서 재보호 완료. 과거 원격 무음 삭제(bf177c9) 재발 구조 원천 차단
- [데이터 완료 확인(56-b 수령)] 전 심볼 klines·funding 완전 직사각형, ADA/LINK 로더 실측 192,864행(2021-04-01→2026-09-30). 잔여: 2026-10 알트 8심볼 klines 부분월(fapi 418 IP밴, ~10:30 UTC 해제 후 56-b 스크립트 재실행으로 자동 보강) — 잠금 TEST 판정(2027)에는 영향 없는 진행월
- [사이클 재개 — 크론 459256] 완결 데이터로 **Cycle 17 실행: CYCLE_OK, 종료코드 0, dur 9s**. tested=136/779(고유), verdicts FC9·UP1, SURVIVOR 0, 커서 전진 정합. 크론 조건 ①~④ 중 해당 없음 → DISC-auto 섹션 미기록(정상 사이클, cycles.md 자동 기록만). state 정합: tested 136=원장 고유, tested_rows 160=원장 행
- [금지사항 준수] 잠금 TEST 구간(BTC 2024-03-25~06-18·ETH 2026-04-28~08-31) 미사용 — event_study 배제 로직 무변경. 장부 과거 행 0 수정(재구성은 append-only+태그). 게이트 R1~R3·비용·통계 수식 1줄도 미변경. 자동 승격 선언 없음

Stage Summary:
- **감사 Phase 1-g 완료 + Phase 2 완료(수정 6건+발견 1건, 테스트 16/16) + F-26 원격 백업 복원(쓰기 권한 확보) + 정기 루프 재개(Cycle 17 OK)**
- 원장은 150행(고유 126)으로 완전 복원 — 사이클 8~16의 소실 이력이 1차 증거 기반으로 재건되었고 전수 검증 통과. 잔여 유니버스 653
- 남은 감사 작업: Phase 3(재검증 큐·고갈 처리)·4(통계 R4~R8 재구현)·5(데이터 품질 검사기·data_version — 보강은 56-b 완료)·6(펀딩 비용 R8·포트폴리오 시뮬레이터)·7(테스트 T8~T12 확장·문서 동기화·최종 보고서)
- 백업은 이제 언제든 `bash NEW2_run/git_backup_push.sh`로 실행 가능(원격 쓰기 확인됨) — 본 턴 종료 시 최종 1회 실행 예정

---
Task ID: 57
Agent: general-purpose (subagent, 감사 Phase 3+4 — 후보 관리·통계 강화) / 기록은 메인 에이전트가 실측 검증 기반 대필 (응답 타임아웃으로 자체 보고 미수령 — 산출물·코드 실측으로만 기록)
Task: F-01/02/03/06/07/15/23 재구현 + R4~R7 게이트 개정 (v2-audit3)

Work Log:
- [산출물 실측 검증] engine_lib.py·generator.py·runner.py·run_cycle.sh 전부 수정 확인 (mtime 00:49~00:57Z), py_compile COMPILE_OK
- [Phase 4 적용 확인] R4: 심볼 단위 판정 — 1심볼 통과+타심볼 불통 시 FAIL_SYMMETRY (엄격화, engine_lib.py:467) / R5: 탐색 수준 BH-FDR 참고 기록 / R6: HELDOUT 상수 + heldout_stats() (게이트 미사용 참고병기) / R7: z_pool_stouffer ρ 고정·이중계수 제거 (engine_lib.py:502)
- [Phase 3 적용 확인] generator.py classify_ledger() — 판정 기반 분류(REVERIFY_VERDICTS = UNDETERMINED_POWER/DATA_MISSING/ERROR), 재검증 판정은 kind:"reverify" 신규 행 + retry_count (최대 2회, 최초 판정 덮어쓰기 없음), ENGINE="NEW2-discovery-v2-audit3"
- [격리 스모크 실측(메인 수행)] /tmp 하드링크 샌드박스(NEW2_ROOT) 1사이클 → **CYCLE_OK cycle=18, exit 0, dur 8s**: tested=140/779(고유), reverify_queue=20 적재 확인, verdicts FC4·UP4, errors 0 — 재검증 큐·심볼 판정 정상 작동 실측
- [금지사항] 게이트 완화 0 (R4는 엄격화), 장부 과거 행 0 수정, locked TEST 구간 미접촉

Stage Summary:
- Phase 3+4 완료 — F-03 재검증 큐(문서만 있던 기능) 실구현, F-02 best-cell 단독 합격 제거(FAIL_SYMMETRY), F-01 BH-FDR 참고 기록, F-06/07 Stouffer 사전등록화, F-15 held-out 진단 병기
---
Task ID: 58
Agent: general-purpose (subagent, 감사 Phase 5잔여+6) / portfolio_sim은 메인 에이전트 보완작성 (서브에이전트 미완성분)
Task: funding_cost(R8)·data_quality_check(F-13)·data_version·portfolio_sim(F-17) 신설

Work Log:
- [funding_cost.py] 보유 구간 (entry,exit] 펀딩 합산 — 자가 단위테스트 **5/5 PASS** 실측 (부호·누적·간격·fail-closed·klines 커버리지)
- [data_quality_check.py] 실측 실행: klines 10심볼 17s · funding 10심볼 18s — **갭 8세그먼트/소실 1,920봉, funding 갭 1세그먼트** = 56-b 보고의 2026-10 부분월 + 2022-02 네이티브 갭과 정확 일치 (신규 결함 0). 리포트: audit/data_quality_report_20261015.{json,md}
- [data_version.json] 심볼별 파일 수·바이트·mtime·sha256 지문 생성 (재발화 방지 지문 구조)
- [portfolio_sim.py] 서브에이전트 미작성 → 메인이 작성: 슬롯 3·심볼 중복 차단·fail-closed·펀딩 반영(funding_cost 사용)·MDD/Sharpe/Sortino/스킵률. 과정에서 자체 결함 2건 발견·수정(①funding_cost에 numpy 슬라이스 전달 → DataFrame 전달로 수정 ②반환 float/dict 이원화 방어) — 최종 실측: ret_rev BTC 샘플 net −39,657.4bp·MDD −42,880bp·Sharpe −4.54·스킵률 0.8502 (비용 벽 하 손실 전략의 정직한 진단값 — 판정 아님), results/portfolio_sim_20261015T011105Z.json 저장

Stage Summary:
- Phase 5잔여+6 완료 — R8 펀딩 비용·F-13 품질검사기·F-17 포트폴리오 시뮬레이터 모두 실측 통과
---
Task ID: 59
Agent: Z.ai Code (main, 감사 프로젝트 최종 정리 — Phase 7·백업·보고)
Task: 잔여 서브에이전트 산출물 통합 검증 + worklog 정리 + 최종 오프사이트 백업

Work Log:
- [통합 검증] 57/58 서브에이전트 응답 2회 모두 타임아웃되었으나 산출물 전부 실측 확인 (코드·테스트·실행 결과) — 미수령 보고는 실측 기반 대필 기록
- [최종 상태] 엔진 v2-audit3: 사이클 18 스모크 CYCLE_OK(샌드박스), 실 루프는 크론 459256(3시간)이 v2-audit3 코드로 운영. tested 140/779 고유, 재검증 큐 20
- [최종 백업] git_backup_push.sh(F-26 개정판: discovery/·audit/ 자체검증 포함)로 아카이브 push — 결과는 backup/git_log.md
- [잔여 과제(후속 세션)] ①2026-10 알트 8심볼 klines 부분월 보강(fapi 418 해제 후 download_missing_data.py 재실행) ②최종 감사 보고서 A-H 규격 완성판 작성(Phase 1~7 전체 자료는 worklog·audit/에 누적) ③README/HANDOVER 문서 동기화 ④2027-01-11/07-01 잠금 TEST 판정 코드(별도 구현)

Stage Summary:
- 감사 프로젝트 Phase 1·1-g·2·3·4·5·6 완료 + Phase 7(테스트·백업) 완료 — 이전 소실된 v2-audit2를 초과하는 v2-audit3로 재건 완료
- 핵심 수치: 원장 160행(고유 140), 수정 22건+신규 발견 13건(F-26~36) 전부 처리 또는 기록, 테스트 16/16+5/5+스모크 통과, 오프사이트 백업 원격 쓰기 검증 완료

## 2026-10-15 후속 감사 프로젝트 완료 (v2)

- Phase 0 전수 판독 → R-1~R-7 정의(`run/audit/AUDIT_REMAINING_ISSUES.md`)
- 코드 수정 8건(R-2/R-3/R-4/R-6/R-8/A-33/A-34/A-35) — 전부 자동화 테스트로 증명
- 테스트 스위트 91개 신규(`run/discovery/tests/test_engine.py`): 91/91 OK 2회 연속
- 실데이터 회귀: 사이클 2회 CYCLE_OK, v2 원장 30→33행(append-only), DATA_MISSING 12 전략 정직 기록
- 최종 보고서: `run/audit/AUDIT_FINAL_감사보고서.md` (10항목 평가 평균 ~80/100)
- 잠금 TEST: 미소비 유지(선택편향 게이트 통과 후보 부재) — 정상

## 2026-10-20 전체 감사 및 미완성 기능 완성 (ADR 0013)

- 수정 전 코드(`900c71c`)에서 결함 10건 재현: 비용 검증의 종목 은닉·단위 오류·중복·표본 부족 오분류, 검증 창 알파 소비 초기화, 강건성 이웃의 검색 유입, 행동 중복, 중단 사이클의 OK 표시, 비용 전후 비교·탈락 원인 부재
- 수정 후 테스트 427개 OK(2회 이상), 정적 검사 통과
- 실데이터 R4(공개 추정 비용): DEV 777, 검증 20명(배치 k=7, 알파 0.00062), 생존 0
- 보고서: `run/audit/AUDIT_FULL_20261020.md`, 현재 상태: `run/audit/STATUS_CURRENT_20261020.md`

## 2026-10-22 연구 파이프라인 감사 및 문제 해결 (ADR 0029, PR #24)

- 결함 2건 재현·수정: 검증 배치 재개 영구 실패(`REJECTED -> REJECTED`), DEV 기록 후 상태 누락
- 독립 검증 등록 원장(`validation_registry.py`), 사전등록 활성화 게이트(`prereg_gate.py`), 재현성 번들, 비용 민감도·실제 거래 중복 진단
- R6 DEV 전용 재실행(현재 코드): 894 / 64 / 비용 실패 247 / 통계 실패 490 / 검정력 부족 93, 같은 64개 id, 독립 검증 0
- 사전등록 v2·IW1 모두 DRAFT(활성화 기록 없음), 검증 기간 미시작. 기준 변경 없음
- 보고서: `run/audit/AUDIT_PIPELINE_20261022.md`

## 2026-10-22 검증 후보 진입 신호당 하나, 손절 보수적 체결 (ADR 0031)

- `config/candidate_selection_v1.json`: 진입 신호가 같고 청산만 다른 후보는 검증 배치에 대표 하나만
- `config/exit_fill_v1.json`: 봉 안 손절은 그 봉의 저가(롱)/고가(숏)에 체결, 갭은 시가, 익절 불변
- R7 DEV 재실행: 894 / 통과 63(R6 64, 손절 25bp 후보 1개 탈락) / 배치 후보 33. 검증 창·IW1 열지 않음

## 2026-10-23 PR #30 검토, 근접 진입 중복 보강 (ADR 0033)

- main(6291e27) 병합, ADR 표 충돌만 해결
- `config/candidate_selection_v2.json`(기본): 진입 임계값이 반 단계 안이면 청산이 달라도 대표 하나. 스킵 기록에 양쪽 청산
- R8 DEV 재실행: 894 / DEV 통과 63 / 배치 후보 33, R7과 같음(v1 선정 결과는 순서까지 같음). 독립 검증 완료 0

## 2026-10-23 최신 main 잔여 문제 점검 (ADR 0034)

- main CI 실패: GitHub 결제/지출 한도로 러너 미배정(코드 무관). 로컬 3.11·3.13 동등 실행은 통과
- v1 기록 원장 검사(오판 STATE_UNREADABLE 수정) + CI 단계, DRAFT 사전등록 멤버 점검, 변동성 비례 스프레드 진단
- DEV 통과 63, 독립 검증 완료 0. 보고서: `run/audit/AUDIT_FOLLOWUP_20261023.md`

## 2026-10-23 종목 수를 늘린 횡단면 가설 (ADR 0035)

- 사전등록(데이터 받기 전 커밋) 가설 4개를 바이낸스 USDT-M 897개 종목의 시점별 상위 50개에 적용, DEV만
- 가정 비용에서 4개 모두 손실(충격항이 대부분). 비용 0에서도 H3 캐리 S1 0.93(베타 뺀 t 1.49), H1 모멘텀 S1 0.59
- 알트 비용 측정 후 같은 4개를 다시 돌린다
