"""ADR 0066: one standard strategy record for engine and independent
strategies, with a dedup rule.  Synthetic specs only."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import strategy_record as sr  # noqa: E402
from strategy_spec import SPEC_SCHEMA  # noqa: E402


def spec(conds, direction="long"):
    return {"schema": SPEC_SCHEMA, "direction": direction, "symbols": ["BTCUSDT"],
            "exit": {"horizon": 16}, "entry": {"conditions": conds}}


A = {"feature": "ret_z", "params": {"lookback": 4}, "op": "lt", "value": -2.0}
B = {"feature": "hour_utc", "params": {}, "op": "between", "value": [1, 3]}


class TestRecord(unittest.TestCase):
    def test_engine_record_has_every_field(self):
        r = sr.from_engine_spec(spec([A, B]), strategy_id="S1", evaluation_status="RESEARCH_ONLY")
        self.assertEqual(set(r) - {"record_version"}, set(sr.FIELDS))
        self.assertEqual(r["required_features"], ["hour_utc", "ret_z"])
        self.assertEqual(r["data_requirements"], ["klines"])
        self.assertEqual(r["validation_status"], "NOT_VALIDATED")
        self.assertTrue(r["dedup_key"].startswith("bk:"))

    def test_condition_order_does_not_change_the_dedup_key(self):
        r1 = sr.from_engine_spec(spec([A, B]), strategy_id="S1")
        r2 = sr.from_engine_spec(spec([B, A]), strategy_id="S2")
        reps, dups = sr.dedup([r1, r2])
        self.assertEqual([r["strategy_id"] for r in reps], ["S1"])
        self.assertEqual(dups[0]["duplicate_of"], "S1")
        r3 = sr.from_engine_spec(spec([A, B], "short"), strategy_id="S3")
        self.assertEqual(len(sr.dedup([r1, r3])[0]), 2)

    def test_independent_record_rules(self):
        kw = dict(family="cross_section", source_script="run/alpha101_dev.py", symbols="top50_alts",
                  timeframe="1w", entry="Alpha#12  rank  long top 10", exit="hold one week",
                  data_requirements=["daily"], evaluation_status="NOT_PROMISING", adr="0054")
        r = sr.research_record(**kw)
        same = sr.research_record(**{**kw, "entry": "alpha#12 rank long top 10"})   # whitespace / case
        self.assertEqual(r["dedup_key"], same["dedup_key"])
        self.assertIsNone(r["spec"])
        with self.assertRaises(sr.RecordError):
            sr.research_record(**{**kw, "evaluation_status": "PAPER_TEST_READY"})
        bad = dict(r, validation_status="VALIDATION_SURVIVOR")
        with self.assertRaises(sr.RecordError):
            sr.validate(bad)
        with self.assertRaises(sr.RecordError):
            sr.validate({k: v for k, v in r.items() if k != "lineage"})

    def test_engine_record_wins_over_independent_duplicate(self):
        e = sr.from_engine_spec(spec([A]), strategy_id="E1")
        i = dict(sr.research_record(family="momentum", source_script="run/x.py", symbols=["BTCUSDT"],
                                    timeframe="15m", entry="ret_z lt -2", exit="16 bars",
                                    data_requirements=["klines"], evaluation_status="NOT_PROMISING",
                                    adr="0000"), dedup_key=e["dedup_key"])
        reps, dups = sr.dedup([i, e])
        self.assertEqual(reps[0]["strategy_id"], "E1")
        self.assertEqual(dups[0]["source_script"], "run/x.py")


if __name__ == "__main__":
    unittest.main()
