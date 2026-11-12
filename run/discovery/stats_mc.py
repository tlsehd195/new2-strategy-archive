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
This is exactly "Alpha-Spending" of Tian & Ramdas (2021, arXiv 1910.04900,
eq. (2): test H_i at alpha_i := alpha*gamma_i with sum gamma_i = 1; "works
even when the p-values are arbitrarily dependent", Sec. 3) with the gamma
sequence gamma_i = 6/(pi^2 i^2) they use in their own experiments (Fig. 3-5).
Their Online Fallback (eq. (4)/(5), Prop. 2: valid under arbitrary
dependence, at least as powerful) is available as ``fallback_alpha`` but is
NOT wired into any gate (adopting it needs a new preregistration).

Paper checks added 2026-10-22 (ADR 0027): ``ppw_block_length`` (Politis &
White 2004 eq. (9) with the corrected D_SB of Patton, Politis & White 2009),
``mc_pvalue`` / ``mmc_pvalue`` (Dufour 2006 eq. (2.16)/(2.35), Prop. 4.1).
None of them changes a default: every frozen procedure keeps ``default_q``.
"""

from __future__ import annotations

import math
import random
from typing import Mapping, Optional, Sequence

#: version tag recorded in every decision (changing any rule => new tag)
MC_METHOD_VERSION = "mc-v4(stationary-bootstrap,RC,SPA,StepM-studentized,StepSPA-diagnostic,MCS-Tmax,BY,alpha-spending-1/k^2,B>=5/alpha)"


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
    q to the user).  Recorded with every result.  The n^(1/3) RATE is the
    optimal one (Politis & White 2004 eq. (6): b_opt = (2G^2/D)^(1/3) n^(1/3));
    the constant 1 is ours — ``ppw_block_length`` estimates it from data."""
    return min(1.0, 1.0 / max(1.0, n ** (1.0 / 3.0)))


def _flat_top(t: float) -> float:
    """Flat-top lag window (Politis & White 2004, Sec. 3.2)."""
    t = abs(t)
    if t <= 0.5:
        return 1.0
    if t <= 1.0:
        return 2.0 * (1.0 - t)
    return 0.0


def ppw_block_length(x: Sequence[float]) -> float:
    """Expected block length of the STATIONARY bootstrap estimated from the
    data: Politis & White (2004) eq. (9), b = (2 G^2 / D_SB)^(1/3) n^(1/3),
    G = sum_{|k|<=M} lam(k/M) |k| R_k, with the corrected
    D_SB = 2 g(0)^2, g(0) = sum_{|k|<=M} lam(k/M) R_k (Patton, Politis & White
    2009, eq. (8)).  M = 2 m_hat, m_hat = the smallest m such that
    |rho_{m+k}| < c sqrt(log10(n)/n) for k = 1..K_N, c = 2,
    K_N = max(5, sqrt(log10 n)) (PW 2004 footnote c, rounded up to an integer).

    Not in the papers but in the authors' code (ppw.R, Patton/Racine 2008):
    lags searched up to ceil(sqrt(n)) + K_N; if no run of K_N insignificant
    lags exists, m_hat = largest significant lag (1 if none); M capped at
    that search range; b capped at ceil(min(3 sqrt(n), n/3)) and floored at 1.
    (ppw.R takes m_hat one lag later than footnote c; the footnote is followed.)
    """
    n = len(x)
    if n < 10:
        raise ValueError("too few observations for a block-length estimate")
    mu = _mean(x)
    u = [v - mu for v in x]
    r0 = sum(v * v for v in u) / n
    if r0 <= 0:
        return 1.0
    kn = max(5, int(math.ceil(math.sqrt(math.log10(n)))))
    mmax = int(math.ceil(math.sqrt(n))) + kn
    mmax = min(mmax, n - 1)

    def acov(k: int) -> float:
        return sum(u[t] * u[t + k] for t in range(n - k)) / n

    R = [r0] + [acov(k) for k in range(1, mmax + 1)]
    crit = 2.0 * math.sqrt(math.log10(n) / n)
    insig = [abs(R[k] / r0) < crit for k in range(1, mmax + 1)]     # insig[k-1] ~ lag k
    m_hat = None
    for m in range(1, mmax - kn + 1):       # smallest POSITIVE m (footnote c)
        if all(insig[m:m + kn]):            # lags m+1..m+kn insignificant
            m_hat = m
            break
    if m_hat is None:
        sig = [k for k in range(1, mmax + 1) if not insig[k - 1]]
        m_hat = max(sig) if sig else 1
    M = min(2 * m_hat, mmax)
    G = sum(_flat_top(k / M) * abs(k) * R[abs(k)] for k in range(-M, M + 1))
    g0 = sum(_flat_top(k / M) * R[abs(k)] for k in range(-M, M + 1))
    if g0 <= 0:
        return 1.0
    b = (2.0 * G * G / (2.0 * g0 * g0)) ** (1.0 / 3.0) * n ** (1.0 / 3.0)
    return max(1.0, min(b, math.ceil(min(3.0 * math.sqrt(n), n / 3.0))))


def ppw_q(series: Mapping[str, Sequence[float]]) -> float:
    """One stationary-bootstrap q = 1/b for a JOINT resample of several
    series.  The papers give b per series; taking the LONGEST of them (so no
    series' dependence is cut shorter than its own estimate) is our choice."""
    keys, _ = _check_matrix(series)
    return 1.0 / max(ppw_block_length(series[k]) for k in keys)


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


# ---------------------------------------------------------------------------
# Goetze & Kuensch (1996) studentized blockwise bootstrap (sample mean)
# ---------------------------------------------------------------------------

def _parzen(x: float) -> float:
    x = abs(x)
    if x <= 0.5:
        return 1.0 - 6.0 * x * x + 6.0 * x ** 3
    if x <= 1.0:
        return 2.0 * (1.0 - x) ** 3
    return 0.0


def gk_block_length(n: int, lag_window: str = "parzen") -> int:
    """Block length l = lag-window width.  GK (1996, end of Sec. 2) give only
    the ORDER: l = O(n^(1/4)) for rectangular weights, O(n^(1/3)) for weights
    with v(x) - 1 ~ -c x^2; the constant 1 is ours."""
    if lag_window not in ("parzen", "rectangular"):
        raise ValueError("lag_window must be 'parzen' or 'rectangular'")
    return max(2, int(round(n ** (0.25 if lag_window == "rectangular" else 1.0 / 3.0))))


def gk_lag_window_variance(d: Sequence[float], l: int, lag_window: str = "parzen") -> float:
    """GK (1996) eq. (3) for H = identity: sigma_hat^2 = sum_{k=0}^{l-1} w_k
    gamma_hat_k, gamma_hat_k = n^-1 sum_{j=1}^{n-l} (Y_j - Ybar)(Y_{j+k} - Ybar)
    (the same n - l terms for every lag, as printed), w_0 = 1, w_k = 2 v(k/l).
    ``rectangular``: v = 1, the lowest bias (n^(1/4) rate) but NOT guaranteed
    >= 0.  ``parzen``: v(x) - 1 ~ -6 x^2 and a valid (non-negative) kernel,
    the n^(1/3) case — the paper names only the condition, Parzen is our pick.
    The triangular v(x) = 1 - x is excluded by GK (second-order correctness
    fails)."""
    n = len(d)
    if not 1 <= l < n:
        raise ValueError("need 1 <= l < n")
    mu = _mean(d)
    u = [x - mu for x in d]
    total = 0.0
    for k in range(l):
        g = sum(u[j] * u[j + k] for j in range(n - l)) / n
        if k == 0:
            total += g
        else:
            total += 2.0 * (1.0 if lag_window == "rectangular" else _parzen(k / l)) * g
    return total


def gk_mean_of_bootstrap_mean(x: Sequence[float], l: int, circular: bool = False) -> float:
    """mu*_n = E*[Ybar*] for the truncated moving-block bootstrap of GK (1996)
    (start uniform on {0..n-l}, ceil(n/l) blocks cut to n values): position t
    of a resample has expectation m_{t mod l}, m_o = mean of x[s+o], s = 0..n-l.
    For n a multiple of l this is GK's weighted mean with weights
    min(j/l, 1, (n+1-j)/l).  Circular blocks (start uniform on {0..n-1},
    wrapped): mu*_n = Ybar_n exactly."""
    n = len(x)
    if circular:
        return sum(x) / n
    m_off = [sum(x[o:o + n - l + 1]) / (n - l + 1) for o in range(l)]
    return sum(m_off[t % l] for t in range(n)) / n


def gk_blockwise_t(x: Sequence[float], l: int, starts: Sequence[int],
                   mu_star: Optional[float] = None, circular: bool = False) -> float:
    """One draw of GK's studentized bootstrap statistic n^(1/2) (Ybar* - mu*_n)
    / sigma_hat* for the sample mean (H = identity) from the block starts
    ``starts`` (moving blocks of length l, ceil(n/l) of them, uniform on
    {0..n-l}, not wrapped; the resample is cut to n values).

    * mu*_n = E*[Ybar*], NOT Ybar_n: GK show centring at Ybar_n leaves a bias
      of O(l^(1/2) n^(-1/2)), larger than the skewness term, and second-order
      correctness is lost.
    * sigma_hat*^2 = eq. (5): n^-1 times the sum over pairs (i, j) inside the
      SAME bootstrap block of (Y*_i - Ybar*)(Y*_j - Ybar*) = the mean of the
      squared block sums.  Pairs across blocks are independent and left out; for
      linear H this gives E* sigma_hat*^2 = sigma*^2 (1 - 1/b) — a bias of the
      same order as that of sigma_hat_n^2 in (3), which is what GK require.
    Returns 0.0 when sigma_hat*^2 = 0."""
    n = len(x)
    ys: list[float] = []
    for s in starts:
        ys.extend(x[(s + o) % n] for o in range(l)) if circular else ys.extend(x[s:s + l])
    ys = ys[:n]
    ybar = sum(ys) / n
    mu_star = gk_mean_of_bootstrap_mean(x, l, circular) if mu_star is None else mu_star
    ss = 0.0
    for b0 in range(0, n, l):
        blk = ys[b0:b0 + l]
        dev = sum(blk) - len(blk) * ybar
        ss += dev * dev
    var = ss / n
    return math.sqrt(n) * (ybar - mu_star) / math.sqrt(var) if var > 0 else 0.0


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


class InsufficientBootstrapResolution(ValueError):
    """ADR 0078 (D-01): a bootstrap test cannot be run at level ``alpha`` with ``B``
    draws when alpha * (B + 1) < 1 — the (1 - alpha) quantile index is clamped to
    the largest draw and the real level stays near 1/(B + 1) however small alpha
    is.  Alpha spending (6/pi^2/k^2) drives alpha below 1/B from the 4th batch on a
    window at B = 1000; the test is refused instead of silently running at ~0.001."""


#: draws in the tail beyond the critical value required by :func:`bootstrap_B_for`
MIN_TAIL_DRAWS = 5


def bootstrap_B_for(alpha: float, base_B: int) -> int:
    """B for a test at level ``alpha``: the configured B, raised so that at least
    MIN_TAIL_DRAWS draws lie beyond the critical value (ADR 0078)."""
    return max(int(base_B), int(math.ceil(MIN_TAIL_DRAWS / alpha)) + 1)


def _require_resolution(alpha: float, B: int) -> None:
    if alpha * (B + 1) < 1.0:
        raise InsufficientBootstrapResolution(
            f"alpha {alpha:.3g} needs B >= {int(math.ceil(1.0 / alpha))} bootstrap draws, got {B} "
            "(the critical value would be the largest draw for any smaller alpha)")


def stepm(series: Mapping[str, Sequence[float]], *, alpha: float, B: int = 1000,
          q: Optional[float] = None, seed: int = 0, studentize: str = "fixed",
          block_len: Optional[int] = None, lag_window: str = "parzen",
          blocks: str = "moving") -> dict:
    """Romano & Wolf (2005) studentized StepM (Algorithm 4.1; RSW2008 Alg. 4.3
    with k=1) for the one-sided hypotheses H0_k: E d_k <= 0.  Difference from
    the papers' bootstrap step (RW2005 eq. (11), RSW2008 Alg. A.3): sigma* is
    NOT recomputed on each resample, see the returned note.  Returns the rejected keys —
    strategies whose mean is shown > 0 with asymptotic FWER <= alpha.

    ``studentize="bootstrap_t"`` is the papers' form: sigma*_k is recomputed
    on every resample by "applying the same algorithm to the bootstrap data
    matrix" (RW2005 Sec. 4.1) — here the same Hansen variance.  Synthetic
    FWER (ADR 0027) is closer to alpha with it; it costs B x m variance
    evaluations.  Not the default: frozen procedures keep "fixed".

    ``studentize="gk_block"``: the blockwise bootstrap of Goetze & Kuensch
    (1996) instead of the stationary bootstrap — moving blocks of length
    ``block_len`` (default ``gk_block_length``), the draw centred at the
    bootstrap mean mu*_n and studentized with the within-block estimator
    (eq. (5)), the observed statistic studentized with the lag-window
    estimator (eq. (3), ``lag_window`` 'parzen' or 'rectangular').  GK prove
    second-order correctness of the studentized bootstrap distribution for a
    smooth function of means (here the mean); RW2005's StepM uses it as the
    bootstrap-t step, which is an application, not something GK treat.
    ``blocks="circular"``: wrapped blocks (start uniform on {0..n-1}, mu*_n =
    the sample mean), the variant Romano & Wolf's own simulations use (2003
    working paper Sec. 7.2; GK's argument carries over, RW 2003 Sec. 4.1).
    ``q`` is not used in this mode."""
    if blocks not in ("moving", "circular"):
        raise ValueError("blocks must be 'moving' or 'circular'")
    if studentize not in ("fixed", "bootstrap_t", "gk_block"):
        raise ValueError("studentize must be 'fixed', 'bootstrap_t' or 'gk_block'")
    keys, n = _check_matrix(series)
    q = q or default_q(n)
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    _require_resolution(alpha, B)
    rng = random.Random(seed)
    dbar = {k: _mean(series[k]) for k in keys}
    sig = {}
    gk_l = None
    if studentize == "gk_block":
        gk_l = block_len or gk_block_length(n, lag_window)
        if not 2 <= gk_l < n:
            raise ValueError("block_len must satisfy 2 <= l < n")
    for k in keys:
        var = (gk_lag_window_variance(series[k], gk_l, lag_window) if gk_l
               else bootstrap_variance(series[k], q))
        if var <= 0:
            raise ValueError(f"non-positive variance estimate for {k}"
                             + (" (rectangular weights can go negative; use 'parzen')" if gk_l else ""))
        sig[k] = math.sqrt(var / n)
    z = {k: dbar[k] / sig[k] for k in keys}
    # joint bootstrap draws, reused across steps (same resamples for every k)
    draws: list[dict] = []
    circ = blocks == "circular"
    mu_star = {k: gk_mean_of_bootstrap_mean(series[k], gk_l, circ) for k in keys} if gk_l else {}
    for _ in range(B):
        if gk_l:
            starts = [rng.randrange(n if circ else n - gk_l + 1) for _ in range(-(-n // gk_l))]
            draws.append({k: gk_blockwise_t(series[k], gk_l, starts, mu_star[k], circ) for k in keys})
            continue
        ix = stationary_bootstrap_indices(n, q, rng)
        d = {}
        for k in keys:
            xs = [series[k][i] for i in ix]
            if studentize == "fixed":
                d[k] = (_mean(xs) - dbar[k]) / sig[k]
            else:
                vs = bootstrap_variance(xs, q)
                d[k] = (_mean(xs) - dbar[k]) / math.sqrt(vs / n) if vs > 0 else 0.0
        draws.append(d)
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
    if studentize == "gk_block":
        return {"method": "romano_wolf2005_stepm_gk1996_blockwise", "alpha": alpha,
                "rejected": sorted(rejected), "z": z, "steps": steps, "B": B,
                "block_len": gk_l, "lag_window": lag_window, "blocks": blocks, "n": n, "m": len(keys),
                "seed": seed,
                "note": "Goetze-Kuensch 1996: moving blocks of length l, draw centred at the "
                        "bootstrap mean mu*_n, studentized by the within-block estimator "
                        "(eq. (5)); observed statistic by the lag-window estimator (eq. (3))"}
    if studentize == "bootstrap_t":
        return {"method": "romano_wolf2005_stepm_bootstrap_t", "alpha": alpha,
                "rejected": sorted(rejected), "z": z, "steps": steps, "B": B, "q": q,
                "n": n, "m": len(keys), "seed": seed,
                "note": "sigma* recomputed on each resample with the same Hansen-2005 "
                        "variance (RW2005 eq. (11), Sec. 4.1)"}
    return {"method": "romano_wolf2005_stepm_studentized", "alpha": alpha,
            "rejected": sorted(rejected), "z": z, "steps": steps, "B": B, "q": q,
            "n": n, "m": len(keys), "seed": seed,
            "note": "sigma from Hansen-2005 bootstrap-population variance, held fixed "
                    "across resamples: RW2005 Assumption 4.1 / RSW2008 Assumption 4.2 only "
                    "need sqrt(T)sigma and sqrt(T)sigma* to share one limit; Hsu-Hsu-Kuan "
                    "2010 fn. 6 do the same (no per-resample sigma* formula exists for the "
                    "stationary bootstrap in RW2005/RSW2008)"}


def step_spa(series: Mapping[str, Sequence[float]], *, alpha: float, B: int = 1000,
             q: Optional[float] = None, seed: int = 0) -> dict:
    """Hsu-Hsu-Kuan (2010) Step-SPA (studentized) for H0_k: E d_k <= 0.
    Diagnostic only — see module docstring."""
    keys, n = _check_matrix(series)
    q = q or default_q(n)
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    _require_resolution(alpha, B)
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
    _require_resolution(alpha, B)
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
    """alpha for the k-th (1-based) use of one data window; sum <= alpha_total.
    Tian & Ramdas (2021) Alpha-Spending with gamma_k = 6/(pi^2 k^2)."""
    if k < 1:
        raise ValueError("k is 1-based")
    return alpha_total * 6.0 / (math.pi ** 2) / (k * k)


def fallback_alpha(rejected: Sequence[bool], alpha_total: float = 0.05) -> float:
    """Online Fallback-1 (Tian & Ramdas 2021 eq. (5)): alpha_1 = alpha*gamma_1,
    alpha_i = alpha*gamma_i + R_{i-1} alpha_{i-1}, gamma_i = 6/(pi^2 i^2).
    ``rejected`` holds R_1..R_{i-1} of the earlier tests; returns alpha_i.
    FWER <= alpha under arbitrary dependence (their Prop. 2).  NOT used by any
    gate — it would raise a later test's level after a rejection, which needs
    a new preregistration."""
    a = spending_alpha(1, alpha_total)
    for i, r in enumerate(rejected, start=2):
        a = spending_alpha(i, alpha_total) + (a if r else 0.0)
    return a


# ---------------------------------------------------------------------------
# Monte Carlo tests (Dufour 2006)
# ---------------------------------------------------------------------------

def mc_pvalue(s0: float, sims: Sequence[float], rng: Optional[random.Random] = None) -> float:
    """Dufour (2006) Monte Carlo p-value p_N = (N G_N(s0) + 1)/(N + 1), eq.
    (2.16), with the randomised tie-break of Sec. 2.2 / eq. (2.35) when a
    ``rng`` is given (each statistic gets an independent U(0,1); ties in S are
    ordered by U).  Exact, P[p <= a] = I[a(N+1)]/(N+1), when (S_0..S_N) are
    EXCHANGEABLE under H0 (Prop. 2.2/2.4) — a bootstrap from the observed
    data does not give that."""
    n = len(sims)
    if n < 1:
        raise ValueError("no simulated statistics")
    if rng is None:
        ge = sum(1 for s in sims if s >= s0)
    else:
        u0 = rng.random()
        ge = sum(1 for s in sims if s > s0 or (s == s0 and rng.random() >= u0))
    return (ge + 1) / (n + 1)


def mmc_pvalue(s0: float, sims_by_nuisance: Mapping[object, Sequence[float]],
               rng_seed: Optional[int] = None) -> dict:
    """Maximized Monte Carlo p-value (Dufour 2006 Prop. 4.1, eq. (4.21)):
    sup over the nuisance set of p_N(s0 | y).  Level <= a in finite samples
    when the sup is over the WHOLE null set and S_i(y) = S[g(y, u_i)] uses the
    same draws u_i for every y.  Here the sup is over the values supplied (a
    grid): the paper does not cover an approximate maximisation, so a grid
    that misses the worst y can over-reject."""
    if not sims_by_nuisance:
        raise ValueError("empty nuisance set")
    best_y, best_p = None, -1.0
    for y, sims in sims_by_nuisance.items():
        rng = random.Random(rng_seed) if rng_seed is not None else None
        p = mc_pvalue(s0, sims, rng)
        if p > best_p:
            best_y, best_p = y, p
    return {"p": best_p, "argmax": best_y, "grid_size": len(sims_by_nuisance)}


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
