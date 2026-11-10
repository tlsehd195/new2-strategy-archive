# ADR 0066 — 독립 연구 통합: 표준 전략 기록·중복 규칙, run/*.py 분류표, 선택형 피처 세트 v2, 피처 데이터 레지스트리, DEV 구간 보호 로더

- 상태: Accepted (코드·테스트·분류표; 기본 동작과 기존 컨텍스트 해시 변경 없음)
- 날짜: 2026-11-10
- 요청: 동동, 2026-11-10 전수 감사 지시문 §2, §3, §4, §9, §15
- 감사 문제 ID: L-01 (High), I-01, I-02, F-01, F-02 (Medium) — `run/audit/AUDIT_FULL_20261110.md`

## 맥락
- **L-01**: DEV 패널 도구들이 공용으로 쓰는 `broad_universe_dev.load_funding`·`load_bar2345`에는 읽기 금지 구간 검사(`assert_allowed`)가 없었다. `load_daily`만 검사했다.
  - `newlisting_short_dev.py`(ADR 0060)는 종목별 펀딩을 2021-01-01~2025-10-20 한 번에 읽었다. 이 범위에는 읽기 금지 구간(2023-09-19~2024-06-18, 검증·잠금 구간)이 들어 있다.
  - 실제로 결과에 쓰인 값은 DEV 구간 안의 보유 기간 합계뿐이었다(보유 종료 ≤ 구간 끝). 그래서 발표된 수치는 바뀌지 않는다. 다만 "읽지 않는다"는 규칙(§15) 위반이었다.
- **I-01**: `run/*.py` 136개는 `LEGACY_SCRIPTS.md`의 폐기 목록 말고는 분류가 없었다. 엔진 전략과 독립 도구 전략을 같은 필드로 적는 형식도 없어서 비교와 중복 판정을 한곳에서 할 수 없었다.
- **F-01**: 엔진 피처는 10개(klines·funding·fng)였다. 과거 v1 가설 중 klines만으로 만들 수 있는 것(A1 비유동성, X12 왜도, 변동 범위, 이동평균 거리, 요일)은 엔진에 없었다.
- **F-02**: OI, 베이시스, 청산, 호가, DVOL, 체결 건수가 필요한 가설은 어디에도 "자료 없음"으로 기록돼 있지 않았다.

## 결정
1. **로더 보호 (L-01)**
   - `load_funding`과 `load_bar2345`가 요청 범위에 대해 `assert_allowed`를 호출한다.
   - 펀딩은 마지막 DEV 날 다음 00:00 정산 1건을 그대로 읽는다. 이 정산은 마지막 날 보유분의 지급이고, 요율은 그 전 8시간(DEV 시간)의 프리미엄으로 정해진다.
   - `newlisting_short_dev`는 펀딩을 DEV 구간별로 따로 읽는다.
   - 테스트: `run/test_dev_window_guard.py`.
2. **표준 전략 기록** `run/discovery/strategy_record.py` (`strategy-record-v1`)
   - 필드: strategy_id, family, version, spec, symbols, timeframe, entry, exit, filters, risk, required_features, data_requirements, cost_model, source_script, lineage, evaluation_status, validation_status, dedup_key.
   - 엔진 전략은 `from_engine_spec`로 만들고, 중복 키는 `evolution.behaviour_key`다.
   - 독립 도구 전략은 `research_record`로 만든다. 중복 키는 계열·시간 단위·유니버스·진입·청산 문장의 해시다. 검증 상태는 항상 NOT_VALIDATED이고, 독립 도구의 판정은 엔진 상태가 되지 못한다.
   - `dedup()`은 엔진 기록을 먼저 대표로 두고 나머지를 사유와 함께 따로 둔다.
3. **분류표** `run/audit/research_inventory_20261110.py`가 다음 세 파일을 만든다.
   - `RESEARCH_SCRIPTS_20261110.{json,md}`: 모든 run/*.py의 분류, 사유, 조사 수준.
   - `RESEARCH_STRATEGIES_20261110.jsonl`: 독립 도구 13개 가설의 표준 기록.
   - 조사 수준은 INVENTORIED(머리말·import·경로·위험 패턴 확인), LOGIC_TRACED(위험 줄을 읽고 추적), TESTED(run/test_*.py 통과) 중 하나다. 본문을 다 읽지 않은 파일은 CODE_READ로 적지 않았다.
4. **선택형 피처 세트 v2** (`strategy_spec.FEATURE_SETS["v2"]`, `enable_feature_set`)
   - 피처: `range_z`, `illiq_z`(A1 규칙), `ma_dist_z`, `skew_z`(X12 규칙), `dow_utc`. 모두 klines만 쓰고 인과적이다. 미래 봉을 바꿔도 값이 같다는 테스트가 있다.
   - 기본으로는 등록되지 않으므로 기존 `registry_identity`, 컨텍스트, DEV 결과가 그대로다.
   - 켜면 `registry_identity`에 `feature_sets: ["v2"]`가 붙어 새 실험 컨텍스트가 된다. 실행 옵션은 `runner.py --mode evolve --feature-set v2`다.
   - 정수 피처(`dow_utc`)는 `hour_utc`와 같은 정수 구간 규칙을 쓴다.
5. **피처 데이터 레지스트리** `run/discovery/feature_data_registry.py`
   - 계열별로 필요한 자료, 시점 규칙, 상태를 적는다. 상태는 IN_ENGINE / OPT_IN / DATA_MISSING / NOT_DERIVABLE 중 하나다.
   - `availability(data_dir)`는 자료 디렉터리에 파일이 없으면 DATA_MISSING을 낸다. 없는 자료를 다른 계열로 대신하지 않는다.
   - `check_feature_sets()`는 표와 피처 등록부가 맞는지 확인한다.

## 결과
- 기본 실행(legacy, evolve), 판정 기준, 컨텍스트 해시는 그대로다. `evolution.py`의 정수 피처 처리를 `hour_utc` 하나에서 `INTEGER_FEATURES`로 넓혔지만, 기본 피처에서는 난수 사용 순서가 같다.
- v2 피처를 쓰는 탐색은 아직 실행하지 않았다(성과 주장 없음). 실행하면 새 DEV 시행으로 세고, DSR·PBO 시행 집합에 들어간다.
- 남은 일:
  - 2자산 전략 유형(X4/F3 BTC-ETH 스프레드)은 엔진에 없다.
  - DATA_MISSING 계열은 자료를 수집해야 시험할 수 있다(감사 보고서 F절).
