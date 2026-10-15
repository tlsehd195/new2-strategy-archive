#!/usr/bin/env python3
# X3 베이시스 마진 붕괴 진단 — PREREG_x3_basis_collapse_2026-10-12.md 규격 그대로 구현.
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
SPANS = {  # 잠금 해제 구간 (끝 미포함) — ADR-0088/0089 준용, P1과 동일
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
BUCKETS = {"B80": 1.2816, "B90": 1.6449}   # 하측만 (붕괴)
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
DLAG = 16      # 4h 변화 (16봉)
NWIN = 960     # 직전 10일 (960봉)
SEEDS = 100

def load_premium(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/premium/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "close"]:
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

def basis_delta_z(basis):
    """Δ_t = basis_t − basis_{t−16}; z_t = (Δ_t − mean(직전 960Δ)) / std(직전 960Δ) — 현재 제외(인과).
    pandas rolling(shift 1)로 P1 funding_z와 동일한 '직전 nwin' 정의를 벡터화."""
    b = basis.to_numpy(dtype=float)
    d = np.full(len(b), np.nan)
    d[DLAG:] = b[DLAG:] - b[:-DLAG]
    ds = pd.Series(d)
    m = ds.rolling(NWIN).mean().shift(1).to_numpy()
    s = ds.rolling(NWIN).std(ddof=1).shift(1).to_numpy()
    z = np.where((~np.isnan(m)) & (~np.isnan(s)) & (s > 0), (d - m) / s, np.nan)
    # rolling(960)은 960개 미만이면 NaN — 직전 960 미만 스킵 조건 자동 충족
    return z

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(sym, k, prem, z_prem, s_str, e_str):
    """신호 봉 t(프리미엄 시리즈) → kline에서 t+1 시가 진입, t+1+h 시가 청산. 되돌림 롱 고정."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    pot = prem["open_time"].to_numpy(dtype=np.int64)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    # 품질
    q = {}
    q["kline_bars_in_span"] = int(((kot >= s_ms) & (kot < e_ms)).sum())
    q["expected_bars"] = int((e_ms - s_ms) // 900000)
    q["kline_missing"] = q["expected_bars"] - q["kline_bars_in_span"]
    in_mask = (pot >= s_ms) & (pot < e_ms)
    q["prem_bars_in_span"] = int(in_mask.sum())
    # 신호 봉: 구간 내, z 유효, z ≤ −thr (하측만). 인접 봉 연속(ot+15m에 다음 봉) 필요.
    sig = []
    zs = z_prem
    for i in range(len(pot) - 1):
        t = pot[i]
        if not (s_ms <= t < e_ms): continue
        if not np.isfinite(zs[i]): continue
        if pot[i + 1] != t + 900000: continue  # 신호 봉 직후 봉 없으면 스킵(fail-closed)
        sig.append((i, t, zs[i]))
    q["signal_bars_all"] = len(sig)
    trades = {k2: [] for k2 in [(b, h) for b in BUCKETS for h in HORIZONS]}
    dropped = {k2: 0 for k2 in trades}
    for i, t, z in sig:
        ei = np.searchsorted(kot, t + 900000, side="left")
        if ei >= len(kot) or kot[ei] != t + 900000 or kot[ei] >= e_ms:
            continue
        for b, thr in BUCKETS.items():
            if z > -thr: continue  # z ≤ −thr 만 (하측만)
            for h in HORIZONS:
                xi = ei + h
                if xi >= len(kot) or kot[xi] >= e_ms: continue
                seg = kot[ei:xi + 1]
                if (np.diff(seg) != 900000).any():
                    dropped[(b, h)] += 1; continue  # fail-closed
                ret = (kop[xi] - kop[ei]) / kop[ei]  # 되돌림 롱 고정
                trades[(b, h)].append((ret, t))
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
    out["follow_net16"] = float((-r.mean() - COST["c16"]) * 1e4)  # 추종(숏) 통제
    rng = np.random.default_rng(20261012)
    rand_means = []
    for _ in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * r).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def main():
    report = {"prereg": "PREREG_x3_basis_collapse_2026-10-12.md", "family_size": 24,
              "signal": "basis 4h-change z <= -1.2816/-1.6449 (collapse), reversal LONG", "spans": {}, "cells": []}
    quality = {}
    klines, prems, zmaps = {}, {}, {}
    for sym in SYMBOLS:
        prems[sym], pd_ = load_premium(sym)
        klines[sym] = load_klines(sym)
        quality[f"{sym}_dup_prem"] = pd_
        zmaps[sym] = basis_delta_z(prems[sym]["close"])
        quality[f"{sym}_prem_rows"] = int(len(prems[sym]))
        quality[f"{sym}_z_valid_rows"] = int(np.isfinite(zmaps[sym]).sum())
    for sym in SYMBOLS:
        k = klines[sym]; prem = prems[sym]; zp = zmaps[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            q, trades, dropped = run_span(sym, k, prem, zp, s_str, e_str)
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

    # OOS 규칙 (사전 고정): Holm 통과 셀이 있을 때만 — 각 심볼-구간 시간순 60/40 분할, 동일 설정 적용.
    oos = {"executed": False, "segments": []}
    if passed:
        oos["executed"] = True
        oos["segments"] = oos_eval(klines, prems, zmaps, [c for c in passed])
        report["oos"] = oos

    # 패밀리 상관 사전검증 (정보성)
    fam = family_precheck(prems, zmaps)
    report["family_precheck"] = fam

    with open(f"{OUT}/x3_basis_collapse_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4")

def oos_eval(klines, prems, zmaps, passed_cells):
    """Holm 통과 (bucket,horizon) 조합들만 OOS(각 구간 하위 40%)에 동일 적용. 세그먼트별 순16 양수 여부."""
    segs = []
    combos = sorted({(c["bucket"], c["horizon"]) for c in passed_cells})
    for sym in SYMBOLS:
        k = klines[sym]; prem = prems[sym]; zp = zmaps[sym]
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
                    if not np.isfinite(zp[i]) or zp[i] > -thr: continue
                    if pot[i + 1] != t + 900000: continue
                    ei = np.searchsorted(kot, t + 900000, side="left")
                    if ei >= len(kot) or kot[ei] != t + 900000: continue
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                    rets.append((kop[xi] - kop[ei]) / kop[ei])
                n = len(rets)
                net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                segs.append({"symbol": sym, "span": f"{s_str}~{e_str}", "bucket": b, "horizon": h,
                             "n": n, "net16": net16})
    return segs

def family_precheck(prems, zmaps):
    """정보성: X3 z vs P1 펀딩 z 스피어만 + 극단 동시발생률. C1과의 구조적 반대(100%)는 별도 기록."""
    out = {"c1_structural_note": "X3 이벤트 봉(z<0)에서 C1형 추종 방향은 정의상 숏 → X3(롱)와 100% 반대. 변수 동일(베이시스 4h 변화 z), 방향 매핑+이벤트 제한만 상이.",
           "pairs": []}
    for sym in SYMBOLS:
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
        pot = prems[sym]["open_time"].to_numpy(dtype=np.int64)
        zp = zmaps[sym]
        idx = np.searchsorted(pot, ft, side="right") - 1  # 정산 시각이 속한 봉
        pairs = [(zp[i], fz[j]) for j, i in enumerate(idx)
                 if 0 <= i < len(pot) and np.isfinite(zp[i]) and np.isfinite(fz[j])]
        if len(pairs) >= 100:
            a = np.array([x[0] for x in pairs]); b = np.array([x[1] for x in pairs])
            rho, pv = stats.spearmanr(a, b)
            ev = np.isfinite(a) & (a <= -1.2816)
            co = float(np.mean(np.abs(b[ev]) >= 1.2816)) if ev.sum() >= 10 else None
            out["pairs"].append({"symbol": sym, "n_pairs": len(pairs), "spearman_rho": float(rho),
                                 "p": float(pv), "x3_B80에서_펀딩극단_동시발생": co})
    return out

def write_md(report, quality):
    L = ["# X3 베이시스 마진 붕괴 결과 (사전등록 규격)", "",
         "신호: 베이시스(프리미엄 인덱스) 4h 변화 z ≤ −1.2816/−1.6449 → 되돌림 **롱** (하측만, 일방향)",
         f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · 통제: 추종(숏) + 무작위 100시드", ""]
    for sym in SYMBOLS:
        for si in range(2):
            key = [k for k in report["spans"] if k.startswith(f"{sym}_span{si+1}_")][0]
            qq = report["spans"][key]
            L.append(f"## {key} — kline {qq['kline_bars_in_span']}/{qq['expected_bars']}(결측 {qq['kline_missing']}), 프리미엄봉 {qq['prem_bars_in_span']}, 신호봉 {qq['signal_bars_all']}")
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
        L.append(f"  - {v['cell']} — p_holm={v['p_holm']:.4f}, 순16={v['net16']:.2f}bp, 동일설정 4구간 중 양수 {v['config_pos_of_4']}개")
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
    if fp_.get("pairs"):
        L.append("")
        L.append("## 패밀리 상관 사전검증 (정보성)")
        for p in fp_["pairs"]:
            L.append(f"- {p['symbol']}: X3 z vs P1 펀딩 z 스피어만 ρ={p['spearman_rho']:.4f} (p={p['p']:.3g}, n={p['n_pairs']}), X3 B80 이벤트 중 펀딩 |z|≥1.28 동시발생 {p['x3_B80에서_펀딩극단_동시발생'] if p['x3_B80에서_펀딩극단_동시발생'] is None else round(p['x3_B80에서_펀딩극단_동시발생'], 3)}")
        L.append(f"- {fp_['c1_structural_note']}")
    with open(f"{OUT}/x3_basis_collapse_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
