#!/usr/bin/env python3
"""Spot-index long / perp short basis harvest, DEV only (ADR 0059).

Runs exactly the two configs of
``run/audit/evidence_strong_hypotheses_20261102/prereg_basis_harvest_v1.json``
(committed before any index-price data was downloaded).

Never reads: 2020, 2023-09-19..2024-06-18, 2025-10-21 onwards; BTCUSDT.

Usage::

    python3 run/basis_harvest_dev.py --data-dir <dir> --step fetch_index
    python3 run/basis_harvest_dev.py --data-dir <dir> --step run
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import broad_universe_fetch as bf  # noqa: E402

EVID = HERE / "audit" / "evidence_strong_hypotheses_20261102"
PREREG = EVID / "prereg_basis_harvest_v1.json"
CALIB = HERE / "audit" / "lowliq_cost_20261027" / "calibration_v1.json"
DAY = bu.DAY_MS
SPOT_SIDE = 0.0015
HURDLE = 0.04


def job_index(out: Path, sym: str, ym: str) -> str:
    dst = out / "index" / f"{sym}-1d-{ym}.csv"
    if dst.exists():
        return "skip"
    body = bf.fetch_checked(f"indexPriceKlines/{sym}/1d/{sym}-1d-{ym}.zip")
    if body is None:
        return "404"
    dst.write_text(bf.unzip_one(body))
    return "ok"


def fetch_index(data: Path, syms: list) -> None:
    (data / "index").mkdir(exist_ok=True)
    counts: dict = {}
    with cf.ThreadPoolExecutor(16) as ex:
        for k in ex.map(lambda a: job_index(data, *a), [(s, ym) for s in syms for ym in bf.months()]):
            counts[k] = counts.get(k, 0) + 1
    print("index", counts)


def load_idx(data: Path, sym: str, t0: int, t1: int) -> dict:
    out = {}
    for p in sorted((data / "index").glob(f"{sym}-1d-*.csv")):
        for f in bu.read_rows(p):
            t = int(f[0])
            if t0 <= t <= t1:
                out[t] = float(f[1])
    return out


def load_perp(data: Path, sym: str, t0: int, t1: int) -> dict:
    out = {}
    for p in sorted((data / "daily").glob(f"{sym}-1d-*.csv")):
        for f in bu.read_rows(p):
            t = int(f[0])
            if t0 <= t <= t1:
                out[t] = (float(f[1]), float(f[7]))
    return out


def run_segment(data: Path, seg: str, syms: list, cal: dict) -> dict:
    lo, hi, _ = bu.SEGMENTS[seg]
    t0, t1 = bu.ms(lo), bu.ms(hi)
    bu.assert_allowed(t0, t1)
    days = np.arange(t0, t1 + 1, DAY)
    n = len(syms)
    po = pd.DataFrame(index=days, columns=syms, dtype=float)
    io = po.copy()
    qv = po.copy()
    for s in syms:
        pp, ii = load_perp(data, s, t0, t1), load_idx(data, s, t0, t1)
        for t, (o, q) in pp.items():
            po.at[t, s], qv.at[t, s] = o, q
        for t, o in ii.items():
            io.at[t, s] = o
    fund = bu.load_funding(data, syms, t0, t1)
    fund["day"] = ((fund["t"] - 1) // DAY) * DAY
    fd = fund.groupby(["day", "sym"])["rate"].sum().unstack().reindex(days).reindex(columns=syms)
    ok = po.notna() & io.notna() & (po > 0) & (io > 0)
    ret_p = po.shift(-1) / po - 1.0
    ret_i = io.shift(-1) / io - 1.0
    ok_next = ok & ok.shift(-1).fillna(False)
    pnl = (ret_i - ret_p + fd.fillna(0.0)).where(ok_next)
    basis_chg = (ret_i - ret_p).where(ok_next)
    q30 = qv.rolling(30, min_periods=1).mean()
    w = 1.0 / n
    res = {}
    for cid in ("B1_always_on", "B2_funding_gate"):
        if cid == "B1_always_on":
            on = ok_next.copy()
        else:
            gate = fd.fillna(0.0).rolling(7, min_periods=7).mean().shift(1) > 0
            on = ok_next & gate.reindex(columns=syms).fillna(False)
        gross = (pnl.where(on, 0.0) * w).sum(axis=1).values / 2.0  # per unit capital (2 = spot + margin)
        cost_s = np.zeros(len(days))
        cost_spot = np.zeros(len(days))
        flips = 0
        for s in syms:
            col = on[s].values.astype(bool)
            prev = False
            for k in range(len(days)):
                if col[k] != prev:
                    flips += 1
                    b = q30[s].iloc[k] / 96.0
                    if not b > 0:
                        b = 1.0
                    cost_s[k] += w * bu.side_cost(b, cal, "x", sym_q15=b) / 2.0
                    cost_spot[k] += w * SPOT_SIDE / 2.0
                    prev = col[k]
            if prev:  # final exit at segment end
                k = len(days) - 1
                b = q30[s].iloc[k] / 96.0 if q30[s].iloc[k] > 0 else 1.0
                cost_s[k] += w * bu.side_cost(b, cal, "x", sym_q15=b) / 2.0
                cost_spot[k] += w * SPOT_SIDE / 2.0
        yrs = len(days) / 365.25
        rows = {}
        for name, dly in (("cost0", gross), ("measured", gross - cost_s - cost_spot),
                          ("spot_x2", gross - cost_s - 2 * cost_spot)):
            sd = dly.std(ddof=1)
            cum = np.cumsum(dly)
            r30 = pd.Series(dly).rolling(30).sum()
            rows[name] = {"annual_return_per_capital": float(dly.sum() / yrs),
                          "sharpe": float(dly.mean() / sd * math.sqrt(365.25)) if sd > 0 else float("nan"),
                          "max_drawdown": float((np.maximum.accumulate(cum) - cum).max()),
                          "worst_30d": float(r30.min()), "positive_months_share": float(
                              (pd.Series(dly, index=pd.to_datetime(days, unit="ms")).resample("MS").sum() > 0).mean())}
        h = len(days) // 2
        rows["measured_half_sums"] = [float((gross - cost_s - cost_spot)[:h].sum()), float((gross - cost_s - cost_spot)[h:].sum())]
        rows["annual_cost_per_capital"] = float((cost_s + cost_spot).sum() / yrs)
        rows["cost_split"] = {"perp": float(cost_s.sum()), "spot": float(cost_spot.sum())}
        rows["flips"] = flips
        rows["gross_over_cost"] = float(rows["cost0"]["annual_return_per_capital"] / rows["annual_cost_per_capital"]) \
            if rows["annual_cost_per_capital"] > 0 else float("nan")
        rows["mean_active_symbols"] = float(on.sum(axis=1).mean())
        rows["basis_change_daily_std"] = float(np.nanstd(basis_chg.values))
        rows["funding_mean_annual_when_on"] = float(np.nanmean(fd.where(on).values) * 365.25)
        res[cid] = rows
    return res


def verdict(r: dict) -> str:
    try:
        ok = True
        for seg in ("S1", "S2"):
            x = r[seg]
            ok &= (x["gross_over_cost"] >= 3 and x["measured"]["annual_return_per_capital"] > HURDLE
                   and x["measured"]["sharpe"] >= 1.0 and x["measured"]["max_drawdown"] <= 0.10
                   and x["spot_x2"]["annual_return_per_capital"] > 0)
        ok &= all(h > 0 for h in r["S1"]["measured_half_sums"])
        return "DEV_PROMISING" if ok else "NOT_PROMISING"
    except KeyError:
        return "INSUFFICIENT"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--step", required=True, choices=("fetch_index", "run"))
    ap.add_argument("--out", type=Path, default=EVID / "basis_harvest_dev.json")
    a = ap.parse_args(argv)
    pre = json.loads(PREREG.read_text())
    syms = pre["symbols"]
    if "BTCUSDT" in syms:
        raise SystemExit("BTCUSDT not allowed")
    if a.step == "fetch_index":
        fetch_index(a.data_dir, syms)
        return 0
    cal = bu.load_cost_calibration(CALIB)
    out = {"version": "basis-harvest-dev-v1", "prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(),
           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "spot_side_assumed": SPOT_SIDE, "hurdle": HURDLE, "configs": {}}
    segs = {s: run_segment(a.data_dir, s, syms, cal) for s in bu.SEGMENTS}
    for cid in ("B1_always_on", "B2_funding_gate"):
        out["configs"][cid] = {s: segs[s][cid] for s in segs}
        out["configs"][cid]["verdict"] = verdict(out["configs"][cid])
        for s in segs:
            x = segs[s][cid]
            print(cid, s, "gross/yr", f'{x["cost0"]["annual_return_per_capital"]:.4f}',
                  "net/yr", f'{x["measured"]["annual_return_per_capital"]:.4f}', "SR", f'{x["measured"]["sharpe"]:.2f}',
                  "maxDD", f'{x["measured"]["max_drawdown"]:.4f}', "g/c", f'{x["gross_over_cost"]:.2f}',
                  "flips", x["flips"], "halves", [round(v, 4) for v in x["measured_half_sums"]])
        print(cid, out["configs"][cid]["verdict"])
    a.out.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
