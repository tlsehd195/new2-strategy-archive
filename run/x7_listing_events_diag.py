#!/usr/bin/env python3
# X7 상장 예고 공지 이벤트 진단 — PREREG_x7_listing_events_2026-10-12.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
import json, glob, os, re
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
RE_A = re.compile(r"Will List|Will Launch", re.I)
RE_B = re.compile(r"Will Delist|Delisting", re.I)
DELS = [0, 4, 16]     # 진입 지연 (15m봉) — 사전 고정
HORIZONS = [4, 16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
SEEDS = 100
CLUSTER_MS = 4 * 3600000   # 4h 미만 연속 공지 dedup — 사전 고정
CELLS = [(d, h) for d in DELS for h in HORIZONS]  # 9셀/구성 → 패밀리 36
BAR = 900000

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

def load_events():
    d = json.load(open(f"{RES}/x7_listing_announcements.json"))
    arts = d["articles"]
    a_cls, b_cls, missing = [], [], 0
    for x in arts:
        rd = x.get("releaseDate")
        if not rd:
            missing += 1; continue
        t = x.get("title") or ""
        rec = {"id": x.get("id"), "ts": int(rd), "title": t}
        if RE_A.search(t): a_cls.append(rec)
        elif RE_B.search(t): b_cls.append(rec)
    # 클래스 A dedup: ts 정렬 후 4h 미만 간격은 첫 것만
    a_cls.sort(key=lambda r: r["ts"])
    kept, collapsed = [], 0
    for r in a_cls:
        if kept and r["ts"] - kept[-1]["ts"] < CLUSTER_MS:
            collapsed += 1; continue
        kept.append(r)
    q = {"articles_total": len(arts), "missing_releaseDate": missing,
         "class_A_raw": len(a_cls), "class_A_kept": len(kept), "class_A_collapsed": collapsed,
         "class_B": len(b_cls)}
    return kept, b_cls, q

def execute(kot, kop, t0, h, e_ms):
    ei = np.searchsorted(kot, t0, side="left")
    if ei >= len(kot) or kot[ei] >= e_ms:
        return None
    xi = ei + h
    if xi >= len(kot) or kot[xi] >= e_ms:
        return None
    if (np.diff(kot[ei:xi + 1]) != BAR).any():
        return "gap"
    return (kop[xi] - kop[ei]) / kop[ei]

def run_events(k, evs, s_str, e_str):
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    in_span = [r for r in evs if s_ms <= r["ts"] < e_ms]
    trades = {c: [] for c in CELLS}
    dropped = {c: {"gap": 0, "outside": 0} for c in CELLS}
    adopted = 0
    for r in in_span:
        hit_any = False
        for d in DELS:
            t0 = r["ts"] + d * BAR
            for h in HORIZONS:
                out = execute(kot, kop, t0, h, e_ms)
                if out == "gap":
                    dropped[(d, h)]["gap"] += 1
                elif out is None:
                    dropped[(d, h)]["outside"] += 1
                else:
                    trades[(d, h)].append((out, r["ts"]))
                    hit_any = True
        adopted += int(hit_any)
    q = {"events_in_span": len(in_span), "events_adopted_any_cell": adopted}
    return q, trades, dropped

def drift_baseline(k, s_str, e_str):
    """정보성: 구간 전체 96봉 간격 무조건 롱."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = k["open_time"].to_numpy(dtype=np.int64)
    kop = k["open"].to_numpy(dtype=float)
    out = {}
    for h in HORIZONS:
        rets = []
        i = np.searchsorted(kot, s_ms)
        while i + h < len(kot) and kot[i + h] < e_ms:
            r = execute(kot, kop, int(kot[i]), h, e_ms)
            if isinstance(r, float):
                rets.append(r)
            i += 96
        out[h] = {"n": len(rets), "total_bp": float(np.mean(rets) * 1e4) if rets else None}
    return out

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

def fng_precheck(events):
    """정보성: 클래스 A 공지일의 F&G 평균 vs 전체 평균 (리스크온 군집 확인)."""
    f = glob.glob(f"{RES}/fng_hist*.json") + glob.glob(f"{RES}/*fng*.json")
    if not f:
        return {"note": "F&G 파일 미발견 — 스킵"}
    try:
        raw = json.load(open(sorted(f)[0]))
        data = raw.get("data") if isinstance(raw, dict) else raw
        days = {}
        for x in data:
            v = x.get("value"); ts = x.get("timestamp")
            if v is None or ts is None: continue
            ts = int(ts)
            if ts > 1e12: ts //= 1000
            days[ts // 86400] = int(v)
    except Exception as e:
        return {"note": f"F&G 로드 실패: {e}"}
    ev_days = [r["ts"] // 1000 // 86400 for r in events]
    vals = [days[d] for d in ev_days if d in days]
    allv = list(days.values())
    if len(vals) < 30:
        return {"note": f"매칭 {len(vals)}건 < 30 — 파워 미달, 수치만 기록"}
    t, p = stats.ttest_1samp(vals, np.mean(allv))
    return {"event_day_fg_mean": float(np.mean(vals)), "overall_fg_mean": float(np.mean(allv)),
            "n_event_days_matched": len(vals), "t": float(t), "p": float(p),
            "note": "공지일 F&G가 구간 평균보다 높으면 리스크온 군집 확인(보정 아님, 정보성)"}

def write_md(report, quality):
    L = ["# X7 상장 예고 공지 이벤트 결과 (사전등록 규격)", "",
         "신호: Will List/Launch 공지(releaseDate) 직후 **롱 고정**, 진입 지연 δ∈{0,4,16}봉 × h∈{4,16,48}봉",
         f"시도 수(패밀리): {len(report['cells'])}셀 · Holm 보정 · 통제: 반대(정보성)+무작위 100+구간 드리프트",
         "**§6 사전 고지: h48 셀은 n≥1,440 요건 vs 이벤트 882건(구간 내 더 적음) → 구조적 PASS 불가(L1 선례)**", ""]
    L.append(f"- 이벤트: 원본 {quality['class_A_raw']}건 → 4h dedup 후 {quality['class_A_kept']}건(축소 {quality['class_A_collapsed']}), 클래스B {quality['class_B']}건, releaseDate 결측 {quality['missing_releaseDate']}건")
    L.append("")
    for sym in SYMBOLS:
        for si in range(2):
            key = f"{sym}_span{si+1}"
            qq = report["spans"].get(key, {})
            L.append(f"## {key} — 구간 내 공지 {qq.get('events_in_span', 0)}건, 채택(any cell) {qq.get('events_adopted_any_cell', 0)}건")
            L.append("| δ | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 반대순16 | P(랜덤≥) | 드랍(gap/out) |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                if c["symbol"] != sym or c["span_idx"] != si: continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                L.append(f"| {c['delta']} | {c['horizon']} | {c['n']} | {c['valid_n']:.1f} | {fmt(c['total_bp'])} | "
                         f"{fmt(c['net12'])} | {fmt(c['net16'])} | {fmt(c['net20'])} | {fmt(c['t'])} | {c['p']:.4f} | "
                         f"{c['p_holm']:.4f} | {fmt(c['oppose_net16'])} | {fmt(c['p_ctrl'])} | "
                         f"{c['dropped']['gap']}/{c['dropped']['outside']} |")
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
        L.append("")
        L.append("## OOS (각 구간 하위 40%)")
        pos = [s for s in report["oos"]["segments"] if s["net16"] is not None and s["net16"] > 0]
        for s in report["oos"]["segments"]:
            ns = "n/a" if s["net16"] is None else f"{s['net16']:.2f}bp"
            L.append(f"- {s['symbol']} {s['span']} δ{s['delta']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        L.append(f"- OOS 양수 세그먼트: {len(pos)}/{len(report['oos']['segments'])} (기준 ≥2)")
    fp = report.get("fng_precheck", {})
    L.append("")
    L.append("## 패밀리 사전검증 (정보성) — 공지일 F&G")
    if "note" in fp and "event_day_fg_mean" not in fp:
        L.append(f"- {fp['note']}")
    else:
        L.append(f"- 공지일 F&G 평균 {fp.get('event_day_fg_mean'):.1f} vs 전체 {fp.get('overall_fg_mean'):.1f} "
                 f"(t={fp.get('t'):.2f}, p={fp.get('p'):.3g}, n={fp.get('n_event_days_matched')})")
    with open(f"{OUT}/x7_listing_events_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

def main():
    report = {"prereg": "PREREG_x7_listing_events_2026-10-12.md", "family_size": 36,
              "signal": "Will List/Launch announcement -> LONG, delay d in {0,4,16} bars, h in {4,16,48}",
              "spans": {}, "cells": [], "drift_baseline": {}}
    events, b_cls, qev = load_events()
    quality = dict(qev)
    quality["class_B_records"] = [{"ts": r["ts"], "title": r["title"]} for r in b_cls]
    klines = {}
    for sym in SYMBOLS:
        klines[sym] = load_klines(sym)
    for sym in SYMBOLS:
        k = klines[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            q, trades, dropped = run_events(k, events, s_str, e_str)
            key = f"{sym}_span{si+1}"
            report["spans"][key] = q
            for d, h in CELLS:
                st = cell_stats([x[0] for x in trades[(d, h)]], h)
                st.update({"symbol": sym, "span_idx": si, "delta": d, "horizon": h,
                           "dropped": dropped[(d, h)]})
                report["cells"].append(st)
            report["drift_baseline"][key] = drift_baseline(k, s_str, e_str)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        same = [x for x in report["cells"] if x["delta"] == c["delta"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30]
        verdict["holm_pass_cells"].append({"cell": f"{c['symbol']} {c['span_idx']+1} δ{c['delta']} h{c['horizon']}",
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
        combos = sorted({(c["delta"], c["horizon"]) for c in passed})
        for sym in SYMBOLS:
            k = klines[sym]
            kot = k["open_time"].to_numpy(dtype=np.int64)
            kop = k["open"].to_numpy(dtype=float)
            for si, (s_str, e_str) in enumerate(SPANS[sym]):
                s_ms, e_ms = ms(s_str), ms(e_str)
                cut = s_ms + int((e_ms - s_ms) * 0.6)
                in_span = [r for r in events if s_ms <= r["ts"] < e_ms]
                for d, h in combos:
                    rets = []
                    for r in in_span:
                        if r["ts"] < cut: continue
                        out = execute(kot, kop, r["ts"] + d * BAR, h, e_ms)
                        if isinstance(out, float):
                            rets.append(out)
                    n = len(rets)
                    net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                    oos["segments"].append({"symbol": sym, "span": f"{s_str}~{e_str}", "delta": d,
                                            "horizon": h, "n": n, "net16": net16})
        report["oos"] = oos

    report["fng_precheck"] = fng_precheck(events)
    with open(f"{OUT}/x7_listing_events_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report, quality)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4 valid_n={v['valid_n']:.1f}")
    ev_in = {k: v["events_in_span"] for k, v in report["spans"].items()}
    print("events_in_span:", ev_in)
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:5]
    print("top5 net16:", [(f"d{c['delta']}h{c['horizon']}", c['symbol'], c['span_idx']+1, round(c['net16'], 2), round(c['valid_n'],1)) for c in ranked])

if __name__ == "__main__":
    main()
