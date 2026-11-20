"""Tests for the round-1 new-source DEV check (ADR 0096). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import newsrc_r1_dev as nr  # noqa: E402


class NewSrcR1Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(nr.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], nr.N_TRIALS)
        self.assertEqual(nr.N_TRIALS, 7)
        ids = sorted(f"{h}_{u}" if u != "EVT" else h for h, u in nr.TRIAL_KEYS)
        self.assertEqual(ids, sorted(p["trials"]))
        self.assertEqual(nr.HOLD, 14)
        self.assertEqual(nr.HIGH_LB, 180)
        self.assertEqual(nr.EV_WEIGHT, -0.10)

    def test_spot_name_and_time_units(self):
        self.assertEqual(nr.spot_name("1000PEPEUSDT"), "PEPEUSDT")
        self.assertEqual(nr.spot_name("1000000MOGUSDT"), "MOGUSDT")
        self.assertEqual(nr.spot_name("1MBABYDOGEUSDT"), "BABYDOGEUSDT")
        self.assertEqual(nr.spot_name("SOLUSDT"), "SOLUSDT")
        self.assertEqual(nr.spot_name("1INCHUSDT"), "1INCHUSDT")
        self.assertEqual(nr.norm_ms(1735689600000000), 1735689600000)
        self.assertEqual(nr.norm_ms(1735689600000), 1735689600000)

    def test_forbidden_windows(self):
        for d in ("2020-06-01", "2023-12-01", "2025-11-01"):
            with self.assertRaises(SystemExit):
                bu.assert_allowed(bu.ms(d), bu.ms(d))

    def test_signals_use_only_cut(self):
        n = 220
        days = np.arange(bu.ms("2021-01-01"), bu.ms("2021-01-01") + n * bu.DAY_MS, bu.DAY_MS)
        close = pd.DataFrame({"A": np.linspace(1, 2, n), "B": np.linspace(2, 1, n)}, index=days)
        qv = pd.DataFrame({"A": 4.0, "B": 1.0}, index=days)
        spot = pd.DataFrame({"A": 1.0, "B": 1.0}, index=days)
        prem = pd.DataFrame({"A": 0.0, "B": 0.0}, index=days)
        piv = {"close": close, "qv": qv}
        cut = 200
        s = nr.xs_signals(piv, spot, prem, cut)
        self.assertAlmostEqual(s["P1_HIGH180"]["A"], 1.0)
        self.assertLess(s["P1_HIGH180"]["B"], 1.0)
        self.assertAlmostEqual(s["V1_PERPSPOT_LEVEL"]["A"], np.log(4.0))
        self.assertAlmostEqual(s["V2_PERPSPOT_CHANGE"]["A"], 0.0)
        # future values must not change the signal
        close2 = close.copy()
        close2.iloc[cut + 1:] = 100.0
        s2 = nr.xs_signals({"close": close2, "qv": qv}, spot, prem, cut)
        self.assertEqual(s["P1_HIGH180"]["B"], s2["P1_HIGH180"]["B"])
        # not enough history -> no P signal
        self.assertTrue(np.isnan(nr.xs_signals(piv, spot, prem, 100)["P1_HIGH180"]["A"]))

    def test_insufficient_events_flag(self):
        self.assertTrue(nr.flag({}, n_events_s1=5).startswith("INSUFFICIENT"))


if __name__ == "__main__":
    unittest.main()
