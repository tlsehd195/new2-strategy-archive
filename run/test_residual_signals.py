"""Tests for the market-factor-residual DEV check (ADR 0050). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import residual_signals_dev as r


def synth(n_days=91, n_coins=12, seed=3):
    rng = np.random.default_rng(seed)
    m = rng.normal(0, 0.03, n_days)
    betas = np.linspace(0.5, 1.8, n_coins)
    idio = rng.normal(0, 0.01, (n_days, n_coins))
    rets = pd.DataFrame(m[:, None] * betas[None, :] + idio, columns=[f"C{i}" for i in range(n_coins)])
    return rets, betas


class ResidualSignalsTest(unittest.TestCase):
    def test_prereg_is_the_committed_one(self):
        p = json.loads(r.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual([h["id"] for h in p["hypotheses"]], list(r.HYPS))
        for h in p["hypotheses"]:
            self.assertEqual(h["long"] == "highest", r.HYPS[h["id"]][0])
            self.assertEqual(h["hold_days"], r.HYPS[h["id"]][1])
        self.assertEqual(list(p["universe"]["universes"]), list(r.UNIVERSES))
        self.assertEqual(len(r.HYPS) * len(r.UNIVERSES), 8)

    def test_residual_removes_the_market_factor(self):
        rets, betas = synth()
        m = r.market_factor(rets, list(rets.columns))
        beta, e = r.residuals(rets, m)
        self.assertTrue(np.allclose(beta.values, betas, atol=0.25))
        for c in e.columns:
            raw_corr = abs(np.corrcoef(rets[c], m)[0, 1])
            res_corr = abs(np.corrcoef(e[c], m)[0, 1])
            self.assertLess(res_corr, 0.35)
            self.assertGreater(raw_corr, 0.8)

    def test_too_few_observations_gives_nan(self):
        rets, _ = synth()
        rets.iloc[:50, 0] = np.nan
        m = r.market_factor(rets, list(rets.columns))
        beta, e = r.residuals(rets, m)
        self.assertTrue(np.isnan(beta.iloc[0]))
        self.assertTrue(e.iloc[:, 0].isna().all())
        self.assertFalse(np.isnan(beta.iloc[1]))

    def test_signal_ignores_data_after_the_window(self):
        rets, _ = synth(n_days=120)
        base = rets.iloc[:90]
        m = r.market_factor(base, list(base.columns))
        _, e1 = r.residuals(base, m)
        s1 = r.residual_signals(e1)
        later = rets.copy()
        later.iloc[90:] = 9.0  # data after the cut-off must not matter
        base2 = later.iloc[:90]
        _, e2 = r.residuals(base2, r.market_factor(base2, list(base2.columns)))
        s2 = r.residual_signals(e2)
        for h in r.HYPS:
            pd.testing.assert_series_equal(s1[h], s2[h])

    def test_signal_windows_and_missing_days(self):
        rets, _ = synth(n_days=91)
        m = r.market_factor(rets, list(rets.columns))
        _, e = r.residuals(rets, m)
        e.iloc[-3, 2] = np.nan  # a hole inside every window
        s = r.residual_signals(e)
        for h in r.HYPS:
            self.assertTrue(np.isnan(s[h].iloc[2]))
            self.assertFalse(np.isnan(s[h].iloc[3]))
        self.assertAlmostEqual(s["R2_RESREV7"].iloc[0], e.iloc[-7:, 0].sum())
        self.assertAlmostEqual(s["R4_RESMAX28"].iloc[0], e.iloc[-28:, 0].max())

    def test_limit_row_is_070_of_calibrated_cost(self):
        cal = {"maxp": 0.05}
        calls = []
        orig = r._side_cost
        r._side_cost = lambda row, c, b, q: calls.append(row) or 10.0
        try:
            self.assertAlmostEqual(r.side_cost("cal_limit070", cal, 1.0, 1.0), 7.0)
            self.assertEqual(r.side_cost("cal_x2", cal, 1.0, 1.0), 10.0)
        finally:
            r._side_cost = orig
        self.assertEqual(calls, ["cal", "cal_x2"])


if __name__ == "__main__":
    unittest.main()
