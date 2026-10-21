"""Long/short bundle diagnostic on DEV daily series (ADR 0018).

Combines the DEV-pass strategies of ONE experiment context into equal-weight
bundles (long side, short side, symmetric, long+short) and reports daily mean,
annualised Sharpe, max drawdown and the long/short correlation.  Reads only
``dev_series.jsonl`` / ``specs.jsonl`` / ``statuses.jsonl`` of a state dir: no
validation or locked data, no ledger writes, no statistical gate.  The members
were selected on these same DEV days, so every number is selection-biased and
is a diagnostic for "does a short leg diversify", never evidence for promotion.

    python3 bundle_dev.py <state_dir>/evolution [--context C2-...]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine_lib import iter_jsonl  # noqa: E402

BUNDLE_VERSION = "bundle-dev-v1"
DAYS_PER_YEAR = 365.0   # crypto trades every day


def pooled_daily(series: Mapping) -> dict:
    """{day_index: bp summed over symbols} (same pooling as pipeline._pbo)."""
    acc: dict[int, float] = {}
    for d0, vals in series.values():
        for i, v in enumerate(vals):
            acc[d0 + i] = acc.get(d0 + i, 0.0) + v
    return acc


def combine(members: Sequence[Mapping], days: Sequence[int]) -> list:
    """Equal-weight mean of the members' pooled daily bp on ``days`` (flat = 0)."""
    if not members:
        return [0.0] * len(days)
    return [sum(m.get(d, 0.0) for m in members) / len(members) for d in days]


def stats(x: Sequence[float]) -> dict:
    n = len(x)
    if n < 2:
        return {"days": n, "mean_bp_day": None, "sharpe_ann": None, "mdd_bp": None}
    mu = sum(x) / n
    sd = math.sqrt(sum((v - mu) ** 2 for v in x) / (n - 1))
    peak = cum = mdd = 0.0
    for v in x:
        cum += v
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {"days": n, "mean_bp_day": mu,
            "sharpe_ann": (mu / sd * math.sqrt(DAYS_PER_YEAR)) if sd > 0 else None,
            "mdd_bp": mdd}


def corr(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((v - ma) ** 2 for v in a)
    vb = sum((v - mb) ** 2 for v in b)
    if va <= 0 or vb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def build_report(evo_dir: Path, context: Optional[str] = None) -> dict:
    ctxs = [r["context_id"] for r in iter_jsonl(evo_dir / "contexts.jsonl")]
    ctx = context or (ctxs[-1] if ctxs else None)
    if ctx is None:
        raise SystemExit("no experiment context found")
    direction = {r["strategy_id"]: r["definition"]["direction"]
                 for r in iter_jsonl(evo_dir / "specs.jsonl")}
    status = {}
    for r in iter_jsonl(evo_dir / "statuses.jsonl"):
        if r.get("context_id") == ctx and r.get("stage") == "dev_screen":
            status[r["strategy_id"]] = r["status"]
    series = {r["strategy_id"]: pooled_daily(r["series"])
              for r in iter_jsonl(evo_dir / "dev_series.jsonl") if r["context_id"] == ctx}
    passers = sorted(s for s, st in status.items() if st == "RESEARCH_ONLY" and s in series)
    side = {d: [s for s in passers if direction.get(s) == d]
            for d in ("long", "short", "symmetric")}
    days = sorted({d for s in passers for d in series[s]})
    if not days:
        raise SystemExit("no DEV-pass strategy with a daily series in this context")
    days = list(range(days[0], days[-1] + 1))
    mem = {k: [series[s] for s in v] for k, v in side.items()}
    bundles = {
        "long": combine(mem["long"], days),
        "short": combine(mem["short"], days),
        "symmetric": combine(mem["symmetric"], days),
        "long+short": combine(mem["long"] + mem["short"], days),
        "long+short+symmetric": combine(mem["long"] + mem["short"] + mem["symmetric"], days),
    }
    # half/half weighting so the few short legs are not diluted by 56 longs
    bundles["long50/short50"] = [0.5 * a + 0.5 * b for a, b in zip(bundles["long"], bundles["short"])]
    return {
        "version": BUNDLE_VERSION, "context_id": ctx, "data": "dev only",
        "use": "diagnostic only (members selected on the same DEV days; never a gate)",
        "members": {k: v for k, v in side.items()},
        "n_members": {k: len(v) for k, v in side.items()},
        "bundles": {k: stats(v) for k, v in bundles.items()},
        "corr_long_vs_short": corr(bundles["long"], bundles["short"]),
        "corr_long_vs_symmetric": corr(bundles["long"], bundles["symmetric"]),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("evolution_dir", type=Path)
    ap.add_argument("--context")
    ap.add_argument("--out", type=Path, help="write the JSON report here")
    a = ap.parse_args(argv)
    rep = build_report(a.evolution_dir, a.context)
    text = json.dumps(rep, indent=1, sort_keys=True)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
