#!/usr/bin/env python3
# X5 교차검증 — 엔진 CandleCostModel 적용. 신호/거래 생성은 x5_stable_supply_diag.py와 동일 로직.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run"
OUT = f"{BASE}/results"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
LOOKBACKS = [1, 7, 30]
HORIZONS = [96, 288, 672]
FLAT_C16 = 0.0016
THR = 1.2816
WIN90 = 90
DAY = 86400000
ENTRY_OFFSET = 900000
CELLS = [(L, h) for L in LOOKBACKS for h in HORIZONS]
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

def load_supply():
    d = json.load(open(f"{BASE}/research/r3_llama_usdt_hist.json"))
    rows = [(int(x["date"]) * 1000, float(x["totalCirculating"]["peggedUSD"])) for x in d]
    return pd.DataFrame(rows, columns=["day_ms", "supply"]).drop_duplicates("day_ms").sort_values("day_ms").reset_index(drop=True)

def build_z(sup, L):
    t = sup["day_ms"].to_numpy(np.int64); v = sup["supply"].to_numpy(float)
    d = np.full(len(t), np.nan)
    for i in range(len(t)):
        target = t[i] - L * DAY
        j = np.searchsorted(t, target)
        if j < len(t) and t[j] == target: d[i] = v[i] - v[j]
    fin = np.where(np.isfinite(d))[0]; dv = d[fin]
    rm = pd.Series(dv).rolling(WIN90).mean().shift(1).to_numpy()
    rs = pd.Series(dv).rolling(WIN90).std(ddof=1).shift(1).to_numpy()
    z = np.full(len(t), np.nan)
    ok = np.isfinite(rm) & np.isfinite(rs) & (rs > 0)
    z[fin[ok]] = (dv[ok] - rm[ok]) / rs[ok]
    sup = sup.copy(); sup[f"z{L}"] = z
    return sup

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

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx]); adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, sup, s_str, e_str):
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(np.int64); kop = k["open"].to_numpy(float); kqv = k["qv"].to_numpy(float)
    fd = sup["day_ms"].to_numpy(np.int64)
    z_by_L = {L: sup[f"z{L}"].to_numpy(float) for L in LOOKBACKS}
    trades = {c: [] for c in CELLS}
    for j in range(len(fd)):
        t = int(fd[j])
        if not (s_ms <= t < e_ms): continue
        ei = np.searchsorted(kot, t + ENTRY_OFFSET, side="left")
        if ei >= len(kot) or kot[ei] != t + ENTRY_OFFSET or kot[ei] >= e_ms: continue
        for L, h in CELLS:
            z = z_by_L[L][j]
            if not np.isfinite(z): continue
            if z >= THR: s2 = 1.0
            elif z <= -THR: s2 = -1.0
            else: continue
            xi = ei + h
            if xi >= len(kot) or kot[xi] >= e_ms: continue
            if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
            trades[(L, h)].append((s2 * (kop[xi] - kop[ei]) / kop[ei], kqv[ei], kqv[xi]))
    return trades

def main():
    report = {"crosscheck_of": "x5_stable_supply_results.json", "cost_model": "CandleCostModel (costs.py:104-117)",
              "scenarios": {"o10k": 10_000.0, "o100k": 100_000.0}, "cells": []}
    sup = load_supply()
    for L in LOOKBACKS:
        sup = build_z(sup, L)
    for sym in SYMBOLS:
        k = load_klines(sym)
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            trades = run_span(k, sup, s_str, e_str)
            for L, h in CELLS:
                arr = trades[(L, h)]
                st = {"symbol": sym, "span": f"{s_str}~{e_str}", "lookback": L, "horizon": h, "n": len(arr)}
                if st["n"] == 0:
                    st.update({"net_flat16": None, "net_o10k": None})
                    report["cells"].append(st); continue
                r = np.array([x[0] for x in arr])
                st["total_bp"] = float(r.mean() * 1e4)
                st["net_flat16"] = float((r.mean() - FLAT_C16) * 1e4)
                for tag, ov in report["scenarios"].items():
                    costs, fc = [], 0
                    for _, qv_e, qv_x in arr:
                        c = engine_roundtrip_cost(ov, qv_e, qv_x)
                        if c is None: fc += 1
                        else: costs.append(c)
                    if len(costs) == st["n"]:
                        cm = float(np.mean(costs))
                        st[f"cost_{tag}_bp"] = cm * 1e4
                        st[f"net_{tag}"] = float((r.mean() - cm) * 1e4)
                        sd = r.std(ddof=1); vn = st["n"] / max(1, h // 96)
                        t = (r.mean() - cm) / (sd / np.sqrt(vn)) if sd > 0 else 0.0
                        st[f"p_{tag}"] = float(2 * stats.t.sf(abs(t), df=max(vn - 1, 1)))
                    else:
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
        pos = [c for c in report["cells"] if c.get(f"net_{tag}") is not None and c[f"net_{tag}"] > 0]
        report["verdict"][tag] = {"holm_pass": len(passed), "net_gt0": len(pos),
                                   "cells": [f"{c['symbol']} {c['span']} L{c['lookback']} h{c['horizon']} (net={c.get('net_'+tag):.2f}bp)" for c in passed]}
    with open(f"{OUT}/x5_crosscheck_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    L = ["# X5 교차검증 — 엔진 CandleCostModel 적용", "",
         "- 비용 공식: fee 5bp + half_spread 2bp + 10%*sqrt(participation) per side · valid_n 일간 규약", ""]
    for tag in ("o10k", "o100k"):
        v = report["verdict"][tag]
        L.append(f"- {tag}: Holm 통과 {v['holm_pass']}/36 · 엔진 net>0 {v['net_gt0']}/36")
        for cell in v["cells"]:
            L.append(f"  - {cell}")
    with open(f"{OUT}/x5_crosscheck_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")
    print(json.dumps(report["verdict"], indent=1, ensure_ascii=False))

if __name__ == "__main__":
    main()
