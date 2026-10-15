#!/usr/bin/env python3
# A1 교차검증 — 기존 정액 비용(12/16/20bp) 대신 저장소 엔진 CandleCostModel
# (src/cointrader/backtest/costs.py:104-117 공식)을 그대로 적용해 재계산.
# 신호/거래 생성은 a1_illiq_diag.py(사전등록 규격)와 완전히 동일 로직 — 결과 재현 보장.
# A1 특유 쟁점: 고비유동 바에서 진입 → 참여율(order_value/qv)이 5% 상한을 넘어 fail-closed
# 되는 비중이 경제성 자체를 판정함(비유동성 시점 진입의 실현 가능성 정량).
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
LOOKBACKS = [96, 288, 960, 2880]
BUCKETS = {"B80": 1.2816, "B90": 1.6449}
HORIZONS = [4, 16, 48]
FLAT_C16 = 0.0016
NWIN = 960
CELLS = [(b, L, h) for b in BUCKETS for L in LOOKBACKS for h in HORIZONS]

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
    for c in ["open_time", "open", "close", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df

def illiq_z(close, ot, qv, L):
    c = np.asarray(close, dtype=float)
    t_ = np.asarray(ot, dtype=np.int64)
    q = np.asarray(qv, dtype=float)
    r = np.full(len(c), np.nan)
    r[1:] = np.log(c[1:] / c[:-1])
    gap = t_[1:] - t_[:-1]
    r[1:][gap != 900000] = np.nan
    r[0] = np.nan
    illiq = np.where(q > 0, np.abs(r) / np.where(q > 0, q, 1.0), np.nan)
    s = pd.Series(illiq)
    roll = s.rolling(L).mean().to_numpy()
    m = pd.Series(roll).rolling(NWIN).mean().shift(1).to_numpy()
    sd = pd.Series(roll).rolling(NWIN).std(ddof=1).shift(1).to_numpy()
    return np.where((~np.isnan(m)) & (~np.isnan(sd)) & (sd > 0) & np.isfinite(roll), (roll - m) / sd, np.nan)

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def main():
    report = {"crosscheck_of": "a1_illiq_results.json", "cost_model": "CandleCostModel (costs.py:104-117)",
              "params": {"fee_rate": FEE, "half_spread": HALF_SPREAD, "impact_coef": IMPACT,
                          "max_participation": MAX_PART, "scenarios": {"o10k": 10_000.0, "o100k": 100_000.0}},
              "cells": []}
    for sym in SYMBOLS:
        df = load_klines(sym)
        ot = df["open_time"].to_numpy(dtype=np.int64)
        close = df["close"].to_numpy(dtype=float)
        qv = df["qv"].to_numpy(dtype=float)
        kop = df["open"].to_numpy(dtype=float)
        zmaps = {L: illiq_z(close, ot, qv, L) for L in LOOKBACKS}
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            in_span = (ot >= s_ms) & (ot < e_ms)
            trades = {c: [] for c in CELLS}
            fc_entry_part = {c: 0 for c in CELLS}
            for i in range(len(ot) - 1):
                t = ot[i]
                if not (s_ms <= t < e_ms): continue
                if ot[i + 1] != t + 900000: continue
                ei = i + 1
                if ot[ei] >= e_ms: continue
                for b, thr in BUCKETS.items():
                    for L in LOOKBACKS:
                        z = zmaps[L][i]
                        if not np.isfinite(z): continue
                        if z >= thr: sgn = +1.0
                        elif z <= -thr: sgn = -1.0
                        else: continue
                        for h in HORIZONS:
                            xi = ei + h
                            if xi >= len(ot) or ot[xi] >= e_ms: continue
                            if (np.diff(ot[ei:xi + 1]) != 900000).any(): continue
                            raw = (kop[xi] - kop[ei]) / kop[ei]
                            trades[(b, L, h)].append((sgn * raw, qv[ei], qv[xi], qv[ei]))
            for (b, L, h), arr in trades.items():
                st = {"symbol": sym, "span": f"{s_str}~{e_str}", "bucket": b, "lookback": L, "horizon": h, "n": len(arr)}
                if st["n"] == 0:
                    st.update({"net_engine": None, "net_flat16": None, "fail_closed": 0})
                    report["cells"].append(st); continue
                r = np.array([x[0] for x in arr])
                st["total_bp"] = float(r.mean() * 1e4)
                st["net_flat16"] = float((r.mean() - FLAT_C16) * 1e4)
                for tag, ov in report["params"]["scenarios"].items():
                    costs, fc = [], 0
                    for _, qv_e, qv_x, _qe in arr:
                        c = engine_roundtrip_cost(ov, qv_e, qv_x)
                        if c is None: fc += 1
                        else: costs.append(c)
                    st[f"fail_closed_{tag}"] = fc
                    if len(costs) == st["n"]:
                        cm = float(np.mean(costs))
                        st[f"cost_{tag}_bp"] = cm * 1e4
                        st[f"net_{tag}"] = float((r.mean() - cm) * 1e4)
                        sd = r.std(ddof=1)
                        # t-검정 표준: diag(a1_illiq_diag.py)와 동일한 valid_n = n/h (겹침 보정).
                        # 초기 실행은 raw n을 사용했으나 diag 표준과의 불일치를 측정 후 정정(규격 변경 없음).
                        vn = max(st["n"] / h, 1.0)
                        t = (r.mean() - cm) / (sd / np.sqrt(vn)) if sd > 0 else 0.0
                        st[f"t_{tag}"] = float(t)
                        st[f"p_{tag}"] = float(2 * stats.t.sf(abs(t), df=max(vn - 1, 1)))
                        st["valid_n"] = vn
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
        passed = [c for c in report["cells"] if c.get(f"p_holm_{tag}", 1.0) < 0.05 and c.get(f"net_{tag}") is not None and c[f"net_{tag}"] > 0]
        report["verdict"][tag] = {"net_positive_cells": len(passed), "holm_pass_cells": [
            f"{c['symbol']} {c['span']} {c['bucket']} L{c['lookback']}h{c['horizon']} (net={c[f'net_{tag}']:.2f}bp)" for c in passed]}
    with open(f"{OUT}/a1_crosscheck_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    L = ["# A1 교차검증 — 엔진 CandleCostModel 적용 (정액 16bp 대비)", "",
         "- 비용 공식: `fee 5bp + half_spread 2bp + 10%*sqrt(participation)` per side (costs.py:104-117)",
         "- participation = 주문금액 / 바 거래대금(qv) · 5% 초과 시 fail-closed(제외 카운트)",
         "- A1 특유 쟁점: 고비유동 바 진입 → 참여율 상한 도달 비중 = 실현 가능성 자체 판정",
         "- 신호/거래 생성: a1_illiq_diag.py(사전등록 규격)와 동일 로직 — 1차 방향(고비유동→롱) 고정", ""]
    for sym in SYMBOLS:
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            L.append(f"## {sym} span{si+1} ({s_str}~{e_str})")
            L.append("| 버킷 | L | h | n | 총bp | 정액순16 | 엔진순($10k) | 엔진비용bp | FC10k | 엔진순($100k) | FC100k |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                if c["symbol"] != sym or c["span"] != f"{s_str}~{e_str}": continue
                fmt = lambda v: "n/a" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))
                L.append(f"| {c['bucket']} | {c['lookback']} | {c['horizon']} | {c['n']} | {fmt(c.get('total_bp'))} | "
                         f"{fmt(c.get('net_flat16'))} | {fmt(c.get('net_o10k'))} | {fmt(c.get('cost_o10k_bp'))} | "
                         f"{c.get('fail_closed_o10k', '-')} | {fmt(c.get('net_o100k'))} | {c.get('fail_closed_o100k', '-')} |")
            L.append("")
    L.append("## 결론")
    for tag, v in report["verdict"].items():
        L.append(f"- {tag}: 엔진 순익 양수 셀 **{v['net_positive_cells']}/96**" + (f" — {v['holm_pass_cells']}" if v['holm_pass_cells'] else ""))
    with open(f"{OUT}/a1_crosscheck_results.md", "w") as f:
        f.write("\n".join(L) + "\n")
    print("engine net>0 ($10k):", report["verdict"]["o10k"]["net_positive_cells"], "/96")
    print("engine net>0 ($100k):", report["verdict"]["o100k"]["net_positive_cells"], "/96")
    fc10 = [c for c in report["cells"] if c.get("fail_closed_o10k") is not None and c["n"] > 0]
    if fc10:
        ratios = [c["fail_closed_o10k"] / c["n"] for c in fc10]
        print(f"fail-closed 비중($10k): 평균 {np.mean(ratios)*100:.1f}% | 최대 {max(ratios)*100:.1f}% ({len(fc10)}셀)")

if __name__ == "__main__":
    main()
