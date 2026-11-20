"""Tests for round 15b (ADR 0113): supervised + unsupervised + RL combination. Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ml_combo_r15b_dev as ml  # noqa: E402

DAY = ml.DAY


class MlComboR15bTest(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(ml.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(ml.KEYS))
        self.assertEqual(p["context_id"], ml.CONTEXT_ID)
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in ml.KEYS))

    def test_ridge_recovers_signal(self):
        rng = np.random.default_rng(1)
        X = rng.normal(size=(500, 3))
        y = X @ np.array([1.0, 0.0, -2.0])
        b = ml.ridge(X, y, 1e-6)
        np.testing.assert_allclose(b, [1.0, 0.0, -2.0], atol=1e-4)

    def test_usable_needs_complete_label(self):
        samples = [{"t": k * 7 * DAY} for k in range(5)]
        t_now = 4 * 7 * DAY
        # label of sample at 3*7 days ends at 4*7 days > t_now - 2 days -> not usable
        self.assertEqual([p["t"] // (7 * DAY) for p in ml.usable(samples, t_now)], [0, 1, 2])

    def test_kmeans_sorted_by_return_column(self):
        Z = np.vstack([np.tile([0, -3, 0, 0], (10, 1)), np.tile([0, 0, 0, 0], (10, 1)), np.tile([0, 3, 0, 0], (10, 1))])
        cen = ml.kmeans(Z.astype(float))
        self.assertEqual(list(cen[:, 1]), [-3.0, 0.0, 3.0])
        self.assertEqual(list(ml.assign(np.array([[0, 2.5, 0, 0.0]]), cen)), [2])

    def test_rl_prefers_flat_when_rewards_below_cost(self):
        n = 40
        samples = [{"t": k * 7 * DAY, "state": np.array([0.0, 0.0, 0.0, 0.0])} for k in range(n)]
        hist = [0.001] * n  # +10bp gross, below 30bp cost
        self.assertEqual(ml.rl_exposure(samples, hist, (n + 2) * 7 * DAY, np.zeros(4)), 0.0)
        hist = [0.02] * n
        self.assertEqual(ml.rl_exposure(samples, hist, (n + 2) * 7 * DAY, np.zeros(4)), 1.0)

    def test_crank_centred(self):
        r = ml.crank(pd.Series([3.0, 1.0, np.nan, 2.0]))
        self.assertTrue(np.isnan(r.iloc[2]))
        self.assertAlmostEqual(float(r.dropna().sum()), 1.5 - 1.5 + 0.0 + (1 / 3 + 2 / 3 + 1) - 1.5)


if __name__ == "__main__":
    unittest.main()
