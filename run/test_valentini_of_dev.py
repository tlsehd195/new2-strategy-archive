"""Synthetic checks of run/valentini_of_dev.py and run/aggtrades_minute_build.py (ADR 0114). No market data."""

import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aggtrades_minute_build as amb  # noqa: E402
import valentini_of_dev as vo  # noqa: E402


def _book(n=200, ns=1, price=100.0):
    a = {f: np.full((n, ns), np.nan) for f in vo.FIELDS}
    for f in ("open", "high", "low", "close", "vwap"):
        a[f][:] = price
    for f in ("qv", "buy_qv", "big_buy_qv", "big_sell_qv", "sell_at_low_qv", "buy_at_high_qv"):
        a[f][:] = 0.0
    a["qv"][:] = 1000.0
    a["buy_qv"][:] = 500.0
    return a


class Profile(unittest.TestCase):
    def test_value_area_contains_poc(self):
        rng = np.random.default_rng(0)
        vw = 100 + rng.normal(0, 0.5, 1440)
        q = np.ones(1440)
        poc, val, vah = vo.profile(vw, q, 100.0)
        self.assertLess(val, poc)
        self.assertLess(poc, vah)
        self.assertAlmostEqual(poc, 100.0, delta=0.3)

    def test_too_few_minutes(self):
        self.assertIsNone(vo.profile(np.full(100, 100.0), np.ones(100), 100.0))


class Aggregate(unittest.TestCase):
    def test_minute_bars_and_flow(self):
        t = np.array([0, 10_000, 59_999, 60_000, 61_000], np.int64)
        p = np.array([10.0, 11.0, 9.0, 10.0, 10.5])
        q = np.array([1.0, 2.0, 1.0, 5.0, 1.0])
        ibm = np.array([False, False, True, True, False])  # buy, buy, sell, sell, buy
        b = amb._agg_day(p, q, t, ibm, big=50.0)
        self.assertEqual(list(b["t"]), [0, 60_000])
        self.assertEqual(list(b["open"]), [10.0, 10.0])
        self.assertEqual(list(b["high"]), [11.0, 10.5])
        self.assertEqual(list(b["low"]), [9.0, 10.0])
        self.assertEqual(list(b["close"]), [9.0, 10.5])
        self.assertEqual(list(b["buy_qv"]), [32.0, 10.5])
        self.assertEqual(list(b["big_sell_qv"]), [0.0, 50.0])
        self.assertEqual(list(b["sell_at_low_qv"]), [9.0, 50.0])

    def test_plan_never_touches_closed_days(self):
        for _seg, ym, parts in amb.plan():
            for kind, stamp in parts:
                if kind == "daily":
                    self.assertTrue(stamp <= "2023-09-18" or "2024-06-19" <= stamp <= "2025-10-20", stamp)
                else:
                    self.assertNotIn(stamp, ("2023-09", "2024-06", "2025-10"))


class TradePath(unittest.TestCase):
    def _D(self, a):
        n = a["open"].shape[0]
        return {"T0": 0, "nm": n, "a": a, "fund": [(np.array([], np.int64), np.array([0.0]))], "n_days": 1}

    def _X(self, a):
        n, ns = a["open"].shape
        return {"delta": np.zeros((n, ns)), "bignet": np.zeros((n, ns)),
                "m60": {f: np.ones((n, ns)) for f in ("qv", "sell_at_low_qv", "buy_at_high_qv")},
                "dvwap": a["close"].copy(), "low15": np.zeros((n // 15 + 1, ns)), "high15": np.zeros((n // 15 + 1, ns)),
                "q15": np.full((n, ns), 1e7), "q15m": np.full((n, ns), 1e7)}

    def _fns(self):
        return {"one": lambda row, b, q, entry: 0.0 if row == "cost0" else 0.001, "maxp": 0.05}

    def test_long_stop(self):
        a = _book()
        a["low"][12, 0] = 98.0
        c = {"k": 0, "side": 1.0, "e": 10, "close_min": 100, "stop_v": 99.0, "final_v": 110.0,
             "min_final_R": 2.0, "model": "TREND"}
        t = vo.run_trade(self._D(a), self._X(a), c, self._fns())
        self.assertAlmostEqual(t["gross"], -0.01)
        self.assertEqual(t["pieces"][-1][3], "stop")

    def test_short_partial_then_breakeven(self):
        a = _book()
        a["low"][11, 0] = 98.9   # +1R for a short entered at 100 with stop 101
        a["high"][13, 0] = 100.5  # back to breakeven
        c = {"k": 0, "side": -1.0, "e": 10, "close_min": 100, "stop_v": -101.0, "final_v": -90.0,
             "min_final_R": 2.0, "model": "TREND"}
        t = vo.run_trade(self._D(a), self._X(a), c, self._fns())
        self.assertAlmostEqual(t["gross"], 0.5 * 0.01, places=9)
        self.assertEqual([p[3] for p in t["pieces"]], ["partial_1R", "breakeven_or_trail"])
        self.assertAlmostEqual(t["cost"]["measured"], 0.001 + 0.001)

    def test_mr_needs_one_R_to_poc(self):
        a = _book()
        c = {"k": 0, "side": 1.0, "e": 10, "close_min": 100, "stop_v": 99.0, "final_v": 100.5,
             "min_final_R": 1.0, "model": "MR"}
        self.assertIsNone(vo.run_trade(self._D(a), self._X(a), c, self._fns()))


if __name__ == "__main__":
    unittest.main()
