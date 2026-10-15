#!/usr/bin/env python3
# X2 매크로 이벤트(FOMC/CPI) 사후반응 추종 진단 — PREREG_x2_macro_events_2026-10-12.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
import json, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
RES = "/home/z/my-project/NEW2_run/research"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
WTYPES = ["FOMC", "CPI"]
WINS = [1, 4]          # 반응 윈도우 W (15m봉) — 사전 고정
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
SEEDS = 100
CELLS = [(w, h) for w in WINS for h in HORIZONS]  # 6셀/구성 → 패밀리 48
BAR = 900000

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "close", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df

import glob
def load_events():
    d = json.load(open(f"{RES}/x2_macro_events.json"))
    fomc = [{"ts": r["ts_utc"], "etype": "FOMC", "date": r["date"]} for r in d["fomc"]]
    cpi = [{"ts": r["ts_utc"], "etype": "CPI", "date": r["date"]} for r in d["cpi"]]
    return fomc, cpi, d["quality"]

def build_trades(k, evs, s_str, e_str):
    """evs: [{ts, etype}] — 사전등록 §3 규격: t0=정확히 발표 ts에 개장하는 봉, R_W=open[t0]→close[t0+W-1],
    진입 open[t0+W], 방향=sign(R_W), 청산 open[t0+W+h]. fail-closed: no_bar/gap/zero/외부 드랍 기록."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    kcl = k["close"].to_numpy(dtype=float)
    kqv = k["qv"].to_numpy(dtype=float)
    in_span = [r for r in evs if s_ms <= r["ts"] < e_ms]
    raw = {(e, w, h): [] for e in WTYPES for (w, h) in CELLS}
    qv = {(e, w, h): [] for e in WTYPES for (w, h) in CELLS}
    dropped = {(e, w, h): {"no_bar": 0, "outside": 0, "gap": 0, "zero": 0} for e in WTYPES for (w, h) in CELLS}
    for r in in_span:
        ei = np.searchsorted(kot, r["ts"], side="left")
        if ei >= len(kot) or kot[ei] != r["ts"] or kot[ei] >= e_ms:
            for c in CELLS: dropped[(r["etype"], *c)]["no_bar"] += 1
            continue
        for w in WINS:
            ri = ei + w - 1
            if ri >= len(kot) or kot[ri] >= e_ms:
                for h in HORIZONS: dropped[(r["etype"], w, h)]["outside"] += 1
                continue
            if (np.diff(kot[ei:ri + 1]) != BAR).any():
                for h in HORIZONS: dropped[(r["etype"], w, h)]["gap"] += 1
                continue
            R = (kcl[ri] - kop[ei]) / kop[ei]
            ent = ei + w
            if ent >= len(kot) or kot[ent] >= e_ms or abs(R) < 1e-12:
                for h in HORIZONS: dropped[(r["etype"], w, h)]["zero" if abs(R) < 1e-12 else "outside"] += 1
                continue
            sgn = 1.0 if R > 0 else -1.0
            for h in HORIZONS:
                xi = ent + h
                if xi >= len(kot) or kot[xi] >= e_ms:
                    dropped[(r["etype"], w, h)]["outside"] += 1
                    continue
                if (np.diff(kot[ent:xi + 1]) != BAR).any():
                    dropped[(r["etype"], w, h)]["gap"] += 1
                    continue
                ret = sgn * (kop[xi] - kop[ent]) / kop[ent]
                raw[(r["etype"], w, h)].append((ret, R, sgn, int(r["ts"])))
                qv[(r["etype"], w, h)].append((kqv[ent], kqv[xi]))
    return in_span, raw, qv, dropped

def cell_stats(trades, h):
    n = len(trades)
    out = {"n": n, "valid_n": n / h}
    if n == 0:
        out.update({"total_bp": None, "net12": None, "net16": None, "net20": None,
                    "t": None, "p": 1.0, "oppose_net16": None, "p_ctrl": None})
        return out
    r = np.array([t[0] for t in trades])
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
        dsign = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((dsign * np.abs(r)).mean() - COST["c16"]) * 1e4)
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

def reaction_profile(k, evs, s_str, e_str):
    """정보성: 반응 방향 분포 + 발표봉(|R_1|) vs 구간 일반 봉 정보량."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    kcl = k["close"].to_numpy(dtype=float)
    in_span = [r for r in evs if s_ms <= r["ts"] < e_ms]
    prof = {}
    all_bar = np.abs((kcl - kop) / kop)
    med = float(np.median(all_bar[(kot >= s_ms) & (kot < e_ms)]))
    for et in WTYPES:
        rets1, ups = [], []
        for r in [x for x in in_span if x["etype"] == et]:
            ei = np.searchsorted(kot, r["ts"], side="left")
            if ei >= len(kot) or kot[ei] != r["ts"] or kot[ei] >= e_ms: continue
            R = (kcl[ei] - kop[ei]) / kop[ei]
            rets1.append(R); ups.append(1.0 if R > 0 else 0.0)
        prof[et] = {"n": len(rets1), "upshare": float(np.mean(ups)) if ups else None,
                    "absR1_med_bp": float(np.median(np.abs(rets1)) * 1e4) if rets1 else None,
                    "median_bar_bp": med * 1e4}
    return prof

def write_md(report, quality):
    L = ["# X2 매크로 이벤트(FOMC/CPI) 사후반응 추종 결과 (사전등록 규격)", "",
         "신호: 발표 직후 W봉 반응 방향 추종, 진입 t0+W open, h봉 홀딩",
         f"시도 수(패밀리): {len(report['cells'])}셀 · Holm 보정 · 통제: 반대(정보성)+무작위 100+구간 드리프트",
         "**§0 사전 고지: 구간당 이벤트 17~28건 → valid_n<30 구조적 PASS 불가(진단 등급, L1 선례)**", "",
         f"- 데이터 품질: FOMC {quality['fomc_n']}건, CPI {quality['cpi_n']}건, 보정 {[(x['old'], x['new']) for x in quality['schedule_overrides']]}, 제외 {quality['dropped']}"]
    L.append("")
    for sym in SYMBOLS:
        for si in range(2):
            key = f"{sym}_span{si+1}"
            qq = report["spans"].get(key, {})
            cnt = qq.get("events_by_type", {})
            L.append(f"## {key} — FOMC {cnt.get('FOMC', 0)}건 / CPI {cnt.get('CPI', 0)}건")
            L.append("| 유형 | W | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 반대순16 | P(랜덤≥) | 드랍(no_bar/out/gap/zero) |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                if c["symbol"] != sym or c["span_idx"] != si: continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                d = c["dropped"]
                L.append(f"| {c['etype']} | {c['W']} | {c['horizon']} | {c['n']} | {c['valid_n']:.1f} | {fmt(c['total_bp'])} | "
                         f"{fmt(c['net12'])} | {fmt(c['net16'])} | {fmt(c['net20'])} | {fmt(c['t'])} | {c['p']:.4f} | "
                         f"{c['p_holm']:.4f} | {fmt(c['oppose_net16'])} | {fmt(c['p_ctrl'])} | "
                         f"{d['no_bar']}/{d['outside']}/{d['gap']}/{d['zero']} |")
            rp = report["reaction_profile"].get(key, {})
            for et in WTYPES:
                p = rp.get(et, {})
                if p.get("upshare") is not None:
                    L.append(f"- [정보성] {et} 반응: 상승 비율 {p['upshare']*100:.0f}% (n={p['n']}), 발표봉 |R| 중앙값 {p['absR1_med_bp']:.1f}bp vs 구간 일반봉 {p['median_bar_bp']:.1f}bp")
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
    L.append(f"- **PASS = {report['verdict']['PASS']}** (§0 구조적 사전 고지대로 valid_n≥30 셀 0개)")
    if report.get("oos", {}).get("executed"):
        pos = [s for s in report["oos"]["segments"] if s["net16"] is not None and s["net16"] > 0]
        for s in report["oos"]["segments"]:
            ns = "n/a" if s["net16"] is None else f"{s['net16']:.2f}bp"
            L.append(f"- OOS {s['symbol']} {s['span']} {s['etype']} W{s['W']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        L.append(f"- OOS 양수 세그먼트: {len(pos)}/{len(report['oos']['segments'])} (기준 ≥2)")
    with open(f"{OUT}/x2_macro_events_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

def main():
    report = {"prereg": "PREREG_x2_macro_events_2026-10-12.md", "family_size": 48,
              "signal": "post-announcement reaction follow: dir=sign(R_W), entry open[t0+W], h in {4,16,48}",
              "spans": {}, "cells": [], "drift_baseline": {}, "reaction_profile": {}}
    fomc, cpi, quality = load_events()
    ev_all = fomc + cpi
    klines = {sym: load_klines(sym) for sym in SYMBOLS}
    for sym in SYMBOLS:
        k = klines[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            in_span, raw, qv, dropped = build_trades(k, ev_all, s_str, e_str)
            key = f"{sym}_span{si+1}"
            report["spans"][key] = {"events_in_span": len(in_span),
                                    "events_by_type": {et: sum(1 for x in in_span if x["etype"] == et) for et in WTYPES}}
            for et in WTYPES:
                for w, h in CELLS:
                    st = cell_stats(raw[(et, w, h)], h)
                    st.update({"symbol": sym, "span_idx": si, "etype": et, "W": w, "horizon": h,
                               "dropped": dropped[(et, w, h)], "qv": qv[(et, w, h)]})
                    report["cells"].append(st)
            report["drift_baseline"][key] = drift_baseline(k, s_str, e_str)
            report["reaction_profile"][key] = reaction_profile(k, ev_all, s_str, e_str)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
        c.pop("qv", None)  # JSON 직렬화용 — 엔진 스크립트는 재계산
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        same = [x for x in report["cells"] if x["etype"] == c["etype"] and x["W"] == c["W"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30]
        verdict["holm_pass_cells"].append({"cell": f"{c['symbol']} {c['span_idx']+1} {c['etype']} W{c['W']} h{c['horizon']}",
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
        combos = sorted({(c["etype"], c["W"], c["horizon"]) for c in passed})
        for sym in SYMBOLS:
            k = klines[sym]
            for si, (s_str, e_str) in enumerate(SPANS[sym]):
                s_ms, e_ms = ms(s_str), ms(e_str)
                cut = s_ms + int((e_ms - s_ms) * 0.6)
                in_span, raw, qv, dropped = build_trades(k, ev_all, s_str, e_str)
                for et, w, h in combos:
                    rets = [t[0] for t in raw[(et, w, h)] if t[3] >= cut]
                    n = len(rets)
                    net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                    oos["segments"].append({"symbol": sym, "span": f"{s_str}~{e_str}", "etype": et,
                                            "W": w, "horizon": h, "n": n, "net16": net16})
        report["oos"] = oos

    with open(f"{OUT}/x2_macro_events_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    ev_in = {k: v["events_by_type"] for k, v in report["spans"].items()}
    print("events:", ev_in)
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:6]
    print("top6 net16:", [(f"{c['etype'][:2]}W{c['W']}h{c['horizon']}", c['symbol'], c['span_idx']+1, round(c['net16'], 2), round(c['valid_n'],1)) for c in ranked])
    up = {k: {et: v[et]["upshare"] for et in WTYPES} for k, v in report["reaction_profile"].items()}
    print("upshare:", {k: {et: (round(v, 2) if v is not None else None) for et, v in d.items()} for k, d in up.items()})

if __name__ == "__main__":
    main()
