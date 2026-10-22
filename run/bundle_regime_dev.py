#!/usr/bin/env python3
"""Top-5 bundle by market regime and against buy-and-hold, DEV only (ADR 0026).

Question (동동, 2026-10-22): was the top-5 bundle of ADR 0024 tested in bear markets, and is it better than just holding?

Re-runs the ADR 0024 search (``liquid_search_dev``: same data, rules, costs, S1 selection) to get the same top-5 distinct leaders,
then splits their daily net series by price-based regimes fixed in
run/audit/evidence_bundle_regime_20261022/prereg_bundle_regime_v1.json before any result, and compares with buy-and-hold of BTC, ETH
and the equal-weight basket (entry cost once, funding every day).  Reads only the S1 / S2 DEV stretches of ``liquid_search_dev``
(``assert_allowed`` refuses everything else).  Research only; writes no ledger or status.

    python3 run/bundle_regime_dev.py --data-dir <dir with klines/ funding/> --out <dir>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import liquid_search_dev as L  # noqa: E402

MIN_DAYS = 60
TRAIL_DAYS, TRAIL_BAND = 90, 0.20
DD_LEVEL = 0.75


def market_index(dl: "L.Daily") -> np.ndarray:
    """Equal-weight daily-rebalanced index at each day's open; index[0] = 1 at read_start (length D+1)."""
    r = np.nan_to_num(dl.ret).copy()
    ok = ~np.isnan(dl.ret)
    mean_r = np.where(ok.any(0), r.sum(0) / np.maximum(ok.sum(0), 1), 0.0)
    return np.concatenate([[1.0], np.cumprod(1.0 + mean_r)])


def regimes(idx: np.ndarray, n_days: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-day labels known at the day's open.  R1: 'BULL'/'BEAR'/'SIDE'/'NA'; R2: 'DD'/'OK'."""
    r1, r2 = np.full(n_days, "NA", dtype=object), np.full(n_days, "OK", dtype=object)
    peak = np.maximum.accumulate(idx)
    for d in range(n_days):
        if d >= TRAIL_DAYS:
            t = idx[d] / idx[d - TRAIL_DAYS] - 1.0
            r1[d] = "BULL" if t >= TRAIL_BAND else "BEAR" if t <= -TRAIL_BAND else "SIDE"
        if idx[d] <= DD_LEVEL * peak[d]:
            r2[d] = "DD"
    return r1, r2


def stats(x: np.ndarray) -> dict:
    if len(x) < MIN_DAYS:
        return {"days": int(len(x)), "note": f"표본 부족(<{MIN_DAYS}일)"}
    return {"days": int(len(x)), "mean_bp_day": float(x.mean()), "sr_ann": L.sr_ann(x),
            "cum_bp": float(x.sum()), "mdd_bp": L.mdd(x)}


def monthly(x: np.ndarray, days: np.ndarray, mask: np.ndarray) -> dict:
    import datetime as dt
    out: dict = {}
    for v, d, m in zip(x, days, mask):
        if m:
            k = dt.datetime.fromtimestamp(int(d) * 86400, dt.timezone.utc).strftime("%Y-%m")
            out[k] = out.get(k, 0.0) + float(v)
    return {k: round(v, 1) for k, v in out.items()}


def nw_t(y: np.ndarray, x: np.ndarray, lag: int = 5) -> dict:
    n = len(y)
    if n < MIN_DAYS:
        return {"note": f"표본 부족(<{MIN_DAYS}일)", "days": n}
    X = np.column_stack([np.ones(n), x])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ b
    xe = X * e[:, None]
    S = xe.T @ xe
    for k in range(1, lag + 1):
        G = xe[k:].T @ xe[:-k]
        S += (1 - k / (lag + 1)) * (G + G.T)
    A = np.linalg.inv(X.T @ X)
    cov = A @ S @ A
    return {"days": n, "alpha_bp_day": float(b[0]), "beta": float(b[1]),
            "alpha_t_nw5": float(b[0] / math.sqrt(cov[0, 0])), "beta_t_nw5": float(b[1] / math.sqrt(cov[1, 1]))}


def hold_series(dl: "L.Daily", cost: dict, sym: str | None) -> np.ndarray:
    S = len(dl.syms)
    w = np.ones((S, len(dl.days)))
    if sym is not None:
        w = np.zeros((S, len(dl.days)))
        w[dl.syms.index(sym)] = S  # run() averages over symbols: S on one row = 100% of capital in that symbol
    return dl.run(w, cost)


def top5(ev1: "L.Evaluator", data1: dict) -> tuple[list, dict]:
    """The ADR 0024 selection (stage 1, stage 2 filters, distinct leaders), unchanged."""
    specs = L.stage1_specs()
    ids, spec_by, mat, nev = [], {}, [], []
    for sid, s in specs:
        if sid in spec_by:
            continue
        r = ev1.spec(s)
        ids.append(sid); spec_by[sid] = s; mat.append(r["daily"]); nev.append(r["n"])
    M = np.array(mat).T
    srs = np.array([L.sr_ann(M[:, j]) for j in range(M.shape[1])])
    order = [int(j) for j in np.argsort(-np.nan_to_num(srs, nan=-9)) if nev[j] >= 60]
    base = [r for r, _ in L.clusters(M, order, rho=0.7, limit=40)]
    for j in base:
        s = spec_by[ids[j]]
        for f in L.FILTERS:
            try:
                sid, s2 = L.make_spec(s["direction"], s["entry"]["conditions"], s["exit"]["horizon"], [f])
            except Exception:
                continue
            if sid in spec_by:
                continue
            r = ev1.spec(s2)
            ids.append(sid); spec_by[sid] = s2; mat.append(r["daily"]); nev.append(r["n"])
    M = np.array(mat).T
    srs = np.array([L.sr_ann(M[:, j]) for j in range(M.shape[1])])
    nev = np.array(nev)
    order = [int(j) for j in np.argsort(-np.nan_to_num(srs, nan=-9)) if nev[j] >= 60]
    reps = L.clusters(M, order, rho=0.7, limit=25)
    return [ids[j] for j, _ in reps[:5]], spec_by


def exposure(ev: "L.Evaluator", spec: dict) -> dict:
    r = ev.spec(spec, keep_events=True)
    n_sym, n_days = len(ev.data), ev.d1 - ev.d0 + 1
    dur = [(e[4] - e[3]) / L.DAY_MS for e in r["events"]]
    sgn = [e[2] for e in r["events"]]
    return {"in_position_share": float(sum(dur) / (n_days * n_sym)),
            "net_long_share": float(sum(d * s for d, s in zip(dur, sgn)) / (n_days * n_sym)),
            "horizon_bars": spec["exit"]["horizon"], "direction": spec["direction"]}


def analyse(name: str, ev: "L.Evaluator", data: dict, specs: list, cost: dict) -> dict:
    seg = L.SEGMENTS[name]
    dl = L.Daily(data, seg)
    bund_each = [ev.spec(s)["daily"] for s in specs]
    bund = np.mean(bund_each, axis=0)
    days = dl.days[dl.first:]
    idx = market_index(dl)
    r1, r2 = regimes(idx, len(dl.days))
    r1, r2 = r1[dl.first:], r2[dl.first:]
    holds = {"EW_basket": hold_series(dl, cost, None)}
    for s in ("BTCUSDT", "ETHUSDT"):
        if s in dl.syms:
            holds[s] = hold_series(dl, cost, s)
    assert len(bund) == len(days) == len(holds["EW_basket"]), (len(bund), len(days))
    masks = {"ALL": np.ones(len(days), bool)}
    for lab in ("BULL", "SIDE", "BEAR", "NA"):
        masks[f"R1_{lab}"] = r1 == lab
    masks["R2_DRAWDOWN"] = r2 == "DD"
    masks["R2_NOT_DRAWDOWN"] = r2 == "OK"
    out: dict = {"days": int(len(days)), "first_day": int(days[0]), "last_day": int(days[-1]),
                 "index_total_return": float(idx[-1] / idx[dl.first] - 1.0),
                 "exposure_per_rule": [exposure(ev, s) for s in specs],
                 "regime_days": {k: int(m.sum()) for k, m in masks.items()}}
    series = {"bundle_top5": bund, **{f"hold_{k}": v for k, v in holds.items()}}
    out["by_regime"] = {rg: {nm: stats(x[m]) for nm, x in series.items()} for rg, m in masks.items()}
    bk = holds["EW_basket"]
    out["beta_vs_EW_basket"] = {rg: nw_t(bund[masks[rg]], bk[masks[rg]]) for rg in
                                ("ALL", "R1_BEAR", "R1_BULL", "R1_SIDE", "R2_DRAWDOWN", "R2_NOT_DRAWDOWN")}
    out["monthly_bp_R2_drawdown_days"] = {nm: monthly(x, days, masks["R2_DRAWDOWN"]) for nm, x in series.items()}
    out["worst_month_bp_all"] = {nm: min(monthly(x, days, masks["ALL"]).values()) for nm, x in series.items()}
    vm = {}
    for hn, h in series.items():
        if hn == "bundle_top5":
            continue
        k = float(h.std(ddof=1) / bund.std(ddof=1))
        vm[hn] = {"leverage_on_bundle_to_match_hold_vol": k,
                  "bundle_scaled": stats(bund * k), "hold": stats(h),
                  "hold_scaled_to_bundle_vol": stats(h / k),
                  "bundle": stats(bund),
                  "bundle_scaled_in_R2_drawdown": stats((bund * k)[masks["R2_DRAWDOWN"]]),
                  "hold_in_R2_drawdown": stats(h[masks["R2_DRAWDOWN"]])}
    out["vol_matched"] = vm
    out["corr_bundle_hold"] = {hn: float(np.corrcoef(bund, h)[0, 1]) for hn, h in series.items() if hn != "bundle_top5"}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    cost = L.load_cost()
    data1, data2 = L.load_segment("S1", a.data_dir), L.load_segment("S2", a.data_dir)
    ev1, ev2 = L.Evaluator("S1", data1, cost), L.Evaluator("S2", data2, cost)
    ids, spec_by = top5(ev1, data1)
    specs = [spec_by[i] for i in ids]
    rep = {"version": "bundle-regime-dev-v1", "leaders": ids, "cost": {k: cost[k] for k in ("profile_id", "sha256")},
           "reproduction": {"S1_sr_ann": L.sr_ann(np.mean([ev1.spec(s)["daily"] for s in specs], axis=0)),
                            "S2_sr_ann": L.sr_ann(np.mean([ev2.spec(s)["daily"] for s in specs], axis=0)),
                            "adr0024": [2.1603, 2.4560]},
           "S1": analyse("S1", ev1, data1, specs, cost), "S2": analyse("S2", ev2, data2, specs, cost),
           "runtime_seconds": time.time() - t0}
    (a.out / "bundle_regime_dev.json").write_text(json.dumps(rep, indent=1, default=float, ensure_ascii=False) + "\n")
    print(json.dumps(rep["reproduction"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
