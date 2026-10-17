"""ADR 0009: the single-use locked TEST window is not spent until the cost model
is verified for the strategy's use (symbols and order size)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from costs_bridge import CostContext  # noqa: E402
from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider  # noqa: E402

WINDOW = "BTCUSDT:LOCK:2024-03-25:2024-06-18"


class TestLockedCostGate(PipelineCase):
    def _survivor(self, prov):
        p = self.pipe(prov)
        ctx = p.context_id()
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        self.assertEqual(p.statuses.status(edge, ctx), "VALIDATION_SURVIVOR")
        return p, ctx, edge

    def test_out_of_scope_verified_cost_keeps_window_untouched(self):
        prov = SyntheticProvider()
        p, ctx, edge = self._survivor(prov)
        with mock.patch.object(CostContext, "verified_for",
                               return_value=(False, ["order value 10000 outside verified range"])):
            self.assertIsNone(p.run_locked_campaign())
            self.assertIsNone(p.run_locked_campaign())          # idempotent across cycles
        rec = p.statuses.get(edge, ctx)
        self.assertEqual((rec["status"], rec["stage"], rec["resume_to"]),
                         ("BLOCKED_COST", "locked", "VALIDATION_SURVIVOR"))
        self.assertIn("locked window preserved", rec["reasons"][0])
        self.assertEqual(prov.locked_loads, 0)
        self.assertEqual(p.lock_registry.uses(edge, "BTCUSDT"), 0)
        self.assertEqual(p.campaigns.counted_campaigns(WINDOW), 0)
        self.assertEqual(p.campaigns.records(), [])

    def test_waiting_strategy_enters_once_cost_covers_it(self):
        p, ctx, edge = self._survivor(SyntheticProvider())
        with mock.patch.object(CostContext, "verified_for", return_value=(False, ["x"])):
            p.run_locked_campaign()
        self.assertEqual(p.statuses.status(edge, ctx), "BLOCKED_COST")
        camp = p.run_locked_campaign()                         # real (test-only verified) cost
        self.assertTrue(camp["results"][edge]["pass"])
        self.assertEqual(p.lock_registry.uses(edge, "BTCUSDT"), 1)
        self.assertEqual(p.statuses.status(edge, ctx), "PAPER_TEST_READY")


if __name__ == "__main__":
    unittest.main()
