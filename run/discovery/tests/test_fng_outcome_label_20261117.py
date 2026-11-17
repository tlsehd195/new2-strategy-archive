"""ADR 0084 (C-5): the aggregate label of a legacy FNG PAPER_PASSED strategy.

``pipeline.outcome_label`` turned status PAPER_PASSED into PASS and
``evolution_report`` counted it as a survivor, although no forward plan can pin
FNG values (ADR 0079 G-04, C-4).  Reporting only: statuses, verdict logic and
thresholds are not touched.  SYNTHETIC records in temp directories only.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for _p in (str(DISC), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pipeline import OUTCOME_LABELS, outcome_label  # noqa: E402

PASSED = {"status": "PAPER_PASSED", "stage": "paper_forward"}
FNG_SPEC = {"data": ["fng"]}
PLAIN_SPEC = {"data": []}


class TestOutcomeLabelFng(unittest.TestCase):
    def test_fng_paper_passed_is_not_pass(self):
        got = outcome_label(PASSED, spec=FNG_SPEC)
        self.assertEqual(got, "UNVERIFIED")
        self.assertIn(got, OUTCOME_LABELS)

    def test_non_fng_paper_passed_still_pass(self):
        self.assertEqual(outcome_label(PASSED, spec=PLAIN_SPEC), "PASS")

    def test_unreadable_or_missing_spec_is_not_pass(self):
        self.assertEqual(outcome_label(PASSED, spec=None), "UNVERIFIED")
        self.assertEqual(outcome_label(PASSED, spec={"data": 5}), "UNVERIFIED")

    def test_other_statuses_unchanged_by_spec(self):
        for st in ("VALIDATION_SURVIVOR", "PAPER_TEST_READY", "PAPER_TESTING"):
            with self.subTest(st=st):
                self.assertEqual(outcome_label({"status": st, "stage": "validation"}, spec=FNG_SPEC), "PASS")

    def test_reverify_still_first(self):
        self.assertEqual(outcome_label(PASSED, spec=FNG_SPEC, reverify=True), "REVERIFY_PENDING")


def _w(p: Path, rows):
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


class TestReportFng(unittest.TestCase):
    def _state(self, td: Path) -> Path:
        evo = td / "evolution"
        evo.mkdir(parents=True)
        ctx = "ctx1"
        _w(evo / "contexts.jsonl", [{"context_id": ctx, "components": {}}])
        _w(evo / "specs.jsonl", [
            {"strategy_id": "S_FNG", "definition": FNG_SPEC, "source": "legacy:x"},
            {"strategy_id": "S_OK", "definition": PLAIN_SPEC, "source": "legacy:y"}])
        _w(evo / "statuses.jsonl", [
            {"strategy_id": s, "context_id": ctx, "status": "PAPER_PASSED", "stage": "paper_forward"}
            for s in ("S_FNG", "S_OK")])
        return td

    def test_report_does_not_count_fng_as_survivor(self):
        from evolution_report import build_report
        with tempfile.TemporaryDirectory() as t:
            reg = Path(t) / "reg.jsonl"
            rep = build_report(self._state(Path(t) / "s"), registry_path=reg)
        self.assertEqual(rep["outcomes"]["total"].get("PASS"), 1)
        self.assertEqual(rep["outcomes"]["total"].get("UNVERIFIED"), 1)
        self.assertEqual(rep["survivors"], ["S_OK"])
        self.assertEqual(rep["unverified_prior_conclusions"], ["S_FNG"])
        # the stored status is untouched: the report is a view
        self.assertEqual(rep["statuses"], {"PAPER_PASSED": 2})


if __name__ == "__main__":
    unittest.main()
