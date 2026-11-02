#!/usr/bin/env python3
"""New-listing short event study, DEV only (ADR 0059).

Runs exactly the six configs of
``run/audit/evidence_strong_hypotheses_20261102/prereg_newlisting_short_v1.json``
(committed before any listing data was downloaded).

Never reads: 2020 (IW1), 2023-09-19..2024-06-18, 2025-10-21 onwards; BTCUSDT in S2.
Listing month is taken from S3 *file names only* (``first_month.json``); a
symbol whose first month lies before 2021-02 or inside the never-read gap is
not a DEV listing event.

Usage (data from run/broad_universe_fetch.py steps daily/funding)::

    python3 run/newlisting_short_dev.py --data-dir <dir> --step first_month
    python3 run/newlisting_short_dev.py --data-dir <dir> --step events
    python3 run/newlisting_short_dev.py --data-dir <dir> --step run
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import sys
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import broad_universe_fetch as bf  # noqa: E402

EVID = HERE / "audit" / "evidence_strong_hypotheses_20261102"
PREREG = EVID / "prereg_newlisting_short_v1.json"
CALIB = HERE / "audit" / "lowliq_cost_20261027" / "calibration_v1.json"
SEGS = bu.SEGMENTS
DAY = bu.DAY_MS


def first_months(data: Path) -> dict:
    out = {}
    syms = json.loads((data / "symbols_listed.json").read_text())
    for s in syms:
        pfx = urllib.parse.quote(f"data/futures/um/monthly/klines/{s}/1d/")
        x = urllib.parse.unquote(bf.get(bf.S3 + "?delimiter=/&prefix=" + pfx).decode())
        ms_ = sorted(set(re.findall(rf"{re.escape(s)}-1d-(\d{{4}}-\d{{2}})\.zip<", x)))
        out[s] = ms_[0] if ms_ else None
    (data / "first_month.json").write_text(json.dumps(out))
    return out


def seg_of(t: int):
    for k, (lo, hi, _) in SEGS.items():
        if bu.ms(lo) <= t <= bu.ms(hi):
            return k
    return None


def load_symbol(path: Path) -> dict:
    rows = {}
    for f in bu.read_rows(path):
        t = int(f[0])
        if seg_of(t) is None:
            continue
        rows[t] = (float(f[1]), float(f[2]), float(f[4]), float(f[7]))  # open high close qv
    return rows


def event_symbols(data: Path) -> list:
    fm = json.loads((data / "first_month.json").read_text())
    keep = []
    for s, m in fm.items():
        if m is None:
            continue
        if s == "BTCUSDT":
            continue
        # first month must be readable: 2021-02.. 2023-09, or 2024-06..2025-10 (2021-01 handled by day check)
        ok = ("2021-01" <= m <= "2023-09") or ("2024-06" <= m <= "2025-10")
        if ok:
            keep.append(s)
    return sorted(keep)


def build_events(data: Path) -> dict:
    syms = event_symbols(data)
    all_syms = sorted(p.name.split("-1d-")[0] for p in (data / "daily").glob("*.csv"))
    all_syms = sorted(set(all_syms))
    # market proxy: equal-weight open-to-open return of symbols older than 60 bars
    opens = {}
    for s in all_syms:
        rows = {}
        for p in sorted((data / "daily").glob(f"{s}-1d-*.csv")):
            rows.update(load_symbol(p))
        opens[s] = rows
    return {"syms": syms, "data": opens}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--step", required=True, choices=("first_month", "list_event_syms", "run"))
    ap.add_argument("--out", type=Path, default=EVID / "newlisting_short_dev.json")
    a = ap.parse_args(argv)
    data = a.data_dir
    if a.step == "first_month":
        fm = first_months(data)
        print(len(fm), "symbols; none:", sum(v is None for v in fm.values()))
        return 0
    if a.step == "list_event_syms":
        syms = event_symbols(data)
        (data / "event_symbols.json").write_text(json.dumps(syms))
        print(len(syms), "candidate listing symbols")
        return 0
    return run(a)


def run(a) -> int:
    data = a.data_dir
    pre = json.loads(PREREG.read_text())
    cal = bu.load_cost_calibration(CALIB)
    assumed = bu.load_cost()
    ev_syms = json.loads((data / "event_symbols.json").read_text())
    fm = json.loads((data / "first_month.json").read_text())
    all_files = sorted((data / "daily").glob("*.csv"))
    by_sym: dict = {}
    for p in all_files:
        by_sym.setdefault(p.name.split("-1d-")[0], []).append(p)
    px = {}
    for s, ps in by_sym.items():
        if s == "BTCUSDT":
            continue  # S1 only would be allowed; the proxy and events never need it
        rows = {}
        for p in sorted(ps):
            rows.update(load_symbol(p))
        px[s] = rows
    # market proxy per segment day: eq-weight open-to-open return of symbols with >= 60 earlier bars
    mkt = {}
    for seg, (lo, hi, _) in SEGS.items():
        days = np.arange(bu.ms(lo), bu.ms(hi) + 1, DAY)
        o = pd.DataFrame({s: {t: r[t][0] for t in r if bu.ms(lo) <= t <= bu.ms(hi)} for s, r in px.items()}).reindex(days)
        nb = o.notna().cumsum()
        r = o.shift(-1) / o - 1.0
        r = r.where(nb >= 60)
        # S2 has no BTCUSDT because it was dropped above for both segments
        mkt[seg] = r.mean(axis=1).fillna(0.0)
    funding_cache = {}

    def fund_sum(sym, t0, t1):
        if sym not in funding_cache:
            funding_cache[sym] = bu.load_funding(data, [sym], bu.ms("2021-01-01"), bu.ms("2025-10-20"))
        f = funding_cache[sym]
        m = (f["t"] > t0) & (f["t"] <= t1)
        return float(f.loc[m, "rate"].sum())

    events = []
    dropped = {"first_bar_2021_01_01": 0, "no_first_bar_in_dev": 0, "window_past_segment_end": 0, "missing_prices": 0}
    for s in ev_syms:
        rows = px.get(s)
        if not rows:
            dropped["no_first_bar_in_dev"] += 1
            continue
        d0 = min(rows)
        if d0 == bu.ms("2021-01-01"):
            dropped["first_bar_2021_01_01"] += 1
            continue
        seg = seg_of(d0)
        lo, hi, _ = SEGS[seg]
        for cfg in pre["configs"]:
            te, tx = d0 + cfg["entry_day"] * DAY, d0 + (cfg["entry_day"] + cfg["hold_days"]) * DAY
            if tx > bu.ms(hi):
                dropped["window_past_segment_end"] += 1
                continue
            win = [rows.get(d0 + k * DAY) for k in range(cfg["entry_day"], cfg["entry_day"] + cfg["hold_days"] + 1)]
            if any(w is None for w in win):
                dropped["missing_prices"] += 1
                continue
            o_in, o_out = win[0][0], win[-1][0]
            r_short = -(o_out / o_in - 1.0)
            fund = fund_sum(s, te, tx)
            gross = r_short + fund
            mae = max(w[1] for w in win[:-1]) / o_in - 1.0  # worst adverse for a short (highs before exit day)
            pre_q = [rows[d0 + k * DAY][3] for k in range(0, cfg["entry_day"]) if (d0 + k * DAY) in rows]
            qd = float(np.mean(pre_q)) if pre_q else win[0][3]
            b = qd / 96.0
            if not b > 0:
                b = 1.0
            cm = 2.0 * bu.side_cost(b, cal, "x", sym_q15=b)
            ca = 2.0 * (assumed["fixed"] + assumed["imp"] * math.sqrt(bu.ORDER_VALUE / b))
            mk = mkt[seg].reindex(np.arange(te, tx, DAY))
            mret = float(np.prod(1.0 + mk.fillna(0.0).values) - 1.0)
            events.append({"cfg": cfg["id"], "sym": s, "seg": seg, "d0": d0, "te": te, "gross": gross,
                           "price_short": r_short, "fund": fund, "excess": gross + mret, "mae": mae,
                           "c_measured": cm, "c_assumed": ca, "mkt": mret})
    df = pd.DataFrame(events)
    df["month"] = pd.to_datetime(df["te"], unit="ms").dt.strftime("%Y-%m")
    out = {"version": "newlisting-short-dev-v1", "prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(),
           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "calibration_sha256": hashlib.sha256(CALIB.read_bytes()).hexdigest(),
           "dropped": dropped, "n_event_symbols": len(ev_syms), "configs": {}}

    def cluster_t(x: pd.Series, g: pd.Series) -> float:
        n = len(x)
        if n < 3:
            return float("nan")
        m = x.mean()
        se = math.sqrt(sum(((x[g == k] - m).sum()) ** 2 for k in g.unique())) / n
        return float(m / se) if se > 0 else float("nan")

    for cfg in pre["configs"]:
        d = df[df["cfg"] == cfg["id"]].copy()
        res = {"n": {k: int((d["seg"] == k).sum()) for k in SEGS}}
        for seg in SEGS:
            x = d[d["seg"] == seg]
            if len(x) == 0:
                res[seg] = {"n": 0}
                continue
            cm = x["c_measured"].mean()
            net = x["gross"] - x["c_measured"]
            top3 = x.sort_values("gross", ascending=False).iloc[3:]
            res[seg] = {
                "n": int(len(x)), "gross_mean": float(x["gross"].mean()), "price_short_mean": float(x["price_short"].mean()),
                "funding_mean": float(x["fund"].mean()), "cost_measured_rt_mean": float(cm),
                "cost_assumed_rt_mean": float(x["c_assumed"].mean()),
                "gross_over_cost": float(x["gross"].mean() / cm) if cm > 0 else float("nan"),
                "net_mean": float(net.mean()), "net_x2_mean": float((x["gross"] - 2 * x["c_measured"]).mean()),
                "net_assumed_mean": float((x["gross"] - x["c_assumed"]).mean()),
                "excess_cost0_mean": float(x["excess"].mean()), "hit_rate_gross": float((x["gross"] > 0).mean()),
                "median_gross": float(x["gross"].median()),
                "drop_best3_net_mean": float((top3["gross"] - top3["c_measured"]).mean()) if len(top3) else float("nan"),
                "worst5pct_gross": float(x["gross"].quantile(0.05)),
                "adverse_gt_100pct": int((x["mae"] > 1.0).sum()), "adverse_gt_100pct_share": float((x["mae"] > 1.0).mean()),
            }
        pool = d.copy()
        pool["net"] = pool["gross"] - pool["c_measured"]
        res["pooled_net_mean"] = float(pool["net"].mean()) if len(pool) else float("nan")
        res["pooled_net_t_month_cluster"] = cluster_t(pool["net"], pool["month"]) if len(pool) else float("nan")
        s1 = pool[pool["seg"] == "S1"].sort_values("te")
        h = len(s1) // 2
        res["s1_half_net_means"] = [float(s1["net"].iloc[:h].mean()), float(s1["net"].iloc[h:].mean())] if h else [None, None]
        out["configs"][cfg["id"]] = res
        out["configs"][cfg["id"]]["verdict"] = verdict(res)
    out["n_events_total"] = int(len(df))
    EVID.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1))
    df.to_csv(a.out.with_suffix(".events.csv"), index=False)
    for k, v in out["configs"].items():
        s1, s2 = v.get("S1", {}), v.get("S2", {})
        print(k, v["verdict"], "n", v["n"], "S1 gross/cost", _f(s1.get("gross_over_cost")), "S2", _f(s2.get("gross_over_cost")),
              "net S1", _f(s1.get("net_mean")), "S2", _f(s2.get("net_mean")), "t", _f(v["pooled_net_t_month_cluster"]))
    print(dropped)
    return 0


def _f(x):
    return "na" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.4f}"


def verdict(r: dict) -> str:
    try:
        s1, s2 = r["S1"], r["S2"]
        if s1["n"] < 10 or s2["n"] < 10:
            return "INSUFFICIENT_EVENTS"
        ok = (s1["gross_over_cost"] >= 3 and s2["gross_over_cost"] >= 3
              and s1["net_mean"] > 0 and s2["net_mean"] > 0
              and r["pooled_net_t_month_cluster"] >= 2.6
              and all(h is not None and h > 0 for h in r["s1_half_net_means"])
              and s1["net_x2_mean"] > 0 and s2["net_x2_mean"] > 0
              and s1["drop_best3_net_mean"] > 0 and s2["drop_best3_net_mean"] > 0
              and s1["excess_cost0_mean"] > 0 and s2["excess_cost0_mean"] > 0
              and s1["adverse_gt_100pct_share"] <= 0.05 and s2["adverse_gt_100pct_share"] <= 0.05)
        return "DEV_PROMISING" if ok else "NOT_PROMISING"
    except KeyError:
        return "INSUFFICIENT_EVENTS"


if __name__ == "__main__":
    sys.exit(main())
