#!/usr/bin/env python3
"""Round 7 of the repeated new-source search, DEV only (ADR 0102).

Coin Metrics all-exchange reported spot volume vs Binance perp volume (level,
change), size by estimated market cap, attention (volume rank minus market-cap
rank).  Runs exactly the 4 trials of
``run/audit/evidence_newsrc_r7_20261120/prereg_newsrc_r7_v1.json``.  Data: the
ADR 0099 Coin Metrics files (``newsrc_r4_dev.py --fetch``).

Usage::

    python3 run/newsrc_r7_dev.py --data-dir <dir>
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
import newsrc_r4_dev as r4  # noqa: E402
import wide_universe_dev as wu  # noqa: E402

VERSION = "newsrc-r7-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r7_20261120"
PREREG = EVID / "prereg_newsrc_r7_v1.json"
CONTEXT_ID = "U50-14d-newsrc-r7-cmvolume-v1"
KEYS = [("XS1_SPOTSHARE_LEVEL", "U50"), ("XS2_SPOTSHARE_CHANGE", "U50"),
        ("SZ1_MCAP_SIZE", "U50"), ("AT1_ATTENTION", "U50")]
LONG_TOP = {"XS1_SPOTSHARE_LEVEL": True, "XS2_SPOTSHARE_CHANGE": True, "SZ1_MCAP_SIZE": False, "AT1_ATTENTION": False}
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}
COLS = ("volume_reported_spot_usd_1d", "CapMrktEstUSD")
_cache: dict = {}


def load_cols(data: Path, days: np.ndarray, syms: list) -> dict:
    t0, t1 = int(days[0]), int(days[-1])
    bu.assert_allowed(t0, t1)
    amap = json.loads((data / "coinmetrics_map.json").read_text())
    frames = {c: {} for c in COLS}
    for s in syms:
        a = amap.get(s)
        p = data / "coinmetrics" / f"{a}.csv"
        if not a or not p.exists():
            continue
        vals = {c: {} for c in COLS}
        with p.open() as fh:
            for r in csv.DictReader(fh):
                t = bu.ms(r["time"][:10])
                if not (t0 <= t <= t1):
                    continue
                for c in COLS:
                    try:
                        x = float(r.get(c) or "")
                    except ValueError:
                        continue
                    if x > 0:
                        vals[c][t] = x
        for c in COLS:
            if vals[c]:
                frames[c][s] = pd.Series(vals[c])
    return {c: pd.DataFrame(frames[c]).reindex(index=days, columns=syms) for c in COLS}


def cmd(ctx: dict) -> dict:
    if ctx["seg"] not in _cache:
        _cache[ctx["seg"]] = load_cols(ctx["data"], ctx["days"], ctx["syms"])
    return _cache[ctx["seg"]]


def signals(ctx: dict) -> dict:
    cut, ranked = ctx["cut"], ctx["ranked"]
    u50 = wu.slice_universe(ranked, "U50")
    if not u50 or cut - 34 < 0:
        return {}
    d = cmd(ctx)
    qv = ctx["piv"]["qv"][u50]
    sv = d["volume_reported_spot_usd_1d"][u50]
    a, b = sv.iloc[cut - 27: cut + 1], qv.iloc[cut - 27: cut + 1]
    ok = (a > 0) & (b > 0)
    lvl = np.log(a.where(ok).sum() / b.where(ok).sum()).where(ok.sum() >= 20)
    lr = np.log(sv.iloc[cut - 34: cut + 1] / qv.iloc[cut - 34: cut + 1]).replace([np.inf, -np.inf], np.nan)
    rec, old = lr.iloc[-7:], lr.iloc[:-7]
    chg = (rec.mean() - old.mean()).where((rec.notna().sum() >= 5) & (old.notna().sum() >= 20))
    mc = d["CapMrktEstUSD"][u50].iloc[cut - 6: cut + 1]
    size = np.log(mc.mean()).where(mc.notna().sum() >= 5)
    v30 = ctx["piv"]["qv"][u50].iloc[cut - 29: cut + 1].mean()
    both = size.dropna().index.intersection(v30.dropna().index)
    att = pd.Series(np.nan, index=u50)
    if len(both):
        att[both] = v30[both].rank(ascending=False) - size[both].rank(ascending=False)
    sig = {"XS1_SPOTSHARE_LEVEL": lvl, "XS2_SPOTSHARE_CHANGE": chg, "SZ1_MCAP_SIZE": size, "AT1_ATTENTION": att}
    return {(h, "U50"): sig[h].replace([np.inf, -np.inf], np.nan) for h, _ in KEYS}


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    return r4.pick(sig, universe, LONG_TOP[h])


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r7_dev.json")
    a = ap.parse_args(argv)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
            "source": "Coin Metrics community data, github.com/coinmetrics/data (csv/<asset>.csv)"}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
