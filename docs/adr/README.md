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
| [0008](0008-residual-audit-cost-estimate-lookahead-forward-integrity.md) | 잔여 감사: 공개 시세 추정 v2, 비용 계산 시점 누출 제거, 체결 증거 규칙, 전진 페이퍼 무결성 | Proposed (PR #8) |
| [0009](0009-locked-test-requires-verified-cost.md) | 비용 모델이 검증되기 전에는 잠금 TEST를 열지 않는다 | Proposed (PR #8) |
