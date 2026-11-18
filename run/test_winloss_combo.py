"""Tests of the ADR 0092 win-rate / loss-profile catalog and combination helpers (synthetic inputs only)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import winloss_catalog as cat  # noqa: E402
import winloss_combo_dev as wc  # noqa: E402


class CatalogGroups(unittest.TestCase):
    def test_high_win(self):
        self.assertEqual(cat.group_of({"n": 30, "hit_rate": 0.6}), "A_high_win")
        self.assertEqual(cat.group_of({"n": 29, "hit_rate": 0.9}), "other")

    def test_low_win_small_loss_needs_every_recorded_metric(self):
        base = {"n": 100, "hit_rate": 0.3}
        self.assertEqual(cat.group_of(base), "B_undetermined")
        self.assertEqual(cat.group_of({**base, "worst_bp": -900.0}), "B_low_win_small_loss")
        self.assertEqual(cat.group_of({**base, "worst_bp": -900.0, "max_dd_pct": 31.0}), "other")
        self.assertEqual(cat.group_of({**base, "worst_bp": -1001.0}), "other")
        self.assertEqual(cat.group_of({**base, "payoff": 1.4}), "other")
        self.assertEqual(cat.group_of({"n": 100, "hit_rate": 0.5, "worst_bp": -10.0}), "other")

    def test_missing_values_stay_none(self):
        r = cat.row(family="f", config="c", seg="S1", n="12", hit_rate=float("nan"))
        self.assertIsNone(r["hit_rate"])
        self.assertIsNone(r["avg_win_bp"])
        self.assertEqual(r["n"], 12.0)

    def test_payoff_derived_only_from_recorded_win_and_loss(self):
        r = cat.row(avg_win_bp=300.0, avg_loss_bp=-100.0)
        self.assertAlmostEqual(r["payoff"], 3.0)
        self.assertIsNone(cat.row(avg_win_bp=300.0)["payoff"])


class Helpers(unittest.TestCase):
    def test_trade_profile(self):
        p = wc.trade_profile([0.02, 0.02, -0.01, -0.03])
        self.assertEqual(p["n"], 4)
        self.assertAlmostEqual(p["hit_rate"], 0.5)
        self.assertAlmostEqual(p["avg_win_bp"], 200.0)
        self.assertAlmostEqual(p["avg_loss_bp"], -200.0)
        self.assertAlmostEqual(p["payoff"], 1.0)
        self.assertAlmostEqual(p["breakeven_hit_rate"], 0.5)
        self.assertAlmostEqual(p["worst_bp"], -300.0)

    def test_regime_uses_only_past_days(self):
        days_full = list(range(0, 10 * wc.DAY_MS, wc.DAY_MS))
        mkt = [0.0] * 10
        mkt[5] = 1.0                         # return realised from open of day 5 to open of day 6
        r = wc.trailing_regime(days_full, mkt, days_full, 3)
        self.assertEqual(r[5], -1.0)         # day 5's own return must not be known at its open
        self.assertEqual(r[6], 1.0)
        self.assertEqual(r[8], 1.0)
        self.assertEqual(r[9], -1.0)         # 3-day window [6, 9) no longer contains day 5

    def test_corr_and_series_stats(self):
        rng = np.random.default_rng(0)
        a = rng.normal(0, 0.01, 400)
        self.assertAlmostEqual(wc.corr(a, a)["pearson"], 1.0)
        self.assertAlmostEqual(wc.corr(a, -a)["spearman"], -1.0)
        self.assertIsNone(wc.corr(a[:10], a[:10])["pearson"])
        s = wc.series_stats(np.array([0.01, -0.02, 0.005, 0.0]))
        self.assertAlmostEqual(s["close_max_dd_pct"], 2.0)
        self.assertAlmostEqual(s["daily_hit_rate_active"], 2 / 3)

    def test_trial_count_does_not_double_count_adr0091(self):
        tot, pending = wc.prior_trials()
        self.assertGreater(tot, 12000)
        self.assertIn(pending, (0, wc.PENDING_ADR0091))


if __name__ == "__main__":
    unittest.main()
