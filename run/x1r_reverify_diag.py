#!/usr/bin/env python3
# X1-R 재검증 진단 — PREREG_x1r_reverify_2026-10-13.md 규격 구현.
# ES/NQ 1h 모멘텀 극단 |z|>=1.2816 -> h48 역방향(되돌림). 패밀리 = 구성당 4셀 {ES,NQ}x{L24,L120}xh48.
# 구성: R(ETH span2 재현) / A(BTC 2024-06-19~2026-04-20 자산 일반화) / C(BTC 2026-04-21~10-12 시간 복제)
#       B(ETH 잠금 제외 2세그먼트, 정보성). 규칙 변경 금지. 결과는 results/ 에 JSON+MD 저장.
import json, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run"
OUT = f"{BASE}/results"
os.makedirs(OUT, exist_ok=True)

def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)

ENDPOINT = "2026-10-12"  # 측정 종점 2026-10-12 00:00 UTC (C3 선례)
LOOKBACKS = [24, 120]
THR = 1.2816
H = 48
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
NWIN = 720
SEEDS = 100
SEED = 20261013
ASSETS = ["ES", "NQ"]
CELLS = [(A, L) for A in ASSETS for L in LOOKBACKS]  # 4셀/구성
HOUR = 3600000

CONFIGS = {
    "R": {"sym": "ETHUSDT", "segments": [("2024-12-10", "2026-04-20")],
          "note": "재현 검증(원 X1 셀 값 일치 확인 — 판정 아님)"},
    "A": {"sym": "BTCUSDT", "segments": [("2024-06-19", "2026-04-20")],
          "note": "자산 일반화(홀드아웃 자산, BTC 잠금 2024-03-25~06-18 구조적 배제)"},
    "C": {"sym": "BTCUSDT", "segments": [("2026-04-21", ENDPOINT)],
          "note": "시간 복제(신규 구간 순수 OOS)"},
    "B": {"sym": "ETHUSDT", "segments": [("2026-04-21", "2026-04-27"), ("2026-09-01", ENDPOINT)],
          "note": "시간 복제 ETH(잠금 2026-04-28~08-31 제외, 정보성)"},
}
# Config R 재현 목표값 (원 X1 결과 테이블 — 역방향 원시 총bp)
REPRO_EXPECT = {("ES", 24): 54.26, ("ES", 120): 47.85, ("NQ", 24): 36.13, ("NQ", 120): 34.76}
REPRO_TOL_BP = 2.0

def load_yahoo_1h(path):
    d = json.load(open(path))
    res = d["chart"]["result"][0]
    ts = res["timestamp"]
    close = res["indicators"]["quote"][0]["close"]
    pairs = [(int(t) * 1000, float(c)) for t, c in zip(ts, close)
             if c is not None and np.isfinite(float(c))]
    pairs = [(t, c) for t, c in pairs if t % HOUR == 0]  # 정시 격자만(방어)
    df = pd.DataFrame(pairs, columns=["open_time", "close"]).drop_duplicates("open_time")
    df = df.sort_values("open_time").reset_index(drop=True)
    return df

def ext_z(df, L):
    """Δ_t = close_t − close_{t−L}, z = 직전 720 관측 Δ 표준화(인과). X1 원구현과 동일."""
    t = df["open_time"].to_numpy(np.int64)
    c = df["close"].to_numpy(float)
    d = np.full(len(t), np.nan)
    for i in range(len(t)):
        target = t[i] - L * HOUR
        j = np.searchsorted(t, target)
        if j < len(t) and t[j] == target:
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
    for c in ["open_time", "open", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_segment(kot, kop, t, z_by_L, s_ms, e_ms, asset):
    """역방향: z>=+THR -> 숏(-1), z<=-THR -> 롱(+1). X1 run_segment와 동일 규칙(방향만 반전)."""
    q = {"signal_bars_any": 0}
    trades = {c: [] for c in CELLS}
    dropped = {c: 0 for c in CELLS}
    for i in np.where((t >= s_ms) & (t < e_ms))[0]:
        tt = int(t[i])
        if any(np.isfinite(z_by_L[L][i]) for L in LOOKBACKS):
            q["signal_bars_any"] += 1
        ei = np.searchsorted(kot, tt + HOUR, side="left")
        if ei >= len(kot) or kot[ei] != tt + HOUR or kot[ei] >= e_ms:
            continue
        for L in LOOKBACKS:
            z = z_by_L[L][i]
            if not np.isfinite(z): continue
            if z >= THR: sgn = -1.0
            elif z <= -THR: sgn = +1.0
            else: continue
            xi = ei + H
            if xi >= len(kot) or kot[xi] >= e_ms: continue
            if (np.diff(kot[ei:xi + 1]) != 900000).any():
                dropped[(asset, L)] += 1; continue
            raw = (kop[xi] - kop[ei]) / kop[ei]
            trades[(asset, L)].append(sgn * raw)
    return q, trades, dropped

def run_config(k, z_by_A, t_by_A, cfg):
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    q_total = {"signal_bars_any": 0}
    trades = {c: [] for c in CELLS}
    dropped = {c: 0 for c in CELLS}
    seg_info = []
    for s_str, e_str in cfg["segments"]:
        s_ms, e_ms = ms(s_str), ms(e_str)
        for A in ASSETS:
            t = t_by_A[A]
            q, tr, dr = run_segment(kot, kop, t, z_by_A[A], s_ms, e_ms, A)
            q_total["signal_bars_any"] += q["signal_bars_any"]
            for c in CELLS:
                trades[c].extend(tr[c]); dropped[c] += dr[c]
        seg_info.append({"span": f"{s_str}~{e_str}", "signal_bars": q["signal_bars_any"]})
    return q_total, trades, dropped, seg_info

def cell_stats(rets):
    n = len(rets)
    out = {"n": n, "valid_n": n / H}
    if n == 0:
        out.update({"total_bp": None, "net12": None, "net16": None, "net20": None,
                    "t": None, "p": 1.0, "follow_net16": None, "p_ctrl": None})
        return out
    r = np.array(rets)
    vn = n / H
    out["total_bp"] = float(r.mean() * 1e4)
    for kk, c in COST.items():
        out[f"net{kk[1:]}"] = float((r.mean() - c) * 1e4)
    sd = r.std(ddof=1)
    t = (r.mean() - COST["c16"]) / (sd / np.sqrt(vn)) if sd > 0 else 0.0
    out["t"] = float(t)
    out["p"] = float(2 * stats.t.sf(abs(t), df=max(vn - 1, 1)))
    out["follow_net16"] = float((-r.mean() - COST["c16"]) * 1e4)  # 통제: 원 X1 추종 방향
    rng = np.random.default_rng(SEED)
    rand_means = []
    for _ in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * r).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def oos_config_a(k, z_by_A, t_by_A, s_str, e_str):
    """Config A 후반 40% OOS — 역방향, G3 참고치."""
    kot = k["open_time"].to_numpy(np.int64)
    kop = k["open"].to_numpy(float)
    s_ms, e_ms = ms(s_str), ms(e_str)
    cut = s_ms + int((e_ms - s_ms) * 0.6)
    segs = []
    for A, L in CELLS:
        t = t_by_A[A]; zl = z_by_A[A][L]
        rets = []
        for i in np.where((t >= cut) & (t < e_ms))[0]:
            tt = int(t[i]); z = zl[i]
            if not np.isfinite(z): continue
            if z >= THR: sgn = -1.0
            elif z <= -THR: sgn = +1.0
            else: continue
            ei = np.searchsorted(kot, tt + HOUR, side="left")
            if ei >= len(kot) or kot[ei] != tt + HOUR: continue
            xi = ei + H
            if xi >= len(kot) or kot[xi] >= e_ms: continue
            if (np.diff(kot[ei:xi + 1]) != 900000).any(): continue
            rets.append(sgn * (kop[xi] - kop[ei]) / kop[ei])
        n = len(rets)
        segs.append({"asset": A, "lookback": L, "n": n, "valid_n": n / H,
                     "total_bp": float(np.mean(rets) * 1e4) if n else None,
                     "net16": float((np.mean(rets) - 0.0016) * 1e4) if n else None})
    return segs

def family_precheck(ext, z_by_A, t_by_A, k_btc):
    out = {"note": "ES·NQ 중복성 + ES Δz vs BTC 1h 수익 진단(Config A 대비, 판정 불가입)", "pairs": []}
    for L in LOOKBACKS:
        te, ze = t_by_A["ES"], z_by_A["ES"][L]
        tn, zn = t_by_A["NQ"], z_by_A["NQ"][L]
        common, ie, in_ = np.intersect1d(te, tn, return_indices=True)
        pairs = [(ze[ie[i]], zn[in_[i]]) for i in range(len(common))
                 if np.isfinite(ze[ie[i]]) and np.isfinite(zn[in_[i]])]
        if len(pairs) >= 100:
            a = np.array([x[0] for x in pairs]); b = np.array([x[1] for x in pairs])
            rho, pv = stats.spearmanr(a, b)
            out["pairs"].append({"pair": f"ES vs NQ L{L}", "n": len(pairs), "rho": float(rho), "p": float(pv)})
    kot = k_btc["open_time"].to_numpy(np.int64)
    kop = k_btc["open"].to_numpy(float)
    cont = (np.diff(kot) == 900000)
    cont4 = cont[:-3] & cont[1:-2] & cont[2:-1] & cont[3:]
    base = np.where((kot[:-4] % HOUR == 0) & cont4 & (kot[4:] - kot[:-4] == HOUR))[0]
    ret1h_t = kot[base]
    ret1h_v = (kop[base + 4] - kop[base]) / kop[base]
    for L in LOOKBACKS:
        te, ze = t_by_A["ES"], z_by_A["ES"][L]
        pos = np.searchsorted(ret1h_t, te, side="left")
        pairs = []
        for i in range(len(te)):
            p = pos[i]
            if p < len(ret1h_t) and ret1h_t[p] == te[i] and np.isfinite(ze[i]):
                pairs.append((ze[i], ret1h_v[p]))
        if len(pairs) >= 100:
            a = np.array([x[0] for x in pairs]); b = np.array([x[1] for x in pairs])
            rho, pv = stats.spearmanr(a, b)
            out["pairs"].append({"pair": f"ES Δz L{L} vs BTC 1h fwd ret", "n": len(pairs),
                                 "rho": float(rho), "p": float(pv)})
    return out

def main():
    report = {"prereg": "PREREG_x1r_reverify_2026-10-13.md", "family_per_config": 4,
              "signal": "ES/NQ 1h momentum z (L in 24/120) |z|>=1.2816, REVERSE (h48), BTC/ETH perp execution",
              "endpoint": ENDPOINT, "configs": {}, "quality": {}}
    k = {sym: load_klines(sym) for sym in ["BTCUSDT", "ETHUSDT"]}
    for sym, kk in k.items():
        report["quality"][f"{sym}_bars"] = int(len(kk))
        report["quality"][f"{sym}_range"] = [str(pd.Timestamp(kk["open_time"].iloc[0], unit="ms", tz="UTC")),
                                             str(pd.Timestamp(kk["open_time"].iloc[-1], unit="ms", tz="UTC"))]
    ext = {A: load_yahoo_1h(f"{BASE}/research/r3_yahoo_{A.lower()}.json") for A in ASSETS}
    for A in ASSETS:
        report["quality"][f"{A}_bars"] = int(len(ext[A]))
        report["quality"][f"{A}_range"] = [str(pd.Timestamp(ext[A]["open_time"].iloc[0], unit="ms", tz="UTC")),
                                           str(pd.Timestamp(ext[A]["open_time"].iloc[-1], unit="ms", tz="UTC"))]
    z_by_A, t_by_A = {}, {}
    for A in ASSETS:
        z_by_A[A] = {}
        for L in LOOKBACKS:
            t, z = ext_z(ext[A], L)
            z_by_A[A][L] = z
            t_by_A[A] = t
            report["quality"][f"{A}_z_valid_L{L}"] = int(np.isfinite(z).sum())

    repro_ok = True
    for cname, cfg in CONFIGS.items():
        sym = cfg["sym"]
        q, trades, dropped, seg_info = run_config(k[sym], z_by_A, t_by_A, cfg)
        cells = []
        for A, L in CELLS:
            st = cell_stats(trades[(A, L)])
            st.update({"asset": A, "lookback": L, "horizon": H, "dropped_gap": dropped[(A, L)]})
            cells.append(st)
        adj = holm([c["p"] for c in cells])
        for c, a in zip(cells, adj):
            c["p_holm"] = float(a)
        # Config R 재현 검증
        repro = None
        if cname == "R":
            repro = {}
            for c in cells:
                key = (c["asset"], c["lookback"])
                diff = abs(c["total_bp"] - REPRO_EXPECT[key])
                repro[f"{c['asset']}_L{c['lookback']}"] = {
                    "measured_bp": round(c["total_bp"], 2), "expected_bp": REPRO_EXPECT[key],
                    "abs_diff_bp": round(diff, 2), "ok": bool(diff <= REPRO_TOL_BP)}
            repro_ok = all(v["ok"] for v in repro.values())
        report["configs"][cname] = {"sym": sym, "note": cfg["note"], "segments": seg_info,
                                    "cells": cells, "repro_check": repro}
        print(f"[{cname}] {sym} signal_bars={q['signal_bars_any']} repro_ok={repro if repro else 'n/a'}")

    engine_map = {}
    try:
        xcc = json.load(open(f"{OUT}/x1r_crosscheck_results.json"))
        for cn, cfgc in xcc["configs"].items():
            for c in cfgc["cells"]:
                engine_map[(cn, c["asset"], c["lookback"])] = c.get("net_o10k")
        engine_src = "engine o10k (x1r_crosscheck_results.json)"
    except Exception:
        engine_src = "engine unavailable — flat net16 proxy (정합성 주의)"
    report["engine_source"] = engine_src
    # 게이트 판정 (PREREG §6 사전 고정 — G2a는 엔진($10k) 기준이 정의)
    def cfg_cells(cname):
        return report["configs"][cname]["cells"]
    a_cells = cfg_cells("A"); c_cells = cfg_cells("C")
    g2a = {"raw_pos": sum(1 for c in a_cells if c["total_bp"] is not None and c["total_bp"] > 0),
           "net16_pos": sum(1 for c in a_cells if c["net16"] is not None and c["net16"] > 0),
           "engine10k_pos": sum(1 for c in a_cells if engine_map.get(("A", c["asset"], c["lookback"])) is not None
                                 and engine_map[("A", c["asset"], c["lookback"])] > 0)}
    g2b = {"raw_pos": sum(1 for c in c_cells if c["total_bp"] is not None and c["total_bp"] > 0),
           "net16_pos": sum(1 for c in c_cells if c["net16"] is not None and c["net16"] > 0)}
    g2a_pass = g2a["raw_pos"] >= 3 and g2a["engine10k_pos"] >= 3
    g2b_pass = g2b["raw_pos"] >= 3 and g2b["net16_pos"] >= 2
    g1_pass_cells = []
    for c in a_cells:
        if c["p_holm"] < 0.05 and c["net16"] is not None and c["net16"] > 0:
            twin = [x for x in a_cells if x["lookback"] == c["lookback"] and x["asset"] != c["asset"]]
            if twin and twin[0]["net16"] is not None and twin[0]["net16"] > 0:
                g1_pass_cells.append(f"{c['asset']} L{c['lookback']}")
    g1_pass = len(g1_pass_cells) >= 1
    oos = {"executed": False}
    if repro_ok:
        oos = {"executed": True, "segments": oos_config_a(k["BTCUSDT"], z_by_A, t_by_A, "2024-06-19", "2026-04-20")}
        g3_pass = any(s["net16"] is not None and s["net16"] > 0 and
                      f"{s['asset']} L{s['lookback']}" in g1_pass_cells for s in oos["segments"]) if g1_pass else None
    else:
        g3_pass = None
    if not repro_ok:
        overall = "IMPLEMENTATION_BUG — 중단 후 수정 재실행 (판정 없음)"
    elif g2a_pass and g2b_pass and g1_pass and g3_pass:
        overall = "CONDITIONAL_CANDIDATE — 조건부 후보 보고, 루프 중단(사용자 결정 대기)"
    elif g2a_pass and g2b_pass:
        overall = "STRENGTHENED_PENDING — 관측 강화, 데이터 추가 축적 후 재검증 유지(승격 후보 아님)"
    else:
        overall = "X1R_DEAD — 폐기 종결(자산 일반화 또는 시간 복제 실패)"
    report["verdict"] = {"g2a": {**g2a, "pass": bool(g2a_pass)}, "g2b": {**g2b, "pass": bool(g2b_pass)},
                         "g1": {"pass_cells": g1_pass_cells, "pass": bool(g1_pass)},
                         "g3": {"pass": g3_pass}, "repro_ok": bool(repro_ok), "overall": overall}
    report["oos_a"] = oos
    report["family_precheck"] = family_precheck(ext, z_by_A, t_by_A, k["BTCUSDT"])

    with open(f"{OUT}/x1r_reverify_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    write_md(report)
    print("G2a:", g2a, "G2b:", g2b, "G1:", g1_pass_cells, "G3:", g3_pass)
    print("OVERALL:", overall)

def write_md(report):
    L = ["# X1-R 재검증 결과 — ES/NQ 모멘텀 극단 후 h48 역방향 (사전등록 규격)", "",
         "신호: ES/NQ 1h Δ z (L∈{24,120}) |z|≥1.2816 → **역방향(되돌림)** (BTC/ETHUSDT 퍼페 실행, h48)",
         "구성당 패밀리 4셀 · 구성 내 Holm · 통제: 추종 방향(원 X1) + 무작위 100시드(seed=20261013)",
         f"측정 종점: {report['endpoint']} 00:00 UTC", ""]
    q = report["quality"]
    L.append(f"- ES 바: {q['ES_bars']} ({q['ES_range'][0][:10]}~{q['ES_range'][1][:10]}) / NQ 바: {q['NQ_bars']} ({q['NQ_range'][0][:10]}~{q['NQ_range'][1][:10]})")
    L.append(f"- BTCUSDT 15m: {q['BTCUSDT_bars']}봉 ({q['BTCUSDT_range'][0][:10]}~{q['BTCUSDT_range'][1][:10]}) / ETHUSDT 15m: {q['ETHUSDT_bars']}봉 ({q['ETHUSDT_range'][0][:10]}~{q['ETHUSDT_range'][1][:10]})")
    L.append("")
    for cname in ["R", "A", "C", "B"]:
        cfg = report["configs"][cname]
        L.append(f"## Config {cname} — {cfg['sym']} ({cfg['note']})")
        for seg in cfg["segments"]:
            L.append(f"- 세그먼트 {seg['span']}: 신호봉 {seg['signal_bars']}")
        L.append("")
        L.append("| 자산 | L | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 추종순16(통제) | P(랜덤≥) | 드랍 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
        for c in cfg["cells"]:
            L.append(f"| {c['asset']} | {c['lookback']} | {c['n']} | {c['valid_n']:.1f} | {fmt(c['total_bp'])} | "
                     f"{fmt(c['net12'])} | {fmt(c['net16'])} | {fmt(c['net20'])} | {fmt(c['t'])} | {c['p']:.4f} | "
                     f"{c['p_holm']:.4f} | {fmt(c['follow_net16'])} | {fmt(c['p_ctrl'])} | {c['dropped_gap']} |")
        if cfg["repro_check"]:
            L.append("")
            L.append("### 재현 검증 (원 X1 역방향 셀 값 대비, 허용 ±2bp)")
            for key, v in cfg["repro_check"].items():
                L.append(f"- {key}: 측정 {v['measured_bp']}bp vs 기대 {v['expected_bp']}bp (차이 {v['abs_diff_bp']}bp) — {'OK' if v['ok'] else 'MISMATCH'}")
        L.append("")
    if report["oos_a"]["executed"]:
        L.append("## Config A OOS (후반 40%)")
        for s in report["oos_a"]["segments"]:
            ns = "n/a" if s["net16"] is None else f"{s['net16']:.2f}bp"
            L.append(f"- {s['asset']} L{s['lookback']} — n={s['n']}, 유효n={s['valid_n']:.1f}, 총bp={s['total_bp']:.2f}, 순16={ns}")
        L.append("")
    v = report["verdict"]
    L.append("## 게이트 판정 (PREREG §6 사전 고정)")
    L.append(f"- G2a (Config A 방향·경제성, 엔진 $10k 기준 — 원: {report['engine_source']}): 원시>0 {v['g2a']['raw_pos']}/4, 정액순16>0 {v['g2a']['net16_pos']}/4, **엔진>0 {v['g2a']['engine10k_pos']}/4** → **{'PASS' if v['g2a']['pass'] else 'FAIL'}**")
    L.append(f"- G2b (Config C 복제): 원시>0 {v['g2b']['raw_pos']}/4, 순16>0 {v['g2b']['net16_pos']}/4 → **{'PASS' if v['g2b']['pass'] else 'FAIL'}**")
    L.append(f"- G1 (Config A Holm): 통과 셀 {v['g1']['pass_cells']} → **{'PASS' if v['g1']['pass'] else 'FAIL'}**")
    L.append(f"- G3 (OOS): {'PASS' if v['g3']['pass'] else ('FAIL' if v['g3']['pass'] is not None else 'n/a')}")
    L.append(f"- 재현 검증: {'OK' if v['repro_ok'] else 'MISMATCH'}")
    L.append(f"- **종합: {v['overall']}**")
    L.append("")
    L.append("## 패밀리 사전검증 (정보성)")
    for p in report["family_precheck"]["pairs"]:
        L.append(f"- {p['pair']}: ρ={p['rho']:.4f} (p={p['p']:.3g}, n={p['n']})")
    with open(f"{OUT}/x1r_reverify_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
