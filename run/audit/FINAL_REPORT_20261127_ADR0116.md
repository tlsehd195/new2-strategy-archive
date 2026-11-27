# 최종 보고 — 전체 문제 해결 및 코드 무결성 강화 (ADR 0116)

- 저장소: `tlsehd195/new2-strategy-archive` (NEW2 거래 시스템과 무관)
- 기준 커밋: `main` `d564456a7df8adf02c64796cec1a3784ea3d0cf3`
- 작업 브랜치: `claude/project-thread-mdlu2n`
- 결정 기록: `docs/adr/0116-data-integrity-collectors-audit-registry.md`, 표준: `docs/data_integrity_standard.md`
- 실거래 계좌 연결·API 비밀키·실주문·자동매매 기능: 추가하지 않음. 통계 기준·승격 조건: 바꾸지 않음. 사전등록 활성화·검증/잠금/IW 구간 개방: 없음.

## A. 검토 범위
- git 트리의 코드 파일 362개를 해시와 함께 `run/audit/CODE_AUDIT_REGISTRY.json`에 등록했다.
- 이번에 직접 읽은 범위: 지시문이 지목한 경로(전진 기록 `forward_nearmiss`·`newsrc_engine`, 일봉 엔진의 데이터 경로, `portfolio.py` 입력 처리, `paper_forward` 펀딩 조회, `data_fetch` 재시도·분류, `funding_schedule`, `src/*.ts` 4개 전부, `run_cycle.sh` 모드 처리, `portfolio_sim.py`, `research-ci.yml` 전체, 감사 도구 3개).
- 레지스트리 상태(검토 해시 기준): 아래 표. 상태는 해당 sha256에서만 유효하고, 바뀌면 CI가 `CHANGED_SINCE_REVIEW`로 바꾼다.

| 종류 | FULL_SOURCE_REVIEW | LOGIC_TRACED | TESTED | PARTIALLY_REVIEWED | NOT_REVIEWED | CHANGED_SINCE_REVIEW | EXEMPTED_WITH_REASON | 합계 |
|---|---|---|---|---|---|---|---|---|
| config | 1 | 0 | 0 | 0 | 4 | 0 | 0 | 5 |
| source | 33 | 73 | 16 | 22 | 76 | 7 | 1 | 228 |
| test | 9 | 0 | 53 | 2 | 65 | 0 | 0 | 129 |
| workflow | 0 | 0 | 0 | 1 | 2 | 0 | 0 | 3 |
| 합계 | 43 | 73 | 69 | 25 | 147 | 7 | 1 | 365 |

FULL_SOURCE_REVIEW / LOGIC_TRACED / TESTED 중 다수는 SOURCE_AUDIT_20261110의 독립 검토자 주장(작성자 미재확인)을 해시와 함께 옮긴 것이다(`review_by` 열).

- 지시문 §11(연구 스크립트 202개 전수 정밀 검토)은 끝나지 않았다. 이번에 바꾼 파일 외에는 다시 읽지 않았고, 그 사실을 상태로 남겼다(E절).

## B. 발견한 문제
ADR 0116 "발견" 표의 F-01~F-15. 요약:
1. 일봉 엔진 전반이 누락 수익률·누락 펀딩을 0으로 계산(F-01). 전진 기록 CL1·FIP1·AF1 포함.
2. 전진 판정에 데이터 상태가 반영되지 않음(F-02).
3. 거래량이 없으면 1.0으로 충격 비용 계산(F-03).
4. 펀딩 정산 지터(00:00:00.002)를 다음 날로 귀속(F-04).
5. 값이 다른 중복 일봉을 조용히 유지(F-05).
6. `portfolio.py`가 `decision_index=-1`을 첫 봉 진입·마지막 봉 거래량으로 해석, 정수 아님·범위 밖 허용(F-06).
7. instrumentation의 특정 PC 절대경로, 수집기의 Windows/공백 경로 오류·폴더 확인 없음·청산 시각 `Date.now()` 대입(F-07, F-08).
8. F&G 감시기의 장기 중단 복구·재시도·충돌 처리 없음(F-09).
9. 메이커 감시기의 불완전한 원장 복원, 중복·순서 역전·손상 처리 없음, 체결률 분모 불명확(F-10).
10. 페이퍼 펀딩 실패 원인 구분 불가(F-11), 폐기된 `portfolio_sim.py`가 그냥 실행됨(F-12).
11. 감사 기록이 파일 해시에 묶이지 않음, "테스트 파일 있음"을 TESTED로 표시(F-13). CI에 TS 타입 검사·테스트·0 대체 검사 없음(F-14).
12. 엔진 단위 테스트를 개별 실행하면 저장소의 공유 잠금 기록에 쓰고, 그 안내 문구도 틀림(F-15).

## C. 수정 내용
- 공통 모듈 `run/research_data_integrity.py`와 일봉 엔진 13개 파일 적용(무효일 NaN·제외·개수 기록, 펀딩 완결성, 거래량 출처, 패널 정리).
- `forward_nearmiss.py`: 6상태(PASS/FAIL/INVALID_DATA/INSUFFICIENT_DATA/UNDETERMINED/ERROR), Holm m=3 고정·무효 후보 p=1, 사전등록 부속 문서(코드 해시, 첫 평가 전 기록).
- `portfolio.py`: `validate_signals`, `SignalInputError`, 데이터 끝 미실행 신호 집계.
- `src/*.ts`: 설정 경로·폴더 확인·실패 시 종료, F&G 복구·재시도·비덮어쓰기, 청산 기록 검증, instrumentation 확인 단계, 메이커 원장 재생·복구·통계 재작성.
- `paper_forward.py`/`data_fetch.py`: 펀딩 실패 `cause`·`cache` 기록, HTTP 오류 본문 보존.
- `portfolio_sim.py`: 명시 플래그 없이 실행 거부, 출력에 사용 금지 표시.
- 감사: `code_audit_registry.py`(해시 기반 8상태), `zero_fill_scan.py`(0 대체 증가 차단), `source_audit` 검사 연동.
- CI: bun 테스트, TS strict 타입 검사, `src/` 절대경로 검사, 레지스트리 검사, 0 대체 검사, 테스트 후 공유 잠금 기록 생성 감시.
- 문서: ADR 0116, `docs/data_integrity_standard.md`, ADR 목록, `run/discovery/README.md`, `docs/worklog.md`.

## D. 테스트 결과
| 항목 | 결과 |
|---|---|
| 엔진 단위 테스트 `run/discovery/tests` 전체 (Python 3.13, stdlib) | 1,206개 통과 (2,130초). 실행 뒤 `locked_shared/` 생성 없음 |
| 엔진 단위 테스트 전체 (Python 3.11) | RESULT_311 |
| 페이퍼·데이터 조회·포트폴리오 관련 묶음 (변경 후 코드, 3.13) | 230개 통과 |
| 루트 연구 테스트 `run/test_*.py` (3.11 + numpy 2.2.6 / pandas 2.2.3, CI 고정 버전) | 323개 통과 |
| 루트 연구 테스트 (3.13 + numpy 2.5.3 / pandas 3.0.5) | 323개 통과 |
| bun 테스트 `src/tests` (bun 1.4.4) | 35개 통과 |
| TypeScript strict 타입 검사 (typescript 5.7.3, bun-types 1.1.38) | 오류 0 (일부러 넣은 타입 오류는 잡힘을 확인) |
| `bun build --no-bundle` 4개 파일 | 통과 |
| ruff E9/F63/F7/F82 (run/*.py, run/audit, run/discovery), F (run/discovery) | 통과 |
| 감사 검사: code_audit_registry / zero_fill_scan / source_audit / audit_provenance / research inventory | 모두 통과 |
| shellcheck | 이 환경에 없음 — GitHub CI에서만 실행 |

새 테스트: `test_research_data_integrity.py` 10, `test_forward_nearmiss.py` 무결성 클래스, `test_cross_section_v2.py` 1, `test_code_audit_registry.py` 7, `test_zero_fill_scan.py` 2, `test_adr0116_portfolio_inputs.py` 16, `test_adr0116_funding_causes.py` 6, `test_adr0116_legacy_entrypoints.py` 3, bun 35. 기준 커밋에서 같은 묶음(discovery 5개 파일)을 격리 없이 돌리면 base·변경 후 모두 같은 잠금 관련 실패가 나는데, 원인은 F-15(격리 모듈 미로드)였고 격리하면 모두 통과한다.

## E. 해결하지 못한 문제 (UNRESOLVED)
1. 연구 스크립트 전수 정밀 검토(§11): 미완료. 레지스트리상 NOT_REVIEWED / CHANGED_SINCE_REVIEW 파일 수는 A절 표와 같다.
2. 옛 DEV 스크립트의 0 대체 39줄(19개 파일): 고치지 않고 기준선으로 묶음. 결론이 이미 NOT_PROMISING인 탐색이라 판정 영향은 없지만, 재사용 전 수정이 필요하다.
3. `confirm_iw0.py`(15분봉, 소진된 IW0): 바꾸지 않음.
4. REST 펀딩은 과거 정산 간격을 증명할 수 없다(ADR 0074). `SYMBOL_UNSUPPORTED`는 오류 본문이 있을 때만 구분된다.
5. 컨테이너가 root라 권한 비트에 의한 "쓰기 불가"는 재현하지 못했다(폴더 생성 실패 경로로 검증). TS 서비스의 실제 네트워크·장기 실행은 하지 않았다(클라우드 장기 실행 금지).
6. 엔진 테스트 개별 실행 시 격리 모듈이 빠지는 구조(F-15)는 그대로다(안내 수정과 CI 감시만).
7. GitHub Actions에서의 실행 결과: PR 단계에서 확인(아래 판정 참조).

## F. 최종 판정
**PARTIALLY_VERIFIED** — 지시문이 지목한 결함(F-01~F-14)은 고쳤고 각각 테스트로 확인했다. 그러나 연구 스크립트 전수 검토가 끝나지 않았고(E-1), 옛 0 대체 줄이 남아 있으며(E-2), 수집기의 실제 장기 실행은 확인하지 않았으므로 VERIFIED가 아니다.
