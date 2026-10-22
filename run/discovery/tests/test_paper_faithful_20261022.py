"""Paper checks of 2026-10-22 (ADR 0027): Politis-White block length,
Tian-Ramdas alpha-spending / fallback, Dufour Monte Carlo p-values.

    cd run && python3 -m unittest discovery.tests.test_paper_faithful_20261022
"""

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stats_mc as sm  # noqa: E402


def ar1(phi, n, seed):
    r = random.Random(seed)
    y = [r.gauss(0, 1)]
    for _ in range(n - 1):
        y.append(phi * y[-1] + r.gauss(0, 1))
    return y


class BlockLengthTest(unittest.TestCase):
    def test_iid_gives_short_blocks(self):
        b = [sm.ppw_block_length(ar1(0.0, 400, s)) for s in range(10)]
        self.assertLess(sum(b) / len(b), 2.5)

    def test_ar1_close_to_theory(self):
        # AR(1): G/g(0) = 2 phi / (1 - phi^2); b_opt = (G/g0)^(2/3) n^(1/3)
        # with the corrected D_SB = 2 g(0)^2 (PPW 2009)
        n, phi = 4000, 0.5
        theory = (2 * phi / (1 - phi * phi)) ** (2 / 3) * n ** (1 / 3)
        b = [sm.ppw_block_length(ar1(phi, n, s)) for s in range(4)]
        self.assertAlmostEqual(sum(b) / len(b) / theory, 1.0, delta=0.3)

    def test_more_dependence_longer_blocks(self):
        lo = sum(sm.ppw_block_length(ar1(0.1, 1000, s)) for s in range(4))
        hi = sum(sm.ppw_block_length(ar1(0.6, 1000, s)) for s in range(4))
        self.assertGreater(hi, lo)

    def test_cap_and_floor(self):
        n = 200
        b = sm.ppw_block_length(ar1(0.99, n, 3))
        self.assertLessEqual(b, math.ceil(min(3 * math.sqrt(n), n / 3)))
        self.assertGreaterEqual(sm.ppw_block_length([float(i % 2) for i in range(50)]), 1.0)

    def test_ppw_q_uses_longest_block(self):
        s = {"a": ar1(0.0, 500, 1), "b": ar1(0.6, 500, 2)}
        self.assertAlmostEqual(sm.ppw_q(s), 1 / sm.ppw_block_length(s["b"]))

    def test_default_q_unchanged(self):
        self.assertAlmostEqual(sm.default_q(365), 365 ** (-1 / 3))


class StepMBootstrapTTest(unittest.TestCase):
    def _series(self, edge):
        r = random.Random(21)
        return {"a": [r.gauss(edge, 1) for _ in range(120)],
                "b": [r.gauss(0, 1) for _ in range(120)]}

    def test_fixed_is_still_the_default(self):
        s = self._series(0.5)
        self.assertEqual(sm.stepm(s, alpha=0.05, B=99, seed=3),
                         sm.stepm(s, alpha=0.05, B=99, seed=3, studentize="fixed"))
        self.assertEqual(sm.stepm(s, alpha=0.05, B=99, seed=3)["method"],
                         "romano_wolf2005_stepm_studentized")

    def test_bootstrap_t_rejects_a_strong_edge(self):
        out = sm.stepm(self._series(0.6), alpha=0.05, B=99, seed=4, studentize="bootstrap_t")
        self.assertEqual(out["method"], "romano_wolf2005_stepm_bootstrap_t")
        self.assertIn("a", out["rejected"])
        self.assertNotIn("b", out["rejected"])

    def test_bad_option(self):
        with self.assertRaises(ValueError):
            sm.stepm(self._series(0.0), alpha=0.05, B=9, studentize="x")


class AlphaSpendingTest(unittest.TestCase):
    def test_spending_is_tian_ramdas_gamma(self):
        total = sum(sm.spending_alpha(k, 0.05) for k in range(1, 200000))
        self.assertLess(total, 0.05)
        self.assertAlmostEqual(total, 0.05, delta=1e-5)

    def test_fallback_without_rejections_equals_spending(self):
        for i in range(1, 8):
            self.assertAlmostEqual(sm.fallback_alpha([False] * (i - 1)), sm.spending_alpha(i))

    def test_fallback_passes_level_on(self):
        a2 = sm.fallback_alpha([True])
        self.assertAlmostEqual(a2, sm.spending_alpha(2) + sm.spending_alpha(1))
        a3 = sm.fallback_alpha([True, True])
        self.assertAlmostEqual(a3, sm.spending_alpha(3) + a2)
        self.assertAlmostEqual(sm.fallback_alpha([True, False]), sm.spending_alpha(3))


class MonteCarloTest(unittest.TestCase):
    def test_exact_size_continuous(self):
        # Dufour Prop. 2.2: N = 19, alpha = 0.05 -> P[p <= 0.05] = 1/20 exactly
        r = random.Random(11)
        reps, rej = 20000, 0
        for _ in range(reps):
            s = [r.gauss(0, 1) for _ in range(20)]
            rej += sm.mc_pvalue(s[0], s[1:]) <= 0.05
        self.assertAlmostEqual(rej / reps, 0.05, delta=4 * math.sqrt(0.05 * 0.95 / reps))

    def test_tie_break_restores_size_for_discrete_statistic(self):
        r = random.Random(12)
        reps, plain, rand = 20000, 0, 0
        for _ in range(reps):
            s = [sum(r.random() < 0.5 for _ in range(6)) for _ in range(20)]
            plain += sm.mc_pvalue(s[0], s[1:]) <= 0.05
            rand += sm.mc_pvalue(s[0], s[1:], rng=r) <= 0.05
        se = math.sqrt(0.05 * 0.95 / reps)
        self.assertLess(plain / reps, 0.05 - 4 * se)                 # conservative
        self.assertAlmostEqual(rand / reps, 0.05, delta=4 * se)      # exact

    def test_mmc_takes_the_largest_p(self):
        out = sm.mmc_pvalue(1.0, {"a": [0.0, 0.5, 2.0], "b": [2.0, 3.0, 0.1]})
        self.assertEqual(out["argmax"], "b")
        self.assertAlmostEqual(out["p"], 3 / 4)


if __name__ == "__main__":
    unittest.main()
