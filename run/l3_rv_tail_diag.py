#!/usr/bin/env python3
# L3 실현변동(RV) 꼬리 방향 신호 진단 — PREREG_l3_rv_tail_2026-10-12.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
# 신호: RV_t(L)=std(직전 L봉 15m 로그수익), z=(RV−직전 960 RV평균)/std — 인과(shift1).
#       z≥+1.2816 → 숏(고RV→저수익, Lee 2024 방향) / z≤−1.2816 → 롱. 양측 B80 단일 버킷.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {  # 잠금 해제 구간 (끝 미포함) — 기존 관행과 동일
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
LOOKBACKS = [96, 288, 960, 2880]  # 1d/3d/10d/30d (15m 봉)
THR = 1.2816                      # B80 양측
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
NWIN = 960     # 직전 960개 RV 관측 (인과, shift 1)
SEEDS = 100
CELLS = [(L, h) for L in LOOKBACKS for h in HORIZONS]  # 12셀/구성 → 패밀리 48

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "close", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    dups = int(df.duplicated("open_time").sum())
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df, dups

def rv_z(close, ot, L):
    """r_t = ln(close_t/close_{t−1}) — 연속 봉만(갭 시 NaN).
    RV_t = std(r_{t−L+1..t}, ddof=1). z = (RV − 직전 960 RV 평균)/std — shift 1(인과).
    결측 RV는 관측에서 제외(rolling이 NaN 스킵, min_periods=960). std≤0 또는 NaN이면 신호 없음."""
    c = close.to_numpy(dtype=float)
    t_ = ot.to_numpy(dtype=np.int64)
    r = np.full(len(c), np.nan)
    r[1:] = np.log(c[1:] / c[:-1])
    gap = t_[1:] - t_[:-1]
    r[1:][gap != 900000] = np.nan
    r[0] = np.nan
    rv = pd.Series(r).rolling(L).std(ddof=1).to_numpy()
    rvs = pd.Series(rv)
    m = rvs.rolling(NWIN).mean().shift(1).to_numpy()
    s = rvs.rolling(NWIN).std(ddof=1).shift(1).to_numpy()
    z = np.where((~np.isnan(m)) & (~np.isnan(s)) & (s > 0), (rv - m) / s, np.nan)
    return z

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, z_by_L, s_str, e_str):
    """문헌 방향 매핑: z≥+THR → 숏(sgn=−1), z≤−THR → 롱(sgn=+1).
    신호봉 t → t+1 시가 진입, t+1+h 시가 청산. 갭/구간 밖 드랍(fail-closed)."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    q = {}
    q["kline_bars_in_span"] = int(((kot >= s_ms) & (kot < e_ms)).sum())
    q["expected_bars"] = int((e_ms - s_ms) // 900000)
    q["kline_missing"] = q["expected_bars"] - q["kline_bars_in_span"]
    # 신호봉 수: |z|≥THR인 L이 하나라도 있는 연속 신호봉
    mask_any = np.zeros(len(kot), dtype=bool)
    for L in LOOKBACKS:
        mask_any |= np.abs(z_by_L[L]) >= THR
    sig_idx = []
    for i in range(len(kot) - 1):
        t = kot[i]
        if not (s_ms <= t < e_ms): continue
        if kot[i + 1] != t + 900000: continue
        if not mask_any[i]: continue
        sig_idx.append(i)
    q["signal_bars_any"] = len(sig_idx)
    trades = {k2: [] for k2 in CELLS}
    dropped = {k2: 0 for k2 in CELLS}
    sig_per_L = {L: 0 for L in LOOKBACKS}
    for i in sig_idx:
        t = kot[i]
        ei = i + 1
        if kot[ei] != t + 900000 or kot[ei] >= e_ms: continue
        for L in LOOKBACKS:
            z = z_by_L[L][i]
            if not np.isfinite(z): continue
            if z >= THR: sgn = -1.0      # 고RV → 숏 (문헌 방향)
            elif z <= -THR: sgn = +1.0   # 저RV → 롱 (문헌 방향)
            else: continue
            sig_per_L[L] += 1
            for h in HORIZONS:
                xi = ei + h
                if xi >= len(kot) or kot[xi] >= e_ms: continue
                if (np.diff(kot[ei:xi + 1]) != 900000).any():
                    dropped[(L, h)] += 1; continue  # fail-closed
                raw = (kop[xi] - kop[ei]) / kop[ei]
                trades[(L, h)].append((sgn * raw, t))
    q["signal_bars_per_L"] = sig_per_L
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
    out["oppose_net16"] = float((-r.mean() - COST["c16"]) * 1e4)  # 문헌 반대(고RV→롱) 통제
    rng = np.random.default_rng(20261012)
    rand_means = []
    for _ in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * r).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def main():
    report = {"prereg": "PREREG_l3_rv_tail_2026-10-12.md", "family_size": 48,
              "signal": "RV z (L in 96/288/960/2880) |z|>=1.2816, LEE-DIRECTION (z>=+ -> SHORT, z<=- -> LONG)",
              "spans": {}, "cells": []}
    quality = {}
    klines, zmaps = {}, {}
    for sym in SYMBOLS:
        klines[sym], dup = load_klines(sym)
        quality[f"{sym}_dup_kline"] = dup
        quality[f"{sym}_kline_rows"] = int(len(klines[sym]))
        quality[f"{sym}_kline_first"] = str(pd.to_datetime(klines[sym]["open_time"].iloc[0], unit="ms", utc=True))
        zmaps[sym] = {L: rv_z(klines[sym]["close"], klines[sym]["open_time"], L) for L in LOOKBACKS}
        quality[f"{sym}_z_valid_rows"] = {str(L): int(np.isfinite(zmaps[sym][L]).sum()) for L in LOOKBACKS}
    for sym in SYMBOLS:
        k = klines[sym]; zm = zmaps[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            q, trades, dropped = run_span(k, zm, s_str, e_str)
            key = f"{sym}_span{si+1}_{s_str}~{e_str}"
            report["spans"][key] = q
            quality[key] = q
            for L, h in CELLS:
                st = cell_stats([x[0] for x in trades[(L, h)]], h)
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
        oos["segments"] = oos_eval(klines, zmaps, passed)
        report["oos"] = oos

    fam = family_precheck(klines, zmaps)
    report["family_precheck"] = fam

    with open(f"{OUT}/l3_rv_tail_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4")
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:5]
    print("top5 net16:", [(f"L{c['lookback']}h{c['horizon']}", c['symbol'], round(c['net16'], 2)) for c in ranked])
    ranked_o = sorted([c for c in report["cells"] if c["oppose_net16"] is not None], key=lambda x: -x["oppose_net16"])[:5]
    print("top5 반대(고RV→롱) 순16:", [(f"L{c['lookback']}h{c['horizon']}", c['symbol'], round(c['oppose_net16'], 2)) for c in ranked_o])

def oos_eval(klines, zmaps, passed_cells):
    """Holm 통과 (L,h) 조합만 OOS(각 구간 하위 40%)에 동일 적용. 세그먼트별 순16 양수 여부."""
    segs = []
    combos = sorted({(c["lookback"], c["horizon"]) for c in passed_cells})
    for sym in SYMBOLS:
        k = klines[sym]; zm = zmaps[sym]
        kot = k["open_time"].to_numpy(dtype=np.int64)
        kop = k["open"].to_numpy(dtype=float)
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            cut = s_ms + int((e_ms - s_ms) * 0.6)
            for L, h in combos:
                rets = []
                for i in range(len(kot) - 1):
                    t = kot[i]
                    if not (cut <= t < e_ms): continue
                    if kot[i + 1] != t + 900000: continue
                    z = zm[L][i]
                    if not np.isfinite(z): continue
                    if z >= THR: sgn = -1.0
                    elif z <= -THR: sgn = +1.0
                    else: continue
                    ei = i + 1
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                    rets.append(sgn * (kop[xi] - kop[ei]) / kop[ei])
                n = len(rets)
                net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                segs.append({"symbol": sym, "span": f"{s_str}~{e_str}", "lookback": L, "horizon": h,
                             "n": n, "net16": net16})
    return segs

def family_precheck(klines, zmaps):
    """정보성: L3 RV z(L별) vs P1 펀딩 z, vs C1 베이시스 모멘텀 z(L별) 스피어만."""
    out = {"design_note": "Lee(2024) 크로스섹션 주장을 BTC/ETH 2자산 시계열 꼬리로 변환 — 양방향 중 하나만 우연히 유의할 수 있음(사전등록 §0). vol_gate(비율 진입차단기)와 메커니즘 상이.",
           "pairs_funding": [], "pairs_c1": []}
    for sym in SYMBOLS:
        k = klines[sym]
        kot = k["open_time"].to_numpy(dtype=np.int64)
        f_files = sorted(glob.glob(f"{BASE}/funding/{sym}-fundingRate-*.csv"))
        f = pd.concat([pd.read_csv(x) for x in f_files], ignore_index=True)
        f = f.drop_duplicates("calc_time").sort_values("calc_time").reset_index(drop=True)
        r = f["last_funding_rate"].to_numpy(dtype=float)
        fz = np.full(len(r), np.nan)
        N90 = 90
        for i in range(N90, len(r)):
            w = r[i - N90:i]; s = w.std(ddof=1)
            if s > 0: fz[i] = (r[i] - w.mean()) / s
        ft = f["calc_time"].to_numpy(dtype=np.int64)
        idx = np.searchsorted(kot, ft, side="right") - 1
        for L in LOOKBACKS:
            zp = zmaps[sym][L]
            pairs = [(zp[i], fz[j]) for j, i in enumerate(idx)
                     if 0 <= i < len(kot) and np.isfinite(zp[i]) and np.isfinite(fz[j])]
            if len(pairs) >= 100:
                a = np.array([x[0] for x in pairs]); b = np.array([x[1] for x in pairs])
                rho, pv = stats.spearmanr(a, b)
                out["pairs_funding"].append({"symbol": sym, "lookback": L, "n_pairs": len(pairs),
                                             "spearman_rho_vs_funding_z": float(rho), "p": float(pv)})
        # C1 베이시스 모멘텀 z (프리미엄 인덱스 기반) 재계산
        cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
        files = sorted(glob.glob(f"{BASE}/premium/{sym}-15m-*.csv"))
        prem = pd.concat([pd.read_csv(x, header=None, names=cols) for x in files], ignore_index=True)
        prem = prem[pd.to_numeric(prem["open_time"], errors="coerce").notna()]
        prem["close"] = pd.to_numeric(prem["close"]); prem["open_time"] = pd.to_numeric(prem["open_time"]).astype(np.int64)
        prem = prem.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
        pot = prem["open_time"].to_numpy(dtype=np.int64)
        b = prem["close"].to_numpy(dtype=float)
        for L in LOOKBACKS:
            d = np.full(len(b), np.nan); d[16:] = b[16:] - b[:-16]
            ds = pd.Series(d)
            m_ = ds.rolling(960).mean().shift(1).to_numpy(); s_ = ds.rolling(960).std(ddof=1).shift(1).to_numpy()
            zc1 = np.where((~np.isnan(m_)) & (~np.isnan(s_)) & (s_ > 0), (d - m_) / s_, np.nan)
            zp = zmaps[sym][L]
            j = np.searchsorted(pot, kot, side="left")
            ok = (j < len(pot)) & (pot[np.minimum(j, len(pot) - 1)] == kot)
            jj = np.where(ok, j, -1)
            pairs = [(zp[i], zc1[jj[i]]) for i in range(len(kot))
                     if jj[i] >= 0 and np.isfinite(zp[i]) and np.isfinite(zc1[jj[i]])]
            if len(pairs) >= 100:
                a = np.array([x[0] for x in pairs]); bc = np.array([x[1] for x in pairs])
                rho, pv = stats.spearmanr(a, bc)
                out["pairs_c1"].append({"symbol": sym, "rv_L": L, "c1_L": 16, "n_pairs": len(pairs),
                                        "spearman_rho_vs_c1_z": float(rho), "p": float(pv)})
    return out

def write_md(report, quality):
    L = ["# L3 실현변동 꼬리 방향 신호 결과 (사전등록 규격)", "",
         "신호: RV z (L∈{96,288,960,2880}) |z|≥1.2816 → **문헌 방향** (z≥+ 숏 / z≤− 롱, 양측 B80 단일)",
         f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · 통제: 문헌 반대(고RV→롱) + 무작위 100시드", ""]
    for sym in SYMBOLS:
        for si in range(2):
            key = [k for k in report["spans"] if k.startswith(f"{sym}_span{si+1}_")][0]
            qq = report["spans"][key]
            spl = " / ".join(f"L{lv}:{n}" for lv, n in qq["signal_bars_per_L"].items())
            L.append(f"## {key} — kline {qq['kline_bars_in_span']}/{qq['expected_bars']}(결측 {qq['kline_missing']}), 신호봉 {qq['signal_bars_any']} ({spl})")
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
        L.append("## OOS (각 구간 하위 40%, 사전 고정 규칙)")
        pos_segs = [s for s in report["oos"]["segments"] if s["net16"] is not None and s["net16"] > 0]
        for s in report["oos"]["segments"]:
            ns = "n/a" if s["net16"] is None else f"{s['net16']:.2f}bp"
            L.append(f"- {s['symbol']} {s['span']} L{s['lookback']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        L.append(f"- OOS 양수 세그먼트: {len(pos_segs)}/{len(report['oos']['segments'])} (승격 자격 기준 ≥2)")
    fp_ = report.get("family_precheck", {})
    L.append("")
    L.append("## 패밀리 사전검증 (정보성)")
    L.append(f"- {fp_['design_note']}")
    for p in fp_["pairs_funding"]:
        L.append(f"- {p['symbol']} L{p['lookback']}: RV z vs 펀딩 z 스피어만 ρ={p['spearman_rho_vs_funding_z']:.4f} (p={p['p']:.3g}, n={p['n_pairs']})")
    for p in fp_["pairs_c1"]:
        L.append(f"- {p['symbol']} RV L{p['rv_L']}: RV z vs C1(베이시스 모멘텀 L16) z 스피어만 ρ={p['spearman_rho_vs_c1_z']:.4f} (p={p['p']:.3g}, n={p['n_pairs']})")
    with open(f"{OUT}/l3_rv_tail_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
