# ADR 0063 — 비용 증거: 실계좌 체결만 VERIFIED, 비용 구성요소별 처리·증거 표

- 상태: Accepted (코드·테스트, 기준 숫자 변경 없음)
- 날짜: 2026-11-10
- 요청: 동동, 2026-11-10 전수 감사 지시문 §8 (실제 체결비용 검증 체계)
- 감사 문제 ID: C-01 (Critical), C-02 (Medium) — `run/audit/AUDIT_FULL_20261110.md`

## 맥락
- `fills_import.py`의 자료 출처는 `user_export` / `synthetic_test` 둘뿐이었다. 계좌가 실계좌인지 모의(데모)인지 적는 칸이 없었다.
- `cost_calibration.calibrate_and_verify`는 `exchange_fills` 관측이 기준(종목별 검증 체결 200건, 평균·p90 과소추정 한도, 거부율)을 채우면 VERIFIED를 냈다.
- 그래서 **모의투자 계정의 거래 내보내기**를 `user_export`로 가져오면 체결 수만 충분할 때 비용 모델이 VERIFIED가 될 수 있었다(재현: `tests/test_cost_evidence_20261110.py`의 `test_demo_paper_and_undeclared_accounts_never_verify`가 수정 전 코드에서 VERIFIED를 받는다). 2026-11-02 OKX 모의 기록(ADR 0058)이 처음에 실계좌로 기록됐던 것과 같은 경로다.
- 비용 결과에는 "UNVERIFIED/ASSUMED/ESTIMATED" 상태 하나만 붙어 있어 수수료·스프레드·충격·부분체결·지연·펀딩 중 무엇이 모델링되고 무엇이 빠졌는지 기록에서 알 수 없었다.

## 결정
1. **계좌 종류 필수.** `ACCOUNT_KINDS = real | demo | paper | unknown`.
   - `fills_import.py` CLI는 `--account-kind`가 필수(기본값 없음). 라이브러리 기본값은 `unknown`.
   - `calibrate_and_verify(..., account_kind=)`: `exchange_fills`가 있는데 `real`이 아니면 `BLOCKED_EXTERNAL_EVIDENCE`(모델을 판정하지 않음). 무결성 문제(DATA_INVALID)가 먼저다.
   - `check_record`: `account_kind == "real"`이 아닌 VERIFIED 기록(ADR 0063 이전 기록 포함, 현재 0건)은 거부 → `CostContext.from_verification` 실패, `evidence_status` = NOT_CALIBRATED.
2. **증거 등급 표** `EVIDENCE_TIERS` / `evidence_tier()` (지시문 §8-2): REAL_ACCOUNT_FILLS, PUBLIC_BOOK_SNAPSHOTS, PAPER_FILLS, OHLCV, DEMO_ACCOUNT_RECORDS(+UNDECLARED_ACCOUNT). 기록마다 `evidence_tiers`를 남긴다. 아래 등급을 위로 올리는 경로는 없다.
3. **구성요소 표** `costs_bridge.cost_component_evidence()`: 메이커/테이커 수수료, 스프레드, 슬리피지, 시장 충격, 부분 체결, 체결 지연, 펀딩, 롱·숏 비대칭, 규모·변동성·종목 의존성마다 처리 방식(MODELLED / NOT_MODELLED / MEASURED)과 증거 수준을 적는다. 모든 평가 기록의 `cost_model.components`에 들어간다(보고용, 판정에 쓰지 않음).

## 결과
- 기준 숫자(`cost_verification_v1.json`)는 그대로다. VERIFIED 요건이 하나 늘었을 뿐 완화된 것은 없다.
- 기존 테스트 4개 파일의 합성 체결 과정은 "실계좌 체결을 흉내 낸 것"이므로 `account_kind="real"`을 명시하게 바꿨다(판정 조건 변경 없음). 모의·미기재 경우는 새 테스트가 따로 검사한다.
- 평가 기록 내용(`cost_model` 블록)이 바뀌므로 새 기록의 record_id가 달라진다. 실험 컨텍스트(코드 해시)도 바뀐다.
- 남은 일(외부 증거 필요): 실계좌 체결 0건. 종목별 검증 체결 200건 이상(보정 절반 별도 → 총 약 400건/종목)과 주문 전체 로그(미체결 포함)가 있어야 VERIFIED 판정이 가능하다. 부분 체결·지연·변동성 의존 비용은 모델에 없다(표에 NOT_MODELLED로 표시).
