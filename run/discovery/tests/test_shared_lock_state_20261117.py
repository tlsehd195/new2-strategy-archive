"""ADR 0085 (L-1): the locked-TEST campaign count and the per-(strategy, symbol)
exposure record are shared across state directories.  Two directories must never
each take campaign 1 of the same locked window, nor each expose the same
strategy on it.  Synthetic temp dirs only; no real locked window is read."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
if str(DISC) not in sys.path:
    sys.path.insert(0, str(DISC))

from campaigns import CampaignLedger, WindowRetired, window_key  # noqa: E402
from engine_lib import LockRegistryError, LockTestRegistry, shared_lock_dir  # noqa: E402

KW = dict(source="evolution", context_id="ctx", alpha_total=0.05, max_campaigns=5,
          method="bonferroni", config_hash="h")


class SharedBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        p = mock.patch.dict(os.environ, {"NEW2_LOCK_SHARED_DIR": str(self.root / "shared")})
        p.start()
        self.addCleanup(p.stop)

    def dirs(self):
        a, b = self.root / "stateA", self.root / "stateB"
        a.mkdir(), b.mkdir()
        return a, b


class TestSharedCampaigns(SharedBase):
    def test_env_selects_shared_dir(self):
        self.assertEqual(shared_lock_dir(), self.root / "shared")

    def test_second_directory_gets_next_campaign_index(self):
        a, b = self.dirs()
        la = CampaignLedger(a / "locked_window_campaigns.jsonl")
        lb = CampaignLedger(b / "locked_window_campaigns.jsonl")
        ca = la.preregister(source_ref="a", members={"S1": ["BTCUSDT"]}, **KW)
        cb = lb.preregister(source_ref="b", members={"S2": ["BTCUSDT"]}, **KW)
        w = window_key("BTCUSDT")
        self.assertEqual(ca["windows"][w]["campaign_index"], 1)
        self.assertEqual(cb["windows"][w]["campaign_index"], 2)       # was 1 before ADR 0085
        self.assertLess(cb["windows"][w]["alpha_campaign"], ca["windows"][w]["alpha_campaign"])
        self.assertEqual(la.counted_campaigns(w), 2)                   # A sees B's campaign too
        self.assertEqual(lb.counted_campaigns(w), 2)

    def test_window_retired_across_directories(self):
        a, b = self.dirs()
        la = CampaignLedger(a / "c.jsonl")
        lb = CampaignLedger(b / "c.jsonl")
        kw = dict(KW, max_campaigns=1)
        la.preregister(source_ref="a", members={"S1": ["BTCUSDT"]}, **kw)
        self.assertTrue(lb.window_status("BTCUSDT", max_campaigns=1)["retired"])
        with self.assertRaises(WindowRetired):
            lb.preregister(source_ref="b", members={"S2": ["BTCUSDT"]}, **kw)

    def test_aborted_without_exposure_does_not_count_anywhere(self):
        a, b = self.dirs()
        la = CampaignLedger(a / "c.jsonl")
        lb = CampaignLedger(b / "c.jsonl")
        ca = la.preregister(source_ref="a", members={"S1": ["BTCUSDT"]}, **KW)
        la.abort_no_exposure(ca, "data missing")
        cb = lb.preregister(source_ref="b", members={"S2": ["BTCUSDT"]}, **KW)
        self.assertEqual(cb["windows"][window_key("BTCUSDT")]["campaign_index"], 1)

    def test_existing_local_ledger_is_migrated_not_forgotten(self):
        """A ledger written before ADR 0085 (no shared file) still counts for the
        other directory once its owner touches it again."""
        a, b = self.dirs()
        pa = a / "c.jsonl"
        with mock.patch.dict(os.environ, {"NEW2_LOCK_SHARED_DIR": str(self.root / "old_shared")}):
            CampaignLedger(pa).preregister(source_ref="old", members={"S1": ["BTCUSDT"]}, **KW)
        old_bytes = pa.read_bytes()
        la = CampaignLedger(pa)
        la.preregister(source_ref="new", members={"S3": ["BTCUSDT"]}, **KW)   # migrates "old"
        self.assertTrue(pa.read_bytes().startswith(old_bytes))                # no rewrite
        lb = CampaignLedger(b / "c.jsonl")
        self.assertEqual(lb.counted_campaigns(window_key("BTCUSDT")), 2)

    def test_same_content_preregistration_is_idempotent_per_directory(self):
        a, _ = self.dirs()
        la = CampaignLedger(a / "c.jsonl")
        c1 = la.preregister(source_ref="r", members={"S1": ["BTCUSDT"]}, **KW)
        c2 = la.preregister(source_ref="r", members={"S1": ["BTCUSDT"]}, **KW)
        self.assertEqual(c1["campaign_id"], c2["campaign_id"])
        self.assertEqual(la.counted_campaigns(window_key("BTCUSDT")), 1)

    def test_concurrent_directories_never_share_an_index(self):
        n = 4
        ledgers = []
        for i in range(n):
            d = self.root / f"s{i}"
            d.mkdir()
            ledgers.append(CampaignLedger(d / "c.jsonl"))
        out: list = [None] * n

        def work(i):
            out[i] = ledgers[i].preregister(source_ref=f"r{i}", members={f"S{i}": ["BTCUSDT"]},
                                            **dict(KW, max_campaigns=10))
        ts = [threading.Thread(target=work, args=(i,)) for i in range(n)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        w = window_key("BTCUSDT")
        self.assertEqual(sorted(c["windows"][w]["campaign_index"] for c in out), [1, 2, 3, 4])


class TestSharedLockUsage(SharedBase):
    def test_exposure_in_one_directory_blocks_the_other(self):
        a, b = self.dirs()
        ra = LockTestRegistry(a / "lock_test_usage.json")
        rb = LockTestRegistry(b / "lock_test_usage.json")
        r = ra.reserve("S1", "BTCUSDT", "ctxA")
        self.assertTrue(r["ok"])
        ra.start(r["token"])
        ra.complete(r["token"], {"pass": False})
        res = rb.reserve("S1", "BTCUSDT", "ctxB")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "ALREADY_USED")
        self.assertFalse(rb.entry_status("S1", "BTCUSDT")["enterable"])
        self.assertEqual(rb.uses("S1", "BTCUSDT"), 1)
        self.assertTrue(rb.reserve("S1", "ETHUSDT", "ctxB")["ok"])     # other symbol unaffected

    def test_racing_reservations_only_one_directory_may_expose(self):
        a, b = self.dirs()
        ra = LockTestRegistry(a / "lock_test_usage.json")
        rb = LockTestRegistry(b / "lock_test_usage.json")
        ta = ra.reserve("S1", "BTCUSDT", "ctxA")["token"]
        tb = rb.reserve("S1", "BTCUSDT", "ctxB")["token"]             # both reserved, none exposed yet
        ra.start(ta)
        with self.assertRaises(LockRegistryError):
            rb.start(tb)
        self.assertEqual(rb.attempts("S1", "BTCUSDT")[0]["exposed"], False)

    def test_unexposed_attempts_do_not_consume_anywhere(self):
        a, b = self.dirs()
        ra = LockTestRegistry(a / "lock_test_usage.json")
        rb = LockTestRegistry(b / "lock_test_usage.json")
        ta = ra.reserve("S1", "BTCUSDT", "ctxA")["token"]
        ra.block(ta, "data missing")
        self.assertTrue(rb.reserve("S1", "BTCUSDT", "ctxB")["ok"])

    def test_legacy_v1_use_is_shared_once_the_owner_touches_the_registry(self):
        a, b = self.dirs()
        pa = a / "lock_test_usage.json"
        v1 = {"S1": {"BTCUSDT": [{"context_id": "old", "ts": "2026-01-01"}]}}
        pa.write_text(json.dumps(v1))
        ra = LockTestRegistry(pa)
        ra.sync_shared()
        rb = LockTestRegistry(b / "lock_test_usage.json")
        self.assertEqual(rb.reserve("S1", "BTCUSDT", "ctxB")["reason"], "ALREADY_USED")
        self.assertEqual(json.loads(pa.read_text()), v1)               # the v1 file is not rewritten


if __name__ == "__main__":
    unittest.main()
