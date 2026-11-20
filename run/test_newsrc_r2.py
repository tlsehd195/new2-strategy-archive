"""Tests for round 2 of the new-source search (ADR 0097) and the shared engine. Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import newsrc_engine as ne  # noqa: E402
import newsrc_r2_dev as r2  # noqa: E402


class NewSrcR2Test(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(r2.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(r2.KEYS))
        self.assertEqual(sorted(p["trials"]), sorted(f"{h}_{u}" for h, u in r2.KEYS))
        self.assertEqual(p["segments"]["S1"]["eval_from"], r2.EVAL_START["S1"])
        self.assertEqual(p["segments"]["S2"]["eval_from"], r2.EVAL_START["S2"])
        self.assertEqual((r2.LOOKBACK, r2.VM_TARGET, r2.VM_CAP), (90, 0.60, 2.0))

    def test_risk_signals(self):
        rng = np.random.default_rng(0)
        n = 94
        base = rng.normal(0, 0.03, (n, 8))
        df = pd.DataFrame(base, columns=[f"N{k}" for k in range(8)])
        m = df.mean(axis=1)
        df["HIBETA"] = 2.0 * m + rng.normal(0, 0.001, n)
        df["LAG"] = m.shift(1).fillna(0.0) + rng.normal(0, 0.001, n)
        out = r2.risk_signals(df, list(df.columns))
        self.assertEqual(out["BAB_BETA"].idxmax(), "HIBETA")
        self.assertEqual(out["DLY_DELAY"].idxmax(), "LAG")
        self.assertLess(out["DLY_DELAY"]["HIBETA"], 0.1)

    def test_vm_weights_cap(self):
        u = ["A", "B"]
        w = r2.weights("VM1_VOLMANAGED", pd.Series(0.1, index=u), u, {})
        self.assertAlmostEqual(sum(w.values()), 2.0)
        w = r2.weights("VM1_VOLMANAGED", pd.Series(1.2, index=u), u, {})
        self.assertAlmostEqual(sum(w.values()), 0.5)
        self.assertEqual(r2.weights("VM1_VOLMANAGED", pd.Series(np.nan, index=u), u, {}), {})

    def test_engine_flag(self):
        row = {"S1": {"cal": {"sr_ann": 3.0, "alpha_t": 2.5}, "halves_sr_ann": [1, 1], "cal_x2": {"sr_ann": 1},
                      "cal_tail_x2": {"sr_ann": 1}}, "S2": {"cal": {"sr_ann": 0.6}}, "nk": 2.0}
        self.assertEqual(ne.flag(row, "nk"), "DEV_PROMISING")
        row["S1"]["halves_sr_ann"] = [1, -0.1]
        self.assertEqual(ne.flag(row, "nk"), "NOT_PROMISING")


if __name__ == "__main__":
    unittest.main()
