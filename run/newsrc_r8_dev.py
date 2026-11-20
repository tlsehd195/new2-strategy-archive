#!/usr/bin/env python3
"""Round 8 of the repeated new-source search, DEV only (ADR 0103).

Ethereum on-chain timing of the equal-weight U50 alt index (Coin Metrics eth.csv):
MVRV, exchange net flow, exchange-held supply, active addresses.  Runs exactly
the 4 trials of ``run/audit/evidence_newsrc_r8_20261120/prereg_newsrc_r8_v1.json``.
btc.csv is never read.

Usage::

    python3 run/newsrc_r8_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import csv
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

VERSION = "newsrc-r8-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r8_20261120"
PREREG = EVID / "prereg_newsrc_r8_v1.json"
CONTEXT_ID = "U50-14d-newsrc-r8-ethonchain-timing-v1"
KEYS = [("OC1_ETH_MVRV_TIMING", "U50"), ("OC2_ETH_NETFLOW_TIMING", "U50"),
        ("OC3_ETH_EXSUPPLY_TIMING", "U50"), ("OC4_ETH_ACTIVITY_TIMING", "U50")]
EVAL_START = {"S1": "2021-07-05", "S2": "2024-12-16"}
COLS = ("CapMVRVCur", "FlowInExNtv", "FlowOutExNtv", "SplyExNtv", "AdrActCnt")
_cache: dict = {}


def load_eth(data: Path, days: np.ndarray) -> pd.DataFrame:
    t0, t1 = int(days[0]), int(days[-1])
    bu.assert_allowed(t0, t1)
    rows = {}
    with (data / "coinmetrics" / "eth.csv").open() as fh:
        for r in csv.DictReader(fh):
            t = bu.ms(r["time"][:10])
            if t0 <= t <= t1:
                vals = {}
                for c in COLS:
                    try:
                        vals[c] = float(r.get(c) or "")
                    except ValueError:
                        vals[c] = np.nan
                rows[t] = vals
    return pd.DataFrame.from_dict(rows, orient="index").reindex(index=days, columns=list(COLS))


def states(e: pd.DataFrame, cut: int) -> dict:
    out = {}
    mv = e["CapMVRVCur"].iloc[cut - 179: cut + 1]
    out["OC1_ETH_MVRV_TIMING"] = (bool(mv.iloc[-1] <= mv.median())
                                  if mv.notna().sum() >= 120 and not np.isnan(mv.iloc[-1]) else None)
    net = (e["FlowInExNtv"] - e["FlowOutExNtv"]).rolling(7, min_periods=7).sum().iloc[cut - 179: cut + 1]
    out["OC2_ETH_NETFLOW_TIMING"] = (bool(net.iloc[-1] <= net.median())
                                     if net.notna().sum() >= 120 and not np.isnan(net.iloc[-1]) else None)
    sx = e["SplyExNtv"]
    a, b = sx.iloc[cut], sx.iloc[cut - 28]
    out["OC3_ETH_EXSUPPLY_TIMING"] = bool(np.log(a / b) < 0) if a > 0 and b > 0 else None
    aa = e["AdrActCnt"]
    rec, old = aa.iloc[cut - 27: cut + 1], aa.iloc[cut - 55: cut - 27]
    out["OC4_ETH_ACTIVITY_TIMING"] = (bool(np.log(rec.mean() / old.mean()) > 0)
                                      if rec.notna().sum() >= 20 and old.notna().sum() >= 20 else None)
    return out


def signals(ctx: dict) -> dict:
    cut, ranked = ctx["cut"], ctx["ranked"]
    u50 = wu.slice_universe(ranked, "U50")
    if not u50 or cut - 185 < 0:
        return {}
    if ctx["seg"] not in _cache:
        _cache[ctx["seg"]] = load_eth(ctx["data"], ctx["days"])
    st = states(_cache[ctx["seg"]], cut)
    return {(h, "U50"): pd.Series(np.nan if st[h] is None else (1.0 / len(u50) if st[h] else 0.0), index=u50)
            for h, _ in KEYS}


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    s = sig.dropna()
    return {k: float(v) for k, v in s.items() if v != 0 and k in universe}


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r8_dev.json")
    a = ap.parse_args(argv)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
            "source": "Coin Metrics community data eth.csv"}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
