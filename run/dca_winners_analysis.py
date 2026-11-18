#!/usr/bin/env python3
"""Winners-vs-losers contrast of the ADR 0088 daily scale-in cycles (ADR 0090, part 2). DEV only.

Descriptive, NOT a strategy test: no new trials, no gate.  Spec fixed in
``run/audit/evidence_dca_limit_20261118/prereg_dca_limit_v1.json`` (part2), committed before any data was read.

Cycles: the 4 ADR 0088 base configs (no structural stop, leverage 5, market entry) re-run by the
ADR 0088 engine (``dca_extreme_dev.simulate``) on S1 and S2.  Every cycle, win AND loss, is kept.
Entry-time features use data up to the signal day close only; post-hoc variables (max adverse
excursion, tranches, holding days, exit reason) describe the cycle afterwards and are never rule inputs.

    python3 run/dca_winners_analysis.py --data-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import dca_extreme_dev as base  # noqa: E402

VERSION = "dca-winners-v1"
EVID = HERE / "audit" / "evidence_dca_limit_20261118"
PREREG = EVID / "prereg_dca_limit_v1.json"
BASES = {"B1": (30, 0.04, 4), "B2": (30, 0.08, 2), "B3": (90, 0.04, 4), "B4": (90, 0.08, 2)}
N_BOOT = 2000
SEED = 20261118
NUMERIC = ["dist_to_line", "range_width", "vol30", "ret5_dir", "ret20_dir", "ret60_dir", "mkt_ret30", "mkt_ret90",
           "mkt_vol30", "log_dv30", "concurrent_open", "same_day_signals", "is_long", "regime_bull"]
POSTHOC = ["mae", "ntr", "held", "liquidated"]
ALPHA = 0.05 / len(NUMERIC)


def auc(x: np.ndarray, y: np.ndarray) -> float:
    """P(feature of a win > feature of a loss) + 0.5 ties."""
    pos, neg = x[y == 1], x[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    r = pd.Series(np.concatenate([pos, neg])).rank().values
    return float((r[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def cluster_ci(df: pd.DataFrame, col: str, rng: np.random.Generator, level: float) -> tuple:
    months = df["month"].values
    um = np.unique(months)
    groups = {m: np.where(months == m)[0] for m in um}
    x, y = df[col].values.astype(float), df["win"].values
    out = []
    for _ in range(N_BOOT):
        pick = rng.choice(um, size=len(um), replace=True)
        idx = np.concatenate([groups[m] for m in pick])
        out.append(auc(x[idx], y[idx]))
    out = np.array([v for v in out if not math.isnan(v)])
    return float(np.quantile(out, level / 2)), float(np.quantile(out, 1 - level / 2))


def build_features(data: Path, seg: str, cfg_id: str, cfg: dict, run: dict, piv: dict, days: np.ndarray, syms: list) -> pd.DataFrame:
    o, h, l, c, qv = (piv[k] for k in ("open", "high", "low", "close", "qv"))
    n = cfg["N"]
    lo_line = l.shift(1).rolling(n, min_periods=n).min()
    hi_line = h.shift(1).rolling(n, min_periods=n).max()
    lret = np.log(c / c.shift(1))
    vol30 = lret.rolling(30, min_periods=20).std()
    dv30 = qv.rolling(30, min_periods=10).mean()
    ew = lret.mean(axis=1)                     # equal-weight alt market daily log return
    mkt30 = ew.rolling(30, min_periods=20).sum()
    mkt90 = ew.rolling(90, min_periods=60).sum()
    mvol30 = ew.rolling(30, min_periods=20).std()
    rows = []
    for cy in run["cycles"]:
        k, sd, d0 = cy["k"], cy["side"], cy["d0"]
        s = d0 - 1                              # signal day (close known, order placed for d0 open)
        de = cy["exit"][0]
        sym = syms[k]
        cl = c.iat[s, k]
        line = lo_line.iat[s, k] if sd > 0 else hi_line.iat[s, k]
        other = hi_line.iat[s, k] if sd > 0 else lo_line.iat[s, k]
        first = cy["px"][0]
        net = (cy["pp"] + cy["fund"] - cy["cost"]) / cy["maxnot"]
        seg_lo = np.nanmin(l.values[d0: de + 1, k])
        seg_hi = np.nanmax(h.values[d0: de + 1, k])
        mae = (seg_lo / first - 1) if sd > 0 else (1 - seg_hi / first)       # <= 0 means adverse
        t = pd.Timestamp(int(days[d0]), unit="ms", tz="UTC")
        rows.append({
            "base": cfg_id, "sym": sym, "side": int(sd), "entry_day": t.strftime("%Y-%m-%d"), "month": t.strftime("%Y-%m"),
            "year_half": f"{t.year}H{1 if t.month <= 6 else 2}", "dow": t.dayofweek, "d0": d0, "de": de,
            "dist_to_line": (cl / line - 1) if sd > 0 else (1 - cl / line),
            "range_width": hi_line.iat[s, k] / lo_line.iat[s, k] - 1,
            "vol30": vol30.iat[s, k],
            "ret5_dir": sd * (cl / c.iat[s - 5, k] - 1), "ret20_dir": sd * (cl / c.iat[s - 20, k] - 1),
            "ret60_dir": sd * (cl / c.iat[s - 60, k] - 1),
            "mkt_ret30": mkt30.iat[s], "mkt_ret90": mkt90.iat[s], "mkt_vol30": mvol30.iat[s],
            "regime_bull": float(mkt90.iat[s] > 0), "log_dv30": math.log(dv30.iat[s, k]) if dv30.iat[s, k] > 0 else np.nan,
            "is_long": float(sd > 0),
            "net_bp": net * 1e4, "win": int(net > 0), "exit": cy["exit"][1],
            "mae": mae, "ntr": cy["ntr"], "held": de - d0, "liquidated": float(cy["exit"][1] == "LIQUIDATED"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["concurrent_open"] = [int(((df["d0"] < r.d0) & (df["de"] > r.d0)).sum()) for r in df.itertuples()]
    df["same_day_signals"] = df.groupby("d0")["d0"].transform("count") - 1
    return df


def summarize(df: pd.DataFrame) -> dict:
    w, ls = df[df["win"] == 1], df[df["win"] == 0]
    tot = df["net_bp"].sum()
    return {"n": int(len(df)), "win_rate": float(df["win"].mean()), "mean_net_bp": float(df["net_bp"].mean()),
            "mean_win_bp": float(w["net_bp"].mean()) if len(w) else None,
            "mean_loss_bp": float(ls["net_bp"].mean()) if len(ls) else None,
            "sum_win_bp": float(w["net_bp"].sum()), "sum_loss_bp": float(ls["net_bp"].sum()),
            "exit_reasons_win": w["exit"].value_counts().to_dict(), "exit_reasons_loss": ls["exit"].value_counts().to_dict(),
            "post_hoc": {c: {"win_mean": float(w[c].mean()) if len(w) else None, "loss_mean": float(ls[c].mean()) if len(ls) else None,
                             "win_median": float(w[c].median()) if len(w) else None, "loss_median": float(ls[c].median()) if len(ls) else None}
                         for c in POSTHOC},
            "winners_with_mae_worse_than_10pct": float((w["mae"] <= -0.10).mean()) if len(w) else None,
            "winners_with_mae_worse_than_15pct": float((w["mae"] <= -0.15).mean()) if len(w) else None,
            "losers_with_mae_worse_than_10pct": float((ls["mae"] <= -0.10).mean()) if len(ls) else None,
            "mean_net_bp_winners_mae_worse_than_10pct": float(w[w["mae"] <= -0.10]["net_bp"].mean()) if (w["mae"] <= -0.10).any() else None,
            "total_net_bp": float(tot)}


def categorical(df: pd.DataFrame, col: str) -> dict:
    g = df.groupby(col).agg(n=("win", "size"), win_rate=("win", "mean"), mean_net_bp=("net_bp", "mean"), sum_net_bp=("net_bp", "sum"))
    return {str(k): {kk: float(vv) for kk, vv in v.items()} for k, v in g.iterrows()}


def feature_tests(s1: pd.DataFrame, s2: pd.DataFrame, rng: np.random.Generator) -> dict:
    out = {}
    for col in NUMERIC:
        d1, d2 = s1.dropna(subset=[col]), s2.dropna(subset=[col])
        a1, a2 = auc(d1[col].values.astype(float), d1["win"].values), auc(d2[col].values.astype(float), d2["win"].values)
        lo, hi = cluster_ci(d1, col, rng, ALPHA)
        corr1 = float(d1[[col, "net_bp"]].corr(method="spearman").iloc[0, 1])
        corr2 = float(d2[[col, "net_bp"]].corr(method="spearman").iloc[0, 1])
        sig1 = lo > 0.5 or hi < 0.5
        consistent = bool(sig1 and (a2 - 0.5) * (a1 - 0.5) > 0 and abs(a2 - 0.5) >= 0.03)
        out[col] = {"auc_S1": a1, "auc_S1_ci_bonferroni": [lo, hi], "auc_S2": a2, "spearman_net_S1": corr1, "spearman_net_S2": corr2,
                    "S1_significant_corrected": bool(sig1), "consistent": consistent}
    return out


def rule_check(s1: pd.DataFrame, s2: pd.DataFrame, col: str) -> dict:
    """Tercile cut on S1; worst-S1 tercile (by mean net bp) dropped from S2. Post-hoc, not evidence."""
    q = s1[col].quantile([1 / 3, 2 / 3]).values
    def bin_(v):
        return np.where(v <= q[0], 0, np.where(v <= q[1], 1, 2))
    b1, b2 = bin_(s1[col].values), bin_(s2[col].values)
    stats1 = [float(s1["net_bp"].values[b1 == i].mean()) for i in range(3)]
    worst = int(np.argmin(stats1))
    keep = b2 != worst
    return {"cuts_S1": [float(v) for v in q], "S1_mean_net_bp_by_tercile": stats1, "dropped_tercile": worst,
            "S2_mean_net_bp_all": float(s2["net_bp"].mean()), "S2_mean_net_bp_kept": float(s2["net_bp"].values[keep].mean()),
            "S2_mean_net_bp_dropped": float(s2["net_bp"].values[~keep].mean()) if (~keep).any() else None,
            "S2_n_kept": int(keep.sum()), "S2_n_dropped": int((~keep).sum()),
            "S2_win_rate_kept": float(s2["win"].values[keep].mean()), "S2_win_rate_dropped": float(s2["win"].values[~keep].mean()) if (~keep).any() else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "dca_winners.json")
    ap.add_argument("--cycles-csv", type=Path, default=EVID / "dca_winners_cycles.csv")
    a = ap.parse_args(argv)
    cfgs = [{"id": f"N{n}_s{s:.2f}_K{k}_target_time_only", "N": n, "s": s, "K": k, "exit": "target_time_only", "base": b}
            for b, (n, s, k) in BASES.items()]
    costs = {"measured": bu.load_cost_record(base.COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(base.CALIBRATION)}
    frames = {}
    for seg in base.SEGMENTS:
        r = base.run_segment(a.data_dir, seg, cfgs, costs)
        piv = base.load_ohlcv(a.data_dir, seg)
        frames[seg] = (r, piv)
    rng = np.random.default_rng(SEED)
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "base_script_sha256": bu.sha(HERE / "dca_extreme_dev.py"), "alpha_bonferroni": ALPHA, "n_numeric_features": len(NUMERIC),
           "n_boot": N_BOOT, "seed": SEED, "bases": {}}
    allrows = []
    for cfg in cfgs:
        dfs = {}
        for seg, (r, piv) in frames.items():
            days = piv["open"].index.values
            dfs[seg] = build_features(a.data_dir, seg, cfg["base"], cfg, r["runs"][cfg["id"]], piv, days, list(piv["open"].columns))
            dfs[seg]["seg"] = seg
            allrows.append(dfs[seg])
        s1, s2 = dfs["S1"], dfs["S2"]
        rec = {"cfg": cfg["id"], "S1": summarize(s1), "S2": summarize(s2),
               "tests": feature_tests(s1, s2, rng),
               "by_sym_S1": categorical(s1, "sym"), "by_sym_S2": categorical(s2, "sym"),
               "by_year_half_S1": categorical(s1, "year_half"), "by_year_half_S2": categorical(s2, "year_half"),
               "by_dow_S1": categorical(s1, "dow"), "by_dow_S2": categorical(s2, "dow"),
               "by_side_S1": categorical(s1, "side"), "by_side_S2": categorical(s2, "side"),
               "by_regime_S1": categorical(s1, "regime_bull"), "by_regime_S2": categorical(s2, "regime_bull")}
        cons = [c for c, t in rec["tests"].items() if t["consistent"]]
        rec["consistent_features"] = cons
        rec["rule_check_post_hoc"] = {c: rule_check(s1, s2, c) for c in cons}
        # post-hoc variable by mae bucket: win rate and mean net by S1+S2 MAE bucket (description only)
        both = pd.concat([s1, s2])
        edges = [-1.01, -0.30, -0.20, -0.10, -0.05, 0.0001]
        both["mae_bucket"] = pd.cut(both["mae"], edges)
        rec["post_hoc_by_mae_bucket"] = {str(k): {kk: float(vv) for kk, vv in v.items()} for k, v in
                                         both.groupby("mae_bucket", observed=True).agg(n=("win", "size"), win_rate=("win", "mean"), mean_net_bp=("net_bp", "mean")).iterrows()}
        out["bases"][cfg["base"]] = rec
    cyc = pd.concat(allrows)
    cyc.to_csv(a.cycles_csv, index=False, float_format="%.6g")
    out["consistent_in_n_bases"] = {c: sum(1 for b in out["bases"].values() if c in b["consistent_features"]) for c in NUMERIC}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    for b, rec in out["bases"].items():
        for seg in ("S1", "S2"):
            sm = rec[seg]
            print(f"{b} {seg} n={sm['n']} win {sm['win_rate']:.2f} mean {sm['mean_net_bp']:.0f}bp win {sm['mean_win_bp']:.0f} loss {sm['mean_loss_bp']:.0f} "
                  f"liq-in-losers {sm['exit_reasons_loss']} winners mae<-10% {sm['winners_with_mae_worse_than_10pct']:.2f}")
        print(" consistent:", rec["consistent_features"])
        for c, t in rec["tests"].items():
            print(f"   {c:18s} S1 {t['auc_S1']:.3f} [{t['auc_S1_ci_bonferroni'][0]:.3f},{t['auc_S1_ci_bonferroni'][1]:.3f}] S2 {t['auc_S2']:.3f} {'CONSISTENT' if t['consistent'] else ''}")
    print("consistent in n bases:", {c: v for c, v in out["consistent_in_n_bases"].items() if v})
    return 0


if __name__ == "__main__":
    sys.exit(main())
