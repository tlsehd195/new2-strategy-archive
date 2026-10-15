#!/usr/bin/env python3
# F2/X6 공포·탐욕 극단 역발상 진단 — PREREG_f2x6_fng_extreme_2026-10-12.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
# 신호: 일 D의 15m 신호봉 ← D−1 확정 F&G 값. B90(≤10 롱 / ≥90 숏), B80(≤20 롱 / ≥80 숏) 역발상.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
FG_PATH = "/home/z/my-project/NEW2_run/research/r3_fng_hist.json"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
BUCKETS = {"B90": (10, 90), "B80": (20, 80)}  # (공포 임계, 탐욕 임계) — 역발상 매핑
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
SEEDS = 100
CELLS = [(b, h) for b in BUCKETS for h in HORIZONS]  # 6셀/구성 → 패밀리 24
DAY_MS = 86_400_000

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

def load_fg():
    d = json.load(open(FG_PATH))["data"]
    fg = pd.DataFrame([{"ts": int(x["timestamp"]), "v": int(x["value"])} for x in d])
    fg = fg.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    # ts = 해당 일자 00:00 UTC 스탬프(초) → 일자 키(ms)로
    fg["day_ms"] = (fg["ts"] // 86400) * 86400 * 1000
    fg = fg.drop_duplicates("day_ms")
    return dict(zip(fg["day_ms"].to_numpy(dtype=np.int64), fg["v"].to_numpy(dtype=float)))

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, fg_map, s_str, e_str):
    """일 D 신호봉 ← FG(D−1). 역발상: 공포→롱(sgn=+1), 탐욕→숏(sgn=−1).
    신호봉 t → t+1 시가 진입, t+1+h 시가 청산. 갭/구간 밖 드랍(fail-closed)."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    q = {}
    q["kline_bars_in_span"] = int(((kot >= s_ms) & (kot < e_ms)).sum())
    q["expected_bars"] = int((e_ms - s_ms) // 900000)
    q["kline_missing"] = q["expected_bars"] - q["kline_bars_in_span"]
    # 바별 FG(D−1) 사전 매핑
    day_ms = (kot // DAY_MS) * DAY_MS
    fgv = np.array([fg_map.get(int(d - DAY_MS), np.nan) for d in day_ms], dtype=float)
    trades = {k2: [] for k2 in CELLS}
    dropped = {k2: 0 for k2 in CELLS}
    sig_counts = {b: 0 for b in BUCKETS}
    for i in range(len(kot) - 1):
        t = kot[i]
        if not (s_ms <= t < e_ms): continue
        if kot[i + 1] != t + 900000: continue
        v = fgv[i]
        if not np.isfinite(v): continue
        ei = i + 1
        if kot[ei] >= e_ms: continue
        for b, (lo, hi) in BUCKETS.items():
            if v <= lo: sgn = +1.0       # 극단 공포 → 역발상 롱
            elif v >= hi: sgn = -1.0     # 극단 탐욕 → 역발상 숏
            else: continue
            sig_counts[b] += 1
            for h in HORIZONS:
                xi = ei + h
                if xi >= len(kot) or kot[xi] >= e_ms: continue
                if (np.diff(kot[ei:xi + 1]) != 900000).any():
                    dropped[(b, h)] += 1; continue  # fail-closed
                raw = (kop[xi] - kop[ei]) / kop[ei]
                trades[(b, h)].append((sgn * raw, t))
    q["signal_bars_per_bucket"] = sig_counts
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
    out["oppose_net16"] = float((-r.mean() - COST["c16"]) * 1e4)  # 추종 반대 통제
    rng = np.random.default_rng(20261012)
    rand_means = []
    for _ in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * r).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def main():
    report = {"prereg": "PREREG_f2x6_fng_extreme_2026-10-12.md", "family_size": 24,
              "signal": "FG(D-1) B90 (<=10 long / >=90 short), B80 (<=20 long / >=80 short) — CONTRARIAN",
              "spans": {}, "cells": []}
    quality = {}
    fg_map = load_fg()
    quality["fg_days"] = len(fg_map)
    klines = {sym: load_klines(sym) for sym in SYMBOLS}
    for sym in SYMBOLS:
        k = klines[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            q, trades, dropped = run_span(k, fg_map, s_str, e_str)
            key = f"{sym}_span{si+1}_{s_str}~{e_str}"
            report["spans"][key] = q
            quality[key] = q
            for b, h in CELLS:
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
        oos["segments"] = oos_eval(klines, fg_map, passed)
        report["oos"] = oos

    fam = family_precheck(klines, fg_map)
    report["family_precheck"] = fam

    with open(f"{OUT}/f2x6_fng_extreme_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4")
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:5]
    print("top5 net16:", [(c['bucket'], f"h{c['horizon']}", c['symbol'], round(c['net16'], 2)) for c in ranked])
    ranked_o = sorted([c for c in report["cells"] if c["oppose_net16"] is not None], key=lambda x: -x["oppose_net16"])[:5]
    print("top5 반대(추종) 순16:", [(c['bucket'], f"h{c['horizon']}", c['symbol'], round(c['oppose_net16'], 2)) for c in ranked_o])

def oos_eval(klines, fg_map, passed_cells):
    """Holm 통과 (버킷,h) 조합만 OOS(각 구간 하위 40%)에 동일 적용."""
    segs = []
    combos = sorted({(c["bucket"], c["horizon"]) for c in passed_cells})
    for sym in SYMBOLS:
        k = klines[sym]
        kot = k["open_time"].to_numpy(dtype=np.int64)
        kop = k["open"].to_numpy(dtype=float)
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            cut = s_ms + int((e_ms - s_ms) * 0.6)
            day_ms = (kot // DAY_MS) * DAY_MS
            fgv = np.array([fg_map.get(int(d - DAY_MS), np.nan) for d in day_ms], dtype=float)
            for b, h in combos:
                lo, hi = BUCKETS[b]
                rets = []
                for i in range(len(kot) - 1):
                    t = kot[i]
                    if not (cut <= t < e_ms): continue
                    if kot[i + 1] != t + 900000: continue
                    v = fgv[i]
                    if not np.isfinite(v): continue
                    if v <= lo: sgn = +1.0
                    elif v >= hi: sgn = -1.0
                    else: continue
                    ei = i + 1
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
                    rets.append(sgn * (kop[xi] - kop[ei]) / kop[ei])
                n = len(rets)
                net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                segs.append({"symbol": sym, "span": f"{s_str}~{e_str}", "bucket": b, "horizon": h,
                             "n": n, "net16": net16})
    return segs

def family_precheck(klines, fg_map):
    """정보성: FG(D−1) vs 당일 RV z(L96) 첫봉 값, FG(D−1) vs 당일 펀딩 z 일평균. 스피어만."""
    out = {"design_note": "일간 센티먼트 → 15m 실행 구조. 역발상이 관행 방향, 추종 반대 통제가 자동 검정됨. 탐욕 측 저파워(B90 BTC s1/s2 0~1일)는 사전 고지됨.",
           "pairs_rv": [], "pairs_funding": []}
    DAY = DAY_MS
    for sym in SYMBOLS:
        k = klines[sym]
        kot = k["open_time"].to_numpy(dtype=np.int64)
        # RV z L96 (l3_rv_tail_diag.py와 동일 함수)
        c = k["close"].to_numpy(dtype=float)
        r = np.full(len(c), np.nan)
        r[1:] = np.log(c[1:] / c[:-1])
        r[1:][(kot[1:] - kot[:-1]) != 900000] = np.nan
        r[0] = np.nan
        rv = pd.Series(r).rolling(96).std(ddof=1).to_numpy()
        rvs = pd.Series(rv)
        m = rvs.rolling(960).mean().shift(1).to_numpy()
        s = rvs.rolling(960).std(ddof=1).shift(1).to_numpy()
        rvz = np.where((~np.isnan(m)) & (~np.isnan(s)) & (s > 0), (rv - m) / s, np.nan)
        # 일별 첫 유효 RV z
        day_ms = (kot // DAY) * DAY
        first_rvz = {}
        for i in range(len(kot)):
            d = int(day_ms[i])
            if d not in first_rvz and np.isfinite(rvz[i]):
                first_rvz[d] = rvz[i]
        # 펀딩 z 일평균
        f_files = sorted(glob.glob(f"{BASE}/funding/{sym}-fundingRate-*.csv"))
        f = pd.concat([pd.read_csv(x) for x in f_files], ignore_index=True)
        f = f.drop_duplicates("calc_time").sort_values("calc_time").reset_index(drop=True)
        rr = f["last_funding_rate"].to_numpy(dtype=float)
        fz = np.full(len(rr), np.nan)
        N90 = 90
        for i in range(N90, len(rr)):
            w = rr[i - N90:i]; sd_ = w.std(ddof=1)
            if sd_ > 0: fz[i] = (rr[i] - w.mean()) / sd_
        ft = f["calc_time"].to_numpy(dtype=np.int64)  # ms 단위
        fday_ms = (ft // DAY) * DAY
        fday_mean = {}
        dfz = pd.DataFrame({"d": fday_ms, "z": fz}).dropna()
        fday_mean = dfz.groupby("d")["z"].mean().to_dict()
        # 페어 구성 (구간 내 전 기간 — 정보성)
        for d, v_rv in first_rvz.items():
            fgv = fg_map.get(d - DAY, np.nan)
            if np.isfinite(fgv):
                out["pairs_rv"].append((fgv, v_rv))
            fzv = fday_mean.get(d, np.nan)
            if np.isfinite(fzv):
                out["pairs_funding"].append((fgv, fzv))
    for key, label in (("pairs_rv", "FG vs RVz(L96)"), ("pairs_funding", "FG vs 펀딩z")):
        arr = out[key]
        if len(arr) >= 100:
            a = np.array([x[0] for x in arr]); b = np.array([x[1] for x in arr])
            rho, pv = stats.spearmanr(a, b)
            out[key] = {"label": label, "n_pairs": len(arr), "spearman_rho": float(rho), "p": float(pv)}
        else:
            out[key] = {"label": label, "n_pairs": len(arr), "spearman_rho": None, "p": None}
    return out

def write_md(report, quality):
    L = ["# F2/X6 공포·탐욕 극단 역발상 결과 (사전등록 규격)", "",
         "신호: FG(D−1) — B90(≤10 롱/≥90 숏), B80(≤20 롱/≥80 숏) **역발상**, 일 D 전체 15m 신호봉",
         f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · 통제: 추종 반대 + 무작위 100시드", ""]
    for sym in SYMBOLS:
        for si in range(2):
            key = [k for k in report["spans"] if k.startswith(f"{sym}_span{si+1}_")][0]
            qq = report["spans"][key]
            sb = " / ".join(f"{b}:{n}" for b, n in qq["signal_bars_per_bucket"].items())
            L.append(f"## {key} — kline {qq['kline_bars_in_span']}/{qq['expected_bars']}(결측 {qq['kline_missing']}), 신호봉 {sb}")
            L.append("| 버킷 | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 반대순16 | P(랜덤≥) | 드랍 |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                spans_list = SPANS[sym]
                if c["symbol"] != sym or c["span"] != f"{spans_list[si][0]}~{spans_list[si][1]}": continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                L.append(f"| {c['bucket']} | {c['horizon']} | {c['n']} | {c['valid_n']:.0f} | {fmt(c['total_bp'])} | "
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
            L.append(f"- {s['symbol']} {s['span']} {s['bucket']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        L.append(f"- OOS 양수 세그먼트: {len(pos_segs)}/{len(report['oos']['segments'])} (승격 자격 기준 ≥2)")
    fp_ = report.get("family_precheck", {})
    L.append("")
    L.append("## 패밀리 사전검증 (정보성)")
    L.append(f"- {fp_['design_note']}")
    for key in ("pairs_rv", "pairs_funding"):
        p = fp_[key]
        if p["spearman_rho"] is not None:
            L.append(f"- {p['label']}: 스피어만 ρ={p['spearman_rho']:.4f} (p={p['p']:.3g}, n={p['n_pairs']})")
        else:
            L.append(f"- {p['label']}: 페어 부족(n={p['n_pairs']}) — 미산출")
    with open(f"{OUT}/f2x6_fng_extreme_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
