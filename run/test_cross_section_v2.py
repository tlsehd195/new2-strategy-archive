"""Tests for the cross-section v2 DEV check (ADR 0040). Needs numpy and pandas."""

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import cross_section_v2_dev as x  # noqa: E402


def piv_from(close: np.ndarray, qv: np.ndarray, tbq: np.ndarray, names: list) -> dict:
    idx = np.arange(close.shape[0])
    f = lambda a: pd.DataFrame(a, index=idx, columns=names)  # noqa: E731
    return {"open": f(close), "close": f(close), "qv": f(qv), "tbq": f(tbq)}


class CrossSectionV2Test(unittest.TestCase):
    def test_prereg_is_the_committed_one(self):
        p = json.loads(x.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual([h["id"] for h in p["hypotheses"]], list(x.LONG_TOP))
        sides = {h["id"]: h["long"].startswith("highest") for h in p["hypotheses"]}
        self.assertEqual(sides, x.LONG_TOP)

    def test_never_read_ranges_refused(self):
        for lo, hi in (("2020-12-01", "2021-01-31"), ("2023-09-01", "2023-09-19"), ("2025-10-01", "2025-10-21")):
            with self.assertRaises(SystemExit):
                bu.assert_allowed(bu.ms(lo), bu.ms(hi))

    def test_rebalance_every_28_days_from_first_monday(self):
        lo, hi, first = x.SEGMENTS["S1"]
        days = np.arange(bu.ms(lo), bu.ms(hi) + 1, bu.DAY_MS)
        reb = x.rebalance_days(days, first)
        self.assertEqual(int(days[reb[0]]), bu.ms("2021-03-01"))  # a Monday
        self.assertTrue(all(b - a == x.HOLD_DAYS for a, b in zip(reb, reb[1:])))

    def test_signals_hand_checked(self):
        n, names = 70, ["A", "B", "C"]
        close = np.ones((n, 3))
        close[50, 0] = 1.5  # A: one +50% day in the last 28 returns, back to 1 next day
        qv = np.full((n, 3), 100.0)
        qv[-7:, 1] = 400.0  # B: volume x4 in the last 7 days
        tbq = qv * np.array([0.5, 0.5, 0.6])
        s = x.signals(piv_from(close, qv, tbq, names), n - 1, names)
        self.assertAlmostEqual(s["N3_MAX"]["A"], 0.5)
        self.assertAlmostEqual(s["N3_MAX"]["B"], 0.0)
        self.assertAlmostEqual(s["N2_VOLSHOCK"]["B"], math.log(4.0))
        self.assertAlmostEqual(s["N2_VOLSHOCK"]["A"], 0.0)
        self.assertAlmostEqual(s["N1_SIZE"]["B"], (23 * 100 + 7 * 400) / 30)
        self.assertAlmostEqual(s["N4_TAKERFLOW"]["C"], 0.6)
        # composite: high = long side of each; A worst on N3 (lottery), C best on N4
        self.assertTrue(s["N5_COMPOSITE"]["C"] > s["N5_COMPOSITE"]["A"])

    def test_signal_needs_full_window(self):
        n, names = 70, ["A", "B"]
        close = np.ones((n, 2))
        close[-5, 1] = np.nan
        qv = np.full((n, 2), 100.0)
        s = x.signals(piv_from(close, qv, qv * 0.5, names), n - 1, names)
        self.assertTrue(math.isnan(s["N3_MAX"]["B"]))
        self.assertTrue(math.isnan(s["N5_COMPOSITE"]["B"]))
        self.assertFalse(math.isnan(s["N3_MAX"]["A"]))

    def test_cost_rows(self):
        m = bu.load_cost_record(x.COST_RECORD)
        a = bu.load_cost()
        b = 1e6
        base = m["fixed"] + m["imp"] * math.sqrt(bu.ORDER_VALUE / b)
        self.assertAlmostEqual(x.side_cost(b, "measured", m, a), base)
        self.assertAlmostEqual(x.side_cost(b, "measured_x2", m, a), 2 * base)
        self.assertEqual(x.side_cost(b, "cost0", m, a), 0.0)
        self.assertAlmostEqual(x.side_cost(b, "assumed_public_v1", m, a),
                               0.0010 + a["imp"] * math.sqrt(bu.ORDER_VALUE / b))

    def test_end_to_end_flat_prices_only_cost(self):
        """Constant prices and no funding: cost0 earns exactly 0, measured loses only on rebalance days,
        and the first rebalance trades gross 1 (from flat to +-1/20 x 20)."""
        lo, hi, _ = x.SEGMENTS["S1"]
        days = np.arange(bu.ms(lo), bu.ms(hi) + 1, bu.DAY_MS)
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for sub in ("daily", "funding", "bar2345"):
                (d / sub).mkdir()
            for k in range(25):
                sym = f"S{k:02d}USDT"
                qv = 1e6 * (k + 1)
                rows = [f"{t},1,1,1,1,1,{t + bu.DAY_MS - 1},{qv},10,0.5,{qv * (0.4 + k / 100)},0" for t in days]
                (d / "daily" / f"{sym}-1d-all.csv").write_text("\n".join(rows) + "\n")
            m, a = bu.load_cost_record(x.COST_RECORD), bu.load_cost()
            r = x.run_segment(d, "S1", m, a)
        for h in x.LONG_TOP:
            self.assertTrue(np.all(r["daily"][h]["cost0"] == 0.0))
            meas = r["daily"][h]["measured"]
            neg = np.nonzero(meas < 0)[0]
            self.assertTrue(len(neg) >= 1)
            self.assertTrue(np.all(np.diff(neg) % x.HOLD_DAYS == 0))
        self.assertEqual(r["flags"]["missing_return_days"], 0)
        # N1 (size) never changes ranking with constant volumes: only the first rebalance trades
        self.assertEqual(int((r["daily"]["N1_SIZE"]["measured"] < 0).sum()), 1)


if __name__ == "__main__":
    unittest.main()
