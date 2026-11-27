"""Code audit registry built from the git tree (ADR 0116, directive §10).

One row per tracked code file (``*.py``, ``*.ts``, ``*.sh``, workflow ``*.yml``,
``src/*`` manifests and ``tsconfig.json``) with its sha256, kind, review
status, the sha256 the review was made at, who claims the review, and the
test files that reference it.  ``CODE_AUDIT_REGISTRY.json`` is the record;
``CODE_AUDIT_REGISTRY.md`` the summary.

    python3 run/audit/code_audit_registry.py init          # once: seed from SOURCE_AUDIT_20261110 (ADR 0070)
    python3 run/audit/code_audit_registry.py build         # refresh hashes / tests, mark changes
    python3 run/audit/code_audit_registry.py review PATH STATUS --by WHO --note TEXT
    python3 run/audit/code_audit_registry.py check         # CI gate

Statuses (honest by construction):
  FULL_SOURCE_REVIEW   every line was read at ``reviewed_sha256``
  LOGIC_TRACED         the data / control flow that decides results was followed
  TESTED               read as LOGIC_TRACED and dedicated tests exercise it
  PARTIALLY_REVIEWED   only the parts named in ``note`` were read
  INVENTORIED          listed, header / imports / risk patterns scanned only
  NOT_REVIEWED         no per-file review record exists
  CHANGED_SINCE_REVIEW the file differs from ``reviewed_sha256``; ``prior_status`` keeps the old claim
  EXEMPTED_WITH_REASON not reviewed on purpose; ``note`` says why

``build`` never upgrades a status and never moves ``reviewed_sha256``: only
``review`` does, for one named file, with a reviewer and a note.  A changed
file is marked CHANGED_SINCE_REVIEW automatically.

``check`` fails when a tracked code file is not in the registry (new and
unclassified), when a row names a file that no longer exists (moved or
deleted), when a row's sha256 differs from the file (registry stale: run
``build``), when a read status does not match the file at its review hash,
when a status is unknown, when NOT_REVIEWED / EXEMPTED / PARTIALLY_REVIEWED
has no note, and when a source file added after the baseline commit has no
test that references it and is not EXEMPTED_WITH_REASON.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT_JSON = HERE / "CODE_AUDIT_REGISTRY.json"
OUT_MD = HERE / "CODE_AUDIT_REGISTRY.md"
SEED = HERE / "SOURCE_AUDIT_20261110.jsonl"
VERSION = "code-audit-registry-v1"
BASELINE_COMMIT = "d564456a7df8adf02c64796cec1a3784ea3d0cf3"

STATUSES = ("FULL_SOURCE_REVIEW", "LOGIC_TRACED", "TESTED", "PARTIALLY_REVIEWED", "INVENTORIED",
            "NOT_REVIEWED", "CHANGED_SINCE_REVIEW", "EXEMPTED_WITH_REASON")
READ = {"FULL_SOURCE_REVIEW", "LOGIC_TRACED", "TESTED", "PARTIALLY_REVIEWED"}
NEEDS_NOTE = {"NOT_REVIEWED", "EXEMPTED_WITH_REASON", "PARTIALLY_REVIEWED"}
CODE_RE = re.compile(r"(\.py|\.ts|\.sh)$|^\.github/workflows/.*\.ya?ml$|^src/[^/]+\.(json|prisma)$")
SEED_BY = "SOURCE_AUDIT_20261110 (independent reviewer subagent claim, ADR 0070; not author-verified)"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout


def tracked() -> list[str]:
    """Tracked files plus untracked, not ignored ones (a new file is covered before its commit)."""
    out = git("ls-files", "--cached", "--others", "--exclude-standard").split("\n")
    return sorted({p for p in out if p and CODE_RE.search(p) and (REPO / p).is_file()})


def baseline_paths() -> set[str] | None:
    try:
        return set(git("ls-tree", "-r", "--name-only", BASELINE_COMMIT).split())
    except subprocess.CalledProcessError:
        return None                    # shallow clone without the baseline: cannot tell


def sha256(p: str) -> str:
    return hashlib.sha256((REPO / p).read_bytes()).hexdigest()


def kind(p: str) -> str:
    name = p.rsplit("/", 1)[-1]
    if p.startswith(".github/"):
        return "workflow"
    if name.startswith("test_") or "/tests/" in p or name.endswith(".test.ts"):
        return "test"
    if name.endswith((".json", ".prisma")):
        return "config"
    return "source"


def references(paths: list[str]) -> dict[str, list[str]]:
    """source path -> test files that import it or name it."""
    tests = [p for p in paths if kind(p) == "test"]
    text = {t: (REPO / t).read_text(encoding="utf-8", errors="replace") for t in tests}
    out: dict[str, list[str]] = {}
    for p in paths:
        if kind(p) != "source":
            continue
        stem = Path(p).stem
        if p.endswith(".ts"):
            pat = re.compile(r"""from\s+["'][./]*(?:\.\./)*""" + re.escape(stem) + r"""["']""")
        else:
            pat = re.compile(r"(?:^|\s)(?:import|from)\s+(?:[\w.]+\.)?" + re.escape(stem) + r"\b|"
                             + re.escape(Path(p).name), re.M)
        out[p] = sorted(t for t, s in text.items() if pat.search(s))
    return out


def load() -> dict:
    return json.loads(OUT_JSON.read_text(encoding="utf-8")) if OUT_JSON.exists() else {}


def save(reg: dict) -> None:
    reg["files"] = sorted(reg["files"], key=lambda r: r["path"])
    OUT_JSON.write_text(json.dumps(reg, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(reg), encoding="utf-8")


def seed_rows() -> dict[str, dict]:
    rows = {}
    for line in SEED.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            path = r["file"] if "/" in r["file"] else f"run/{r['file']}"
            rows[path] = r
    return rows


def new_row(p: str, base: set[str] | None) -> dict:
    return {"path": p, "kind": kind(p), "status": "NOT_REVIEWED", "reviewed_sha256": None, "review_by": None,
            "note": "no per-file review record", "prior_status": None,
            # a file the registry has not seen is new by definition; the baseline tree
            # (when the clone has it) only refines that for the initial build
            "added_after_baseline": True if base is None else p not in base}


def refresh(reg: dict) -> dict:
    paths = tracked()
    base = baseline_paths()
    refs = references(paths)
    rows = {r["path"]: r for r in reg.get("files", [])}
    removed = [p for p in rows if p not in set(paths)]
    for p in removed:
        reg.setdefault("removed", []).append({"path": p, "last_status": rows[p]["status"],
                                              "last_sha256": rows[p]["sha256"]})
        del rows[p]
    for p in paths:
        r = rows.get(p) or new_row(p, base)
        r["sha256"], r["lines"], r["kind"] = sha256(p), (REPO / p).read_bytes().count(b"\n"), kind(p)
        r["tests"] = refs.get(p, [])
        if r.get("added_after_baseline") is None and base is not None:
            r["added_after_baseline"] = p not in base
        if r["status"] in READ and r["reviewed_sha256"] != r["sha256"]:
            r["prior_status"], r["status"] = r["status"], "CHANGED_SINCE_REVIEW"
        elif r["status"] == "CHANGED_SINCE_REVIEW" and r["reviewed_sha256"] == r["sha256"]:
            r["status"], r["prior_status"] = r["prior_status"], None    # change reverted
        rows[p] = r
    reg.update(version=VERSION, baseline_commit=BASELINE_COMMIT, files=list(rows.values()))
    return reg


def cmd_init() -> int:
    if OUT_JSON.exists():
        print("registry exists; use build / review", file=sys.stderr)
        return 1
    reg = refresh({})
    seeds = seed_rows()
    inv = {f"run/{x['file']}": x["read_status"]
           for x in json.loads((HERE / "RESEARCH_SCRIPTS_20261110.json").read_text(encoding="utf-8"))}
    for r in reg["files"]:
        s = seeds.get(r["path"])
        if r["path"] in inv and not s:
            r["note"] = (f"no hash-bound per-file review; RESEARCH_SCRIPTS_20261110 lists read_status="
                         f"{inv[r['path']]} (TESTED there means a run/test_*.py exists, not a review)")
        if s:
            ra = s.get("reviewed_against") or {}
            r.update(status=s["audit_level"], reviewed_sha256=ra.get("sha256"), review_by=SEED_BY,
                     note=f"reviewed at commit {ra.get('commit')}", prior_status=None)
    save(refresh(reg))
    return 0


def cmd_build() -> int:
    save(refresh(load()))
    return 0


def cmd_review(path: str, status: str, by: str, note: str) -> int:
    if status not in STATUSES or status == "CHANGED_SINCE_REVIEW":
        print(f"status must be one of {STATUSES[:-2] + STATUSES[-1:]}", file=sys.stderr)
        return 1
    reg = refresh(load())
    row = next((r for r in reg["files"] if r["path"] == path), None)
    if row is None:
        print(f"{path}: not a tracked code file", file=sys.stderr)
        return 1
    if status in NEEDS_NOTE and not note.strip():
        print(f"{status} needs --note", file=sys.stderr)
        return 1
    row.update(status=status, reviewed_sha256=row["sha256"] if status in READ else None, review_by=by,
               note=note, prior_status=None)
    save(reg)
    return 0


def problems(reg: dict) -> list[str]:
    out = []
    if reg.get("version") != VERSION:
        return [f"registry version {reg.get('version')!r} != {VERSION}"]
    paths = set(tracked())
    rows = {r["path"]: r for r in reg["files"]}
    for p in sorted(paths - set(rows)):
        out.append(f"UNREGISTERED {p}: new code file not classified (run build, then review)")
    for p in sorted(set(rows) - paths):
        out.append(f"MISSING {p}: registered file no longer exists (moved or deleted; run build)")
    for p in sorted(paths & set(rows)):
        r = rows[p]
        if r["sha256"] != sha256(p):
            out.append(f"STALE {p}: file changed after the registry was built (run build)")
        if r["status"] not in STATUSES:
            out.append(f"BAD_STATUS {p}: {r['status']!r}")
        if r["status"] in READ and r["reviewed_sha256"] != r["sha256"]:
            out.append(f"HASH_MISMATCH {p}: {r['status']} claimed for another version")
        if r["status"] == "CHANGED_SINCE_REVIEW" and not r.get("prior_status"):
            out.append(f"BAD_ROW {p}: CHANGED_SINCE_REVIEW without prior_status")
        if r["status"] in NEEDS_NOTE and not (r.get("note") or "").strip():
            out.append(f"NO_NOTE {p}: {r['status']} needs a note")
        if (r["kind"] == "source" and r.get("added_after_baseline") and not r.get("tests")
                and r["status"] != "EXEMPTED_WITH_REASON"):
            out.append(f"UNTESTED_NEW_CODE {p}: added after {BASELINE_COMMIT[:7]} without a test that references it")
    return out


def cmd_check() -> int:
    reg = load()
    if not reg:
        print("CODE_AUDIT_REGISTRY.json missing", file=sys.stderr)
        return 1
    bad = problems(reg)
    if not bad and OUT_MD.read_text(encoding="utf-8") != render_md(reg):
        bad.append("CODE_AUDIT_REGISTRY.md is not generated from the JSON (run build)")
    for b in bad:
        print(b)
    c = collections.Counter(r["status"] for r in reg["files"])
    print(f"{len(reg['files'])} files: " + ", ".join(f"{k} {c[k]}" for k in STATUSES if c[k])
          + ("" if not bad else f" — {len(bad)} problem(s)"))
    return 1 if bad else 0


def render_md(reg: dict) -> str:
    rows = reg["files"]
    c = collections.Counter(r["status"] for r in rows)
    by_kind = collections.Counter((r["kind"], r["status"]) for r in rows)
    out = ["# Code audit registry (ADR 0116)", "",
           f"Generated by `run/audit/code_audit_registry.py` from the git tree; baseline commit "
           f"`{reg['baseline_commit'][:12]}`. The JSON is the record; this page is its summary.", "",
           "A status is a claim about the file at `reviewed_sha256` only. `CHANGED_SINCE_REVIEW` is set "
           "automatically when the file changes; `review_by` names who makes the claim (rows seeded from "
           "SOURCE_AUDIT_20261110 are an independent reviewer's claims, not author-verified).", "",
           "| status | files |", "|---|---|"]
    out += [f"| {k} | {c[k]} |" for k in STATUSES if c[k]]
    out += ["", "| kind | status | files |", "|---|---|---|"]
    out += [f"| {k} | {s} | {n} |" for (k, s), n in sorted(by_kind.items())]
    out += ["", "## Files", "", "| path | kind | status | prior | tests | review by | note |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        note = (r.get("note") or "").replace("|", "/")[:160]
        by = (r.get("review_by") or "").split(" (")[0]
        out.append(f"| `{r['path']}` | {r['kind']} | {r['status']} | {r.get('prior_status') or ''} | "
                   f"{len(r.get('tests') or [])} | {by} | {note} |")
    if reg.get("removed"):
        out += ["", "## Removed or moved files", ""]
        out += [f"- `{x['path']}` (last status {x['last_status']})" for x in reg["removed"]]
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("build")
    sub.add_parser("check")
    rv = sub.add_parser("review")
    rv.add_argument("path")
    rv.add_argument("status")
    rv.add_argument("--by", required=True)
    rv.add_argument("--note", default="")
    a = ap.parse_args(argv)
    if a.cmd == "review":
        return cmd_review(a.path, a.status, a.by, a.note)
    return {"init": cmd_init, "build": cmd_build, "check": cmd_check}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
