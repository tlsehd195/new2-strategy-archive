"""Tests for the broad-universe DEV check (ADR 0035). Needs numpy and pandas."""

import json
import math
import sys
import unittest
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as b  # noqa: E402


class BroadUniverseTest(unittest.TestCase):
    def test_never_read_ranges_refused(self):
        for lo, hi in (("2020-12-01", "2021-01-31"), ("2023-09-01", "2023-09-19"),
                       ("2024-06-18", "2024-07-01"), ("2025-10-01", "2025-10-21")):
            with self.assertRaises(SystemExit):
                b.assert_allowed(b.ms(lo), b.ms(hi))
        for lo, hi, _ in b.SEGMENTS.values():
            b.assert_allowed(b.ms(lo), b.ms(hi))

    def test_pick_sides_weights_and_ties(self):
        names = [f"S{i:02d}USDT" for i in range(30)]
        sig = pd.Series({n: float(i) for i, n in enumerate(names)})
        w = b.pick(sig, names, long_top=True)
        self.assertEqual(len(w), 2 * b.K_SIDE)
        self.assertAlmostEqual(sum(w.values()), 0.0)
        self.assertAlmostEqual(sum(abs(v) for v in w.values()), 1.0)
        self.assertGreater(w["S29USDT"], 0)
        self.assertLess(w["S00USDT"], 0)
        rev = b.pick(sig, names, long_top=False)
        self.assertLess(rev["S29USDT"], 0)
        tied = pd.Series({n: 1.0 for n in names})
        self.assertEqual(b.pick(tied, names, True), b.pick(tied, list(reversed(names)), True))
        self.assertEqual(b.pick(sig, names[:15], True), {})

    def test_cost_matches_assumed_profile(self):
        cost = b.load_cost()
        prof = json.loads(b.COST_PROFILE.read_text())
        self.assertAlmostEqual(cost["fixed"], 0.0010)
        qv = 1_000_000.0
        want = 0.0010 + prof["impact_coefficient"] * math.sqrt(b.ORDER_VALUE / qv)
        self.assertAlmostEqual(b.side_cost(qv, cost, "assumed"), want)
        self.assertAlmostEqual(b.side_cost(qv, cost, "no_impact"), 0.0010)

    def test_cost_from_measured_record(self):
        rec = Path(__file__).resolve().parent / "audit" / "alt_cost_measured_20261027" / "estimate_v3_alts.json"
        cost = b.load_cost_record(rec)
        q = json.loads(rec.read_text())["params"]
        self.assertAlmostEqual(cost["fixed"], q["fee_rate"] + q["half_spread"])
        self.assertAlmostEqual(b.side_cost(1e6, cost, "assumed"),
                               cost["fixed"] + q["impact_coefficient"] * math.sqrt(b.ORDER_VALUE / 1e6))

    def test_volume_scaled_cost_rises_as_volume_falls(self):
        cal_path = Path(__file__).resolve().parent / "audit" / "lowliq_cost_20261027" / "calibration_v1.json"
        cost = b.load_cost_calibration(cal_path)
        cal = cost["calib"]
        qs = [1e3, 1e4, 1e5, 1e6, 1e7, 1e9]
        c = [b.side_cost(1e6, cost, "assumed", q) for q in qs]
        self.assertEqual(c, sorted(c, reverse=True))
        self.assertEqual(c[0], c[1])  # clipped at q_min
        self.assertEqual(c[-1], c[-2])  # no credit above the most liquid sample
        for sym, v in cal["per_symbol"].items():  # envelope covers every measured symbol
            from lowliq_cost_calibration import cost_params
            fixed, k = cost_params(cal, v["qv15_median"])
            self.assertGreaterEqual(fixed - cal["fee_rate"], v["half_spread"] * (1 - 1e-9), sym)
            self.assertGreaterEqual(k, v["impact_coef"] * (1 - 1e-9), sym)

    def test_prereg_is_the_committed_one(self):
        p = json.loads(b.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual([h["id"] for h in p["hypotheses"]], list(b.LONG_TOP))


if __name__ == "__main__":
    unittest.main()
