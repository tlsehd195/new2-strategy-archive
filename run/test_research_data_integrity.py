"""ADR 0116: common data-integrity rules of the daily research scripts. Synthetic data only."""

import math
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import research_data_integrity as rdi  # noqa: E402

H = 3_600_000
D0 = bu.ms("2026-06-01")


def fund_df(rows):
    return pd.DataFrame(rows, columns=["sym", "t", "rate", "interval_ms"])


def eight_hourly(sym, days, rate=1e-4, jitter=0, iv=8):
    return [(sym, D0 + k * rdi.DAY_MS + h * H + jitter, rate, iv * H) for k in range(-1, days + 1) for h in (0, 8, 16)]


class FundingCalendarTest(unittest.TestCase):
    def test_complete_series_and_day_sum(self):
        fc = rdi.FundingCalendar(fund_df(eight_hourly("A", 5)), ["A"])
        self.assertEqual(fc.status("A", D0 + rdi.DAY_MS), "OK")
        # (D, D+1day]: 08:00, 16:00 and the next 00:00
        self.assertAlmostEqual(fc.day_sum("A", D0 + rdi.DAY_MS), 3e-4)

    def test_jitter_is_not_a_gap_and_is_attributed_to_the_held_day(self):
        fc = rdi.FundingCalendar(fund_df(eight_hourly("A", 5, jitter=6)), ["A"])
        self.assertEqual(fc.status("A", D0 + rdi.DAY_MS), "OK")
        self.assertAlmostEqual(fc.day_sum("A", D0 + rdi.DAY_MS), 3e-4)

    def test_missing_settlement_is_a_gap(self):
        rows = [r for r in eight_hourly("A", 5) if r[1] != D0 + 2 * rdi.DAY_MS + 8 * H]
        fc = rdi.FundingCalendar(fund_df(rows), ["A"])
        self.assertEqual(fc.status("A", D0 + 2 * rdi.DAY_MS), "FUNDING_SETTLEMENT_GAP")
        self.assertEqual(fc.status("A", D0 + 3 * rdi.DAY_MS), "OK")

    def test_no_series_no_interval_and_bad_rate(self):
        fc = rdi.FundingCalendar(fund_df(eight_hourly("A", 5)), ["A", "B"])
        self.assertEqual(fc.status("B", D0), "FUNDING_DATA_MISSING")
        rows = [(s, t, r, float("nan")) for s, t, r, _ in eight_hourly("A", 5)]
        self.assertEqual(rdi.FundingCalendar(fund_df(rows), ["A"]).status("A", D0 + rdi.DAY_MS),
                         "FUNDING_SCHEDULE_UNKNOWN")
        rows = eight_hourly("A", 5)
        rows[4] = (rows[4][0], rows[4][1], float("nan"), rows[4][3])
        self.assertEqual(rdi.FundingCalendar(fund_df(rows), ["A"]).status("A", D0 + rdi.DAY_MS),
                         "FUNDING_DATA_INVALID")

    def test_zero_rate_is_observed(self):
        fc = rdi.FundingCalendar(fund_df(eight_hourly("A", 5, rate=0.0)), ["A"])
        self.assertEqual(fc.status("A", D0 + rdi.DAY_MS), "OK")
        self.assertEqual(fc.day_sum("A", D0 + rdi.DAY_MS), 0.0)


class PanelTest(unittest.TestCase):
    def test_duplicates_off_grid_invalid_values(self):
        df = pd.DataFrame([("A", D0, 1.0, 5.0), ("A", D0, 1.0, 5.0),          # exact duplicate: kept once
                           ("A", D0 + rdi.DAY_MS, 1.0, 5.0), ("A", D0 + rdi.DAY_MS, 2.0, 5.0),  # conflict
                           ("A", D0 + 2 * rdi.DAY_MS + 1, 1.0, 5.0),           # off grid
                           ("A", D0 + 3 * rdi.DAY_MS, -1.0, 5.0),               # bad price
                           ("A", D0 + 4 * rdi.DAY_MS, 1.0, -5.0)],              # negative volume
                          columns=["sym", "t", "open", "qv"])
        out, log = rdi.clean_panel(df, ("open", "qv"), positive_cols=("open",), nonneg_cols=("qv",))
        c = log.to_json()["counts"]
        self.assertEqual(c["PANEL_DUPLICATE_CONFLICT"], 1)
        self.assertEqual(c["PANEL_OFF_GRID_TIMESTAMP"], 1)
        self.assertEqual(c["PANEL_INVALID_VALUE"], 2)
        self.assertEqual(sorted(out["t"]), [D0, D0 + 3 * rdi.DAY_MS, D0 + 4 * rdi.DAY_MS])
        self.assertTrue(math.isnan(out.loc[out["t"] == D0 + 3 * rdi.DAY_MS, "open"].iloc[0]))
        self.assertTrue(math.isnan(out.loc[out["t"] == D0 + 4 * rdi.DAY_MS, "qv"].iloc[0]))


class SeriesStateTest(unittest.TestCase):
    def setUp(self):
        self.days = np.array([D0 + k * rdi.DAY_MS for k in range(12)])
        self.fc = rdi.FundingCalendar(fund_df(eight_hourly("A", 13, rate=0.0) + eight_hourly("B", 13, rate=0.0)),
                                      ["A", "B"])

    def run_hold(self, ret, drift=True):
        st = rdi.SeriesState(len(self.days), "t")
        daily = np.zeros(len(self.days))

        def add(d, pnl):
            daily[d] += pnl
        cur = {"A": 0.5, "B": -0.5}
        st.rebalance(0, {}, self.days)
        st.hold(cur, 0, 6, ret, {"A": 0, "B": 1}, self.days, self.fc, add, drift=drift)
        st.rebalance(6, cur, self.days)
        st.hold({"A": 0.5}, 6, 11, ret, {"A": 0, "B": 1}, self.days, self.fc, add, drift=drift)
        st.finish([daily])
        return st, daily

    def test_missing_return_spoils_the_rest_of_the_period_and_the_next_rebalance(self):
        ret = np.full((12, 2), 0.01)
        ret[3, 1] = np.nan
        st, daily = self.run_hold(ret)
        self.assertEqual(list(np.nonzero(st.invalid)[0]), [3, 4, 5, 6])
        self.assertTrue(np.isnan(daily[3:7]).all())
        self.assertTrue(np.isfinite(daily[:3]).all() and np.isfinite(daily[7:11]).all())
        c = st.log.to_json()["counts"]
        self.assertEqual(c["PRICE_RETURN_MISSING"], 1)
        self.assertEqual(c["POSITION_STATE_UNKNOWN"], 2)

    def test_fixed_weights_only_lose_the_day(self):
        ret = np.full((12, 2), 0.01)
        ret[3, 1] = np.nan
        st, daily = self.run_hold(ret, drift=False)
        self.assertEqual(list(np.nonzero(st.invalid)[0]), [3])

    def test_stats_exclude_and_count_unobserved_days(self):
        x = np.array([0.01, -0.02, np.nan, 0.03, np.nan])
        st = bu.stats(x, np.zeros(5))
        self.assertEqual((st["n_days"], st["n_days_unobserved"]), (3, 2))
        self.assertAlmostEqual(st["unobserved_share"], 0.4)
        self.assertEqual(rdi.withhold_flag("DEV_PROMISING", [st]), "INVALID_DATA")
        self.assertEqual(rdi.withhold_flag("DEV_PROMISING", [bu.stats(np.ones(5) * 0.01, np.zeros(5))]),
                         "DEV_PROMISING")

    def test_series_status_limits(self):
        self.assertEqual(rdi.series_status(100, 0, {"estimated_share": 0.0})["status"], "COMPLETE")
        self.assertEqual(rdi.series_status(100, 1, {"estimated_share": 0.0})["status"], "COMPLETE_WITH_EXCLUSIONS")
        self.assertEqual(rdi.series_status(100, 3, {})["status"], "INVALID_DATA")
        self.assertEqual(rdi.series_status(100, 0, {"estimated_share": 0.5})["status"], "UNDETERMINED")
        self.assertEqual(rdi.series_status(0, 0, {})["status"], "INSUFFICIENT_DATA")


if __name__ == "__main__":
    unittest.main()
