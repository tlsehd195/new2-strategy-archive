# ADR 0027 — 추정·변형 항목을 논문 원문과 대조해 구현 (StepM·블록 길이·alpha 지출·Dufour MC·e-값 베팅)

- 상태: Accepted (측정·도구 추가. 게이트 기본값과 기준은 바꾸지 않음. v2/v3 초안은 여전히 효력 없음)
- 날짜: 2026-10-22
- 범위: `run/discovery/stats_mc.py`(함수·옵션 추가, 독스트링 정정, 기본값 그대로), `run/discovery/tests/test_paper_faithful_20261022.py`(신규), `run/evalue_walkforward_sim.py`(베팅 변형 3종, `mmc` 파트), `run/test_evalue_walkforward.py`, `run/stepm_block_sim.py`(신규), `run/test_stepm_block_sim.py`(신규), `run/audit/evidence_paper_faithful_20261022/`(결과). 시장 데이터·검증·잠금 구간·IW1은 읽지 않았다.
- 선행: [ADR 0005](0005-project-wide-audit-cost-gate-evidence-integrity-handoff.md)(StepM 식별), [ADR 0025](0025-walkforward-evalue-prereg-v3-draft.md)(e-값 v3 초안). 항목 목록: 프로젝트 파일 `papers/estimated_parts_only_20261022.md`
- 사용자 요청: 동동, 2026-10-22 ("그 논문들 찾아서 직접 구현해")
- 항목별 전체 보고: 프로젝트 파일 `papers/paper_faithful_implementation_20261022.md`

## 원문 확보
- 업로드 전문(이미 프로젝트에 있음): Romano–Shaikh–Wolf 2008, Hansen–Lunde–Nason 2011(Econometrica), Hsu–Hsu–Kuan 2010, Bailey 외 PBO, White 2000.
- 웹에서 나눠 읽은 원문(약 39k자씩 offset으로 끝까지): Romano–Wolf 2005 워킹페이퍼 전체, Dufour 2006 전체, Politis–White 2004, Patton–Politis–White 2009 정정, Tian–Ramdas 2021(앞 39k자), Wang–Ramdas 2310.03722(3장 포함 앞부분), Arnold 외 2404.18678(3.4·5.1.2절 포함), Waudby-Smith–Ramdas 2010.09686 v1·v3·v7 본문.
- **원문 미확보**: Waudby-Smith–Ramdas 부록 B(aGRAPA·ONS·dKelly), E.5(비 i.i.d.), v1 부록 C.4. Götze–Künsch 1996(블록 부트스트랩 "natural" 분산), Romano–Wolf 2003/2006. 이 부분은 추측으로 구현하지 않았다.

## 결정
1. **기본값은 그대로 둔다.** `default_q`, `stepm`, `spending_alpha`는 IW1 사전등록과 검증 배치가 쓰는 절차다. 바꾸려면 새 사전등록이 필요하다. 논문 그대로의 대안은 새 함수로만 추가했다.
2. 추가: `stepm(..., studentize="bootstrap_t")`(RW2005 식 (11): σ*를 재표본마다 같은 분산식으로 다시 계산, 기본값은 "fixed"), `ppw_block_length`/`ppw_q`(Politis–White 2004 식 (9) + PPW 2009 정정 D_SB = 2ĝ(0)², m̂은 각주 c 그대로), `mc_pvalue`/`mmc_pvalue`(Dufour 식 (2.16)·(2.35), 명제 4.1), `fallback_alpha`(Tian–Ramdas 식 (5), **게이트에 연결 안 함**).
3. 독스트링 정정: alpha 지출은 Tian–Ramdas의 Alpha-Spending과 같다(γ = 6/(π²k²)까지 같음). StepM의 고정 σ는 RW2005 가정 4.1·RSW2008 가정 4.2를 만족하고 HHK2010 각주 6과 같은 방식이며, 논문 알고리즘(σ*를 재표본마다 다시 계산)과는 다르다. e-값 plug-in은 WS-R의 GROW/aSOS 식이고 정규화 상수가 다르다.
4. 시뮬레이션 도구에 논문 그대로의 변형을 넣어 비교했다: WS-R PrPl-EB(식 15), WS-R 정규화의 GROW, Arnold 외 기본 고정 베팅 λ=(2c)⁻¹, Dufour MMC, RW2005 식 (11)의 bootstrap-t StepM, RW2005 알고리즘 6.1 블록 선택.

## 결과 (합성 전용, `run/audit/evidence_paper_faithful_20261022/README.md`)
- **e-값 1종 오류**: 새 베팅 3종 모두 7개 귀무 과정·120블록에서 최대 0.55%(PrPl-EB), 목표 3.04% 이하.
- **e-값 검정력**: WS-R PrPl-EB가 가장 높다. v3 α 0.0091에서 GARCH t4, 연 샤프 2의 36블록 기각 37.6%(현행 plug-in) → 52.0%, 샤프 3은 87.5% → 94.0%. Arnold 고정 1/4는 매우 약하다(샤프 3, 36블록 26%).
- **Dufour MMC**(GARCH-t 귀무 족, 10점 격자): 5% 수준에서 크기 1.7~2.2%(족 안·밖 모두), AR 0.1 예측성 검출 13%. 정상 부트스트랩이 과보수인 원인: 재표본이 관측 계열에서 만들어져 S₀와 교환가능하지 않다(S₀와 재표본 중앙값의 상관 0.56~0.75, 재표본 중앙값이 평균적으로 S₀보다 큼).
- **StepM FWER**(전략 10개, 365일, 800회, α 5%): 현행 게이트(고정 σ, b≈7.1)는 iid t4 5.25%, GARCH 상관 6.0%, AR 0.2 5.6%, AR 0.6 11.4%. 논문 그대로의 bootstrap-t는 4.1%, 5.5%, 4.75%, 7.1%로 모든 과정에서 명목에 가장 가깝다. PPW 자동 블록은 개선 없음(5.1~11.5%). 블록 15·30은 과대 기각. RW 알고리즘 6.1은 자료에 따라 b = 1~15를 고른다.

## 정정 (2026-10-22, [ADR 0032](0032-pdf-papers-gk1996-wsr-appendix-rw2003.md))
이 ADR이 "WS-R PrPl-EB"라 부른 e-값 규칙은 식 (15)/(26)의 λ로 만든 베팅곱(논문의 정리 3 헤지 K⁺)이다. 논문의 PrPl-EB(정리 2, 식 (13))는 지수형 초마팅게일로 다른 규칙이다. 계산과 숫자는 그대로다. 또 "Götze–Künsch 원문 미확보" 항목은 ADR 0032에서 확인했다.

## 사용자 결정이 필요한 것
- v3 베팅 규칙(V3, 열려 있음): 권장을 현행 plug-in에서 **WS-R PrPl-EB**로 바꾼다(논문 식 그대로, 사전분포 없음, 같은 1종 오류에서 검정력 높음). 초안의 `bet_rule`은 아직 바꾸지 않았다.
- StepM 학생화: 검증 게이트를 bootstrap-t로 바꾸면 FWER가 명목에 더 가깝다(엄격해지는 쪽). IW1은 아직 열지 않았으므로 바꾸려면 IW1 새 사전등록이 필요하다. 계산은 B×m배 느려진다(365일·20개·B=1000에서 수 분).
- PBO 기준: 논문은 "PBO > 0.05면 기각이 관례"라고 쓴다. 우리는 0.5다. 0.05로 바꾸는 것은 기준을 엄격하게 하는 쪽이지만 승격 게이트가 바뀌므로 사용자 결정으로 둔다.

## 한계
- 모두 합성 과정이다. 실제 암호화폐 블록 손익 분포에서의 오류율은 여전히 확인 못 했다.
- MMC는 격자 위의 최댓값이다. 논문은 근사 최대화가 수준을 지키는지 다루지 않는다. 귀무 족 밖의 과정(국면, 비대칭 꼬리)에서는 정확성이 보장되지 않는다(측정값은 수준 이하였다).
- 정상 부트스트랩용 σ* 공식은 원문에 없다. bootstrap-t 변형은 RW2005 4.1절의 "같은 알고리즘을 부트스트랩 자료에 적용"을 따랐다.
