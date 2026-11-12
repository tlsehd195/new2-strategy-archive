# ADR 0078 — 저장소 전체 전수 감사와 통합 결함 수정 (2026-11-12)

- 상태: Accepted
- 날짜: 2026-11-12
- 요청: 동동, 2026-11-11 지시문 "NEW2 Strategy Archive — 저장소 전체 전수 감사 및 통합 결함 해결 프로젝트" §1–22
- 기준 커밋: `2d6616a` (main, PR #78 병합)
- 관련: ADR 0063–0077
- 상세 보고서(A–H): [`run/audit/FINAL_REPORT_20261112.md`](../../run/audit/FINAL_REPORT_20261112.md)

## 1. 범위

`main` `2d6616a`의 모든 실행 경로를 대상으로 했다. 데이터, 전략 생성·진화, 백테스트, 비용·Funding, 통계·다중검정, 포트폴리오, 상태·원장·복구·동시 실행, Paper Forward, 운영·CI·보안, 폐기 코드 분리가 포함된다.

- 추적 파일은 1,946개다. 그중 코드(`.py .sh .ts .yml`)는 272개다.
  - `run/discovery` 런타임 52개
  - `run/discovery/tests` 56개
  - `run/*.py` 137개
  - `run/audit/*.py` 17개
  - `src/` 8개
  - 워크플로 2개
- `data/`, `research/`, `results/`는 내용을 읽지 않았다. 존재와 경로만 확인했고, 보호 데이터는 쓰지 않았다.
- 검토는 영역별 독립 검토 에이전트 8개(A 데이터, B 진화, C 비용·Funding, D 통계, E 포트폴리오, F 상태, G Paper Forward, H 운영·CI)와 작성자가 나눠 맡았다.
  - 검토자가 "전체를 읽었다"고 한 것은 **검토자 주장**이다.
  - 작성자는 변경한 함수와 그 호출부만 다시 읽었다(PARTIAL).
  - 두 범위는 `AUDIT_PROVENANCE_20261111`에서 행을 분리해 기록했다. `AUTHOR_2026_11_12`가 작성자 행이다.

## 2. 결함과 근본 원인 (요약)

전체 표(ID, 심각도, 파일/함수, 문제, 근본 원인, 재현, 수정)는 보고서 §B에 있다. 근본 원인은 다섯 묶음이다.

1. **시간 경계가 두 곳에서 따로 계산됐다.** 이 원인에 속한 결함:
   - A-01 (Critical): `merged_bars`가 파일 선택용 월초(`d0`)를 날짜 컷에도 썼다. 그래서 잠금 TEST·paper replay 창이 선택·검증 데이터와 겹쳤다(BTC 잠금 봉의 22%).
   - A-09: 다음 봉 시가에 찍힌 정산을 결정 시점에 아는 것으로 처리했다.
   - D-05: CI가 검증 창이 아니라 첫·마지막 청산일 사이만 썼다.
2. **"없음"이 "0"이나 "정상"으로 바뀌었다.** 이 원인에 속한 결함:
   - ME-01/C-02 (High): 잠금 연구·paper replay가 펀딩 목록을 새로 만들면서 선언 주기를 잃어 `NOMINAL_8H_LEGACY`로 떨어졌다.
   - A-02: 상수 구간의 표준편차가 1e-20 수준이라 z 값이 나왔다.
   - A-06: FNG 값 범위·충돌 검사가 없었다.
   - G-02 (High): 손상 파일을 건너뛰고 뒤 파일을 전달해 영구 구멍이 생겼다.
   - G-01 (Critical): 데이터 구멍과 겹친 표본 거래를 조용히 빼서 손실 미결 포지션이 사라지고 `PAPER_PASSED`가 기록됐다.
   - E-02, E-06, C-03, C-05, C-06.
3. **크래시 지점마다 복구가 따로 구현됐다.** 이 원인에 속한 결함:
   - F-01 (High): 잠금 TEST 결과를 기록하기 전에 죽으면, 재시작 때 "이미 소비됨" FAIL_STAT으로 확정됐다.
   - F-02 (High): 캠페인 재개 때 잠금 기록·리뷰가 중복되고 `InvalidStatusTransition`이 반복됐다.
   - F-03 (High): DEGRADED replay 뒤 PAPER_FAILED를 잃었다.
   - F-04, F-05, F-06.
4. **공유 파일에 잠금 없이 썼고, 손상된 꼬리를 처리하지 않았다.** 이 원인에 속한 결함: F-07(검증 등록 원장), F-08(승격 큐), F-09(ProcessLock이 같은 PID를 살아 있는 것으로 봄), D-03(창 이력에 쓰지 않아 상태 디렉터리 사이에 알파 지출이 공유되지 않음).
5. **수치·식별 규칙이 계산과 어긋났다.** 이 원인에 속한 결함:
   - D-01 (High): `alpha*(B+1)<1`이면 임계값이 최대 뽑기로 고정돼 실제 수준이 약 1/(B+1)이었다.
   - D-02/D-06: DSR 시행 수가 현재 맥락·계산 가능 후보로 줄었다.
   - D-04: PBO가 T mod S행을 버렸다.
   - B-01/B-02: 효과 없는 파라미터 차이로 쌍둥이 가설이 생겼다.
   - B-03~B-07: 탐색 예산·라운드·계보 중복.
   - E-04/E-05: 청산가를 넘는 손절, 거절된 주문의 나머지 다리.
   - H-01~H-09: 신호·종료 코드·입력 검증·CI.

## 3. 결정 (공통 구조)

- **시간 컷은 한 곳에서 한다.** `merged_bars`는 `start_iso`부터 자른다. 월초는 파일 선택에만 쓴다. 품질 게이트는 자른 창에만 적용하고, 파싱할 수 없는 파일은 그대로 막는다.
- **Funding**
  - `calc_time`이 정시에서 `FUNDING_TOL_MS` 안이면 정시로 맞추고, 원래 값은 보고서에 남긴다.
  - 정산마다 주기를 두 해석(끝·시작)으로 판정한다. 모호하면 `SCHEDULE_UNKNOWN`이다(`funding-schedule-v2`).
  - 잠금·paper·비용 민감도 경로는 선언 주기를 넘긴다. 주기가 없으면 `require_funding`으로 막는다.
- **표준편차 0 판정은 하나로 통일했다.** `strategy_spec.nondegenerate_sd`(상대 1e-9)를 runner·strategy_spec의 모든 z 특징이 쓴다.
- **부트스트랩 해상도**
  - `stats_mc`는 `alpha*(B+1) < 5`면 `InsufficientBootstrapResolution`을 낸다.
  - 파이프라인은 창마다 `B = bootstrap_B_for(alpha, base_B)`를 사전등록에 기록한다. 그래도 부족하면 `BLOCKED_STATISTICS`이고, 기각하지 않는다(`mc-v4`).
- **알파 지출 공유**
  - `preregister_batch`는 평가 **전에** `validation_history`에 배치 행을 쓴다. 이때 `exclusive_file_lock`을 잡는다.
  - 크래시가 나면 과다 계산만 될 수 있다(유령 배치가 알파를 쓴다). 번호 재사용은 생기지 않는다.
- **DSR·PBO 시행 수**는 탐색 풀 전체(`dsr_trial_set`)다. 게이트 값은 `dsr_at_trials_searched`다.
- **복구는 "기록이 있으면 다시 적용"으로 바꿨다.**
  - 잠금 TEST: 레지스트리에 CONSUMED 결과가 있으면 그 결과로 최종 기록을 다시 만든다.
  - 캠페인: 같은 campaign_id의 잠금 기록과 리뷰를 재사용한다.
  - robustness: `_apply_robustness`.
  - paper replay: 기록된 실행을 `_apply_run`으로 다시 적용한 뒤 거절한다.
  - forward: DEGRADED replay가 있으면 등록을 거절한다.
  - reconcile: `enqueue_reverify`를 거친다.
  - IW: 등록됐지만 시작하지 않은 실행은 `--resume`으로만 이어 간다.
- **잠금과 JSONL**
  - `engine_lib.exclusive_file_lock`(flock)을 쓰고, `ProcessLock`은 `<path>.held` flock과 "pid 시작시각"으로 판단한다.
  - `process_lock_status`는 health·ledger_check와 공유한다.
  - 검증 등록 원장과 승격 큐는 `append_jsonl`/`scan_jsonl`을 쓴다. 꼬리 손상은 격리하고, 내부 손상은 실패로 닫는다.
- **Paper Forward**
  - 표본(시작~cutoff 진입) 거래가 데이터 구멍에 걸려 관찰할 수 없으면 `PAPER_BLOCKED_DATA`다(`UNOBSERVABLE_SAMPLE_TRADES`). 비최종이다.
  - `FileFeed`는 막힌 파일·충돌 봉부터 전달을 멈춘다(`held_back_from_ts`).
- **진화**
  - 효과 계산 키(`_effective_computation`)를 쓰고, `FROZEN_PARAMS`(`ret_z.min_history`)는 변이 대상에서 뺐다.
  - 예산은 탐색 풀로 센다. 라운드는 통째로 하고, 같은 계보 기록은 다시 쓰지 않는다(`fp-v3`, `evo-gen-v2`, `evo-pipeline-v2`).
- **포트폴리오**
  - 청산가를 넘는 손절은 청산 이벤트로 센다.
  - 진입하지 못한 주문의 나머지 다리는 `primary_not_entered`다.
  - 거절 비율을 모르면 판정하지 않는다(`portfolio-multi-v4`, `portfolio-mtm-v4`).
- **운영**
  - `ops.run_loop`가 사이클 사이의 SIGTERM/SIGINT를 받는다.
  - Runner는 종료할 때 신호 처리기를 되돌린다(`restore_signal_handlers`).
  - full 모드에서 legacy가 중단되면 evolve를 건너뛴다(rc 1). rc는 1(실패)이 우선한다.
  - newlisting은 discover가 계속 실패하면 실패로 끝난다.
  - `fetch_missing`·`fetch-data.yml`은 입력을 검증하고, 업로드 범위를 줄였다.
  - `run_cycle.sh`는 trap을 `mktemp`보다 먼저 건다.
  - CI는 빠른 검사를 먼저 돌리고, 타임아웃은 75분이다. 워크플로·`.gitignore` 변경도 CI를 돌린다.
- **감사표**
  - `source_audit_20261110.py`는 `reviewed_against.sha256`으로 검토 이후 바뀐 파일을 표시한다. 표시하지 않으면 check가 실패한다.
  - provenance는 검토자 실행(`executed_by_reviewer`)과 작성자 실행을 나누고, 재현 기록이 없으면 `None`(미기록)으로 둔다.
  - 등급은 올리지 않았다.

## 4. 결과에 미치는 영향 (호환성)

- 버전이 바뀌었다: datapol-v2, funding-schedule-v2, mc-v4, evo-pipeline-v2, fp-v3, evo-gen-v2, power-analysis-v2, portfolio-multi-v4, portfolio-mtm-v4.
  - 맥락 ID(context_id)에 코드 해시와 버전이 들어가므로 기존 결과와 섞이지 않는다.
  - 기존 원장·상태·평가 결과 파일은 손대지 않았다.
- `ProcessLock` 파일 내용이 "pid 시작시각"으로 바뀌었다.
  - 옛 형식(pid만)이고 그 pid가 살아 있는 다른 프로세스면 살아 있는 것으로 본다. 보수적인 처리다.
- `fills_import.load_decisions`의 키는 `(SYMBOL, order_id)`다. 예전 `{order_id: row}` 형태도 받는다. 행의 symbol로 키를 다시 만든다.
- 기존 테스트는 지우거나 완화하지 않았다. 픽스처 4곳만 바뀌었다.
  - 테스트 픽스처에 선언 펀딩 주기를 넣었다.
  - 4h→8h 변경 테스트는 이제 SCHEDULE_UNKNOWN을 기대한다. 두 해석 판정 때문이다.

## 5. 수정하지 않은 항목 (사유)

| ID | 분류 | 사유 |
|---|---|---|
| E-01 | ACCEPTED_LIMITATION (사용자 결정 필요) | `base` 시나리오(1x, position_fraction 1.0)에서 겹치는 두 번째 다리가 증거금 부족으로 거절되면 UNDETERMINED가 된다. 실패를 닫는 방향이라 잘못된 PASS는 없다. 바꾸려면 포트폴리오 설정의 새 버전이 필요하다 |
| ME-04 | ACCEPTED_LIMITATION (사용자 결정 필요) | ETH DEV가 BTC 잠금 기간을 덮는 교차 심볼 영향. ADR 0010에 기록돼 있다 |
| A-05 | ACCEPTED_LIMITATION | taker 열이 없으면 0이 된다. 그래도 그 특징으로는 PASS할 수 없다. 실제 파일에는 열이 있다 |
| B-06(일부) | ACCEPTED_LIMITATION | legacy id의 심볼 순서·정수/실수 표기를 바꾸면 기존 legacy id가 모두 바뀐다. -0.0만 수정했다 |
| C-04 | ACCEPTED_LIMITATION | 손으로 쓴 VERIFIED 기록. 실행 경로의 호출자가 없다. 재계산 검증은 실계좌 체결이 생긴 뒤 한다 |
| C-07 | ACCEPTED_LIMITATION | 공개 추정치는 연구 전용이다(VERIFIED 아님) |
| H-10 | INTENTIONALLY_RETIRED | `git_backup_push`는 LEGACY_SCRIPTS.md에서 DEPRECATED다 |
| A-10, A-11, B-08, C-08, E-07, E-08, F-11, G-03, G-04, H-11, H-12 | POSSIBLE | 재현하지 못했다. 보고서 §B의 미확인 위험 목록에 둔다 |

## 6. 테스트

- 신규: `run/discovery/tests/test_integrated_audit_20261112.py` (55개). 합성 데이터·임시 디렉터리·mock만 쓴다.
  - 수정 전 `2d6616a`에서 52개가 실패한다(결함 재현).
  - 나머지 3개는 대조군이다(갭 없는 손실 포지션 → PAPER_FAILED, 내부 손상 → RegistryCorrupt, 정시 정산의 보유 규칙).
  - 수정 후에는 55개 모두 통과한다.
- 전체 결과(3.11 / 3.13)와 GitHub Actions 결과는 보고서 §E·§G에 있다.

## 7. 외부 증거가 필요한 문제 (코드로 해결했다고 주장하지 않음)

- 비용 VERIFIED: 실계좌 체결 200건/심볼 이상이 필요하다. OKX 기록은 DEMO라 쓸 수 없다.
- 검정력: SR 4에서도 80%에 미치지 못한다(ADR 0064). 전략 생존 0은 그대로다.
- 독립 검증 창(IW1), 잠금 TEST, 전진 관측은 열지 않았다. 사전등록도 활성화하지 않았다.
