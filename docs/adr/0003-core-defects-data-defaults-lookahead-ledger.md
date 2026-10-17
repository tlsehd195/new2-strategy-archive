# ADR 0003 — 핵심 결함 수정: 15분봉 무결성, 기본값 단일 해석, 시점 누출, 원장 복구

- 상태: Accepted (PR #3 병합)
- 날짜: 2026-10-15
- 범위: `run/discovery/{data_quality.py,data_exceptions.json,evaluator.py,engine_lib.py,generator.py,runner.py,store.py,generation.py}`
- 선행: [ADR 0002](0002-fdr-family-notions-and-run-cycle-exit-codes.md)

## 맥락
자동 진화 지시문 2장의 결함을 코드로 재현해 확인했다.
- 간격 검사가 `gap > 2×15분`일 때만 실패해 **봉 하나가 빠진 30분 간격을 통과**시켰다. CSV 내부 파싱 실패 행은 조용히 건너뛰었다.
- `build_aux`가 기본값이 제거된 정규화 파라미터를 읽어 `ret_rev` 기본 lookback 4가 32로, `fund_z` 기본 window 30이 90으로 실행되었다. **같은 ID에 다른 신호**가 만들어진 것이다.
- `fund_z` z-점수를 15분봉 계단 함수에 계산해 거의 발동하지 않았다. 펀딩 정산이 빠진 구간은 비용을 적게 합산했다.
- `iter_jsonl`이 손상된 원장 중간 행을 건너뛰었다. 원장 기록 직후 상태 기록 전에 중단되면 재시작 때 같은 평가가 중복 실행되었다.
- 검증 구간 인덱스가 데이터가 자정에 시작한다고 가정했다.

## 결정
1. **정확한 15분 격자**: 간격이 15분을 넘으면 `gap//15분 − 1`개의 누락 봉으로 세고 BLOCK 처리한다. 거래소의 문서화된 예외만 `data_exceptions.json`(빈 목록에서 시작)으로 WARN 처리하고, 그 파일의 해시는 실험 컨텍스트에 들어간다.
   - 품질 판정은 OK/WARN/BLOCK 하나로 통일했다. 코드는 `PARSE_ERROR`, `TORN_TAIL`, `DUPLICATE_TS`, `TIME_INVERSION`, `MISALIGNED_TS`, `MISSING_BARS`, `NON_FINITE`, `BAD_OHLC`, `NEGATIVE_VOLUME`, `TAKER_GT_VOLUME`, `STALE` 등이다.
   - 헤더는 첫 줄에서만 인정한다. 줄바꿈 없이 잘린 마지막 행은 `TORN_TAIL`로 내부 손상과 구분한다.
   - BLOCK 데이터는 백테스트에 들어가지 않는다(`DATA_MISSING`).
2. **기본값 단일 해석기** `generator.resolve_params`를 신호·보조지표·명세 변환이 모두 사용한다. `FAMILY_DEFAULTS`는 SHA-256으로 고정해, 바꾸면 import 단계에서 실패한다. 선언된 `_defaults`가 레지스트리와 다르면 `DefaultsMismatch`가 난다.
3. **시점**
   - 펀딩 z는 "마지막으로 공개된 정산값"을 그 이전 정산 window개와 비교한다(봉 단위가 아님).
   - 정산 공백과 겹치는 보유 구간은 버린다(작게 합산하지 않음).
   - FNG는 하루 늦게(D−1까지) 사용한다.
   - embargo는 `max(보유기간, 96봉)`이며, 구간 인덱스는 실제 타임스탬프로 계산한다.
   - 모든 계열에 대해 "미래를 오염시켜도 과거 신호가 같은지" 접두 불변성 테스트를 둔다.
4. **원장 무결성**
   - 내부 손상 행은 `JsonlCorruption`으로 실행을 멈춘다. 운영자가 `acknowledge_corrupt_line`으로 격리 파일에 원문을 보존한 경우에만 진행한다.
   - 잘린 꼬리 행은 다음 append 전에 격리하고 복구한다.
   - 평가 ID를 RUNNING 전에 기록해 두고, 재시작 때 원장에 같은 ID가 있으면 재실행하지 않고 그 결과를 적용한다(`recovered_from_ledger`).

## 결과
- `tests/test_core_defects.py` 27개 추가. 이 단계 기준 전체 239개가 통과했다.
- 실데이터에서는 검증하지 못했다. 이 환경에 시장 데이터가 없고 거래소 아카이브 접근도 막혀 있다(BLOCKED_DATA).
- 남은 위험: 문서화된 거래소 예외 목록이 비어 있다. 실제 점검 시간대가 생기면 근거와 함께 추가해야 하며, 그때까지는 해당 월이 BLOCK된다(보수적).
