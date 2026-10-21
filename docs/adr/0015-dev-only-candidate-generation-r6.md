# ADR 0015 — DEV 단계 후보 추가 생성(R6, 8라운드 탐색)

- 상태: Accepted
- 날짜: 2026-10-21
- 범위: 기록만(코드·통계 기준 변경 없음)
- 선행: [ADR 0014](0014-archive-independence-fills-import-dedup-v2-search-family.md), [ADR 0012](0012-evolve-real-data-run-public-estimate-cost.md)

## 맥락
후보 전략을 더 만들어 달라는 요청이다. 새 독립 검증 구간이 없고 BTC/ETH 검증 구간은 이미 7번 사용했으므로 검증은 돌리지 않고 DEV 단계 후보 생성까지만 했다. 잠금 TEST는 열지 않았고 통계 기준은 바꾸지 않았다.

## 실행
- 데이터: 업로드 f6379f02, 빈 상태 폴더, NEW2 없음, 비용 상태 `ESTIMATED_FROM_PUBLIC_DATA`.
- 명령: `runner.py --mode evolve --dev-only --rounds 8 --evals 4000` (R5는 3라운드). 상세는 `run/audit/evidence_candidates_20261021/dev_only_r6_command.txt`.
- 컨텍스트 `C2-f64e792160a0222650f2`, CYCLE_OK, 3,553초. `ledger_check` CONSISTENT. `validation_window_history.jsonl` 변경 없음, 검증 배치 0, 잠금 사용 0, alpha 소비 0.

## 결과
| 항목 | R5(3라운드) | R6(8라운드) |
|---|---|---|
| 등록·DEV 평가 | 777 | 894 (시드 705 + 탐색 189) |
| DEV 통과(RESEARCH_ONLY) | 41 | 64 (시드 25 + 탐색 39) |
| INCONCLUSIVE / REJECTED | 64 / 672 | 93 / 737 |
| 중복 제외 | 6 | 행동 동일 30, 같은 정의 15, 무효 103 |
| 검증·승격 | 0 | 0 |

- DEV 통과 64개 목록: `run/audit/evidence_candidates_20261021/dev_pass_candidates.txt`. 보고서: `dev_only_r6_report.md`.
- DEV 통과는 후보 선별일 뿐 검정 통과가 아니다. 검증을 보지 않았으므로 생존·승격 판정은 없다.
- 탐색 라운드를 늘려 DEV 통과가 41에서 64로 늘었다. 많이 만들수록 우연히 통과하는 후보도 늘어나므로 이 수를 성과로 읽지 않는다.

## 한계와 다음 단계
- 새 독립 검증 구간이 생기기 전에는 이 후보들을 검증하지 않는다. 구간은 사전등록 후 `validation_window_history.jsonl`에 기록한다.
- 비용은 공개 호가 추정이며 VERIFIED가 아니다(ADR 0009). 승격 후보와 전진 페이퍼 대상은 여전히 0건이다.
