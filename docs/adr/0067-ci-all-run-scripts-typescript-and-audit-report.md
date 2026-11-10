# ADR 0067 — CI 범위 확장(모든 run/ 스크립트·TS)과 2026-11-10 전수 감사 보고서

- 상태: Accepted (CI·문서)
- 날짜: 2026-11-10
- 요청: 동동, 2026-11-10 전수 감사 지시문 §13 (CI), §14 (문서), §19 (최종 보고서)
- 감사 문제 ID: Q-01, Q-02 — `run/audit/AUDIT_FULL_20261110.md`

## 맥락
- **Q-01:** `research-ci.yml`은 엔진(`run/discovery`)과 `run/*_dev.py`·검정력 감사 도구만 컴파일했다.
  - 폐기 v1 스크립트 84개와 `run/audit/*.py`는 문법 회귀가 생겨도 알 수 없었다.
- **Q-02:** `src/*.ts`(수집기, 페이퍼 체결 모니터)는 CI에 없었다.

## 결정
1. **모든 run/ 스크립트 컴파일과 정적 분석.**
   - `python -m compileall -q run/*.py run/audit/*.py`와 `ruff check --select E9,F63,F7,F82`를 CI 단계로 추가한다.
   - 폐기 스크립트는 컴파일만 하고 실행하지 않는다. 기존 "현재 도구 컴파일" 단계는 이 단계에 포함된다.
2. **분류표 최신 여부.**
   - `run/audit/research_inventory_20261110.py`를 다시 실행한 뒤 `git diff --exit-code`로 결과를 비교한다(ADR 0066).
   - run/*.py를 더하거나 바꾸면 분류표도 함께 갱신해야 한다.
3. **TypeScript 검사(같은 test 작업 안의 단계).**
   - `oven-sh/setup-bun@v2`(bun 1.1.38 고정)로 `bun build --no-bundle`을 실행해 트랜스파일 문법만 검사한다. 번들링과 네트워크는 쓰지 않는다.
   - `src/*_package.json`이 파싱되는지도 확인한다.
   - 형식 검사(tsc)는 하지 않는다. 의존 패키지 형식이 저장소에 없어서 형식 검사를 하면 거짓 실패만 나기 때문이다.
   별도 작업을 두지 않은 이유: 워크플로의 checkout을 하나로 제한하는 독립성 테스트(`test_ci_checks_out_only_this_repository`)를 그대로 지키기 위해서다.
4. **보고서.** `run/audit/AUDIT_FULL_20261110.md`는 지시문 §19의 A~G 형식을 따른다. 내용은 조사 범위, 문제 표, 수정, 분류, 테스트, 미해결, 평가다.

## 결과
- 워크플로 `paths`에 `src/**`를 추가했다.
- 로컬에서 새 단계를 모두 확인했다: 컴파일 137+2개, ruff 통과, TS 4개 트랜스파일 성공, 일부러 넣은 문법 오류는 실패.
- GitHub Actions는 계정 결제 문제로 돌지 않는다. 그래서 병합 판단은 로컬 CI(3.11·3.13)에 의존한다.
