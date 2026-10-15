#!/usr/bin/env python3
# X7 교차검증 — 저장소 엔진 CandleCostModel (costs.py:104-117) 적용.
# 신호/거래 생성은 x7_listing_events_diag.py(사전등록 규격)와 동일 로직 — 결과 재현 보장.
import json, sys
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, "/home/z/my-project/NEW2_run")
from x7_listing_events_diag import (SYMBOLS, SPANS, DELS, HORIZONS, CELLS, BAR,
                                    load_klines, load_events, execute, holm, ms)

OUT = "/home/z/my-project/NEW2_run/results"
FEE, HALF_SPREAD, IMPACT, MAX_PART = 0.0005, 0.0002, 0.1, 0.05
FLAT_C16 = 0.0016

def engine_roundtrip_cost(order_value, qv_entry, qv_exit):
    def side(qv):
        if qv <= 0: return None
        part = order_value / qv
        if part > MAX_PART: return None
        return FEE + HALF_SPREAD + IMPACT * np.sqrt(part)
    a, b = side(qv_entry), side(qv_exit)
    if a is None or b is None: return None
    return a + b

def run_events_qv(k, evs, s_str, e_str):
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    kqv = k["qv"].to_numpy(dtype=float)
    trades = {c: [] for c in CELLS}
    for r in evs:
        if not (s_ms <= r["ts"] < e_ms):
            continue
        for d in DELS:
            t0 = r["ts"] + d * BAR
            ei = np.searchsorted(kot, t0, side="left")
            if ei >= len(kot) or kot[ei] >= e_ms:
                continue
            for h in HORIZONS:
                xi = ei + h
                if xi >= len(kot) or kot[xi] >= e_ms:
                    continue
                if (np.diff(kot[ei:xi + 1]) != BAR).any():
                    continue
                trades[(d, h)].append(((kop[xi] - kop[ei]) / kop[ei], kqv[ei], kqv[xi]))
    return trades

def main():
    report = {"crosscheck_of": "x7_listing_events_results.json", "cost_model": "CandleCostModel (costs.py:104-117)",
              "params": {"fee_rate": FEE, "half_spread": HALF_SPREAD, "impact_coef": IMPACT,
                          "max_participation": MAX_PART, "scenarios": {"o10k": 10_000.0, "o100k": 100_000.0}},
              "cells": []}
    events, _, _ = load_events()
    for sym in SYMBOLS:
        k = load_klines(sym)
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            trades = run_events_qv(k, events, s_str, e_str)
            for d, h in CELLS:
                arr = trades[(d, h)]
                st = {"symbol": sym, "span_idx": si, "delta": d, "horizon": h, "n": len(arr)}
                if st["n"] == 0:
                    st.update({"net_o10k": None, "net_o100k": None})
                    report["cells"].append(st); continue
                r = np.array([x[0] for x in arr])
                st["total_bp"] = float(r.mean() * 1e4)
                st["net_flat16"] = float((r.mean() - FLAT_C16) * 1e4)
                for tag, ov in report["params"]["scenarios"].items():
                    costs, fc = [], 0
                    for _, qv_e, qv_x in arr:
                        c = engine_roundtrip_cost(ov, qv_e, qv_x)
                        if c is None: fc += 1
                        else: costs.append(c)
                    st[f"fail_closed_{tag}"] = fc
                    if len(costs) == st["n"]:
                        cm = float(np.mean(costs))
                        st[f"cost_{tag}_bp"] = cm * 1e4
                        st[f"net_{tag}"] = float((r.mean() - cm) * 1e4)
                    else:
                        st[f"cost_{tag}_bp"] = None
                        st[f"net_{tag}"] = None
                report["cells"].append(st)
    net_pos = {tag: sum(1 for c in report["cells"] if c.get(f"net_{tag}") is not None and c[f"net_{tag}"] > 0) for tag in ("o10k", "o100k")}
    report["verdict"] = {"engine_net_positive_cells": net_pos, "PASS": False,
                         "note": "X7 1차 방향(롱)은 원시에서 이미 음수 — 엔진 수치는 비용벽 문서화용"}
    with open(f"{OUT}/x7_crosscheck_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    L = ["# X7 교차검증 — 엔진 CandleCostModel 적용", "",
         "- 비용 공식: `fee 5bp + half_spread 2bp + 10%*sqrt(participation)` per side",
         "- 1차 방향(공지 후 롱)은 원시 총bp에서 이미 0~음수 — 엔진 순익은 전 셀 음수 예상(문서화용)", ""]
    for sym in SYMBOLS:
        for si in range(2):
            L.append(f"## {sym} span{si+1}")
            L.append("| δ | h | n | 총bp | 정액순16 | 엔진순($10k) | 엔진비용bp | 엔진순($100k) | 엔진비용bp |")
            L.append("|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                if c["symbol"] != sym or c["span_idx"] != si: continue
                fmt = lambda v: "n/a" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))
                L.append(f"| {c['delta']} | {c['horizon']} | {c['n']} | {fmt(c.get('total_bp'))} | "
                         f"{fmt(c.get('net_flat16'))} | {fmt(c.get('net_o10k'))} | {fmt(c.get('cost_o10k_bp'))} | "
                         f"{fmt(c.get('net_o100k'))} | {fmt(c.get('cost_o100k_bp'))} |")
            L.append("")
    L.append("## 결론")
    L.append(f"- 엔진 net>0 셀: $10k {net_pos['o10k']}/36 · $100k {net_pos['o100k']}/36")
    with open(f"{OUT}/x7_crosscheck_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")
    print(json.dumps(report["verdict"], ensure_ascii=False))
    best = sorted([c for c in report["cells"] if c.get("net_o10k") is not None], key=lambda x: -x["net_o10k"])[:3]
    for c in best:
        print(f"best o10k: {c['symbol']} s{c['span_idx']+1} d{c['delta']} h{c['horizon']} net={c['net_o10k']:.2f}bp cost={c['cost_o10k_bp']:.1f}bp")

if __name__ == "__main__":
    main()
