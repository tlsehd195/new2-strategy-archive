#!/usr/bin/env python3
# C7 교차거래소 퍼페-퍼페 스프레드 정상화 진단 — PREREG_c7_cross_venue_spread_2026-10-13.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 일절 미사용(z 윈도우 오염도 차단).
# 1차 방향 = 정상화 단일 레그(저평가 레그 매수): z>=+THR → 바이낸스 롱, z<=-THR → 바이낸스 숏. 추종은 반대 통제로만.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

VENUES = ["bybit", "okx"]
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
END = ms("2026-10-12")
SEGS = {
    "BTCUSDT": {"lock": (ms("2024-03-25"), ms("2024-06-19")),
                "spans": [("START", "2024-03-25"), ("2024-06-19", "END")]},
    "ETHUSDT": {"lock": (ms("2026-04-28"), ms("2026-09-01")),
                "spans": [("START", "2026-04-28"), ("2026-09-01", "END")]},
}
LOOKBACKS = [96, 288, 960, 2880]  # 1일, 3일, 10일, 30일
THR = 1.2816
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
SEEDS = 100
CELLS = [(L, h) for L in LOOKBACKS for h in HORIZONS]  # 12셀/구성

def load_venue(venue, sym):
    df = pd.read_csv(f"{BASE}/{venue}/{sym}-15m.csv")
    for c in ["open_time", "close"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    return df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    return df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

def level_z(p, L):
    """z_t = (S_t − mean(S[t−L..t−1])) / std — 엄격 윈도우(min_periods=L: 결측·잠금봉 1개라도 있으면 NaN)."""
    s = pd.Series(p)
    m = s.rolling(L, min_periods=L).mean().shift(1).to_numpy()
    sd = s.rolling(L, min_periods=L).std(ddof=1).shift(1).to_numpy()
    z = np.where((~np.isnan(m)) & (~np.isnan(sd)) & (sd > 0), (np.asarray(p, dtype=float) - m) / sd, np.nan)
    return z

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, pot, z_by_L, s_ms, e_ms):
    """정상화 단일 레그: z>=+THR → 롱(+1), z<=-THR → 숏(−1). 신호봉 t → t+1 퍼페 시가 진입, t+1+h 시가 청산."""
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    q = {}
    q["expected_bars"] = int((e_ms - s_ms) // 900000)
    q["spread_aligned_in_span"] = int(((pot >= s_ms) & (pot < e_ms)).sum())
    q["kline_in_span"] = int(((kot >= s_ms) & (kot < e_ms)).sum())
    sig_idx = []
    for i in range(len(pot) - 1):
        t = pot[i]
        if not (s_ms <= t < e_ms): continue
        if pot[i + 1] != t + 900000: continue
        if not any(np.isfinite(z_by_L[L][i]) for L in LOOKBACKS): continue
        sig_idx.append(i)
    q["signal_bars_any"] = len(sig_idx)
    trades = {k2: [] for k2 in CELLS}
    dropped = {k2: 0 for k2 in CELLS}
    for i in sig_idx:
        t = pot[i]
        ei = np.searchsorted(kot, t + 900000, side="left")
        if ei >= len(kot) or kot[ei] != t + 900000 or kot[ei] >= e_ms:
            continue
        for L in LOOKBACKS:
            z = z_by_L[L][i]
            if not np.isfinite(z): continue
            if z >= THR: sgn = 1.0   # 상대 베뉴 과대 → 바이낸스 저평가 → 롱 (정상화)
            elif z <= -THR: sgn = -1.0  # 바이낸스 과대 → 숏 (정상화)
            else: continue
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
    rng = np.random.default_rng(20261013)
    rand_means = []
    for _ in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * r).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def main():
    report = {"prereg": "PREREG_c7_cross_venue_spread_2026-10-13.md", "family_size": 96,
              "signal": "cross-venue perp-perp spread level z (L in 96/288/960/2880) |z|>=1.2816, NORMALIZE single-leg (z>=+ -> long Binance, z<=- -> short Binance)",
              "end_note": "측정 종점 2026-10-12 00:00 UTC (Binance 로컬 퍼페 완결봉 기준 — C3와 동일). 잠금 구간 완전 제외+마스킹",
              "configs": {}, "cells": []}
    quality = {}
    kl = {sym: load_klines(sym) for sym in SYMBOLS}
    series = {}  # (venue, sym) -> (ot, S, z_by_L)
    for venue in VENUES:
        for sym in SYMBOLS:
            xv = load_venue(venue, sym)
            bn = kl[sym][["open_time", "close"]].rename(columns={"close": "bn"})
            mg = pd.merge(xv[["open_time", "close"]].rename(columns={"close": "xv"}),
                          bn, on="open_time", how="inner")
            quality[f"{venue}_{sym}_venue_bars"] = int(len(xv))
            quality[f"{venue}_{sym}_aligned_bars"] = int(len(mg))
            quality[f"{venue}_{sym}_aligned_first"] = str(pd.Timestamp(mg["open_time"].iloc[0], unit="ms", tz="UTC"))
            S = np.log(mg["xv"].to_numpy(dtype=float) / mg["bn"].to_numpy(dtype=float))
            ot = mg["open_time"].to_numpy(dtype=np.int64)
            l0, l1 = SEGS[sym]["lock"]
            lock_mask = (ot >= l0) & (ot < l1)
            quality[f"{venue}_{sym}_locked_bars_masked"] = int(lock_mask.sum())
            S[lock_mask] = np.nan
            zm = {L: level_z(S, L) for L in LOOKBACKS}
            quality[f"{venue}_{sym}_z_valid"] = {str(L): int(np.isfinite(zm[L]).sum()) for L in LOOKBACKS}
            series[(venue, sym)] = (ot, S, zm)
            q = S[np.isfinite(S)]
            quality[f"{venue}_{sym}_spread_mean_bp"] = float(q.mean() * 1e4)
            quality[f"{venue}_{sym}_spread_std_bp"] = float(q.std() * 1e4)
            quality[f"{venue}_{sym}_spread_q"] = {k: float(np.quantile(q, v) * 1e4) for k, v in
                                                   [("p01", 0.01), ("p05", 0.05), ("p50", 0.5), ("p95", 0.95), ("p99", 0.99)]}
    for venue in VENUES:
        for sym in SYMBOLS:
            k = kl[sym]
            ot, Sarr, zm = series[(venue, sym)]
            start_ms = int(ot[0])
            for si, (s_str, e_str) in enumerate(SEGS[sym]["spans"]):
                s_ms = start_ms if s_str == "START" else ms(s_str)
                e_ms = END if e_str == "END" else ms(e_str)
                q, trades, dropped = run_span(k, ot, zm, s_ms, e_ms)
                key = f"{venue}_{sym}_span{si+1}"
                report["configs"][key] = dict(q, start=str(pd.Timestamp(s_ms, unit="ms", tz="UTC")),
                                              end=str(pd.Timestamp(e_ms, unit="ms", tz="UTC")))
                quality[key] = q
                for L, h in CELLS:
                    st = cell_stats([x[0] for x in trades[(L, h)]], h)
                    st.update({"venue": venue, "symbol": sym, "span": f"span{si+1}",
                               "lookback": L, "horizon": h, "dropped_gap": dropped[(L, h)]})
                    report["cells"].append(st)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        same = [x for x in report["cells"] if x["venue"] == c["venue"]
                and x["lookback"] == c["lookback"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30]
        verdict["holm_pass_cells"].append({"cell": f"{c['venue']} {c['symbol']} {c['span']} L{c['lookback']} h{c['horizon']}",
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
        oos["segments"] = oos_eval(kl, series, passed)
        report["oos"] = oos

    fam = family_precheck(series)
    report["family_precheck"] = fam

    with open(f"{OUT}/c7_cross_venue_spread_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4")
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:5]
    print("top5 net16:", [(f"{c['venue']} L{c['lookback']}h{c['horizon']}", c['symbol'], c['span'], round(c['net16'], 2)) for c in ranked])

def oos_eval(kl, series, passed_cells):
    segs = []
    combos = sorted({(c["venue"], c["lookback"], c["horizon"]) for c in passed_cells})
    for venue in VENUES:
        for sym in SYMBOLS:
            k = kl[sym]
            ot, _, zm_all = series[(venue, sym)]
            s0 = int(ot[0])
            kot = k["open_time"].to_numpy(dtype=np.int64)
            kop = k["open"].to_numpy(dtype=float)
            for si, (s_str, e_str) in enumerate(SEGS[sym]["spans"]):
                s_ms = s0 if s_str == "START" else ms(s_str)
                e_ms = END if e_str == "END" else ms(e_str)
                cut = s_ms + int((e_ms - s_ms) * 0.6)
                for vv, L, h in combos:
                    if vv != venue: continue
                    rets = []
                    for i in range(len(ot) - 1):
                        t = ot[i]
                        if not (cut <= t < e_ms): continue
                        if ot[i + 1] != t + 900000: continue
                        z = zm_all[L][i]
                        if not np.isfinite(z): continue
                        if z >= THR: sgn = 1.0
                        elif z <= -THR: sgn = -1.0
                        else: continue
                        ei = np.searchsorted(kot, t + 900000, side="left")
                        if ei >= len(kot) or kot[ei] != t + 900000: continue
                        xi = ei + h
                        if xi >= len(kot) or kot[xi] >= e_ms: continue
                        if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                        rets.append(sgn * (kop[xi] - kop[ei]) / kop[ei])
                    n = len(rets)
                    net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                    segs.append({"venue": venue, "symbol": sym, "span": f"span{si+1}",
                                 "lookback": L, "horizon": h, "n": n, "net16": net16})
    return segs

def family_precheck(series):
    """정보성: C7 z vs C1 베이시스 모멘텀 z / vs P1 펀딩 z / vs C3 코인베이스 프리미엄 z 스피어만."""
    out = {"note": "C7=교차 베뉴 퍼페-퍼페 스프레드 수준 z. C1=바이낸스 스팟-퍼페 베이시스 변화 z. C3=Coinbase 현물 vs Binance 현물 수준 z. 판정 불가입 — 독립성 정량 목적.",
           "pairs": []}
    for venue in VENUES:
        for sym in SYMBOLS:
            ot7, _, zm7 = series[(venue, sym)]
            # C1 z (Δ basis momentum, NWIN=960 — c3 family_precheck와 동일 공식)
            pf = sorted(glob.glob(f"{BASE}/premium/{sym}-15m-*.csv"))
            cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
            prem = pd.concat([pd.read_csv(f, header=None, names=cols) for f in pf], ignore_index=True)
            prem = prem[pd.to_numeric(prem["open_time"], errors="coerce").notna()]
            prem["open_time"] = pd.to_numeric(prem["open_time"]).astype(np.int64)
            prem = prem.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
            b = prem["close"].to_numpy(dtype=float)
            ot1 = prem["open_time"].to_numpy(dtype=np.int64)
            for L in [96, 288]:
                d = np.full(len(b), np.nan); d[L:] = b[L:] - b[:-L]
                ds = pd.Series(d)
                m = ds.rolling(960).mean().shift(1).to_numpy()
                s = ds.rolling(960).std(ddof=1).shift(1).to_numpy()
                z1 = np.where((~np.isnan(m)) & (~np.isnan(s)) & (s > 0), (d - m) / s, np.nan)
                idx = np.searchsorted(ot1, ot7, side="left")
                ok = (idx < len(ot1)) & (ot1[np.minimum(idx, len(ot1) - 1)] == ot7)
                pairs = [(zm7[L][i], z1[idx[i]]) for i in range(len(ot7))
                         if ok[i] and np.isfinite(zm7[L][i]) and np.isfinite(z1[min(idx[i], len(z1) - 1)])]
                if len(pairs) >= 100:
                    a = np.array([x[0] for x in pairs]); bb = np.array([x[1] for x in pairs])
                    rho, pv = stats.spearmanr(a, bb)
                    out["pairs"].append({"vs": "C1_basis_mom", "venue": venue, "symbol": sym, "L": L,
                                         "n_pairs": len(pairs), "spearman_rho": float(rho), "p": float(pv)})
            # 펀딩 z
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
            idx = np.searchsorted(ot7, ft, side="right") - 1
            for L in [96, 288]:
                pairs = [(zm7[L][i], fz[j]) for j, i in enumerate(idx)
                         if 0 <= i < len(ot7) and np.isfinite(zm7[L][i]) and np.isfinite(fz[j])]
                if len(pairs) >= 100:
                    a = np.array([x[0] for x in pairs]); bb = np.array([x[1] for x in pairs])
                    rho, pv = stats.spearmanr(a, bb)
                    out["pairs"].append({"vs": "P1_funding", "venue": venue, "symbol": sym, "L": L,
                                         "n_pairs": len(pairs), "spearman_rho": float(rho), "p": float(pv)})
            # C3 z (Coinbase 프리미엄 수준 z 재계산)
            prod = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD"}[sym]
            cb = pd.read_csv(f"{BASE}/coinbase/{prod}-15m.csv")
            cb["open_time"] = cb["open_time"].astype(np.int64) * 1000
            cols2 = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
            spf = sorted(glob.glob(f"{BASE}/spot15m/{sym}-15m-*.csv"))
            sp = pd.concat([pd.read_csv(x, header=None, names=cols2) for x in spf], ignore_index=True)
            sp = sp[pd.to_numeric(sp["open_time"], errors="coerce").notna()]
            sp["open_time"] = pd.to_numeric(sp["open_time"]).astype(np.int64)
            sp = sp.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
            mg3 = pd.merge(cb[["open_time", "close"]].rename(columns={"close": "cb"}),
                           sp[["open_time", "close"]].rename(columns={"close": "bn"}), on="open_time", how="inner")
            p3 = np.log(mg3["cb"].to_numpy(dtype=float) / mg3["bn"].to_numpy(dtype=float))
            ot3 = mg3["open_time"].to_numpy(dtype=np.int64)
            l0, l1 = SEGS[sym]["lock"]
            p3[(ot3 >= l0) & (ot3 < l1)] = np.nan
            for L in [96, 288]:
                z3 = level_z(p3, L)
                idx = np.searchsorted(ot3, ot7, side="left")
                ok = (idx < len(ot3)) & (ot3[np.minimum(idx, len(ot3) - 1)] == ot7)
                pairs = [(zm7[L][i], z3[idx[i]]) for i in range(len(ot7))
                         if ok[i] and np.isfinite(zm7[L][i]) and np.isfinite(z3[min(idx[i], len(z3) - 1)])]
                if len(pairs) >= 100:
                    a = np.array([x[0] for x in pairs]); bb = np.array([x[1] for x in pairs])
                    rho, pv = stats.spearmanr(a, bb)
                    out["pairs"].append({"vs": "C3_coinbase_prem", "venue": venue, "symbol": sym, "L": L,
                                         "n_pairs": len(pairs), "spearman_rho": float(rho), "p": float(pv)})
    return out

def write_md(report, quality):
    L = ["# C7 교차거래소 퍼페-퍼페 스프레드 정상화 결과 (사전등록 규격)", "",
         "신호: 교차 베뉴 퍼페 스프레드 수준 z (L∈{96,288,960,2880}) |z|≥1.2816 → **정상화 단일 레그** (z≥+ 바이낸스 롱 / z≤− 바이낸스 숏, 양측)",
         "스프레드 = ln(상대 베뉴 퍼페 close / Binance 퍼페 close), 봉 시작시각 정확 일치만. 실행 = 바이낸스 퍼페",
         f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · 통제: 추종(부호 반전) + 무작위 100시드",
         f"비고: {report['end_note']}", ""]
    L.append("## 품질 (정렬·잠금 마스킹·스프레드 분포)")
    for venue in VENUES:
        for sym in SYMBOLS:
            k0 = f"{venue}_{sym}"
            L.append(f"- {k0}: venue {quality[f'{k0}_venue_bars']}봉, 정렬 {quality[f'{k0}_aligned_bars']}봉"
                     f"(첫 {quality[f'{k0}_aligned_first'][:16]}), 잠금마스킹 {quality[f'{k0}_locked_bars_masked']}봉")
            qq = quality[f"{k0}_spread_q"]
            L.append(f"  - 스프레드 분포: 평균 {quality[f'{k0}_spread_mean_bp']:.2f}bp, std {quality[f'{k0}_spread_std_bp']:.2f}bp, "
                     f"p01 {qq['p01']:.1f} / p50 {qq['p50']:.1f} / p99 {qq['p99']:.1f}bp")
            L.append(f"  - z 유효봉: {quality[f'{k0}_z_valid']}")
    L.append("")
    for venue in VENUES:
        for sym in SYMBOLS:
            for si in range(2):
                key = f"{venue}_{sym}_span{si+1}"
                qq = report["configs"][key]
                L.append(f"## {key} ({qq['start'][:10]}~{qq['end'][:10]}) — 정렬 스프레드봉 {qq['spread_aligned_in_span']}, 퍼페 kline {qq['kline_in_span']}, 신호봉 {qq['signal_bars_any']}")
                L.append("| L | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 추종순16 | P(랜덤≥) | 드랍 |")
                L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
                for c in report["cells"]:
                    if c["venue"] != venue or c["symbol"] != sym or c["span"] != f"span{si+1}": continue
                    fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                    L.append(f"| {c['lookback']} | {c['horizon']} | {c['n']} | {c['valid_n']:.0f} | {fmt(c['total_bp'])} | "
                             f"{fmt(c['net12'])} | {fmt(c['net16'])} | {fmt(c['net20'])} | {fmt(c['t'])} | {c['p']:.4f} | "
                             f"{c['p_holm']:.4f} | {fmt(c['oppose_net16'])} | {fmt(c['p_ctrl'])} | {c['dropped_gap']} |")
                L.append("")
    L.append("## 판정 (사전 고정 규칙)")
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    L.append(f"- Holm p<0.05 셀: {len(passed)}개")
    for v in report["verdict"]["holm_pass_cells"]:
        L.append(f"  - {v['cell']} — p_holm={v['p_holm']:.4f}, 순16={v['net16']:.2f}bp, 동일베뉴 4구성 중 양수 {v['config_pos_of_4']}개")
    L.append(f"- **PASS = {report['verdict']['PASS']}**")
    if report.get("oos", {}).get("executed"):
        L.append("")
        L.append("## OOS (각 세그먼트 하위 40%, 사전 고정 규칙)")
        pos_segs = [s for s in report["oos"]["segments"] if s["net16"] is not None and s["net16"] > 0]
        for s in report["oos"]["segments"]:
            ns = "n/a" if s["net16"] is None else f"{s['net16']:.2f}bp"
            L.append(f"- {s['venue']} {s['symbol']} {s['span']} L{s['lookback']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        L.append(f"- OOS 양수 세그먼트: {len(pos_segs)}/{len(report['oos']['segments'])} (승격 자격 기준 ≥2)")
    fp_ = report.get("family_precheck", {})
    L.append("")
    L.append("## 패밀리 사전검증 (정보성)")
    L.append(f"- {fp_['note']}")
    for p in fp_["pairs"]:
        L.append(f"- {p['venue']} {p['symbol']} L{p['L']}: C7 z vs {p['vs']} z 스피어만 ρ={p['spearman_rho']:.4f} (p={p['p']:.3g}, n={p['n_pairs']})")
    with open(f"{OUT}/c7_cross_venue_spread_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
