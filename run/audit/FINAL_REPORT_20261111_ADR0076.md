# 최종 보고 — 독립 감사(ADR 0075 대상) 대응, ADR 0076 (paper-forward-cutoff-v1)

- 날짜: 2026-11-11, 기준 커밋 `4fd25d2`
- 입력: `audit_report_paper_forward_20261111.docx` (판정 CHANGES REQUIRED: F-1 Medium, F-2 Low, 7.3 보완 권고)

## 항목별 결과

| 항목 | 결과 | 처리 |
|---|---|---|
| F-1 다중 심볼 경계 min(last_ts) | 결함 아님 | 유지. 감사 권고(최댓값/심볼별)는 늦은 심볼의 아직 안 보인 진입을 빠뜨린 표본으로 결론을 낼 수 있어 더 위험. 단위 테스트 4개로 고정. 다중 심볼 전체 경로 테스트는 픽스처 부재로 미실행 |
| F-2 피드 정지 시 무기한 OBSERVING | 재현됨 | 시계가 max_days 창을 지났는데 데이터가 없으면 PAPER_BLOCKED_DATA(OBSERVATION_WINDOW_NOT_COVERED, 비최종), 창 진행 중 1일 이상 지연은 DATA_LAG 사유 |
| 추가: cutoff가 step 시점에 좌우 | 재현됨 (61/75/120일 step → 표본 61/75/120일) | cutoff = 원장에서 계산한 '규칙이 성립하는 가장 이른 봉'. 어떤 주기로 돌려도 60일 |
| 추가: 늦은 step 하나로 표본이 max_days 초과 | 재현됨 (max_days 70인데 90일 표본으로 PASSED) | 표본·days_observed·missing_bar_share를 창으로 제한 → INCONCLUSIVE |
| 7.3 원장·cutoff 앵커 | 반영 | 평가 기록에 anchors(원장 sha256·기록 수, 봉 로그 head, 증거 head, cutoff), `verify_conclusion()` |

## 검증

- 재현 3개: `4fd25d2`에서 실패, 수정 후 통과
- discovery 977개(955 + 22), 루트 152개, legacy 1개: Python 3.11 / 3.13 모두 OK
- 기존 테스트 수정: cutoff 위치를 단언하던 6곳(positions 4, funding 2). 결론(PASS/FAIL/ERROR/BLOCKED) 단언 완화 없음

## 남은 한계

- 다중 심볼 step 전체 경로 미검증(합성 다중 심볼 전략 픽스처 없음)
- 평가 기록 파일(evaluations.jsonl) 자체를 통째로 다시 쓰는 위조는 저장소 밖 앵커가 필요
- 실제 forward 계획은 없음(PAPER_TEST_READY 전략 없음). prereg·검증/잠금/IW1 구간·실거래 무관
