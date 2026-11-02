"""Effective number of independent symbols, measured on DEV S1 only (ADR 0058).

Reads ONLY 2021-01-01..2023-09-18 bars of the alt archive (the S1 selection window,
already spent as DEV).  Computes daily close-to-close returns, the correlation
matrix, and:
  rho_bar   mean pairwise correlation
  N_pr      participation ratio (sum lam)^2 / sum lam^2 of the correlation matrix
  N_rho     N / (1 + (N-1) rho_bar)       (equal-correlation equivalent)
for raw returns and for returns after removing the equal-weight market return
(residual = what a market-neutral / cross-sectional edge lives on).
Then the evidence-mass table: M = years * N_eff, needed M for 80% power.
python3 -I neff_s1.py --alts DIR --out results.json [--spot-dq _dq_pre2020_spot.jsonl]
"""
from __future__ import annotations
import argparse, glob, json, math
from pathlib import Path
import numpy as np

S1_LO, S1_HI = "2021-01-01", "2023-09-18"
ALTS = ["ADA", "AVAX", "BCH", "BNB", "DOGE", "LINK", "LTC", "NEAR", "SOL", "TRX", "UNI", "XLM", "XMR", "XRP", "ZEC"]


def daily_close(alts: Path, sym: str) -> dict[str, float]:
    last: dict[str, tuple[int, float]] = {}
    for p in sorted(glob.glob(str(alts / "klines" / f"{sym}USDT-15m-20*.csv"))):
        ym = p.rsplit("-15m-", 1)[1][:7]
        if not (S1_LO[:7] <= ym <= S1_HI[:7]):
            continue  # S2/validation/paper months are never opened
        for line in open(p):
            f = line.split(",")
            if not f[0].isdigit():
                continue  # header row
            t = int(f[0]) // 1000
            d = np.datetime64(t, "s").astype("datetime64[D]").astype(str)
            if not (S1_LO <= d <= S1_HI):
                continue
            if d not in last or t > last[d][0]:
                last[d] = (t, float(f[4]))
    return {d: v[1] for d, v in last.items()}


def neff(R: np.ndarray) -> dict:
    C = np.corrcoef(R, rowvar=False)
    n = C.shape[0]
    lam = np.clip(np.linalg.eigvalsh(C), 0, None)
    rho = (C.sum() - n) / (n * (n - 1))
    return {"n": n, "rho_bar": float(rho), "N_pr": float(lam.sum() ** 2 / (lam ** 2).sum()),
            "N_rho": float(n / (1 + (n - 1) * rho)), "lam1_share": float(lam.max() / lam.sum())}


def zsum(alpha: float, power: float = 0.8) -> float:
    from statistics import NormalDist
    nd = NormalDist()
    return nd.inv_cdf(1 - alpha) + nd.inv_cdf(power)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--alts", required=True); ap.add_argument("--out", required=True); ap.add_argument("--spot-dq", default="")
    a = ap.parse_args(); alts = Path(a.alts)
    px = {s: daily_close(alts, s) for s in ALTS}
    days = sorted(set.intersection(*[set(v) for v in px.values()]))
    P = np.array([[px[s][d] for s in ALTS] for d in days])
    R = np.diff(np.log(P), axis=0)
    out = {"window": [S1_LO, S1_HI], "days": len(days), "symbols": ALTS}
    out["daily_raw"] = neff(R)
    Rres = R - R.mean(axis=1, keepdims=True)
    # the equal-weight residual has rank n-1; add tiny jitter-free handling via correlation of residuals
    rr_ = neff(Rres + 1e-12 * np.random.default_rng(0).standard_normal(Rres.shape))
    # rho_bar / N_rho of a market-removed matrix is -1/(n-1) by construction: not meaningful. Keep the
    # participation ratio and express it per rank (n-1 = maximum possible).
    out["daily_residual"] = {"n": rr_["n"], "N_pr": rr_["N_pr"], "N_pr_per_rank": rr_["N_pr"] / (rr_["n"] - 1), "lam1_share": rr_["lam1_share"]}
    W = np.array([R[i:i + 7].sum(axis=0) for i in range(0, len(R) - 6, 7)])
    out["weekly_raw"] = neff(W)
    # per half-year stability of rho_bar
    halves = {}
    for i in range(0, len(R) - 90, 182):
        halves[days[i + 1]] = neff(R[i:i + 182])["rho_bar"]
    out["rho_bar_by_half_year"] = halves
    # saturation: expected N_eff when only k of 15 symbols are used (all subsets sampled)
    rng = np.random.default_rng(1)
    sat = {}
    for k in (2, 4, 6, 9, 12, 15):
        v = [neff(R[:, rng.choice(len(ALTS), k, replace=False)])["N_rho"] for _ in range(200)] if k < 15 else [neff(R)["N_rho"]]
        sat[k] = float(np.mean(v))
    out["N_rho_vs_symbols_raw"] = sat
    # extrapolation of the equal-correlation model to more symbols
    rho = out["daily_raw"]["rho_bar"]
    out["extrapolated_N_rho_raw"] = {str(n): n / (1 + (n - 1) * rho) for n in (6, 9, 15, 30, 86, 300)}
    out["limit_1_over_rho_raw"] = 1 / rho
    # evidence mass needed (asset-years): M = (z/SR)^2 with per-asset annual SR
    need = {}
    for K in (1, 10):
        z = zsum(0.05 / K)
        need[f"K={K}"] = {str(s): (z / s) ** 2 for s in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)}
    out["asset_years_needed_80pct_power"] = need
    # ---- evidence supply of each candidate source (no price data of the sealed block is read:
    #      only the per-month symbol counts from the download integrity log) ----
    dq_path = Path(a.spot_dq) if a.spot_dq else None
    cnt = {}
    if dq_path and dq_path.exists():
        for l in dq_path.read_text().splitlines():
            r = json.loads(l); cnt[r["month"]] = cnt.get(r["month"], 0) + 1
    ND = out["daily_raw"]["N_rho"]; NR = out["daily_residual"]["N_pr"]; kres = out["daily_residual"]["N_pr_per_rank"]
    rho_lo, rho_hi = 0.58, 0.72   # S1 mean pairwise corr (0.58) and its worst half-year (0.72)
    nd = lambda n, r: n / (1 + (n - 1) * r)
    nr = lambda n: kres * (n - 1)  # assumption: same per-rank participation as S1
    def src(years, n):
        return {"years": years, "symbols": n, "Neff_dir": [nd(n, rho_hi), nd(n, rho_lo)], "Neff_res": nr(n),
                "M_dir": [years * nd(n, rho_hi), years * nd(n, rho_lo)], "M_res": years * nr(n)}
    sources = {
        "IW1_2020_trancheA": src(1.0, 6), "IW1_2020_A+B": src(1.0, 13),
        "forward_12m_9sym": src(1.0, 9), "forward_36m_9sym": src(3.0, 9),
        "alt_gap_windows_15sym(entangled_with_BTC_ETH_windows)": src(1.7, 15),
        "DEV_S1+S2_15sym(SPENT,reference)": src(4.05, 15),
    }
    if cnt:
        ms = sorted(cnt)
        md = [sum(nd(cnt[m], rho_hi) for m in ms) / 12, sum(nd(cnt[m], rho_lo) for m in ms) / 12]
        mr = sum(nr(cnt[m]) for m in ms) / 12
        sources["spot_2017-08..2019-12_(never_read)"] = {"years": len(ms) / 12, "symbols_by_month": cnt, "M_dir": md, "M_res": mr}
    out["sources"] = sources
    # detectable portfolio-level SR (DEV breadth) for a given evidence mass, 80% power
    def detect(M, Nref, K):
        return zsum(0.05 / K) / math.sqrt(M) * math.sqrt(Nref)
    combos = {
        "IW1 A": ["IW1_2020_trancheA"], "spot pre2020": ["spot_2017-08..2019-12_(never_read)"],
        "IW1 A + spot pre2020": ["IW1_2020_trancheA", "spot_2017-08..2019-12_(never_read)"],
        "IW1 A + spot pre2020 + fwd12m": ["IW1_2020_trancheA", "spot_2017-08..2019-12_(never_read)", "forward_12m_9sym"],
        "IW1 A + spot pre2020 + fwd36m": ["IW1_2020_trancheA", "spot_2017-08..2019-12_(never_read)", "forward_36m_9sym"],
        "fwd12m only (status quo)": ["forward_12m_9sym"], "fwd36m only (status quo)": ["forward_36m_9sym"],
    }
    det = {}
    for name, keys in combos.items():
        Md = [sum(sources[k]["M_dir"][i] if isinstance(sources[k]["M_dir"], list) else 0 for k in keys) for i in (0, 1)]
        Mr = sum(sources[k]["M_res"] for k in keys)
        det[name] = {"M_dir": Md, "M_res": Mr,
                     "SR_detectable_dir_K1": [detect(m, ND, 1) for m in Md], "SR_detectable_dir_K10": [detect(m, ND, 10) for m in Md],
                     "SR_detectable_res_K1": detect(Mr, NR, 1), "SR_detectable_res_K10": detect(Mr, NR, 10)}
    out["detectable_portfolio_SR_80pct"] = det
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("daily_residual","sources","detectable_portfolio_SR_80pct")}, indent=1))


if __name__ == "__main__":
    main()
