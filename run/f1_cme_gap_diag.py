#!/usr/bin/env python3
# F1 CME 주말 갭 진단 — PREREG_f1_cme_gap_2026-10-12.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
CME_SYM = {"BTCUSDT": "BTC_F", "ETHUSDT": "ETH_F"}
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {  # 잠금 해제 구간 (끝 미포함) — X3·P1과 동일
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
BUCKETS = {"G50": 0.5, "G100": 1.0}   # |gap|% 절대 임계값 (고정)
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
ENTRY_DELAY_MAX_MS = 1_800_000  # 30분 (사전 고정)
SEEDS = 100

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

def load_cme(sym):
    j = json.load(open(f"{BASE}/cme/{CME_SYM[sym]}_1d.json"))
    res = j["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"stamp_s": ts, "open": q["open"], "close": q["close"]})
    df = df.dropna(subset=["open", "close"]).drop_duplicates("stamp_s").sort_values("stamp_s").reset_index(drop=True)
    df["stamp_ms"] = (df["stamp_s"] * 1000).astype(np.int64)
    df["dow"] = pd.to_datetime(df["stamp_ms"], unit="ms", utc=True).dt.dayofweek  # 0=Mon..6=Sun
    return df

def weekend_events(cdf):
    """연속 일봉 간 달력차 ≥2일 & 현재 봉이 일/월 & 직전 봉이 금 → 주말 갭 이벤트.
    signal_instant: 월 스탬프 = 재개(일 17:00 CT) 6시간 후 / 일 스탬프 = 재개 18시간 전 (midnight ET 관례)."""
    evs = []
    conv = {"monday_stamped": 0, "sunday_stamped": 0, "dropped_prev_not_fri": 0}
    for i in range(1, len(cdf)):
        prev, cur = cdf.iloc[i - 1], cdf.iloc[i]
        delta_s = cur["stamp_s"] - prev["stamp_s"]
        if delta_s < 2 * 86400: continue
        dow = int(cur["dow"])
        if dow == 6:      # Sunday-stamped
            sig_ms = int(cur["stamp_ms"]) + 18 * 3600 * 1000
            conv["sunday_stamped"] += 1
        elif dow == 0:    # Monday-stamped
            sig_ms = int(cur["stamp_ms"]) - 6 * 3600 * 1000
            conv["monday_stamped"] += 1
        else:
            continue  # 비주말 데이터 구멍(휴일 등) — 주말 갭 아님
        if int(prev["dow"]) != 4:  # 직전 봉이 금요일 아님 → 폐기(카운트)
            conv["dropped_prev_not_fri"] += 1; continue
        gap_pct = (cur["open"] - prev["close"]) / prev["close"] * 100.0
        evs.append({"sig_ms": sig_ms, "gap_pct": gap_pct})
    return evs, conv

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(k, events, s_str, e_str, cut_ms=None, end_ms=None):
    """되돌림 방향 고정: gap>0 → 숏, gap<0 → 롱. cut_ms 지정 시 그 이후 신호만(=OOS)."""
    s_ms, e_ms = ms(s_str), ms(e_str) if end_ms is None else end_ms
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    trades = {k2: [] for k2 in [(b, h) for b in BUCKETS for h in HORIZONS]}
    dropped_delay = {b: 0 for b in BUCKETS}
    dropped_gap = {k2: 0 for k2 in trades}
    for ev in events:
        t = ev["sig_ms"]
        if not (s_ms <= t < e_ms): continue
        if cut_ms is not None and t < cut_ms: continue  # OOS: cut 이후만
        if cut_ms is None and e_ms is not None and t >= e_ms: pass
        ei = np.searchsorted(kot, t, side="left")
        if ei >= len(kot) or kot[ei] - t > ENTRY_DELAY_MAX_MS: 
            for b in BUCKETS: dropped_delay[b] += 1
            continue
        if kot[ei] < t or kot[ei] >= e_ms: continue
        for b, thr in BUCKETS.items():
            if abs(ev["gap_pct"]) < thr: continue
            direction = -1.0 if ev["gap_pct"] > 0 else 1.0  # 되돌림 고정
            for h in HORIZONS:
                xi = ei + h
                if xi >= len(kot) or kot[xi] >= e_ms: continue
                if (np.diff(kot[ei:xi + 1]) != 900000).any():
                    dropped_gap[(b, h)] += 1; continue
                ret = direction * (kop[xi] - kop[ei]) / kop[ei]
                trades[(b, h)].append(ret)
    return trades, dropped_delay, dropped_gap

def cell_stats(rets, h):
    n = len(rets)
    out = {"n": n, "valid_n": n}  # 주간 이벤트: 겹침 없음 → valid_n = n (사전 고정)
    if n == 0:
        out.update({"total_bp": None, "net12": None, "net16": None, "net20": None,
                    "t": None, "p": 1.0, "follow_net16": None, "p_ctrl": None})
        return out
    r = np.array(rets)
    out["total_bp"] = float(r.mean() * 1e4)
    for k, c in COST.items():
        out[f"net{k[1:]}"] = float((r.mean() - c) * 1e4)
    sd = r.std(ddof=1)
    t = (r.mean() - COST["c16"]) / (sd / np.sqrt(n)) if sd > 0 else 0.0
    out["t"] = float(t)
    out["p"] = float(2 * stats.t.sf(abs(t), df=max(n - 1, 1)))
    out["follow_net16"] = float((-r.mean() - COST["c16"]) * 1e4)
    rng = np.random.default_rng(20261012)
    rand_means = [((rng.choice([-1.0, 1.0], size=n) * r).mean() - COST["c16"]) * 1e4 for _ in range(SEEDS)]
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def main():
    report = {"prereg": "PREREG_f1_cme_gap_2026-10-12.md", "family_size": 24,
              "signal": "CME weekend gap >= 0.5%/1.0% → reversal (gap up→short, gap down→long)", "spans": {}, "cells": []}
    quality = {}
    klines, events_all, conv_all = {}, {}, {}
    for sym in SYMBOLS:
        klines[sym] = load_klines(sym)
        cdf = load_cme(sym)
        quality[f"{sym}_cme_bars"] = int(len(cdf))
        evs, conv = weekend_events(cdf)
        events_all[sym] = evs; conv_all[sym] = conv
        quality[f"{sym}_events_total"] = len(evs)
        quality[f"{sym}_convention"] = conv
        gaps = np.array([e["gap_pct"] for e in evs])
        if len(gaps):
            quality[f"{sym}_gap_pct_median"] = float(np.median(np.abs(gaps)))
            quality[f"{sym}_gap_pct_p90"] = float(np.percentile(np.abs(gaps), 90))
        delays = []
        kot = klines[sym]["open_time"].to_numpy(dtype=np.int64)
        for e in evs:
            i = np.searchsorted(kot, e["sig_ms"], side="left")
            if i < len(kot): delays.append(kot[i] - e["sig_ms"])
        d = np.array(delays)
        quality[f"{sym}_entry_delay_ms_median"] = float(np.median(d)) if len(d) else None
        quality[f"{sym}_entry_delay_ms_max"] = float(d.max()) if len(d) else None
        quality[f"{sym}_entry_delay_over_30min"] = int((d > ENTRY_DELAY_MAX_MS).sum()) if len(d) else 0
    for sym in SYMBOLS:
        k = klines[sym]; evs = events_all[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            in_span = [e for e in evs if s_ms <= e["sig_ms"] < e_ms]
            q = {"events_in_span": len(in_span),
                 "ev_g50": sum(1 for e in in_span if abs(e["gap_pct"]) >= 0.5),
                 "ev_g100": sum(1 for e in in_span if abs(e["gap_pct"]) >= 1.0)}
            key = f"{sym}_span{si+1}_{s_str}~{e_str}"
            report["spans"][key] = q
            quality[key] = q
            trades, dd, dg = run_span(k, evs, s_str, e_str)
            for b in BUCKETS:
                for h in HORIZONS:
                    st = cell_stats(trades[(b, h)], h)
                    st.update({"symbol": sym, "span": f"{s_str}~{e_str}", "bucket": b, "horizon": h,
                               "dropped_delay": dd[b], "dropped_gap": dg[(b, h)]})
                    report["cells"].append(st)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        same = [x for x in report["cells"] if x["bucket"] == c["bucket"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["n"] >= 15]
        verdict["holm_pass_cells"].append({"cell": f"{c['symbol']} {c['span']} {c['bucket']} h{c['horizon']}",
                                           "p_holm": c["p_holm"], "net16": c["net16"], "n": c["n"],
                                           "config_pos_of_4": len(pos)})
    if verdict["holm_pass_cells"]:
        best = max(verdict["holm_pass_cells"], key=lambda x: x["config_pos_of_4"])
        verdict["PASS"] = best["config_pos_of_4"] >= 3
        verdict["best_config"] = best
    report["verdict"] = verdict

    # OOS 규칙 (사전 고정): Holm 통과 셀 있을 때만 — 각 심볼-구간 시간순 60/40, 동일 설정.
    oos = {"executed": False, "segments": []}
    if passed:
        oos["executed"] = True
        combos = sorted({(c["bucket"], c["horizon"]) for c in passed})
        for sym in SYMBOLS:
            k = klines[sym]; evs = events_all[sym]
            for si, (s_str, e_str) in enumerate(SPANS[sym]):
                s_ms, e_ms = ms(s_str), ms(e_str)
                cut = s_ms + int((e_ms - s_ms) * 0.6)
                tr, _, _ = run_span(k, evs, s_str, e_str, cut_ms=cut)
                for b, h in combos:
                    n = len(tr[(b, h)])
                    net16 = float((np.mean(tr[(b, h)]) - 0.0016) * 1e4) if n else None
                    oos["segments"].append({"symbol": sym, "span": f"{s_str}~{e_str}", "bucket": b,
                                            "horizon": h, "n": n, "net16": net16})
        report["oos"] = oos

    with open(f"{OUT}/f1_cme_gap_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} n={v['n']} pos={v['config_pos_of_4']}/4")
    if oos["executed"]:
        pos = sum(1 for s in oos["segments"] if s["net16"] is not None and s["net16"] > 0)
        print(f"OOS positive segments: {pos}/{len(oos['segments'])}")

def write_md(report, quality):
    L = ["# F1 CME 주말 갭 결과 (사전등록 규격)", "",
         "신호: 주말 갭 |gap| ≥ 0.5%/1.0% → 되돌림 (갭 상승→숏, 갭 하락→롱) · 진입 지연 ≤30분 fail-closed",
         f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · valid_n = n (주간 이벤트 비겹침, 사전 고정)", ""]
    L.append("## 데이터 품질")
    for k, v in quality.items():
        if "_span" not in k:
            L.append(f"- {k}: {v}")
    L.append("")
    for sym in SYMBOLS:
        for si in range(2):
            key = [k for k in report["spans"] if k.startswith(f"{sym}_span{si+1}_")][0]
            qq = report["spans"][key]
            L.append(f"## {key} — 이벤트 {qq['events_in_span']} (G50 {qq['ev_g50']} / G100 {qq['ev_g100']})")
            L.append("| 버킷 | h | n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 추종순16 | P(랜덤≥) | 지연드랍 | 갭드랍 |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                spans_list = SPANS[sym]
                if c["symbol"] != sym or c["span"] != f"{spans_list[si][0]}~{spans_list[si][1]}": continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                L.append(f"| {c['bucket']} | {c['horizon']} | {c['n']} | {fmt(c['total_bp'])} | "
                         f"{fmt(c['net12'])} | {fmt(c['net16'])} | {fmt(c['net20'])} | {fmt(c['t'])} | {c['p']:.4f} | "
                         f"{c['p_holm']:.4f} | {fmt(c['follow_net16'])} | {fmt(c['p_ctrl'])} | {c['dropped_delay']} | {c['dropped_gap']} |")
            L.append("")
    L.append("## 판정 (사전 고정 규칙)")
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    L.append(f"- Holm p<0.05 셀: {len(passed)}개")
    for v in report["verdict"]["holm_pass_cells"]:
        L.append(f"  - {v['cell']} — p_holm={v['p_holm']:.4f}, 순16={v['net16']:.2f}bp, n={v['n']}, 동일설정 4구간 중 양수 {v['config_pos_of_4']}개")
    L.append(f"- **PASS = {report['verdict']['PASS']}**")
    if report.get("oos", {}).get("executed"):
        L.append("")
        L.append("## OOS (각 구간 하위 40%, 사전 고정 규칙)")
        for s in report["oos"]["segments"]:
            ns = "n/a" if s["net16"] is None else f"{s['net16']:.2f}bp"
            L.append(f"- {s['symbol']} {s['span']} {s['bucket']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        pos = sum(1 for s in report["oos"]["segments"] if s["net16"] is not None and s["net16"] > 0)
        L.append(f"- OOS 양수 세그먼트: {pos}/{len(report['oos']['segments'])} (승격 자격 기준 ≥2)")
    with open(f"{OUT}/f1_cme_gap_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
