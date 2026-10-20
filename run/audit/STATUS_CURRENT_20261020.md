# 현재 상태 요약 — 2026-10-20 (전체 감사 이후)

이 문서는 연구 전체의 현재 상태를 한곳에 모은 요약이다. 이전 문서(`STATUS_CURRENT_20261017.md` 등)는 작성 시점의 기록으로 남겨 두었다. 상세 근거는 [AUDIT_FULL_20261020.md](AUDIT_FULL_20261020.md)와 [ADR 0013](../../docs/adr/0013-audit-search-pool-cost-evidence-alpha-history.md)에 있다.

- 기준: `main` `900c71c`(PR #11 병합) + 브랜치 `claude/project-thread-vabu8n`의 ADR 0013 변경.
- 이 저장소는 연구·백테스트 전용이다. NEW2 매매 시스템과 데이터·API·파이프라인으로 연결하지 않는다. 비용 계산에 NEW2 코드(고정 커밋 `8ec6371`)를 읽기 전용으로 쓴다.

## 1. 판정

| 구분 | 상태 |
|---|---|
| 연구 엔진 코드 | 확인한 결함은 수정했다(ADR 0001~0013). 테스트 427개 OK |
| 전략 수익성 | **승격 후보 0건.** 독립 검증을 통과한 전략이 없다 |
| 비용 | 실행에 쓴 비용은 `ESTIMATED_FROM_PUBLIC_DATA`. 체결 검증은 `BLOCKED_EXTERNAL_EVIDENCE`(계정 체결 없음) |
| 전체 | **PARTIAL.** 코드 측면은 정리됐다. 비용 검증·잠금 TEST·전진 관측은 외부 증거 없이는 끝나지 않는다 |

## 2. 전략 연구 현황

| 묶음 | 결과 | 출처 |
|---|---|---|
| 수동 측정 32건 | 전부 FAIL 또는 폐기 | `docs/NEW2_전략연구_보고서_v29.md` |
| legacy 엔진(779 후보) | 실데이터 전량 실행 없음 | `STATUS_CURRENT_20261017.md` §2 |
| evolve 실데이터 R1~R3(기본·공개 추정 비용) | 검증 생존 0 | ADR 0012, `STATUS_CURRENT_20261017.md` |
| **evolve 실데이터 R4**(2026-10-20, 공개 추정 비용, 코드 `8a22e73`) | DEV 777, 검증 20명, **생존 0** | `evidence_audit_20261020/r4_*` |

R4 요약:
- 상태: REJECTED 679, INCONCLUSIVE 76, RESEARCH_ONLY 22. 잠금 TEST 사용 0.
- 검증 탈락 원인: INSUFFICIENT_DATA 13, FAIL_COST 3, NO_EDGE_OUT_OF_SAMPLE 2, FAIL_STAT 2.
- DEV 중앙값: 총수익 1.65bp, 비용 14.10bp, 펀딩 −0.97bp, 순수익 −12.56bp. 총수익이 양수인 413건 중 232건이 비용 후 음수다.
- 검증 배치 번호 7(창 이력 6건 포함), 알파 0.00062.

## 3. 검증 창 사용 기록

`run/discovery/validation_window_history.jsonl`에 실데이터 검증 배치 7건(A1, A2, D1, R1, R2, R3, R4)이 있다. `runner.py --mode evolve`는 이 파일이 없으면 실행을 거부한다. 다음 배치는 k=8부터 시작하므로 알파가 더 작다. 같은 창을 더 보기보다 새 구간이 쌓인 뒤 새 창을 사전등록하는 편이 낫다.

## 4. 외부 증거가 필요한 항목

| 항목 | 필요한 것 | 현재 |
|---|---|---|
| 비용 VERIFIED | 계정 체결 기록, 종목당 검증 체결 200건 이상 | 없음 |
| 공개 호가 추정 갱신 | Oracle VM 장기 수집분 업로드 | 수집 중 |
| 잠금 TEST | 검증 생존자 + VERIFIED 비용 | 생존자 0 |
| 전진 페이퍼 | `PAPER_TEST_READY` 전략 + VERIFIED 비용 | 없음 |

## 5. 보는 법

- 원장에서 직접 집계: `python3 run/discovery/evolution_report.py <state_dir> --md out.md`
- 실행 상태: `python3 run/discovery/ops.py health`. `CYCLE_INTERRUPTED`는 중단된 사이클이다.
- 비용 검증: `python3 run/discovery/cost_calibration.py … --quarantine bad.jsonl`. 상태는 `VERIFIED` / `NOT_VERIFIED` / `INSUFFICIENT_DATA` / `BLOCKED_EXTERNAL_EVIDENCE`.
