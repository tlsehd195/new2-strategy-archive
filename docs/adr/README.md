# Architecture Decision Records

탐색 엔진(`run/discovery/`)의 설계 결정 기록. 형식: 상태 · 맥락 · 결정 · 결과(장점/비용/남은 위험). 기록은 덮어쓰지 않고, 바뀌면 새 ADR로 대체한다.

| ADR | 제목 | 상태 |
|---|---|---|
| [0001](0001-discovery-engine-residual-defect-fixes.md) | 탐색 엔진 잔여 핵심 결함 수정 (세대 단위 FDR, MTM, 비용 분류, 잠금 TEST) | Accepted (PR #1, `66646cd`) |
| [0002](0002-fdr-family-notions-and-run-cycle-exit-codes.md) | FDR 가족 개념 분리, 과거 컨텍스트 PASS 처리, run_cycle 종료 코드 정책 | Accepted (PR #2 병합) |
| [0003](0003-core-defects-data-defaults-lookahead-ledger.md) | 핵심 결함 수정: 15분봉 무결성, 기본값 단일 해석, 시점 누출, 원장 복구 | Accepted (PR #3 병합) |
| [0004](0004-auto-evolution-staged-validation-and-paper-review.md) | 자동 전략 진화, 단계별 검증, 잠금 창 캠페인, 논문 적용·보류 | Accepted (PR #3 병합) |
| [0005](0005-project-wide-audit-cost-gate-evidence-integrity-handoff.md) | 전체 감사: 비용 검증 게이트, 증거 무결성, 페이퍼 상태, NEW2 인계 | Accepted (PR #3·#4 병합) |
| [0006](0006-cost-verification-forward-paper-data-manifest-ops-ci.md) | 잔여 작업 완료: 비용 검증 절차, 전진 페이퍼, 데이터 매니페스트, 체결 시뮬, 운영·CI | Accepted (PR #5, `733e4e7`) |
| [0007](0007-assumed-cost-profile.md) | 가정 비용(ASSUMED)·공개 시세 추정: 실측 체결 없이 연구 지속, 승격은 차단 | Accepted (PR #6·#7 병합, `c39578f`) |
| [0008](0008-residual-audit-cost-estimate-lookahead-forward-integrity.md) | 잔여 감사: 공개 시세 추정 v2, 비용 계산 시점 누출 제거, 체결 증거 규칙, 전진 페이퍼 무결성 | Accepted (PR #8 병합, `a2f63c0`) |
| [0009](0009-locked-test-requires-verified-cost.md) | 비용 모델이 검증되기 전에는 잠금 TEST를 열지 않는다 | Accepted (PR #8 병합, `a2f63c0`) |
| [0010](0010-legacy-lock-gates-cross-symbol-independence-mode.md) | legacy 잠금 TEST 비용 게이트, 다종목 잠금 창 독립성, 실행 모드 표시 | Accepted (PR #9 병합, `3685355`) |
| [0011](0011-public-market-estimate-first-run.md) | 공개 호가 80시간 수집분으로 첫 비용 추정 (ESTIMATED_FROM_PUBLIC_DATA) | Accepted |
| [0012](0012-evolve-real-data-run-public-estimate-cost.md) | 공개 호가 추정 비용으로 실데이터 evolve R3 (검증 진입 20, 생존 0) | Accepted |
| [0013](0013-audit-search-pool-cost-evidence-alpha-history.md) | 전체 감사: 검색 풀 분리, 행동 중복, 비용 전후 비교, 비용 검증 상태 세분, 창 단위 알파 소비 | Proposed |
| [0014](0014-archive-independence-fills-import-dedup-v2-search-family.md) | 아카이브 독립, 체결 가져오기, 행동 중복 fp-v2, 탐색 단위 다중검정(promo-v3), 상태·원장 검사 | Proposed |
| [0015](0015-dev-only-candidate-generation-r6.md) | DEV 단계 후보 추가 생성 R6 (894개 평가, DEV 통과 64) | Accepted |
| [0016](0016-independent-window-2020-market-cap-tranches.md) | 새 독립 검증 구간 IW1: 2020년 데이터, 시총 순 묶음, 사전등록과 1회 사용 | Proposed |
| [0017](0017-paper-review-ssrn3261943-power-precheck.md) | 업로드 논문 검토(SSRN 3261943): DSR 식 대조, IW1 검정력 계산, 사전점검 제안 | Proposed |
| [0018](0018-short-and-long-short-support-check.md) | 숏·롱숏 지원 점검, DEV 묶음 진단 도구 `bundle_dev.py` | Accepted |
| [0019](0019-strong-candidate-search-map-elites-dev.md) | 더 강한 후보 탐색(DEV 전용, IW1 측정값): 없음. MAP-Elites 부모 선택을 DEV 전용 옵션으로 추가하고 같은 예산·시드로 비교 | Accepted |
| [0020](0020-structural-return-sources-dev-only.md) | 구조적 수익원 탐색(DEV 전용, 22개 사전등록): 가정 비용 뒤 남는 것 없음 | Accepted |
| [0021](0021-dev-bundle-stable-small-profit.md) | 후보 묶음의 비용 후 안정 수익 DEV 점검(사전 고정 규칙, 앞/뒤 절반), IW1 v2 묶음 검정 초안 | Accepted |
| [0022](0022-false-negative-power-audit.md) | 거짓 음성 점검: 가상 엣지 검정력, 관문별 기여, 비용 가정, 숏 보류 후보, 검정력 개선 제안 | Accepted (측정만) |
| [0023](0023-forward-window-prereg-v2-draft.md) | 사전등록 v2 초안: 12개월 전진 창, 바구니 평균 검정 (효력 없음) | Proposed |
| [0024](0024-liquid-universe-dev-search.md) | 유동성 높은 9종목 DEV 재탐색 (가정 비용): 단일 후보 기준 미달, 묶음은 유망하나 베타 미확인 | Accepted (측정만) |
| [0025](0025-walkforward-evalue-prereg-v3-draft.md) | 매 블록 e-값 누적 검증: 합성 시뮬레이션(1종 오류·검정력·사전분포·널 생성법)과 사전등록 v3 초안 (효력 없음) | Proposed |
| [0026](0026-top5-bundle-regime-and-buy-and-hold.md) | 상위 5개 묶음 국면별(하락장) 성적과 단순 보유 비교(DEV, 가정 비용): 하락장 약 0, 수익은 상승장에 집중, 베타는 작음 | Accepted (측정만) |
| [0027](0027-paper-faithful-recheck-of-estimated-parts.md) | 추정·변형 항목을 논문 원문과 대조: alpha 지출=Tian–Ramdas와 같음, StepM bootstrap-t·Politis–White 블록·Dufour MMC·WS-R PrPl-EB 추가(기본값 그대로), 합성 오류율 재확인 | Accepted (측정·도구) |
| [0028](0028-short-top-candidates-and-long-short-regime-mix-dev.md) | 숏 전용 상위 후보 성적, 롱·숏 묶음 상관과 국면별 비중 전환(앞/뒤 절반) DEV 점검: 우연 최대치 못 넘고 조합 이득 없음 | Accepted (측정만) |
| [0029](0029-dev-to-independent-flow-prereg-gate-repro.md) | DEV→독립 검증 흐름 연결(등록 원장·재개), 사전등록 활성화 게이트, 재현성 번들, 비용 민감도·실제 거래 중복 진단, 재시작 결함 2건 수정 | Accepted |
| [0030](0030-adopt-prpl-eb-bootstrap-t-pbo005-as-drafts.md) | 사용자 결정으로 논문 그대로 채택(초안·새 버전, 효력 없음): e-값 v3 베팅 PrPl-EB, StepM bootstrap-t·PBO 0.05인 promotion_v4, IW1 v2 초안 | Accepted (초안) |
| [0031](0031-one-candidate-per-entry-signal-and-conservative-stop-fill.md) | 검증 배치에 진입 신호당 후보 하나(청산만 다른 변형 제외), 손절은 봉 극단가 체결(보수적): R6 재실행에서 DEV 통과 64→63, 배치 후보 33 | Accepted |
| [0032](0032-pdf-papers-gk1996-wsr-appendix-rw2003.md) | PDF로 받은 논문 3편 대조(Götze–Künsch 1996, WS-R v7 부록, Romano–Wolf 2003): GK 블록 StepM·WS-R 베팅 규칙 추가(기본값 그대로), ONS 인쇄 부호 오류·"PrPl-EB" 이름 정정 | Accepted (측정·도구) |
| [0033](0033-pr30-review-near-entry-duplicates.md) | PR #30 검토: 진입이 반 단계 안이고 청산만 다른 후보도 한 가설(selection v2), 스킵에 양쪽 청산 기록, R8에서 R7 결과 재현(DEV 통과 63, 배치 33, 독립 검증 0) | Accepted |
| [0034](0034-ci-billing-v1-ledger-check-draft-members-vol-spread.md) | main CI 실패 원인은 GitHub 결제(코드 무관), v1 기록 원장 검사·CI 단계 추가, DRAFT 사전등록도 멤버 동결 점검 보고, 변동성 비례 스프레드 진단 | Accepted (진단·검사) |
| [0035](0035-broad-universe-cross-sectional-dev.md) | 종목 수를 늘린 횡단면 가설 4개(모멘텀·반전·펀딩 캐리·저변동성, 시점별 상위 50개, DEV 전용): 가정 비용에서 전부 손실, 비용 전에도 우연 이상 없음, 측정 비용으로 재실행 예정 | Accepted (측정) |
| [0036](0036-iw1-refreeze-and-prereg-v2-final-form-draft.md) | IW1 멤버 재동결 초안(코드 해시 고정, 20개 모두 IW1 방식 DEV 손실 → 닫아 두기 추천), 사전등록 v2 최종 양식 초안·동결 도구, 리허설 진입 기준 통과 0/33, 알트 수집 종목이 v2 바구니를 못 덮음(Q1) | Accepted (초안·도구) |
| [0037](0037-prereg-v2-liquid9-basket-alt-cost-criteria-v3.md) | 사용자 결정 Q1-B: v2 바구니를 유동성 9종목으로, 알트 비용 기준 v3(알트 7개) 설정 파일을 수집 자료를 보기 전에 확정, v2 초안 r2(남은 질문 Q2) | Accepted |
| [0038](0038-measured-alt-cost-estimate-and-reruns.md) | 측정한 알트 호가(96시간)로 비용 추정(사용 가능, 반스프레드 7.3bp·충격계수 0.0015), 진입 기준 통과 1/33(5개 미만이라 등록 불가), 알트 4개 가설 재실행도 모두 NOT_PROMISING | Accepted (측정) |
| [0039](0039-v2-1031-options-and-freeze-date-origin.md) | 10-31 v2 판단 선택지 정리(보류 / 규칙 변경 / v3 e-값 전환)와 동결일 10-31의 출처(ADR 0023 D5), 기준 변경 없음 |
| [0040](0040-cross-section-v2-five-new-hypotheses-measured-cost.md) | 새 횡단면 가설 5개(크기·거래량 충격·MAX·테이커 흐름·합성, 28일 리밸런싱, 측정 알트 비용, DEV 전용) 사전등록 후 실행: 모두 NOT_PROMISING, 비용은 작아졌지만 비용 전 수익이 약함, 가장 나은 N4도 우연 최대치 미달 | Accepted (측정) |
| [0041](0041-volume-scaled-low-liquidity-cost-rerun.md) | 저유동 코인용 거래량 비례 비용 보정(측정 10종목 호가를 거래량에 외삽, 결과 보기 전 고정)으로 알트 4개 가설 DEV 재실행, 모두 NOT_PROMISING | Accepted (측정) |
| [0042](0042-v3-evalue-final-form-first-block-20261201.md) | v3 e-값 누적 검증 최종형 초안(첫 블록 2026-12-01, 측정 비용, 구성원 처음 5개 고정, 하루 끝 손절, 해시 사슬 장부), v2 미등록, v2 몫 알파 유지; 블록 도구와 DEV 리허설, 효력 없음 | Accepted (초안) |
| [0043](0043-hilo-line-volume-spike-dev.md) | 최근 고점·저점 라인 + 거래량 급증 전략(반전 16 + 돌파 거울 1, 일봉, 상위 50, 측정 알트 비용, DEV 전용) 사전등록 후 실행: 17개 모두 NOT_PROMISING, 반전은 비용 전에도 손실 | Accepted (측정) |
| [0044](0044-candidate-regime-scorecard-dev.md) | R9 후보 33개와 묶음 5개의 장세별(상승·횡보·하락, 하락 국면) 성적표, 측정 비용, DEV 전용: 횡보장이 가장 약하고 S1·S2 장세별 부호가 일관되지 않으며 6칸 모두 양수인 후보는 1개(탈락용 진단, 통과 증거 아님) | Accepted (측정) |
| [0045](0045-fast-short-horizon-cost-wall-dev.md) | 더 빠른 단기 전략(보유 15분~4시간) 20개 DEV: 거래 수는 쌓이지만 거래당 총수익(−11~+11bp)이 왕복 비용 26.6bp에 못 미침, 전부 탈락. 메이커 낙관 행(검증 불가)도 구간 간 부호 뒤집힘. 손익분기 적중률 표 | Accepted (측정) |
| [0046](0046-hilo-line-intraday-swing-breakout-dev.md) | 고점·저점 라인 + 거래량 급증을 15분·1시간봉, 롤링/스윙 고저점, 페이드/돌파 48개로 사전등록 후 DEV 실행: 모두 NOT_PROMISING, 비용 0에서도 우위 없음(최고 1.27 < 1.41), 15분봉 연 비용 부담 중앙값 232% | Accepted (측정) |
| [0047](0047-wide-universe-effective-names-dev.md) | 종목 수 확대(상위 200, 거래대금 하한 200만/일)와 유효 독립 종목 수 측정: 5가설×3유니버스 15건 모두 NOT_PROMISING, 이름 2.7배 늘어도 N_eff 3.2→2.7 |
| [0048](0048-synthetic-null-and-planted-edge-pipeline-check.md) | 합성 데이터(부호 뒤집기 널·엣지 심기)로 탐색 파이프라인 점검: 비용 0이면 우연 최댓값 공식이 맞고(2.36 대 2.31), 비용을 넣으면 약 1.6; 진입 기준 SR≥1.0은 우연에도 거의 항상 통과; 연 샤프 1~2 엣지는 규칙 지목이 안 됨 | Accepted (측정만) |
| [0049](0049-limit-order-entry-cost-estimate-and-dev-reevaluation.md) | 지정가(메이커) 진입 비용 추정(공개 호가+체결 전수, ESTIMATED): 편도 비용 약 30% 절감(보수 환산 왕복 26.6→약 18.5bp), 메이커 수수료 4bp면 효과 0; 빠른 단기 20개 전부 탈락, R9 진입 기준 통과 1→4개(중복 빼면 3)로 등록 규칙 5개에는 미달 |
| [0050](0050-unsupervised-regime-classifier-dev.md) | DEV(S1)로만 학습해 고정한 비지도 장세 분류기(k-means 3군집) 시험: 라벨은 사람 규칙보다 덜 흔들리나(지속 6일 대 2일) S2에서 급락 군집이 29일뿐이고 S1·S2 부호 일치는 무작위 대조와 구분 안 됨(비율 0.19), 사전등록 판정 NOT_USEFUL; 후보 33개는 강한 상승 군집에서만 양수(상승장 노출) | Accepted (측정만) |
| [0051](0051-market-residual-signals-dev.md) | 시장 요인을 뺀 잔차 신호 4개(모멘텀·반전·고유변동성·MAX)×U50/W200 사전등록 후 DEV 실행: 8건 모두 NOT_PROMISING, 잔차 신호와 원신호의 순위 상관 0.84~0.93이라 새 정보가 거의 없음 | Accepted (측정) |
| [0052](0052-okx-paper-tendencies-dev-test.md) | 사용자 본인 방법(15분봉 장대봉·고저선 근접·약한 거래량 증가 페이드, 14개)과 OKX 기록의 범위 위치 경향(12개)을 사전등록하고 DEV에서만 시험(BTC/ETH + 알트 15개, 측정 비용): 총손익 −8~+5bp 대 비용 약 26bp, 비용 0 샤프도 우연 기준(1.26) 아래, 26개 모두 NOT_PROMISING; 방향은 약하게 사용자 말과 맞으나(페이드 +2bp, 추종 −2bp) 비용의 1/12; OKX 기록은 가설 출처일 뿐 증거 아님 | Accepted (측정만) |
| [0053](0053-candidate-rebuttal-checklist-from-tradingagents.md) | TradingAgents 조사(주식 3종목·3개월·비용 언급 없음·LLM 오염 미통제 → 증거 아님, 신호 생성엔 안 씀)에서 리스크 심사 구조만 빌려, 후보 반박 체크리스트 10항목(비용·시도 수·중복·장세·쏠림·누출·사전등록·낙폭·저유동)을 기존 ADR 도구에 연결해 문서화 | Accepted (문서만) |
| [0054](0054-quant-competitions-alpha101-dev.md) | 해외 퀀트 대회(G-Research·Numerai Crypto·Quant League·Quantopian·WorldQuant) 공개 전략 조사: 코인 선물에 쓸 수 있는 라이브 검증·비용 반영 고수익 전략 없음(순위 상위는 짧은 기간 최댓값); 유일하게 공식이 공개된 101 Alphas 8개를 U50 주간 롱숏으로 사전등록 DEV 시험(실측 비용): 8개 모두 NOT_PROMISING, 비용 0 S1 최고 0.94 ≈ 우연 기준 0.91, S2에서 뒤집힘 | Accepted (측정만) |
| [0055](0055-dropped-candidates-criteria-rescreen-and-pipeline-power.md) | 탈락 후보 32행을 기준별로 재정리하고 기준을 풀어 보기: 완전한 16행에서 현행 0개, 풀면 3~4개 남으나 엣지 0일 때 우연 기대(2.4~4.0)와 같아 구분 불가; 합성 검정력: 진짜 샤프 1은 현행 판정 통과 24%, 0.5는 5%, 2는 83%; 풀어서 사전등록 DEV 시험할 가치 없음(새 정보 없음), 약한 엣지는 확인에 20년+ | Accepted (측정·정리만) |
| [0056](0056-maker-entry-cost-rescore-of-dev-candidates.md) | 지정가 진입 비용(ADR 0049 비율)으로 탈락 후보 25개 DEV 재계산(새 자료 없음, 비용 재환산): 보수/중립/낙관 모두 ADR 0040 조건 a~e 통과 0개, S1·S2 모두 양은 7~9개; H1·N4·A101은 알파 t<1, W200 잔차 MAX가 가장 가깝지만 t 1.81·S2 미달; 비용이 아니라 신호 크기가 벽 | Accepted (측정만) |
| [0057](0057-independent-pipeline-recheck.md) | 파이프라인 독립 재검증("계산이 틀려서 안 되는가?"): 원자료를 새로 받고 기존 계산 코드를 보지 않고 새로 짠 엔진으로 H1·N4·A101·W200 잔차 MAX 비용 0 샤프 재현(차이 0.01~0.13); 오라클·누설·부호 반전·무작위 200회·지연 ±1봉·문헌 모멘텀(횡단면 4/4, 시계열 BTC·ETH 양수) 모두 정상; 자료 품질 이상 없음; 결론을 바꾸는 오류 없음(첫 주 0일 포함 관례만, 영향 <2%) | Accepted (점검만) |
| [0059](0059-independent-evidence-sources-pre2020-spot.md) | 독립 증거 구간 늘리기: 자료별 독립 표본(코인·년) 계산 — 알트 방향성 수익은 독립 베팅 1.6개(종목을 늘려도 1.72에서 멈춤)라 시간만이 지렛대, 새 자료(IW1·현물 2017~19·전진 1년)를 다 더해도 검출 가능 샤프 2.5~2.8→1.2~1.3; 바이낸스 현물 2017-08~2019-12 15분봉 31종 확보(미개봉, 통계 미계산), 우선순위·IW1 개봉 위험 정리 | Accepted (측정·자료 확보만) |
| [0058](0058-okx-real-fill-cost-first-record.md) | OKX 소액 실거래 1건(BTC 롱, 4,491 USDT, 약 67시간)으로 왕복 비용 역산: 가격 변동 +137.1bp 대비 실현 차이 15.1bp = 거래 수수료 7.1bp(진입 메이커 2.0bp·청산 테이커 5.0bp) + 펀딩 8.0bp, 미끄러짐 잔차 0; 이 계정 메이커 2bp는 ADR 0049 가정(4bp)보다 낮으나 n=1(BTC)이라 추정 상태 변경 없음; 실거래 기록 CSV·템플릿·올리는 법 안내(종목별 200건, 체결 단위, 수수료·펀딩·메이커/테이커 분리) | Accepted (측정·기록만) |
| [0060](0060-strong-hypotheses-newlisting-short-and-basis-harvest.md) | 강한 가설 후보 조사(비용 0 총수익 ≥ 비용 3배): 신규 상장 숏 6개와 현물지수-무기한 베이시스 수확 2개 DEV 전용 사전등록 후 실행, 모두 NOT_PROMISING; 30일 숏은 건당 순 +2.5~10%로 3배 기준은 넘지만 월 군집 t 1.0~1.4, 이벤트 10~17%가 진입가의 2배 이상 폭등(청산 구간)이라 탈락, 베이시스는 연 5%/2%대로 S1 뒤 절반 음수·현금 수익 미달; 나머지 5개 후보는 자료 부재로 사전등록 초안만 | Accepted (측정) |
