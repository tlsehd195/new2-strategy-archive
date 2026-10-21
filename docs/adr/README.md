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
| [0008](0008-residual-audit-cost-estimate-lookahead-forward-integrity.md) | 잔여 감사: 공개 시세 추정 v2, 비용 계산 시점 누출 제거, 체결 증거 규칙, 전진 페이퍼 무결성 | Accepted (PR #8 병합, `a2f63c0`) |
| [0009](0009-locked-test-requires-verified-cost.md) | 비용 모델이 검증되기 전에는 잠금 TEST를 열지 않는다 | Accepted (PR #8 병합, `a2f63c0`) |
| [0010](0010-legacy-lock-gates-cross-symbol-independence-mode.md) | legacy 잠금 TEST 비용 게이트, 다종목 잠금 창 독립성, 실행 모드 표시 | Accepted (PR #9 병합, `3685355`) |
| [0011](0011-public-market-estimate-first-run.md) | 공개 호가 80시간 수집분으로 첫 비용 추정 (ESTIMATED_FROM_PUBLIC_DATA) | Accepted |
| [0012](0012-evolve-real-data-run-public-estimate-cost.md) | 공개 호가 추정 비용으로 실데이터 evolve R3 (검증 진입 20, 생존 0) | Accepted |
| [0013](0013-audit-search-pool-cost-evidence-alpha-history.md) | 전체 감사: 검색 풀 분리, 행동 중복, 비용 전후 비교, 비용 검증 상태 세분, 창 단위 알파 소비 | Proposed |
| [0014](0014-archive-independence-fills-import-dedup-v2-search-family.md) | 아카이브 독립, 체결 가져오기, 행동 중복 fp-v2, 탐색 단위 다중검정(promo-v3), 상태·원장 검사 | Proposed |
| [0016](0016-independent-window-2020-market-cap-tranches.md) | 새 독립 검증 구간 IW1: 2020년 데이터, 시총 순 묶음, 사전등록과 1회 사용 | Proposed |
| [0017](0017-paper-review-ssrn3261943-power-precheck.md) | 업로드 논문 검토(SSRN 3261943): DSR 식 대조, IW1 검정력 계산, 사전점검 제안 | Proposed |
| [0018](0018-short-and-long-short-support-check.md) | 숏·롱숏 지원 점검, DEV 묶음 진단 도구 `bundle_dev.py` | Accepted |
