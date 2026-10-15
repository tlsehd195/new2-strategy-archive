#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NEW2 자율탐색 엔진 — 공통 라이브러리 (engine-discovery-v1 잠금 스펙)
=====================================================================
기존 프로젝트 잠금 방법론을 그대로 계승 (변경 금지):
  - 비용: 엔진 CandleCostModel (costs.py:104-117 공식) — FEE 5bp + half-spread 2bp
    + impact 0.1*sqrt(participation), MAX_PART 0.05, 시나리오 o10k/o100k
  - 검정: 세포별 t(단측) + 후보 내 Holm 다중검정 보정
  - 승격 게이트 준용: valid_n>=30, 엔진 비용 차감 후 기대값 양수, 2구간 부호 일치
  - locked/reserved TEST 구간 절대 배제: BTC 2024-03-25~06-18, ETH 2026-04-28~08-31
  - 개발 구간: BTC/ETH는 기존 패밀리 잠금 SPANS, 기타 심볼은 ALT_SPANS (2026-04-20까지)
엔진 자체 신규 규칙 (엄격 방향, 사전 고지):
  - R1: 게이트는 비중첩(non-overlap) 표본으로만 판정 — 중첩 창의 유의성 과대 계상 차단
        (raw 중첩 통계는 참고용으로 병기)
  - R2: 승자 저주 수축 — mean_defl = mean − 2.2414*se > 0 까지 요구
  - R3: 승격은 절대 자동 선언하지 않음 — SURVIVOR는 promotion_queue 적재 후
        2027-01-11 / 2027-07-01 잠금 TEST 판정에서만 처리
v2-audit3 추가 규칙 (감사 Phase 3+4 — 엄격 방향만, 게이트 완화 0):
  - R4 (F-02 수정, 엄격화): 심볼 정합 — SURVIVOR는 후보의 모든 심볼이
        POWER·COST·CONSIST·STAT을 통과할 때만. 1심볼 통과+타 심볼 FAIL_*이면
        FAIL_SYMMETRY (v1의 최고 t 셀 1개 단독 합격 금지).
  - R5 (F-01 참고 지표, 게이트 아님·완화 아님): 후보 내 Holm 판정 유지 + 탐색 수준
        Benjamini-Hochberg FDR q 참고 병기(state/원장 누적 기록 — runner).
  - R6 (F-15 진단, 게이트 아님): 개발·잠금 구간 밖 held-out 구간 raw 통계를
        생존자의 통과 심볼 셀에 한해 참고 병기 (선택/조정/판정 미사용 — 오염 방지).
  - R7 (F-06/F-07 수정, 재량 제거): Stouffer ρ=0.119 사전등록 고정 + w_i=1
        (t는 이미 √n 스케일 — √vn 이중계수 제거, ρ 그리드 max 선택 금지).
데이터: 캐시만 사용 (NEW2_run/data). 네트워크 다운로드 0바이트.
"""
import glob
import hashlib
import json
import math
import os

import numpy as np
import pandas as pd
from scipy import stats

# F-11 수정 (Phase 2): 런타임 루트를 환경변수로 구성 가능. 기본값은 기존 절대 경로와
# 동일 — 방법론 잠금 스펙(비용·통계·게이트·잠금 구간)은 일절 변경 없음.
RUN = os.environ.get("NEW2_ROOT", "/home/z/my-project/NEW2_run")
BASE = f"{RUN}/data"
OUT = f"{RUN}/results"
DISC = f"{RUN}/discovery"

# ── 잠금 상수 ──
BAR_MS = 900_000
FEE, HALF_SPREAD, IMPACT, MAX_PART = 0.0005, 0.0002, 0.1, 0.05
SCEN = {"o10k": 10_000.0, "o100k": 100_000.0}
VN_MIN = 30
Z_M1, Z_M2 = 1.959964, 2.241403
NWIN = 960

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
           "ADAUSDT", "DOGEUSDT", "LINKUSDT", "TRXUSDT", "ZECUSDT"]
PAIRS = [("BTCUSDT", "ETHUSDT"), ("SOLUSDT", "XRPUSDT"), ("BNBUSDT", "ADAUSDT"),
         ("DOGEUSDT", "LINKUSDT"), ("TRXUSDT", "ZECUSDT")]

SPANS = {
    "BTCUSDT": [("2021-05-02", "2022-08-17"), ("2023-04-07", "2024-03-18")],
    "ETHUSDT": [("2021-05-02", "2022-08-17"), ("2024-12-10", "2026-04-20")],
}
ALT_SPANS = [("2021-05-02", "2023-06-01"), ("2023-06-01", "2026-04-20")]

LOCKED = {
    "BTCUSDT": [("2024-03-25", "2024-06-18")],
    "ETHUSDT": [("2026-04-28", "2026-08-31")],
}

# R6 (F-15 진단): held-out 진단 구간 — 개발 구간·잠금 TEST 구간 어디에도 포함되지
# 않는 구간(종료 None = 데이터 말미까지). 게이트에 절대 사용하지 않는다
# (오염 방지 — 2027 잠금 TEST 전 사전 진단 참고 병기 전용).
HELDOUT = {
    "BTCUSDT": [("2024-06-19", None)],   # dev SPANS 종료(2024-03-18)·잠금(06-18) 이후
    "ETHUSDT": [("2026-09-01", None)],   # dev SPANS 종료(2026-04-20)·잠금(08-31) 이후
}

# R7 (F-06/F-07 수정): Stouffer 결합 상관 계수 — 사전등록 고정값.
# v1은 ρ∈{0,0.119,0.238,0.5} 그리드에서 max를 골랐다(유리한 ρ 선택 = 다중검정
# 재량, F-06). v2-audit3부터 운용 코드는 본 상수만 사용한다.
STOUFFER_RHO = 0.119

KLINE_FAMILIES = {"flow_z", "ret_rev", "ts_mom", "vol_break", "tod"}


def ms(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)


def dev_spans(sym: str):
    return SPANS.get(sym, ALT_SPANS)


def sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def cand_hash(family: str, symbols, params: dict) -> str:
    payload = json.dumps({"family": family, "symbols": list(symbols),
                          "params": params}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────
# 데이터 로더 (캐시 전용)
# ─────────────────────────────────────────────────────────────
_kl_cache: dict = {}


def load_klines(sym: str) -> pd.DataFrame:
    if sym in _kl_cache:
        return _kl_cache[sym]
    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    files = sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    if not files:
        raise FileNotFoundError(f"klines 없음: {sym}")
    df = pd.concat([pd.read_csv(f, header=None, names=cols) for f in files],
                   ignore_index=True)
    df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
    for c in ["open_time", "open", "volume", "tbv", "qv"]:
        df[c] = pd.to_numeric(df[c])
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    _kl_cache[sym] = df
    return df


def load_funding(sym: str) -> pd.DataFrame:
    files = sorted(glob.glob(f"{BASE}/funding/{sym}-fundingRate-*.csv"))
    if not files:
        raise FileNotFoundError(f"funding 없음: {sym}")
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df["calc_time"] = pd.to_numeric(df["calc_time"], errors="coerce")
    df = df.dropna(subset=["calc_time"]).copy()
    df["calc_time"] = df["calc_time"].astype(np.int64)
    df["rate"] = pd.to_numeric(df["last_funding_rate"], errors="coerce")
    df = df.dropna(subset=["rate"])
    return df.drop_duplicates("calc_time").sort_values("calc_time").reset_index(drop=True)


def load_fng() -> pd.DataFrame:
    d = json.load(open(f"{RUN}/research/r3_fng_hist.json"))["data"]
    df = pd.DataFrame(d)
    df["ts_ms"] = df["timestamp"].astype(np.int64) * 1000
    df["value"] = pd.to_numeric(df["value"])
    return df.sort_values("ts_ms").reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# 시그널 빌더 — 전부 per-bar int8 배열 (0/±1) 반환
# ─────────────────────────────────────────────────────────────
def _rolling_z(x: np.ndarray, win: int = NWIN) -> np.ndarray:
    s = pd.Series(x)
    m = s.rolling(win).mean().shift(1).to_numpy()
    sd = s.rolling(win).std(ddof=1).shift(1).to_numpy()
    with np.errstate(invalid="ignore"):
        z = np.where((sd > 0) & np.isfinite(sd) & np.isfinite(x), (x - m) / sd, np.nan)
    return z


def sig_flow_z(df: pd.DataFrame, L: int, thr: float, mode: str) -> np.ndarray:
    tbv = df["tbv"].to_numpy(float)
    vol = df["volume"].to_numpy(float)
    ts = pd.Series(tbv).rolling(L).sum().to_numpy()
    vs = pd.Series(vol).rolling(L).sum().to_numpy()
    R = np.where(vs > 0, ts / vs, np.nan)
    z = _rolling_z(R)
    sgn = np.zeros(len(df), dtype=np.int8)
    if mode == "mom":
        sgn[z >= thr] = 1
        sgn[z <= -thr] = -1
    else:  # rev
        sgn[z >= thr] = -1
        sgn[z <= -thr] = 1
    return sgn


def sig_ret_rev(df: pd.DataFrame, k: int, thr: float) -> np.ndarray:
    close = df["close"].to_numpy(float)
    r = pd.Series(close).pct_change(k).to_numpy()
    z = _rolling_z(r)
    sgn = np.zeros(len(df), dtype=np.int8)
    sgn[z >= thr] = -1   # 단기 급등 → 역발상 숏
    sgn[z <= -thr] = 1   # 단기 급락 → 역발상 롱
    return sgn


def sig_ts_mom(df: pd.DataFrame, L: int, thr: float) -> np.ndarray:
    close = df["close"].to_numpy(float)
    r = pd.Series(close).pct_change(L).to_numpy()
    z = _rolling_z(r)
    sgn = np.zeros(len(df), dtype=np.int8)
    sgn[z >= thr] = 1
    sgn[z <= -thr] = -1
    return sgn


def sig_vol_break(df: pd.DataFrame, Lv: int, thr: float) -> np.ndarray:
    close = df["close"].to_numpy(float)
    lr = np.diff(np.log(close), prepend=np.log(close[0]))
    rv = pd.Series(lr).rolling(Lv).std(ddof=1).to_numpy()
    vz = _rolling_z(rv)
    rL = pd.Series(close).pct_change(Lv).to_numpy()
    sgn = np.zeros(len(df), dtype=np.int8)
    m = np.isfinite(vz) & np.isfinite(rL)
    sgn[m & (vz >= thr) & (rL > 0)] = 1    # 변동성 확장 + 상방 돌파 → 모멘텀
    sgn[m & (vz >= thr) & (rL < 0)] = -1
    return sgn


def sig_tod(df: pd.DataFrame, h0: int, h1: int, direction: str) -> np.ndarray:
    kot = df["open_time"].to_numpy(np.int64)
    hour = ((kot // 3_600_000) % 24)
    sgn = np.zeros(len(df), dtype=np.int8)
    base = 1 if direction == "long" else -1
    sgn[(hour >= h0) & (hour < h1)] = base
    return sgn


def sig_funding_z(df: pd.DataFrame, sym: str, zthr: float) -> np.ndarray:
    """8h 펀딩비 z(90일 창) 극단 → 역발상(펀딩 지불 쪽 반대). 펀딩 시각당 첫 15m 봉 1개만 신호."""
    fnd = load_funding(sym)
    rates = fnd["rate"].to_numpy(float)
    z = _rolling_z(rates, win=270)  # 270*8h = 90일
    kot = df["open_time"].to_numpy(np.int64)
    sgn = np.zeros(len(df), dtype=np.int8)
    fts = fnd["calc_time"].to_numpy(np.int64)
    for j in np.where(np.isfinite(z) & (np.abs(z) >= zthr))[0]:
        i0 = int(np.searchsorted(kot, fts[j]))
        if i0 < len(kot):
            sgn[i0] = -1 if z[j] > 0 else 1
    return sgn


def sig_fng(df: pd.DataFrame, lo: int, hi: int) -> np.ndarray:
    """D-1 확정 F&G 값 → D일 첫 15m 봉 1개만 신호 (하루 1이벤트/방향). 역발상."""
    fng = load_fng()
    kot = df["open_time"].to_numpy(np.int64)
    sgn = np.zeros(len(df), dtype=np.int8)
    day_ms = 86_400_000
    vals = fng["value"].to_numpy(int)
    ts = fng["ts_ms"].to_numpy(np.int64)
    for j in range(1, len(fng)):
        v = vals[j - 1]  # D-1 확정값 → D일 적용
        if v <= lo or v >= hi:
            d0 = (ts[j] // day_ms) * day_ms
            i0 = int(np.searchsorted(kot, d0))
            if i0 < len(kot) and kot[i0] < d0 + day_ms:
                if v <= lo:
                    sgn[i0] = 1
                else:
                    sgn[i0] = -1
    return sgn


def apply_filter(df: pd.DataFrame, sgn: np.ndarray, name: str) -> np.ndarray:
    """조합 변형용 국면 필터: vol_calm(vz<1) / vol_stress(vz>=1), Lv=96."""
    if name not in ("vol_calm", "vol_stress"):
        return sgn
    close = df["close"].to_numpy(float)
    lr = np.diff(np.log(close), prepend=np.log(close[0]))
    rv = pd.Series(lr).rolling(96).std(ddof=1).to_numpy()
    vz = _rolling_z(rv)
    out = sgn.copy()
    if name == "vol_calm":
        out[~(vz < 1)] = 0
    else:
        out[~(vz >= 1)] = 0
    return out


# ─────────────────────────────────────────────────────────────
# 이벤트 추출 + 비용 + 통계
# ─────────────────────────────────────────────────────────────
def _contig_ok(kot: np.ndarray, idx: np.ndarray, h: int) -> np.ndarray:
    d = np.diff(kot)
    bad = (d != BAR_MS).astype(np.int64)
    cs = np.concatenate([[0], np.cumsum(bad)])
    hi = idx + h + 1  # diffs idx..idx+h (h+1개) 연속 필요: i→entry→exit
    hi_c = np.minimum(hi, len(bad))
    return (hi <= len(bad)) & (cs[hi_c] - cs[idx] == 0)


def event_study(df: pd.DataFrame, sgn: np.ndarray, horizons, sym: str) -> dict:
    """시그널 봉 i → 진입 i+1 시가 → 청산 i+1+h 시가. locked TEST 구간 진입 배제."""
    kot = df["open_time"].to_numpy(np.int64)
    kop = df["open"].to_numpy(float)
    kqv = df["qv"].to_numpy(float)
    sig_idx = np.where(sgn != 0)[0]
    lms = [(ms(a), ms(b)) for a, b in LOCKED.get(sym, [])]
    out = {}
    for h in horizons:
        idx = sig_idx[_contig_ok(kot, sig_idx, h)]
        ei = idx + 1
        xi = ei + h
        fin = xi < len(kot)
        idx, ei, xi = idx[fin], ei[fin], xi[fin]
        ems = kot[ei]
        keep = np.ones(len(idx), dtype=bool)
        for a, b in lms:
            keep &= ~((ems >= a) & (ems < b))
        idx, ei, xi, ems = idx[keep], ei[keep], xi[keep], ems[keep]
        raw_bp = (kop[xi] - kop[ei]) / kop[ei] * 1e4 * sgn[idx]
        out[h] = {"n": int(len(idx)), "entry_ms": ems, "exit_ms": kot[xi],
                  "sgn": sgn[idx], "raw_bp": raw_bp,
                  "qv_e": kqv[ei], "qv_x": kqv[xi]}
    return out


def cost_vec(ov: float, qv_e: np.ndarray, qv_x: np.ndarray):
    pe = np.where(qv_e > 0, ov / np.maximum(qv_e, 1e-9), np.inf)
    px = np.where(qv_x > 0, ov / np.maximum(qv_x, 1e-9), np.inf)
    ok = (qv_e > 0) & (qv_x > 0) & (pe <= MAX_PART) & (px <= MAX_PART)
    c = 2 * FEE + 2 * HALF_SPREAD + IMPACT * (
        np.sqrt(np.where(ok, pe, 0.0)) + np.sqrt(np.where(ok, px, 0.0)))
    return ok, c * 1e4


def nonoverlap_keep(ems: np.ndarray, h: int) -> np.ndarray:
    """R1 규칙: 시간순 greedy 비중첩 선택 (entry >= 직전 entry + h봉)."""
    keep = np.zeros(len(ems), dtype=bool)
    last = -1
    for j in np.argsort(ems):
        if ems[j] >= last:
            keep[j] = True
            last = ems[j] + h * BAR_MS
    return keep


def holm(pvals) -> np.ndarray:
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    mx = 0.0
    for rank, i2 in enumerate(order):
        mx = max(mx, (m - rank) * p[i2])
        adj[i2] = min(mx, 1.0)
    return adj


def one_sided_p(t: float, vn: int) -> float:
    if vn < 2 or not np.isfinite(t):
        return 1.0
    return float(stats.t.sf(t, df=vn - 1))


def cell_stats(ev_h: dict, h: int, sym: str) -> dict:
    """단일 (sym,h) 셀: 비중첩 표본 게이트 통계 + raw 참고 통계.
    개발 구간 제한: 진입·청산 모두 dev span 내부 (X8 규격 준용)."""
    ems = ev_h["entry_ms"]
    xms = ev_h["exit_ms"]
    raw_bp = ev_h["raw_bp"]
    ok10, c10 = cost_vec(SCEN["o10k"], ev_h["qv_e"], ev_h["qv_x"])
    ok100, c100 = cost_vec(SCEN["o100k"], ev_h["qv_e"], ev_h["qv_x"])
    net10 = raw_bp - c10
    in_span = np.zeros(len(ems), dtype=bool)
    for (a, b) in dev_spans(sym):
        s0, e0 = ms(a), ms(b)
        in_span |= (ems >= s0) & (xms < e0)
    base_idx = np.where(in_span & ok10)[0]
    keep2 = nonoverlap_keep(ems[base_idx], h)
    sel_idx = base_idx[keep2]
    sel = net10[sel_idx]
    keep = sel_idx  # 최종 채택 인덱스 (스팬 내 + 비용가능 + 비중첩)
    vn = int(len(sel))
    st = {"sym": sym, "h": h, "n_raw": int(ev_h["n"]),
          "vn_nonov": vn, "fail_closed_o10k": int((~ok10).sum())}
    if vn >= 2:
        mean = float(np.mean(sel))
        se = float(np.std(sel, ddof=1) / math.sqrt(vn))
        t = mean / se if se > 0 else 0.0
        st.update({"mean_net_bp": mean, "se": se, "t": t,
                   "p": one_sided_p(t, vn)})
        # 구간별 부호 (일관성 게이트용)
        s_means = []
        for (a, b) in dev_spans(sym):
            s0, e0 = ms(a), ms(b)
            m_in = (ems[sel_idx] >= s0) & (xms[sel_idx] < e0)
            if m_in.sum() >= 2:
                s_means.append(float(np.mean(net10[sel_idx][m_in])))
            else:
                s_means.append(None)
        st["span_means"] = s_means
        # raw 중첩 참고 (스팬 내 전체, 비용가능)
        okr = in_span & ok10
        st["raw_mean_bp"] = float(np.mean(net10[okr])) if okr.sum() else None
        st["raw_n"] = int(okr.sum())
    else:
        st.update({"mean_net_bp": None, "se": None, "t": None, "p": 1.0,
                   "span_means": [None, None], "raw_mean_bp": None, "raw_n": 0})
    # o100k 시나리오 (참고 — 비용벽 가혹 시나리오)
    sel100 = (raw_bp - c100)[sel_idx[ok100[sel_idx]]]
    st["mean_net_o100k_bp"] = float(np.mean(sel100)) if len(sel100) >= 2 else None
    return st


def _symbol_gate(cells_sym: list):
    """R4 보조: 단일 심볼 셀 모음에 게이트 체인 적용 (순서 고정 POWER→COST→
    CONSIST→STAT). 반환: (심볼 판정, 해당 심볼 최고 t 셀).
    PASS = 4게이트 모두 통과. v1과 동일한 게이트 조건 — 적용 단위만 심볼로 확대."""
    fin = [c for c in cells_sym if c.get("t") is not None]
    if not fin:
        return "UNDETERMINED_POWER", None
    best = max(fin, key=lambda c: c["t"])
    if best["vn_nonov"] < VN_MIN:
        return "UNDETERMINED_POWER", best
    if best["mean_net_bp"] is None or best["mean_net_bp"] <= 0:
        return "FAIL_COST", best
    sm = best["span_means"]
    if not (len(sm) == 2 and sm[0] is not None and sm[1] is not None
            and sm[0] > 0 and sm[1] > 0):
        return "FAIL_CONSIST", best
    mean_defl = best["mean_net_bp"] - Z_M2 * best["se"]
    best["mean_defl_bp"] = mean_defl
    if best["p_holm"] >= 0.05 or mean_defl <= 0:
        return "FAIL_STAT", best
    return "PASS", best


# 전체 판정 우선순위 (게이트 순서 — 전 심볼 FAIL 시 가장 앞 게이트 실패를 대표 판정으로)
_GATE_ORDER = ("UNDETERMINED_POWER", "FAIL_COST", "FAIL_CONSIST", "FAIL_STAT")


def verdict_from_cells(cells: list):
    """R4 (F-02 수정, v2-audit3 — 엄격화): 심볼 단위 판정.
    각 심볼의 셀 모음에 게이트 체인(POWER→COST→CONSIST→STAT)을 개별 적용한다.
      · SURVIVOR: 모든 심볼이 4게이트 통과할 때만 부여 (v1은 최고 t 셀 1개 통과로
        합격 가능 → 1심볼 통과+타 심볼 FAIL_*이면 FAIL_SYMMETRY로 정직 기록).
      · UNDETERMINED_POWER: 어느 심볼이든 데이터 부족(vn<30/셀 없음)이면 전체를
        미결정으로 분류해 재검증 큐로 보낸다(성급한 FAIL 선언 금지 — 데이터 보강
        후 재판정; 게이트 완화 아님 — UNDETERMINED는 절대 합격이 아님).
      · 전 심볼 FAIL: 게이트 순서 우선(POWER>COST>CONSIST>STAT)으로 대표 판정.
    Holm 보정은 기존대로 후보 내 전 셀에 공통 적용한다.
    셀에는 sym_verdict(심볼별 판정), 최고 셀에는 sym_verdicts 요약을 기록한다."""
    fin = [c for c in cells if c.get("t") is not None]
    if fin:
        # Holm (후보 내 전 셀 — 기존 규격 유지)
        ps = [max(c["p"], 1e-300) for c in fin]
        padj = holm(ps)
        for c, p in zip(fin, padj):
            c["p_holm"] = float(p)
    by_sym = {}
    for c in cells:
        by_sym.setdefault(c["sym"], []).append(c)
    if not by_sym:
        return "UNDETERMINED_POWER", None
    sv, bests = {}, {}
    for sym, cs in by_sym.items():
        v, b = _symbol_gate(cs)
        sv[sym] = v
        for c in cs:
            c["sym_verdict"] = v
        if b is not None:
            bests[sym] = b
    if all(v == "PASS" for v in sv.values()):
        verdict = "SURVIVOR"
    elif any(v == "UNDETERMINED_POWER" for v in sv.values()):
        verdict = "UNDETERMINED_POWER"
    else:
        passes = [s for s in sv if sv[s] == "PASS"]
        fails = [s for s in sv if sv[s] != "PASS"]
        if passes and fails:
            verdict = "FAIL_SYMMETRY"   # 1심볼 통과 + 1심볼 불통 — 정직 기록 (R4)
        else:
            verdict = min((sv[s] for s in fails), key=lambda v: _GATE_ORDER.index(v))
    best = max(bests.values(), key=lambda c: c["t"]) if bests else None
    if best is not None:
        best["sym_verdicts"] = dict(sv)   # 심볼별 세부 판정 요약 (원장/results 직렬화용)
    return verdict, best


def heldout_spans(sym: str):
    """R6: 심볼의 held-out 진단 구간 (개발 구간·잠금 TEST 구간 밖, 종료 None=데이터 말미).
    알트는 ALT_SPANS 종료(2026-04-20) 익일부터 — 잠금 구간 없음."""
    return HELDOUT.get(sym, [("2026-04-21", None)])


def heldout_stats(ev_h: dict, sym: str) -> dict:
    """R6 (F-15 진단): held-out 구간 raw 참고 통계 — 생존자의 통과 심볼 셀에 한해
    산출·병기된다(runner process_candidate). 게이트에 절대 사용하지 않는다
    (선택·조정·판정 미사용 — 2027 잠금 TEST 전 사전 진단 참고만, 오염 방지).
    통계: 비용가능(o10k) 진입 전체(중첩 허용 raw)의 순수익 평균 + 표본 수."""
    ems = ev_h["entry_ms"]
    xms = ev_h["exit_ms"]
    raw_bp = ev_h["raw_bp"]
    ok10, c10 = cost_vec(SCEN["o10k"], ev_h["qv_e"], ev_h["qv_x"])
    net10 = raw_bp - c10
    inh = np.zeros(len(ems), dtype=bool)
    for (a, b) in heldout_spans(sym):
        s0 = ms(a)
        inh |= (ems >= s0) if b is None else ((ems >= s0) & (xms < ms(b)))
    m = inh & ok10
    if not m.any():
        return {"n": 0, "mean_net_bp": None}
    return {"n": int(m.sum()), "mean_net_bp": float(np.mean(net10[m]))}


def z_pool_stouffer(ts, vns, rho: float = None) -> float:
    """R7 (F-06/F-07 수정, v2-audit3): Stouffer 결합 — 양수 t 성분만 (쌍대 음수 불가).

        z = (Σ t_i) / sqrt( k + 2ρ·k(k−1)/2 )

    · w_i = 1 통일: t 통계량은 이미 √n 스케일이므로 v1의 w=√vn 추가 가중은
      √n·√n = n 이중계수가 된다(F-07) → 제거하고 등가중 결합으로 고정.
      var = Σ w_i² + 2ρ·(쌍 개수) = k + 2ρ·k(k−1)/2.
    · ρ는 사전등록 상수 STOUFFER_RHO=0.119 고정. v1의 ρ 그리드 max 선택은 금지
      (F-06: 유리한 ρ 선택 = 다중검정 재량). rho 인자는 단위 테스트(T9: 단조성·
      기댓값·복제 무이득) 전용이며 운용 코드는 상수만 전달한다.
    · 정합성: k=1 → z=t_1 (불변). 동일 전략 복제(t1=t2=t, ρ=1) → z=t (무이득).
      모든 t_i=t, ρ=0 → z=t·√k (클래식 Stouffer 등가중). — T9 단위검증 대상."""
    keep = [(t, v) for t, v in zip(ts, vns) if v >= VN_MIN and t > 0]
    if not keep:
        return float("-inf")
    if rho is None:
        rho = STOUFFER_RHO
    k = len(keep)
    num = float(sum(t for t, _ in keep))
    var = float(k + 2.0 * rho * (k * (k - 1) / 2.0))
    if var <= 0:
        return float("-inf")
    return num / math.sqrt(var)
