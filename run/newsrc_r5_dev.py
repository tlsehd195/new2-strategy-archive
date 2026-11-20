#!/usr/bin/env python3
"""Round 5 of the repeated new-source search, DEV only (ADR 0100).

First use of Binance COIN-M futures: alt quarterly basis (market timing and
cross-section) and coin-margined perp volume share.  Runs exactly the 4 trials
of ``run/audit/evidence_newsrc_r5_20261120/prereg_newsrc_r5_v1.json``.  Traded
legs are USDT-M perps (``newsrc_engine``); COIN-M series are signals only.
BTCUSD COIN-M contracts are never downloaded or read.

Usage::

    python3 run/newsrc_r5_dev.py --data-dir <dir> --fetch
    python3 run/newsrc_r5_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import broad_universe_fetch as bf  # noqa: E402
import newsrc_engine as ne  # noqa: E402
import newsrc_r1_dev as r1  # noqa: E402
import wide_universe_dev as wu  # noqa: E402

VERSION = "newsrc-r5-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r5_20261120"
PREREG = EVID / "prereg_newsrc_r5_v1.json"
CONTEXT_ID = "U50-14d-newsrc-r5-coinm-v1"
CM_BASE = bf.S3 + "/data/futures/cm/monthly/klines"
CM_LIST = bf.S3 + "?delimiter=/&prefix=data/futures/cm/monthly/klines/"
KEYS = [("QB1_QBASIS_TIMING", "U50"), ("QB2_QBASIS_XS", "U50"),
        ("CM1_CMSHARE_LEVEL", "U50"), ("CM2_CMSHARE_CHANGE", "U50")]
EVAL_START = {"S1": "2021-07-05", "S2": "2024-12-16"}
MIN_EXPIRY_DAYS = 14
_cache: dict = {}


# --------------------------------------------------------------------------- fetch

def list_contracts() -> list:
    out, marker = [], ""
    while True:
        body = bf.get(CM_LIST + (f"&marker={marker}" if marker else "")).decode()
        ps = re.findall(r"<Prefix>([^<]*)</Prefix>", body)[1:]
        out += [p.rstrip("/").split("/")[-1] for p in ps]
        if "<IsTruncated>true" not in body:
            return sorted(n for n in out if not n.startswith("BTC"))
        marker = ps[-1]


def fetch(data: Path, workers: int = 32) -> int:
    out = data / "cm1d"
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(n, m) for n in list_contracts() for m in bf.months()]
    counts, errors = {}, []

    def one(job):
        n, m = job
        dst = out / f"{n}-{m}.csv"
        if dst.exists():
            return "skip"
        body = r1.checked(CM_BASE, f"{n}/1d/{n}-1d-{m}.zip")
        if body is None:
            return "404"
        dst.write_text(bf.unzip_one(body))
        return "ok"

    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(one, j): j for j in jobs}
        for f in cf.as_completed(futs):
            try:
                k = f.result()
            except Exception as e:  # recorded, never silently skipped
                k = "error"
                errors.append(f"{futs[f]}: {e}")
            counts[k] = counts.get(k, 0) + 1
    print("coin-m", counts)
    return 1 if errors else 0


# --------------------------------------------------------------------------- loaders

def expiry_ms(contract: str) -> int:
    d = contract.split("_")[1]
    return int(dt.datetime(2000 + int(d[:2]), int(d[2:4]), int(d[4:6]), 8, tzinfo=dt.timezone.utc).timestamp() * 1000)


def load_cm(data: Path, days: np.ndarray) -> dict:
    """{'close': {contract: Series}, 'basevol': {contract: Series}} inside the segment only."""
    t0, t1 = int(days[0]), int(days[-1])
    bu.assert_allowed(t0, t1)
    close, vol = {}, {}
    for p in sorted((data / "cm1d").glob("*.csv")):
        n = p.name.rsplit("-", 2)[0]
        if n.startswith("BTC"):
            continue
        for f in bu.read_rows(p):
            t = int(f[0])
            if t0 <= t <= t1:
                close.setdefault(n, {})[t] = float(f[4])
                vol.setdefault(n, {})[t] = float(f[7]) * float(f[4])
    to = lambda d: pd.DataFrame({k: pd.Series(v) for k, v in d.items()}).reindex(days)  # noqa: E731
    return {"close": to(close), "usdvol": to(vol)}


def basis_table(cm: dict, days: np.ndarray) -> pd.DataFrame:
    """basis_ann per base (column = USDT-M symbol XUSDT) per day."""
    close = cm["close"]
    bases = sorted({c.split("_")[0] for c in close.columns if not c.endswith("_PERP")})
    out = {}
    for b in bases:
        perp = close.get(f"{b}_PERP")
        if perp is None:
            continue
        quarters = sorted((c for c in close.columns if c.startswith(b + "_") and not c.endswith("_PERP")), key=expiry_ms)
        vals = np.full(len(days), np.nan)
        for j, t in enumerate(days):
            for q in quarters:
                left = (expiry_ms(q) - int(t)) / bu.DAY_MS
                if left > MIN_EXPIRY_DAYS:
                    fq, fp = close[q].iloc[j], perp.iloc[j]
                    if fq > 0 and fp > 0:
                        vals[j] = (fq / fp - 1.0) * 365.0 / left
                    break
        out[b[:-3] + "USDT"] = vals
    return pd.DataFrame(out, index=days)


def seg_cache(ctx: dict) -> dict:
    if ctx["seg"] not in _cache:
        cm = load_cm(ctx["data"], ctx["days"])
        perps = {c: c[:-8] + "USDT" for c in cm["usdvol"].columns if c.endswith("_PERP")}
        _cache[ctx["seg"]] = {"basis": basis_table(cm, ctx["days"]),
                              "cmvol": cm["usdvol"][list(perps)].rename(columns=perps)}
    return _cache[ctx["seg"]]


# --------------------------------------------------------------------------- signals

def pick_k(sig: pd.Series, universe: list, div: int, min_n: int, long_top: bool) -> dict:
    s = sig.reindex(universe).dropna()
    k = len(s) // div
    if len(s) < min_n or k < 1:
        return {}
    order = sorted(s.index, key=lambda n: (-s[n], n))
    top, bot = order[:k], order[-k:]
    longs, shorts = (top, bot) if long_top else (bot, top)
    w = {n: 1.0 / (2 * k) for n in longs}
    w.update({n: -1.0 / (2 * k) for n in shorts})
    return w


def qb1(basis: pd.DataFrame, cut: int, u50: list) -> pd.Series:
    b = basis.iloc[cut - 179: cut + 1]
    med = b.median(axis=1, skipna=True).where(b.notna().sum(axis=1) >= 4)
    if med.notna().sum() < 120 or np.isnan(med.iloc[-1]):
        return pd.Series(np.nan, index=u50)
    on = med.iloc[-1] <= med.median()
    return pd.Series(1.0 / len(u50) if on else 0.0, index=u50)


def signals(ctx: dict) -> dict:
    cut, ranked = ctx["cut"], ctx["ranked"]
    u50 = wu.slice_universe(ranked, "U50")
    if not u50 or cut - 179 < 0:
        return {}
    c = seg_cache(ctx)
    qv = ctx["piv"]["qv"]
    out = {("QB1_QBASIS_TIMING", "U50"): qb1(c["basis"], cut, u50)}
    b7 = c["basis"].iloc[cut - 6: cut + 1]
    out[("QB2_QBASIS_XS", "U50")] = b7.mean().where(b7.notna().sum() >= 5)
    cmv = c["cmvol"].reindex(columns=qv.columns)
    a, u = cmv.iloc[cut - 27: cut + 1], qv.iloc[cut - 27: cut + 1]
    ok = (a > 0) & (u > 0)
    out[("CM1_CMSHARE_LEVEL", "U50")] = np.log(a.where(ok).sum() / u.where(ok).sum()).where(ok.sum() >= 20)
    lr = np.log(cmv.iloc[cut - 34: cut + 1] / qv.iloc[cut - 34: cut + 1]).replace([np.inf, -np.inf], np.nan)
    rec, old = lr.iloc[-7:], lr.iloc[:-7]
    out[("CM2_CMSHARE_CHANGE", "U50")] = (rec.mean() - old.mean()).where((rec.notna().sum() >= 5) & (old.notna().sum() >= 20))
    return {k: v.replace([np.inf, -np.inf], np.nan) for k, v in out.items()}


def weights(h: str, sig: pd.Series, universe: list, ctx: dict) -> dict:
    if h == "QB1_QBASIS_TIMING":
        s = sig.dropna()
        return {k: float(v) for k, v in s.items() if v != 0 and k in universe}
    if h == "QB2_QBASIS_XS":
        return pick_k(sig, universe, 3, 6, long_top=False)
    return pick_k(sig, universe, 5, 10, long_top=True)


SPEC = ne.Spec(keys=KEYS, signals=signals, weights=weights, eval_start=EVAL_START, hold=14)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r5_dev.json")
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
