"""Synthetic tests for the independent re-check engine (ADR 0057). No market data."""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit", "evidence_independent_check_20261102"))
import independent_engine as E  # noqa: E402


def synth_panel(n_sym=60, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range(E.S1[0], E.S2[1], freq="D")
    syms = [f"S{i:03d}USDT" for i in range(n_sym)]
    r = rng.normal(0, 0.03, (len(dates), n_sym))
    close = pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=dates, columns=syms)
    gap = (dates > E.S1[1]) & (dates < E.S2[0])
    close[gap] = np.nan
    open_ = close.shift(1).fillna(close)
    open_[gap] = np.nan
    qv = pd.DataFrame(rng.uniform(1e6, 1e8, (len(dates), n_sym)), index=dates, columns=syms)
    qv[gap] = np.nan
    p = {"open": open_, "close": close, "high": np.maximum(open_, close) * 1.01,
         "low": np.minimum(open_, close) * 0.99, "volume": qv / close, "quote_volume": qv,
         "taker_buy_quote_volume": qv * 0.5, "funding": close * 0.0}
    return p


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = synth_panel()

    def test_sign_flip_negates_without_drift(self):
        sp = E.Spec("m", 7, True, "U50", drift=False, funding=False)
        a, _ = E.run(self.p, sp, E.S1, E.sig_mom(28))
        b, _ = E.run(self.p, E.Spec(**{**sp.__dict__, "long_high": False}), E.S1, E.sig_mom(28))
        self.assertLess(float((a + b).abs().max()), 1e-12)

    def test_oracle_is_huge_and_noise_is_small(self):
        sp = E.Spec("m", 7, True, "U50", drift=True, funding=False)
        o, _ = E.run(self.p, sp, E.S1, E.sig_oracle)
        m, _ = E.run(self.p, sp, E.S1, E.sig_mom(28))
        self.assertGreater(E.stats(o)["sr_ann"], 5)
        self.assertLess(abs(E.stats(m)["sr_ann"]), 2.5)

    def test_no_return_across_never_read_gap(self):
        cc, _ = E.day_returns(self.p)
        self.assertTrue(np.isnan(cc.loc[E.S2[0]]).all())

    def test_segment_days_stay_inside(self):
        sp = E.Spec("m", 28, True, "U50")
        s, _ = E.run(self.p, sp, E.S2, E.sig_taker)
        self.assertGreaterEqual(s.index.min(), E.S2[2])
        self.assertLessEqual(s.index.max(), E.S2[1])


if __name__ == "__main__":
    unittest.main()
