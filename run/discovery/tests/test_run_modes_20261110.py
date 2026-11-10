"""ADR 0065: read-only run modes, a strict search-config file, DEV series
integrity and the meaning of a cycle status.  Synthetic state only."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger_check  # noqa: E402
import runner  # noqa: E402
from autoloop import EvolutionCycleSummary  # noqa: E402
from runner import UsageError, load_search_config, parse_options  # noqa: E402
from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider  # noqa: E402


class TestOptions(unittest.TestCase):
    def test_new_modes_and_search_config_option(self):
        self.assertEqual(parse_options(["--mode", "audit"])["mode"], "audit")
        self.assertEqual(parse_options(["--mode=power"])["mode"], "power")
        self.assertEqual(parse_options(["--mode", "evolve", "--search-config", "x.json"])["search-config"],
                         "x.json")
        with self.assertRaises(UsageError):
            parse_options(["--search-config", "x.json"])          # legacy mode

    def test_search_config_is_strict(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps({"seed": 5, "children_per_round": 8}))
            cfg = load_search_config(str(p), "pareto_niche")
            self.assertEqual((cfg.seed, cfg.children_per_round, cfg.selection), (5, 8, "pareto_niche"))
            for bad in ({"seeed": 5}, {"selection": "map_elites"}, [1]):
                p.write_text(json.dumps(bad))
                with self.assertRaises(UsageError):
                    load_search_config(str(p), "pareto_niche")
            with self.assertRaises(UsageError):
                load_search_config(str(Path(d) / "missing.json"), "pareto_niche")


class TestReadOnlyModes(unittest.TestCase):
    def _main(self, args, env):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = runner.main(args)
        return rc, out.getvalue()

    def test_audit_mode_reads_only(self):
        with tempfile.TemporaryDirectory() as d:
            st = Path(d) / "state"
            st.mkdir()
            env = {"NEW2_STATE_DIR": str(st), "NEW2_RESULTS_DIR": str(Path(d) / "res"),
                   "NEW2_DATA_DIR": str(Path(d) / "data")}
            before = sorted(p.name for p in st.rglob("*"))
            rc, out = self._main(["--mode", "audit"], env)
            self.assertEqual(rc, 0, out)
            self.assertEqual(json.loads(out.strip().splitlines()[-1])["status"], "CONSISTENT")
            self.assertEqual(sorted(p.name for p in st.rglob("*")), before)   # no lock, nothing written


class TestDevSeriesIntegrity(PipelineCase):
    def test_ledger_check_flags_ok_record_without_series(self):
        p = self.pipe(SyntheticProvider())
        self.register_all(p, [EDGE] + NOISE[:2])
        self.assertNotIn("MISSING_DEV_SERIES", ledger_check.check(p.state_dir)["counts"])
        rows = p.series_path.read_text().splitlines()
        p.series_path.write_text("\n".join(rows[1:]) + "\n")              # simulate the old crash window
        self.assertEqual(ledger_check.check(p.state_dir)["counts"].get("MISSING_DEV_SERIES"), 1)


class TestCycleStatusMeaning(unittest.TestCase):
    def test_cycle_ok_is_not_a_strategy_verdict(self):
        s = EvolutionCycleSummary(status="CYCLE_OK", status_counts={"REJECTED": 9, "RESEARCH_ONLY": 3})
        j = s.to_json()
        self.assertEqual(j["strategies_passed"], {})
        self.assertIn("not a strategy verdict", j["status_meaning"])
        j = EvolutionCycleSummary(status="CYCLE_OK", status_counts={"PAPER_TEST_READY": 1}).to_json()
        self.assertEqual(j["strategies_passed"], {"PAPER_TEST_READY": 1})


if __name__ == "__main__":
    unittest.main()
