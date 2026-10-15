#!/usr/bin/env python3
# F2/X6 공포탐욕 극단 역발 진단 — PREREG_f2x6_fng_2026-10-12.md 규격 구현.
# 일간 신호: FG(00:00 UTC 발행) → 00:15 UTC 15m봉 진입, h∈{96,288,672}. valid_n = n/max(1,h//96) 일간 규약.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run"
OUT = f"{BASE}/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
THRS = [(25, 75), (20, 80), (30, 70)]
HORIZONS = [96, 288, 672]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
SEEDS = 100
CELLS = [(t, h) for t in THRS for h in HORIZONS]  # 9셀/구성 → 36셀
DAY = 86400000
ENTRY_OFFSET = 900000  # 00:15 UTC

def load_fng():
    d = json.load(open(f"{BASE}/research/r3_fng_hist.json"))["data"]
    rows = [(int(x["timestamp"]) * 1000, int(x["value"])) for x in d]
    df = pd.DataFrame(rows, columns=["day_ms", "fg"]).drop_duplicates("day_ms").sort_values("day_ms")
    return df

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
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, fng, s_str, e_str):
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    fd = fng["day_ms"].to_numpy(np.int64)
    fv = fng["fg"].to_numpy(int)
    q = {"days_in_span": int(((fd >= s_ms) & (fd < e_ms)).sum()), "signal_days": 0}
    trades = {c: [] for c in CELLS}
    dropped = {c: 0 for c in CELLS}
    for j in range(len(fd)):
        t = int(fd[j])
        if not (s_ms <= t < e_ms):
            continue
        v = fv[j]
        sgn = None
        for lo, hi in THRS:
            if v <= lo: sgn = 1.0
            elif v >= hi: sgn = -1.0
            else: continue
            break
        if sgn is None:
            continue
        q["signal_days"] += 1
        ei = np.searchsorted(kot, t + ENTRY_OFFSET, side="left")
        if ei >= len(kot) or kot[ei] != t + ENTRY_OFFSET or kot[ei] >= e_ms:
            continue
        for thr, h in CELLS:
            lo, hi = thr
            if v <= lo: s2 = 1.0
            elif v >= hi: s2 = -1.0
            else: continue
            xi = ei + h
            if xi >= len(kot) or kot[xi] >= e_ms: continue
            if (np.diff(kot[ei:xi + 1]) != 900000).any():
                dropped[(thr, h)] += 1; continue
            raw = (kop[xi] - kop[ei]) / kop[ei]
            trades[(thr, h)].append(s2 * raw)
    return q, trades, dropped

def cell_stats(rets, h):
    n = len(rets)
    valid_n = n / max(1, h // 96)  # 일간 규약 (사전 고정)
    out = {"n": n, "valid_n": valid_n}
    if n == 0:
        out.update({"total_bp": None, "net12": None, "net16": None, "net20": None,
                    "t": None, "p": 1.0, "oppose_net16": None, "p_ctrl": None})
        return out
    r = np.array(rets)
    out["total_bp"] = float(r.mean() * 1e4)
    for k, c in COST.items():
        out[f"net{k[1:]}"] = float((r.mean() - c) * 1e4)
    sd = r.std(ddof=1)
    t = (r.mean() - COST["c16"]) / (sd / np.sqrt(valid_n)) if sd > 0 else 0.0
    out["t"] = float(t)
    out["p"] = float(2 * stats.t.sf(abs(t), df=max(valid_n - 1, 1)))
    out["oppose_net16"] = float((-r.mean() - COST["c16"]) * 1e4)
    rng = np.random.default_rng(20261012)
    rand_means = []
    for _ in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * r).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def main():
    report = {"prereg": "PREREG_f2x6_fng_2026-10-12.md", "family_size": 36,
              "signal": "F&G extreme contrarian (<=lo LONG, >=hi SHORT), thr pairs (25/75,20/80,30/70), h in {96,288,672}, daily valid_n rule",
              "spans": {}, "cells": []}
    quality = {}
    fng = load_fng()
    quality["fng_days"] = int(len(fng))
    klines = {sym: load_klines(sym) for sym in SYMBOLS}
    for sym in SYMBOLS:
        k = klines[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            q, trades, dropped = run_span(k, fng, s_str, e_str)
            key = f"{sym}_span{si+1}_{s_str}~{e_str}"
            report["spans"][key] = q
            quality[key] = q
            for thr, h in CELLS:
                st = cell_stats(trades[(thr, h)], h)
                st.update({"symbol": sym, "span": f"{s_str}~{e_str}", "thr": f"{thr[0]}/{thr[1]}", "horizon": h,
                           "dropped_gap": dropped[(thr, h)]})
                report["cells"].append(st)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        same = [x for x in report["cells"] if x["thr"] == c["thr"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30]
        verdict["holm_pass_cells"].append({"cell": f"{c['symbol']} {c['span']} thr{c['thr']} h{c['horizon']}",
                                           "p_holm": c["p_holm"], "net16": c["net16"],
                                           "config_pos_of_4": len(pos)})
    if verdict["holm_pass_cells"]:
        best = max(verdict["holm_pass_cells"], key=lambda x: x["config_pos_of_4"])
        verdict["PASS"] = best["config_pos_of_4"] >= 3
        verdict["best_config"] = best
    report["verdict"] = verdict

    oos = {"executed": False, "segments": []}
    if passed:
        oos["executed"] = True
        oos["segments"] = oos_eval(klines, fng, passed)
        report["oos"] = oos

    fam = family_precheck(fng)
    report["family_precheck"] = fam

    with open(f"{OUT}/f2x6_fng_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4")
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:5]
    print("top5 net16:", [(f"thr{c['thr']}h{c['horizon']}", c['symbol'], round(c['net16'], 2)) for c in ranked])

def oos_eval(klines, fng, passed_cells):
    segs = []
    combos = sorted({(c["thr"], c["horizon"]) for c in passed_cells})
    for sym in SYMBOLS:
        k = klines[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            cut = s_ms + int((e_ms - s_ms) * 0.6)
            kot = k["open_time"].to_numpy(np.int64)
            kop = k["open"].to_numpy(float)
            fd = fng["day_ms"].to_numpy(np.int64)
            fv = fng["fg"].to_numpy(int)
            for thr, h in combos:
                lo, hi = thr
                rets = []
                for j in range(len(fd)):
                    t = int(fd[j])
                    if not (cut <= t < e_ms): continue
                    v = fv[j]
                    if v <= lo: sgn = 1.0
                    elif v >= hi: sgn = -1.0
                    else: continue
                    ei = np.searchsorted(kot, t + ENTRY_OFFSET, side="left")
                    if ei >= len(kot) or kot[ei] != t + ENTRY_OFFSET: continue
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                    rets.append(sgn * (kop[xi] - kop[ei]) / kop[ei])
                n = len(rets)
                net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                segs.append({"symbol": sym, "span": f"{s_str}~{e_str}", "thr": f"{lo}/{hi}", "horizon": h,
                             "n": n, "net16": net16})
    return segs

def family_precheck(fng):
    out = {"note": "FG 일간값 vs 펀딩 z(당일 00:00 UTC) 스피어만 — 센티먼트·캐리 중복 확인(정보성)", "pairs": []}
    for sym in SYMBOLS:
        f_files = sorted(glob.glob(f"{BASE}/data/funding/{sym}-fundingRate-*.csv"))
        f = pd.concat([pd.read_csv(x) for x in f_files], ignore_index=True)
        f = f.drop_duplicates("calc_time").sort_values("calc_time").reset_index(drop=True)
        r = f["last_funding_rate"].to_numpy(dtype=float)
        fz = np.full(len(r), np.nan)
        for i in range(90, len(r)):
            w = r[i - 90:i]; s = w.std(ddof=1)
            if s > 0: fz[i] = (r[i] - w.mean()) / s
        ft = f["calc_time"].to_numpy(np.int64)
        # 일간(00:00 UTC) 펀딩만 매핑
        day_mask = (ft % DAY == 0)
        fd, fzd = ft[day_mask], fz[day_mask]
        merged = pd.DataFrame({"day_ms": fd, "fz": fzd}).merge(fng, on="day_ms", how="inner")
        if len(merged) >= 100:
            rho, pv = stats.spearmanr(merged["fg"].to_numpy(float), merged["fz"].to_numpy(float))
            out["pairs"].append({"pair": f"{sym} FG vs 펀딩z(일간 00:00)", "n": len(merged), "rho": float(rho), "p": float(pv)})
    return out

def write_md(report, quality):
    L = ["# F2/X6 공포탐욕 극단 역발 결과 (사전등록 규격 — 일간 규약)", "",
         "신호: F&G ≤lo → 롱 / ≥hi → 숏 (역발), thr쌍 (25/75, 20/80, 30/70), h∈{96,288,672}",
         f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · valid_n = n/max(1,h÷96) 일간 규약 · 통제: 반전 + 무작위 100시드", "",
         f"F&G 데이터: {quality['fng_days']}일", ""]
    for sym in SYMBOLS:
        for si in range(2):
            key = [k for k in report["spans"] if k.startswith(f"{sym}_span{si+1}_")][0]
            qq = report["spans"][key]
            L.append(f"## {key} — 구간 일수 {qq['days_in_span']}, 신호일 {qq['signal_days']}")
            L.append("| thr | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 반대순16 | P(랜덤≥) | 드랍 |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                spans_list = SPANS[sym]
                if c["symbol"] != sym or c["span"] != f"{spans_list[si][0]}~{spans_list[si][1]}": continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                L.append(f"| {c['thr']} | {c['horizon']} | {c['n']} | {c['valid_n']:.0f} | {fmt(c['total_bp'])} | "
                         f"{fmt(c['net12'])} | {fmt(c['net16'])} | {fmt(c['net20'])} | {fmt(c['t'])} | {c['p']:.4f} | "
                         f"{c['p_holm']:.4f} | {fmt(c['oppose_net16'])} | {fmt(c['p_ctrl'])} | {c['dropped_gap']} |")
            L.append("")
    L.append("## 판정 (사전 고정 규칙)")
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    L.append(f"- Holm p<0.05 셀: {len(passed)}개")
    for v in report["verdict"]["holm_pass_cells"]:
        L.append(f"  - {v['cell']} — p_holm={v['p_holm']:.4f}, 순16={v['net16']:.2f}bp, 동일설정 4구성 중 양수 {v['config_pos_of_4']}개")
    L.append(f"- **PASS = {report['verdict']['PASS']}**")
    if report.get("oos", {}).get("executed"):
        L.append("")
        L.append("## OOS (각 구간 하위 40%)")
        pos_segs = [s for s in report["oos"]["segments"] if s["net16"] is not None and s["net16"] > 0]
        for s in report["oos"]["segments"]:
            ns = "n/a" if s["net16"] is None else f"{s['net16']:.2f}bp"
            L.append(f"- {s['symbol']} {s['span']} thr{s['thr']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        L.append(f"- OOS 양수 세그먼트: {len(pos_segs)}/{len(report['oos']['segments'])}")
    L.append("")
    L.append("## 패밀리 사전검증 (정보성)")
    for p in report["family_precheck"]["pairs"]:
        L.append(f"- {p['pair']}: ρ={p['rho']:.4f} (p={p['p']:.3g}, n={p['n']})")
    with open(f"{OUT}/f2x6_fng_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
