# ADR 0011 — 공개 호가 80시간 수집분으로 첫 비용 추정 (ESTIMATED_FROM_PUBLIC_DATA)

- 상태: Accepted
- 날짜: 2026-10-20
- 범위: `data/public_market_estimate/estimate_v1.json`, `data/public_market_estimate/collect_manifest.json` (코드 변경 없음)
- 선행: [ADR 0007](0007-assumed-cost-profile.md), [ADR 0008](0008-residual-audit-cost-estimate-lookahead-forward-integrity.md), [ADR 0009](0009-locked-test-requires-verified-cost.md)

## 맥락
사용자가 Windows PC에서 `public_market_collect.py`로 BTCUSDT·ETHUSDT 공개 호가를 수집해 `public_market.zip`(sha256 `97b5f86b…`, 압축 해제 166MB)로 올렸다. 수집기는 이 환경에서 실행하지 않았다. 매니페스트가 이미 있어 `--manifest-only`는 필요하지 않았다.

## 결과
- `public_cost_estimate.py --in <dir> --out estimate_v1.json` → `ESTIMATED_FROM_PUBLIC_DATA` (record `PE-371c80b76eeba61b6dd27843`, 기준 v2 그대로, 완화 없음).
- `--verify` → `ok: true`, 재계산 상태 동일.
- 종목별 사용 가능: 각 79.997시간, 30.0초 간격, 수집 공백 0, 유효 스냅샷 BTC 9,600 / ETH 9,599(ETH 1건은 호가·마감 봉 누락으로 무효).
- 엔진 매개변수: half_spread 1.92e-4(ETH 값, 종목 간 큰 쪽), impact_coefficient 1.0013e-3(ETH), fee_rate 5e-4(공개 수수료표 가정값, 관측 아님), max_participation 0.05.
- 지원 최대 주문 크기 100,000 USDT. 1,000,000 USDT는 50단계 호가로 못 채우는 스냅샷이 BTC 85%, ETH 25%라 적합에서 제외됐다.

## 한계
공개 호가는 체결이 아니므로 등급은 추정이다. 수집 구간(약 3.3일, 2026-10-16~19)의 한 시장 상황만 반영한다. 수수료는 가정값이다. 잠금 TEST는 계속 닫혀 있고(ADR 0009/0010) 승격 기준은 바뀌지 않는다. VERIFIED에는 계정 체결 기록이 필요하다.
