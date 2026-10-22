import sys
import unittest
from pathlib import Path

_P = str(Path(__file__).resolve().parents[1])
if _P not in sys.path:
    sys.path.insert(0, _P)

import short_top_dev as S  # noqa: E402


def _row(direction, mean, t, status, series):
    return {"spec": {"direction": direction}, "metrics": {"dev_mean_bp": mean, "dev_t": t},
            "status": status, "series": series}


class ShortTopTests(unittest.TestCase):
    def test_rank_filters_direction_sign_and_dedups_same_series(self):
        pool = {"a": _row("short", 5, 3.0, "RESEARCH_ONLY", {"X": [0, [1.0]]}),
                "b": _row("short", 5, 2.0, "INCONCLUSIVE", {"X": [0, [1.0]]}),   # same series as a
                "c": _row("short", -1, 9.0, "REJECTED", {"X": [0, [2.0]]}),      # negative mean
                "d": _row("long", 5, 9.0, "RESEARCH_ONLY", {"X": [0, [3.0]]}),
                "e": _row("short", 5, 99.0, "INCONCLUSIVE", {"X": [0, [4.0]]})}
        self.assertEqual(S.rank(pool, "short", passing_only=False, n=10), ["e", "a"])  # t capped at 6, b dropped
        self.assertEqual(S.rank(pool, "short", passing_only=True, n=10), ["a"])

    def test_regime_labels_use_only_earlier_days(self):
        mkt = [1.0] * 70 + [-50.0] + [1.0] * 5
        a = S.regime_labels("trend60", mkt)
        b = S.regime_labels("trend60", mkt[:70] + [+50.0] + mkt[71:])
        self.assertEqual(a[:71], b[:71])          # day 70's own return never changes day 70's label
        self.assertIsNone(a[10])
        self.assertEqual(a[69], "up")

    def test_three_state_has_sideways(self):
        self.assertIn("side", set(S.regime_labels("trend60_3state", [0.0] * 120)))

    def test_regime_stats_masks_days(self):
        r = S.regime_stats([1.0, 2.0, 3.0, 4.0], [True, False, True, True])
        self.assertEqual(r["days"], 3)
        self.assertAlmostEqual(r["total_pct"], 8.0)


if __name__ == "__main__":
    unittest.main()
