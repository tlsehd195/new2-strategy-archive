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


def build_report(state_dir: Path, context_id: Optional[str] = None,
                 registry_path: Optional[Path] = None) -> dict:
    """``registry_path``: the independent-validation registry (ADR 0028); its
    runs are reported in ``independent`` and never mixed with the DEV screen or
    the in-state validation batches (which ran on the spent BTC/ETH windows)."""
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
    # directive vocabulary (pipeline.outcome_label), per strategy, from the same ledgers
    from pipeline import outcome_label
    dev_by = {r["strategy_id"]: r for r in dev}
    reverify = _reverify_ids(evo, ctx, statuses)
    labels = {sid: outcome_label(statuses.get(sid), dev=dev_by.get(sid), validation=val.get(sid),
                                 reverify=sid in reverify)
              for sid in sorted(set(statuses) | set(dev_by) | reverify)}
    by_stage = {"dev_screen": Counter(), "validation": Counter()}
    for sid, lab in labels.items():
        by_stage["validation" if (statuses.get(sid) or {}).get("stage") == "validation" else "dev_screen"][lab] += 1
    dedup = _dedup(evo, lineage)
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
    # consistency of the ledgers with each other (ADR 0028): nothing incomplete
    # is counted as complete, and every count above can be traced to records
    dev_ids = [r["strategy_id"] for r in dev]
    dup_dev = sorted(k for k, n in Counter(dev_ids).items() if n > 1)
    val_ids = [r["strategy_id"] for r in evals if r["stage"] == "validation"]
    consistency = {
        "duplicate_dev_records": dup_dev,
        "duplicate_validation_records": sorted(k for k, n in Counter(val_ids).items() if n > 1),
        # search-pool candidates only: robustness neighbours are DEV-evaluated without a status
        "dev_ok_without_status": sorted(r["strategy_id"] for r in dev_ok
                                        if r["strategy_id"] not in statuses and r["strategy_id"] in pool),
        "status_without_dev_record": sorted(s for s, r in statuses.items()
                                            if r.get("stage") in ("dev", "dev_screen") and s not in set(dev_ids)),
        "incomplete_batches": sorted(b["batch_id"] for b in pre if b["batch_id"] not in results),
    }
    consistency["ok"] = not any(consistency.values())
    independent = _independent(registry_path)
    return {
        "context_id": ctx,
        "consistency": consistency,
        "independent": independent,
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
        "outcomes": {"total": dict(Counter(labels.values())),
                     "by_stage": {k: dict(v) for k, v in by_stage.items()}},
        "dedup": dedup,
        "survivors": sorted(s for s, r in statuses.items() if r["status"] in SURVIVOR_STATUSES),
        "locked": {"registry_present": lp.exists(), "attempts": attempts, "exposed_uses": exposed},
    }


def _reverify_ids(evo: Path, ctx: Optional[str], statuses: dict) -> set:
    """Strategies with a confirmatory status under ANOTHER context and none
    under ``ctx`` (same rule as ``EvolutionPipeline.reverification_pending``)."""
    advanced = {"VALIDATION_SURVIVOR", "PAPER_TEST_READY", "PAPER_TESTING", "PAPER_PASSED",
                "BLOCKED_COST", "BLOCKED_STATISTICS"}
    latest: dict = {}
    for r in iter_jsonl(evo / "statuses.jsonl"):
        latest[(r["strategy_id"], r["context_id"])] = r
    out = set()
    for (sid, c), r in latest.items():
        if c != ctx and sid not in statuses and r["status"] in advanced:
            if r["status"].startswith("BLOCKED") and r.get("resume_to") != "VALIDATION_SURVIVOR":
                continue
            out.add(sid)
    return out


def _independent(registry_path: Optional[Path]) -> dict:
    """Independent validation runs (validation_registry.jsonl): only COMPLETED
    runs count; RUNNING runs are listed as incomplete."""
    if registry_path is None:
        from independent_window import DEFAULT_REGISTRY
        registry_path = DEFAULT_REGISTRY
    from validation_registry import ValidationRegistry
    return {"registry": str(registry_path), **ValidationRegistry(Path(registry_path)).summary()}


def _dedup(evo: Path, lineage: list) -> dict:
    """Audit trail of duplicate detection: every proposal the search refused
    (exact definition / identical behaviour, with its representative and
    reason) and every near duplicate it kept and flagged."""
    refused, kinds = [], Counter()
    for r in iter_jsonl(evo / "rounds.jsonl"):
        for x in r.get("dedup_log") or []:
            kinds[x["kind"]] += 1
            refused.append({"round": r.get("round_index"), **x})
    for r in iter_jsonl(evo / "rounds.jsonl"):
        if "dedup_log" not in r:                     # rounds written before fp-v2: counts only
            kinds["behaviour_equal (count only, pre fp-v2)"] += int(r.get("semantic_duplicates") or 0)
            kinds["exact_definition (count only, pre fp-v2)"] += int(r.get("duplicates") or 0)
    flagged = [{"strategy_id": r["child_id"], "near_duplicate_of": r["detail"]["near_duplicate_of"],
                "reason": r["detail"].get("near_reason"), "operator": r["operator"],
                "generation": r.get("generation")}
               for r in lineage if isinstance(r.get("detail"), dict) and "near_duplicate_of" in r["detail"]]
    unique: dict = {}
    for x in refused:
        unique.setdefault(x["kind"], set()).add(x["proposed_id"])
    return {"counts": dict(kinds), "unique_proposed": {k: len(v) for k, v in sorted(unique.items())},
            "counts_note": "counts = refusal decisions (one definition can be proposed and refused "
                           "several times); unique_proposed = distinct refused definitions",
            "decisions": refused, "near_duplicates_kept": flagged,
            "fingerprint_versions": sorted({r.get("fingerprint_version") or "pre fp-v2" for r in lineage})}


def to_markdown(rep: dict) -> str:
    c, d, v = rep["candidates"], rep["dev"], rep["validation"]
    lines = [f"# Evolution report — context `{rep['context_id']}`", "",
             "Derived from the ledgers by `run/discovery/evolution_report.py`. Not a strategy verdict. "
             "DEV screen pass is not a validation; only INDEPENDENT rows are independent evidence.", "",
             "| stage | count |", "|---|---|",
             f"| registered specifications | {c['registered_specs']} ({c['by_source']}) |",
             f"| search pool (seed + search) | {c['search_pool']} |",
             f"| DEV evaluated / OK / screen pass | {d['evaluated']} / {d['ok']} / {d['screen_pass']} |",
             f"| validation batches / members | {v['batches']} / {v['members']} |",
             f"| validation outcomes | {v['outcomes']} |",
             f"| failure classes | {v['failure_classes']} |",
             f"| statuses | {rep['statuses']} |",
             f"| outcomes (directive vocabulary) | {rep['outcomes']['total']} |",
             f"| outcomes by stage | {rep['outcomes']['by_stage']} |",
             f"| dedup decisions (distinct definitions) | {rep['dedup']['counts']} ({rep['dedup']['unique_proposed']}) |",
             f"| in-state validation survivors (spent BTC/ETH windows) | {len(rep['survivors'])} |",
             f"| INDEPENDENT runs completed / incomplete | {rep['independent']['by_state'].get('COMPLETED', 0)} / "
             f"{len(rep['independent']['incomplete_runs'])} |",
             f"| INDEPENDENT members evaluated / passes | {rep['independent']['completed_members']} / "
             f"{len(rep['independent']['independent_passes'])} |",
             f"| ledger consistency | {'ok' if rep['consistency']['ok'] else {k: v for k, v in rep['consistency'].items() if v and k != 'ok'}} |",
             f"| locked TEST attempts / exposed uses | {rep['locked']['attempts']} / {rep['locked']['exposed_uses']} |", "",
             "## Validation members", "",
             "| strategy | outcome | class | status | per symbol n / gross / cost / net (bp) |",
             "|---|---|---|---|---|"]
    for m in v["members_detail"]:
        ps = "; ".join(f"{s}: {x['n']} / {_f(x['gross_mean_bp'])} / {_f(x['cost_mean_bp'])} / {_f(x['mean_bp'])}"
                       for s, x in sorted(m["per_symbol"].items()))
        lines.append(f"| `{m['strategy_id']}` | {m['outcome']} | {m['failure_class']} | {m['status']} | {ps} |")
    dd = rep["dedup"]
    if dd["decisions"] or dd["near_duplicates_kept"]:
        lines += ["", "## Duplicate detection", "",
                  "| round | kind | proposed | representative | operator | reason |", "|---|---|---|---|---|---|"]
        for x in dd["decisions"]:
            lines.append(f"| {x['round']} | {x['kind']} | `{x['proposed_id']}` | `{x['representative']}` | "
                         f"{x['operator']} | {x['reason']} |")
        for x in dd["near_duplicates_kept"]:
            lines.append(f"| - | near_duplicate_kept | `{x['strategy_id']}` | `{x['near_duplicate_of']}` | "
                         f"{x['operator']} | {x['reason']} |")
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
    print(json.dumps({k: rep[k] for k in ("context_id", "candidates", "dev", "statuses", "outcomes",
                                          "survivors")}
                     | {"validation": {k: rep["validation"][k] for k in ("batches", "members", "outcomes",
                                                                          "failure_classes")}},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
