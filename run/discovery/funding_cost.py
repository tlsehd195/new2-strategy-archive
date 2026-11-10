#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NEW2 감사 — 펀딩 보유비용 모듈 (F-16 / R8 재구현)
==================================================
engine_lib.cost_vec는 fee+spread+impact만 반영하고 보유 구간의 펀딩 비용은
미반영이다(F-16 확정). 본 모듈은 R8(펀딩 보유비용)을 엔진 잠금 스펙과 분리된
독립 순수함수로 재구현한다 — engine_lib 수정 없음, import 의존 없음(순수 numpy).

규약 (R8 규격):
  - 보유 구간은 반개구간 (entry_ms, exit_ms]: entry 시점 펀딩 제외(진입 직전
    정산분은 지불 대상 아님), exit 시점 펀딩 포함.
  - 부호(포지션 관점): funding rate r>0 → 롱이 숏에게 지불.
      direction=+1 (롱): 기여분 = -r   (지불 → 비용)
      direction=-1 (숏): 기여분 = +r   (수령 → 수익)
    즉 funding_ret = -direction * Σ r.
  - 간격: 심볼별 funding_interval_hours 열을 행 단위로 존중(8h 기본, 일부 심볼은
    변동성 국기간 2h/4h 전환 이력 있음 — 실측: SOLUSDT 2/4/8h 혼재).
  - fail-closed: 보유 구간 내 펀딩 이벤트 결측 의심(간격 규칙 위반) 시
    complete=False — 호출자(포트폴리오 시뮬레이터)는 이 이벤트를 스킵해야 한다.
    결측분을 0으로 간주해 비용을 과소계산하지 않는 것이 원칙.

엔진 연동(게이트 R8 편입)은 병행 서브에이전트(Task 57, Phase 4)가 담당하며,
본 모듈은 import 부작용이 없으므로 안전하게 임포트 가능하다.

단위 테스트: `python3 funding_cost.py` → 합성 데이터 수학 검증(파일 I/O 없음).
  1) 구간 경계 반개구간 (entry 제외·exit 포함)
  2) 부호 (롱 지불/숏 수령, 음수 펀딩 역전)
  3) 누적 (다중 이벤트 합산 + 8h vs 4h 간격 반영)
  4) 결측 fail-closed (중간 이벤트 결측 → complete=False)
  5) klines 커버리지 플래그 (entry/exit 미존재·내부 갭 계수)
"""
import numpy as np
import pandas as pd

BAR_MS = 900_000                    # 15m 봉 (engine_lib 잠금 상수와 동일값)
H_MS = 3_600_000
DEFAULT_INTERVAL_MS = 8 * H_MS      # 기본 펀딩 간격 8h
GAP_TOL = 1.5                       # 결측 판정: 허용 간격 배수
JITTER_MS = 60_000                  # 펀딩 타임스탬프 지터 허용(실측 ±수십 ms)


def _rates_and_intervals(df_funding):
    """(calc_time int64 오름차순, rate float, interval_ms float) 반환."""
    if "calc_time" not in df_funding.columns:
        raise KeyError("df_funding에 calc_time 열 없음")
    rcol = None
    for c in ("rate", "last_funding_rate"):
        if c in df_funding.columns:
            rcol = c
            break
    if rcol is None:
        raise KeyError("df_funding에 rate/last_funding_rate 열 없음")
    t = pd.to_numeric(df_funding["calc_time"], errors="coerce")
    r = pd.to_numeric(df_funding[rcol], errors="coerce")
    ok = t.notna() & r.notna()
    t = t[ok].astype(np.int64).to_numpy()
    r = r[ok].astype(float).to_numpy()
    order = np.argsort(t, kind="stable")
    t, r = t[order], r[order]
    if "funding_interval_hours" in df_funding.columns:
        iv = pd.to_numeric(df_funding["funding_interval_hours"],
                           errors="coerce").to_numpy(float)
        iv = iv[ok][order] * H_MS
    else:
        iv = np.full(len(t), np.nan)
    return t, r, iv


def _fallback_interval_ms(t, iv):
    """행 단위 간격이 없을 때의 폴백: 간격 열 최빈값 → 관측 diff 중앙값 → 8h."""
    fin = iv[np.isfinite(iv) & (iv > 0)]
    if len(fin):
        vals, cnts = np.unique(fin, return_counts=True)
        return float(vals[np.argmax(cnts)])
    if len(t) >= 2:
        d = np.diff(t)
        d = d[(d > 0) & np.isfinite(d)]
        if len(d):
            return float(np.median(d))
    return float(DEFAULT_INTERVAL_MS)


def prepare_pre(df_klines, df_funding):
    """대량 호출용 사전 계산(포트폴리오 시뮬레이터 등):
    정렬된 klines open_time과 funding (ts, rate, interval) 배열을 1회만 계산.
    funding_cost_detail(..., pre=prepare_pre(kl, fnd)) 형태로 재사용 —
    이벤트 수만큼 정렬을 반복하지 않음. 결과는 입력 df 불변성에 의존한다."""
    kot = None
    if df_klines is not None:
        kot = np.sort(pd.to_numeric(df_klines["open_time"], errors="coerce")
                      .dropna().astype(np.int64).to_numpy())
        kot = np.unique(kot)
    return {"kot": kot, "fnd": _rates_and_intervals(df_funding)}


def funding_cost(df_klines, df_funding, entry_ms, exit_ms, direction=1):
    """보유 구간 (entry_ms, exit_ms]의 펀딩 합(포지션 관점 부호 포함) — float.

    direction: +1 롱(펀딩 지불 → 음수 기여), -1 숏(수령 → 양수 기여).
    상세(fail-closed 완결성 플래그 등)가 필요하면 funding_cost_detail 사용.
    """
    return funding_cost_detail(df_klines, df_funding, entry_ms, exit_ms,
                               direction=direction)["funding_ret"]


def funding_cost_detail(df_klines, df_funding, entry_ms, exit_ms,
                        direction=1, return_events=False, pre=None):
    """R8 펀딩 보유비용 상세.

    반환 dict:
      funding_ret   포지션 관점 펀딩 기여분(수익률 frac, 미할증) — -direction*Σr
      sum_rates     구간 내 rate 원합 (지불자 관점 부호 그대로)
      n_events      구간 (entry, exit] 내 펀딩 이벤트 수
      complete      fail-closed 플래그: 간격 규칙상 결측 의심 없음=True
      gaps          결측 의심 위치 목록 [{after_ts, gap_ms, allowed_ms}]
      interval_ms   폴백 간격(ms) — 행 단위 간격 결측 시 사용
      kline_covered entry/exit 가 모두 klines open_time에 존재 (df_klines=None이면 None)
      kline_gap_count  [entry, exit] 내 15m 연속성 위반 경계 수
      kline_missing_bars  위반으로 인한 소실 봉 수 합
    결측 판정 규칙: 인접 펀딩 이벤트 간 간격이 (직전 행의 자체 간격 × 1.5 + 60s)
    초과 시 결측으로 간주. 경계(진입 직후·청산 직전)도 동일 규칙 적용.
    간격 전환 경계(예: 8h→2h)에서 보수 방향 오타 가능 — fail-closed 설계상 안전.
    """
    if direction not in (1, -1):
        raise ValueError(f"direction은 ±1: {direction}")
    entry_ms = int(entry_ms)
    exit_ms = int(exit_ms)
    if exit_ms <= entry_ms:
        raise ValueError(f"exit_ms({exit_ms}) <= entry_ms({entry_ms})")

    if pre is not None:
        ts_all, r_all, iv_all = pre["fnd"]
        kot = pre["kot"]
    else:
        ts_all, r_all, iv_all = _rates_and_intervals(df_funding)
        kot = None
    fb_iv = _fallback_interval_ms(ts_all, iv_all) if len(ts_all) else float(DEFAULT_INTERVAL_MS)

    lo = int(np.searchsorted(ts_all, entry_ms, side="right"))
    hi = int(np.searchsorted(ts_all, exit_ms, side="right"))
    ts, rs, ivs = ts_all[lo:hi], r_all[lo:hi], iv_all[lo:hi]
    n = len(ts)
    sum_rates = float(rs.sum()) if n else 0.0

    gaps = []
    complete = True
    if n == 0:
        # 데이터 시작 전 구간이거나 실결측. 보유가 정산 간격 1배를 넘으면 정산이
        # 최소 한 번은 있어야 한다(ADR 0070: 이전 1.5배 기준은 8~12h 보유의 누락 정산을 놓침).
        if (exit_ms - entry_ms) > fb_iv + JITTER_MS:
            complete = False
            gaps.append({"after_ts": int(entry_ms), "gap_ms": int(exit_ms - entry_ms),
                         "allowed_ms": int(fb_iv + JITTER_MS),
                         "where": "no_events_in_window"})
    else:
        # 진입 경계: 윈도우 직전 마지막 이벤트(prev)와 첫 이벤트 간 간격
        prev = lo - 1
        if prev >= 0:
            iv_prev = iv_all[prev] if np.isfinite(iv_all[prev]) else fb_iv
            gap0 = int(ts[0] - ts_all[prev])
            if gap0 > GAP_TOL * iv_prev + JITTER_MS:
                complete = False
                gaps.append({"after_ts": int(ts_all[prev]), "gap_ms": gap0,
                             "allowed_ms": int(GAP_TOL * iv_prev + JITTER_MS),
                             "where": "entry_boundary"})
        else:
            # 데이터 시작 자체가 윈도우 내: 이전 데이터가 존재하면 결측 의심
            if ts_all[0] != ts[0] and int(ts[0] - entry_ms) > GAP_TOL * fb_iv + JITTER_MS:
                complete = False
                gaps.append({"after_ts": int(entry_ms), "gap_ms": int(ts[0] - entry_ms),
                             "allowed_ms": int(GAP_TOL * fb_iv + JITTER_MS),
                             "where": "before_data_start"})
        # 내부 간격 (행 단위 자체 간격 존중)
        if n >= 2:
            d = np.diff(ts)
            iv_prev_arr = np.where(np.isfinite(ivs[:-1]) & (ivs[:-1] > 0), ivs[:-1], fb_iv)
            bad = d > (GAP_TOL * iv_prev_arr + JITTER_MS)
            if bad.any():
                complete = False
                for j in np.where(bad)[0][:10]:
                    gaps.append({"after_ts": int(ts[j]), "gap_ms": int(d[j]),
                                 "allowed_ms": int(GAP_TOL * iv_prev_arr[j] + JITTER_MS),
                                 "where": "internal"})
        # 청산 경계: 마지막 이벤트 이후 청산까지 자체 간격 초과 시 결측 의심
        iv_last = ivs[-1] if np.isfinite(ivs[-1]) and ivs[-1] > 0 else fb_iv
        tail = int(exit_ms - ts[-1])
        if tail > iv_last + JITTER_MS:
            complete = False
            gaps.append({"after_ts": int(ts[-1]), "gap_ms": tail,
                         "allowed_ms": int(iv_last + JITTER_MS),
                         "where": "exit_boundary"})

    # klines 커버리지 (정보 제공 — 판정은 호출자)
    kline_covered = None
    kline_gap_count = None
    kline_missing_bars = None
    if df_klines is not None:
        if kot is None:
            kot = np.sort(pd.to_numeric(df_klines["open_time"], errors="coerce")
                          .dropna().astype(np.int64).to_numpy())
            kot = np.unique(kot)
        i0 = int(np.searchsorted(kot, entry_ms))
        i1 = int(np.searchsorted(kot, exit_ms, side="right"))
        kline_covered = bool(i0 < len(kot) and kot[i0] == entry_ms
                             and i1 > 0 and kot[i1 - 1] == exit_ms)
        seg = kot[kot > entry_ms]
        seg = seg[seg <= exit_ms]
        if len(seg) >= 2:
            d = np.diff(seg)
            bad = d != BAR_MS
            kline_gap_count = int(bad.sum())
            kline_missing_bars = int(np.sum(d[bad & (d > BAR_MS)] // BAR_MS - 1)
                                     if (bad & (d > BAR_MS)).any() else 0)
        else:
            kline_gap_count = 0
            kline_missing_bars = 0

    out = {"funding_ret": -float(direction) * sum_rates,
           "sum_rates": sum_rates, "n_events": int(n),
           "complete": bool(complete), "gaps": gaps,
           "interval_ms": float(fb_iv),
           "kline_covered": kline_covered,
           "kline_gap_count": kline_gap_count,
           "kline_missing_bars": kline_missing_bars,
           "entry_ms": entry_ms, "exit_ms": exit_ms,
           "direction": int(direction), "events": None}
    if return_events:
        out["events"] = [[int(a), float(b)] for a, b in zip(ts, rs)]
    return out


# ─────────────────────────────────────────────────────────────
# 단위 테스트 (합성 데이터 — 파일 I/O 없음, 실측 시장 데이터 아님)
# ─────────────────────────────────────────────────────────────
def _mk_funding(ts_rates, interval_h=8):
    return pd.DataFrame({"calc_time": [t for t, _ in ts_rates],
                         "funding_interval_hours": interval_h,
                         "last_funding_rate": [r for _, r in ts_rates]})


def _mk_klines(t0, t1, drop=None):
    ts = list(range(t0, t1 + BAR_MS, BAR_MS))
    if drop:
        ts = [t for t in ts if t not in drop]
    n = len(ts)
    return pd.DataFrame({"open_time": ts, "open": [1.0] * n})


def _self_test():
    H = H_MS
    t0 = 1_700_000_000_000
    t0 -= t0 % (8 * H)  # 8h 그리드 정렬 (00:00 UTC 유사)
    results = []

    # ── 케이스 1: 구간 경계 반개구간 (entry 제외·exit 포함) ──
    entry, mid, exit_ = t0, t0 + 8 * H, t0 + 16 * H
    fnd = _mk_funding([(entry, 0.01), (mid, 0.02), (exit_, 0.03)])
    kl = _mk_klines(entry, exit_)
    d = funding_cost_detail(kl, fnd, entry, exit_, direction=1, return_events=True)
    assert abs(d["funding_ret"] - (-(0.02 + 0.03))) < 1e-15, d
    assert d["n_events"] == 2 and d["events"][0][0] == mid and d["events"][1][0] == exit_
    assert d["kline_covered"] is True and d["complete"] is True
    d_s = funding_cost_detail(kl, fnd, entry, exit_, direction=-1)
    assert abs(d_s["funding_ret"] - (0.02 + 0.03)) < 1e-15
    results.append("CASE1 half-open 경계: entry 이벤트 제외·exit 이벤트 포함, 롱 −0.05/숏 +0.05 ✓")

    # ── 케이스 2: 부호 (양수 펀딩 → 롱 지불/숏 수령, 음수 펀딩 역전) ──
    fnd2 = _mk_funding([(t0 + 8 * H, 0.0001)])
    kl2 = _mk_klines(t0, t0 + 16 * H)
    assert funding_cost(kl2, fnd2, t0, t0 + 16 * H, direction=1) == -0.0001
    assert funding_cost(kl2, fnd2, t0, t0 + 16 * H, direction=-1) == +0.0001
    fnd2n = _mk_funding([(t0 + 8 * H, -0.0002)])
    assert funding_cost(kl2, fnd2n, t0, t0 + 16 * H, direction=1) == +0.0002
    assert funding_cost(kl2, fnd2n, t0, t0 + 16 * H, direction=-1) == -0.0002
    results.append("CASE2 부호: r>0 롱 −r·숏 +r, r<0 역전 ✓")

    # ── 케이스 3: 누적 + 간격(8h vs 4h) 반영 ──
    rates = [0.0001, 0.0002, -0.0001]  # 08h, 16h, 24h
    fnd3 = _mk_funding([(t0 + (k + 1) * 8 * H, rates[k]) for k in range(3)])
    kl3 = _mk_klines(t0, t0 + 24 * H)
    manual = -(0.0001 + 0.0002 - 0.0001)
    got = funding_cost(kl3, fnd3, t0, t0 + 24 * H, direction=1)
    assert abs(got - manual) < 1e-15, (got, manual)
    assert funding_cost_detail(kl3, fnd3, t0, t0 + 24 * H)["n_events"] == 3
    # 4h 간격 심볼: 같은 24h 창에 이벤트 6개 (t0은 4h 그리드와 정렬됨: t0%4h==0)
    fnd4 = _mk_funding([(t0 + k * 4 * H, 0.000025) for k in range(1, 7)], interval_h=4)
    d4 = funding_cost_detail(kl3, fnd4, t0, t0 + 24 * H, direction=1)
    assert d4["n_events"] == 6, d4["n_events"]
    assert abs(d4["funding_ret"] - (-(6 * 0.000025))) < 1e-15
    results.append("CASE3 누적·간격: 3이벤트 합 −0.0002, 4h 심볼은 동일 창 6이벤트 ✓")

    # ── 케이스 4: 결측 fail-closed ──
    fnd5 = _mk_funding([(t0, 0.0001), (t0 + 16 * H, 0.0001)])  # 08h 결측
    d5 = funding_cost_detail(kl3, fnd5, t0, t0 + 24 * H, direction=1)
    assert d5["complete"] is False and len(d5["gaps"]) >= 1, d5
    # 청산 경계 결측: 마지막 이벤트 08h, 청산 24h+ → 16h 이벤트 결측 의심
    fnd6 = _mk_funding([(t0 + 8 * H, 0.0001)])
    d6 = funding_cost_detail(kl3, fnd6, t0, t0 + 24 * H, direction=1)
    assert d6["complete"] is False
    # 정상 경계: 청산이 마지막 이벤트 + 간격 이내
    fnd7 = _mk_funding([(t0 + 8 * H, 0.0001), (t0 + 16 * H, 0.0001)])
    d7 = funding_cost_detail(kl3, fnd7, t0, t0 + 16 * H, direction=1)
    assert d7["complete"] is True and d7["n_events"] == 2
    # 펀딩 데이터 전무 + 긴 창 → fail-closed
    empty = _mk_funding([])
    d8 = funding_cost_detail(kl3, empty, t0, t0 + 24 * H, direction=1)
    assert d8["complete"] is False and d8["n_events"] == 0
    results.append("CASE4 fail-closed: 중간·청산 경계 결측→complete=False, 정상 경계 True, 전무 데이터 False ✓")

    # ── 케이스 5: klines 커버리지 플래그 ──
    kl5 = _mk_klines(t0, t0 + 24 * H, drop={t0 + 8 * H, t0 + 8 * H + BAR_MS})
    d9 = funding_cost_detail(kl5, fnd3, t0, t0 + 24 * H, direction=1)
    assert d9["kline_covered"] is True and d9["kline_gap_count"] == 1 \
        and d9["kline_missing_bars"] == 2  # 연속 2봉 결측 → 경계 1회·소실 2봉
    d10 = funding_cost_detail(kl5, fnd3, t0 + 8 * H, t0 + 24 * H, direction=1)
    assert d10["kline_covered"] is False  # entry 시각(삭제된 봉)이 klines에 없음
    results.append("CASE5 klines: 내부 갭 계수 1·소실 2봉, entry 미존재→covered=False ✓")

    for r in results:
        print("  " + r)
    print(f"FUNDING_COST SELF-TEST: {len(results)}/5 PASS")
    return results


if __name__ == "__main__":
    _self_test()
