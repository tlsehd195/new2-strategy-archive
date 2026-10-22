"""Directive 2026-10-23 (ADR 0034): the frozen v1 history passes the ledger
check (it was reported unreadable), draft preregistrations report a members
file that fails its freeze, the CI runs the ledger check, tests import nothing
from another repository, and the real repository holds no completed
independent validation.  Temp dirs and committed files only — no window is
opened, no network, no account."""
from __future__ import annotations

import ast
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
REPO = DISC.parent.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import ledger_check  # noqa: E402
import prereg_gate as pg  # noqa: E402

IW1_PREREG = DISC / "config" / "independent_window_v1.json"
IW1_MEMBERS = REPO / "run" / "audit" / "evidence_independent_window_20261021" / "IW1_members.json"


class TestV1Archive(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td)
        for f in ("state.json", "discovery_ledger.jsonl"):
            shutil.copy(DISC / f, self.td / f)

    def _state(self) -> dict:
        return json.loads((self.td / "state.json").read_text())

    def _write(self, st: dict):
        (self.td / "state.json").write_text(json.dumps(st))

    def test_committed_history_is_consistent(self):
        rep = ledger_check.check(DISC)
        self.assertEqual(rep["status"], "CONSISTENT", rep["findings"])
        self.assertEqual(self._state()["engine"], ledger_check.V1_ENGINE)

    def test_cache_drift_is_found(self):
        for key, val in (("tested", 135), ("tested_rows", 161), ("cycle", 18),
                         ("last_ts", "2026-10-16T00:00:00Z")):
            st = json.loads((DISC / "state.json").read_text())
            st[key] = val
            self._write(st)
            rep = ledger_check.check(self.td)
            self.assertEqual(rep["counts"], {"V1_ARCHIVE_MISMATCH": 1}, key)

    def test_survivor_without_survivor_verdict_is_found(self):
        st = self._state()
        st["survivors"] = ["d938c1ae6b316c47"]          # a FAIL_COST candidate
        st["promotion_queue"] = ["d938c1ae6b316c47"]
        self._write(st)
        rep = ledger_check.check(self.td)
        self.assertEqual(rep["counts"], {"V1_ARCHIVE_MISMATCH": 2})

    def test_extra_ledger_row_is_found(self):
        rows = (self.td / "discovery_ledger.jsonl").read_text().splitlines()
        (self.td / "discovery_ledger.jsonl").write_text("\n".join(rows + [rows[-1]]) + "\n")
        rep = ledger_check.check(self.td)
        self.assertIn("V1_ARCHIVE_MISMATCH", rep["counts"])

    def test_corrupt_ledger_line_is_unreadable(self):
        with open(self.td / "discovery_ledger.jsonl", "a") as f:
            f.write('{"broken": \n')
        self.assertEqual(ledger_check.check(self.td)["status"], "UNREADABLE")


class TestDraftMembersReport(unittest.TestCase):
    def test_real_iw1_members_need_a_refreeze(self):
        rep = pg.evaluate(IW1_PREREG, members_path=IW1_MEMBERS)
        self.assertEqual(rep["lifecycle"], "DRAFT")
        self.assertTrue(any("refreeze" in x for x in rep["members_issues"]), rep["members_issues"])
        self.assertTrue(pg.window_status(rep, json.loads(IW1_PREREG.read_text())).startswith("NOT_STARTED"))

    def test_tampered_members_reported_but_still_only_draft(self):
        with tempfile.TemporaryDirectory() as td:
            m = json.loads(IW1_MEMBERS.read_text())
            m["members"] = m["members"][:-1]
            p = Path(td) / "m.json"
            p.write_text(json.dumps(m))
            rep = pg.evaluate(IW1_PREREG, members_path=p)
        self.assertEqual(rep["lifecycle"], "DRAFT")
        self.assertTrue(any("does not match its hash" in x for x in rep["members_issues"]))

    def test_no_members_no_report(self):
        self.assertNotIn("members_issues", pg.evaluate(IW1_PREREG))


class TestVolScaledSpread(unittest.TestCase):
    def test_vol_scenario_is_costlier_and_spares_calm_entries(self):
        import cost_sensitivity as cs
        ev = [{"symbol": "X", "entry_ts": t, "gross_bp": 10.0, "cost_bp": 12.0, "funding_bp": 0.0,
               "net_bp": -2.0} for t in (1, 2, 3)]
        ratio = {("X", 1): 0.5, ("X", 2): 1.0, ("X", 3): 3.0}
        row = cs.vol_scaled_spread_row(ev, ratio, 0.0005, 0, {1: "low_vol", 2: "low_vol", 3: "high_vol"})
        self.assertEqual(row["n"], 3)
        self.assertAlmostEqual(row["cost_bp"], 12.0 + (2 * 5.0 * 2.0) / 3)    # only the 3x event pays more
        self.assertAlmostEqual(row["net_bp_low_vol"], -2.0)
        self.assertAlmostEqual(row["net_bp_high_vol"], -22.0)
        self.assertIn(cs.VOL_SCENARIO, cs.COSTLIER)                          # part of the monotone check


class TestRepositoryState(unittest.TestCase):
    def test_no_completed_independent_validation_exists(self):
        """Nothing in the repository records a started or completed
        independent validation, and no activation record exists."""
        self.assertFalse((DISC / "validation_registry.jsonl").exists())
        acts = [p for p in REPO.rglob("*.json") if ".git" not in p.parts
                and '"prereg_activation_v1"' in p.read_text(encoding="utf-8", errors="replace")]
        self.assertEqual(acts, [])
        iw1_rows = [json.loads(x) for x in (DISC / "validation_window_history.jsonl").read_text().splitlines()
                    if x.strip()]
        self.assertFalse(any(str(r.get("run", "")).startswith("IW1") or "IW1" in json.dumps(r.get("window_keys"))
                             for r in iw1_rows))


class TestCiAndTestIndependence(unittest.TestCase):
    def test_ci_runs_the_ledger_check(self):
        wf = (REPO / ".github" / "workflows" / "research-ci.yml").read_text()
        self.assertIn("ledger_check.py . ", wf)
        self.assertIn("ledger_check.py state", wf)

    def test_tests_import_nothing_from_another_repository(self):
        files = sorted(HERE.glob("test_*.py")) + sorted((DISC.parent).glob("test_*.py"))
        bad = []
        for p in files:
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                bad += [f"{p.name}: {n}" for n in names if n.split(".")[0] in ("cointrader", "src")]
                # a path into another checkout handed to Path/open/environ (the
                # needle lists of the independence checks themselves are not paths)
                fn = getattr(node, "func", None) if isinstance(node, ast.Call) else None
                if fn is not None and (getattr(fn, "id", "") in ("Path", "open", "PurePath")
                                       or getattr(fn, "attr", "") in ("joinpath", "getenv", "get")):
                    for a in node.args:
                        if isinstance(a, ast.Constant) and isinstance(a.value, str) and (
                                a.value.startswith(("/home/z/", "../NEW2")) or "NEW2_public_clone" in a.value
                                or a.value == "COINTRADER_SRC"):
                            bad.append(f"{p.name}:{node.lineno}: {a.value[:60]!r}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
