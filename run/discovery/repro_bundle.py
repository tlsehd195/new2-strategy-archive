"""Reproducibility bundle of a DEV run or an independent validation run (ADR 0027).

A bundle states everything needed to regenerate and check a result:
repository commit (and whether the work tree was dirty), the hash of every
evaluation code file, the candidate list and the sha256 of every candidate
definition, the data identity (per-symbol sha256, time range, timeframe),
the cost model (version, parameters, status), the backtest settings, the
random seeds, the statistical test and multiple-testing settings, the
command that was run, and the result status with its failure reasons.

What it does NOT contain: environment variables, tokens, account data or
anything outside the whitelist below (:func:`assert_no_secrets` refuses a
bundle with a key that looks like a credential).

What cannot be reproduced bit for bit, and why, is listed in
``limits`` of every bundle (e.g. ``at`` timestamps, the interpreter version
when it differs: ``math.erfc``/float formatting may differ across Python
versions, which is why ``runtime`` is part of the experiment context).

``verify_rerun(bundle, result)`` compares a re-run with the bundle: the
record hash (content of the result without timestamps) must be identical.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from engine_lib import canonical_json, git_commit, iso_now, iter_jsonl, runtime_identity  # noqa: E402

SCHEMA = "repro_bundle_v1"
_SECRET_WORDS = ("token", "secret", "password", "passwd", "api_key", "apikey", "credential",
                 "private_key", "authorization")
LIMITS = [
    "'at' / 'created_at' timestamps differ on every run (excluded from record hashes)",
    "results are reproducible only on the same Python minor version (runtime is part of the context)",
    "git_dirty=true means the code hashes, not the commit, identify the code",
]


def _git_dirty() -> Optional[bool]:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(HERE), "status", "--porcelain", "--", "."],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(r.stdout.strip()) if r.returncode == 0 else None


def assert_no_secrets(obj, path: str = "") -> None:
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if any(w in str(k).lower() for w in _SECRET_WORDS):
                raise ValueError(f"bundle key {path + str(k)!r} looks like a credential")
            assert_no_secrets(v, path + str(k) + ".")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_no_secrets(v, f"{path}{i}.")


def _seal(b: dict) -> dict:
    assert_no_secrets(b)
    body = {k: v for k, v in b.items() if k not in ("bundle_sha256", "at")}
    b["bundle_sha256"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return b


def independent_bundle(result: Mapping, *, pre: Mapping, members: Mapping, cost,
                       command: Sequence[str]) -> dict:
    """Bundle of one independent-window result (``independent_window.open_tranche``)."""
    from prereg_gate import research_code_identity
    from validation_registry import members_pin
    code = research_code_identity()
    b = {
        "schema": SCHEMA, "kind": "independent_validation", "run_id": result["run_id"],
        "repo": {"commit": git_commit(HERE), "git_dirty": _git_dirty()},
        "code": code,
        "candidates": {"members_sha256": members["members_sha256"],
                       "ids": [m["strategy_id"] for m in members["members"]],
                       "spec_sha256": members_pin(members["members"]),
                       "context_id": members["context_id"]},
        "data": {"source": (pre.get("source") or {}).get("archive"), "per_symbol": result["data"],
                 "period": pre["period"], "timeframe": "15m", "symbols": result["symbols"]},
        "cost": {"version": cost.version, "status": getattr(cost, "status", None),
                 "params": dict(cost.params), "profile": (pre.get("cost") or {}).get("profile")},
        "backtest": {"order_value": (pre.get("cost") or {}).get("order_value"),
                     "booking": "exit-day daily series, equal-weight over tranche symbols",
                     "warmup_days_after_first_bar": pre["period"].get("warmup_days_after_first_bar")},
        "seeds": {"bootstrap_seed": pre["test"].get("bootstrap_seed")},
        "statistics": {"method": pre["test"].get("method"), "B": pre["test"].get("bootstrap_B"),
                       "alpha": result["test"].get("alpha"), "q": result["test"].get("q"),
                       "multiple_testing": "Romano-Wolf StepM over the members (FWER) + alpha "
                                           "spending over tranches; BY only as a diagnostic",
                       "pass_rule": pre["test"].get("pass")},
        "prereg_sha256": result["prereg_sha256"],
        "command": list(command),
        "runtime": runtime_identity(),
        "result": {"record_sha256": result["record_sha256"], "outcome": result["outcome"],
                   "rejected": result["test"].get("rejected"), "excluded": result["test"].get("excluded")},
        "limits": LIMITS, "at": iso_now(),
    }
    return _seal(b)


def dev_bundle(state_dir: Path, *, command: Sequence[str] = ()) -> dict:
    """Bundle of a DEV state directory (latest context): context components,
    candidate definitions, evaluation counts and ledger hashes."""
    evo = Path(state_dir) / "evolution"
    ctxs = list(iter_jsonl(evo / "contexts.jsonl"))
    if not ctxs:
        raise ValueError(f"{evo}: no experiment context")
    ctx = ctxs[-1]
    comps = ctx.get("components") or {}
    specs = {r["strategy_id"]: r for r in iter_jsonl(evo / "specs.jsonl")}
    evals = [r for r in iter_jsonl(evo / "evaluations.jsonl") if r.get("context_id") == ctx["context_id"]]
    dev = [r for r in evals if r.get("stage") == "dev"]
    status: dict = {}
    for r in iter_jsonl(evo / "statuses.jsonl"):
        if r.get("context_id") == ctx["context_id"]:
            status[r["strategy_id"]] = r["status"]

    def fsha(name):
        p = evo / name
        return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    lineage = list(iter_jsonl(evo / "lineage.jsonl"))
    seeds = sorted({r.get("seed") for r in lineage if r.get("seed") is not None})
    b = {
        "schema": SCHEMA, "kind": "dev", "context_id": ctx["context_id"],
        "repo": {"commit": git_commit(HERE), "git_dirty": _git_dirty()},
        "code": comps.get("code"), "data": comps.get("data"), "data_policy": comps.get("data_policy"),
        "cost": comps.get("cost"), "validation_config": comps.get("validation"),
        "runtime": comps.get("runtime"),
        "candidates": {"registered": len(specs),
                       "list_sha256": hashlib.sha256("\n".join(sorted(specs)).encode()).hexdigest()},
        "seeds": {"lineage_seeds": seeds},
        "statistics": {"dev_screen": "promotion config dev_screen (hash in validation_config)",
                       "multiple_testing": "none at DEV (screen only); FWER is spent at validation"},
        "ledgers": {n: fsha(n) for n in ("specs.jsonl", "lineage.jsonl", "evaluations.jsonl",
                                         "statuses.jsonl", "validation_batches.jsonl")},
        "result": {"dev_evaluated": len(dev),
                   "dev_ok": sum(1 for r in dev if r.get("status") == "OK"),
                   "dev_screen_pass": sum(1 for r in dev if (r.get("screen") or {}).get("pass")),
                   "statuses": {s: sum(1 for v in status.values() if v == s) for s in sorted(set(status.values()))},
                   "failure_reasons": _reasons(dev)},
        "command": list(command), "limits": LIMITS, "at": iso_now(),
    }
    return _seal(b)


def _reasons(dev: Sequence[Mapping]) -> dict:
    out: dict = {}
    for r in dev:
        for f in (r.get("screen") or {}).get("failures") or ([r["status"]] if r.get("status") != "OK" else []):
            out[f] = out.get(f, 0) + 1
    return dict(sorted(out.items()))


def verify_rerun(bundle: Mapping, result: Mapping) -> list[str]:
    """Differences between a bundle and a re-run of the same inputs."""
    out = []
    if bundle.get("kind") == "independent_validation":
        if result.get("record_sha256") != bundle["result"]["record_sha256"]:
            out.append("record_sha256 differs: the re-run did not reproduce the result")
        if result.get("prereg_sha256") != bundle.get("prereg_sha256"):
            out.append("different preregistration")
    elif bundle.get("kind") == "dev":
        for k in ("dev_evaluated", "dev_ok", "dev_screen_pass", "statuses"):
            if canonical_json(bundle["result"].get(k)) != canonical_json((result.get("result") or {}).get(k)):
                out.append(f"{k} differs")
        if bundle.get("context_id") != result.get("context_id"):
            out.append("context differs (code, data, cost or config changed)")
    else:
        out.append(f"unknown bundle kind {bundle.get('kind')!r}")
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="reproducibility bundle (ADR 0027)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dev")
    d.add_argument("--state-dir", required=True)
    d.add_argument("--out", required=True)
    v = sub.add_parser("verify-dev")
    v.add_argument("--bundle", required=True)
    v.add_argument("--state-dir", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "dev":
        b = dev_bundle(Path(a.state_dir), command=sys.argv)
        Path(a.out).write_text(json.dumps(b, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(b["bundle_sha256"])
        return 0
    diff = verify_rerun(json.loads(Path(a.bundle).read_text()), dev_bundle(Path(a.state_dir)))
    print(json.dumps({"reproduced": not diff, "differences": diff}, indent=1))
    return 0 if not diff else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
