#!/usr/bin/env python3
# KP 엔진 교차검증 — 정액 비용(12/16/20bp) 대신 저장소 엔진 CandleCostModel 공식 적용.
# 신호/거래 생성은 kp_diag.py(사전등록 규격, 이벤트 병합 정정판)와 완전히 동일 로직 — 결과 재현 보장.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run"
KP_PATH = f"{BASE}/data/kp/kp_series_1d.csv"
OUT = f"{BASE}/results"

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
KP_COL = {"BTCUSDT": "KP_BTC", "ETHUSDT": "KP_ETH"}
LOCK = {"BTCUSDT": ("2024-03-25", "2024-06-18"), "ETHUSDT": ("2026-04-28", "2026-08-31")}
END_STR = "2026-10-08"
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {
    "BTCUSDT": [("2021-04-01", "2024-03-25"), ("2024-06-19", END_STR)],
    "ETHUSDT": [("2021-04-01", "2026-04-28"), ("2026-09-01", END_STR)],
}
ZLS = [90, 288]
THRS = {"B80": 1.2816, "B90": 1.6449}
HORIZONS = [16, 48]
CELLS = [(L, b, h) for L in ZLS for b in THRS for h in HORIZONS]
DAY_MS = 86_400_000

FEE, HALF_SPREAD, IMPACT, MAX_PART = 0.0005, 0.0002, 0.1, 0.05
SIZES = {"10k": 10_000, "100k": 100_000}

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

def load_kp_z(sym):
    df = pd.read_csv(KP_PATH).sort_values("day").reset_index(drop=True)
    days = (pd.to_datetime(df["day"], utc=True).astype("int64") // 10**6).to_numpy(dtype=np.int64)
    kp = df[KP_COL[sym]].to_numpy(dtype=float)
    lk0 = ms(LOCK[sym][0]); lk1 = ms(LOCK[sym][1]) + DAY_MS
    locked = (days >= lk0) & (days < lk1)
    zmaps = {}
    for L in ZLS:
        z = np.full(len(kp), np.nan)
        for i in range(L, len(kp)):
            if locked[i]: continue
            w = kp[i - L:i]
            if np.isnan(w).any() or locked[i - L:i].any(): continue
            sd = w.std(ddof=1)
            if sd > 0: z[i] = (kp[i] - w.mean()) / sd
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

def run_span_engine(k, zmaps, sym, s_str, e_str):
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    kqv = k["qv"].to_numpy(dtype=float)
    day_ms = (kot // DAY_MS) * DAY_MS
    zvals = {}
    for L in ZLS:
        zm = zmaps[L]
        zvals[L] = np.array([zm.get(int(d - DAY_MS), np.nan) for d in day_ms], dtype=float)
    trades = {c: [] for c in CELLS}
    active = {(L, b): False for L in ZLS for b in THRS}
    for i in range(len(kot) - 1):
        t = kot[i]
        if not (s_ms <= t < e_ms): continue
        if t % DAY_MS == 0: continue
        if kot[i + 1] != t + 900000: continue
        d = int(day_ms[i])
        for L in ZLS:
            v = zvals[L][i]
            for b, thr in THRS.items():
                key = (L, b)
                if not np.isfinite(v) or (v < thr and v > -thr):
                    active[key] = False
                    continue
                if active[key]: continue
                sgn = -1.0 if v >= thr else +1.0
                active[key] = True
                ei = i + 1
                if kot[ei] >= e_ms: continue
                for h in HORIZONS:
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if kot[xi] != kot[ei] + h * 900000: continue
                    trades[(L, b, h)].append({"t": int(t), "entry": float(kop[ei]), "exit": float(kop[xi]),
                                              "qv_entry": float(kqv[ei]), "qv_exit": float(kqv[xi]),
                                              "sgn": sgn, "gross_bp": sgn * (kop[xi] / kop[ei] - 1) * 1e4})
    out = {}
    for c in CELLS:
        tr = trades[c]
        rec = {"n_raw": len(tr)}
        if len(tr) >= 5:
            for sz, ov in SIZES.items():
                nets, fc = [], 0
                for x in tr:
                    # 포지션 방향과 무관하게 진입/청산 각 1면 taker
                    c_cost = engine_roundtrip_cost(ov, x["qv_entry"], x["qv_exit"])
                    if c_cost is None:
                        fc += 1; continue
                    nets.append(x["gross_bp"] - c_cost * 1e4)
                rec[f"engine_{sz}"] = {"n_used": len(nets), "fail_closed": fc,
                                       "net_mean_bp": float(np.mean(nets)) if nets else None,
                                       "net_pos": int(np.sum(np.array(nets) > 0)) if nets else 0}
            g = np.array([x["gross_bp"] for x in tr])
            rec.update({"gross_mean_bp": float(g.mean()),
                        "t_stat": float(stats.ttest_1samp(g, 0.0).statistic),
                        "p_raw": float(stats.ttest_1samp(g, 0.0).pvalue),
                        "n_event": len(tr)})
        out[str(c)] = rec
    return out

def main():
    results = {"meta": {"script": "kp_engine_crosscheck.py", "engine": "CandleCostModel fee=5bp half_spread=2bp impact=0.1*sqrt(part) max_part=5%",
                        "end": END_STR, "event_merge": "에피소드당 1트레이드 (kp_diag.py 정정판과 동일)"}}
    fam = []
    for sym in SYMBOLS:
        k = load_klines(sym)
        zmaps = load_kp_z(sym)
        for si, (s_str, e_str) in enumerate(SPANS[sym], 1):
            cells = run_span_engine(k, zmaps, sym, s_str, e_str)
            results.setdefault("segments", {})[f"{sym}_s{si}"] = {"span": [s_str, e_str], "cells": cells}
            for c in CELLS:
                r = cells[str(c)]
                if "p_raw" in r:
                    fam.append((f"{sym}_s{si}|L{c[0]}{c[1]}h{c[2]}", r["p_raw"], r["engine_10k"], r["engine_100k"], r["gross_mean_bp"]))
    # Holm (gross 기준 — 원측정 절차 준용)
    adj = holm([f[1] for f in fam]) if fam else []
    hm = dict(zip([f[0] for f in fam], adj))
    results["engine_verdict"] = {
        "cells_with_data": len(fam),
        "engine10k_net_pos": sum(1 for f in fam if f[2]["net_mean_bp"] is not None and f[2]["net_mean_bp"] > 0),
        "engine100k_net_pos": sum(1 for f in fam if f[3]["net_mean_bp"] is not None and f[3]["net_mean_bp"] > 0),
        "holm_passing_n": int(sum(1 for v in hm.values() if v < 0.05)),
        "best_10k": sorted([(f[0], f[2]) for f in fam if f[2]["net_mean_bp"] is not None],
                           key=lambda x: -x[1]["net_mean_bp"])[:5]}
    with open(f"{OUT}/kp_crosscheck_results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    lines = ["# KP 김치 프리미엄 — 엔진 교차검증 (CandleCostModel)", "",
             f"- 셀(데이터 있음) {len(fam)} · 엔진 net>0: $10k {results['engine_verdict']['engine10k_net_pos']}/32 · $100k {results['engine_verdict']['engine100k_net_pos']}/32"]
    for cid, e in results["engine_verdict"]["best_10k"]:
        lines.append(f"- 최고 {cid}: net {e['net_mean_bp']:+.2f}bp (n_used {e['n_used']}, FC {e['fail_closed']})")
    with open(f"{OUT}/kp_crosscheck_results.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps(results["engine_verdict"], indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
