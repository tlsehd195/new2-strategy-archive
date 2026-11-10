"""Reproducible power analysis of the evolve pipeline's confirmatory gates (ADR 0064).

Synthetic data only — no market file, validation, locked, IW0/IW1 or paper
window is read.  Every number comes from the repository's own test functions
(``stats_mc.stepm``, ``engine_lib.deflated_sharpe``, the stationary-bootstrap CI
of ``pipeline.robustness``) with the alphas, thresholds and sample sizes of a
promotion config, so a change of the gates changes the result.

Model of one strategy's daily net series (bp-free units): ``x_t = d + e_t`` with
``d = SR_annual / sqrt(365)`` (24/7 market) and ``e_t`` an AR(1) process with
Student-t innovations scaled to unit variance (heavy tails, serial dependence
as in real daily P&L).  Stages, each simulated ``n_sims`` times per scenario:

* ``V``  validation StepM on a batch of ``batch_m`` members (one true edge,
  ``batch_m - 1`` zero-edge members) over ``validation_days``, at the
  per-window alpha ``spending_alpha(k, alpha_total_per_search / windows)``.
* ``CI`` the robustness bootstrap CI of the same true member's validation
  series: lower bound > ``ci_lower_bound_min_bp``.
* ``DSR`` the DEV deflated Sharpe of the true edge against ``dev_trials``
  zero-edge trials over ``dev_days`` (their SR dispersion is simulated).
* ``ALL`` = V and CI on the same draw, times the independent DSR pass rate
  (DEV and validation are disjoint windows).

Type I error: SR 0 → share of draws where V/CI/DSR pass (FWER of V uses every
null member).  Power, Type II (= 1 - power), the minimum detectable SR at 80%
power and the validation days needed for 80% StepM power are reported per
scenario (symbol window, regime half).  FDR: with one true edge per batch the
expected false discoveries / discoveries is reported from the same draws.

Usage: ``python3 power_analysis.py [--config config/power_analysis_v1.json]
[--out result.json]``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from pathlib import Path
from typing import Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config" / "power_analysis_v1.json"
POWER_VERSION = "power-analysis-v1"


def _series(rng: random.Random, n: int, sr_annual: float, phi: float, df: float) -> list[float]:
    """Unit-variance AR(1) Student-t noise plus a constant daily edge."""
    d = sr_annual / math.sqrt(365.0)
    scale = math.sqrt((df - 2.0) / df) if df > 2 else 1.0
    innov_sd = math.sqrt(max(1e-12, 1.0 - phi * phi))
    out, e = [], 0.0
    for _ in range(n):
        # Student-t via normal / sqrt(chi2/df)
        z = rng.gauss(0.0, 1.0)
        chi = sum(rng.gauss(0.0, 1.0) ** 2 for _ in range(int(df))) if df < 30 else df
        t = z / math.sqrt(chi / df) * scale if df < 30 else z
        e = phi * e + innov_sd * t
        out.append(d + e)
    return out


def _moments(x: Sequence[float]) -> tuple[float, float, float, float]:
    n = len(x)
    mu = sum(x) / n
    sd = (sum((v - mu) ** 2 for v in x) / (n - 1)) ** 0.5
    sk = sum(((v - mu) / sd) ** 3 for v in x) / n
    ku = sum(((v - mu) / sd) ** 4 for v in x) / n
    return mu, sd, sk, ku


def _ci_lower(x: Sequence[float], *, level: float, B: int, seed: int) -> float:
    from stats_mc import default_q, stationary_bootstrap_indices
    rng = random.Random(seed)
    q = default_q(len(x))
    ms = sorted(sum(x[i] for i in stationary_bootstrap_indices(len(x), q, rng)) / len(x) for _ in range(B))
    return ms[int((1 - level) / 2 * B)]


def _dsr_pass(rng: random.Random, sr_annual: float, n_dev: int, trials: int, phi: float,
              df: float, min_dsr: float) -> tuple[bool, float]:
    from engine_lib import deflated_sharpe
    x = _series(rng, n_dev, sr_annual, phi, df)
    mu, sd, sk, ku = _moments(x)
    # the other trials: zero-edge SR estimates; their dispersion is what DSR deflates by
    null_sr = [rng.gauss(0.0, 1.0 / math.sqrt(n_dev)) for _ in range(trials - 1)]
    all_sr = null_sr + [mu / sd]
    m = sum(all_sr) / len(all_sr)
    var_sr = sum((v - m) ** 2 for v in all_sr) / (len(all_sr) - 1)
    d = deflated_sharpe(mu / sd, n_dev, sk, ku, trials, var_sr)
    return (math.isfinite(d) and d >= min_dsr), d


def _dsr_best_of_null(rng: random.Random, n_dev: int, trials: int, min_dsr: float) -> bool:
    """Type I of the DSR gate as a SELECTION gate: the best of ``trials`` zero-edge
    strategies (normal returns; its moments are those of a normal series)."""
    from engine_lib import deflated_sharpe
    srs = [rng.gauss(0.0, 1.0 / math.sqrt(n_dev)) for _ in range(trials)]
    m = sum(srs) / len(srs)
    var_sr = sum((v - m) ** 2 for v in srs) / (len(srs) - 1)
    d = deflated_sharpe(max(srs), n_dev, 0.0, 3.0, trials, var_sr)
    return math.isfinite(d) and d >= min_dsr


def simulate(cfg: Mapping, promo: Mapping, *, progress=None) -> dict:
    from stats_mc import default_q, spending_alpha, stepm
    from strategy_spec import SUPPORTED_SYMBOLS
    rob, val = promo["robustness"], promo["validation"]
    alpha_total = float(val.get("alpha_total_per_search", val.get("alpha_total_per_window", 0.05)))
    windows = len(SUPPORTED_SYMBOLS) if "alpha_total_per_search" in val else 1
    phi, df = float(cfg["ar1_phi"]), float(cfg["t_df"])
    out = {"scenarios": []}
    for sc in cfg["scenarios"]:
        k = int(sc.get("batch_index", 1))
        a_w = spending_alpha(k, alpha_total / windows)
        n_val, m = int(sc["validation_days"]), int(cfg["batch_m"])
        rows = []
        for sr in cfg["sr_grid"]:
            seed = int(hashlib.sha256(f"{cfg['seed']}:{sc['name']}:{sr}".encode()).hexdigest()[:8], 16)
            rng = random.Random(seed)
            v_pass = ci_pass = vc_pass = dsr_pass = 0
            false_disc = disc = any_null = dsr_best_null = 0
            for i in range(int(cfg["n_sims"])):
                series = {"true": _series(rng, n_val, sr, phi, df)}
                for j in range(m - 1):
                    series[f"null{j}"] = _series(rng, n_val, 0.0, phi, df)
                res = stepm(series, alpha=a_w, B=int(cfg["stepm_B"]), q=default_q(n_val),
                            seed=seed + i, studentize=val.get("studentize", "fixed"))
                rej = set(res["rejected"])
                v_ok = "true" in rej and sr > 0
                nulls = len(rej - {"true"}) + (1 if ("true" in rej and sr == 0) else 0)
                any_null += nulls > 0
                false_disc += nulls
                disc += len(rej)
                lo = _ci_lower(series["true"], level=float(rob["ci_level"]),
                               B=int(cfg["ci_B"]), seed=seed + i)
                c_ok = lo > float(rob.get("ci_lower_bound_min_bp", 0.0))
                v_pass += v_ok
                ci_pass += c_ok
                vc_pass += v_ok and c_ok
                dsr_pass += _dsr_pass(rng, sr, int(cfg["dev_days"]), int(cfg["dev_trials"]), phi, df,
                                      float(rob.get("min_dsr", 0.95)))[0]
                if sr == 0:
                    dsr_best_null += _dsr_best_of_null(rng, int(cfg["dev_days"]), int(cfg["dev_trials"]),
                                                       float(rob.get("min_dsr", 0.95)))
            n = int(cfg["n_sims"])
            row = {"sr_annual": sr, "n_sims": n, "seed": seed,
                   "power_validation_stepm": v_pass / n, "pass_ci": ci_pass / n,
                   "pass_dsr": dsr_pass / n, "power_all": (vc_pass / n) * (dsr_pass / n),
                   "fwer_validation": any_null / n,
                   "fdr_validation": (false_disc / disc) if disc else 0.0}
            if sr == 0:
                row["pass_dsr_best_of_null"] = dsr_best_null / n
            row["type2_all"] = 1.0 - row["power_all"]
            rows.append(row)
            if progress:
                progress(sc["name"], row)
        mde = next((r["sr_annual"] for r in rows if r["sr_annual"] > 0 and r["power_all"] >= 0.8), None)
        # validation days for 80% one-sided power of a single mean test at the Bonferroni
        # bound alpha_w / m (normal approximation, AR(1) variance inflation (1+phi)/(1-phi))
        from engine_lib import norm_ppf
        infl = (1 + phi) / (1 - phi)
        z = norm_ppf(1 - a_w / m) + norm_ppf(0.8)
        need = {str(sr): math.ceil(z * z * infl / (sr / math.sqrt(365.0)) ** 2)
                for sr in cfg["sr_grid"] if sr > 0}
        null = next((r for r in rows if r["sr_annual"] == 0), None)
        out["scenarios"].append({
            "name": sc["name"], "validation_days": n_val, "batch_index": k, "alpha_window": a_w,
            "batch_m": m, "rows": rows,
            "type1": ({"fwer_validation": null["fwer_validation"], "pass_ci_null": null["pass_ci"],
                       "pass_dsr_single_null": null["pass_dsr"],
                       "pass_dsr_best_of_null": null["pass_dsr_best_of_null"]} if null else None),
            "min_detectable_sr_at_80pct_all_gates": mde,
            "validation_days_for_80pct_stepm_power_normal_approx": need,
            "status": "OK" if mde is not None else "UNDETERMINED_POWER"})
    return out


def run(config_path: Path = DEFAULT_CONFIG, *, promotion_path: Optional[Path] = None,
        progress=None) -> dict:
    import pipeline
    raw = Path(config_path).read_bytes()
    cfg = json.loads(raw)
    if cfg.get("schema") != "power_analysis_config_v1":
        raise ValueError(f"{config_path}: not a power_analysis_config_v1 file")
    promo, promo_hash = pipeline.load_promotion_config(promotion_path)
    t0 = time.time()
    res = simulate(cfg, promo, progress=progress)
    return {"version": POWER_VERSION, "config": cfg,
            "config_sha256": hashlib.sha256(raw).hexdigest(),
            "promotion_config": {"version": promo.get("version"), "hash": promo_hash},
            "python": sys.version.split()[0], "runtime_seconds": round(time.time() - t0, 1),
            "data": "synthetic only (no market data read)", **res}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--promotion-config", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    rep = run(a.config, promotion_path=a.promotion_config,
              progress=lambda s, r: print(f"{s} SR {r['sr_annual']}: V {r['power_validation_stepm']:.2f} "
                                          f"CI {r['pass_ci']:.2f} DSR {r['pass_dsr']:.2f} "
                                          f"ALL {r['power_all']:.3f} FWER {r['fwer_validation']:.2f}",
                                          file=sys.stderr, flush=True))
    txt = json.dumps(rep, indent=1, sort_keys=True)
    if a.out:
        a.out.write_text(txt + "\n")
    print(txt if not a.out else json.dumps({k: rep[k] for k in ("version", "config_sha256",
                                                                   "promotion_config", "runtime_seconds")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
