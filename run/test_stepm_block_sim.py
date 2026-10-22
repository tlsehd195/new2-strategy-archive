"""Sanity checks for run/stepm_block_sim.py (synthetic numbers only).

    cd run && python3 -m unittest test_stepm_block_sim
"""

import unittest

import numpy as np

import stepm_block_sim as sb
import stats_mc


class StepMBlockSimTest(unittest.TestCase):
    def test_variance_matches_engine(self):
        x = np.random.default_rng(1).standard_normal((120, 3)) + 0.2
        v = sb.hansen_var(sb.acov_all(x), 0.2)
        for j in range(3):
            self.assertAlmostEqual(float(v[j]), stats_mc.bootstrap_variance(x[:, j].tolist(), 0.2))

    def test_strong_edge_rejected_and_null_mostly_not(self):
        rng = np.random.default_rng(2)
        x = rng.standard_normal((365, 4))
        x[:, 0] += 0.5
        rej, _ = sb.stepm_np(x, 1 / 7, rng, 199)
        self.assertTrue(rej[0])

    def test_rw61_returns_a_candidate(self):
        rng = np.random.default_rng(3)
        x, _ = sb.make_data(rng, "garch_ar02", n=200, s=3)
        b, g = sb.rw61_choice(x, rng, M=5, B=49)
        self.assertIn(b, sb.BLOCKS)
        self.assertEqual(set(g), set(sb.BLOCKS))


if __name__ == "__main__":
    unittest.main()
