#!/usr/bin/env python3
# KP 김치 프리미엄 정상화 진단 — PREREG_kp_kimchi_2026-10-13.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
# 신호: 일 D 확정 KP z ← D+1일 00:15~ 신호봉 진입. z≥+thr 숏 / z≤−thr 롱 (정상화 역발상, Seo 2024).
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run"
KP_PATH = f"{BASE}/data/kp/kp_series_1d.csv"
OUT = f"{BASE}/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
KP_COL = {"BTCUSDT": "KP_BTC", "ETHUSDT": "KP_ETH"}
LOCK = {"BTCUSDT": ("2024-03-25", "2024-06-18"), "ETHUSDT": ("2026-04-28", "2026-08-31")}
END_STR = "2026-10-08"  # 측정 종점(사전 고정) — KP 합성 마지막 일자
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {
    "BTCUSDT": [("2021-04-01", "2024-03-25"), ("2024-06-19", END_STR)],
    "ETHUSDT": [("2021-04-01", "2026-04-28"), ("2026-09-01", END_STR)],
}
ZLS = [90, 288]
THRS = {"B80": 1.2816, "B90": 1.6449}
HORIZONS = [16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
SEEDS = 100
CELLS = [(L, b, h) for L in ZLS for b in THRS for h in HORIZONS]  # 8셀/세그먼트
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

def load_kp_z(sym):
    """KP 일간 → 인과 z(L∈{90,288}), 잠금 구간 오염 → NaN. 반환: {L: {day_ms: z}}, kp 설명"""
    df = pd.read_csv(KP_PATH).sort_values("day").reset_index(drop=True)
    days = (pd.to_datetime(df["day"], utc=True).astype("int64") // 10**6).to_numpy(dtype=np.int64)
    kp = df[KP_COL[sym]].to_numpy(dtype=float)
    lk0 = ms(LOCK[sym][0]); lk1 = ms(LOCK[sym][1]) + DAY_MS
    locked = (days >= lk0) & (days < lk1)
    zmaps = {}
    for L in ZLS:
        z = np.full(len(kp), np.nan)
        for i in range(L, len(kp)):
            if locked[i]:
                continue
            w = kp[i - L:i]
            if np.isnan(w).any() or locked[i - L:i].any():
                continue  # z 윈도우 오염 차단(fail-closed)
            sd = w.std(ddof=1)
            if sd > 0:
                z[i] = (kp[i] - w.mean()) / sd
        zmaps[L] = dict(zip(days[~np.isnan(z)].tolist(), z[~np.isnan(z)].tolist()))
    desc = {"n_days": int(len(kp)), "mean": float(np.nanmean(kp)), "p01": float(np.nanpercentile(kp, 1)),
            "p50": float(np.nanpercentile(kp, 50)), "p99": float(np.nanpercentile(kp, 99)),
            "first_day": str(pd.Timestamp(days[0], unit="ms", tz="UTC").date()),
            "last_day": str(pd.Timestamp(days[-1], unit="ms", tz="UTC").date())}
    return zmaps, desc

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, zmaps, sym, s_str, e_str):
    """S일 00:15~ 신호봉 ← z(S−1) 확정 KP. z≥+thr 숏(sgn=−1) / z≤−thr 롱(sgn=+1).
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
        # S일 봉 t는 z(S−1) 사용 — day d = t의 날짜, z 키 = d − 1일
        zvals[L] = np.array([zm.get(int(d - DAY_MS), np.nan) for d in day_ms], dtype=float)
    trades = {c: [] for c in CELLS}
    dropped = {c: 0 for c in CELLS}
    sig_bars = {(L, b): 0 for L in ZLS for b in THRS}
    sig_days = {(L, b): 0 for L in ZLS for b in THRS}
    # 이벤트 상태 머신: 극단 에피소드당 1 트레이드 (PREREG §5 "이벤트 중복 제거" 구현)
    # z가 극단을 벗어나면 에피소드 종료. 지속 중에는 첫 신호봉(첫 S일)만 진입.
    active = {(L, b): False for L in ZLS for b in THRS}
    for i in range(len(kot) - 1):
        t = kot[i]
        if not (s_ms <= t < e_ms): continue
        if t % DAY_MS == 0: continue  # 00:00 봉 제외(확정 경계 — 인과성 보장)
        if kot[i + 1] != t + 900000: continue
        d = int(day_ms[i])
        for L in ZLS:
            v = zvals[L][i]
            for b, thr in THRS.items():
                key = (L, b)
                if not np.isfinite(v) or (v < thr and v > -thr):
                    active[key] = False  # 극단 이탈 → 에피소드 종료
                    continue
                if active[key]:
                    continue  # 지속 중인 에피소드 — 중복 진입 금지(이벤트 중복 제거)
                sgn = -1.0 if v >= thr else +1.0   # 과열 숏 / 저조 롱 (정상화 사전 고정)
                sig_bars[key] += 1
                sig_days[key] += 1
                active[key] = True
                ei = i + 1
                if kot[ei] >= e_ms: continue
                for h in HORIZONS:
                    xi = ei + h
                    if xi >= len(kot) or kot[xi] >= e_ms: continue
                    if kot[xi] != kot[ei] + h * 900000:
                        dropped[(L, b, h)] += 1; continue  # 갭 fail-closed
                    entry, exitp = kop[ei], kop[xi]
                    gross_bp = sgn * (exitp / entry - 1) * 1e4
                    trades[(L, b, h)].append({"t": int(t), "S_day": str(pd.Timestamp(d, unit="ms", tz="UTC").date()),
                                              "z": float(v), "entry": float(entry), "exit": float(exitp),
                                              "gross_bp": float(gross_bp), "oos": None})
    out = {}
    for c in CELLS:
        tr = trades[c]
        gp = np.array([x["gross_bp"] for x in tr]) if tr else np.array([])
        rec = {"n": len(gp), "sig_bars": sig_bars[(c[0], c[1])], "sig_days": sig_days[(c[0], c[1])],
               "dropped_gap": dropped[c]}
        if len(gp) >= 5:
            rec.update({"gross_mean_bp": float(gp.mean()), "gross_median_bp": float(np.median(gp)),
                        "net16_mean_bp": float(gp.mean() - 16.0),
                        "c12_mean_bp": float(gp.mean() - 12.0), "c20_mean_bp": float(gp.mean() - 20.0),
                        "win_rate": float((gp > 0).mean()),
                        "t_stat": float(stats.ttest_1samp(gp, 0.0).statistic),
                        "p_raw": float(stats.ttest_1samp(gp, 0.0).pvalue)})
            # OOS = 스팬 후반 40% 이벤트
            ts = np.array([x["t"] for x in tr])
            cut = s_ms + (e_ms - s_ms) * 0.6
            oos = gp[ts >= cut]
            rec["oos_n"] = int(len(oos))
            rec["oos_net16_mean_bp"] = float(oos.mean() - 16.0) if len(oos) > 0 else None
            for x, o in zip(trades[c], (ts >= cut)):
                x["oos"] = bool(o)
        else:
            rec.update({"gross_mean_bp": None, "net16_mean_bp": None, "p_raw": None, "oos_n": 0})
        out[str(c)] = rec
    return out, q, trades

def main():
    results = {"meta": {"prereg": "PREREG_kp_kimchi_2026-10-13.md", "end": END_STR, "executed_utc": pd.Timestamp.utcnow().isoformat(),
                        "lock": LOCK, "spans": {s: SPANS[s] for s in SYMBOLS},
                        "cells": "32 = 2sym x {L90,L288} x {B80,B90} x {h16,h48} x {s1,s2}",
                        "primary_direction": "KP z>=+thr -> SHORT / z<=-thr -> LONG (정상화 역발상 사전 고정)"}}
    family = []  # (cell_id, p_raw)
    for sym in SYMBOLS:
        k = load_klines(sym)
        zmaps, kp_desc = load_kp_z(sym)
        results.setdefault("kp_desc", {})[sym] = kp_desc
        for si, (s_str, e_str) in enumerate(SPANS[sym], 1):
            cells, q, trades = run_span(k, zmaps, sym, s_str, e_str)
            results.setdefault("segments", {})[f"{sym}_s{si}"] = {
                "span": [s_str, e_str], "quality": q,
                "cells": {str(c): cells[str(c)] for c in CELLS}}
            for c in CELLS:
                rec = cells[str(c)]
                if rec.get("p_raw") is not None:
                    family.append((f"{sym}_s{si}|L{c[0]}{c[1]}h{c[2]}", rec["p_raw"], rec["n"], rec["net16_mean_bp"]))
        print(f"{sym} done", flush=True)

    # Holm 보정 (32셀 단일 패밀리)
    ids = [f[0] for f in family]; ps = [f[1] for f in family]
    adj = holm(ps) if ps else []
    holm_map = dict(zip(ids, adj))
    results["holm"] = {"n_cells_with_data": len(ids),
                       "passing": [i for i in ids if holm_map[i] < 0.05],
                       "detail": {i: {"p_raw": p, "p_holm": float(holm_map[i]), "n": n, "net16_bp": net}
                                  for i, p, n, net in family}}
    n_pass = len(results["holm"]["passing"])
    pos_pass = [i for i in results["holm"]["passing"]
                if next(f for f in family if f[0] == i)[3] > 0]
    results["verdict"] = {
        "PASS": bool(n_pass > 0 and len(pos_pass) > 0),
        "holm_passing_n": n_pass, "holm_passing_positive_n": len(pos_pass),
        "note": "PASS = holm p<0.05 & net16>0 (동일 (L,b,h) s1·s2 부호 일치·OOS·n>=30는 통과 셀에서 후속 확인)"}

    with open(f"{OUT}/kp_kimchi_results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # MD 요약
    lines = ["# KP 김치 프리미엄 정상화 — 측정 결과 (Task 29)", "",
             f"- PREREG: PREREG_kp_kimchi_2026-10-13.md · 종점 {END_STR} · 방향 사전 고정: KP z≥+thr 숏 / z≤−thr 롱",
             f"- Holm 패밀리: 32셀 · 통과 {n_pass}셀 (양수 방향 {len(pos_pass)}셀)"]
    for seg, v in results.get("segments", {}).items():
        lines.append(f"\n## {seg} span={v['span']} missing={v['quality']['kline_missing']}")
        for c in CELLS:
            r = v["cells"].get(str(c), {})
            if r.get("n", 0) > 0:
                lines.append(f"- L{c[0]} {c[1]} h{c[2]}: n={r['n']} gross={r['gross_mean_bp']:+.2f}bp net16={r['net16_mean_bp']:+.2f}bp "
                             f"win={r['win_rate']:.2f} p={r['p_raw']:.4f} oos_n={r.get('oos_n')}")
            else:
                lines.append(f"- L{c[0]} {c[1]} h{c[2]}: n={r.get('n', 0)} (이벤트 부족)")
    lines.append(f"\n## 판정: **{'PASS' if results['verdict']['PASS'] else 'FAIL (PASS=False)'}** — Holm 통과 {n_pass}셀(양수 {len(pos_pass)})")
    with open(f"{OUT}/kp_kimchi_results.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps(results["verdict"], indent=2))
    print("holm passing:", results["holm"]["passing"][:10])

if __name__ == "__main__":
    main()
