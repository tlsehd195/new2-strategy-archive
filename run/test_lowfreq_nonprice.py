"""Tests for the low-frequency / metrics DEV check (ADR 0094). Needs numpy and pandas."""

import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import lowfreq_nonprice_dev as lf  # noqa: E402

HDR = ("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
       "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio\n")


class LowFreqNonPriceTest(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(lf.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_counted_trials"], lf.N_TRIALS)
        self.assertEqual(lf.N_TRIALS, 8)
        ids = [h["id"] for h in p["hypotheses"]]
        self.assertEqual(sorted(set(h for h, _ in lf.TRIAL_KEYS)), sorted(ids))
        for h in p["hypotheses"]:
            if h["id"] in lf.M_LONG_TOP:
                self.assertEqual(h["long"] == "highest", lf.M_LONG_TOP[h["id"]])
                self.assertEqual(sorted(h["universes"]), sorted(u for k, u in lf.TRIAL_KEYS if k == h["id"]))
        self.assertEqual(lf.HOLD, 28)

    def test_metrics_never_read_and_start(self):
        with self.assertRaises(SystemExit):
            bu.assert_allowed(bu.ms("2023-09-20"), bu.ms("2023-09-20"))
        # first M grid day: cut-28 must be on/after the metrics start
        lo, hi, first = lf.SEGMENTS["S1"]
        days = np.arange(bu.ms(lo), bu.ms(hi) + 1, bu.DAY_MS)
        reb = lf.wu.rebalance_days(days, first, 28)
        ok = [i for i in reb if days[i - 2 - 28] >= bu.ms(lf.METRICS_START)]
        self.assertEqual(int(days[ok[0]]), bu.ms(lf.EVAL_START["M"]["S1"]))
        self.assertGreaterEqual(bu.ms(lf.EVAL_START["M"]["S2"]) - 30 * bu.DAY_MS, bu.ms(lf.SEGMENTS["S2"][0]))

    def test_parse_metrics_day(self):
        txt = HDR + "2022-01-01 00:05:00,XUSDT,100,1,1,2.0,1.5,1\n2022-01-01 23:55:00,XUSDT,120,1,1,4.0,2.5,1\n"
        d = lf.parse_metrics_day(txt)
        self.assertEqual(d["oi"], 120.0)  # last row of the day
        self.assertAlmostEqual(d["retail"], 2.0)
        self.assertAlmostEqual(d["top"], 3.0)
        self.assertIsNone(lf.parse_metrics_day(HDR))

    def test_m_signals_and_min_days(self):
        days = np.arange(40) * bu.DAY_MS
        cut = 35
        met = {}
        for k in range(7):
            met[("A", int(days[cut - k]))] = {"oi": 200.0 if k == 0 else 150.0, "retail": 2.0, "top": 4.0}
        for k in range(5):  # B has only 5 of 7 ratio days
            met[("B", int(days[cut - k]))] = {"oi": 50.0, "retail": 1.0, "top": 1.0}
        met[("A", int(days[cut - 28]))] = {"oi": 100.0, "retail": 2.0, "top": 4.0}
        s = lf.m_signals(met, days, cut, ["A", "B"])
        self.assertAlmostEqual(s["M1_OIGROWTH28"]["A"], math.log(2.0))
        self.assertNotIn("B", s["M1_OIGROWTH28"])  # no OI 28 days back
        self.assertAlmostEqual(s["M2_RETAILLS7"]["A"], 2.0)
        self.assertNotIn("B", s["M2_RETAILLS7"])
        self.assertAlmostEqual(s["M3_TOPVSRETAIL7"]["A"], math.log(2.0))

    def test_ts_weights(self):
        sig = pd.Series({"A": 0.1, "B": -0.2, "C": float("nan"), "D": 0.0})
        u = ["A", "B", "C", "D"]
        lf_w = lf.ts_weights(sig, u, "LONGFLAT")
        self.assertEqual(lf_w, {"A": 1 / 3})
        ls = lf.ts_weights(sig, u, "LS")
        self.assertEqual(ls, {"A": 1 / 3, "B": -1 / 3, "D": -1 / 3})
        self.assertAlmostEqual(sum(abs(v) for v in ls.values()), 1.0)
        self.assertEqual(lf.ts_weights(pd.Series(dtype=float), u, "LS"), {})

    def test_ts_signal_needs_full_lookback(self):
        close = pd.DataFrame({"A": np.linspace(1, 2, 100)})
        self.assertTrue(np.isnan(lf.ts_signal(close, 83)["A"]))
        self.assertAlmostEqual(lf.ts_signal(close, 90)["A"], close["A"].iloc[90] / close["A"].iloc[6] - 1)

    def test_m_target_quintile(self):
        u = [f"S{i}" for i in range(50)]
        sig = pd.Series({n: float(i) for i, n in enumerate(u)})
        w = lf.target("M1_OIGROWTH28", "U50", u, None, {"M1_OIGROWTH28": sig})
        self.assertEqual(len(w), 20)
        self.assertGreater(w["S0"], 0)  # lowest OI growth is long
        self.assertLess(w["S49"], 0)
        w3 = lf.target("M3_TOPVSRETAIL7", "U50", u, None, {"M3_TOPVSRETAIL7": sig})
        self.assertGreater(w3["S49"], 0)  # highest top-vs-retail is long

    def test_prior_trials_excludes_own_line(self):
        rows = [json.loads(x) for x in lf.TRIALS.read_text().splitlines() if x.strip()]
        own = sum(r["dev_evaluated"] for r in rows if r.get("context_id") == lf.CONTEXT_ID)
        self.assertEqual(lf.prior_trials(), sum(r.get("dev_evaluated", 0) for r in rows) - own)


if __name__ == "__main__":
    unittest.main()
