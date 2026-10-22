"""Walk-forward e-value accumulation: does it keep its error rate?  (synthetic only)

Design under test (prereg v3 DRAFT, ADR 0025):

  hypothesis   "the strategy the engine picks with data up to the start of a
               block wins in that block"
  null H0      E[X_b | everything before block b] <= 0 for every block b, where
               X_b is the realised net P&L of the block (fraction of the capital
               given to the block), after cost, stop and liquidation
  bound        X_b >= -B by mechanism: an in-block stop at -L (trading halts for
               the rest of the block) and isolated margin B (a gap through the
               stop cannot lose more than B).  B and L are fixed in advance.
  e-process    E_b = prod_{i<=b} (1 + lam_i * Z_i), Z_i = (X_i - c) / (B + c),
               lam_i in [0, lam_max] chosen from blocks < i only.  c is a
               pre-fixed gap allowance: the margin floor hands the trader the
               part of a gap below -B, so E[X | past] can be slightly > 0
               under a no-skill market; c >= that bias keeps E[Z | past] <= 0.  Reject H0 the first time
               E_b >= 1/alpha (Ville: P(ever) <= alpha under H0).

Everything here is SYNTHETIC: random-number generators with a known truth.
Nothing reads market data, ledgers, IW1, validation or locked windows.  No
criterion is changed: this file only measures.

Parts (each writes one key of the output JSON):

  type1        size of the e-process under H0 on synthetic daily P&L with
               volatility clustering (GARCH), fat tails (t4), skew + leverage
               (GJR, skewed t), volatility regimes, and AR levels (where H0
               does NOT hold exactly, reported as such).  Also the naive
               "clip the block loss after the fact" variant, which is wrong.
  pipeline     a toy engine (12 momentum/reversal rules, pick the best by
               trailing-365-day net Sharpe, trade the next block) on a
               martingale asset (H0 true by construction, selection included)
               and on an AR asset (an edge the engine can find).
  power        rejection probability by 12/24/36/60 blocks and the time to
               reject for annual Sharpe 1-4, per betting rule.
  prior        a fixed Kelly bet built from a prior Sharpe (e.g. from spent
               DEV / BTC-ETH windows) that is k times the truth.
  mc_null      Dufour (2006) Monte Carlo test of a search statistic (max net
               Sharpe of the 12 rules) with four null generators: daily sign
               flip, 30-day block sign flip, stationary block bootstrap and
               iid bootstrap (both centred).  Size at 5 % per data process.
  mmc          Dufour (2006) MAXIMIZED MC test (Prop. 4.1) with a GARCH(1,1)-t
               null family (sup over a nuisance grid), size inside and
               outside the family, and why the stationary bootstrap is
               conservative (its draws are not exchangeable with S_0).

    cd run && python3 evalue_walkforward_sim.py --out audit/evidence_evalue_v3_20261022/sim_results.json
    cd run && python3 evalue_walkforward_sim.py --quick   # smoke run, minutes -> seconds
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

DAYS = 30                      # block length in days
S0 = 0.01                      # strategy daily vol, fraction of block capital
BLOCK_SD = S0 * math.sqrt(DAYS)
ALPHA = 0.0304                 # same alpha as IW1 v1 / prereg v2 (not looser)
LOG_THR = math.log(1.0 / ALPHA)
ANN = 365.0

#: main design (in units of the strategy's block sd); chosen from part "design"
#: (stop at 1 sd, margin floor at 2 sd, bet at most 0.75 of the bound) and
#: the gap allowance c: Z = (X - c) / (B + c) so that E[Z | past] <= 0 even
#: though the margin floor hands the trader the part of a gap below -B
L_MULT, B_MULT, LAM_MAX, GAP_C = 1.0, 2.0, 0.75, 0.05
PRIOR_K0 = 3.0                 # pseudo-blocks behind the plug-in estimates

ASSET_SD = 0.035               # toy pipeline: asset daily vol (crypto-like)
LOOKBACKS = (1, 3, 7, 14, 30, 60)
TRAIN = 365


# ---------------------------------------------------------------- innovations
def std_t(rng, shape, nu=4.0):
    return rng.standard_t(nu, size=shape) / math.sqrt(nu / (nu - 2.0))


def abs_mean_std_t(nu=4.0):
    """E|z| for a unit-variance Student t."""
    m = 2.0 * math.sqrt(nu) * math.gamma((nu + 1) / 2) / (
        math.sqrt(math.pi) * (nu - 1) * math.gamma(nu / 2))
    return m / math.sqrt(nu / (nu - 2.0))


def skew_t(rng, shape, nu=4.0, s=0.25):
    """Left-skewed unit-variance t (losses stretched by 1+s, gains by 1-s),
    centred exactly with the analytic mean."""
    z = std_t(rng, shape, nu)
    z = np.where(z < 0, z * (1 + s), z * (1 - s))
    m = -s * abs_mean_std_t(nu)
    return (z - m) / math.sqrt(1 + s * s - m * m)


def innovations(rng, kind, n, T, phi=0.0):
    """(n, T) series with unconditional variance ~1.  Conditional mean is 0
    for every kind except 'ar_garch' (levels autocorrelated)."""
    if kind == "iid_normal":
        return rng.standard_normal((n, T))
    if kind == "regime_t4":
        v = np.array([0.6, math.sqrt(2 - 0.36)])
        z = std_t(rng, (n, T))
        st = rng.integers(0, 2, n)
        flips = rng.random((n, T)) < 0.02
        out = np.empty((n, T))
        for t in range(T):
            st = np.where(flips[:, t], 1 - st, st)
            out[:, t] = v[st] * z[:, t]
        return out
    if kind in ("garch_t4", "ar_garch"):
        a, b = 0.10, 0.88
        z = std_t(rng, (n, T))
        h = np.ones(n)
        out = np.empty((n, T))
        for t in range(T):
            out[:, t] = np.sqrt(h) * z[:, t]
            h = (1 - a - b) + a * out[:, t] ** 2 + b * h
        if kind == "ar_garch":
            c = math.sqrt(1 - phi * phi)
            for t in range(1, T):
                out[:, t] = phi * out[:, t - 1] + c * out[:, t]
        return out
    if kind in ("gjr_skewt", "gjr_skewt3"):
        a, g, b = 0.03, 0.12, 0.86
        z = skew_t(rng, (n, T)) if kind == "gjr_skewt" else skew_t(rng, (n, T), nu=3.0, s=0.4)
        h = np.ones(n)
        out = np.empty((n, T))
        for t in range(T):
            out[:, t] = np.sqrt(h) * z[:, t]
            e = out[:, t]
            h = (1 - a - g / 2 - b) + (a + g * (e < 0)) * e * e + b * h
        return out
    raise ValueError(kind)


# ---------------------------------------------------------------- blocks
def block_pnl(r, L, B):
    """Daily P&L r (n, nb*DAYS) -> realised block P&L with an in-block stop at
    -L (None = no stop) and isolated-margin floor -B.

    Returns (X, overshoot_mask, raw_end) where raw_end is the block sum with no
    stop and no floor."""
    n = r.shape[0]
    x = r.reshape(n, -1, DAYS)
    c = np.cumsum(x, axis=2)
    end = c[..., -1]
    if L is None:
        X = end.copy()
    else:
        hit = c <= -L
        any_hit = hit.any(axis=2)
        first = hit.argmax(axis=2)
        stopped = np.take_along_axis(c, first[..., None], axis=2)[..., 0]
        X = np.where(any_hit, stopped, end)
    over = X < -B
    return np.maximum(X, -B), over, end


# ---------------------------------------------------------------- betting
def log_e_fixed(Z, lam):
    return np.cumsum(np.log1p(lam * Z), axis=1)


def log_e_mixture(Z, lam_max=LAM_MAX, grid=20):
    lams = np.linspace(lam_max / grid, lam_max, grid)
    L = np.stack([log_e_fixed(Z, l) for l in lams])          # (g, n, nb)
    m = L.max(axis=0)
    return m + np.log(np.exp(L - m).mean(axis=0))


def log_e_plugin(Z, sz0, lam_max=LAM_MAX, k0=PRIOR_K0):
    """Predictable plug-in: lam_i = mu/(v+mu^2) from blocks before i, mean
    shrunk to 0 and variance to the prior sz0^2 with k0 pseudo-blocks.  No
    prior on the edge.  The formula is the GROW bet of Waudby-Smith & Ramdas
    (2010.09686 v1 Sec. 4.4) / aSOS (v3 Sec. 5.3) written in Z units
    (Z = (X - m)/m maps lam_X <= c/m to lam_Z <= c); the paper's appendix
    aGRAPA could not be read.  Differences from the paper: k0 = 3 pseudo-blocks
    instead of 1 and an uncentred second moment; see ``log_e_wsr``."""
    n, nb = Z.shape
    s1 = np.zeros(n)
    s2 = np.zeros(n)
    out = np.empty((n, nb))
    acc = np.zeros(n)
    for b in range(nb):
        mu = s1 / (b + k0)
        v = (k0 * sz0 * sz0 + s2) / (b + k0)
        lam = np.clip(mu / (v + mu * mu), 0.0, lam_max)
        acc = acc + np.log1p(lam * Z[:, b])
        out[:, b] = acc
        s1 += Z[:, b]
        s2 += Z[:, b] ** 2
    return out


def _wsr_moments(Z, sz0):
    """WS-R regularised running moments, structure of 2010.09686 v7 eq. (15):
    mu_t = (mu_0 + sum_{i<=t} Z_i)/(t+1), s2_t = (s_0^2 + sum_{i<=t} (Z_i -
    mu_i)^2)/(t+1).  The paper's (mu_0, s_0^2) = (1/2, 1/4) belong to [0,1]
    data; Z has no upper bound, so (0, sz0^2) is our mapping.  Returns the
    PREDICTABLE values used for block b (moments up to b-1)."""
    n, nb = Z.shape
    mu_pred = np.empty((n, nb))
    s2_pred = np.empty((n, nb))
    s1 = np.zeros(n)
    ss = np.zeros(n)
    mu, s2 = np.zeros(n), np.full(n, sz0 * sz0)        # t = 0
    for b in range(nb):
        mu_pred[:, b], s2_pred[:, b] = mu, s2
        s1 += Z[:, b]
        mu = s1 / (b + 2)
        ss += (Z[:, b] - mu) ** 2
        s2 = (sz0 * sz0 + ss) / (b + 2)
    return mu_pred, s2_pred


def log_e_wsr(Z, sz0, lam_max=LAM_MAX):
    """GROW / aSOS bet with WS-R's own regularisation (one pseudo-observation,
    centred residuals): lam_t = (mu_{t-1})/(s2_{t-1} + mu_{t-1}^2), cut to
    [0, c], c = lam_max (paper default 1/2 or 3/4).  Valid for any predictable
    lam in [0, 1] because Z >= -1 (WS-R v7 Prop. 3, supermartingale form)."""
    mu, s2 = _wsr_moments(Z, sz0)
    lam = np.clip(mu / (s2 + mu * mu), 0.0, lam_max)
    return np.cumsum(np.log1p(lam * Z), axis=1)


def log_e_prpl_eb(Z, sz0, alpha=ALPHA, lam_max=LAM_MAX):
    """PrPl-EB of WS-R v7 eq. (15) in Z units:
    lam_t = sqrt(2 log(2/alpha) / (s2_{t-1} t log(1+t))) ^ c.  Built for
    confidence sequences (no estimate of the edge in the bet)."""
    _, s2 = _wsr_moments(Z, sz0)
    t = np.arange(1, Z.shape[1] + 1)[None, :]
    lam = np.minimum(np.sqrt(2 * math.log(2 / alpha) / (s2 * t * np.log1p(t))), lam_max)
    return np.cumsum(np.log1p(lam * Z), axis=1)


def log_e_prior(Z, sr_prior_ann, sz0, lam_max=LAM_MAX, k0=PRIOR_K0):
    """Fixed Kelly-style bet from a prior annual Sharpe (e.g. DEV estimate);
    only the block vol is updated predictably."""
    n, nb = Z.shape
    d = sr_prior_ann * math.sqrt(DAYS / ANN)
    s2 = np.zeros(n)
    out = np.empty((n, nb))
    acc = np.zeros(n)
    for b in range(nb):
        sz = np.sqrt((k0 * sz0 * sz0 + s2) / (b + k0))
        m = d * sz
        lam = np.clip(m / (sz * sz + m * m), 0.0, lam_max)
        acc = acc + np.log1p(lam * Z[:, b])
        out[:, b] = acc
        s2 += Z[:, b] ** 2
    return out


def first_cross(logE, log_thr=LOG_THR):
    hit = logE >= log_thr
    any_hit = hit.any(axis=1)
    tau = np.where(any_hit, hit.argmax(axis=1) + 1, np.inf)
    return tau


def summarise(tau, horizons):
    d = {f"reject_by_{h}": float(np.mean(tau <= h)) for h in horizons}
    finite = tau[np.isfinite(tau)]
    d["median_blocks_to_reject"] = (float(np.median(tau))
                                    if np.mean(np.isfinite(tau)) > 0.5 else None)
    d["mean_blocks_given_reject"] = float(finite.mean()) if finite.size else None
    return d


def run_bets(X, B, horizons, prior_srs=(), fixed_max=False, c=None):
    c = GAP_C * BLOCK_SD if c is None else c
    Z = (X - c) / (B + c)
    sz0 = BLOCK_SD / (B + c)
    res = {}
    if fixed_max:   # most aggressive constant bet, a stress test of validity
        res["fixed_lam_max"] = summarise(first_cross(log_e_fixed(Z, LAM_MAX)), horizons)
    res |= {
        "plugin": summarise(first_cross(log_e_plugin(Z, sz0)), horizons),
        "mixture": summarise(first_cross(log_e_mixture(Z)), horizons),
        "wsr_grow": summarise(first_cross(log_e_wsr(Z, sz0)), horizons),
        "wsr_prpl_eb": summarise(first_cross(log_e_prpl_eb(Z, sz0)), horizons),
        # Arnold et al. (2404.18678) Sec. 3.4 default lam = (2c)^-1 for
        # |d| <= c/2; with d = X - c_gap >= -(B + c_gap) that is lam_Z = 1/4
        "arnold_fixed_quarter": summarise(first_cross(log_e_fixed(Z, 0.25)), horizons),
    }
    for p in prior_srs:
        res[f"prior_sr{p:g}"] = summarise(first_cross(log_e_prior(Z, p, sz0)), horizons)
    return res


def se(p, n):
    return math.sqrt(max(p * (1 - p), 1e-12) / n)


# ---------------------------------------------------------------- part: type I
def direct_blocks(rng, kind, n, nb, sr_ann, L, B, phi=0.0, batch=4000):
    mu = sr_ann * S0 / math.sqrt(ANN)
    Xs, overs, ends = [], [], []
    for i in range(0, n, batch):
        m = min(batch, n - i)
        r = mu + S0 * innovations(rng, kind, m, nb * DAYS, phi)
        X, over, end = block_pnl(r, L, B)
        Xs.append(X), overs.append(over), ends.append(end)
    return np.concatenate(Xs), np.concatenate(overs), np.concatenate(ends)


def part_type1(rng, n, nb):
    L, B = L_MULT * BLOCK_SD, B_MULT * BLOCK_SD
    horizons = (12, 24, 36, 60, nb)
    cases = [
        ("iid_normal", 0.0, 0.0), ("garch_t4", 0.0, 0.0), ("gjr_skewt", 0.0, 0.0),
        ("regime_t4", 0.0, 0.0), ("gjr_skewt3", 0.0, 0.0), ("garch_t4", -0.5, 0.0),
        ("ar_garch", 0.0, 0.05), ("ar_garch", 0.0, -0.05),
    ]
    out = {"alpha": ALPHA, "n_paths": n, "blocks": nb, "L": L, "B": B,
           "lam_max": LAM_MAX, "gap_allowance_c": GAP_C * BLOCK_SD,
           "mc_se_at_alpha": se(ALPHA, n), "cases": []}
    for kind, sr, phi in cases:
        t0 = time.time()
        X, over, end = direct_blocks(rng, kind, n, nb, sr, L, B, phi)
        row = {"dgp": kind, "sr_ann": sr, "phi": phi,
               "h0_holds": phi == 0.0,
               "block_mean_realised": float(X.mean()),
               "block_mean_realised_se": float(X.std() / math.sqrt(X.size)),
               "overshoot_rate": float(over.mean()),
               "stop_rate": float((X <= -L + 1e-15).mean()),
               "bets": run_bets(X, B, horizons, prior_srs=(2.0, 4.0), fixed_max=True)}
        # wrong variant: no stop, clip the loss at -L after the fact
        Xc = np.maximum(end, -L)
        row["naive_clip_after_the_fact"] = {
            "block_mean": float(Xc.mean()),
            "bets": run_bets(Xc, L, horizons, fixed_max=True, c=0.0)}
        row["secs"] = round(time.time() - t0, 1)
        out["cases"].append(row)
        print("type1", kind, sr, phi, row["bets"]["plugin"][f"reject_by_{nb}"],
              row["naive_clip_after_the_fact"]["bets"]["plugin"][f"reject_by_{nb}"], flush=True)
    return out


# ---------------------------------------------------------------- part: design grid
def part_design(rng, n, nb):
    """Size (GJR skewed t, the DGP with the most gap risk) and power (SR 2, 3,
    GARCH t4) of the plug-in bet for stop/bound/lam_max choices, WITHOUT the
    gap allowance (c = 0) so the bias of the margin floor shows."""
    rows = []
    for Lm, Bm in ((0.5, 1.0), (1.0, 1.5), (1.0, 2.0), (1.5, 2.0), (1.5, 3.0), (2.0, 4.0)):
        L, B = Lm * BLOCK_SD, Bm * BLOCK_SD
        for lam_max in (0.5, 0.75, 0.9):
            row = {"L_sd": Lm, "B_sd": Bm, "lam_max": lam_max}
            for kind, sr in (("gjr_skewt", 0.0), ("garch_t4", 2.0), ("garch_t4", 3.0)):
                X, over, _ = direct_blocks(rng, kind, n, nb, sr, L, B)
                Z = X / B
                tau = first_cross(log_e_plugin(Z, BLOCK_SD / B, lam_max=lam_max))
                key = f"sr{sr:g}"
                row[key] = {"dgp": kind, "block_mean": float(X.mean()),
                            "block_mean_se": float(X.std() / math.sqrt(X.size)),"reject_by_24": float(np.mean(tau <= 24)),
                            "reject_by_36": float(np.mean(tau <= 36)),
                            "reject_by_nb": float(np.mean(tau <= nb)),
                            "realised_sr_ann": float(X.mean() / X.std() * math.sqrt(ANN / DAYS)),
                            "overshoot_rate": float(over.mean())}
            rows.append(row)
            print("design", Lm, Bm, lam_max, row["sr0"]["reject_by_nb"],
                  row["sr2"]["reject_by_24"], row["sr3"]["reject_by_24"], flush=True)
    return {"n_paths": n, "blocks": nb, "rows": rows}


# ---------------------------------------------------------------- part: power
def part_power(rng, n, nb):
    L, B = L_MULT * BLOCK_SD, B_MULT * BLOCK_SD
    horizons = (12, 24, 36, 60)
    rows = []
    for kind in ("iid_normal", "garch_t4", "regime_t4"):
        for sr in (1.0, 2.0, 3.0, 4.0):
            X, over, _ = direct_blocks(rng, kind, n, nb, sr, L, B)
            row = {"dgp": kind, "sr_ann_daily": sr,
                   "sr_ann_realised_blocks": float(X.mean() / X.std() * math.sqrt(ANN / DAYS)),
                   "bets": run_bets(X, B, horizons, prior_srs=(sr,))}
            rows.append(row)
            print("power", kind, sr, row["bets"]["plugin"]["reject_by_24"],
                  row["bets"]["mixture"]["reject_by_24"], flush=True)
    return {"n_paths": n, "blocks": nb, "L": L, "B": B, "rows": rows}


# ---------------------------------------------------------------- part: prior
def part_prior(rng, n, nb):
    L, B = L_MULT * BLOCK_SD, B_MULT * BLOCK_SD
    horizons = (12, 24, 36, 60)
    rows = []
    for sr in (0.0, 1.0, 2.0, 3.0):
        X, _, _ = direct_blocks(rng, "garch_t4", n, nb, sr, L, B)
        c = GAP_C * BLOCK_SD
        Z, sz0 = (X - c) / (B + c), BLOCK_SD / (B + c)
        row = {"true_sr_ann": sr,
               "plugin": summarise(first_cross(log_e_plugin(Z, sz0)), horizons),
               "mixture": summarise(first_cross(log_e_mixture(Z)), horizons),
               "prior": {}}
        priors = (1.0, 2.0, 3.0, 5.0) if sr == 0 else (0.5, 1.0, 1.5, 2.0, 3.0, 5.0)
        for k in priors:
            p = k if sr == 0 else k * sr
            row["prior"][f"x{k:g}" if sr else f"sr{k:g}"] = {
                "prior_sr_ann": p,
                **summarise(first_cross(log_e_prior(Z, p, sz0)), horizons)}
        rows.append(row)
        print("prior", sr, row["plugin"]["reject_by_24"],
              {k: v["reject_by_24"] for k, v in row["prior"].items()}, flush=True)
    return {"dgp": "garch_t4", "n_paths": n, "blocks": nb, "rows": rows}


# ---------------------------------------------------------------- toy pipeline
def rule_pnl(r, lev, cost):
    """r (n, T) asset returns -> net daily P&L (n, R, T) of 12 rules
    (momentum/reversal at 6 lookbacks) and their positions."""
    n, T = r.shape
    cs = np.concatenate([np.zeros((n, 1)), np.cumsum(r, axis=1)], axis=1)
    pnls, poss = [], []
    for k in LOOKBACKS:
        past = np.zeros((n, T))
        past[:, k:] = cs[:, k:T] - cs[:, :T - k]          # sum r[t-k..t-1]
        sig = np.sign(past)
        for d in (1.0, -1.0):
            pos = d * sig * lev
            dpos = np.abs(np.diff(pos, axis=1, prepend=0.0))
            pnls.append(pos * r - cost * dpos)
            poss.append(pos)
    return np.stack(pnls, axis=1), np.stack(poss, axis=1)


def pipeline_blocks(rng, kind, n, nb, phi, L, B, cost=0.0005, batch=400):
    """Toy engine: at each block start pick the rule with the best trailing
    TRAIN-day net Sharpe (flat if none > 0); trade it for DAYS days with the
    stop and the floor; pay entry and exit cost on the position."""
    lev = S0 / ASSET_SD
    T = TRAIN + max(LOOKBACKS) + nb * DAYS
    X_all, chosen_all = [], []
    for i in range(0, n, batch):
        m = min(batch, n - i)
        r = ASSET_SD * innovations(rng, kind, m, T, phi)
        P, POS = rule_pnl(r, lev, cost)
        c1 = np.concatenate([np.zeros((m, P.shape[1], 1)), np.cumsum(P, axis=2)], axis=2)
        c2 = np.concatenate([np.zeros((m, P.shape[1], 1)), np.cumsum(P * P, axis=2)], axis=2)
        X = np.empty((m, nb))
        ch = np.empty((m, nb), dtype=int)
        rows = np.arange(m)
        for b in range(nb):
            s = TRAIN + max(LOOKBACKS) + b * DAYS
            mu = (c1[:, :, s] - c1[:, :, s - TRAIN]) / TRAIN
            var = (c2[:, :, s] - c2[:, :, s - TRAIN]) / TRAIN - mu * mu
            score = mu / np.sqrt(np.maximum(var, 1e-18))
            best = score.argmax(axis=1)
            flat = score[rows, best] <= 0
            daily = P[rows, best, s:s + DAYS].copy()
            pos = POS[rows, best, s:s + DAYS]
            # inside the rule's own P&L the first day's cost is vs the rule's
            # previous position; the block opens from flat instead
            daily[:, 0] += cost * np.abs(pos[:, 0] - POS[rows, best, s - 1]) - cost * np.abs(pos[:, 0])
            c = np.cumsum(daily, axis=1)
            hit = c <= -L
            any_hit = hit.any(axis=1)
            first = np.where(any_hit, hit.argmax(axis=1), DAYS - 1)
            val = c[rows, first] - cost * np.abs(pos[rows, first])   # close out
            val = np.maximum(val, -B)
            X[:, b] = np.where(flat, 0.0, val)
            ch[:, b] = np.where(flat, -1, best)
        X_all.append(X), chosen_all.append(ch)
    return np.concatenate(X_all), np.concatenate(chosen_all)


def part_pipeline(rng, n, nb):
    L, B = L_MULT * BLOCK_SD, B_MULT * BLOCK_SD
    horizons = (12, 24, 36, 60, nb)
    rows = []
    for kind, phi in (("garch_t4", 0.0), ("gjr_skewt", 0.0), ("regime_t4", 0.0),
                      ("ar_garch", 0.05), ("ar_garch", 0.10), ("ar_garch", 0.15),
                      ("ar_garch", 0.20)):
        X, ch = pipeline_blocks(rng, kind, n, nb, phi, L, B)
        active = X != 0
        sr = (float(X.mean() / X.std() * math.sqrt(ANN / DAYS)) if X.std() > 0 else 0.0)
        row = {"dgp": kind, "phi": phi, "h0_holds": phi == 0.0,
               "block_mean": float(X.mean()), "block_mean_se": float(X.std() / math.sqrt(X.size)),
               "share_blocks_traded": float(active.mean()),
               "realised_sr_ann_blocks": sr,
               "bets": run_bets(X, B, horizons)}
        rows.append(row)
        print("pipeline", kind, phi, row["block_mean"], sr,
              row["bets"]["plugin"][f"reject_by_{nb}"], row["bets"]["mixture"][f"reject_by_{nb}"],
              flush=True)
    return {"n_paths": n, "blocks": nb, "train_days": TRAIN, "rules": 2 * len(LOOKBACKS),
            "cost_per_unit_turnover": 0.0005, "rows": rows}


# ---------------------------------------------------------------- part: MC null generators
def search_stat(r, lev=S0 / ASSET_SD, cost=0.0005):
    """max over the 12 rules of the full-sample net Sharpe; r (m, T)."""
    P, _ = rule_pnl(r, lev, cost)
    P = P[:, :, max(LOOKBACKS):]
    mu = P.mean(axis=2)
    sd = P.std(axis=2)
    return (mu / np.maximum(sd, 1e-18)).max(axis=1)


def null_draws(rng, x, N, how):
    T = x.size
    if how == "sign_flip":
        return x[None, :] * rng.choice((-1.0, 1.0), size=(N, T))
    if how == "block_sign_flip":
        s = rng.choice((-1.0, 1.0), size=(N, -(-T // DAYS)))
        return x[None, :] * np.repeat(s, DAYS, axis=1)[:, :T]
    xc = x - x.mean()
    if how == "iid_boot":
        return xc[rng.integers(0, T, size=(N, T))]
    if how == "stationary_boot":
        p = 1.0 / DAYS
        idx = np.empty((N, T), dtype=int)
        idx[:, 0] = rng.integers(0, T, N)
        new = rng.random((N, T)) < p
        jump = rng.integers(0, T, (N, T))
        for t in range(1, T):
            idx[:, t] = np.where(new[:, t], jump[:, t], (idx[:, t - 1] + 1) % T)
        return xc[idx]
    raise ValueError(how)


def part_mc_null(rng, reps, N=99, T=730, level=0.05):
    gens = ("sign_flip", "block_sign_flip", "stationary_boot", "iid_boot")
    rows = []
    for kind, phi in (("garch_t4", 0.0), ("regime_t4", 0.0), ("gjr_skewt", 0.0),
                      ("ar_garch", 0.05)):
        x_all = ASSET_SD * innovations(rng, kind, reps, T, phi)
        s0 = search_stat(x_all)
        row = {"dgp": kind, "phi": phi, "h0_no_predictability": phi == 0.0}
        for g in gens:
            rej = 0
            for i in range(reps):
                sn = search_stat(null_draws(rng, x_all[i], N, g))
                p = (1 + np.sum(sn >= s0[i])) / (N + 1)
                rej += p <= level
            row[g] = {"reject_rate": rej / reps, "mc_se": se(level, reps)}
        rows.append(row)
        print("mc_null", kind, phi, {g: row[g]["reject_rate"] for g in gens}, flush=True)
    return {"level": level, "N": N, "T": T, "reps": reps,
            "statistic": "max over 12 rules of full-sample net daily Sharpe", "rows": rows}


# ---------------------------------------------------------------- part: Dufour MMC
#: null family for the maximized MC test: zero-mean GARCH(1,1) with
#: unit-variance Student-t innovations; nuisance y = (a, b, nu)
MMC_GRID = tuple((a, b, nu) for a, b in ((0.05, 0.80), (0.05, 0.88), (0.10, 0.80),
                                         (0.10, 0.88), (0.15, 0.80))
                 for nu in (4.0, 8.0))


def garch_t_paths(seed, N, T, a, b, nu):
    """S_i(y) = S[g(y, u_i)] (Dufour 2006 Sec. 4): the SAME random stream
    (seed) for every nuisance value y = (a, b, nu)."""
    rng = np.random.default_rng(seed)
    z = std_t(rng, (N, T), nu)
    h = np.ones(N)
    out = np.empty((N, T))
    for t in range(T):
        out[:, t] = np.sqrt(h) * z[:, t]
        h = (1 - a - b) + a * out[:, t] ** 2 + b * h
    return out


def part_mmc(rng, reps, N=99, T=730, level=0.05):
    """Maximized Monte Carlo test (Dufour 2006 Prop. 4.1) of the search
    statistic with the GARCH-t null family above, sup over the grid; plus a
    diagnostic of WHY the stationary bootstrap is conservative: its draws
    are built from the observed series, so they are not exchangeable with S_0
    (corr(S_0, median of the draws) and the mean shift are reported)."""
    rows = []
    for kind, phi in (("garch_t4", 0.0), ("regime_t4", 0.0), ("gjr_skewt", 0.0),
                      ("ar_garch", 0.05), ("ar_garch", 0.10)):
        x_all = ASSET_SD * innovations(rng, kind, reps, T, phi)
        s0 = search_stat(x_all)
        rej_mmc = rej_local = 0
        argmax = {}
        sb_med = np.empty(reps)
        for i in range(reps):
            seed = int(rng.integers(2 ** 62))
            ps = {}
            for y in MMC_GRID:
                sn = search_stat(garch_t_paths(seed, N, T, *y))
                ps[y] = (1 + np.sum(sn >= s0[i])) / (N + 1)
            pmax = max(ps.values())
            ystar = max(ps, key=ps.get)
            argmax[str(ystar)] = argmax.get(str(ystar), 0) + 1
            rej_mmc += pmax <= level
            # local MC (Dufour Sec. 6): one point, the grid value nearest the
            # true process is unknown in practice; use the family centre
            rej_local += ps[(0.10, 0.88, 4.0)] <= level
            if i < min(reps, 300):
                sb_med[i] = np.median(search_stat(null_draws(rng, x_all[i], N, "stationary_boot")))
        k = min(reps, 300)
        row = {"dgp": kind, "phi": phi, "dgp_in_null_family": kind == "garch_t4",
               "h0_no_predictability": phi == 0.0,
               "mmc_reject_rate": rej_mmc / reps, "local_mc_reject_rate": rej_local / reps,
               "mc_se": se(level, reps), "argmax_counts": argmax,
               "stationary_boot_diag": {
                   "n": k, "corr_s0_null_median": float(np.corrcoef(s0[:k], sb_med[:k])[0, 1]),
                   "mean_s0": float(s0[:k].mean()), "mean_null_median": float(sb_med[:k].mean())}}
        rows.append(row)
        print("mmc", kind, phi, row["mmc_reject_rate"], row["local_mc_reject_rate"],
              row["stationary_boot_diag"], flush=True)
    return {"level": level, "N": N, "T": T, "reps": reps, "grid": [list(y) for y in MMC_GRID],
            "statistic": "max over 12 rules of full-sample net daily Sharpe", "rows": rows}


# ---------------------------------------------------------------- part: alpha split v2/v3
def part_split(rng, n, nb=36):
    """v2 (frozen bundle, one look after 12 months) and v3 (this e-process) on
    the SAME forward data, alpha split by Bonferroni: v2 gets w*alpha, v3 gets
    (1-w)*alpha, so P(either rejects | both nulls) <= alpha whatever their
    correlation.  Both see the same synthetic daily P&L path (the strongest
    possible correlation: the bundle and the pipeline earn the same edge).

    v2 is modelled as its unhedged gate only, a one-sided t test on 360 daily
    returns (normal critical value); its beta-hedged second gate can only
    lower its power, so v2 numbers are upper bounds."""
    from statistics import NormalDist
    L, B = L_MULT * BLOCK_SD, B_MULT * BLOCK_SD
    weights = (1.0, 0.8, 0.7, 0.5, 0.3, 0.2, 0.0)
    rows = []
    for sr in (0.0, 1.0, 2.0, 3.0, 4.0):
        mu = sr * S0 / math.sqrt(ANN)
        r = mu + S0 * innovations(rng, "garch_t4", n, nb * DAYS)
        d12 = r[:, :12 * DAYS]
        t12 = d12.mean(axis=1) / d12.std(axis=1, ddof=1) * math.sqrt(d12.shape[1])
        X, _, _ = block_pnl(r, L, B)
        c = GAP_C * BLOCK_SD
        logE = log_e_plugin((X - c) / (B + c), BLOCK_SD / (B + c))
        for w in weights:
            a2, a3 = w * ALPHA, (1 - w) * ALPHA
            v2 = (t12 > NormalDist().inv_cdf(1 - a2)) if a2 > 0 else np.zeros(n, bool)
            tau = first_cross(logE, math.log(1 / a3)) if a3 > 0 else np.full(n, np.inf)
            row = {"true_sr_ann": sr, "w_v2": w, "alpha_v2": a2, "alpha_v3": a3,
                   "v2_reject_at_12": float(v2.mean())}
            for h in (12, 24, 36):
                row[f"v3_reject_by_{h}"] = float(np.mean(tau <= h))
                row[f"either_by_{h}"] = float(np.mean(v2 | (tau <= h)))
            rows.append(row)
        print("split", sr, [(w, rows[-len(weights) + i]["either_by_24"])
                            for i, w in enumerate(weights)], flush=True)
    return {"dgp": "garch_t4", "n_paths": n, "blocks": nb, "alpha_total": ALPHA,
            "rows": rows}


# ---------------------------------------------------------------- main
PARTS = {"design": part_design, "type1": part_type1, "power": part_power,
         "prior": part_prior, "pipeline": part_pipeline, "split": part_split}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--parts", default="design,type1,power,prior,pipeline,mc_null")
    a = ap.parse_args(argv)
    q = a.quick
    size = {  # (paths, blocks)
        "design": (300, 60) if q else (4000, 120),
        "type1": (400, 60) if q else (20000, 120),
        "power": (300, 60) if q else (5000, 60),
        "prior": (300, 60) if q else (5000, 60),
        "pipeline": (200, 36) if q else (4000, 60),
        "split": (300,) if q else (20000,),
    }
    out = {"schema": "evalue_walkforward_sim_v1", "seed": a.seed, "quick": q,
           "synthetic_only": True, "alpha": ALPHA, "block_days": DAYS,
           "strategy_daily_sd": S0, "main_design": {"L_sd": L_MULT, "B_sd": B_MULT,
                                                    "lam_max": LAM_MAX, "gap_c_sd": GAP_C,
                                                    "k0": PRIOR_K0}}
    for i, part in enumerate(a.parts.split(",")):
        rng = np.random.default_rng([a.seed, i])
        t0 = time.time()
        if part == "mc_null":
            out[part] = part_mc_null(rng, 60 if q else 1000)
        elif part == "mmc":
            out[part] = part_mmc(rng, 20 if q else 600)
        else:
            out[part] = PARTS[part](rng, *size[part])
        out[part]["secs"] = round(time.time() - t0, 1)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    main()
