"""Prereg v2 entry-bar tool (ADR 0036): S2 never reads BTC, every read is an
allowed DEV read, and the bar is S1 annual SR >= 1.0 (NaN never passes).
No market data is read."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))

import forward_v2_entry_bar_dev as eb  # noqa: E402
import strong_search_dev as T  # noqa: E402


class TestEntryBarTool(unittest.TestCase):
    def test_segments_never_read_btc_in_s2(self):
        seg = eb.segments_for(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        self.assertEqual(seg["S1"]["symbols"], ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        self.assertEqual(seg["S2"]["symbols"], ["ETHUSDT", "SOLUSDT"])
        for s in seg.values():
            for sym in s["symbols"]:
                T.assert_allowed(sym, s["read_start"], s["read_end"])
        self.assertEqual({k: v["read_end"] for k, v in seg.items()},
                         {k: v["read_end"] for k, v in T.SEGMENTS.items()})

    def test_bar(self):
        self.assertTrue(eb.entry_bar_pass({"S1": {"sr_ann": 1.0}}))
        self.assertFalse(eb.entry_bar_pass({"S1": {"sr_ann": 0.99}, "S2": {"sr_ann": 5.0}}))
        self.assertFalse(eb.entry_bar_pass({"S1": {"sr_ann": float("nan")}}))


if __name__ == "__main__":
    unittest.main()
