"""Read-only report of an evolution state directory (ADR 0013).

``python3 evolution_report.py <state_dir> [--context C] [--md OUT.md] [--json OUT.json]``

Counts every stage from the ledgers themselves — never from a summary file
or a document — so the numbers in a status report can be re-derived:

* candidates: registered specifications by source (seed / search /
  robustness neighbour), lineage edges by operator
* DEV: evaluations under the context, screen pass / fail
* validation: preregistered batches, members, outcome and failure class per
  member (DATA_MISSING / INSUFFICIENT_DATA / NO_EDGE_OUT_OF_SAMPLE /
  FAIL_COST / FAIL_STAT, see ``pipeline.validation_failure_class``)
* statuses: final status per strategy with its reasons
* locked TEST: reservations and uses

Nothing is written into the state directory.  A pipeline exit code or a
``CYCLE_OK`` line is not a strategy verdict; this report is not one either —
it only restates what the ledgers hold.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

from engine_lib import iter_jsonl

SURVIVOR_STATUSES = ("VALIDATION_SURVIVOR", "PAPER_TEST_READY", "PAPER_TESTING", "PAPER_PASSED")


def _latest_context(evo: Path) -> Optional[str]:
    last = None
    for r in iter_jsonl(evo / "contexts.jsonl"):
        last = r.get("context_id")
    return last


def build_report(state_dir: Path, context_id: Optional[str] = None) -> dict:
    from pipeline import validation_failure_class
    evo = Path(state_dir) / "evolution"
    ctx = context_id or _latest_context(evo)
    specs = list(iter_jsonl(evo / "specs.jsonl"))
    lineage = list(iter_jsonl(evo / "lineage.jsonl"))
    pool = {r["child_id"] for r in lineage}
    src = Counter("seed" if r["source"].startswith("legacy:") else
                  "robustness_neighbour" if r["source"].startswith("neighbour_of:") else
                  r["source"] for r in specs)
    evals = [r for r in iter_jsonl(evo / "evaluations.jsonl") if r.get("context_id") == ctx]
    dev = [r for r in evals if r["stage"] == "dev"]
    dev_ok = [r for r in dev if r.get("status") == "OK"]
    statuses: dict = {}
    for r in iter_jsonl(evo / "statuses.jsonl"):
        if r.get("context_id") == ctx:
            statuses[r["strategy_id"]] = r
    batches = list(iter_jsonl(evo / "validation_batches.jsonl"))
    pre = [b for b in batches if b["kind"] == "PREREGISTERED" and b.get("context_id") == ctx]
    results = {b["batch_id"]: b for b in batches if b["kind"] == "RESULT"}
    val = {r["strategy_id"]: r for r in evals if r["stage"] == "validation"}
    members = []
    for b in pre:
        res = results.get(b["batch_id"])
        for m in b["members"]:
            v = val.get(m) or {}
            fc = v.get("failure_class")
            if fc is None and v:
                syms = sorted(v.get("per_symbol") or {})
                fc = (validation_failure_class(v["per_symbol"], syms, rejected=v.get("stepm_rejected", False),
                                               powered=v.get("powered", False), min_events=1)
                      if all("gross_mean_bp" in (v["per_symbol"][s] or {}) for s in syms)
                      else "UNKNOWN_PRE_V3_RECORD")
            st = statuses.get(m) or {}
            members.append({"strategy_id": m, "batch_id": b["batch_id"],
                            "outcome": (res or {}).get("outcome", {}).get(m, "NOT_RUN"),
                            "failure_class": fc, "status": st.get("status"),
                            "reasons": st.get("reasons", []),
                            "per_symbol": {s: {k: x.get(k) for k in ("n", "mean_bp", "gross_mean_bp",
                                                                      "cost_mean_bp", "funding_mean_bp",
                                                                      "hac_p")}
                                           for s, x in (v.get("per_symbol") or {}).items()}})
    st_counts = Counter(r["status"] for r in statuses.values())
    lp = Path(state_dir) / "lock_test_usage.json"
    attempts = exposed = 0
    if lp.exists():
        from engine_lib import LockTestRegistry
        data = LockTestRegistry(lp)._load()
        for per_sym in data["attempts"].values():
            for atts in per_sym.values():
                attempts += len(atts)
                exposed += sum(1 for x in atts if x.get("exposed"))
        exposed += sum(len(v) for per in data["legacy_v1"].values() if isinstance(per, dict)
                       for v in per.values() if isinstance(v, list))
    return {
        "context_id": ctx,
        "candidates": {"registered_specs": len(specs), "by_source": dict(src),
                       "search_pool": len(pool),
                       "lineage_edges": len(lineage),
                       "by_operator": dict(Counter(r["operator"] for r in lineage))},
        "dev": {"evaluated": len(dev), "ok": len(dev_ok),
                "screen_pass": sum(1 for r in dev_ok if (r.get("screen") or {}).get("pass")),
                "not_ok": dict(Counter(r["status"] for r in dev if r.get("status") != "OK"))},
        "validation": {"batches": len(pre), "members": len(members),
                       "outcomes": dict(Counter(m["outcome"] for m in members)),
                       "failure_classes": dict(Counter(m["failure_class"] for m in members)),
                       "members_detail": members},
        "statuses": dict(st_counts),
        "survivors": sorted(s for s, r in statuses.items() if r["status"] in SURVIVOR_STATUSES),
        "locked": {"registry_present": lp.exists(), "attempts": attempts, "exposed_uses": exposed},
    }


def to_markdown(rep: dict) -> str:
    c, d, v = rep["candidates"], rep["dev"], rep["validation"]
    lines = [f"# Evolution report — context `{rep['context_id']}`", "",
             "Derived from the ledgers by `run/discovery/evolution_report.py`. Not a strategy verdict.", "",
             "| stage | count |", "|---|---|",
             f"| registered specifications | {c['registered_specs']} ({c['by_source']}) |",
             f"| search pool (seed + search) | {c['search_pool']} |",
             f"| DEV evaluated / OK / screen pass | {d['evaluated']} / {d['ok']} / {d['screen_pass']} |",
             f"| validation batches / members | {v['batches']} / {v['members']} |",
             f"| validation outcomes | {v['outcomes']} |",
             f"| failure classes | {v['failure_classes']} |",
             f"| statuses | {rep['statuses']} |",
             f"| survivors | {len(rep['survivors'])} |",
             f"| locked TEST attempts / exposed uses | {rep['locked']['attempts']} / {rep['locked']['exposed_uses']} |", "",
             "## Validation members", "",
             "| strategy | outcome | class | status | per symbol n / gross / cost / net (bp) |",
             "|---|---|---|---|---|"]
    for m in v["members_detail"]:
        ps = "; ".join(f"{s}: {x['n']} / {_f(x['gross_mean_bp'])} / {_f(x['cost_mean_bp'])} / {_f(x['mean_bp'])}"
                       for s, x in sorted(m["per_symbol"].items()))
        lines.append(f"| `{m['strategy_id']}` | {m['outcome']} | {m['failure_class']} | {m['status']} | {ps} |")
    return "\n".join(lines) + "\n"


def _f(x) -> str:
    return "-" if x is None else f"{x:.2f}"


def main(argv=None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("state_dir")
    ap.add_argument("--context")
    ap.add_argument("--md")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    rep = build_report(Path(a.state_dir), a.context)
    if rep["context_id"] is None:
        print("no evolution context in this state directory", file=sys.stderr)
        return 1
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1, ensure_ascii=False, default=str))
    if a.md:
        Path(a.md).write_text(to_markdown(rep))
    print(json.dumps({k: rep[k] for k in ("context_id", "candidates", "dev", "statuses", "survivors")}
                     | {"validation": {k: rep["validation"][k] for k in ("batches", "members", "outcomes",
                                                                          "failure_classes")}},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
