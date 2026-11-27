#!/usr/bin/env python3
"""Round 14 of the repeated new-source search, DEV only (ADR 0109).

Stablecoin supply growth (USDT+USDC+BUSD+DAI, Coin Metrics community data)
as timing of the equal-weight U50 index, and price relative to the 30-day
VWAP (U50 quintiles).  Runs exactly the 3 trials of
``run/audit/evidence_newsrc_r14_20261120/prereg_newsrc_r14_v1.json``.

Usage::

    python3 run/newsrc_r14_dev.py --data-dir <dir> [--fetch]
    # --fetch downloads csv/{usdt,usdc,busd,dai}.csv of github.com/coinmetrics/data into <dir>/cm_stable
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import research_data_integrity as rdi  # noqa: E402
import newsrc_engine as ne  # noqa: E402
import wide_universe_dev as wu  # noqa: E402

VERSION = "newsrc-r14-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r14_20261120"
PREREG = EVID / "prereg_newsrc_r14_v1.json"
CONTEXT_ID = "U50-14d-newsrc-r14-v1"
KEYS = [("ST1_STABLE14", "U50"), ("ST2_STABLE28_REL", "U50"), ("VW1_VWAP30", "U50")]
TIMING = {"ST1_STABLE14", "ST2_STABLE28_REL"}
STABLES = ("usdt", "usdc", "busd", "dai")
RAW = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/"
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}
_CACHE: dict = {}


def fetch(data: Path) -> None:
    out = data / "cm_stable"
    out.mkdir(parents=True, exist_ok=True)
    for a in STABLES:
        with urllib.request.urlopen(RAW + f"{a}.csv", timeout=120) as r:
            (out / f"{a}.csv").write_bytes(r.read())


def stable_supply(data: Path, seg: str, days: np.ndarray) -> pd.Series:
    """Summed SplyCur by day, segment rows only; NaN where usdt has no value."""
    lo, hi, _ = bu.SEGMENTS[seg]
    t0, t1 = bu.ms(lo), bu.ms(hi)
    bu.assert_allowed(t0, t1)
    cols = {}
    for a in STABLES:
        vals = {}
        with open(data / "cm_stable" / f"{a}.csv", newline="") as f:
            for row in csv.DictReader(f):
                t = bu.ms(row["time"][:10])
                if t0 <= t <= t1 and row.get("SplyCur"):
                    vals[t] = float(row["SplyCur"])
        cols[a] = pd.Series(vals, dtype=float)
    df = pd.DataFrame(cols).reindex(days)
    return df.sum(axis=1, min_count=1).where(df["usdt"].notna())


def load_volume(data: Path, seg: str, days: np.ndarray, syms: list) -> pd.DataFrame:
    lo, hi, _ = bu.SEGMENTS[seg]
    t0, t1 = bu.ms(lo), bu.ms(hi)
    bu.assert_allowed(t0, t1)
    recs = []
    for p in sorted((data / "daily").glob("*.csv")):
        sym = p.name.split("-1d-")[0]
        if seg == "S2" and sym == "BTCUSDT":
            continue
        for f in bu.read_rows(p):
            t = int(f[0])
            if t0 <= t <= t1:
                recs.append((sym, t, float(f[5])))
    df = pd.DataFrame(recs, columns=["sym", "t", "vol"])
    df, _ = rdi.clean_panel(df, ("vol",), nonneg_cols=("vol",),
                            log=rdi.panel_log("newsrc_r14_dev.load_volume", data, seg))   # ADR 0116
    return df.pivot(index="t", columns="sym", values="vol").reindex(days).reindex(columns=syms)


def extras(ctx: dict) -> tuple:
    key = (str(ctx["data"]), ctx["seg"])
    if key not in _CACHE:
        _CACHE[key] = (stable_supply(ctx["data"], ctx["seg"], ctx["days"]),
                       load_volume(ctx["data"], ctx["seg"], ctx["days"], ctx["syms"]))
    return _CACHE[key]


def st1_on(s: pd.Series, cut: int) -> float:
    a, b = s.iloc[cut], s.iloc[cut - 14]
    if not (a > 0 and b > 0):
        return float("nan")
    return 1.0 if np.log(a / b) > 0 else 0.0


def st2_on(s: pd.Series, cut: int) -> float:
    g = np.log(s / s.shift(28)).iloc[: cut + 1].replace([np.inf, -np.inf], np.nan)
    if g.notna().sum() < 60 or not np.isfinite(g.iloc[cut]):
        return float("nan")
    return 1.0 if g.iloc[cut] > g.dropna().median() else 0.0


def vwap_signal(close: pd.DataFrame, qv: pd.DataFrame, vol: pd.DataFrame, cut: int) -> pd.Series:
    q, v = qv.iloc[cut - 29: cut + 1], vol.iloc[cut - 29: cut + 1]
    ok = (q.notna() & v.notna() & (v > 0))
    vw = q.where(ok).sum() / v.where(ok).sum()
    sig = np.log(close.iloc[cut] / vw).where(ok.sum() >= 20)
    return sig.replace([np.inf, -np.inf], np.nan)


def signals(ctx: dict) -> dict:
    cut, ranked = ctx["cut"], ctx["ranked"]
    u50 = wu.slice_universe(ranked, "U50")
    if not u50 or cut - 29 < 0:
        return {}
    sup, vol = extras(ctx)
    piv = ctx["piv"]
    return {("ST1_STABLE14", "U50"): pd.Series(st1_on(sup, cut), index=u50),
            ("ST2_STABLE28_REL", "U50"): pd.Series(st2_on(sup, cut), index=u50),
            ("VW1_VWAP30", "U50"): vwap_signal(piv["close"][u50], piv["qv"][u50], vol[u50], cut)}


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    if h in TIMING:
        on = float(sig.iloc[0]) if len(sig) else float("nan")
        return {s: 1.0 / len(universe) for s in universe} if on == 1.0 else {}
    return wu.pick(sig, universe, False)


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r14_dev.json")
    a = ap.parse_args(argv)
    if a.fetch:
        fetch(a.data_dir)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
            "source": "Coin Metrics community data, github.com/coinmetrics/data (csv/usdt,usdc,busd,dai.csv)"}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
