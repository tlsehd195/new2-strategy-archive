"""Tests for round 5 (COIN-M basis / volume share) of the new-source search (ADR 0100). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import newsrc_r5_dev as r5  # noqa: E402


class NewSrcR5Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r5.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(r5.KEYS))
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_U50" if h != "QB2_QBASIS_XS" else h for h, _ in r5.KEYS))

    def test_expiry_and_basis(self):
        self.assertEqual(r5.expiry_ms("ADAUSD_220325"), bu.ms("2022-03-25") + 8 * 3_600_000)
        days = np.array([bu.ms("2022-01-01"), bu.ms("2022-03-15")])
        close = pd.DataFrame({"ADAUSD_PERP": [1.0, 1.0], "ADAUSD_220325": [1.01, 1.01],
                              "ADAUSD_220624": [1.02, 1.02]}, index=days)
        b = r5.basis_table({"close": close}, days)
        left0 = (r5.expiry_ms("ADAUSD_220325") - int(days[0])) / bu.DAY_MS
        self.assertAlmostEqual(b["ADAUSDT"].iloc[0], 0.01 * 365 / left0)
        # 10 days before March expiry -> rolls to June
        left1 = (r5.expiry_ms("ADAUSD_220624") - int(days[1])) / bu.DAY_MS
        self.assertAlmostEqual(b["ADAUSDT"].iloc[1], 0.02 * 365 / left1)

    def test_pick_k_tercile(self):
        names = [f"N{k}" for k in range(7)]
        w = r5.pick_k(pd.Series(np.arange(7.0), index=names), names, 3, 6, long_top=False)
        self.assertEqual(len(w), 4)
        self.assertAlmostEqual(sum(w.values()), 0.0)
        self.assertEqual(r5.pick_k(pd.Series(np.arange(5.0), index=names[:5]), names, 3, 6, False), {})


if __name__ == "__main__":
    unittest.main()
