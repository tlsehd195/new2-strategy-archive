"""How many REAL edges would the archive's gates miss?  (synthetic, diagnostic only)

Every return series in this file is SYNTHETIC: drawn from a random-number
generator with a known, planted edge.  Nothing here is market data and no
number produced here describes a real strategy.  The point is the opposite of
a search: we know the truth, run it through copies of the gates and count how
often a strategy that DOES have an edge is let through (power) and which gate
stops the others (false negatives).

Gates copied from ``discovery/config/promotion_v3.json`` and
``discovery/pipeline.py`` (evolve mode, two symbols BTC+ETH):

  DEV screen        n >= 30 per symbol, pooled mean > 0, pooled t >= 1,
                    >= 2 of 4 time folds positive, cost share <= 0.8
  batch entry       top-20 of the DEV passers by capped DEV t
                    (``--entry-t``: the 20th DEV t among real competitors;
                    0 = everyone gets in)
  validation        per symbol, daily series of the 6-month window, one-sided
                    test at the spent alpha of batch k on that window,
                    BOTH symbols must reject, n >= 30 per symbol
  robustness        cost x1.5 mean > 0, 4 random regimes >= 50 % positive,
                    MDD <= 2500 bp, top-5 % events <= 60 % and top day <= 35 %
                    of profit, walk-forward 4 DEV folds + 2 validation halves
                    >= 60 % positive.  Parameter neighbours and PBO are search
                    properties a synthetic single strategy does not have: they
                    are assumed PASSED (optimistic for the pipeline).
  locked TEST       needs a VERIFIED cost record: none exists, so 0 % today.
                    Its power IF cost were verified is reported separately.

StepM is replaced by the two analytic bounds used in the IW1 power pre-check
(ADR 0016/0017): K=1 (the strategy alone, best case) and Bonferroni K=20
(worst case); the engine's StepM lies between them.  Events are independent
draws (Student t, 4 d.o.f., scaled to the stated sd) with a CONSTANT edge —
the friendliest world for a test, so every power here is an upper bound for
edges that come and go.

Nothing reads market data, the ledgers, IW1 or any locked window.  No
criterion is changed: this file only measures.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import NormalDist

import numpy as np

N01 = NormalDist()
DAY = 1.0 / 365.0

#: promo-v3 numbers (copied, not imported, so a config change cannot silently
#: change an audit that was already published)
DEV_MIN_N, DEV_MIN_T, DEV_FOLDS, DEV_MIN_FOLD_SHARE, DEV_MAX_COST_SHARE = 30, 1.0, 4, 0.5, 0.8
VAL_MIN_N, VAL_ALPHA_SEARCH, N_SUPPORTED = 30, 0.05, 2
ROB_COST_MULT, ROB_REGIME_MIN_N, ROB_REGIME_SHARE = 1.5, 10, 0.5
ROB_MAX_MDD, ROB_TOP5, ROB_TOPDAY, ROB_WF = 2500.0, 0.6, 0.35, 0.6
BOOT_B = 1000

#: window lengths in years (engine split_windows, embargo 1 day, 2021-04 data start)
DEV_YEARS = {"BTCUSDT": 2.47, "ETHUSDT": 4.55}
VAL_YEARS = 172 / 365
LOCK_YEARS = {"BTCUSDT": 86 / 365, "ETHUSDT": 126 / 365}


def window_alpha(k: int) -> float:
    """promo-v3 alpha of batch k on one validation window."""
    return VAL_ALPHA_SEARCH / N_SUPPORTED * 6 / math.pi ** 2 / k ** 2


def z_crit(alpha: float, K: int, boot_floor: bool) -> float:
    """one-sided critical z.  ``boot_floor``: the engine's StepM takes the
    ceil((1-alpha)B)-th of B=1000 bootstrap maxima, so it can never be
    stricter than the largest of 1000 draws (alpha_eff >= ~1/B)."""
    a = alpha / K
    if boot_floor:
        a = max(a, 1.0 / (BOOT_B + 1) / K)
    return N01.inv_cdf(1 - a)


# ---------------------------------------------------------------------------
# one synthetic strategy
# ---------------------------------------------------------------------------

def draw_events(rng, years, rate, mu, sd, cost):
    n = rng.poisson(rate * years)
    ts = np.sort(rng.uniform(0.0, years, n))
    z = rng.standard_t(4, n) / math.sqrt(2.0)          # t4 has variance 2
    net = mu + sd * z
    return ts, net + cost, np.full(n, cost), net          # ts, gross, cost, net


def tstat(x):
    n = len(x)
    if n < 3:
        return float("nan")
    s = x.std(ddof=1)
    return float(x.mean() / s * math.sqrt(n)) if s > 0 else float("nan")


def daily(ts, net, years):
    nd = max(1, int(round(years * 365)))
    d = np.zeros(nd)
    np.add.at(d, np.minimum((ts * 365).astype(int), nd - 1), net)
    return d


def mdd(x):
    c = np.cumsum(x)
    return float(np.max(np.maximum.accumulate(np.maximum(c, 0)) - c)) if len(c) else 0.0


def one_strategy(rng, p, val_z):
    """returns the first gate that stops it (or 'PASS'), and per-gate booleans
    for the leave-one-out table."""
    syms = list(DEV_YEARS)
    dev = {s: draw_events(rng, DEV_YEARS[s], p["rate"], p["mu"], p["sd"], p["cost"]) for s in syms}
    val = {s: draw_events(rng, VAL_YEARS, p["rate"], p["mu"], p["sd"], p["cost"]) for s in syms}
    g = {}
    # DEV screen (pooled events, folds on a common time axis)
    nets = np.concatenate([dev[s][3] for s in syms])
    gross = np.concatenate([dev[s][1] for s in syms])
    costs = np.concatenate([dev[s][2] for s in syms])
    tsall = np.concatenate([dev[s][0] for s in syms])
    span = max(DEV_YEARS.values())
    folds = []
    for i in range(DEV_FOLDS):
        m = (tsall >= span * i / DEV_FOLDS) & (tsall < span * (i + 1) / DEV_FOLDS)
        folds.append(nets[m].mean() if m.any() else None)
    t = tstat(nets)
    g["dev_n"] = all(len(dev[s][3]) >= DEV_MIN_N for s in syms)
    g["dev_mean"] = len(nets) > 0 and nets.mean() > 0
    g["dev_t"] = math.isfinite(t) and t >= DEV_MIN_T
    g["dev_folds"] = sum(1 for f in folds if f is not None and f > 0) / DEV_FOLDS >= DEV_MIN_FOLD_SHARE
    g["dev_cost_share"] = (np.abs(gross).sum() > 0) and costs.sum() / np.abs(gross).sum() <= DEV_MAX_COST_SHARE
    g["batch_entry"] = math.isfinite(t) and min(t, 6.0) >= p["entry_t"]
    # validation: per symbol daily test, both must reject, n >= 30 each
    rej = []
    for s in syms:
        d = daily(val[s][0], val[s][3], VAL_YEARS)
        sd_ = d.std(ddof=1)
        rej.append(sd_ > 0 and d.mean() / sd_ * math.sqrt(len(d)) > val_z)
    g["val_n"] = all(len(val[s][3]) >= VAL_MIN_N for s in syms)
    g["val_both_reject"] = all(rej)
    g["val_one_reject"] = any(rej)
    # robustness (validation events of both symbols)
    vt = np.concatenate([val[s][0] for s in syms])
    order = np.argsort(vt)
    vnet = np.concatenate([val[s][3] for s in syms])[order]
    vgross = np.concatenate([val[s][1] for s in syms])[order]
    vcost = np.concatenate([val[s][2] for s in syms])[order]
    vt = vt[order]
    g["rob_cost_x1.5"] = len(vnet) > 0 and (vgross - ROB_COST_MULT * vcost).mean() > 0
    reg = rng.integers(0, 4, len(vnet))
    usable = [vnet[reg == r].mean() for r in range(4) if (reg == r).sum() >= ROB_REGIME_MIN_N]
    g["rob_regimes"] = bool(usable) and sum(1 for x in usable if x > 0) / len(usable) >= ROB_REGIME_SHARE
    g["rob_mdd"] = mdd(vnet) <= ROB_MAX_MDD
    tot = vnet.sum()
    pos = np.sort(vnet[vnet > 0])[::-1]
    top = pos[:max(1, math.ceil(0.05 * len(vnet)))].sum() if len(pos) else 0.0
    dd = daily(vt, vnet, VAL_YEARS)
    pdays = dd[dd > 0]
    g["rob_concentration"] = (tot > 0 and top / tot <= ROB_TOP5
                              and len(pdays) > 0 and pdays.max() / pdays.sum() <= ROB_TOPDAY)
    mid = VAL_YEARS / 2
    halves = [vnet[vt < mid], vnet[vt >= mid]]
    wf = folds + [h.mean() if len(h) else None for h in halves]
    g["rob_walk_forward"] = sum(1 for x in wf if x is not None and x > 0) / len(wf) >= ROB_WF
    return g


DEV_GATES = ["dev_n", "dev_mean", "dev_t", "dev_folds", "dev_cost_share"]
VAL_GATES = ["batch_entry", "val_n", "val_both_reject"]
ROB_GATES = ["rob_cost_x1.5", "rob_regimes", "rob_mdd", "rob_concentration", "rob_walk_forward"]
ALL_GATES = DEV_GATES + VAL_GATES + ROB_GATES


def scenario(rng, sr_ann, rate, sd, cost, k, K, entry_t, n_sims, boot_floor=True):
    mu = sr_ann * sd / math.sqrt(rate)         # per-event net mean for that annual SR
    p = {"rate": rate, "mu": mu, "sd": sd, "cost": cost, "entry_t": entry_t}
    vz = z_crit(window_alpha(k), K, boot_floor)
    rows = [one_strategy(rng, p, vz) for _ in range(n_sims)]
    out = {"sr_ann": sr_ann, "events_per_year_per_symbol": rate, "event_sd_bp": sd,
           "round_trip_cost_bp": cost, "net_mean_bp": round(mu, 2), "batch_index": k, "K": K,
           "val_alpha": window_alpha(k), "val_z": round(vz, 3), "entry_t": entry_t}
    alive = np.ones(n_sims, bool)
    att = {}
    for gname in ALL_GATES:
        alive &= np.array([r[gname] for r in rows])
        att[gname] = round(float(alive.mean()), 4)
    out["survival_after_gate"] = att
    out["pass_all"] = att[ALL_GATES[-1]]
    out["pass_dev"] = att[DEV_GATES[-1]]
    # first stopper
    first = {}
    for r in rows:
        f = next((gname for gname in ALL_GATES if not r[gname]), "PASS")
        first[f] = first.get(f, 0) + 1
    out["first_stopper_share"] = {k_: round(v / n_sims, 4) for k_, v in sorted(first.items())}
    # leave-one-out: pass rate with ONE gate switched off
    loo = {}
    for gname in ALL_GATES:
        keep = [x for x in ALL_GATES if x != gname]
        loo[gname] = round(float(np.mean([all(r[x] for x in keep) for r in rows])), 4)
    out["pass_if_gate_removed"] = loo
    out["gate_pass_rate_alone"] = {gname: round(float(np.mean([r[gname] for r in rows])), 4)
                                   for gname in ALL_GATES}
    rob_ok = [all(r[x] for x in ROB_GATES) for r in rows]
    out["robustness_all_pass_rate_alone"] = round(float(np.mean(rob_ok)), 4)
    out["val_one_symbol_reject_given_dev"] = round(float(np.mean(
        [r["val_one_reject"] for r in rows if all(r[x] for x in DEV_GATES)] or [0])), 4)
    return out


# ---------------------------------------------------------------------------
# closed-form power of designs (normal approximation, constant edge)
# ---------------------------------------------------------------------------

def power(sr_ann, years, alpha, K=1, symbols=1, rho=0.0):
    """power of a one-sided test of the pooled (equal-weight) daily mean.
    Pooling M symbols whose daily series correlate rho raises the Sharpe of
    the pooled series by sqrt(M / (1 + (M-1) rho))."""
    boost = math.sqrt(symbols / (1 + (symbols - 1) * rho))
    z = sr_ann * boost * math.sqrt(years)
    return 1 - N01.cdf(N01.inv_cdf(1 - alpha / K) - z)


def sr_for_power(target, years, alpha, K=1, symbols=1, rho=0.0):
    boost = math.sqrt(symbols / (1 + (symbols - 1) * rho))
    return (N01.inv_cdf(1 - alpha / K) + N01.inv_cdf(target)) / (boost * math.sqrt(years))


def designs():
    """annual SR needed for 80 % power under the current design and under
    designs that keep alpha and FWER as they are but collect more evidence."""
    a_fresh = window_alpha(1)
    a_now = window_alpha(8)
    rows = []

    def add(name, years, alpha, K, both, symbols=1, rho=0.0, note=""):
        best = sr_for_power(0.8 ** (0.5 if both else 1.0), years, alpha, 1, symbols, rho)
        worst = sr_for_power(0.8 ** (0.5 if both else 1.0), years, alpha, K, symbols, rho)
        p2 = [power(2.0, years, alpha, k_, symbols, rho) ** (2 if both else 1) for k_ in (1, K)]
        rows.append({"design": name, "years": round(years, 3), "alpha": alpha, "K": K,
                     "both_symbols_must_pass": both, "pooled_symbols": symbols, "rho": rho,
                     "sr_ann_for_80pct_power": [round(best, 2), round(worst, 2)],
                     "power_at_sr2": [round(p2[0], 3), round(p2[1], 3)], "note": note})

    add("NOW: BTC/ETH validation, batch 8, 20 members, both symbols", VAL_YEARS, a_now, 20, True,
        note="the 8th look at the same 6-month windows, nominal alpha")
    add("NOW as implemented (StepM B=1000 cannot go below alpha ~1/1001)", VAL_YEARS,
        max(a_now, 1.0 / (BOOT_B + 1)), 20, True,
        note="effective size of the bootstrap test at batch >= 4")
    add("fresh 6-month window, batch 1, 20 members, both symbols", VAL_YEARS, a_fresh, 20, True)
    add("fresh 6-month window, batch 1, 5 members, both symbols", VAL_YEARS, a_fresh, 5, True)
    add("fresh 12-month window, batch 1, 20 members, both symbols", 1.0, a_fresh, 20, True)
    add("fresh 24-month window, batch 1, 20 members, both symbols", 2.0, a_fresh, 20, True)
    add("IW1 tranche A (335 d, 6 symbols pooled, alpha 0.0304, 20 members)", 335 / 365, 0.0304, 20, False,
        note="ADR 0016/0017 numbers: daily series is already the 6-symbol pool")
    add("IW1 tranche A, 3 members", 335 / 365, 0.0304, 3, False)
    for m, rho in ((1, 0.0), (6, 0.6), (15, 0.6), (15, 0.3)):
        add(f"pooled test over {m} symbols (rho {rho}), 12-month fresh window, batch 1, 5 members",
            1.0, a_fresh, 5, False, symbols=m, rho=rho,
            note="sqrt(M/(1+(M-1)rho)) boost only for a signal that is not just market beta")
    return rows


def short_trade_power():
    """the 148 'dev_underpowered' shorts have 8-15 DEV trades in ~4.5 years.
    What per-trade edge would be needed to show anything, and how much
    annual Sharpe could such a strategy carry at all?"""
    out = []
    for n_dev in (8, 12, 15):
        rate = n_dev / 4.55
        for sr_trade in (0.2, 0.5, 1.0):
            sr_ann = sr_trade * math.sqrt(rate)
            # validation on a fresh 6-month window: expected events
            n_val = rate * VAL_YEARS
            out.append({"dev_trades": n_dev, "trades_per_year": round(rate, 2),
                        "per_trade_sharpe": sr_trade, "annual_sr_ceiling": round(sr_ann, 2),
                        "expected_trades_in_6m_validation": round(n_val, 2),
                        "dev_t_expected": round(sr_trade * math.sqrt(n_dev), 2)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261021)
    ap.add_argument("--entry-t", type=float, default=0.0,
                    help="20th capped DEV t among real DEV passers (0 = no competition)")
    a = ap.parse_args(argv)
    rng = np.random.default_rng(a.seed)
    grid = []
    # event sd 240 bp ~ a 16 h hold on BTC/ETH; cost 14 bp ~ public estimate
    # round trip at 10k USDT; 40 bp ~ assumed_public_v1 on BTC/ETH
    for rate in (12, 50, 150, 500):
        for sr in (0.5, 1.0, 2.0, 3.0, 4.0):
            for k in (8, 1):
                for K in (1, 20):
                    grid.append(scenario(rng, sr, rate, 240.0, 14.0, k, K, a.entry_t, a.sims))
    cost_rows = []
    for cost in (14.0, 40.0, 120.0):
        for sr in (1.0, 2.0):
            # same GROSS edge, different cost: net SR falls as cost rises
            gross_mu = sr * 240.0 / math.sqrt(50) + 14.0
            net_sr = (gross_mu - cost) / 240.0 * math.sqrt(50)
            r = scenario(rng, net_sr, 50, 240.0, cost, 1, 20, a.entry_t, a.sims)
            r["gross_mean_bp"] = round(gross_mu, 2)
            r["sr_ann_at_14bp_cost"] = sr
            cost_rows.append(r)
    res = {
        "SYNTHETIC": "every series is random numbers with a planted edge; not market data",
        "version": "power-audit-synthetic-v1",
        "seed": a.seed, "sims_per_scenario": a.sims, "entry_t": a.entry_t,
        "gates": ALL_GATES,
        "assumed_passed": ["parameter_neighbourhood", "pbo"],
        "locked_test_today": "0 % for every edge size: no VERIFIED cost record (ADR 0009)",
        "window_alpha": {f"k={k}": window_alpha(k) for k in (1, 2, 3, 4, 8)},
        "bootstrap_floor_alpha": 1.0 / BOOT_B,
        "grid": grid,
        "cost_sensitivity": cost_rows,
        "designs": designs(),
        "underpowered_shorts": short_trade_power(),
        "locked_power_if_cost_verified": {
            s: {str(sr): round(power(sr, LOCK_YEARS[s], window_alpha(1), 1), 3) for sr in (1, 2, 3, 4)}
            for s in LOCK_YEARS},
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, sort_keys=True))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
