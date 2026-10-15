#!/usr/bin/env python3
# X1 외부자산(ES/NQ) 1h 모멘텀 동조 진단 — PREREG_x1_external_momentum_2026-10-12.md 규격 구현.
# 단일 구성(ETHUSDT span2 유일) — 패밀리 24셀 (A∈{ES,NQ} × L∈{24,120,480,960} × h∈{4,16,48}).
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장.
import json, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run"
OUT = f"{BASE}/results"
os.makedirs(OUT, exist_ok=True)

def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPAN = ("ETHUSDT", [("2024-12-10", "2026-04-20")])  # 잠금 해제 유일 구간
LOOKBACKS = [24, 120, 480, 960]
THR = 1.2816
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
NWIN = 720
SEEDS = 100
ASSETS = ["ES", "NQ"]
CELLS = [(A, L, h) for A in ASSETS for L in LOOKBACKS for h in HORIZONS]  # 24셀
HOUR = 3600000

def load_yahoo_1h(path):
    d = json.load(open(path))
    res = d["chart"]["result"][0]
    ts = res["timestamp"]
    close = res["indicators"]["quote"][0]["close"]
    pairs = [(int(t) * 1000, float(c)) for t, c in zip(ts, close)
             if c is not None and np.isfinite(float(c))]
    pairs = [(t, c) for t, c in pairs if t % HOUR == 0]  # 정시 격자만(방어)
    df = pd.DataFrame(pairs, columns=["open_time", "close"]).drop_duplicates("open_time")
    df = df.sort_values("open_time").reset_index(drop=True)
    return df

def ext_z(df, L):
    """Δ_t = close_t − close_{t−L} (격자 정시 일치 필요), z = 직전 720 관측 Δ 표준화(인과). C5와 동일 구현."""
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

def run_span(k, z_by_A, t_by_A, s_str, e_str):
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    q = {"signal_bars_any": 0}
    trades = {c: [] for c in CELLS}
    dropped = {c: 0 for c in CELLS}
    for A in ASSETS:
        t = t_by_A[A]; z_by_L = z_by_A[A]
        in_idx = np.where((t >= s_ms) & (t < e_ms))[0]
        for i in in_idx:
            tt = int(t[i])
            if any(np.isfinite(z_by_L[L][i]) for L in LOOKBACKS):
                q["signal_bars_any"] += 1
            ei = np.searchsorted(kot, tt + HOUR, side="left")
            if ei >= len(kot) or kot[ei] != tt + HOUR or kot[ei] >= e_ms:
                continue
            for L in LOOKBACKS:
                z = z_by_L[L][i]
                if not np.isfinite(z): continue
                if z >= THR: sgn = 1.0
                elif z <= -THR: sgn = -1.0
                else: continue
                for h in HORIZONS:
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any():
                        dropped[(A, L, h)] += 1; continue
                    raw = (kop[xi] - kop[ei]) / kop[ei]
                    trades[(A, L, h)].append(sgn * raw)
    return q, trades, dropped

def cell_stats(rets, h):
    n = len(rets)
    out = {"n": n, "valid_n": n / h}
    if n == 0:
        out.update({"total_bp": None, "net12": None, "net16": None, "net20": None,
                    "t": None, "p": 1.0, "oppose_net16": None, "p_ctrl": None})
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
    out["oppose_net16"] = float((-r.mean() - COST["c16"]) * 1e4)
    rng = np.random.default_rng(20261012)
    rand_means = []
    for _ in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * r).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def main():
    report = {"prereg": "PREREG_x1_external_momentum_2026-10-12.md", "family_size": 24,
              "signal": "ES/NQ 1h momentum z (L in 24/120/480/960) |z|>=1.2816, FOLLOW, ETHUSDT perp execution",
              "config_note": "단일 구성(ETHUSDT span2) — ES/NQ 1h 데이터가 이 구간과만 겹침(Yahoo 1h 2024-05~ 실측)",
              "spans": {}, "cells": []}
    quality = {}
    k = load_klines("ETHUSDT")
    ext = {A: load_yahoo_1h(f"{BASE}/research/r3_yahoo_{A.lower()}.json") for A in ASSETS}
    for A in ASSETS:
        quality[f"{A}_bars"] = int(len(ext[A]))
        quality[f"{A}_range"] = [str(pd.Timestamp(ext[A]["open_time"].iloc[0], unit="ms", tz="UTC")),
                                  str(pd.Timestamp(ext[A]["open_time"].iloc[-1], unit="ms", tz="UTC"))]
    z_by_A, t_by_A = {}, {}
    for A in ASSETS:
        z_by_A[A] = {}
        t_by_A[A] = {}
        for L in LOOKBACKS:
            t, z = ext_z(ext[A], L)
            z_by_A[A][L] = z
            t_by_A[A] = t
            quality[f"{A}_z_valid_L{L}"] = int(np.isfinite(z).sum())
    s_str, e_str = SPAN[1][0]
    q, trades, dropped = run_span(k, z_by_A, t_by_A, s_str, e_str)
    key = f"ETHUSDT_span2_{s_str}~{e_str}"
    report["spans"][key] = q
    quality[key] = q
    for A, L, h in CELLS:
        st = cell_stats(trades[(A, L, h)], h)
        st.update({"asset": A, "lookback": L, "horizon": h, "dropped_gap": dropped[(A, L, h)]})
        report["cells"].append(st)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        twin = [x for x in report["cells"] if x["lookback"] == c["lookback"] and x["horizon"] == c["horizon"]]
        ok_all = all(x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30 for x in twin)
        verdict["holm_pass_cells"].append({"cell": f"{c['asset']} L{c['lookback']} h{c['horizon']}",
                                           "p_holm": c["p_holm"], "net16": c["net16"],
                                           "both_assets_positive": ok_all})
    if verdict["holm_pass_cells"]:
        verdict["PASS"] = any(v["both_assets_positive"] for v in verdict["holm_pass_cells"])
    report["verdict"] = verdict

    oos = {"executed": False}
    if passed:
        oos["executed"] = True
        s_ms, e_ms = ms(s_str), ms(e_str)
        cut = s_ms + int((e_ms - s_ms) * 0.6)
        kot = k["open_time"].to_numpy(np.int64)
        kop = k["open"].to_numpy(float)
        segs = []
        for A, L, h in sorted({(c["asset"], c["lookback"], c["horizon"]) for c in passed}):
            t = t_by_A[A]; zl = z_by_A[A][L]
            rets = []
            for i in np.where((t >= cut) & (t < e_ms))[0]:
                tt = int(t[i]); z = zl[i]
                if not np.isfinite(z): continue
                if z >= THR: sgn = 1.0
                elif z <= -THR: sgn = -1.0
                else: continue
                ei = np.searchsorted(kot, tt + HOUR, side="left")
                if ei >= len(kot) or kot[ei] != tt + HOUR: continue
                xi = ei + h
                if xi >= len(kot) or kot[xi] >= e_ms: continue
                if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                rets.append(sgn * (kop[xi] - kop[ei]) / kop[ei])
            n = len(rets)
            segs.append({"asset": A, "lookback": L, "horizon": h, "n": n,
                         "net16": float((np.mean(rets) - 0.0016) * 1e4) if n else None})
        oos["segments"] = segs
        report["oos"] = oos

    fam = family_precheck(ext, z_by_A, t_by_A, k)
    report["family_precheck"] = fam

    with open(f"{OUT}/x1_external_momentum_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} both_positive={v['both_assets_positive']}")
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:5]
    print("top5 net16:", [(f"{c['asset']}L{c['lookback']}h{c['horizon']}", round(c['net16'], 2)) for c in ranked])
    ranked_rev = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: x["net16"])[:5]
    print("bottom5 net16:", [(f"{c['asset']}L{c['lookback']}h{c['horizon']}", round(c['net16'], 2)) for c in ranked_rev])

def family_precheck(ext, z_by_A, t_by_A, k_eth):
    out = {"note": "ES·NQ 동일 지수군 중복성 + ES Δz vs ETH 1h 수익 전체 분위수 진단(판정 불가입)", "pairs": []}
    # ES vs NQ Δz (공통 시각)
    for L in LOOKBACKS:
        te, ze = t_by_A["ES"], z_by_A["ES"][L]
        tn, zn = t_by_A["NQ"], z_by_A["NQ"][L]
        common, ie, in_ = np.intersect1d(te, tn, return_indices=True)
        pairs = [(ze[ie[i]], zn[in_[i]]) for i in range(len(common))
                 if np.isfinite(ze[ie[i]]) and np.isfinite(zn[in_[i]])]
        if len(pairs) >= 100:
            a = np.array([x[0] for x in pairs]); b = np.array([x[1] for x in pairs])
            rho, pv = stats.spearmanr(a, b)
            out["pairs"].append({"pair": f"ES vs NQ L{L}", "n": len(pairs), "rho": float(rho), "p": float(pv)})
    # ES Δz vs ETH 1h 수익 (정보성 — 진단). 15m 격자에서 연속 4봉 = 1h (open→open).
    kot = k_eth["open_time"].to_numpy(np.int64)
    kop = k_eth["open"].to_numpy(float)
    cont = (np.diff(kot) == 900000)
    cont4 = cont[:-3] & cont[1:-2] & cont[2:-1] & cont[3:]
    base = np.where((kot[:-4] % HOUR == 0) & cont4 & (kot[4:] - kot[:-4] == HOUR))[0]
    ret1h_t = kot[base]
    ret1h_v = (kop[base + 4] - kop[base]) / kop[base]
    for L in LOOKBACKS:
        te, ze = t_by_A["ES"], z_by_A["ES"][L]
        pos = np.searchsorted(ret1h_t, te, side="left")
        pairs = []
        for i in range(len(te)):
            p = pos[i]
            if p < len(ret1h_t) and ret1h_t[p] == te[i] and np.isfinite(ze[i]):
                pairs.append((ze[i], ret1h_v[p]))
        if len(pairs) >= 100:
            a = np.array([x[0] for x in pairs]); b = np.array([x[1] for x in pairs])
            rho, pv = stats.spearmanr(a, b)
            out["pairs"].append({"pair": f"ES Δz L{L} vs ETH 1h fwd ret", "n": len(pairs),
                                  "rho": float(rho), "p": float(pv)})
    return out

def write_md(report, quality):
    L = ["# X1 외부자산(ES/NQ) 1h 모멘텀 동조 결과 (사전등록 규격)", "",
         "신호: ES/NQ 1h Δ z (L∈{24,120,480,960}) |z|≥1.2816 → **추종** (ETHUSDT 퍼페 실행, 단일 구성)",
         f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · 통제: 반대방향 + 무작위 100시드", "",
         f"- ES 바 수: {quality['ES_bars']} ({quality['ES_range'][0][:10]}~{quality['ES_range'][1][:10]})",
         f"- NQ 바 수: {quality['NQ_bars']} ({quality['NQ_range'][0][:10]}~{quality['NQ_range'][1][:10]})", ""]
    L.append("| 자산 | L | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 반대순16 | P(랜덤≥) | 드랍 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in report["cells"]:
        fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
        L.append(f"| {c['asset']} | {c['lookback']} | {c['horizon']} | {c['n']} | {c['valid_n']:.0f} | {fmt(c['total_bp'])} | "
                 f"{fmt(c['net12'])} | {fmt(c['net16'])} | {fmt(c['net20'])} | {fmt(c['t'])} | {c['p']:.4f} | "
                 f"{c['p_holm']:.4f} | {fmt(c['oppose_net16'])} | {fmt(c['p_ctrl'])} | {c['dropped_gap']} |")
    L.append("")
    L.append("## 판정 (사전 고정 규칙 — 단일 구성 대체)")
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    L.append(f"- Holm p<0.05 셀: {len(passed)}개")
    for v in report["verdict"]["holm_pass_cells"]:
        L.append(f"  - {v['cell']} — p_holm={v['p_holm']:.4f}, 순16={v['net16']:.2f}bp, ES·NQ 양수 2/2: {v['both_assets_positive']}")
    L.append(f"- **PASS = {report['verdict']['PASS']}** (통과 시에도 단일 구성 → 조건부 후보 보고)")
    if report.get("oos", {}).get("executed"):
        L.append("")
        L.append("## OOS (구간 하위 40%)")
        for s in report["oos"]["segments"]:
            ns = "n/a" if s["net16"] is None else f"{s['net16']:.2f}bp"
            L.append(f"- {s['asset']} L{s['lookback']} h{s['horizon']} — n={s['n']}, 순16={ns}")
    L.append("")
    L.append("## 패밀리 사전검증 (정보성)")
    for p in report["family_precheck"]["pairs"]:
        L.append(f"- {p['pair']}: ρ={p['rho']:.4f} (p={p['p']:.3g}, n={p['n']})")
    with open(f"{OUT}/x1_external_momentum_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
