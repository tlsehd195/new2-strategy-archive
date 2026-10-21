"""Read one DEV-only state directory and ask: what did the cost gate and the
trade-count gate throw away, and how good could those strategies have been?

Read-only and diagnostic.  It reads ``evaluations.jsonl``, ``specs.jsonl``
and ``dev_series.jsonl`` of an evolve state directory (DEV records only;
validation, IW1 and locked windows are never read because a DEV-only run
has none).  Nothing is written to the state directory and no status or
criterion changes.  Counterfactual cost numbers are arithmetic on the SAME
recorded events (net' = gross - c * cost - funding), not a new evaluation,
and they are a ceiling: the trades themselves would not change with a
cheaper cost, but nothing says the cheaper cost is real.

Usage:
    python3 run/power_audit_ledger.py --state <root>/state/evolution --out <file.json>
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def jsonl(p: Path):
    with p.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def daily_sr(series: dict) -> tuple[float | None, int]:
    """annualised SR of the equal-weight pooled daily net series (same
    construction as bundle_dev / the IW1 pre-check)."""
    cols = []
    for d0, vals in series.values():
        cols.append((int(d0), vals))
    if not cols:
        return None, 0
    lo = min(d0 for d0, _ in cols)
    hi = max(d0 + len(v) for d0, v in cols)
    tot = [0.0] * (hi - lo)
    cnt = [0] * (hi - lo)
    for d0, vals in cols:
        for i, x in enumerate(vals):
            tot[d0 - lo + i] += x
            cnt[d0 - lo + i] += 1
    xs = [t / c for t, c in zip(tot, cnt) if c]
    n = len(xs)
    if n < 30:
        return None, n
    m = sum(xs) / n
    sd = (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5
    return (m / sd * math.sqrt(365) if sd > 0 else None), n


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 3)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    specs = {}
    for r in jsonl(a.state / "specs.jsonl"):
        sid = r.get("strategy_id") or r.get("id")
        specs[sid] = r.get("spec") or r.get("definition") or r
    first = {}
    for r in jsonl(a.state / "evaluations.jsonl"):
        if r.get("stage") == "dev" and r.get("status") == "OK" and r["strategy_id"] not in first:
            first[r["strategy_id"]] = r
    series = {}
    for r in jsonl(a.state / "dev_series.jsonl"):
        series.setdefault(r["strategy_id"], r["series"])
    ctx = {r["context_id"] for r in first.values()}

    rows = []
    for sid, r in first.items():
        m = r["metrics"]
        sp = specs.get(sid) or {}
        sr, ndays = daily_sr(series.get(sid, {}))
        fails = r["screen"]["failures"]
        n = m["dev_n"]
        net, gross = m.get("dev_net_mean_bp"), m.get("dev_gross_mean_bp")
        cost, fund = m.get("dev_cost_mean_bp") or 0.0, m.get("dev_funding_mean_bp") or 0.0
        t = m.get("dev_t")
        # event sd backed out of t = mean / sd * sqrt(n)
        sd_e = abs(net) * math.sqrt(n) / abs(t) if (t and abs(t) > 0.05 and net) else None
        cf = {}
        for c in (0.5, 0.0):
            nn = None if gross is None else gross - c * cost - fund
            cf[str(c)] = {"net_mean_bp": nn,
                          "t": (nn / sd_e * math.sqrt(n)) if (sd_e and nn is not None) else None}
        # SR shift: the daily series moves by the per-day cost saving
        sr_cf = {}
        if sr is not None and ndays and net:
            # daily sd backed out of the recorded SR and the per-day mean
            per_day_net = net * n / ndays
            sd_d = per_day_net / (sr / math.sqrt(365)) if sr else None
            for c in (0.5, 0.0):
                if sd_d:
                    per_day = (gross - c * cost - fund) * n / ndays
                    sr_cf[str(c)] = per_day / sd_d * math.sqrt(365)
        rows.append({"id": sid, "direction": sp.get("direction"), "family": m.get("family"),
                     "n": n, "per_symbol_n": m.get("per_symbol_n"), "net": net, "gross": gross,
                     "cost": cost, "funding": fund, "t": t, "fails": fails,
                     "screen_pass": m.get("screen_pass"), "dev_sr_ann": sr, "days": ndays,
                     "cf": cf, "sr_cf": sr_cf})

    def summarise(sel, label):
        xs = [r for r in rows if sel(r)]
        by_dir = {}
        for r in xs:
            by_dir[r["direction"]] = by_dir.get(r["direction"], 0) + 1
        fams = {}
        for r in xs:
            fams[r["family"]] = fams.get(r["family"], 0) + 1
        return {"label": label, "count": len(xs), "by_direction": by_dir,
                "families": dict(sorted(fams.items(), key=lambda kv: -kv[1])[:10]),
                "n_trades_q10_50_90": [q([r["n"] for r in xs], p) for p in (0.1, 0.5, 0.9)],
                "net_bp_q10_50_90": [q([r["net"] for r in xs], p) for p in (0.1, 0.5, 0.9)],
                "t_q10_50_90": [q([r["t"] for r in xs], p) for p in (0.1, 0.5, 0.9)],
                "dev_sr_ann_q10_50_90_max": [q([r["dev_sr_ann"] for r in xs], p) for p in (0.1, 0.5, 0.9, 1.0)]}

    underp = lambda r: "dev_underpowered" in r["fails"]
    shorts_underp = lambda r: underp(r) and r["direction"] == "short"
    cost_fail = lambda r: (r["gross"] or 0) > 0 >= (r["net"] or 0)
    out = {
        "context_ids": sorted(ctx),
        "dev_evaluated": len(rows),
        "screen_pass": sum(1 for r in rows if r["screen_pass"]),
        "groups": [
            summarise(lambda r: True, "all"),
            summarise(lambda r: r["screen_pass"], "DEV screen pass"),
            summarise(underp, "dev_underpowered (any direction)"),
            summarise(lambda r: underp(r) and (r["net"] or 0) > 0, "dev_underpowered with positive net mean"),
            summarise(shorts_underp, "dev_underpowered shorts"),
            summarise(lambda r: shorts_underp(r) and (r["net"] or 0) > 0, "dev_underpowered shorts, net > 0"),
            summarise(cost_fail, "FAIL_COST (gross > 0 >= net)"),
        ],
    }
    # underpowered positive shorts: what is the most they could carry?
    us = [r for r in rows if shorts_underp(r) and (r["net"] or 0) > 0]
    out["underpowered_shorts_positive"] = {
        "count": len(us),
        "distinct_families": len({r["family"] for r in us}),
        "trades_per_year_median": q([r["n"] / 4.55 for r in us], 0.5),
        "share_with_t_ge_2": round(sum(1 for r in us if (r["t"] or 0) >= 2) / len(us), 3) if us else None,
        "dev_sr_ann_median_max": [q([r["dev_sr_ann"] for r in us], 0.5), q([r["dev_sr_ann"] for r in us], 1.0)],
        "note": "dev_sr_ann is the pooled daily SR over the whole DEV span: a strategy that trades "
                "2-3 times a year is flat almost every day, so even a large per-trade edge gives a "
                "small annual SR and almost no trades in a 6-month validation window",
        "top10_by_dev_sr": sorted(({k: r[k] for k in ("id", "family", "n", "per_symbol_n", "net", "t",
                                                      "dev_sr_ann")} for r in us),
                                  key=lambda x: -(x["dev_sr_ann"] or -9))[:10],
    }
    # cost gate: what if the cost were half / zero (same events)?
    cfails = [r for r in rows if cost_fail(r)]
    passes_cf = {}
    for c in ("0.5", "0.0"):
        ok = [r for r in cfails
              if (r["cf"][c]["net_mean_bp"] or 0) > 0 and (r["cf"][c]["t"] or 0) >= 1.0
              and min((r["per_symbol_n"] or {"x": 0}).values()) >= 30]
        passes_cf[c] = {"would_meet_mean_and_t_screen": len(ok),
                        "sr_ann_cf_q50_q90_max": [q([r["sr_cf"].get(c) for r in ok], p) for p in (0.5, 0.9, 1.0)],
                        "by_direction": {d: sum(1 for r in ok if r["direction"] == d)
                                         for d in {r["direction"] for r in ok}}}
    out["fail_cost_counterfactual"] = {
        "count": len(cfails),
        "cost_bp_per_trade_median": q([r["cost"] for r in cfails], 0.5),
        "gross_bp_per_trade_median": q([r["gross"] for r in cfails], 0.5),
        "if_cost_multiplied_by": passes_cf,
        "note": "ceiling: folds and cost-share gates not re-checked; the counterfactual cost is not evidence",
    }
    sp = [r for r in rows if r["screen_pass"]]
    out["screen_pass_dev_t_sorted"] = sorted((min(r["t"] or 0, 6.0) for r in sp), reverse=True)
    out["entry_t_20th"] = out["screen_pass_dev_t_sorted"][19] if len(sp) >= 20 else 0.0
    out["screen_pass_dev_sr_ann_q50_max"] = [q([r["dev_sr_ann"] for r in sp], 0.5),
                                             q([r["dev_sr_ann"] for r in sp], 1.0)]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1, sort_keys=True))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
