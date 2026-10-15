#!/usr/bin/env python3
# X4/F3 BTC-ETH 스프레드(로그비율) 평균회귀 1레그 진단 — PREREG_x4f3_pair_spread_2026-10-12.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
LEGS = ["ETHUSDT", "BTCUSDT"]
LOOKBACKS = [96, 288, 960, 2880]
THRS = [1.2816, 1.6449]
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
SEEDS = 100
CELLS = [(l, t, h) for l in LOOKBACKS for t in THRS for h in HORIZONS]  # 24셀/레그-구간 → 패밀리 96
BAR = 900000

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "close", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    return df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

def spread_z(eth, btc):
    """S = ln(ETH close) - ln(BTC close) — 타임스탬프 교집합만, 불일치 기록."""
    e_ot = eth["open_time"].to_numpy(dtype=np.int64)
    b_ot = btc["open_time"].to_numpy(dtype=np.int64)
    common, ea, ba = np.intersect1d(e_ot, b_ot, return_indices=True)
    eth_c = eth["close"].to_numpy(dtype=float)[ea]
    btc_c = btc["close"].to_numpy(dtype=float)[ba]
    S = np.log(eth_c) - np.log(btc_c)
    z_by_L = {}
    for L in LOOKBACKS:
        z = np.full(len(S), np.nan)
        # rolling mean/std — pandas 사용(성능/정확)
        s = pd.Series(S)
        m = s.rolling(L).mean().to_numpy()
        sd = s.rolling(L).std(ddof=1).to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            z = (S - m) / sd
        z_by_L[L] = z
    q = {"common_bars": int(len(common)), "eth_only": int(len(e_ot) - len(common)), "btc_only": int(len(b_ot) - len(common)),
         "spread_nan": int(np.isnan(S).sum())}
    return common, z_by_L, q

def run_span_leg(sym, k, common, z_by_L, s_str, e_str):
    """1레그 평균회귀: z<=-thr → ETH 롱/BTC 숏, z>=+thr → ETH 숏/BTC 롱.
    신호봉 t(common 기준) → t+1 시가 진입(해당 레그 심볼), t+1+h 시가 청산. 연속 신호 허용."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    kqv = k["qv"].to_numpy(dtype=float)
    pos_of = {int(t): i for i, t in enumerate(common)}
    trades = {c: [] for c in CELLS}
    qvs = {c: [] for c in CELLS}
    dropped = {c: {"no_next": 0, "gap": 0, "outside": 0} for c in CELLS}
    for i in range(len(common) - 1):
        t = int(common[i])
        if not (s_ms <= t < e_ms):
            continue
        if common[i + 1] != t + BAR:
            continue  # 신호봉 직후 봉 연속성(진입봉 존재 보장)
        ent_t = t + BAR
        for L in LOOKBACKS:
            z = z_by_L[L][i]
            if not np.isfinite(z):
                continue
            for thr in THRS:
                if z <= -thr:
                    sgn = 1.0 if sym == "ETHUSDT" else -1.0
                elif z >= thr:
                    sgn = -1.0 if sym == "ETHUSDT" else 1.0
                else:
                    continue
                ei = np.searchsorted(kot, ent_t, side="left")
                if ei >= len(kot) or kot[ei] != ent_t or kot[ei] >= e_ms:
                    for h in HORIZONS:
                        dropped[(L, thr, h)]["no_next"] += 1
                    continue
                for h in HORIZONS:
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms:
                        dropped[(L, thr, h)]["outside"] += 1
                        continue
                    if (np.diff(kot[ei:xi + 1]) != BAR).any():
                        dropped[(L, thr, h)]["gap"] += 1
                        continue
                    raw = (kop[xi] - kop[ei]) / kop[ei]
                    trades[(L, thr, h)].append(sgn * raw)
                    qvs[(L, thr, h)].append((kqv[ei], kqv[xi]))
    return trades, qvs, dropped

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
    for kk, c in COST.items():
        out[f"net{kk[1:]}"] = float((r.mean() - c) * 1e4)
    sd = r.std(ddof=1)
    t = (r.mean() - COST["c16"]) / (sd / np.sqrt(vn)) if sd > 0 else 0.0
    out["t"] = float(t)
    out["p"] = float(2 * stats.t.sf(abs(t), df=max(vn - 1, 1)))
    out["oppose_net16"] = float((-r.mean() - COST["c16"]) * 1e4)
    rng = np.random.default_rng(20261012)
    rand_means = []
    for _ in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * np.abs(r)).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def drift_baseline(k, s_str, e_str):
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    out = {}
    for h in HORIZONS:
        rets = []
        i = np.searchsorted(kot, s_ms)
        while i + h < len(kot) and kot[i + h] < e_ms:
            rets.append((kop[i + h] - kop[i]) / kop[i])
            i += 96
        out[h] = {"n": len(rets), "total_bp": float(np.mean(rets) * 1e4) if rets else None}
    return out

def family_precheck(eth, btc, common, z_by_L, fund):
    """정보성: 스프레드 z vs 펀딩 z, vs C1 베이시스 z 스피어만."""
    out = {}
    try:
        import json as _j
        # 펀딩 z: 15m fundingRate 파일 로컬 사용
        def load_fund(sym):
            files = sorted(glob.glob(f"{BASE}/fundingRate/{sym}-15m-*.csv"))
            if not files:
                return None
            cols = ["calc_time", "funding_interval_hours", "last_funding_rate"]
            df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
            df["calc_time"] = pd.to_numeric(df["calc_time"], errors="coerce")
            df = df.dropna(subset=["calc_time"]).drop_duplicates("calc_time").sort_values("calc_time")
            return df
        fz = {}
        for sym in ["BTCUSDT", "ETHUSDT"]:
            f = load_fund(sym)
            if f is None:
                continue
            fr = f["last_funding_rate"].to_numpy(dtype=float)
            frz = (fr - np.nanmean(fr)) / (np.nanstd(fr) + 1e-12)
            fz[sym] = (f["calc_time"].to_numpy(dtype=np.int64), frz)
        # common 봉 위치의 펀딩 z 매핑(마지막 확정 펀딩 — 대략 매핑, 정보성 한계 명시)
        for L in [96, 960]:
            sz = z_by_L[L]
            for sym in ["BTCUSDT", "ETHUSDT"]:
                if sym not in fz:
                    continue
                ot, z = fz[sym]
                idx = np.searchsorted(ot, common, side="right") - 1
                ok = (idx >= 0) & np.isfinite(sz)
                if ok.sum() > 100:
                    rho, p = stats.spearmanr(sz[ok], z[idx[ok]])
                    out[f"sprz_L{L}_vs_fund_{sym}"] = {"rho": float(rho), "p": float(p), "n": int(ok.sum())}
    except Exception as e:
        out["error"] = str(e)
    return out

def write_md(report, quality):
    L = ["# X4/F3 BTC-ETH 스프레드 평균회귀 1레그 결과 (사전등록 규격)", "",
         "신호: S=ln(ETH)−ln(BTC) z(L봉) |z|≥thr → 평균회귀 1레그(ETH: z− 롱/z+ 숏, BTC: z− 숏/z+ 롱)",
         f"시도 수(패밀리): {len(report['cells'])}셀 · Holm 보정 · 통제: 반대(정보성)+무작위 100+구간 드리프트",
         "**§0: 2레그 동시거래 금지(r3 제약) — 각 거래 단일 레그. 연속 신호봉 허용(C1 규약)**", "",
         f"- 데이터 정렬: 공통봉 {quality['common_bars']:,}, ETH 전용 {quality['eth_only']:,}, BTC 전용 {quality['btc_only']:,}, 스프레드 NaN {quality['spread_nan']:,}"]
    L.append("")
    for leg in LEGS:
        for si in range(2):
            key = f"{leg}_span{si+1}"
            L.append(f"## {key}")
            L.append("| L | thr | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 반대순16 | P(랜덤≥) | 드랍(no_next/out/gap) |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                if c["leg"] != leg or c["span_idx"] != si: continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                L.append(f"| {c['L']} | {c['thr']} | {c['horizon']} | {c['n']:,} | {c['valid_n']:.1f} | {fmt(c['total_bp'])} | "
                         f"{fmt(c['net12'])} | {fmt(c['net16'])} | {fmt(c['net20'])} | {fmt(c['t'])} | {c['p']:.4f} | "
                         f"{c['p_holm']:.4f} | {fmt(c['oppose_net16'])} | {fmt(c['p_ctrl'])} | "
                         f"{c['dropped']['no_next']}/{c['dropped']['outside']}/{c['dropped']['gap']} |")
            sb = report["drift_baseline"].get(key, {})
            parts = []
            for h, v in sorted(sb.items()):
                tb = "n/a" if v["total_bp"] is None else f"{v['total_bp']:.2f}"
                parts.append(f"h{h}: n={v['n']}, 총bp={tb}")
            L.append("- 구간 드리프트(96봉 간격 무조건 롱): " + ", ".join(parts))
            L.append("")
    L.append("## 판정 (사전 고정 규칙)")
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    L.append(f"- Holm p<0.05 셀: {len(passed)}개")
    for v in report["verdict"]["holm_pass_cells"]:
        L.append(f"  - {v['cell']} — p_holm={v['p_holm']:.4f}, 순16={v['net16']:.2f}bp, 동일설정 4구성 양수 {v['config_pos_of_4']}/4, 유효n {v['valid_n']:.1f}")
    L.append(f"- **PASS = {report['verdict']['PASS']}**")
    if report.get("oos", {}).get("executed"):
        pos = [s for s in report["oos"]["segments"] if s["net16"] is not None and s["net16"] > 0]
        for s in report["oos"]["segments"]:
            ns = "n/a" if s["net16"] is None else f"{s['net16']:.2f}bp"
            L.append(f"- OOS {s['leg']} {s['span']} L{s['L']} thr{s['thr']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        L.append(f"- OOS 양수 세그먼트: {len(pos)}/{len(report['oos']['segments'])} (기준 ≥2)")
    fp_ = report.get("family_precheck", {})
    if fp_:
        L.append("")
        L.append("## 패밀리 사전검증 (정보성)")
        for k2, v in fp_.items():
            if "rho" in v:
                L.append(f"- {k2}: ρ={v['rho']:.4f} (p={v['p']:.3g}, n={v['n']})")
    with open(f"{OUT}/x4f3_pair_spread_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

def main():
    report = {"prereg": "PREREG_x4f3_pair_spread_2026-10-12.md", "family_size": 96,
              "signal": "S=ln(ETH)-ln(BTC) z |z|>=thr -> mean-reversion 1-leg, entry t+1 open, h in {4,16,48}",
              "spans": {}, "cells": [], "drift_baseline": {}, "family_precheck": {}}
    eth = load_klines("ETHUSDT")
    btc = load_klines("BTCUSDT")
    common, z_by_L, qalign = spread_z(eth, btc)
    quality = dict(qalign)
    klines = {"ETHUSDT": eth, "BTCUSDT": btc}
    for leg in LEGS:
        k = klines[leg]
        for si, (s_str, e_str) in enumerate(SPANS[leg]):
            trades, qvs, dropped = run_span_leg(leg, k, common, z_by_L, s_str, e_str)
            key = f"{leg}_span{si+1}"
            report["spans"][key] = {"signal_bars": int(((common >= ms(s_str)) & (common < ms(e_str))).sum())}
            for (L, thr, h) in CELLS:
                st = cell_stats(trades[(L, thr, h)], h)
                st.update({"leg": leg, "span_idx": si, "L": L, "thr": thr, "horizon": h,
                           "dropped": dropped[(L, thr, h)], "qv": qvs[(L, thr, h)]})
                report["cells"].append(st)
            report["drift_baseline"][key] = drift_baseline(k, s_str, e_str)
    report["family_precheck"] = family_precheck(eth, btc, common, z_by_L, None)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
        c.pop("qv", None)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        same = [x for x in report["cells"] if x["L"] == c["L"] and x["thr"] == c["thr"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30]
        verdict["holm_pass_cells"].append({"cell": f"{c['leg']} s{c['span_idx']+1} L{c['L']} t{c['thr']} h{c['horizon']}",
                                           "p_holm": c["p_holm"], "net16": c["net16"],
                                           "config_pos_of_4": len(pos), "valid_n": c["valid_n"]})
    if verdict["holm_pass_cells"]:
        best = max(verdict["holm_pass_cells"], key=lambda x: x["config_pos_of_4"])
        verdict["PASS"] = best["config_pos_of_4"] >= 3
        verdict["best_config"] = best
    report["verdict"] = verdict

    oos = {"executed": False, "segments": []}
    if passed:
        oos["executed"] = True
        combos = sorted({(c["L"], c["thr"], c["horizon"]) for c in passed})
        for leg in LEGS:
            k = klines[leg]
            for si, (s_str, e_str) in enumerate(SPANS[leg]):
                s_ms, e_ms = ms(s_str), ms(e_str)
                cut = s_ms + int((e_ms - s_ms) * 0.6)
                trades, qvs, dropped = run_span_leg(leg, k, common, z_by_L, s_str, e_str)
                for L, thr, h in combos:
                    # OOS: 신호봉 기준 하위 40% 제외 — run_span_leg는 ts를 보관하지 않으므로 재계산(컷 필터 버전)
                    rets = oos_rets(leg, k, common, z_by_L, s_str, e_str, cut, L, thr, h)
                    n = len(rets)
                    net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                    oos["segments"].append({"leg": leg, "span": f"{s_str}~{e_str}", "L": L, "thr": thr,
                                            "horizon": h, "n": n, "net16": net16})
        report["oos"] = oos

    with open(f"{OUT}/x4f3_pair_spread_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    print("align:", quality)
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:6]
    print("top6 net16:", [(f"{c['leg'][:3]}s{c['span_idx']+1} L{c['L']} t{c['thr']} h{c['horizon']}", round(c['net16'], 2), round(c['valid_n'],1)) for c in ranked])
    npos = [c for c in report["cells"] if c["net16"] is not None and c["net16"] > 0 and c["valid_n"] >= 30]
    print("cells net16>0 & valid_n>=30:", len(npos))

def oos_rets(leg, k, common, z_by_L, s_str, e_str, cut, L, thr, h):
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    rets = []
    for i in range(len(common) - 1):
        t = int(common[i])
        if not (cut <= t < e_ms):
            continue
        if common[i + 1] != t + BAR:
            continue
        z = z_by_L[L][i]
        if not np.isfinite(z):
            continue
        if z <= -thr:
            sgn = 1.0 if leg == "ETHUSDT" else -1.0
        elif z >= thr:
            sgn = -1.0 if leg == "ETHUSDT" else 1.0
        else:
            continue
        ei = np.searchsorted(kot, t + BAR, side="left")
        if ei >= len(kot) or kot[ei] != t + BAR or kot[ei] >= e_ms:
            continue
        xi = ei + h
        if xi >= len(kot) or kot[xi] >= e_ms:
            continue
        if (np.diff(kot[ei:xi + 1]) != BAR).any():
            continue
        rets.append(sgn * (kop[xi] - kop[ei]) / kop[ei])
    return rets

if __name__ == "__main__":
    main()
