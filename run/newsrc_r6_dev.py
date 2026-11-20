#!/usr/bin/env python3
"""Round 6 of the repeated new-source search, DEV only (ADR 0101).

Runs exactly the 5 trials of
``run/audit/evidence_newsrc_r6_20261120/prereg_newsrc_r6_v1.json``:
session-decomposed momentum (Asia 00-08, US 16-24 UTC, difference), jump
frequency, funding-rate volatility.  Engine: ``newsrc_engine``.

Usage::

    python3 run/newsrc_r6_dev.py --data-dir <dir> --fetch   # 00/08/16 UTC opens of ever-U50 names from the 15m archive
    python3 run/newsrc_r6_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import broad_universe_fetch as bf  # noqa: E402
import newsrc_engine as ne  # noqa: E402
import wide_universe_dev as wu  # noqa: E402

VERSION = "newsrc-r6-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r6_20261120"
PREREG = EVID / "prereg_newsrc_r6_v1.json"
CONTEXT_ID = "U50-14d-newsrc-r6-v1"
KEYS = [("SE1_ASIA_MOM", "U50"), ("SE2_US_MOM", "U50"), ("SE3_ASIA_MINUS_US", "U50"),
        ("JR1_JUMPS", "U50"), ("FV1_FUNDING_VOL", "U50")]
LONG_TOP = {"SE1_ASIA_MOM": True, "SE2_US_MOM": True, "SE3_ASIA_MINUS_US": True, "JR1_JUMPS": False, "FV1_FUNDING_VOL": False}
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}
BOUNDS = (0, 8 * 60, 16 * 60)
_cache: dict = {}


# --------------------------------------------------------------------------- fetch

def job_sessions(out: Path, sym: str, ym: str) -> str:
    dst = out / "sess" / f"{sym}-sess-{ym}.csv"
    if dst.exists():
        return "skip"
    body = bf.fetch_checked(f"klines/{sym}/15m/{sym}-15m-{ym}.zip")
    if body is None:
        return "404"
    rows = []
    for r in csv.reader(io.StringIO(bf.unzip_one(body))):
        if not r or not r[0].isdigit():
            continue
        if (int(r[0]) // 60_000) % 1440 in BOUNDS:
            rows.append(f"{r[0]},{r[1]}")
    dst.write_text("open_time,open\n" + "\n".join(rows) + "\n")
    return "ok"


def fetch(data: Path, workers: int = 32) -> int:
    (data / "sess").mkdir(parents=True, exist_ok=True)
    syms = json.loads((data / "ever_u50_14d.json").read_text())
    jobs = [(s, ym) for s in syms for ym in bf.months()]
    counts, errors = {}, []
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(job_sessions, data, s, ym): (s, ym) for s, ym in jobs}
        for f in cf.as_completed(futs):
            try:
                k = f.result()
            except Exception as e:  # recorded, never silently skipped; rerun fetches the rest
                k = "error"
                errors.append(f"{futs[f]}: {e}")
            counts[k] = counts.get(k, 0) + 1
    print("sessions", counts)
    for e in errors[:10]:
        print("ERROR", e)
    return 1 if errors else 0


# --------------------------------------------------------------------------- loaders

def load_sessions(data: Path, days: np.ndarray, syms: list) -> dict:
    """{'asia': DataFrame, 'eu': ..., 'us': ...} of session log returns, indexed by day."""
    t0, t1 = int(days[0]), int(days[-1])
    bu.assert_allowed(t0, t1)
    px = {}
    for p in sorted((data / "sess").glob("*-sess-*.csv")):
        sym = p.name.split("-sess-")[0]
        if sym not in syms:
            continue
        for r in bu.read_rows(p):
            t = int(r[0])
            if t0 <= t <= t1 + bu.DAY_MS - 1:
                px.setdefault(sym, {})[t] = float(r[1])
    out = {k: pd.DataFrame(np.nan, index=days, columns=syms) for k in ("asia", "eu", "us")}
    for sym, d in px.items():
        s = pd.Series(d).sort_index()
        for j, (name, a, b) in enumerate((("asia", 0, 8), ("eu", 8, 16), ("us", 16, 24))):
            start = days + a * 3_600_000
            end = days + b * 3_600_000
            pa, pb = s.reindex(start).values, s.reindex(end).values
            with np.errstate(divide="ignore", invalid="ignore"):
                out[name][sym] = np.log(pb / pa)
    # the US session of the last day ends at the next day's 00:00 -> outside the segment for the last day only
    return out


def seg_cache(ctx: dict) -> dict:
    if ctx["seg"] not in _cache:
        _cache[ctx["seg"]] = load_sessions(ctx["data"], ctx["days"], ctx["syms"])
    return _cache[ctx["seg"]]


# --------------------------------------------------------------------------- signals

def jumps(rets: pd.DataFrame) -> pd.Series:
    a = rets.abs()
    med = a.median()
    cnt = (a.gt(3.0 * med)).sum().astype(float)
    return cnt.where(rets.notna().sum() >= 60)


def funding_vol(data: Path, ctx: dict, cut: int, names: list) -> pd.Series:
    key = ("fund", ctx["seg"])
    if key not in _cache:
        days = ctx["days"]
        f = bu.load_funding(data, ctx["syms"], int(days[0]), int(days[-1]))
        f["day"] = ((f["t"] - 1) // bu.DAY_MS) * bu.DAY_MS
        _cache[key] = f
    f = _cache[key]
    lo, hi = int(ctx["days"][cut - 27]), int(ctx["days"][cut])
    sub = f[(f["day"] >= lo) & (f["day"] <= hi) & f["sym"].isin(names)]
    g = sub.groupby("sym")["rate"]
    sd, n = g.std(ddof=1), g.count()
    return sd.where(n >= 40)


def signals(ctx: dict) -> dict:
    cut, ranked = ctx["cut"], ctx["ranked"]
    u50 = wu.slice_universe(ranked, "U50")
    if not u50 or cut - 89 < 0:
        return {}
    s = seg_cache(ctx)
    asia = s["asia"].iloc[cut - 27: cut + 1][u50]
    us = s["us"].iloc[cut - 27: cut + 1][u50]
    se1 = asia.sum().where(asia.notna().sum() >= 24)
    se2 = us.sum().where(us.notna().sum() >= 24)
    rets = ctx["cret"].iloc[cut - 89: cut + 1][u50]
    sig = {"SE1_ASIA_MOM": se1, "SE2_US_MOM": se2, "SE3_ASIA_MINUS_US": se1 - se2,
           "JR1_JUMPS": jumps(rets), "FV1_FUNDING_VOL": funding_vol(ctx["data"], ctx, cut, u50)}
    return {(h, "U50"): sig[h].replace([np.inf, -np.inf], np.nan) for h, _ in KEYS}


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    return wu.pick(sig, universe, LONG_TOP[h])


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r6_dev.json")
    a = ap.parse_args(argv)
    if a.fetch:
        return fetch(a.data_dir)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__))}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
