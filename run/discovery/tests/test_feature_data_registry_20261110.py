"""ADR 0066: feature data registry — missing datasets are DATA_MISSING, the
table matches the feature registry.  Synthetic directory only."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feature_data_registry as fdr  # noqa: E402


class TestRegistry(unittest.TestCase):
    def test_table_matches_feature_registry(self):
        self.assertEqual(fdr.check_feature_sets(), [])

    def test_missing_data_is_data_missing(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "klines").mkdir()
            (Path(d) / "klines" / "BTCUSDT-15m-2022-01.csv").write_text("x\n")
            av = fdr.availability(Path(d))
            self.assertEqual(av["klines"]["status"], "PRESENT")
            for k in ("funding", "open_interest", "premium_index", "liquidations", "order_book", "dvol"):
                self.assertEqual(av[k]["status"], "DATA_MISSING", k)
            self.assertEqual(av["trade_count"]["status"], "NOT_DERIVABLE")
            fam = fdr.family_report(Path(d))
            self.assertEqual(fam["momentum"]["status"], "IN_ENGINE")
            self.assertEqual(fam["carry"]["status"], "DATA_MISSING")      # funding absent here
            self.assertEqual(fam["positioning"]["status"], "DATA_MISSING")


if __name__ == "__main__":
    unittest.main()
