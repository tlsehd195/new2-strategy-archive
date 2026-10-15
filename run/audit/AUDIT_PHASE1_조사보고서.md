# NEW2 자율 전략 탐색 엔진 — 감사 Phase 1 조사 보고서 (재수행판)

- 작성: 2026-10-15, Task 55 (감사 프로젝트 Phase 1 — 코드 수정 전 조사)
- 감사 대상: 런타임 `/home/z/my-project/NEW2_run` + GitHub `tlsehd195/NEW2`(공개) + `tlsehd195/new2-strategy-archive`(비공개, Deploy Key 읽기 전용)
- 엔진 코드 기준: 아카이브 커밋 `daa5ca6`(2026-10-14 14:56Z)에서 복원한 **engine-discovery-v1** (4핵심파일 바이트 검증 일치)
- 원칙 준수 선언: 본 보고서 작성 시점까지 **코드 0줄 수정, 장부 0행 수정**(복구 목적의 파일 복원·장부 병합만 수행 — Task 54 참조)

---

## A. 요약 (먼저 읽을 부분)

**3건의 중대한 환경 사실**이 이번 감사의 전제다:

1. 플랫폼 디스크 복원이 10-14 당일 **2회**(13:05Z, ~17:49Z), 10-15 새벽 **1회**(3차) 발생 — 현재 샌드박스는 10-13 20:51Z 스냅샷 기준
2. 이전 환경에서 감사 프로젝트가 **1회 완주**되었으나(Task 49~51: 이전 지적 25건 판정, 16건 수정, 14종 테스트 통과, 엔진 v2-audit2), 그 산출물은 **GitHub에 push된 적이 없어 3차 복원으로 영구 소실** (F-26)
3. 본 감사는 v1 코드 기준으로 **재검증·재구현**을 수행한다. v2-audit2의 게이트 규격(R4~R8 개정 전문)은 PREREG_discd008~016 86건에 문서화되어 있어 재구현 근거로 사용한다

**코드 930줄 전수 정독 + 실측 결과 (요약):**

| 구분 | 수치 | 근거 |
|---|---|---|
| 유니버스 후보 수 | **779** (flow_z 360·ret_rev 120·ts_mom 60·vol_break 60·tod 60·fund_z 45·fng 74) | generator.py 코드 직접 실행 재계산 — 문서 주장과 정확 일치 |
| 원장 행 수 | 62 (전부 base) | discovery_ledger.jsonl 실측 |
| 고유 후보 해시 | 53 (중복 9쌍 — 사이클 2·3·4의 seq 1↔8, 2↔9, 3↔10) | 실측 — 초기 next_batch 버그 잔재, 현행 코드는 proposed-set으로 수정 완료 상태 |
| 판정 분포 | FAIL_COST 56 · UNDETERMINED_POWER 6 | 실측 |
| 아카이브 결과 JSON | 150건 (FAIL_COST 115 · UNDETERMINED_POWER 18 · DATA_MISSING 16 · FAIL_CONSIST 1) | 아카이브 results/ 실측 — 사이클 8~16 원장 행 재구성 가능 |
| 데이터 결함 | ADA/LINK 전무, 알트 6종 25개월 구멍, BTC·ETH 잠금TEST 내부 결측, zip 전용 월 로더 무시 | §11 실측 |

---

## 1. 현재 저장소의 디렉터리 구조

### 1.1 세 개의 층위

| 층위 | 경로 | 내용 | 비고 |
|---|---|---|---|
| **런타임** | `/home/z/my-project/NEW2_run/` | data(301MB: klines·funding·bybit·cme·coinbase·dvol·okx·premium·qfut·spot15m·spot1h·kp) · discovery(엔진 v1, Task 54에서 복원) · research · results · audit · PREREG_*.md | 10-13 스냅샷 기준 + 10-15 복원분 |
| **원본 코드** | GitHub `tlsehd195/NEW2` (공개, main=8ec6371) | src/cointrader(508파일·215 py) — 백테스트·페이퍼 엔진 원본. `src/cointrader/backtest/costs.py`가 비용 모델의 원형 | 로컬 미클론, gh/main으로 fetch해 조사 |
| **오프사이트** | GitHub `tlsehd195/new2-strategy-archive` (비공개, main=92716f6, 16커밋) | run/(PREREG 140+·백업 스크립트) · docs/(보고서 v3~**v29**·worklog Tasks 1~53) · results/(488파일, disc_* 150JSON) · data/(liquidations.db 등 장부 3종) · src/(미니서비스 3종 소스) · research/ | **HEAD에는 엔진 코드 없음** — 710f558·daa5ca6 이력에만 존재 |

### 1.2 런타임 discovery/ (엔진 디렉터리, 복원 후)

```
NEW2_run/discovery/
├── engine_lib.py   415줄  공통 라이브러리 (데이터 로더·시그널·비용·통계·게이트)
├── generator.py    243줄  후보 생성기 (문법 그리드·커서·중복제거·조합 생성)
├── runner.py       355줄  사이클 오케스트레이터 (PREREG→백테스트→게이트→장부→커서)
├── run_cycle.sh     24줄  셸 래퍼 (락·로그)
├── state.json             사이클 상태 (복원분: cycle 7, tested 62)
├── discovery_ledger.jsonl append-only 원장 (복원분: 62행)
├── cycles.md              사이클 다이제스트 (복원분: cycle 7까지)
├── promotion_queue.md     승격 큐 기록 (빈 상태로 복원)
└── cycle_log.txt          래퍼 로그
```

### 1.3 아카이브 구조 특이점

- 이전 검토가 가정한 `run/discovery/...` 경로는 **HEAD 트리에 없음**(bf177c9에서 삭제 — F-26). `git ls-tree daa5ca6`에 9파일 존재 → 이력에서만 복원 가능
- README·HANDOVER(루트+docs)도 bf177c9에서 삭제 → f2ebc27/daa5ca6에서 복원하여 `NEW2_run/*_archive.md`로 보관
- docs/worklog.md(199,790B)는 **Tasks 1~53의 완전판** — 로컬 장부(1~34+DISC-auto)의 상위집합임을 프로그램으로 검증 후 병합 완료

## 2. 자율 전략 탐색 엔진의 진입점

- **셸 진입점**: `bash /home/z/my-project/NEW2_run/discovery/run_cycle.sh` — 유일한 크론 호출점
- **Python 진입점**: `runner.py main()` (`if __name__ == "__main__"`) — fatal 예외 시 errors.log 기록 + `CYCLE_FAILED` + exit 1
- **라이브러리 진입점**: `engine_lib.py`(순수 함수 집합, 실행 부 없음)·`generator.py`(next_batch/combo_candidates/stouffer_pair_candidates)
- v1에는 테스트 진입점이 **없다** (§10 참조)

## 3. 실행 순서와 모듈 간 의존성

```
run_cycle.sh
  └─ mkdir /tmp/new2_disc.lock (락) → trap rmdir
  └─ python3 runner.py 2>&1 | tee -a cycle_log.txt | tail -15   ← 파이프 종료코드 유실 (F-04)
       runner.main()
         ├─ engine_lib 임포트 (경로 상수·로더·통계) ← 모든 모듈이 의존
         ├─ generator 임포트 (후보 생성)
         ├─ load_state() → state.json (비원자 저장 — F-09)
         ├─ G.next_batch(state, BATCH=10)      ← load_tested_hashes()로 원장 전체 스캔
         ├─ G.combo_candidates(state, tested, 4) (생존자 있을 때만)
         ├─ 루프: process_candidate()
         │    ├─ write_prereg() → NEW2_run/PREREG_disc{cid}_{date}.md  (선)
         │    ├─ run_candidate() → engine_lib.sig_*·event_study·cell_stats (후)
         │    ├─ verdict_from_cells() → 게이트 판정
         │    ├─ results JSON/MD 저장 → results/disc_{cid}_results.*
         │    ├─ append_ledger() → discovery_ledger.jsonl (장부가 유일 진실)
         │    ├─ 커서 전진 (base만, fam_idx+1)
         │    └─ SURVIVOR → survivors/survivor_specs/promotion_queue + promote_note()
         ├─ run_stouffer() (생존≥2) → z_pool_stouffer ρ max (F-06/F-07)
         └─ save_state()·cycles.md digest·print CYCLE_OK
```

- 순환 의존 없음. `engine_lib ← generator ← runner` 단방향. run_cycle.sh→runner.py만 셸 의존
- 크래시 안전 설계: next_batch는 커서를 건드리지 않고, 장부 append 성공 후 커서 전진 (generator.py:11-13 문서화 + runner.py:242-244 구현 일치 ✓)

## 4. 데이터가 생성되고 저장되며 소비되는 과정

| 단계 | 실제 구현 | 위치 |
|---|---|---|
| 원본 수집 | **엔진 밖** (수동/별도 스크립트 — Binance fapi 월별 15m klines·funding CSV 수동 캐싱) | NEW2_run/data/{klines,funding}/ |
| 검증·중복·정렬 | 로더가 **파편적**으로 일부 수행: open_time 수치 coerce·drop_duplicates·sort (engine_lib.py:95-101, 110-116) | engine_lib.load_klines/load_funding |
| 품질 검사 | **없음** (갭·OHLC 이상·최신성 무검증 — 결측 월은 조용히 건너뜀) | 없음 → Phase 5 과제 |
| 저장 | 정적 CSV/JSON 캐시 | NEW2_run/data |
| 소비 | 엔진 로더 **캐시 전용** — 다운로드 0바이트 원칙(engine_lib.py:19) | engine_lib.load_* |
| 실시간 수집 | 별개 미니서비스(liq-collector 3020·fng-watcher 3021·maker-fill-monitor 3022) — **엔진 입력과 미연결** | mini-services/, src/ |

**핵심 한계(이전 지적 §9 확인)**: REFRESH=1은 run_cycle.sh:20-22에서 "무시" 로그만 출력. 데이터 최신성·완전성 보장 장치가 없어 아래 §11의 결측이 **무음으로** 게이트 통계에 반영된다.

## 5. 후보 전략의 생성부터 최종 검증까지의 전체 흐름

1. **생성**: 문법 그리드 7패밀리 × 5 심볼 페어(PAIRS) × 파라미터 → 779 (코드 재계산 일치)
   - fng는 F2/X6 사전 측정분 배제: (BTC,ETH)페어 × (10,90)/(20,80) × h{4,16,48} 6건 제외 → 80−6=74 (generator.py:24-26,92-97)
2. **중복 제거**: 원장 cand_hash(sha1[family+symbols+params], 16hex) — engine_lib.py:75-78
3. **배치**: 패밀리 라운드로빈, 커서 기반, proposed-set으로 사이클 내 중복 차단 (generator.py:119-147)
4. **PREREG 사전등록**: 백테스트 **이전** 규격 고정 + sha256 원장 기록 (runner.py:95-125, 236) ✓ 무결성 설계 양호
5. **이벤트 스터디**: 신호봉 i → 진입 i+1 시가 → 청산 i+1+h 시가, 연속성 fail-closed, 잠금 구간 **진입** 배제 (engine_lib.py:253-285)
6. **게이트(엄격 순서)**: POWER(vn≥30 비중첩) → COST(o10k 순수익>0) → CONSIST(2구간 부호+) → STAT(후보 내 Holm p<0.05 ∧ 수축 mean−2.2414·se>0) (engine_lib.py:376-399)
7. **원장 기록**: append-only JSONL — 판정 포함 전부 기록 (ERROR/DATA_MISSING도 정직 기록 ✓)
8. **조합 단계**: 생존자 발생 시에만 섭동/필터 변형(실제 재검증 수행) + Stouffer 결합(통계량만 결합 — §7 문제)
9. **최종 검증**: **미구현** — 승격 큐 적재 후 2027-01-11/2027-07-01 잠금 TEST 수행 코드 없음 (R3: 수동 판정 설계 — 자동화하더라도 별도 구현 필요, §20)

## 6. 상태 파일과 실험 원장의 역할

- **discovery_ledger.jsonl (유일한 진실)**: 후보 정의·PREREG sha256·판정·best_cell 전부 기록. append-only. 크래시 후 커서·테스트 이력 전부 원장에서 재유도 가능한 설계 ✓
- **state.json (파생 캐시)**: cycle·cursors·tested·survivors·survivor_specs·stouffer_done·promotion_queue·last_ts·last_status. runner가 매 후보마다 저장(비원자 — F-09). **원장과 정합성 검증 장치 없음** → 불일치 시 무음 발산 (F-14)
- **실측된 정의 불일치(F-23)**: `st["tested"] += 1`은 행 수 카운트(runner.py:245), `CYCLE_OK tested=N`은 고유 해시 수(runner.py:334,343) — 동일 사이클 내 두 숫자가 서로 다른 정의. Task 53 기록 "tested=126"은 고유 수, "tested 116"(cycle 15)은 행 수였을 가능성 — v2에서 분리 필요(원장 62행=53유니크 실측으로 재확인됨)
- **promotion_queue**: state.promotion_queue(cid 배열) + promotion_queue.md(사람용 append) 이중 기록 — R3(자동 승격 금지) 준수 ✓

## 7. 자동 실행 및 스케줄러의 연결 구조

**실측 가능한 것**:
- 본 샌드박스의 크론(Job 459256, 3시간 주기)이 run_cycle.sh 호출 — 오늘 04:49Z 발화 실측(당시 인프라 부재로 실행 불가였고, 복구 후 재개 가능)
- v1 run_cycle.sh 락: /tmp/new2_disc.lock — 동시 실행 시 `exit 0`(메시지 "CYCLE_SKIPPED") — **명세 종료코드 3과 불일치**(F-35), SIGKILL 시 락 잔존(F-05)
- 이전 환경 기록(원장, 검증 불가 부분 명시): 백업 크론 458723(매일 09:00 KST)·탐색 크론 458843 존재했다가 Task 48에서 458843 임시 삭제→Task 51 재등록(=459256으로 추정). **본 환경에서 458723의 생존은 확인 불가** — 크론 목록 실측 결과 본 세션에서 확인된 것은 459256뿐. 미확인 항목은 "확인 불가"로 기록한다

**구조적 한계**: 종료코드 체계가 파이프라인에서 유실(F-04)되어 스케줄러가 실패를 감지할 수 없다. 백업 크론과 탐색 크론의 상호 인지 장치 없음.

## 8. 현재 실제 실행 환경과 보관 저장소의 차이

| 항목 | 런타임(현재) | 아카이브 HEAD(92716f6) | 아카이브 이력 |
|---|---|---|---|
| 엔진 코드 | v1 복원 완료(cycle 7 상태) | **없음**(F-26 삭제) | 710f558·daa5ca6 = v1 |
| worklog | Tasks 1~34+DISC-auto → **병합 완료: 1~53+DISC-auto** | docs/worklog.md Tasks 1~53 | 동일 |
| 보고서 | v3~v26 | v3~**v29** | v27~29는 아카이브에만 존재 → 로컬 미복원(용량·판단 보류, 복원 권장) |
| PREREG | 29건(구형) | +discd 150건대(86+64) | 완전 |
| results disc_* | 0건 | **300파일(150 JSON)** | 재구성 1차 증거 |
| 데이터 캐시 | 301MB 생존 | 미포함(용량) | — |
| v2-audit2 산출물 | **영구 소실** | **없음(push 이력 자체가 없음)** | 복구 불가 → 재구현 |

## 9. 기존 문서와 실제 코드의 불일치

1. **GATES_TEXT "UNDETERMINED_POWER — 재검증 큐"** (runner.py:51) vs **큐 구현 부재** (generator.load_tested_hashes가 전 행 무조건 tested 처리, generator.py:104-116) — 문서만 있는 기능. 확인.
2. **"REFRESH=1(데이터 갱신)"** 주석 (run_cycle.sh:8) vs **무시** (run_cycle.sh:20-22) — 오해 유발.
3. **state.engine 라벨**: state.json="NEW2-discovery-v1", results JSON="NEW2-discovery-v1" 하드코딩 3곳(runner.py:76,223,281) — 버전 갱신 누락 위험(F-25 후속으로 이전 감사가 매 사이클 갱신으로 수정했다고 기록 — v1엔 없음).
4. **README(HANDOVER)**: 아카이브 README가 `ledgers/·prereg/` 구조를 설명하나 실제는 `data/·run/`으로 재편(3d565f9 cleanup) — 문서 미갱신. 또한 두 문서 모두 원격 HEAD에서 삭제됨(F-33).
5. **measurement_queue_status.md**(구 수동 루프)와 자율탐색 큐의 혼동 가능성 — 구분 문서 필요(§19).
6. **크론 명세 종료코드**(0/2/1/3) vs v1 스크립트(SKIPPED=0, PARTIAL 개념 없음) — F-35.

## 10. 기존 테스트의 종류와 테스트 범위

- **v1 엔진 자체 테스트: 0건** (discovery/tests/ 부재 — 아카이브 전체 히스토리에도 없음)
- 이전 환경의 14종 테스트 스위트(test_engine_v2.py)는 소실 — 존재했다는 기록만 worklog에 남아 있음. **"테스트가 있었다"는 기록을 테스트 완료로 간주하지 않는다** — 본 감사에서 재작성
- 원본 NEW2 저장소의 테스트(tests/)는 원본 프로그램용으로 존재하나 엔진 커버 없음
- 수동 검증 기록: 이전 감사가 실시한 실측(룩어헤드 조작 테스트·state 파손 복구 등)은 worklog에 요약만 존재하고 재현 수단 소실

## 11. 실행에 필요한 외부 데이터 및 환경 의존성 (+데이터 실측)

**환경 의존성**: python3(pandas·numpy·scipy), 절대 경로 `/home/z/my-project/NEW2_run`(engine_lib.py:31-34, run_cycle.sh:17 — F-11), 캐시 파일 존재 전제, /tmp 락. 원본 costs.py는 런타임에 **미존재**(엔진은 공식만 복제 — §12 비교).

**데이터 커버리지 실측 (신규 결함 다수):**

| 심볼 | klines csv | 갭 | 펀딩 csv | 갭 |
|---|---|---|---|---|
| BTCUSDT | 64개월 2021-04~2026-09 | **2024-04~05 결측 = 잠금 TEST 구간 내부**(F-28) | 30개월 **2024-03에서 종료** | 2022-09~2023-02 + **잠금TEST·현대 구간 전무**(F-30) |
| ETHUSDT | 57개월 | **2026-05~08 결측 = 잠금 TEST 구간 내부**(F-29), 2024-05~10 | 35개월 2021-04~2026-04 | 2022-09~2024-10 |
| SOL·XRP·BNB·TRX·ZEC·DOGE | 각 41개월 | **2022-10~2024-10 (25개월!) — ALT_SPANS 경계(2023-06-01) 가로지름**(F-27) | 각 41개월 | 동일 25개월 |
| ADAUSDT·LINKUSDT | **0파일** (F-24 재확정 — 페어 3·4 전체 DATA_MISSING) | 전무 | **0파일** | 전무 |

추가: **zip 전용 월**(BTC 10개월·ETH 14개월)은 로더 glob(`*.csv`)이 무시 — 데이터 존재하지만 미사용(F-31). FNG 3,172일(2018-02~2026-10-12) 정상, D-1 매핑 룩어헤드 없음(코드 재검증 ✓).

**파급 효과**: F-27로 알트 CONSIST 게이트는 **2021-05~2022-09 vs 2024-11~2026-04**라는 비인접 레짐 비교가 됨(설계 의도와 다름). F-28/29로 2027 잠금 판정 자체가 현재 데이터로는 **실행 불가**.

## 12. 기존 문제를 재현하기 위한 테스트 계획

Phase 2~7에서 작성할 자동화 테스트(합성 데이터 기반, 실측 시장 데이터로 수익성 주장 아님을 명시):

| # | 테스트 | 재현 대상 |
|---|---|---|
| T1 | state.json 파손 주입 → 원장 기반 재구성 → 정합 | F-09/F-14 |
| T2 | 락 점유 상태 실행 → skip 종료코드 3 | F-05/F-35 |
| T3 | 스테일 락(PID 사망+4h) → 회수 후 정상 실행 | F-05 |
| T4 | python exit 2 주입 → run_cycle.sh 종료코드 전파 | F-04 |
| T5 | bogus family 주입 → ERROR 행 기록 + CYCLE_PARTIAL(exit 2) + 전체 비정상 아님 | F-08 |
| T6 | 유니버스 779 재계산 + 해시 유니크성(동일 파라미터=동일 해시, 1축 변경=다른 해시, variant 포함) | §12 후보ID |
| T7 | 심볼별 합격 조건: 1심볼 통과·1심볼 반대 → 불합격(또는 명시적 분리) | F-02 |
| T8 | Holm·BH-FDR 수학 단위검증 | F-01 |
| T9 | Stouffer: ρ 단조성·이중계수 제거 후 기댓값 일치 | F-06/F-07 |
| T10 | 펀딩 보유비용 수학(방향·부호·구간) | F-16 |
| T11 | 룩어헤드: 신호봉 이후 데이터 조작 → 신호·진입 불변 | §10.1 |
| T12 | 잠금 구간 진입 배제(경계 반개구간) | R규칙 |
| T13 | 원자적 저장(os.replace)·재시작 후 큐 유지 | F-09/F-03 |
| T14 | 데이터 품질 보고(갭·중복·OHLC 위반 탐지) | §9.2 |

---

## F. Finding 종합 판정표

### 이전 검토 지적사항 대조 (코드 근거와 함께 재판정)

| ID | 제목 | 코드 근거(파일:함수/줄) | 판정 |
|---|---|---|---|
| F-01 | 탐색 전체 다중검정 미통제(Holm이 후보 내 소수 셀에만) | engine_lib.py:381-385 — holm(ps) 입력=후보 내 셀(최대 2심볼×h), 전체 779 순차 탐색과 무관 | **확인** |
| F-02 | best 셀 단독 합격(2심볼 중 1심볼만 통과 가능) | engine_lib.py:386-397 — `best=max(fin,key=t)` 후 전 게이트를 best에만 적용 | **확인** |
| F-03 | 재검증 큐 부재(문서만 존재) | generator.py:104-116, runner.py:51 | **확인** |
| F-04 | pipefail 부재 → python 실패 은폐 | run_cycle.sh:24 — `python3 … \| tee \| tail` 마지막 종료코드 | **확인** |
| F-05 | 스테일 락 무처리 | run_cycle.sh:10-15 — mkdir/rmdir trap, PID·수명 없음 | **확인** |
| F-06 | Stouffer ρ max 선택(유리한 ρ 선택) | runner.py:272-274 — ρ∈{0,0.119,0.238,0.5} 중 max | **확인** |
| F-07 | Stouffer w=√vn·t 이중계수(t가 이미 √n 스케일) | engine_lib.py:402-415 | **확인** |
| F-08 | 후보 단위 오류가 전체 상태에 미반영 | runner.py:200-216(예외 흡수→ERROR 행→계속), 330-331(`last_status="ok"`, CYCLE_OK 고정) | **확인** |
| F-09 | state.json 비원자 저장 | runner.py:82-83 — 직접 덮어쓰기 | **확인** |
| F-11 | 하드코딩 절대 경로 | engine_lib.py:31-34, run_cycle.sh:17 | **확인** |
| F-15 | 알트 잠금/독립 검증 구간 부재 | engine_lib.py:53-58 — LOCKED는 BTC·ETH만, ALT_SPANS 공용 | **확인** |
| F-16 | 펀딩 보유 비용 미반영 | engine_lib.cost_vec — fee+spread+impact만, fund_z는 신호로만 사용 | **확인** |
| F-22 | 룩어헤드 | 코드 정독: 신호 i는 i봉까지 정보만 사용, 진입 i+1 시가 — **편향 없음**(이전 감사 기각·유지) | **기각 유지** |
| F-23 | tested 정의 불일치(행 수 vs 고유 수) | runner.py:245 vs 334,343 | **확인** |
| F-24 | ADA/LINK 데이터 소실 | §11 실측 — 0파일(3차 복원으로 재소실) | **확인(재발)** |
| F-12(해시) | combo 해시가 실제 파라미터 미반영 | generator.py:198-216 — 해시 payload에 variant 태그 포함, delta→q 유도 가능 | **기각 유지**(설계 취약성은 Phase 3에서 개선) |
| F-25 | 백업 push 성공 오판 | 아카이브 스크립트는 이미 수정판(실측 대조) — 현재판 코드에서 재현 불가, 이력 사고로 기록 유지 | **기각(수정 이력 존재)** |

### 신규 발견 (본 감사)

| ID | 제목 | 심각도 | 근거 |
|---|---|---|---|
| **F-26** | 백업 allow-list가 `NEW2_run/discovery/`·`audit/`·tests를 수집하지 않음 → 아카이브 HEAD에서 엔진 무음 삭제(bf177c9) 및 v2-audit2 영구 소실 | **치명** | run/git_backup_push.sh(cp 대상 목록), git ls-tree 실측 |
| **F-27** | 알트 6종 klines 25개월 구멍(2022-10~2024-10) — 개발 구간 경계 가로지름 → CONSIST 게이트가 비인접 레짐 비교로 왜곡 | **높음** | §11 실측 |
| **F-28** | BTC klines 2024-04~05 결측 = **잠금 TEST 구간 내부** → 2027-01-11 BTC 판정 실행 불가 | **높음** | §11 실측 |
| **F-29** | ETH klines 2026-05~08 결측 = **잠금 TEST 구간 내부** → 2027-07-01 ETH 판정 실행 불가 | **높음** | §11 실측 |
| **F-30** | BTC funding csv가 2024-03에서 종료(+29개월 zip 미사용) → 최근 구간 펀딩 비용·신호 불가 | 중간 | §11 실측 |
| **F-31** | zip 전용 월(BTC 10·ETH 14) 로더 무시 — 데이터 존재·미사용 혼란 | 낮음 | loader glob |
| **F-32** | results JSON에 cand_hash·cycle 필드 없음 → 원장 재구성 시 재계산 의존 | 낮음 | runner.py:219-224 |
| **F-33** | README·HANDOVER가 원격 HEAD에서 삭제(bf177c9) — 복원자 문서 상실 | 중간 | git 이력 실측 |
| **F-34** | cell_stats의 o100k 참고평균이 ok100 하위집합 한정 → 시나리오 내 생존편향 참고치 | 낮음 | engine_lib.py:371-372 |
| **F-35** | SKIPPED 시 exit 0 — 크론 명세(3=SKIPPED)와 불일치, CYCLE_PARTIAL 개념 부재 | 중간 | run_cycle.sh:13 |
| **F-36** | 백업 크론(458723)의 본 환경 생존 **확인 불가** — 백업 연속성 단절 가능 | 높음(환경) | 크론 목록 실측 한계 명시 |

## G. 복구 계획 (Phase 1-g, 코드 수정과 분리되는 데이터 복구)

아카이브 results/disc_*_results.json 150건(1차 증거) + PREREG 150건(sha256 재계산 가능)으로 사이클 8~16의 소실 원장 행을 **재구성**한다:

1. 62행 기존 원장 유지(무수정) + 미포함 cid만 추가(append-only)
2. 각 행: cid→cycle/seq 역산, cand_hash 재계산(base: (family,symbols,params) 결정론), prereg_sha256은 아카이브 PREREG 바이트로 재계산, verdict·cells는 결과 JSON 그대로, best_cell은 cells에서 결정론 재유도
3. 재구성 행에 `recovered_from: "archive-results-json"` 표기(과거 행과 구분 — 원칙 10·22 준수)
4. state.json 재구성: cycle=16, cursors=패밀리별 최종 base 후보의 fam_idx+1, tested(유니크)·runs(행) 분리 기록, survivors/queue=빈(실측)
5. 검증: 재구성 후 `next_batch`가 779 유니버스에서 소실 0건(proposed 전부 스킵) 확인 + 원장 행↔결과 JSON 1:1 대조 + PREREG sha256 전수 일치

단, 이 작업은 **수정 전 조사 보고서 확정 후**(본 문서) 실행하며, 실행 전 장부 백업 스냅샷을 남긴다.

## H. 다음 단계 (Phase 2~7)

1. **Phase 2** 실행 안정성: F-04(pipefail+PIPESTATUS)·F-05(PID 락+스테일 회수+exit 3)·F-08(CYCLE_PARTIAL)·F-09(원자적 저장+원장 기반 reconcile)·F-11(NEW2_ROOT 환경변수)·**F-26(백업 allow-list에 discovery/·audit/·tests 추가 — 최우선)**
2. **Phase 3** 후보 관리: 재검증 큐(UNDETERMINED_POWER·DATA_MISSING·ERROR 정책)·cand_id/run_id 분리(F-23)·고갈 처리(UNIVERSE_EXHAUSTED)·F-32
3. **Phase 4** 통계: F-02(심볼 정합 R4)·F-01(BH-FDR R5)·독립 검증 구간 진단 R6·F-06/F-07(Stouffer 실측 ρ+가중치 개정 R7) — v2 규격은 PREREG 문서에 존재하므로 그대로 재구현
4. **Phase 5** 데이터: ADA/LINK 재다운로드(F-24)·알트 구멍 메우기(F-27)·잠금 TEST 구간 보강(F-28/29)·funding 보강(F-30)·zip 해결(F-31)·품질 검사기·data_version
5. **Phase 6** 백테스트: F-16(펀딩 보유비용 R8)·포트폴리오 시뮬레이터(슬롯·중복·MDD)·F-34·비용모델 시장 불일치 문서화(원본 costs.py 대조 완료: 원본=Upbit KRW 현물 기본값, 엔진=왕복 변환 — 보수적 방향이나 근거 없는 추정임을 명시)
6. **Phase 7** 테스트 14종 재작성·문서 동기화(README·HANDOVER 복구 포함)·최종 감사 보고서(A~H 규격)

각 Phase 종료 시 변경사항·테스트 결과를 본 장부(worklog)와 감사 보고서에 기록하고, **모든 산출물을 즉시 개정 백업 스크립트로 아카이브에 보호**한다.
