"""Preregistration lifecycle gate (ADR 0029).

A preregistration is DRAFT until a human activates it.  The lifecycle is
DERIVED from evidence, never read from a field the file sets for itself:

``DRAFT``     no activation record, a draft schema/status, or a required
              field missing.
``REVIEWED``  an activation record names a reviewer but no approver (or the
              approval checklist is incomplete).
``ACTIVE``    an activation record that (1) hashes to its own content,
              (2) names the exact prereg and members file by sha256,
              (3) carries the approver and a complete checklist, (4) whose
              members file is canonical, hash-consistent and pinned to the
              CURRENT evaluation code, and (5) whose prereg, members file and
              activation record are committed to git, unmodified in the work
              tree, with the last commit touching each at or before the
              deadline (``window_start`` of a forward window).
``INVALID``   an activation record exists but fails one of the checks — the
              reasons are listed; an INVALID prereg cannot start anything.

Changing a date in any file does not change the lifecycle: the dates the
gate uses are git commit times, and every file is bound by its sha256, so an
edited draft is a different file with no activation.  ``window_status`` never
reports a window as started unless the prereg is ACTIVE, whatever today's
date is.  The gate changes no statistical criterion.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from engine_lib import canonical_json, code_identity, strategy_id  # noqa: E402

ACTIVATION_SCHEMA = "prereg_activation_v1"
LIFECYCLE = ("DRAFT", "REVIEWED", "ACTIVE", "INVALID")
#: items a human confirms before a prereg is activated (all must be true)
CHECKLIST = ("members_frozen", "final_read_through_by_user", "cost_assumption_confirmed",
             "criteria_not_looser_than_promo_v3", "window_not_yet_seen")
#: required fields per prereg schema (dotted paths).  An unknown schema has no
#: rule, so it can never become ACTIVE until a rule is added here.
REQUIRED_FIELDS = {
    "independent_window_prereg_v1": {
        "deadline": None,                       # retrospective window: no forward deadline
        "fields": ("window_id", "period.read_start", "period.read_end", "period.entry_start",
                   "tranches", "members.rule", "members.max_batch_size", "cost.profile",
                   "cost.order_value", "test.method", "test.alpha_total_family",
                   "test.bootstrap_B", "test.bootstrap_seed", "test.pass", "single_use",
                   "outcome_meaning", "source.archive"),
    },
    "forward_window_prereg_v2": {
        "deadline": "window.window_start",
        "fields": ("family", "window.window_start", "window.window_end", "window.single_look",
                   "basket.symbols", "members.max", "members.rule", "cost.profile",
                   "cost.order_value", "test.method", "test.alpha", "test.bootstrap_B",
                   "test.pass", "single_use"),
    },
}


class GateRefused(RuntimeError):
    """The preregistration is not ACTIVE: nothing may be started under it."""


def research_code_identity() -> dict:
    """Hash of every file that decides an evaluation (DEV pipeline + the
    independent-window evaluator).  Pinned into a members file at freeze."""
    from pipeline import EVO_CODE_FILES
    files = tuple(sorted(set(EVO_CODE_FILES) | {"independent_window.py"}))
    ci = code_identity(files=files)
    return {"code_hash": ci["code_hash"], "files": ci["files"]}


def _get(d: Mapping, path: str):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, Mapping) or k not in cur:
            return None
        cur = cur[k]
    return cur


def record_sha(rec: Mapping) -> str:
    body = {k: v for k, v in rec.items() if k != "record_sha256"}
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def seal_activation(rec: Mapping) -> dict:
    out = {"schema": ACTIVATION_SCHEMA, **dict(rec)}
    out["record_sha256"] = record_sha(out)
    return out


class GitProbe:
    """Commit facts about a file (injectable in tests)."""

    def __init__(self, repo: Optional[Path] = None):
        self.repo = Path(repo) if repo else HERE

    def _git(self, *args: str) -> Optional[str]:
        try:
            r = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True,
                               text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    def committed_clean(self, path: Path) -> bool:
        p = str(Path(path).resolve())
        if self._git("ls-files", "--error-unmatch", p) is None:
            return False
        return self._git("status", "--porcelain", "--", p) == ""

    def last_commit_time(self, path: Path) -> Optional[int]:
        out = self._git("log", "-1", "--format=%ct", "--", str(Path(path).resolve()))
        return int(out) if out and out.isdigit() else None


def _day_s_utc(day: str) -> int:
    return int(dt.datetime.fromisoformat(day + "T00:00:00+00:00").timestamp())


def check_members_file(members: Mapping, prereg_sha: str) -> list[str]:
    """Freeze integrity: content hash, prereg binding, canonical specs, ids,
    and the code pin against the current evaluation code."""
    from strategy_spec import canonicalize_spec
    out = []
    body = {k: v for k, v in members.items() if k not in ("members_sha256", "frozen_at")}
    if hashlib.sha256(canonical_json(body).encode()).hexdigest() != members.get("members_sha256"):
        out.append("members file content does not match its hash (modified after freeze)")
    if members.get("prereg_sha256") != prereg_sha:
        out.append("members were frozen for a different preregistration")
    for m in members.get("members") or []:
        try:
            if canonicalize_spec(m["spec"]) != m["spec"]:
                out.append(f"{m.get('strategy_id')}: spec is not canonical")
            elif strategy_id(m["spec"]) != m["strategy_id"]:
                out.append(f"{m.get('strategy_id')}: id does not match the spec (definition changed)")
        except Exception as e:  # noqa: BLE001 — any invalid spec is a finding
            out.append(f"{m.get('strategy_id')}: invalid spec ({e})")
    if not members.get("members"):
        out.append("no members")
    code = (members.get("code") or {}).get("code_hash")
    if not code:
        out.append("members file records no evaluation-code hash (frozen before ADR 0029): refreeze")
    elif code != research_code_identity()["code_hash"]:
        out.append("evaluation code changed after the freeze (FROZEN_CODE_CHANGED): refreeze or "
                   "restore the frozen code")
    return out


def evaluate(prereg_path: Path, *, activation_path: Optional[Path] = None,
             members_path: Optional[Path] = None, git: Optional[GitProbe] = None) -> dict:
    """Lifecycle of one preregistration with every reason."""
    git = git or GitProbe()
    prereg_path = Path(prereg_path)
    raw = prereg_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    pre = json.loads(raw)
    schema = str(pre.get("schema") or "")
    status_txt = str(pre.get("status") or "")
    rep: dict = {"prereg": str(prereg_path), "prereg_sha256": sha, "schema": schema,
                 "draft_marks": [], "required_missing": [], "issues": [], "human_items": []}
    if schema.endswith("_DRAFT") or "DRAFT" in status_txt.upper() or "NOT IN FORCE" in status_txt.upper():
        rep["draft_marks"].append(f"schema/status marks a draft ({schema!r})")
    rule = REQUIRED_FIELDS.get(schema.removesuffix("_DRAFT"))
    if rule is None:
        rep["issues"].append(f"no required-field rule for schema {schema!r}")
    else:
        rep["required_missing"] = [f for f in rule["fields"] if _get(pre, f) in (None, "", [], {})]
    if not activation_path or not Path(activation_path).exists():
        rep["human_items"] = ["freeze the members file", "final read-through by the user",
                              "write and commit a prereg_activation_v1 record (approved_by, checklist)"]
        rep["lifecycle"] = "DRAFT"
        return rep
    act = json.loads(Path(activation_path).read_text(encoding="utf-8"))
    iss = rep["issues"]
    if act.get("schema") != ACTIVATION_SCHEMA:
        iss.append("activation record has the wrong schema")
    if act.get("record_sha256") != record_sha(act):
        iss.append("activation record does not match its own hash (edited after sealing)")
    if act.get("prereg_sha256") != sha:
        iss.append("activation names a different prereg sha256 (prereg edited after activation)")
    members = None
    if members_path and Path(members_path).exists():
        members = json.loads(Path(members_path).read_text(encoding="utf-8"))
        if act.get("members_sha256") != members.get("members_sha256"):
            iss.append("activation names a different members file")
        iss += check_members_file(members, sha)
    else:
        iss.append("members file missing")
    reviewed = bool(act.get("reviewed_by"))
    approved = bool(act.get("approved_by")) and bool(act.get("approved_at"))
    ck = act.get("checklist") or {}
    open_items = [k for k in CHECKLIST if ck.get(k) is not True]
    rep["human_items"] = ([] if approved else ["approval (approved_by / approved_at)"]) + \
        [f"checklist: {k}" for k in open_items]
    # git binding: committed, clean, last touched at or before the deadline
    deadline_field = (rule or {}).get("deadline")
    deadline = _get(pre, deadline_field) if deadline_field else None
    files = [prereg_path, Path(activation_path)] + ([Path(members_path)] if members_path else [])
    for f in files:
        if not git.committed_clean(f):
            iss.append(f"{f.name}: not committed or modified in the work tree")
            continue
        t = git.last_commit_time(f)
        if t is None:
            iss.append(f"{f.name}: no commit time")
        elif deadline and t >= _day_s_utc(deadline):
            iss.append(f"{f.name}: last commit {dt.datetime.fromtimestamp(t, dt.timezone.utc):%Y-%m-%dT%H:%MZ} "
                       f"is not before {deadline_field}={deadline}")
    if rep["draft_marks"] or rep["required_missing"]:
        iss += rep["draft_marks"] + [f"required field missing: {f}" for f in rep["required_missing"]]
    if iss:
        rep["lifecycle"] = "INVALID"
    elif approved and not open_items:
        rep["lifecycle"] = "ACTIVE"
    elif reviewed or approved:
        rep["lifecycle"] = "REVIEWED"
    else:
        rep["lifecycle"] = "DRAFT"
    rep["deadline"] = deadline
    return rep


def window_status(rep: Mapping, pre: Mapping, today: Optional[dt.date] = None) -> str:
    """Never 'started' unless ACTIVE — a date alone starts nothing."""
    if rep.get("lifecycle") != "ACTIVE":
        return f"NOT_STARTED (prereg {rep.get('lifecycle')})"
    start = _get(pre, "window.window_start") or _get(pre, "period.entry_start")
    end = _get(pre, "window.window_end") or _get(pre, "period.read_end")
    today = today or dt.datetime.now(dt.timezone.utc).date()
    if start and today < dt.date.fromisoformat(start):
        return "ACTIVE_BEFORE_WINDOW"
    if end and today > dt.date.fromisoformat(end):
        return "WINDOW_ENDED_AWAITING_EVALUATION"
    return "IN_WINDOW"


def require_active(prereg_path: Path, *, activation_path: Optional[Path], members_path: Optional[Path],
                   git: Optional[GitProbe] = None) -> dict:
    rep = evaluate(prereg_path, activation_path=activation_path, members_path=members_path, git=git)
    if rep["lifecycle"] != "ACTIVE":
        why = rep["issues"] or rep["human_items"] or rep["draft_marks"]
        raise GateRefused(f"preregistration is {rep['lifecycle']}, not ACTIVE: " + "; ".join(why))
    return rep


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="preregistration lifecycle gate (ADR 0029)")
    ap.add_argument("--prereg", required=True)
    ap.add_argument("--activation")
    ap.add_argument("--members")
    a = ap.parse_args(argv)
    rep = evaluate(Path(a.prereg), activation_path=Path(a.activation) if a.activation else None,
                   members_path=Path(a.members) if a.members else None)
    pre = json.loads(Path(a.prereg).read_text(encoding="utf-8"))
    rep["window_status"] = window_status(rep, pre)
    print(json.dumps(rep, indent=1, ensure_ascii=False, sort_keys=True))
    return 0 if rep["lifecycle"] == "ACTIVE" else 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
