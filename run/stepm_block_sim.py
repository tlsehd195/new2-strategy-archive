"""StepM family-wise error rate vs the stationary-bootstrap block length
(synthetic only, ADR 0027).

The validation gate is the studentized StepM of ``discovery/stats_mc.py``
(Romano & Wolf 2005 Alg. 4.1 / Romano-Shaikh-Wolf 2008 Alg. 4.3, k = 1) with
sigma held fixed across resamples and mean block length 1/q = n^(1/3)
(``default_q``).  Two paper-based ways to choose the block length exist:

  ppw   Politis & White (2004) eq. (9) with the corrected D_SB of Patton,
        Politis & White (2009) — ``stats_mc.ppw_block_length``, longest block
        over the strategies (``stats_mc.ppw_q``).
  rw61  Romano & Wolf (2005) Algorithm 6.1 (= RSW 2008 Alg. A.5 with k = 1),
        first step: fit a VAR(1) (their Remark 6.1: "a low-order VAR"),
        simulate M data sets from it with resampled residuals, and pick the
        block length whose first-step joint region covers the VAR-implied
        mean closest to 1 - alpha.  RW note it is expensive and claim no
        optimality (Remarks 6.2, 6.3).

This file measures, on synthetic daily P&L with a known truth, the FWER of
the gate at alpha for fixed block lengths, the ppw choice and n^(1/3), the
paper's bootstrap-t form (sigma* per resample, ``stepm_boot_t``), and which
block length rw61 picks.  Nothing reads market data, validation,
locked windows or IW1; no criterion changes.

    cd run && python3 stepm_block_sim.py --out audit/evidence_paper_faithful_20261022/stepm_block_results.json
    cd run && python3 stepm_block_sim.py --quick
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "discovery"))
import stats_mc  # noqa: E402

ALPHA = 0.05
N_DAYS = 365
S = 10
BLOCKS = (1.0, 3.0, 7.0, 15.0, 30.0)


# ---------------------------------------------------------------- data
def make_data(rng, kind, n=N_DAYS, s=S, n_alt=0, sr_alt=2.0):
    """(n, s) daily P&L, unit-ish variance; the first n_alt series get annual
    Sharpe sr_alt, the rest mean 0 (true nulls)."""
    T = n + 200
    if kind == "iid_t4":
        e = rng.standard_t(4, (T, s)) / math.sqrt(2.0)
    else:
        f = rng.standard_t(4, T) / math.sqrt(2.0)
        z = rng.standard_t(4, (T, s)) / math.sqrt(2.0)
        z = math.sqrt(0.5) * f[:, None] + math.sqrt(0.5) * z        # cross corr 0.5
        a, b = 0.10, 0.85
        h = np.ones(s)
        e = np.empty((T, s))
        for t in range(T):
            e[t] = np.sqrt(h) * z[t]
            h = (1 - a - b) + a * e[t] ** 2 + b * h
        phi = {"garch_corr": 0.0, "garch_ar02": 0.2, "garch_ar06": 0.6}[kind]
        if phi:
            c = math.sqrt(1 - phi * phi)
            for t in range(1, T):
                e[t] = phi * e[t - 1] + c * e[t]
    x = e[200:]
    mu = np.zeros(s)
    mu[:n_alt] = sr_alt / math.sqrt(365.0) * x[:, :n_alt].std(axis=0)
    return x + mu, mu


# ---------------------------------------------------------------- StepM (numpy copy of stats_mc.stepm)
def acov_all(x):
    """Autocovariances (lag 0..n-1) of every column, divisor n."""
    n = x.shape[0]
    u = x - x.mean(axis=0)
    f = np.fft.rfft(u, n=2 * n, axis=0)
    return np.fft.irfft(f * np.conj(f), axis=0)[:n] / n


def hansen_var(ac, q):
    """Hansen (2005) bootstrap-population variance of sqrt(n) dbar per column
    from autocovariances ``ac`` (n, s) — same formula as stats_mc."""
    n = ac.shape[0]
    i = np.arange(1, n)
    kappa = (n - i) / n * (1 - q) ** i + i / n * (1 - q) ** (n - i)
    kappa[kappa < 1e-15] = 0.0
    return np.maximum(ac[0] + 2 * (kappa[:, None] * ac[1:]).sum(axis=0), 0.0)


def sb_indices(rng, n, q, B):
    idx = np.empty((B, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, B)
    new = rng.random((B, n)) < q
    jump = rng.integers(0, n, (B, n))
    for t in range(1, n):
        idx[:, t] = np.where(new[:, t], jump[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def stepm_np(x, q, rng, B, alpha=ALPHA, ac=None):
    n = x.shape[0]
    ac = acov_all(x) if ac is None else ac
    sig = np.sqrt(hansen_var(ac, q) / n)
    dbar = x.mean(axis=0)
    z = dbar / sig
    idx = sb_indices(rng, n, q, B)
    draws = (x[idx].mean(axis=1) - dbar) / sig                         # (B, s)
    rejected = np.zeros(x.shape[1], bool)
    k = int(math.ceil((1 - alpha) * B)) - 1
    while True:
        rem = ~rejected
        if not rem.any():
            break
        crit = np.sort(draws[:, rem].max(axis=1))[min(B - 1, k)]
        new = rem & (z > crit)
        if not new.any():
            break
        rejected |= new
    return rejected, sig


def stepm_boot_t(x, q, rng, B, alpha=ALPHA, ac=None):
    """Bootstrap-t StepM as written in RW2005 eq. (11) / RSW2008 Alg. A.3:
    sigma*_k recomputed on every resample.  For time series RW2005 Sec. 4.1
    says sigma* "can be obtained by applying the same algorithm to the
    bootstrap data matrix"; the same estimator (Hansen kappa variance) is
    applied to each resampled series.  (Goetze-Kuensch's "natural" estimator
    is for moving/circular blocks and was not read.)"""
    n = x.shape[0]
    ac = acov_all(x) if ac is None else ac
    sig = np.sqrt(hansen_var(ac, q) / n)
    dbar = x.mean(axis=0)
    z = dbar / sig
    idx = sb_indices(rng, n, q, B)
    xs = x[idx]                                                        # (B, n, s)
    u = xs - xs.mean(axis=1, keepdims=True)
    f = np.fft.rfft(u, n=2 * n, axis=1)
    acs = np.fft.irfft(f * np.conj(f), axis=1)[:, :n] / n              # (B, n, s)
    i = np.arange(1, n)
    kappa = (n - i) / n * (1 - q) ** i + i / n * (1 - q) ** (n - i)
    kappa[kappa < 1e-15] = 0.0
    v = np.maximum(acs[:, 0] + 2 * np.einsum("i,bis->bs", kappa, acs[:, 1:]), 1e-300)
    draws = (xs.mean(axis=1) - dbar) / np.sqrt(v / n)
    rejected = np.zeros(x.shape[1], bool)
    k = int(math.ceil((1 - alpha) * B)) - 1
    while True:
        rem = ~rejected
        if not rem.any():
            break
        crit = np.sort(draws[:, rem].max(axis=1))[min(B - 1, k)]
        new = rem & (z > crit)
        if not new.any():
            break
        rejected |= new
    return rejected


def first_step_crit(x, q, rng, B, alpha=ALPHA):
    """sigma and the first-step critical value d_1 of the studentized StepM."""
    n = x.shape[0]
    sig = np.sqrt(hansen_var(acov_all(x), q) / n)
    idx = sb_indices(rng, n, q, B)
    draws = (x[idx].mean(axis=1) - x.mean(axis=0)) / sig
    k = int(math.ceil((1 - alpha) * B)) - 1
    return sig, np.sort(draws.max(axis=1))[min(B - 1, k)]


# ---------------------------------------------------------------- RW Algorithm 6.1
def rw61_choice(x, rng, M, B, blocks=BLOCKS, alpha=ALPHA):
    """Romano & Wolf (2005) Algorithm 6.1, first step (i = 1)."""
    n, s = x.shape
    Y, X = x[1:], np.column_stack([np.ones(n - 1), x[:-1]])
    coef, *_ = np.linalg.lstsq(X, Y, rcond=None)                        # (1+s, s)
    c, A = coef[0], coef[1:].T
    resid = Y - X @ coef
    theta = np.linalg.solve(np.eye(s) - A, c)                           # VAR-implied mean
    if np.max(np.abs(np.linalg.eigvals(A))) >= 0.999:
        return None, {}
    cover = {b: 0 for b in blocks}
    for _ in range(M):
        e = resid[rng.integers(0, n - 1, n + 100)]
        y = np.empty((n + 100, s))
        y[0] = theta
        for t in range(1, n + 100):
            y[t] = c + A @ y[t - 1] + e[t]
        xs = y[100:]
        for b in blocks:
            sig, d1 = first_step_crit(xs, 1.0 / b, rng, B, alpha)
            cover[b] += bool(np.all(theta >= xs.mean(axis=0) - sig * d1))
    g = {b: cover[b] / M for b in blocks}
    best = min(blocks, key=lambda b: (abs(g[b] - (1 - alpha)), b))
    return best, g


# ---------------------------------------------------------------- experiment
def run(rng, reps, B, rw_reps, M, rw_B):
    rows = []
    for kind in ("iid_t4", "garch_corr", "garch_ar02", "garch_ar06"):
        for n_alt in (0, 3):
            t0 = time.time()
            methods = {f"b{b:g}": b for b in BLOCKS}
            fwer = {k: 0 for k in list(methods) + ["n13_default", "ppw", "n13_boot_t", "b1_boot_t"]}
            power = {k: 0.0 for k in fwer}
            ppw_b = []
            for _ in range(reps):
                x, mu = make_data(rng, kind, n_alt=n_alt)
                ac = acov_all(x)
                cfg = dict(methods)
                cfg["n13_default"] = 1.0 / stats_mc.default_q(N_DAYS)
                bp = max(stats_mc.ppw_block_length(x[:, j].tolist()) for j in range(S))
                cfg["ppw"] = bp
                ppw_b.append(bp)
                cfg["n13_boot_t"] = cfg["n13_default"]
                cfg["b1_boot_t"] = 1.0
                for name, b in cfg.items():
                    if name.endswith("boot_t"):
                        rej = stepm_boot_t(x, 1.0 / b, rng, B, ac=ac)
                    else:
                        rej, _ = stepm_np(x, 1.0 / b, rng, B, ac=ac)
                    fwer[name] += bool(rej[mu == 0].any())
                    if n_alt:
                        power[name] += rej[mu > 0].mean()
            row = {"dgp": kind, "n_alt": n_alt, "reps": reps, "B": B,
                   "fwer": {k: v / reps for k, v in fwer.items()},
                   "mc_se_at_alpha": math.sqrt(ALPHA * (1 - ALPHA) / reps),
                   "ppw_block": {"mean": float(np.mean(ppw_b)), "p10": float(np.percentile(ppw_b, 10)),
                                 "p90": float(np.percentile(ppw_b, 90))}}
            if n_alt:
                row["avg_power_alt"] = {k: v / reps for k, v in power.items()}
            picks = {}
            for _ in range(rw_reps if n_alt == 0 else 0):
                x, _ = make_data(rng, kind)
                b, _g = rw61_choice(x, rng, M, rw_B)
                picks[str(b)] = picks.get(str(b), 0) + 1
            if picks:
                row["rw61_choice_counts"] = picks
                row["rw61"] = {"M": M, "B": rw_B, "reps": rw_reps}
            row["secs"] = round(time.time() - t0, 1)
            rows.append(row)
            print(kind, n_alt, row["fwer"], row.get("rw61_choice_counts"), flush=True)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args(argv)
    rng = np.random.default_rng(a.seed)
    reps, B, rw_reps, M, rw_B = (40, 199, 3, 20, 99) if a.quick else (800, 499, 40, 100, 199)
    out = {"schema": "stepm_block_sim_v1", "seed": a.seed, "quick": a.quick, "synthetic_only": True,
           "alpha": ALPHA, "n_days": N_DAYS, "strategies": S,
           "gate": "studentized StepM, fixed sigma (Hansen 2005 kappa variance), stationary bootstrap",
           "rows": run(rng, reps, B, rw_reps, M, rw_B)}
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, indent=1) + "\n")
    return out


if __name__ == "__main__":
    main()
