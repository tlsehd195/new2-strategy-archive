"""Build / check the 2026-11-10 source audit table (ADR 0070).

Input : raw reviewer rows (one JSON object per file, any of the two shapes the
        reviewers wrote).  Output: SOURCE_AUDIT_20261110.jsonl (normalised) and
        SOURCE_AUDIT_20261110.md (counts + every CONFIRMED / POSSIBLE finding).

    python3 run/audit/source_audit_20261110.py build RAW1.jsonl [RAW2 ...]
    python3 run/audit/source_audit_20261110.py check      # CI: table vs inventory
    python3 run/audit/source_audit_20261110.py pin COMMIT # record the reviewed file versions
    python3 run/audit/source_audit_20261110.py refresh    # mark files changed since the review

The check fails when any script in RESEARCH_SCRIPTS_20261110.json is still only
INVENTORIED, when a row names a file that does not exist, when a row claims a level that is not allowed, or when lines_read is
less than lines_total for a FULL_SOURCE_REVIEW / LOGIC_TRACED / TESTED row,
and (ADR 0078) when a file differs from the reviewed version (sha256 at the
table commit) without a ``changed_since_review`` mark.
"""

from __future__ import annotations

import collections
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT_JSONL = HERE / "SOURCE_AUDIT_20261110.jsonl"
OUT_MD = HERE / "SOURCE_AUDIT_20261110.md"
INVENTORY = HERE / "RESEARCH_SCRIPTS_20261110.json"

LEVELS = ("FULL_SOURCE_REVIEW", "LOGIC_TRACED", "TESTED", "INVENTORIED", "NOT_REVIEWED", "BLOCKED")
READ_LEVELS = {"FULL_SOURCE_REVIEW", "LOGIC_TRACED", "TESTED"}
RISKS = ("leakage_risk", "cost_risk", "backtest_error_risk")
REVIEWER = ("independent read-only reviewer subagent (2026-11-10); rows are its claims. "
            "Rows with spot_checked=true were re-read by the thread author.")

# claims re-read line by line by the thread author (not only by the reviewer)
SPOT_CHECKED = {
    "alt_funding_r2_diag.py": "OOS span is the last 40% of the selection span (pseudo-OOS) - confirmed",
    "liquid_search_dev.py": "cross-check runs on ZECUSDT, which is not in TRANCHE_A - confirmed",
    "run/discovery/store.py": "pop_ready drop-before-promote + no orphan requeue - confirmed, fixed (ADR 0070)",
    "run/discovery/repro_bundle.py": "dev verify compared counts only - confirmed, fixed (ADR 0070)",
    "run/discovery/newlisting_collect.py": "corrupt state.json silently re-baselined - confirmed, fixed (ADR 0070)",
}
FIXED = {"run/discovery/store.py", "run/discovery/repro_bundle.py", "run/discovery/newlisting_collect.py"}


def _risk(row: dict, key: str) -> dict:
    v = row.get(key)
    ev_keys = (key.replace("_risk", "_evidence"), key.replace("_error_risk", "_evidence"))
    if isinstance(v, dict):
        level, detail = v.get("level", ""), v.get("evidence", "")
    else:
        s = str(v or "")
        level, _, detail = s.partition(":")
        for ev_key in dict.fromkeys(ev_keys):
            if row.get(ev_key):
                detail = (detail + " " + str(row[ev_key])).strip()
    head = level.strip().split()[0] if level.strip() else "UNKNOWN"
    if head not in ("NONE_FOUND", "POSSIBLE", "CONFIRMED"):
        head = "UNKNOWN"
    return {"level": head, "detail": (level.strip() + (": " + detail.strip() if detail.strip() else ""))}


def normalise(row: dict) -> dict:
    out = {
        "file": row["file"],
        "audit_level": row["audit_level"],
        "lines_total": row["lines_total"],
        "lines_read": row["lines_read"],
        "purpose": row.get("purpose"),
        "runnable_here": row.get("runnable_here"),
        "has_test": row.get("has_test"),
        "legacy_class": row.get("legacy_class"),
        "duplicate_of": row.get("duplicate_of"),
        "keep": row.get("keep"),
        "stale_paths_or_deps": row.get("stale_paths_or_deps"),
        "error_handling": row.get("error_handling"),
    }
    for k in RISKS:
        out[k] = _risk(row, k)
    for k in ("test_command", "result", "notes"):
        if row.get(k) is not None:
            out[k] = row[k]
    out["reviewer"] = REVIEWER
    out["spot_checked"] = row["file"] in SPOT_CHECKED
    if out["spot_checked"]:
        out["spot_check_note"] = SPOT_CHECKED[row["file"]]
    out["fixed_in"] = "ADR 0070" if row["file"] in FIXED else None
    return out


def render_md(rows: list[dict]) -> str:
    lv = collections.Counter(r["audit_level"] for r in rows)
    L = ["# 소스 감사 표 2026-11-10 (ADR 0070)", "",
         f"- 대상: {len(rows)}개 파일 (INVENTORIED였던 run/*.py 112개 + 핵심 모듈 12개)",
         "- 검토자: 읽기 전용 독립 검토 에이전트. 각 행은 검토자의 주장이다.",
         "  `spot_checked`가 참인 행만 작성자가 직접 다시 읽어 확인했다.",
         "- 모든 행에서 lines_read = lines_total(파일 전체를 읽음).",
         "- ADR 0078: 각 행은 검토한 파일 판본(`reviewed_against`: 표 작성 커밋의 sha256)에 묶인다.",
         "  그 뒤 바뀐 파일은 `검토 후 변경`에 표시되며, 그 행의 수준은 검토한 판본에만 해당한다"
         f" (현재 {sum(1 for r in rows if r.get('changed_since_review'))}개).", "",
         "## 감사 수준별 개수", "", "| 수준 | 개수 |", "|---|---|"]
    L += [f"| {k} | {lv.get(k, 0)} |" for k in LEVELS]
    L += ["", "## 위험 판정 개수", "", "| 항목 | NONE_FOUND | POSSIBLE | CONFIRMED | UNKNOWN |", "|---|---|---|---|---|"]
    for k in RISKS:
        c = collections.Counter(r[k]["level"] for r in rows)
        L.append(f"| {k} | {c['NONE_FOUND']} | {c['POSSIBLE']} | {c['CONFIRMED']} | {c['UNKNOWN']} |")
    L += ["", "## CONFIRMED 발견(전부)", ""]
    for r in rows:
        for k in RISKS:
            if r[k]["level"] == "CONFIRMED":
                tag = " [작성자 확인]" if r["spot_checked"] else ""
                fix = f" [수정: {r['fixed_in']}]" if r["fixed_in"] else ""
                L.append(f"- `{r['file']}` {k}{tag}{fix}: {r[k]['detail'][:600]}")
    L += ["", "## POSSIBLE 발견(전부)", ""]
    for r in rows:
        for k in RISKS:
            if r[k]["level"] == "POSSIBLE":
                fix = f" [수정: {r['fixed_in']}]" if r["fixed_in"] else ""
                L.append(f"- `{r['file']}` {k}{fix}: {r[k]['detail'][:400]}")
    L += ["", "## 파일별 수준", "",
          "| 파일 | 수준 | 줄 | 누수 | 비용 | 백테스트 | 작성자 확인 | 검토 후 변경 |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ch = r.get("changed_since_review")
        L.append(f"| `{r['file']}` | {r['audit_level']} | {r['lines_total']} | {r['leakage_risk']['level']} | "
                 f"{r['cost_risk']['level']} | {r['backtest_error_risk']['level']} | {'예' if r['spot_checked'] else ''} | "
                 f"{('예 (현재 ' + str(ch['lines_now']) + '줄)') if ch else ''} |")
    return "\n".join(L) + "\n"


def build(paths: list[str]) -> None:
    raw = [json.loads(line) for p in paths for line in Path(p).read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = sorted((normalise(r) for r in raw), key=lambda r: r["file"])
    OUT_JSONL.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    OUT_MD.write_text(render_md(rows), encoding="utf-8")
    print(f"wrote {len(rows)} rows")


def _path(f: str) -> Path:
    return REPO / f if "/" in f else REPO / "run" / f


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pin(commit: str) -> None:
    """ADR 0078 (H-06): record, per row, the file content the review describes
    (sha256 + line count of the file at ``commit``, the commit that wrote the
    table).  Rows whose file differs NOW get ``changed_since_review`` (the level
    then applies to the reviewed version only — a downgrade marker, never an
    upgrade).  Existing review fields are not touched."""
    rows = [json.loads(line) for line in OUT_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]
    for r in rows:
        rel = str(_path(r["file"]).relative_to(REPO))
        old = subprocess.run(["git", "-C", str(REPO), "show", f"{commit}:{rel}"],
                             capture_output=True, check=False).stdout
        r["reviewed_against"] = {"commit": commit, "sha256": _sha(old) if old else None,
                                 "lines": old.decode("utf-8", "replace").count("\n") if old else None}
        _mark(r)
    OUT_JSONL.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                         encoding="utf-8")
    OUT_MD.write_text(render_md(rows), encoding="utf-8")
    print(f"pinned {len(rows)} rows to {commit}; changed since review: "
          f"{sum(1 for r in rows if r.get('changed_since_review'))}")


def _mark(r: dict) -> None:
    ra = r.get("reviewed_against") or {}
    p = _path(r["file"])
    now = p.read_bytes() if p.exists() else b""
    if ra.get("sha256") and _sha(now) != ra["sha256"]:
        r["changed_since_review"] = {"sha256_now": _sha(now),
                                     "lines_now": now.decode("utf-8", "replace").count("\n"),
                                     "meaning": "file changed after the review: the audit level "
                                                "describes the reviewed version only"}
    else:
        r.pop("changed_since_review", None)


def refresh() -> None:
    """Re-mark ``changed_since_review`` against the current files (after a code
    change).  Levels and review fields are never changed."""
    rows = [json.loads(line) for line in OUT_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]
    for r in rows:
        _mark(r)
    OUT_JSONL.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                         encoding="utf-8")
    OUT_MD.write_text(render_md(rows), encoding="utf-8")
    print(f"changed since review: {sum(1 for r in rows if r.get('changed_since_review'))} of {len(rows)}")


def check() -> list[str]:
    rows = [json.loads(line) for line in OUT_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]
    errs = []
    # ADR 0078 (H-06): the table must say, for every reviewed file, whether the
    # file still is the reviewed version (the old check compared the stored
    # lines_read with the stored lines_total only, so changed files kept their
    # level silently)
    for r in rows:
        ra = r.get("reviewed_against") or {}
        if not ra.get("sha256"):
            errs.append(f"{r['file']}: no reviewed_against.sha256 (run: pin <commit>)")
            continue
        p = _path(r["file"])
        cur = _sha(p.read_bytes()) if p.exists() else None
        flagged = (r.get("changed_since_review") or {}).get("sha256_now")
        if cur != ra["sha256"] and flagged != cur:
            errs.append(f"{r['file']}: changed since the review and not marked (run: refresh)")
        if cur == ra["sha256"] and r.get("changed_since_review"):
            errs.append(f"{r['file']}: marked changed but equals the reviewed version (run: refresh)")
    files = [r["file"] for r in rows]
    if len(files) != len(set(files)):
        errs.append("duplicate rows")
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    left = sorted(r["file"] for r in inv if r["read_status"] == "INVENTORIED")
    if left:
        errs.append(f"scripts still only INVENTORIED (no whole-file read): {left}")
    known = {r["file"] for r in inv}
    stray = sorted(f for f in files if "/" not in f and f not in known)
    if stray:
        errs.append(f"audit rows for run/*.py files that do not exist: {stray}")
    for f in files:
        if "/" in f and not (REPO / f).exists():
            errs.append(f"audit row for a missing module: {f}")
    for r in rows:
        if r["audit_level"] not in LEVELS:
            errs.append(f"{r['file']}: unknown level {r['audit_level']}")
        if r["audit_level"] in READ_LEVELS and r["lines_read"] < r["lines_total"]:
            errs.append(f"{r['file']}: {r['audit_level']} but read {r['lines_read']}/{r['lines_total']} lines")
        for k in RISKS:
            if r[k]["level"] not in ("NONE_FOUND", "POSSIBLE", "CONFIRMED", "UNKNOWN"):
                errs.append(f"{r['file']}: bad {k}")
    return errs


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "build":
        build(sys.argv[2:])
    elif len(sys.argv) == 3 and sys.argv[1] == "pin":
        pin(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "refresh":
        refresh()
    elif len(sys.argv) == 2 and sys.argv[1] == "check":
        e = check()
        print("\n".join(e) if e else "source audit table OK")
        sys.exit(1 if e else 0)
    else:
        print(__doc__)
        sys.exit(2)
