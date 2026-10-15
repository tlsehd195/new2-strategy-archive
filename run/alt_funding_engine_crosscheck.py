#!/usr/bin/env python3
# Task 33 ALT-P1 엔진 교차검증 — 저장소 CandleCostModel(costs.py:104-117) 기본 파라미터 적용.
# 신호/거래 생성은 alt_funding_diag.py(PREREG 규격)와 완전히 동일 로직 — 결과 재현 보장.
# 회계: net_engine = (가격수익 + 펀딩캐리) − 엔진 왕복비용 (diag와 동일 캐리 포함 — PREREG §6).
# 감도: half_spread ×2(4bp) 참조행(승격 판정 아님 — PREREG §6). t는 valid_n=n/h 표준(교훈 ⑧).
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
SYMBOLS = ["BNBUSDT", "XRPUSDT", "SOLUSDT", "TRXUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-10-01")]
BUCKETS = {"B80": 1.2816, "B90": 1.6449}
HORIZONS = [16, 48, 96]
N90 = 90
BAR = 900000
FLAT_C16 = 0.0016

FEE, HALF_SPREAD, IMPACT, MAX_PART = 0.0005, 0.0002, 0.1, 0.05

def engine_side(order_value, qv, half_spread=HALF_SPREAD):
    if qv is None or qv <= 0:
        return None
    part = order_value / qv
    if part > MAX_PART:
        return None
    return FEE + half_spread + IMPACT * np.sqrt(part)

def engine_roundtrip(order_value, qv_e, qv_x, half_spread=HALF_SPREAD):
    a = engine_side(order_value, qv_e, half_spread)
    b = engine_side(order_value, qv_x, half_spread)
    if a is None or b is None:
        return None
    return a + b

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    dfs = []
    for f in sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv")):
        d = pd.read_csv(f, header=None, names=cols, low_memory=False)
        d = d[pd.to_numeric(d["open_time"], errors="coerce").notna()].copy()
        if len(d):
            dfs.append(d)
    df = pd.concat(dfs, ignore_index=True)
    for c in ["open_time", "open", "qv"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open_time", "open", "qv"])
    df["open_time"] = df["open_time"].astype(np.int64)
    if df["open_time"].max() > 10**14:
        df["open_time"] = df["open_time"] // 1000
    return df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

def load_funding(sym):
    dfs = []
    for f in sorted(glob.glob(f"{BASE}/funding/{sym}-fundingRate-*.csv")):
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
    return df.drop_duplicates(tcol).sort_values(tcol).reset_index(drop=True)[[tcol, rcol]].rename(columns={tcol: "calc_time", rcol: "rate"})

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

def main():
    report = {"prereg": "PREREG_alt_funding_extreme_2026-10-14.md", "family_size": 48,
              "engine_params": {"fee": FEE, "half_spread": HALF_SPREAD, "impact": IMPACT, "max_part": MAX_PART},
              "order_sizes": [10000, 100000], "cells": []}
    for sym in SYMBOLS:
        k = load_klines(sym); f = load_funding(sym)
        ot = k["open_time"].to_numpy(dtype=np.int64)
        op = k["open"].to_numpy(dtype=float)
        qv = k["qv"].to_numpy(dtype=float)
        ft = f["calc_time"].to_numpy(dtype=np.int64)
        fr = f["rate"].to_numpy(dtype=float)
        zs = funding_z(f["rate"])
        for si, (s_str, e_str) in enumerate(SPANS):
            s_ms, e_ms = ms(s_str), ms(e_str)
            sig = [(t, z) for t, z in zip(ft, zs) if s_ms <= t < e_ms and np.isfinite(z)]
            for b, thr in BUCKETS.items():
                for h in HORIZONS:
                    recs = []
                    for T, z in sig:
                        if abs(z) < thr:
                            continue
                        d = -1.0 if z > 0 else 1.0
                        ei = np.searchsorted(ot, T, side="right")
                        if ei >= len(ot) or ot[ei] >= e_ms:
                            continue
                        xi = ei + h
                        if xi >= len(ot) or ot[xi] >= e_ms:
                            continue
                        seg = ot[ei:xi + 1]
                        if (np.diff(seg) != BAR).any():
                            continue
                        lo, hi = ot[ei], ot[xi]
                        sl = ft[(ft > lo) & (ft <= hi)]
                        carry = -d * float(fr[np.searchsorted(ft, sl)].sum()) if len(sl) else 0.0
                        price = d * (op[xi] - op[ei]) / op[ei]
                        recs.append((price, carry, qv[ei], qv[xi]))
                    n = len(recs)
                    vn = n / h
                    cell = {"symbol": sym, "span": f"{s_str}~{e_str}", "bucket": b, "horizon": h, "n": n, "valid_n": vn}
                    for ov in report["order_sizes"]:
                        # fail-closed: participation 초과 트레이드 제외한 유효 트레이드만 사용
                        valid = [(p, c_, engine_roundtrip(ov, qe, qx)) for p, c_, qe, qx in recs]
                        valid = [(p, c_, ec) for p, c_, ec in valid if ec is not None]
                        n_eff = len(valid)
                        if n_eff:
                            Rv = np.array([p + c_ for p, c_, _ in valid])
                            ec = np.array([e for _, _, e in valid])
                            net = Rv - ec
                            t = net.mean() / (net.std(ddof=1) / np.sqrt(n_eff / h)) if net.std(ddof=1) > 0 else 0.0
                            cell[f"ov{ov}"] = {
                                "n_fc": int(len(recs) - n_eff),
                                "engine_cost_bp": float(ec.mean() * 1e4),
                                "net_mean_bp": float(net.mean() * 1e4),
                                "net_pos": int((net > 0).sum()),
                                "t": float(t),
                                "p": float(2 * stats.t.sf(abs(t), df=max(n_eff / h - 1, 1)))}
                        else:
                            cell[f"ov{ov}"] = {"n_fc": int(len(recs)), "engine_cost_bp": None,
                                               "net_mean_bp": None, "net_pos": 0, "t": None, "p": 1.0}
                        valid2 = [(p + c_, engine_roundtrip(ov, qe, qx, half_spread=HALF_SPREAD * 2)) for p, c_, qe, qx in recs]
                        valid2 = [(r_, e_) for r_, e_ in valid2 if e_ is not None]
                        if valid2 and ov == 10000:  # 감도는 $10k만 참조행
                            Rv2 = np.array([r_ for r_, _ in valid2])
                            ec2 = np.array([e for _, e in valid2])
                            net2 = Rv2 - ec2
                            cell["sens_2x_hs"] = {"net_mean_bp": float(net2.mean() * 1e4),
                                                  "net_pos": int((net2 > 0).sum()), "n": int(len(net2))}
                    report["cells"].append(cell)
    # 요약 판정: 승격 기준은 diag의 Holm 기준이 이미 FAIL — 엔진은 재확증 역할
    for ov in report["order_sizes"]:
        key = f"ov{ov}"
        pos = [c for c in report["cells"] if c[key]["net_mean_bp"] is not None and c[key]["net_mean_bp"] > 0]
        report[f"engine_net_pos_{ov}"] = f"{len(pos)}/{len(report['cells'])}"
        best = max(report["cells"], key=lambda c: c[key]["net_mean_bp"] if c[key]["net_mean_bp"] is not None else -9e9)
        report[f"engine_best_{ov}"] = {k2: best[k2] for k2 in ("symbol", "span", "bucket", "horizon", "n")} | best[key]
    sens = [c for c in report["cells"] if c.get("sens_2x_hs", {}).get("net_mean_bp") is not None and c["sens_2x_hs"]["net_mean_bp"] > 0]
    report["sens_2x_hs_pos_10k"] = f"{len(sens)}/{len([c for c in report['cells'] if c.get('sens_2x_hs')])}"
    report["verdict"] = {"PASS": False,
                         "note": "diag Holm 0/48로 승격 대상 셀 부재 — 엔진은 FAIL 재확증(전 셀 실행, PREREG §6)"}
    with open(f"{OUT}/alt_funding_crosscheck_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    lines = ["# ALT-P1 엔진 교차검증 (CandleCostModel + 캐리 회계 동일 포함)", "",
             f"엔진 파라미터: fee 5bp·half_spread 2bp·impact 0.1·max_part 5% · 주문 $10k/$100k · t=valid_n 표준", ""]
    for ov in report["order_sizes"]:
        b = report[f"engine_best_{ov}"]
        lines.append(f"- 엔진 net>0 ({ov}): **{report[f'engine_net_pos_{ov}']}** — 최고 {b['symbol']} {b['span']} {b['bucket']} h{b['horizon']} net {b['net_mean_bp']:.1f}bp(비용 {b['engine_cost_bp']:.1f}bp, p {b['p']:.3f}, FC {b['n_fc']})")
    lines.append(f"- half_spread ×2 감도($10k) net>0: {report['sens_2x_hs_pos_10k']}")
    lines.append("- **판정: FAIL 재확증** — diag Holm 0/48, 승격 대상 셀 부재")
    with open(f"{OUT}/alt_funding_crosscheck_results.md", "w") as fp:
        fp.write("\n".join(lines))
    print("\n".join(lines))

if __name__ == "__main__":
    main()
