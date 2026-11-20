"""Tests for the 4h-bar retest DEV check (ADR 0095). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import h4_retest_dev as x  # noqa: E402

NS = len(x.SYMBOLS)


def bars15(nb, price=100.0):
    arr = {k: np.full((nb, NS), price) for k in ("open", "high", "low", "close")}
    arr["qv"] = np.full((nb, NS), 1_000_000.0)
    arr["tbq"] = np.full((nb, NS), 500_000.0)
    return arr


class H4RetestTest(unittest.TestCase):
    def test_grid_matches_prereg(self):
        p = json.loads(x.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        cfgs = x.make_configs()
        self.assertEqual(len(cfgs), x.N_TRIALS)
        self.assertEqual(sum(f["configs"] for f in p["families"].values()), x.N_TRIALS)
        self.assertEqual(len({c["id"] for c in cfgs}), x.N_TRIALS)
        self.assertEqual(p["universe"]["symbols"], x.SYMBOLS)

    def test_resample_ohlcv_and_missing(self):
        nb = 32
        a = bars15(nb)
        a["open"][:, 0] = np.arange(nb) + 1.0
        a["close"][:, 0] = np.arange(nb) + 1.5
        a["high"][:, 0] = np.arange(nb) + 2.0
        a["low"][:, 0] = np.arange(nb) + 0.5
        a["open"][20, 1] = np.nan
        b = x.to_h4(a)
        self.assertEqual(b["open"].shape, (2, NS))
        self.assertEqual(b["open"][0, 0], 1.0)
        self.assertEqual(b["close"][0, 0], 16.5)
        self.assertEqual(b["high"][0, 0], 17.0)
        self.assertEqual(b["low"][0, 0], 0.5)
        self.assertEqual(b["qv"][0, 0], 16_000_000.0)
        self.assertTrue(np.isnan(b["close"][1, 1]))       # one 15m bar missing -> 4h bar missing
        self.assertFalse(np.isnan(b["close"][0, 1]))

    def test_breakout_decides_on_4h_close_without_lookahead(self):
        m = 30
        b = {k: np.full((m, NS), 100.0) for k in ("open", "high", "low", "close")}
        b["qv"] = np.full((m, NS), 1.0)
        b["high"][:] = 101.0
        b["low"][:] = 99.0
        b["close"][25, 0] = 105.0
        cfg = {"id": "T1_N20_H6", "fam": "T1", "N": 20, "H": 6}
        ev = x.h4_events(cfg, b)
        self.assertEqual(ev, [(25 * 16 + 15, 0, 1.0, ev[0][3], 96)])
        b2 = {k: v.copy() for k, v in b.items()}
        b2["close"][27:, :] = 50.0                           # future bars change nothing before them
        ev2 = [e for e in x.h4_events(cfg, b2) if e[0] < 27 * 16]
        self.assertEqual(ev2, ev)

    def test_fade_direction_and_hilo_volume(self):
        m = 200
        rng = np.random.default_rng(0)
        r = rng.normal(0, 0.01, (m, NS))
        b = {"open": np.full((m, NS), 100.0)}
        b["close"] = 100.0 * np.exp(r)
        b["close"][190, 2] = 100.0 * np.exp(0.2)              # +20% 4h bar
        b["high"], b["low"], b["qv"] = np.maximum(b["open"], b["close"]), np.minimum(b["open"], b["close"]), np.ones((m, NS))
        ev = [e for e in x.h4_events({"fam": "T3", "k": 3.0, "H": 1}, b) if e[1] == 2 and e[0] == 190 * 16 + 15]
        self.assertEqual(ev[0][2], -1.0)
        self.assertEqual(ev[0][4], 16)
        b["qv"][190, 2] = 5.0                                 # volume spike at a new high -> fade short
        ev4 = [e for e in x.h4_events({"fam": "T4", "N": 42, "H": 6}, b) if e[1] == 2 and e[0] == 190 * 16 + 15]
        self.assertEqual(ev4[0][2], -1.0)

    def test_ema_cross_and_cross_section(self):
        m = 200
        c = np.full((m, NS), 100.0)
        c[120:, 0] = 130.0
        c[:120, 1] = 130.0
        b = {"open": c.copy(), "close": c, "high": c, "low": c, "qv": np.ones((m, NS))}
        ev = x.h4_events({"fam": "T2", "fast": 10, "slow": 40, "H": 6}, b)
        e0 = [e for e in ev if e[1] == 0]
        self.assertEqual(e0[0][:3], (120 * 16 + 15, 0, 1.0))
        e1 = [e for e in ev if e[1] == 1]
        self.assertEqual(e1[0][:3], (120 * 16 + 15, 1, -1.0))
        cs = x.h4_events({"fam": "T5", "L": 6, "dir": "reversal", "H": 6}, b)
        self.assertTrue(all(e[0] % 96 == 95 for e in cs))      # once a day at the 23:45 bar close
        day = [e for e in cs if e[0] == 20 * 96 + 95]
        self.assertEqual(sum(e[2] > 0 for e in day), 3)
        self.assertEqual(sum(e[2] < 0 for e in day), 3)

    def test_limit_chase_is_070_of_measured(self):
        costs = {"measured": bu.load_cost_record(x.fs.COST_RECORD), "assumed": bu.load_cost(),
                 "volume_scaled": bu.load_cost_calibration(x.fs.CALIBRATION)}
        f = x.make_cost_fns(costs)["one"]
        for q in (2e5, 5e6):
            self.assertAlmostEqual(f("limit_chase", q, q, True), 0.70 * f("measured", q, q, True), places=15)
            self.assertGreater(f("measured", q, q, True), f("limit_chase", q, q, False))
        self.assertEqual(f("cost0", 1e6, 1e6, True), 0.0)

    def test_moments_constant_series(self):
        self.assertEqual(x.moments(np.zeros(10)), (0.0, 0.0, 3.0))


if __name__ == "__main__":
    unittest.main()
