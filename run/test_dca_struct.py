"""Tests for the structural-fix extension of the daily scale-in engine (ADR 0091). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dca_extreme_dev as base  # noqa: E402
import dca_struct_dev as x  # noqa: E402
from test_dca_extreme import ctx_for, frames, total  # noqa: E402

N = 0.05


def cfg(**kw):
    return {"id": "t", "N": 30, "s": 0.04, "K": 0, **x.OPTS, **kw}


def clean(fr):
    for key, f in (("high", np.maximum), ("low", np.minimum)):
        fr[key].iloc[:, 0] = f.reduce([fr["open"].iloc[:, 0], fr["close"].iloc[:, 0], fr[key].iloc[:, 0]])
    return fr


class StructTest(unittest.TestCase):
    def test_grid_matches_prereg(self):
        p = json.loads(x.PREREG.read_text())
        cs = x.make_configs()
        self.assertEqual((len(cs), p["n_configs"], p["counted_trials"]), (32, 32, 32))
        self.assertEqual(len({c["id"] for c in cs}), 32)
        self.assertEqual(set(x.VARIANTS), set(p["variants_per_base"]))

    def test_all_off_lev5_reproduces_adr0088_engine(self):
        fr = clean(frames({"close": {(95, 0): 95.0, (97, 0): 88.0, (99, 0): 112.0}}))
        a = x.simulate_struct(cfg(K=2, lev=5.0), ctx_for(fr, cost=0.001))
        b = base.simulate({"id": "t", "N": 30, "s": 0.04, "K": 2, "exit": "target_time_only"}, ctx_for(fr, cost=0.001))
        for sd in x.SIDES:
            np.testing.assert_allclose(a["gross"][sd], b["gross"][sd], atol=1e-15)
        np.testing.assert_allclose(a["adv"], b["adv"], atol=1e-15)
        self.assertEqual([c["exit"] for c in a["cycles"]], [c["exit"] for c in b["cycles"]])

    def test_weights_sum_to_one_and_taper_front_loaded(self):
        w = x.weights(4, 0.6)
        self.assertAlmostEqual(sum(w), 1.0)
        self.assertTrue(all(a > b for a, b in zip(w, w[1:])))
        self.assertEqual(x.weights(2, None), [1 / 3] * 3)

    def test_partial_take_profit_halves_and_keeps_total_pnl(self):
        # long, entry d91 open 100; d93 close 106 (>= +4%) -> half sold at d94 open 106; d96 close 112 -> exit d97 open 112
        fr = clean(frames({"close": {(90, 0): 100.0, (93, 0): 106.0, (94, 0): 106.0, (95, 0): 106.0, (96, 0): 112.0},
                           "open": {(94, 0): 106.0, (95, 0): 106.0, (96, 0): 106.0, (97, 0): 112.0}}))
        res = x.simulate_struct(cfg(pt=0.04), ctx_for(fr))
        cy = res["cycles"][0]
        self.assertEqual(cy["exit"][1], "TARGET")
        # half at +6%, other half at +12% of N notional
        self.assertAlmostEqual(cy["pp"], N * (0.5 * 0.06 + 0.5 * 0.12), places=12)
        self.assertAlmostEqual(total(res, 1.0), cy["pp"], places=12)

    def test_time_cut_halves_then_target_on_remaining_half(self):
        # entry d91 open 100, held >= 3 at d94 close -> half sold d95 open 100 (pnl 0); d98 close 120 -> exit d99 open 120
        fr = clean(frames({"close": {(90, 0): 100.0, (98, 0): 120.0, (99, 0): 120.0},
                           "open": {(99, 0): 120.0}}))
        res = x.simulate_struct(cfg(tm=3), ctx_for(fr))
        cy = res["cycles"][0]
        self.assertEqual((cy["exit"][0], cy["exit"][1]), (99, "TARGET"))
        self.assertTrue(cy["tm_done"])
        self.assertAlmostEqual(cy["pp"], N * 0.5 * 0.20, places=12)
        self.assertAlmostEqual(total(res, 1.0), cy["pp"], places=12)

    def test_cap_refuses_second_cycle(self):
        names = ("AAAUSDT", "BBBUSDT")
        fr = clean(frames({}, names))
        res = x.simulate_struct(cfg(cap=1), ctx_for(fr))
        self.assertGreater(res["refused"]["cap"], 0)
        res0 = x.simulate_struct(cfg(), ctx_for(fr))
        self.assertEqual(res0["refused"]["cap"], 0)


if __name__ == "__main__":
    unittest.main()
