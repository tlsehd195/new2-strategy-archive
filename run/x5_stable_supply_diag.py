#!/usr/bin/env python3
# X5 스테이블코인 공급 모멘텀 진단 — PREREG_x5_stable_supply_2026-10-12.md 규격 구현.
# 일간 신호: 공급 스냅샷(00:00 UTC) → 00:15 UTC 15m봉 진입, h∈{96,288,672}. valid_n = n/max(1,h//96) 일간 규약.
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
LOOKBACKS = [1, 7, 30]
HORIZONS = [96, 288, 672]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
SEEDS = 100
WIN90 = 90
THR = 1.2816
DAY = 86400000
ENTRY_OFFSET = 900000
CELLS = [(L, h) for L in LOOKBACKS for h in HORIZONS]  # 9셀/구성 → 36셀

def load_supply():
    d = json.load(open(f"{BASE}/research/r3_llama_usdt_hist.json"))
    rows = [(int(x["date"]) * 1000, float(x["totalCirculating"]["peggedUSD"])) for x in d]
    return pd.DataFrame(rows, columns=["day_ms", "supply"]).drop_duplicates("day_ms").sort_values("day_ms").reset_index(drop=True)

def load_fng():
    d = json.load(open(f"{BASE}/research/r3_fng_hist.json"))["data"]
    rows = [(int(x["timestamp"]) * 1000, float(x["value"])) for x in d]
    return pd.DataFrame(rows, columns=["day_ms", "fg"]).drop_duplicates("day_ms").sort_values("day_ms").reset_index(drop=True)

def build_z(sup, L):
    t = sup["day_ms"].to_numpy(np.int64)
    v = sup["supply"].to_numpy(float)
    d = np.full(len(t), np.nan)
    for i in range(len(t)):
        target = t[i] - L * DAY
        j = np.searchsorted(t, target)
        if j < len(t) and t[j] == target:
            d[i] = v[i] - v[j]
    fin = np.where(np.isfinite(d))[0]
    dv = d[fin]
    rm = pd.Series(dv).rolling(WIN90).mean().shift(1).to_numpy()
    rs = pd.Series(dv).rolling(WIN90).std(ddof=1).shift(1).to_numpy()
    z = np.full(len(t), np.nan)
    ok = np.isfinite(rm) & np.isfinite(rs) & (rs > 0)
    z[fin[ok]] = (dv[ok] - rm[ok]) / rs[ok]
    out = sup.copy()
    out[f"z{L}"] = z
    return out

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

def run_span(k, sup, s_str, e_str):
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    fd = sup["day_ms"].to_numpy(np.int64)
    z_by_L = {L: sup[f"z{L}"].to_numpy(float) for L in LOOKBACKS}
    q = {"days_in_span": int(((fd >= s_ms) & (fd < e_ms)).sum()), "signal_days": 0}
    trades = {c: [] for c in CELLS}
    dropped = {c: 0 for c in CELLS}
    for j in range(len(fd)):
        t = int(fd[j])
        if not (s_ms <= t < e_ms):
            continue
        if not any(np.isfinite(z_by_L[L][j]) for L in LOOKBACKS):
            continue
        q["signal_days"] += 1
        ei = np.searchsorted(kot, t + ENTRY_OFFSET, side="left")
        if ei >= len(kot) or kot[ei] != t + ENTRY_OFFSET or kot[ei] >= e_ms:
            continue
        for L, h in CELLS:
            z = z_by_L[L][j]
            if not np.isfinite(z): continue
            if z >= THR: s2 = 1.0
            elif z <= -THR: s2 = -1.0
            else: continue
            xi = ei + h
            if xi >= len(kot) or kot[xi] >= e_ms: continue
            if (np.diff(kot[ei:xi + 1]) != 900000).any():
                dropped[(L, h)] += 1; continue
            raw = (kop[xi] - kop[ei]) / kop[ei]
            trades[(L, h)].append(s2 * raw)
    return q, trades, dropped

def cell_stats(rets, h):
    n = len(rets)
    valid_n = n / max(1, h // 96)
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

def oos_eval(klines, sup, passed_cells):
    segs = []
    combos = sorted({(c["lookback"], c["horizon"]) for c in passed_cells})
    for sym in SYMBOLS:
        k = klines[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            cut = s_ms + int((e_ms - s_ms) * 0.6)
            kot = k["open_time"].to_numpy(np.int64)
            kop = k["open"].to_numpy(float)
            fd = sup["day_ms"].to_numpy(np.int64)
            for L, h in combos:
                zl = sup[f"z{L}"].to_numpy(float)
                rets = []
                for j in range(len(fd)):
                    t = int(fd[j])
                    if not (cut <= t < e_ms): continue
                    z = zl[j]
                    if not np.isfinite(z): continue
                    if z >= THR: sgn = 1.0
                    elif z <= -THR: sgn = -1.0
                    else: continue
                    ei = np.searchsorted(kot, t + ENTRY_OFFSET, side="left")
                    if ei >= len(kot) or kot[ei] != t + ENTRY_OFFSET: continue
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                    rets.append(sgn * (kop[xi] - kop[ei]) / kop[ei])
                n = len(rets)
                net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                segs.append({"symbol": sym, "span": f"{s_str}~{e_str}", "lookback": L, "horizon": h,
                             "n": n, "net16": net16})
    return segs

def family_precheck(sup, fng):
    out = {"note": "공급 Δz vs 펀딩 z / F&G 스피어만 — 유동성 프록시 중복 확인(정보성)", "pairs": []}
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
        day_mask = (ft % DAY == 0)
        base = pd.DataFrame({"day_ms": ft[day_mask], "fz": fz[day_mask]})
        mrg = base.merge(sup, on="day_ms", how="inner")
        mrg2 = mrg.merge(fng, on="day_ms", how="inner")
        for L in LOOKBACKS:
            zz = mrg[f"z{L}"].to_numpy(float)
            bb = mrg["fz"].to_numpy(float)
            pairs = [(zz[i], bb[i]) for i in range(len(mrg)) if np.isfinite(zz[i]) and np.isfinite(bb[i])]
            if len(pairs) >= 100:
                a = np.array([x[0] for x in pairs]); b2 = np.array([x[1] for x in pairs])
                rho, pv = stats.spearmanr(a, b2)
                out["pairs"].append({"pair": f"{sym} 공급Δz L{L} vs 펀딩z", "n": len(pairs), "rho": float(rho), "p": float(pv)})
        if len(mrg2) >= 100:
            zz = mrg2["z1"].to_numpy(float); gg = mrg2["fg"].to_numpy(float)
            pairs = [(zz[i], gg[i]) for i in range(len(mrg2)) if np.isfinite(zz[i])]
            if len(pairs) >= 100:
                a = np.array([x[0] for x in pairs]); b2 = np.array([x[1] for x in pairs])
                rho, pv = stats.spearmanr(a, b2)
                out["pairs"].append({"pair": f"{sym} 공급Δz L1 vs F&G", "n": len(pairs), "rho": float(rho), "p": float(pv)})
    return out

def main():
    report = {"prereg": "PREREG_x5_stable_supply_2026-10-12.md", "family_size": 36,
              "signal": "stablecoin total supply delta z (L in 1/7/30d) |z|>=1.2816 FOLLOW, h in {96,288,672}, daily valid_n rule",
              "spans": {}, "cells": []}
    quality = {}
    sup = load_supply()
    for L in LOOKBACKS:
        sup = build_z(sup, L)
    quality["supply_days"] = int(len(sup))
    fng = load_fng()
    klines = {sym: load_klines(sym) for sym in SYMBOLS}
    for sym in SYMBOLS:
        k = klines[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            q, trades, dropped = run_span(k, sup, s_str, e_str)
            key = f"{sym}_span{si+1}_{s_str}~{e_str}"
            report["spans"][key] = q
            quality[key] = q
            for L, h in CELLS:
                st = cell_stats(trades[(L, h)], h)
                st.update({"symbol": sym, "span": f"{s_str}~{e_str}", "lookback": L, "horizon": h,
                           "dropped_gap": dropped[(L, h)]})
                report["cells"].append(st)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        same = [x for x in report["cells"] if x["lookback"] == c["lookback"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30]
        verdict["holm_pass_cells"].append({"cell": f"{c['symbol']} {c['span']} L{c['lookback']} h{c['horizon']}",
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
        oos["segments"] = oos_eval(klines, sup, passed)
        report["oos"] = oos

    fam = family_precheck(sup, fng)
    report["family_precheck"] = fam

    with open(f"{OUT}/x5_stable_supply_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4")
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:5]
    print("top5 net16:", [(f"L{c['lookback']}h{c['horizon']}", c['symbol'], round(c['net16'], 2)) for c in ranked])
    ranked_rev = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: x["net16"])[:5]
    print("bottom5 net16:", [(f"L{c['lookback']}h{c['horizon']}", c['symbol'], round(c['net16'], 2)) for c in ranked_rev])

def write_md(report, quality):
    L = ["# X5 스테이블코인 공급 모멘텀 결과 (사전등록 규격 — 일간 규약)", "",
         "신호: 총공급 Δz (L∈{1,7,30}일) |z|≥1.2816 → **추종** (확장 롱/축소 숏), h∈{96,288,672}",
         f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · valid_n = n/max(1,h÷96) 일간 규약 · 통제: 반전 + 무작위 100시드", "",
         f"공급 데이터: {quality['supply_days']}일 (전체 스테이블코인 peggedUSD — 사전등록 §0 정직성 선언)", ""]
    for sym in SYMBOLS:
        for si in range(2):
            key = [k for k in report["spans"] if k.startswith(f"{sym}_span{si+1}_")][0]
            qq = report["spans"][key]
            L.append(f"## {key} — 구간 일수 {qq['days_in_span']}, 신호일 {qq['signal_days']}")
            L.append("| L | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 반대순16 | P(랜덤≥) | 드랍 |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                spans_list = SPANS[sym]
                if c["symbol"] != sym or c["span"] != f"{spans_list[si][0]}~{spans_list[si][1]}": continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                L.append(f"| {c['lookback']} | {c['horizon']} | {c['n']} | {c['valid_n']:.0f} | {fmt(c['total_bp'])} | "
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
            L.append(f"- {s['symbol']} {s['span']} L{s['lookback']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        L.append(f"- OOS 양수 세그먼트: {len(pos_segs)}/{len(report['oos']['segments'])}")
    L.append("")
    L.append("## 패밀리 사전검증 (정보성)")
    for p in report["family_precheck"]["pairs"]:
        L.append(f"- {p['pair']}: ρ={p['rho']:.4f} (p={p['p']:.3g}, n={p['n']})")
    with open(f"{OUT}/x5_stable_supply_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
