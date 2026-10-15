#!/usr/bin/env python3
# L1 선물 만기(납품) 효과 진단 — PREREG_l1_expiry_2026-10-12.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
# §0 사전 고지: 분기 납품 이벤트 ~20건 → valid_n>=30 구조적 불가 → 진단 등급(정보성).
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {  # 잠금 해제 구간 (끝 미포함) — P1/X3/C1/C4/C5/X1/L3/F2X6/X5와 동일
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
WINS = [4, 24]          # b_pre 윈도우 (1h) — 사전 고정
HORIZONS = [4, 16, 48]  # 15m봉 — 사전 고정
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
SEEDS = 100
CELLS = [(W, h) for W in WINS for h in HORIZONS]  # 6셀/구성 → 패밀리 24
HOUR = 3600000
DELIV_HOUR_MS = 8 * HOUR  # 납품 08:00 UTC

def load_1h(path_glob):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(path_glob))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    for c in ["open_time", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df[["open_time", "close"]]

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df

def close_at_1h(df, ts):
    """정확히 ts인 1h close 반환(없으면 NaN) — 결측 보간 금지(fail-closed)."""
    i = np.searchsorted(df["open_time"].to_numpy(np.int64), ts)
    t = df["open_time"].to_numpy(np.int64)
    if i < len(t) and t[i] == ts:
        return float(df["close"].to_numpy(float)[i])
    return np.nan

def build_events(sym):
    """계약 파일 → 이벤트 목록. t0 = 납품일 08:00 UTC (사전 고정 규칙)."""
    spot = load_1h(f"{BASE}/spot1h/{sym}-1h.csv")
    events, quality = [], []
    for f in sorted(glob.glob(f"{BASE}/qfut/{sym}_*-1h.csv")):
        name = os.path.basename(f).replace("-1h.csv", "")
        d = name.split("_")[1]
        t0 = ms(f"20{d[:2]}-{d[2:4]}-{d[4:6]}") + DELIV_HOUR_MS
        cdf = load_1h(f)
        last_ts = int(cdf["open_time"].to_numpy(np.int64)[-1])
        quality.append({"contract": name, "t0": str(pd.Timestamp(t0, unit="ms", tz="UTC")),
                        "last_bar": str(pd.Timestamp(last_ts, unit="ms", tz="UTC")),
                        "last_bar_match_t0": bool(last_ts == t0), "bars": int(len(cdf))})
        # 구간 배정
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            if ms(s_str) <= t0 < ms(e_str):
                ev = {"contract": name, "symbol": sym, "span_idx": si, "t0": t0}
                for W in WINS:
                    near_c = close_at_1h(cdf, t0 - W * HOUR)
                    spot_c = close_at_1h(spot, t0 - W * HOUR)
                    b = (near_c / spot_c - 1.0) if (np.isfinite(near_c) and np.isfinite(spot_c) and spot_c > 0) else np.nan
                    ev[f"b_pre_{W}h"] = b
                events.append(ev)
    return events, quality, spot

def execute(k, kot, kop, t0, sgn, h, e_ms):
    ei = np.searchsorted(kot, t0, side="left")
    if ei >= len(kot) or kot[ei] >= e_ms:  # 진입봉: t0 이후 첫 15m봉(같은 날)
        return None
    xi = ei + h
    if xi >= len(kot) or kot[xi] >= e_ms:
        return None
    if (np.diff(kot[ei:xi + 1]) != 900000).any():
        return "gap"
    return sgn * (kop[xi] - kop[ei]) / kop[ei], int(kot[ei])

def run_events(k, events, e_ms):
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    trades = {c: [] for c in CELLS}
    dropped = {c: {"gap": 0, "no_entry": 0, "outside": 0} for c in CELLS}
    for ev in events:
        t0 = ev["t0"]
        for W in WINS:
            b = ev.get(f"b_pre_{W}h", np.nan)
            if not np.isfinite(b) or b == 0.0:
                for h in HORIZONS:
                    dropped[(W, h)]["no_entry"] += 1
                continue
            sgn = -1.0 if b > 0 else 1.0  # fade: 프리미엄→롱, 디스카운트→숏
            for h in HORIZONS:
                r = execute(k, kot, kop, t0, sgn, h, e_ms)
                if r == "gap":
                    dropped[(W, h)]["gap"] += 1
                elif r is None:
                    dropped[(W, h)]["outside"] += 1
                else:
                    trades[(W, h)].append((r[0], t0, float(b)))
    return trades, dropped

def session_baseline(k, s_str, e_str):
    """세션 통제(정보성): 비납품 금요일 08:00 UTC 무조건 롱, 동일 실행 규칙."""
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    s_ms, e_ms = ms(s_str), ms(e_str)
    expiries = set()
    for f in glob.glob(f"{BASE}/qfut/*_*-1h.csv"):
        d = os.path.basename(f).split("_")[1].replace("-1h.csv", "")
        expiries.add(ms(f"20{d[:2]}-{d[2:4]}-{d[4:6]}"))
    out = {}
    day = pd.Timestamp(s_str, tz="UTC").normalize()
    end = pd.Timestamp(e_str, tz="UTC")
    while day < end:
        if day.dayofweek == 4:  # Friday
            t0 = int(day.timestamp() * 1000) + DELIV_HOUR_MS
            if not any(abs(t0 - x) < 24 * HOUR for x in expiries):  # 납품 주간 금요일 제외
                for h in HORIZONS:
                    r = execute(k, kot, kop, t0, 1.0, h, e_ms)
                    if r not in ("gap", None) and isinstance(r, tuple):
                        out.setdefault(h, []).append(r[0])
        day += pd.Timedelta(days=1)
    return {h: {"n": len(v), "total_bp": float(np.mean(v) * 1e4)} for h, v in out.items()}

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
        rand_means.append(((d * r).mean() - COST["c16"]) * 1e4)
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

def family_precheck(events, spot):
    """정보성: b_pre 값 vs 동일 시각 퍼페 프리미엄 인덱스 스피어만 (납품계약 베이시스 ≈ 퍼페 프리미엄?)."""
    out = {"note": "납품계약 베이시스(b_pre)와 퍼페 프리미엄 인덱스(X3/C1 패밀리) 정보 동질성 확인. 표본 ~20으로 파워 낮음(사전 고지).",
           "pairs": []}
    for sym in SYMBOLS:
        cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
        files = sorted(glob.glob(f"{BASE}/premium/{sym}-15m-*.csv"))
        if not files:
            continue
        p = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
        p = p[pd.to_numeric(p["open_time"], errors="coerce").notna()]
        p["open_time"] = p["open_time"].astype(np.int64)
        p = p.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
        pt = p["open_time"].to_numpy(np.int64)
        pc = p["close"].to_numpy(float)  # 이미 프리미엄율(예: -0.000425 = -4.25bp) — X3 로더 관행
        for W in WINS:
            xs, ys = [], []
            for ev in events:
                if ev.get("symbol") != sym:
                    continue
                b = ev.get(f"b_pre_{W}h", np.nan)
                if not np.isfinite(b):
                    continue
                ts = ev["t0"] - W * HOUR
                j = np.searchsorted(pt, ts)
                if j < len(pt) and pt[j] == ts and np.isfinite(pc[j]):
                    xs.append(b); ys.append(pc[j])
            if len(xs) >= 8:
                rho, pv = stats.spearmanr(xs, ys)
                out["pairs"].append({"symbol": sym, "W": W, "n": len(xs),
                                     "spearman_rho_vs_perp_premium": float(rho), "p": float(pv)})
    return out

def write_md(report, quality):
    L = ["# L1 선물 만기(납품) 효과 결과 (사전등록 규격 — 진단 등급)", "",
         "신호: 납품 직전 납품계약 베이시스 b_pre(W∈{4,24}h) 부호 **fade** (프리미엄→납품 후 롱 / 디스카운트→숏)",
         f"이벤트: 분기 납품일 08:00 UTC · 시도 수(패밀리): {len(report['cells'])}셀 · Holm 보정 · 반대/무작위 통제",
         "**§0 사전 고지: 이벤트 ~20건 → valid_n≥30 구조적 불가 → 승격 후보 아님(효과크기 진단)**", ""]
    for sym in SYMBOLS:
        for si in range(2):
            key = f"{sym}_span{si+1}"
            qq = report["spans"].get(key, {})
            L.append(f"## {key} — 이벤트 {qq.get('events', 0)}건, 거래 성립(셀별 최대) {qq.get('max_trades', 0)}건")
            L.append("| W | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 반대순16 | P(랜덤≥) | 드랍(gap/no) |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                if c["symbol"] != sym or c["span_idx"] != si: continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                L.append(f"| {c['W']} | {c['horizon']} | {c['n']} | {c['valid_n']:.1f} | {fmt(c['total_bp'])} | "
                         f"{fmt(c['net12'])} | {fmt(c['net16'])} | {fmt(c['net20'])} | {fmt(c['t'])} | {c['p']:.4f} | "
                         f"{c['p_holm']:.4f} | {fmt(c['oppose_net16'])} | {fmt(c['p_ctrl'])} | "
                         f"{c['dropped']['gap']}/{c['dropped']['no_entry']} |")
            sb = report["session_baseline"].get(key, {})
            L.append(f"- 세션 통제(비납품 금요일 08:00 UTC 롱): " +
                     ", ".join(f"h{h}: n={v['n']}, 총bp={v['total_bp']:.2f}" for h, v in sorted(sb.items())))
            L.append("")
    L.append("## 판정 (사전 고정 규칙)")
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    L.append(f"- Holm p<0.05 셀: {len(passed)}개")
    L.append(f"- valid_n≥30 셀: {sum(1 for c in report['cells'] if c['valid_n'] >= 30)}개 (§0 예상대로 0개면 구조적 불가 확정)")
    L.append(f"- **PASS = {report['verdict']['PASS']}** (사전 고지대로 승격 불가 구조)")
    L.append("")
    L.append("## 진단 판독 (정보성)")
    L.append(f"- fade 방향 총bp 중앙값: {report['diagnosis']['median_total_bp']:.2f}bp · 반대(압력 추종) 우세 셀: {report['diagnosis']['oppose_dominant_cells']}/24")
    L.append(f"- 엔진 비용 대비: 최고 셀 총bp {report['diagnosis']['best_total_bp']:.2f}bp (엔진비용 $10k ~33-37bp 대비)")
    fp_ = report.get("family_precheck", {})
    L.append("")
    L.append("## 패밀리 사전검증 (정보성)")
    L.append(f"- {fp_['note']}")
    for p in fp_["pairs"]:
        L.append(f"- {p['symbol']} W{p['W']}: b_pre vs 퍼페 프리미엄 스피어만 ρ={p['spearman_rho_vs_perp_premium']:.4f} (p={p['p']:.3g}, n={p['n']})")
    with open(f"{OUT}/l1_expiry_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

def main():
    report = {"prereg": "PREREG_l1_expiry_2026-10-12.md", "family_size": 24,
              "signal": "delivery-day basis-sign FADE (b_pre W in 4/24h, t0=delivery 08:00 UTC)",
              "spans": {}, "cells": [], "session_baseline": {}}
    quality = {"contract_check": {}, "events": {}}
    klines = {}
    all_events = {}
    for sym in SYMBOLS:
        klines[sym] = load_klines(sym)
        events, cq, spot = build_events(sym)
        quality["contract_check"][sym] = cq
        all_events[sym] = events
        match = sum(1 for x in cq if x["last_bar_match_t0"])
        quality[f"{sym}_last_bar_match"] = f"{match}/{len(cq)}"
        for si in range(2):
            evs = [e for e in events if e["span_idx"] == si]
            quality["events"][f"{sym}_span{si+1}"] = [
                {"contract": e["contract"], "t0": str(pd.Timestamp(e["t0"], unit="ms", tz="UTC")),
                 **{f"b_pre_{W}h": (None if not np.isfinite(e.get(f"b_pre_{W}h", np.nan)) else float(e[f"b_pre_{W}h"])) for W in WINS}}
                for e in evs]
    for sym in SYMBOLS:
        k = klines[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            evs = [e for e in all_events[sym] if e["span_idx"] == si]
            trades, dropped = run_events(k, evs, ms(e_str))
            key = f"{sym}_span{si+1}"
            report["spans"][key] = {"events": len(evs),
                                    "max_trades": max(len(trades[c]) for c in CELLS)}
            for W, h in CELLS:
                st = cell_stats([x[0] for x in trades[(W, h)]], h)
                st.update({"symbol": sym, "span_idx": si, "W": W, "horizon": h,
                           "dropped": dropped[(W, h)]})
                report["cells"].append(st)
            report["session_baseline"][key] = session_baseline(k, s_str, e_str)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05 and c["valid_n"] >= 30]
    verdict = {"holm_pass_cells": len([c for c in report["cells"] if c["p_holm"] < 0.05]),
               "PASS": bool(passed)}
    report["verdict"] = verdict
    tv = [c["total_bp"] for c in report["cells"] if c["total_bp"] is not None]
    opp_dom = sum(1 for c in report["cells"] if c["total_bp"] is not None
                  and c["oppose_net16"] is not None
                  and c["oppose_net16"] + COST["c16"] > c["total_bp"])
    best = max(tv) if tv else float("nan")
    report["diagnosis"] = {"median_total_bp": float(np.median(tv)) if tv else float("nan"),
                           "oppose_dominant_cells": opp_dom, "best_total_bp": float(best)}
    fam = family_precheck(all_events["BTCUSDT"] + all_events["ETHUSDT"], None)  # 내부에서 심볼 필터
    report["family_precheck"] = fam
    with open(f"{OUT}/l1_expiry_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", verdict["holm_pass_cells"],
          "| valid_n>=30 cells =", sum(1 for c in report["cells"] if c["valid_n"] >= 30))
    print("events:", {k: v["events"] for k, v in report["spans"].items()})
    print("diagnosis:", report["diagnosis"])
    print("family:", [(p["symbol"], p["W"], round(p["spearman_rho_vs_perp_premium"], 4)) for p in fam["pairs"]])
    for sym in SYMBOLS:
        print(sym, "last_bar_match:", quality[f"{sym}_last_bar_match"])

if __name__ == "__main__":
    main()
