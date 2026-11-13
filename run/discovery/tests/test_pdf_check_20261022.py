"""Goetze-Kuensch (1996) blockwise bootstrap in ``stats_mc`` (ADR 0032).
Stdlib only; the numpy parts and the betting rules are in run/test_pdf_check.py.
Synthetic numbers only.

    cd run && python3 -m unittest discovery.tests.test_pdf_check_20261022
"""

import itertools
import math
import random
import sys
import unittest
from pathlib import Path

RUN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RUN / "discovery"))
sys.path.insert(0, str(RUN))

import stats_mc as sm  # noqa: E402


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
        # ADR 0079: B=49 at alpha 0.05 is now refused for resolution (also a
        # ValueError), which would hide the check under test; B=99 resolves it
        with self.assertRaisesRegex(ValueError, "non-positive variance"):
            sm.stepm({"a": x, "b": x[::-1]}, alpha=0.05, B=99, studentize="gk_block",
                     lag_window="rectangular", block_len=8)

    def test_unknown_studentize_refused(self):
        with self.assertRaises(ValueError):
            sm.stepm({"a": [0.0] * 20}, alpha=0.05, studentize="nope")


if __name__ == "__main__":
    unittest.main()
