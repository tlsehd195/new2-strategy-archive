"""ADR 0064: the power analysis is reproducible and reads the live gates.

Tiny synthetic config (seconds); no market data is read."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import power_analysis as pa  # noqa: E402

TINY = {"schema": "power_analysis_config_v1", "seed": 7, "n_sims": 3, "sr_grid": [0.0, 6.0],
        "ar1_phi": 0.1, "t_df": 4, "batch_m": 3, "stepm_B": 50, "ci_B": 60,
        "dev_days": 200, "dev_trials": 20,
        "scenarios": [{"name": "tiny", "validation_days": 120, "batch_index": 1}]}


class TestPowerAnalysis(unittest.TestCase):
    def _run(self, cfg=TINY):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps(cfg))
            return pa.run(p)

    def test_same_seed_same_numbers_and_records_the_gates(self):
        a, b = self._run(), self._run()
        self.assertEqual(a["scenarios"], b["scenarios"])
        self.assertEqual(a["promotion_config"]["version"], "promo-v5")
        self.assertEqual(len(a["config_sha256"]), 64)
        sc = a["scenarios"][0]
        self.assertEqual([r["sr_annual"] for r in sc["rows"]], [0.0, 6.0])
        self.assertIsNotNone(sc["type1"])
        for r in sc["rows"]:
            for k in ("power_validation_stepm", "pass_ci", "pass_dsr", "power_all", "fwer_validation"):
                self.assertTrue(0.0 <= r[k] <= 1.0, k)
            self.assertAlmostEqual(r["type2_all"], 1 - r["power_all"])
        # SR 0 never counts as power
        self.assertEqual(sc["rows"][0]["power_validation_stepm"], 0.0)
        self.assertIn(sc["status"], ("OK", "UNDETERMINED_POWER"))

    def test_alpha_follows_the_promotion_config(self):
        from stats_mc import spending_alpha
        from strategy_spec import SUPPORTED_SYMBOLS
        sc = self._run()["scenarios"][0]
        self.assertAlmostEqual(sc["alpha_window"], spending_alpha(1, 0.05 / len(SUPPORTED_SYMBOLS)))

    def test_wrong_schema_is_refused(self):
        with self.assertRaises(ValueError):
            self._run({**TINY, "schema": "x"})


if __name__ == "__main__":
    unittest.main()
