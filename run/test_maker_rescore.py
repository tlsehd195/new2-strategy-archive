"""Tests for the limit-order cost re-pricing (ADR 0056). Standard library only."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import maker_rescore_dev as m  # noqa: E402

R0 = {"ann_return_pct": 20.0, "sr_ann": 1.0, "alpha_bp_day": 8.0, "alpha_t": 1.6}
RB = {"ann_return_pct": 10.0, "sr_ann": 0.5, "alpha_bp_day": 4.0, "alpha_t": 0.8}


class Reprice(unittest.TestCase):
    def test_lambda_zero_is_cost0(self):
        self.assertAlmostEqual(m.reprice(R0, RB, 0.0, 0.0)["sr"], 1.0)

    def test_lambda_one_is_base(self):
        r = m.reprice(R0, RB, 1.0, 0.0)
        self.assertAlmostEqual(r["sr"], 0.5)
        self.assertAlmostEqual(r["alpha_t"], 0.8)

    def test_lambda_two_extrapolates(self):
        self.assertAlmostEqual(m.reprice(R0, RB, 2.0, 0.0)["sr"], 0.0)

    def test_fill_loss_lowers_sharpe(self):
        self.assertLess(m.reprice(R0, RB, 0.79, 0.34)["sr"], m.reprice(R0, RB, 0.79, 0.0)["sr"])

    def test_breakeven(self):
        self.assertAlmostEqual(m.breakeven_lambda(R0, RB), 2.0)
        self.assertIsNone(m.breakeven_lambda({**R0, "ann_return_pct": -1.0, "sr_ann": -0.1}, RB))

    def test_published_inputs_exist_and_scenarios_ordered(self):
        for _, p, *_ in m.SOURCES:
            self.assertTrue(p.exists(), p)
        for r in m.collect():
            s = r["scenarios"]
            self.assertGreaterEqual(s["OPTIMISTIC"]["S1"], s["CENTRAL"]["S1"] - 1e-9)
            self.assertGreaterEqual(s["CENTRAL"]["S1"], s["BREAKEVEN_FEE"]["S1"] - 1e-9)


if __name__ == "__main__":
    unittest.main()
