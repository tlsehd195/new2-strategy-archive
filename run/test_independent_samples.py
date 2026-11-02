"""Synthetic tests for the independent-sample accounting tool (ADR 0059). No market data."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit", "evidence_independent_samples_20261102"))
import neff_s1 as N  # noqa: E402


class T(unittest.TestCase):
    def test_equal_correlation_matrix(self):
        rng = np.random.default_rng(0)
        n, rho = 12, 0.6
        common = rng.standard_normal((20000, 1))
        R = np.sqrt(rho) * common + np.sqrt(1 - rho) * rng.standard_normal((20000, n))
        d = N.neff(R)
        self.assertAlmostEqual(d["rho_bar"], rho, delta=0.02)
        self.assertAlmostEqual(d["N_rho"], n / (1 + (n - 1) * rho), delta=0.1)

    def test_independent_symbols_give_full_breadth(self):
        R = np.random.default_rng(1).standard_normal((20000, 10))
        self.assertGreater(N.neff(R)["N_pr"], 9.8)

    def test_power_arithmetic_matches_adr0055(self):
        # ADR 0055: Sharpe 1.0 needs 6.2 years at K=1 and 11.7 at K=10 (80% power, one-sided)
        self.assertAlmostEqual((N.zsum(0.05) / 1.0) ** 2, 6.18, delta=0.05)
        self.assertAlmostEqual((N.zsum(0.005) / 1.0) ** 2, 11.68, delta=0.05)


if __name__ == "__main__":
    unittest.main()
