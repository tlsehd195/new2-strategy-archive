#!/usr/bin/env python3
"""Round 4 of the repeated new-source search, DEV only (ADR 0099).

First on-chain / supply source: Coin Metrics community data
(github.com/coinmetrics/data, csv/<asset>.csv).  Runs exactly the 6 trials of
``run/audit/evidence_newsrc_r4_20261120/prereg_newsrc_r4_v1.json``: implied
supply growth 28/90 days, perp turnover / market cap, active-address growth,
MVRV.  Engine: ``newsrc_engine``.

Usage::

    python3 run/newsrc_r4_dev.py --data-dir <dir> --fetch   # csv/<asset>.csv of ever-W200 bases into <dir>/coinmetrics
    python3 run/newsrc_r4_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import newsrc_engine as ne  # noqa: E402
import wide_universe_dev as wu  # noqa: E402

VERSION = "newsrc-r4-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r4_20261120"
PREREG = EVID / "prereg_newsrc_r4_v1.json"
AMEND = EVID / "prereg_newsrc_r4_amend1.json"
CONTEXT_ID = "U50W200-14d-newsrc-r4-onchain-v1"
RAW = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/"
KEYS = [("SI1_SUPPLY28", "U50"), ("SI2_SUPPLY90", "U50"), ("SI2_SUPPLY90", "W200"),
        ("TO1_TURNOVER_MCAP", "U50"), ("AA1_ACTIVE_ADDR", "U50"), ("MV1_MVRV", "U50")]
LONG_TOP = {"SI1_SUPPLY28": False, "SI2_SUPPLY90": False, "TO1_TURNOVER_MCAP": False,
            "AA1_ACTIVE_ADDR": True, "MV1_MVRV": False}
EVAL_START = {"S1": "2021-04-12", "S2": "2024-09-30"}
COLS = ("CapMrktEstUSD", "ReferenceRateUSD", "AdrActCnt", "CapMVRVCur")
MIN_SIG = 10
UNIT_PREFIX = re.compile(r"^(1000000|1000|1M)(?=[A-Z])")
_cache: dict = {}


def asset(perp: str) -> str:
    return UNIT_PREFIX.sub("", perp)[:-4].lower()


def fetch(data: Path, workers: int = 16) -> int:
    out = data / "coinmetrics"
    out.mkdir(parents=True, exist_ok=True)
    syms = json.loads((data / "ever_w200.json").read_text())
    got, missing, errors = {}, [], []

    def one(s):
        a = asset(s)
        dst = out / f"{a}.csv"
        if dst.exists():
            return s, a
        try:
            with urllib.request.urlopen(RAW + a + ".csv", timeout=60) as r:
                dst.write_bytes(r.read())
            return s, a
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return s, None
            raise

    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(one, s): s for s in syms}
        for f in cf.as_completed(futs):
            try:
                s, a = f.result()
                if a:
                    got[s] = a
                else:
                    missing.append(s)
            except Exception as e:  # recorded, never silently skipped
                errors.append(f"{futs[f]}: {e}")
    (data / "coinmetrics_map.json").write_text(json.dumps(got, sort_keys=True))
    print("coinmetrics files", len(got), "missing", len(missing), "errors", len(errors))
    return 1 if errors else 0


def load_cm(data: Path, days: np.ndarray, syms: list) -> dict:
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
                    v = r.get(c) or ""
                    try:
                        x = float(v)
                    except ValueError:
                        continue
                    if x > 0:
                        vals[c][t] = x
        for c in COLS:
            if vals[c]:
                frames[c][s] = pd.Series(vals[c])
    return {c: pd.DataFrame(frames[c]).reindex(index=days, columns=syms) for c in COLS}


def cm(ctx: dict) -> dict:
    if ctx["seg"] not in _cache:
        _cache[ctx["seg"]] = load_cm(ctx["data"], ctx["days"], ctx["syms"])
    return _cache[ctx["seg"]]


def pick(sig: pd.Series, universe: list, long_top: bool) -> dict:
    s = sig.reindex(universe).dropna()
    k = len(s) // 5
    if len(s) < MIN_SIG or k < 1:
        return {}
    order = sorted(s.index, key=lambda n: (-s[n], n))
    top, bot = order[:k], order[-k:]
    longs, shorts = (top, bot) if long_top else (bot, top)
    w = {n: 1.0 / (2 * k) for n in longs}
    w.update({n: -1.0 / (2 * k) for n in shorts})
    return w


def signals(ctx: dict) -> dict:
    cut = ctx["cut"]
    if cut - 89 < 0:
        return {}
    d = cm(ctx)
    # amendment 1 (prereg_newsrc_r4_amend1.json): ReferenceRateUSD is empty in the DEV years -> perp close
    supply = (d["CapMrktEstUSD"] / ctx["piv"]["close"]).replace([np.inf, -np.inf], np.nan)
    qv = ctx["piv"]["qv"]
    s28 = np.log(supply.iloc[cut] / supply.iloc[cut - 28])
    s90 = np.log(supply.iloc[cut] / supply.iloc[cut - 89])
    mc = d["CapMrktEstUSD"].iloc[cut - 27: cut + 1]
    to = np.log(qv.iloc[cut - 27: cut + 1].mean() / mc.mean()).where(mc.notna().sum() >= 20)
    aa = d["AdrActCnt"]
    rec, old = aa.iloc[cut - 27: cut + 1], aa.iloc[cut - 55: cut - 27]
    aag = np.log(rec.mean() / old.mean()).where((rec.notna().sum() >= 20) & (old.notna().sum() >= 20))
    mv = d["CapMVRVCur"].iloc[cut - 2: cut + 1].ffill().iloc[-1]
    clean = lambda x: x.replace([np.inf, -np.inf], np.nan)  # noqa: E731
    sig = {"SI1_SUPPLY28": clean(s28), "SI2_SUPPLY90": clean(s90), "TO1_TURNOVER_MCAP": clean(to),
           "AA1_ACTIVE_ADDR": clean(aag), "MV1_MVRV": clean(mv)}
    return {(h, u): sig[h] for h, u in KEYS}


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    return pick(sig, universe, LONG_TOP[h])


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r4_dev.json")
    a = ap.parse_args(argv)
    if a.fetch:
        return fetch(a.data_dir)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "amendment_sha256": bu.sha(AMEND),
            "script_sha256": bu.sha(Path(__file__)), "source": "Coin Metrics community data, github.com/coinmetrics/data (csv/<asset>.csv)"}
    out = ne.evaluate(a.data_dir, SPEC, CONTEXT_ID, head)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    ne.print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
