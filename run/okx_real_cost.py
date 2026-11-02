"""실거래 체결 기록(research/okx_real/real_fills.csv)에서 왕복 비용을 역산한다.
연구 전용: 자료는 사용자가 올린 본인 체결 기록뿐이며 어떤 시장 구간도 열지 않는다.
사용: python run/okx_real_cost.py [csv경로]
"""
import csv, sys, statistics as st

def f(x):
    return float(x) if x not in ("", None) else None

def analyze(row):
    e, x, n, pnl = f(row["entry"]), f(row["exit"]), f(row["notional_usdt"]), f(row["realized_pnl_usdt"])
    sgn = 1 if row["side"].lower() == "long" else -1
    qty = n / e                      # 진입 명목 기준 수량
    gross = sgn * qty * (x - e)      # 가격 변동만의 총손익
    move_bp = sgn * (x / e - 1) * 1e4
    cost = gross - pnl               # 수수료+펀딩(+슬리피지 잔차)
    cost_bp = cost / n * 1e4         # 진입 명목 대비 왕복 bp
    out = dict(no=row["no"], symbol=row["symbol"], move_bp=move_bp, gross=gross,
               realized=pnl, cost_usdt=cost, cost_bp=cost_bp)
    fee, fund = f(row.get("fee_usdt")), f(row.get("funding_usdt"))
    if fee is not None:
        out["fee_bp"] = fee / n * 1e4
    if fund is not None:
        out["funding_bp"] = fund / n * 1e4
    return out

def main(path):
    rows = [analyze(r) for r in csv.DictReader(open(path, encoding="utf-8"))]
    for r in rows:
        print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    by = {}
    for r in rows:
        by.setdefault(r["symbol"], []).append(r["cost_bp"])
    for s, v in by.items():
        print(f"{s}: n={len(v)} 평균 {st.mean(v):.1f}bp 중앙 {st.median(v):.1f}bp 필요(검증용) 200건, 부족 {max(0,200-len(v))}")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "research/okx_real/real_fills.csv")
