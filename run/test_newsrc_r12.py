"""Tests for round 12 of the new-source search (ADR 0107). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import newsrc_r12_dev as r12  # noqa: E402


class NewSrcR12Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r12.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(r12.KEYS))
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in r12.KEYS))

    def test_breadth_and_frog(self):
        n, cut = 220, 200
        up, dn = np.linspace(1, 2, n), np.linspace(2, 1, n)
        close = pd.DataFrame({"A": up, "B": up, "C": dn}, index=range(n))
        self.assertAlmostEqual(r12.breadth(close, cut), 2 / 3)
        wig = 1 + 0.01 * np.sin(np.arange(n))
        close = close.assign(B=up * wig)
        r = close.pct_change(fill_method=None)
        f = r12.frog(close, r, cut)
        self.assertGreater(f["A"], f["C"])
        close2 = close.copy()
        close2.iloc[cut + 1:] = 50.0
        self.assertEqual(r12.breadth(close, cut), r12.breadth(close2, cut))

    def test_dispersion_flag(self):
        rng = np.random.default_rng(0)
        n, cut = 220, 210
        r = pd.DataFrame(rng.normal(0, 0.05, (n, 10)))
        r.iloc[cut - 13: cut + 1] *= 0.1
        self.assertEqual(r12.dispersion_on(r, cut), 1.0)
        r.iloc[cut - 13: cut + 1] *= 100
        self.assertEqual(r12.dispersion_on(r, cut), 0.0)

    def test_weights(self):
        u = ["A", "B"]
        self.assertEqual(r12.weights("BR1_BREADTH", pd.Series(1.0, index=u), u, {}), {"A": 0.5, "B": 0.5})
        self.assertEqual(r12.weights("DS1_DISPERSION", pd.Series(np.nan, index=u), u, {}), {})


if __name__ == "__main__":
    unittest.main()
