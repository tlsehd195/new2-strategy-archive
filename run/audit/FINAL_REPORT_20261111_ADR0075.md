# 최종 보고 — ADR 0075 Paper Forward 미결 포지션·최종 판정 무결성 (2026-11-11)

기준: main `80a687d`(ADR 0074, PR #75 병합). 합성 데이터·mock·임시 디렉터리만 사용. 실제 API·시장 데이터·잠금/IW/sealed 구간 미접근. 상세: `docs/adr/0075-paper-forward-open-positions-final-verdict.md`.

## A. 발견한 문제
- 실제 결함이었다. 재현 테스트 4개(`TestReproduction0075`)가 수정 전 코드에서 모두 실패했다.
- 경로: `step()` → `_evaluate()`가 `open_positions`를 계산 → `_verdict()`는 `metrics()`(청산 원장 `events.jsonl`만)로 판정, 미결 여부 미확인 → `_conclusion_problems()`도 펀딩·증거만 확인 → `_conclude()`가 PAPER_PASSED 기록.
- 미결 포지션이 있는 채 PAPER_PASSED가 실제로 나왔다(−30 % 손실 미결, 낙폭 한도 초과 상태 포함; 청산 뒤에는 PAPER_FAILED가 맞음).
- 추가로 재현: 원장 중복 줄 이중 집계 → PASS, `_verdict` 뒤 원장 변경을 `_conclude`가 보지 않음. 코드 읽기로 찾고 단위 테스트로 확인: 미결 탐지 루프의 `break`, `late`·체결 상태 등 비교 누락, 삭제 기록 조용히 재추가, `setdefault`로 이전 지표 잔존.

## B. 판정 정책 (paper-forward-positions-v1)
- 정상 미결: 중단 규칙이 처음 성립한 봉을 평가 cutoff로 state에 고정. cutoff까지 진입한 포지션이 열려 있으면 PAPER_OBSERVING(OPEN_POSITIONS_PENDING)으로 청산까지 대기. 평가손익을 판정에 넣지 않고 강제 청산하지 않음. 표본은 cutoff까지 진입한 청산 거래.
- 비정상(원장·평가기·미결 불일치, 수량·가격·시각 누락이나 비정상, 중복, 삭제, cutoff 위조): PAPER_ERROR(단계에서 발견되면 ERROR 고정), 결론 단계에서 발견되면 CONCLUSION_REFUSED → PAPER_BLOCKED_DATA, 아무것도 기록 안 함.
- PAPER_PASSED 보장: [start, cutoff] 진입 거래 전부가 청산까지 관찰·실현되었고, 미실현 손익 미포함, 결론 순간 디스크 원장과 판정 지표 일치, ADR 0073/0074 검사 통과.
- 새 판정 상태 없음(기존 OBSERVING/ERROR/BLOCKED_DATA 사용).

## C. 수정 내역
- 파일: `run/discovery/paper_forward.py`(유일한 엔진 변경), 문서 `docs/adr/0075-…`, `docs/adr/README.md`, `run/discovery/README.md`.
- 함수: 신규 `_open_positions`, `_position_problems`, `_trade_log_len`, `_sample_bound`, `_pending`, `_decide`; 변경 `step`, `_evaluate`, `metrics`, `_verdict`, `_conclusion_problems`(판정 인자 추가), `_conclude`, `_state_problems`.
- 영향: 미결이 없는 단일 심볼 정상 데이터의 판정·지표·이벤트 불변(기준선 테스트 통과). 관찰 끝에 미결이 있으면 결론이 최대 보유 기간(horizon 봉)만큼 늦어짐. 다중 심볼은 표본 경계가 심볼별 마지막 관찰 시각 중 가장 이른 시각.

## D. 테스트 결과
- 신규 37개, 기존 discovery 테스트 918개(이 중 2개 보강: 재시도 단계가 미결 대기가 되는 것을 단언한 뒤 원래의 PASS 단언 유지).
- 로컬 CI(워크플로 단계 재현), Python 3.11과 3.13 각각: unit 955 통과/0 실패, 루트 152 통과, legacy 1 통과, compile·ruff·config·ledger·인벤토리·소스 감사·출처표 통과. bun 트랜스파일·manifest 통과(1회).
- 실행 못함: ShellCheck(환경에 없음, `run_cycle.sh` 변경 없음).
- 수정 중 첫 실행에서 기존 테스트 14개 실패: 12개는 결론 게이트 지표 비교의 내 버그(수정), 2개는 정책 변경(보강).

## E. 기존 기능 회귀
- ADR 0073(펀딩 확인, BLOCKED_DATA, 소스 변경 차단), ADR 0074(경계, 주기, REST 거부, 로그 체인, state head, 워밍업 sha256, legacy 차단): 코드 변경 없음, 기존 테스트 통과, 새 검사는 기존 검사 뒤에 덧붙임.
- 결론 검증: 기존 독립 재검사 유지 + 원장 재읽기·지표·판정 재계산 추가.

## F. 남은 문제
- `events.jsonl`과 `evaluation_cutoff`는 해시 앵커가 없다. state와 원장을 함께 고치거나 cutoff를 규칙이 성립하는 다른 시각으로 옮기는 위조는 탐지 못함.
- 낙폭은 청산 거래 누적 기준(보유 중 미실현 낙폭은 판정 밖).
- 미결 평가손익은 총수익 근사(정보용).
- 다중 심볼 계획은 전체 경로로 시험하지 못함(픽스처가 단일 심볼).
- 평가 중 일반 예외는 기존처럼 전파(state 미저장, 결론 없음).
- GitHub Actions는 계정 수준 실패가 계속됨.

## G. GitHub 반영
- 브랜치 `claude/project-thread-jp2x2d`, 커밋 `cc41882`(코드·테스트·ADR) + 이 보고서 커밋.
- PR #76 https://github.com/tlsehd195/new2-strategy-archive/pull/76
- Actions: research-ci #330(push), #331(pull_request) 모두 2초 만에 failure, runner_id 0, 단계 실행 없음(11-09 이후 같은 계정 수준 실패). CI 통과로 보지 않음.
- 병합: 메모리의 병합 정책(로컬 CI 3.11·3.13 통과 시 자체 병합)에 따라 진행. prereg 활성화·구간 개방 없음.
