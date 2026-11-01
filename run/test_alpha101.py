"""Tests for the 101 Formulaic Alphas DEV check (ADR 0054). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import alpha101_dev as a  # noqa: E402
import broad_universe_dev as bu  # noqa: E402
import cross_section_v2_dev as cs  # noqa: E402


def make_piv(n_days=30, names=("AUSDT", "BUSDT", "CUSDT"), seed=0):
    rng = np.random.default_rng(seed)
    idx = np.arange(n_days)
    piv = {}
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, (n_days, len(names))), axis=0))
    o = c * np.exp(rng.normal(0, 0.01, c.shape))
    h = np.maximum(o, c) * (1 + rng.uniform(0.001, 0.03, c.shape))
    lo = np.minimum(o, c) * (1 - rng.uniform(0.001, 0.03, c.shape))
    v = rng.uniform(1e3, 1e4, c.shape)
    vw = (h + lo + c) / 3
    for k, arr in (("open", o), ("high", h), ("low", lo), ("close", c), ("volume", v), ("qv", v * vw), ("tbq", v * vw / 2)):
        piv[k] = pd.DataFrame(arr, index=idx, columns=list(names))
    return piv


class Alpha101Test(unittest.TestCase):
    def test_prereg_matches_code(self):
        p = json.loads(a.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(tuple(h["id"] for h in p["hypotheses"]), a.ALPHAS)
        self.assertIn("held 7 days", p["timing"])
        self.assertEqual(a.HOLD_DAYS, 7)
        self.assertTrue(all(a.LONG_TOP.values()))

    def test_never_read_ranges_refused(self):
        for lo, hi in (("2020-12-01", "2021-01-31"), ("2023-09-01", "2023-09-19"), ("2025-10-01", "2025-10-21")):
            with self.assertRaises(SystemExit):
                bu.assert_allowed(bu.ms(lo), bu.ms(hi))

    def test_formulas_against_direct_computation(self):
        piv = make_piv()
        names = list(piv["open"].columns)
        cut = 25
        sig = a.signals(piv, cut, names)
        o, h, lo, c, v = (piv[k] for k in ("open", "high", "low", "close", "volume"))
        vw = piv["qv"] / v
        for s in names:
            # A006
            exp6 = -np.corrcoef(o[s].iloc[cut - 9: cut + 1], v[s].iloc[cut - 9: cut + 1])[0, 1]
            self.assertAlmostEqual(sig["A006"][s], exp6, places=10)
            # A012
            exp12 = np.sign(v[s].iloc[cut] - v[s].iloc[cut - 1]) * -(c[s].iloc[cut] / c[s].iloc[cut - 1] - 1)
            self.assertAlmostEqual(sig["A012"][s], exp12, places=12)
            self.assertAlmostEqual(sig["A033"][s], o[s].iloc[cut] / c[s].iloc[cut] - 1, places=12)
            exp41 = (np.sqrt(h[s].iloc[cut] * lo[s].iloc[cut]) - vw[s].iloc[cut]) / vw[s].iloc[cut]
            self.assertAlmostEqual(sig["A041"][s], exp41, places=12)
            x = ((c[s] - lo[s]) - (h[s] - c[s])) / (c[s] - lo[s])
            self.assertAlmostEqual(sig["A053"][s], -(x.iloc[cut] - x.iloc[cut - 9]), places=10)
            exp54 = -((lo[s].iloc[cut] - c[s].iloc[cut]) * o[s].iloc[cut] ** 5) / (
                (lo[s].iloc[cut] - h[s].iloc[cut]) * c[s].iloc[cut] ** 5)
            self.assertAlmostEqual(sig["A054"][s], exp54, places=10)
            exp101 = (c[s].iloc[cut] - o[s].iloc[cut]) / ((h[s].iloc[cut] - lo[s].iloc[cut]) + 0.001 * c[s].iloc[cut])
            self.assertAlmostEqual(sig["A101"][s], exp101, places=12)
        # A002 uses cross-sectional ranks on each of the 6 window days
        dlv = np.log(v).diff(2).iloc[cut - 5: cut + 1].rank(axis=1, pct=True)
        ret = ((c - o) / o).iloc[cut - 5: cut + 1].rank(axis=1, pct=True)
        for s in names:
            self.assertAlmostEqual(sig["A002"][s], -np.corrcoef(dlv[s], ret[s])[0, 1], places=10)

    def test_no_future_bar_used(self):
        piv = make_piv()
        names = list(piv["open"].columns)
        cut = 20
        base = a.signals(piv, cut, names)
        for k in piv:
            piv[k].iloc[cut + 1:] = piv[k].iloc[cut + 1:] * 3.0 + 7.0
        after = a.signals(piv, cut, names)
        for k in a.ALPHAS:
            pd.testing.assert_series_equal(base[k], after[k])

    def test_missing_and_degenerate_values_are_nan(self):
        piv = make_piv()
        names = list(piv["open"].columns)
        cut = 25
        piv["volume"].iloc[cut - 3, 0] = np.nan
        piv["high"].iloc[cut, 1] = piv["low"].iloc[cut, 1]
        sig = a.signals(piv, cut, names)
        self.assertTrue(np.isnan(sig["A006"].iloc[0]))
        self.assertTrue(np.isnan(sig["A002"].iloc[0]))
        self.assertTrue(np.isnan(sig["A054"].iloc[1]))

    def test_install_swaps_engine_parts(self):
        saved = (cs.load_daily, cs.signals, cs.HOLD_DAYS, cs.LONG_TOP, cs.PREREG, cs.EVID)
        try:
            a.install()
            self.assertIs(cs.signals, a.signals)
            self.assertEqual(cs.HOLD_DAYS, 7)
            self.assertEqual(list(cs.LONG_TOP), list(a.ALPHAS))
            lo, hi, first = bu.SEGMENTS["S1"]
            days = np.arange(bu.ms(lo), bu.ms(hi) + 1, bu.DAY_MS)
            reb = cs.rebalance_days(days, first)
            self.assertTrue(all(b - x == 7 for x, b in zip(reb, reb[1:])))
        finally:
            cs.load_daily, cs.signals, cs.HOLD_DAYS, cs.LONG_TOP, cs.PREREG, cs.EVID = saved


if __name__ == "__main__":
    unittest.main()
