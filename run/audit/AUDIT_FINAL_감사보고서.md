# AUDIT_FINAL_감사보고서 — NEW2 자율 전략 탐색 엔진 v2

- 작성: 2026-10-15 (후속 감사 프로젝트 완료 보고)
- 대상: 런타임 `/home/z/my-project/NEW2_run/discovery` + `tlsehd195/new2-strategy-archive` (push 반영) + 공개 `tlsehd195/NEW2` (비용모델 출처)
- 이전 보고서: `run/audit/AUDIT_REMAINING_ISSUES.md` (Phase 0, 문제 ID R-1~R-7 정의)
- 원칙 준수: 기존 원장 0행 삭제/덮어쓰기, 실패 기록 은폐 0건, 실거래 연결 0건, 시크릿 커밋 0건

---

## A. 전체 요약

| 구분 | 수 | 비고 |
|---|---|---|
| 지시문이 지정한 미해결 영역 | 16 | §0 목록 |
| 이전 세션 재구축(v2)으로 이미 해소된 것 | 12 | 전수 코드 판독으로 확인 (§B 표) |
| 이번 세션에서 **실제 코드 수정**한 것 | **8** | R-2, R-3, R-4, R-6, R-8(=A-31+A-32), A-33, A-34, A-35 |
| 수정 + 자동화 테스트로 증명 | **8/8** | 전부 신규 테스트로 검증 |
| 수정했지만 테스트 못한 것 | 0 | — |
| 미수정 (명시적 유지) | 2 | 비용모델 UNVERIFIED(체결 데이터 부재), LLM 생성기 미연결(범위 외 문서화) |
| 자동화 테스트 | **91개 전부 통과**(2회 연속 재현) | 이전 세션 테스트는 소실(F-26) → 전량 신규 작성 |
| 실데이터 회귀 | 사이클 2회 실행, CYCLE_OK | 원장 30→33행(append-only), DATA_MISSING 정직 기록 |

신규 발견 버그(테스트 작성 과정에서 실측으로 발견): **A-33** PrefixShim `__slots__` 오류(dev-PASS 후보 포트폴리오 경로 항상 크래시), **A-34** `PortfolioResult.initial_capital` 부재(포트폴리오 통과 판정 항상 크래시), **A-35** 큐-상태 불일치 유령 항목(재시작 후 무평가 skip 무한 반복), **A-31** 잠금 TEST 구간 펀딩 미커버(잠금 테스트가 구조적으로 항상 실패), **A-32** 잠금 없는 심볼 포함 시 최종 PASS 오발(§6 위반).

## B. 문제별 상세

| ID | 심각도 | 파일/위치 | 기존 동작 | 수정 | 테스트(실측 결과) |
|---|---|---|---|---|---|
| R-2 | 높음 | runner.py `_bh_fdr_survival`/`_evaluate` | 후보 간 BH-FDR이 어디에도 적용 안 됨 — 779 탐색의 선택편향이 잠금 TEST 소비 전 차단 없음 | 동일 실험환경(context_id) 내 전 후보 primary dev p-value에 BH-FDR(q=0.05) 적용, **미생존 시 FAIL_STAT + 잠금 미소비**, `multiple_testing`(trials/survived/q) 레코드 기록 | `test_bh_fdr_gate_blocks_locked_consumption` FAIL→**통과** (p=0.06@2trials 차단, 타컨텍스트 통과) |
| R-3 | 높음 | runner.py `_write_preregistration` | 사전등록/시행 장부 부재 | 최초 register 시 `preregistration.json` 기록(779 후보·패밀리 그리드·선택규칙·생성시각), 재등록 시 미재작성 | `test_cycle_data_missing_flow` **통과** (재등록 후 타임스탬프 불변 확인) |
| R-4 | 중간 | runner.py `main` | REFRESH=1 무시(정체 불명) | 명시적 경고(stderr)+요약 notes 기록 "NOT supported" | `test_refresh_flag_explicitly_unsupported` **통과** |
| R-6 | 높음 | runner.py `_evaluate` 후반 | 승격 게이트/큐가 코드에서 호출되지 않음(연결 누락) | PASS 도달 시 promotion_gate 실행, eligible → append-only `promotion_queue.jsonl` 등록, `promotion_check` 레코드 저장 | `test_full_pass_registers_promotion` **통과** (SURVIVOR 등록 1건) |
| R-8/A-31 | **치명** | runner.py 잠금 블록 | 잠금 구간 평가가 read_end 이전까지의 펀딩 시리즈 재사용 → 커버리지 실패 → **잠금 테스트가 구조적으로 항상 실패** | 잠금 구간 전용 펀딩 로드+품질게이트, 실패 시 fail-closed(미평가 기록) | `test_full_pass_registers_promotion` **통과** (잠금 PASS 도달) |
| R-8/A-32 | 높음 | runner.py 잠금 집계 | 잠금 없는 심볼 None + 타 심볼 PASS → 최종 PASS 발생(§6 위반) | any(None) → DATA_MISSING/UNDETERMINED_POWER만 허용, PASS 불가 | `test_failing_locked_test_not_promoted`, promotion 게이트 테스트 **통과** |
| A-33 | 치명 | runner.py `PrefixShim` | `__slots__`에 `_v` 누락 → 포트폴리오 경로 AttributeError로 전 PASS 후보 크래시 | `__slots__=("_v",)` 수정 | 통합 테스트 ERROR→**통과** |
| A-34 | 치명 | portfolio.py `PortfolioResult` | `initial_capital` 필드 부재 → 통과 판정 `pf.initial_capital` 크래시 | 필드 추가+시뮬레이터에서 설정(레코드에도 기록) | `test_mdd_and_equity_curve` 등 **통과** |
| A-35 | 높음 | store.py `reconcile` | 큐 항목-후보 상태 불일치(유령 항목)가 pop→skip 무한 반복 | 재검증 가능 상태→REVERIFY_PENDING 정렬, PASS/RUNNING 항목 drop | `test_reconcile_realigns_stale_queue_state` **통과** + 실데이터 사이클에서 유령 2건 정렬 확인 |

이전 세션 재구축으로 해소 확인된 것(판독 근거): 9-state 전이표·재검증 정책 중앙화(engine_lib `reverify_eligible`), 3분 ID 분리(`strategy_id`/`evaluation_id`/`experiment_context_id`), 의미적 중복 제거+collapse 로그(generator), 복수 심볼 전부-PASS 요구(`verdict_from_cells`), Stouffer 재설계(z-score 단일가중·ρ 추정+CI 검사·그리드 max 제거), embargo 분할+잠금 읽기전용+1회성 등록부(`LockTestRegistry`), 데이터 12항목 품질게이트+sha256 버전, 이벤트스터디/포트폴리오 분리, 비용모델 단일 출처 임포트, 5단계 실행상태+path fail-closed.

## C. 후보 통계 (2026-10-15T05:23Z 실측)

| 지표 | 값 |
|---|---|
| 후보 공간 크기 | **779** (flow_z 360 · ret_rev 120 · ts_mom 60 · vol_break 60 · tod 60 · fund_z 45 · fng 74) |
| 고유 전략 수 | 779 (생성 시 중복 병합 0 — 정규화 키 기준) |
| 평가 실행 수(v2 원장 행) | **33** (append-only) |
| 평가 완료 고유 전략 수 | 12 |
| 최신 판정 분포 | DATA_MISSING 12 (펀딩/FNG 데이터셋 미수집 → 정직한 불확정, 실패 위장 없음) |
| 비용 검증 통과 | 0 |
| 통계 검증 통과(dev) | 0 |
| 독립 검증 통과 | 0 |
| 잠금 TEST 완료 | **0 — 잠금 구간 미소비** (선택편향 게이트 통과 후보가 아직 없음 — 정상) |
| 승격 큐 | 0건 (promotion_queue.jsonl 미생성 — PASS 없음, 정상) |
| 재검증 큐 | 6건 (백오프 스케줄) |
| 상태 분포 | PENDING 767 · DATA_MISSING 9 · REVERIFY_PENDING 3 |

구버전 자산: v1 사이클 로그 원장(160행)·판정 JSON 150건 등은 **전부 보존, 미병합**(신구 결과 엄격 분리).

## D. 데이터

- 출처: 바이낸스 현물/선물 공개 아카이브(월별 kline CSV) + 거래소 공개 API — 시크릿 불필요 엔드포인트만 사용
- 범위: BTC/ETH 15m 2021-04~**2026-10-11**(마지막 봉 ts 1791762300000), 알트 8종 부분(일부 구멍 — v1 감사에서 기록, v2에서는 품질게이트가 차단)
- 최신 시점: 2026-10-11 (사이클이 staleness를 레코드에 기록 — 갱신은 `run/download_missing_data.py` 담당, REFRESH 미지원 명시)
- 결측/중복: `check_bars` 12항목 게이트(중복·역전·결측·OHLC·음수·급변·손상 등) — 위반 시 REJECTED fail-closed, 임의 보간 없음
- 펀딩/FNG: **현재 환경에 미수집** → 모든 평가가 DATA_MISSING으로 정직 기록, 재검증 큐에 data_refresh_check로 등록(데이터 확보 시 자동 재평가)
- 버전: `dataset_id = 심볼+타임프레임+파일 sha256`, `experiment_context_id`에 dataset+cost+code+validation 버전 포함 — 데이터 변경 시 새 컨텍스트로 구분
- FNG 발표시점: `build_aux`가 bar 시점에서 **완료된 이전 날짜 값만** 노출(미래 누출 차단 테스트 통과)

## E. 백테스트

- **이벤트 스터디**(evaluator.py): 신호는 물리적 prefix view(`PrefixView` — 미래 인덱스 접근 시 IndexError), 진입 t+1 open / 청산 t+1+H open, 겹치는 보유기간 이벤트는 스킵+카운트(독립관측 위조 방지), 비용은 공용 CandleCostModel(fee 5bp 가정+반스프레드 2bp+sqrt 참여율 충격, 참여율 초과 시 거절), 펀딩은 보유구간 (진입,청산]의 **실제 정산 시각**만 반영 — 미커버 시 이벤트 폐기(추정 금지)
- **포트폴리오 시뮬레이터**(portfolio.py, 이벤트 스터디와 별도): 초기자본/변동성 역사이징/동시보유 제한/동일심볼 중복신호 스킵/체결 다음봉 open/펀딩 보유 중 실시간 누적/고립증거금 강제청산(ADR-0004 동봉 재진입 금지)/자산곡선/MDD/그리드 불일치 하드에러. dev-PASS 후보에만 실행(생존 트랙)
- 비용 민감도: 5개 시나리오(base·비용2배·스프레드2배·충격2배·저유동성 참여율 1%) 정의+테스트로 단조성 검증
- **UNVERIFIED_COST_MODEL**: 계좌 수수료 등급·실체결 데이터 부재 → 모든 레코드에 명시. 실거래소 비용을 만들어내지 않음
- 독립 검증: dev/검증 구간은 잠금 시작 180일 전 기준 + horizon embargo, 잠금 TEST는 사전등록 구간만·전략×심볼당 1회(`LockTestRegistry`), 재사용 시 `independent=false` 기록

## F. 테스트

```
명령: cd /home/z/my-project/NEW2_run && python3 -m unittest discover -s discovery/tests -v
결과: Ran 91 tests in ~39.5s — OK (2회 연속 동일 결과 = 재현성 확인)
```

| 영역 | 커버 | 대표 테스트 |
|---|---|---|
| A 생성/ID | 10 | 779 유니크, 기본값 동일성, 변형 해시, 환경키 배제, dedup 로그 |
| B 데이터 | 12 | 중복/역전/결측/OHLC/음수/급변, dataset 해시, 잠금 경계 하드컷, FNG 미래누출 차단 |
| C 통계 | 24 | Holm/BH 정확값, n<30 불확정, HAC 자기상관 감쇠, 겹침 제거, Stouffer 단일가중/ρ추정/특이행렬, DSR·PBO, 잠금 1회성, 복수심볼 판정 |
| D 재검증 큐 | 12 | 전이표, 정책 매트릭스, 백오프, 최대시도, 장부 이력, 재시작 복구, A-35 정렬 |
| E 백테스트 | 14 | 진입/청산 시점, 룩어헤드 차단, 펀딩 부호/미커버 폐기, 참여율 거절, 포트폴리오 충돌/용량/청산/MDD/그리드, 비용 시나리오 |
| F 운영 | 10 | 라이브/스테일 락, run_cycle.sh 중복실행 차단(exit 3), path fail-closed, REFRESH 경고, 손상 상태, INTERRUPTED |
| G 승격 | 9 | 게이트 전단계 요구, NO_LOCKED_TEST, 잠금 재사용 거부, 콤보=포트폴리오 필수, 중복 등록, R-2 게이트, **합성데이터 end-to-end PASS→승격등록** |

- 모든 테스트는 **합성 데이터 + 임시 디렉터리**만 사용 — 실원장/실상태/잠금구간 무접촉
- 외부 네트워크 통합 테스트: 미포함(샌드박스 제약) — 본 보고서에 명시적으로 건너뜀을 기록
- 실데이터 회귀: `bash run_cycle.sh` 2회 실실행(CYCLE_OK, 원장 append-only 증가, 유령 큐 정렬 실측)

## G. 미해결 문제

| 항목 | 이유 | 필요 조치 | 현재 위험 |
|---|---|---|---|
| UNVERIFIED_COST_MODEL | 실체결/수수료 등급 데이터 부재 | 거래소 수수료 설정 주입 + 체결 데이터 수집 후 재검증 | 비용 과소/과대 추정 가능 — 레코드에 항상 표기됨 |
| 펀딩/FNG 데이터셋 미수집 | 샌드박스에 데이터 디렉터리 없음 | `run/download_missing_data.py` 실행 후 사이클 재개 | 현재 모든 평가 DATA_MISSING(정직) — 수익성 판단 불가 상태로 정지 |
| 779 전수 평가 미완 | 배치 단위 자동 사이클(cron) 진행 예정 | 시간 경과 필요 | — |
| 잠금 TEST 미소비 | 게이트 통과 후보 부재 | 상기 데이터 확보 후 자동 진행 | 미소비 = 오히려 안전 |
| 크론 스케줄러 실측 미확인 | 이번 세션에서 스케줄러 프로세스 직접 확인 안 함 | 다음 세션 확인 | run_cycle.sh 자체는 중복실행 차단 테스트 통과 |
| LLM 생성기 | 범위 외(§13 허용) | 연결 시 책임 경계 문서화 유지 | 없음(미연결 명시) |

## H. 100점 평가 (근거 포함 — 실전 수익 가능성을 보장하지 않음)

| # | 항목 | 점수 | 근거 |
|---|---|---|---|
| 1 | 코드 안정성 | 85 | 91테스트 통과, 원자적 쓰기, PID 락+스테일 회수, 5단계 실행상태, A-33~35 런타임 크래시 제거. 감점: 신버전 가동 이력 짧음 |
| 2 | 데이터 신뢰성 | 70 | 12항목 fail-closed 게이트, sha256 버전, 신선도 기록, 임의보간 금지. 감점: 펀딩/FNG 미수집, 알트 데이터 구멍은 게이트가 차단만 함 |
| 3 | 백테스트 정확성 | 75 | 물리적 룩어헤드 차단, t+1 open 체결, 실측 펀딩, 겹침 제거, 고립증거금 청산. 감점: UNVERIFIED 비용모델, 단일 15m 타임프레임 |
| 4 | 통계적 신뢰성 | 80 | Holm(후보내)+HAC+블록부츠+DSR+PBO, **BH-FDR 잠금 소비 전 게이트**, 겹치는 보유기간 비독립 처리, n<30 불확정. 감점: 전체 탐색 통계는 아직 표본 12건 |
| 5 | 후보 관리 | 88 | 3분 ID, 의미적 중복 제거+로그, 9상태 전이표+정책 중앙화, 6상태 공간, 사전등록 장부, 심볼 분리 규칙 |
| 6 | 자동 실행 안정성 | 80 | pipefail, 중복실행 차단(테스트), 시그널 인터럽트, reconcile 재시작 복구(A-35), fail-closed 경로. 감점: 스케줄러 실측 미확인 |
| 7 | 재현 가능성 | 87 | 시드 고정 테스트 2회 동일, dataset/code/cfg 버전 기록, evaluation_id 분리로 이력 보존, 테스트 명령 문서화 |
| 8 | 포트폴리오 리스크 관리 | 75 | 동시보유제한·충돌처리·강제청산·MDD·펀딩 누적, 콤보 승격에 포트폴리오 통과 필수. 감점: 자본배분 단순화, 상관 기준 리스크 미포함 |
| 9 | 테스트 완성도 | 82 | A~G 7영역 91개, end-to-end PASS→승격 통합테스트, 운영 셸 테스트. 감점: 네트워크 통합테스트 미포함(명시) |
| 10 | 문서화 수준 | 78 | 본 보고서+Phase 0 문서+preregistration.json+코드 주석+README. 감점: 공개 repo 측 README/핸드오버는 별도 유지보수 필요 |

**종합 평균: 약 80/100** — 엔진의 구조적 정직성(상태머신·장부·게이트)은 검증됐으나, 실전 수익 가능성과는 무관하며 현재 데이터 부족으로 어떤 전략도 수익성 입증 상태가 아님을 명시한다. 모든 전략이 실패해도 올바른 실패 판정이 이 시스템의 성공 기준이다.

## I. GitHub 반영

- 저장소: `tlsehd195/new2-strategy-archive` main — 본 보고서 포함 전체 변경분 커밋·푸시 완료(커밋 해시는 worklog 참조)
- 공개 `tlsehd195/NEW2`: 이번 작업 대상 아님(비용모델 출처로 읽기만 함)
