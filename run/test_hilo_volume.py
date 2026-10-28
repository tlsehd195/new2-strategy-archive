"""Tests for the high/low line + volume-spike DEV check (ADR 0043). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import hilo_volume_dev as x  # noqa: E402


def piv_from(high, low, close, qv, names):
    idx = np.arange(close.shape[0])
    f = lambda a: pd.DataFrame(a, index=idx, columns=names)  # noqa: E731
    return {"open": f(close), "high": f(high), "low": f(low), "close": f(close), "qv": f(qv)}


class HiloVolumeTest(unittest.TestCase):
    def test_prereg_is_the_committed_one(self):
        p = json.loads(x.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        cfgs = x.make_configs()
        self.assertEqual(len(cfgs), 17)
        self.assertEqual(p["grid"]["reversal"]["configs"] + p["grid"]["breakout"]["configs"], 17)
        b = [c for c in cfgs if c["mode"] == "B"][0]
        self.assertEqual({k: b[k] for k in ("N", "a", "m")} | {"H_days": b["H"]}, {k: v for k, v in p["grid"]["breakout"]["setting"].items()})
        self.assertIn("R_N20_a0.10_m2.0_H7", {c["id"] for c in cfgs})

    def test_never_read_ranges_refused(self):
        with self.assertRaises(SystemExit):
            bu.assert_allowed(bu.ms("2023-09-01"), bu.ms("2023-09-19"))

    def test_signal_hand_checked_event_bar_not_in_own_lines(self):
        n = 40
        high = np.full((n, 2), 11.0)
        low = np.full((n, 2), 9.0)
        close = np.full((n, 2), 10.0)
        qv = np.full((n, 2), 100.0)
        # A: day 30 closes at 10.9 (near the 9..11 high line) on 3x volume; B: same volume, mid-range close
        close[30, 0] = 10.9
        high[30, 0] = 12.0  # own high above the line must not move the line it is compared to
        qv[30, :] = 300.0
        nh, nl, v = x.signal_arrays(piv_from(high, low, close, qv, ["A", "B"]), 20, 0.10, 2.0)
        self.assertTrue(nh[30, 0] and not nh[30, 1])
        self.assertFalse(nl[30].any())
        self.assertAlmostEqual(v[30, 0], 3.0)
        # no spike -> no signal
        qv2 = qv.copy(); qv2[30, :] = 150.0
        nh2, _, _ = x.signal_arrays(piv_from(high, low, close, qv2, ["A", "B"]), 20, 0.10, 2.0)
        self.assertFalse(nh2.any())

    def test_lines_need_n_bars(self):
        n = 25
        a = np.full((n, 1), 10.0)
        nh, nl, _ = x.signal_arrays(piv_from(a + 1, a - 1, a, a, ["A"]), 50, 0.1, 2.0)
        self.assertFalse(nh.any() or nl.any())

    def test_trade_pnl_and_cost_long_short(self):
        n, h = 12, 3
        mask = np.ones((n, 1), bool)
        near_hi = np.zeros((n, 1), bool)
        near_lo = np.zeros((n, 1), bool)
        near_hi[4, 0] = True  # reversal: short opened at day 5
        v = np.full((n, 1), 2.0)
        ret = np.zeros((n, 1)); ret[5:8, 0] = -0.01  # price falls 1%/day while held: short wins
        flags = {"missing_return_days": 0}
        ctx = {"start": 2, "syms": ["A"], "ret": ret, "fnd": np.zeros((n, 1)), "flags": flags,
               "impact": lambda k, idx: (1e6, 1e6), "side_cost": lambda b, row, q: 0.001}
        cfg = {"id": "t", "N": 20, "a": 0.1, "m": 2.0, "H": h, "mode": "R"}
        r = x.simulate(cfg, (near_hi, near_lo, v), mask, ctx)
        self.assertEqual(len(r["trades"]), 1)
        self.assertAlmostEqual(r["gross"][5:8].sum(), x.WEIGHT * 0.03)
        self.assertAlmostEqual(r["cost"]["measured"][5], -x.WEIGHT * 0.001)
        self.assertAlmostEqual(r["cost"]["measured"][8], -x.WEIGHT * 0.001)
        # breakout mirror takes the other side
        cfg["mode"] = "B"
        rb = x.simulate(cfg, (near_hi, near_lo, v), mask, ctx)
        self.assertAlmostEqual(rb["gross"][5:8].sum(), -x.WEIGHT * 0.03)

    def test_one_position_per_symbol_and_capacity(self):
        n, h = 14, 3
        mask = np.ones((n, 2), bool)
        near_hi = np.zeros((n, 2), bool)
        near_hi[4, 0] = near_hi[5, 0] = near_hi[4, 1] = True  # A signals again while held
        v = np.full((n, 2), 2.0)
        ctx = {"start": 2, "syms": ["A", "B"], "ret": np.zeros((n, 2)), "fnd": np.zeros((n, 2)),
               "flags": {"missing_return_days": 0}, "impact": lambda k, i: (1e6, 1e6), "side_cost": lambda b, r, q: 0.0}
        cfg = {"id": "t", "N": 20, "a": 0.1, "m": 2.0, "H": h, "mode": "R"}
        r = x.simulate(cfg, (near_hi, np.zeros((n, 2), bool), v), mask, ctx)
        self.assertEqual([(t["sym"], t["e"]) for t in r["trades"]], [("A", 5), ("B", 5)])

    def test_universe_not_empty_before_first_monday_is_masked(self):
        n = 120
        idx = np.arange(bu.ms("2020-12-01"), bu.ms("2020-12-01") + n * bu.DAY_MS, bu.DAY_MS)
        z = pd.DataFrame(np.full((n, 3), 10.0), index=idx, columns=list("ABC"))
        mask, mondays = x.universe_mask({"open": z, "close": z, "qv": z * 10}, "2021-03-01")
        self.assertEqual(int(idx[mondays[0]]), bu.ms("2021-03-01"))
        self.assertFalse(mask[:mondays[0]].any())
        self.assertTrue(mask[mondays[0]].all())


if __name__ == "__main__":
    unittest.main()
