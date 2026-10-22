"""Sanity checks for run/evalue_walkforward_sim.py (synthetic numbers only).

    cd run && python3 -m unittest test_evalue_walkforward
"""

import math
import unittest

import numpy as np

import evalue_walkforward_sim as ev


class EValueWalkForwardTest(unittest.TestCase):
    def test_skew_t_is_centred_with_unit_variance(self):
        z = ev.skew_t(np.random.default_rng(1), 2_000_000)
        self.assertAlmostEqual(float(z.mean()), 0.0, delta=0.005)
        self.assertAlmostEqual(float(z.var()), 1.0, delta=0.03)
        self.assertLess(float(np.mean(z ** 3)), 0.0)          # left skew

    def test_garch_has_unit_variance_and_volatility_clustering(self):
        e = ev.innovations(np.random.default_rng(2), "garch_t4", 400, 2000)
        self.assertAlmostEqual(float(e.var()), 1.0, delta=0.15)
        a = np.abs(e[:, 1:]).ravel() - np.abs(e).mean()
        b = np.abs(e[:, :-1]).ravel() - np.abs(e).mean()
        self.assertGreater(float(np.mean(a * b)), 0.02)        # |r| autocorrelated

    def test_block_stop_and_floor(self):
        r = np.zeros((1, ev.DAYS))
        r[0, 2] = -0.03
        r[0, 3] = -0.05                     # gap through the stop
        r[0, 4] = 0.10                      # after the stop: not traded
        X, over, end = ev.block_pnl(r, L=0.02, B=0.06)
        self.assertAlmostEqual(float(X[0, 0]), -0.03)        # stopped day 2
        self.assertFalse(over[0, 0])
        X, over, _ = ev.block_pnl(r, L=0.04, B=0.06)
        self.assertAlmostEqual(float(X[0, 0]), -0.06)        # floor at -B
        self.assertTrue(over[0, 0])
        self.assertAlmostEqual(float(end[0, 0]), 0.02)

    def test_bet_factor_never_negative_with_allowance(self):
        B, c = 0.1, 0.005
        X = np.array([[-B, 0.0, 0.3]])
        Z = (X - c) / (B + c)
        self.assertGreaterEqual(float(Z.min()), -1.0 - 1e-12)
        self.assertTrue(np.all(1 + ev.LAM_MAX * Z > 0))

    def test_size_under_null_is_below_alpha(self):
        rng = np.random.default_rng(3)
        L, B = ev.L_MULT * ev.BLOCK_SD, ev.B_MULT * ev.BLOCK_SD
        X, _, _ = ev.direct_blocks(rng, "garch_t4", 3000, 60, 0.0, L, B)
        r = ev.run_bets(X, B, (60,), prior_srs=(4.0,), fixed_max=True)
        for name, d in r.items():
            self.assertLessEqual(d["reject_by_60"], ev.ALPHA + 3 * ev.se(ev.ALPHA, 3000), name)

    def test_fixed_bet_mean_e_value_at_most_one_under_null(self):
        Z = np.random.default_rng(4).choice((-0.5, 0.5), size=(200_000, 4))
        E = np.exp(ev.log_e_fixed(Z, 0.75)[:, -1])
        self.assertLess(float(E.mean()), 1.02)

    def test_strong_edge_is_found(self):
        rng = np.random.default_rng(5)
        L, B = ev.L_MULT * ev.BLOCK_SD, ev.B_MULT * ev.BLOCK_SD
        X, _, _ = ev.direct_blocks(rng, "iid_normal", 500, 60, 4.0, L, B)
        self.assertGreater(ev.run_bets(X, B, (36,))["plugin"]["reject_by_36"], 0.8)

    def test_sign_flip_keeps_absolute_returns(self):
        x = np.random.default_rng(6).standard_normal(100)
        rng = np.random.default_rng(7)
        for how in ("sign_flip", "block_sign_flip"):
            d = ev.null_draws(rng, x, 5, how)
            np.testing.assert_allclose(np.abs(d), np.abs(x)[None, :].repeat(5, 0))
        d = ev.null_draws(rng, x, 5, "stationary_boot")
        self.assertAlmostEqual(float(d.mean()), 0.0, delta=0.5)

    def test_pipeline_on_martingale_asset_loses_on_average(self):
        rng = np.random.default_rng(8)
        L, B = ev.L_MULT * ev.BLOCK_SD, ev.B_MULT * ev.BLOCK_SD
        X, ch = ev.pipeline_blocks(rng, "garch_t4", 200, 12, 0.0, L, B)
        self.assertLess(float(X.mean()), 0.0)
        self.assertTrue(np.all(X >= -B - 1e-12))
        self.assertTrue(set(np.unique(ch)) <= set(range(-1, 2 * len(ev.LOOKBACKS))))

    def test_wsr_moments_are_predictable(self):
        Z = np.random.default_rng(10).standard_normal((3, 8)) * 0.2
        mu, s2 = ev._wsr_moments(Z, 0.2)
        Z2 = Z.copy()
        Z2[:, 5:] += 5.0                                     # change the future only
        mu2, s22 = ev._wsr_moments(Z2, 0.2)
        np.testing.assert_allclose(mu[:, :6], mu2[:, :6])
        np.testing.assert_allclose(s2[:, :6], s22[:, :6])
        self.assertTrue(np.all(mu[:, 0] == 0) and np.allclose(s2[:, 0], 0.04))
        # WS-R eq. (15) structure: mu_1 = (0 + Z_1) / 2
        np.testing.assert_allclose(mu[:, 1], Z[:, 0] / 2)

    def test_wsr_bets_stay_in_range(self):
        Z = np.random.default_rng(11).uniform(-1, 3, (50, 30))
        for f in (ev.log_e_wsr, ev.log_e_hedged_prpl_bet):
            self.assertTrue(np.all(np.isfinite(f(Z, 0.5))))

    def test_mmc_paths_share_the_random_stream(self):
        a = ev.garch_t_paths(5, 3, 50, 0.05, 0.80, 4.0)
        b = ev.garch_t_paths(5, 3, 50, 0.15, 0.80, 4.0)
        np.testing.assert_allclose(np.sign(a), np.sign(b))   # same u_i, other y
        self.assertFalse(np.allclose(a, b))

    def test_alpha_split_sums_to_family_alpha_and_either_dominates(self):
        out = ev.part_split(np.random.default_rng(9), 300, nb=24)
        for r in out["rows"]:
            self.assertAlmostEqual(r["alpha_v2"] + r["alpha_v3"], ev.ALPHA)
            self.assertGreaterEqual(r["either_by_24"], max(r["v2_reject_at_12"], r["v3_reject_by_24"]))


if __name__ == "__main__":
    unittest.main()
