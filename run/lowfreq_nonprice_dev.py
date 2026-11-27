#!/usr/bin/env python3
"""Low-frequency (28-day) time-series momentum + Binance metrics cross-section, DEV only (ADR 0094).

Runs exactly the 8 trials fixed in
``run/audit/evidence_lowfreq_nonprice_20261120/prereg_lowfreq_nonprice_v1.json``
(committed before any metrics file was downloaded):

  L1_TSMOM84_LONGFLAT, L2_TSMOM84_LS              on U50
  M1_OIGROWTH28, M2_RETAILLS7, M3_TOPVSRETAIL7    on U50 and W200

Universe, cost (ADR 0041 calibration) and the drift/rebalance accounting are the
ADR 0047 ones (``wide_universe_dev``).  Never reads: 2020 (IW1),
2023-09-19..2024-06-18, 2025-10-21 onwards; BTCUSDT in S2.  No orders, no
private API, no NEW2.

Usage::

    python3 run/broad_universe_fetch.py --out <dir> --step daily
    python3 run/wide_universe_dev.py --data-dir <dir> --write-universe        # ever_w200.json
    python3 run/broad_universe_fetch.py --out <dir> --step funding --symbols <dir>/ever_w200.json
    python3 run/broad_universe_fetch.py --out <dir> --step bar2345 --symbols <dir>/ever_w200.json
    python3 run/lowfreq_nonprice_dev.py --data-dir <dir> --fetch-metrics       # Binance metrics archive (S3 mirror)
    python3 run/lowfreq_nonprice_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import io
import json
import math
import sys
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))
import broad_universe_dev as bu  # noqa: E402
import research_data_integrity as rdi  # noqa: E402
import broad_universe_fetch as bf  # noqa: E402
import cross_section_v2_dev as cs  # noqa: E402
import wide_universe_dev as wu  # noqa: E402
from engine_lib import deflated_sharpe, expected_max_sr, pbo_cscv  # noqa: E402

VERSION = "lowfreq-nonprice-dev-v1"
EVID = HERE / "audit" / "evidence_lowfreq_nonprice_20261120"
PREREG = EVID / "prereg_lowfreq_nonprice_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"
SEGMENTS = bu.SEGMENTS
HOLD = 28
TS_LOOKBACK = 84
OI_LOOKBACK = 28
RATIO_DAYS = 7
RATIO_MIN_DAYS = 6
METRICS_START = "2021-12-01"
#: evaluation start of each family per segment (prereg "segments")
EVAL_START = {"L": {"S1": "2021-03-01", "S2": "2024-08-19"}, "M": {"S1": "2022-01-03", "S2": "2024-08-19"}}
METRICS_BASE = bf.S3 + "/data/futures/um/daily/metrics"
#: trial id -> (family, universe)
TRIAL_KEYS = [("L1_TSMOM84_LONGFLAT", "U50"), ("L2_TSMOM84_LS", "U50"),
              ("M1_OIGROWTH28", "U50"), ("M1_OIGROWTH28", "W200"),
              ("M2_RETAILLS7", "U50"), ("M2_RETAILLS7", "W200"),
              ("M3_TOPVSRETAIL7", "U50"), ("M3_TOPVSRETAIL7", "W200")]
BENCH = ("B0_EW_LONG", "U50")
M_LONG_TOP = {"M1_OIGROWTH28": False, "M2_RETAILLS7": False, "M3_TOPVSRETAIL7": True}
COST_ROWS = wu.COST_ROWS
N_TRIALS = len(TRIAL_KEYS)
CONTEXT_ID = "U50W200-28d-lowfreq-nonprice-v1"


def prior_trials() -> int:
    """DEV evaluations of earlier searches (this run's own trials line excluded, so a re-run does not double count)."""
    rows = [json.loads(ln) for ln in TRIALS.read_text().splitlines() if ln.strip()]
    return sum(r.get("dev_evaluated", 0) for r in rows if r.get("context_id") != CONTEXT_ID)


def family(h: str) -> str:
    return "L" if h.startswith(("L", "B")) else "M"


def day_iso(t: int) -> str:
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- metrics archive

def metrics_jobs(data: Path) -> list:
    """(symbol, day) pairs needed by M1..M3: W200 names at each grid rebalance, days cut-6..cut and cut-28."""
    jobs = set()
    mstart = bu.ms(METRICS_START)
    for seg in SEGMENTS:
        piv = cs.load_daily(data, seg)
        days = piv["open"].index.values
        n_bars = piv["close"].notna().cumsum()
        ev0 = bu.ms(EVAL_START["M"][seg])
        for i in wu.rebalance_days(days, SEGMENTS[seg][2], HOLD):
            cut = i - 2
            if int(days[i]) < ev0 or cut - OI_LOOKBACK < 0:
                continue
            uni = wu.slice_universe(wu.eligible(piv, n_bars, i), "W200")
            need = [cut - k for k in range(RATIO_DAYS)] + [cut - OI_LOOKBACK]
            for d in need:
                t = int(days[d])
                assert t >= mstart
                bu.assert_allowed(t, t)
                for s in uni:
                    jobs.add((s, day_iso(t)))
    return sorted(jobs)


def fetch_metric(out: Path, sym: str, day: str) -> str:
    dst = out / "metrics" / f"{sym}-metrics-{day}.csv"
    if dst.exists():
        return "skip"
    path = urllib.parse.quote(f"{sym}/{sym}-metrics-{day}.zip")
    body = bf.get(f"{METRICS_BASE}/{path}")
    if body is None:
        return "404"
    chk = bf.get(f"{METRICS_BASE}/{path}.CHECKSUM")
    if chk is None:
        raise RuntimeError(f"no CHECKSUM for {path}")
    if chk.decode().split()[0].strip().lower() != hashlib.sha256(body).hexdigest():
        raise RuntimeError(f"CHECKSUM mismatch {path}")
    dst.write_text(bf.unzip_one(body))
    return "ok"


def fetch_all(data: Path, workers: int) -> int:
    (data / "metrics").mkdir(parents=True, exist_ok=True)
    jobs = metrics_jobs(data)
    (data / "metrics_jobs.json").write_text(json.dumps(jobs))
    counts: dict = {}
    errors = []
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_metric, data, s, d): (s, d) for s, d in jobs}
        for f in cf.as_completed(futs):
            try:
                k = f.result()
            except Exception as e:  # recorded, never silently skipped
                k = "error"
                errors.append(f"{futs[f]}: {e}")
            counts[k] = counts.get(k, 0) + 1
    print("metrics", len(jobs), "jobs", counts)
    for e in errors[:20]:
        print("ERROR", e)
    return 1 if errors else 0


def parse_metrics_day(text: str) -> dict | None:
    """Day summary: last 5-minute sum_open_interest, mean global account ratio, mean top-trader position ratio."""
    oi, retail, top = None, [], []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            v = float(r["sum_open_interest"])
            if v > 0:
                oi = v
        except (KeyError, ValueError):
            pass
        for key, acc in (("count_long_short_ratio", retail), ("sum_toptrader_long_short_ratio", top)):
            try:
                x = float(r[key])
                if x > 0 and math.isfinite(x):
                    acc.append(x)
            except (KeyError, ValueError):
                pass
    if oi is None and not retail and not top:
        return None
    return {"oi": oi, "retail": float(np.mean(retail)) if retail else None,
            "top": float(np.mean(top)) if top else None}


def load_metrics(data: Path, t0: int, t1: int) -> dict:
    """(symbol, day ms) -> day summary, for files inside [t0, t1] only."""
    bu.assert_allowed(t0, t1)
    out = {}
    for p in sorted((data / "metrics").glob("*-metrics-*.csv")):
        sym, day = p.name[:-4].split("-metrics-")
        t = bu.ms(day)
        if not (t0 <= t <= t1):
            continue
        s = parse_metrics_day(p.read_text())
        if s is not None:
            out[(sym, t)] = s
    return out


# --------------------------------------------------------------------------- signals

def ts_signal(close: pd.DataFrame, cut: int) -> pd.Series:
    if cut - TS_LOOKBACK < 0:
        return pd.Series(np.nan, index=close.columns)
    return close.iloc[cut] / close.iloc[cut - TS_LOOKBACK] - 1.0


def m_signals(met: dict, days: np.ndarray, cut: int, names: list) -> dict:
    out = {h: {} for h in M_LONG_TOP}
    if cut - OI_LOOKBACK < 0:
        return {h: pd.Series(dtype=float) for h in out}
    t_cut, t_back = int(days[cut]), int(days[cut - OI_LOOKBACK])
    win = [int(days[cut - k]) for k in range(RATIO_DAYS)]
    for s in names:
        a, b = met.get((s, t_cut)), met.get((s, t_back))
        if a and b and a["oi"] and b["oi"]:
            out["M1_OIGROWTH28"][s] = math.log(a["oi"] / b["oi"])
        rows = [met.get((s, t)) for t in win]
        ret = [r["retail"] for r in rows if r and r["retail"]]
        if len(ret) >= RATIO_MIN_DAYS:
            out["M2_RETAILLS7"][s] = float(np.mean(ret))
        dif = [math.log(r["top"]) - math.log(r["retail"]) for r in rows if r and r["top"] and r["retail"]]
        if len(dif) >= RATIO_MIN_DAYS:
            out["M3_TOPVSRETAIL7"][s] = float(np.mean(dif))
    return {h: pd.Series(v, dtype=float) for h, v in out.items()}


def ts_weights(sig: pd.Series, universe: list, mode: str) -> dict:
    s = sig.reindex(universe).dropna()
    n = len(s)
    if n == 0:
        return {}
    if mode == "B0":
        return {k: 1.0 / n for k in s.index}
    if mode == "LONGFLAT":
        return {k: 1.0 / n for k, v in s.items() if v > 0}
    return {k: (1.0 / n if v > 0 else -1.0 / n) for k, v in s.items()}


def target(h: str, u: str, universe: list, ts: pd.Series, msig: dict) -> dict:
    if h == "B0_EW_LONG":
        return ts_weights(pd.Series(1.0, index=universe), universe, "B0")
    if h == "L1_TSMOM84_LONGFLAT":
        return ts_weights(ts, universe, "LONGFLAT")
    if h == "L2_TSMOM84_LS":
        return ts_weights(ts, universe, "LS")
    return wu.pick(msig[h], universe, M_LONG_TOP[h])


# --------------------------------------------------------------------------- simulation

def run_segment(data: Path, seg: str, cal: dict) -> dict:
    piv = cs.load_daily(data, seg)
    days = piv["open"].index.values
    syms = list(piv["open"].columns)
    t0, t1 = int(days[0]), int(days[-1])
    fund = bu.load_funding(data, syms, t0, t1)
    fund["day"] = rdi.funding_day(fund["t"].values)       # ADR 0116: settlement jitter
    fcal = rdi.FundingCalendar(fund, syms)
    bars = bu.load_bar2345(data, syms, t0, t1)
    met = load_metrics(data, max(t0, bu.ms(METRICS_START)), t1)
    opn, qv = piv["open"], piv["qv"]
    ret = (opn.shift(-1) / opn - 1.0).values
    col = {s: k for k, s in enumerate(syms)}
    n_bars = piv["close"].notna().cumsum()
    keys = TRIAL_KEYS + [BENCH]
    daily = {k: {r: np.zeros(len(days)) for r in COST_ROWS} for k in keys}
    mkt = {k: np.full(len(days), np.nan) for k in keys}
    cost_paid = {k: 0.0 for k in keys}
    turnover = {k: [] for k in keys}
    n_reb = {k: 0 for k in keys}
    flat_reb = {k: 0 for k in keys}
    names_with_signal = {k: [] for k in keys}
    gross = {k: [] for k in keys}
    flags = {"missing_return_days": 0, "impact_bar_fallback": 0, "metrics_days_loaded": len(met),
             "funding_invalid_name_days": 0, "impact_volume_missing": 0}
    state = {k: rdi.SeriesState(len(days), f"{k[1]}:{k[0]}") for k in keys}
    held = {k: {} for k in keys}
    reb = wu.rebalance_days(days, SEGMENTS[seg][2], HOLD)
    ev = {f: bu.ms(EVAL_START[f][seg]) for f in ("L", "M")}
    for j, i in enumerate(reb):
        cut = i - 2
        if cut < 0:
            continue
        ranked = wu.eligible(piv, n_bars, i)
        end = reb[j + 1] if j + 1 < len(reb) else len(days) - 1
        bar_t = int(days[i - 1]) + 23 * 3_600_000 + 45 * 60_000
        v30 = qv.iloc[max(0, cut - 29): cut + 1].mean()
        ts = ts_signal(piv["close"], cut)
        msig = m_signals(met, days, cut, wu.slice_universe(ranked, "W200")) if int(days[i]) >= ev["M"] else None
        for k in keys:
            h, u = k
            if int(days[i]) < ev[family(h)]:
                continue
            universe = wu.slice_universe(ranked, u)
            n_reb[k] += 1
            if not universe:
                w = {}
            else:
                w = target(h, u, universe, ts, msig) if (msig is not None or family(h) == "L") else {}
                names_with_signal[k].append(int(ts.reindex(universe).notna().sum()) if family(h) == "L"
                                            else int(msig[h].reindex(universe).notna().sum()))
                uc = [col[s] for s in universe]
                for d in range(i, end):
                    r = ret[d, uc]
                    mkt[k][d] = float(np.nanmean(r)) if np.isfinite(r).any() else float("nan")
            if not w:
                flat_reb[k] += 1
            gross[k].append(float(sum(abs(x) for x in w.values())))
            prev = held[k]
            state[k].rebalance(i, prev, days)
            to = 0.0
            for s in sorted(set(w) | set(prev)):
                dw = abs(w.get(s, 0.0) - prev.get(s, 0.0))
                if dw == 0:
                    continue
                # ADR 0116: observed vs estimated impact volume; none = cost unknown (old: 1.0 / q15 0)
                b, kind = rdi.impact_volume(bars, qv, s, i, bar_t)
                if kind != "OBSERVED_BAR2345":
                    flags["impact_bar_fallback"] += 1
                if not state[k].cost_volume(i, s, days, b, kind, float(v30[s])):
                    flags["impact_volume_missing"] += 1
                    to += dw
                    continue
                q15 = float(v30[s]) / 96.0
                for row in COST_ROWS:
                    daily[k][row][i] -= dw * wu.side_cost(row, cal, b, q15)
                cost_paid[k] += dw * wu.side_cost("cal", cal, b, q15)
                to += dw
            turnover[k].append(to)
            cur = dict(w)

            def add(d, pnl, k=k):
                for row in COST_ROWS:
                    daily[k][row][d] += pnl
            # a missing return / funding is an unobserved day, never 0 (ADR 0116)
            state[k].hold(cur, i, end, ret, col, days, fcal, add, drift=True, flags=flags)
            held[k] = cur
    spans = {}
    for k in keys:
        a = int(np.searchsorted(days, ev[family(k[0])]))
        spans[k] = slice(a, len(days) - 1)
        state[k].finish(daily[k].values())
    flags["invalid_days"] = {f"{k[1]}:{k[0]}": int(state[k].invalid[spans[k]].sum()) for k in keys}
    return {"days": days, "spans": spans, "daily": daily, "mkt": mkt, "cost_paid": cost_paid,
            "turnover": turnover, "n_reb": n_reb, "flat_reb": flat_reb, "names_with_signal": names_with_signal,
            "gross": gross, "flags": flags,
            "issues": {f"{k[1]}:{k[0]}": state[k].log.to_json() for k in keys},
            "volume": {f"{k[1]}:{k[0]}": state[k].volume.to_json() for k in keys},
            "panel_issues": rdi.panel_issues(data, seg)}


def flag(row: dict) -> str:
    s1 = row["S1"]["cal"]
    ok = (s1["sr_ann"] > row["null_max_sr_ann_N8"] and s1["alpha_t"] >= 2 and min(row["S1"]["halves_sr_ann"]) > 0
          and row["S2"]["cal"]["sr_ann"] > 0.5 and row["S1"]["cal_x2"]["sr_ann"] > 0
          and row["S1"]["cal_tail_x2"]["sr_ann"] > 0)
    return "DEV_PROMISING" if ok else "NOT_PROMISING"


def moments(x: np.ndarray) -> tuple:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]          # ADR 0116: unobserved days are excluded (counted in data_status)
    sd = x.std(ddof=1)
    if not sd > 0:
        return 0.0, 0.0, 3.0
    z = (x - x.mean()) / sd
    return float(x.mean() / sd), float(np.mean(z ** 3)), float(np.mean(z ** 4))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--fetch-metrics", action="store_true")
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--out", type=Path, default=EVID / "lowfreq_nonprice_dev.json")
    a = ap.parse_args(argv)
    if a.fetch_metrics:
        return fetch_all(a.data_dir, a.workers)
    cal = bu.load_cost_calibration(wu.CALIBRATION)
    cal = {**cal["calib"], "maxp": cal["maxp"]}
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "helper_sha256": {n: bu.sha(HERE / n) for n in ("broad_universe_dev.py", "wide_universe_dev.py",
                                                           "cross_section_v2_dev.py")},
           "calibration_sha256": bu.sha(wu.CALIBRATION), "segments": {}, "trials": {}}
    segs = {s: run_segment(a.data_dir, s, cal) for s in SEGMENTS}
    prior = prior_trials()
    for s, r in segs.items():
        out["segments"][s] = {"first_day": day_iso(int(r["days"][0])), "last_day": day_iso(int(r["days"][-2])),
                              "flags": r["flags"], "issues": r["issues"], "volume_provenance": r["volume"],
                              "panel_issues": r["panel_issues"]}
    out["data_policy"], out["cost_evidence"] = rdi.POLICY, rdi.COST_EVIDENCE
    s1_series = {}
    for k in TRIAL_KEYS + [BENCH]:
        h, u = k
        row = {}
        for s, r in segs.items():
            sp = r["spans"][k]
            mk = r["mkt"][k][sp]
            row[s] = {c: bu.stats(v[sp], mk) for c, v in r["daily"][k].items()}
            x = r["daily"][k]["cal"][sp]
            half = len(x) // 2
            row[s]["halves_sr_ann"] = [bu.stats(x[:half], mk[:half])["sr_ann"], bu.stats(x[half:], mk[half:])["sr_ann"]]
            row[s]["eval_from"] = day_iso(int(r["days"][sp.start]))
            row[s]["years"] = len(x) / 365.0
            row[s]["rebalances"] = r["n_reb"][k]
            row[s]["flat_rebalances"] = r["flat_reb"][k]
            row[s]["mean_names_with_signal"] = float(np.mean(r["names_with_signal"][k])) if r["names_with_signal"][k] else 0.0
            row[s]["mean_gross"] = float(np.mean(r["gross"][k])) if r["gross"][k] else 0.0
            row[s]["mean_turnover_per_rebalance"] = float(np.mean(r["turnover"][k])) if r["turnover"][k] else 0.0
            row[s]["cost_paid_pct_of_capital"] = r["cost_paid"][k] * 100
            row[s]["cost_share_of_gross_abs_pct"] = (
                100 * r["cost_paid"][k] / max(1e-12, abs(float(np.sum(r["daily"][k]["cost0"][sp])))))
        y1 = row["S1"]["years"]
        row["null_max_sr_ann_N8"] = expected_max_sr(N_TRIALS) / math.sqrt(y1)
        row["null_max_sr_ann_cumulative"] = expected_max_sr(prior + N_TRIALS) / math.sqrt(y1)
        if k == BENCH:
            row["flag"] = "BENCHMARK_NOT_COUNTED"
            out["benchmark"] = row
            continue
        row["flag"] = rdi.withhold_flag(flag(row), [row[s]["cal"] for s in segs])
        out["trials"][f"{u}:{h}"] = row
        s1_series[k] = segs["S1"]["daily"][k]["cal"]
    # multiple testing: DSR at N=8 and cumulative N, PBO on the common S1 span of all 8
    srs = {k: moments(segs["S1"]["daily"][k]["cal"][segs["S1"]["spans"][k]]) for k in TRIAL_KEYS}
    var_sr = float(np.var([v[0] for v in srs.values()], ddof=1))
    for k, (sr, sk, ku) in srs.items():
        xs = segs["S1"]["daily"][k]["cal"][segs["S1"]["spans"][k]]
        n = int(np.isfinite(xs).sum())
        row = out["trials"][f"{k[1]}:{k[0]}"]
        row["dsr_N8"] = deflated_sharpe(sr, n, sk, ku, N_TRIALS, var_sr)
        row["dsr_cumulative"] = deflated_sharpe(sr, n, sk, ku, prior + N_TRIALS, var_sr)
    common = max(segs["S1"]["spans"][k].start for k in TRIAL_KEYS)
    stop = segs["S1"]["spans"][TRIAL_KEYS[0]].stop
    mat = np.column_stack([s1_series[k][common:stop] for k in TRIAL_KEYS])
    ok_rows = np.isfinite(mat).all(axis=1)     # one calendar for PBO: days unobserved in any trial dropped
    mat = mat[ok_rows]
    pbo, ncomb = pbo_cscv(mat.tolist(), n_partitions=16)
    out["multiple_testing"] = {"n_trials_here": N_TRIALS, "prior_trials": prior, "cumulative": prior + N_TRIALS,
                               "cross_trial_var_sr_daily": var_sr,
                               "pbo_cscv_16": pbo, "pbo_combinations": ncomb,
                               "pbo_days_dropped_invalid": int((~ok_rows).sum()),
                               "pbo_span": [day_iso(int(segs["S1"]["days"][common])), day_iso(int(segs["S1"]["days"][stop - 1]))]}
    z = wu.NormalDist().inv_cdf
    out["power"] = {f: {"s1_years": out["trials"][f"U50:{h}"]["S1"]["years"],
                        "min_detectable_annual_sr_80pct_bonf8": (z(1 - 0.05 / N_TRIALS) + z(0.8)) / math.sqrt(out["trials"][f"U50:{h}"]["S1"]["years"])}
                    for f, h in (("L", "L1_TSMOM84_LONGFLAT"), ("M", "M1_OIGROWTH28"))}
    # daily correlations of the S1 'cal' series on the common span (report only)
    out["s1_corr_common_span"] = {"keys": [f"{u}:{h}" for h, u in TRIAL_KEYS],
                                  "corr": np.corrcoef(mat.T).round(3).tolist()}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("prior", prior, "PBO", round(pbo, 3), "power", {f: round(v["min_detectable_annual_sr_80pct_bonf8"], 2)
                                                         for f, v in out["power"].items()})
    for key, row in list(out["trials"].items()) + [("U50:B0_EW_LONG", out["benchmark"])]:
        line = [key, row["flag"], f"null8 {row['null_max_sr_ann_N8']:.2f}"]
        for s in SEGMENTS:
            for c in COST_ROWS:
                st = row[s][c]
                line.append(f"{s}-{c} SR {st['sr_ann']:.2f} ret {st['ann_return_pct']:.1f}% dd {st['max_dd_pct']:.0f}% at {st['alpha_t']:.2f} b {st['beta']:.2f}")
            line.append(f"{s} halves {[round(v, 2) for v in row[s]['halves_sr_ann']]} reb {row[s]['rebalances']} flat {row[s]['flat_rebalances']}"
                        f" names {row[s]['mean_names_with_signal']:.0f} gross {row[s]['mean_gross']:.2f} to {row[s]['mean_turnover_per_rebalance']:.2f}"
                        f" cost {row[s]['cost_paid_pct_of_capital']:.1f}%")
        if "dsr_N8" in row:
            line.append(f"DSR8 {row['dsr_N8']:.3f} DSRcum {row['dsr_cumulative']:.3f}")
        print(" | ".join(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
