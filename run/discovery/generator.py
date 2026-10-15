#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NEW2 자율탐색 엔진 — 후보 생성기 (grammar-based, 결정론적)
==========================================================
패밀리 문법 × 심볼 페어(5쌍 순환) × 파라미터 그리드 → 사전등록 가능한 후보 열거.
이미 시도된 후보는 discovery_ledger.jsonl의 cand_hash로 중복 제거 (append-only 장부).
F2/X6 사전 측정분(BTC/ETH × (10,90)/(20,80) × h{4,16,48})은 배제 목록으로 중복 방지.
조합 단계: 생존 전략 풀에서 ①파라미터 섭동 ②국면 필터 변형 ③Stouffer 풀 결합 생성.

F-03 (v2-audit3): 판정 기반 후보 분류 — classify_ledger()가 원장을 최신 판정 기준으로
분류한다. SURVIVOR/FAIL_*는 tested 확정, UNDETERMINED_POWER/DATA_MISSING/ERROR는
재검증 큐로 분리(후보당 최대 REVERIFY_MAX_RETRIES=2회 재시도, 초과 시 tested 확정).
재검증 판정은 최초 판정을 덮어쓰지 않고 새 행(kind:"reverify", retry_count 포함)으로
기록되므로 각 해시의 '최신 행'이 현재 상태를 결정한다. 큐는 원장에서 매 사이클
재유도된다(state는 파생 캐시 — 크래시 안전).

커서 규약 (크래시 안전): next_batch는 커서를 건드리지 않고 (fam, idx)를 반환.
runner는 장부 append 성공 후에만 state["cursors"][fam] = idx+1 로 전진 →
중간 크래시 시 다음 사이클이 동일 후보를 재시도 (장부가 유일한 진실).
"""
import json
import os

import engine_lib as E

DISC = E.DISC
LEDGER = f"{DISC}/discovery_ledger.jsonl"

# F2/X6 기존 측정분 배제: BTC/ETH 페어 × 임계(10,90)/(20,80) × h{4,16,48}
FNG_EXCLUDE_PAIRS = {("BTCUSDT", "ETHUSDT")}
FNG_EXCLUDE_THR = {(10, 90), (20, 80)}
FNG_EXCLUDE_H = {4, 16, 48}


def build_grid():
    """family → [params, ...] 결정론적 순서."""
    g = {}
    g["flow_z"] = [
        {"L": L, "thr": thr, "mode": mode, "horizons": [h]}
        for mode in ("mom", "rev")
        for L in (16, 96, 672)
        for thr in (1.2816, 1.6449, 2.3263)
        for h in (4, 16, 48, 96)
    ]
    g["ret_rev"] = [
        {"k": k, "thr": thr, "horizons": [h]}
        for k in (4, 16, 48)
        for thr in (1.6449, 2.3263)
        for h in (4, 16, 48, 96)
    ]
    g["ts_mom"] = [
        {"L": L, "thr": thr, "horizons": [h]}
        for L in (672, 2880)
        for thr in (1.0, 1.5)
        for h in (16, 48, 192)
    ]
    g["vol_break"] = [
        {"Lv": Lv, "thr": thr, "horizons": [h]}
        for Lv in (96, 672)
        for thr in (1.5, 2.0)
        for h in (4, 16, 48)
    ]
    g["tod"] = [
        {"sess": s, "dir": d, "horizons": [h]}
        for s, (h0, h1) in {"ASIA": (0, 8), "EU": (8, 16), "US": (16, 24)}.items()
        for d in ("long", "short")
        for h in (16, 48)
    ]
    g["fund_z"] = [
        {"zthr": zt, "horizons": [h]}
        for zt in (1.5, 2.0, 2.5)
        for h in (16, 48, 192)
    ]
    g["fng"] = [
        {"lo": lo, "hi": hi, "horizons": [h]}
        for (lo, hi) in ((5, 95), (10, 90), (15, 85), (20, 80))
        for h in (4, 16, 48, 96)
    ]
    return g


def family_order():
    return ["flow_z", "ret_rev", "ts_mom", "vol_break", "tod", "fund_z", "fng"]


def enumerate_family(family):
    """family의 전 후보 (pair 포함) — 결정론적 리스트. 각 항목에 fam_idx 부여."""
    grid = build_grid()[family]
    out = []
    for p in grid:
        for pi, pair in enumerate(E.PAIRS):
            if family == "fng" and _is_fng_excluded(pair, p):
                continue
            out.append({"family": family, "pair_idx": pi, "params": dict(p)})
    return out


def _is_fng_excluded(pair, p):
    if tuple(sorted(pair)) not in FNG_EXCLUDE_PAIRS:
        return False
    if (p["lo"], p["hi"]) not in FNG_EXCLUDE_THR:
        return False
    return p["horizons"][0] in FNG_EXCLUDE_H


def family_universe_size():
    return {f: len(enumerate_family(f)) for f in family_order()}


def load_tested_hashes():
    """원장에 행이 존재하는 모든 후보 해시(판정 무관) — next_batch/조합의 중복
    제안 방지 전용. 재검증 큐 항목도 행이 있으므로 여기서 제외된다(큐로 재시도).
    판정 기반 분류(terminal vs 큐)는 classify_ledger() 참조."""
    tested = set()
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    h = json.loads(line).get("cand_hash")
                except Exception:
                    continue
                if h:
                    tested.add(h)
    return tested


# ── F-03 (Phase 3): 판정 기반 후보 분류 — 재검증 큐 ──
# tested 확정 판정(재시도 없음) vs 재검증 대상 판정의 경계. 엄격 유지:
# UNDETERMINED/데이터누락/오류는 '실패 확정'도 '합격'도 아니므로 데이터 보강 후
# 유한 회수(최대 2회)만 허용하고, 소진 시 tested 확정(큐 무한순환 방지).
REVERIFY_VERDICTS = ("UNDETERMINED_POWER", "DATA_MISSING", "ERROR")
REVERIFY_MAX_RETRIES = 2


def classify_ledger():
    """원장 전 행을 '최신 판정' 기준으로 분류한다 (F-03).
    반환: (tested_terminal, all_hashes, reverify_queue)
      tested_terminal : 더 이상 시도하지 않는 후보 해시 집합 — SURVIVOR/FAIL_* 확정
                        또는 재검증 소진(retry_count ≥ REVERIFY_MAX_RETRIES).
      all_hashes      : 원장에 1개 이상 행이 있는 모든 후보 해시 (중복 제안 방지).
      reverify_queue  : 재검증 대기 목록 — 최신 판정이 REVERIFY_VERDICTS에 속하고
                        retry_count < REVERIFY_MAX_RETRIES. 항목: {cand_hash,
                        family, symbols, params, variant, retry_count, last_verdict}.
                        원장 순서(=최초 발견 순, 오래된 항목 우선) 유지.
    재검증 판정은 새 행(kind:"reverify")으로 추가 기록되므로 최초 판정 행은
    무수정 보존된다(append-only 원칙)."""
    latest = {}
    order = []
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                h = r.get("cand_hash")
                if not h:
                    continue   # fdr_ref 등 비후보 행 제외
                if h not in latest:
                    order.append(h)
                latest[h] = {
                    "cand_hash": h,
                    "family": r.get("family"),
                    "symbols": r.get("symbols"),
                    "params": r.get("params"),
                    "variant": r.get("variant"),
                    "retry_count": int(r.get("retry_count") or 0),
                    "last_verdict": r.get("verdict"),
                }
    tested, queue = set(), []
    for h in order:
        e = latest[h]
        if e["last_verdict"] in REVERIFY_VERDICTS:
            if e["retry_count"] < REVERIFY_MAX_RETRIES:
                queue.append(e)
            else:
                tested.add(h)   # 재검증 소진 → tested 확정 (큐 무한순환 방지)
        else:
            tested.add(h)
    return tested, set(order), queue


def next_batch(state, batch_size):
    """패밀리 라운드로빈 미시도 후보. 반환 항목에 fam_idx 포함, 커서 미변경.
    배치 내 중복 방지: proposed 집합으로 동일 사이클 내 재제안 차단.
    tested = 원장 전 행 해시(재검증 큐 항목 포함) — 큐 항목은 next_batch가 아니라
    재검증 경로로만 재시도된다(이중 제안 차단). 전 후보가 소진되면 빈 배치 반환
    → runner가 UNIVERSE_EXHAUSTED 판정(F-03 고갈 처리)."""
    tested = load_tested_hashes()
    proposed = set(tested)
    batch = []
    fams = family_order()
    lists = {f: enumerate_family(f) for f in fams}
    progress = True
    while len(batch) < batch_size and progress:
        progress = False
        for f in fams:
            if len(batch) >= batch_size:
                break
            lst = lists[f]
            i = state["cursors"].get(f, 0)
            while i < len(lst):
                c = lst[i]
                syms = [E.PAIRS[c["pair_idx"]][0], E.PAIRS[c["pair_idx"]][1]]
                ch = E.cand_hash(c["family"], syms, c["params"])
                if ch in proposed:
                    i += 1
                    continue
                proposed.add(ch)
                batch.append({**c, "symbols": syms, "cand_hash": ch,
                              "kind": "base", "fam_idx": i})
                progress = True
                break
    return batch


# ─────────────────────────────────────────────────────────────
# 조합 단계: 생존 전략 → 변형/결합 후보
# ─────────────────────────────────────────────────────────────
PARAM_AXES = {
    "flow_z": {"L": [16, 96, 672], "thr": [1.2816, 1.6449, 2.3263],
               "mode": ["mom", "rev"]},
    "ret_rev": {"k": [4, 16, 48], "thr": [1.6449, 2.3263]},
    "ts_mom": {"L": [672, 2880], "thr": [1.0, 1.5]},
    "vol_break": {"Lv": [96, 672], "thr": [1.5, 2.0]},
    "tod": {"dir": ["long", "short"]},
    "fund_z": {"zthr": [1.5, 2.0, 2.5]},
    "fng": {"lo": [5, 10, 15, 20], "hi": [80, 85, 90, 95]},
}


def neighbors(family, params, max_n=4):
    """파라미터 축 ±1스텝 섭동 (조합 변형)."""
    out = []
    axes = PARAM_AXES.get(family, {})
    for axis, vals in axes.items():
        if axis not in params:
            continue
        try:
            i = vals.index(params[axis])
        except ValueError:
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals) and vals[j] != params[axis]:
                q = dict(params)
                q[axis] = vals[j]
                out.append(("perturb", {axis: vals[j]}, q))
        if len(out) >= max_n:
            break
    return out[:max_n]


def combo_candidates(state, tested, max_combo):
    """생존 풀 기반 조합 후보: 섭동 + 국면 필터 변형 (실제 이벤트 스터디로 재검증)."""
    out = []
    survivors = state.get("survivors", [])
    if not survivors:
        return out
    specs = state.get("survivor_specs", {})
    for sv_cid in survivors:
        sv = specs.get(sv_cid)
        if not sv:
            continue
        for _tag, delta, q in neighbors(sv["family"], sv["params"]):
            ch = E.cand_hash(sv["family"], sv["symbols"],
                             {**sv["params"], "variant": ("perturb", delta)})
            if ch in tested:
                continue
            out.append({"family": sv["family"], "symbols": sv["symbols"],
                        "params": q, "cand_hash": ch, "kind": "combo",
                        "variant": {"type": "perturb", "delta": delta},
                        "surv_ref": sv_cid})
            if len(out) >= max_combo:
                return out
        for fname in ("vol_calm", "vol_stress"):
            ch = E.cand_hash(sv["family"], sv["symbols"],
                             {**sv["params"], "variant": ("filter", fname)})
            if ch in tested:
                continue
            out.append({"family": sv["family"], "symbols": sv["symbols"],
                        "params": sv["params"], "cand_hash": ch, "kind": "combo",
                        "variant": {"type": "filter", "name": fname},
                        "surv_ref": sv_cid})
            if len(out) >= max_combo:
                return out
    return out


def stouffer_pair_candidates(state, max_n=3):
    """생존 2개 이상 → Stouffer 결합 (해석형, combo_exhaustive_scan 방법 준용:
    w=√vn, ρ 그리드 {0,0.119,0.238,0.5}, max Z_pool, 양수 t 성분만)."""
    survivors = state.get("survivors", [])
    out = []
    if len(survivors) < 2:
        return out
    done_pairs = {tuple(sorted(p)) for p in state.get("stouffer_done", [])}
    specs = state.get("survivor_specs", {})
    for i in range(len(survivors)):
        for j in range(i + 1, len(survivors)):
            a, b = survivors[i], survivors[j]
            if tuple(sorted((a, b))) in done_pairs:
                continue
            sa, sb = specs.get(a), specs.get(b)
            if not sa or not sb:
                continue
            out.append({"kind": "combo_stouffer", "pair": [a, b],
                        "cells": [sa.get("best_cell"), sb.get("best_cell")]})
            if len(out) >= max_n:
                return out
    return out
