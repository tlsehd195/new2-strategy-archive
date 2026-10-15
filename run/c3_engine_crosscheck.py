#!/usr/bin/env python3
# C3 교차검증 — 기존 정액 비용(12/16/20bp) 대신 저장소 엔진 CandleCostModel
# (src/cointrader/backtest/costs.py:104-117 공식)을 그대로 적용해 재계산.
# 신호/거래 생성은 c3_coinbase_premium_diag.py(사전등록 규격)와 완전히 동일 로직 — 결과 재현 보장.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"

SYMBOLS = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD"}
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
END = ms("2026-10-12")
SEGS = {
    "BTCUSDT": {"lock": (ms("2024-03-25"), ms("2024-06-19")),
                "spans": [("START", "2024-03-25"), ("2024-06-19", "END")]},
    "ETHUSDT": {"lock": (ms("2026-04-28"), ms("2026-09-01")),
                "spans": [("START", "2026-04-28"), ("2026-09-01", "END")]},
}
LOOKBACKS = [96, 288, 960, 2880]
THR = 1.2816
HORIZONS = [4, 16, 48]
CELLS = [(L, h) for L in LOOKBACKS for h in HORIZONS]

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

def load_coinbase(prod):
    df = pd.read_csv(f"{BASE}/coinbase/{prod}-15m.csv")
    df["open_time"] = df["open_time"].astype(np.int64) * 1000
    return df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

def load_spot(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/spot15m/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "close"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    return df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    return df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

def level_z(p, L):
    s = pd.Series(p)
    m = s.rolling(L, min_periods=L).mean().shift(1).to_numpy()
    sd = s.rolling(L, min_periods=L).std(ddof=1).shift(1).to_numpy()
    z = np.where((~np.isnan(m)) & (~np.isnan(sd)) & (sd > 0), (np.asarray(p, dtype=float) - m) / sd, np.nan)
    return z

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, pot, z_by_L, s_ms, e_ms):
    """c3_diag run_span과 동일하되 trades에 (ret, qv_entry, qv_exit) 수집."""
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    kqv = k["qv"].to_numpy(dtype=float)
    trades = {k2: [] for k2 in CELLS}
    for i in range(len(pot) - 1):
        t = pot[i]
        if not (s_ms <= t < e_ms): continue
        if pot[i + 1] != t + 900000: continue
        ei = np.searchsorted(kot, t + 900000, side="left")
        if ei >= len(kot) or kot[ei] != t + 900000 or kot[ei] >= e_ms: continue
        for L in LOOKBACKS:
            z = z_by_L[L][i]
            if not np.isfinite(z): continue
            if z >= THR: sgn = 1.0
            elif z <= -THR: sgn = -1.0
            else: continue
            for h in HORIZONS:
                xi = ei + h
                if xi >= len(kot) or kot[xi] >= e_ms: continue
                if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                raw = (kop[xi] - kop[ei]) / kop[ei]
                trades[(L, h)].append((sgn * raw, kqv[ei], kqv[xi]))
    return trades

def main():
    report = {"crosscheck_of": "c3_coinbase_premium_results.json", "cost_model": "CandleCostModel (costs.py:104-117)",
              "params": {"fee_rate": FEE, "half_spread": HALF_SPREAD, "impact_coef": IMPACT,
                          "max_participation": MAX_PART, "scenarios": {"o10k": 10_000.0, "o100k": 100_000.0}},
              "cells": []}
    for sym, prod in SYMBOLS.items():
        cb = load_coinbase(prod)
        sp = load_spot(sym)
        mg = pd.merge(cb[["open_time", "close"]].rename(columns={"close": "cb"}),
                      sp[["open_time", "close"]].rename(columns={"close": "bn"}), on="open_time", how="inner")
        p = np.log(mg["cb"].to_numpy(dtype=float) / mg["bn"].to_numpy(dtype=float))
        ot = mg["open_time"].to_numpy(dtype=np.int64)
        l0, l1 = SEGS[sym]["lock"]
        p[(ot >= l0) & (ot < l1)] = np.nan
        zm = {L: level_z(p, L) for L in LOOKBACKS}
        k = load_klines(sym)
        s0 = int(ot[0])
        for si, (s_str, e_str) in enumerate(SEGS[sym]["spans"]):
            s_ms = s0 if s_str == "START" else ms(s_str)
            e_ms = END if e_str == "END" else ms(e_str)
            trades = run_span(k, ot, zm, s_ms, e_ms)
            for L, h in CELLS:
                arr = trades[(L, h)]
                st = {"symbol": sym, "span": f"span{si+1}", "lookback": L, "horizon": h, "n": len(arr)}
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
                        sd = r.std(ddof=1); vn = st["n"] / h
                        t = (r.mean() - cm) / (sd / np.sqrt(vn)) if sd > 0 else 0.0
                        st[f"t_{tag}"] = float(t)
                        st[f"p_{tag}"] = float(2 * stats.t.sf(abs(t), df=max(vn - 1, 1)))
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
            f"{c['symbol']} {c['span']} L{c['lookback']} h{c['horizon']} (net={c.get('net_' + tag):.2f}bp)" if c.get("net_" + tag) is not None
            else f"{c['symbol']} {c['span']} L{c['lookback']} h{c['horizon']}" for c in passed]}
    with open(f"{OUT}/c3_crosscheck_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    L = ["# C3 교차검증 — 엔진 CandleCostModel 적용 (정액 16bp 대비)", "",
         "- 비용 공식: `fee 5bp + half_spread 2bp + 10%*sqrt(participation)` per side (costs.py:104-117)",
         "- participation = 주문금액 / 바 거래대금(qv) · 5% 초과 시 fail-closed(제외 카운트)",
         "- 신호/거래 생성: c3_coinbase_premium_diag.py(사전등록 규격)와 동일 로직 — 양측 추종 고정", ""]
    for sym in SYMBOLS:
        for si in range(2):
            L.append(f"## {sym} span{si+1}")
            L.append("| L | h | n | 총bp | 정액순16 | 엔진순($10k) | 엔진비용bp | t | Holm | 엔진순($100k) | 엔진비용bp | FC10k | FC100k |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                if c["symbol"] != sym or c["span"] != f"span{si+1}": continue
                fmt = lambda v: "n/a" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))
                L.append(f"| {c['lookback']} | {c['horizon']} | {c['n']} | {fmt(c.get('total_bp'))} | "
                         f"{fmt(c.get('net_flat16'))} | {fmt(c.get('net_o10k'))} | {fmt(c.get('cost_o10k_bp'))} | "
                         f"{fmt(c.get('t_o10k'))} | {fmt(c.get('p_holm_o10k'))} | {fmt(c.get('net_o100k'))} | "
                         f"{fmt(c.get('cost_o100k_bp'))} | {c.get('fail_closed_o10k', '-')} | {c.get('fail_closed_o100k', '-')} |")
            L.append("")
    L.append("## 결론")
    for tag, v in report["verdict"].items():
        L.append(f"- {tag}: Holm 통과 셀 {len(v['holm_pass_cells'])}개")
        for cell in v["holm_pass_cells"]:
            L.append(f"  - {cell}")
    with open(f"{OUT}/c3_crosscheck_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")
    print(json.dumps(report["verdict"], indent=1, ensure_ascii=False))
    for tag in ("o10k", "o100k"):
        pos = [c for c in report["cells"] if c.get(f"net_{tag}") is not None and c[f"net_{tag}"] > 0]
        print(f"{tag}: net>0 cells = {len(pos)}/48")

if __name__ == "__main__":
    main()
