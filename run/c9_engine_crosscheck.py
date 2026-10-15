#!/usr/bin/env python3
# C9 엔진 교차검증 — 정액 비용(12/16/20bp) 대신 저장소 엔진 CandleCostModel
# (src/cointrader/backtest/costs.py:104-117 공식)을 그대로 적용해 재계산.
# 신호/거래 생성은 c9_dvol_diag.py(사전등록 규격)와 완전히 동일 로직 — 결과 재현 보장.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run"
DVOL_DIR = f"{BASE}/data/dvol"
OUT = f"{BASE}/results"

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
CUR = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}
LOCK = {"BTCUSDT": ("2024-03-25", "2024-06-18"), "ETHUSDT": ("2026-04-28", "2026-08-31")}
END_STR = "2026-10-12"
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {
    "BTCUSDT": [("2021-03-25", "2024-03-25"), ("2024-06-19", END_STR)],
    "ETHUSDT": [("2021-03-25", "2026-04-28"), ("2026-09-01", END_STR)],
}
ZLS = [30, 90]
THRS = {"B80": 1.2816, "B90": 1.6449}
HORIZONS = [96, 288, 672]
FLAT_C16 = 0.0016
CELLS = [(L, b, h) for L in ZLS for b in THRS for h in HORIZONS]
DAY_MS = 86_400_000

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
    files = sorted(glob.glob(f"{BASE}/data/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df

def load_dvol_z(cur, sym):
    df = pd.read_csv(f"{DVOL_DIR}/c9_dvol_{cur}_1d.csv").sort_values("ts_ms").reset_index(drop=True)
    dv = df["close"].to_numpy(dtype=float)
    days = df["ts_ms"].to_numpy(dtype=np.int64)
    lk0 = ms(LOCK[sym][0]); lk1 = ms(LOCK[sym][1]) + DAY_MS
    locked = (days >= lk0) & (days < lk1)
    zmaps = {}
    for L in ZLS:
        z = np.full(len(dv), np.nan)
        for i in range(L, len(dv)):
            if locked[i]:
                continue
            w = dv[i - L:i]
            if np.isnan(w).any() or locked[i - L:i].any():
                continue
            sd = w.std(ddof=1)
            if sd > 0:
                z[i] = (dv[i] - w.mean()) / sd
        zmaps[L] = dict(zip(days[~np.isnan(z)].tolist(), z[~np.isnan(z)].tolist()))
    return zmaps

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, zmaps, s_str, e_str):
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    kqv = k["qv"].to_numpy(dtype=float)
    day_ms = (kot // DAY_MS) * DAY_MS
    zvals = {}
    for L in ZLS:
        zm = zmaps[L]
        zvals[L] = np.array([zm.get(int(d - DAY_MS), np.nan) for d in day_ms], dtype=float)
    trades = {k2: [] for k2 in CELLS}
    for i in range(len(kot) - 1):
        t = kot[i]
        if not (s_ms <= t < e_ms): continue
        if t % DAY_MS == 0: continue
        if kot[i + 1] != t + 900000: continue
        for L in ZLS:
            v = zvals[L][i]
            if not np.isfinite(v): continue
            for b, thr in THRS.items():
                if v >= thr: sgn = +1.0
                elif v <= -thr: sgn = -1.0
                else: continue
                ei = i + 1
                if kot[ei] >= e_ms: continue
                for h in HORIZONS:
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                    raw = (kop[xi] - kop[ei]) / kop[ei]
                    trades[(L, b, h)].append((sgn * raw, kqv[ei], kqv[xi]))
    return trades

def main():
    report = {"crosscheck_of": "c9_dvol_results.json", "cost_model": "CandleCostModel (costs.py:104-117)",
              "params": {"fee_rate": FEE, "half_spread": HALF_SPREAD, "impact_coef": IMPACT,
                          "max_participation": MAX_PART, "scenarios": {"o10k": 10_000.0, "o100k": 100_000.0}},
              "cells": []}
    zmaps_all, klines = {}, {}
    for sym in SYMBOLS:
        zmaps_all[sym] = load_dvol_z(CUR[sym], sym)
        klines[sym] = load_klines(sym)
    for sym in SYMBOLS:
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            trades = run_span(klines[sym], zmaps_all[sym], s_str, e_str)
            for (L, b, h) in CELLS:
                arr = trades[(L, b, h)]
                st = {"symbol": sym, "span": f"{s_str}~{e_str}", "L": L, "bucket": b, "horizon": h, "n": len(arr)}
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
            f"{c['symbol']} {c['span']} L{c['L']} {c['bucket']} h{c['horizon']} (net={c.get('net_' + tag):.2f}bp)" if c.get("net_" + tag) is not None
            else f"{c['symbol']} {c['span']} L{c['L']} {c['bucket']} h{c['horizon']}" for c in passed]}
    with open(f"{OUT}/c9_crosscheck_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    L = ["# C9 DVOL 교차검증 — 엔진 CandleCostModel 적용 (정액 16bp 대비)", "",
         "- 비용 공식: `fee 5bp + half_spread 2bp + 10%*sqrt(participation)` per side (costs.py:104-117)",
         "- participation = 주문금액 / 바 거래대금(qv) · 5% 초과 시 fail-closed(제외 카운트)",
         "- 신호/거래 생성: c9_dvol_diag.py(사전등록 규격)와 동일 로직 — 역발상 방향 고정", ""]
    for sym in SYMBOLS:
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            L.append(f"## {sym} span{si+1} ({s_str}~{e_str})")
            L.append("| L | 버킷 | h | n | 총bp | 정액순16 | 엔진순($10k) | 엔진비용bp | t | Holm | 엔진순($100k) | 엔진비용bp | FC10k | FC100k |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                if c["symbol"] != sym or c["span"] != f"{s_str}~{e_str}": continue
                fmt = lambda v: "n/a" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))
                L.append(f"| {c['L']} | {c['bucket']} | {c['horizon']} | {c['n']} | {fmt(c.get('total_bp'))} | "
                         f"{fmt(c.get('net_flat16'))} | {fmt(c.get('net_o10k'))} | {fmt(c.get('cost_o10k_bp'))} | "
                         f"{fmt(c.get('t_o10k'))} | {fmt(c.get('p_holm_o10k'))} | {fmt(c.get('net_o100k'))} | "
                         f"{fmt(c.get('cost_o100k_bp'))} | {c.get('fail_closed_o10k', '-')} | {c.get('fail_closed_o100k', '-')} |")
            L.append("")
    L.append("## 결론")
    for tag, v in report["verdict"].items():
        L.append(f"- {tag}: Holm 통과 셀 {len(v['holm_pass_cells'])}개")
        for cell in v["holm_pass_cells"]:
            L.append(f"  - {cell}")
    pos10 = [c for c in report["cells"] if c.get("net_o10k") is not None and c["net_o10k"] > 0]
    L.append(f"- 엔진 net>0 셀($10k): {len(pos10)}/48")
    with open(f"{OUT}/c9_crosscheck_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")
    print(json.dumps(report["verdict"], indent=1, ensure_ascii=False))
    for tag in ("o10k", "o100k"):
        pos = [c for c in report["cells"] if c.get(f"net_{tag}") is not None and c[f"net_{tag}"] > 0]
        print(f"{tag}: net>0 cells = {len(pos)}/48")
    top = sorted([c for c in report["cells"] if c.get("net_o10k") is not None], key=lambda x: -x["net_o10k"])[:5]
    print("top5 엔진순($10k):", [(f"L{c['L']}", c['bucket'], f"h{c['horizon']}", c['symbol'], round(c['net_o10k'], 2)) for c in top])

if __name__ == "__main__":
    main()
