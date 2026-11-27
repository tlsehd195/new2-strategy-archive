"""Ratchet on zero-fill patterns in research code (ADR 0116, directive §5 / §14).

Counts, per ``run/**/*.py`` file (tests excluded), the lines that replace a
missing value with zero: ``fillna(0``, ``nan_to_num(`` without a ``nan=``
sentinel argument, ``reindex(...fill_value=0``.  The counts at ADR 0116 are
recorded in ``ZERO_FILL_BASELINE.json``: those lines are LEGACY and NOT
claimed to be correct (many are benign display / ranking code, some are not;
see ADR 0116 "UNRESOLVED").  ``check`` fails when a file gets MORE such lines
than its baseline (or a new file has any), so new code cannot add a silent
zero fill.  A line may opt out with the comment ``# zero-fill-ok: <reason>``
when zero is a real observation (e.g. "no position -> 0 PnL").

    python3 run/audit/zero_fill_scan.py check
    python3 run/audit/zero_fill_scan.py baseline   # only to LOWER counts after a fix
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
BASELINE = HERE / "ZERO_FILL_BASELINE.json"
PATTERN = re.compile(r"fillna\(\s*0|nan_to_num\((?![^)]*nan\s*=)|fill_value\s*=\s*0(?![.\d]*[1-9])")
OPT_OUT = "# zero-fill-ok:"


def files(root: Path = RUN) -> list[Path]:
    return sorted(p for p in root.rglob("*.py")
                  if "/tests/" not in p.as_posix() and not p.name.startswith("test_") and "__pycache__" not in p.parts and p.name != "zero_fill_scan.py")


def count(p: Path) -> int:
    return sum(1 for line in p.read_text(encoding="utf-8", errors="replace").splitlines()
               if PATTERN.search(line.split("#", 1)[0]) and OPT_OUT not in line)


def scan(root: Path = RUN) -> dict[str, int]:
    return {p.relative_to(root.parent).as_posix(): n for p in files(root) if (n := count(p))}


def problems(now: dict[str, int], base: dict[str, int]) -> list[str]:
    return [f"{f}: {n} zero-fill line(s), baseline {base.get(f, 0)} (new silent zero fill?)"
            for f, n in sorted(now.items()) if n > base.get(f, 0)]


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "check"
    now = scan()
    if cmd == "baseline":
        old = json.loads(BASELINE.read_text()) if BASELINE.exists() else None
        if old is not None and problems(now, old):
            print("refusing: counts would go UP; fix the code or add '# zero-fill-ok: <reason>'", file=sys.stderr)
            return 1
        BASELINE.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n")
        return 0
    bad = problems(now, json.loads(BASELINE.read_text()))
    for b in bad:
        print(b)
    print(f"zero-fill scan: {sum(now.values())} legacy line(s) in {len(now)} file(s); {len(bad)} increase(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
