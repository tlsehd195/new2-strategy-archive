"""Tests of the ADR 0093 maker low-loss bundle tool (synthetic inputs + the committed catalog)."""
from __future__ import annotations

import csv
import math
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dca_stop_dev as ds  # noqa: E402
import maker_lowloss_dev as m  # noqa: E402


def synth_ctx(seed: int = 0, n_days: int = 400, n_sym: int = 3, cost: float = 0.0008) -> dict:
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.04, (n_days, n_sym))
    close = 100 * np.exp(np.cumsum(r, axis=0))
    opn = np.vstack([close[:1], close[:-1]]) * np.exp(rng.normal(0, 0.01, (n_days, n_sym)))
    high = np.maximum(opn, close) * np.exp(np.abs(rng.normal(0, 0.03, (n_days, n_sym))))
    low = np.minimum(opn, close) * np.exp(-np.abs(rng.normal(0, 0.03, (n_days, n_sym))))
    df = {k: pd.DataFrame(v) for k, v in (("open", opn), ("high", high), ("low", low), ("close", close))}
    lines = {}
    for n in (30, 90):
        lo = df["low"].shift(1).rolling(n, min_periods=n).min().values
        hi = df["high"].shift(1).rolling(n, min_periods=n).max().values
        lines[n] = (lo, hi)
    return {**df, "close_raw": df["close"], "fnd": rng.normal(0, 1e-4, (n_days, n_sym)),
            "impact": lambda k, d: (1e6, 1e6), "side_cost": lambda b, row, q15=None: 0.0 if row == "cost0" else cost,
            "lines": lines}


CFG = {"id": "N90_s0.04_K4_avg_intra0.10", "N": 90, "s": 0.04, "K": 4, "stop": "avg_intra", "x": 0.10}


class Breakeven(unittest.TestCase):
    def test_net_zero_at_breakeven_long_and_short(self):
        for side in (1.0, -1.0):
            units, px = [1.0, 2.0], [100.0, 96.0]
            P = m.breakeven_px(side, units, px, fund=-0.3, entry_cost=0.25, c_exit=0.0007)
            self.assertAlmostEqual(m.net_at(side, units, px, -0.3, 0.25, 0.0007, P), 0.0, places=9)
            Pp = P * (1 + side * m.BE_MARGIN)
            self.assertGreater(m.net_at(side, units, px, -0.3, 0.25, 0.0007, Pp), 0.0)


class Selection(unittest.TestCase):
    def test_rule(self):
        base = {"seg": "S1", "n": "100", "mean_net_bp": "5", "sr_ann": "0.2", "worst_bp": "-2000",
                "max_dd_pct": "", "liq_share": ""}
        self.assertTrue(m.passes_selection(base))
        self.assertFalse(m.passes_selection({**base, "worst_bp": "-2501"}))
        self.assertFalse(m.passes_selection({**base, "sr_ann": "-0.1"}))
        self.assertFalse(m.passes_selection({**base, "worst_bp": ""}))           # no loss metric recorded
        self.assertFalse(m.passes_selection({**base, "liq_share": "0.06"}))
        self.assertFalse(m.passes_selection({**base, "max_dd_pct": "20.5"}))
        self.assertFalse(m.passes_selection({**base, "n": "29"}))

    def test_catalog_reproduces_prereg_members(self):
        sel = m.select_members(list(csv.DictReader(open(m.CATALOG))))
        pre = m.prereg()["selection"]
        self.assertEqual(sel["passing"], pre["rows_passing"])
        self.assertEqual(sel["R9"], pre["members_R9"])
        self.assertEqual(sorted(sel["scalein"]), sorted(pre["members_scalein"]))
        self.assertEqual(len(sel["R9"]) + len(sel["scalein"]), pre["n_members"])
        self.assertEqual(2 * pre["n_members"] + 6, m.prereg()["trials"]["counted"])


class ScaleIn(unittest.TestCase):
    def test_market_mode_reproduces_adr0089_engine(self):
        ctx = synth_ctx(1)
        mk = m.simulate_maker({**CFG, "mode": m.DIAG_MODES["D0_market_all"]}, ctx, "CTRL")
        ref = ds.simulate_stop({**CFG, "base": "x"}, {**ctx, "side_cost": lambda b, row, q15=None: ctx["side_cost"](b, row)})
        ref_net = sum(ref["gross"][sd] + ref["cost"]["measured"][sd] for sd in ds.SIDES)
        np.testing.assert_allclose(mk["gross"] + mk["cost"]["conservative"], ref_net, atol=1e-12)

    def test_profit_exits_are_net_positive(self):
        for seed in range(4):
            ctx = synth_ctx(seed)
            for v in m.VARIANTS:
                run = m.simulate_maker(CFG, ctx, v)
                cs = m.cycle_summary(run["cycles"])
                self.assertEqual(cs["profit_exits_net_le_0"], 0)
                for cy in run["cycles"]:
                    if cy["exit"][1].startswith("TARGET"):
                        self.assertGreater(cy["pp"] + cy["fund"] - cy["cost"]["conservative"], 0)

    def test_rule_has_no_timeout_and_caps_at_twice(self):
        ctx = synth_ctx(2)
        run = m.simulate_maker(CFG, ctx, "RULE")
        for cy in run["cycles"]:
            self.assertNotEqual(cy["exit"][1], "TIMEOUT")
            self.assertLessEqual(cy["exit"][0] - cy["d0"], 2 * 60 + 1)

    def test_cost_rows_share_paths(self):
        run = m.simulate_maker(CFG, synth_ctx(3), "CTRL")
        c = run["cost"]
        self.assertTrue(np.all(c["optimistic"] >= c["central"] - 1e-15))
        self.assertTrue(np.all(c["central"] >= c["conservative"] - 1e-15))
        self.assertTrue(np.all(c["cost0"] == 0))


class Stats(unittest.TestCase):
    def test_sr_and_dd(self):
        x = np.array([0.01, -0.02, 0.03, -0.01])
        self.assertAlmostEqual(m.max_dd(x), 0.02)
        self.assertAlmostEqual(m.sr_ann(x), x.mean() / x.std(ddof=1) * math.sqrt(365))


if __name__ == "__main__":
    unittest.main()
