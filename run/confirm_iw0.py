#!/usr/bin/env python3
"""Stage 1 of the near-miss confirmation (ADR 0110/0111): IW0, Binance spot 2017-08..2019-12.

Tests exactly the 4 statistics of
``run/audit/evidence_confirm_nearmiss_20261120/prereg_confirm_nearmiss_v1.json``
(DS1, CL1, FIP1, COMBO) once, with Holm step-down over the 4.  The candidate
signals are imported unchanged from the DEV tools (newsrc_r11_dev.compute,
newsrc_r12_dev.frog / dispersion_on).  Nothing from 2020 or later is used.

Usage::

    python3 run/confirm_iw0.py --zip <pre2020_spot_binance_15m.zip> --out <json>
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import newsrc_r11_dev as r11  # noqa: E402
import newsrc_r12_dev as r12  # noqa: E402

VERSION = "confirm-iw0-v1"
EVID = HERE / "audit" / "evidence_confirm_nearmiss_20261120"
PREREG = EVID / "prereg_confirm_nearmiss_v1.json"
READ_FROM, READ_TO = "2017-08-01", "2019-12-31"
EVAL_FROM = "2018-06-04"
HOLD = 14
MIN_BARS = 60
MIN_QV30 = 1_000_000.0
MIN_N = 10
COST_BP = 15.0
NW_LAG = 14
ALPHA = 0.05
TESTS = ["DS1_IW0", "CL1_IW0", "FIP1_IW0", "COMBO_IW0"]
DAY = 86_400_000


def load_daily(zpath: Path) -> dict:
    t_hi = bu.ms("2020-01-01")
    frames = []
    with zipfile.ZipFile(zpath) as zf:
        for name in sorted(zf.namelist()):
            if not (name.startswith("klines/") and name.endswith(".csv")):
                continue
            sym = name.split("/")[-1].split("-15m-")[0]
            df = pd.read_csv(io.BytesIO(zf.read(name)), header=None, usecols=range(9))
            df = df[pd.to_numeric(df[0], errors="coerce").notna()].astype(float)
            df.columns = ["t", "open", "high", "low", "close", "vol", "ct", "qv", "count"]
            df["t"] = df["t"].astype("int64")
            df = df[df["t"] < t_hi]
            df["day"] = (df["t"] // DAY) * DAY
            df["sym"] = sym
            frames.append(df)
    raw = pd.concat(frames).drop_duplicates(["sym", "t"]).sort_values(["sym", "t"])
    assert raw["t"].max() < t_hi, "2020 data must never be read"
    g = raw.groupby(["sym", "day"])
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "qv": g["qv"].sum(), "count": g["count"].sum()}).reset_index()
    days = np.arange(bu.ms(READ_FROM), bu.ms(READ_TO) + 1, DAY)
    return {k: d.pivot(index="day", columns="sym", values=k).reindex(days) for k in
            ("open", "high", "low", "close", "qv", "count")}


def rebalance_days(days: np.ndarray) -> list:
    t0 = bu.ms(EVAL_FROM)
    return [int(np.searchsorted(days, t)) for t in range(t0, int(days[-1]) + 1, HOLD * DAY)]


def universe(piv: dict, nbars: pd.DataFrame, cut: int) -> list:
    qv30 = piv["qv"].iloc[max(0, cut - 29): cut + 1].mean()
    ok = (nbars.iloc[cut] >= MIN_BARS) & (qv30 >= MIN_QV30) & piv["close"].iloc[cut].notna()
    return sorted(ok[ok].index)


def targets(piv: dict, cret: pd.DataFrame, cut: int, u: list) -> dict:
    """Target weights per test; CL1/FIP1 = top quintile long minus equal-weight universe."""
    if len(u) < MIN_N or cut - 192 < 0:
        return {t: {} for t in TESTS[:3]}
    ew = {s: 1.0 / len(u) for s in u}
    k = len(u) // 5
    out = {}
    on = r12.dispersion_on(cret[u], cut)
    out["DS1_IW0"] = dict(ew) if on == 1.0 else {}
    cl = r11.compute(piv["close"][u], piv["qv"][u], piv["high"][u], piv["low"][u], piv["count"][u],
                     cret[u], cut)["CL1_CLOSE_LOCATION"]
    fp = r12.frog(piv["close"][u], cret[u], cut)
    for name, sig in (("CL1_IW0", cl), ("FIP1_IW0", fp)):
        s = sig.reindex(u).dropna()
        if len(s) < 2 * k or k < 1:
            out[name] = {}
            continue
        top = sorted(s.index, key=lambda n: (-s[n], n))[:k]
        w = {n: -v for n, v in ew.items()}
        for n in top:
            w[n] = w.get(n, 0.0) + 1.0 / k
        out[name] = w
    return out


def simulate(piv: dict) -> dict:
    days = piv["open"].index.values
    opn = piv["open"]
    ret = (opn.shift(-1) / opn - 1.0)
    cret = piv["close"].pct_change(fill_method=None)
    nbars = piv["close"].notna().cumsum()
    reb = rebalance_days(days)
    names = list(opn.columns)
    gross = {t: np.zeros(len(days)) for t in TESTS[:3]}
    cost = {t: np.zeros(len(days)) for t in TESTS[:3]}
    held = {t: pd.Series(0.0, index=names) for t in TESTS[:3]}
    nuni, missing = [], 0
    last = len(days) - 1  # open-to-open return of the last day would need 2020-01-01: never used
    for j, i in enumerate(reb):
        end = reb[j + 1] if j + 1 < len(reb) else last
        cut = i - 2
        u = universe(piv, nbars, cut)
        nuni.append(len(u))
        tg = targets(piv, cret, cut, u)
        for t in TESTS[:3]:
            w = pd.Series(tg[t], dtype=float).reindex(names).fillna(0.0)
            cost[t][i] += float((w - held[t]).abs().sum()) * COST_BP / 1e4
            h = w.copy()
            for d in range(i, end):
                r = ret.iloc[d].reindex(names)
                bad = (h != 0) & r.isna()
                missing += int(bad.sum())
                r = r.fillna(0.0)
                gross[t][d] = float((h * r).sum())
                h = h * (1.0 + r)
            held[t] = h
    ev = int(reb[0])
    sl = slice(ev, last)
    out = {}
    for mult, row in ((1.0, "cost"), (2.0, "cost_x2"), (0.0, "cost0")):
        series = {t: gross[t][sl] - mult * cost[t][sl] for t in TESTS[:3]}
        series["COMBO_IW0"] = np.mean([series[t] for t in TESTS[:3]], axis=0)
        out[row] = series
    return {"rows": out, "eval_days": [int(days[ev]), int(days[last - 1])], "n_universe": nuni,
            "n_rebalances": len(reb), "missing_return_cells": missing}


def nw_t(x: np.ndarray, lag: int = NW_LAG) -> float:
    x = np.asarray(x, float)
    n = len(x)
    e = x - x.mean()
    s = float(e @ e) / n
    for k in range(1, lag + 1):
        s += 2.0 * (1.0 - k / (lag + 1)) * float(e[k:] @ e[:-k]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def p_one_sided(t: float) -> float:
    return 0.5 * math.erfc(t / math.sqrt(2.0)) if math.isfinite(t) else 1.0


def holm(p: dict, alpha: float = ALPHA) -> dict:
    order = sorted(p, key=lambda k: p[k])
    m, rej, stop = len(p), {}, False
    for r, k in enumerate(order):
        if not stop and p[k] <= alpha / (m - r):
            rej[k] = True
        else:
            stop = True
            rej[k] = False
    return rej


def sr(x: np.ndarray) -> float:
    sd = float(np.std(x, ddof=1))
    return float(np.mean(x) / sd * math.sqrt(365)) if sd > 0 else float("nan")


def decide(sim: dict) -> dict:
    main = sim["rows"]["cost"]
    p = {t: p_one_sided(nw_t(main[t])) for t in TESTS}
    rej = holm(p)
    res = {}
    for t in TESTS:
        x = main[t]
        h = len(x) // 2
        halves = [sr(x[:h]), sr(x[h:])]
        x2 = sim["rows"]["cost_x2"][t]
        ok = bool(rej[t] and min(halves) > 0 and np.mean(x2) > 0)
        res[t] = {"sr_ann": sr(x), "sr_cost0": sr(sim["rows"]["cost0"][t]), "sr_cost_x2": sr(x2),
                  "ann_return_pct": float(np.mean(x) * 365 * 100), "nw_t": nw_t(x), "p_one_sided": p[t],
                  "holm_reject": bool(rej[t]), "halves_sr": halves, "verdict": "PASS" if ok else "FAIL"}
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "confirm_iw0_result.json")
    a = ap.parse_args(argv)
    piv = load_daily(a.zip)
    sim = simulate(piv)
    res = decide(sim)
    head = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
            "r11_sha256": bu.sha(HERE / "newsrc_r11_dev.py"), "r12_sha256": bu.sha(HERE / "newsrc_r12_dev.py"),
            "zip_sha256": bu.sha(a.zip)}
    out = {**head, "eval_days": [str(pd.Timestamp(t, unit="ms").date()) for t in sim["eval_days"]],
           "n_rebalances": sim["n_rebalances"], "n_universe": sim["n_universe"],
           "missing_return_cells": sim["missing_return_cells"], "tests": res}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    for t, v in res.items():
        print(t, v["verdict"], f"SR {v['sr_ann']:.2f} x2 {v['sr_cost_x2']:.2f} c0 {v['sr_cost0']:.2f} "
              f"t {v['nw_t']:.2f} p {v['p_one_sided']:.4f} holm {v['holm_reject']} halves "
              f"{v['halves_sr'][0]:.2f}/{v['halves_sr'][1]:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
