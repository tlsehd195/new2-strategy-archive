# ADR 0079: ADR 0078 보완 감사 — D-01 부트스트랩 해상도, Paper Forward FNG, 잠금 밖 결정, runner/engine_lib 전수 검토

- 상태: Accepted
- 날짜: 2026-11-12
- 기준: PR #79 HEAD `757114f` (main `2d6616a`)
- 지시: 사용자 동동 2026-11-12 14:00Z "ADR 0078 보완 감사 및 D-01 통계 해상도 결함 수정 지시"
- 보고서: [run/audit/FINAL_REPORT_ADR0079_20261112.md](../../run/audit/FINAL_REPORT_ADR0079_20261112.md)
- 회귀 테스트: `run/discovery/tests/test_audit_followup_20261112.py` (33개, `757114f`에서 22개 실패, 11개는 대조·문서 고정 테스트)

## 1. 배경

ADR 0078은 "alpha*(B+1) < 5이면 검정 거부"라고 적었고 `bootstrap_B_for`와 `MC_METHOD_VERSION`도 5를 썼다. 그러나 실제 검사 `_require_resolution`은 `< 1.0`만 막았다. 그래서 1 이상 5 미만 구간이 통과했다. 예: 이전 버전이 사전등록한 B=164 배치(alpha 0.0152, alpha*(B+1)=2.5)에서 StepM이 실행되어 PASSED_STEPM이 나왔다(재현 테스트 `test_legacy_small_B_batch_is_blocked_not_rejected`).

## 2. 결정

### D-01 부트스트랩 해상도 (stats_mc.py, pipeline.py, independent_window.py, validation_registry.py)

- 기준은 `MIN_TAIL_DRAWS`(=5) 하나. `_require_resolution`은 `alpha*(B+1) < MIN_TAIL_DRAWS`이면 `InsufficientBootstrapResolution`. 비교는 `Fraction`으로 정확히 한다(경계 5.0이 반올림으로 갈리지 않음).
- alpha: 실수(불리언 제외), 유한, 0 < alpha < 1. B: 정수(불리언·실수 제외), B ≥ 1. 아니면 `ValueError`. `stepm`(세 studentize 모드), `step_spa`, `mcs`(모델 1개인 경우 포함)가 같은 검사를 거친다.
- `bootstrap_B_for`: 같은 입력 검사. 공식(`ceil(5/alpha)+1`)은 그대로라 새 배치의 B는 바뀌지 않는다. 반환한 B는 `_require_resolution`을 항상 통과한다(테스트로 고정).
- 방법 태그 `mc-v5(...,refuse-alpha(B+1)<5)`(새 맥락 ID).
- 파이프라인: 기록된 B로 해상도가 안 되는 창은 `stepm_blocked` 기록. 그 창의 멤버는 결과 `BLOCKED_STATISTICS`, 상태 `BLOCKED_STATISTICS`(stage validation), 검증 기록 `stepm_blocked`·`failure_class=BLOCKED_STATISTICS`. 기각(REJECTED)도 통과도 없다. 저장된 B·alpha·배치 번호·알파 이력 행은 그대로 쓰고 바꾸지 않는다(B를 올려 다시 돌리는 것은 사전등록되지 않은 재검정). 이 멤버는 새 배치 후보에서 빠진다(검증 창 재조회 금지). `step_spa`·`mcs` 거부는 `step_spa_blocked`·`mcs_blocked`로 따로 기록(이전에는 `spa_error`에 섞임). `outcome_label`은 UNDETERMINED_POWER.
- 재개: ADR 0079 이전 기록에는 `stepm_blocked` 필드가 없으므로 있는 필드만 대조한다.
- 독립 창(IW): 동결된 B가 그 tranche의 alpha를 해상하지 못하면 **등록·이력 기록·데이터 읽기 전에** `WindowRefused`(tranche는 쓰이지 않음). 실행 중 거부되면 멤버 전원 `BLOCKED_STATISTICS`. 실제 IW1 사전등록(alpha 0.05, B 1000, tranche 2개)은 k=1 30.4, k=2 7.6으로 영향 없음.

### G-04 Paper Forward의 FNG

- 재현: FNG를 쓰는 전략의 계획은 FNG 값을 고정하지 않았다(계획에 해시·스냅샷 없음). 평가기와 재생이 매 단계 연구용 provider의 FNG를 읽었다. 등록 후 FNG 원본이 바뀌어도(확장·수정) 감지 못 하고 PAPER_PASSED가 나왔다(합성 provider). 운영 provider는 FNG 해시가 맥락 ID에 들어가 PLAN_CONTEXT_CHANGED로 막히지만, 그것은 간접 보호이고 provider에 따라 달라진다.
- 전진 피드(FileFeed, BinancePublicFeed)에 FNG 원천이 없어서, 관측 기간의 FNG를 등록 시점에 스냅샷으로 고정할 수 없다. 그래서 **새 FNG 계획은 등록 거부**(`FNG_NOT_PINNED`). ADR 0079 이전에 등록된 FNG 계획은 결론을 내지 않는다(`_verdict` PAPER_BLOCKED_DATA, `_conclusion_problems`). 이미 기록된 결론은 다시 쓰지 않고, `verify_conclusion`과 재개 경로가 확인해 주지 않는다. FNG를 쓰지 않는 계획은 영향 없음(대조 테스트).

### G-03 재생(replay)과 평가기의 공유 범위

독립 재생(ADR 0077)이 독립적인 것은 **포지션 순회**(겹침·미결·원장 대조)와 **데이터 구멍 재도출**(봉 로그에서 다시 계산해 state와 비교)뿐이다. 아래는 양쪽이 같은 코드를 쓰므로 그 안의 결함은 재생으로 잡히지 않는다.

| 영역 | 공유 함수 | 결과 |
|---|---|---|
| 신호 | `build_spec_signal` | 같은 결함이 양쪽에 반복 |
| 지표 | `build_spec_aux`(특징 함수 전체) | 같음 |
| 인과성 | `PrefixView` | 같음 |
| 펀딩 | `_funding_series`, `FundingIndex` | 재생은 커버리지(None 여부)만 확인, 금액은 확인 안 함 |
| 진입·청산 | `stop_exit_rule`, 비용 거부 `cost.round_trip_bp` | 같음 |
| 입력 | `_warmup`, `read_bar_log`, `_row_bar` | 같음(파일 해시는 별도로 검사) |
| 구멍 처리 | `GAP_EMBARGO_BARS` 규칙 | 규칙은 같고, 구멍 목록은 독립 재도출 |
| FNG | `provider.fng()` | 같음(이번에 FNG 계획 자체를 막음) |

재생이 전혀 보지 않는 것: 체결 시뮬레이션(`apply_execution`), 순손익·비용·펀딩 금액, 지표·판정 임계값. `TestG03SharedSurface`가 이 목록을 AST로 고정한다(공유 호출이 늘거나 줄면 문서와 함께 바꿔야 한다). 독립성은 "누락·위치 이동 같은 포지션 결함 검출"로 한정해서 말한다.

### F-11 잠금 밖의 결정

`append_jsonl`·`atomic_write_json`·`exclusive_file_lock`·`ProcessLock` 호출부 전부를 분류했다(보고서 §A 표). 운영에서 한 상태 디렉터리의 쓰기는 `runner.py`의 `run.lock`(ProcessLock) 하나로 직렬화된다. 여러 상태 디렉터리가 함께 쓰는 파일(검증 창 이력, IW 레지스트리, 잠금 TEST 레지스트리)은 이미 파일 잠금 안에서 읽고·판단하고·썼다. 그러나 라이브러리 사용(테스트, 연구 스크립트)과 여러 인스턴스에서는 다음이 잠금 없이 판단했다. 재현 후 수정:

- `StatusStore.set`: 메모리 캐시로 전이를 판단해, 다른 쓰기가 REJECTED(종결)로 바꾼 뒤에도 VALIDATION_SURVIVOR를 기록했다 → 파일 잠금 안에서 파일 크기가 바뀌었으면 다시 읽고 판단.
- `CampaignLedger.preregister`: 두 쓰기가 같은 잠금 창에 캠페인 번호 1을 받았다(알파 두 번 사용) → 원장 잠금 안에서.
- `ForwardPaper.register_plan`: 한 전략에 계획 두 개 → 계획 원장 잠금 안에서.
- 배치 원장(`validation_batches.jsonl`): 이력 파일이 없을 때 잠금이 없었다 → 항상 잠금(순서: 이력 → 배치 원장).
- `contexts.jsonl`: 같은 맥락 행 중복 가능 → 잠금(무해했지만 같이 정리).

### runner.py / engine_lib.py 저자 전수 검토

두 파일의 모든 줄(2,107 / 1,626)을 이 스레드에서 읽었다. 출처표에 FULL로 기록하고 읽은 판의 sha256을 고정했다(파일이 바뀌면 `audit_provenance check` 실패). 발견:

- R-1 (수정): `run_one`·`reevaluate_prior_pass`가 `LookupError`를 DATA_MISSING으로 처리했다. `KeyError`·`IndexError`도 여기에 포함된다. `PrefixView` 밖 봉 읽기(미래 참조)의 `IndexError`가 "데이터 없음"(데이터가 오면 재검증 가능)으로 기록됐다. 이제 `ERROR`/`LOGIC_ERROR`(재시도 없음). 의도된 `LookupError`(FNG·펀딩 없음)와 `FileNotFoundError`는 그대로 DATA_MISSING.
- R-2~R-5, E-1~E-4: 기록만(보고서 §F). 거짓 PASS로 이어지는 경로는 찾지 못했다.

## 3. 결과·영향

- 기존 `data/`, `research/`, `results/`, state, 원장, PREREG, `private_fills/`는 바꾸지 않았다. 과거 기록을 새 규칙으로 다시 평가하지 않았다(새 상태값은 앞으로의 실행에만 쓰인다).
- 기존 테스트 1건 조정: `test_pdf_check_20261022.test_rectangular_negative_variance_is_refused`가 B=49(alpha*(B+1)=2.5)를 써서 이제 해상도 거부(같은 ValueError)로 통과해 버린다. 검사 대상(직사각 가중 음분산)을 계속 시험하도록 B=99로 바꾸고 메시지까지 확인한다(완화 아님, 오히려 강화).
- 버전: mc-v5. 맥락 ID가 바뀐다(ADR 0078에서 이미 바뀌었고, 병합 전이다).

## 4. 사용자 결정이 필요한 것

- FNG 전략의 전진 관측: FNG 원천을 전진 피드에 추가하고 계획별로 해시 체인 기록을 남길지(지금은 등록 거부).
- 잠금 TEST 캠페인 수와 사용 기록은 상태 디렉터리마다 따로 센다(`locked_window_campaigns.jsonl`, `lock_test_usage.json`). 두 디렉터리가 같은 잠금 창을 각자 캠페인 1로 쓸 수 있다(D-03의 잠금 창 판). 공유 이력으로 바꾸면 앞으로의 캠페인 알파가 바뀐다.
- ADR 0078의 E-01, ME-04는 그대로 남는다.
