"""Who checked what, and how (ADR 0071, directive 2026-11-11 §9).

The 2026-11-10 source audit table (SOURCE_AUDIT_20261110.jsonl) records
``lines_read == lines_total`` for every row.  That field is the REVIEWER's
claim; it is not a proof that a person or the thread author read the file,
and the table never covered the core engine modules (pipeline.py,
portfolio_multi.py, evaluator.py, costs_bridge.py, data_quality.py).  This
file separates the kinds of evidence per file:

  review_by            who made the claims in the row
  whole_file_claim     the reviewer CLAIMED a whole-file read (lines_read == lines_total)
  author_reverified    NONE | SPOT (named claims re-read) | PARTIAL (named line ranges) | FULL
  executed             a test or the script itself was run by the author in a thread (bool)
  has_test             a test file exercises it (from the reviewer row or the author)
  defect_reproduced    a defect was reproduced by a failing test before the fix (bool)
  fixed_in             ADR of the fix, or None
  regression_test      test that keeps the fix (or None)

    python3 run/audit/audit_provenance_20261111.py build   # writes AUDIT_PROVENANCE_20261111.{jsonl,md}
    python3 run/audit/audit_provenance_20261111.py check   # CI
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = HERE / "SOURCE_AUDIT_20261110.jsonl"
OUT_JSONL = HERE / "AUDIT_PROVENANCE_20261111.jsonl"
OUT_MD = HERE / "AUDIT_PROVENANCE_20261111.md"
REVERIFY = ("NONE", "SPOT", "PARTIAL", "FULL")

#: what the thread author of 2026-11-11 (ADR 0071) re-read itself.  FULL means
#: every line was read in this thread; PARTIAL names the ranges.  Nothing here
#: is copied from a reviewer.
AUTHOR_2026_11_11 = {
    "run/discovery/portfolio_multi.py": ("FULL", "all 808 lines before the change; rewritten parts "
                                         "re-read after the change", True, True,
                                         "ADR 0071", "tests/test_portfolio_core_defects_20261111.py"),
    "run/discovery/evaluator.py": ("FULL", "all 264 lines", True, True, "ADR 0071",
                                   "tests/test_portfolio_core_defects_20261111.py"),
    "run/discovery/funding_schedule.py": ("FULL", "new module (ADR 0071)", True, False, "ADR 0071",
                                          "tests/test_portfolio_core_defects_20261111.py"),
    "run/discovery/costs_bridge.py": ("FULL", "all 416 lines; no defect found", True, False, None, None),
    "run/discovery/pipeline.py": ("PARTIAL", "L55-120 constants/transitions, L160-182 config load, "
                                  "L245-300 data provider, L436-720 identity/study/DEV, L839-1000 "
                                  "validation batch, L1175-1810 robustness/portfolio/locked/promotion; "
                                  "not re-read: search/variation helpers, legacy seeding", True, True,
                                  "ADR 0071", "tests/test_portfolio_core_defects_20261111.py"),
    "run/discovery/data_quality.py": ("PARTIAL", "L400-521 funding loader / gaps / quality", True, False,
                                      None, None),
    "run/discovery/funding_cost.py": ("PARTIAL", "L1-200 (legacy pandas module, only imported by the "
                                      "DEPRECATED portfolio_sim.py); not in the runtime path", True, False,
                                      None, None),
    "run/discovery/tests/test_portfolio_multi_20261110.py": ("FULL", "all tests read and run", True, False,
                                                            None, None),
}
#: claims the 2026-11-10 author re-read (ADR 0070, unchanged)
SPOT_2026_11_10 = "SPOT (2026-11-10 author, named claims only)"


def build() -> list[dict]:
    rows = [json.loads(x) for x in SRC.read_text(encoding="utf-8").splitlines() if x.strip()]
    out = []
    for r in rows:
        f = r["file"]
        out.append({
            "file": f, "source": "SOURCE_AUDIT_20261110",
            "review_by": "independent reviewer subagent (2026-11-10)",
            "whole_file_claim": r["lines_read"] == r["lines_total"],
            "whole_file_claim_is": "reviewer claim, not author-verified",
            "audit_level_claimed": r["audit_level"],
            "author_reverified": "SPOT" if r.get("spot_checked") else "NONE",
            "author_note": (SPOT_2026_11_10 + ": " + r["spot_check_note"]) if r.get("spot_checked") else None,
            "executed": bool(r.get("test_command") or r.get("result")),
            "has_test": r.get("has_test") is not None and not str(r.get("has_test")).startswith("none"),
            "defect_reproduced": bool(r.get("fixed_in")),
            "fixed_in": r.get("fixed_in"),
            "regression_test": None,
        })
    seen = {r["file"] for r in out}
    for f, (lvl, note, executed, repro, fixed, reg) in sorted(AUTHOR_2026_11_11.items()):
        row = {"file": f, "source": "AUTHOR_2026_11_11", "review_by": "thread author 2026-11-11 (ADR 0071)",
               "whole_file_claim": lvl == "FULL", "whole_file_claim_is": "author read",
               "audit_level_claimed": "LOGIC_TRACED" if lvl == "FULL" else "PARTIAL",
               "author_reverified": lvl, "author_note": note, "executed": executed,
               "has_test": True, "defect_reproduced": repro, "fixed_in": fixed, "regression_test": reg}
        if f in seen:                       # keep the reviewer row, add the author's own
            row["source"] = "AUTHOR_2026_11_11 (also in SOURCE_AUDIT_20261110)"
        out.append(row)
    return out


def render_md(rows: list[dict]) -> str:
    n = len([r for r in rows if r["source"] == "SOURCE_AUDIT_20261110"])
    spot = [r["file"] for r in rows if r["author_reverified"] == "SPOT"]
    auth = [r for r in rows if r["source"].startswith("AUTHOR_2026_11_11")]
    lines = ["# 감사 증거 출처표 (2026-11-11, ADR 0071)", "",
             "`SOURCE_AUDIT_20261110`의 `lines_read == lines_total`은 **독립 검토 에이전트의 주장**이다. "
             "작성자가 파일 전체를 읽었다는 증거가 아니다. 이 표는 주장과 작성자 직접 확인을 분리한다.", "",
             f"- 검토 에이전트 행: {n}개 (모두 '전체 읽음' 주장, 작성자 미검증)",
             f"- 2026-11-10 작성자 부분 재확인(SPOT, 지목된 주장만): {len(spot)}개 — {', '.join(spot)}",
             f"- 2026-11-11 작성자 직접 재검토: {len(auth)}개 (아래)",
             "- 2026-11-10 표에는 핵심 엔진 모듈(pipeline, portfolio_multi, evaluator, costs_bridge, "
             "data_quality)의 행이 없었다. 아래 작성자 행이 처음이다.", "",
             "| 파일 | 작성자 재검토 | 범위 | 실행 | 결함 재현 | 수정 | 회귀 테스트 |", "|---|---|---|---|---|---|---|"]
    for r in auth:
        lines.append(f"| `{r['file']}` | {r['author_reverified']} | {r['author_note']} | "
                     f"{'예' if r['executed'] else '아니오'} | {'예' if r['defect_reproduced'] else '아니오'} | "
                     f"{r['fixed_in'] or '-'} | {r['regression_test'] or '-'} |")
    lines += ["", "나머지 행은 `AUDIT_PROVENANCE_20261111.jsonl` 참조."]
    return "\n".join(lines) + "\n"


def write() -> None:
    rows = build()
    OUT_JSONL.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                         encoding="utf-8")
    OUT_MD.write_text(render_md(rows), encoding="utf-8")
    print(f"wrote {len(rows)} rows")


def check() -> list[str]:
    errs = []
    want = build()
    have = [json.loads(x) for x in OUT_JSONL.read_text(encoding="utf-8").splitlines() if x.strip()]
    if have != want:
        errs.append("AUDIT_PROVENANCE_20261111.jsonl is stale: run `build`")
    if OUT_MD.read_text(encoding="utf-8") != render_md(want):
        errs.append("AUDIT_PROVENANCE_20261111.md is stale: run `build`")
    for r in have:
        if r["author_reverified"] not in REVERIFY:
            errs.append(f"{r['file']}: author_reverified {r['author_reverified']!r}")
        if r["source"] == "SOURCE_AUDIT_20261110" and r["author_reverified"] in ("FULL", "PARTIAL"):
            errs.append(f"{r['file']}: a reviewer row cannot claim an author re-read")
        if r["author_reverified"] == "FULL" and r["file"] not in AUTHOR_2026_11_11:
            errs.append(f"{r['file']}: FULL author re-read without an author entry")
        if "/" in r["file"] and not (REPO / r["file"]).exists():
            errs.append(f"{r['file']}: file missing")
        if r["defect_reproduced"] and not r["fixed_in"]:
            errs.append(f"{r['file']}: reproduced defect without a fix record")
    return errs


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "build":
        write()
    elif len(sys.argv) == 2 and sys.argv[1] == "check":
        e = check()
        print("\n".join(e) if e else "audit provenance OK")
        sys.exit(1 if e else 0)
    else:
        print(__doc__)
        sys.exit(2)
