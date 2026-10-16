"""ASSUMED cost profile (ADR 0007): public fee schedule + slippage margin.

It lets research run without execution reports, but it is never a
verification: it is labelled ASSUMED_COST_MODEL everywhere and can never
reach PAPER_TEST_READY.  Synthetic data and temp dirs only.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from costs_bridge import (ASSUMED_COST_MODEL, CandleCostModel, CostConfigError,  # noqa: E402
                          CostContext, UNVERIFIED_COST_MODEL, load_assumed_profile)
from engine_lib import iter_jsonl  # noqa: E402
from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider  # noqa: E402

SHIPPED = json.loads((DISC / "config" / "cost_assumed_public_v1.json").read_text())


def write_profile(td: Path, **over) -> Path:
    prof = dict(SHIPPED, **over)
    p = td / f"profile_{abs(hash(json.dumps(prof, sort_keys=True)))}.json"
    p.write_text(json.dumps(prof))
    return p


class TestAssumedProfile(PipelineCase):
    def test_shipped_profile_is_assumed_and_conservative(self):
        c = CostContext.from_assumed_profile("assumed_public_v1")
        self.assertEqual(c.status, ASSUMED_COST_MODEL)
        self.assertFalse(c.verified)
        self.assertFalse(c.test_only)
        self.assertIn("#assumed:assumed_public_v1:", c.version)
        # taker fee on every fill, half spread + slippage margin per side
        self.assertEqual(c.params["fee_rate"], 0.0005)
        self.assertAlmostEqual(c.params["half_spread"], 0.0005)
        d = CostContext.futures_default()
        self.assertGreater(c.round_trip_bp(10_000, 1e7, 1e7), d.round_trip_bp(10_000, 1e7, 1e7))
        ok, why = c.verified_for(["BTCUSDT"], 10_000)
        self.assertEqual((ok, why), (False, [ASSUMED_COST_MODEL]))

    def test_assumption_can_never_be_verified(self):
        with self.assertRaises(CostConfigError):
            CostContext(model=CandleCostModel(fee_rate=0.0005, half_spread=0.0005,
                                              impact_coefficient=0.1, max_participation=0.05),
                        version="x", verified=True, assumption={"profile_id": "p", "sha256": "0"})

    def test_bad_profiles_are_refused(self):
        for over in ({"status": "VERIFIED"}, {"schema": "other"}, {"fee_rate": -0.001},
                     {"slippage_margin_per_side": float("nan")}, {"fee_rate": True},
                     {"verified": True}, {"fee_source": ""}):
            with self.subTest(over=over), self.assertRaises(CostConfigError):
                load_assumed_profile(str(write_profile(self.td, **over)))
        with self.assertRaises(CostConfigError):
            load_assumed_profile(str(self.td / "missing.json"))

    def test_default_identity_unchanged_and_assumed_context_differs(self):
        d = CostContext.futures_default().identity()
        self.assertNotIn("assumption_profile", d)          # default cost identity keeps its shape
        self.assertNotIn("status", d)
        a = CostContext.from_assumed_profile("assumed_public_v1").identity()
        self.assertEqual(a["status"], ASSUMED_COST_MODEL)
        self.assertEqual(a["assumption_profile"], "assumed_public_v1")
        self.assertEqual(len(a["assumption_sha256"]), 64)
        pa = self.pipe(SyntheticProvider(), cost=CostContext.from_assumed_profile("assumed_public_v1"))
        pd = self.pipe(SyntheticProvider(), cost=CostContext.futures_default(), state=self.td / "s2")
        self.assertNotEqual(pa.context_id(), pd.context_id())
        # editing the profile file is a new context too
        p2 = write_profile(self.td, slippage_margin_per_side=0.0004)
        pb = self.pipe(SyntheticProvider(), cost=CostContext.from_assumed_profile(str(p2)),
                       state=self.td / "s3")
        self.assertNotEqual(pa.context_id(), pb.context_id())

    def test_assumed_edge_is_labelled_and_never_paper_ready(self):
        # a deliberately cheap assumed profile so the synthetic edge survives every
        # statistical stage — the cost gate alone must stop it
        prof = write_profile(self.td, fee_rate=0.0002, base_half_spread=0.0,
                             slippage_margin_per_side=0.0)
        p = self.pipe(SyntheticProvider(), cost=CostContext.from_assumed_profile(str(prof)))
        ctx = p.context_id()
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        dev = p.evaluation("dev", edge)
        self.assertEqual(dev["cost_model"]["status"], ASSUMED_COST_MODEL)
        self.assertIn("ASSUMED", dev["cost_model"]["claim"])
        p.run_validation()
        p.run_locked_campaign()
        self.assertEqual(p.statuses.status(edge, ctx), "BLOCKED_COST")
        rv = [r for r in iter_jsonl(p.review_path) if r["strategy_id"] == edge][-1]
        self.assertEqual(rv["decision"], "BLOCKED_COST")
        self.assertFalse(rv["cost_verified"])
        self.assertIn(ASSUMED_COST_MODEL, rv["labels"])
        self.assertIn(UNVERIFIED_COST_MODEL, rv["labels"])
        st = [r for r in iter_jsonl(p.statuses.path) if r["strategy_id"] == edge][-1]
        self.assertTrue(st["reasons"][0].startswith(ASSUMED_COST_MODEL))


class TestRunnerCostProfile(unittest.TestCase):
    def test_env_selects_profile(self):
        import runner
        with mock.patch.dict(os.environ, {"NEW2_COST_PROFILE": ""}):
            self.assertEqual(runner.cost_from_env().status, UNVERIFIED_COST_MODEL)
        with mock.patch.dict(os.environ, {"NEW2_COST_PROFILE": "assumed_public_v1"}):
            self.assertEqual(runner.cost_from_env().status, ASSUMED_COST_MODEL)
        with mock.patch.dict(os.environ, {"NEW2_COST_PROFILE": "verified_please"}):
            with self.assertRaises(CostConfigError):
                runner.cost_from_env()

    def test_unknown_profile_is_a_config_error_exit_1(self):
        import shutil
        import tempfile
        import runner
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, True)
        (td / "data").mkdir()
        env = {"NEW2_COST_PROFILE": "nope", "NEW2_DISCOVERY_ROOT": str(td),
               "NEW2_DATA_DIR": str(td / "data")}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(runner.main(["--mode", "evolve"]), 1)
        self.assertFalse((td / "state" / "lock_test_usage.json").exists())


if __name__ == "__main__":
    unittest.main()
