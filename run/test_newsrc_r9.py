"""Tests for round 9 (age, lead-lag, weekend momentum) of the new-source search (ADR 0104)."""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import newsrc_r9_dev as r9  # noqa: E402


class NewSrcR9Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r9.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(r9.KEYS))
        self.assertEqual(sorted(p["trials"]), sorted(["AG1_AGE_U50", "AG2_AGE_W200", "LL1_LEADLAG_U50", "WK1_WEEKEND_MOM_U50"]))

    def test_weekend(self):
        self.assertTrue(r9.is_weekend(bu.ms("2021-02-06")))   # Saturday
        self.assertTrue(r9.is_weekend(bu.ms("2021-02-07")))   # Sunday
        self.assertFalse(r9.is_weekend(bu.ms("2021-02-08")))  # Monday


if __name__ == "__main__":
    unittest.main()
