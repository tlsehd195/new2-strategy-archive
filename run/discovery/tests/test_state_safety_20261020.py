"""State / ledger consistency and run safety (directive 2026-10-20 §7).

- ``ledger_check`` finds nothing on a clean synthetic run and finds each
  injected defect (impossible transition, broken chain, tampered evidence,
  duplicate evaluation / submission / result, summary mismatch, interrupted
  cycle).
- SIGTERM during an evolution cycle records ``CYCLE_FAILED`` (not a lingering
  ``CYCLE_RUNNING``) and the rerun resumes without evaluating anything twice.
- ``run_cycle.sh`` forwards SIGTERM to the runner and exits 1 with
  ``reason=signal``.
- ``--dev-only`` runs DEV and search only and never loads a validation window.
- ``outcome_label`` maps stored statuses to the directive vocabulary.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
sys.path.insert(0, str(DISC))
sys.path.insert(0, str(HERE))

import ledger_check  # noqa: E402
from test_pipeline import PipelineCase, SyntheticProvider, verified_fee_cost  # noqa: E402


def _lines(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def _append(p: Path, rec: dict) -> None:
    with p.open("a") as f:
        f.write(json.dumps(rec) + "\n")


class CycleCase(PipelineCase):
    def cycle(self, state, prov=None, **kw):
        from autoloop import run_evolution_cycle
        from search import SearchConfig
        return run_evolution_cycle(state, prov or SyntheticProvider(), verified_fee_cost(), symbols=["BTCUSDT"],
                                   search_cfg=SearchConfig(children_per_round=6, seed=3), seed_legacy=False,
                                   results_dir=self.td / "res", rounds=1, max_dev_evals=20, **kw)

    def kinds(self, state):
        return {f["kind"] for f in ledger_check.check(state)["findings"]}


class TestLedgerCheck(CycleCase):
    def setUp(self):
        super().setUp()
        self.st = self.td / "st"
        self.cycle(self.st)
        self.evo = self.st / "evolution"
        # the derived summary lives next to the source ledgers in a real state dir
        shutil.copy(self.td / "res" / "evolution_cycle_summary.json", self.evo / "evolution_cycle_summary.json")

    def test_clean_run_is_consistent(self):
        rep = ledger_check.check(self.st)
        self.assertEqual(rep["status"], "CONSISTENT", rep["findings"])
        self.assertEqual(ledger_check.main([str(self.st)]), 0)

    def test_impossible_transition_and_broken_chain(self):
        last = _lines(self.evo / "statuses.jsonl")[-1]
        _append(self.evo / "statuses.jsonl", {**last, "previous": last["status"], "status": "PAPER_PASSED"})
        _append(self.evo / "statuses.jsonl", {**last, "previous": "PENDING_NOTHING", "status": last["status"]})
        k = self.kinds(self.st)
        self.assertIn("IMPOSSIBLE_TRANSITION", k)
        self.assertIn("BROKEN_STATUS_CHAIN", k)
        self.assertEqual(ledger_check.main([str(self.st)]), 2)

    def test_tampered_and_duplicate_evaluation(self):
        ev = _lines(self.evo / "evaluations.jsonl")
        dev = next(r for r in ev if r["stage"] == "dev")
        _append(self.evo / "evaluations.jsonl", dev)                       # evaluated twice
        _append(self.evo / "evaluations.jsonl", {**dev, "stage": "dev2", "net_mean": 9.9})  # content edited
        k = self.kinds(self.st)
        self.assertIn("DUPLICATE_EVALUATION", k)
        self.assertIn("EVIDENCE_TAMPERED", k)

    def test_duplicate_submission_and_result(self):
        from test_pipeline import EDGE, NOISE
        st = self.td / "st2"
        p = self.pipe(SyntheticProvider(), state=st)
        self.register_all(p, [EDGE] + NOISE[:3])
        p.run_validation()
        vb = st / "evolution" / "validation_batches.jsonl"
        self.assertEqual(ledger_check.check(st)["status"], "CONSISTENT", ledger_check.check(st)["findings"])
        batches = _lines(vb)
        pre = next(b for b in batches if b["kind"] == "PREREGISTERED")
        res = next(b for b in batches if b["kind"] == "RESULT")
        _append(vb, {**pre, "batch_id": pre["batch_id"] + "-again"})
        _append(vb, res)
        _append(vb, {**res, "batch_id": "VB-nowhere"})
        k = self.kinds(st)
        self.assertIn("DUPLICATE_SUBMISSION", k)
        self.assertIn("DUPLICATE_RESULT", k)
        self.assertIn("ORPHAN_RESULT", k)

    def test_summary_mismatch_and_interrupted_cycle(self):
        sp = self.evo / "evolution_cycle_summary.json"
        s = json.loads(sp.read_text())
        sp.write_text(json.dumps({**s, "status_counts": {"RESEARCH_ONLY": 10 ** 6}}))
        self.assertIn("SUMMARY_MISMATCH", self.kinds(self.st))
        sp.write_text(json.dumps({**s, "status": "CYCLE_RUNNING"}))
        self.assertIn("INTERRUPTED_CYCLE", self.kinds(self.st))

    def test_unreadable_ledger(self):
        with (self.evo / "statuses.jsonl").open("a") as f:
            f.write("{not json\n")
        self.assertEqual(ledger_check.check(self.st)["status"], "UNREADABLE")


class TestInterruptAndResume(CycleCase):
    def test_sigterm_records_failed_and_rerun_has_no_duplicates(self):
        import pipeline
        import runner
        orig = pipeline.EvolutionPipeline.dev_evaluate
        calls = {"n": 0}

        def dev_then_term(self, *a, **k):
            out = orig(self, *a, **k)
            calls["n"] += 1
            if calls["n"] == 3:
                os.kill(os.getpid(), signal.SIGTERM)
            return out
        pipeline.EvolutionPipeline.dev_evaluate = dev_then_term
        self.addCleanup(setattr, pipeline.EvolutionPipeline, "dev_evaluate", orig)
        st = self.td / "st"
        old = runner._install_interrupt_handlers()
        try:
            with self.assertRaises(runner.CycleInterrupted):
                self.cycle(st)
        finally:
            runner._restore_handlers(old)
        s = json.loads((self.td / "res" / "evolution_cycle_summary.json").read_text())
        self.assertEqual(s["status"], "CYCLE_FAILED")
        self.assertIn("SIGTERM", s["notes"][0])
        pipeline.EvolutionPipeline.dev_evaluate = orig
        done = self.cycle(st)                                             # same command again
        self.assertNotEqual(done.status, "CYCLE_FAILED")
        rep = ledger_check.check(st)
        self.assertNotIn("DUPLICATE_EVALUATION", {f["kind"] for f in rep["findings"]}, rep["findings"])
        self.assertEqual(rep["status"], "CONSISTENT", rep["findings"])


class TestDevOnly(CycleCase):
    def test_dev_only_never_loads_validation_or_locked_data(self):
        prov = SyntheticProvider()
        s = self.cycle(self.td / "st", prov, confirmatory=False)
        self.assertGreater(s.dev_evaluated, 0)
        self.assertEqual(prov.locked_loads, 0)
        self.assertFalse((self.td / "st" / "evolution" / "validation_batches.jsonl").exists())
        self.assertTrue(any("DEV-only" in n for n in s.notes))
        self.assertTrue(s.status_counts)

    def test_parse_options(self):
        from runner import UsageError, parse_options
        self.assertTrue(parse_options(["--mode", "evolve", "--dev-only"])["dev_only"])
        self.assertFalse(parse_options(["--mode", "evolve"]).get("dev_only", False))
        with self.assertRaises(UsageError):
            parse_options(["--mode", "legacy", "--dev-only"])


class TestOutcomeLabel(unittest.TestCase):
    def test_mapping(self):
        from pipeline import OUTCOME_LABELS, outcome_label
        cases = [
            (None, {}, "PENDING"),
            (None, {"dev": {"status": "BLOCKED_DATA"}}, "DATA_MISSING"),
            ({"status": "RESEARCH_ONLY", "stage": "dev"}, {}, "PENDING"),
            ({"status": "VALIDATION_SURVIVOR", "stage": "validation"}, {}, "PASS"),
            ({"status": "BLOCKED_DATA", "stage": "dev"}, {}, "DATA_MISSING"),
            ({"status": "BLOCKED_STATISTICS", "stage": "dev"}, {}, "UNDETERMINED_POWER"),
            ({"status": "REJECTED", "stage": "validation"},
             {"validation": {"failure_class": "INSUFFICIENT_DATA"}}, "UNDETERMINED_POWER"),
            ({"status": "REJECTED", "stage": "validation"},
             {"validation": {"failure_class": "NO_EDGE_OUT_OF_SAMPLE"}}, "FAIL_STAT"),
            ({"status": "REJECTED", "stage": "validation"},
             {"validation": {"failure_class": "DATA_MISSING"}}, "DATA_MISSING"),
            ({"status": "VALIDATION_SURVIVOR", "stage": "validation"}, {"reverify": True}, "REVERIFY_PENDING"),
        ]
        for st, kw, want in cases:
            with self.subTest(st=st, kw=kw):
                got = outcome_label(st, **kw)
                self.assertEqual(got, want)
                self.assertIn(got, OUTCOME_LABELS)
        # data shortage is never reported as a statistical failure, nor as a pass
        self.assertNotIn(outcome_label({"status": "INCONCLUSIVE", "stage": "dev"}), ("PASS",))


class TestRunCycleSignal(unittest.TestCase):
    def test_sigterm_is_forwarded_and_cycle_fails(self):
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, True)
        started, got = td / "started", td / "got_term"
        fake = td / "fake_py.sh"
        fake.write_text("#!/bin/sh\n"
                        f"trap 'echo term > {got}; exit 143' TERM\n"
                        f"echo up > {started}\n"
                        "i=0; while [ $i -lt 600 ]; do sleep 0.1; i=$((i+1)); done\nexit 0\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        root = td / "disc"
        env = dict(os.environ, NEW2_DISCOVERY_ROOT=str(root), PYTHON=str(fake), NEW2_DISC_MODE="evolve",
                   TMPDIR=str(td))
        p = subprocess.Popen(["bash", str(DISC / "run_cycle.sh"), "1"], env=env, cwd=str(td),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(200):
                if started.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(started.exists(), "fake runner never started")
            p.send_signal(signal.SIGTERM)
            out, err = p.communicate(timeout=30)
        finally:
            if p.poll() is None:
                p.kill()
        self.assertEqual(p.returncode, 1, out + err)
        self.assertIn("CYCLE_FAILED signal=TERM", out)
        self.assertNotIn("CYCLE_OK", out)
        self.assertTrue(got.exists(), "runner did not receive SIGTERM")
        self.assertIn("reason=signal", (root / "cycle_audit.log").read_text())
        self.assertEqual(sorted(x.name for x in td.glob("run_cycle_*")), [])     # temp files removed


if __name__ == "__main__":
    unittest.main()
