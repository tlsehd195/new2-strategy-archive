"""Directive 2026-10-20 §3 — the archive runs and tests on its own.

1. the cost model is the archive's own (``cost_model.py``) and gives the
   pinned numbers of the formula it replaced (reference cross-check:
   ``run/audit/evidence_independence_20261020/cost_model_crosscheck.txt``)
2. no engine file imports, checks out or assumes a path of the main
   trading repository; the CI workflow checks out this repository only
3. the engine imports in a fresh interpreter with the old package blocked
   and no ``COINTRADER_SRC``
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
REPO = DISC.parent.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import costs_bridge  # noqa: E402
from cost_model import CandleCostModel  # noqa: E402


class CostModelTests(unittest.TestCase):
    # values of fee + half_spread + impact*sqrt(participation); identical (==) to
    # the replaced implementation on 200 000 random inputs (evidence file above)
    GOLDEN = [((0.0005, 0.0002, 0.1, 0.05), 10_000, 1e6, 0.010700000000000001),
              ((0.0005, 0.0002, 0.1, 0.05), 10_000, 1e7, 0.0038622776601683795),
              ((0.0005, 0.0002, 0.1, 0.05), 1, 1e9, 0.0007031622776601684),
              ((0.0005, 0.0002, 0.1, 0.05), 50_000, 1e6, 0.023060679774997896),
              ((0.0005, 0.000192, 0.0010013, 0.05), 10_000, 2e7, 0.0007143897486587054)]

    def test_golden_values(self):
        for params, ov, bv, want in self.GOLDEN:
            self.assertEqual(CandleCostModel(*params).cost_fraction(ov, bv), want)

    def test_refusals_are_not_priced(self):
        m = CandleCostModel(0.0005, 0.0002, 0.1, 0.05)
        self.assertEqual(m.cost_fraction(0, 1e6), 0.0)
        for ov, bv in ((-1, 1e6), (10, 0), (10, -5), (60_000, 1e6)):
            with self.assertRaises(ValueError):
                m.cost_fraction(ov, bv)
        self.assertGreater(m.cost_fraction(50_000, 1e6), 0)       # exactly at the cap is priced

    def test_no_implicit_defaults(self):
        with self.assertRaises(TypeError):
            CandleCostModel()                                     # every parameter must be stated

    def test_identity_is_this_repository_file(self):
        self.assertEqual(Path(costs_bridge.COST_MODEL_SOURCE_PATH).resolve(), DISC / "cost_model.py")
        self.assertTrue(costs_bridge.COST_MODEL_VERSION.startswith("archive.cost_model.CandleCostModel@sha256:"))
        self.assertIs(costs_bridge.CandleCostModel, CandleCostModel)


class NoMainRepositoryDependencyTests(unittest.TestCase):
    FORBIDDEN_IMPORTS = {"cointrader"}
    FORBIDDEN_TEXT = re.compile(r"COINTRADER_SRC|NEW2_public_clone|tlsehd195/NEW2\b(?!-)")

    def _engine_files(self):
        return sorted(p for p in DISC.rglob("*.py") if "tests" not in p.parts)

    def test_engine_does_not_import_the_main_repository(self):
        bad = []
        for p in self._engine_files():
            for node in ast.walk(ast.parse(p.read_text())):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                bad += [f"{p.name}: {n}" for n in names if n.split(".")[0] in self.FORBIDDEN_IMPORTS]
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "import_module":
                    bad.append(f"{p.name}: dynamic import_module")
        self.assertEqual(bad, [])

    def test_engine_code_names_no_main_repository_path(self):
        """Every string the engine can USE (docstrings and comments excluded:
        they may name the provenance) and every line of the cycle script."""
        bad = []
        for p in self._engine_files():
            tree = ast.parse(p.read_text())
            docs = {id(n.body[0].value) for n in ast.walk(tree)
                    if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.body and isinstance(n.body[0], ast.Expr)
                    and isinstance(n.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in docs and self.FORBIDDEN_TEXT.search(node.value)):
                    bad.append(f"{p.name}:{node.lineno}: {node.value[:60]!r}")
        for i, line in enumerate((DISC / "run_cycle.sh").read_text().splitlines(), 1):
            if self.FORBIDDEN_TEXT.search(line):
                bad.append(f"run_cycle.sh:{i}: {line.strip()}")
        self.assertEqual(bad, [])

    def test_ci_checks_out_this_repository_only(self):
        for wf in sorted((REPO / ".github" / "workflows").glob("*.yml")):
            text = wf.read_text()
            self.assertNotRegex(text, r"(?m)^\s*repository:", wf.name)
            self.assertNotIn("NEW2_READ_TOKEN", text, wf.name)
            self.assertNotIn("COINTRADER_SRC", text, wf.name)

    def test_engine_imports_with_the_old_package_blocked(self):
        code = ("import sys; sys.modules['cointrader'] = None\n"
                "import costs_bridge, cost_calibration, fills_import, pipeline, runner\n"
                "print(costs_bridge.COST_MODEL_VERSION)")
        env = {k: v for k, v in os.environ.items() if k != "COINTRADER_SRC"}
        r = subprocess.run([sys.executable, "-c", code], cwd=DISC, env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("archive.cost_model.CandleCostModel@sha256:", r.stdout)


if __name__ == "__main__":
    unittest.main()
