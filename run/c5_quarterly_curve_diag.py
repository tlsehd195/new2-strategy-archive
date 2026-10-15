#!/usr/bin/env python3
# C5 분기선물 커브 슬로프 진단 — PREREG_c5_quarterly_curve_2026-10-12.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
#
# 결측 처리 원칙 (C1 '직전 N개 관측' 관행과 동일 의미로 구현):
# - slope가 특정 시각에 결측(near/far/spot 중 하나라도 1h close 없음) → 해당 시각 Δ 없음(신호 없음)
# - z 윈도우 = 직전 720개 '관측된' Δ (결측 Δ는 관측에서 제외, 현재 Δ 제외 shift 1)
# - 실행 구간 15m 갭 → 드랍(fail-closed)
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {  # 잠금 해제 구간 (끝 미포함) — X3·C1·C4와 동일
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
LOOKBACKS = [24, 120, 480, 960]   # 1d, 5d, 20d, 40d (1h 격자)
THR = 1.2816                      # B80 양측 추종
HORIZONS = [4, 16, 48]            # 15m봉 (1h, 4h, 12h)
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
NWIN = 720     # 직전 720개 관측 Δ (인과)
SEEDS = 100
CELLS = [(L, h) for L in LOOKBACKS for h in HORIZONS]  # 12셀/구성 → 패밀리 48
HOUR = 3600000
NEAR_BUFFER = 7 * 24 * HOUR  # 납품 7일 전 계약은 near에서 제외 (사전 고정)

def load_1h(path_glob):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(path_glob))
    dfs = []
    for f in files:
        df = pd.read_csv(f, header=None, names=cols)
        dfs.append(df)
    df = pd.concat(dfs, ignore_index=True)
    for c in ["open_time", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df[["open_time", "close"]]

def check_contract_files(sym):
    """probe로 확인된 유효 계약과 실제 파일 일치 검증 — 누락 시 near/far 쌍 오염 방지(fail-closed)."""
    probe = json.load(open(f"{BASE}/qfut/probe_results.json"))
    missing, files = [], set()
    for f in glob.glob(f"{BASE}/qfut/{sym}_*-1h.csv"):
        files.add(os.path.basename(f).replace("-1h.csv", ""))
    for name, info in probe.items():
        if not name.startswith(sym + "_") or not info.get("ok"):
            continue
        d = name.split("_")[1]
        exp_ms = ms(f"20{d[:2]}-{d[2:4]}-{d[4:6]}")
        if exp_ms >= ms("2021-02-01") and name not in files:
            missing.append(name)
    if missing:
        raise RuntimeError(f"계약 파일 누락 — near/far 쌍 오염 위험, 측정 중단: {missing}")
    return len(files)

def build_slope(sym):
    """1h 격자에서 slope_t = (far_close − near_close)/spot_close. near = 납품일 ≥ t+7d 최소 계약."""
    n_files = check_contract_files(sym)
    spot = load_1h(f"{BASE}/spot1h/{sym}-1h.csv")
    contracts = []
    for f in sorted(glob.glob(f"{BASE}/qfut/{sym}_*-1h.csv")):
        name = os.path.basename(f).replace("-1h.csv", "")
        expiry_str = name.split("_")[1]
        exp_ms = ms(f"20{expiry_str[:2]}-{expiry_str[2:4]}-{expiry_str[4:6]}")
        df = load_1h(f)
        contracts.append({"sym": name, "exp": exp_ms, "t": df["open_time"].to_numpy(np.int64),
                          "c": df["close"].to_numpy(float)})
        # 데이터 커버리지 기록
    contracts.sort(key=lambda x: x["exp"])
    exps = np.array([c["exp"] for c in contracts], dtype=np.int64)
    # 격자: 스팟 1h open_time (UTC 정시)
    grid = spot["open_time"].to_numpy(np.int64)
    spot_c = spot["close"].to_numpy(float)
    slope = np.full(len(grid), np.nan)
    near_ids = np.searchsorted(exps, grid + NEAR_BUFFER, side="left")
    quality = {"contracts_used": len(contracts),
               "contract_list": [{"sym": c["sym"], "first": str(pd.Timestamp(c["t"][0], unit="ms", tz="UTC")),
                                   "last": str(pd.Timestamp(c["t"][-1], unit="ms", tz="UTC")),
                                   "bars": int(len(c["t"]))} for c in contracts]}
    for i in range(len(grid)):
        ni = near_ids[i]
        if ni >= len(contracts) or ni + 1 >= len(contracts):
            continue
        near, far = contracts[ni], contracts[ni + 1]
        if grid[i] >= near["exp"] or grid[i] >= far["exp"]:
            continue
        jn = np.searchsorted(near["t"], grid[i])
        if jn >= len(near["t"]) or near["t"][jn] != grid[i]:
            continue
        jf = np.searchsorted(far["t"], grid[i])
        if jf >= len(far["t"]) or far["t"][jf] != grid[i]:
            continue
        if not (np.isfinite(spot_c[i]) and spot_c[i] > 0):
            continue
        slope[i] = (far["c"][jf] - near["c"][jn]) / spot_c[i]
    return grid, slope, quality

def curve_z(grid, slope, L):
    """Δ_t = slope_t − slope_{t−L} (1h 격자, 결측 시 NaN). z = (Δ − mean(직전 720 관측 Δ))/std — 인과."""
    d = np.full(len(grid), np.nan)
    tmap = {t: i for i, t in enumerate(grid.tolist())}
    tt = grid
    for i in range(len(grid)):
        target = tt[i] - L * HOUR
        j = np.searchsorted(tt, target)
        if j < len(tt) and tt[j] == target and np.isfinite(slope[i]) and np.isfinite(slope[j]):
            d[i] = slope[i] - slope[j]
    fin_idx = np.where(np.isfinite(d))[0]
    dv = d[fin_idx]
    rm = pd.Series(dv).rolling(NWIN).mean().shift(1).to_numpy()   # RM[j] = mean(직전 720 관측)
    rs = pd.Series(dv).rolling(NWIN).std(ddof=1).shift(1).to_numpy()
    z = np.full(len(grid), np.nan)
    ok = np.isfinite(rm) & np.isfinite(rs) & (rs > 0)
    z[fin_idx[ok]] = (dv[ok] - rm[ok]) / rs[ok]
    return z, tmap

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

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, grid, z_by_L, s_str, e_str):
    """양측 추종: z≥+THR → 롱, z≤−THR → 숏. 신호 1h봉 t → open_time==t+1h 퍼페 15m봉 시가 진입, +h봉 시가 청산."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    q = {"hours_in_span": int(((grid >= s_ms) & (grid < e_ms)).sum())}
    sig_idx_all = []
    for i in range(len(grid)):
        t = grid[i]
        if not (s_ms <= t < e_ms):
            continue
        if any(np.isfinite(z_by_L[L][i]) for L in LOOKBACKS):
            sig_idx_all.append(i)
    q["signal_hours_any"] = len(sig_idx_all)
    trades = {k2: [] for k2 in CELLS}
    dropped = {k2: 0 for k2 in CELLS}
    for i in sig_idx_all:
        t = int(grid[i])
        ei = np.searchsorted(kot, t + HOUR, side="left")
        if ei >= len(kot) or kot[ei] != t + HOUR or kot[ei] >= e_ms:
            continue
        for L in LOOKBACKS:
            z = z_by_L[L][i]
            if not np.isfinite(z):
                continue
            if z >= THR:
                sgn = 1.0
            elif z <= -THR:
                sgn = -1.0
            else:
                continue
            for h in HORIZONS:
                xi = ei + h
                if xi >= len(kot) or kot[xi] >= e_ms: continue
                if (np.diff(kot[ei:xi + 1]) != 900000).any():
                    dropped[(L, h)] += 1; continue  # fail-closed
                raw = (kop[xi] - kop[ei]) / kop[ei]
                trades[(L, h)].append((sgn * raw, t))
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

def oos_eval(klines, zmaps_by_span, passed_cells):
    segs = []
    combos = sorted({(c["lookback"], c["horizon"]) for c in passed_cells})
    for sym in SYMBOLS:
        k = klines[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            cut = s_ms + int((e_ms - s_ms) * 0.6)
            grid, z_by_L = zmaps_by_span[(sym, si)]["grid"], zmaps_by_span[(sym, si)]["z"]
            kot = k["open_time"].to_numpy(dtype=np.int64)
            kop = k["open"].to_numpy(dtype=float)
            for L, h in combos:
                rets = []
                for i in range(len(grid)):
                    t = int(grid[i])
                    if not (cut <= t < e_ms): continue
                    z = z_by_L[L][i]
                    if not np.isfinite(z): continue
                    if z >= THR: sgn = 1.0
                    elif z <= -THR: sgn = -1.0
                    else: continue
                    ei = np.searchsorted(kot, t + HOUR, side="left")
                    if ei >= len(kot) or kot[ei] != t + HOUR: continue
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                    rets.append(sgn * (kop[xi] - kop[ei]) / kop[ei])
                n = len(rets)
                net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                segs.append({"symbol": sym, "span": f"{s_str}~{e_str}", "lookback": L, "horizon": h,
                             "n": n, "net16": net16})
    return segs

def family_precheck(slopes_by_span):
    """정보성: C5 Δz(L별) vs P1 펀딩 z 스피어만 — 커브가 펀딩 기대 프록시인지 확인."""
    out = {"note": "커브 슬로프(원월−근월)는 이론적으로 미래 펀딩 기대와 연동 가능 — |ρ|≥0.5면 펀딩 변형으로 기록(판정 규칙 변경 없음)",
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
        for si in range(2):
            grid, zm = slopes_by_span[(sym, si)]["grid"], slopes_by_span[(sym, si)]["z"]
            s_ms, e_ms = ms(SPANS[sym][si][0]), ms(SPANS[sym][si][1])
            idx = np.searchsorted(grid, ft, side="right") - 1
            for L in LOOKBACKS:
                zp = zm[L]
                pairs = [(zp[i], fz[j]) for j, i in enumerate(idx)
                         if 0 <= i < len(grid) and s_ms <= ft[j] < e_ms
                         and np.isfinite(zp[i]) and np.isfinite(fz[j])]
                if len(pairs) >= 100:
                    a = np.array([x[0] for x in pairs]); b = np.array([x[1] for x in pairs])
                    rho, pv = stats.spearmanr(a, b)
                    out["pairs"].append({"symbol": sym, "span": si + 1, "lookback": L, "n_pairs": len(pairs),
                                         "spearman_rho_vs_funding_z": float(rho), "p": float(pv)})
    return out

def write_md(report, quality):
    L = ["# C5 분기선물 커브 슬로프 결과 (사전등록 규격)", "",
         "신호: 커브 슬로프 Δ z (L∈{24,120,480,960}h) |z|≥1.2816 → **추종** (z≥+ 롱 / z≤− 숏, 양측)",
         f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · 통제: 반대방향 + 무작위 100시드", ""]
    for sym in SYMBOLS:
        for si in range(2):
            key = [k for k in report["spans"] if k.startswith(f"{sym}_span{si+1}_")][0]
            qq = report["spans"][key]
            L.append(f"## {key} — 슬로프 유효 시간 {qq['slope_valid_hours']}/{qq['hours_in_span']}, 신호 시간(any L) {qq['signal_hours_any']}")
            if qq["slope_valid_hours"] == 0:
                L.append("  - 구조적 데이터 한계: near(납품≥t+7d)+far 동시 상장 필요 → Binance 분기선물이 상시 2계약 유지를 시작한 2023-09-29 이전 구간은 커브 부재(fail-closed, 결측 보간 금지 — 사전등록 §8)")
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
    L.append(f"- {fp_['note']}")
    for p in fp_["pairs"]:
        L.append(f"- {p['symbol']} span{p['span']} L{p['lookback']}: C5 Δz vs P1 펀딩 z 스피어만 ρ={p['spearman_rho_vs_funding_z']:.4f} (p={p['p']:.3g}, n={p['n_pairs']})")
    with open(f"{OUT}/c5_quarterly_curve_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

def main():
    report = {"prereg": "PREREG_c5_quarterly_curve_2026-10-12.md", "family_size": 48,
              "signal": "curve slope z (near-vs-far calendar spread, L in 24/120/480/960 h) |z|>=1.2816, FOLLOW",
              "spans": {}, "cells": []}
    quality = {}
    klines, slopes = {}, {}
    zmaps_by_span = {}
    for sym in SYMBOLS:
        klines[sym] = load_klines(sym)
        grid, slope, q = build_slope(sym)
        quality[f"{sym}_slope"] = q
        valid_hours = int(np.isfinite(slope).sum())
        quality[f"{sym}_slope_valid_total"] = valid_hours
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            m = (grid >= s_ms) & (grid < e_ms)
            z_by_L = {}
            for Lk in LOOKBACKS:
                z, _ = curve_z(grid, slope, Lk)
                z_by_L[Lk] = z
            zmaps_by_span[(sym, si)] = {"grid": grid, "z": z_by_L}
            slopes[(sym, si)] = {"grid": grid, "z": z_by_L}
            quality[f"{sym}_span{si+1}_slope_valid"] = int(np.isfinite(slope[m]).sum())
    for sym in SYMBOLS:
        k = klines[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            z_by_L = zmaps_by_span[(sym, si)]["z"]
            grid = zmaps_by_span[(sym, si)]["grid"]
            m = (grid >= ms(s_str)) & (grid < ms(e_str))
            q, trades, dropped = run_span(k, grid, z_by_L, s_str, e_str)
            q["slope_valid_hours"] = int((np.isfinite(slopes[(sym, si)]["z"][LOOKBACKS[0]]) & m).sum())
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
        oos["segments"] = oos_eval(klines, zmaps_by_span, passed)
        report["oos"] = oos

    fam = family_precheck(slopes)
    report["family_precheck"] = fam

    with open(f"{OUT}/c5_quarterly_curve_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4")
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:5]
    print("top5 net16:", [(f"L{c['lookback']}h{c['horizon']}", c['symbol'], round(c['net16'], 2)) for c in ranked])
    ranked_rev = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: x["net16"])[:5]
    print("bottom5 net16:", [(f"L{c['lookback']}h{c['horizon']}", c['symbol'], round(c['net16'], 2)) for c in ranked_rev])

if __name__ == "__main__":
    main()
