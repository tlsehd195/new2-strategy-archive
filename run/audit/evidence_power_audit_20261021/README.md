# 거짓 음성 점검 증거 (ADR 0022, 2026-10-21)

| 파일 | 내용 | 데이터 |
|---|---|---|
| `synthetic_power.json` | 엣지를 심은 가상 전략의 관문별 통과율, 관문 하나 뺀 통과율, 설계별 필요 샤프 | **가상(난수)** |
| `r6_ledger_audit.json` | R6 재현 원장에서 비용 탈락·거래 수 부족 후보 요약 | 실데이터 DEV 기록 |
| `strong_search_cost_sensitivity.json` | ADR 0019 탐색을 비용 가정 3가지로 다시 계산한 요약 | 실데이터 DEV(S1/S2) |
| `cost_per_side_by_symbol_S1.txt` | 1만 USDT 한쪽 비용: 가정 비용 vs 공개 호가 추정, 종목별 | 실데이터 거래대금(2021-01~2023-09) |
| `diag_cost_*.json` | 진단용 비용 가정 2개(엔진 설정 아님, 판정에 쓰지 않음) | — |

## 재현
```
# 1) 가상 검정력 (약 1분)
python3 run/power_audit_synthetic.py --out synthetic_power.json --sims 2000 --entry-t 1.6863

# 2) R6 재현 (약 50분, ADR 0015 명령 그대로) 후 원장 요약
env -u COINTRADER_SRC NEW2_DATA_DIR=<unzipped f6379f02> NEW2_DISCOVERY_ROOT=<fresh> NEW2_DISC_MODE=evolve \
  NEW2_COST_PROFILE=public_estimate:data/public_market_estimate/estimate_v1.json \
  python3 run/discovery/runner.py --mode evolve --dev-only --rounds 8 --evals 4000
python3 run/power_audit_ledger.py --state <fresh>/state/evolution --out r6_ledger_audit.json
#    → CYCLE_OK, context C2-f64e792160a0222650f2, REJECTED 737 / INCONCLUSIVE 93 / RESEARCH_ONLY 64 (R6과 동일)
#    --entry-t 1.6863 = 64개 DEV 통과 후보의 20번째 DEV t(상한 6)

# 3) 비용 민감도: run/strong_search_dev.py(커밋 88caa15, PR 대기 중)의
#    COST_PROFILE 한 줄만 환경변수로 바꾼 사본으로 같은 --data-dir에서 3번 실행
#    (f6379f02의 BTC/ETH + alts 묶음의 BNB/XRP/TRX/ZEC). 가정 비용 실행은 원래 결과와 숫자가 정확히 같다.
```
읽지 않은 것: IW1(2020), BTC/ETH 검증·잠금 구간, 2025-10-21 이후. 검증 배치·알파 소비·잠금 사용 0, `validation_window_history.jsonl` 변경 없음.
