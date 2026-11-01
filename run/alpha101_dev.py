#!/usr/bin/env python3
"""Eight WorldQuant '101 Formulaic Alphas' as a weekly cross-section, DEV only (ADR 0054).

Runs exactly the alphas fixed in
``run/audit/evidence_alpha101_20261101/prereg_alpha101_v1.json`` (committed
before any data was downloaded) on the point-in-time top-50 universe of
ADR 0035/0040 with the measured alt cost, rebalancing every Monday and
holding 7 days.  The portfolio, cost and statistics engine is
``cross_section_v2_dev.run_segment`` unchanged; only the daily loader (adds
high, low, base volume), the signal function, the holding period and the
hypothesis list are swapped in.

Never reads: 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards; BTCUSDT
in S2.  No orders, no private API, no NEW2.

Usage (data from run/broad_universe_fetch.py, steps daily/funding/bar2345)::

    python3 run/alpha101_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import cross_section_v2_dev as cs  # noqa: E402

VERSION = "alpha101-dev-v1"
EVID = HERE / "audit" / "evidence_alpha101_20261101"
PREREG = EVID / "prereg_alpha101_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"
HOLD_DAYS = 7
ALPHAS = ("A002", "A006", "A012", "A033", "A041", "A053", "A054", "A101")
#: paper convention: the alpha value is the desired position -> highest values are long
LONG_TOP = {a: True for a in ALPHAS}
FIELDS = ("open", "high", "low", "close", "volume", "qv", "tbq")


def load_daily(data: Path, seg: str) -> dict:
    """Daily OHLC, base volume, quote volume, taker-buy quote volume of every symbol in the segment."""
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
                recs.append((sym, t, float(f[1]), float(f[2]), float(f[3]), float(f[4]),
                             float(f[5]), float(f[7]), float(f[10])))
    df = pd.DataFrame(recs, columns=["sym", "t", *FIELDS]).drop_duplicates(["sym", "t"])
    assert df["t"].between(t0, t1).all()
    days = np.arange(t0, t1 + 1, bu.DAY_MS)
    return {k: df.pivot(index="t", columns="sym", values=k).reindex(days) for k in FIELDS}


def ts_corr(x: pd.DataFrame, y: pd.DataFrame) -> pd.Series:
    """Pearson correlation per column over all rows; NaN if any value is missing or a series is constant."""
    xv, yv = x.values.astype(float), y.values.astype(float)
    ok = np.isfinite(xv).all(axis=0) & np.isfinite(yv).all(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns -> NaN, set below
        xd, yd = xv - np.nanmean(xv, axis=0), yv - np.nanmean(yv, axis=0)
        r = (xd * yd).sum(axis=0) / np.sqrt((xd ** 2).sum(axis=0) * (yd ** 2).sum(axis=0))
    r[~ok | ~np.isfinite(r)] = np.nan
    return pd.Series(r, index=x.columns)


def _clean(s: pd.Series) -> pd.Series:
    return s.where(np.isfinite(s.astype(float)))


def signals(piv: dict, cut: int, universe: list) -> dict:
    """Alpha values from daily bars with index <= cut, restricted to the universe."""
    o, h, l, c, v = (piv[k][universe] for k in ("open", "high", "low", "close", "volume"))
    qv = piv["qv"][universe]
    vwap = qv / v.where(v > 0)

    def w(df: pd.DataFrame, n: int) -> pd.DataFrame:
        """Last n rows ending at the cut (fewer if the segment starts later)."""
        return df.iloc[max(0, cut - n + 1): cut + 1]

    out = {}
    with np.errstate(invalid="ignore", divide="ignore"):
        # A002: -corr(rank(delta(log v, 2)), rank((c-o)/o), 6)
        dlv = np.log(v.where(v > 0)).diff(2)
        x2 = w(dlv, 6).rank(axis=1, pct=True)
        y2 = w((c - o) / o, 6).rank(axis=1, pct=True)
        a2 = -ts_corr(x2, y2)
        if len(x2) < 6:
            a2[:] = np.nan
        out["A002"] = a2
        # A006: -corr(open, volume, 10)
        a6 = -ts_corr(w(o, 10), w(v, 10))
        if len(w(o, 10)) < 10:
            a6[:] = np.nan
        out["A006"] = a6
        # A012: sign(delta(v,1)) * -(c/c_{t-1} - 1)   (price-level free, prereg deviation)
        out["A012"] = _clean(np.sign(v.iloc[cut] - v.iloc[cut - 1]) * -(c.iloc[cut] / c.iloc[cut - 1] - 1.0)) \
            if cut >= 1 else pd.Series(np.nan, index=universe)
        # A033: rank(-(1 - o/c)); ranking is monotone, so the raw value picks the same names
        out["A033"] = _clean(o.iloc[cut] / c.iloc[cut] - 1.0)
        # A041: (sqrt(h*l) - vwap) / vwap   (price-level free, prereg deviation)
        out["A041"] = _clean((np.sqrt(h.iloc[cut] * l.iloc[cut]) - vwap.iloc[cut]) / vwap.iloc[cut])
        # A053: -delta(((c-l)-(h-c))/(c-l), 9)
        x53 = ((c - l) - (h - c)) / (c - l).where(c != l)
        out["A053"] = _clean(-(x53.iloc[cut] - x53.iloc[cut - 9])) if cut >= 9 else pd.Series(np.nan, index=universe)
        # A054: -((l-c)*o^5) / ((l-h)*c^5)
        lo_, hi_, op, cl = l.iloc[cut], h.iloc[cut], o.iloc[cut], c.iloc[cut]
        out["A054"] = _clean(-((lo_ - cl) * op ** 5) / ((lo_ - hi_).where(lo_ != hi_) * cl ** 5))
        # A101: (c-o) / ((h-l) + 0.001*c)   (scale-free constant, prereg deviation)
        out["A101"] = _clean((cl - op) / ((hi_ - lo_) + 0.001 * cl))
    return out


def prior_trials() -> int:
    return sum(json.loads(ln).get("dev_evaluated", 0) for ln in TRIALS.read_text().splitlines() if ln.strip())


def install() -> None:
    """Swap this check's loader, signals, holding period and hypothesis list into the ADR 0040 engine."""
    cs.load_daily = load_daily
    cs.signals = signals
    cs.HOLD_DAYS = HOLD_DAYS
    cs.LONG_TOP = dict(LONG_TOP)
    cs.PREREG = PREREG
    cs.EVID = EVID


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "alpha101_dev.json")
    a = ap.parse_args(argv)
    install()
    prior = prior_trials()
    rc = cs.main(["--data-dir", str(a.data_dir), "--out", str(a.out)])
    out = json.loads(a.out.read_text())
    out["version"] = VERSION
    out["engine_script_sha256"] = out.pop("script_sha256")
    out["script_sha256"] = bu.sha(Path(__file__))
    s1_years = out["segments"]["S1"]["n_days"] / 365.0
    key = f"N={prior + len(ALPHAS)} (dev_search_trials.jsonl sum + this)"
    out["null_max_sr_ann"][key] = bu.expected_max_sr(prior + len(ALPHAS)) / math.sqrt(s1_years)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("null max SR", {k: round(val, 2) for k, val in out["null_max_sr_ann"].items()})
    return rc


if __name__ == "__main__":
    sys.exit(main())
