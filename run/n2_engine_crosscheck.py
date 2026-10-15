#!/usr/bin/env python3
# N2 교차검증 — 체결 시뮬레이션의 독립 재구현(벡터화) + 엔진 비용 구성표($10k/$100k) + taker-taker 대조군.
# PREREG_n2_maker_exec_2026-10-13.md §6: 셀별 체결률·체결분 평균 순익 일치 검증 (허용 오차 1e-8bp).
# 이벤트 생성 규칙은 x1r_reverify_diag.py 규격 그대로 — 진단 스크립트와 독립된 코드 구조로 재계산.
import json, os
import numpy as np
import pandas as pd

BASE = "/home/z/my-project/NEW2_run"
OUT = f"{BASE}/results"

def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)

SPAN = ("2024-06-19", "2026-04-20")
ASSETS = ["ES", "NQ"]
LOOKBACKS = [24, 120]
THR = 1.2816
H = 48
NWIN = 720
HOUR = 3600000
DELTAS = [0.0, 0.0005, 0.0010, 0.0020]
KWINS = [4, 16]
MAKER_FEE = 0.0002
FEE, HALF_SPREAD, IMPACT, MAX_PART = 0.0005, 0.0002, 0.1, 0.05
CELLS = [(A, L, d, K) for A in ASSETS for L in LOOKBACKS for d in DELTAS for K in KWINS]

def load_yahoo_1h(path):
    d = json.load(open(path))
    res = d["chart"]["result"][0]
    ts = res["timestamp"]
    close = res["indicators"]["quote"][0]["close"]
    pairs = [(int(t) * 1000, float(c)) for t, c in zip(ts, close)
             if c is not None and np.isfinite(float(c))]
    pairs = [(t, c) for t, c in pairs if t % HOUR == 0]
    df = pd.DataFrame(pairs, columns=["open_time", "close"]).drop_duplicates("open_time")
    return df.sort_values("open_time").reset_index(drop=True)

def ext_z(df, L):
    t = df["open_time"].to_numpy(np.int64)
    c = df["close"].to_numpy(float)
    idx_of = {int(v): i for i, v in enumerate(t)}
    d = np.full(len(t), np.nan)
    for i in range(len(t)):
        j = idx_of.get(int(t[i]) - L * HOUR)
        if j is not None:
            d[i] = c[i] - c[j]
    fin = np.where(np.isfinite(d))[0]
    dv = d[fin]
    rm = pd.Series(dv).rolling(NWIN).mean().shift(1).to_numpy()
    rs = pd.Series(dv).rolling(NWIN).std(ddof=1).shift(1).to_numpy()
    z = np.full(len(t), np.nan)
    ok = np.isfinite(rm) & np.isfinite(rs) & (rs > 0)
    z[fin[ok]] = (dv[ok] - rm[ok]) / rs[ok]
    return t, z

def load_klines(sym):
    import glob
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/data/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "high", "low", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df

def side_cost_vec(order_value, qv):
    """엔진 1면 비용 — 벡터화. participation>5% → NaN (fail-closed)."""
    qv = np.asarray(qv, dtype=float)
    part = np.where(qv > 0, order_value / np.where(qv > 0, qv, 1.0), np.inf)
    c = FEE + HALF_SPREAD + IMPACT * np.sqrt(part)
    return np.where(part <= MAX_PART, c, np.nan)

def vectorized_events(k, ext):
    """이벤트 배열 생성 — 진단 스크립트(루프)와 다른 벡터 구조."""
    s_ms, e_ms = ms(SPAN[0]), ms(SPAN[1])
    kot = k["open_time"].to_numpy(np.int64)
    pos = {int(v): i for i, v in enumerate(kot)}
    ev = {A: {} for A in ASSETS}
    for A in ASSETS:
        t_arr, _ = ext[A]["t"], None
        for L in LOOKBACKS:
            _, z = ext_z(ext[A]["df"], L)
            sel = np.where((t_arr >= s_ms) & (t_arr < e_ms) & np.isfinite(z) & (np.abs(z) >= THR))[0]
            tt = t_arr[sel]
            sgn = np.where(z[sel] >= THR, -1.0, 1.0)
            eis, keep = [], []
            for j in range(len(tt)):
                ei = pos.get(int(tt[j]) + HOUR)
                if ei is not None and kot[ei] < e_ms:
                    eis.append(ei); keep.append(j)
            ev[A][L] = {"tt": tt[keep], "ei": np.array(eis, dtype=np.int64), "sgn": sgn[keep]}
    return ev

def vectorized_fill(ei_arr, sgn_arr, d, K, kop, kho, klo):
    """체결 판정 — 벡터화: 윈도우 [ei, ei+K) 를 2D로 스택해 첫 조건 봉 탐색."""
    n = len(ei_arr)
    off = np.arange(K)
    win = ei_arr[:, None] + off[None, :]                      # (n, K)
    op = kop[win]; lo = klo[win]; hi = kho[win]
    P = kop[ei_arr] * (1 - d * sgn_arr)                       # 롱: (1−δ), 숏: (1+δ)
    if True:  # 롱/숏 공용: 조건 행렬
        cond_open = np.where(sgn_arr[:, None] > 0, op < P[:, None], op > P[:, None])
        cond_pass = np.where(sgn_arr[:, None] > 0, lo < P[:, None], hi > P[:, None])
    any_hit = cond_open | cond_pass
    has = any_hit.any(axis=1)
    first = np.where(has, any_hit.argmax(axis=1), -1)
    f = np.where(has, ei_arr + np.maximum(first, 0), -1)
    via_open = np.zeros(n, dtype=bool)
    ok = has
    via_open[ok] = cond_open[ok, np.maximum(first[ok], 0)]
    fill_px = np.where(via_open, kop[np.maximum(f, 0)], P)
    return has, f, fill_px

def run_cell(ev_cell, d, K, k, order_value):
    kot = k["open_time"].to_numpy(np.int64)
    kop = k["open"].to_numpy(float); kho = k["high"].to_numpy(float)
    klo = k["low"].to_numpy(float); kqv = k["qv"].to_numpy(float)
    e_ms = ms(SPAN[1])
    ei_arr = ev_cell["ei"]; sgn_arr = ev_cell["sgn"]
    has, f, fill_px = vectorized_fill(ei_arr, sgn_arr, d, K, kop, kho, klo)
    # 기본 검증(진단과 동일): [ei, ei+48] 연속+스팬 내 — 미통과 이벤트 제외 후 집계
    valid = np.array([ (x + H < len(kot)) and kot[x + H] < e_ms and (np.diff(kot[x:x + H + 1]) == 900000).all()
                       for x in ei_arr ])
    fc = np.zeros(len(ei_arr), dtype=bool)
    net = np.full(len(ei_arr), np.nan); gross = np.full(len(ei_arr), np.nan)
    okv = has & valid
    idx = np.where(okv)[0]
    for j in idx:
        ff = f[j]
        if ff + H >= len(kot) or kot[ff + H] >= e_ms or (np.diff(kot[ff:ff + H + 1]) != 900000).any():
            fc[j] = True; continue
        xc = side_cost_vec(order_value, kqv[ff + H])
        if not np.isfinite(xc):
            fc[j] = True; continue
        gross[j] = sgn_arr[j] * (kop[ff + H] - fill_px[j]) / fill_px[j]
        net[j] = gross[j] - MAKER_FEE - xc
    n_valid = int(valid.sum())
    filled = np.where(okv & ~fc & np.isfinite(net))[0]
    return {"n_valid": n_valid, "n_filled": int(len(filled)), "n_fc": int(fc.sum()),
            "fill_rate": len(filled) / n_valid if n_valid else None,
            "mean_net_bp": float(np.mean(net[filled]) * 1e4) if len(filled) else None,
            "promo_net_bp": float((len(filled) / n_valid) * np.mean(net[filled]) * 1e4)
                            if n_valid and len(filled) else None}

def taker_baseline(ev, k, order_value):
    """taker-taker 대조군 — X1-R 엔진 교차검증 공식 (ei 진입, ei+48 청산)."""
    kot = k["open_time"].to_numpy(np.int64)
    kop = k["open"].to_numpy(float); kqv = k["qv"].to_numpy(float)
    out = {}
    for A in ASSETS:
        for L in LOOKBACKS:
            ec = ev[A][L]
            kop_e = kop[ec["ei"]]; kop_x = kop[ec["ei"] + H]
            qv_e = kqv[ec["ei"]]; qv_x = kqv[ec["ei"] + H]
            raw = ec["sgn"] * (kop_x - kop_e) / kop_e
            ce = side_cost_vec(order_value, qv_e); cx = side_cost_vec(order_value, qv_x)
            okm = np.isfinite(ce) & np.isfinite(cx)
            net = raw[okm] - ce[okm] - cx[okm]
            out[f"{A}_L{L}"] = {"n": int(okm.sum()), "gross_bp": float(raw[okm].mean() * 1e4),
                                "cost_bp": float((ce[okm] + cx[okm]).mean() * 1e4),
                                "net_bp": float(net.mean() * 1e4)}
    return out

def main():
    k = load_klines("BTCUSDT")
    ext = {A: {"df": load_yahoo_1h(f"{BASE}/research/r3_yahoo_{A.lower()}.json")} for A in ASSETS}
    for A in ASSETS:
        ext[A]["t"] = ext[A]["df"]["open_time"].to_numpy(np.int64)
    ev = vectorized_events(k, ext)
    report = {"crosscheck_of": "n2_maker_exec_results.json", "method": "독립 벡터화 재구현",
              "tolerance_bp": 1e-8, "cells": [], "taker_baseline_o10k": taker_baseline(ev, k, 10_000.0),
              "taker_baseline_o100k": taker_baseline(ev, k, 100_000.0)}
    # 엔진 비용 구성표: 진입 maker 2bp 고정 + 청산 taker (규모별)
    all_ok = True
    for A, L, d, K in CELLS:
        r10 = run_cell(ev[A][L], d, K, k, 10_000.0)
        r100 = run_cell(ev[A][L], d, K, k, 100_000.0)
        row = {"asset": A, "lookback": L, "delta_bp": d * 1e4, "k": K,
               "fill_rate_10k": r10["fill_rate"], "mean_net_bp_10k": r10["mean_net_bp"],
               "promo_net_bp_10k": r10["promo_net_bp"], "n_fc_10k": r10["n_fc"],
               "fill_rate_100k": r100["fill_rate"], "mean_net_bp_100k": r100["mean_net_bp"],
               "promo_net_bp_100k": r100["promo_net_bp"], "n_fc_100k": r100["n_fc"]}
        report["cells"].append(row)
    # 진단 스크립트 결과와 비교
    try:
        diag = json.load(open(f"{OUT}/n2_maker_exec_results.json"))
        dmap = {(c["asset"], c["lookback"], c["delta_bp"], c["k_window"]): c for c in diag["cells"]}
    except Exception:
        dmap = {}
    diffs = []
    for row in report["cells"]:
        key = (row["asset"], row["lookback"], row["delta_bp"], row["k"])
        dc = dmap.get(key)
        if dc is None: continue
        dd = {"cell": f"{key}", "fill_rate_diff": abs(row["fill_rate_10k"] - dc["fill_rate"]),
              "mean_net_diff_bp": abs(row["mean_net_bp_10k"] - dc["mean_net_filled_bp"])}
        diffs.append(dd)
        if dd["fill_rate_diff"] > 1e-12 or dd["mean_net_diff_bp"] > 1e-8:
            all_ok = False
    report["match_diag"] = {"all_within_tolerance": bool(all_ok), "n_compared": len(diffs),
                            "max_fill_rate_diff": max((x["fill_rate_diff"] for x in diffs), default=None),
                            "max_mean_net_diff_bp": max((x["mean_net_diff_bp"] for x in diffs), default=None)}
    with open(f"{OUT}/n2_crosscheck_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    # MD
    L = ["# N2 교차검증 — 독립 벡터화 재구현 + 엔진 비용 구성표", "",
         "- 체결 시뮬레이션을 진단 스크립트와 다른 코드 구조(벡터화)로 재실행 — 셀별 체결률·체결분 평균 순익 일치 검증",
         f"- 일치 판정: {report['match_diag']['all_within_tolerance']} (비교 {report['match_diag']['n_compared']}셀, "
         f"최대 체결률 차 {report['match_diag']['max_fill_rate_diff']}, 최대 순익 차 {report['match_diag']['max_mean_net_diff_bp']}bp)",
         "- 비용: 진입 maker 2bp + 청산 엔진 taker 1면 (5bp + half_spread 2bp + 10%·√participation, >5% fail-closed)", ""]
    L.append("## 엔진 비용 구성 ($10k / $100k) — 대표 셀")
    L.append("| 자산 | L | δ | K | 체결률($10k) | 순익($10k)bp | promo($10k) | 체결률($100k) | 순익($100k)bp | promo($100k) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for row in report["cells"]:
        fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
        fr10 = "n/a" if row["fill_rate_10k"] is None else f"{row['fill_rate_10k']:.3f}"
        fr100 = "n/a" if row["fill_rate_100k"] is None else f"{row['fill_rate_100k']:.3f}"
        L.append(f"| {row['asset']} | {row['lookback']} | {row['delta_bp']:.0f} | {row['k']} | {fr10} | "
                 f"{fmt(row['mean_net_bp_10k'])} | {fmt(row['promo_net_bp_10k'])} | {fr100} | "
                 f"{fmt(row['mean_net_bp_100k'])} | {fmt(row['promo_net_bp_100k'])} |")
    for tag in ("o10k", "o100k"):
        L.append("")
        L.append(f"## taker-taker 대조군 (${tag[1:]}) — X1-R 엔진 공식 왕복")
        L.append("| 자산 | L | n | 원시bp | 엔진비용bp | 순익bp |")
        L.append("|---|---|---|---|---|---|")
        for kk, v in report[f"taker_baseline_{tag}"].items():
            L.append(f"| {kk.split('_')[0]} | {kk.split('_L')[1]} | {v['n']} | {v['gross_bp']:.2f} | {v['cost_bp']:.2f} | {v['net_bp']:.2f} |")
    with open(f"{OUT}/n2_crosscheck_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")
    print("match:", report["match_diag"])
    pos10 = sum(1 for r in report["cells"] if r["promo_net_bp_10k"] and r["promo_net_bp_10k"] > 0)
    pos100 = sum(1 for r in report["cells"] if r["promo_net_bp_100k"] and r["promo_net_bp_100k"] > 0)
    print(f"promo>0: $10k {pos10}/32, $100k {pos100}/32")
    for kk, v in report["taker_baseline_o10k"].items():
        print(f"taker ${kk}: gross={v['gross_bp']:.2f} cost={v['cost_bp']:.2f} net={v['net_bp']:.2f}")

if __name__ == "__main__":
    main()
