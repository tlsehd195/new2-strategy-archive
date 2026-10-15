#!/usr/bin/env python3
# Task 34 ALT-P1-R2 §5 사전검증 — 방향 정보 없는 분포·트리거 수 기록 (전략 수익 열람 전 실행).
# PREREG_alt_funding_extreme_r2_2026-10-14.md §5 준용: 펀딩 분포 + 버킷 트리거 수만 계산.
import json, glob
import numpy as np
import pandas as pd

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/research/alt_funding_r2_precheck.json"
SYMBOLS = ["ZECUSDT", "DOGEUSDT"]
SPANS = [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-10-01")]
N90 = 90
BUCKETS = {"B80": 1.2816, "B90": 1.6449}

def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)

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

out = {"prereg": "PREREG_alt_funding_extreme_r2_2026-10-14.md", "stage": "precheck_before_returns",
       "symbols": {}}
for sym in SYMBOLS:
    f = load_funding(sym)
    r = f["rate"].to_numpy(dtype=float)
    ft = f["calc_time"].to_numpy(dtype=np.int64)
    zs = funding_z(f["rate"])
    d = {
        "n_settlements_total": int(len(r)),
        "range_utc": [str(pd.Timestamp(ft.min(), unit="ms", tz="UTC")), str(pd.Timestamp(ft.max(), unit="ms", tz="UTC"))],
        "median_bp": float(np.nanmedian(r) * 1e4),
        "p01_bp": float(np.nanpercentile(r, 1) * 1e4),
        "p99_bp": float(np.nanpercentile(r, 99) * 1e4),
        "share_abs_ge_10bp": float(np.nanmean(np.abs(r) >= 0.001)),
        "share_abs_ge_30bp": float(np.nanmean(np.abs(r) >= 0.003)),
        "spans": {},
    }
    for si, (s_str, e_str) in enumerate(SPANS):
        s_ms, e_ms = ms(s_str), ms(e_str)
        m = (ft >= s_ms) & (ft < e_ms)
        n_set = int(m.sum())
        trig = {}
        for b, thr in BUCKETS.items():
            z_in = zs[m & np.isfinite(zs)]
            trig[b] = int(np.sum(np.abs(z_in) >= thr))
        d["spans"][f"span{si+1}_{s_str}~{e_str}"] = {"settlements": n_set, "triggers": trig}
    out["symbols"][sym] = d

with open(OUT, "w") as fp:
    json.dump(out, fp, indent=1)

for sym, d in out["symbols"].items():
    print(f"{sym}: 정산 {d['n_settlements_total']}·중앙값 {d['median_bp']:.1f}bp·p01 {d['p01_bp']:.0f}/p99 {d['p99_bp']:.0f}bp·|r|≥10bp {d['share_abs_ge_10bp']*100:.2f}%·≥30bp {d['share_abs_ge_30bp']*100:.2f}%")
    for sk, sv in d["spans"].items():
        print(f"  {sk}: 정산 {sv['settlements']}·B80 {sv['triggers']['B80']}·B90 {sv['triggers']['B90']}")
