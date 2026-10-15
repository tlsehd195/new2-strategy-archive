#!/usr/bin/env python3
# P1 교차검증 — 기존 정액 비용(12/16/20bp 왕복) 대신 저장소 엔진의 CandleCostModel
# (src/cointrader/backtest/costs.py:104-117 공식)을 그대로 적용해 재계산.
# 신호/거래 생성은 fr_extreme_diag.py(사전등록 규격)와 완전히 동일 로직 — 결과 재현 보장.
# 비용 모델 파라미터(명시): fee_rate=0.0005(Binance USD-M taker VIP0), half_spread=0.0002,
# impact_coef=0.1, max_participation=0.05 — CandleCostModel 기본값 + 실제 테이커 수수료.
# 주문 크기 시나리오: $10k / $100k (participation = order_value / bar_qv).
import json, glob, os, sys
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
BUCKETS = {"B80": 1.2816, "B90": 1.6449}
HORIZONS = [4, 16, 48]
N90 = 90
FLAT_C16 = 0.0016  # 기존 진단의 왕복 16bp(비교 기준)

# --- 엔진 비용 모델 (costs.py:104-117 공식 그대로) ---
FEE, HALF_SPREAD, IMPACT, MAX_PART = 0.0005, 0.0002, 0.1, 0.05

def engine_roundtrip_cost(order_value, qv_entry, qv_exit):
    """왕복 비용 = 진입 per-side + 청산 per-side. participation 초과 시 None(fail-closed)."""
    def side(qv):
        if qv <= 0: return None
        part = order_value / qv
        if part > MAX_PART: return None
        return FEE + HALF_SPREAD + IMPACT * np.sqrt(part)
    a, b = side(qv_entry), side(qv_exit)
    if a is None or b is None: return None
    return a + b

# --- 데이터 로드 (fr_extreme_diag.py와 동일) ---
def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df

def load_funding(sym):
    files = sorted(glob.glob(f"{BASE}/funding/{sym}-fundingRate-*.csv"))
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df = df.drop_duplicates("calc_time").sort_values("calc_time").reset_index(drop=True)
    return df

def funding_z(rates, nwin=N90):
    r = rates.to_numpy(dtype=float)
    z = np.full(len(r), np.nan)
    for i in range(nwin, len(r)):
        w = r[i - nwin:i]
        s = w.std(ddof=1)
        if s > 0:
            z[i] = (r[i] - w.mean()) / s
    return z

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, f, zs, s_str, e_str):
    """fr_extreme_diag.py run_span과 동일하되 trades에 (ret, qv_entry, qv_exit) 수집."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    ot = k["open_time"].to_numpy(dtype=np.int64)
    op = k["open"].to_numpy(dtype=float)
    qv = k["qv"].to_numpy(dtype=float)
    sig_t, sig_z = [], []
    for t, z in zip(f["calc_time"].to_numpy(dtype=np.int64), zs):
        if s_ms <= t < e_ms and np.isfinite(z):
            sig_t.append(t); sig_z.append(z)
    trades = {}
    for b in BUCKETS:
        for h in HORIZONS:
            trades[(b, h)] = []
    for T, z in zip(sig_t, sig_z):
        ei = np.searchsorted(ot, T, side="right")
        if ei >= len(ot) or ot[ei] >= e_ms:
            continue
        for b, thr in BUCKETS.items():
            if abs(z) < thr: continue
            direction = -1.0 if z > 0 else 1.0
            for h in HORIZONS:
                xi = ei + h
                if xi >= len(ot) or ot[xi] >= e_ms: continue
                seg = ot[ei:xi + 1]
                if (np.diff(seg) != 900000).any(): continue
                ret = direction * (op[xi] - op[ei]) / op[ei]
                trades[(b, h)].append((ret, qv[ei], qv[xi]))
    return trades

def main():
    klines, fundings = {}, {}
    for sym in SYMBOLS:
        klines[sym] = load_klines(sym)
        fundings[sym] = load_funding(sym)
    scenarios = {"o10k": 10_000.0, "o100k": 100_000.0}
    report = {"crosscheck_of": "fr_extreme_results.json", "cost_model": "CandleCostModel (costs.py:104-117)",
              "params": {"fee_rate": FEE, "half_spread": HALF_SPREAD, "impact_coef": IMPACT,
                          "max_participation": MAX_PART, "scenarios": scenarios}, "cells": []}
    for sym in SYMBOLS:
        k = klines[sym]; f = fundings[sym]
        zs_all = funding_z(f["last_funding_rate"])
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            trades = run_span(k, f, zs_all, s_str, e_str)
            for b in BUCKETS:
                for h in HORIZONS:
                    arr = trades[(b, h)]
                    st = {"symbol": sym, "span": f"{s_str}~{e_str}", "bucket": b, "horizon": h, "n": len(arr)}
                    if st["n"] == 0:
                        st.update({"net_engine": None, "net_flat16": None, "fail_closed": 0})
                        report["cells"].append(st); continue
                    r = np.array([x[0] for x in arr])
                    st["total_bp"] = float(r.mean() * 1e4)
                    st["net_flat16"] = float((r.mean() - FLAT_C16) * 1e4)
                    for tag, ov in scenarios.items():
                        costs = []
                        fc = 0
                        for _, qv_e, qv_x in arr:
                            c = engine_roundtrip_cost(ov, qv_e, qv_x)
                            if c is None: fc += 1
                            else: costs.append(c)
                        st[f"fail_closed_{tag}"] = fc
                        if len(costs) == st["n"]:
                            cm = float(np.mean(costs))
                            st[f"cost_{tag}_bp"] = cm * 1e4
                            st[f"net_{tag}"] = float((r.mean() - cm) * 1e4)
                            sd = r.std(ddof=1); vn = st["n"] / h
                            t = (r.mean() - cm) / (sd / np.sqrt(vn)) if sd > 0 else 0.0
                            st[f"t_{tag}"] = float(t)
                            st[f"p_{tag}"] = float(2 * stats.t.sf(abs(t), df=max(vn - 1, 1)))
                        else:
                            st[f"cost_{tag}_bp"] = None
                            st[f"net_{tag}"] = None
                    report["cells"].append(st)
    # Holm: 시나리오별 24셀 p 재보정
    for tag in scenarios:
        ps = [c.get(f"p_{tag}", 1.0) if c.get(f"p_{tag}") is not None else 1.0 for c in report["cells"]]
        adj = holm(ps)
        for c, a in zip(report["cells"], adj):
            c[f"p_holm_{tag}"] = float(a)
    report["verdict"] = {}
    for tag in scenarios:
        passed = [c for c in report["cells"] if c.get(f"p_holm_{tag}", 1.0) < 0.05]
        report["verdict"][tag] = {"holm_pass_cells": [
            f"{c['symbol']} {c['span']} {c['bucket']} h{c['horizon']}" for c in passed]}
    with open(f"{OUT}/fr_crosscheck_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    # 마크다운 요약
    L = ["# P1 교차검증 — 엔진 CandleCostModel 적용 (정액 16bp 대비)", "",
         "- 비용 공식: `fee 5bp + half_spread 2bp + 10%*sqrt(participation)` per side (costs.py:104-117)",
         "- participation = 주문금액 / 바 거래대금(qv) · 5% 초과 시 fail-closed(제외 카운트)",
         "- 신호/거래 생성: fr_extreme_diag.py(사전등록 규격)와 동일 로직", ""]
    for sym in SYMBOLS:
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            L.append(f"## {sym} span{si+1} ({s_str}~{e_str})")
            L.append("| 버킷 | h | n | 총bp | 정액순16 | 엔진순($10k) | 엔진비용bp | t | Holm | 엔진순($100k) | 엔진비용bp | FC10k | FC100k |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                if c["symbol"] != sym or c["span"] != f"{s_str}~{e_str}": continue
                fmt = lambda v: "n/a" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))
                L.append(f"| {c['bucket']} | {c['horizon']} | {c['n']} | {fmt(c.get('total_bp'))} | "
                         f"{fmt(c.get('net_flat16'))} | {fmt(c.get('net_o10k'))} | {fmt(c.get('cost_o10k_bp'))} | "
                         f"{fmt(c.get('t_o10k'))} | {fmt(c.get('p_holm_o10k'))} | {fmt(c.get('net_o100k'))} | "
                         f"{fmt(c.get('cost_o100k_bp'))} | {c.get('fail_closed_o10k', '-')} | {c.get('fail_closed_o100k', '-')} |")
            L.append("")
    L.append("## 결론")
    for tag, v in report["verdict"].items():
        L.append(f"- {tag}: Holm 통과 셀 {len(v['holm_pass_cells'])}개 {v['holm_pass_cells'] if v['holm_pass_cells'] else ''}")
    with open(f"{OUT}/fr_crosscheck_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")
    print(json.dumps(report["verdict"], indent=1))
    # 최고 셀 요약 출력
    cells = [c for c in report["cells"] if c.get("net_o10k") is not None]
    cells.sort(key=lambda x: -x["net_o10k"])
    print("TOP5 by net_o10k:")
    for c in cells[:5]:
        print(f"  {c['symbol']} {c['span']} {c['bucket']} h{c['horizon']} — total {c['total_bp']:.1f}bp, "
              f"flat16 {c['net_flat16']:.1f}bp, engine10k {c['net_o10k']:.1f}bp, holm {c['p_holm_o10k']:.4f}")

if __name__ == "__main__":
    main()
