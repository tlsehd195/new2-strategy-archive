# ADR 0065 — 실행 모드 audit·power, 엄격한 검색 설정 파일, DEV 시계열 무결성 점검, CYCLE_OK 의미 명시

- 상태: Accepted (코드·테스트, 판정 기준 변경 없음)
- 날짜: 2026-11-10
- 요청: 동동, 2026-11-10 전수 감사 지시문 §11 (실행 모드), §12 (상태·무결성)
- 감사 문제 ID: S-01, S-02, S-03, M-01, M-02 — `run/audit/AUDIT_FULL_20261110.md`

## 맥락
- `runner.py`에는 `legacy | evolve | full` 세 모드뿐이었다. 지시문 §11은 Audit과 Power 모드를 요구한다. 상태 점검 도구(`ledger_check.py`)와 검정력 분석(`power_analysis.py`, ADR 0064)은 별도 스크립트로만 실행할 수 있었다.
- 검색 설정(`search.SearchConfig`)은 코드 기본값과 `--selection`만 바꿀 수 있었다. `SearchConfig.from_json`은 모르는 키를 조용히 버린다. 그래서 오타가 난 설정 파일도 기본값으로 실행된다.
- ADR 0064 이전의 `dev_evaluate`는 OK 평가 기록을 먼저 쓰고 DEV 일별 시계열을 나중에 썼다. 그 사이에 프로세스가 죽으면 다음과 같이 된다.
  - 재개 시 기록이 이미 있어서 평가를 건너뛴다.
  - 그 전략은 DSR·PBO 시행 집합에서 조용히 빠진다.
  - `ledger_check`는 이 상태를 찾지 못했다.
- 사이클 요약의 `status`(`CYCLE_OK` 등)는 사이클 실행 상태다. 하지만 요약 파일만 보면 전략이 통과했다는 뜻으로 읽힐 수 있다(지시문 금지 사항 6).

## 결정
1. **`--mode audit`**: `ledger_check.check(state_dir)`를 실행하고 결과를 JSON으로 출력한다. 종료 코드는 일관 0, 불일치 2, 읽기 불가 1이다.
   - 잠금을 잡지 않고, `NEW2_DATA_DIR`이 필요 없고, 상태 디렉터리에 아무것도 만들지 않는다(테스트로 확인).
2. **`--mode power`**: `power_analysis.run()`(기본 설정 `config/power_analysis_v1.json`, 합성 자료만)을 `results/power_analysis.json`에 쓴다. 시장 자료는 읽지 않는다.
3. **`--search-config FILE`** (evolve 전용): `SearchConfig` 필드의 JSON 객체를 받는다.
   - 모르는 키, 객체가 아닌 JSON, 읽기 실패는 USAGE_ERROR다.
   - `selection`은 파일에 둘 수 없다. `--selection`으로만 정하며, map_elites는 DEV 전용이라는 규칙(ADR 0019)이 그대로 적용된다.
   - 설정 해시는 검색 상태에 기록되므로 새 파일은 새 검색이다.
4. **`MISSING_DEV_SERIES`** (`ledger_check`): OK인 DEV 평가 기록에 같은 (전략, 컨텍스트)의 `dev_series` 행이 없으면 불일치로 보고한다. 기록 순서 자체는 ADR 0064에서 고쳤다(시계열을 먼저 쓴다).
5. **요약의 의미 명시**: `EvolutionCycleSummary`에 두 필드를 추가한다.
   - `status_meaning = "cycle health only, not a strategy verdict"`
   - `strategies_passed`: `status_counts` 중 VALIDATION_SURVIVOR·PAPER_TEST_READY만 모은 것. 비어 있으면 통과한 전략이 없다.

## 결과
- 기존 모드의 동작, 판정 기준, 컨텍스트 해시는 바뀌지 않는다. `SearchConfig` 기본값과 해시도 그대로다.
- 요약 JSON에 필드 두 개가 늘었다. 원장(진실 원천)은 바뀌지 않는다.
- 테스트: `tests/test_run_modes_20261110.py`.
