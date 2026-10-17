"""Auto-evolution §4-3: the legacy generation path enters locked windows only
through preregistered campaigns shared with the evolution pipeline
(synthetic data, temp dirs)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine_lib import append_jsonl  # noqa: E402
import test_engine  # noqa: E402

WKEY = "BTCUSDT:LOCK:2024-03-25:2024-06-18"


class TestLegacyCampaigns(unittest.TestCase):
    _write_btc_dataset = test_engine.TestPromotion._write_btc_dataset
    _runner_one = test_engine.TestPromotion._runner_one
    TOD_DEFN = test_engine.TestPromotion.TOD_DEFN

    def test_pass_is_recorded_as_one_campaign(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            self._write_btc_dataset(data, lock_drift=0.0005)
            r, sid = self._runner_one(td, data)
            r.register_all()
            r.run_one(sid, r._defn_by_sid[sid])
            fin = r.finalize_fdr()
            self.assertIsNotNone(fin["campaign"])
            recs = r.campaigns.records()
            self.assertEqual([x["kind"] for x in recs], ["PREREGISTERED", "RESULT"])
            self.assertEqual(recs[0]["source"], "legacy_fdr")
            self.assertEqual(r.campaigns.counted_campaigns(WKEY), 1)
            rec = r.ledger.latest_by_strategy()[sid]
            self.assertEqual(rec["status"], "PASS")
            res = r.lock_registry.result(sid, "BTCUSDT")
            self.assertEqual(res["campaign_id"], recs[0]["campaign_id"])
            self.assertLessEqual(res["p"], res["campaign_alpha_member"])

    def test_retired_window_holds_survivor_without_touching_lock(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            self._write_btc_dataset(data, lock_drift=0.0005)
            r, sid = self._runner_one(td, data)
            for i in range(3):                       # three earlier looks at the window
                append_jsonl(r.campaigns.path, {"kind": "PREREGISTERED", "campaign_id": f"LC-x{i}",
                                                "source": "evolution", "source_ref": str(i),
                                                "windows": {WKEY: {"symbol": "BTCUSDT"}},
                                                "members": {}})
            r.register_all()
            r.run_one(sid, r._defn_by_sid[sid])
            fin = r.finalize_fdr()
            self.assertIn(sid, fin["campaign_window_retired"])
            self.assertIn(sid, fin["waiting"])
            self.assertEqual(r.lock_registry.uses(sid, "BTCUSDT"), 0)
            self.assertEqual(r.store.entry(sid).state, "AWAITING_FDR")


if __name__ == "__main__":
    unittest.main()
