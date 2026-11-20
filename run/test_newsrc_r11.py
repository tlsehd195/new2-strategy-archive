"""Tests for round 11 of the new-source search (ADR 0106). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import newsrc_r11_dev as r11  # noqa: E402


class NewSrcR11Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r11.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in r11.KEYS))

    def test_signals_and_no_lookahead(self):
        n, cut = 120, 100
        idx = range(n)
        close = pd.DataFrame({"A": np.full(n, 10.0), "B": np.full(n, 10.0)}, index=idx)
        high, low = close + 1.0, close - 1.0
        close.loc[:, "A"] = 10.5  # closes near the high
        qv = pd.DataFrame({"A": np.full(n, 100.0), "B": np.full(n, 100.0)}, index=idx)
        cnt = pd.DataFrame({"A": np.full(n, 10.0), "B": np.full(n, 10.0)}, index=idx)
        cnt.loc[cut - 13:, "A"] = 5.0  # trade size doubles for A
        r = pd.DataFrame({"A": np.full(n, 0.01), "B": np.full(n, 0.01)}, index=idx)
        s = r11.compute(close, qv, high, low, cnt, r, cut)
        self.assertAlmostEqual(s["TS1_TRADESIZE_CHANGE"]["A"], np.log(2.0))
        self.assertAlmostEqual(s["TS1_TRADESIZE_CHANGE"]["B"], 0.0)
        self.assertAlmostEqual(s["CL1_CLOSE_LOCATION"]["A"], 0.5)
        self.assertAlmostEqual(s["CL1_CLOSE_LOCATION"]["B"], 0.0)
        self.assertAlmostEqual(s["IL1_AMIHUD_CHANGE"]["A"], 0.0)
        cnt2 = cnt.copy()
        cnt2.iloc[cut + 1:] = 1.0
        s2 = r11.compute(close, qv, high, low, cnt2, r, cut)
        self.assertEqual(s["TS1_TRADESIZE_CHANGE"]["A"], s2["TS1_TRADESIZE_CHANGE"]["A"])

    def test_resid(self):
        x = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        y = 2 * x + 1
        self.assertTrue(np.allclose(r11.resid(y, x).values, 0.0))


if __name__ == "__main__":
    unittest.main()
