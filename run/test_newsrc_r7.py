"""Tests for round 7 (Coin Metrics spot volume share, size, attention) of the new-source search (ADR 0102)."""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import newsrc_r7_dev as r7  # noqa: E402


class NewSrcR7Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r7.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(r7.KEYS))
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in r7.KEYS))
        self.assertEqual(r7.LONG_TOP, {"XS1_SPOTSHARE_LEVEL": True, "XS2_SPOTSHARE_CHANGE": True,
                                       "SZ1_MCAP_SIZE": False, "AT1_ATTENTION": False})


if __name__ == "__main__":
    unittest.main()
