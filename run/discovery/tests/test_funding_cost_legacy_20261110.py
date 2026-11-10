"""ADR 0070: funding_cost (legacy, pandas) must flag a missing settlement inside
a hold longer than one funding interval.  Synthetic rows only."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    import pandas  # noqa: F401
    HAVE_PANDAS = True
except ImportError:          # the stdlib-only unit-test step; a later CI step runs this with pandas
    HAVE_PANDAS = False


@unittest.skipUnless(HAVE_PANDAS, "pandas not installed (run by the legacy-module CI step)")
class TestFundingGapTolerance(unittest.TestCase):
    def test_missing_settlement_inside_a_9h_hold_is_incomplete(self):
        import pandas as pd
        from funding_cost import funding_cost_detail
        H = 3_600_000
        t0 = 1_700_000_000_000 - (1_700_000_000_000 % (8 * H))
        # the t0+8h settlement row is missing
        ts = [t0 - 24 * H, t0 - 16 * H, t0 - 8 * H, t0, t0 + 16 * H, t0 + 24 * H, t0 + 32 * H]
        fnd = pd.DataFrame({"calc_time": ts, "rate": [1e-4] * len(ts)})
        d = funding_cost_detail(None, fnd, t0 + 3 * H, t0 + 12 * H)
        self.assertEqual(d["interval_ms"], 8 * H)
        self.assertEqual(d["n_events"], 0)
        self.assertFalse(d["complete"])
        # a 5h hold without a settlement time inside is fine
        self.assertTrue(funding_cost_detail(None, fnd, t0 + 1 * H, t0 + 6 * H)["complete"])


if __name__ == "__main__":
    unittest.main()
