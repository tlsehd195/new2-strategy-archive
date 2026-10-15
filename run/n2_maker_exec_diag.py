#!/usr/bin/env python3
# N2 지정가(maker) 진입 실행계층 진단 — PREREG_n2_maker_exec_2026-10-13.md 규격 구현.
# 이벤트 셋: X1-R Config A 규격 그대로 (ES/NQ 1h Δz |z|>=1.2816 역방향, BTCUSDT 2024-06-19~2026-04-20).
# 실행계층: 지정가 진입(δ 오프셋, K 체결윈도우) + taker 청산(h=48). 규칙 변경 금지. 결과는 results/ 에 JSON+MD 저장.
import json, os
import numpy as np
import pandas as pd
from scipy import stats

BASE = "/home/z/my-project/NEW2_run"
OUT = f"{BASE}/results"
os.makedirs(OUT, exist_ok=True)

def ms(s): return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)

SPAN = ("2024-06-19", "2026-04-20")   # X1-R Config A 그대로 (BTC 잠금 2024-03-25~06-18 구조적 배제)
ASSETS = ["ES", "NQ"]
LOOKBACKS = [24, 120]
THR = 1.2816
H = 48                                 # 홀드 12h (X1-R 준용)
NWIN = 720                             # 인과 z 윈도우 (X1-R 준용)
HOUR = 3600000
DELTAS = [0.0, 0.0005, 0.0010, 0.0020] # 0, 5, 10, 20 bp
KWINS = [4, 16]                        # 1h, 4h
MAKER_FEE = 0.0002                     # 2bp 사전 고정
FEE, HALF_SPREAD, IMPACT, MAX_PART = 0.0005, 0.0002, 0.1, 0.05  # 엔진 taker 1면 (costs.py:104-117)
ORDER_VALUE = 10_000.0                 # $10k 시나리오 (교차검증에서 $100k 재계산)
SEED = 20261013
N_RAND = 2000
CELLS = [(A, L, d, K) for A in ASSETS for L in LOOKBACKS for d in DELTAS for K in KWINS]  # 32셀

def load_yahoo_1h(path):
    d = json.load(open(path))
    res = d["chart"]["result"][0]
    ts = res["timestamp"]
    close = res["indicators"]["quote"][0]["close"]
    pairs = [(int(t) * 1000, float(c)) for t, c in zip(ts, close)
             if c is not None and np.isfinite(float(c))]
    pairs = [(t, c) for t, c in pairs if t % HOUR == 0]
    df = pd.DataFrame(pairs, columns=["open_time", "close"]).drop_duplicates("open_time")
    df = df.sort_values("open_time").reset_index(drop=True)
    return df

def ext_z(df, L):
    """Δ_t = close_t − close_{t−L}, z = 직전 720 관측 Δ 표준화(인과). X1-R 원구현과 동일."""
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
    for c in ["open_time", "open", "high", "low", "qv"]:
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

def contig(kot, a, b, e_ms):
    """봉 a..b 연속 + 스팬 내 (b 포함)."""
    if b >= len(kot) or kot[b] >= e_ms: return False
    return bool((np.diff(kot[a:b + 1]) == 900000).all())

def gen_events(kot, t_arr, z_by_L, s_ms, e_ms):
    """X1-R run_segment 이벤트 생성 규칙 그대로 (방향만 역방향 고정). 반환: [(t, ei, sgn, L), ...]"""
    events = []
    for i in np.where((t_arr >= s_ms) & (t_arr < e_ms))[0]:
        tt = int(t_arr[i])
        ei = np.searchsorted(kot, tt + HOUR, side="left")
        if ei >= len(kot) or kot[ei] != tt + HOUR or kot[ei] >= e_ms: continue
        for L in LOOKBACKS:
            z = z_by_L[L][i]
            if not np.isfinite(z): continue
            if z >= THR: sgn = -1.0        # 역방향: 상승모멘텀 극단 → 숏
            elif z <= -THR: sgn = +1.0     # 하락모멘텀 극단 → 롱
            else: continue
            events.append((tt, ei, sgn, L))
    return events

def engine_side_cost(order_value, qv):
    if qv <= 0: return None
    part = order_value / qv
    if part > MAX_PART: return None
    return FEE + HALF_SPREAD + IMPACT * np.sqrt(part)

def simulate(events, kot, kop, kho, klo, kqv, e_ms):
    """이벤트별 기본 검증 + (δ, K) 체결 시뮬레이션.
    반환: valid 이벤트 리스트, 시뮬 결과 dict[(d, K)] = per-event list."""
    # 기본 검증: [ei, ei+48] 연속 + 스팬 내 (무조건부 기준선의 공통 조건 — X1-R 드랍 규칙 준용)
    valid = []
    for ev in events:
        tt, ei, sgn, L = ev[0], ev[1], ev[2], ev[3]
        if contig(kot, ei, ei + H, e_ms):
            valid.append(ev)
    out = {}
    for d in DELTAS:
        for K in KWINS:
            rows = []
            for tt, ei, sgn, L, *_ in valid:
                P = kop[ei] * (1 - d) if sgn > 0 else kop[ei] * (1 + d)
                f = None; fill_px = None
                for b in range(ei, ei + K):
                    if sgn > 0:
                        if kop[b] < P: f, fill_px = b, kop[b]; break
                        if klo[b] < P: f, fill_px = b, P; break
                    else:
                        if kop[b] > P: f, fill_px = b, kop[b]; break
                        if kho[b] > P: f, fill_px = b, P; break
                if f is None:
                    rows.append({"filled": False, "fc": False}); continue
                if not contig(kot, f, f + H, e_ms):
                    rows.append({"filled": False, "fc": True}); continue
                xc = engine_side_cost(ORDER_VALUE, kqv[f + H])
                if xc is None:
                    rows.append({"filled": False, "fc": True}); continue
                gross = sgn * (kop[f + H] - fill_px) / fill_px
                net = gross - MAKER_FEE - xc
                imp = (kop[ei] - fill_px) / kop[ei] if sgn > 0 else (fill_px - kop[ei]) / kop[ei]
                fwd_ei = sgn * (kop[f + H] - kop[ei]) / kop[ei]
                rows.append({"filled": True, "fc": False, "gross": gross, "net": net,
                             "net_mf0": gross - 0.0 - xc, "net_mf1": gross - 0.0001 - xc,
                             "imp": imp, "fwd_ei": fwd_ei, "wait": f - ei})
            out[(d, K)] = rows
    return valid, out

def cell_stats(rows, valid, s_ms, e_ms, cut_ms):
    """셀 통계 — promotion-basis 순엣지 = 체결률 × E[순익|체결] (PREREG §3)."""
    n_valid = len(valid)
    fill = [r for r in rows if r["filled"]]
    fc = sum(1 for r in rows if r["fc"])
    n = len(fill)
    st = {"n_events": n_valid, "n_filled": n, "n_fc_drop": fc, "fill_rate": n / n_valid if n_valid else None}
    if n == 0:
        st.update({"promo_net_bp": None, "mean_net_filled_bp": None, "mean_gross_filled_bp": None,
                   "entry_improve_bp": None, "as_bp": None, "t": None, "p": 1.0,
                   "mean_wait_bars": None, "seg": {"first60": {"promo_net_bp": None}, "last40": {"promo_net_bp": None}}})
        return st
    gross = np.array([r["gross"] for r in fill]); net = np.array([r["net"] for r in fill])
    net0 = np.array([r["net_mf0"] for r in fill]); net1 = np.array([r["net_mf1"] for r in fill])
    imp = np.array([r["imp"] for r in fill]); fwd = np.array([r["fwd_ei"] for r in fill])
    fill_rate = n / n_valid
    mean_net = float(net.mean())
    st["promo_net_bp"] = float(fill_rate * mean_net * 1e4)
    st["mean_net_filled_bp"] = float(mean_net * 1e4)
    st["mean_gross_filled_bp"] = float(gross.mean() * 1e4)
    st["mean_net_mf0_bp"] = float(net0.mean() * 1e4)
    st["mean_net_mf1_bp"] = float(net1.mean() * 1e4)
    st["entry_improve_bp"] = float(imp.mean() * 1e4)
    st["as_bp"] = None  # main에서 전체 기준선 주입 후 계산 (PREREG §4 — 체결분 fwd_ei − 무조건부 fwd_ei)
    vn = n / H
    sd = net.std(ddof=1)
    t = mean_net / (sd / np.sqrt(vn)) if sd > 0 else 0.0
    st["t"] = float(t)
    st["p"] = float(2 * stats.t.sf(abs(t), df=max(vn - 1, 1)))
    st["valid_n"] = float(vn)
    st["mean_wait_bars"] = float(np.mean([r["wait"] for r in fill]))
    # 세그먼트 (전반 60% / 후반 40% — X1-R OOS cut 규칙 준용)
    segs = {}
    for tag in ("first60", "last40"):
        fidx = [j for j, ev in enumerate(valid)
                if (ev[0] < cut_ms if tag == "first60" else ev[0] >= cut_ms)]
        fn = sum(1 for j in fidx if rows[j]["filled"])
        fr = fn / len(fidx) if fidx else None
        if fn:
            mn = float(np.mean([rows[j]["net"] for j in fidx if rows[j]["filled"]]))
            segs[tag] = {"promo_net_bp": float(fr * mn * 1e4), "n_events": len(fidx), "n_filled": fn, "fill_rate": fr}
        else:
            segs[tag] = {"promo_net_bp": None, "n_events": len(fidx), "n_filled": 0, "fill_rate": fr}
    st["seg"] = segs
    return st

def main():
    s_ms, e_ms = ms(SPAN[0]), ms(SPAN[1])
    cut_ms = s_ms + int((e_ms - s_ms) * 0.6)
    report = {"prereg": "PREREG_n2_maker_exec_2026-10-13.md",
              "type": "execution-layer diagnostic (비신호 측정 — 프로그램 최초)",
              "event_set": "X1-R Config A 규격 준용 (ES/NQ 1h Δz |z|>=1.2816 역방향, BTCUSDT)",
              "span": f"{SPAN[0]}~{SPAN[1]}", "maker_fee": MAKER_FEE, "deltas_bp": [x * 1e4 for x in DELTAS],
              "k_windows": KWINS, "horizon": H, "cells": [], "quality": {}}
    k = load_klines("BTCUSDT")
    kot = k["open_time"].to_numpy(np.int64)
    kop = k["open"].to_numpy(float); kho = k["high"].to_numpy(float)
    klo = k["low"].to_numpy(float); kqv = k["qv"].to_numpy(float)
    in_mask = (kot >= s_ms) & (kot < e_ms)
    q = {"btc_bars_total": int(len(k)),
         "btc_bars_in_span": int(in_mask.sum()),
         "btc_expected_bars": int((e_ms - s_ms) // 900000),
         "span_contiguous": bool((np.diff(kot[in_mask]) == 900000).all())}
    report["quality"].update(q)
    print("quality:", q)
    ext = {A: load_yahoo_1h(f"{BASE}/research/r3_yahoo_{A.lower()}.json") for A in ASSETS}
    for A in ASSETS:
        report["quality"][f"{A}_bars"] = int(len(ext[A]))
    # 이벤트 생성 (자산 태그 포함 — ES/NQ 동시 발화 시 별개 이벤트로 유지, 자산 차원)
    events_tagged = []
    for A in ASSETS:
        t_arr, z24 = ext_z(ext[A], 24)
        _, z120 = ext_z(ext[A], 120)
        z_by_L = {24: z24, 120: z120}
        for i in np.where((t_arr >= s_ms) & (t_arr < e_ms))[0]:
            tt = int(t_arr[i])
            ei = np.searchsorted(kot, tt + HOUR, side="left")
            if ei >= len(kot) or kot[ei] != tt + HOUR or kot[ei] >= e_ms: continue
            for L in LOOKBACKS:
                z = z_by_L[L][i]
                if not np.isfinite(z): continue
                if z >= THR: sgn = -1.0
                elif z <= -THR: sgn = +1.0
                else: continue
                events_tagged.append((tt, ei, sgn, L, A))
    for A in ASSETS:
        report["quality"][f"{A}_events_raw"] = sum(1 for e in events_tagged if e[4] == A)
    report["quality"]["events_total"] = len(events_tagged)
    valid_t, sim_t = simulate(events_tagged, kot, kop, kho, klo, kqv, e_ms)
    report["quality"]["events_valid"] = len(valid_t)
    # 무조건부 기준선 (자산-sgn-L별): sgn×(open(ei+48)−open(ei))/open(ei) — PREREG §4 AS 분모
    uncond_by_ALL = {}
    for tt, ei, sgn, L, A in valid_t:
        uncond_by_ALL.setdefault((A, sgn, L), []).append(sgn * (kop[ei + H] - kop[ei]) / kop[ei])
    uncond_mean = {kk: float(np.mean(v)) for kk, v in uncond_by_ALL.items()}
    report["uncond_fwd_ei_bp"] = {f"{k[0]}_sgn{int(k[1])}_L{k[2]}": v * 1e4 for k, v in uncond_mean.items()}
    for A, L, d, K in CELLS:
        ev_idx = [j for j, ev in enumerate(valid_t) if ev[4] == A and ev[3] == L]
        rows = [sim_t[(d, K)][j] for j in ev_idx]
        sub_valid = [valid_t[j] for j in ev_idx]
        st = cell_stats(rows, sub_valid, s_ms, e_ms, cut_ms)
        # AS 계산 (전체 기준선 주입: 동일 자산-sgn-L의 무조건부 평균)
        if st["n_filled"] > 0:
            sgn = sub_valid[0][2]
            fill_fwd = [sim_t[(d, K)][j]["fwd_ei"] for j in ev_idx if sim_t[(d, K)][j]["filled"]]
            st["as_bp"] = float((np.mean(fill_fwd) - uncond_mean[(A, sgn, L)]) * 1e4)
        st.update({"asset": A, "lookback": L, "delta_bp": d * 1e4, "k_window": K})
        report["cells"].append(st)
    # Holm (32셀)
    ps = [c["p"] for c in report["cells"]]
    adj = holm(ps)
    for c, a in zip(report["cells"], adj):
        c["p_holm"] = float(a)
    # 게이트 (PREREG §5 사전 고정)
    passed = [c for c in report["cells"] if c["p_holm"] < 0.05]
    gn1_cells = [c for c in report["cells"] if c["promo_net_bp"] is not None and c["promo_net_bp"] > 0]
    gn3 = []
    for c in passed:
        s1 = c["seg"]["first60"]["promo_net_bp"]; s2 = c["seg"]["last40"]["promo_net_bp"]
        ok = s1 is not None and s2 is not None and s1 > 0 and s2 > 0
        gn3.append({"cell": f"{c['asset']} L{c['lookback']} d{c['delta_bp']}bp K{c['k_window']}",
                    "first60": s1, "last40": s2, "pass": bool(ok)})
    g_n1 = len(gn1_cells) >= 1
    g_n2 = len(passed) >= 1
    g_n3 = all(x["pass"] for x in gn3) if gn3 else False
    g_n4 = all((x["last40"] or 0) > 0 for x in gn3) if gn3 else False
    if g_n1 and g_n2 and g_n3 and g_n4:
        overall = "CONDITIONAL_CANDIDATE — 루프 중단, 사용자 결정 대기 (maker 실행계층+X1-R 조합)"
    else:
        overall = "N2_CLOSED — 실행계층 구조 실측 기록 보존, 승격 경로로서 폐기"
    report["verdict"] = {"g_n1": {"pass": bool(g_n1), "pos_cells": len(gn1_cells),
                                  "cells": [f"{c['asset']} L{c['lookback']} d{c['delta_bp']}bp K{c['k_window']} promo={c['promo_net_bp']:.2f}bp" for c in gn1_cells[:10]]},
                         "g_n2": {"pass": bool(g_n2), "holm_pass": len(passed)},
                         "g_n3": {"pass": bool(g_n3), "detail": gn3},
                         "g_n4": {"pass": bool(g_n4)}, "overall": overall}
    # 무신호 대조군 (무작위 2,000 시점 × 양방향, 동일 δ·K)
    report["random_control"] = random_control(kot, kop, kho, klo, s_ms, e_ms)
    with open(f"{OUT}/n2_maker_exec_results.json", "w") as fp:
        json.dump(report, fp, indent=1, default=str)
    write_md(report)
    print("G-N1:", g_n1, "| G-N2:", g_n2, f"({len(passed)} holm pass) | G-N3:", g_n3, "| G-N4:", g_n4)
    print("OVERALL:", overall)
    top = sorted([c for c in report["cells"] if c["promo_net_bp"] is not None], key=lambda x: -x["promo_net_bp"])[:8]
    print("top8 promo_net:", [(f"{c['asset']}L{c['lookback']}d{c['delta_bp']}K{c['k_window']}",
                                round(c['promo_net_bp'], 2), f"fr={c['fill_rate']:.2f}") for c in top])

def random_control(kot, kop, kho, klo, s_ms, e_ms):
    """무신호 대조군: 무작위 2,000 시점 × 양방향 — 체결률·AS (δ·K 격자)."""
    rng = np.random.default_rng(SEED)
    s_idx = int(np.searchsorted(kot, s_ms)); e_idx = int(np.searchsorted(kot, e_ms))
    picks = []
    tries = 0
    while len(picks) < N_RAND and tries < N_RAND * 50:
        j = int(rng.integers(s_idx, e_idx - H - 1))
        tries += 1
        if (np.diff(kot[j:j + H + 1]) == 900000).all() and kot[j + H] < e_ms:
            picks.append(j)
    out = {"n_times": len(picks), "grid": []}
    for d in DELTAS:
        for K in KWINS:
            for sgn in (1.0, -1.0):
                fills, fwd_all = [], []
                fwd_fill = []
                for j in picks:
                    fwd = sgn * (kop[j + H] - kop[j]) / kop[j]
                    fwd_all.append(fwd)
                    P = kop[j] * (1 - d) if sgn > 0 else kop[j] * (1 + d)
                    for b in range(j, j + K):
                        hit = False
                        if sgn > 0 and (kop[b] < P or klo[b] < P):
                            hit = True; break
                        if sgn < 0 and (kop[b] > P or kho[b] > P):
                            hit = True; break
                    if hit:
                        fills.append(1); fwd_fill.append(fwd)
                    else:
                        fills.append(0)
                fr = float(np.mean(fills)) if fills else None
                as_ = float((np.mean(fwd_fill) - np.mean(fwd_all)) * 1e4) if fwd_fill else None
                out["grid"].append({"delta_bp": d * 1e4, "k": K, "sgn": int(sgn),
                                    "fill_rate": fr, "as_bp": as_})
    return out

def write_md(report):
    L = ["# N2 지정가(maker) 진입 실행계층 결과 (사전등록 규격)", "",
         "이벤트 셋: X1-R Config A 규격 준용 — ES/NQ 1h Δz(L∈{24,120}) |z|≥1.2816 → **역방향**, BTCUSDT 2024-06-19~2026-04-20",
         "실행계층: 지정가 진입 δ∈{0,5,10,20}bp × 체결윈도우 K∈{4,16}봉 → 체결 후 h48 taker 청산",
         "비용: 진입 maker 2bp + 청산 엔진 taker 1면 (5bp + half_spread 2bp + 10%·√participation, >5% fail-closed)",
         f"promotion-basis 순엣지 = 체결률 × E[순익|체결] · 패밀리 {len(report['cells'])}셀 Holm", ""]
    q = report["quality"]
    L.append(f"- 품질: BTC 스팬 내 {q['btc_bars_in_span']}/{q['btc_expected_bars']}봉, 연속성={q['span_contiguous']}, 이벤트 {q['events_total']}건 → 기본검증 통과 {q['events_valid']}건")
    L.append(f"- 무조건부 기준선(ei 기준 h48): " + ", ".join(f"{kk}={vv:.1f}bp" for kk, vv in report["uncond_fwd_ei_bp"].items()))
    L.append("")
    L.append("| 자산 | L | δ(bp) | K | n이벤트 | n체결 | 체결률 | 진입개선bp | AS bp | 체결분총bp | 체결분순bp | **promo순bp** | t | p | Holm | FC드랍 | 대기봉 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in report["cells"]:
        fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
        L.append(f"| {c['asset']} | {c['lookback']} | {c['delta_bp']:.0f} | {c['k_window']} | {c['n_events']} | {c['n_filled']} | "
                 f"{c['fill_rate']:.3f} | {fmt(c['entry_improve_bp'])} | {fmt(c['as_bp'])} | {fmt(c['mean_gross_filled_bp'])} | "
                 f"{fmt(c['mean_net_filled_bp'])} | **{fmt(c['promo_net_bp'])}** | {fmt(c['t'])} | {c['p']:.4f} | {c['p_holm']:.4f} | "
                 f"{c['n_fc_drop']} | {fmt(c.get('mean_wait_bars'))} |")
    L.append("")
    L.append("## 게이트 판정 (PREREG §5 사전 고정)")
    v = report["verdict"]
    L.append(f"- G-N1 경제성 (promo순>0 셀 존재): {v['g_n1']['pos_cells']}개 → **{'PASS' if v['g_n1']['pass'] else 'FAIL'}**")
    for cc in v["g_n1"]["cells"]:
        L.append(f"  - {cc}")
    L.append(f"- G-N2 유의성 (Holm p<0.05): {v['g_n2']['holm_pass']}개 → **{'PASS' if v['g_n2']['pass'] else 'FAIL'}**")
    L.append(f"- G-N3 일관성 (전반60+후반40 양수): **{'PASS' if v['g_n3']['pass'] else 'FAIL'}**")
    for d in v["g_n3"]["detail"]:
        f1 = "n/a" if d["first60"] is None else f"{d['first60']:.2f}"
        f2 = "n/a" if d["last40"] is None else f"{d['last40']:.2f}"
        L.append(f"  - {d['cell']}: 전60={f1} / 후40={f2} → {'OK' if d['pass'] else 'NG'}")
    L.append(f"- G-N4 OOS (후반40 양수): **{'PASS' if v['g_n4']['pass'] else 'FAIL'}**")
    L.append(f"- **종합: {v['overall']}**")
    L.append("")
    L.append("## 무신호 대조군 (무작위 2,000 시점 × 양방향)")
    rc = report["random_control"]
    L.append(f"- 표본: {rc['n_times']} 시점 — 체결률·AS(체결분 − 전체, ei 기준)")
    L.append("| δ(bp) | K | 방향 | 체결률 | AS bp |")
    L.append("|---|---|---|---|---|")
    for g in rc["grid"]:
        fmta = "n/a" if g["as_bp"] is None else f"{g['as_bp']:.2f}"
        L.append(f"| {g['delta_bp']:.0f} | {g['k']} | {'롱' if g['sgn'] == 1 else '숏'} | {g['fill_rate']:.3f} | {fmta} |")
    L.append("")
    L.append("## maker 수수료 민감도 (정보성 — 승격 회계는 2bp 고정)")
    L.append("| 자산 | L | δ(bp) | K | 순익(mf0)bp | 순익(mf1)bp | 순익(mf2)bp |")
    L.append("|---|---|---|---|---|---|---|")
    for c in report["cells"]:
        if c["n_filled"] == 0: continue
        f0 = "n/a" if c.get("mean_net_mf0_bp") is None else f"{c['mean_net_mf0_bp']:.2f}"
        f1 = "n/a" if c.get("mean_net_mf1_bp") is None else f"{c['mean_net_mf1_bp']:.2f}"
        f2 = "n/a" if c.get("mean_net_filled_bp") is None else f"{c['mean_net_filled_bp']:.2f}"
        L.append(f"| {c['asset']} | {c['lookback']} | {c['delta_bp']:.0f} | {c['k_window']} | {f0} | {f1} | {f2} |")
    with open(f"{OUT}/n2_maker_exec_results.md", "w") as fp:
        fp.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
