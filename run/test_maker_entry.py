"""Tests for the limit-order entry cost estimate (ADR 0049). Needs numpy."""

import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import maker_entry_estimate as m  # noqa: E402


def book(n=40, bid=100.0, ask=100.02, bq=50.0, aq=50.0):
    t = np.arange(n) * 30_000.0
    return {"t": t, "bb": np.full(n, bid), "bbq": np.full(n, bq), "ba": np.full(n, ask), "baq": np.full(n, aq),
            "vwb": np.full(n, ask), "vws": np.full(n, bid), "mid": np.full(n, (bid + ask) / 2)}


class FillRules(unittest.TestCase):
    def test_strict_needs_print_below(self):
        pp, qq = np.array([100.0, 100.0, 99.99]), np.array([1.0, 1.0, 1.0])
        sell = np.array([True, True, True])
        q, s, t = m.first_fills(pp, qq, sell, 100.0, 1000.0)
        self.assertEqual((q, s, t), (2, 2, 0))      # queue never drains at 100.0; the print at 99.99 fills (strict)

    def test_queue_drains_at_price(self):
        pp, qq = np.array([100.0, 100.0, 100.0]), np.array([30.0, 30.0, 30.0])
        q, s, t = m.first_fills(pp, qq, np.array([True] * 3), 100.0, 50.0)
        self.assertEqual((q, s, t), (1, -1, 0))     # 30 + 30 >= 50 at the second print

    def test_buy_aggressor_prints_do_not_fill_a_buy(self):
        pp, qq = np.array([99.0, 100.0]), np.array([5.0, 5.0])
        self.assertEqual(m.first_fills(pp, qq, np.array([False, False]), 100.0, 1.0), (-1, -1, -1))

    def test_bounds_order(self):
        rng = np.random.default_rng(1)
        pp = 100 + np.round(rng.integers(-2, 3, 500) * 0.01, 2)
        qq = rng.uniform(0.1, 5, 500)
        sell = rng.random(500) < 0.5
        q, s, t = m.first_fills(pp, qq, sell, 99.99, 20.0)
        if s >= 0:
            self.assertTrue(0 <= t <= q <= s)       # TOUCH earliest, STRICT latest, QUEUE in between


class MidAndVwap(unittest.TestCase):
    def test_vwap_walks_levels(self):
        self.assertAlmostEqual(m.vwap_side([("10", "1"), ("20", "1")], 20.0), 20.0 / 1.5)
        self.assertTrue(np.isnan(m.vwap_side([("10", "1")], 100.0)))

    def test_mid_interpolates_and_rejects_gaps(self):
        b = {"t": np.array([0.0, 30_000.0, 200_000.0]), "mid": np.array([100.0, 101.0, 105.0])}
        got = m.mid_at(b, np.array([15_000.0, 100_000.0, 250_000.0]))
        self.assertAlmostEqual(got[0], 100.5)
        self.assertTrue(np.isnan(got[1]) and np.isnan(got[2]))   # > 60 s between neighbours, beyond the last snapshot


class Simulation(unittest.TestCase):
    def tape_down_then_up(self, bk):
        # every snapshot: one sell print below the bid 1 s after it, so all buys fill; no buy-aggressor prints above the ask
        t = np.concatenate([bk["t"] + 1000.0])
        return {"t": np.sort(np.concatenate([t, [bk["t"][-1] + 700_000.0]])),
                "p": np.concatenate([np.full(len(t), 99.99), [100.0]]), "q": np.ones(len(t) + 1),
                "m": np.concatenate([np.ones(len(t), bool), [True]])}

    def test_flat_book_all_buys_fill_with_half_spread_gain(self):
        bk = book()
        sim = m.simulate_symbol(bk, self.tape_down_then_up(bk), 0.5)
        buys = sim["side"] == 1
        self.assertTrue(buys.any())
        self.assertTrue(np.all(np.isfinite(sim["fill_STRICT"][buys])))
        self.assertAlmostEqual(float(sim["slip"][buys][0]), -1.0, places=3)      # 0.01 / 100.01 in bp
        oc = m.order_outcomes(bk, sim, "STRICT", 120)
        self.assertTrue(np.all(oc["filled"][buys]))
        self.assertFalse(np.any(oc["filled"][~buys]))                            # no buy-aggressor print: sells never fill

    def test_chase_cost_of_unfilled_includes_vwap_offset(self):
        bk = book()
        sim = m.simulate_symbol(bk, self.tape_down_then_up(bk), 0.5)
        oc = m.order_outcomes(bk, sim, "STRICT", 120)
        sells = sim["side"] == -1
        ch = oc["chase"][sells & oc["chase_ok"]]
        ch = ch[np.isfinite(ch)]                                                 # the last snapshots have no later mid
        self.assertGreater(len(ch), 10)
        self.assertAlmostEqual(float(np.mean(ch)), 1.0, places=3)                # flat price: only the half spread


class Ratios(unittest.TestCase):
    def test_worst_symbol_picks_max(self):
        per = {"A": {"QUEUE|120s": {"pooled|fee2": {"C_side_chase_bp": 3.0}}}, "B": {"QUEUE|120s": {"pooled|fee2": {"C_side_chase_bp": 5.0}}}}
        w = m.worst_symbol(per, "QUEUE", 120, "pooled", 2.0, "C_side_chase_bp")
        self.assertEqual((w["symbol"], w["value_bp"]), ("B", 5.0))


if __name__ == "__main__":
    unittest.main()
