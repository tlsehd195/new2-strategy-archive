"""Synthetic error-rate checks for the two papers read from PDF on 2026-10-22
(ADR 0028): Goetze & Kuensch (1996) and Waudby-Smith & Ramdas (2010.09686 v7).

  gk     StepM family-wise error rate (and power) with the blockwise bootstrap
         of Goetze-Kuensch (moving blocks, draw centred at the bootstrap mean,
         within-block studentization eq. (5), lag-window sigma eq. (3)) against
         the gate's stationary bootstrap with a fixed sigma and the
         bootstrap-t of ADR 0027.  Same data generators as stepm_block_sim.py.
  bets   Size and power of the betting rules of WS-R appendix B (GRAPA,
         aGRAPA, LBOW, ONS, gKelly) and of PrPl-EB in its two forms
         (hedged-capital betting product, Theorem 2 exponential), against the
         ADR 0027 rules, with the block design of evalue_walkforward_sim.py.

Everything is synthetic.  No market data, validation or locked window, no IW1;
no criterion is changed.

    cd run && python3 pdf_check_sim.py --parts gk --out audit/evidence_pdf_check_20261022/gk_results.json
    cd run && python3 pdf_check_sim.py --parts bets --out audit/evidence_pdf_check_20261022/bets_results.json
    cd run && python3 pdf_check_sim.py --quick
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
import evalue_walkforward_sim as ev  # noqa: E402
import stats_mc  # noqa: E402
import stepm_block_sim as sb  # noqa: E402

ALPHA = 0.05


# ---------------------------------------------------------------- GK (numpy)
def gk_lag_var(x, l, lag_window):
    """gk_lag_window_variance for every column (eq. (3): n - l terms per lag)."""
    n = x.shape[0]
    u = x - x.mean(axis=0)
    total = (u[: n - l] * u[: n - l]).sum(axis=0) / n
    for k in range(1, l):
        w = 2.0 * (1.0 if lag_window == "rectangular" else stats_mc._parzen(k / l))
        total = total + w * (u[: n - l] * u[k: n - l + k]).sum(axis=0) / n
    return total


def gk_t_draws(x, l, starts, circular=False):
    """GK studentized draws for every column from given block starts (B, nb);
    vectorised ``stats_mc.gk_blockwise_t``."""
    n, s = x.shape
    B, nb = starts.shape
    idx = (starts[:, :, None] + np.arange(l)[None, None, :]).reshape(B, nb * l)[:, :n]
    if circular:
        idx = idx % n
    xs = x[idx]                                                       # (B, n, s)
    ybar = xs.mean(axis=1)
    if circular:
        mu_star = x.mean(axis=0)
    else:
        m_off = np.stack([x[o: o + n - l + 1].mean(axis=0) for o in range(l)])   # (l, s)
        mu_star = m_off[np.arange(n) % l].mean(axis=0)                # (s,)
    dev = xs - ybar[:, None, :]
    pad = np.zeros((B, nb * l - n, s))
    blk = np.concatenate([dev, pad], axis=1).reshape(B, nb, l, s).sum(axis=2)
    var = (blk * blk).sum(axis=1) / n
    ok = var > 0
    t = np.sqrt(n) * (ybar - mu_star) / np.sqrt(np.where(ok, var, 1.0))
    return np.where(ok, t, 0.0)


def stepm_gk(x, l, rng, B, lag_window="parzen", alpha=ALPHA, circular=False):
    n, s = x.shape
    var = gk_lag_var(x, l, lag_window)
    if (var <= 0).any():
        return None                                    # rectangular weights went negative
    z = np.sqrt(n) * x.mean(axis=0) / np.sqrt(var)
    nb = -(-n // l)
    starts = rng.integers(0, n if circular else n - l + 1, (B, nb))
    draws = gk_t_draws(x, l, starts, circular)
    rejected = np.zeros(s, bool)
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


def part_gk(rng, reps, B, circ_only=False, dgps=None, rowdir=None):
    n = sb.N_DAYS
    l_par = stats_mc.gk_block_length(n, "parzen")
    l_rect = stats_mc.gk_block_length(n, "rectangular")
    rows = []
    for kind in (dgps or ("iid_t4", "garch_corr", "garch_ar02", "garch_ar06")):
        for n_alt in (0, 3):
            rowfile = rowdir / f"{'circ_' if circ_only else ''}{kind}_{n_alt}.json" if rowdir else None
            if rowfile is not None and rowfile.exists():       # container restarts kill long runs: resume
                rows.append(json.loads(rowfile.read_text()))
                continue
            t0 = time.time()
            names = ["current_fixed", "boot_t_stationary", "gk_parzen", "gk_rect", "gk_parzen_l15"]
            if circ_only:
                names = ["gk_parzen_circular", "gk_parzen_circular_l15"]
            fwer = {k: 0 for k in names}
            power = {k: 0.0 for k in names}
            used = {k: 0 for k in names}
            for _ in range(reps):
                x, mu = sb.make_data(rng, kind, n_alt=n_alt)
                ac = sb.acov_all(x)
                q = stats_mc.default_q(n)
                if circ_only:
                    res = {"gk_parzen_circular": stepm_gk(x, l_par, rng, B, "parzen", circular=True),
                           "gk_parzen_circular_l15": stepm_gk(x, 15, rng, B, "parzen", circular=True)}
                else:
                    res = {
                        "current_fixed": sb.stepm_np(x, q, rng, B, ac=ac)[0],
                        "boot_t_stationary": sb.stepm_boot_t(x, q, rng, B, ac=ac),
                        "gk_parzen": stepm_gk(x, l_par, rng, B, "parzen"),
                        "gk_rect": stepm_gk(x, l_rect, rng, B, "rectangular"),
                        "gk_parzen_l15": stepm_gk(x, 15, rng, B, "parzen"),
                    }
                for k, rej in res.items():
                    if rej is None:
                        continue
                    used[k] += 1
                    fwer[k] += bool(rej[mu == 0].any())
                    if n_alt:
                        power[k] += rej[mu > 0].mean()
            row = {"dgp": kind, "n_alt": n_alt, "reps": reps, "B": B,
                   "block_len": {"gk_parzen": l_par, "gk_rect": l_rect, "gk_parzen_l15": 15,
                                 "stationary_mean_block": 1.0 / stats_mc.default_q(n)},
                   "fwer": {k: fwer[k] / max(used[k], 1) for k in names},
                   "usable_reps": used,
                   "mc_se_at_alpha": math.sqrt(ALPHA * (1 - ALPHA) / reps)}
            if n_alt:
                row["avg_power_alt"] = {k: power[k] / max(used[k], 1) for k in names}
            row["secs"] = round(time.time() - t0, 1)
            rows.append(row)
            if rowfile is not None:
                rowdir.mkdir(parents=True, exist_ok=True)
                rowfile.write_text(json.dumps(row))
            print("gk", kind, n_alt, row["fwer"], flush=True)
    return {"n_days": n, "strategies": sb.S, "alpha": ALPHA, "rows": rows}


# ---------------------------------------------------------------- betting rules
def rules(Z, sz0, alpha, thr_alpha, with_grapa=True):
    r = {
        "plugin_adr0027": ev.log_e_plugin(Z, sz0),
        "agrapa_wsr_B3": ev.log_e_agrapa(Z, sz0),
        "lbow_B4": ev.log_e_lbow(Z, sz0),
        "ons_B5": ev.log_e_ons(Z),
        "ons_as_printed": ev.log_e_ons(Z, as_printed=True),
        "gkelly_B6": ev.log_e_gkelly(Z),
        "prpl_betting_hedged_K+": ev.log_e_prpl_eb(Z, sz0, alpha=alpha),
        "prpl_eb_exp_thm2": ev.log_e_prpl_eb_exp(Z, sz0, alpha=alpha),
    }
    if with_grapa:
        r["grapa_B2"] = ev.log_e_grapa(Z)
    return r


def part_bets(rng, n, nb, quick):
    L, B = ev.L_MULT * ev.BLOCK_SD, ev.B_MULT * ev.BLOCK_SD
    c = ev.GAP_C * ev.BLOCK_SD
    sz0 = ev.BLOCK_SD / (B + c)
    horizons = (12, 24, 36, 60)
    out = {"n_paths": n, "blocks": nb, "L": L, "B": B, "gap_c": c, "type1": [], "power": {}}
    # size at the preregistered v2 alpha (0.0304) on every null process
    cases = [("iid_normal", 0.0, 0.0), ("garch_t4", 0.0, 0.0), ("gjr_skewt", 0.0, 0.0),
             ("regime_t4", 0.0, 0.0), ("gjr_skewt3", 0.0, 0.0), ("garch_t4", -0.5, 0.0)]
    thr = math.log(1 / ev.ALPHA)
    for kind, sr, phi in cases:
        t0 = time.time()
        X, _, _ = ev.direct_blocks(rng, kind, n, nb, sr, L, B, phi)
        Z = (X - c) / (B + c)
        res = {k: ev.summarise(ev.first_cross(v, thr), horizons)
               for k, v in rules(Z, sz0, ev.ALPHA, ev.ALPHA).items()}
        out["type1"].append({"dgp": kind, "sr_ann": sr, "alpha": ev.ALPHA, "rules": res,
                             "secs": round(time.time() - t0, 1)})
        print("bets type1", kind, sr, {k: v[f"reject_by_{nb}"] for k, v in res.items()}, flush=True)
    # power (and size) at the v3 split alpha, GARCH t4
    a3 = 0.0091
    thr3 = math.log(1 / a3)
    for sr in (0.0, 1.0, 2.0, 3.0, 4.0):
        X, _, _ = ev.direct_blocks(rng, "garch_t4", n, nb, sr, L, B)
        Z = (X - c) / (B + c)
        res = {k: ev.summarise(ev.first_cross(v, thr3), horizons)
               for k, v in rules(Z, sz0, a3, a3).items()}
        out["power"][f"sr{sr:g}"] = {"alpha": a3, "dgp": "garch_t4", "rules": res}
        print("bets power", sr, {k: (v["reject_by_24"], v["reject_by_36"]) for k, v in res.items()}, flush=True)
    return out


def hash_dgp(s):
    return sum(ord(c) * 31 ** i for i, c in enumerate(s)) % 100003


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--parts", default="gk,bets")
    ap.add_argument("--rowdir", type=Path, default=None, help="checkpoint one JSON per gk row; resumes")
    ap.add_argument("--dgps", default=None, help="comma list for gk parts (parallel runs)")
    a = ap.parse_args(argv)
    q = a.quick
    out = {"schema": "pdf_check_sim_v1", "seed": a.seed, "quick": q, "synthetic_only": True}
    for i, part in enumerate(a.parts.split(",")):
        rng = np.random.default_rng([a.seed, 28, i] + ([hash_dgp(a.dgps)] if a.dgps else []))
        t0 = time.time()
        if part == "gk":
            out[part] = part_gk(rng, 30 if q else 600, 199 if q else 499,
                                dgps=a.dgps.split(",") if a.dgps else None,
                                rowdir=a.rowdir)
        elif part == "gk_circ":
            out[part] = part_gk(rng, 30 if q else 800, 199 if q else 499, circ_only=True)
        elif part == "bets":
            out[part] = part_bets(rng, 300 if q else 10000, 60 if q else 60, q)
        else:
            raise ValueError(part)
        out[part]["secs"] = round(time.time() - t0, 1)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    main()
