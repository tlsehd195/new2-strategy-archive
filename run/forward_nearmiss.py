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


def verdicts(series: dict, n_days: int) -> dict:
    """series: {key: {"cal": arr, "cal_x2": arr, "cost0": arr}} over forward days."""
    stage = "FINAL" if n_days >= FINAL_DAYS else ("INTERIM" if n_days >= INTERIM_DAYS else "OBSERVING")
    t = {k: nw_t(v["cal"][:FINAL_DAYS]) for k, v in series.items()}
    p = {k: p_one_sided(x) for k, x in t.items()}
    rej = holm(p, ALPHA_FAMILY)
    out = {}
    for k, v in series.items():
        x = v["cal"][:FINAL_DAYS]
        h = len(x) // 2
        row = {"days": len(x), "sr_ann": sr(x), "sr_cost0": sr(v["cost0"][:FINAL_DAYS]),
               "sr_cost_x2": sr(v["cal_x2"][:FINAL_DAYS]), "cum_return_pct": float(np.sum(x) * 100),
               "nw_t": t[k], "p_one_sided": p[k], "halves_sr": [sr(x[:h]), sr(x[h:])] if h > 1 else [None, None]}
        if stage == "FINAL":
            ok = rej[k] and min(row["halves_sr"]) > 0 and np.mean(v["cal_x2"][:FINAL_DAYS]) > 0
            row["verdict"] = "FORWARD_PASS" if ok else "FORWARD_FAIL"
        elif stage == "INTERIM" and t[k] < -1.0:
            row["verdict"] = "FORWARD_FAIL_FUTILITY"
        else:
            row["verdict"] = "OBSERVING"
        out[k] = row
    return {"stage": stage, "alpha_family": ALPHA_FAMILY, "holm_reject": rej, "tests": out}


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
    out = {"version": VERSION, "asof": asof.isoformat(), "read": [LOOKBACK_FROM, end],
           "forward_from": FORWARD_START, "forward_days": n_days, "prereg_commit": committed.isoformat(),
           "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "flags": r["flags"], **verdicts(series, n_days)}
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
    print(out["stage"], out["forward_days"], "days")
    for k, v in out["tests"].items():
        print(k, v["verdict"], f"SR {v['sr_ann']:.2f} t {v['nw_t']:.2f} cum {v['cum_return_pct']:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
