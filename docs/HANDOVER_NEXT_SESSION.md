# NEW2 다음 세션 인수인계서 (HANDOVER)

> **작성일**: 2026-10-14 · **작성 주체**: 당시 작업 에이전트 (Task 46) · **최종 갱신**: 2026-10-14 자율탐색 엔진 신설(Task 47, §12)
> **이 문서의 위치가 중요합니다**: 이 문서는 GitHub 저장소 `tlsehd195/new2-strategy-archive`에 영구 보관됩니다.
> 샌드박스가 통째로 사라져도 **이 저장소만 살아 있으면 프로젝트 전체를 복원할 수 있도록** 쓰였습니다.
> 다음 세션(에이전트)은 **반드시 이 문서를 처음부터 끝까지 읽고 시작**하십시오.

---

## 0. 30초 요약 (바쁜 다음 세션을 위해)

- **이 프로젝트는 무엇인가**: 크립토 선물 "실전 승격 가능 전략"을 엄격한 사이언스 파이프라인으로 발굴하는 연구. **29건 측정 전부 FAIL/폐기** — 원인은 비용 벽. 루프는 **종료 판정 유지** 중이며, 승격 판정은 **2027-01-11 / 2027-07-01**.
- **지금 무엇을 하고 있는가**: 3개 상시 데이터 수집기 + 매일 자동 백업(로컬+이 저장소) + **자율탐색 엔진(3시간 주기 크론 Job 458843)**이 돌아가는 중. **세션이 임의로 새 측정을 시작하지 마십시오** — 신규 탐색·검증·조합은 자율탐색 엔진(§12)이 자동 수행합니다. 세션은 엔진 결과 모니터링과 장부 무결성 유지만 담당합니다.
- **다음 세션이 처음 해야 할 일**: `worklog.md` 마지막 Task ID 확인 → 서비스 생존 확인(§5) → 백업 로그 확인(§6) → 재개 트리거 체크(§8). 그 외 아무것도 하지 마십시오.

---

## 1. 프로젝트 사명과 규칙

### 1.1 사명
NEW2 저장소 기준 파이프라인을 통과해 "실전 전략 승격 후보"를 확정할 때까지 탐색:
**PREREG 사전등록 → Holm 다중검정 보정 → 엔진 CandleCostModel 비용 교차검증 → OOS(구간 외) 검증 → 일관성(2구간 부호 일치)**

### 1.2 승격 기준 (Phase A 통과, 절대 완화 금지)
- Holm 보정 p < 0.05
- 엔진 비용 차감 후 기대값 양수
- 2개 구간 부호 일치
- OOS 통과

### 1.3 절대 금지 사항 (위반 = 프로젝트 무결성 붕괴)
1. **locked/reserved TEST 구간 사용 금지**: BTC 2024-03-25~06-18, ETH 2026-04-28~08-31 — 최종 판정용으로 잠겨 있음
2. **장부 무수정**: 측정 결과(results/), PREREG, worklog, 종합정리는 append/신규만 가능. 과거 기록 재작성·삭제·"재해석" 금지
3. **결과 과장 금지**: 실패는 실패대로 정직하게 기록 (지금까지 29건 전부 정직하게 FAIL 기록됨 — 이게 이 프로젝트의 신뢰성 원천)
4. **측정 등급**: 신규 측정은 Phase A 진단 등급으로만 (승격 주장 아님)

---

## 2. 현재 상태 (2026-10-14 기준)

### 2.1 측정 전적
- **29건 측정 전부 FAIL/폐기**: P1, X3, F1, C1, C4, C5, X1, L3, F2/X6, X5, L1, X7, X2, X4/F3, C3, X1-R, C7, C9, X8, X9, N2, KP, X10, A1, X12, ALT-P1, ALT-P1-R2, C-COMBO, ALT-P1-R3
- **주원인 = 비용 벽**: taker 왕복 중앙값 BTC/ETH 37.6bp, altcoin 92~104bp (엔진 교차검증으로 실측)

### 2.2 구조적 병목 (발견 ㊹)
- 검정력: t = (mean/std)·√valid_n. 최고 생존 관측(F2/X6-R maker +53.29bp, std 597.6bp, valid_n 59)조차 t=0.685 — 승격급 검정에 **추가 11.2년** 데이터 필요
- 포트폴리오 조합: 31개 부분집합 × 4시나리오 완전탐색 max Z=1.544 < 2.241 — 조합으로도 문제 닫히지 않음
- 승자 저주: 통과율 6.8% → 보정 후 3.1% — 명목 통과도 회의적 해석 필요

### 2.3 루프 상태
- **수동 루프 종료 판정 유지** (원리 스캔 10회차 — 신규 비겹침 원리 0건) → 임무를 **자율탐색 엔진(engine-discovery-v1, §12)**으로 이관. 구 수동 루프 크론(Job 453321)은 2026-10-14 삭제(중복 방지)
- 종료 후 상시 대기 경로 3개:
  1. **2027-01-11 동시 판정**: F2/X6-R 재검증 1차 + P3 청산 캐스케이드 진단
  2. **2027-07-01 최종 판정**: F2/X6-R 2차
  3. **사용자 신규 지시** (직전 소진분: Task 41 maker 인프라 착수, Task 45 오프사이트 백업 완성, Task 47 자율탐색 완전 자동화)

---

## 3. 상시 가동 데이터 경로 (판정일까지 계속 쌓이는 것들)

| 경로 | 목적 | 판정일 |
|---|---|---|
| **P3 청산 캐스케이드** (포트 3020 수집기) | 대규모 청산 파동 이벤트 축적 → 3개월 비중단 진단 | 2027-01-11 (진단), 승격 판정은 이후 |
| **F2/X6-R 공포탐욕 극단 재검증** | 규격이 PREREG_f2x6_reverify_2027.md에 고정됨 — 데이터는 자동 수집 | 2027-01-11 / 2027-07-01 |
| **maker 체결 모니터** (포트 3022) | F2/X6-R 승격 심사의 최대 불확실성(실전 체결률)을 실측으로 준비. δ0/δ5 × BTC/ETH × 롱/숏 8셀 페이퍼 post-only | 2027 판정 시 참고 데이터 |
| **F&G 감시기** (포트 3021) | 공포탐욕 지수 일일 장부 | F2/X6-R 판정 재료 |

**조기 관측(2026-10-14, 표본 8건 — 통계 판단 불가, 참고만)**: δ0(join best) 체결 0.2~148초 vs δ5(±5bp) 22~213초. "maker는 공짜 절감이 아니라 교환"(N2 발견㉙)과 정합.

---

## 4. 데이터 위치 전수 지도 (로컬 샌드박스)

| 데이터 | 경로 |
|---|---|
| 청산 수집 DB | `mini-services/liq-collector/data/liquidations.db` (SQLite+WAL) |
| F&G 장부 | `mini-services/fng-watcher/data/fng_ledger.jsonl` |
| maker 체결 장부 | `mini-services/maker-fill-monitor/data/fill_ledger.jsonl` |
| 측정 결과 | `NEW2_run/results/*.{json,md}` (JSON=원본, MD=보고서, 1쌍) |
| PREREG 사전등록 | `NEW2_run/PREREG_*.md` (34건) |
| 시세 원천 (재다운로드 가능) | `NEW2_run/data/` (13폴더 326MB — 백업 제외) |
| 연구 기록 | `NEW2_run/research/` (124파일) |
| 작업 로그 | `worklog.md` (Task 순차 기록 — **가장 먼저 읽을 것**) |
| 활문서(요약) | `NEW2_전략탐색_종합정리.md` (§0~11 스냅샷 + §12 append 전용) |
| 보고서 전 버전 | `NEW2_전략연구_보고서_v3.md` ~ `v28.md` |
| maker 규격 | `NEW2_run/SPEC_maker_fill_monitor.md` (데이터 위치 표 포함) |
| 백업 로컬 스냅샷 | `NEW2_run/backup/` (7세대 회전 + SHA256 manifest + log.md) |
| 백업 스크립트 | `NEW2_run/backup_snapshot.sh` · `NEW2_run/git_backup_push.sh` |
| SSH 전송기 | `NEW2_run/gitssh/gitssh.js` (GIT_SSH 대체, ssh2 기반) |

---

## 5. 인프라 — 다음 세션 생존 확인 절차

```bash
# 1) 서비스 3종 리슨 확인 (3020 청산 / 3021 F&G / 3022 maker 체결)
ss -tlnp | grep -E ":30(20|21|22)"
# 2) Next.js(3000)의 src/instrumentation.ts 부트 가드가 서비스를 자동 스폰함
#    — 죽어 있으면 instrumentation이 5분 워치독으로 부활시킴 (bun run dev 실행 상태 필요)
# 3) 장부 카운트
bun -e "const {Database}=require('bun:sqlite');const d=new Database('mini-services/liq-collector/data/liquidations.db',{readonly:true});console.log(d.query('SELECT COUNT(*) n FROM liquidations').get().n)"
wc -l mini-services/fng-watcher/data/fng_ledger.jsonl mini-services/maker-fill-monitor/data/fill_ledger.jsonl
# 4) 백업 상태
tail -3 NEW2_run/backup/log.md NEW2_run/backup/git_log.md
```

- 예시 정상치(2026-10-14): 청산 2,909건↑, F&G 10행, maker fill 30행
- **장부 카운트가 감소하면 무언가 심각히 잘못된 것** — 즉시 보고

---

## 6. 백업 체계 (2중)

| 층 | 내용 | 자동화 |
|---|---|---|
| 로컬 스냅샷 | `NEW2_run/backup/NEW2_tier1_*.tar.gz` 7세대 회전 + SHA256 manifest | cron Job 458540 & 458723 (매일 09:00 KST) |
| **GitHub 오프사이트** | **이 저장소** — TIER1 전체 커밋 push | cron Job 458723 (매일 09:00 KST) |

- 인증: **Deploy Key**(단일 저장소 read/write 한정, 개인키는 샌드박스 `NEW2_run/gitssh/deploy_key`, 채팅·외부 미노출)
- 스크립트 출력 계약: `SNAPSHOT_OK` / `GITBK_OK` / `GITBK_FAILED` — FAILED 시 원인 조사 후 사용자에게 크게 보고
- **cron 자체는 플랫폼 측에 존재** — 샌드박스가 사라지면 cron도 사라짐. 복원 절차는 §9

---

## 7. 운영 교훈 (사고 전례 기반 — 새 세션은 반드시 숙지)

1. **서비스 킬은 포트 기반 PID로 한정**: `pkill -f "bun index.ts"` 금지 — 3서비스 동시 사망 사고 전례 (SQLite WAL 덕에 0손실 복구)
   ```bash
   PID=$(ss -tlnp | grep :3020 | grep -oP 'pid=\K[0-9]+' | head -1); kill $PID
   ```
2. **장기 서비스 파일 append는 node:fs appendFileSync** — bun의 `Bun.write {append}`가 무시/트렁케이트된 사고 2연발 전례
3. **에이전트 셸 백그라운드 프로세스는 명령 종료 후 회수됨** — 상시 서비스는 반드시 `src/instrumentation.ts`의 SERVICES에 등록해서 Next.js 부트 가드+워치독이 관리하게
4. **ssh2 라이브러리는 ed25519 키를 OpenSSH 형식만 지원** (PKCS8 "Unsupported key format") — 키 생성은 `NEW2_run/gitssh/genkey_ssh2.js` 사용
5. **샌드박스 DLP가 도구 출력의 개인키 머리글을 마스킹**함(`[REDACTED:ssh_private_key]`) — 파일 무결성은 출력이 아니라 Grep으로 확인
6. **bun:sqlite 활성 DB 백업은 `VACUUM INTO`** (readonly 연결) — 파일 직접 복사는 WAL 일관성 위험

---

## 8. 재개 트리거 — 다음 세션이 실제로 일해야 하는 시점

| 트리거 | 시점 | 해야 할 일 |
|---|---|---|
| 판정일 ① | **2027-01-11** | F2/X6-R 1차 판정(PREREG_f2x6_reverify_2027.md 규격 실행) + P3 진단(3개월 비중단 여부) |
| 판정일 ② | **2027-07-01** | F2/X6-R 최종 판정 |
| 사용자 신규 지시 | 수시 | 지시 이행 (직전 패턴: maker 인프라→오프사이트 백업) |
| 백업 실패 보고 | 매일 09:00 크론 | 원인 조사·복구·크게 보고 |
| 자율탐색 사이클 | **3시간마다 (Job 458843)** | 엔진 자동 수행(§12) — 세션 개입 불필요. **CYCLE_FAILED 또는 SURVIVOR/SURVIVOR_COMBO 발생 시에만** 원인 조사·보고 |
| 그 외 | — | **세션 임의 측정 시작 금지** — 신규 탐색은 전부 자율탐색 엔진 경유(§12). 엔진 우회 측정은 장부 무결성 위반 |

승격 후보가 생기면: 즉시 모든 작업 중단 → 근거 데이터 포함 보고 → 사용자 결정 대기.

---

## 9. 재난 복구 매뉴얼 (샌드박스가 통째로 사라진 경우)

1. **새 환경에서 이 저장소 clone** (사용자 GitHub 계정으로) — 이 문서(HANDOVER.md)가 안에 있다
2. 트리 복원: `ledgers/` → 미니서비스 data 디렉토리로 복사, `results/ research/ prereg/ docs/` → 원래 경로로 복사
3. 수집기 3종 재구축: `src/mini_services/` 소스 → `mini-services/{liq-collector,fng-watcher,maker-fill-monitor}/` 로 복원, 각각 `bun install`
4. `src/instrumentation.ts`가 부트 가드로 서비스 스폰
5. **Deploy Key 재등록**: 새 환경에서 `genkey_ssh2.js`로 새 키 생성 → **공개키만** 사용자에게 전달 → 사용자가 GitHub 저장소 Settings → Deploy keys에 추가 (구 키는 삭제 가능) → Allow write access
6. **cron 재등록** (샌드박스 소실로 cron도 소실됨):
   - 자율탐색 루프: **3시간 고정 주기(agentTurn)** — 메시지: worklog 상태 확인 → `bash /home/z/my-project/NEW2_run/discovery/run_cycle.sh` 실행 → CYCLE_OK/SURVIVOR/CYCLE_FAILED 판별·보고 (§12 절차 그대로)
   - 일일 백업: 매일 09:00 KST `backup_snapshot.sh` + `git_backup_push.sh`
7. `worklog.md` 복구 후 마지막 Task ID 이어서 기록 — 히스토리 단절 없음
8. 시세 원천(326MB)은 백업 대상 아님 — 거래소에서 재다운로드 (inventory_detail.md 참조)

---

## 10. 사용자 커뮤니케이션 규범

- 사용자 언어: **한국어**. 답변은 구체적 숫자·경로·검증 결과 기반 (실측 후 보고가 원칙)
- 사용자는 보안에 민감: **토큰·키를 채팅에 붙여넣는 방식 금지** (Deploy Key 방식 확립 — 유지할 것)
- 사용자 질문 패턴: 데이터 위치/내구성/자동화 범위 — 항상 "실제 파일을 읽어 확인한 값"으로 답변할 것
- 정직이 최우선: 실패·사고·한계는 숨기지 않고 즉시 보고 (이 프로젝트의 모든 신뢰는 여기서 나옴)

---

## 11. 이 문서 갱신 규칙

- 큰 구조 변화(서비스 추가/제거, 판정 완료, 백업 체계 변경)마다 다음 세션이 이 문서를 **새 커밋으로 갱신**하고 push할 것
- 세부 로그는 여기에 복사하지 말 것 — `worklog.md`가 원본, 이 문서는 요약+인계 전용

---

## 12. 자율탐색 엔진 (engine-discovery-v1) — Task 47 신설, 완전 자동화 핵심

사용자 지시("탐색→검증→조합→승격을 완전 자동화")에 따라 신설. **기존 잠금 방법론을 코드로 이식**한
결정론적 순환 엔진 — 세션 개입 없이 3시간마다(Job 458843) 자동 실행.

### 12.1 파일 (NEW2_run/discovery/)
| 파일 | 역할 |
|---|---|
| `engine_lib.py` | 공통 라이브러리: CandleCostModel·이벤트 스터디·Holm·게이트·Stouffer |
| `generator.py` | 후보 생성기: 7패밀리 문법 × 5심볼페어 × 파라미터 그리드 = **779 후보 유니버스** |
| `runner.py` | 사이클 오케스트레이터: PREREG(선)→백테스트(후)→판정→장부 append |
| `run_cycle.sh` | 락 래퍼 (동시 실행 방지, `bash .../run_cycle.sh`) |
| `state.json` | 상태(사이클 번호·패밀리 커서·생존 풀·승격 큐) |
| `discovery_ledger.jsonl` | **append-only 시도 장부** (PREREG sha256 포함) |
| `cycles.md` | 사이클별 요약(자동 기록 — worklog 대신 여기에 쌓임) |
| `promotion_queue.md` | SURVIVOR 적재 대장 (2027 판정 대상) |

### 12.2 7패밀리 문법 (캐시 데이터만 사용 — 신규 다운로드 0바이트)
flow_z(taker 매수비율 z: mom/rev) 360 · ret_rev(단기 반전) 120 · ts_mom(추세) 60 ·
vol_break(변동성 돌파) 60 · tod(세션 계절성) 60 · fund_z(펀딩비 극단 역발상) 45 ·
fng(공포·탐욕 극단 역발상 스윕) 74 — F2/X6 기존 측정분(BTC/ETH×(10,90)/(20,80)×h{4,16,48})은 배제.

### 12.3 게이트 (기존 승격 기준 그대로 이식 + 엔진 강화 3규칙)
판정 순서: **POWER**(비중첩 valid_n≥30) → **COST**(엔진 비용 차감 후 양수) →
**CONSIST**(2구간 부호 일치) → **STAT**(Holm p<0.05 + 수축 후 양수).
- R1: 게이트는 비중첩 표본으로만 (중첩 창 유의성 과대 계상 차단 — raw 통계는 참고 병기)
- R2: 승자 저주 수축 mean_defl = mean − 2.2414·se > 0 까지 요구
- R3: **승격 절대 자동 선언 없음** — SURVIVOR는 promotion_queue 적재, 실제 승격은
  잠금 TEST 구간에서 2027-01-11 / 2027-07-01 판정 시에만
- 판정 5종: SURVIVOR / FAIL_COST / FAIL_CONSIST / FAIL_STAT / UNDETERMINED_POWER(재검증 큐)

### 12.4 순환 논리 (사용자 요구사항과의 대응)
1. **탐색**: 패밀리 라운드로빈으로 779 유니버스를 소진할 때까지 사이클당 ~10개 검증
2. **검증**: 각 후보 PREREG 사전등록(해시 장부 고정) → 백테스트 → 4중 게이트 → 정직한 판정
3. **조합**: SURVIVOR 발생 시 ①파라미터 ±1스텝 섭동 ②vol_calm/vol_stress 국면 필터 변형을
   실제 이벤트 스터디로 재검증 + 생존 2개 이상 시 ③Stouffer 결합(w=√vn, ρ 그리드 max —
   combo_exhaustive_scan 방법 준용) → 조합 결과도 동일 게이트 적용
4. **승격**: promotion_queue만 유지 — 판정일에 잠금 TEST 구간으로 최종 판정

### 12.5 운영 참고
- 1사이클 ~3초(메모리 캐시) — 3시간 주기면 779개 소진까지 약 10일
- 유니버스 소진 후: 생존 풀 조합만 지속 + UNDETERMINED_POWER는 데이터 누적 후 재검증 대기
- 파라미터 확장으로 유니버스를 늘릴 때는 generator.py의 build_grid()만 수정(커서·해시 안전)
- 수동 실행: `NEW2_DISC_BATCH=3 bash NEW2_run/discovery/run_cycle.sh` (테스트용)
- 예상 결과: 비용 벽(37.6~104bp) 때문에 대부분 FAIL_COST — 이것이 정상이며 장부가 증거가 됨
