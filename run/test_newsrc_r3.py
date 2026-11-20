"""Tests for round 3 of the new-source search (ADR 0098). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import newsrc_r3_dev as r3  # noqa: E402


class NewSrcR3Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r3.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(r3.KEYS))
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in r3.KEYS))
        self.assertEqual(p["segments"]["S1"]["eval_from"], r3.EVAL_START["S1"])
        self.assertEqual(p["segments"]["S2"]["eval_from"], r3.EVAL_START["S2"])

    def test_af1_on_off(self):
        n = 200
        names = ["A", "B"]
        f = pd.DataFrame(0.001, index=range(n), columns=names)
        f.iloc[-7:] = 0.01  # recent funding high -> flat
        self.assertEqual(r3.af1(f, n - 1, names).sum(), 0.0)
        f.iloc[-7:] = -0.01  # low -> long, gross 1
        self.assertAlmostEqual(r3.af1(f, n - 1, names).sum(), 1.0)

    def test_ae1_sign(self):
        close = pd.DataFrame({"ETHUSDT": [1.0] * 30, "X": np.linspace(1, 2, 30)})
        w = r3.ae1(close, 29, ["ETHUSDT", "X"])
        self.assertAlmostEqual(w["X"], 0.5)
        self.assertAlmostEqual(w["ETHUSDT"], -0.5)
        self.assertIsNone(r3.ae1(close, 29, ["X"]))

    def test_quiet_reversal_dollar_neutral(self):
        rng = np.random.default_rng(1)
        names = [f"N{k}" for k in range(20)]
        close = pd.DataFrame(np.cumprod(1 + rng.normal(0, 0.02, (40, 20)), axis=0), columns=names)
        qv = pd.DataFrame(rng.uniform(1, 2, (40, 20)), columns=names)
        w = r3.rv1(close, qv, 39, names)
        self.assertAlmostEqual(w.sum(), 0.0)
        self.assertEqual(len(w), 4)


if __name__ == "__main__":
    unittest.main()
