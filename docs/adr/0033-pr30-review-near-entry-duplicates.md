# ADR 0033 — PR #30 검토: 진입이 거의 같고 청산이 다른 후보도 한 가설로

- 상태: Accepted (후보 선정 규칙만 보강. 통계 기준·비용 가정·체결 가정은 그대로)
- 날짜: 2026-10-23
- 범위: `run/discovery/evolution.py`(`near_entry_duplicate`), `run/discovery/pipeline.py`(선정 규칙·스킵 근거),
  새 설정 `run/discovery/config/candidate_selection_v2.json`(기본값), 테스트
  `run/discovery/tests/test_selection_stopfill_20261022.py`, 증거 `run/audit/evidence_selection_stopfill_20261022/r8_*`
- 사용자 요청: 동동, 2026-10-23 지시문 2·5번 (PR #30 검토, 전략 중복 제거)
- 앞선 결정: [ADR 0031](0031-one-candidate-per-entry-signal-and-conservative-stop-fill.md)

## 검토 결과 (PR #30, ADR 0031)
| 항목 | 판단 | 근거 |
|---|---|---|
| 대표 선정 규칙 | 문제 없음 | 상한 둔 DEV t, 그다음 id. 결정적. 이미 배치에 낸 후보도 셈 |
| 진입 신호 키 | 문제 없음 | `behaviour_key`에서 `exit`만 뺀 sha256 80비트. 충돌 확률 무시 가능. `risk`는 어떤 평가도 읽지 않아 빠지는 게 맞음. 정수 특성 임계값·조건 하나짜리 any/all은 같은 키(테스트 추가) |
| 다른 위험 특성 병합 | 허용하되 근거 남김 | 청산만 다른 변형은 같은 진입 가설의 재검정. 빠진 후보는 상태를 바꾸지 않고 RESEARCH_ONLY로 남음. 이번에 스킵 기록에 양쪽 청산(보유 기간·손절·익절)을 남기게 함 |
| **누락** | 보완함 | 정확히 같은 진입만 잡고, 근접 중복 규칙은 청산을 구조에 넣어서 "임계값이 반 단계 안 + 청산이 다름"은 둘 다 배치에 들어갔음 |
| 손절 체결 | 문제 없음 | 봉 안 손절은 그 봉의 최저가(롱)/최고가(숏). 손절가보다 좋을 수 없음. 갭은 시가. 비용은 그 위에 따로 더함 |
| 미래 데이터 | 없음 | 체결가는 청산 봉 자신의 저가/고가. 그 뒤 봉은 읽지 않음. 진입 결정은 닫힌 봉만(PrefixView) |
| 기존 결과·비용 영향 | 문맥 분리 | 두 설정 파일이 코드 해시에 들어가 새 문맥 id. 예전 DEV 결과와 섞이지 않음. 비용 모델 파일은 안 바뀜 |
| main 병합 | 충돌 1건 해결 | `docs/adr/README.md` 표 행만 충돌(0030·0031·0032 모두 유지) |
| 3.11·3.13 테스트 | 로컬 통과 | GitHub Actions는 계정 결제 문제로 실행 안 됨(ADR 0034). 같은 단계를 로컬 가상환경에서 그대로 실행: 600 + 40 테스트 통과 |

## 결정
1. `evolution.near_entry_duplicate(a, b)`: b의 청산을 a의 것으로 바꾼 뒤 기존 `near_duplicate`를 적용. 즉 진입
   임계값이 반 단계 미만 차이(z 척도 0.125, 0~100 척도 2.5, 정수 특성은 완전 일치만)면 청산과 상관없이 같은 가설.
   경계(정확히 반 단계)는 다른 가설. 다른 매개변수(window), 방향, 종목은 다른 가설.
2. `config/candidate_selection_v2.json`(기본값): v1 규칙 + `one_per_near_entry_signal: true`. v1 파일은 R7 증거를
   위해 그대로 두고 읽을 수 있다(v1로 읽으면 새 규칙은 꺼짐). 이 규칙은 후보를 빼기만 하고 더하지 않는다.
3. 모든 진입 신호 스킵 기록에 `entry_signal_key`, `exit`, `twin_exit`를 남긴다. 청산이 같으면 "same exit"로 적는다
   (예전 문구는 항상 "different exit"였음).

## 실행 결과 (R8, DEV만)
- R7 명령 그대로, 코드 e5b016a. 894 평가 / DEV 통과 63 / INCONCLUSIVE 93 / REJECTED 738: R7과 같음.
- v1 선정으로 다시 고르면 R7의 33개와 순서까지 같음(재현). v2도 33개: 이번 실행엔 "근접 진입 + 다른 청산" 쌍이 없음.
- **DEV 통과 63개, 검증 배치 후보 33개, 독립 검증 완료 0개.** 검증·잠금·IW1 구간은 열지 않았다.

## 영향
- 코드 해시가 바뀌어 문맥 id가 새로 생김(R8: C2-a488c80b29e95ba2c425). DEV 판정 논리는 같음.
- 통계 기준(promotion_v3 등), 비용 가정, 손절 체결 가정 파일은 바꾸지 않았다.
