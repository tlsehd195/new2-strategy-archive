#!/usr/bin/env python3
# Task 31 — A1(Amihud 비유동성) 패밀리 사전검증.
# 원리: Amihud(2002) ILLIQ_t = |r_t| / qv_t (달러거래량당 가격충격), Lou(2016) 거래량 성분 자체 예측력.
# 기존 패밀리와의 신호 상관: L3(RV z) / X8(테이커 플로우 z) / P1(펀딩 z).
# 판정 기준(사전 고정): 전 패밀리 |ρ|<0.5 → 비겹침 측정 진행 / 하나라도 ≥0.8 → 구조적 중복으로 폐기.
import json, glob
import numpy as np
import pandas as pd

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/research"

def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
L_CMP = 288  # 비교 공통 윈도우(3일)

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "close", "qv", "volume", "tbv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df

def load_funding(sym):
    files = sorted(glob.glob(f"{BASE}/funding/{sym}-fundingRate-*.csv"))
    rows = []
    for f in files:
        try:
            d = pd.read_csv(f)
            if {"calc_time", "last_funding_rate"} <= set(d.columns):
                rows.append(d[["calc_time", "last_funding_rate"]])
        except Exception:
            pass
    f = pd.concat(rows, ignore_index=True)
    f["calc_time"] = pd.to_numeric(f["calc_time"], errors="coerce")
    f["last_funding_rate"] = pd.to_numeric(f["last_funding_rate"], errors="coerce")
    f = f.dropna().drop_duplicates("calc_time").sort_values("calc_time")
    return f

def zscore_causal(x, L, nwin=960):
    """인과 z: (x − 직전 nwin x평균)/std — shift 1. l3/rv_z 규약 동일."""
    s = pd.Series(x)
    m = s.rolling(nwin).mean().shift(1).to_numpy()
    sd = s.rolling(nwin).std(ddof=1).shift(1).to_numpy()
    return np.where((~np.isnan(m)) & (~np.isnan(sd)) & (sd > 0) & np.isfinite(x), (x - m) / sd, np.nan)

def roll_sum_z_causal(x, L, nwin=960):
    s = pd.Series(x)
    R = s.rolling(L).sum().to_numpy()
    m = pd.Series(R).rolling(nwin).mean().shift(1).to_numpy()
    sd = pd.Series(R).rolling(nwin).std(ddof=1).shift(1).to_numpy()
    return np.where((~np.isnan(m)) & (~np.isnan(sd)) & (sd > 0) & np.isfinite(R), (R - m) / sd, np.nan)

results = []
for sym, spans in SPANS.items():
    k = load_klines(sym)
    ot = k["open_time"].to_numpy(dtype=np.int64)
    close = k["close"].to_numpy(dtype=float)
    qv = k["qv"].to_numpy(dtype=float)
    vol = k["volume"].to_numpy(dtype=float)
    tbv = k["tbv"].to_numpy(dtype=float)

    r = np.full(len(close), np.nan)
    r[1:] = np.log(close[1:] / close[:-1])
    gap = ot[1:] - ot[:-1]
    r[1:][gap != 900000] = np.nan

    # A1: Amihud illiquidity, 15m — |r|/qv (qv>0만 유효)
    qv_safe = np.where(qv > 0, qv, np.nan)
    illiq = np.abs(r) / qv_safe
    illiq_z = zscore_causal(illiq, L_CMP)

    # 비교 신호
    rv = pd.Series(r).rolling(L_CMP).std(ddof=1).to_numpy()
    rv_z = zscore_causal(rv, L_CMP)
    flow_z = roll_sum_z_causal(tbv, L_CMP) - roll_sum_z_causal(vol, L_CMP)  # X8 R=Σtbv/Σvol 대신 근사: z(tb_sum)−z(vol_sum)는 부적절 → 올바른 비율로 재계산
    tb_s = pd.Series(tbv).rolling(L_CMP).sum().to_numpy()
    v_s = pd.Series(vol).rolling(L_CMP).sum().to_numpy()
    R_flow = tb_s / v_s
    m = pd.Series(R_flow).rolling(960).mean().shift(1).to_numpy()
    sd = pd.Series(R_flow).rolling(960).std(ddof=1).shift(1).to_numpy()
    flow_z = np.where((~np.isnan(m)) & (~np.isnan(sd)) & (sd > 0) & np.isfinite(R_flow), (R_flow - m) / sd, np.nan)

    f = load_funding(sym)
    f_ot = f["calc_time"].to_numpy(dtype=np.int64)
    f_val = f["last_funding_rate"].to_numpy(dtype=float)
    f_z_all = zscore_causal(f_val, 640)  # ~90일(8h 간격) 펀딩 z

    for si, (s_str, e_str) in enumerate(spans):
        sm, em = ms(s_str), ms(e_str)
        m_span = (ot >= sm) & (ot < em)
        idx = np.where(m_span)[0]
        # 펀딩 z 매핑: 각 kline t에 대해 calc_time<=t 인 최근 펀딩 z
        f_idx = np.searchsorted(f_ot, ot[idx], side="right") - 1
        fund_z_span = np.where(f_idx >= 0, f_z_all[np.clip(f_idx, 0, None)], np.nan)

        pairs = {
            "illiq_vs_rv": (illiq_z[idx], rv_z[idx]),
            "illiq_vs_flow": (illiq_z[idx], flow_z[idx]),
            "illiq_vs_funding": (illiq_z[idx], fund_z_span),
        }
        row = {"symbol": sym, "span": si, "window": f"{s_str}~{e_str}", "n": int(m_span.sum())}
        for pname, (a, b) in pairs.items():
            ok = np.isfinite(a) & np.isfinite(b)
            if ok.sum() > 100:
                row[pname] = {"pearson": round(float(np.corrcoef(a[ok], b[ok])[0, 1]), 4),
                              "spearman": round(float(pd.Series(a[ok]).corr(pd.Series(b[ok]), method="spearman")), 4),
                              "n": int(ok.sum())}
            else:
                row[pname] = {"n": int(ok.sum()), "insufficient": True}
        results.append(row)
        print(sym, si, json.dumps(row, ensure_ascii=False))

verdict = {
    "date": "2026-10-13",
    "principle": "A1 Amihud illiquidity |r|/qv (Amihud 2002, Lou 2016)",
    "rule": "|rho|>=0.8 vs any family -> redundant DISCARD; else proceed to PREREG+measure",
    "results": results,
}
with open(f"{OUT}/a1_family_precheck.json", "w") as fh:
    json.dump(verdict, fh, indent=2, ensure_ascii=False)
print("saved:", f"{OUT}/a1_family_precheck.json")
