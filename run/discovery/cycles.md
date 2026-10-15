## Cycle 1 — 2026-10-14T11:25:55Z
- tested=2/779(문법 유니버스) verdicts={'FAIL_COST': 2} survivors_total=0
- 패밀리 커서: {"flow_z": 1, "ret_rev": 1}

## Cycle 2 — 2026-10-14T11:26:27Z
- tested=9/779(문법 유니버스) verdicts={'FAIL_COST': 9, 'UNDETERMINED_POWER': 1} survivors_total=0
- 패밀리 커서: {"flow_z": 2, "ret_rev": 2, "ts_mom": 1, "vol_break": 1, "tod": 1, "fund_z": 1, "fng": 1}

## Cycle 3 — 2026-10-14T11:27:57Z
- tested=16/779(문법 유니버스) verdicts={'FAIL_COST': 9, 'UNDETERMINED_POWER': 1} survivors_total=0
- 패밀리 커서: {"flow_z": 3, "ret_rev": 3, "ts_mom": 2, "vol_break": 2, "tod": 2, "fund_z": 2, "fng": 2}

## Cycle 4 — 2026-10-14T11:28:00Z
- tested=23/779(문법 유니버스) verdicts={'FAIL_COST': 9, 'UNDETERMINED_POWER': 1} survivors_total=0
- 패밀리 커서: {"flow_z": 4, "ret_rev": 4, "ts_mom": 3, "vol_break": 3, "tod": 3, "fund_z": 3, "fng": 3}

## Cycle 5 — 2026-10-14T11:28:49Z
- tested=33/779(문법 유니버스) verdicts={'FAIL_COST': 9, 'UNDETERMINED_POWER': 1} survivors_total=0
- 패밀리 커서: {"flow_z": 6, "ret_rev": 6, "ts_mom": 5, "vol_break": 4, "tod": 4, "fund_z": 4, "fng": 4}

## Cycle 6 — 2026-10-14T11:33:11Z
- tested=43/779(문법 유니버스) verdicts={'FAIL_COST': 9, 'UNDETERMINED_POWER': 1} survivors_total=0
- 패밀리 커서: {"flow_z": 8, "ret_rev": 8, "ts_mom": 7, "vol_break": 5, "tod": 5, "fund_z": 5, "fng": 5}

## Cycle 7 — 2026-10-14T11:36:50Z
- tested=53/779(문법 유니버스) verdicts={'FAIL_COST': 9, 'UNDETERMINED_POWER': 1} survivors_total=0
- 패밀리 커서: {"flow_z": 10, "ret_rev": 10, "ts_mom": 9, "vol_break": 6, "tod": 6, "fund_z": 6, "fng": 6}

## 복구 기록 (Phase 1-g) — 20261015T000206Z
- 소실 원장 행 88건을 아카이브 결과 JSON(archive-results-json+prereg@92716f6)에서 append-only 재구성 (recovered_from 태그로 신구 엄격 구분)
- 원장: 150행 = 고유 126 + 중복 24 (사이클 2·3·4 재시도 9쌍 포함 24중복행)
  ※ 고유 126 = 사이클 1~16 처리 후보. 판정 분포 {"FAIL_COST": 115, "UNDETERMINED_POWER": 18, "DATA_MISSING": 16, "FAIL_CONSIST": 1}
- 검증: 기존 62행↔결과 JSON 전수 정합 · PREREG sha256 150/150 일치 · 커서 통과 후보 미테스트 0건 · next_batch 제안 10건 전부 미테스트 신규
- state.json 재구축: cycle=16 · tested(고유)=126 · tested_rows=150 · SURVIVOR 0건 — 승격 큐 변동 없음 (R3 준수)

## Cycle 17 — 2026-10-15T00:25:07Z
- tested=136/779(문법 유니버스) verdicts={'FAIL_COST': 9, 'UNDETERMINED_POWER': 1} survivors_total=0
- 패밀리 커서: {"flow_z": 28, "ret_rev": 25, "ts_mom": 24, "vol_break": 15, "tod": 15, "fund_z": 15, "fng": 14}

