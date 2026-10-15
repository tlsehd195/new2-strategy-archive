#!/usr/bin/env python3
# C4 프리미엄 인덱스 인트라-인터벌 스파이크 회귀 진단 — PREREG_c4_premium_spike_2026-10-12.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {  # X3·C1과 동일 잠금 해제 구간
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
BUCKETS = {"B80": 1.2816, "B90": 1.6449}
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
NWIN = 960
SEEDS = 100

def load_premium(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/premium/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "close"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    dups = int(df.duplicated("open_time").sum())
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df, dups

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

def spike_z(prem):
    """Δp = close − open (봉 내 순변화); z = (Δp − mean(직전 960)) / std(직전 960) — 인과."""
    dp = (prem["close"] - prem["open"]).to_numpy(dtype=float)
    ds = pd.Series(dp)
    m = ds.rolling(NWIN).mean().shift(1).to_numpy()
    s = ds.rolling(NWIN).std(ddof=1).shift(1).to_numpy()
    z = np.where((~np.isnan(m)) & (~np.isnan(s)) & (s > 0), (dp - m) / s, np.nan)
    return z

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, prem, zs, s_str, e_str):
    """양측 회귀: z≥+thr → 숏, z≤−thr → 롱. 신호봉 t → t+1 시가 진입, t+1+h 시가 청산."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    pot = prem["open_time"].to_numpy(dtype=np.int64)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    q = {}
    q["kline_bars_in_span"] = int(((kot >= s_ms) & (kot < e_ms)).sum())
    q["expected_bars"] = int((e_ms - s_ms) // 900000)
    q["kline_missing"] = q["expected_bars"] - q["kline_bars_in_span"]
    in_mask = (pot >= s_ms) & (pot < e_ms)
    q["prem_bars_in_span"] = int(in_mask.sum())
    sig_idx = []
    for i in range(len(pot) - 1):
        t = pot[i]
        if not (s_ms <= t < e_ms): continue
        if pot[i + 1] != t + 900000: continue
        if not np.isfinite(zs[i]): continue
        sig_idx.append(i)
    q["signal_bars_any"] = len(sig_idx)
    trades = {k2: [] for k2 in [(b, h) for b in BUCKETS for h in HORIZONS]}
    dropped = {k2: 0 for k2 in trades}
    for i in sig_idx:
        t = pot[i]
        ei = np.searchsorted(kot, t + 900000, side="left")
        if ei >= len(kot) or kot[ei] != t + 900000 or kot[ei] >= e_ms:
            continue
        for b, thr in BUCKETS.items():
            z = zs[i]
            if z >= thr:
                sgn = -1.0   # 스파이크 상승 → 숏 (회귀)
            elif z <= -thr:
                sgn = 1.0    # 스파이크 하락 → 롱 (회귀)
            else:
                continue
            for h in HORIZONS:
                xi = ei + h
                if xi >= len(kot) or kot[xi] >= e_ms: continue
                if (np.diff(kot[ei:xi + 1]) != 900000).any():
                    dropped[(b, h)] += 1; continue
                raw = (kop[xi] - kop[ei]) / kop[ei]
                trades[(b, h)].append((sgn * raw, t))
    return q, trades, dropped

def cell_stats(rets, h):
    n = len(rets)
    out = {"n": n, "valid_n": n / h}
    if n == 0:
        out.update({"total_bp": None, "net12": None, "net16": None, "net20": None,
                    "t": None, "p": 1.0, "follow_net16": None, "p_ctrl": None})
        return out
    r = np.array(rets)
    vn = n / h
    out["total_bp"] = float(r.mean() * 1e4)
    for k, c in COST.items():
        out[f"net{k[1:]}"] = float((r.mean() - c) * 1e4)
    sd = r.std(ddof=1)
    t = (r.mean() - COST["c16"]) / (sd / np.sqrt(vn)) if sd > 0 else 0.0
    out["t"] = float(t)
    out["p"] = float(2 * stats.t.sf(abs(t), df=max(vn - 1, 1)))
    out["follow_net16"] = float((-r.mean() - COST["c16"]) * 1e4)  # 스파이크 추종 통제
    rng = np.random.default_rng(20261012)
    rand_means = []
    for _ in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * r).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def main():
    report = {"prereg": "PREREG_c4_premium_spike_2026-10-12.md", "family_size": 24,
              "signal": "premium intrabar spike z (close-open, |z|>=1.2816/1.6449) -> REVERSAL on perp",
              "spans": {}, "cells": []}
    quality = {}
    klines, prems, zmaps = {}, {}, {}
    for sym in SYMBOLS:
        prems[sym], pd_ = load_premium(sym)
        klines[sym] = load_klines(sym)
        quality[f"{sym}_dup_prem"] = pd_
        zmaps[sym] = spike_z(prems[sym])
        quality[f"{sym}_prem_rows"] = int(len(prems[sym]))
        quality[f"{sym}_z_valid_rows"] = int(np.isfinite(zmaps[sym]).sum())
    for sym in SYMBOLS:
        k = klines[sym]; prem = prems[sym]; zs = zmaps[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            q, trades, dropped = run_span(k, prem, zs, s_str, e_str)
            key = f"{sym}_span{si+1}_{s_str}~{e_str}"
            report["spans"][key] = q
            quality[key] = q
            for b in BUCKETS:
                for h in HORIZONS:
                    st = cell_stats([x[0] for x in trades[(b, h)]], h)
                    st.update({"symbol": sym, "span": f"{s_str}~{e_str}", "bucket": b, "horizon": h,
                               "dropped_gap": dropped[(b, h)]})
                    report["cells"].append(st)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
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

    oos = {"executed": False, "segments": []}
    if passed:
        oos["executed"] = True
        oos["segments"] = oos_eval(klines, prems, zmaps, passed)
        report["oos"] = oos

    fam = family_precheck(prems, zmaps)
    report["family_precheck"] = fam

    with open(f"{OUT}/c4_premium_spike_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4")
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:5]
    print("top5 net16:", [(f"{c['bucket']}h{c['horizon']}", c['symbol'], round(c['net16'], 2)) for c in ranked])

def oos_eval(klines, prems, zmaps, passed_cells):
    segs = []
    combos = sorted({(c["bucket"], c["horizon"]) for c in passed_cells})
    for sym in SYMBOLS:
        k = klines[sym]; prem = prems[sym]; zs = zmaps[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            cut = s_ms + int((e_ms - s_ms) * 0.6)
            pot = prem["open_time"].to_numpy(dtype=np.int64)
            kot = k["open_time"].to_numpy(dtype=np.int64)
            kop = k["open"].to_numpy(dtype=float)
            for b, h in combos:
                thr = BUCKETS[b]
                rets = []
                for i in range(len(pot) - 1):
                    t = pot[i]
                    if not (cut <= t < e_ms): continue
                    if pot[i + 1] != t + 900000: continue
                    z = zs[i]
                    if not np.isfinite(z): continue
                    if z >= thr: sgn = -1.0
                    elif z <= -thr: sgn = 1.0
                    else: continue
                    ei = np.searchsorted(kot, t + 900000, side="left")
                    if ei >= len(kot) or kot[ei] != t + 900000: continue
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                    rets.append(sgn * (kop[xi] - kop[ei]) / kop[ei])
                n = len(rets)
                net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                segs.append({"symbol": sym, "span": f"{s_str}~{e_str}", "bucket": b, "horizon": h,
                             "n": n, "net16": net16})
    return segs

def family_precheck(prems, zmaps):
    """정보성: C4 z vs C1 L16 z, C4 z vs P1 펀딩 z 스피어만."""
    out = {"c1_structural_note": "C1 L16은 봉간 close→close 16봉 변화, C4는 봉 내 open→close 1봉 변화 — 동일 변수(프리미엄 변화) 패밀리, 방향 매핑은 C1=추종 / C4=회귀로 반대.",
           "pairs": []}
    for sym in SYMBOLS:
        # C1 L16 z 재계산 (정의 동일)
        b = prems[sym]["close"].to_numpy(dtype=float)
        d = np.full(len(b), np.nan); d[16:] = b[16:] - b[:-16]
        ds = pd.Series(d)
        m = ds.rolling(NWIN).mean().shift(1).to_numpy()
        s = ds.rolling(NWIN).std(ddof=1).shift(1).to_numpy()
        z_c1 = np.where((~np.isnan(m)) & (~np.isnan(s)) & (s > 0), (d - m) / s, np.nan)
        z_c4 = zmaps[sym]
        mask = np.isfinite(z_c1) & np.isfinite(z_c4)
        if mask.sum() >= 100:
            rho, pv = stats.spearmanr(z_c1[mask], z_c4[mask])
            out["pairs"].append({"symbol": sym, "pair": "C4z_vs_C1L16z", "n": int(mask.sum()),
                                 "spearman_rho": float(rho), "p": float(pv)})
        f_files = sorted(glob.glob(f"{BASE}/funding/{sym}-fundingRate-*.csv"))
        f = pd.concat([pd.read_csv(x) for x in f_files], ignore_index=True)
        f = f.drop_duplicates("calc_time").sort_values("calc_time").reset_index(drop=True)
        r = f["last_funding_rate"].to_numpy(dtype=float)
        fz = np.full(len(r), np.nan)
        N90 = 90
        for i in range(N90, len(r)):
            w = r[i - N90:i]; sd = w.std(ddof=1)
            if sd > 0: fz[i] = (r[i] - w.mean()) / sd
        ft = f["calc_time"].to_numpy(dtype=np.int64)
        pot = prems[sym]["open_time"].to_numpy(dtype=np.int64)
        idx = np.searchsorted(pot, ft, side="right") - 1
        pairs = [(z_c4[i], fz[j]) for j, i in enumerate(idx)
                 if 0 <= i < len(pot) and np.isfinite(z_c4[i]) and np.isfinite(fz[j])]
        if len(pairs) >= 100:
            a = np.array([x[0] for x in pairs]); bb = np.array([x[1] for x in pairs])
            rho, pv = stats.spearmanr(a, bb)
            out["pairs"].append({"symbol": sym, "pair": "C4z_vs_P1fundingz", "n": len(pairs),
                                 "spearman_rho": float(rho), "p": float(pv)})
    return out

def write_md(report):
    L = ["# C4 프리미엄 인덱스 인트라-인터벌 스파이크 회귀 결과 (사전등록 규격)", "",
         "신호: 프리미엄 봉 내 스파이크 z (close−open) |z|≥1.2816/1.6449 → **회귀** (스파이크 반대 방향 퍼페 진입)",
         f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · 통제: 스파이크 추종 + 무작위 100시드", ""]
    for sym in SYMBOLS:
        for si in range(2):
            key = [k for k in report["spans"] if k.startswith(f"{sym}_span{si+1}_")][0]
            qq = report["spans"][key]
            L.append(f"## {key} — kline {qq['kline_bars_in_span']}/{qq['expected_bars']}(결측 {qq['kline_missing']}), 프리미엄봉 {qq['prem_bars_in_span']}, 신호봉 {qq['signal_bars_any']}")
            L.append("| 버킷 | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 추종순16 | P(랜덤≥) | 드랍 |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                spans_list = SPANS[sym]
                if c["symbol"] != sym or c["span"] != f"{spans_list[si][0]}~{spans_list[si][1]}": continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                L.append(f"| {c['bucket']} | {c['horizon']} | {c['n']} | {c['valid_n']:.0f} | {fmt(c['total_bp'])} | "
                         f"{fmt(c['net12'])} | {fmt(c['net16'])} | {fmt(c['net20'])} | {fmt(c['t'])} | {c['p']:.4f} | "
                         f"{c['p_holm']:.4f} | {fmt(c['follow_net16'])} | {fmt(c['p_ctrl'])} | {c['dropped_gap']} |")
            L.append("")
    L.append("## 판정 (사전 고정 규칙)")
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    L.append(f"- Holm p<0.05 셀: {len(passed)}개")
    for v in report["verdict"]["holm_pass_cells"]:
        L.append(f"  - {v['cell']} — p_holm={v['p_holm']:.4f}, 순16={v['net16']:.2f}bp, 동일설정 4구성 중 양수 {v['config_pos_of_4']}개")
    L.append(f"- **PASS = {report['verdict']['PASS']}**")
    if report.get("oos", {}).get("executed"):
        L.append("")
        L.append("## OOS (각 구간 하위 40%, 사전 고정 규칙)")
        pos_segs = [s for s in report["oos"]["segments"] if s["net16"] is not None and s["net16"] > 0]
        for s in report["oos"]["segments"]:
            ns = "n/a" if s["net16"] is None else f"{s['net16']:.2f}bp"
            L.append(f"- {s['symbol']} {s['span']} {s['bucket']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        L.append(f"- OOS 양수 세그먼트: {len(pos_segs)}/{len(report['oos']['segments'])} (승격 자격 기준 ≥2)")
    fp_ = report.get("family_precheck", {})
    L.append("")
    L.append("## 패밀리 사전검증 (정보성)")
    L.append(f"- {fp_['c1_structural_note']}")
    for p in fp_["pairs"]:
        L.append(f"- {p['symbol']} {p['pair']}: ρ={p['spearman_rho']:.4f} (p={p['p']:.3g}, n={p['n']})")
    with open(f"{OUT}/c4_premium_spike_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
