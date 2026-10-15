#!/usr/bin/env python3
# Task 34 ALT-P1-R2 진단 — PREREG_alt_funding_extreme_r2_2026-10-14.md 규격 그대로 구현.
# ALT-P1(alt_funding_diag.py) 규약 재용 + R2 변경: 2코인(ZEC/DOGE)·24 cell-span·Holm·
# 승격 일관성 4스팬 중 ≥3(75% 비례 유지)·OOS 스팬 후반 40% 재측정 구현(§6).
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["ZECUSDT", "DOGEUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = [
    ("2021-05-02", "2022-08-17"),
    ("2024-12-10", "2026-10-01"),
]
BUCKETS = {"B80": 1.2816, "B90": 1.6449}
HORIZONS = [16, 48, 96]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020, "c30": 0.0030}
N90 = 90
SEEDS = 100
BAR = 900000

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    dfs = []
    for f in files:
        d = pd.read_csv(f, header=None, names=cols, low_memory=False)
        d = d[pd.to_numeric(d["open_time"], errors="coerce").notna()].copy()
        if len(d):
            dfs.append(d)
    df = pd.concat(dfs, ignore_index=True)
    for c in ["open_time", "open", "high", "low", "close", "qv"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open_time", "open", "close", "qv"])
    df["open_time"] = df["open_time"].astype(np.int64)
    if df["open_time"].max() > 10**14:
        df["open_time"] = df["open_time"] // 1000
    dups = int(df.duplicated("open_time").sum())
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df, dups

def load_funding(sym):
    files = sorted(glob.glob(f"{BASE}/funding/{sym}-fundingRate-*.csv"))
    dfs = []
    for f in files:
        d = pd.read_csv(f)
        if len(d):
            dfs.append(d)
    df = pd.concat(dfs, ignore_index=True)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = "calc_time" if "calc_time" in df.columns else df.columns[0]
    rcol = "last_funding_rate" if "last_funding_rate" in df.columns else df.columns[-1]
    df = df[pd.to_numeric(df[tcol], errors="coerce").notna()].copy()
    df[tcol] = pd.to_numeric(df[tcol]).astype(np.int64)
    if df[tcol].max() > 10**14:
        df[tcol] = df[tcol] // 1000
    df[rcol] = pd.to_numeric(df[rcol], errors="coerce")
    dups = int(df.duplicated(tcol).sum())
    df = df.drop_duplicates(tcol).sort_values(tcol).reset_index(drop=True)
    return df[[tcol, rcol]].rename(columns={tcol: "calc_time", rcol: "rate"}), dups

def funding_z(rates, nwin=N90):
    r = rates.to_numpy(dtype=float)
    z = np.full(len(r), np.nan)
    for i in range(nwin, len(r)):
        w = r[i - nwin:i]
        s = w.std(ddof=1)
        if s > 0:
            z[i] = (r[i] - w.mean()) / s
    return z

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, f, zs, s_str, e_str):
    s_ms, e_ms = ms(s_str), ms(e_str)
    ot = k["open_time"].to_numpy(dtype=np.int64)
    op = k["open"].to_numpy(dtype=float)
    in_span = (ot >= s_ms) & (ot < e_ms)
    q = {"bars_in_span": int(in_span.sum()), "expected_bars": int((e_ms - s_ms) // BAR)}
    q["missing_bars"] = q["expected_bars"] - q["bars_in_span"]
    ft = f["calc_time"].to_numpy(dtype=np.int64)
    fr = f["rate"].to_numpy(dtype=float)
    fmask = (ft >= s_ms) & (ft < e_ms)
    q["funding_in_span"] = int(fmask.sum())
    q["expected_funding"] = int((e_ms - s_ms) // 28800000)
    sig_t, sig_z = [], []
    for t, z in zip(ft, zs):
        if s_ms <= t < e_ms and np.isfinite(z):
            sig_t.append(t); sig_z.append(z)
    sig_t = np.array(sig_t); sig_z = np.array(sig_z)
    q["signals_all"] = int(len(sig_t))
    q["triggers"] = {f"{b}|h{h}": 0 for b in BUCKETS for h in HORIZONS}
    trades = {k2: [] for k2 in q["triggers"]}
    dropped = {k2: 0 for k2 in q["triggers"]}
    for T, z in zip(sig_t, sig_z):
        ei = np.searchsorted(ot, T, side="right")
        if ei >= len(ot) or ot[ei] >= e_ms:
            continue
        for b, thr in BUCKETS.items():
            if abs(z) < thr:
                continue
            d = -1.0 if z > 0 else 1.0  # 역발상 고정(PREREG §3)
            for h in HORIZONS:
                xi = ei + h
                if xi >= len(ot) or ot[xi] >= e_ms:
                    continue
                seg = ot[ei:xi + 1]
                if (np.diff(seg) != BAR).any():
                    dropped[f"{b}|h{h}"] += 1
                    continue
                lo, hi = ot[ei], ot[xi]
                sl = ft[(ft > lo) & (ft <= hi)]
                if len(sl):
                    rr = fr[np.searchsorted(ft, sl)]
                    carry = -d * float(rr.sum())
                else:
                    carry = 0.0
                price = d * (op[xi] - op[ei]) / op[ei]
                trades[f"{b}|h{h}"].append((price, carry, d, T))
                q["triggers"][f"{b}|h{h}"] += 1
    return q, trades, dropped

def cell_stats(trades, h, key):
    n = len(trades)
    out = {"n": n, "valid_n": n / h}
    base = {"total_bp": None, "net12": None, "net16": None, "net20": None, "net30": None,
            "t": None, "p": 1.0, "p_ctrl": None, "price_bp": None, "carry_bp": None,
            "follow_net16": None}
    if n == 0:
        out.update(base); return out
    price = np.array([x[0] for x in trades]); carry = np.array([x[1] for x in trades])
    darr = np.array([x[2] for x in trades])
    R = price + carry
    vn = n / h
    out["total_bp"] = float(R.mean() * 1e4)
    out["price_bp"] = float(price.mean() * 1e4)
    out["carry_bp"] = float(carry.mean() * 1e4)
    for ck, cv in COST.items():
        out[f"net{ck[1:]}"] = float((R.mean() - cv) * 1e4)
    sd = R.std(ddof=1)
    t = (R.mean() - COST["c16"]) / (sd / np.sqrt(vn)) if sd > 0 else 0.0
    out["t"] = float(t)
    out["p"] = float(2 * stats.t.sf(abs(t), df=max(vn - 1, 1)))
    out["follow_net16"] = float((-R.mean() - COST["c16"]) * 1e4)
    rng = np.random.default_rng(20261014)
    K = R * darr
    rand_means = []
    for _ in range(SEEDS):
        dsel = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((dsel * K).mean() - COST["c16"]))
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def episode_merge(trades, h):
    if not trades:
        return {"n": 0}
    ts = sorted(trades, key=lambda x: x[3])
    used_until = -1; R = []
    for price, carry, d, T in ts:
        if T <= used_until:
            continue
        R.append(price + carry)
        used_until = T + h * BAR
    R = np.array(R)
    return {"n": int(len(R)), "net16": float((R.mean() - COST["c16"]) * 1e4) if len(R) else None}

def oos_subspan(s_str, e_str):
    """스팬 후반 40% 구간 계산(§6 — 판정 기준 계산 전 고정: 시간 길이 기준 후반 40%)."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    cut = s_ms + (e_ms - s_ms) * 0.6
    return str(pd.Timestamp(int(cut), unit="ms", tz="UTC")).replace(" ", "T"), e_str

def main():
    report = {"prereg": "PREREG_alt_funding_extreme_r2_2026-10-14.md", "family_size": 24,
              "symbols": SYMBOLS, "spans": {}, "cells": [], "supplementary_episode": [],
              "oos_cells": []}
    quality = {}
    for sym in SYMBOLS:
        k, kd = load_klines(sym)
        f, fd = load_funding(sym)
        quality[f"{sym}_dup_kline"] = kd
        quality[f"{sym}_dup_funding"] = fd
        quality[f"{sym}_kline_rows"] = int(len(k))
        quality[f"{sym}_funding_rows"] = int(len(f))
        quality[f"{sym}_kline_range"] = [str(pd.Timestamp(k["open_time"].min(), unit="ms", tz="UTC")), str(pd.Timestamp(k["open_time"].max(), unit="ms", tz="UTC"))]
        quality[f"{sym}_funding_range"] = [str(pd.Timestamp(f["calc_time"].min(), unit="ms", tz="UTC")), str(pd.Timestamp(f["calc_time"].max(), unit="ms", tz="UTC"))]
        r = f["rate"].to_numpy(dtype=float)
        quality[f"{sym}_funding_dist"] = {
            "median_bp": float(np.nanmedian(r) * 1e4),
            "p01_bp": float(np.nanpercentile(r, 1) * 1e4),
            "p99_bp": float(np.nanpercentile(r, 99) * 1e4),
            "share_abs_ge_10bp": float(np.nanmean(np.abs(r) >= 0.001)),
            "share_abs_ge_30bp": float(np.nanmean(np.abs(r) >= 0.003)),
        }
        zs = funding_z(f["rate"])
        for si, (s_str, e_str) in enumerate(SPANS):
            q, trades, dropped = run_span(k, f, zs, s_str, e_str)
            key = f"{sym}_span{si+1}_{s_str}~{e_str}"
            report["spans"][key] = q
            quality[key] = q
            for b in BUCKETS:
                for h in HORIZONS:
                    kk = f"{b}|h{h}"
                    st = cell_stats(trades[kk], h, kk)
                    st.update({"symbol": sym, "span": f"{s_str}~{e_str}", "bucket": b, "horizon": h,
                               "dropped_gap": dropped[kk]})
                    report["cells"].append(st)
                    em = episode_merge(trades[kk], h)
                    em.update({"symbol": sym, "span": si + 1, "bucket": b, "horizon": h})
                    report["supplementary_episode"].append(em)
        # OOS: Holm 통과 셀에 한해 스팬 후반 40% 재측정(§6) — 전 셀 판정 후 통과 셀만 실행
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05 and c["net16"] is not None]
    # OOS 재측정(통과 셀의 (버킷,h) 조합 × 해당 코인의 양 스팬)
    if passed:
        combos = sorted({(c["symbol"], c["bucket"], c["horizon"]) for c in passed})
        for sym in SYMBOLS:
            k, _ = load_klines(sym)
            f, _ = load_funding(sym)
            zs = funding_z(f["rate"])
            for (csym, b, h) in [c for c in combos if c[0] == sym]:
                for (s_str, e_str) in SPANS:
                    o_s, o_e = oos_subspan(s_str, e_str)
                    _, otrades, _ = run_span(k, f, zs, o_s, o_e)
                    st = cell_stats(otrades[f"{b}|h{h}"], h, f"{b}|h{h}")
                    st.update({"symbol": sym, "bucket": b, "horizon": h,
                               "oos_span": f"{o_s}~{o_e}", "is_cell_net16": [c["net16"] for c in passed if c["symbol"] == sym and c["bucket"] == b and c["horizon"] == h][0]})
                    report["oos_cells"].append(st)
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        same = [x for x in report["cells"] if x["bucket"] == c["bucket"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30]
        oos_same = [x for x in report["oos_cells"] if x["bucket"] == c["bucket"] and x["horizon"] == c["horizon"]]
        oos_pos = [x for x in oos_same if x["net16"] is not None and x["net16"] > 0]
        verdict["holm_pass_cells"].append({
            "cell": f"{c['symbol']} {c['span']} {c['bucket']} h{c['horizon']}",
            "p_holm": c["p_holm"], "net16": c["net16"], "total_bp": c["total_bp"],
            "carry_bp": c["carry_bp"], "price_bp": c["price_bp"],
            "config_pos_of_4": len(pos), "oos_pos_of_4": len(oos_pos)})
    if verdict["holm_pass_cells"]:
        best = max(verdict["holm_pass_cells"], key=lambda x: (x["config_pos_of_4"], x["oos_pos_of_4"]))
        verdict["PASS"] = best["config_pos_of_4"] >= 3 and best["oos_pos_of_4"] >= 3
        verdict["best_config"] = best
    report["verdict"] = verdict
    with open(f"{OUT}/alt_funding_r2_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    lines = ["# ALT-P1-R2 펀딩 극단 확장 2차 결과 — ZEC·DOGE (PREREG_alt_funding_extreme_r2_2026-10-14 규격)", "",
             f"가족: {len(report['cells'])} cell-span(2코인×2스팬×2버킷×3h) · Holm · 캐리 회계 포함 · 통제: 추종+무작위 100시드", ""]
    for sym in SYMBOLS:
        d = quality[f"{sym}_funding_dist"]
        lines.append(f"## {sym} — 펀딩 중앙값 {d['median_bp']:.1f}bp·p01 {d['p01_bp']:.0f}/p99 {d['p99_bp']:.0f}bp·|r|≥10bp {d['share_abs_ge_10bp']*100:.2f}%·≥30bp {d['share_abs_ge_30bp']*100:.2f}%")
        for key in [k for k in report["spans"] if k.startswith(f"{sym}_span")]:
            qq = report["spans"][key]
            lines.append(f"- {key}: 봉 {qq['bars_in_span']}/{qq['expected_bars']}(결측 {qq['missing_bars']}), 정산 {qq['funding_in_span']}/{qq['expected_funding']}, 신호 {qq['signals_all']}")
    lines.append("")
    holm_cells = [c for c in report["cells"] if c["p_holm"] < 0.05]
    lines.append(f"## Holm 통과 {len(holm_cells)}/{len(report['cells'])}")
    for c in sorted(holm_cells, key=lambda x: x["p_holm"])[:20]:
        lines.append(f"- {c['symbol']} {c['span']} {c['bucket']} h{c['horizon']}: total {c['total_bp']:.1f}bp(가격 {c['price_bp']:.1f}+캐리 {c['carry_bp']:.1f}) net16 {c['net16']:.1f}bp p_holm {c['p_holm']:.2e} n={c['n']} vn={c['valid_n']:.0f}")
    if not holm_cells:
        best = sorted([c for c in report["cells"] if c["p"] is not None], key=lambda x: x["p"])[:8]
        lines.append("(Holm 통과 0 — 원시 p 최소 셀:")
        for c in best:
            lines.append(f"- {c['symbol']} {c['span']} {c['bucket']} h{c['horizon']}: total {c['total_bp']:.1f}bp(가격 {c['price_bp']:.1f}+캐리 {c['carry_bp']:.1f}) net16 {c['net16']:.1f}bp p {c['p']:.3f} n={c['n']} vn={c['valid_n']:.0f}")
        lines.append(")")
    lines.append("")
    lines.append(f"**판정: PASS={verdict['PASS']}** (승격 기준: Holm<0.05 + 동일 구성 4스팬 중 ≥3 양수+OOS ≥3/4)")
    pos_cells = [c for c in report["cells"] if c["net16"] is not None and c["net16"] > 0 and c["valid_n"] >= 30]
    lines.append(f"net16>0 & valid_n≥30 셀: {len(pos_cells)}/{len(report['cells'])}")
    vn30 = [c for c in report["cells"] if c["valid_n"] >= 30]
    lines.append(f"valid_n≥30 셀 자체: {len(vn30)}/{len(report['cells'])}")
    contr = [c for c in report["cells"] if c["price_bp"] is not None]
    neg_price = [c for c in contr if c["price_bp"] < 0]
    lines.append(f"역발상 가격 성분 음수 셀: {len(neg_price)}/{len(contr)}")
    with open(f"{OUT}/alt_funding_r2_results.md", "w") as fp:
        fp.write("\n".join(lines))
    print("\n".join(lines[-20:]))

if __name__ == "__main__":
    main()
