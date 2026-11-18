#!/usr/bin/env python3
"""Win-rate / loss-profile catalog of every published DEV result that records a hit rate (ADR 0092, Part 1).

Reads only result files already in the repository (no market data, no new trials).  One row per
(family, config, segment).  Values the source does not record stay ``None`` ("기록 없음"); nothing is
estimated.  Groups follow ``run/audit/evidence_winloss_combo_20261118/prereg_winloss_combo_v1.json``:

* A_high_win            : S1 hit >= 0.60 and n >= 30
* B_low_win_small_loss  : S1 hit <= 0.45, n >= 30 and every recorded loss metric small
                          (worst >= -1000 bp, S1 max DD <= 30 %, payoff >= 1.5)
* B_undetermined        : S1 hit <= 0.45, n >= 30, but no loss metric recorded
* other

    python3 run/winloss_catalog.py [--out-dir run/audit/evidence_winloss_combo_20261118]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUD = HERE / "audit"
EVID = AUD / "evidence_winloss_combo_20261118"

A_HIT, B_HIT, MIN_N = 0.60, 0.45, 30
B_WORST_BP, B_MAX_DD_PCT, B_PAYOFF = -1000.0, 30.0, 1.5

FIELDS = ["family", "adr", "config", "seg", "n", "hit_rate", "mean_net_bp", "median_net_bp", "avg_win_bp",
          "avg_loss_bp", "payoff", "worst_bp", "skew", "sr_ann", "max_dd_pct", "liq_share", "hold", "exit",
          "direction", "cost_row"]


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def row(**kw) -> dict:
    r = {k: None for k in FIELDS}
    r.update(kw)
    for k in ("n", "hit_rate", "mean_net_bp", "median_net_bp", "avg_win_bp", "avg_loss_bp", "payoff", "worst_bp",
              "skew", "sr_ann", "max_dd_pct", "liq_share"):
        r[k] = _f(r[k])
    if r["payoff"] is None and r["avg_win_bp"] is not None and r["avg_loss_bp"]:
        r["payoff"] = r["avg_win_bp"] / abs(r["avg_loss_bp"])
    return r


def load(rel: str):
    p = AUD / rel if not rel.startswith("results/") else HERE.parent / rel
    return json.loads(p.read_text()) if p.exists() else None


# ---------------------------------------------------------------------------- per-family readers

def entry_bar(rel, family, adr, cost_row):
    o = load(rel)
    out = []
    for c in (o or {}).get("candidates", []):
        for seg in ("S1", "S2"):
            s = c.get(seg) or {}
            if "hit_rate" not in s:
                continue
            out.append(row(family=family, adr=adr, config=c["strategy_id"], seg=seg, n=s.get("n_events"),
                           hit_rate=s.get("hit_rate"), mean_net_bp=s.get("mean_net_bp"), worst_bp=s.get("worst_net_bp"),
                           skew=s.get("skew"), sr_ann=s.get("sr_ann"), hold="15m-engine (hours~days)",
                           exit="engine stop/target/time", direction=c.get("direction"), cost_row=cost_row))
    return out


def trade_cfg(rel, family, adr, hold, exit_, sr_key="measured"):
    """Configs with S1/S2 {'trades': {...}, 'measured': {...}} (fast_short, hilo, okx tendency)."""
    o = load(rel)
    out = []
    for cid, c in (o or {}).get("configs", {}).items():
        for seg in ("S1", "S2"):
            s = c.get(seg) or {}
            t = s.get("trades") or {}
            if "hit_rate" not in t:
                continue
            m = s.get(sr_key) or {}
            d = "long" if not t.get("n_short") else "short" if not t.get("n_long") else "both"
            out.append(row(family=family, adr=adr, config=cid, seg=seg, n=t.get("n"), hit_rate=t.get("hit_rate"),
                           mean_net_bp=t.get("mean_net_bp"), sr_ann=m.get("sr_ann"), max_dd_pct=m.get("max_dd_pct"),
                           hold=hold, exit=exit_, direction=d, cost_row="measured"))
    return out


def dca(rel, family, adr):
    o = load(rel)
    out = []
    for cid, c in (o or {}).get("configs", {}).items():
        for seg in ("S1", "S2"):
            s = c.get(seg) or {}
            cy = s.get("cycles") or {}
            if "hit_rate" not in cy:
                continue
            m = s.get("measured") or s.get("central") or {}
            p = c.get("params", {})
            ex = "target+10% / time 60d" + (f" / stop {p.get('stop')}{p.get('x') or ''}" if p.get("stop") not in (None, "none") else "")
            if p.get("exit") == "stop_target":
                ex = "target / line stop"
            out.append(row(family=family, adr=adr, config=cid, seg=seg, n=cy.get("n"), hit_rate=cy.get("hit_rate"),
                           mean_net_bp=cy.get("mean_net_bp"), median_net_bp=cy.get("median_net_bp"),
                           worst_bp=None if cy.get("worst_cycle_net_pct") is None else 100 * cy["worst_cycle_net_pct"],
                           sr_ann=m.get("sr_ann"), max_dd_pct=m.get("max_dd_pct"), liq_share=cy.get("liquidated_share"),
                           hold="days (cycle mean %.0fd)" % (cy.get("mean_days_held") or 0), exit=ex, direction="both",
                           cost_row="measured" if "measured" in s else "central"))
    return out


def dca_winners():
    o = load("evidence_dca_limit_20261118/dca_winners.json")
    out = []
    for b, v in (o or {}).get("bases", {}).items():
        for seg in ("S1", "S2"):
            s = v.get(seg) or {}
            if "win_rate" not in s:
                continue
            out.append(row(family="scale-in winners/losers (base re-run)", adr="0090", config=f"{b}:{v['cfg']}", seg=seg,
                           n=s.get("n"), hit_rate=s.get("win_rate"), mean_net_bp=s.get("mean_net_bp"),
                           avg_win_bp=s.get("mean_win_bp"), avg_loss_bp=s.get("mean_loss_bp"), hold="days",
                           exit="target+10% / time 60d / 5x liquidation", direction="both", cost_row="measured"))
    return out


def gold():
    o = load("evidence_gold_dev_20261117/gold_dev_result.json")
    out = []
    for cid, c in (o or {}).get("trials", {}).items():
        for seg in ("S1", "S2"):
            s = (c.get(seg) or {}).get("perp_taker") or {}
            if "hit_rate" not in s:
                continue
            out.append(row(family="gold XAUUSD", adr="0086", config=cid, seg=seg, n=s.get("trades"), hit_rate=s.get("hit_rate"),
                           mean_net_bp=s.get("mean_net_bp"), sr_ann=s.get("sharpe"),
                           max_dd_pct=None if s.get("mdd") is None else 100 * abs(s["mdd"]), hold="15m~days",
                           exit="time/engine", direction="both", cost_row="perp_taker"))
    return out


def kimchi():
    o = load("results/kp_kimchi_results.json")
    out = []
    for seg_key, sv in (o or {}).get("segments", {}).items():
        seg = "S1" if seg_key.endswith("s1") else "S2" if seg_key.endswith("s2") else seg_key
        for cell, c in sv.get("cells", {}).items():
            out.append(row(family="kimchi premium", adr="(results/kp)", config=f"{seg_key}:{cell}", seg=seg, n=c.get("n"),
                           hit_rate=c.get("win_rate"), mean_net_bp=c.get("net16_mean_bp"), median_net_bp=None,
                           hold="bars (16)", exit="time", direction="long", cost_row="net16 (win_rate is gross)"))
    return out


def newlisting():
    o = load("evidence_strong_hypotheses_20261102/newlisting_short_dev.json")
    out = []
    for cid, c in (o or {}).get("configs", {}).items():
        for seg in ("S1", "S2"):
            s = c.get(seg) or {}
            if "hit_rate_gross" not in s:
                continue
            out.append(row(family="new-listing short", adr="0062-0064 era (strong hypotheses)", config=cid, seg=seg,
                           n=s.get("n"), hit_rate=s.get("hit_rate_gross"),
                           mean_net_bp=None if s.get("net_mean") is None else 1e4 * s["net_mean"],
                           median_net_bp=None, hold="days", exit="time", direction="short",
                           cost_row="measured (hit rate is GROSS)"))
    return out


def short_top():
    o = load("evidence_short_top_20261022/short_top_dev.json")
    out = []
    for key in ("long_top", "short_top", "short_pass_top"):
        for i, c in enumerate((o or {}).get(key, [])):
            p = c.get("profile") or {}
            if "win_share" not in p:
                continue
            g = c.get("net") or c.get("gross") or {}
            out.append(row(family=f"BTC/ETH engine {key}", adr="0028", config=f"{key}#{i + 1}:{c.get('family', '')}", seg="DEV(all)",
                           n=p.get("trades"), hit_rate=p.get("win_share"), mean_net_bp=p.get("net_bp_mean"),
                           max_dd_pct=g.get("mdd_pct"), sr_ann=g.get("sharpe_ann"),
                           hold="%.0fh median" % (p.get("hold_hours_median") or 0), exit="engine", direction=c.get("direction"),
                           cost_row="public estimate v1"))
    return out


SOURCES = [
    ("R9 entry-bar candidates (liquid 9)", lambda: entry_bar("alt_cost_measured_20261027/entry_bar_liquid9_measured.json", "R9 entry-bar candidates (liquid 9)", "0041/0044", "measured alt")),
    ("MAP-Elites top (BTC/ETH)", lambda: entry_bar("evidence_map_elites_20261021/iw1_statistic_top_dev.json", "MAP-Elites top (BTC/ETH)", "0019", "public estimate")),
    ("IW1 refreeze members", lambda: entry_bar("evidence_refreeze_v2_20261024/IW1_refreeze_members_dev_scores.json", "IW1 refreeze members", "0036", "assumed/estimate")),
    ("fast short-horizon", lambda: trade_cfg("evidence_fast_short_20261028/fast_short_dev.json", "fast short-horizon", "0045", "15m bars (1-16)", "time")),
    ("high/low + volume spike", lambda: trade_cfg("evidence_hilo_volume_20261028/hilo_volume_dev.json", "high/low + volume spike (daily)", "0043", "days (1-5)", "time")),
    ("high/low intraday", lambda: trade_cfg("evidence_hilo_intraday_20261028/hilo_intraday_dev.json", "high/low intraday", "0043", "15m-1h bars", "time")),
    ("OKX tendency", lambda: trade_cfg("evidence_okx_tendency_20261029/okx_tendency_dev.json", "OKX demo tendency", "0052", "15m bars", "time")),
    ("gold", gold),
    ("scale-in ADR 0088", lambda: dca("evidence_dca_extreme_20261117/dca_extreme_dev.json", "scale-in (5x, no stop)", "0088")),
    ("scale-in stop ADR 0089", lambda: dca("evidence_dca_stop_20261118/dca_stop_dev.json", "scale-in + stop", "0089")),
    ("scale-in limit ADR 0090", lambda: dca("evidence_dca_limit_20261118/dca_limit_dev.json", "scale-in low-lev limit", "0090")),
    ("scale-in winners ADR 0090", dca_winners),
    ("kimchi", kimchi),
    ("new listing short", newlisting),
    ("BTC/ETH engine top (ADR 0028)", short_top),
]


def group_of(s1: dict) -> str:
    n, h = s1.get("n"), s1.get("hit_rate")
    if n is None or h is None or n < MIN_N:
        return "other"
    if h >= A_HIT:
        return "A_high_win"
    if h <= B_HIT:
        checks = []
        if s1.get("worst_bp") is not None:
            checks.append(s1["worst_bp"] >= B_WORST_BP)
        if s1.get("max_dd_pct") is not None:
            checks.append(s1["max_dd_pct"] <= B_MAX_DD_PCT)
        if s1.get("payoff") is not None:
            checks.append(s1["payoff"] >= B_PAYOFF)
        if not checks:
            return "B_undetermined"
        return "B_low_win_small_loss" if all(checks) else "other"
    return "other"


def build() -> dict:
    rows, missing = [], []
    for name, fn in SOURCES:
        r = fn()
        if not r:
            missing.append(name)
        rows += r
    # duplicates: ADR 0089 reference runs (none/struct reproduce ADR 0088) and slippage robustness rows are not
    # separate strategies; the ADR 0090 winners/losers re-run of the 4 ADR 0088 bases only adds avg win / avg loss.
    rows = [r for r in rows if not (r["adr"] == "0089" and (r["config"].endswith(("_none", "_struct")) or "_slip" in r["config"]))]
    win = {(r["config"].split(":", 1)[1], r["seg"]): r for r in rows if r["adr"] == "0090" and r["family"].startswith("scale-in winners")}
    for r in rows:
        w = win.get((r["config"], r["seg"])) if r["adr"] == "0088" else None
        if w:
            r["avg_win_bp"], r["avg_loss_bp"] = w["avg_win_bp"], w["avg_loss_bp"]
            r["payoff"] = w["avg_win_bp"] / abs(w["avg_loss_bp"])
    rows = [r for r in rows if not (r["adr"] == "0090" and r["family"].startswith("scale-in winners"))]
    # group by S1 row (or the DEV(all) row where no split exists)
    key = lambda r: (r["family"], r["config"])  # noqa: E731
    by = {}
    for r in rows:
        by.setdefault(key(r), {})[r["seg"]] = r
    for k, segs in by.items():
        s1 = segs.get("S1") or segs.get("DEV(all)") or {}
        g = group_of(s1)
        for r in segs.values():
            r["group"] = g
    groups = {}
    for k, segs in by.items():
        s1 = segs.get("S1") or segs.get("DEV(all)") or {}
        s2 = segs.get("S2") or {}
        g = s1.get("group", "other")
        groups.setdefault(g, []).append({"family": k[0], "config": k[1], "S1": s1, "S2": s2})
    summary = {}
    for g, items in groups.items():
        fam = {}
        for it in items:
            f = fam.setdefault(it["family"], {"n_configs": 0, "s1_mean_net_pos": 0, "s2_mean_net_pos": 0, "s2_known": 0,
                                              "hit": [], "mean_net_bp": [], "worst_bp": [], "max_dd_pct": [], "sr_s1": [], "sr_s2": []})
            f["n_configs"] += 1
            s1, s2 = it["S1"], it["S2"]
            f["s1_mean_net_pos"] += (s1.get("mean_net_bp") or 0) > 0
            if s2.get("mean_net_bp") is not None:
                f["s2_known"] += 1
                f["s2_mean_net_pos"] += s2["mean_net_bp"] > 0
            for fld, src in (("hit", "hit_rate"), ("mean_net_bp", "mean_net_bp"), ("worst_bp", "worst_bp"), ("max_dd_pct", "max_dd_pct"), ("sr_s1", "sr_ann")):
                if s1.get(src) is not None:
                    f[fld].append(s1[src])
            if s2.get("sr_ann") is not None:
                f["sr_s2"].append(s2["sr_ann"])
        for f in fam.values():
            for fld in ("hit", "mean_net_bp", "worst_bp", "max_dd_pct", "sr_s1", "sr_s2"):
                v = sorted(f[fld])
                f[fld] = None if not v else {"min": v[0], "median": v[len(v) // 2], "max": v[-1]}
        summary[g] = {"n_configs": len(items), "by_family": fam}
    return {"version": "winloss-catalog-v1", "prereg": "prereg_winloss_combo_v1.json", "rules": {
        "A_high_win": f"S1 hit >= {A_HIT} and n >= {MIN_N}",
        "B_low_win_small_loss": f"S1 hit <= {B_HIT}, n >= {MIN_N}, worst >= {B_WORST_BP} bp, S1 maxDD <= {B_MAX_DD_PCT}%, payoff >= {B_PAYOFF} (each only if recorded)",
        "B_undetermined": "S1 hit <= 0.45 and n >= 30 but no loss metric recorded"},
        "sources_missing": missing, "n_rows": len(rows), "summary": summary, "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=EVID)
    a = ap.parse_args(argv)
    out = build()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    (a.out_dir / "winloss_catalog.json").write_text(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1, ensure_ascii=False))
    with open(a.out_dir / "winloss_catalog_rows.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS + ["group"])
        w.writeheader()
        for r in out["rows"]:
            w.writerow({k: ("" if r.get(k) is None else (round(r[k], 4) if isinstance(r[k], float) else r[k])) for k in FIELDS + ["group"]})
    print("rows", out["n_rows"], "missing", out["sources_missing"])
    for g, s in sorted(out["summary"].items()):
        print(f"== {g}: {s['n_configs']} configs")
        for fam, f in s["by_family"].items():
            h = f["hit"]
            print(f"   {fam:45s} {f['n_configs']:4d}  hit {h and round(h['median'], 2)}  S1 net>0 {f['s1_mean_net_pos']}  "
                  f"S2 net>0 {f['s2_mean_net_pos']}/{f['s2_known']}  worst {f['worst_bp'] and round(f['worst_bp']['median'])}  "
                  f"DD {f['max_dd_pct'] and round(f['max_dd_pct']['median'], 1)}  SR1 {f['sr_s1'] and round(f['sr_s1']['median'], 2)}  SR2 {f['sr_s2'] and round(f['sr_s2']['median'], 2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
