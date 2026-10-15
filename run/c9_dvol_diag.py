#!/usr/bin/env python3
# C9 Deribit DVOL 변동성 지수 극단 역발상 진단 — PREREG_c9_dvol_2026-10-13.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
# 신호: 일 D의 15m 신호봉(00:15~23:45) ← D−1 확정 DVOL 종가 z. z≥+thr 롱 / z≤−thr 숏 (역발상, Giot 2005).
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run"
DVOL_DIR = f"{BASE}/data/dvol"
FG_PATH = f"{BASE}/research/r3_fng_hist.json"
OUT = f"{BASE}/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
CUR = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}
LOCK = {"BTCUSDT": ("2024-03-25", "2024-06-18"), "ETHUSDT": ("2026-04-28", "2026-08-31")}
END_STR = "2026-10-12"  # 측정 종점(사전 고정) — 이 일자 00:00 UTC 미만
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {
    "BTCUSDT": [("2021-03-25", "2024-03-25"), ("2024-06-19", END_STR)],
    "ETHUSDT": [("2021-03-25", "2026-04-28"), ("2026-09-01", END_STR)],
}
ZLS = [30, 90]
THRS = {"B80": 1.2816, "B90": 1.6449}
HORIZONS = [96, 288, 672]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
SEEDS = 100
CELLS = [(L, b, h) for L in ZLS for b in THRS for h in HORIZONS]  # 12셀/구성 → 패밀리 48
DAY_MS = 86_400_000

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

def load_dvol_z(cur, sym):
    """DVOL 일간 종가 → 인과 z(L∈{30,90}), 잠금 구간 오염 → NaN. 반환: {day_ms: {L: z}}"""
    df = pd.read_csv(f"{DVOL_DIR}/c9_dvol_{cur}_1d.csv").sort_values("ts_ms").reset_index(drop=True)
    dv = df["close"].to_numpy(dtype=float)
    days = df["ts_ms"].to_numpy(dtype=np.int64)
    lk0 = ms(LOCK[sym][0]); lk1 = ms(LOCK[sym][1]) + DAY_MS  # 잠금 일자 전체 [lk0, lk1)
    locked = (days >= lk0) & (days < lk1)
    zmaps = {}
    for L in ZLS:
        z = np.full(len(dv), np.nan)
        for i in range(L, len(dv)):
            if locked[i]:
                continue
            w = dv[i - L:i]
            if np.isnan(w).any() or locked[i - L:i].any():
                continue  # z 윈도우 오염 차단(fail-closed)
            sd = w.std(ddof=1)
            if sd > 0:
                z[i] = (dv[i] - w.mean()) / sd
        zmaps[L] = dict(zip(days[~np.isnan(z)].tolist(), z[~np.isnan(z)].tolist()))
    dvol_desc = {"n_days": int(len(dv)), "mean": float(np.nanmean(dv)),
                 "p01": float(np.nanpercentile(dv, 1)), "p99": float(np.nanpercentile(dv, 99)),
                 "first_day": str(pd.Timestamp(days[0], unit="ms", tz="UTC").date()),
                 "last_day": str(pd.Timestamp(days[-1], unit="ms", tz="UTC").date())}
    return zmaps, dvol_desc

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, zmaps, sym, s_str, e_str):
    """일 D 신호봉(00:15~23:45) ← z(D−1). z≥+thr 롱 / z≤−thr 숏.
    신호봉 t → t+1 시가 진입, t+1+h 시가 청산. 갭/구간 밖 드랍(fail-closed)."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    q = {}
    q["kline_bars_in_span"] = int(((kot >= s_ms) & (kot < e_ms)).sum())
    q["expected_bars"] = int((e_ms - s_ms) // 900000)
    q["kline_missing"] = q["expected_bars"] - q["kline_bars_in_span"]
    day_ms = (kot // DAY_MS) * DAY_MS
    zvals = {}
    for L in ZLS:
        zm = zmaps[L]
        zvals[L] = np.array([zm.get(int(d - DAY_MS), np.nan) for d in day_ms], dtype=float)
    trades = {k2: [] for k2 in CELLS}
    dropped = {k2: 0 for k2 in CELLS}
    sig_bars = {(L, b): 0 for L in ZLS for b in THRS}
    sig_days = {(L, b): 0 for L in ZLS for b in THRS}
    seen_day = {}
    for i in range(len(kot) - 1):
        t = kot[i]
        if not (s_ms <= t < e_ms): continue
        if t % DAY_MS == 0: continue  # 00:00 봉 제외(D−1 종가 확정 경계 — 인과성 보장)
        if kot[i + 1] != t + 900000: continue
        d = int(day_ms[i])
        for L in ZLS:
            v = zvals[L][i]
            if not np.isfinite(v): continue
            for b, thr in THRS.items():
                if v >= thr: sgn = +1.0       # 고변동 극단 → 역발상 롱
                elif v <= -thr: sgn = -1.0    # 저변동 극단 → 역발상 숏
                else: continue
                key = (L, b)
                sig_bars[key] += 1
                if seen_day.get((L, b)) != d:
                    seen_day[(L, b)] = d; sig_days[key] += 1
                ei = i + 1
                if kot[ei] >= e_ms: continue
                for h in HORIZONS:
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any():
                        dropped[(L, b, h)] += 1; continue
                    raw = (kop[xi] - kop[ei]) / kop[ei]
                    trades[(L, b, h)].append((sgn * raw, t))
    q["signal_bars_per_L_bucket"] = {f"L{L}_{b}": n for (L, b), n in sig_bars.items()}
    q["signal_days_per_L_bucket"] = {f"L{L}_{b}": n for (L, b), n in sig_days.items()}
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
    report = {"prereg": "PREREG_c9_dvol_2026-10-13.md", "family_size": 48,
              "signal": "DVOL(D-1) close z(L∈{30,90}) B80(1.2816)/B90(1.6449) — z>=+thr LONG / z<=-thr SHORT (CONTRARIAN, Giot 2005)",
              "endpoint": END_STR, "spans": {}, "cells": []}
    quality = {"dvol": {}, "spans": {}}
    zmaps_all, klines = {}, {}
    for sym in SYMBOLS:
        zmaps_all[sym], quality["dvol"][sym] = load_dvol_z(CUR[sym], sym)
        klines[sym] = load_klines(sym)
    for sym in SYMBOLS:
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            q, trades, dropped = run_span(klines[sym], zmaps_all[sym], sym, s_str, e_str)
            key = f"{sym}_span{si+1}_{s_str}~{e_str}"
            report["spans"][key] = q
            quality["spans"][key] = q
            for (L, b, h) in CELLS:
                st = cell_stats([x[0] for x in trades[(L, b, h)]], h)
                st.update({"symbol": sym, "span": f"{s_str}~{e_str}", "L": L, "bucket": b, "horizon": h,
                           "dropped_gap": dropped[(L, b, h)]})
                report["cells"].append(st)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        same = [x for x in report["cells"] if x["L"] == c["L"] and x["bucket"] == c["bucket"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30]
        verdict["holm_pass_cells"].append({"cell": f"{c['symbol']} {c['span']} L{c['L']} {c['bucket']} h{c['horizon']}",
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
        oos["segments"] = oos_eval(klines, zmaps_all, passed)
        report["oos"] = oos

    fam = family_precheck(klines, zmaps_all)
    report["family_precheck"] = fam

    with open(f"{OUT}/c9_dvol_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4")
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:5]
    print("top5 net16:", [(f"L{c['L']}", c['bucket'], f"h{c['horizon']}", c['symbol'], round(c['net16'], 2)) for c in ranked])
    ranked_o = sorted([c for c in report["cells"] if c["oppose_net16"] is not None], key=lambda x: -x["oppose_net16"])[:5]
    print("top5 반대(추종) 순16:", [(f"L{c['L']}", c['bucket'], f"h{c['horizon']}", c['symbol'], round(c['oppose_net16'], 2)) for c in ranked_o])

def oos_eval(klines, zmaps_all, passed_cells):
    segs = []
    combos = sorted({(c["L"], c["bucket"], c["horizon"]) for c in passed_cells})
    for sym in SYMBOLS:
        k = klines[sym]
        kot = k["open_time"].to_numpy(dtype=np.int64)
        kop = k["open"].to_numpy(dtype=float)
        day_ms = (kot // DAY_MS) * DAY_MS
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            cut = s_ms + int((e_ms - s_ms) * 0.6)
            for (L, b, h) in combos:
                thr = THRS[b]
                zm = zmaps_all[sym][L]
                zt = np.array([zm.get(int(d - DAY_MS), np.nan) for d in day_ms], dtype=float)
                rets = []
                for i in range(len(kot) - 1):
                    t = kot[i]
                    if not (cut <= t < e_ms): continue
                    if t % DAY_MS == 0: continue
                    if kot[i + 1] != t + 900000: continue
                    v = zt[i]
                    if not np.isfinite(v): continue
                    if v >= thr: sgn = +1.0
                    elif v <= -thr: sgn = -1.0
                    else: continue
                    ei = i + 1
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                    rets.append(sgn * (kop[xi] - kop[ei]) / kop[ei])
                n = len(rets)
                net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                segs.append({"symbol": sym, "span": f"{s_str}~{e_str}", "L": L, "bucket": b, "horizon": h,
                             "n": n, "net16": net16})
    return segs

def family_precheck(klines, zmaps_all):
    out = {"design_note": "일간 내재변동성 → 15m 실행 구조. 역발상(Giot 2005)이 1차 방향, 추종 반대 통제가 자동 검정됨. ETH s2 저파워는 사전 고지.",
           "pairs_fg": [], "pairs_rv": [], "pairs_funding": []}
    DAY = DAY_MS
    for sym in SYMBOLS:
        k = klines[sym]
        kot = k["open_time"].to_numpy(dtype=np.int64)
        zm = zmaps_all[sym][30]
        c = k["close"].to_numpy(dtype=float)
        r = np.full(len(c), np.nan)
        r[1:] = np.log(c[1:] / c[:-1])
        r[1:][(kot[1:] - kot[:-1]) != 900000] = np.nan
        rv = pd.Series(r).rolling(96).std(ddof=1).to_numpy()
        rvs = pd.Series(rv)
        m = rvs.rolling(960).mean().shift(1).to_numpy()
        s = rvs.rolling(960).std(ddof=1).shift(1).to_numpy()
        rvz = np.where((~np.isnan(m)) & (~np.isnan(s)) & (s > 0), (rv - m) / s, np.nan)
        day_ms = (kot // DAY) * DAY
        first_rvz = {}
        for i in range(len(kot)):
            d = int(day_ms[i])
            if d not in first_rvz and np.isfinite(rvz[i]):
                first_rvz[d] = rvz[i]
        f_files = sorted(glob.glob(f"{BASE}/data/funding/{sym}-fundingRate-*.csv"))
        f = pd.concat([pd.read_csv(x) for x in f_files], ignore_index=True)
        f = f.drop_duplicates("calc_time").sort_values("calc_time").reset_index(drop=True)
        rr = f["last_funding_rate"].to_numpy(dtype=float)
        fz = np.full(len(rr), np.nan)
        N90 = 90
        for i in range(N90, len(rr)):
            w = rr[i - N90:i]; sd_ = w.std(ddof=1)
            if sd_ > 0: fz[i] = (rr[i] - w.mean()) / sd_
        ft = f["calc_time"].to_numpy(dtype=np.int64)
        fday_ms = (ft // DAY) * DAY
        dfz = pd.DataFrame({"d": fday_ms, "z": fz}).dropna()
        fday_mean = dfz.groupby("d")["z"].mean().to_dict()
        d = json.load(open(FG_PATH))["data"]
        fg = pd.DataFrame([{"ts": int(x["timestamp"]), "v": int(x["value"])} for x in d])
        fg = fg.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
        fg["day_ms"] = (fg["ts"] // 86400) * 86400 * 1000
        fg_map = dict(zip(fg["day_ms"].to_numpy(dtype=np.int64), fg["v"].to_numpy(dtype=float)))
        for day, zv in zm.items():
            nxt = day  # z_day = DVOL(day) 종가 기준 → FG/RV/펀딩도 day 값과 페어(정보성)
            fgv = fg_map.get(nxt, np.nan)
            if np.isfinite(fgv):
                out["pairs_fg"].append((zv, fgv))
            rvv = first_rvz.get(nxt, np.nan)
            if np.isfinite(rvv):
                out["pairs_rv"].append((zv, rvv))
            fzv = fday_mean.get(nxt, np.nan)
            if np.isfinite(fzv):
                out["pairs_funding"].append((zv, fzv))
    for key, label in (("pairs_fg", "DVOLz(L30) vs FG"), ("pairs_rv", "DVOLz(L30) vs RVz(L96)"),
                       ("pairs_funding", "DVOLz(L30) vs 펀딩z")):
        arr = out[key]
        if len(arr) >= 100:
            a = np.array([x[0] for x in arr]); b = np.array([x[1] for x in arr])
            rho, pv = stats.spearmanr(a, b)
            out[key] = {"label": label, "n_pairs": len(arr), "spearman_rho": float(rho), "p": float(pv)}
        else:
            out[key] = {"label": label, "n_pairs": len(arr), "spearman_rho": None, "p": None}
    return out

def write_md(report, quality):
    L = ["# C9 Deribit DVOL 극단 역발상 결과 (사전등록 규격)", "",
         "신호: DVOL(D−1) 일간 종가 z — B80(|z|≥1.2816)/B90(|z|≥1.6449), **z≥+thr 롱 / z≤−thr 숏 (역발상)**, 일 D 00:15~23:45 신호봉",
         f"측정 종점: {report['endpoint']} · 시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · 통제: 추종 반대 + 무작위 100시드", ""]
    dvq = quality["dvol"]
    L.append("## DVOL 데이터 품질")
    for sym, q in dvq.items():
        L.append(f"- {sym}: {q['n_days']}일 ({q['first_day']}~{q['last_day']}), 평균 {q['mean']:.1f}, p01 {q['p01']:.1f}, p99 {q['p99']:.1f}")
    L.append("")
    for sym in SYMBOLS:
        for si in range(2):
            key = [k for k in report["spans"] if k.startswith(f"{sym}_span{si+1}_")][0]
            qq = report["spans"][key]
            sb = " / ".join(f"{k}:{v}" for k, v in list(qq["signal_days_per_L_bucket"].items()))
            L.append(f"## {key} — kline {qq['kline_bars_in_span']}/{qq['expected_bars']}(결측 {qq['kline_missing']}), 신호일 {sb}")
            L.append("| L | 버킷 | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 반대순16 | P(랜덤≥) | 드랍 |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                spans_list = SPANS[sym]
                if c["symbol"] != sym or c["span"] != f"{spans_list[si][0]}~{spans_list[si][1]}": continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                L.append(f"| {c['L']} | {c['bucket']} | {c['horizon']} | {c['n']} | {c['valid_n']:.0f} | {fmt(c['total_bp'])} | "
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
            L.append(f"- {s['symbol']} {s['span']} L{s['L']} {s['bucket']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        L.append(f"- OOS 양수 세그먼트: {len(pos_segs)}/{len(report['oos']['segments'])} (승격 자격 기준 ≥2)")
    fp_ = report.get("family_precheck", {})
    L.append("")
    L.append("## 패밀리 사전검증 (정보성)")
    L.append(f"- {fp_['design_note']}")
    for key in ("pairs_fg", "pairs_rv", "pairs_funding"):
        p = fp_[key]
        if p.get("spearman_rho") is not None:
            L.append(f"- {p['label']}: 스피어만 ρ={p['spearman_rho']:.4f} (p={p['p']:.3g}, n={p['n_pairs']})")
        else:
            L.append(f"- {p['label']}: 페어 부족(n={p['n_pairs']}) — 미산출")
    with open(f"{OUT}/c9_dvol_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
