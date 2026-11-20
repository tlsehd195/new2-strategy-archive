#!/usr/bin/env python3
"""Round 9 of the repeated new-source search, DEV only (ADR 0104).

Listing age (U50, W200), large-to-small lead-lag timing, weekend momentum.
Runs exactly the 4 trials of
``run/audit/evidence_newsrc_r9_20261120/prereg_newsrc_r9_v1.json``.

Usage::

    python3 run/newsrc_r9_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import newsrc_engine as ne  # noqa: E402
import wide_universe_dev as wu  # noqa: E402

VERSION = "newsrc-r9-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r9_20261120"
PREREG = EVID / "prereg_newsrc_r9_v1.json"
CONTEXT_ID = "U50W200-14d-newsrc-r9-v1"
KEYS = [("AG1_AGE", "U50"), ("AG1_AGE", "W200"), ("LL1_LEADLAG", "U50"), ("WK1_WEEKEND_MOM", "U50")]
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}
_first: dict = {}


def first_bars(data: Path) -> dict:
    """symbol -> first daily bar time (ms) among the daily files on disk (allowed months only)."""
    if not _first:
        earliest = {}
        for p in (data / "daily").glob("*-1d-*.csv"):
            sym, ym = p.name[:-4].split("-1d-")
            if sym not in earliest or ym < earliest[sym][0]:
                earliest[sym] = (ym, p)
        for sym, (ym, p) in earliest.items():
            rows = bu.read_rows(p)
            if rows:
                _first[sym] = int(rows[0][0])
    return _first


def is_weekend(t: int) -> bool:
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).weekday() >= 5


def signals(ctx: dict) -> dict:
    cut, ranked, days = ctx["cut"], ctx["ranked"], ctx["days"]
    if cut - 55 < 0:
        return {}
    first = first_bars(ctx["data"])
    tcut = int(days[cut])
    age = pd.Series({s: (tcut - first[s]) / bu.DAY_MS for s in ranked if s in first}, dtype=float)
    out = {("AG1_AGE", "U50"): age, ("AG1_AGE", "W200"): age}
    u50 = wu.slice_universe(ranked, "U50")
    cret = ctx["cret"]
    if len(u50) >= 50:
        top, rest = u50[:10], u50[10:]
        r7 = (ctx["piv"]["close"].iloc[cut][top] / ctx["piv"]["close"].iloc[cut - 7][top] - 1.0).mean()
        on = bool(r7 > 0) if not np.isnan(r7) else None
        if on is not None:
            out[("LL1_LEADLAG", "U50")] = pd.Series({s: (1.0 / len(rest) if on else 0.0) for s in rest})
    win = cret.iloc[cut - 55: cut + 1][u50]
    wk = np.array([is_weekend(int(t)) for t in days[cut - 55: cut + 1]])
    w = win[wk]
    out[("WK1_WEEKEND_MOM", "U50")] = w.sum().where(w.notna().sum() >= 12)
    return out


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    if h == "LL1_LEADLAG":
        s = sig.dropna()
        return {k: float(v) for k, v in s.items() if v != 0 and k in universe}
    return wu.pick(sig, universe, True)


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r9_dev.json")
    a = ap.parse_args(argv)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__))}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    f = first_bars(a.data_dir)
    out["age_lower_bound_names"] = sum(1 for t in f.values() if t == bu.ms("2021-01-01"))
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    print("names whose first bar is 2021-01-01 (age is a lower bound):", out["age_lower_bound_names"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
