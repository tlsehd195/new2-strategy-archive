"""Tests for round 15d (ADR 0113): Qullamaggie breakout and Minervini trend template. Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import traders_r15d_dev as tr  # noqa: E402


def qm_path(breakout: float) -> pd.Series:
    up = np.linspace(100, 160, 50)            # 60% move
    flat = 150 + np.linspace(4, 1, 49) * np.sin(np.arange(49))  # tightening range
    flat[-1] = 151.0  # last close above the 20-day SMA
    return pd.Series(np.concatenate([np.full(10, 100.0), up, flat, [breakout]]))


class TradersR15dTest(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(tr.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(tr.KEYS))
        self.assertEqual(p["context_id"], tr.CONTEXT_ID)
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in tr.KEYS))

    def test_qm_trigger_and_no_lookahead(self):
        s = qm_path(155.0)
        c = len(s) - 1
        self.assertGreaterEqual(tr.qm_setup(s, c), 1.30)
        self.assertTrue(np.isnan(tr.qm_setup(qm_path(151.0), c)))
        s2 = pd.concat([s, pd.Series([1.0, 1.0])], ignore_index=True)
        self.assertEqual(tr.qm_setup(s, c), tr.qm_setup(s2, c))

    def test_qm_exits(self):
        close = pd.DataFrame({"A": np.full(40, 100.0)})
        st = {"pos": {"A": {"stop": 101.0, "days": 1}}}
        kept, new = tr.qm_step(st, pd.Series(dtype=float), close, 30)
        self.assertEqual((kept, new), ([], []))
        st = {"pos": {"A": {"stop": 90.0, "days": tr.MAX_DAYS - 1}}}
        self.assertEqual(tr.qm_step(st, pd.Series(dtype=float), close, 30)[0], [])
        st = {"pos": {"A": {"stop": 90.0, "days": 1}}}
        self.assertEqual(tr.qm_step(st, pd.Series(dtype=float), close, 30)[0], ["A"])

    def test_trend_template(self):
        n = 400
        close = pd.DataFrame({"UP": np.linspace(50, 200, n), "DN": np.linspace(200, 50, n),
                              "MID": np.linspace(100, 120, n)})
        self.assertEqual(tr.trend_template(close, n - 1, ["UP", "DN", "MID"]), ["UP"])
        self.assertEqual(tr.trend_template(close, 300, ["UP", "DN", "MID"]), [])


if __name__ == "__main__":
    unittest.main()
