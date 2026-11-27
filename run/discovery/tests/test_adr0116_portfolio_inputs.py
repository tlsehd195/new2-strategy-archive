"""ADR 0116 (directive §4): portfolio.simulate_portfolio refuses invalid signals
and bars instead of interpreting them (decision_index -1 used to enter at the
first bar and read the LAST bar's volume as the "previous" bar).

Synthetic data, temp-free, real implementation.  Boundary cases first, then
the portfolio mechanics the directive lists (duplicate entry, concurrency,
funding, liquidation, fees, slippage-style cost, partial reduction).
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (str(HERE.parent), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from data_quality import Bar  # noqa: E402
from costs_bridge import CostContext  # noqa: E402
from portfolio import (  # noqa: E402
    PortfolioConfig, SignalInputError, portfolio_passes, simulate_portfolio, validate_signals,
)

MS15 = 900_000


def cost(fee=0.0, half_spread=0.0):
    return CostContext.from_params({"fee_rate": fee, "half_spread": half_spread,
                                    "impact_coefficient": 0.0, "max_participation": 1.0},
                                   version="TEST", test_only=True)


def bars(prices, qv=1e12, lows=None):
    out = []
    for i, p in enumerate(prices):
        lo = lows[i] if lows and lows[i] is not None else p
        out.append(Bar(ts=i * MS15, open=p, high=p, low=min(lo, p), close=p, volume=1e9, quote_volume=qv))
    return out


def sim(bys, sigs, c=None, funding=None, **cfg):
    base = dict(initial_capital=10_000.0, size_fraction=1.0, leverage=1.0, max_concurrent=2)
    base.update(cfg)
    if not isinstance(bys, dict):
        bys = {"A": bys}
    return simulate_portfolio(bys, sigs, funding or {s: [] for s in bys}, c or cost(), PortfolioConfig(**base))


def sig(d, h=3, sym="A", side=1, **kw):
    return {"symbol": sym, "decision_index": d, "side": side, "horizon": h, **kw}


class DecisionIndexBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.b = bars([100.0] * 10)

    def test_negative_index_refused(self):
        # v4: entry_i = 0 -> entered at the first bar, cost read from bars[-1] (the LAST bar)
        with self.assertRaisesRegex(SignalInputError, "negative decision_index -1"):
            sim(self.b, [sig(-1)])

    def test_index_beyond_data_refused(self):
        for d in (9, 10, 50):
            with self.assertRaisesRegex(SignalInputError, "leaves no entry bar"):
                sim(self.b, [sig(d)])

    def test_non_integer_index_refused(self):
        for d in (3.7, "3", None, True, float("nan")):
            with self.assertRaisesRegex(SignalInputError, "is not an integer"):
                sim(self.b, [sig(d)])

    def test_boundary_values_accepted(self):
        r = sim(self.b, [sig(0, h=2)])                 # first bar decides, enters at bar 1
        self.assertEqual(r.trades, 1)
        self.assertEqual(r.signals_received, 1)
        r = sim(self.b, [sig(8)])                      # entry would be the last (forced-close) bar
        self.assertEqual((r.trades, r.signals_unexecuted_at_data_end), (0, 1))

    def test_numpy_integer_accepted(self):
        try:
            import numpy as np
        except ImportError:  # pragma: no cover - CI unit step is stdlib-only
            self.skipTest("numpy not installed")
        self.assertEqual(sim(self.b, [sig(np.int64(2), h=2)]).trades, 1)


class SignalFieldTest(unittest.TestCase):
    def setUp(self):
        self.b = bars([100.0] * 10)

    def test_side_symbol_horizon_reduce(self):
        for bad, msg in ((sig(2, side=0), "side 0"), (sig(2, side=2), "side 2"), (sig(2, side=1.0), "side 1.0"),
                         (sig(2, sym="ZZ"), "unknown symbol"), (sig(2, h=0), "horizon 0"),
                         (sig(2, h=2.5), "horizon 2.5"), ({"symbol": "A", "decision_index": 2, "reduce": 1.0},
                                                          r"reduce 1.0 not in \(0, 1\)"),
                         ("not a dict", "not a mapping")):
            with self.subTest(msg=msg), self.assertRaisesRegex(SignalInputError, msg):
                sim(self.b, [bad])

    def test_bad_bars_refused(self):
        b = bars([100.0] * 10)
        b[3] = Bar(ts=b[3].ts, open=float("nan"), high=100, low=100, close=100, volume=1, quote_volume=1e12)
        with self.assertRaisesRegex(SignalInputError, "non-finite or non-positive price"):
            sim(b, [sig(2)])
        b = bars([100.0] * 10)
        b[3] = Bar(ts=b[3].ts, open=100, high=99, low=98, close=100, volume=1, quote_volume=1e12)
        with self.assertRaisesRegex(SignalInputError, "inconsistent OHLC"):
            sim(b, [sig(3)])
        b = bars([100.0] * 10)
        b[2] = Bar(ts=b[2].ts, open=100, high=100, low=100, close=100, volume=1, quote_volume=-5)
        with self.assertRaisesRegex(SignalInputError, "invalid quote volume"):
            sim(b, [sig(2)])

    def test_reversed_or_duplicate_grid_refused(self):
        b = bars([100.0] * 10)
        b[5] = Bar(ts=b[4].ts, open=100, high=100, low=100, close=100, volume=1, quote_volume=1e12)
        with self.assertRaisesRegex(SignalInputError, "not strictly increasing"):
            sim(b, [sig(1)])

    def test_validator_returns_normalised_copies(self):
        s = [sig(2)]
        out = validate_signals({"A": self.b}, s)
        self.assertEqual(out[0]["decision_index"], 2)
        self.assertIsNot(out[0], s[0])

    def test_runner_style_caller_fails_closed(self):
        # runner.py wraps simulate_portfolio in `except ValueError` -> passed False
        self.assertTrue(issubclass(SignalInputError, ValueError))


class PortfolioMechanicsTest(unittest.TestCase):
    def test_duplicate_entry_same_symbol_skipped(self):
        r = sim(bars([100.0] * 12), [sig(1, h=5), sig(2, h=2)])
        self.assertEqual((r.trades, r.skipped_same_symbol_conflict), (1, 1))

    def test_concurrent_positions_capacity(self):
        bys = {s: bars([100.0] * 12) for s in ("A", "B", "C")}
        r = sim(bys, [sig(1, sym="A"), sig(1, sym="B"), sig(1, sym="C")], size_fraction=0.3)
        self.assertEqual((r.trades, r.skipped_capacity), (2, 1))

    def test_fee_and_spread_charged_each_side(self):
        r = sim(bars([100.0] * 10), [sig(1, h=3)], c=cost(fee=0.001, half_spread=0.0005), size_fraction=0.5)
        # notional 5000 entry + 5000 exit at 15 bp per side = 15
        self.assertAlmostEqual(r.total_fees, 15.0, places=6)
        self.assertAlmostEqual(r.final_equity, 10_000.0 - 15.0, places=6)

    def test_funding_settles_inside_the_hold(self):
        f = {"A": [(3 * MS15, 0.001)]}
        r = sim(bars([100.0] * 10), [sig(1, h=4)], funding=f, size_fraction=0.5)
        self.assertAlmostEqual(r.total_funding, 5.0, places=6)      # long pays 0.1 % of 5000
        self.assertEqual(r.funding_status, "FUNDING_DATA_MISSING")  # no interval declared: never a pass
        self.assertFalse(portfolio_passes(r.to_json())[0])

    def test_liquidation(self):
        b = bars([100.0] * 10, lows=[None, None, None, 40.0, None, None, None, None, None, None])
        r = sim(b, [sig(1, h=5)], leverage=2.0, size_fraction=1.0)
        self.assertEqual(r.liquidation_events, 1)
        self.assertFalse(portfolio_passes(r.to_json())[0])

    def test_partial_reduction(self):
        r = sim(bars([100.0, 100.0, 100.0, 110.0, 110.0, 110.0, 110.0, 110.0]),
                [sig(1, h=5), {"symbol": "A", "decision_index": 2, "reduce": 0.5}], size_fraction=0.5)
        self.assertEqual(r.partial_closes, 1)
        self.assertTrue(math.isfinite(r.final_equity))


if __name__ == "__main__":
    unittest.main()
