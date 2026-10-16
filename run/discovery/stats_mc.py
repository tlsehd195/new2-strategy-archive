"""Data-snooping-robust multiple comparison (paper-based, stdlib only).

Implemented from the source texts (checked 2026-10-15; see docs/adr/0003):

* Stationary bootstrap — Politis & Romano (1994) as used by White (2000) §2:
  blocks start at a uniform index, block length is geometric with mean
  ``1/q``, indices wrap around (circular).  Assumes a strictly stationary,
  weakly dependent (alpha-mixing) series.
* Reality Check — White (2000): V = max_k sqrt(n) * dbar_k, bootstrap
  V* = max_k sqrt(n) * (dbar*_k - dbar_k), p = share of V* >= V
  (least-favourable null E d_k = 0 for every k).
* SPA — Hansen (2005): studentized T = max(max_k sqrt(n) dbar_k / w_k, 0);
  recentering g_l / g_c / g_u, where g_c keeps the mean of a model only when
  dbar_k >= -sqrt(w_k^2/n * 2 log log n) (the "consistent" p-value);
  p = share of T* > T.  w_k^2 is the bootstrap-population variance
  gamma_0 + 2 sum kappa(n,i) gamma_i with
  kappa(n,i) = (n-i)/n (1-q)^i + i/n (1-q)^(n-i)   (Hansen 2005, §3).
* StepM — Romano & Wolf (2005) / Romano-Shaikh-Wolf (2008) Alg. 4.3 with k=1 (studentized): relabel by the
  studentized statistic, critical value = (1-alpha) bootstrap quantile of
  max over the NOT-yet-rejected hypotheses of (w*_k - w_k) / sigma*_k,
  reject every remaining z_k above it, repeat until nothing new is rejected.
  Asymptotic STRONG FWER control under arbitrary dependence between the
  strategies (the joint bootstrap keeps the cross-sectional dependence).
* Step-SPA — Hsu, Hsu & Kuan (2010), full text read: the Step-RC loop of
  Romano-Wolf, but the critical value at every step is the SPA one — the
  (1-alpha) quantile of max_k sqrt(n)(dbar*_k - dbar_k + mu_hat_k) with the
  Hansen re-centering mu_hat_k = dbar_k * 1(sqrt(n) dbar_k <= -sigma_k
  sqrt(2 log log n)), floored at 0 — recomputed on the not-yet-rejected
  models.  The paper proves FWE <= alpha asymptotically and strictly more
  power than Step-RC under Hansen's stationarity / alpha-mixing assumption.
  Because it is LESS conservative than StepM it is only a DIAGNOSTIC here;
  the validation gate stays StepM (criteria are not relaxed).  Studentized
  form (the paper notes studentizing is more powerful) with the
  bootstrap-population sigma used elsewhere in this module.
* Diebold & Mariano (1995), full text read: for a loss differential d_t
  (covariance stationary, short memory) sqrt(T)(dbar - mu) -> N(0, 2 pi f_d(0)),
  S = dbar / sqrt(2 pi f_hat_d(0) / T) ~ N(0,1) under H0: E d_t = 0.  Used for
  parent-vs-child strategy comparisons on DEV daily returns (d = child -
  parent).  Difference from the paper: no forecast horizon exists here, so
  f_hat is the Bartlett-weighted (Newey-West) long-run variance with the
  usual n^(2/9) lag rule instead of the paper's rectangular h-1 truncation
  (which can go negative).  DEV-only, diagnostic.
* Model Confidence Set — Hansen, Lunde & Nason (2011), working-paper text
  (CREATES RP 2010-76): d_ij = L_i - L_j, t_i. = dbar_i. / sqrt(var(dbar_i.)),
  T_max,M = max_i t_i., elimination rule e = argmax t_i. (the object with the
  largest relative loss), p-value = bootstrap share of T*_max >= T_max, MCS
  p-value of the j-th eliminated object = running maximum of the test
  p-values (the last survivor gets 1).  The paper bootstraps with a MOVING
  block; here the stationary bootstrap (random block length) is used, and the
  variance of dbar_i. is the bootstrap variance (the paper defers the
  estimator to an appendix that was not read).  The MCS is a SET of
  statistically indistinguishable models — it is never turned into a ranking.
* Benjamini & Yekutieli (2001) Theorem 1.3: BH with q / sum_{i<=m} 1/i
  controls FDR under ANY dependence (BH itself needs PRDS, their Thm 1.2).

Conditions that differ from the papers (and are therefore stated, not
assumed away): the papers use daily (or lower-frequency) returns of rules
evaluated on one long sample; here d_{k,t} are DAILY net returns of 15m
strategies (events assigned to the UTC day of their exit, flat days = 0),
so stationarity of crypto returns over the window is an ASSUMPTION; the
benchmark is "no position" (d = strategy net return), as in White's
trading-rule application.

Alpha spending over repeated use of the SAME data window (validation
batches, locked-TEST campaigns): batch/campaign k of a window is tested at
alpha_k = alpha_total * (6/pi^2) / k^2, so sum_k alpha_k <= alpha_total
(union bound — valid under arbitrary dependence between batches, including
batches whose members were chosen after looking at earlier DEV results).
"""

from __future__ import annotations

import math
import random
from typing import Mapping, Optional, Sequence

#: version tag recorded in every decision (changing any rule => new tag)
MC_METHOD_VERSION = "mc-v3(stationary-bootstrap,RC,SPA,StepM-studentized,StepSPA-diagnostic,MCS-Tmax,BY,alpha-spending-1/k^2)"


# ---------------------------------------------------------------------------
# stationary bootstrap
# ---------------------------------------------------------------------------

def stationary_bootstrap_indices(n: int, q: float, rng: random.Random) -> list[int]:
    """One stationary-bootstrap resample of 0..n-1 (Politis & Romano 1994)."""
    if n <= 0:
        return []
    if not 0.0 < q <= 1.0:
        raise ValueError("q must be in (0, 1]")
    out = [0] * n
    idx = rng.randrange(n)
    for t in range(n):
        if t > 0:
            if rng.random() < q:
                idx = rng.randrange(n)          # new block
            else:
                idx = (idx + 1) % n             # continue block, wrap around
        out[t] = idx
    return out


def default_q(n: int) -> float:
    """Mean block length ~ n^(1/3) (a common rate choice; White 2000 leaves
    q to the user).  Recorded with every result."""
    return min(1.0, 1.0 / max(1.0, n ** (1.0 / 3.0)))


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def bootstrap_variance(d: Sequence[float], q: float) -> float:
    """Hansen (2005) §3 bootstrap-population variance of sqrt(n) * dbar."""
    n = len(d)
    mu = _mean(d)
    u = [x - mu for x in d]
    g0 = sum(v * v for v in u) / n
    total = g0
    for i in range(1, n):
        kappa = (n - i) / n * (1 - q) ** i + i / n * (1 - q) ** (n - i)
        if kappa < 1e-15:
            continue
        gi = sum(u[t] * u[t + i] for t in range(n - i)) / n
        total += 2 * kappa * gi
    return max(total, 0.0)


def _check_matrix(series: Mapping[str, Sequence[float]]) -> tuple[list[str], int]:
    keys = sorted(series)
    if not keys:
        raise ValueError("no series")
    n = len(series[keys[0]])
    if any(len(series[k]) != n for k in keys):
        raise ValueError("all series must share one time index")
    if n < 10:
        raise ValueError(f"too few observations ({n}) for a bootstrap test")
    for k in keys:
        if any(not math.isfinite(x) for x in series[k]):
            raise ValueError(f"non-finite value in series {k}")
    return keys, n


def reality_check(series: Mapping[str, Sequence[float]], *, B: int = 1000,
                  q: Optional[float] = None, seed: int = 0) -> dict:
    """White (2000) Reality Check p-value for H0: max_k E d_k <= 0."""
    keys, n = _check_matrix(series)
    q = q or default_q(n)
    rng = random.Random(seed)
    dbar = {k: _mean(series[k]) for k in keys}
    v = max(math.sqrt(n) * dbar[k] for k in keys)
    count = 0
    for _ in range(B):
        ix = stationary_bootstrap_indices(n, q, rng)
        vb = max(math.sqrt(n) * (_mean([series[k][i] for i in ix]) - dbar[k]) for k in keys)
        if vb >= v:
            count += 1
    return {"method": "white2000_reality_check", "statistic": v, "p": count / B,
            "B": B, "q": q, "n": n, "m": len(keys), "seed": seed}


def spa_test(series: Mapping[str, Sequence[float]], *, B: int = 1000,
             q: Optional[float] = None, seed: int = 0) -> dict:
    """Hansen (2005) SPA test: lower / consistent / upper p-values."""
    keys, n = _check_matrix(series)
    q = q or default_q(n)
    rng = random.Random(seed)
    dbar = {k: _mean(series[k]) for k in keys}
    w = {}
    for k in keys:
        var = bootstrap_variance(series[k], q)
        if var <= 0:
            raise ValueError(f"zero variance for {k}: SPA statistic undefined")
        w[k] = math.sqrt(var)
    t_obs = max(max(math.sqrt(n) * dbar[k] / w[k] for k in keys), 0.0)
    thr = math.sqrt(2.0 * math.log(math.log(n))) if n > 15 else 0.0
    g = {
        "l": {k: max(0.0, dbar[k]) for k in keys},
        "c": {k: dbar[k] if dbar[k] >= -w[k] / math.sqrt(n) * thr else 0.0 for k in keys},
        "u": {k: dbar[k] for k in keys},
    }
    counts = {"l": 0, "c": 0, "u": 0}
    for _ in range(B):
        ix = stationary_bootstrap_indices(n, q, rng)
        means = {k: _mean([series[k][i] for i in ix]) for k in keys}
        for tag in counts:
            tb = max(max(math.sqrt(n) * (means[k] - g[tag][k]) / w[k] for k in keys), 0.0)
            if tb > t_obs:
                counts[tag] += 1
    return {"method": "hansen2005_spa", "statistic": t_obs,
            "p_lower": counts["l"] / B, "p_consistent": counts["c"] / B,
            "p_upper": counts["u"] / B, "B": B, "q": q, "n": n, "m": len(keys),
            "seed": seed}


def stepm(series: Mapping[str, Sequence[float]], *, alpha: float, B: int = 1000,
          q: Optional[float] = None, seed: int = 0) -> dict:
    """Romano & Wolf (2005) studentized StepM (Algorithm 4.1) for the
    one-sided hypotheses H0_k: E d_k <= 0.  Returns the rejected keys —
    strategies whose mean is shown > 0 with asymptotic FWER <= alpha."""
    keys, n = _check_matrix(series)
    q = q or default_q(n)
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    rng = random.Random(seed)
    dbar = {k: _mean(series[k]) for k in keys}
    sig = {}
    for k in keys:
        var = bootstrap_variance(series[k], q)
        if var <= 0:
            raise ValueError(f"zero variance for {k}")
        sig[k] = math.sqrt(var / n)
    z = {k: dbar[k] / sig[k] for k in keys}
    # joint bootstrap draws, reused across steps (same resamples for every k)
    draws: list[dict] = []
    for _ in range(B):
        ix = stationary_bootstrap_indices(n, q, rng)
        draws.append({k: (_mean([series[k][i] for i in ix]) - dbar[k]) / sig[k] for k in keys})
    rejected: list[str] = []
    steps = []
    remaining = sorted(keys, key=lambda k: -z[k])
    while remaining:
        maxes = sorted(max(d[k] for k in remaining) for d in draws)
        crit = maxes[min(B - 1, int(math.ceil((1 - alpha) * B)) - 1)]
        newly = [k for k in remaining if z[k] > crit]
        steps.append({"remaining": len(remaining), "critical": crit, "rejected": sorted(newly)})
        if not newly:
            break
        rejected += newly
        remaining = [k for k in remaining if k not in newly]
    return {"method": "romano_wolf2005_stepm_studentized", "alpha": alpha,
            "rejected": sorted(rejected), "z": z, "steps": steps, "B": B, "q": q,
            "n": n, "m": len(keys), "seed": seed,
            "note": "sigma from Hansen-2005 bootstrap-population variance, held fixed "
                    "across resamples (Romano-Wolf allow sigma*; fixed sigma is the "
                    "simpler consistent choice under their Assumption 4.1)"}


def step_spa(series: Mapping[str, Sequence[float]], *, alpha: float, B: int = 1000,
             q: Optional[float] = None, seed: int = 0) -> dict:
    """Hsu-Hsu-Kuan (2010) Step-SPA (studentized) for H0_k: E d_k <= 0.
    Diagnostic only — see module docstring."""
    keys, n = _check_matrix(series)
    q = q or default_q(n)
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    rng = random.Random(seed)
    dbar = {k: _mean(series[k]) for k in keys}
    w = {}
    for k in keys:
        var = bootstrap_variance(series[k], q)
        if var <= 0:
            raise ValueError(f"zero variance for {k}")
        w[k] = math.sqrt(var)
    z = {k: math.sqrt(n) * dbar[k] / w[k] for k in keys}
    a_n = -math.sqrt(2.0 * math.log(math.log(n))) if n > 15 else 0.0
    mu_hat = {k: (dbar[k] if z[k] <= a_n else 0.0) for k in keys}
    draws: list[dict] = []
    for _ in range(B):
        ix = stationary_bootstrap_indices(n, q, rng)
        draws.append({k: math.sqrt(n) * (_mean([series[k][i] for i in ix]) - dbar[k] + mu_hat[k]) / w[k]
                      for k in keys})
    rejected: list[str] = []
    steps = []
    remaining = sorted(keys, key=lambda k: -z[k])
    while remaining:
        maxes = sorted(max(d[k] for k in remaining) for d in draws)
        crit = max(maxes[min(B - 1, int(math.ceil((1 - alpha) * B)) - 1)], 0.0)
        newly = [k for k in remaining if z[k] > crit]
        steps.append({"remaining": len(remaining), "critical": crit, "rejected": sorted(newly)})
        if not newly:
            break
        rejected += newly
        remaining = [k for k in remaining if k not in newly]
    return {"method": "hsu_hsu_kuan2010_step_spa_studentized", "alpha": alpha,
            "rejected": sorted(rejected), "z": z, "steps": steps, "B": B, "q": q,
            "n": n, "m": len(keys), "seed": seed}


def dm_test(d: Sequence[float], *, lag: Optional[int] = None) -> dict:
    """Diebold-Mariano statistic for H0: E[d_t] = 0 (d = loss/return
    differential).  Returns z and the one-sided p for 'mean(d) > 0'."""
    n = len(d)
    if n < 10:
        raise ValueError(f"too few observations ({n}) for a DM test")
    if any(not math.isfinite(x) for x in d):
        raise ValueError("non-finite value in the differential")
    if lag is None:
        lag = int(math.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    mu = _mean(d)
    u = [x - mu for x in d]
    lrv = sum(v * v for v in u) / n
    for l in range(1, min(lag, n - 1) + 1):
        lrv += 2.0 * (1.0 - l / (lag + 1.0)) * sum(u[i] * u[i - l] for i in range(l, n)) / n
    if not lrv > 0:
        raise ValueError("non-positive long-run variance: DM statistic undefined")
    z = mu / math.sqrt(lrv / n)
    return {"method": "diebold_mariano1995_bartlett_lrv", "z": z,
            "p_one_sided_better": 0.5 * math.erfc(z / math.sqrt(2.0)), "n": n, "lag": lag,
            "mean": mu}


def mcs(series: Mapping[str, Sequence[float]], *, alpha: float = 0.10, B: int = 1000,
        q: Optional[float] = None, seed: int = 0) -> dict:
    """Model Confidence Set (T_max, Hansen–Lunde–Nason 2011) over daily NET
    RETURN series (higher is better; the loss is the negative return).
    Returns each model's MCS p-value, the set with p >= alpha and the
    elimination order.  Statistically indistinguishable models all stay in
    the set — callers must not rank inside it."""
    keys, n = _check_matrix(series)
    q = q or default_q(n)
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if len(keys) < 2:
        return {"method": "hln2011_mcs_tmax", "alpha": alpha, "p": {keys[0]: 1.0},
                "set": list(keys), "eliminated": [], "B": B, "q": q, "n": n, "seed": seed}
    rng = random.Random(seed)
    loss = {k: [-x for x in series[k]] for k in keys}
    lbar = {k: _mean(loss[k]) for k in keys}
    boot = []
    for _ in range(B):
        ix = stationary_bootstrap_indices(n, q, rng)
        boot.append({k: _mean([loss[k][i] for i in ix]) for k in keys})
    M = list(keys)
    eliminated: list[str] = []
    pvals: dict[str, float] = {}
    running = 0.0
    while len(M) > 1:
        mbar = _mean([lbar[k] for k in M])
        dbar = {k: lbar[k] - mbar for k in M}
        dstar = []
        for b in boot:
            mb = _mean([b[k] for k in M])
            dstar.append({k: b[k] - mb for k in M})
        sd = {}
        for k in M:
            xs = [d[k] for d in dstar]
            mu = _mean(xs)
            var = sum((x - mu) ** 2 for x in xs) / (B - 1)
            if var <= 0:
                raise ValueError(f"zero bootstrap variance for {k}: MCS statistic undefined")
            sd[k] = math.sqrt(var)
        t = {k: dbar[k] / sd[k] for k in M}
        t_obs = max(t.values())
        count = sum(1 for d in dstar if max((d[k] - dbar[k]) / sd[k] for k in M) >= t_obs)
        running = max(running, count / B)
        worst = max(M, key=lambda k: (t[k], k))
        pvals[worst] = running
        eliminated.append(worst)
        M.remove(worst)
    pvals[M[0]] = 1.0
    return {"method": "hln2011_mcs_tmax", "alpha": alpha, "p": pvals,
            "set": sorted(k for k, v in pvals.items() if v >= alpha),
            "eliminated": eliminated, "B": B, "q": q, "n": n, "m": len(keys), "seed": seed,
            "note": "stationary (not moving-block) bootstrap; bootstrap variance of dbar_i."}


def by_fdr(pvalues: Sequence[float], m_family: int, q: float = 0.05) -> list[bool]:
    """Benjamini–Yekutieli (2001, Thm 1.3): BH at q / H_m — valid under any
    dependence.  Hypotheses without a statistic are counted in m."""
    from engine_lib import bh_fdr_family
    h = sum(1.0 / i for i in range(1, max(1, m_family) + 1))
    return bh_fdr_family(pvalues, m_family, q / h)


def spending_alpha(k: int, alpha_total: float = 0.05) -> float:
    """alpha for the k-th (1-based) use of one data window; sum <= alpha_total."""
    if k < 1:
        raise ValueError("k is 1-based")
    return alpha_total * 6.0 / (math.pi ** 2) / (k * k)


# ---------------------------------------------------------------------------
# helpers: event returns -> aligned daily series
# ---------------------------------------------------------------------------

DAY_MS = 86_400_000


def daily_series(events: Sequence[Mapping], start_ms: int, end_ms: int) -> list[float]:
    """Daily net returns (bp) on the UTC-day grid [start, end]: every event's
    net return is booked on the day of its exit; days without exits are 0."""
    d0, d1 = start_ms // DAY_MS, end_ms // DAY_MS
    out = [0.0] * (d1 - d0 + 1)
    for e in events:
        d = int(e["exit_ts"]) // DAY_MS
        if d0 <= d <= d1:
            out[d - d0] += float(e["net_bp"])
    return out
