# ADR 0054 — 해외 퀀트 대회 공개 전략 조사와 101 Alphas 8개 DEV 시험

- 상태: Accepted (측정만, 기준 변경 없음)
- 날짜: 2026-11-01
- 범위: 조사 보고 `run/audit/evidence_alpha101_20261101/quant_competitions_survey.md`, 사전등록 `run/audit/evidence_alpha101_20261101/prereg_alpha101_v1.json`(자료를 받기 전에 커밋 `71d764c`), 새 도구 `run/alpha101_dev.py`(ADR 0040 엔진 `cross_section_v2_dev.run_segment`를 그대로 쓰고 일봉 로더·신호·보유일·가설 목록만 교체), 테스트 `run/test_alpha101.py`(6개), 결과 `alpha101_dev.json`, 시도 기록 `run/audit/dev_search_trials.jsonl` 1줄(8건). 엔진·통계 기준·비용 프로필·사전등록 v2/v3 변경 없음.
- 사용자 요청: 동동, 2026-11-01 ("해외 퀀트 대회 있지 않나? 거기서 자신의 전략 공개하고 수익률이 높은걸로 찾아서 적용해봐") → 조사 후 결정 카드에서 "101 Alphas 일부 DEV 시험" 선택
- 열지 않은 것: 사전등록 활성화, 검증·잠금·IW1·종이 구간. 읽은 시장 자료는 DEV 구간뿐.

## 조사 결론 (요약)
- 코인 선물에 바로 쓸 수 있고, 라이브로 검증됐고, 비용 반영 수익률이 공개된 대회 전략은 찾지 못함.
- G-Research(Kaggle): 해법 공개, 최종 순위는 3개월 라이브지만 지표가 상관계수이고 같은 시간대는 ADR 0045에서 비용 벽 확인. Numerai Crypto: 개인 모델 비공개, 평균 신호만, 과거 없음. Quant League: 약 3개월 라이브 종이거래 Sharpe(2025 Q3 1위 3.93)는 많은 팀 중 최댓값이라 운 포함, 대부분 주식. Quantopian 888개 연구: 백테스트 Sharpe가 실전을 설명하는 R² 0.01~0.02.
- 공식이 완전히 공개돼 사전등록할 수 있는 유일한 후보가 WorldQuant "101 Formulaic Alphas"(Kakushadze 2016) → 8개만 시험.

## 무엇을 고정했나 (결과를 보기 전)
- 가격·거래량·VWAP만 쓰고 창 10일 이하인 8개: #2, #6, #12, #33, #41, #53, #54, #101. 가격 수준에 좌우되는 #12, #41, #101은 비율로 바꿈(사전등록에 명시한 이탈).
- 유니버스·비용·판정은 ADR 0040과 같음: 그 시점 거래대금 상위 50, 상위 10 롱·하위 10 숏(논문 관례: 알파 값이 큰 쪽이 롱), 실측 알트 비용 PE-ed757f47, 판정 5조건(우연 최댓값 초과, 알파 t ≥ 2, S1 앞·뒤 반 양수, S2 > 0.5, 비용×2에서 S1 > 0).
- 논문은 매일 리밸런싱이지만 비용 때문에 매주 월요일 리밸런싱, 7일 보유로 고정.

## 결과 (연 샤프, 실측 비용; 우연 최댓값 N=8 0.91, 누적 11,920건 2.44)

| 알파 | S1 | S1 비용0 | S1 앞/뒤 반 | S2 | 판정 |
|---|---|---|---|---|---|
| A002 | −0.87 | −0.37 | −1.15 / −0.49 | −0.38 | NOT_PROMISING |
| A006 | 0.42 | 0.82 | 0.91 / −0.31 | 0.20 | NOT_PROMISING |
| A012 | −0.60 | −0.18 | −1.14 / 0.17 | 1.61 | NOT_PROMISING |
| A033 | −0.69 | −0.28 | −0.74 / −0.63 | −1.88 | NOT_PROMISING |
| A041 | −0.16 | 0.28 | −0.20 / −0.11 | −1.80 | NOT_PROMISING |
| A053 | 0.47 | 0.94 | 1.27 / −0.70 | −1.95 | NOT_PROMISING |
| A054 | 0.33 | 0.76 | 0.47 / 0.11 | −0.68 | NOT_PROMISING |
| A101 | 0.19 | 0.65 | 0.29 / 0.05 | 1.24 | NOT_PROMISING |

- 8개 모두 NOT_PROMISING. 비용 0으로도 S1 최고 0.94(A053)로 우연 기준 0.91과 비슷하고, 그 A053은 S2에서 −1.68(비용 0)로 뒤집힘.
- 주당 회전 약 1.6(롱숏 합), S1 동안 비용이 자본의 약 29%(연 약 11%). 비용 0 샤프와 실측 샤프의 차이가 0.4~0.5.
- A012·A101은 S2에서만 양수(1.61, 1.24)이고 S1은 음수 또는 약함. S2를 보고 고르면 선택 편향이므로 후보로 올리지 않음(S1이 판단 구간).
- 결론: 주식에서 공개된 단기 가격·거래량 알파는 코인 상위 50 선물에서 비용을 넘는 안정적 엣지를 보이지 않음. 다른 공식·보유일·유니버스로 다시 시험하면 새 시도(새 사전등록·ADR).

## 재현
```
python3 run/broad_universe_fetch.py --out <dir> --step daily
python3 run/broad_universe_dev.py --data-dir <dir> --write-universe
python3 run/broad_universe_fetch.py --out <dir> --step funding --symbols <dir>/ever_top50.json
python3 run/broad_universe_fetch.py --out <dir> --step bar2345 --symbols <dir>/ever_top50.json   # IncompleteRead는 다시 실행
python3 run/alpha101_dev.py --data-dir <dir>   # 약 20초
```
