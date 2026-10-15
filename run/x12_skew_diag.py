#!/usr/bin/env python3
# X12 롤링 왜도(MAX/로터리 prior) 방향 신호 진단 — PREREG_x12_skewness_2026-10-13.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
# 신호: skew_{L,i} = rolling(L).skew() of 15m log returns, z=(skew−직전960 skew평균)/std — 인과(shift1).
#       1차 방향(사전 고정): z≥+thr → 숏(고왜도/복권형 — Bali et al. 2011 MAX prior),
#                            z≤−thr → 롱(저왜도 — 거울 일관). 반대 방향은 통제로만 기록.
#       버킷 B80(|z|≥1.2816)/B90(|z|≥1.6449).
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
LOOKBACKS = [96, 288, 960, 2880]
BUCKETS = {"B80": 1.2816, "B90": 1.6449}
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
NWIN = 960
SEEDS = 100
CELLS = [(b, L, h) for b in BUCKETS for L in LOOKBACKS for h in HORIZONS]  # 24셀/스팬 → 패밀리 96

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

def skew_z(close, ot, L):
    """r_t=ln(c_t/c_{t−1}) 연속봉만. skew=rolling(L).skew()(unbiased).
    z=인과 960윈도우 shift1 — A1의 ILLIQ z와 동일 구조(평균/std 윈도우 960, 현재 제외)."""
    c = close.to_numpy(dtype=float)
    t_ = ot.to_numpy(dtype=np.int64)
    r = np.full(len(c), np.nan)
    r[1:] = np.log(c[1:] / c[:-1])
    gap = t_[1:] - t_[:-1]
    r[1:][gap != 900000] = np.nan
    r[0] = np.nan
    s = pd.Series(r)
    roll = s.rolling(L).skew().to_numpy()          # L봉 롤링 왜도
    m = pd.Series(roll).rolling(NWIN).mean().shift(1).to_numpy()
    sd = pd.Series(roll).rolling(NWIN).std(ddof=1).shift(1).to_numpy()
    z = np.where((~np.isnan(m)) & (~np.isnan(sd)) & (sd > 0) & np.isfinite(roll), (roll - m) / sd, np.nan)
    return z

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, zmaps, s_str, e_str):
    """1차 방향: z≥+thr → 숏(sgn=−1, MAX prior), z≤−thr → 롱(sgn=+1) — 사전 고정.
    신호봉 t → t+1 시가 진입, t+1+h 시가 청산. 갭/스팬 밖 드랍(fail-closed)."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    q = {}
    q["kline_bars_in_span"] = int(((kot >= s_ms) & (kot < e_ms)).sum())
    q["expected_bars"] = int((e_ms - s_ms) // 900000)
    q["kline_missing"] = q["expected_bars"] - q["kline_bars_in_span"]
    trades = {c: [] for c in CELLS}
    dropped = {c: 0 for c in CELLS}
    sig_count = {f"{b}_L{L}": 0 for b in BUCKETS for L in LOOKBACKS}
    for i in range(len(kot) - 1):
        t = kot[i]
        if not (s_ms <= t < e_ms): continue
        if kot[i + 1] != t + 900000: continue
        ei = i + 1
        if kot[ei] >= e_ms: continue
        for b, thr in BUCKETS.items():
            for L in LOOKBACKS:
                z = zmaps[L][i]
                if not np.isfinite(z): continue
                if z >= thr: sgn = -1.0      # 1차 방향: 고왜도 → 숏 (MAX prior)
                elif z <= -thr: sgn = +1.0   # 저왜도 → 롱 (거울 일관)
                else: continue
                sig_count[f"{b}_L{L}"] += 1
                for h in HORIZONS:
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any():
                        dropped[(b, L, h)] += 1; continue  # fail-closed
                    raw = (kop[xi] - kop[ei]) / kop[ei]
                    trades[(b, L, h)].append((sgn * raw, t))
    q["signal_count"] = sig_count
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
    out["oppose_net16"] = float((-r.mean() - COST["c16"]) * 1e4)  # 반대 방향 통제(승격 불가)
    rng = np.random.default_rng(20261013)
    rand_means = []
    for _ in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * r).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def oos_eval(klines, zmaps, passed):
    """Holm 통과 셀: 각 스팬 후반 40% 구간 재측정 — 규격 §3."""
    segs = []
    for sym in SYMBOLS:
        k = klines[sym]
        kot = k["open_time"].to_numpy(dtype=np.int64)
        kop = k["open"].to_numpy(dtype=float)
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            m_span = (kot >= s_ms) & (kot < e_ms)
            idx = np.where(m_span)[0]
            if len(idx) == 0: continue
            cut = kot[idx[int(len(idx) * 0.6)]]  # 후반 40% 시작
            for c in passed:
                b, L, h = c["bucket"], c["lookback"], c["horizon"]
                thr = BUCKETS[b]
                rets = []
                for i in idx:
                    if kot[i] < cut or i + 1 >= len(kot): continue
                    if kot[i + 1] != kot[i] + 900000 or kot[i + 1] >= e_ms: continue
                    z = zmaps[sym][L][i]
                    if not np.isfinite(z): continue
                    if z >= thr: sgn = -1.0
                    elif z <= -thr: sgn = +1.0
                    else: continue
                    xi = i + 1 + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[i + 1:xi + 1]) != 900000).any(): continue
                    rets.append(sgn * (kop[xi] - kop[i + 1]) / kop[i + 1])
                st = cell_stats(rets, h)
                segs.append({"symbol": sym, "span": si + 1, "bucket": b, "lookback": L, "horizon": h,
                             "net16": st["net16"], "n": st["n"], "valid_n": st["valid_n"]})
    return segs

def main():
    report = {"prereg": "PREREG_x12_skewness_2026-10-13.md", "family_size": 96,
              "signal": "rolling skewness z (L in 96/288/960/2880), B80/B90, PRIMARY-DIRECTION FIXED (z>=+ -> SHORT lottery prior, z<=- -> LONG mirror)",
              "spans": {}, "cells": []}
    quality = {}
    klines, zmaps = {}, {}
    for sym in SYMBOLS:
        klines[sym], dup = load_klines(sym)
        quality[f"{sym}_dup_kline"] = dup
        quality[f"{sym}_kline_rows"] = int(len(klines[sym]))
        quality[f"{sym}_kline_first"] = str(pd.to_datetime(klines[sym]["open_time"].iloc[0], unit="ms", utc=True))
        quality[f"{sym}_kline_last"] = str(pd.to_datetime(klines[sym]["open_time"].iloc[-1], unit="ms", utc=True))
        zmaps[sym] = {L: skew_z(klines[sym]["close"], klines[sym]["open_time"], L) for L in LOOKBACKS}
        quality[f"{sym}_z_valid_rows"] = {str(L): int(np.isfinite(zmaps[sym][L]).sum()) for L in LOOKBACKS}
    for sym in SYMBOLS:
        k = klines[sym]; zm = zmaps[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            q, trades, dropped = run_span(k, zm, s_str, e_str)
            key = f"{sym}_span{si+1}_{s_str}~{e_str}"
            report["spans"][key] = q
            quality[key] = q
            for b, L, h in CELLS:
                st = cell_stats([x[0] for x in trades[(b, L, h)]], h)
                st.update({"symbol": sym, "span": f"{s_str}~{e_str}", "bucket": b,
                           "lookback": L, "horizon": h, "dropped_gap": dropped[(b, L, h)]})
                report["cells"].append(st)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        tag = f"{c['symbol']} {c['span']} {c['bucket']} L{c['lookback']}h{c['horizon']}"
        verdict["holm_pass_cells"].append({**c, "tag": tag})
    # 승격 판정: Holm 통과 + 동일 조합 4구성 ≥3 양수 + valid_n≥30 + OOS ≥2/4
    combo = {}
    for c in passed:
        k2 = (c["bucket"], c["lookback"], c["horizon"])
        combo.setdefault(k2, []).append(c)
    cands = []
    for k2, arr in combo.items():
        pos = sum(1 for x in arr if x["net16"] is not None and x["net16"] > 0)
        vn_ok = all(x["valid_n"] >= 30 for x in arr)
        if pos >= 3 and vn_ok and len(arr) >= 3:
            cands.append({"bucket": k2[0], "lookback": k2[1], "horizon": k2[2],
                          "pos_compositions": pos, "total": len(arr)})
    verdict["promotion_candidates_combo"] = cands
    if cands:
        oos = oos_eval(klines, zmaps, passed)
        verdict["oos_segments"] = oos
        for cnd in cands:
            segs = [s for s in oos if (s["bucket"], s["lookback"], s["horizon"]) ==
                    (cnd["bucket"], cnd["lookback"], cnd["horizon"]) and s["valid_n"] >= 30]
            cnd["oos_pos"] = sum(1 for s in segs if s["net16"] is not None and s["net16"] > 0)
            cnd["oos_total"] = len(segs)
            if cnd["oos_pos"] >= 2:
                verdict["PASS"] = True
    report["verdict"] = verdict
    report["quality"] = quality
    with open(f"{OUT}/x12_skew_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)

    # MD 요약
    L = ["# X12 롤링 왜도(MAX prior) 진단 결과", "",
         "- PREREG: PREREG_x12_skewness_2026-10-13.md (1차 방향 사전 고정: 고왜도→숏/저왜도→롱)",
         "- 패밀리: 96 cell-span Holm (2심볼×2스팬×B{80,90}×L{96,288,960,2880}×h{4,16,48})", ""]
    for sym in SYMBOLS:
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            L.append(f"## {sym} span{si+1} ({s_str}~{e_str})")
            L.append("| 버킷 | L | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | p_holm | 반대순16 | 드랍 |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                if c["symbol"] != sym or c["span"] != f"{s_str}~{e_str}": continue
                fmt = lambda v: "n/a" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))
                L.append(f"| {c['bucket']} | {c['lookback']} | {c['horizon']} | {c['n']} | {c['valid_n']:.1f} | "
                         f"{fmt(c.get('total_bp'))} | {fmt(c.get('net12'))} | {fmt(c.get('net16'))} | {fmt(c.get('net20'))} | "
                         f"{fmt(c.get('t'))} | {c['p']:.3f} | {c['p_holm']:.3f} | {fmt(c.get('oppose_net16'))} | {c['dropped_gap']} |")
            L.append("")
    L.append("## 결론")
    L.append(f"- Holm 통과 셀: **{len(passed)}/96**")
    for c in passed:
        L.append(f"  - {c['symbol']} {c['span']} {c['bucket']} L{c['lookback']}h{c['horizon']}: "
                 f"net16 {c['net16']:.2f}bp (총bp {c['total_bp']:.2f}, p_holm {c['p_holm']:.4f}, 유효n {c['valid_n']:.1f})")
    L.append(f"- 승격 후보 조합: **{len(cands)}개** — 종합 PASS: **{verdict['PASS']}**")
    if cands:
        for cnd in cands:
            L.append(f"  - {cnd['bucket']} L{cnd['lookback']}h{cnd['horizon']}: {cnd['pos_compositions']}/{cnd['total']} 양수, OOS {cnd['oos_pos']}/{cnd['oos_total']}")
    with open(f"{OUT}/x12_skew_results.md", "w") as f:
        f.write("\n".join(L) + "\n")
    print("holm passed:", len(passed), "/96")
    print("promotion candidates:", len(cands), "PASS:", verdict["PASS"])
    pos16 = [c for c in report["cells"] if c["net16"] is not None and c["net16"] > 0 and c["valid_n"] >= 30]
    print("net16>0 & valid_n>=30 cells:", len(pos16), "/96")
    top = sorted([c for c in report["cells"] if c["n"] > 0], key=lambda c: -(c["total_bp"] or 0))[:5]
    for c in top:
        print(f"top: {c['symbol']} {c['span'][:19]} {c['bucket']} L{c['lookback']}h{c['horizon']} 총bp {c['total_bp']:.2f} 순16 {c['net16']:.2f} 유효n {c['valid_n']:.1f} p {c['p']:.3f}")

if __name__ == "__main__":
    main()
