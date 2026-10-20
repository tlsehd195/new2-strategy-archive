# ADR 0014 — 아카이브 독립, 체결 가져오기, 행동 중복 fp-v2, 탐색 단위 다중검정(promo-v3), 상태·원장 검사

- 상태: Proposed (이 브랜치 `claude/project-thread-xtayok`)
- 날짜: 2026-10-20
- 범위: `run/discovery/cost_model.py`(신규), `fills_import.py`(신규), `ledger_check.py`(신규), `config/promotion_v3.json`(신규), `config/fills_import_v1.json`(신규), `costs_bridge.py`, `cost_calibration.py`, `evolution.py`, `search.py`, `pipeline.py`, `autoloop.py`, `runner.py`, `run_cycle.sh`, `evolution_report.py`, `.github/workflows/research-ci.yml`, 테스트 5개 파일(신규), `README.md`, `STATISTICS_AND_STATES.md`
- 선행: [ADR 0013](0013-audit-search-pool-cost-evidence-alpha-history.md)
- 상세 보고서: `run/audit/AUDIT_FIXES_20261020.md`

## 맥락
사용자 지시문(미해결 문제 수정 및 검증, 2026-10-20)의 다섯 가지 목표를 코드 수준에서 처리했다. 연구 파이프라인의 정상 실행은 전략의 수익성 증명이 아니다. 이 변경으로 통과 전략은 생기지 않았다(0개).

## 결정
1. **NEW2 독립.** 비용 공식을 `cost_model.py`에 다시 구현했다(원본 NEW2@8ec6371 `costs.py`와 무작위 20만 건 비교, 불일치 0). 비용 모델의 정체성은 이 파일의 sha256이다. CI에서 NEW2 체크아웃, `NEW2_READ_TOKEN`, `COINTRADER_SRC`를 제거했다. 이 변경으로 실험 context id가 바뀐다.
2. **체결 가져오기.** `fills_import.py`가 거래내역 CSV/JSON을 검사한다(열·타입·결측·중복·이상치·시간 순서·시간대). 수수료 누락은 0으로 보지 않는다. 수수료 자산 환산은 방법과 가격 시각을 기록한다. 결과는 git에서 제외되는 `private_fills/`에 둔다. 심볼당 200건 기준은 그대로다. 합성 데이터는 `VERIFIED`가 될 수 없다.
3. **비용 상태.** `cost-calib-v4`에 `DATA_INVALID`를 추가했고(중복·격리 행·가져오기 실패), 주문 결과를 관찰할 수 없으면 `INSUFFICIENT_DATA`다. 지시문 어휘는 `evidence_status()` 보기로 연결하고 저장 상태는 늘리지 않았다.
4. **중복 fp-v2.** 정의 지문, 행동 키(구간 교집합·정수 피처·연산자 정규화), 근사 서명(변이 보폭의 절반)을 분리했다. 정확한 중복은 대표 하나만 남기고 이유를 기록하며, 근사 중복은 남기되 검증 배치에는 그룹당 하나만 넣는다. 계보에 세대와 지문 버전을 남긴다.
5. **promo-v3.** 탐색 전체를 하나의 FWER family로 묶어 0.05를 BTC·ETH 창 두 개에 나눈다. 더 엄격해지기만 했다. 이미 v2로 쓴 7개 배치 때문에 이력 전체 상한은 약 0.096이다. legacy 엔진의 잠금 캠페인도 같은 분할을 쓴다.
6. **상태·원장·실행 안전.** `ledger_check.py`(읽기 전용)가 전이 위반, 해시 위조, 중복 평가·등록·승격, 요약 불일치, 중단된 사이클을 찾는다. `run_cycle.sh`는 TERM/INT/HUP을 runner에 전달하고 `CYCLE_FAILED reason=signal`로 끝낸다. evolve 실행은 SIGTERM에 CYCLE_FAILED를 남기고 다시 실행하면 이어진다. `--dev-only`는 검증·잠금 창을 읽지 않는다.

## 검증
- NEW2 없는 깨끗한 복사본에서 전체 테스트 489개 OK (기준선 427 + 신규 62). compileall, ruff(CI 설정), shellcheck 통과. 신규 모듈 mypy 통과.
- DEV-only 재평가(실데이터 f6379f02, 공개 추정 비용): 777 평가, DEV 통과 41, 검증 0, alpha 소비 0, `ledger_check` CONSISTENT.
- R4의 검증 결과(기각 7, 미결 13, 통과 0)는 새 context에서 REVERIFY_REQUIRED다. 새 독립 창이 없어 다시 검증하지 못했다.

## 한계
- 실제 계정 체결이 없어 비용은 `ESTIMATED_FROM_PUBLIC_DATA`이고 실체결 기준으로는 `INSUFFICIENT_DATA`다.
- 과거 `run/*.py` 1회성 스크립트와 `NEW2_DATA_DIR` 기본값이 옛 작업공간 경로를 가리킨다(엔진 밖, fail-closed).
- 이 변경분의 GitHub Actions(Python 3.11/3.13)는 PR에서 처음 실행된다.
