"""Tests for the daily scale-in (DCA) engine (ADR 0088). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dca_extreme_dev as x  # noqa: E402

ND = 130


def frames(prices: dict, names=("AAAUSDT",)):
    base = np.full((ND, len(names)), 100.0)
    out = {}
    for key in ("open", "high", "low", "close"):
        a = base.copy()
        for (d, k), v in prices.get(key, {}).items():
            a[d, k] = v
        out[key] = pd.DataFrame(a, columns=list(names))
    return out


def ctx_for(fr, lref=100.0, href=1000.0, cost=0.0, fund=0.0):
    n = fr["open"].shape[1]
    lines = {nn: (np.full((ND, n), lref), np.full((ND, n), href)) for nn in (30, 90)}
    return {**fr, "close_raw": fr["close"], "fnd": np.full((ND, n), fund), "lines": lines,
            "impact": lambda k, i: (1e9, 1e9), "side_cost": lambda b, r, q: cost}


def total(res, sd, row="cost0"):
    return float(res["gross"][sd].sum() + res["cost"][row][sd].sum())


class DcaEngineTest(unittest.TestCase):
    def test_prereg_grid_is_24_and_committed(self):
        p = json.loads(x.PREREG.read_text())
        self.assertEqual(p["status"], "DRAFT_FOR_DEV_ONLY")
        self.assertEqual(len(x.make_configs()), p["grid"]["n_configs"])
        self.assertEqual(len({c["id"] for c in x.make_configs()}), 24)

    def test_average_price_pnl_with_one_scale_in(self):
        # K=2, s=0.04, long. entry signal d95 (close 102 <= 103), fills d96 open 100.
        # d97 close 95 <= 96 -> add 1 level, filled d98 open 95. avg = 97.4359, target 1.10*avg=107.18.
        # d99 close 110 -> exit, filled d100 open 110.
        pr = {"close": {(95, 0): 102.0, (97, 0): 95.0, (98, 0): 95.0, (99, 0): 110.0},
              "open": {(98, 0): 95.0, (99, 0): 110.0, (100, 0): 110.0}}
        for key in ("high", "low"):
            pr[key] = {}
        fr = frames(pr)
        for d, v in ((97, 95.0), (98, 95.0)):
            fr["low"].iat[d, 0] = min(95.0, fr["low"].iat[d, 0])
        fr["low"].iat[97, 0] = 95.0
        fr["high"].iloc[:, 0] = np.maximum.reduce([fr["open"].iloc[:, 0], fr["close"].iloc[:, 0], fr["high"].iloc[:, 0]])
        fr["low"].iloc[:, 0] = np.minimum.reduce([fr["open"].iloc[:, 0], fr["close"].iloc[:, 0], fr["low"].iloc[:, 0]])
        cfg = {"id": "t", "N": 30, "s": 0.04, "K": 2, "exit": "stop_target"}
        res = x.simulate(cfg, ctx_for(fr))
        n = 0.05 / 3
        expected = n * 10 / 100 + n * 15 / 95          # tranche1 100->110, tranche2 95->110
        self.assertAlmostEqual(total(res, 1.0), expected, places=10)
        self.assertEqual(len(res["cycles"]), 1)
        cy = res["cycles"][0]
        self.assertEqual((cy["ntr"], cy["exit"][1]), (2, "TARGET"))
        self.assertAlmostEqual(cy["pp"], expected, places=10)
        self.assertEqual(total(res, -1.0), 0.0)

    def test_cost_is_charged_per_tranche_fill_and_exit(self):
        pr = {"close": {(95, 0): 102.0, (97, 0): 95.0, (99, 0): 110.0}, "open": {(98, 0): 95.0, (100, 0): 110.0}}
        fr = frames(pr)
        for key, f in (("high", np.maximum), ("low", np.minimum)):
            fr[key].iloc[:, 0] = f.reduce([fr["open"].iloc[:, 0], fr["close"].iloc[:, 0], fr[key].iloc[:, 0]])
        cfg = {"id": "t", "N": 30, "s": 0.04, "K": 2, "exit": "stop_target"}
        res = x.simulate(cfg, ctx_for(fr, cost=0.001))
        n = 0.05 / 3
        units = n / 100 + n / 95
        want = n * 0.001 * 2 + units * 110 * 0.001      # two entries + one exit
        self.assertAlmostEqual(res["cycles"][0]["cost"], want, places=12)   # later flat-price cycle (cooldown over) is still open

    def test_liquidation_loses_exactly_the_margin(self):
        # K=0 target_time_only, entry d96 open 100 (n=0.05); d100 low 79 <= 100*(1-0.2+0.005)=80.5
        pr = {"close": {(95, 0): 102.0}, "low": {(100, 0): 79.0}, "close_x": {}}
        fr = frames(pr)
        fr["close"].iat[100, 0] = 90.0
        fr["open"].iat[100, 0] = 95.0
        fr["high"].iat[100, 0] = 100.0
        cfg = {"id": "t", "N": 30, "s": 0.04, "K": 0, "exit": "target_time_only"}
        res = x.simulate(cfg, ctx_for(fr))
        self.assertEqual(len(res["cycles"]), 1)
        self.assertEqual(res["cycles"][0]["exit"][1], "LIQUIDATED")
        self.assertAlmostEqual(total(res, 1.0), -0.05 / x.LEV, places=12)
        self.assertLessEqual(res["adv"][100], -0.05 / x.LEV + 1e-12)

    def test_short_is_exact_mirror_and_funding_sign(self):
        pr = {"close": {(95, 0): 98.0}}
        fr = frames(pr)
        for d in range(97, 100):
            fr["open"].iat[d, 0] = fr["close"].iat[d, 0] = 90.0   # short gains from d97
            fr["high"].iat[d, 0] = fr["low"].iat[d, 0] = 90.0
        cfg = {"id": "t", "N": 30, "s": 0.04, "K": 0, "exit": "stop_target"}
        res = x.simulate(cfg, ctx_for(fr, lref=0.0, href=100.0, fund=0.0))
        self.assertGreater(total(res, -1.0), 0.0)
        self.assertEqual(total(res, 1.0), 0.0)
        # positive funding rate is paid by longs, received by shorts
        res_f = x.simulate(cfg, ctx_for(fr, lref=0.0, href=100.0, fund=0.001))
        self.assertGreater(total(res_f, -1.0), total(res, -1.0))

    def test_no_trade_in_lookback_days_and_no_signal_far_from_line(self):
        fr = frames({})
        cfg = {"id": "t", "N": 30, "s": 0.04, "K": 2, "exit": "stop_target"}
        res = x.simulate(cfg, ctx_for(fr, lref=50.0, href=200.0))       # price 100 is mid-range
        self.assertEqual(len(res["cycles"]), 0)
        self.assertEqual(total(res, 1.0), 0.0)

    def test_tail_stats_envelope_includes_intraday_worst(self):
        net = np.array([0.01, 0.01, -0.02, 0.0])
        adv = np.array([0.0, 0.0, -0.05, 0.0])
        t = x.tail_stats(net, adv, np.ones(4))
        self.assertAlmostEqual(t["close_max_dd"], 0.02)
        self.assertAlmostEqual(t["envelope_max_dd"], 0.05)   # peak 0.02, intraday low 0.02-0.05=-0.03


if __name__ == "__main__":
    unittest.main()
