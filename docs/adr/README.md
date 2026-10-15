# Architecture Decision Records

탐색 엔진(`run/discovery/`)의 설계 결정 기록. 형식: 상태 · 맥락 · 결정 · 결과(장점/비용/남은 위험). 기록은 덮어쓰지 않고, 바뀌면 새 ADR로 대체한다.

| ADR | 제목 | 상태 |
|---|---|---|
| [0001](0001-discovery-engine-residual-defect-fixes.md) | 탐색 엔진 잔여 핵심 결함 수정 (세대 단위 FDR, MTM, 비용 분류, 잠금 TEST) | Accepted (PR #1, `66646cd`) |
| [0002](0002-fdr-family-notions-and-run-cycle-exit-codes.md) | FDR 가족 개념 분리, 과거 컨텍스트 PASS 처리, run_cycle 종료 코드 정책 | Proposed (PR #2) |
