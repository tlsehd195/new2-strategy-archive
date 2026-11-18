"""Tests for the low-leverage limit-entry scale-in engine (ADR 0090). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dca_limit_dev as x  # noqa: E402
from test_dca_extreme import ctx_for, frames  # noqa: E402

N = 0.05


def cfg(K=0, lev=3.0, stop="none", u=0.0, fm=0.0005, s=0.04):
    return {"id": "t", "N": 30, "s": s, "K": K, "lev": lev, "stop": stop, "u": u, "fm": fm, "counted": True}


def fr_set(kw=None):
    fr = frames({})
    for (key, d), v in (kw or {}).items():
        fr[key].iat[d, 0] = v
    return fr


def tot(res, row="cost0", sd=1.0):
    return float(res["gross"][sd].sum() + res["cost"][row][sd].sum())


class LimitTest(unittest.TestCase):
    def test_grid_matches_prereg(self):
        p = json.loads(x.PREREG.read_text())["part1"]
        cs = x.make_configs()
        self.assertEqual((len(cs), p["grid"]["n_configs"], p["grid"]["n_trials_counted"]), (52, 52, 52))
        self.assertEqual(len({c["id"] for c in cs + x.make_robust(cs)}), len(cs) + len(x.make_robust(cs)))
        self.assertEqual(len(x.make_robust(cs)), 24)

    def test_touch_without_trade_through_does_not_fill(self):
        # signal d90 (close 100), limit 100 for d91; low 99.97 is only 3bp through -> no fill (needs 5bp)
        res = x.simulate_limit(cfg(), ctx_for(fr_set({("low", 91): 99.97})))
        self.assertEqual(res["cycles"], [])
        self.assertEqual(res["counts"]["first_fills"], 0)

    def test_fill_at_limit_price_even_if_open_is_better(self):
        fr = fr_set({("low", 91): 99.9, ("open", 91): 99.0, ("close", 91): 101.0, ("high", 91): 101.0})
        res = x.simulate_limit(cfg(), ctx_for(fr))
        self.assertEqual(res["counts"]["first_fills"], 1)
        # d91: filled at 100, close 101 -> mark +1% of the tranche; day-92 open re-reference
        self.assertAlmostEqual(res["gross"][1.0][91], N * 0.01, places=12)

    def test_unfilled_order_expires_after_one_day(self):
        fr = fr_set({("low", 92): 90.0})          # trades through on d92, but the order was for d91 only
        for d in range(91, 130):                     # keep signals away: price far above line after d90
            fr["close"].iat[d, 0] = 150.0
            fr["open"].iat[d, 0] = 150.0
            fr["high"].iat[d, 0] = 150.0
            fr["low"].iat[d, 0] = 150.0
        fr["low"].iat[92, 0] = 90.0
        res = x.simulate_limit(cfg(), ctx_for(fr))
        self.assertEqual(res["counts"]["first_fills"], 0)

    def test_add_levels_fill_at_level_and_same_day_after_entry(self):
        # K=2, s=.04: levels 96 and 92; d91 low 90 -> first entry 100, both adds fill the same day
        fr = fr_set({("low", 91): 90.0, ("close", 91): 95.0})
        res = x.simulate_limit(cfg(K=2), ctx_for(fr))
        self.assertEqual(res["counts"]["add_levels_filled"], 2)
        cy = [c for c in res["cycles"]] or None
        # tranche prices 100, 96, 92 -> check via loss at d91 close 95: sum(u*(95-px)), u=N/3/px
        tr = N / 3
        expected = sum(tr / p * (95.0 - p) for p in (100.0, 96.0, 92.0))
        self.assertAlmostEqual(res["gross"][1.0][91], expected, places=12)

    def test_entry_cost_multiplier_applies_to_entries_only(self):
        ctxd = ctx_for(fr_set({("low", 91): 99.9}), cost=0.001)
        res = x.simulate_limit(cfg(), ctxd)
        # one entry (no exit before the end): central = 0.70 * 0.001 * N; conservative = 1.00 * ...
        self.assertAlmostEqual(res["cost"]["central"][1.0].sum(), -0.70 * 0.001 * N, places=12)
        self.assertAlmostEqual(res["cost"]["conservative"][1.0].sum(), -0.001 * N, places=12)
        self.assertAlmostEqual(res["cost"]["optimistic"][1.0].sum(), -0.47 * 0.001 * N, places=12)
        self.assertEqual(res["cost"]["cost0"][1.0].sum(), 0.0)

    def test_leverage_changes_only_liquidation(self):
        # low 70 on d92 after entry at 100: L3 liq at 100*(1-1/3+.005)=67.2 not hit; L5 liq 80.5 hit
        fr = fr_set({("low", 91): 99.9, ("low", 92): 70.0, ("close", 92): 95.0})
        r3 = x.simulate_limit(cfg(lev=3.0), ctx_for(fr))
        r5 = x.simulate_limit(cfg(lev=5.0), ctx_for(fr))
        self.assertEqual(r3["cycles"], [])
        self.assertEqual([c["exit"][1] for c in r5["cycles"]], ["LIQUIDATED"])
        self.assertAlmostEqual(r5["cycles"][0]["pp"], -N / 5, places=12)
        fr2 = fr_set({("low", 91): 99.9, ("low", 92): 99.0})
        for lv in (1.0, 2.0, 3.0):          # same path without liquidation: identical PnL at every leverage
            self.assertAlmostEqual(tot(x.simulate_limit(cfg(lev=lv), ctx_for(fr2))), tot(x.simulate_limit(cfg(lev=3.0), ctx_for(fr2))), places=15)

    def test_avg_close_stop_exits_next_open_with_gap(self):
        fr = fr_set({("low", 91): 99.9, ("close", 93): 84.0, ("open", 94): 82.0, ("low", 94): 81.0})
        res = x.simulate_limit(cfg(stop="avg_close"), ctx_for(fr))
        cy = res["cycles"][0]
        self.assertEqual(cy["exit"], (94, "STOP"))
        # 100 -> 82 at the open: -18% of N before cost
        self.assertAlmostEqual(cy["pp"], -0.18 * N, places=10)


if __name__ == "__main__":
    unittest.main()
