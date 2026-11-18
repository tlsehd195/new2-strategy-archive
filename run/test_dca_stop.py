"""Tests for the stop-rule extension of the daily scale-in engine (ADR 0089). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dca_extreme_dev as base  # noqa: E402
import dca_stop_dev as x  # noqa: E402
from test_dca_extreme import ctx_for, frames, total  # noqa: E402

N = 0.05      # K=0 single tranche notional, entry at d91 open 100 (signal d90 close 100 <= 103)


def fr_with(day, open_=None, low=None, high=None, close=None):
    fr = frames({})
    for key, v in (("open", open_), ("low", low), ("high", high), ("close", close)):
        if v is not None:
            fr[key].iat[day, 0] = v
    return fr


def cfg(stop, xv=None, K=0, slip=0.0, s=0.04):
    return {"id": "t", "N": 30, "s": s, "K": K, "stop": stop, "x": xv, "slip": slip, "counted": True}


class StopTest(unittest.TestCase):
    def test_grid_matches_prereg(self):
        p = json.loads(x.PREREG.read_text())
        self.assertEqual(p["status"], "DRAFT_FOR_DEV_ONLY")
        cs = x.make_configs()
        self.assertEqual((len(cs), p["grid"]["n_configs"], p["grid"]["n_trials_counted"]), (52, 52, 52))
        ids = [c["id"] for c in cs + x.make_refs() + x.make_slip(cs)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(x.make_refs()), 10)
        self.assertEqual(len(x.make_slip(cs)), 26)

    def test_none_and_struct_reproduce_adr0088_engine(self):
        fr = frames({"close": {(95, 0): 95.0, (97, 0): 88.0, (99, 0): 112.0}})
        for key, f in (("high", np.maximum), ("low", np.minimum)):
            fr[key].iloc[:, 0] = f.reduce([fr["open"].iloc[:, 0], fr["close"].iloc[:, 0], fr[key].iloc[:, 0]])
        for stop, exit_mode in (("none", "target_time_only"), ("struct", "stop_target")):
            a = x.simulate_stop(cfg(stop, K=2), ctx_for(fr, cost=0.001))
            b = base.simulate({"id": "t", "N": 30, "s": 0.04, "K": 2, "exit": exit_mode}, ctx_for(fr, cost=0.001))
            for sd in x.SIDES:
                np.testing.assert_allclose(a["gross"][sd], b["gross"][sd], atol=1e-15)
            np.testing.assert_allclose(a["adv"], b["adv"], atol=1e-15)
            self.assertEqual([c["exit"] for c in a["cycles"]], [c["exit"] for c in b["cycles"]])

    def test_intraday_avg_stop_fills_at_stop_price(self):
        fr = fr_with(97, low=85.0, close=90.0)
        res = x.simulate_stop(cfg("avg_intra", 0.10), ctx_for(fr))
        cy = res["cycles"][0]
        self.assertEqual((cy["exit"][0], cy["exit"][1]), (97, "STOP"))
        self.assertAlmostEqual(cy["pp"], -0.10 * N, places=12)          # filled at 90 = 100*(1-0.10)
        self.assertAlmostEqual(total(res, 1.0), -0.10 * N, places=12)
        self.assertAlmostEqual(res["adv"][97], -0.10 * N, places=12)

    def test_gap_through_stop_fills_at_open_and_gap_beyond_liquidation_loses_margin(self):
        fr = fr_with(97, open_=85.0, low=82.0, close=84.0)               # stop 90, liq 80.5: gap fill at 85
        res = x.simulate_stop(cfg("avg_intra", 0.10), ctx_for(fr))
        self.assertAlmostEqual(res["cycles"][0]["pp"], -0.15 * N, places=12)
        fr = fr_with(97, open_=79.0, low=78.0, close=79.0)               # open below liq 80.5
        res = x.simulate_stop(cfg("avg_intra", 0.10), ctx_for(fr))
        self.assertEqual(res["cycles"][0]["exit"][1], "LIQUIDATED")
        self.assertAlmostEqual(res["cycles"][0]["pp"], -N / base.LEV, places=12)

    def test_liquidation_precedes_a_stop_farther_than_liquidation(self):
        fr = fr_with(97, open_=100.0, low=70.0, close=75.0)
        res = x.simulate_stop(cfg("avg_intra", 0.30), ctx_for(fr))       # stop 70 < liq 80.5
        self.assertEqual(res["cycles"][0]["exit"][1], "LIQUIDATED")
        res = x.simulate_stop(cfg("avg_intra", 0.15), ctx_for(fr))       # stop 85 > liq 80.5
        self.assertEqual(res["cycles"][0]["exit"][1], "STOP")
        self.assertAlmostEqual(res["cycles"][0]["pp"], -0.15 * N, places=12)

    def test_close_stop_fills_next_open(self):
        fr = fr_with(97, low=88.0, close=88.0)
        fr["open"].iat[98, 0] = 85.0
        fr["low"].iat[98, 0] = 85.0
        fr["close"].iat[98, 0] = 85.0
        res = x.simulate_stop(cfg("avg_close", 0.10), ctx_for(fr))
        cy = res["cycles"][0]
        self.assertEqual((cy["exit"][0], cy["exit"][1]), (98, "STOP"))
        self.assertAlmostEqual(cy["pp"], -0.15 * N, places=12)          # exit at next open 85

    def test_last_stop_uses_last_tranche_and_avg_stop_uses_average(self):
        # K=2 s=0.04: d91 fill 100; d92 close 95 <= 96 -> add filled d93 open 95. last=95 avg=97.4359.
        fr = frames({"close": {(92, 0): 95.0}, "open": {(93, 0): 95.0}})
        for key, f in (("high", np.maximum), ("low", np.minimum)):
            fr[key].iloc[:, 0] = f.reduce([fr["open"].iloc[:, 0], fr["close"].iloc[:, 0], fr[key].iloc[:, 0]])
        fr["low"].iat[95, 0] = 85.0
        fr["close"].iat[95, 0] = 90.0
        fr["open"].iat[96, 0] = 95.0
        n = 0.05 / 3
        res = x.simulate_stop(cfg("last_intra", 0.10, K=2), ctx_for(fr))     # stop 95*0.9 = 85.5; low 85 hits
        cy = res["cycles"][0]
        self.assertEqual((cy["exit"][0], cy["exit"][1]), (95, "STOP"))
        want = n / 100 * (85.5 - 100) + n / 95 * (85.5 - 95)
        self.assertAlmostEqual(cy["pp"], want, places=12)
        res = x.simulate_stop(cfg("avg_intra", 0.10, K=2), ctx_for(fr))      # stop 97.4359*0.9 = 87.69 -> hit earlier level
        avg = (n + n) / (n / 100 + n / 95)
        self.assertAlmostEqual(res["cycles"][0]["pp"], n / 100 * (avg * 0.9 - 100) + n / 95 * (avg * 0.9 - 95), places=12)

    def test_slippage_worsens_stop_fill(self):
        fr = fr_with(97, low=85.0, close=90.0)
        res = x.simulate_stop(cfg("avg_intra", 0.10, slip=0.005), ctx_for(fr))
        self.assertAlmostEqual(res["cycles"][0]["pp"], N / 100 * (90.0 * 0.995 - 100), places=12)

    def test_short_mirror(self):
        fr = fr_with(97, high=115.0, close=110.0)
        res = x.simulate_stop(cfg("avg_intra", 0.10), ctx_for(fr, lref=0.0, href=100.0))
        cy = res["cycles"][0]
        self.assertEqual((cy["side"], cy["exit"][1]), (-1.0, "STOP"))
        self.assertAlmostEqual(cy["pp"], -0.10 * N, places=12)          # short filled at 110

    def test_cost_charged_on_stop_exit(self):
        fr = fr_with(97, low=85.0, close=90.0)
        res = x.simulate_stop(cfg("avg_intra", 0.10), ctx_for(fr, cost=0.001))
        want = N * 0.001 + (N / 100) * 90.0 * 0.001
        self.assertAlmostEqual(res["cycles"][0]["cost"], want, places=12)


if __name__ == "__main__":
    unittest.main()
