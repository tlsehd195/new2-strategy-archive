#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NEW2 자율탐색 엔진 — 사이클 오케스트레이터
============================================
1사이클 = [기본 후보 배치 + 조합 후보 배치] 생성 → 각 후보별
  PREREG 사전등록(선) → 이벤트 스터디 백테스트(후) → 게이트 판정 →
  append-only 장부 기록 → results JSON/MD 저장 → 커서 전진.
생존자 발생 시 promotion_queue 적재 (자동 승격 없음 — R3).
생존 2개 이상 시 Stouffer 결합 검정(해석형) 실행.

출력: 성공 시 "CYCLE_OK ..." / 실패 시 "CYCLE_FAILED ..." (run_cycle.sh가 캡처)
"""
import datetime as dt
import json
import math
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_lib as E  # noqa: E402
import generator as G  # noqa: E402

# Phase 2 (F-08/F-35): 후보 단위 오류가 있으면 CYCLE_PARTIAL(종료코드 2)로 보고.
# 방법론(게이트 R1~R3·비용·통계)은 일절 변경 없음 — 실행 안정성만 개선.

STATE_PATH = f"{E.DISC}/state.json"
LEDGER = G.LEDGER
CYCLES_MD = f"{E.DISC}/cycles.md"
PROMO_MD = f"{E.DISC}/promotion_queue.md"

BATCH = int(os.environ.get("NEW2_DISC_BATCH", "10"))
COMBO_BATCH = int(os.environ.get("NEW2_DISC_COMBO", "4"))
BUDGET_S = float(os.environ.get("NEW2_DISC_BUDGET", "540"))

HYP = {
    "flow_z": "taker 매수비율 R=Σtbv/Σvol({L}봉) 표준화 극단(±{thr}σ) → {mode} "
              "(mom=연속, rev=역발상). 진입: 신호 다음 봉 시가, 청산: +{h}봉 시가.",
    "ret_rev": "단기 {k}봉 수익률 표준화 극단(±{thr}σ) 역발상. 진입: 다음 봉 시가, "
               "청산: +{h}봉 시가.",
    "ts_mom": "{L}봉 수익률 모멘텀 연속(±{thr}σ). 진입: 다음 봉 시가, 청산: +{h}봉 시가.",
    "vol_break": "실현변동성 z≥{thr} 확장 국면에서 {Lv}봉 방향 돌파 연속. "
                 "진입: 다음 봉 시가, 청산: +{h}봉 시가.",
    "tod": "UTC 세션 {sess} 고정 방향({dir}) 계절성. 청산: +{h}봉 시가.",
    "fund_z": "8h 펀딩비 z(90일 창) 극단(|z|≥{zthr}) 역발상(펀딩 지불 쪽 반대). "
              "펀딩 시각 직후 15m 봉 1회 진입, 청산: +{h}봉 시가.",
    "fng": "D-1 확정 공포·탐욕 지수 ≤{lo}(롱) / ≥{hi}(숏) 역발상. D일 첫 15m 봉 1회 진입, "
           "청산: +{h}봉 시가.",
}

GATES_TEXT = """## 게이트 (engine-discovery-v1 잠금 — 순서 고정)
1. POWER: 비중첩 표본 valid_n ≥ 30 (미달 시 UNDETERMINED_POWER — 재검증 큐)
2. COST: 엔진 CandleCostModel o10k 시나리오 비용 차감 후 평균 순수익 > 0
3. CONSIST: 개발 2구간 모두 순수익 부호 양수 (부호 일치)
4. STAT: 후보 내 Holm 보정 단측 p < 0.05 **AND** 승자 저주 수축
   mean_defl = mean − 2.2414×se > 0
규칙 R1: 게이트는 비중첩(non-overlap) 표본으로만 판정, 중첩 통계는 참고 병기.
규칙 R2: 위 수축 조건까지 통과해야 SURVIVOR.
규칙 R3: SURVIVOR는 승격 주장이 아니라 promotion_queue 적재 — 실제 승격 여부는
잠금 TEST 구간에서 2027-01-11 / 2027-07-01 판정 시에만 결정.

## 잠금/예약 TEST 구간 (절대 미사용)
- BTC 2024-03-25 ~ 2024-06-18
- ETH 2026-04-28 ~ 2026-08-31
개발 구간: BTC/ETH는 기존 패밀리 잠금 SPANS, 기타 심볼은
2021-05-02~2023-06-01 / 2023-06-01~2026-04-20 (ALT_SPANS).

## 비용 모형 (CandleCostModel, costs.py:104-117 공식)
fee 5bp + half-spread 2bp + impact 0.1·√(주문/구간대금), 최대 체결비중 5%,
시나리오 o10k($10k) / o100k($100k). 참여율 초과 체결 불가(fail_closed).
"""


def rebuild_state_from_ledger():
    """F-09/F-14 보완: state.json 파손 시 원장(유일한 진실)에서 재구성.
    커서=패밀리별 마지막 base 행의 fam_idx+1, tested=원장 고유 해시 수."""
    st = {"engine": "NEW2-discovery-v1", "cycle": 0, "cursors": {},
          "tested": 0, "survivors": [], "survivor_specs": {},
          "stouffer_done": [], "promotion_queue": [],
          "last_ts": None, "last_status": "recovered"}
    import generator as _G
    fam_lists = {f: _G.enumerate_family(f) for f in _G.family_order()}
    tested = set()
    max_cycle = 0
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                tested.add(r.get("cand_hash"))
                max_cycle = max(max_cycle, int(r.get("cycle") or 0))
                if r.get("kind") == "base":
                    for i, c in enumerate(fam_lists.get(r["family"], [])):
                        syms = [E.PAIRS[c["pair_idx"]][0], E.PAIRS[c["pair_idx"]][1]]
                        if syms == r["symbols"] and c["params"] == r["params"]:
                            st["cursors"][r["family"]] = max(
                                st["cursors"].get(r["family"], 0), i + 1)
                            break
                if r.get("verdict") in ("SURVIVOR", "SURVIVOR_COMBO"):
                    if r["cid"] not in st["survivors"]:
                        st["survivors"].append(r["cid"])
                        st["promotion_queue"].append(r["cid"])
                if r.get("kind") == "combo_stouffer" and r.get("pair"):
                    st["stouffer_done"].append(r["pair"])
    tested.discard(None)
    st["cycle"] = max_cycle
    st["tested"] = len(tested)
    with open(f"{E.DISC}/reconcile.log", "a") as f:
        f.write(f"{now_iso()} state rebuilt from ledger: cycle={st['cycle']} "
                f"tested={st['tested']} cursors={json.dumps(st['cursors'])}\n")
    return st


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH))
        except Exception:
            # 파손된 state → 원장 기반 재구성 (F-09/F-14)
            return rebuild_state_from_ledger()
    return {"engine": "NEW2-discovery-v1", "cycle": 0, "cursors": {},
            "tested": 0, "survivors": [], "survivor_specs": {},
            "stouffer_done": [], "promotion_queue": [],
            "last_ts": None, "last_status": "init"}


def save_state(st):
    # F-09 수정: 원자적 저장 — 임시 파일 작성 후 os.replace (전원 단절·동시 읽기 안전)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_PATH)


def append_ledger(rec: dict):
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_prereg(cand: dict, cid: str, date_str: str) -> str:
    p = cand["params"]
    h = p["horizons"][0]
    hyp = HYP[cand["family"]].format(**{**p, "h": h})
    if cand.get("variant"):
        v = cand["variant"]
        if v["type"] == "perturb":
            hyp += f" [조합-섭동 변형: {v['delta']} (생존 {cand['surv_ref']} 기반)]"
        else:
            hyp += f" [조합-필터 변형: {v['name']} (생존 {cand['surv_ref']} 기반)]"
    cells = ", ".join(f"{s}/h{h}" for s in cand["symbols"])
    txt = f"""# PREREG — 자율탐색 후보 {cid} ({date_str})

> 본 문서는 백테스트 실행 **이전**에 자동 작성되었으며, 작성 후 내용 수정 금지(append-only).
> 엔진: NEW2-discovery-v1 · 등급: Phase A 진단 (승격 주장 아님)

## 가설
{hyp}

## 규격
- 패밀리: {cand['family']} · 종목: {', '.join(cand['symbols'])} · 셀: {cells}
- 파라미터: `{json.dumps(p, ensure_ascii=False)}`
- 종류: {cand.get('kind', 'base')}{(' · 변형: ' + json.dumps(cand['variant'])) if cand.get('variant') else ''}
- 데이터: 캐시 전용 (NEW2_run/data) — 신규 다운로드 0바이트

{GATES_TEXT}
"""
    path = f"{E.RUN}/PREREG_disc{cid}_{date_str}.md"
    with open(path, "w") as f:
        f.write(txt)
    return path


def run_candidate(cand: dict) -> list:
    """후보 백테스트 → 셀 통계 리스트."""
    cells = []
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
        ev = E.event_study(df, sgn, p["horizons"], sym)
        for h in p["horizons"]:
            cells.append(E.cell_stats(ev[h], h, sym))
    return cells


def results_md(cid: str, cand: dict, cells: list, verdict: str, best) -> str:
    rows = []
    for c in cells:
        m = c.get("mean_net_bp")
        t = c.get("t")
        ph = c.get("p_holm")
        rows.append(
            f"| {c['sym']}/h{c['h']} | {c['n_raw']} | {c['vn_nonov']} | "
            f"{m if m is None else round(m, 2)} | "
            f"{t if t is None else round(t, 3)} | "
            f"{ph if ph is None else round(ph, 5)} | {verdict if best and c is best else ''} |")
    sp = ""
    if best and best.get("span_means") and all(x is not None for x in best["span_means"]):
        sp = f"\n- 최고 셀 구간별 평균: [{best['span_means'][0]:.2f}, {best['span_means'][1]:.2f}] bp"
    dl = ""
    if best and best.get("mean_defl_bp") is not None:
        dl = f"\n- 수축 후 기대값: {best['mean_defl_bp']:.2f} bp"
    return f"""# 자율탐색 결과 — {cid} ({now_iso()})

- 패밀리: {cand['family']} · 종목: {', '.join(cand['symbols'])}
- 파라미터: `{json.dumps(cand['params'], ensure_ascii=False)}`
- **판정: {verdict}** (게이트: POWER→COST→CONSIST→STAT, engine-discovery-v1){sp}{dl}

| 셀 | n(raw) | valid_n(비중첩) | net bp(o10k) | t | p(Holm) | 최고 |
|---|---|---|---|---|---|---|
{chr(10).join(rows)}

> 등급: Phase A 진단. 승격 주장 아님 — 승격은 잠금 TEST 구간 2027 판정에서만.
"""


def promote_note(cid: str, cand: dict, best: dict):
    line = (f"- {now_iso()} {cid} [{cand['family']} {cand['symbols']} "
            f"{json.dumps(cand['params'])}] best={best['sym']}/h{best['h']} "
            f"net={best['mean_net_bp']:.2f}bp t={best['t']:.2f} "
            f"→ **2027-01-11 / 2027-07-01 잠금 TEST 판정 대상 (자동 승격 없음 — R3)**\n")
    with open(PROMO_MD, "a") as f:
        f.write(line)


def process_candidate(st, cand, seq, date_str, t0):
    cycle = st["cycle"]
    cid = f"d{cycle:03d}{seq:02d}_" + dt.datetime.now(dt.timezone.utc).strftime("%H%M%S")
    # Phase 2 (T5 발견 보완): 사전등록 단계 실패도 후보 단위 ERROR로 기록 —
    # 전체 사이클 사망 방지 + append-only 정직성 유지. PREREG 미작성 시
    # 백테스트를 실행하지 않음(사전등록-후-실행 순서 원칙 보존).
    prereg, prereg_sha, prereg_note = "", "", ""
    cells, verdict, best = [], "ERROR", None
    try:
        prereg = write_prereg(cand, cid, date_str)
        prereg_sha = E.sha256_text(open(prereg).read())
    except Exception as ex:
        prereg_note = f"prereg_failed: {ex}"
        with open(f"{E.DISC}/errors.log", "a") as f:
            f.write(f"{now_iso()} {cid} PREREG {traceback.format_exc()}\n")
    else:
        try:
            cells = run_candidate(cand)
            verdict, best = E.verdict_from_cells(cells)
        except FileNotFoundError as ex:
            cells, verdict, best = [], "DATA_MISSING", None
            prereg_note = str(ex)
        except Exception as ex:  # 기록 후 계속 — append-only 정직성
            cells, verdict, best = [], "ERROR", None
            with open(f"{E.DISC}/errors.log", "a") as f:
                f.write(f"{now_iso()} {cid} {traceback.format_exc()}\n")
            prereg_note = str(ex)
    rj = f"{E.OUT}/disc_{cid}_results.json"
    rm = f"{E.OUT}/disc_{cid}_results.md"
    json.dump({"cid": cid, "ts": now_iso(), "family": cand["family"],
               "symbols": cand["symbols"], "params": cand["params"],
               "variant": cand.get("variant"), "kind": cand.get("kind"),
               "verdict": verdict, "cells": cells,
               "prereg": prereg, "engine": "NEW2-discovery-v1"},
              open(rj, "w"), ensure_ascii=False, indent=1)
    open(rm, "w").write(results_md(cid, cand, cells, verdict, best))
    best_rec = None
    if best is not None:
        best_rec = {"label": f"{best['sym']}/h{best['h']}",
                    "vn": best["vn_nonov"], "mean_net_bp": best.get("mean_net_bp"),
                    "t": best.get("t"), "p_holm": best.get("p_holm"),
                    "span_means": best.get("span_means")}
    rec = {"ts": now_iso(), "cycle": cycle, "cid": cid, "kind": cand.get("kind", "base"),
           "family": cand["family"], "symbols": cand["symbols"],
           "params": cand["params"], "variant": cand.get("variant"),
           "cand_hash": cand["cand_hash"], "prereg": prereg,
           "prereg_sha256": prereg_sha,
           "results_json": rj, "verdict": verdict, "best_cell": best_rec,
           "n_cells": len(cells)}
    if prereg_note:
        rec["note"] = prereg_note
    append_ledger(rec)
    # 성공 후에만 커서 전진 (크래시 안전)
    if cand.get("kind") == "base":
        st["cursors"][cand["family"]] = cand["fam_idx"] + 1
    st["tested"] += 1
    if verdict == "SURVIVOR":
        if cid not in st["survivors"]:
            st["survivors"].append(cid)
        st["survivor_specs"][cid] = {"family": cand["family"],
                                     "symbols": cand["symbols"],
                                     "params": cand["params"],
                                     "variant": cand.get("variant"),
                                     "best_cell": best_rec}
        if cid not in st["promotion_queue"]:
            st["promotion_queue"].append(cid)
        promote_note(cid, cand, best)
    save_state(st)
    return verdict


def run_stouffer(st, seq):
    """생존 2개 이상 → Stouffer 결합 (해석형)."""
    cands = G.stouffer_pair_candidates(st, max_n=3)
    out = []
    for k, c in enumerate(cands):
        a, b = c["pair"]
        ca, cb = c["cells"]
        ts = [x.get("t") if x else None for x in (ca, cb)]
        vns = [x.get("vn") if x else None for x in (ca, cb)]
        if None in ts or None in vns:
            continue
        z_by_rho = {r: E.z_pool_stouffer(ts, vns, r) for r in (0.0, 0.119, 0.238, 0.5)}
        zmax = max(z_by_rho.values())
        verdict = "SURVIVOR_COMBO" if zmax >= E.Z_M2 else "FAIL_STAT"
        cid = f"stf{st['cycle']:03d}{seq:02d}{k}"
        rj = f"{E.OUT}/disc_{cid}_results.json"
        json.dump({"cid": cid, "ts": now_iso(), "kind": "combo_stouffer",
                   "pair": [a, b], "input_cells": [ca, cb],
                   "z_pool_by_rho": z_by_rho, "z_max": zmax,
                   "z_crit": E.Z_M2, "verdict": verdict,
                   "engine": "NEW2-discovery-v1"}, open(rj, "w"),
                  ensure_ascii=False, indent=1)
        open(f"{E.OUT}/disc_{cid}_results.md", "w").write(
            f"# Stouffer 결합 — {cid} ({now_iso()})\n\n"
            f"- 생존 쌍: {a} + {b}\n- Z_pool(ρ별): `{z_by_rho}`\n"
            f"- Z_max={zmax:.3f} vs 임계 {E.Z_M2} → **{verdict}**\n"
            f"> combo_exhaustive_scan 방법 준용(w=√vn, 양수 t 성분, ρ 그리드 max). "
            f"승격은 2027 잠금 TEST 판정에서만.\n")
        append_ledger({"ts": now_iso(), "cycle": st["cycle"], "cid": cid,
                       "kind": "combo_stouffer", "pair": [a, b],
                       "z_max": zmax, "verdict": verdict, "results_json": rj})
        st["stouffer_done"].append([a, b])
        if verdict == "SURVIVOR_COMBO" and cid not in st["promotion_queue"]:
            st["promotion_queue"].append(cid)
            with open(PROMO_MD, "a") as f:
                f.write(f"- {now_iso()} {cid} Stouffer[{a}+{b}] Z_max={zmax:.3f} "
                        f"→ 2027 잠금 TEST 판정 대상 (자동 승격 없음 — R3)\n")
        save_state(st)
        out.append(verdict)
    return out


def main():
    t0 = time.time()
    st = load_state()
    os.makedirs(f"{E.DISC}/candidates", exist_ok=True)
    date_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    st["cycle"] += 1
    cycle = st["cycle"]

    tested = G.load_tested_hashes()
    batch = G.next_batch(st, BATCH)
    combos = G.combo_candidates(st, tested, COMBO_BATCH)
    for c in combos:
        c["fam_idx"] = -1  # 조합 후보는 커서 개념 없음 (장부 해시로 중복 관리)

    verdicts = {}
    seq = 0
    for cand in batch + combos:
        if time.time() - t0 > BUDGET_S:
            break
        seq += 1
        v = process_candidate(st, cand, seq, date_str, t0)
        verdicts[v] = verdicts.get(v, 0) + 1

    stf = run_stouffer(st, seq) if len(st["survivors"]) >= 2 else []
    for v in stf:
        verdicts[v] = verdicts.get(v, 0) + 1

    n_error = verdicts.get("ERROR", 0)
    st["last_ts"] = now_iso()
    st["last_status"] = "partial" if n_error else "ok"
    # F-23 종결: 상태 카운터를 사이클 말미에 원장에서 재계산 (원장 = 유일한 진실)
    # tested = 고유 해시 수, tested_rows = 원장 행 수
    tested_now = G.load_tested_hashes()
    rows_now = sum(1 for ln in open(LEDGER) if ln.strip())
    st["tested_rows"] = rows_now
    st["tested"] = len(tested_now)
    save_state(st)

    universe = G.family_universe_size()
    total_universe = sum(universe.values())
    digest = (f"## Cycle {cycle} — {now_iso()}\n"
              f"- tested={len(tested_now)}/{total_universe}(문법 유니버스) "
              f"verdicts={verdicts} survivors_total={len(st['survivors'])}\n"
              f"- 패밀리 커서: {json.dumps(st['cursors'])}\n")
    with open(CYCLES_MD, "a") as f:
        f.write(digest + "\n")
    # F-08/F-35: 후보 단위 오류(ERROR)가 있으면 CYCLE_PARTIAL + 종료코드 2.
    # DATA_MISSING은 데이터 상태(재검증 대상)이지 코드 오류가 아니므로 ok로 유지 —
    # 재검증 정책은 Phase 3에서 큐로 구현.
    if n_error:
        print(f"CYCLE_PARTIAL cycle={cycle} errors={n_error} "
              f"tested={len(tested_now)}/{total_universe} "
              f"verdicts={json.dumps(verdicts)} survivors={len(st['survivors'])} "
              f"dur={time.time()-t0:.0f}s")
        sys.exit(2)
    print(f"CYCLE_OK cycle={cycle} tested={len(tested_now)}/{total_universe} "
          f"verdicts={json.dumps(verdicts)} survivors={len(st['survivors'])} "
          f"dur={time.time()-t0:.0f}s")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        with open(f"{E.DISC}/errors.log", "a") as f:
            f.write(f"{now_iso()} FATAL {traceback.format_exc()}\n")
        print("CYCLE_FAILED (fatal — errors.log 참조)")
        sys.exit(1)
