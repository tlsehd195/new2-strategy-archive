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
* StepM — Romano & Wolf (2005), Algorithm 4.1 (studentized): relabel by the
  studentized statistic, critical value = (1-alpha) bootstrap quantile of
  max over the NOT-yet-rejected hypotheses of (w*_k - w_k) / sigma*_k,
  reject every remaining z_k above it, repeat until nothing new is rejected.
  Asymptotic STRONG FWER control under arbitrary dependence between the
  strategies (the joint bootstrap keeps the cross-sectional dependence).
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
MC_METHOD_VERSION = "mc-v1(stationary-bootstrap,RC,SPA,StepM-studentized,BY,alpha-spending-1/k^2)"


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
