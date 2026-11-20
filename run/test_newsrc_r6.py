"""Tests for round 6 (sessions, jumps, funding volatility) of the new-source search (ADR 0101). Needs numpy and pandas."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import newsrc_r6_dev as r6  # noqa: E402


class NewSrcR6Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r6.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(r6.KEYS))
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in r6.KEYS))
        self.assertEqual(r6.BOUNDS, (0, 480, 960))

    def test_session_returns(self):
        d0 = bu.ms("2021-02-01")
        days = np.array([d0, d0 + bu.DAY_MS])
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "sess").mkdir()
            rows = [(d0, 100.0), (d0 + 8 * 3_600_000, 110.0), (d0 + 16 * 3_600_000, 99.0),
                    (d0 + bu.DAY_MS, 99.0)]
            (Path(t) / "sess" / "AUSDT-sess-2021-02.csv").write_text(
                "open_time,open\n" + "\n".join(f"{a},{b}" for a, b in rows) + "\n")
            s = r6.load_sessions(Path(t), days, ["AUSDT"])
        self.assertAlmostEqual(s["asia"]["AUSDT"].iloc[0], np.log(1.1))
        self.assertAlmostEqual(s["eu"]["AUSDT"].iloc[0], np.log(0.9))
        self.assertAlmostEqual(s["us"]["AUSDT"].iloc[0], 0.0)
        self.assertTrue(np.isnan(s["us"]["AUSDT"].iloc[1]))

    def test_jumps(self):
        r = pd.DataFrame({"A": [0.01] * 88 + [0.2, -0.2], "B": [0.01] * 90})
        j = r6.jumps(r)
        self.assertEqual(j["A"], 2)
        self.assertEqual(j["B"], 0)


if __name__ == "__main__":
    unittest.main()
