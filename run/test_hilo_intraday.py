"""Tests for the intraday high/low line + volume-spike DEV check (ADR 0044). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import hilo_intraday_dev as x  # noqa: E402


def bars_from(high, low, close, qv):
    f = lambda a: pd.DataFrame(np.asarray(a, float).reshape(len(a), -1))  # noqa: E731
    return {"open": f(close), "high": f(high), "low": f(low), "close": f(close), "qv": f(qv)}


class HiloIntradayTest(unittest.TestCase):
    def test_prereg_is_the_committed_one(self):
        p = json.loads(x.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        cfgs = x.make_configs()
        self.assertEqual(len(cfgs), 48)
        g = p["grid"]
        self.assertEqual(sum(g[k]["configs"] for k in ("FADE_ROLL", "FADE_SWING", "BREAKOUT_ROLL", "BREAKOUT_SWING")), 48)
        for fam, n in (("FR", 16), ("FS", 16), ("BR", 8), ("BS", 8)):
            self.assertEqual(sum(c["fam"] == fam for c in cfgs), n)
        self.assertEqual(len({c["id"] for c in cfgs}), 48)
        self.assertTrue(all(c["H"] in (16, 24) for c in cfgs if c["mode"] == "BO"))

    def test_never_read_ranges_refused(self):
        with self.assertRaises(SystemExit):
            bu.assert_allowed(bu.ms("2023-09-01"), bu.ms("2023-09-19"))

    def test_hourly_aggregation_needs_all_four_bars(self):
        n = 8
        m = {k: np.arange(1.0, n + 1).reshape(n, 1) for k in ("open", "high", "low", "close", "qv")}
        m["low"] = m["low"] - 0.5
        m["qv"][5, 0] = np.nan
        t = x.to_tf(m, 4)
        self.assertEqual(float(t["open"].iloc[0, 0]), 1.0)
        self.assertEqual(float(t["close"].iloc[0, 0]), 4.0)
        self.assertEqual(float(t["high"].iloc[0, 0]), 4.0)
        self.assertEqual(float(t["low"].iloc[0, 0]), 0.5)
        self.assertEqual(float(t["qv"].iloc[0, 0]), 10.0)
        self.assertTrue(np.isnan(t["qv"].iloc[1, 0]))

    def test_roll_signal_event_bar_not_in_own_lines(self):
        n = 40
        high, low = np.full(n, 11.0), np.full(n, 9.0)
        close, qv = np.full(n, 10.0), np.full(n, 100.0)
        close[30], high[30], qv[30] = 10.95, 12.0, 300.0
        b = bars_from(high, low, close, qv)
        v = x.volume_ratio(b, 20)
        lines = x.roll_lines(b, 20)
        ls, ss, _ = x.signal_arrays(b["close"], lines, v, 2.0, "F")
        self.assertTrue(ss[30, 0] and not ls[30, 0])  # near the high line -> fade is short
        self.assertAlmostEqual(v.iloc[30, 0], 3.0)
        lb, sb, _ = x.signal_arrays(b["close"], lines, v, 2.0, "BO")
        self.assertFalse(lb[30, 0])  # 10.95 is below the 11 line: not a breakout
        close[30] = 11.5
        b = bars_from(high, low, close, qv)
        lb, sb, _ = x.signal_arrays(b["close"], x.roll_lines(b, 20), x.volume_ratio(b, 20), 2.0, "BO")
        self.assertTrue(lb[30, 0] and not sb[30, 0])

    def test_swing_known_only_after_k_bars(self):
        n, k = 30, 3
        high, low = np.full(n, 10.0), np.full(n, 8.0)
        high[10] = 12.0  # swing high at 10, known from bar 13
        low[12] = 6.0    # swing low at 12, known from bar 15
        b = bars_from(high, low, np.full(n, 9.0), np.full(n, 1.0))
        h, lo = x.swing_lines(b, k, 20)
        self.assertTrue(np.isnan(h.iloc[12, 0]))
        self.assertEqual(h.iloc[13, 0], 12.0)
        self.assertTrue(np.isnan(lo.iloc[14, 0]))
        self.assertEqual(lo.iloc[15, 0], 6.0)
        # a swing older than cap bars from the swing bar is dropped
        n2 = 60
        high2, low2 = np.full(n2, 10.0), np.full(n2, 8.0)
        high2[10] = 12.0
        b2 = bars_from(high2, low2, np.full(n2, 9.0), np.full(n2, 1.0))
        h2, _ = x.swing_lines(b2, k, 20)
        self.assertEqual(h2.iloc[30, 0], 12.0)
        self.assertTrue(np.isnan(h2.iloc[31, 0]))

    def _ctx(self, n15, h_cost=0.001, ret=None, fnd=None):
        n = n15
        return {"start": 0, "syms": ["A"], "ret": np.zeros((n, 1)) if ret is None else ret,
                "fnd": np.zeros((n, 1)) if fnd is None else fnd, "flags": {"missing_return_intervals": 0},
                "impact": lambda k, i: (1e6, 1e6), "side_cost": lambda b, r, q: h_cost,
                "n15": n, "n_days": n // x.PER_DAY, "day": np.arange(n) // x.PER_DAY}

    def test_trade_pnl_cost_and_funding_15m(self):
        n = 2 * x.PER_DAY
        ret = np.zeros((n, 1)); ret[51:55, 0] = -0.01  # falls 1% per interval while held
        fnd = np.zeros((n, 1)); fnd[52, 0] = 0.0001    # funding in the held window, short receives
        short_s = np.zeros((n, 1), bool); short_s[50, 0] = True
        long_s = np.zeros((n, 1), bool)
        v = np.full((n, 1), 2.0)
        cfg = {"tf": "15m", "H": 4, "mode": "F"}
        r = x.simulate(cfg, (long_s, short_s, v), np.ones((2, 1), bool), self._ctx(n, ret=ret, fnd=fnd))
        self.assertEqual(len(r["trades"]), 1)
        self.assertAlmostEqual(r["gross"].sum(), x.WEIGHT * (0.04 + 0.0001))
        self.assertAlmostEqual(r["cost"]["measured"].sum(), -2 * x.WEIGHT * 0.001)
        self.assertEqual(r["trades"][0]["e"], 51)

    def test_hourly_event_enters_at_next_hour_and_holds_in_15m_units(self):
        n = 2 * x.PER_DAY
        ret = np.zeros((n, 1)); ret[24:32, 0] = 0.005  # hour bars 6,7 -> 15m idx 24..31
        long_s = np.zeros((n // 4, 1), bool); long_s[5, 0] = True  # event hour 5 closes at 15m idx 24
        r = x.simulate({"tf": "1h", "H": 2, "mode": "F"}, (long_s, np.zeros_like(long_s), np.full(long_s.shape, 2.0)),
                       np.ones((2, 1), bool), self._ctx(n, ret=ret))
        self.assertEqual(r["trades"][0]["e"], 24)
        self.assertAlmostEqual(r["gross"].sum(), x.WEIGHT * 0.04)

    def test_one_position_per_symbol_and_window_end(self):
        n = x.PER_DAY
        short_s = np.zeros((n, 1), bool); short_s[10, 0] = short_s[12, 0] = True; short_s[n - 3, 0] = True
        r = x.simulate({"tf": "15m", "H": 4, "mode": "F"}, (np.zeros((n, 1), bool), short_s, np.full((n, 1), 2.0)),
                       np.ones((1, 1), bool), self._ctx(n))
        self.assertEqual([t["e"] for t in r["trades"]], [11])  # 13 ignored (held), last one would exit past the window


if __name__ == "__main__":
    unittest.main()
