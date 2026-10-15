#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NEW2 자율탐색 엔진 — 사이클 오케스트레이터 (v2-audit3)
============================================
1사이클 = [재검증 배치 + 기본 후보 배치 + 조합 후보 배치] 생성 → 각 후보별
  PREREG 사전등록(선) → 이벤트 스터디 백테스트(후) → 게이트 판정 →
  append-only 장부 기록 → results JSON/MD 저장 → 커서 전진.
생존자 발생 시 promotion_queue 적재 (자동 승격 없음 — R3).
생존 2개 이상 시 Stouffer 결합 검정(해석형, R7 고정 ρ) 실행.

Phase 3 (F-03/F-32): 판정 기반 재검증 큐 — UNDETERMINED_POWER/DATA_MISSING/ERROR는
tested 확정이 아니라 재검증 큐로 분리(최대 2회 재시도, kind:"reverify" 행으로 추가
기록 — 최초 판정 덮어쓰기 없음). 다음 base 배치와 큐가 모두 비면 UNIVERSE_EXHAUSTED
기록(생존자 변형/조합은 계속 가능). results JSON에 cand_hash·cycle 필드 추가(F-32).
Phase 4 (R4~R7): 심볼 단위 판정(FAIL_SYMMETRY — 엄격화), 탐색 수준 BH-FDR 참고
기록(게이트 아님·완화 아님), held-out 진단 참고 병기(게이트 미사용), Stouffer
ρ=0.119 사전등록 고정 + w_i=1(F-06/F-07 수정 — 재량 제거).

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
# Phase 3+4 (v2-audit3): 게이트 R4~R7 개정 — 엄격 방향만 (게이트 완화 0).
ENGINE = "NEW2-discovery-v2-audit3"

STATE_PATH = f"{E.DISC}/state.json"
LEDGER = G.LEDGER
CYCLES_MD = f"{E.DISC}/cycles.md"
PROMO_MD = f"{E.DISC}/promotion_queue.md"

BATCH = int(os.environ.get("NEW2_DISC_BATCH", "10"))
COMBO_BATCH = int(os.environ.get("NEW2_DISC_COMBO", "4"))
BUDGET_S = float(os.environ.get("NEW2_DISC_BUDGET", "540"))
# Phase 3 (F-03): 사이클당 재검증 처리 상한 — 오래된 큐 항목부터 처리.
REVERIFY_BATCH = int(os.environ.get("NEW2_DISC_REVERIFY", "4"))

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

GATES_TEXT = """## 게이트 (engine-discovery-v2-audit3 — 순서 고정, v1 대비 엄격화만)
판정 단위(R4): 각 심볼의 셀 모음에 아래 체인을 개별 적용 — 최고 t 셀 단독 합격 금지.
1. POWER: 비중첩 표본 valid_n ≥ 30 (미달 시 UNDETERMINED_POWER — 재검증 큐)
2. COST: 엔진 CandleCostModel o10k 시나리오 비용 차감 후 평균 순수익 > 0
3. CONSIST: 개발 2구간 모두 순수익 부호 양수 (부호 일치)
4. STAT: 후보 내 Holm 보정 단측 p < 0.05 **AND** 승자 저주 수축
   mean_defl = mean − 2.2414×se > 0
규칙 R1: 게이트는 비중첩(non-overlap) 표본으로만 판정, 중첩 통계는 참고 병기.
규칙 R2: 위 수축 조건까지 통과해야 SURVIVOR.
규칙 R3: SURVIVOR는 승격 주장이 아니라 promotion_queue 적재 — 실제 승격 여부는
잠금 TEST 구간에서 2027-01-11 / 2027-07-01 판정 시에만 결정.
규칙 R4 (심볼 정합, F-02 수정 — 엄격화): SURVIVOR는 **모든 심볼**이 위 4게이트를
통과할 때만 부여. 1심볼 통과 + 타 심볼 FAIL_(COST/CONSIST/STAT)이면 합격 없이
FAIL_SYMMETRY로 기록. 타 심볼이 데이터 부족(UNDETERMINED_POWER)이면 전체를
UNDETERMINED_POWER로 분류해 재검증 큐로 보낸다(성급한 FAIL 선언 금지 —
UNDETERMINED는 절대 합격이 아님).
규칙 R5 (탐색 수준 다중검정 참고, F-01 — 게이트 아님·완화 아님): 후보 판정은
위 STAT의 후보 내 Holm을 그대로 유지한다. 별도로 매 사이클 누적 1차 검정 수
N(원장 후보 행 수)과 생존자 1차 p(레거시 결측은 보수적 1.0 처리)에 대한
Benjamini-Hochberg FDR q 최솟값을 state·원장(kind:fdr_ref)에 누적 기록한다.
이는 v1에 전혀 없던 수준 기록의 추가이며 어떤 합격 조건도 완화하지 않는다
(순차 탐색의 BH는 근사 참고 — 최종 권위는 2027 잠금 TEST).
규칙 R6 (held-out 진단, F-15 — 게이트 아님): 개발·잠금 구간 밖 held-out 구간
(BTC 2024-06-19~, ETH 2026-09-01~, 알트 2026-04-21~ — 각 데이터 말미까지)의 raw
통계를 생존자의 통과 심볼 셀에 한해 참고 병기. 선택·조정·판정에 절대 사용하지
않는다(오염 방지 — 2027 잠금 TEST 전 사전 진단 참고만).
규칙 R7 (Stouffer, F-06/F-07 수정 — 재량 제거): 결합 ρ는 사전등록 상수 0.119
고정(ρ 그리드 max 선택 금지). w_i=1 통일: z = (Σ t_i) / sqrt(k + 2ρ·k(k−1)/2).
t는 이미 √n 스케일이므로 v1의 w=√vn 추가 가중(√n 이중계수, F-07)을 제거한다.

## 재검증 큐 (F-03) — kind:"reverify"
UNDETERMINED_POWER / DATA_MISSING / ERROR 후보는 tested 확정이 아니라 재검증
큐로 분리. 데이터 보강 후 이후 사이클에서 재시도하며 후보당 최대 2회
(retry_count 기록, 초과 시 tested 확정 — 큐 무한순환 방지). 재검증 판정은 최초
판정을 덮어쓰지 않고 새 행으로 추가 기록. 다음 base 배치와 큐가 모두 비면
UNIVERSE_EXHAUSTED 기록(생존자 변형/조합은 계속 가능).

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
    커서=패밀리별 마지막 base 행의 fam_idx+1, tested=원장 고유 해시 수,
    tested_rows=후보 행 수(cand_hash 보유 행), reverify_queue=판정 기반 재유도(F-03)."""
    st = {"engine": ENGINE, "cycle": 0, "cursors": {},
          "tested": 0, "tested_rows": 0, "survivors": [], "survivor_specs": {},
          "stouffer_done": [], "promotion_queue": [], "reverify_queue": [],
          "universe_exhausted": None, "fdr_ref": None,
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
                if r.get("cand_hash"):
                    tested.add(r["cand_hash"])
                    st["tested_rows"] += 1
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
    # F-03: 재검증 큐도 원장에서 재유도 (state 유실 무해 — 파생 캐시)
    _, _, st["reverify_queue"] = _G.classify_ledger()
    with open(f"{E.DISC}/reconcile.log", "a") as f:
        f.write(f"{now_iso()} state rebuilt from ledger: cycle={st['cycle']} "
                f"tested={st['tested']} tested_rows={st['tested_rows']} "
                f"reverify_queue={len(st['reverify_queue'])} "
                f"cursors={json.dumps(st['cursors'])}\n")
    return st


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH))
        except Exception:
            # 파손된 state → 원장 기반 재구성 (F-09/F-14)
            return rebuild_state_from_ledger()
    return {"engine": ENGINE, "cycle": 0, "cursors": {},
            "tested": 0, "tested_rows": 0, "survivors": [], "survivor_specs": {},
            "stouffer_done": [], "promotion_queue": [], "reverify_queue": [],
            "universe_exhausted": None, "fdr_ref": None,
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
    if cand.get("kind") == "reverify":
        # F-03: 재검증 시도 사전 고지 — 최초 판정과 재시도 횟수를 PREREG에 명기
        hyp += (f" [재검증 시도 {cand.get('retry_count', 0)}/{G.REVERIFY_MAX_RETRIES} — "
                f"초기 판정: {cand.get('reverify_reason', 'N/A')}; "
                f"데이터 보강 후 유한 재시도(최대 2회), 초과 시 tested 확정]")
    cells = ", ".join(f"{s}/h{h}" for s in cand["symbols"])
    txt = f"""# PREREG — 자율탐색 후보 {cid} ({date_str})

> 본 문서는 백테스트 실행 **이전**에 자동 작성되었으며, 작성 후 내용 수정 금지(append-only).
> 엔진: {ENGINE} · 등급: Phase A 진단 (승격 주장 아님)

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


def run_candidate(cand: dict):
    """후보 백테스트 → (셀 통계 리스트, 심볼×h별 이벤트 배열).
    이벤트 배열은 R6 held-out 진단(생존자 한정)에만 사용된다."""
    cells = []
    evs = {}
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
            evs[(sym, h)] = ev[h]
    return cells, evs


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
            f"{ph if ph is None else round(ph, 5)} | {c.get('sym_verdict', '')} |")
    sym_line = ""
    if best and best.get("sym_verdicts"):
        sym_line = "\n- 심볼별 판정(R4): " + ", ".join(
            f"{s}={v}" for s, v in best["sym_verdicts"].items())
    ho_line = ""
    held = [c for c in cells if c.get("heldout")]
    if held:
        parts = []
        for c in held:
            hh = c["heldout"]
            parts.append(f"{c['sym']}/h{c['h']}: n={hh['n']} "
                         f"mean={hh['mean_net_bp'] if hh['mean_net_bp'] is None else round(hh['mean_net_bp'], 2)}bp")
        ho_line = "\n- held-out 참고(R6 — 게이트 미사용, 2027 잠금 TEST 전 사전 진단): " + "; ".join(parts)
    sp = ""
    if best and best.get("span_means") and all(x is not None for x in best["span_means"]):
        sp = f"\n- 최고 셀 구간별 평균: [{best['span_means'][0]:.2f}, {best['span_means'][1]:.2f}] bp"
    dl = ""
    if best and best.get("mean_defl_bp") is not None:
        dl = f"\n- 수축 후 기대값: {best['mean_defl_bp']:.2f} bp"
    return f"""# 자율탐색 결과 — {cid} ({now_iso()})

- 패밀리: {cand['family']} · 종목: {', '.join(cand['symbols'])}
- 파라미터: `{json.dumps(cand['params'], ensure_ascii=False)}`
- **판정: {verdict}** (게이트: POWER→COST→CONSIST→STAT ×심볼(R4), {ENGINE}){sym_line}{sp}{dl}{ho_line}

| 셀 | n(raw) | valid_n(비중첩) | net bp(o10k) | t | p(Holm) | 심볼 판정(R4) |
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
            cells, evs = run_candidate(cand)
            verdict, best = E.verdict_from_cells(cells)
            # R6 (F-15 진단): 생존자의 '통과 심볼' 셀에 한해 held-out raw 참고 통계
            # 병기. 게이트·선택·조정에 절대 사용하지 않는다(오염 방지 — 2027 잠금
            # TEST 전 사전 진단 참고만). 생존자가 아니면 산출하지 않는다.
            if verdict == "SURVIVOR":
                for c in cells:
                    if c.get("sym_verdict") == "PASS":
                        c["heldout"] = E.heldout_stats(evs[(c["sym"], c["h"])], c["sym"])
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
    # F-32 (Phase 3): results JSON에 cand_hash·cycle 필드 추가 — 원장 재구성 시
    # 재계산 의존 제거. retry_count는 재검증 행 추적용.
    json.dump({"cid": cid, "ts": now_iso(), "cycle": cycle,
               "cand_hash": cand["cand_hash"],
               "retry_count": cand.get("retry_count", 0),
               "family": cand["family"],
               "symbols": cand["symbols"], "params": cand["params"],
               "variant": cand.get("variant"), "kind": cand.get("kind"),
               "verdict": verdict, "cells": cells,
               "prereg": prereg, "engine": ENGINE},
              open(rj, "w"), ensure_ascii=False, indent=1)
    open(rm, "w").write(results_md(cid, cand, cells, verdict, best))
    best_rec = None
    if best is not None:
        best_rec = {"label": f"{best['sym']}/h{best['h']}",
                    "vn": best["vn_nonov"], "mean_net_bp": best.get("mean_net_bp"),
                    "t": best.get("t"), "p_holm": best.get("p_holm"),
                    "span_means": best.get("span_means"),
                    "sym_verdicts": best.get("sym_verdicts")}
    rec = {"ts": now_iso(), "cycle": cycle, "cid": cid, "kind": cand.get("kind", "base"),
           "family": cand["family"], "symbols": cand["symbols"],
           "params": cand["params"], "variant": cand.get("variant"),
           "cand_hash": cand["cand_hash"], "prereg": prereg,
           "prereg_sha256": prereg_sha,
           "results_json": rj, "verdict": verdict, "best_cell": best_rec,
           "n_cells": len(cells)}
    if best is not None and best.get("sym_verdicts"):
        rec["sym_verdicts"] = best["sym_verdicts"]   # R4: 심볼별 세부 판정 기록
    if cand.get("kind") == "reverify":
        # F-03: 재검증 행 — 최초 판정을 덮어쓰지 않고 새 행으로 추가 기록
        rec["retry_count"] = cand["retry_count"]
        rec["reverify_reason"] = cand.get("reverify_reason")
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
    """생존 2개 이상 → Stouffer 결합 (해석형).
    R7 (F-06/F-07 수정): ρ는 사전등록 상수 E.STOUFFER_RHO=0.119 고정 — v1의
    ρ 그리드 max 선택(유리한 ρ 선택 재량)을 제거한다. w_i=1 (t는 이미 √n 스케일
    — √vn 이중계수 제거). 수식과 근거는 engine_lib.z_pool_stouffer 참조."""
    cands = G.stouffer_pair_candidates(st, max_n=3)
    out = []
    for k, c in enumerate(cands):
        a, b = c["pair"]
        ca, cb = c["cells"]
        ts = [x.get("t") if x else None for x in (ca, cb)]
        vns = [x.get("vn") if x else None for x in (ca, cb)]
        if None in ts or None in vns:
            continue
        z = E.z_pool_stouffer(ts, vns, E.STOUFFER_RHO)   # R7: 고정 ρ — max 선택 금지
        verdict = "SURVIVOR_COMBO" if z >= E.Z_M2 else "FAIL_STAT"
        cid = f"stf{st['cycle']:03d}{seq:02d}{k}"
        rj = f"{E.OUT}/disc_{cid}_results.json"
        json.dump({"cid": cid, "ts": now_iso(), "cycle": st["cycle"],
                   "kind": "combo_stouffer",
                   "pair": [a, b], "input_cells": [ca, cb],
                   "rho": E.STOUFFER_RHO,
                   "rho_policy": "R7 사전등록 고정 0.119 (그리드 max 선택 금지 — F-06)",
                   "w_policy": "R7 w_i=1 (t는 √n 스케일 — √vn 이중계수 제거 — F-07)",
                   "z_pool": z, "z_crit": E.Z_M2, "verdict": verdict,
                   "engine": ENGINE}, open(rj, "w"),
                  ensure_ascii=False, indent=1)
        open(f"{E.OUT}/disc_{cid}_results.md", "w").write(
            f"# Stouffer 결합 — {cid} ({now_iso()})\n\n"
            f"- 생존 쌍: {a} + {b}\n"
            f"- ρ={E.STOUFFER_RHO} (R7 사전등록 고정 — 유리한 ρ 선택 금지, F-06)\n"
            f"- Z_pool={z:.3f} vs 임계 {E.Z_M2} → **{verdict}**\n"
            f"> R7: z=(Σt_i)/√(k+2ρ·k(k−1)/2), w_i=1 (t는 이미 √n 스케일 — √vn 이중계수 제거, F-07). "
            f"승격은 2027 잠금 TEST 판정에서만.\n")
        append_ledger({"ts": now_iso(), "cycle": st["cycle"], "cid": cid,
                       "kind": "combo_stouffer", "pair": [a, b],
                       "rho": E.STOUFFER_RHO, "z_pool": z,
                       "verdict": verdict, "results_json": rj})
        st["stouffer_done"].append([a, b])
        if verdict == "SURVIVOR_COMBO" and cid not in st["promotion_queue"]:
            st["promotion_queue"].append(cid)
            with open(PROMO_MD, "a") as f:
                f.write(f"- {now_iso()} {cid} Stouffer[{a}+{b}] Z_pool={z:.3f} "
                        f"(ρ={E.STOUFFER_RHO} 고정) "
                        f"→ 2027 잠금 TEST 판정 대상 (자동 승격 없음 — R3)\n")
        save_state(st)
        out.append(verdict)
    return out


def bh_qmin(ps, N):
    """Benjamini–Hochberg step-up 최소 q: q = min_i (N·p_(i))/i (p 오름차순, i 1 시작).
    R5 참고 지표 전용 수식 — 판정 게이트로 사용하지 않는다. (T8 단위검증 대상)
    근거: BH는 m/N 비율(탐색 중 생존자 수 / 전체 검정 수)로 FDR을 근사 통제하며,
    순차 탐색에서는 보수적 근사임을 GATES_TEXT에 명시했다."""
    ps_sorted = sorted(float(p) for p in ps)
    return min((N * p) / (i + 1) for i, p in enumerate(ps_sorted))


def fdr_reference_update(st, cycle):
    """R5 (F-01): 탐색 전체 수준 다중검정 '참고' 지표의 누적 기록 (게이트 아님).
    판정은 후보 내 Holm으로 유지한다 — 본 함수는 어떤 합격 조건도 변경하지 않는다
    (완화 아님: v1에 전혀 없던 탐색 수준 기록의 '추가'이며, 생존 판정 게이트 불변).
    · N = 누적 프리등록 1차 검정 수 (원장 후보 행 수 — cand_hash 보유 행).
    · 생존자 1차 p = best_cell.p_holm (레거시/결측은 보수적 1.0 처리).
    · 기록처: state.fdr_ref (누적 최신값) + 원장 kind:fdr_ref 행 (영구 이력) +
      cycles.md 다이제스트. SURVIVOR_COMBO(Z 기반)는 p 목록에서 제외(결측 아님 —
      별도 게이트 체계임을 문서화)."""
    ps, n_rows = [], 0
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
                if not r.get("cand_hash"):
                    continue
                n_rows += 1
                if r.get("verdict") == "SURVIVOR":
                    bc = r.get("best_cell") or {}
                    p = bc.get("p_holm")
                    ps.append(float(p) if isinstance(p, (int, float)) else 1.0)
    if n_rows == 0 or not ps:
        return None
    q = min(bh_qmin(ps, n_rows), 1.0)
    ref = {"N": n_rows, "n_surv": len(ps), "bh_q_min": round(q, 6),
           "bh_alpha": 0.05, "meets_0.05": bool(q < 0.05), "ts": now_iso(),
           "gate": False,
           "note": "R5 탐색 수준 FDR 참고 지표 — 판정은 Holm(게이트 불변, 완화 아님)"}
    st["fdr_ref"] = ref
    append_ledger({"ts": now_iso(), "cycle": cycle, "kind": "fdr_ref",
                   "N": ref["N"], "n_surv": ref["n_surv"],
                   "bh_q_min": ref["bh_q_min"], "meets_0.05": ref["meets_0.05"],
                   "note": "R5 탐색 수준 FDR 참고 지표 — 판정 게이트 아님(완화 아님)"})
    return ref


def main():
    t0 = time.time()
    st = load_state()
    st["engine"] = ENGINE   # 엔진 라벨 매 사이클 갱신 (F-25 후속 — runner·results 통일)
    os.makedirs(f"{E.DISC}/candidates", exist_ok=True)
    date_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    st["cycle"] += 1
    cycle = st["cycle"]

    tested = G.load_tested_hashes()   # 원장 전 행 해시 (중복 제안 방지 — 판정 무관)
    # F-03: 판정 기반 분류 → 재검증 큐 (state는 파생 캐시, 원장이 유일한 진실)
    _, _, reverify = G.classify_ledger()
    st["reverify_queue"] = reverify
    rv_batch = []   # 오래된 큐 항목부터 상한 내 처리 (재시도 1회 = retry_count+1)
    for e in reverify[:REVERIFY_BATCH]:
        rv_batch.append({"family": e["family"], "symbols": e["symbols"],
                         "params": e["params"], "variant": e.get("variant"),
                         "cand_hash": e["cand_hash"], "kind": "reverify",
                         "fam_idx": -1, "retry_count": e["retry_count"] + 1,
                         "reverify_reason": e["last_verdict"]})
    batch = G.next_batch(st, BATCH)
    base_empty = (len(batch) == 0)
    combos = G.combo_candidates(st, tested, COMBO_BATCH)
    for c in combos:
        c["fam_idx"] = -1  # 조합 후보는 커서 개념 없음 (장부 해시로 중복 관리)

    verdicts = {}
    seq = 0
    for cand in rv_batch + batch + combos:   # 재검증(오래된 부채) → base → 조합
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
    # tested = 고유 해시 수, tested_rows = 후보 행 수(cand_hash 보유 — fdr_ref 등
    # 비후보 행은 제외 — 행 수 정의 일관성 유지)
    tested_now = G.load_tested_hashes()
    rows_now = 0
    with open(LEDGER) as f:
        for ln in f:
            if not ln.strip():
                continue
            try:
                if json.loads(ln).get("cand_hash"):
                    rows_now += 1
            except Exception:
                pass
    st["tested_rows"] = rows_now
    st["tested"] = len(tested_now)
    # F-03 말미 재분류 (재검증 결과 반영) + 유니버스 고갈 판정
    _, _, reverify_now = G.classify_ledger()
    st["reverify_queue"] = reverify_now
    universe = G.family_universe_size()
    total_universe = sum(universe.values())
    # UNIVERSE_EXHAUSTED: 다음 base 배치 없음 + 재검증 큐 비움.
    # (생존자 변형/조합은 계속 가능 — 고갈은 base 유니버스 기준 판정)
    exhausted = base_empty and not reverify_now
    if exhausted:
        ue = st.get("universe_exhausted") or {}
        st["universe_exhausted"] = {
            "first_cycle": ue.get("first_cycle", cycle), "ts": now_iso(),
            "cycle": cycle, "tested_unique": len(tested_now),
            "universe_total": total_universe}
    # R5 (F-01): 탐색 수준 FDR 참고 지표 — 게이트 아님(판정은 Holm 유지, 완화 아님).
    # 후보 행 수 계산 '후' 호출(N 정의 일관성) — kind:fdr_ref 행을 원장에 추가한다.
    fdr_ref = fdr_reference_update(st, cycle) if os.path.exists(LEDGER) else None
    save_state(st)

    fdr_line = ""
    if fdr_ref:
        fdr_line = (f"- R5 FDR 참고(게이트 아님): N={fdr_ref['N']} "
                    f"surv={fdr_ref['n_surv']} bh_q_min={fdr_ref['bh_q_min']} "
                    f"meets_0.05={fdr_ref['meets_0.05']}\n")
    digest = (f"## Cycle {cycle} — {now_iso()} [{ENGINE}]\n"
              f"- tested={len(tested_now)}/{total_universe}(문법 유니버스) "
              f"verdicts={verdicts} survivors_total={len(st['survivors'])} "
              f"reverify_queue={len(reverify_now)} "
              f"universe_exhausted={'Y' if exhausted else 'N'}\n"
              f"{fdr_line}"
              f"- 패밀리 커서: {json.dumps(st['cursors'])}\n")
    with open(CYCLES_MD, "a") as f:
        f.write(digest + "\n")
    # F-08/F-35: 후보 단위 오류(ERROR)가 있으면 CYCLE_PARTIAL + 종료코드 2.
    # DATA_MISSING/UNDETERMINED_POWER는 재검증 큐(F-03)로 자동 분리되므로 ok —
    # 데이터 상태이지 코드 오류가 아니다.
    ex_tok = "UNIVERSE_EXHAUSTED " if exhausted else ""
    if n_error:
        print(f"CYCLE_PARTIAL cycle={cycle} {ex_tok}errors={n_error} "
              f"tested={len(tested_now)}/{total_universe} "
              f"reverify_queue={len(reverify_now)} "
              f"verdicts={json.dumps(verdicts)} survivors={len(st['survivors'])} "
              f"dur={time.time()-t0:.0f}s")
        sys.exit(2)
    print(f"CYCLE_OK cycle={cycle} {ex_tok}tested={len(tested_now)}/{total_universe} "
          f"reverify_queue={len(reverify_now)} "
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
