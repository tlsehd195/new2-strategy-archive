import json
import sys
import tempfile
import unittest
from pathlib import Path

_P = str(Path(__file__).resolve().parents[1])
if _P not in sys.path:
    sys.path.insert(0, _P)

import bundle_dev as B  # noqa: E402


def _w(p, rows):
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


class BundleDevTests(unittest.TestCase):
    def test_stats_and_corr(self):
        self.assertIsNone(B.stats([1.0])["mean_bp_day"])
        s = B.stats([1.0, -1.0, 2.0, 0.0])
        self.assertAlmostEqual(s["mean_bp_day"], 0.5)
        self.assertAlmostEqual(s["mdd_bp"], 1.0)
        self.assertAlmostEqual(B.corr([1, 2, 3], [2, 4, 6]), 1.0)
        self.assertAlmostEqual(B.corr([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertIsNone(B.corr([1, 1, 1], [1, 2, 3]))

    def test_pooled_and_combine(self):
        p = B.pooled_daily({"A": [10, [1.0, 2.0]], "B": [11, [3.0]]})
        self.assertEqual(p, {10: 1.0, 11: 5.0})
        self.assertEqual(B.combine([p, {10: 3.0}], [10, 11, 12]), [2.0, 2.5, 0.0])
        self.assertEqual(B.combine([], [1, 2]), [0.0, 0.0])

    def test_report_uses_only_dev_pass_of_context(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _w(d / "contexts.jsonl", [{"context_id": "C"}])
            spec = lambda sid, dr: {"strategy_id": sid, "definition": {"direction": dr}}
            _w(d / "specs.jsonl", [spec("L", "long"), spec("S", "short"), spec("X", "short")])
            st = lambda sid, s, c="C": {"strategy_id": sid, "context_id": c, "stage": "dev_screen", "status": s}
            _w(d / "statuses.jsonl", [st("L", "RESEARCH_ONLY"), st("S", "RESEARCH_ONLY"), st("X", "REJECTED")])
            ser = lambda sid, v, c="C": {"strategy_id": sid, "context_id": c, "series": {"BTCUSDT": [0, v]}}
            _w(d / "dev_series.jsonl", [ser("L", [1, 2, 3, 4]), ser("S", [-1, -2, -3, -4]),
                                        ser("X", [9, 9, 9, 9]), ser("L", [100, 100], "OLD")])
            r = B.build_report(d)
            self.assertEqual(r["n_members"], {"long": 1, "short": 1, "symmetric": 0})
            self.assertAlmostEqual(r["corr_long_vs_short"], -1.0)
            self.assertAlmostEqual(r["bundles"]["long+short"]["mean_bp_day"], 0.0)
            self.assertAlmostEqual(r["bundles"]["long"]["mean_bp_day"], 2.5)


if __name__ == "__main__":
    unittest.main()
