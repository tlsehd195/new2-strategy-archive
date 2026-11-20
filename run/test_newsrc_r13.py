"""Tests for round 13 of the new-source search (ADR 0108). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import newsrc_r13_dev as r13  # noqa: E402


class NewSrcR13Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r13.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(r13.KEYS))
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in r13.KEYS))

    def test_pair_goes_against_the_spread(self):
        rng = np.random.default_rng(1)
        n, cut = 120, 110
        base = np.exp(np.cumsum(rng.normal(0, 0.03, n)))
        a = base * np.exp(rng.normal(0, 0.005, n))
        a[cut] = base[cut] * 1.05  # A is rich at cut
        other = np.exp(np.cumsum(rng.normal(0, 0.03, n)))
        close = pd.DataFrame({"A": a, "B": base, "C": other})
        w = r13.pair_weights(close, cut, 1, 1.0)
        self.assertAlmostEqual(w.sum(), 0.0)
        self.assertLess(w["A"], 0)
        self.assertGreater(w["B"], 0)
        self.assertEqual(w["C"], 0.0)
        close2 = close.copy()
        close2.iloc[cut + 1:] = 1.0
        self.assertTrue(w.equals(r13.pair_weights(close2, cut, 1, 1.0)))

    def test_peer_reversal(self):
        rng = np.random.default_rng(2)
        n, cut = 120, 110
        close = pd.DataFrame(np.exp(np.cumsum(rng.normal(0, 0.03, (n, 7)), axis=0)), columns=list("ABCDEFG"))
        r = close.pct_change(fill_method=None)
        s = r13.peer_reversal(close, r, cut)
        self.assertEqual(len(s), 7)
        self.assertTrue(np.isfinite(s).all())


if __name__ == "__main__":
    unittest.main()
