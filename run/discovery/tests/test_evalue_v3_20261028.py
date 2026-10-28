"""Walk-forward e-value v3 final form (ADR 0042): e-process equals the simulated
rule (run/test_evalue_v3.py), stop/floor, day-end marking, members binding, ledger order and chain,
gate rule for the v3 schema, and the final-form draft stays a draft.

    cd run && python3 -m unittest discovery.tests.test_evalue_v3_20261028
"""

import datetime as dt
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

DISC = Path(__file__).resolve().parents[1]
RUN = DISC.parent
sys.path.insert(0, str(DISC))

import evalue_v3 as V  # noqa: E402
import prereg_gate  # noqa: E402

FINAL = RUN / "audit" / "evidence_v3_final_20261028" / "walkforward_evalue_v3_FINALFORM_DRAFT.json"
OLD = RUN / "audit" / "evidence_evalue_v3_20261022" / "walkforward_evalue_v3_DRAFT.json"
SPEC = {"data": ["klines"], "direction": "long",
        "entry": {"conditions": [{"feature": "breakout_k", "op": "gt", "params": {"window": 96}, "value": 1.5}],
                  "logic": "all"},
        "exit": {"horizon": 48, "stop_loss_bp": None, "take_profit_bp": None},
        "family": "spec:breakout_k:long", "filters": [], "primary_cell": {"horizon": 48, "symbol": "BTCUSDT"},
        "risk": {"max_concurrent": 2, "max_leverage": 1.0, "risk_per_trade": 0.01, "sizing": "vol_inverse"},
        "schema": "strategy_spec_v1", "symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "15m"}


def pre():
    p, sha = V.load_prereg(FINAL)
    return p, sha


class EProcessTest(unittest.TestCase):
    # equality with evalue_walkforward_sim (numpy) is in run/test_evalue_v3.py
    def test_params_match_old_draft(self):
        p = V.params(pre()[0])
        old = json.loads(OLD.read_text())
        self.assertEqual(p["alpha"], 0.0091)
        self.assertEqual(p["alpha"], old["alpha"])
        for k, f in (("L", "stop_L"), ("B", "floor_B"), ("c", "gap_allowance_c"), ("block_sd", "block_sd")):
            self.assertEqual(p[k], old["sizing_and_bounds"][f])
        self.assertEqual(p["lam_max"], old["e_process"]["lam_max"])
        self.assertEqual(p["members"], 5)


class StopTest(unittest.TestCase):
    def test_stop_first_day_at_or_below(self):
        r = V.apply_stop([0.01, -0.03, -0.06, -0.02], L=0.0548, B=0.1095)
        self.assertEqual(r["stop_day"], 2)
        self.assertAlmostEqual(r["x"], -0.06)

    def test_floor(self):
        r = V.apply_stop([0.0, -0.2], L=0.0548, B=0.1095)
        self.assertTrue(r["floored"])
        self.assertAlmostEqual(r["x"], -0.1095)
        self.assertAlmostEqual(r["x_raw"], -0.2)

    def test_no_stop_last_value_and_empty(self):
        self.assertAlmostEqual(V.apply_stop([0.01, 0.02], L=0.05, B=0.1)["x"], 0.02)
        self.assertEqual(V.apply_stop([], L=0.05, B=0.1)["x"], 0.0)

    def test_day_end_marking(self):
        D = 86_400_000
        opens = [100.0, 100.0, 110.0, 120.0]
        closes = [100.0, 110.0, 120.0, 90.0]
        ev = {"entry_index": 1, "exit_index": 3, "side": 1, "entry_ts": D // 2, "exit_ts": D + D // 2,
              "cost_bp": 10.0, "funding_bp": 4.0, "net_bp": 1990.0}
        # day 0 ends at D: open, marked at close index 1 (110): +1000 - 10 - 4*(D/2)/D
        path = V.slot_value_path([ev], [D, 2 * D], [1, 3], opens, closes)
        self.assertAlmostEqual(path[0], 1000.0 - 10.0 - 2.0)
        self.assertAlmostEqual(path[1], 1990.0)        # closed: net_bp

    def test_bundle_scale(self):
        p = V.bundle_path({"a": {"X": [100.0], "Y": [0.0]}, "b": {"X": [0.0], "Y": [0.0]}}, 2.0)
        self.assertAlmostEqual(p[0], 2.0 * 25.0 / 1e4)

    def test_vol_scale_cap(self):
        x = [5.0 if i % 2 else -5.0 for i in range(100)]
        sc = V.vol_scale(x, target=0.01, k_max=10.0)
        self.assertAlmostEqual(sc["k_raw"], 100.0 / math.sqrt(25.0 * 100 / 99))
        self.assertEqual(sc["k"], 10.0)
        self.assertFalse(V.vol_scale([400.0 * v for v in x], target=0.01, k_max=10.0)["capped"])


class MembersAndLedgerTest(unittest.TestCase):
    def setUp(self):
        self.pre, self.sha = pre()
        ids = [f"S-{i}" for i in range(7)]
        self.m = V.members_record(self.pre, self.sha, candidate_ids=ids, specs={i: SPEC for i in ids},
                                  context_id="C2-test", code_hash="h")
        self.m = V.with_scale(self.m, {"k": 3.0})

    def test_first_five_no_entry_bar(self):
        self.assertEqual([x["strategy_id"] for x in self.m["members"]], [f"S-{i}" for i in range(5)])
        self.assertEqual(V.check_members(self.m, self.sha), [])

    def test_binding(self):
        self.assertTrue(V.check_members(self.m, "0" * 64))
        bad = dict(self.m, context_id="other")
        self.assertTrue(any("hash" in s for s in V.check_members(bad, self.sha)))
        bad = V.with_scale(dict(self.m, code={"code_hash": "h", "v3_code_sha256": "x"}), {"k": 3.0})
        self.assertTrue(any("FROZEN_CODE_CHANGED" in s for s in V.check_members(bad, self.sha)))

    def test_ledger_order_once_after_end_chain(self):
        with tempfile.TemporaryDirectory() as d:
            led = V.Ledger(Path(d) / "l.jsonl")
            res = {"x": 0.01, "x_raw": 0.01, "stop_day": None}
            with self.assertRaises(V.V3Refused):        # not ended yet
                V.book_block(led, self.pre, self.sha, "m", "2026-12", res, today=dt.date(2026, 12, 31))
            with self.assertRaises(V.V3Refused):        # out of order
                V.book_block(led, self.pre, self.sha, "m", "2027-01", res, today=dt.date(2027, 3, 1))
            r1 = V.book_block(led, self.pre, self.sha, "m", "2026-12", res, today=dt.date(2027, 1, 3))
            with self.assertRaises(V.V3Refused):        # once
                V.book_block(led, self.pre, self.sha, "m", "2026-12", res, today=dt.date(2027, 1, 3))
            r2 = V.book_block(led, self.pre, self.sha, "m", "2027-01", res, today=dt.date(2027, 2, 3))
            p = V.params(self.pre)
            z = (0.01 - p["c"]) / (p["B"] + p["c"])
            e = V.e_path([z, z], s0=p["block_sd"] / (p["B"] + p["c"]), alpha=p["alpha"], lam_max=p["lam_max"])
            self.assertAlmostEqual(r2["log_e"], e[-1]["log_e"])
            self.assertEqual(r2["prev_sha256"], r1["row_sha256"])
            self.assertAlmostEqual(r2["reject_at"], 1 / 0.0091)
            lines = led.path.read_text().splitlines()
            led.path.write_text(lines[0].replace('"x": 0.01', '"x": 0.5') + "\n" + lines[1] + "\n")
            with self.assertRaises(V.V3Refused):        # edited
                led.rows()

    def test_family_over_after_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            led = V.Ledger(Path(d) / "l.jsonl")
            led.append({"block_id": "2026-12", "rejected": True, "void": False, "z": 1.0})
            with self.assertRaises(V.V3Refused):
                V.book_block(led, self.pre, self.sha, "m", "2027-01", {"x": 0.0, "x_raw": 0.0},
                             today=dt.date(2027, 2, 3))


class GateAndDraftTest(unittest.TestCase):
    def test_gate_rule_and_still_draft(self):
        self.assertIn("walkforward_evalue_prereg_v3", prereg_gate.REQUIRED_FIELDS)
        rep = prereg_gate.evaluate(FINAL)
        self.assertEqual(rep["lifecycle"], "DRAFT")
        self.assertEqual(rep["required_missing"], [])
        self.assertTrue(rep["draft_marks"])

    def test_decisions_recorded(self):
        d = json.loads(FINAL.read_text())
        self.assertEqual(d["blocks"]["first_block_start"], "2026-12-01")
        self.assertEqual(d["blocks"]["first_block_id"], "2026-12")
        self.assertIn("NOT moved", d["decisions_20261028"]["Q3"])
        self.assertEqual(len(d["universe"]["symbols"]), 9)
        self.assertEqual(V.block_bounds("2026-12"), ("2026-12-01", "2026-12-31"))
        self.assertEqual(V.next_block("2026-12"), "2027-01")
        self.assertTrue(math.isclose(d["alpha"], 0.0091))


if __name__ == "__main__":
    unittest.main()
