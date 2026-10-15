#!/usr/bin/env python3
# X10 캘린더 계절성 진단 — PREREG_x10_calendar_seasonality_2026-10-13.md 규격 그대로 구현.
# 규칙 변경 금지. 결과는 results/ 에 JSON+MD로 저장. 잠금/예약 TEST 구간 미사용.
# 신호: 결정론 세션 개시(ASIA 00:00 / EU 07:00 / US 13:30 UTC) → 롱(1차 방향 고정), h{16,48}봉 홀드.
import json, glob, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run/data"
OUT = "/home/z/my-project/NEW2_run/results"
os.makedirs(OUT, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
SPANS = {  # 잠금 해제 구간 (끝 미포함) — X8과 동일
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
SESSIONS = {"ASIA_0000": 0, "EU_0700": 25_200_000, "US_1330": 48_600_000}  # UTC 일중 오프셋
HORIZONS = [16, 48]
COST = {"c12": 0.0012, "c16": 0.0016, "c20": 0.0020}
SEEDS = 100
DAY_MS = 86_400_000
CELLS = [(ses, h) for ses in SESSIONS for h in HORIZONS]  # 6셀/구성 → 패밀리 24

def load_klines(sym):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files], ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "volume", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    dups = int(df.duplicated("open_time").sum())
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df, dups

def holm(pvals):
    p = np.asarray(pvals, dtype=float); m = len(p)
    order = np.argsort(p); adj = np.empty(m)
    mx = 0.0
    for rank, idx in enumerate(order):
        mx = max(mx, (m - rank) * p[idx])
        adj[idx] = min(mx, 1.0)
    return adj

def run_span(sub, s_str, e_str):
    """세션 개시봉 시가 진입(롱 고정), +h봉 시가 청산. fail-closed. 셀 내 겹침 없음 → 유효n=n."""
    s_ms, e_ms = ms(s_str), ms(e_str)
    kot = sub["open_time"].to_numpy(dtype=np.int64)
    kop = sub["open"].to_numpy(dtype=float)
    kqv = sub["qv"].to_numpy(dtype=float)
    days_in_span = (e_ms - s_ms) // DAY_MS
    q = {"kline_bars_in_span": int(len(sub)),
         "expected_bars": int((e_ms - s_ms) // 900000),
         "days_in_span": int(days_in_span)}
    q["kline_missing"] = q["expected_bars"] - q["kline_bars_in_span"]
    q["klines_contiguous"] = bool(len(kot) == 0 or (np.diff(kot) == 900000).all())
    idx_of = {t: i for i, t in enumerate(kot)}  # 정확 매칭(세션 개시 시각과 open_time 일치)
    trades = {c: [] for c in CELLS}
    dropped = {c: 0 for c in CELLS}
    missing_entry = {c: 0 for c in CELLS}
    ses_counts = {}
    for ses, off in SESSIONS.items():
        # 세션 개시봉 인덱스 목록 (스팬 내)
        first_day = (s_ms + DAY_MS - 1) // DAY_MS * DAY_MS  # 스팬 시작 이후 첫 자정(UTC)
        ts_list = []
        t0 = first_day + off
        while t0 < e_ms:
            if t0 >= s_ms:
                ts_list.append(t0)
            t0 += DAY_MS
        ses_counts[ses] = len(ts_list)
        for t in ts_list:
            ei = idx_of.get(t)
            if ei is None or ei >= len(kot) or kot[ei] >= e_ms:
                for h in HORIZONS:
                    missing_entry[(ses, h)] += 1
                continue
            for h in HORIZONS:
                xi = ei + h
                if xi >= len(kot) or kot[xi] >= e_ms:
                    continue
                if (np.diff(kot[ei:xi + 1]) != 900000).any():
                    dropped[(ses, h)] += 1; continue  # fail-closed
                raw = (kop[xi] - kop[ei]) / kop[ei]
                trades[(ses, h)].append((raw, t, kqv[ei], kqv[xi]))
    q["session_entry_days"] = ses_counts
    return q, trades, dropped, missing_entry

def cell_stats(rets, h):
    n = len(rets)
    out = {"n": n, "valid_n": float(n)}  # 셀 내 겹침 없음 → 유효n = n (PREREG §4)
    if n == 0:
        out.update({"total_bp": None, "net12": None, "net16": None, "net20": None,
                    "t": None, "p": 1.0, "oppose_net16": None, "p_ctrl": None})
        return out
    r = np.array(rets)
    out["total_bp"] = float(r.mean() * 1e4)
    for k, c in COST.items():
        out[f"net{k[1:]}"] = float((r.mean() - c) * 1e4)
    sd = r.std(ddof=1)
    t = (r.mean() - COST["c16"]) / (sd / np.sqrt(n)) if sd > 0 else 0.0
    out["t"] = float(t)
    out["p"] = float(2 * stats.t.sf(abs(t), df=max(n - 1, 1)))
    out["oppose_net16"] = float((-r.mean() - COST["c16"]) * 1e4)  # 반대방향(숏) 통제
    rng = np.random.default_rng(20261013)
    rand_means = []
    for _ in range(SEEDS):
        d = rng.choice([-1.0, 1.0], size=n)
        rand_means.append(((d * r).mean() - COST["c16"]) * 1e4)
    out["p_ctrl"] = float(np.mean(np.array(rand_means) >= out["net16"]))
    return out

def main():
    report = {"prereg": "PREREG_x10_calendar_seasonality_2026-10-13.md", "family_size": 24,
              "signal": "deterministic TradFi session opens (ASIA 00:00/EU 07:00/US 13:30 UTC) -> LONG (pre-fixed), hold h in {16,48} bars",
              "spans": {}, "cells": []}
    quality = {}
    klines = {}
    subs = {}
    for sym in SYMBOLS:
        df, dups = load_klines(sym)
        quality[f"{sym}_dup_kline"] = dups
        quality[f"{sym}_kline_rows_total"] = int(len(df))
        klines[sym] = df
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            sub = df[(df["open_time"] >= s_ms) & (df["open_time"] < e_ms)].reset_index(drop=True)
            subs[(sym, si)] = sub
            vol_arr = sub["volume"].to_numpy(dtype=float)
            quality[f"{sym}_span{si+1}_bars"] = int(len(sub))
            quality[f"{sym}_span{si+1}_vol0_bars"] = int((vol_arr <= 0).sum())
    for sym in SYMBOLS:
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            sub = subs[(sym, si)]
            q, trades, dropped, missing_entry = run_span(sub, s_str, e_str)
            key = f"{sym}_span{si+1}_{s_str}~{e_str}"
            report["spans"][key] = q
            quality[key] = q
            for ses, h in CELLS:
                arr = trades[(ses, h)]
                st = cell_stats([x[0] for x in arr], h)
                st.update({"symbol": sym, "span": f"{s_str}~{e_str}", "session": ses,
                           "horizon": h, "dropped_gap": dropped[(ses, h)],
                           "missing_entry": missing_entry[(ses, h)]})
                report["cells"].append(st)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    verdict = {"holm_pass_cells": [], "PASS": False}
    for c in passed:
        same = [x for x in report["cells"] if x["session"] == c["session"] and x["horizon"] == c["horizon"]]
        pos = [x for x in same if x["net16"] is not None and x["net16"] > 0 and x["valid_n"] >= 30]
        verdict["holm_pass_cells"].append({"cell": f"{c['symbol']} {c['span']} {c['session']} h{c['horizon']}",
                                           "p_holm": c["p_holm"], "net16": c["net16"],
                                           "config_pos_of_4": len(pos)})
    if verdict["holm_pass_cells"]:
        best = max(verdict["holm_pass_cells"], key=lambda x: x["config_pos_of_4"])
        verdict["PASS"] = best["config_pos_of_4"] >= 3
        verdict["best_config"] = best
    report["verdict"] = verdict

    oos = {"executed": False, "segments": []}
    if passed:
        oos["executed"] = True
        oos["segments"] = oos_eval(klines, passed)
        report["oos"] = oos

    fam = family_descriptive(subs)
    report["family_descriptive"] = fam

    with open(f"{OUT}/x10_calendar_results.json", "w") as fp:
        json.dump({"report": report, "quality": quality}, fp, indent=1, default=str)
    write_md(report)
    print("PASS =", verdict["PASS"], "| holm_pass =", len(passed))
    for v in verdict["holm_pass_cells"]:
        print(f"  {v['cell']} p_holm={v['p_holm']:.4f} net16={v['net16']:.2f} pos={v['config_pos_of_4']}/4")
    ranked = sorted([c for c in report["cells"] if c["net16"] is not None], key=lambda x: -x["net16"])[:6]
    print("top6 net16:", [(f"{c['session']}h{c['horizon']}", c['symbol'], round(c['net16'], 2), 'n=' + str(c['n'])) for c in ranked])
    ranked2 = sorted([c for c in report["cells"] if c["total_bp"] is not None], key=lambda x: -x["total_bp"])[:6]
    print("top6 total:", [(f"{c['session']}h{c['horizon']}", c['symbol'], round(c['total_bp'], 2)) for c in ranked2])

def oos_eval(klines, passed_cells):
    """Holm 통과 (session,h) 조합만 OOS(각 스팬 하위 40%)에 동일 적용. 세그먼트별 순16 양수 여부."""
    segs = []
    combos = sorted({(c["session"], c["horizon"]) for c in passed_cells})
    for sym in SYMBOLS:
        df = klines[sym]
        for si, (s_str, e_str) in enumerate(SPANS[sym]):
            s_ms, e_ms = ms(s_str), ms(e_str)
            sub = df[(df["open_time"] >= s_ms) & (df["open_time"] < e_ms)].reset_index(drop=True)
            kot = sub["open_time"].to_numpy(dtype=np.int64)
            kop = sub["open"].to_numpy(dtype=float)
            cut_i = int(len(sub) * 0.6)  # 하위 40% OOS 시작 인덱스
            idx_of = {t: i for i, t in enumerate(kot)}
            for ses, h in combos:
                off = SESSIONS[ses]
                first_day = (s_ms + DAY_MS - 1) // DAY_MS * DAY_MS
                rets = []
                t0 = first_day + off
                while t0 < e_ms:
                    if t0 >= s_ms:
                        ei = idx_of.get(t0)
                        if ei is not None and ei >= cut_i and ei < len(kot):
                            xi = ei + h
                            if xi < len(kot) and kot[xi] < e_ms and (np.diff(kot[ei:xi + 1]) == 900000).all():
                                rets.append((kop[xi] - kop[ei]) / kop[ei])
                    t0 += DAY_MS
                n = len(rets)
                net16 = float((np.mean(rets) - 0.0016) * 1e4) if n else None
                segs.append({"symbol": sym, "span": f"{s_str}~{e_str}", "session": ses,
                             "horizon": h, "n": n, "net16": net16})
    return segs

def family_descriptive(subs):
    """정보성: 세션 개시 후 h봉 평균 수익 vs 스팬 전체 봉 평균 수익 (기술 통계). 결정론 신호라 상관 기반 검증 무의미(§7)."""
    out = {"note": "캘린더 결정론 신호 — z-상관 기반 패밀리 검증 부적용(PREREG §7). 22건 기존 패밀리 중 캘린더 신호원 0건(§0).",
           "spans": []}
    for (sym, si), sub in subs.items():
        kot = sub["open_time"].to_numpy(dtype=np.int64)
        kop = sub["open"].to_numpy(dtype=float)
        bars_ret = np.diff(kop) / kop[:-1]
        entry = {"symbol": sym, "span": si + 1, "all_bars_mean_bp": float(np.mean(bars_ret) * 1e4) if len(bars_ret) else None}
        for ses, off in SESSIONS.items():
            mask = (kot % DAY_MS) == off
            idxs = np.where(mask)[0]
            idxs = idxs[idxs + 16 < len(kop)]
            if len(idxs):
                r16 = (kop[idxs + 16] - kop[idxs]) / kop[idxs]
                entry[f"{ses}_h16_mean_bp"] = float(r16.mean() * 1e4)
                entry[f"{ses}_h16_n"] = int(len(r16))
        out["spans"].append(entry)
    return out

def write_md(report):
    L = ["# X10 캘린더 계절성 결과 (사전등록 규격)", "",
         "신호: 결정론 TradFi 세션 개시(ASIA 00:00/EU 07:00/US 13:30 UTC, DST 미보정 고정) → **롱(1차 방향 사전 고정)**, h∈{16,48}봉 홀드",
         f"시도 수(가족): {len(report['cells'])}셀 · Holm 보정 · 통제: 반대방향(숏) + 무작위 100시드 · 셀 내 겹침 없음(유효n=n)", ""]
    for sym in SYMBOLS:
        for si in range(2):
            key = [k for k in report["spans"] if k.startswith(f"{sym}_span{si+1}_")][0]
            qq = report["spans"][key]
            L.append(f"## {key} — kline {qq['kline_bars_in_span']}/{qq['expected_bars']}(결측 {qq['kline_missing']}), 연속성={qq['klines_contiguous']}, 일수={qq['days_in_span']}, 세션 진입일 ASIA/EU/US={qq['session_entry_days']['ASIA_0000']}/{qq['session_entry_days']['EU_0700']}/{qq['session_entry_days']['US_1330']}")
            L.append("| 세션 | h | n | 유효n | 총bp | 순12 | 순16 | 순20 | t | p | Holm | 반대순16 | P(랜덤≥) | 드랍 | 결측진입 |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for c in report["cells"]:
                spans_list = SPANS[sym]
                if c["symbol"] != sym or c["span"] != f"{spans_list[si][0]}~{spans_list[si][1]}": continue
                fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
                L.append(f"| {c['session']} | {c['horizon']} | {c['n']} | {c['valid_n']:.0f} | {fmt(c['total_bp'])} | "
                         f"{fmt(c['net12'])} | {fmt(c['net16'])} | {fmt(c['net20'])} | {fmt(c['t'])} | {c['p']:.4f} | "
                         f"{c['p_holm']:.4f} | {fmt(c['oppose_net16'])} | {fmt(c['p_ctrl'])} | {c['dropped_gap']} | {c['missing_entry']} |")
            L.append("")
    L.append("## 판정 (사전 고정 규칙)")
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    L.append(f"- Holm p<0.05 셀: {len(passed)}개")
    for v in report["verdict"]["holm_pass_cells"]:
        L.append(f"  - {v['cell']} — p_holm={v['p_holm']:.4f}, 순16={v['net16']:.2f}bp, 동일설정 4구성 중 양수 {v['config_pos_of_4']}개")
    L.append(f"- **PASS = {report['verdict']['PASS']}**")
    if report.get("oos", {}).get("executed"):
        L.append("")
        L.append("## OOS (각 스팬 하위 40%, 사전 고정 규칙)")
        pos_segs = [s for s in report["oos"]["segments"] if s["net16"] is not None and s["net16"] > 0]
        for s in report["oos"]["segments"]:
            ns = "n/a" if s["net16"] is None else f"{s['net16']:.2f}bp"
            L.append(f"- {s['symbol']} {s['span']} {s['session']} h{s['horizon']} — n={s['n']}, 순16={ns}")
        L.append(f"- OOS 양수 세그먼트: {len(pos_segs)}/{len(report['oos']['segments'])} (승격 자격 기준 ≥2)")
    fd = report.get("family_descriptive", {})
    L.append("")
    L.append("## 패밀리 기술 통계 (정보성)")
    L.append(f"- {fd.get('note','')}")
    for e in fd.get("spans", []):
        parts = [f"{k}={v:.3f}bp" if isinstance(v, float) else f"{k}={v}" for k, v in e.items() if k not in ("symbol", "span")]
        L.append(f"- {e['symbol']} span{e['span']}: " + ", ".join(parts))
    with open(f"{OUT}/x10_calendar_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
