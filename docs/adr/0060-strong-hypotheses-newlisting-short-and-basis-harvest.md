# ADR 0060 — 강한 가설 후보 조사: 신규 상장 숏과 현물지수-무기한 베이시스 수확 (DEV 전용)

- 상태: Accepted (측정)
- 날짜: 2026-11-02
- 사용자 요청: 동동, 2026-11-02 "1 2 진행" (비용 0 총수익이 왕복 비용의 3배 이상으로 기대되는 강한 가설 찾기)
- 범위: 사전등록 `run/audit/evidence_strong_hypotheses_20261102/prereg_newlisting_short_v1.json`(커밋 6a088b5, 신규 상장 데이터 확인 전), `prereg_basis_harvest_v1.json`(커밋 69400af, 지수 일봉 내려받기 전), 도구 `run/newlisting_short_dev.py`·`run/basis_harvest_dev.py`, 결과 JSON·이벤트 CSV, 후보 순위 보고서 `STRONG_HYPOTHESES_20261102.md`.
- 열지 않은 것: 검증·잠금·IW1·종이 구간, 2020, 2023-09-19~2024-06-18, 2025-10-21 이후, S2 BTC(BTCUSDT는 아예 사용 안 함). 사전등록 활성화 없음. 기준·엔진·비용 모델 변경 없음.

## 맥락
기존 ADR은 거래대금 상위 50 유니버스(60봉 이상 이력)만 봤기 때문에 상장 60일 미만 종목은 한 번도 거래 대상이 아니었다. 또 ADR 0020 CC1(펀딩 수확)은 베이시스 손익을 0으로 둔 상한 추정이었다. 이 둘이 "비용 대비 총수익이 클 구조"를 가진 후보라 순위 1·2로 시험했다. 나머지 후보(분기물 베이시스, 청산 연쇄, 언락, 지표, 상폐 공시)는 자료 부재나 표본 부족으로 시험하지 않고 사전등록 초안만 남겼다.

## 결과
- **H_LIST** (6개 설정, 이벤트 S1 약 135·S2 약 290): 6개 모두 NOT_PROMISING. 30일 보유 설정(L3, L6)은 건당 순 S1 +9~10%, S2 +2.5~5.3%, 적중률 70~78%, 비용 대비 총수익 4~24배로 3배 기준은 넘었으나, 월 군집 t 1.0~1.4(기준 2.6), S1 앞 절반 +0.8~2.6% 대 뒤 절반 +17%, 진입가의 두 배 이상으로 오르는 이벤트 10~17.5%(기준 5% 이하)로 탈락. 7·14일 보유는 S2에서 비용 0도 음수/0 근처. 가정 비용에서는 S2 30일 설정도 음수.
- **H_BASIS** (2개 설정): 둘 다 NOT_PROMISING. B1 상시 비용 후 연 5.3%(S1)/1.9%(S2), 샤프 1.29/4.44, 최대 낙폭 4%/0.5%, 비용 대비 약 10배. 그러나 S1 뒤 절반 −3.5%와 S2 수익이 가정 현금 수익(연 4%)에 못 미쳐 기준 b 실패. B2 게이트는 교체 비용 때문에 비용 대비 3.7/1.3배.
- 시도 수 +8 (H_LIST 6, H_BASIS 2).

## 결정
- 승격 후보 없음. 신규 상장 숏은 "중앙값은 크게 이기지만 평균이 소수의 폭등에 좌우됨"이라 위험 한정형 규칙이 필요하며, 그 규칙은 결과를 본 뒤 만든 것이므로 DEV 증거로 못 쓰고 전향 전용 초안(보고서 D1)으로만 남긴다. 활성화는 사용자 최종 확인 필요.
- 현물지수 베이시스는 "실재하지만 작고 시기 의존적인 위험 프리미엄"으로 ADR 0020 결론을 확인.

## 한계
- 지수는 실제 현물 체결가가 아니며 현물 다리 비용 15bp는 가정. 상장 직후 호가는 측정하지 않았고 비용은 일 거래대금/96 근사(ADR 0041 보정식). 일봉 시가 체결 가정. 상장일은 S3 월 파일 이름의 첫 달로 정하고 그 달의 첫 일봉을 D0로 둠. 2021-01 이전 상장은 이벤트에서 제외. 문헌 수치는 기억 기반이라 미확인.
- 이 도구는 numpy·pandas가 필요해 run/discovery 밖에 있고 CI에서 돌지 않는다.

## 재현
```
python3 run/broad_universe_fetch.py --out <dir> --step daily
python3 run/newlisting_short_dev.py --data-dir <dir> --step first_month
python3 run/newlisting_short_dev.py --data-dir <dir> --step list_event_syms
python3 run/broad_universe_fetch.py --out <dir> --step funding --symbols <event+basis symbols json>
python3 run/basis_harvest_dev.py --data-dir <dir> --step fetch_index
python3 run/newlisting_short_dev.py --data-dir <dir> --step run
python3 run/basis_harvest_dev.py --data-dir <dir> --step run
```
