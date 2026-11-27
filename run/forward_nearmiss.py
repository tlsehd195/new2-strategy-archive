#!/usr/bin/env python3
"""Forward paper record of the near-miss candidates CL1, FIP1, AF1 (ADR 0112).

No orders, no account.  The rules are frozen in
``run/audit/evidence_forward_nearmiss_20261120/prereg_forward_nearmiss_v1.json``
(git commit before the forward start 2026-11-23).  Results are computed later
from Binance's own public monthly archives (S3 mirror of data.binance.vision),
so nothing observed after the commit can change the rules.

Reads only [LOOKBACK_FROM, end of the last complete archive month]; the part
before FORWARD_START is signal input only (no return is evaluated there).
BTCUSDT and ETHUSDT are never fetched or read (their validation / lock /
paper windows stay untouched).  The candidates' signal code is imported
unchanged from the DEV tools; the engine is ``newsrc_engine.run_segment``.

Usage::

    python3 run/forward_nearmiss.py fetch --data-dir <dir>
    python3 run/forward_nearmiss.py evaluate --data-dir <dir> [--asof YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import calendar
import concurrent.futures as cf
import datetime as dt
import json
import math
import subprocess
import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import broad_universe_fetch as bf  # noqa: E402
import newsrc_engine as ne  # noqa: E402
import newsrc_r3_dev as r3  # noqa: E402
import newsrc_r11_dev as r11  # noqa: E402
import newsrc_r12_dev as r12  # noqa: E402
import research_data_integrity as rdi  # noqa: E402
import wide_universe_dev as wu  # noqa: E402

VERSION = "forward-nearmiss-v1"
EVID = HERE / "audit" / "evidence_forward_nearmiss_20261120"
PREREG = EVID / "prereg_forward_nearmiss_v1.json"
LOOKBACK_FROM = "2026-05-01"
FORWARD_START = "2026-11-23"
INTERIM_DAYS = 182
FINAL_DAYS = 364
EXCLUDED = ("BTCUSDT", "ETHUSDT")
KEYS = [("CL1_CLOSE_LOCATION", "U50"), ("FIP1_FROGPAN", "U50"), ("AF1_AGGFUND_TIMING", "U50")]
ALPHA_FAMILY = 0.0213
NW_LAG = 14
SEG = "F1"


def months_between(lo: str, hi: str) -> list:
    y, m = int(lo[:4]), int(lo[5:7])
    out = []
    while f"{y}-{m:02d}" <= hi[:7]:
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def last_complete_month(asof: dt.date) -> str:
    first = asof.replace(day=1)
    prev = first - dt.timedelta(days=1)
    return f"{prev.year}-{prev.month:02d}"


def month_end(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{ym}-{calendar.monthrange(y, m)[1]:02d}"


def fetch(data: Path, asof: dt.date, workers: int = 32) -> int:
    hi = last_complete_month(asof)
    yms = months_between(LOOKBACK_FROM, hi)
    syms = [s for s in bf.list_symbols() if s not in EXCLUDED]
    for d in ("daily", "funding", "bar2345"):
        (data / d).mkdir(parents=True, exist_ok=True)
    (data / "symbols_listed.json").write_text(json.dumps(syms))
    errors = []
    for fn in (bf.job_daily, bf.job_funding, bf.job_bar2345):
        counts: dict = {}
        with cf.ThreadPoolExecutor(workers) as ex:
            futs = {ex.submit(fn, data, s, ym): (s, ym) for s in syms for ym in yms}
            for f in cf.as_completed(futs):
                try:
                    k = f.result()
                except Exception as e:  # recorded, never skipped silently
                    k = "error"
                    errors.append(f"{futs[f]}: {e}")
                counts[k] = counts.get(k, 0) + 1
        print(fn.__name__, len(syms), "symbols", yms[0], "..", yms[-1], counts)
    for e in errors[:20]:
        print("ERROR", e)
    return 1 if errors else 0


def prereg_commit_time() -> dt.datetime:
    out = subprocess.run(["git", "log", "--diff-filter=A", "--format=%cI", "--", str(PREREG)],
                         cwd=HERE.parent, capture_output=True, text=True, check=True).stdout.split()
    if not out:
        raise SystemExit("prereg is not committed; refusing to evaluate")
    return dt.datetime.fromisoformat(out[-1])


def guard(end: str) -> tuple:
    """Forward read guard: replaces the DEV never-read list for this run only."""
    never = tuple(r for r in bu.NEVER if r[0] != "2025-10-21")
    lo_gap = (dt.date.fromisoformat(LOOKBACK_FROM) - dt.timedelta(days=1)).isoformat()
    hi_gap = (dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat()
    return never + (("2025-10-21", lo_gap), (hi_gap, "2099-12-31"))


def signals(ctx: dict) -> dict:
    cut, ranked = ctx["cut"], ctx["ranked"]
    u50 = [s for s in wu.slice_universe(ranked, "U50") if s not in EXCLUDED]
    if not u50 or cut - 179 < 0:
        return {}
    piv = ctx["piv"]
    ex = r11.load_extra(ctx["data"], ctx["seg"], ctx["days"], ctx["syms"])
    cl = r11.compute(piv["close"][u50], piv["qv"][u50], ex["high"][u50], ex["low"][u50], ex["count"][u50],
                     ctx["cret"][u50], cut)["CL1_CLOSE_LOCATION"]
    return {("CL1_CLOSE_LOCATION", "U50"): cl.replace([np.inf, -np.inf], np.nan),
            ("FIP1_FROGPAN", "U50"): r12.frog(piv["close"][u50], ctx["cret"][u50], cut),
            ("AF1_AGGFUND_TIMING", "U50"): r3.af1(ctx["fund_daily"], cut, u50)}


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    if h == "AF1_AGGFUND_TIMING":
        s = sig.dropna()
        return {k: float(v) for k, v in s.items() if v != 0 and k in universe}
    return wu.pick(sig, universe, True)


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start={SEG: FORWARD_START}, hold=14)


def nw_t(x: np.ndarray, lag: int = NW_LAG) -> float:
    x = np.asarray(x, float)
    n = len(x)
    if n < 2:
        return float("nan")
    e = x - x.mean()
    s = float(e @ e) / n
    for k in range(1, min(lag, n - 1) + 1):
        s += 2.0 * (1.0 - k / (lag + 1)) * float(e[k:] @ e[:-k]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def p_one_sided(t: float) -> float:
    return 0.5 * math.erfc(t / math.sqrt(2.0)) if math.isfinite(t) else 1.0


def holm(p: dict, alpha: float) -> dict:
    order = sorted(p, key=lambda k: p[k])
    m, rej, stop = len(p), {}, False
    for r, k in enumerate(order):
        ok = not stop and p[k] <= alpha / (m - r)
        stop = stop or not ok
        rej[k] = ok
    return rej


def sr(x: np.ndarray) -> float:
    sd = float(np.std(x, ddof=1)) if len(x) > 1 else 0.0
    return float(np.mean(x) / sd * math.sqrt(365)) if sd > 0 else float("nan")


#: ADR 0116: the six result statuses of the forward record (``status`` of each
#: test; ``verdict`` keeps the preregistered names)
STATUSES = ("PASS", "FAIL", "INVALID_DATA", "INSUFFICIENT_DATA", "UNDETERMINED", "ERROR")
#: integrity addendum (ADR 0116): fail-closed data gates only, no rule change
ADDENDUM = EVID / "prereg_forward_nearmiss_v1_addendum_data_integrity.json"


def data_gate(n_days: int, n_invalid: int, volume: dict, panel_bad: int) -> dict:
    """Data status of one candidate's counted forward days (ADR 0116)."""
    st = rdi.series_status(n_days, n_invalid, volume)
    if panel_bad:
        st["status"] = "INVALID_DATA"
        st["panel_issues_on_held_names"] = panel_bad
    return st


def final_status(stage: str, verdict: str, gate: dict, stats_finite: bool) -> str:
    """The one place that turns a statistical verdict into a result status.

    PASS needs ALL of: FINAL stage, the preregistered FORWARD_PASS rule, no
    invalid day at all, the estimated-volume share within the limit, no panel
    problem on a held name, finite statistics.  Any missing data, substitute
    share over the limit, incomplete required data or a data-check failure
    therefore withholds a PASS — the flags are not just recorded, they are
    checked here."""
    g = gate["status"]
    if not stats_finite and stage != "OBSERVING":
        return "ERROR"
    if g == "INVALID_DATA":
        return "INVALID_DATA"
    if g == "INSUFFICIENT_DATA":
        return "INSUFFICIENT_DATA"
    if verdict == "FORWARD_PASS":
        if g == "UNDETERMINED" or gate["days_invalid"] > 0:
            return "UNDETERMINED"
        return "PASS" if g == "COMPLETE" else "UNDETERMINED"
    if verdict in ("FORWARD_FAIL", "FORWARD_FAIL_FUTILITY"):
        # a failure on observed days stands (excluding days cannot hide a pass),
        # unless the estimated share makes the cost itself unknown
        return "UNDETERMINED" if g == "UNDETERMINED" else "FAIL"
    return "INSUFFICIENT_DATA"            # OBSERVING / interim without futility


def verdicts(series: dict, n_days: int, gates: dict = None) -> dict:
    """series: {key: {"cal": arr, "cal_x2": arr, "cost0": arr}} over forward days
    (NaN = day not observable, ADR 0116).  ``gates``: {key: data_gate(...)};
    without it the gate is computed from the NaN days alone."""
    stage = "FINAL" if n_days >= FINAL_DAYS else ("INTERIM" if n_days >= INTERIM_DAYS else "OBSERVING")
    vals, gates = {}, dict(gates or {})
    for k, v in series.items():
        cal, x2, c0 = (np.asarray(v[c], float)[:FINAL_DAYS] for c in ("cal", "cal_x2", "cost0"))
        m = rdi.valid_mask(cal, x2, c0)
        vals[k] = (cal[m], x2[m], c0[m])
        if k not in gates:
            gates[k] = data_gate(len(cal), int((~m).sum()), {}, 0)
    t = {k: nw_t(vals[k][0]) for k in series}
    # Holm over the fixed m = 3: a candidate whose data are INVALID enters with
    # p = 1 (it cannot be rejected and it cannot make the others' thresholds easier)
    p = {k: (1.0 if gates[k]["status"] in ("INVALID_DATA", "INSUFFICIENT_DATA") else p_one_sided(x))
         for k, x in t.items()}
    rej = holm(p, ALPHA_FAMILY)
    out = {}
    for k in series:
        x, x2, c0 = vals[k]
        h = len(x) // 2
        row = {"days": int(gates[k]["days_total"]), "days_valid": len(x), "sr_ann": sr(x), "sr_cost0": sr(c0),
               "sr_cost_x2": sr(x2), "cum_return_pct": float(np.sum(x) * 100),
               "nw_t": t[k], "p_one_sided": p[k], "halves_sr": [sr(x[:h]), sr(x[h:])] if h > 1 else [None, None],
               "data": gates[k]}
        finite = all(isinstance(z, float) and math.isfinite(z) for z in (row["sr_ann"], t[k]))
        if stage == "FINAL":
            ok = rej[k] and finite and min(row["halves_sr"]) > 0 and float(np.mean(x2)) > 0
            row["verdict"] = "FORWARD_PASS" if ok else "FORWARD_FAIL"
        elif stage == "INTERIM" and math.isfinite(t[k]) and t[k] < -1.0:
            row["verdict"] = "FORWARD_FAIL_FUTILITY"
        else:
            row["verdict"] = "OBSERVING"
        row["status"] = final_status(stage, row["verdict"], gates[k], finite)
        if row["status"] in ("INVALID_DATA", "UNDETERMINED", "ERROR") and row["verdict"] != "OBSERVING":
            row["verdict_withheld"] = row["verdict"]
            row["verdict"] = f"WITHHELD_{row['status']}"
        out[k] = row
    return {"stage": stage, "alpha_family": ALPHA_FAMILY, "holm_reject": rej, "tests": out,
            "data_policy": rdi.POLICY}


def held_symbols(issues: dict) -> set:
    return {e["symbol"] for e in issues.get("events", [])}


def evaluate(data: Path, asof: dt.date) -> dict:
    committed = prereg_commit_time()
    if committed >= dt.datetime.fromisoformat(FORWARD_START + "T00:00:00+00:00"):
        raise SystemExit("prereg committed after the forward start; refusing")
    end = month_end(last_complete_month(asof))
    if end < FORWARD_START:
        raise SystemExit(f"no complete forward month yet (last complete month ends {end})")
    segs = dict(bu.SEGMENTS)
    segs[SEG] = (LOOKBACK_FROM, end, FORWARD_START)
    cal = bu.load_cost_calibration(wu.CALIBRATION)
    cal = {**cal["calib"], "maxp": cal["maxp"]}
    with mock.patch.object(bu, "NEVER", guard(end)), mock.patch.dict(bu.SEGMENTS, segs):
        r = ne.run_segment(data, SEG, cal, SPEC)
    sp = r["span"]
    series = {f"{u}:{h}": {c: np.asarray(r["daily"][(h, u)][c][sp]) for c in ("cal", "cal_x2", "cost0")}
              for h, u in KEYS}
    n_days = sp.stop - sp.start
    fwd0 = bu.ms(FORWARD_START)
    panel_syms = {e["symbol"] for e in r["panel_issues"]["events"]
                  if bu.ms(e["day"]) >= bu.ms(LOOKBACK_FROM)}
    gates = {}
    for h, u in KEYS:
        key = f"{u}:{h}"
        inv = r["invalid"][(h, u)][sp][:FINAL_DAYS]
        # panel problems (conflicting duplicates, invalid prices) on a name this
        # candidate held or traded: its series rests on data that failed a check
        bad = len(panel_syms & held_symbols(r["issues"][key])) if r["panel_issues"]["events_total"] else 0
        if r["panel_issues"]["events_truncated"]:
            bad = max(bad, 1)
        gates[key] = data_gate(len(inv), int(inv.sum()), r["volume"][key], bad)
    out = {"version": VERSION, "asof": asof.isoformat(), "read": [LOOKBACK_FROM, end],
           "forward_from": FORWARD_START, "forward_days": n_days, "prereg_commit": committed.isoformat(),
           "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "addendum_sha256": bu.sha(ADDENDUM) if ADDENDUM.exists() else None,
           "engine_version": r["engine_version"], "cost_evidence": r["cost_evidence"],
           "flags": r["flags"], "data_issues": r["issues"], "volume_provenance": r["volume"],
           "panel_issues": r["panel_issues"], "forward_from_ms": fwd0,
           **verdicts(series, n_days, gates)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("fetch", "evaluate"))
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--asof", default=dt.date.today().isoformat())
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    asof = dt.date.fromisoformat(a.asof)
    if a.cmd == "fetch":
        return fetch(a.data_dir, asof)
    out = evaluate(a.data_dir, asof)
    dst = a.out or EVID / f"forward_{out['read'][1]}.json"
    dst.write_text(json.dumps(out, indent=1, default=float))
    statuses = sorted({v["status"] for v in out["tests"].values()})
    print(out["stage"], out["forward_days"], "days", "status", "/".join(statuses))
    for k, v in out["tests"].items():
        d = v["data"]
        print(k, v["verdict"], v["status"], f"SR {v['sr_ann']:.2f} t {v['nw_t']:.2f} cum {v['cum_return_pct']:.1f}%",
              f"valid {d['days_valid']}/{d['days_total']} est.vol {d['estimated_volume_share']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
