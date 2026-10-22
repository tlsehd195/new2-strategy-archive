"""Sanity checks for run/bundle_regime_dev.py (synthetic arrays only, no data files).

    cd run && python3 -m unittest test_bundle_regime
"""

import unittest

import numpy as np

import bundle_regime_dev as br


class RegimeTest(unittest.TestCase):
    def test_labels_use_only_past_and_flag_unlabeled(self):
        idx = np.concatenate([[1.0], np.linspace(1.0, 2.0, 120), np.linspace(2.0, 1.0, 120)])
        r1, r2 = br.regimes(idx, len(idx) - 1)
        self.assertTrue((r1[:br.TRAIL_DAYS] == "NA").all())
        self.assertEqual(r1[100], "BULL")
        self.assertEqual(r1[-1], "BEAR")
        self.assertEqual(r2[0], "OK")
        self.assertEqual(r2[-1], "DD")

    def test_drawdown_threshold_is_relative_to_running_peak(self):
        idx = np.array([1.0, 2.0, 1.6, 1.4, 1.5])
        _, r2 = br.regimes(idx, len(idx))
        self.assertEqual(list(r2), ["OK", "OK", "OK", "DD", "DD"])

    def test_small_sample_is_not_reported(self):
        self.assertIn("note", br.stats(np.ones(br.MIN_DAYS - 1)))
        self.assertIn("sr_ann", br.stats(np.random.default_rng(0).normal(1, 1, 200)))

    def test_nw_regression_recovers_alpha_and_beta(self):
        rng = np.random.default_rng(1)
        x = rng.normal(0, 10, 4000)
        y = 3.0 + 0.5 * x + rng.normal(0, 1, 4000)
        r = br.nw_t(y, x)
        self.assertAlmostEqual(r["alpha_bp_day"], 3.0, delta=0.2)
        self.assertAlmostEqual(r["beta"], 0.5, delta=0.01)
        self.assertGreater(r["alpha_t_nw5"], 5)


if __name__ == "__main__":
    unittest.main()
