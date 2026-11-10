"""ADR 0070: state / recovery defects found by the 2026-11-10 source audit.
Temp dirs and synthetic records only."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from store import CandidateState, CandidateStore, Ledger, ReverifyQueue, reconcile  # noqa: E402


class TestQueueCrashWindow(unittest.TestCase):
    def _env(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        store = CandidateStore(root / "state.json")
        return root, store, ReverifyQueue(root / "queue.json", store), Ledger(root / "ledger.jsonl")

    def _reverify(self, store, queue, sid="S1"):
        store.set(sid, CandidateState.PENDING)
        store.set(sid, CandidateState.RUNNING)
        store.set(sid, CandidateState.DATA_MISSING)
        self.assertTrue(queue.enqueue(sid, "data_refresh_check"))

    def test_crash_after_item_removed_is_requeued(self):
        root, store, queue, ledger = self._env()
        self._reverify(store, queue)
        # simulate the old crash window: the item is gone, the state never moved
        queue._items = []
        queue.save()
        self.assertEqual(store.entry("S1").state, "REVERIFY_PENDING")
        rep = reconcile(store, ReverifyQueue(root / "queue.json", store), ledger)
        self.assertEqual(rep["orphans_requeued"], ["S1"])
        q2 = ReverifyQueue(root / "queue.json", store)
        item = q2.pop_ready(now_ts=time.time() + 10)
        self.assertEqual(item["strategy_id"], "S1")
        self.assertEqual(store.entry("S1").state, "PENDING")
        # nothing left to requeue and no duplicate item
        self.assertEqual(reconcile(store, q2, ledger)["orphans_requeued"], [])

    def test_pop_promotes_before_dropping_the_item(self):
        root, store, queue, ledger = self._env()
        self._reverify(store, queue)
        saved = []
        orig = queue.save
        queue.save = lambda: saved.append(store.entry("S1").state) or orig()
        queue.pop_ready(now_ts=time.time() + 10)
        # when the queue file is rewritten the candidate is already PENDING
        self.assertEqual(saved, ["PENDING"])


class TestReproVerify(unittest.TestCase):
    def test_equal_counts_with_different_records_is_not_reproduced(self):
        from repro_bundle import verify_rerun
        res = {"dev_evaluated": 2, "dev_ok": 2, "dev_screen_pass": 1, "statuses": {"REJECTED": 1}}
        b = {"kind": "dev", "context_id": "C", "result": res,
             "ledgers": {"evaluations.jsonl": "a" * 64, "statuses.jsonl": "b" * 64}}
        same = {"context_id": "C", "result": res, "ledgers": dict(b["ledgers"])}
        self.assertEqual(verify_rerun(b, same), [])
        other = {"context_id": "C", "result": res,
                 "ledgers": {"evaluations.jsonl": "c" * 64, "statuses.jsonl": "b" * 64}}
        self.assertIn("evaluations.jsonl sha256 differs", verify_rerun(b, other))


class TestNewlistingState(unittest.TestCase):
    def test_corrupt_state_is_an_error_not_a_rebaseline(self):
        import newlisting_collect as nl
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(nl.load_state(d), {})                 # first run: empty
            (d / "state.json").write_text('{"baseline": ["BTCUSDT"]', encoding="utf-8")
            with self.assertRaises(nl.StateCorrupt):
                nl.load_state(d)
            (d / "state.json").write_text("[1, 2]", encoding="utf-8")
            with self.assertRaises(nl.StateCorrupt):
                nl.load_state(d)
            (d / "state.json").write_text(json.dumps({"baseline": []}), encoding="utf-8")
            self.assertEqual(nl.load_state(d), {"baseline": []})


if __name__ == "__main__":
    unittest.main()
