"""Tests for round 14 of the new-source search (ADR 0109). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import newsrc_r14_dev as r14  # noqa: E402


class NewSrcR14Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r14.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(r14.KEYS))
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in r14.KEYS))

    def test_stable_flags_use_only_cut(self):
        s = pd.Series(np.linspace(100, 200, 150))
        self.assertEqual(r14.st1_on(s, 100), 1.0)
        s_dn = s[::-1].reset_index(drop=True)
        self.assertEqual(r14.st1_on(s_dn, 100), 0.0)
        s2 = s.copy()
        s2.iloc[101:] = 1.0
        self.assertEqual(r14.st2_on(s, 100), r14.st2_on(s2, 100))
        self.assertTrue(np.isnan(r14.st2_on(s, 50)))

    def test_vwap_signal(self):
        n, cut = 40, 35
        close = pd.DataFrame({"A": np.full(n, 10.0), "B": np.full(n, 10.0)})
        vol = pd.DataFrame({"A": np.full(n, 2.0), "B": np.full(n, 2.0)})
        qv = pd.DataFrame({"A": np.full(n, 20.0), "B": np.full(n, 10.0)})  # B vwap 5
        sig = r14.vwap_signal(close, qv, vol, cut)
        self.assertAlmostEqual(sig["A"], 0.0)
        self.assertAlmostEqual(sig["B"], np.log(2.0))


if __name__ == "__main__":
    unittest.main()
