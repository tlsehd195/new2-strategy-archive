"""Tests for round 8 (Ethereum on-chain timing) of the new-source search (ADR 0103). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import newsrc_r8_dev as r8  # noqa: E402


class NewSrcR8Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r8.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(sorted(p["trials"]), sorted(h for h, _ in r8.KEYS))

    def test_states(self):
        n = 200
        e = pd.DataFrame({"CapMVRVCur": np.linspace(2, 1, n), "FlowInExNtv": 1.0, "FlowOutExNtv": 2.0,
                          "SplyExNtv": np.linspace(10, 5, n), "AdrActCnt": np.linspace(1, 2, n)})
        st = r8.states(e, n - 1)
        self.assertTrue(st["OC1_ETH_MVRV_TIMING"])
        self.assertTrue(st["OC2_ETH_NETFLOW_TIMING"])
        self.assertTrue(st["OC3_ETH_EXSUPPLY_TIMING"])
        self.assertTrue(st["OC4_ETH_ACTIVITY_TIMING"])
        e["CapMVRVCur"] = np.nan
        self.assertIsNone(r8.states(e, n - 1)["OC1_ETH_MVRV_TIMING"])


if __name__ == "__main__":
    unittest.main()
