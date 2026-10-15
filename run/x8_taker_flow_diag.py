#!/usr/bin/env python3
# X8 테이커 플로우 불균형(집계 프록시) 진단 — PREREG_x8_taker_flow_2026-10-13.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
# 신호: tbv/vol 집계 비중 R(L)의 인과 z |z|>=thr(B80/B90) → 1차 방향=추종 (z>=+ 롱 / z<=- 숏).
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {  # 잠금 해제 구간 (끝 미포함) — X3/C1과 동일
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
LOOKBACKS = [16, 96]                 # 4h, 24h
THRS = {"B80": 1.2816, "B90": 1.6449}
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
NWIN = 960                           # 직전 960봉 z 윈도우 (스팬 내부에서만 형성 — PREREG §3)
SEEDS = 100
CELLS = [(L, b, h) for L in LOOKBACKS for b in THRS for h in HORIZONS]  # 12셀/구성 → 패밀리 48

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "volume", "tbv", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    dups = int(df.duplicated("open_time").sum())
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df, dups

def flow_z(sub, L):
    """스팬 내부 데이터로만 형성: R = Σtbv/Σvol (직전 L봉), z = (R−mean)/std (직전 960R, 현재 제외)."""
    tbv = sub["tbv"].to_numpy(dtype=float)
    vol = sub["volume"].to_numpy(dtype=float)
    tbs = pd.Series(tbv).rolling(L).sum().to_numpy()
    vs = pd.Series(vol).rolling(L).sum().to_numpy()
    R = np.where(vs > 0, tbs / vs, np.nan)
    Rs = pd.Series(R)
    m = Rs.rolling(NWIN).mean().shift(1).to_numpy()
    s = Rs.rolling(NWIN).std(ddof=1).shift(1).to_numpy()
    z = np.where((~np.isnan(m)) & (~np.isnan(s)) & (s > 0) & np.isfinite(R), (R - m) / s, np.nan)
    return z

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k_sub, z_by_L, s_str, e_str):
    """양측 추종: z>=+thr → 롱, z<=-thr → 숏. 신호봉 t → t+1 시가 진입, t+1+h 시가 청산. fail-closed."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k_sub["open_time"].to_numpy(dtype=np.int64)
    kop = k_sub["open"].to_numpy(dtype=float)
    kqv = k_sub["qv"].to_numpy(dtype=float)
    q = {"kline_bars_in_span": int(len(k_sub)),
         "expected_bars": int((e_ms - s_ms) // 900000),
         "first_signal_ready_idx": {str(L): int(np.argmax(np.isfinite(z_by_L[L]))) if np.isfinite(z_by_L[L]).any() else -1 for L in LOOKBACKS}}
    q["kline_missing"] = q["expected_bars"] - q["kline_bars_in_span"]
    in_mask = (kot >= s_ms) & (kot < e_ms)
    q["klines_contiguous"] = bool(len(kot) == 0 or (np.diff(kot) == 900000).all())
    sig_idx = []
    for i in range(len(kot) - 1):
        if not in_mask[i]: continue
        if kot[i + 1] != kot[i] + 900000: continue
        if not any(np.isfinite(z_by_L[L][i]) for L in LOOKBACKS): continue
        sig_idx.append(i)
    q["signal_bars_any"] = len(sig_idx)
    trades = {c: [] for c in CELLS}
    dropped = {c: 0 for c in CELLS}
    for i in sig_idx:
        t = kot[i]
        ei = i + 1 if kot[i + 1] == t + 900000 else None
        if ei is None or ei >= len(kot) or kot[ei] >= e_ms: continue
        for L in LOOKBACKS:
            z = z_by_L[L][i]
            if not np.isfinite(z): continue
            for btag, thr in THRS.items():
                if z >= thr: sgn = 1.0
                elif z <= -thr: sgn = -1.0
                else: continue
                for h in HORIZONS:
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any():
                        dropped[(L, btag, h)] += 1; continue  # fail-closed
                    raw = (kop[xi] - kop[ei]) / kop[ei]
                    trades[(L, btag, h)].append((sgn * raw, t, kqv[ei], kqv[xi]))
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
    out["oppose_net16"] = float((-r.mean() - COST["c16"]) * 1e4)  # 반대방향 통제(되돌림)
    rng = np.random.default_rng(20261013)
    rand_means = []
    for _ in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * r).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def main():
    report = {"prereg": "PREREG_x8_taker_flow_2026-10-13.md", "family_size": 48,
              "signal": "taker-buy share R(L in {16,96}) causal z, |z|>=thr(B80=1.2816/B90=1.6449), FOLLOW (z>=+ -> long, z<=- -> short)",
              "spans": {}, "cells": []}
    quality = {}
    klines, zmaps = {}, {}
    subs = {}
    for sym in SYMBOLS:
        df, dups = load_klines(sym)
        quality[f"{sym}_dup_kline"] = dups
        quality[f"{sym}_kline_rows_total"] = int(len(df))
        zmaps[sym] = {}
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            sub = df[(df["open_time"] >= s_ms) & (df["open_time"] < e_ms)].reset_index(drop=True)
            subs[(sym, si)] = sub
            zmaps[sym][f"span{si+1}"] = {L: flow_z(sub, L) for L in LOOKBACKS}
            vol_arr = sub["volume"].to_numpy(dtype=float)
            quality[f"{sym}_span{si+1}_bars"] = int(len(sub))
            quality[f"{sym}_span{si+1}_vol0_bars"] = int((vol_arr <= 0).sum())
    for sym in SYMBOLS:
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            sub = subs[(sym, si)]
            z_by_L = zmaps[sym][f"span{si+1}"]
            q, trades, dropped = run_span(sub, z_by_L, s_str, e_str)
            key = f"{sym}_span{si+1}_{s_str}~{e_str}"
            report["spans"][key] = q
            quality[key] = q
            for L, btag, h in CELLS:
                arr = trades[(L, btag, h)]
                st = cell_stats([x[0] for x in arr], h)
                st.update({"symbol": sym, "span": f"{s_str}~{e_str}", "lookback": L, "bucket": btag,
                           "horizon": h, "dropped_gap": dropped[(L, btag, h)]})
                report["cells"].append(st)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        same = [x for x in report["cells"] if x["lookback"] == c["lookback"] and x["bucket"] == c["bucket"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30]
        verdict["holm_pass_cells"].append({"cell": f"{c['symbol']} {c['span']} L{c['lookback']} {c['bucket']} h{c['horizon']}",
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

    fam = family_precheck(zmaps, subs)
    report["family_precheck"] = fam

    with open(f"{OUT}/x8_taker_flow_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4")
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:5]
    print("top5 net16:", [(f"L{c['lookback']}{c['bucket']}h{c['horizon']}", c['symbol'], round(c['net16'], 2), 'n=' + str(c['n'])) for c in ranked])
    ranked2 = sorted([c for c in report["cells"] if c["total_bp"] is not None], key=lambda x: -x["total_bp"])[:5]
    print("top5 total:", [(f"L{c['lookback']}{c['bucket']}h{c['horizon']}", c['symbol'], round(c['total_bp'], 2)) for c in ranked2])

def oos_eval(klines, zmaps, passed_cells):
    """Holm 통과 (L,b,h) 조합만 OOS(각 스팬 하위 40%)에 동일 적용. 세그먼트별 순16 양수 여부."""
    segs = []
    combos = sorted({(c["lookback"], c["bucket"], c["horizon"]) for c in passed_cells})
    for sym in SYMBOLS:
        df, _ = load_klines(sym)
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            sub = df[(df["open_time"] >= s_ms) & (df["open_time"] < e_ms)].reset_index(drop=True)
            z_by_L = zmaps[sym][f"span{si+1}"]
            cut_i = int(len(sub) * 0.6)  # 상위 60% 종료 지점(인덱스) — 하위 40% OOS
            kot = sub["open_time"].to_numpy(dtype=np.int64)
            kop = sub["open"].to_numpy(dtype=float)
            for L, btag, h in combos:
                thr = THRS[btag]
                rets = []
                for i in range(max(cut_i, 1), len(sub) - 1):
                    if kot[i + 1] != kot[i] + 900000: continue
                    z = z_by_L[L][i]
                    if not np.isfinite(z): continue
                    if z >= thr: sgn = 1.0
                    elif z <= -thr: sgn = -1.0
                    else: continue
                    ei = i + 1
                    xi = ei + h
                    if xi >= len(sub): continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                    rets.append(sgn * (kop[xi] - kop[ei]) / kop[ei])
                n = len(rets)
                net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                segs.append({"symbol": sym, "span": f"{s_str}~{e_str}", "lookback": L, "bucket": btag,
                             "horizon": h, "n": n, "net16": net16})
    return segs

def family_precheck(zmaps, subs):
    """정보성: X8 z(L별) vs P1 펀딩 z 스피어만 — 정보원 독립성 정량 기록."""
    out = {"note": "X8(실행 공격자 집계 플로우)은 파생상품 가격 기반 패밀리(프리미엄/베이시스/펀딩)와 정보원이 상이 — 정량 확인",
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
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            sub = subs[(sym, si)]
            pot = sub["open_time"].to_numpy(dtype=np.int64)
            idx = np.searchsorted(pot, ft, side="right") - 1
            for L in LOOKBACKS:
                zp = zmaps[sym][f"span{si+1}"][L]
                pairs = [(zp[i], fz[j]) for j, i in enumerate(idx)
                         if 0 <= i < len(pot) and np.isfinite(zp[i]) and np.isfinite(fz[j])]
                if len(pairs) >= 100:
                    a = np.array([x[0] for x in pairs]); b = np.array([x[1] for x in pairs])
                    rho, pv = stats.spearmanr(a, b)
                    out["pairs"].append({"symbol": sym, "span": si + 1, "lookback": L, "n_pairs": len(pairs),
                                         "spearman_rho_vs_funding_z": float(rho), "p": float(pv)})
    return out

def write_md(report):
    L = ["# X8 테이커 플로우 불균형(집계 프록시) 결과 (사전등록 규격)", "",
         "신호: tbv/vol 집계 비중 R(L∈{16,96}) 인과 z(직전 960봉, 스팬 내부 형성) |z|≥thr(B80=1.2816/B90=1.6449) → **추종** (z≥+ 롱 / z≤− 숏, 양측)",
         f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · 통제: 반대방향(되돌림) + 무작위 100시드", ""]
    for sym in SYMBOLS:
        for si in range(2):
            key = [k for k in report["spans"] if k.startswith(f"{sym}_span{si+1}_")][0]
            qq = report["spans"][key]
            L.append(f"## {key} — kline {qq['kline_bars_in_span']}/{qq['expected_bars']}(결측 {qq['kline_missing']}), 연속성={qq['klines_contiguous']}, 신호봉 {qq['signal_bars_any']}, 최초 z 가용 L16={qq['first_signal_ready_idx']['16']} L96={qq['first_signal_ready_idx']['96']}")
            L.append("| L | B | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 반대순16 | P(랜덤≥) | 드랍 |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                spans_list = SPANS[sym]
                if c["symbol"] != sym or c["span"] != f"{spans_list[si][0]}~{spans_list[si][1]}": continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                L.append(f"| {c['lookback']} | {c['bucket']} | {c['horizon']} | {c['n']} | {c['valid_n']:.0f} | {fmt(c['total_bp'])} | "
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
        L.append("## OOS (각 스팬 하위 40%, 사전 고정 규칙)")
        pos_segs = [s for s in report["oos"]["segments"] if s["net16"] is not None and s["net16"] > 0]
        for s in report["oos"]["segments"]:
            ns = "n/a" if s["net16"] is None else f"{s['net16']:.2f}bp"
            L.append(f"- {s['symbol']} {s['span']} L{s['lookback']} {s['bucket']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        L.append(f"- OOS 양수 세그먼트: {len(pos_segs)}/{len(report['oos']['segments'])} (승격 자격 기준 ≥2)")
    fp_ = report.get("family_precheck", {})
    L.append("")
    L.append("## 패밀리 사전검증 (정보성)")
    L.append(f"- {fp_['note']}")
    for p in fp_["pairs"]:
        L.append(f"- {p['symbol']} span{p['span']} L{p['lookback']}: X8 z vs P1 펀딩 z 스피어만 ρ={p['spearman_rho_vs_funding_z']:.4f} (p={p['p']:.3g}, n={p['n_pairs']})")
    with open(f"{OUT}/x8_taker_flow_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
