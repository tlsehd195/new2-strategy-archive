# 현재 상태 요약 — 2026-11-12 (ADR 0078 통합 전수 감사 이후)

이전 요약(`STATUS_CURRENT_20261020.md` 등)은 작성 시점 기록으로 남긴다. 근거는 [FINAL_REPORT_20261112.md](FINAL_REPORT_20261112.md)와 [ADR 0078](../../docs/adr/0078-integrated-full-audit-20261112.md).

| 구분 | 상태 |
|---|---|
| 연구 엔진 코드 | ADR 0078까지 확인한 Critical·High 결함 수정. 수용 한계 E-01·ME-04(사용자 결정), A-05·B-06 일부·C-04·C-07. POSSIBLE 13건은 미확인 |
| 감사 범위 | 핵심 모듈 일부(`runner.py`, `engine_lib.py`)는 부분 검토. 검토자 "전체 읽음"은 주장으로만 기록 |
| 전략 수익성 | 승격 후보 0건(변경 없음). 이번 수정 후 실제 데이터 재실행은 하지 않음 |
| 비용 | VERIFIED 아님. 실계좌 체결 200건/심볼 필요(OKX 기록은 DEMO) |
| 검증 창 | IW1·잠금 TEST·전진 관측 미개방, 사전등록 활성화 없음 |
| 버전 | datapol-v2, funding-schedule-v2, mc-v4, evo-pipeline-v2, fp-v3, evo-gen-v2, power-analysis-v2, portfolio-multi-v4, portfolio-mtm-v4 (새 맥락 ID) |
