#!/usr/bin/env python3
# A1 Amihud 비유동성 방향 신호 진단 — PREREG_a1_illiq_2026-10-13.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
# 신호: ILLIQ_t=|r_t|/qv_t, z=(ILLIQ−직전960 ILLIQ평균)/std — 인과(shift1).
#       1차 방향(사전 고정): z≥+thr → 롱(비유동성 프리미엄), z≤−thr → 숏(거울 대칭).
#       반대 방향은 통제로만 기록. 버킷 B80(|z|≥1.2816)/B90(|z|≥1.6449).
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

def illiq_z(close, ot, qv, L):
    """r_t=ln(c_t/c_{t−1}) 연속봉만. ILLIQ=|r|/qv (qv>0만). z=인과 960윈도우 shift1.
    L봉 롤링 ILLIQ 평균 대비 z — L3의 RV z와 동일 구조(평균/std 윈도우 960, 현재 제외)."""
    c = close.to_numpy(dtype=float)
    t_ = ot.to_numpy(dtype=np.int64)
    q = qv.to_numpy(dtype=float)
    r = np.full(len(c), np.nan)
    r[1:] = np.log(c[1:] / c[:-1])
    gap = t_[1:] - t_[:-1]
    r[1:][gap != 900000] = np.nan
    r[0] = np.nan
    illiq = np.where(q > 0, np.abs(r) / np.where(q > 0, q, 1.0), np.nan)
    illiq = np.where(np.isfinite(illiq), illiq, np.nan)
    s = pd.Series(illiq)
    roll = s.rolling(L).mean().to_numpy()          # L봉 평균 ILLIQ (레벨 스무딩)
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
    """1차 방향: z≥+thr → 롱(sgn=+1), z≤−thr → 숏(sgn=−1) — 사전 고정.
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
                if z >= thr: sgn = +1.0      # 1차 방향: 고비유동 → 롱
                elif z <= -thr: sgn = -1.0   # 거울 대칭: 저비유동 → 숏
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
    """Holm 통과 셀: 각 스팬 후반 40% 구간 재측정 — 규격 §5."""
    segs = []
    for sym in SYMBOLS:
        k = klines[sym]
        kot = k["open_time"].to_numpy(dtype=np.int64)
        kop = k["open"].to_numpy(dtype=float)
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_ms_str(e_str))
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
                    if z >= thr: sgn = +1.0
                    elif z <= -thr: sgn = -1.0
                    else: continue
                    xi = i + 1 + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if (np.diff(kot[i + 1:xi + 1]) != 900000).any(): continue
                    rets.append(sgn * (kop[xi] - kop[i + 1]) / kop[i + 1])
                st = cell_stats(rets, h)
                segs.append({"symbol": sym, "span": si + 1, "bucket": b, "lookback": L, "horizon": h,
                             "net16": st["net16"], "n": st["n"], "valid_n": st["valid_n"]})
    return segs

def e_ms_str(s): return s  # 스팬 끝 문자열 그대로 사용

def main():
    report = {"prereg": "PREREG_a1_illiq_2026-10-13.md", "family_size": 96,
              "signal": "ILLIQ z (|r|/qv, L in 96/288/960/2880), B80/B90, PRIMARY-DIRECTION FIXED (z>=+ -> LONG liquidity-premium, z<=- -> SHORT mirror)",
              "spans": {}, "cells": []}
    quality = {}
    klines, zmaps = {}, {}
    for sym in SYMBOLS:
        klines[sym], dup = load_klines(sym)
        quality[f"{sym}_dup_kline"] = dup
        quality[f"{sym}_kline_rows"] = int(len(klines[sym]))
        quality[f"{sym}_kline_first"] = str(pd.to_datetime(klines[sym]["open_time"].iloc[0], unit="ms", utc=True))
        quality[f"{sym}_kline_last"] = str(pd.to_datetime(klines[sym]["open_time"].iloc[-1], unit="ms", utc=True))
        zmaps[sym] = {L: illiq_z(klines[sym]["close"], klines[sym]["open_time"], klines[sym]["qv"], L) for L in LOOKBACKS}
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
        same = [x for x in report["cells"] if x["bucket"] == c["bucket"] and x["lookback"] == c["lookback"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30]
        verdict["holm_pass_cells"].append({"cell": f"{c['symbol']} {c['span']} {c['bucket']} L{c['lookback']} h{c['horizon']}",
                                           "p_holm": c["p_holm"], "net16": c["net16"], "total_bp": c["total_bp"],
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

    with open(f"{OUT}/a1_illiq_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4")
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:5]
    print("top5 net16:", [(c['bucket'], f"L{c['lookback']}h{c['horizon']}", c['symbol'], round(c['net16'], 2)) for c in ranked])
    ranked_o = sorted([c for c in report["cells"] if c["oppose_net16"] is not None], key=lambda x: -x["oppose_net16"])[:5]
    print("top5 반대(고비유동→숏) 순16:", [(c['bucket'], f"L{c['lookback']}h{c['horizon']}", c['symbol'], round(c['oppose_net16'], 2)) for c in ranked_o])

def write_md(report, quality):
    v = report["verdict"]
    lines = [
        "# A1 Amihud 비유동성 방향 신호 — 측정 결과 (2026-10-13)",
        "",
        f"- 규격: PREREG_a1_illiq_2026-10-13.md | 패밀리 96 cell-span (B80/B90 × L{{96,288,960,2880}} × h{{4,16,48}} × 4스팬)",
        f"- 1차 방향(사전 고정): z≥+thr → 롱(비유동성 프리미엄), z≤−thr → 숏. 반대 방향은 통제로만 기록.",
        f"- **PASS = {v['PASS']}** | Holm 통과 셀: {len(v['holm_pass_cells'])}/96",
        "",
        "## 품질",
    ]
    for key, q in quality.items():
        if isinstance(q, dict):
            lines.append(f"- {key}: " + json.dumps(q, ensure_ascii=False)[:300])
        else:
            lines.append(f"- {key}: {q}")
    lines += ["", "## Holm 통과 셀 상세" if v['holm_pass_cells'] else "", "## Holm 통과 셀: 0 — 전원 FAIL"]
    if v["holm_pass_cells"]:
        lines.pop()
        for hcell in v["holm_pass_cells"]:
            lines.append(f"- {hcell['cell']}: p_holm={hcell['p_holm']:.4f} net16={hcell['net16']:.2f}bp total={hcell['total_bp']:.1f}bp pos={hcell['config_pos_of_4']}/4")
    if report.get("oos", {}).get("executed"):
        lines += ["", "## OOS (스팬 후반 40%)"]
        for s in report["oos"]["segments"]:
            lines.append(f"- {s['symbol']} s{s['span']} {s['bucket']} L{s['lookback']}h{s['horizon']}: net16={s['net16']} n={s['n']}")
    lines += ["", "## 최고 셀 (net16 기준 top10)"]
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:10]
    for c in ranked:
        lines.append(f"- {c['symbol']} {c['span']} {c['bucket']} L{c['lookback']}h{c['horizon']}: total={c['total_bp']:.2f}bp net16={c['net16']:.2f}bp valid_n={c['valid_n']:.1f} p={c['p']:.4f} p_holm={c['p_holm']:.4f} p_ctrl={c['p_ctrl']}")
    with open(f"{OUT}/a1_illiq_results.md", "w") as f:
        f.write("\n".join(lines) + "\n")

if __name__ == "__main__":
    main()
