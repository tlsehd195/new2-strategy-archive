"""Tests for the candidate regime scorecard helpers (ADR 0044)."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import candidate_regime_dev as x  # noqa: E402


class RegimeTest(unittest.TestCase):
    def test_prereg_matches_code(self):
        p = json.loads((x.EV / "prereg_candidate_regime_v1.json").read_text())
        self.assertEqual(len(p["symbols"]), 9)
        self.assertEqual(p["symbols"], x.SYMBOLS)
        self.assertEqual(set(p["bundles"]), {"B1_first5", "B2_all33", "B3_top5_S1", "B4_long_only", "B5_first20"})
        self.assertEqual(len(json.loads(x.CAND.read_text())), 33)

    def test_stats_needs_min_days(self):
        self.assertIn("note", x.stats(np.ones(10)))
        s = x.stats(np.arange(100.0))
        self.assertAlmostEqual(s["mean_bp_day"], 49.5)
        self.assertEqual(s["days"], 100)

    def test_nw_t_signs(self):
        rng = np.random.default_rng(0)
        self.assertGreater(x.nw_t(rng.normal(1, 1, 500)), 5)
        self.assertIsNone(x.nw_t(np.ones(5)))

    def test_flags(self):
        def cell(m, cum):
            return {"mean_bp_day": m, "cum_bp": cum}
        ne = {"R1_BULL": cell(1, 100), "R1_SIDE": cell(-1, -50), "R1_BEAR": cell(2, 100), "R2_DD": cell(-1, 0)}
        sc = {"S1": {"net_entry": ne}, "S2": {"net_entry": ne}}
        f = x.flags(sc)
        self.assertFalse(f["REGIME_ALL3_POS"])
        self.assertFalse(f["BEAR_NEG"])
        self.assertTrue(f["DD_NEG"])
        self.assertTrue(f["ONE_REGIME"] is False)  # 200 total, best 100
        self.assertTrue(f["S2_SAME_SIGN"]["R1_BULL"])


if __name__ == "__main__":
    unittest.main()
