"""Sanity checks for run/power_audit_synthetic.py (synthetic numbers only).

    cd run && python3 -m unittest test_power_audit
"""

import math
import unittest

import numpy as np

import power_audit_synthetic as pa


class PowerAuditTest(unittest.TestCase):
    def test_window_alpha_matches_promo_v3(self):
        # 0.05 split over 2 supported windows, spending 6/pi^2/k^2
        self.assertAlmostEqual(pa.window_alpha(1), 0.025 * 6 / math.pi ** 2)
        self.assertAlmostEqual(pa.window_alpha(8), pa.window_alpha(1) / 64)

    def test_bootstrap_floor_only_loosens_below_one_over_b(self):
        self.assertEqual(pa.z_crit(0.01, 1, True), pa.z_crit(0.01, 1, False))
        self.assertLess(pa.z_crit(1e-4, 1, True), pa.z_crit(1e-4, 1, False))

    def test_power_is_alpha_at_zero_edge_and_rises_with_years(self):
        self.assertAlmostEqual(pa.power(0.0, 1.0, 0.05), 0.05, places=6)
        self.assertGreater(pa.power(2.0, 2.0, 0.01), pa.power(2.0, 1.0, 0.01))
        sr = pa.sr_for_power(0.8, 1.0, 0.05)
        self.assertAlmostEqual(pa.power(sr, 1.0, 0.05), 0.8, places=6)

    def test_no_edge_almost_never_passes(self):
        rng = np.random.default_rng(1)
        r = pa.scenario(rng, 0.0, 150, 240.0, 14.0, 1, 1, 0.0, 300)
        self.assertLessEqual(r["pass_all"], 0.01)

    def test_strong_frequent_edge_passes_on_a_fresh_window(self):
        rng = np.random.default_rng(2)
        r = pa.scenario(rng, 6.0, 150, 240.0, 14.0, 1, 1, 0.0, 300)
        self.assertGreater(r["pass_all"], 0.5)

    def test_rare_trades_never_reach_validation_minimum(self):
        rng = np.random.default_rng(3)
        r = pa.scenario(rng, 4.0, 12, 240.0, 14.0, 1, 1, 0.0, 300)
        self.assertEqual(r["survival_after_gate"]["val_n"], 0.0)


if __name__ == "__main__":
    unittest.main()
