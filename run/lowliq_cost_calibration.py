#!/usr/bin/env python3
"""Volume-scaled alt cost calibration from the measured public order books (ADR 0039).

Rule (fixed before any strategy was re-run with it, see ADR 0039):

* per symbol of the 10 collected alts: ``half_spread_param`` and ``impact_param``
  from the sealed estimator (same criteria as ADR 0038), and the median 15m
  quote volume ``q`` of the snapshots' closed kline;
* ``ln hs = a_hs + b_hs ln q`` and ``ln k = a_k + b_k ln q`` by ordinary least
  squares over the 10 symbols; each intercept is raised by the largest positive
  residual, so the line is an upper envelope that covers every measured symbol;
* both slopes must be <= 0 (costlier when thinner), otherwise refuse;
* ``q`` is clipped to [Q_MIN, max measured median]: no extrapolation toward
  thinner books below Q_MIN, and no credit above the most liquid sample.

Reads order-book files only.  No strategy data, no orders.

Usage::

    python3 run/lowliq_cost_calibration.py --in-dir <orderbook_data dir> --out <json>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "discovery"))
import public_cost_estimate as pce  # noqa: E402

VERSION = "lowliq-cost-calibration-v1"
CRITERIA = HERE / "discovery" / "config" / "cost_public_estimate_v3_alts.json"
FEE_RATE = 0.0005
MAX_PARTICIPATION = 0.05
#: 15m quote-volume floor (USDT) for the fit; thinner symbols are charged as if at this volume
Q_MIN = 10_000.0


def ols(xs: list, ys: list) -> tuple:
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def fit(xs: list, ys: list) -> dict:
    a, b = ols(xs, ys)
    if b > 0:
        raise SystemExit(f"slope {b:.3f} > 0: cost does not rise as volume falls; refusing to extrapolate")
    shift = max(max(y - (a + b * x) for x, y in zip(xs, ys)), 0.0)
    return {"intercept": a + shift, "slope": b, "envelope_shift": shift}


def curve(f: dict, q: float, q_max: float) -> float:
    q = min(max(q, Q_MIN), q_max)
    return math.exp(f["intercept"] + f["slope"] * math.log(q))


def build(in_dir: Path) -> dict:
    crit, crit_sha = pce.load_criteria(CRITERIA)
    files = sorted(in_dir.rglob("public_*.jsonl"))
    lines = pce.read_symbol_lines(files)
    man_sha, man_problems = pce.check_manifest(in_dir, files)
    if man_problems:
        raise SystemExit(f"manifest problems: {man_problems}")
    per = {}
    for sym in sorted(lines):
        r = pce.per_symbol(lines[sym], crit, sym)
        if not r["usable"]:
            raise SystemExit(f"{sym} not usable: {r['not_usable_because']}")
        qv = [float(x["kline_closed"]["quote_volume"]) for x in lines[sym]
              if x.get("status") == "OK" and "kline_closed" in x]
        per[sym] = {"half_spread": r["half_spread_param"], "impact_coef": r["impact_param"],
                    "qv15_median": statistics.median(qv), "snapshots_ok": r["snapshots_ok"]}
    syms = sorted(per)
    lq = [math.log(per[s]["qv15_median"]) for s in syms]
    hs = fit(lq, [math.log(per[s]["half_spread"]) for s in syms])
    kk = fit(lq, [math.log(per[s]["impact_coef"]) for s in syms])
    q_max = max(per[s]["qv15_median"] for s in syms)
    return {"version": VERSION, "criteria_sha256": crit_sha, "manifest_sha256": man_sha,
            "fee_rate": FEE_RATE, "max_participation": MAX_PARTICIPATION, "q_min": Q_MIN, "q_max": q_max,
            "half_spread_fit": hs, "impact_fit": kk, "per_symbol": per,
            "claim": "ESTIMATED_FROM_PUBLIC_DATA extrapolation; not verified, never promotable"}


def cost_params(cal: dict, q15: float) -> tuple:
    """(fixed one-side cost fraction, impact coefficient) for a symbol whose 15m quote volume is ``q15``."""
    return (cal["fee_rate"] + curve(cal["half_spread_fit"], q15, cal["q_max"]),
            curve(cal["impact_fit"], q15, cal["q_max"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    cal = build(a.in_dir)
    cal["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    a.out.write_text(json.dumps(cal, indent=1))
    for q in (10_000, 50_000, 200_000, 410_000, 1_000_000, 8_000_000):
        f, k = cost_params(cal, q)
        print(f"q15 {q:>9,}: half_spread {(f - cal['fee_rate']) * 1e4:6.2f}bp  impact coef {k:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
