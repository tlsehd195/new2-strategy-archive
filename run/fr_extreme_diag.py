#!/usr/bin/env python3
# P1 fr_extreme 진단 — 사전등록(PREREG_local_2026-10-12.md) 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {  # 잠금 해제 구간 (끝 미포함) — ADR-0088/0089 준용
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
WARMUP = {  # 데이터 로드 범위(워밍업 90회 정산 = 30일 포함)
    "BTCUSDT": [("2021-04-01", "2022-08-31"), ("2023-03-01", "2024-03-31")],
    "ETHUSDT": [("2021-04-01", "2022-08-31"), ("2024-11-01", "2026-04-30")],
}
BUCKETS = {"B80": 1.2816, "B90": 1.6449}
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
N90 = 90
SEEDS = 100

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    dups = int(df.duplicated("open_time").sum())
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df, dups

def load_funding(sym):
    files = sorted(glob.glob(f"{BASE}/funding/{sym}-fundingRate-*.csv"))
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    dups = int(df.duplicated("calc_time").sum())
    df = df.drop_duplicates("calc_time").sort_values("calc_time").reset_index(drop=True)
    return df, dups

def funding_z(rates, nwin=N90):
    """z_i = (r_i - mean(r_{i-nwin:i})) / std(r_{i-nwin:i}); 직전 nwin회만 사용(현재 제외, 인과)."""
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

def run_span(sym, k, f, zs, s_str, e_str):
    s_ms, e_ms = ms(s_str), ms(e_str)
    ot = k["open_time"].to_numpy(dtype=np.int64)
    op = k["open"].to_numpy(dtype=float)
    in_span = (ot >= s_ms) & (ot < e_ms)
    q = {}
    q["bars_in_span"] = int(in_span.sum())
    q["expected_bars"] = int((e_ms - s_ms) // 900000)
    q["missing_bars"] = q["expected_bars"] - q["bars_in_span"]
    fmask = (f["calc_time"] >= s_ms) & (f["calc_time"] < e_ms)
    q["funding_in_span"] = int(fmask.sum())
    q["expected_funding"] = int((e_ms - s_ms) // 28800000)
    # 신호 (구간 내 정산, z 유효)
    sig_t, sig_z = [], []
    for t, z in zip(f["calc_time"].to_numpy(dtype=np.int64), zs):
        if s_ms <= t < e_ms and np.isfinite(z):
            sig_t.append(t); sig_z.append(z)
    sig_t = np.array(sig_t); sig_z = np.array(sig_z)
    q["signals_all"] = int(len(sig_t))
    trades = {}
    dropped = {}
    for b in BUCKETS:
        for h in HORIZONS:
            trades[(b, h)] = []; dropped[(b, h)] = 0
    for T, z in zip(sig_t, sig_z):
        ei = np.searchsorted(ot, T, side="right")  # 정산 다음 봉 (엄밀 인과)
        if ei >= len(ot) or ot[ei] >= e_ms:
            continue
        for b, thr in BUCKETS.items():
            if abs(z) < thr: continue
            direction = -1.0 if z > 0 else 1.0   # 역발상 고정
            for h in HORIZONS:
                xi = ei + h
                if xi >= len(ot) or ot[xi] >= e_ms: continue
                seg = ot[ei:xi + 1]
                if (np.diff(seg) != 900000).any():
                    dropped[(b, h)] += 1; continue   # fail-closed
                ret = direction * (op[xi] - op[ei]) / op[ei]
                trades[(b, h)].append((ret, T))
    return q, trades, dropped

def cell_stats(rets, h, seeds_rng, entry_ts):
    n = len(rets)
    out = {"n": n, "valid_n": n / h}
    if n == 0:
        out.update({"total_bp": None, "net12": None, "net16": None, "net20": None,
                    "t": None, "p": 1.0, "follow_net16": None, "p_ctrl": None})
        return out
    r = np.array([x[0] for x in rets])
    vn = n / h
    out["total_bp"] = float(r.mean() * 1e4)
    out["net12"] = float((r.mean() - COST["c12"]) * 1e4)
    out["net16"] = float((r.mean() - COST["c16"]) * 1e4)
    out["net20"] = float((r.mean() - COST["c20"]) * 1e4)
    sd = r.std(ddof=1)
    t = (r.mean() - COST["c16"]) / (sd / np.sqrt(vn)) if sd > 0 else 0.0
    out["t"] = float(t)
    out["p"] = float(2 * stats.t.sf(abs(t), df=max(vn - 1, 1)))
    # 통제1: 추종 방향(거울) — 같은 진입 봉, 반대 부호
    fr = -r
    out["follow_net16"] = float((fr.mean() - COST["c16"]) * 1e4)
    # 통제2: 무작위 방향 100시드 → P(rand >= 관측)
    rr = np.array([seeds_rng.uniform() < 0.5 and 1.0 or -1.0 for _ in range(0)])  # placeholder
    rng = np.random.default_rng(20261012)
    rand_means = []
    arr = np.array([x[0] for x in rets])
    for s in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * arr).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def main():
    report = {"prereg": "PREREG_local_2026-10-12.md", "family_size": 24, "spans": {}, "cells": []}
    quality = {}
    klines, fundings = {}, {}
    for sym in SYMBOLS:
        klines[sym], kd = load_klines(sym)
        fundings[sym], fd = load_funding(sym)
        quality[f"{sym}_dup_kline"] = kd
        quality[f"{sym}_dup_funding"] = fd
    for sym in SYMBOLS:
        k = klines[sym]; f = fundings[sym]
        zs_all = funding_z(f["last_funding_rate"])
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            q, trades, dropped = run_span(sym, k, f, zs_all, s_str, e_str)
            key = f"{sym}_span{si+1}_{s_str}~{e_str}"
            report["spans"][key] = q
            quality[key] = q
            for b in BUCKETS:
                for h in HORIZONS:
                    st = cell_stats(trades[(b, h)], h, None, None)
                    st.update({"symbol": sym, "span": f"{s_str}~{e_str}", "bucket": b, "horizon": h,
                               "dropped_gap": dropped[(b, h)]})
                    report["cells"].append(st)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    # PASS 판정 (사전 고정 규칙)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        same = [x for x in report["cells"] if x["bucket"] == c["bucket"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30]
        verdict["holm_pass_cells"].append({"cell": f"{c['symbol']} {c['span']} {c['bucket']} h{c['horizon']}",
                                           "p_holm": c["p_holm"], "net16": c["net16"],
                                           "config_pos_of_4": len(pos)})
    if verdict["holm_pass_cells"]:
        best = max(verdict["holm_pass_cells"], key=lambda x: x["config_pos_of_4"])
        verdict["PASS"] = best["config_pos_of_4"] >= 3
        verdict["best_config"] = best
    report["verdict"] = verdict
    with open(f"{OUT}/fr_extreme_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    # 마크다운 요약
    lines = ["# P1 fr_extreme 결과 (사전등록 규격)", "",
             f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · 통제: 추종 방향 + 무작위 100시드", ""]
    for sym in SYMBOLS:
        for si in range(2):
            key = [k for k in report["spans"] if k.startswith(f"{sym}_span{si+1}_")][0]
            qq = report["spans"][key]
            lines.append(f"## {key} — 봉 {qq['bars_in_span']}/{qq['expected_bars']}(결측 {qq['missing_bars']}), 펀딩 {qq['funding_in_span']}/{qq['expected_funding']}")
            lines.append("| 버킷 | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 추종순16 | P(랜덤≥) | 드랍 |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                if c["symbol"] == sym and c["span"].startswith(f"{s_str}" ) and si == [i for i, (a, _) in enumerate(SPANS[sym])][si]:
                    pass
            for c in report["cells"]:
                if c["symbol"] != sym: continue
                spans_list = SPANS[sym]
                if c["span"] != f"{spans_list[si][0]}~{spans_list[si][1]}": continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                lines.append(f"| {c['bucket']} | {c['horizon']} | {c['n']} | {c['valid_n']:.0f} | {fmt(c['total_bp'])} | {fmt(c['net12'])} | {fmt(c['net16'])} | {fmt(c['net20'])} | {fmt(c['t'])} | {c['p']:.4f} | {c['p_holm']:.4f} | {fmt(c['follow_net16'])} | {fmt(c['p_ctrl'])} | {c['dropped_gap']} |")
            lines.append("")
    lines.append("## 판정 (사전 고정 규칙)")
    lines.append(f"- Holm p<0.05 셀: {len(passed)}개")
    for v in verdict["holm_pass_cells"]:
        lines.append(f"  - {v['cell']} — p_holm={v['p_holm']:.4f}, 순16={v['net16']:.2f}bp, 동일설정 4구간 중 양수 {v['config_pos_of_4']}개")
    lines.append(f"- **PASS = {verdict['PASS']}**")
    with open(f"{OUT}/fr_extreme_results.md", "w") as fp:
        fp.write("\n".join(lines))
    print("\n".join(lines[-14:]))

if __name__ == "__main__":
    main()
