# ADR 0037 — 사전등록 v2 바구니를 유동성 9종목으로, 알트 비용 기준 v3 확정 (Q1-B)

- 상태: Accepted (비용 기준 설정 파일 1개 신규, v2 사전등록은 여전히 DRAFT)
- 날짜: 2026-10-24
- 사용자 결정: 동동, 2026-10-24 ("너의 추천으로 진행해") — [ADR 0036](0036-iw1-refreeze-and-prereg-v2-final-form-draft.md)의 추천안
- 범위: `run/discovery/config/cost_public_estimate_v3_alts.json`(신규),
  `run/audit/evidence_refreeze_v2_20261024/forward_window_v2_FINALFORM_DRAFT_r2.json`(신규 초안),
  `run/discovery/tests/test_refreeze_v2_20261024.py`(검사 추가). 평가 코드·통계 기준 변경 없음.
- 열지 않은 것: 사전등록 활성화, 검증·잠금·IW1·종이 구간. 수집 중인 알트 호가 자료는 아직 아무도 보지 않았다.

## 결정
1. **Q1 = B**: v2 바구니 = 유동성 9종목(BTC ETH BNB XRP SOL ADA DOGE LTC LINK). v3 초안의 종목 범위(V4)와 같다.
   15종목 기준은 수집하지 않는 6종목(TRX ZEC XMR NEAR XLM BCH) 때문에 측정 비용이 적용될 수 없었다.
   필요한 연 샤프(ρ 0.6, 80% 검정력)는 2.27 → 2.30으로 거의 같다.
2. **알트 비용 기준 v3 확정**: `cost_public_estimate_v3_alts.json`(sha256 `e11e1ccc…`). ADR 0036의 검토 초안과
   설명문만 다르고, `cost_public_estimate_v2_alts.json`과는 필수 종목(알트 7개)만 다르다. 추정기·모든 문턱값 동일.
   BTC/ETH는 필수가 아니다(엔진이 가장 비싼 종목의 값을 쓰므로 알트 최악값 이상으로 청구, 보수적).
   **수집 자료(10-27 종료)를 올리거나 보기 전에** 커밋했다. 테스트가 sha256을 고정한다.
3. **C1·C2 추천안 채택**: 후보 = 검증 후보 순서대로 진입 기준을 넘는 처음 5개, 진입 기준 = S1 연 샤프 ≥ 1.0.
4. v2 초안 r2: 위 결정 반영, 남은 질문은 Q2(등록 여부)뿐. 비용 프로필은 D2대로 10-31 전에 봉인된 추정이 쓸 만하면
   `public_estimate:<기록 파일>`, 아니면 `assumed_public_v1`. 검정·창·α·후보 규칙은 r1과 같다.

## 다음 (10-27 이후)
- 수집 자료가 올라오면 `python3 run/discovery/public_cost_estimate.py --criteria run/discovery/config/cost_public_estimate_v3_alts.json …`로
  추정하고 결과(쓸 만하든 아니든) 기록을 봉인한다.
- 쓸 만하면 `run/forward_v2_entry_bar_dev.py --cost-record <기록>`으로 유동성 9종목 진입 기준을 다시 센다(현재 가정 비용 0/33).
- 10-31: Q2(등록 여부)와 사용자 최종 확인. 5개 미만이면 규칙상 등록하지 않는다.

## 한계
- 측정 비용도 공개 호가 기반 추정이지 체결 검증(VERIFIED)이 아니다.
- 바구니를 줄여도 후보 자체의 약점(가정 비용 S1 최고 +0.17)은 그대로다.
