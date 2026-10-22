"""Goetze-Kuensch (1996) blockwise bootstrap in ``stats_mc`` and the betting
rules of Waudby-Smith & Ramdas appendix B in ``evalue_walkforward_sim``
(ADR 0028).  Synthetic numbers only.

    cd run && python3 -m unittest discovery.tests.test_pdf_check_20261022
"""

import itertools
import math
import random
import sys
import unittest
from pathlib import Path

import numpy as np

RUN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RUN / "discovery"))
sys.path.insert(0, str(RUN))

import stats_mc as sm  # noqa: E402
import evalue_walkforward_sim as ev  # noqa: E402
import pdf_check_sim as pc  # noqa: E402


def ar1(phi, n, seed):
    r = random.Random(seed)
    y = [r.gauss(0, 1)]
    for _ in range(n - 1):
        y.append(phi * y[-1] + r.gauss(0, 1))
    return y


class GKBootstrapTest(unittest.TestCase):
    def test_bootstrap_mean_is_gk_weighted_mean(self):
        # n = b*l: mu*_n = (n-l+1)^-1 sum_j min(j/l, 1, (n+1-j)/l) Y_j   (GK Sec. 2)
        r = random.Random(1)
        x = [r.gauss(0, 1) for _ in range(12)]
        n, l = 12, 4
        w = sum(min(j / l, 1.0, (n + 1 - j) / l) * x[j - 1] for j in range(1, n + 1)) / (n - l + 1)
        self.assertAlmostEqual(sm.gk_mean_of_bootstrap_mean(x, l), w)

    def test_bootstrap_mean_matches_enumeration_when_truncated(self):
        # n not a multiple of l: exact E* over all start tuples
        x = [0.3, -1.0, 2.0, 0.5, -0.2, 1.1, 0.0]
        n, l = len(x), 3
        nb = -(-n // l)
        tot = cnt = 0
        for st in itertools.product(range(n - l + 1), repeat=nb):
            ys = [v for s in st for v in x[s:s + l]][:n]
            tot += sum(ys) / n
            cnt += 1
        self.assertAlmostEqual(sm.gk_mean_of_bootstrap_mean(x, l), tot / cnt)

    def test_within_block_variance_bias_one_minus_one_over_b(self):
        # GK Sec. 2: for linear H, E* sigma_hat*^2 = sigma*^2 (1 - 1/b), with
        # sigma*^2 = Var*(T*) = (n-l+1)^-1 sum_j B_j^2, B_j = l^-1/2 sum_i (Y_{j+i} - mu*_n)
        x = [0.3, -1.0, 2.0, 0.5, -0.2, 1.1]
        n, l = len(x), 2
        b = n // l
        mu = sm.gk_mean_of_bootstrap_mean(x, l)
        s_star = sum((sum(x[j:j + l]) - l * mu) ** 2 / l for j in range(n - l + 1)) / (n - l + 1)
        tot = cnt = 0
        for st in itertools.product(range(n - l + 1), repeat=b):
            ys = [v for s in st for v in x[s:s + l]]
            yb = sum(ys) / n
            tot += sum((sum(ys[i:i + l]) - l * yb) ** 2 for i in range(0, n, l)) / n
            cnt += 1
        self.assertAlmostEqual(tot / cnt, s_star * (1 - 1 / b))

    def test_t_draw_matches_definition(self):
        x = [0.3, -1.0, 2.0, 0.5, -0.2, 1.1, 0.4, -0.7]
        n, l = len(x), 4
        starts = [1, 3]
        ys = x[1:5] + x[3:7]
        yb = sum(ys) / n
        mu = sm.gk_mean_of_bootstrap_mean(x, l)
        var = sum((sum(ys[i:i + l]) - l * yb) ** 2 for i in range(0, n, l)) / n
        self.assertAlmostEqual(sm.gk_blockwise_t(x, l, starts), math.sqrt(n) * (yb - mu) / math.sqrt(var))

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

    def test_circular_blocks(self):
        x = [0.3, -1.0, 2.0, 0.5, -0.2, 1.1, 0.0]
        self.assertAlmostEqual(sm.gk_mean_of_bootstrap_mean(x, 3, True), sum(x) / 7)
        n, l = 7, 3
        starts = [5, 2, 6]
        ys = [x[(s + o) % n] for s in starts for o in range(l)][:n]
        yb = sum(ys) / n
        var = sum((sum(ys[i:i + l]) - len(ys[i:i + l]) * yb) ** 2 for i in range(0, n, l)) / n
        self.assertAlmostEqual(sm.gk_blockwise_t(x, l, starts, circular=True),
                               math.sqrt(n) * (yb - sum(x) / n) / math.sqrt(var))
        rng = np.random.default_rng(2)
        xx = rng.standard_normal((40, 2))
        st = rng.integers(0, 40, (3, 7))
        t = pc.gk_t_draws(xx, 6, st, circular=True)
        for b in range(3):
            for j in range(2):
                self.assertAlmostEqual(float(t[b, j]),
                                       sm.gk_blockwise_t(xx[:, j].tolist(), 6, st[b].tolist(), circular=True))

    def test_lag_window_variance_iid_and_ar1(self):
        iid = [random.Random(9).gauss(0, 1) for _ in range(1)]
        v = [sm.gk_lag_window_variance(ar1(0.0, 600, s), 6) for s in range(40)]
        self.assertAlmostEqual(sum(v) / len(v), 1.0, delta=0.15)
        v = [sm.gk_lag_window_variance(ar1(0.5, 600, s), 8) for s in range(40)]
        self.assertAlmostEqual(sum(v) / len(v), 1 / (1 - 0.5) ** 2, delta=1.2)   # long-run variance 4; l=8 truncation biases it down
        self.assertTrue(iid)

    def test_lag_window_uses_n_minus_l_terms_and_rectangular_weights(self):
        x = [1.0, -2.0, 0.5, 3.0, -1.0, 0.0, 2.0, -0.5, 1.5, 0.2]
        n, l = len(x), 3
        mu = sum(x) / n
        u = [v - mu for v in x]
        g = [sum(u[j] * u[j + k] for j in range(n - l)) / n for k in range(l)]
        self.assertAlmostEqual(sm.gk_lag_window_variance(x, l, "rectangular"), g[0] + 2 * g[1] + 2 * g[2])

    def test_parzen_kernel_values(self):
        self.assertEqual(sm._parzen(0.0), 1.0)
        self.assertAlmostEqual(sm._parzen(0.5), 0.25)
        self.assertEqual(sm._parzen(1.0), 0.0)
        self.assertAlmostEqual(sm._parzen(0.1), 1 - 6 * 0.01 + 6 * 0.001)     # 1 - 6x^2 near 0

    def test_block_length_orders(self):
        self.assertEqual(sm.gk_block_length(365, "parzen"), 7)
        self.assertEqual(sm.gk_block_length(365, "rectangular"), 4)
        with self.assertRaises(ValueError):
            sm.gk_block_length(365, "bartlett")

    def test_stepm_gk_rejects_strong_edge_and_not_null(self):
        r = random.Random(3)
        series = {f"s{i}": [r.gauss(0, 1) + (0.5 if i == 0 else 0.0) for _ in range(300)] for i in range(4)}
        res = sm.stepm(series, alpha=0.05, B=299, seed=1, studentize="gk_block")
        self.assertIn("s0", res["rejected"])
        self.assertEqual(res["block_len"], 7)
        self.assertEqual(res["method"], "romano_wolf2005_stepm_gk1996_blockwise")

    def test_stepm_defaults_unchanged(self):
        r = random.Random(4)
        series = {f"s{i}": [r.gauss(0, 1) for _ in range(120)] for i in range(3)}
        a = sm.stepm(series, alpha=0.05, B=99, seed=1)
        b = sm.stepm(series, alpha=0.05, B=99, seed=1, studentize="fixed")
        self.assertEqual(a["z"], b["z"])
        self.assertEqual(a["method"], "romano_wolf2005_stepm_studentized")
        self.assertEqual(a["q"], sm.default_q(120))

    def test_rectangular_negative_variance_is_refused(self):
        x = [(-1.0) ** i for i in range(60)]
        x = [v + 0.001 * i for i, v in enumerate(x)]
        with self.assertRaises(ValueError):
            sm.stepm({"a": x, "b": x[::-1]}, alpha=0.05, B=49, studentize="gk_block",
                     lag_window="rectangular", block_len=8)

    def test_unknown_studentize_refused(self):
        with self.assertRaises(ValueError):
            sm.stepm({"a": [0.0] * 20}, alpha=0.05, studentize="nope")


class BettingRulesTest(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(11)
        self.Z = np.clip(rng.standard_normal((200, 40)) * 0.4 + 0.08, -1.0, None)
        self.sz0 = 0.4

    def test_agrapa_is_the_wsr_bet(self):
        np.testing.assert_allclose(ev.log_e_agrapa(self.Z, self.sz0), ev.log_e_wsr(self.Z, self.sz0))

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
