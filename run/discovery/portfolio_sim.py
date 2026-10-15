#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NEW2 감사 Phase 6 — 포트폴리오 시뮬레이터 (F-17, Task 58 보완작성)
==================================================================
목적: 이벤트 스터디(셀 단위)와 포트폴리오 관점(슬롯·중복·비용)의 수치 차이를
      정량화한 2027 잠금 TEST 판정의 사전 입력(진단용 — 판정 아님).

규칙:
  - 슬롯 제한: 동시 보유 최대 MAX_SLOTS개 (기본 3)
  - 심볼 중복 차단: 같은 심볼 보유 중 신규 진입 스킵
  - fail-closed: 비용 체결 불가(participation 초과) 이벤트는 스킵 — 찬가 아님
  - 펀딩 보유비용: funding_cost.funding_cost로 (entry,exit] 구간 반영 (R8)
  - 진입 i+1 시가 → 청산 i+1+h 시가 (engine_lib.event_study와 동일 규약)
  - locked TEST 구간 진입 배제 (engine_lib 재사용 — 판정 논리 0 변경)

사용: python3 portfolio_sim.py            # state 생존자 대상
      python3 portfolio_sim.py '{"family":"fng","symbols":["BTCUSDT","ETHUSDT"],"params":{"lo":5,"hi":95,"horizons":[16]}}'
출력: 일일 수익 시계·누적·MDD·Sharpe·Sortino·스킵률 (stdout + results/portfolio_sim_*.json)
"""
import json
import math
import os
import sys
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_lib as E  # noqa: E402
from funding_cost import funding_cost  # noqa: E402

OUT = f"{E.RUN}/results"
MAX_SLOTS = int(os.environ.get("NEW2_SIM_SLOTS", "3"))
OV = 10_000.0  # o10k 시나리오 (게이트와 동일)


def simulate(cand: dict) -> dict:
    """단일 후보(복수 심볼) 포트폴리오 시뮬레이션."""
    h = cand["params"]["horizons"][0]
    events = []  # (entry_ms, exit_ms, sym, raw_bp, cost_bp, fund_ret)
    for sym in cand["symbols"]:
        df = E.load_klines(sym)
        p = cand["params"]
        fam = cand["family"]
        if fam == "flow_z":
            sgn = E.sig_flow_z(df, p["L"], p["thr"], p["mode"])
        elif fam == "ret_rev":
            sgn = E.sig_ret_rev(df, p["k"], p["thr"])
        elif fam == "ts_mom":
            sgn = E.sig_ts_mom(df, p["L"], p["thr"])
        elif fam == "vol_break":
            sgn = E.sig_vol_break(df, p["Lv"], p["thr"])
        elif fam == "tod":
            h0, h1 = {"ASIA": (0, 8), "EU": (8, 16), "US": (16, 24)}[p["sess"]]
            sgn = E.sig_tod(df, h0, h1, p["dir"])
        elif fam == "fund_z":
            sgn = E.sig_funding_z(df, sym, p["zthr"])
        elif fam == "fng":
            sgn = E.sig_fng(df, p["lo"], p["hi"])
        else:
            raise ValueError(f"unknown family {fam}")
        if cand.get("variant") and cand["variant"]["type"] == "filter":
            sgn = E.apply_filter(df, sgn, cand["variant"]["name"])
        ev = E.event_study(df, sgn, [h], sym)[h]
        ok, c = E.cost_vec(OV, ev["qv_e"], ev["qv_x"])
        try:
            fnd = E.load_funding(sym)
        except FileNotFoundError:
            fnd = None
        kot = df["open_time"].to_numpy()
        kop = df["open"].to_numpy(float)
        for j in range(len(ev["entry_ms"])):
            if not ok[j]:
                continue  # fail-closed
            e_ms, x_ms = int(ev["entry_ms"][j]), int(ev["exit_ms"][j])
            e_i = int(kot.searchsorted(e_ms))
            x_i = int(kot.searchsorted(x_ms))
            fund = 0.0
            if fnd is not None and len(fnd):
                fr = funding_cost(df, fnd, e_ms, x_ms,
                                  direction=int(ev["sgn"][j]))
                fund = fr if isinstance(fr, (int, float)) else fr.get("funding_ret", 0.0)
            events.append({"sym": sym, "entry_ms": e_ms, "exit_ms": x_ms,
                           "raw_bp": float(ev["raw_bp"][j]), "cost_bp": float(c[j]),
                           "fund_ret": fund})
    # 슬롯 시뮬레이션: 진입시각 순 정렬, 슬롯·심볼 중복 반영
    events.sort(key=lambda e: (e["entry_ms"], e["sym"]))
    held_until = {}   # sym -> exit_ms
    slots = []        # 진행 중 exit_ms 목록
    day_ret = {}      # exit 일(UTC) -> bp 합
    n_skip_slot = n_skip_sym = 0
    for ev in events:
        slots = [x for x in slots if x > ev["entry_ms"]]
        if ev["sym"] in held_until and held_until[ev["sym"]] > ev["entry_ms"]:
            n_skip_sym += 1
            continue
        if len(slots) >= MAX_SLOTS:
            n_skip_slot += 1
            continue
        net_bp = ev["raw_bp"] - ev["cost_bp"] + ev["fund_ret"] * 1e4
        day = dt.datetime.fromtimestamp(ev["exit_ms"] / 1000, dt.timezone.utc)
        dkey = day.strftime("%Y-%m-%d")
        day_ret[dkey] = day_ret.get(dkey, 0.0) + net_bp
        slots.append(ev["exit_ms"])
        held_until[ev["sym"]] = ev["exit_ms"]
    days = sorted(day_ret)
    rets = [day_ret[d] for d in days]
    n_used = sum(len(v) for v in [rets])
    if rets:
        eq, peak, mdd = 0.0, 0.0, 0.0
        for r in rets:
            eq += r
            peak = max(peak, eq)
            mdd = min(mdd, eq - peak)
        mean = sum(rets) / len(rets)
        sd = (sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 if len(rets) > 1 else 0.0
        sharpe = mean / sd * math.sqrt(365) if sd > 0 else None
        dn = [r for r in rets if r < 0]
        dsd = (sum(r ** 2 for r in dn) / len(dn)) ** 0.5 if dn else 0.0
        sortino = mean / dsd * math.sqrt(365) if dsd > 0 else None
        total_bp = eq
    else:
        mdd = sharpe = sortino = total_bp = None
    n_total = len(events)
    return {"family": cand["family"], "symbols": cand["symbols"],
            "params": cand["params"], "variant": cand.get("variant"),
            "days": len(rets), "events_total": n_total,
            "skip_slot": n_skip_slot, "skip_symbol_dup": n_skip_sym,
            "skip_rate": round((n_skip_slot + n_skip_sym) / n_total, 4) if n_total else None,
            "net_bp_total": None if total_bp is None else round(total_bp, 2),
            "mean_bp_per_day": None if not rets else round(sum(rets) / len(rets), 4),
            "mdd_bp": None if mdd is None else round(mdd, 2),
            "sharpe_annualized": None if sharpe is None else round(sharpe, 3),
            "sortino_annualized": None if sortino is None else round(sortino, 3),
            "scenario": "o10k", "max_slots": MAX_SLOTS,
            "engine": "NEW2-discovery-v2-audit3",
            "note": "진단용(판정 아님) — 2027 잠금 TEST 심사 입력. 이벤트 스터디와 "
                    "수치가 다른 이유: 슬롯 제한·심볼 중복 차단·일 단위 집계 제약"}


def main():
    if len(sys.argv) > 1:
        cand = json.loads(sys.argv[1])
    else:
        st = json.load(open(f"{E.DISC}/state.json"))
        if not st.get("survivor_specs"):
            print("NO_SURVIVORS (생존자 없음 — 시뮬레이션 대상 없음, 정상)")
            return
        cid = st["survivors"][0]
        cand = {"family": st["survivor_specs"][cid]["family"],
                "symbols": st["survivor_specs"][cid]["symbols"],
                "params": st["survivor_specs"][cid]["params"],
                "variant": st["survivor_specs"][cid].get("variant")}
    res = simulate(cand)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = f"{OUT}/portfolio_sim_{ts}.json"
    json.dump(res, open(out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print(f"SAVED {out}")


if __name__ == "__main__":
    main()
