"""Tests for the unsupervised regime classifier helpers (ADR 0050)."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import unsup_regime_dev as u  # noqa: E402


class UnsupRegimeTest(unittest.TestCase):
    def test_prereg_matches_code(self):
        p = json.loads((u.EV / "prereg_unsup_regime_v1.json").read_text())
        self.assertIn("k=3", p["classifier"]["algorithm"])
        self.assertIn(str(u.SEED), p["classifier"]["algorithm"])
        self.assertEqual((u.K, u.LOOK30, u.LOOK60, u.MIN_DAYS), (3, 30, 60, 60))

    def test_features_use_only_past(self):
        rng = np.random.default_rng(1)
        idx = np.cumprod(1 + rng.normal(0, 0.02, 200))
        f = u.features(idx)
        self.assertTrue(np.isnan(f[:61]).all())
        idx2 = idx.copy()
        idx2[150:] *= 5.0                       # change the future
        np.testing.assert_allclose(u.features(idx2)[:151], f[:151])

    def test_kmeans_separates_blobs_deterministically(self):
        rng = np.random.default_rng(0)
        x = np.vstack([rng.normal(m, 0.1, (50, 3)) for m in (-3, 0, 3)])
        i1, c1 = u.kmeans(x, restarts=5)
        i2, c2 = u.kmeans(x, restarts=5)
        np.testing.assert_allclose(c1, c2)
        self.assertEqual(sorted(np.round(c1[:, 0]).tolist()), [-3.0, 0.0, 3.0])

    def test_assign_and_run_stats(self):
        f = np.array([[np.nan] * 3, [-1.0] * 3, [1.0] * 3])
        lab = u.assign(f, np.zeros(3), np.ones(3), np.array([[-1.0] * 3, [0.0] * 3, [1.0] * 3]))
        self.assertEqual(list(lab), ["NA", "U1", "U3"])
        rs = u.run_stats(np.array(["U1", "U1", "U2", "U2", "U2", "NA"], dtype=object))
        self.assertEqual(rs["median_run"], 2.5)
        self.assertEqual(rs["days"], 5)

    def test_frozen_file_consistent(self):
        fz = json.loads((u.EV / "frozen_classifier.json").read_text())
        c = np.array(fz["centroids_z"])
        self.assertEqual(c.shape, (3, 3))
        self.assertTrue(c[0, 0] < c[1, 0] < c[2, 0])


if __name__ == "__main__":
    unittest.main()
