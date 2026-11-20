"""Tests for round 15 of the new-source search (ADR 0113). Needs numpy and pandas."""

import datetime as dt
import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import newsrc_r15_dev as r15  # noqa: E402


def ms(s):
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


class NewSrcR15Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r15.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(r15.KEYS))
        self.assertEqual(p["context_id"], r15.CONTEXT_ID)
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in r15.KEYS))

    def test_ribbon_uses_only_cut(self):
        h = pd.Series(np.linspace(100, 200, 120))
        self.assertEqual(r15.ribbon_on(h, 100), 1.0)
        self.assertEqual(r15.ribbon_on(h[::-1].reset_index(drop=True), 100), 0.0)
        h2 = h.copy()
        h2.iloc[101:] = 0.0
        self.assertEqual(r15.ribbon_on(h, 100), r15.ribbon_on(h2, 100))
        self.assertTrue(np.isnan(r15.ribbon_on(h, 40)))

    def test_expiry_window(self):
        # last Friday of 2022-06 is 2022-06-24
        self.assertEqual(r15.last_friday(2022, 6), dt.date(2022, 6, 24))
        on = {d: r15.expiry_on(ms(d)) for d in ("2022-06-22", "2022-06-23", "2022-06-24", "2022-06-25",
                                                 "2022-06-26", "2022-06-27", "2022-06-28")}
        self.assertEqual([on[k] for k in sorted(on)], [0.0, 1.0, 0.0, 1.0, 1.0, 1.0, 0.0])
        # month boundary: last Friday 2022-09-30, Monday after is 2022-10-03
        self.assertEqual(r15.expiry_on(ms("2022-10-03")), 1.0)
        self.assertEqual(r15.expiry_on(ms("2022-10-04")), 0.0)

    def test_same_weekday_signal_no_lookahead(self):
        n = 120
        days = np.array([ms("2022-01-03") + k * 86_400_000 for k in range(n)])
        ret = pd.DataFrame({"A": np.where(np.arange(n) % 7 == 0, 0.01, 0.0), "B": np.zeros(n)})
        i = 105  # weekday of 2022-01-03 + 105 days = Monday
        sig = r15.same_weekday_signal(ret, days, i, i - 2)
        self.assertAlmostEqual(sig["A"], 0.01)
        ret2 = ret.copy()
        ret2.iloc[i - 1:] = 9.0
        self.assertEqual(list(r15.same_weekday_signal(ret2, days, i, i - 2)), list(sig))
        self.assertTrue(np.isnan(r15.same_weekday_signal(ret, days, 50, 48)["A"]))

    def test_drift_keeps_holdings(self):
        opn = pd.DataFrame({"A": [1.0, 2.0, 2.0], "B": [1.0, 1.0, np.nan]})
        self.assertEqual(r15.drifted({"A": 0.5, "B": 0.5}, opn, 0, 2), {"A": 1.0, "B": 0.5})


if __name__ == "__main__":
    unittest.main()
