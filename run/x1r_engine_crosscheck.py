#!/usr/bin/env python3
# X1-R 재검증 교차검증 — 엔진 CandleCostModel 적용. 신호/거래 생성은 x1r_reverify_diag.py와 동일 로직(역방향 h48).
import json, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run"
OUT = f"{BASE}/results"

def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)

ENDPOINT = "2026-10-12"
LOOKBACKS = [24, 120]
THR = 1.2816
H = 48
NWIN = 720
FLAT_C16 = 0.0016
ASSETS = ["ES", "NQ"]
CELLS = [(A, L) for A in ASSETS for L in LOOKBACKS]
HOUR = 3600000

CONFIGS = {
    "R": {"sym": "ETHUSDT", "segments": [("2024-12-10", "2026-04-20")], "note": "재현 검증"},
    "A": {"sym": "BTCUSDT", "segments": [("2024-06-19", "2026-04-20")], "note": "자산 일반화"},
    "C": {"sym": "BTCUSDT", "segments": [("2026-04-21", ENDPOINT)], "note": "시간 복제 BTC"},
    "B": {"sym": "ETHUSDT", "segments": [("2026-04-21", "2026-04-27"), ("2026-09-01", ENDPOINT)], "note": "시간 복제 ETH"},
}

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

def load_yahoo_1h(path):
    d = json.load(open(path))
    res = d["chart"]["result"][0]
    ts = res["timestamp"]
    close = res["indicators"]["quote"][0]["close"]
    pairs = [(int(t) * 1000, float(c)) for t, c in zip(ts, close)
             if c is not None and np.isfinite(float(c))]
    pairs = [(t, c) for t, c in pairs if t % HOUR == 0]
    df = pd.DataFrame(pairs, columns=["open_time", "close"]).drop_duplicates("open_time")
    return df.sort_values("open_time").reset_index(drop=True)

def ext_z(df, L):
    t = df["open_time"].to_numpy(np.int64)
    c = df["close"].to_numpy(float)
    d = np.full(len(t), np.nan)
    for i in range(len(t)):
        target = t[i] - L * HOUR
        j = np.searchsorted(t, target)
        if j < len(t) and t[j] == target:
            d[i] = c[i] - c[j]
    fin = np.where(np.isfinite(d))[0]
    dv = d[fin]
    rm = pd.Series(dv).rolling(NWIN).mean().shift(1).to_numpy()
    rs = pd.Series(dv).rolling(NWIN).std(ddof=1).shift(1).to_numpy()
    z = np.full(len(t), np.nan)
    ok = np.isfinite(rm) & np.isfinite(rs) & (rs > 0)
    z[fin[ok]] = (dv[ok] - rm[ok]) / rs[ok]
    return t, z

def load_klines(sym):
    import glob
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
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_segment(kot, kop, kqv, t, z_by_L, s_ms, e_ms, asset):
    trades = {c: [] for c in CELLS}
    for i in np.where((t >= s_ms) & (t < e_ms))[0]:
        tt = int(t[i])
        ei = np.searchsorted(kot, tt + HOUR, side="left")
        if ei >= len(kot) or kot[ei] != tt + HOUR or kot[ei] >= e_ms: continue
        for L in LOOKBACKS:
            z = z_by_L[L][i]
            if not np.isfinite(z): continue
            if z >= THR: sgn = -1.0
            elif z <= -THR: sgn = +1.0
            else: continue
            xi = ei + H
            if xi >= len(kot) or kot[xi] >= e_ms: continue
            if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
            raw = (kop[xi] - kop[ei]) / kop[ei]
            trades[(asset, L)].append((sgn * raw, kqv[ei], kqv[xi]))
    return trades

def run_config(k, z_by_A, t_by_A, cfg):
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    kqv = k["qv"].to_numpy(dtype=float)
    trades = {c: [] for c in CELLS}
    for s_str, e_str in cfg["segments"]:
        s_ms, e_ms = ms(s_str), ms(e_str)
        for A in ASSETS:
            tr = run_segment(kot, kop, kqv, t_by_A[A], z_by_A[A], s_ms, e_ms, A)
            for c in CELLS:
                trades[c].extend(tr[c])
    return trades

def main():
    report = {"crosscheck_of": "x1r_reverify_results.json", "prereg": "PREREG_x1r_reverify_2026-10-13.md",
              "cost_model": "CandleCostModel (costs.py:104-117)",
              "params": {"fee_rate": FEE, "half_spread": HALF_SPREAD, "impact_coef": IMPACT,
                          "max_participation": MAX_PART, "scenarios": {"o10k": 10_000.0, "o100k": 100_000.0}},
              "configs": {}}
    k = {sym: load_klines(sym) for sym in ["BTCUSDT", "ETHUSDT"]}
    ext = {A: load_yahoo_1h(f"{BASE}/research/r3_yahoo_{A.lower()}.json") for A in ASSETS}
    z_by_A, t_by_A = {}, {}
    for A in ASSETS:
        z_by_A[A] = {}; t_by_A[A] = {}
        for L in LOOKBACKS:
            t, z = ext_z(ext[A], L)
            z_by_A[A][L] = z; t_by_A[A] = t
    for cname, cfg in CONFIGS.items():
        trades = run_config(k[cfg["sym"]], z_by_A, t_by_A, cfg)
        cells = []
        for A, L in CELLS:
            arr = trades[(A, L)]
            st = {"asset": A, "lookback": L, "horizon": H, "n": len(arr)}
            if st["n"] == 0:
                st.update({"total_bp": None, "net_flat16": None, "net_o10k": None, "net_o100k": None})
                cells.append(st); continue
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
                    sd = r.std(ddof=1); vn = st["n"] / H
                    t = (r.mean() - cm) / (sd / np.sqrt(vn)) if sd > 0 else 0.0
                    st[f"t_{tag}"] = float(t)
                    st[f"p_{tag}"] = float(2 * stats.t.sf(abs(t), df=max(vn - 1, 1)))
                else:
                    st[f"cost_{tag}_bp"] = None; st[f"net_{tag}"] = None
            cells.append(st)
        for tag in ("o10k", "o100k"):
            ps = [c.get(f"p_{tag}") if c.get(f"p_{tag}") is not None else 1.0 for c in cells]
            adj = holm(ps)
            for c, a in zip(cells, adj):
                c[f"p_holm_{tag}"] = float(a)
        report["configs"][cname] = {"sym": cfg["sym"], "note": cfg["note"], "cells": cells}
    # 요약 판정
    summary = {}
    for tag in ("o10k", "o100k"):
        summary[tag] = {}
        for cname in CONFIGS:
            cells = report["configs"][cname]["cells"]
            pos = sum(1 for c in cells if c.get(f"net_{tag}") is not None and c[f"net_{tag}"] > 0)
            holm_pass = sum(1 for c in cells if c.get(f"p_holm_{tag}", 1.0) < 0.05)
            summary[tag][cname] = {"net_pos": f"{pos}/4", "holm_pass": holm_pass}
    report["summary"] = summary
    with open(f"{OUT}/x1r_crosscheck_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    L = ["# X1-R 재검증 교차검증 — 엔진 CandleCostModel 적용 (정액 16bp 대비)", "",
         "- 비용 공식: `fee 5bp + half_spread 2bp + 10%*sqrt(participation)` per side (costs.py:104-117)",
         "- 신호/거래 생성: x1r_reverify_diag.py(사전등록 규격, 역방향 h48)와 동일 로직", ""]
    for cname in ["R", "A", "C", "B"]:
        cfg = report["configs"][cname]
        L.append(f"## Config {cname} — {cfg['sym']} ({cfg['note']})")
        L.append("")
        L.append("| 자산 | L | n | 총bp | 정액순16 | 엔진순($10k) | 엔진비용bp | Holm | 엔진순($100k) | 엔진비용bp | FC10k | FC100k |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        fmt = lambda v: "n/a" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))
        for c in cfg["cells"]:
            L.append(f"| {c['asset']} | {c['lookback']} | {c['n']} | {fmt(c.get('total_bp'))} | "
                     f"{fmt(c.get('net_flat16'))} | {fmt(c.get('net_o10k'))} | {fmt(c.get('cost_o10k_bp'))} | "
                     f"{fmt(c.get('p_holm_o10k'))} | {fmt(c.get('net_o100k'))} | {fmt(c.get('cost_o100k_bp'))} | "
                     f"{c.get('fail_closed_o10k', '-')} | {c.get('fail_closed_o100k', '-')} |")
        L.append("")
    L.append("## 요약 (엔진 net>0 셀 수 / Holm 통과 셀 수)")
    for tag in ("o10k", "o100k"):
        L.append(f"- **{tag}**: " + ", ".join(f"{cn} net+ {summary[tag][cn]['net_pos']}, Holm {summary[tag][cn]['holm_pass']}" for cn in CONFIGS))
    with open(f"{OUT}/x1r_crosscheck_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")
    print(json.dumps(summary, indent=1, ensure_ascii=False))

if __name__ == "__main__":
    main()
