# ADR 0079: ADR 0078 보완 감사 — D-01 부트스트랩 해상도, Paper Forward FNG, 잠금 밖 결정, runner/engine_lib 전수 검토

- 상태: Accepted
- 날짜: 2026-11-12
- 기준: PR #79 HEAD `757114f` (main `2d6616a`)
- 지시: 사용자 동동 2026-11-12 14:00Z "ADR 0078 보완 감사 및 D-01 통계 해상도 결함 수정 지시"
- 보고서: [run/audit/FINAL_REPORT_ADR0079_20261112.md](../../run/audit/FINAL_REPORT_ADR0079_20261112.md)
- 회귀 테스트: `run/discovery/tests/test_audit_followup_20261112.py` (33개, `757114f`에서 22개 실패, 11개는 대조·문서 고정 테스트). 후속(§5) 15개 추가, 합계 48개. 정적 호출 그래프 도구 `run/discovery/tests/static_callgraph.py`

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

재생이 전혀 보지 않는 것: 체결 시뮬레이션(`apply_execution`), 순손익·비용·펀딩 금액, 지표·판정 임계값. `TestG03SharedSurface`는 이름이 코드에 있는지만 확인했다. 후속 작업(§5.2)에서 호출 그래프로 계산한 정확한 목록으로 대체했다. 독립성은 "누락·위치 이동 같은 포지션 결함 검출"로 한정해서 말한다.

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

## 5. 후속 (사용자 지시 2026-11-12 23:20Z, 기준 `3d49e41`)

### 5.1 완료된 FNG 계획의 `step()` 반환

- 재현: `_step_locked`는 `st["concluded"]`이면 FNG 검사보다 먼저 저장된 판정을 그대로 돌려줬다. 그래서 ADR 0079 이전 코드가 PAPER_PASSED로 끝낸 FNG 계획은 `step()`에서 PAPER_PASSED가 나오고, `verify_conclusion()`은 FNG_NOT_PINNED를 냈다(두 API 불일치). 테스트 `test_concluded_fng_plan_step_does_not_return_old_pass`가 `3d49e41`에서 실패한다.
- 수정: `_concluded_result(plan, st)`가 완료 계획의 반환을 만든다. 전략이 FNG를 쓰면 다음을 돌려준다.
  - `verdict: PAPER_BLOCKED_DATA`
  - `status: CONCLUDED` (기존 호출부 `autoloop`는 `status`·`verdict`만 읽는다)
  - `recorded_verdict`: 저장된 판정
  - `conclusion_verified: False`
  - 사유: `FNG_NOT_PINNED`와 `UNVERIFIED_PRIOR_CONCLUSION`
- 읽기 전용: state.json, evaluations.jsonl, 상태 원장, steps.jsonl을 쓰지 않는다. 테스트가 상태 디렉터리 전체의 바이트를 비교한다. 과거 결론은 그대로 남는다.
- FNG가 없는 완료 계획은 이전과 같은 dict를 돌려준다(대조 테스트로 키까지 비교).
- 남는 것: 상태 원장에는 그 전략이 여전히 `PAPER_PASSED`로 남는다(소급 수정 금지). `handoff.build_handoff`는 상태 원장과 평가 기록을 읽으므로, 그런 전략을 내보내면 `paper_forward_verdict: PAPER_PASSED`가 그대로 들어간다. 사람 검토용 문서이고 `requires_human_approval: true`이지만 같은 표시가 없다. 사용자 결정 항목(§5.4).

### 5.2 G-03 공유 범위: 호출 그래프로 계산

`static_callgraph.CallGraph`가 discovery 모듈의 AST에서 **Call 노드**만 따라 호출 관계를 만든다.

- 해석하는 것:
  - 모듈 함수
  - `from m import f as g`(함수 안 import 포함)
  - `import m as a; a.f()`
  - `self.`·`cls.`·`Class.` 메서드(같은 저장소 안의 부모 클래스 포함)
  - 주석이 달린 매개변수와 생성자·반환 주석으로 타입이 정해진 지역 변수의 메서드
  - 타입이 정해진 dataclass 리스트 필드를 도는 반복 변수의 메서드
  - 인자로 넘긴 저장소 함수(ref)
  - 중첩 함수 본문(정의한 함수에 귀속)
- 선언으로 묶는 것: 타입이 소스에 없는 수신자 셋.
  - `self.p.cost` → `costs_bridge.CostContext`
  - `self.p.specs` → `pipeline.SpecStore`
  - `self.p.provider` → 동적 노드 `dyn:self.p.provider.fng`
- 기준점:
  - 평가기: `ForwardPaper._evaluate`
  - 재생: `ForwardPaper._reconstruct_positions`
- 양쪽이 각자 비공유 코드에서 **직접 들어가는** 공유 함수(그 아래는 전부 공유)는 아래 표와 **정확히 일치**해야 한다(`TestG03CallGraph`). 공유 호출이 하나라도 늘거나 줄면 테스트가 실패한다. 원본 사본에 재생 쪽 `apply_execution` 호출을 넣는 변형 테스트로 확인했다.

| 영역 | 공유 진입 함수 | 결과 |
|---|---|---|
| 명세 | `strategy_spec.canonicalize_spec`, `evolution.SpecStore.get` | 같은 명세 해석 |
| 지표·신호 | `strategy_spec.build_spec_aux`, `strategy_spec.build_spec_signal` | 특징 함수 전체(`FEATURES` 표 경유) 공유 |
| 청산 | `strategy_spec.stop_exit_rule` | 반환된 청산 함수까지 공유 |
| FNG | `dyn:self.p.provider.fng` | provider 구현마다 다름(정적으로 알 수 없음) |
| 인과성 | `evaluator.PrefixView` | 같음 |
| 펀딩 | `evaluator.FundingIndex`, `evaluator.FundingIndex.paid_bp`, `paper_forward.ForwardPaper._funding_series`, `paper_forward.ForwardPaper._funding_policy` | 커버리지·금액 계산 코드가 같음 |
| 비용 거부 | `costs_bridge.CostContext.round_trip_bp` | 같음 |
| 입력 | `paper_forward.ForwardPaper._warmup`, `paper_forward.read_bar_log`, `paper_forward._row_bar`, `engine_lib.iter_jsonl` | 같음(파일 해시는 별도 검사) |

- 평가기만 직접 부르는 것:
  - `evaluator.run_event_study`
  - `execution_sim.ExecutionConfig`, `execution_sim.apply_execution`
  - `paper_forward.ForwardPaper._series`, `paper_forward.ForwardPaper._open_positions`, `paper_forward.ForwardPaper._reconcile_trades`
  - `evaluator.EventRecord.to_json`
  - `engine_lib.append_jsonl`, `engine_lib.iso_now`
- 재생만 직접 부르는 것:
  - `paper_forward.ForwardPaper._replay_from_disk`, `paper_forward.ForwardPaper._replay_positions`
  - `costs_bridge.CostContext.identity`
- 양쪽이 직접 읽는 규칙 상수: `GAP_EMBARGO_BARS`, `MS15`.
- 3d49e41 문서 대비 새로 드러난 공유 진입:
  - `canonicalize_spec`, `SpecStore.get`, `iter_jsonl`, `FundingIndex.paid_bp`
  - `round_trip_bp`은 평가기 쪽에서 `run_event_study` 안에서 불린다.

정적 분석의 한계는 테스트와 이 문서에 그대로 적는다.

- 정적 분석으로는 보지 못하는 호출이 있다. 이런 호출은 `untracked`에 기록하고 추정하지 않는다.
  - 실행 때만 정해지는 값으로 하는 호출(`sig(...)`, `exit_rule(...)`, `refusal(...)` 같은 콜러블 매개변수, 함수 사전 `FEATURES[name].fn`, `getattr`)
  - 타입 없는 속성의 메서드(`CostContext.model.cost_fraction`)
  - 던더 메서드(`len()` 등)
  - 표준 라이브러리 내부
- 도달 가능하다는 것과 매 입력에서 실행된다는 것은 다르다(과대 근사).
- 그래서 `TestG03RuntimeTrace`가 합성 계획 하나를 실제로 돌린다. `sys.setprofile`로 양쪽에서 실행된 저장소 함수를 기록한다. 실행된 함수가 정적 도달 집합 밖이면 실패한다. 예외는 위의 동적 종류 셋(던더, 특징 표, cost model)뿐이다.
  - 이 교차 확인에서 처음에 `EventRecord.to_json` 누락이 잡혔다. 그래서 반환 주석과 리스트 필드 타입을 따르게 고쳤다.
  - 실행 한 번은 그 입력이 지나는 분기만 덮는다. 이것은 소스에 대한 증거이지 런타임 동작 전체에 대한 증명이 아니다.

### 5.3 F-11 잠금 범위 재확인

- `append_jsonl`은 꼬리 확인(`repair_jsonl_tail`)과 기록이 한 번에 이루어지지 않는다.
- 다음 순서가 생길 수 있다.
  1. 어떤 쓰기가 꼬리를 확인한다.
  2. 같은 파일의 다른 쓰기가 줄 중간에서 죽어 반쪽 줄을 남긴다. kill된 프로세스는 반쪽 바이트를 남기고, 그 flock은 커널이 푼다.
  3. 처음 쓰기가 자기 기록을 이어 쓴다.
- 이러면 그 기록이 반쪽 줄에 붙는다. 내부 손상 줄이 하나 생기고, 엄격 읽기가 전부 실패하며, 기록은 사라진다.
- 같은 파일의 판단 쓰기가 잠금을 잡아도, 단순 추가가 잠금을 잡지 않으면 이 순서를 막지 못한다. `_interleaving_writer` 테스트로 재현했다(`3d49e41`에서 4개 실패).

| 기록 | 경로 | 분류 | 처리 |
|---|---|---|---|
| `locked_window_campaigns.jsonl` | `CampaignLedger.record_result`, `abort_no_exposure` | 단순 추가. 판정 쓰기(`preregister`)와 같은 파일 | 원장 잠금 안으로(수정) |
| `validation_batches.jsonl` RESULT | `_evaluate_batch`(BLOCKED_COST와 정상 결과) | 단순 추가. 사전등록은 이미 잠금 안 | 배치 원장 잠금 안으로(수정). `_evaluate_batch`는 이 잠금을 잡은 채 불리지 않는다(중첩 없음) |
| `evaluations.jsonl` | `_append_eval` | 단순 추가. 다른 전략의 Paper Forward 계획들이 계획별 잠금만 잡고 동시에 결론을 낸다 | 파일 잠금 안으로(수정) |
| `research_feedback.jsonl` | `ForwardPaper._feedback_once` | 읽고, 판단하고("계획당 한 줄"), 쓴다. 여러 계획이 같은 파일에 쓴다 | 읽기·판단·추가를 한 잠금 안으로(수정) |

- 교착 검토: 새 잠금은 모두 그 안에서 다른 잠금을 기다리지 않는 짧은 잠금이다. 단, 상태·캠페인 원장은 기존 순서대로 잡는다. 긴 잠금(이력 → 배치, IW 이력 → 레지스트리)의 순서는 그대로다.
- 운영 전제: CLI(`runner.py`)는 상태 디렉터리당 `run.lock` 하나로 모든 쓰기를 직렬화한다.
- 라이브러리를 직접 쓸 때 보장되는 것:
  - 위 표의 파일
  - 이미 잠금으로 판단하는 파일(상태 원장, 사전등록, 계획 원장, contexts, 검증 창 이력, IW 레지스트리, 잠금 TEST 레지스트리)
  - 계획별 파일(`step.lock`)
- 보장하지 않는 것(남은 위험, 보고서 §G):
  - 같은 상태 디렉터리에서 두 인스턴스가 **같은 배치**를 동시에 평가하면 평가 기록·RESULT가 중복될 수 있다(`run_validation`의 "열린 배치가 있나" 판단은 잠금 밖). 기록이 바뀌거나 사라지지는 않고, 재개 대조가 불일치를 막는다.
  - `series`·`review` 기록은 파이프라인 전용 단순 추가로, `run.lock`을 전제로 한다.
  - `append_jsonl` 자체에는 잠금을 넣지 않았다. 모든 JSONL 옆에 잠금 파일이 생기고, 이미 잠금을 잡은 호출부와 같은 프로세스 안에서 다시 잡으면 교착이 생긴다. 그래서 FULL 검토된 `engine_lib.py`는 바꾸지 않았다.

### 5.4 사용자 결정

- 상태 원장에 `PAPER_PASSED`로 남은 과거 FNG 전략을 handoff 등 하류 문서에서 어떻게 표시할지. 지금은 `step()`과 `verify_conclusion()`만 미검증으로 표시한다.
- 같은 상태 디렉터리에 여러 인스턴스를 지원할지. 지원하려면 평가 단계 전체를 잠가야 한다.
