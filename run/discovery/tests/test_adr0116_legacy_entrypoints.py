"""ADR 0116 §9: the DEPRECATED portfolio_sim.py refuses to run without an
explicit flag (before importing pandas), and run_cycle.sh keeps its documented
default (legacy, with a warning) and refuses an unknown mode.  No state is
written: the refusals happen before any work."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

DISC = Path(__file__).resolve().parent.parent


class LegacyEntrypointTest(unittest.TestCase):
    def test_portfolio_sim_refuses_without_flag(self):
        p = subprocess.run([sys.executable, str(DISC / "portfolio_sim.py")], capture_output=True, text=True,
                           cwd=str(DISC), timeout=60)
        self.assertEqual(p.returncode, 2)
        self.assertIn("DEPRECATED", p.stderr)
        self.assertIn("--legacy-diagnostic", p.stderr)
        self.assertEqual(p.stdout, "")

    def test_run_cycle_rejects_unknown_mode(self):
        env = dict(os.environ, NEW2_DISC_MODE="turbo")
        p = subprocess.run(["bash", str(DISC / "run_cycle.sh")], capture_output=True, text=True, env=env,
                           cwd=str(DISC), timeout=60)
        self.assertEqual(p.returncode, 1)
        self.assertIn("invalid NEW2_DISC_MODE 'turbo'", p.stdout)

    def test_run_cycle_default_mode_is_documented_legacy(self):
        text = (DISC / "run_cycle.sh").read_text()
        self.assertIn('MODE="${NEW2_DISC_MODE:-legacy}"', text)       # default not changed (directive §9)
        self.assertIn("CYCLE_WARNING NEW2_DISC_MODE not set", text)


if __name__ == "__main__":
    unittest.main()
