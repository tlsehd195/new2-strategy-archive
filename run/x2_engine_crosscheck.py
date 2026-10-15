#!/usr/bin/env python3
# X2 교차검증 — 저장소 엔진 CandleCostModel (costs.py:104-117) 적용.
# 신호/거래 생성은 x2_macro_events_diag.py(사전등록 규격)와 동일 로직 — 결과 재현 보장.
import json, sys
import numpy as np
sys.path.insert(0, "/home/z/my-project/NEW2_run")
from x2_macro_events_diag import (SYMBOLS, SPANS, WTYPES, WINS, HORIZONS, CELLS, BAR,
                                  load_klines, load_events, build_trades, ms)

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
    report = {"crosscheck_of": "x2_macro_events_results.json", "cost_model": "CandleCostModel (costs.py:104-117)",
              "params": {"fee_rate": FEE, "half_spread": HALF_SPREAD, "impact_coef": IMPACT,
                         "max_participation": MAX_PART, "scenarios": {"o10k": 10_000.0, "o100k": 100_000.0}},
              "cells": []}
    fomc, cpi, quality = load_events()
    ev_all = fomc + cpi
    for sym in SYMBOLS:
        k = load_klines(sym)
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            in_span, raw, qv, dropped = build_trades(k, ev_all, s_str, e_str)
            for et in WTYPES:
                for w, h in CELLS:
                    rets = [t[0] for t in raw[(et, w, h)]]
                    qvs = qv[(et, w, h)]
                    net = {"o10k": [], "o100k": []}
                    for r, (q1, q2) in zip(rets, qvs):
                        for sc, ov in (("o10k", 10_000.0), ("o100k", 100_000.0)):
                            c = engine_roundtrip_cost(ov, q1, q2)
                            if c is not None:
                                net[sc].append(r - c)
                    cell = {"symbol": sym, "span_idx": si, "etype": et, "W": w, "horizon": h,
                            "n": len(rets), "valid_n": len(rets) / h}
                    for sc in ("o10k", "o100k"):
                        arr = net[sc]
                        cell[f"net_{sc}_mean_bp"] = float(np.mean(arr) * 1e4) if arr else None
                        cell[f"net_{sc}_n"] = len(arr)
                        cell[f"net_{sc}_pos"] = int(np.sum(np.array(arr) > 0)) if arr else 0
                    report["cells"].append(cell)
    # 최고 셀 ID 문자열 부여 (요약 계산 전)
    for c in report["cells"]:
        c["cell"] = f"{c['symbol']} s{c['span_idx']+1} {c['etype']} W{c['W']} h{c['horizon']}"
    for sc in ("o10k", "o100k"):
        key = f"net_{sc}_mean_bp"
        valid = [c for c in report["cells"] if c[key] is not None]
        pos = [c for c in valid if c[key] > 0]
        report[f"summary_{sc}"] = {"cells_with_trades": len(valid), "net_positive_cells": len(pos),
                                   "best_cell": max(valid, key=lambda x: x[key])["cell"] if valid else None,
                                   "best_bp": max((c[key] for c in valid), default=None)}
    for sc in ("o10k", "o100k"):
        valid = [c for c in report["cells"] if c[f"net_{sc}_mean_bp"] is not None]
        if valid:
            report[f"summary_{sc}"]["best_cell"] = max(valid, key=lambda x: x[f"net_{sc}_mean_bp"])["cell"]
            report[f"summary_{sc}"]["best_bp"] = max(c[f"net_{sc}_mean_bp"] for c in valid)
    with open(f"{OUT}/x2_crosscheck_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    L = ["# X2 엔진 교차검증 (CandleCostModel)", "",
         f"- fee 5bp + half_spread 2bp + impact 0.1·sqrt(participation), max_part 5% — $10k/$100k 시나리오", ""]
    for sc in ("o10k", "o100k"):
        s = report[f"summary_{sc}"]
        L.append(f"- **${sc[1:]}**: 거래 보유 셀 {s['cells_with_trades']}/48, 엔진 순익 양수 셀 **{s['net_positive_cells']}/{s['cells_with_trades']}**, 최고 셀 {s['best_cell']} {s['best_bp']:.2f}bp")
    L.append("")
    L.append("| 셀 | n | $10k 순응(bp) | 양수거래/전체 | $100k 순응(bp) |")
    L.append("|---|---|---|---|---|")
    top = sorted([c for c in report["cells"] if c["net_o10k_mean_bp"] is not None],
                 key=lambda x: -x["net_o10k_mean_bp"])[:12]
    for c in top:
        h100 = "n/a" if c["net_o100k_mean_bp"] is None else f"{c['net_o100k_mean_bp']:.2f}"
        L.append(f"| {c['cell']} | {c['n']} | {c['net_o10k_mean_bp']:.2f} | {c['net_o10k_pos']}/{c['net_o10k_n']} | {h100} |")
    with open(f"{OUT}/x2_crosscheck_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")
    for sc in ("o10k", "o100k"):
        s = report[f"summary_{sc}"]
        print(sc, "pos:", s["net_positive_cells"], "/", s["cells_with_trades"], "best:", s["best_cell"], round(s["best_bp"], 2) if s["best_bp"] else None)

if __name__ == "__main__":
    main()
