#!/usr/bin/env python3
# X4/F3 교차검증 — 저장소 엔진 CandleCostModel (costs.py:104-117) 적용.
# 신호/거래 생성은 x4f3_pair_spread_diag.py(사전등록 규격)와 동일 로직 — 결과 재현 보장.
import json, sys
import numpy as np
sys.path.insert(0, "/home/z/my-project/NEW2_run")
from x4f3_pair_spread_diag import (SPANS, LEGS, CELLS, load_klines, spread_z,
                                   run_span_leg, ms)

OUT = "/home/z/my-project/NEW2_run/results"
FEE, HALF_SPREAD, IMPACT, MAX_PART = 0.0005, 0.0002, 0.1, 0.05

def engine_roundtrip_cost(order_value, qv_entry, qv_exit):
    def side(qv):
        if qv <= 0: return None
        part = order_value / qv
        if part > MAX_PART: return None
        return FEE + HALF_SPREAD + IMPACT * np.sqrt(part)
    a, b = side(qv_entry), side(qv_exit)
    if a is None or b is None: return None
    return a + b

def main():
    report = {"crosscheck_of": "x4f3_pair_spread_results.json", "cost_model": "CandleCostModel (costs.py:104-117)",
              "params": {"fee_rate": FEE, "half_spread": HALF_SPREAD, "impact_coef": IMPACT,
                         "max_participation": MAX_PART, "scenarios": {"o10k": 10_000.0, "o100k": 100_000.0}},
              "cells": []}
    eth = load_klines("ETHUSDT")
    btc = load_klines("BTCUSDT")
    common, z_by_L, _ = spread_z(eth, btc)
    klines = {"ETHUSDT": eth, "BTCUSDT": btc}
    for leg in LEGS:
        k = klines[leg]
        for si, (s_str, e_str) in enumerate(SPANS[leg]):
            trades, qvs, dropped = run_span_leg(leg, k, common, z_by_L, s_str, e_str)
            for (L, thr, h) in CELLS:
                rets = trades[(L, thr, h)]
                net = {"o10k": [], "o100k": []}
                for r, (q1, q2) in zip(rets, qvs[(L, thr, h)]):
                    for sc, ov in (("o10k", 10_000.0), ("o100k", 100_000.0)):
                        c = engine_roundtrip_cost(ov, q1, q2)
                        if c is not None:
                            net[sc].append(r - c)
                cell = {"leg": leg, "span_idx": si, "L": L, "thr": thr, "horizon": h,
                        "cell": f"{leg} s{si+1} L{L} t{thr} h{h}", "n": len(rets), "valid_n": len(rets) / h}
                for sc in ("o10k", "o100k"):
                    arr = net[sc]
                    cell[f"net_{sc}_mean_bp"] = float(np.mean(arr) * 1e4) if arr else None
                    cell[f"net_{sc}_pos"] = int(np.sum(np.array(arr) > 0)) if arr else 0
                report["cells"].append(cell)
    for sc in ("o10k", "o100k"):
        key = f"net_{sc}_mean_bp"
        valid = [c for c in report["cells"] if c[key] is not None]
        pos = [c for c in valid if c[key] > 0]
        report[f"summary_{sc}"] = {"cells_with_trades": len(valid), "net_positive_cells": len(pos),
                                   "best_cell": max(valid, key=lambda x: x[key])["cell"],
                                   "best_bp": max(c[key] for c in valid)}
    with open(f"{OUT}/x4f3_crosscheck_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    L = ["# X4/F3 엔진 교차검증 (CandleCostModel)", "",
         "- fee 5bp + half_spread 2bp + impact 0.1·sqrt(participation), max_part 5% — $10k/$100k", ""]
    for sc in ("o10k", "o100k"):
        s = report[f"summary_{sc}"]
        L.append(f"- **${sc[1:]}**: 거래 보유 셀 {s['cells_with_trades']}/96, 엔진 순익 양수 셀 **{s['net_positive_cells']}/{s['cells_with_trades']}**, 최고 셀 {s['best_cell']} {s['best_bp']:.2f}bp")
    with open(f"{OUT}/x4f3_crosscheck_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")
    for sc in ("o10k", "o100k"):
        s = report[f"summary_{sc}"]
        print(sc, "pos:", s["net_positive_cells"], "/", s["cells_with_trades"], "best:", s["best_cell"], round(s["best_bp"], 2))

if __name__ == "__main__":
    main()
