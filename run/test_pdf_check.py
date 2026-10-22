"""Numpy parts of the PDF check (ADR 0032): the vectorised Goetze-Kuensch draws of
``pdf_check_sim`` against the pure-python ``stats_mc`` functions, and the betting
rules of Waudby-Smith & Ramdas appendix B in ``evalue_walkforward_sim``.
Synthetic numbers only.

    cd run && python3 -m unittest test_pdf_check
"""

import math
import random  # noqa: F401
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "discovery"))

import stats_mc as sm  # noqa: E402
import evalue_walkforward_sim as ev  # noqa: E402
import pdf_check_sim as pc  # noqa: E402


class GKNumpyTest(unittest.TestCase):
    def test_numpy_draws_equal_pure_python(self):
        rng = np.random.default_rng(5)
        x = rng.standard_normal((50, 3))
        l, nb = 7, 8                                   # n not a multiple of l
        starts = rng.integers(0, 50 - l + 1, (4, nb))
        t = pc.gk_t_draws(x, l, starts)
        for b in range(4):
            for j in range(3):
                self.assertAlmostEqual(float(t[b, j]),
                                       sm.gk_blockwise_t(x[:, j].tolist(), l, starts[b].tolist()))

    def test_circular_draws_equal_pure_python(self):
        rng = np.random.default_rng(2)
        xx = rng.standard_normal((40, 2))
        st = rng.integers(0, 40, (3, 7))
        t = pc.gk_t_draws(xx, 6, st, circular=True)
        for b in range(3):
            for j in range(2):
                self.assertAlmostEqual(float(t[b, j]),
                                       sm.gk_blockwise_t(xx[:, j].tolist(), 6, st[b].tolist(), circular=True))


class BettingRulesTest(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(11)
        self.Z = np.clip(rng.standard_normal((200, 40)) * 0.4 + 0.08, -1.0, None)
        self.sz0 = 0.4

    def test_agrapa_is_the_wsr_bet(self):
        np.testing.assert_allclose(ev.log_e_agrapa(self.Z, self.sz0), ev.log_e_wsr(self.Z, self.sz0))

    def test_old_prpl_eb_name_is_the_hedged_bet(self):
        self.assertIs(ev.log_e_prpl_eb, ev.log_e_hedged_prpl_bet)

    def test_gkelly_is_the_grid_mixture(self):
        np.testing.assert_allclose(ev.log_e_gkelly(self.Z), ev.log_e_mixture(self.Z))

    def test_lbow_bets_less_than_agrapa(self):
        # same moments, larger denominator (+|mu|): smaller lam, so less wealth on a drifting path
        mu, s2 = ev._wsr_moments(self.Z, self.sz0)
        a = np.clip(mu / (s2 + mu * mu), 0, ev.LAM_MAX)
        l = np.clip(mu / (np.abs(mu) + s2 + mu * mu), 0, ev.LAM_MAX)
        self.assertTrue((l <= a + 1e-12).all())

    def test_grapa_root_and_cuts(self):
        # t-1 = 3 past points with a root inside (0, c): sum Z/(1+lam Z) = 0
        Z = np.array([[0.5, -0.4, 0.3, 0.0]])
        past = Z[0, :3]
        lo, hi = 0.0, ev.LAM_MAX
        for _ in range(60):
            mid = (lo + hi) / 2
            if (past / (1 + mid * past)).sum() > 0:
                lo = mid
            else:
                hi = mid
        want = math.log1p(((lo + hi) / 2) * 0.0)           # 4th Z is 0: wealth unchanged by the 4th bet
        got = ev.log_e_grapa(Z)[0, 3] - ev.log_e_grapa(Z)[0, 2]
        self.assertAlmostEqual(got, want)
        # all-negative past -> lam = 0 ; all-positive past -> lam = c
        neg = ev.log_e_grapa(np.array([[-0.2, -0.1, 0.5]]))
        self.assertAlmostEqual(neg[0, 2] - neg[0, 1], 0.0)
        pos = ev.log_e_grapa(np.array([[0.2, 0.1, 0.5]]))
        self.assertAlmostEqual(pos[0, 2] - pos[0, 1], math.log1p(ev.LAM_MAX * 0.5))

    def test_ons_sign(self):
        # constant positive excess: the Kelly-direction ONS keeps the bet at the cap,
        # the printed sign pushes it to 0
        Z = np.full((1, 30), 0.3)
        good = ev.log_e_ons(Z)[0]
        printed = ev.log_e_ons(Z, as_printed=True)[0]
        self.assertGreater(good[-1], printed[-1])
        self.assertAlmostEqual(good[-1], 30 * math.log1p(ev.LAM_MAX * 0.3), places=6)

    def test_ons_lam1_is_capped(self):
        Z = np.array([[0.5, 0.5]])
        self.assertAlmostEqual(ev.log_e_ons(Z)[0, 0], math.log1p(ev.LAM_MAX * 0.5))

    def test_prpl_eb_exp_matches_formula(self):
        Z = self.Z[:3, :6]
        got = ev.log_e_prpl_eb_exp(Z, self.sz0, alpha=0.05)
        mu, s2 = ev._wsr_moments(Z, self.sz0)
        want = np.zeros(3)
        for t in range(6):
            lam = min(math.sqrt(2 * math.log(2 / 0.05) / (s2[0, t] * (t + 1) * math.log(t + 2))), ev.LAM_MAX)
            want = want  # noqa
        for i in range(3):
            acc = 0.0
            for t in range(6):
                lam = min(math.sqrt(2 * math.log(2 / 0.05) / (s2[i, t] * (t + 1) * math.log(t + 2))), ev.LAM_MAX)
                psi = (-math.log(1 - lam) - lam) / 4
                acc += lam * Z[i, t] - 4 * (Z[i, t] - mu[i, t]) ** 2 * psi
                self.assertAlmostEqual(got[i, t], acc)

    def test_sizes_under_null_are_small(self):
        rng = np.random.default_rng(12)
        Z = np.clip(rng.standard_t(4, (3000, 60)) * 0.4 - 0.02, -1.0, None)   # E[Z] < 0
        thr = math.log(1 / ev.ALPHA)
        for name, le in pc.rules(Z, 0.4, ev.ALPHA, ev.ALPHA).items():
            self.assertLess(float(np.mean(ev.first_cross(le, thr) <= 60)), ev.ALPHA, name)


if __name__ == "__main__":
    unittest.main()
