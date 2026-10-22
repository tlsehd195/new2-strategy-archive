# ADR 0032 — PDF로 받은 논문(Götze–Künsch 1996, Waudby-Smith–Ramdas v7, Romano–Wolf 2003)과의 대조

- 상태: Accepted (측정·도구 추가. 게이트 기본값·사전등록 v2/v3 초안·검증/잠금 구간·IW1은 그대로)
- 날짜: 2026-10-22
- 범위: `run/discovery/stats_mc.py`(`gk_*` 함수와 `stepm(studentize="gk_block", blocks=...)` 추가, 기본값 그대로), `run/evalue_walkforward_sim.py`(베팅 규칙 함수 추가, 독스트링 정정), `run/pdf_check_sim.py`(신규), `run/discovery/tests/test_pdf_check_20261022.py`(신규), `run/audit/evidence_pdf_check_20261022/`(결과). 시장 데이터·검증·잠금 구간·IW1은 읽지 않았다.
- 선행: [ADR 0027](0027-paper-faithful-recheck-of-estimated-parts.md)이 "원문 미확보, 추측 구현 안 함"으로 남긴 항목, [ADR 0030](0030-adopt-prpl-eb-bootstrap-t-pbo005-as-drafts.md)
- 사용자 요청: 동동, 2026-10-22(논문 PDF를 직접 올림)
- 항목별 전체 보고: 프로젝트 파일 `papers/pdf_check_20261022.md`

## 올려 주신 PDF (첫 쪽 기준)
| 파일 | 논문 | 맞는가 |
|---|---|---|
| 2010.09686v7.pdf (70쪽) | Waudby-Smith & Ramdas, Estimating means of bounded random variables by betting, arXiv v7, 2022-08-25 | 예. 부록 B(B.2~B.8)와 E.5가 들어 있음 |
| Download.pdf (20쪽) | Götze & Künsch, Second-order correctness of the blockwise bootstrap for stationary observations, Ann. Statist. 24(5), 1996 | 예. 전문 |
| ssrn-563209.pdf (36쪽) | Romano & Wolf, Stepwise Multiple Testing as Formalized Data Snooping, 2003년 10월 워킹페이퍼 | 예(Econometrica 2005 판의 선행본) |
| 1210.6861v1.pdf (10쪽) | Gligorov & Williams, bonsai boosted decision tree 트리거 | 이번 대조와 무관. 반영 안 함 |

계속 미확인: WS-R **v1** 부록 C.4(v7의 C.4는 다른 내용), Romano–Wolf 2003의 다른 논문("natural" 분산 설명을 미룬 그것).

## 결과
**Götze–Künsch 1996**
1. ADR 0027이 추측하지 않은 "natural" 분산 추정량을 원문(식 (3)~(5))대로 구현했다. 이동 블록(시작점 {0..n−l} 균등, 길이 l), 재표본 평균 μ*ₙ로 중심화(Ȳₙ로 중심화하면 편향이 2차 항을 깬다), 같은 블록 안의 쌍만 쓰는 σ̂*²(식 (5)), 원자료는 지연창 추정량(식 (3), 가중치 직사각 또는 v(x)−1 ~ −cx²). 삼각 가중치는 원문이 금지한다. 상수(Parzen, l = n^(1/3) 또는 n^(1/4))는 우리 선택이다. 테스트에서 E*σ̂*² = σ*²(1 − 1/b)를 열거로 확인했다.
2. GK는 **이동·순환 블록** 논문이다. 정상 부트스트랩용 σ* 공식은 원문에 없다. 기존 `bootstrap_t`(정상 부트스트랩 + 같은 Hansen 분산)는 RW의 "같은 알고리즘을 부트스트랩 자료에 적용"과 일치하지만 원문이 정해 준 공식은 아니다.
3. 해석(원문 문장이 아닌 우리 추론): Hansen의 κ 가중은 지수 감쇠라 v(x)−1 ~ −x(선형)이다. GK가 2차 정확성의 조건으로 든 v(x)−1 ~ −cxʲ, j ≥ 2에 해당하지 않는다. 우리 게이트는 2차 정확성을 주장한 적이 없다.
4. 합성 FWER(`evidence_pdf_check_20261022/README.md` §1): GK 방식은 같은 블록 길이의 정상 부트스트랩 bootstrap-t보다 명목에 가깝지 않다(AR 0.6: GK parzen l=7 13.3%, bootstrap-t 7.0%, 현행 11.3%). 길이 15는 보수적이다. **게이트 변경 근거 없음.**

**Romano–Wolf 2003(SSRN 563209)**
- 알고리즘 4.1·4.2, 식 (11): σ̂*를 재표본마다 다시 계산한다. ADR 0027의 `bootstrap_t`와 구조가 같다(각주 13이 이유를 설명).
- 원문 시뮬레이션은 순환 블록 + GK natural 추정량을 썼다(원자료 σ̂는 prewhitened QS 커널). 그래서 `blocks="circular"` 옵션도 넣고 쟀다.
- 정상 부트스트랩의 σ̂*는 "같은 알고리즘 적용"뿐이고 공식이 없다.

**Waudby-Smith–Ramdas v7**
1. 부록 B의 규칙을 Z 단위 일방 검정으로 구현했다: GRAPA(B.2, 근 찾기), LBOW(B.4), ONS-m(B.5), 그리고 aGRAPA(B.3)·gKelly(B.6)는 기존 `wsr_grow`·`mixture`와 식이 같음을 확인했다. ConBo·SRP·hgKelly는 신뢰구간용이라 범위 밖이다. E.5(비 i.i.d.)는 시뮬레이션 설명뿐이고 가정은 조건부 평균 μ로 우리 귀무(조건부 평균 ≤ 0)와 같다.
2. **ONS-m의 인쇄된 알고리즘은 부호가 맞지 않는다**(42쪽 그림으로 확인). `z = y/(1 − yλ)`, `λ ← λ − …`는 log(1 − λy)를 올려 초과수익에 반대로 베팅한다. 인쇄 그대로의 검정력은 샤프 3·36블록 6.6%로 사실상 없다. 인용한 Cutkosky–Orabona의 동전 베팅 방향(log(1 + λy)의 기울기)으로 수정해 구현했고 인쇄본은 `as_printed=True`로만 남겼다. 어느 쪽이든 λ가 예측 가능한 [0,1]이면 유효성은 그대로다.
3. **이름 정정**: 논문의 "PrPl-EB"(정리 2, 식 (13))는 지수형 초마팅게일 ∏exp{λᵢ(Xᵢ−m) − vᵢψ_E(λᵢ)}이다. ADR 0027·0030이 "PrPl-EB"라 부른 것은 식 (15)/(26)의 λ로 만든 **베팅곱 ∏(1+λᵢZᵢ)**로, 논문에서는 정리 3의 헤지 자본 K⁺에 해당한다. 코드와 숫자(샤프 2·36블록 52%)는 그대로 맞고 이름만 틀렸다. 이름을 고쳤다: `log_e_hedged_prpl_bet`(옛 이름 `log_e_prpl_eb`는 별칭), v3 초안의 `bet_rule`·`V3` 문구, ADR 0027·0030의 정정 문단. 지수형 PrPl-EB를 그대로 구현해 재 보니 1종 오류는 더 낮지만(0.05%) 검정력은 훨씬 낮다(샤프 2·36블록 16.6%, 샤프 3 67.7%). 베팅곱이 여전히 검정한 규칙 중 최고다(샤프 3·36블록 93.7%).
4. 지수형의 유효성은 논문 정리 2가 아니라 우리 유도다: Z−μ ≥ −1(μ ≤ 0)이면 Fan 외 부등식이 성립하고 M_t(m)이 m에 대해 감소하므로 M_t(0) ≤ M_t(참 평균).

## 바꾸지 않은 것
- `stepm` 기본값은 `"fixed"`, `default_q`, `promotion_v3/v4`, IW1 v1/v2 초안은 그대로다. v3 사전등록 초안(`walkforward_evalue_v3_DRAFT.json`)은 `bet_rule`·`V3`·`prior_from_spent_windows`의 **문구만** 고쳤다(coordinator 지시). 수식·숫자·키 이름·결정 내용은 그대로이고 초안은 여전히 효력이 없다.

## 한계
- 모두 합성 과정이다. 실제 암호화폐 손익에서의 오류율은 확인 못 했다.
- GK 시뮬레이션은 600회(순환 800회), 표준오차 약 0.9%p라 1~2%p 차이는 구분하지 못한다.
- ONS의 Z 단위 이식(A₀ = 1, λ₁ = 1을 c로 절단)과 aGRAPA의 정규화(Z 단위 사전값 (0, s₀²))는 논문 식의 이식이다.
