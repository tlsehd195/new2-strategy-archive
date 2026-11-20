"""Tests for round 10 of the new-source search (ADR 0105). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import newsrc_r10_dev as r10  # noqa: E402


class NewSrcR10Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r10.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in r10.KEYS))

    def test_corr_and_z(self):
        m = pd.Series(np.arange(30.0))
        x = pd.DataFrame({"A": np.arange(30.0), "B": -np.arange(30.0)})
        c = r10.corr_with(x, m, 20)
        self.assertAlmostEqual(c["A"], 1.0)
        self.assertAlmostEqual(c["B"], -1.0)
        zz = r10.z(pd.Series([1.0, 2.0, 3.0]))
        self.assertAlmostEqual(zz.mean(), 0.0)


if __name__ == "__main__":
    unittest.main()
