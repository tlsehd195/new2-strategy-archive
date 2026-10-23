# ADR 0034 — CI 실패 원인(결제), v1 원장 검사, DRAFT 사전등록의 멤버 점검, 변동성 비례 스프레드 진단

- 상태: Accepted (진단·검사 도구만. 통계 기준·비용 가정·선정 규칙은 그대로)
- 날짜: 2026-10-23
- 범위: `run/discovery/ledger_check.py`, `run/discovery/prereg_gate.py`, `run/discovery/cost_sensitivity.py`,
  `.github/workflows/research-ci.yml`(원장 검사 단계 추가), `run/discovery/tests/test_audit_20261023.py`,
  증거 `run/audit/evidence_audit_20261023/`, 보고서 `run/audit/AUDIT_FOLLOWUP_20261023.md`
- 사용자 요청: 동동, 2026-10-23 지시문 (최신 main CI·PR #30·파이프라인 잔여 문제)
- 함께 본 결정: [ADR 0033](0033-pr30-review-near-entry-duplicates.md)(PR #30 브랜치)

## 맥락
1. main 6291e27의 `test (3.11)`, `test (3.13)`이 2~3초 만에 실패했다. 직전 성공 커밋 c7ec1cc와 코드 트리가 같다.
2. 저장소에 커밋된 v1 엔진 기록(`run/discovery/state.json`, `discovery_ledger.jsonl`)을 `ledger_check.py`로 보면
   `STATE_UNREADABLE`이 나왔다. 이 파일은 v1 엔진 형식(`engine: NEW2-discovery-v1`)이라 v2 저장소 스키마로 읽을 수 없을
   뿐, 내용이 깨진 것은 아니었다. 실제 일관성은 검사되지 않고 있었다.
3. `prereg_gate`는 활성화 기록이 없으면 멤버 파일을 아예 보지 않았다. 그래서 IW1 멤버 파일이 이미 재동결이 필요한
   상태(코드 해시 없음)라는 사실이 DRAFT 보고서에 나오지 않았다.
4. 비용 모델의 반 스프레드는 상수다. 변동성이 높을 때 스프레드가 넓어지는 영향을 볼 수단이 없었다.

## 결정
1. **CI 실패 원인: GitHub 계정 결제.** 각 작업의 check-run 주석이 "The job was not started because recent account
   payments have failed or your spending limit needs to be increased"이다. runner_id 0, 로그 404, 재실행해도 같다.
   코드·설정 문제가 아니므로 CI 설정을 바꾸지 않았다. 같은 `run:` 단계를 로컬 3.11·3.13 가상환경에서 그대로
   실행하는 스크립트(`evidence_audit_20261023/local_ci.py`)로 확인했다. 병합 규칙(GitHub CI 통과 후)은 유지한다.
2. **v1 기록 검사** (`ledger_check.check_v1_archive`): `state.json`이 v1 형식이면 원장에서 다시 계산한 값과 비교한다.
   `tested` = 고유 `cand_hash` 수, `tested_rows` = 행 수, `cursors` = 계열별 고유 후보 수, `cycle`·`last_ts` = 마지막
   행, 생존자·승격 대기열·`stouffer_done`·`survivor_specs`는 생존 판정을 받은 후보만. 어긋나면
   `V1_ARCHIVE_MISMATCH`. 커밋된 기록은 CONSISTENT(136 / 160 / 17주기 / 생존자 0 / 대기열 0).
3. **CI에 원장 검사 단계**: `ledger_check.py .`와 `ledger_check.py state`를 실행한다(읽기만, 불일치면 실패).
4. **DRAFT에서도 멤버 파일 점검** (`prereg_gate.evaluate`): 멤버 파일을 주면 `members_issues`에 동결 무결성 결과를
   보고한다. 수명 단계는 DRAFT 그대로이고 아무것도 열지 않는다. 현재 IW1: "코드 해시 없음 → 재동결".
5. **변동성 비례 스프레드 시나리오** (`cost_sensitivity.spread_vol_scaled`): 기준 사건마다 반 스프레드를
   `max(1, 진입 변동성 / 중앙값)`배로 늘린 값을 해석적으로 더한다(재실행 아님, 청산 봉에도 진입 변동성을 씀).
   DEV 진단일 뿐 판정에 쓰지 않는다. 단조성 검사 대상에 넣었다.
6. 테스트: 커밋된 기록 일관성, 캐시 어긋남·생존자 위조·원장 추가 행·손상 행, DRAFT 멤버 보고, 저장소에 활성화
   기록·독립 검증 등록부·IW1 실행 기록 없음, CI 원장 단계, 테스트 파일이 다른 저장소를 import·경로 참조하지 않음.

## 영향
- `ledger_check.py`, `prereg_gate.py`, `cost_sensitivity.py`는 DEV 평가 코드 해시(`EVO_CODE_FILES`)에 없다(prereg_gate의
  `research_code_identity`도 이 셋을 넣지 않음). 문맥 id는 바뀌지 않는다.
- 결제가 고쳐지기 전에는 GitHub CI 기록이 생기지 않는다. 병합은 그 뒤에 한다(동동 님 결정 대기).
