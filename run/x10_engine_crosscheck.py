#!/usr/bin/env python3
# X10 교차검증 — 기존 정액 비용(12/16/20bp) 대신 저장소 엔진 CandleCostModel
# (src/cointrader/backtest/costs.py:104-117 공식)을 그대로 적용해 재계산.
# 신호/거래 생성은 x10_calendar_diag.py(사전등록 규격)와 완전히 동일 로직 — 결과 재현 보장.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
SESSIONS = {"ASIA_0000": 0, "EU_0700": 25_200_000, "US_1330": 48_600_000}
HORIZONS = [16, 48]
FLAT_C16 = 0.0016
DAY_MS = 86_400_000
CELLS = [(ses, h) for ses in SESSIONS for h in HORIZONS]

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

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "volume", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(sub, s_str, e_str):
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = sub["open_time"].to_numpy(dtype=np.int64)
    kop = sub["open"].to_numpy(dtype=float)
    kqv = sub["qv"].to_numpy(dtype=float)
    idx_of = {t: i for i, t in enumerate(kot)}
    trades = {c: [] for c in CELLS}
    first_day = (s_ms + DAY_MS - 1) // DAY_MS * DAY_MS
    for ses, off in SESSIONS.items():
        t0 = first_day + off
        while t0 < e_ms:
            if t0 >= s_ms:
                ei = idx_of.get(t0)
                if ei is not None and ei < len(kot) and kot[ei] < e_ms:
                    for h in HORIZONS:
                        xi = ei + h
                        if xi >= len(kot) or kot[xi] >= e_ms: continue
                        if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                        raw = (kop[xi] - kop[ei]) / kop[ei]
                        trades[(ses, h)].append((raw, kqv[ei], kqv[xi]))
            t0 += DAY_MS
    return trades

def main():
    report = {"crosscheck_of": "x10_calendar_results.json", "cost_model": "CandleCostModel (costs.py:104-117)",
              "params": {"fee_rate": FEE, "half_spread": HALF_SPREAD, "impact_coef": IMPACT,
                          "max_participation": MAX_PART, "scenarios": {"o10k": 10_000.0, "o100k": 100_000.0}},
              "cells": []}
    for sym in SYMBOLS:
        df = load_klines(sym)
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            sub = df[(df["open_time"] >= s_ms) & (df["open_time"] < e_ms)].reset_index(drop=True)
            trades = run_span(sub, s_str, e_str)
            for ses, h in CELLS:
                arr = trades[(ses, h)]
                st = {"symbol": sym, "span": f"{s_str}~{e_str}", "session": ses, "horizon": h, "n": len(arr)}
                if st["n"] == 0:
                    st.update({"net_engine": None, "net_flat16": None, "fail_closed": 0})
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
                        sd = r.std(ddof=1)
                        t = (r.mean() - cm) / (sd / np.sqrt(st["n"])) if sd > 0 else 0.0
                        st[f"t_{tag}"] = float(t)
                        st[f"p_{tag}"] = float(2 * stats.t.sf(abs(t), df=max(st["n"] - 1, 1)))
                    else:
                        st[f"cost_{tag}_bp"] = None
                        st[f"net_{tag}"] = None
                report["cells"].append(st)
    for tag in ("o10k", "o100k"):
        ps = [c.get(f"p_{tag}") if c.get(f"p_{tag}") is not None else 1.0 for c in report["cells"]]
        adj = holm(ps)
        for c, a in zip(report["cells"], adj):
            c[f"p_holm_{tag}"] = float(a)
    report["verdict"] = {}
    for tag in ("o10k", "o100k"):
        passed = [c for c in report["cells"] if c.get(f"p_holm_{tag}", 1.0) < 0.05]
        report["verdict"][tag] = {"holm_pass_cells": [
            f"{c['symbol']} {c['span']} {c['session']} h{c['horizon']} (net={c.get('net_' + tag):.2f}bp)" if c.get("net_" + tag) is not None
            else f"{c['symbol']} {c['span']} {c['session']} h{c['horizon']}" for c in passed]}
    with open(f"{OUT}/x10_crosscheck_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    L = ["# X10 교차검증 — 엔진 CandleCostModel 적용 (정액 16bp 대비)", "",
         "- 비용 공식: `fee 5bp + half_spread 2bp + 10%*sqrt(participation)` per side (costs.py:104-117)",
         "- participation = 주문금액 / 바 거래대금(qv) · 5% 초과 시 fail-closed(제외 카운트)",
         "- 신호/거래 생성: x10_calendar_diag.py(사전등록 규격)와 동일 로직 — 롱 고정", ""]
    for sym in SYMBOLS:
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            L.append(f"## {sym} span{si+1} ({s_str}~{e_str})")
            L.append("| 세션 | h | n | 총bp | 정액순16 | 엔진순($10k) | 엔진비용bp | t | Holm | 엔진순($100k) | 엔진비용bp | FC10k | FC100k |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                if c["symbol"] != sym or c["span"] != f"{s_str}~{e_str}": continue
                fmt = lambda v: "n/a" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))
                L.append(f"| {c['session']} | {c['horizon']} | {c['n']} | {fmt(c.get('total_bp'))} | "
                         f"{fmt(c.get('net_flat16'))} | {fmt(c.get('net_o10k'))} | {fmt(c.get('cost_o10k_bp'))} | "
                         f"{fmt(c.get('t_o10k'))} | {fmt(c.get('p_holm_o10k'))} | {fmt(c.get('net_o100k'))} | "
                         f"{fmt(c.get('cost_o100k_bp'))} | {c.get('fail_closed_o10k', '-')} | {c.get('fail_closed_o100k', '-')} |")
            L.append("")
    L.append("## 결론")
    for tag, v in report["verdict"].items():
        L.append(f"- {tag}: Holm 통과 셀 {len(v['holm_pass_cells'])}개")
        for cell in v["holm_pass_cells"]:
            L.append(f"  - {cell}")
    with open(f"{OUT}/x10_crosscheck_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")
    print(json.dumps(report["verdict"], indent=1, ensure_ascii=False))
    for tag in ("o10k", "o100k"):
        pos = [c for c in report["cells"] if c.get(f"net_{tag}") is not None and c[f"net_{tag}"] > 0]
        print(f"{tag}: net>0 cells = {len(pos)}/24")

if __name__ == "__main__":
    main()
