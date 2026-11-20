"""Tests for round 4 (Coin Metrics on-chain / supply) of the new-source search (ADR 0099). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import newsrc_r4_dev as r4  # noqa: E402


class NewSrcR4Test(unittest.TestCase):
    def test_prereg_and_amendment(self):
        p = json.loads(r4.PREREG.read_text())
        a = json.loads(r4.AMEND.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(r4.KEYS))
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in r4.KEYS))
        self.assertEqual(sorted(a["trials_affected"]), ["SI1_SUPPLY28_U50", "SI2_SUPPLY90_U50", "SI2_SUPPLY90_W200"])

    def test_asset_mapping(self):
        self.assertEqual(r4.asset("1000PEPEUSDT"), "pepe")
        self.assertEqual(r4.asset("SOLUSDT"), "sol")
        self.assertEqual(r4.asset("1INCHUSDT"), "1inch")

    def test_pick_needs_ten_names(self):
        names = [f"N{k}" for k in range(12)]
        sig = pd.Series(np.arange(12.0), index=names)
        w = r4.pick(sig, names + ["X"], long_top=False)
        self.assertEqual(len(w), 4)
        self.assertGreater(w["N0"], 0)
        self.assertLess(w["N11"], 0)
        self.assertEqual(r4.pick(sig.iloc[:9], names, False), {})


if __name__ == "__main__":
    unittest.main()
