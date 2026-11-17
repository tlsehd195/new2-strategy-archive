"""Tests for run/gold_dev.py (ADR 0086)."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gold_dev as g  # noqa: E402


def _bars(n=700, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2017-01-02", periods=n, freq="15min")
    px = 1300 * np.exp(np.cumsum(rng.normal(0, 1e-3, n)))
    return pd.DataFrame({"ts": ts, "open": px, "high": px * 1.001, "low": px * 0.999, "close": px,
                         "volume": np.ones(n), "hs": np.full(n, 1e-5),
                         "open_bid": px, "open_ask": px})


class GoldDevTests(unittest.TestCase):
    def test_trade_net_costs_and_carry(self):
        assert g.trade_net(1, 100, 100, 0, 0, 0, "cost0") == 0
        assert abs(g.trade_net(1, 100, 100, 1e-4, 1e-4, 0, "perp_taker") + 12e-4) < 1e-12
        # a short receives carry, a long pays it
        assert g.trade_net(-1, 100, 100, 0, 0, 365, "perp_taker") > g.trade_net(1, 100, 100, 0, 0, 365, "perp_taker")

    def test_hilo_no_lookahead_and_signal_direction(self):
        b = _bars()
        b.loc[600, "volume"] = 10.0
        b.loc[600, "close"] = b["high"].iloc[504:600].max() * 1.01  # above the high line
        tr = g.hilo_trades(b, 96, 2.0, 4)
        hit = [t for t in tr if t["entry_i"] == 601]
        assert hit and hit[0]["dir"] == -1 and hit[0]["exit_i"] == 605
        mir = [t for t in g.hilo_trades(b, 96, 2.0, 4, mirror=True) if t["entry_i"] == 601]
        assert mir[0]["dir"] == 1
        # changing a bar after the event never changes the event's signal
        b2 = b.copy(); b2.loc[601:, "close"] *= 2; b2.loc[601:, "high"] *= 2
        assert [t for t in g.hilo_trades(b2, 96, 2.0, 4) if t["entry_i"] == 601][0]["dir"] == -1

    def test_hilo_skips_gap_inside_hold(self):
        b = _bars()
        b.loc[600, "volume"] = 10.0
        b.loc[600, "close"] = b["high"].iloc[504:600].max() * 1.01
        b.loc[603:, "ts"] = b.loc[603:, "ts"] + pd.Timedelta(hours=48)
        assert not [t for t in g.hilo_trades(b, 96, 2.0, 4) if t["entry_i"] == 601]

    def test_held_daily_flat_at_end_and_open_to_open(self):
        d = pd.DataFrame({"date": pd.date_range("2017-01-02", periods=4, freq="D"),
                          "open": [100.0, 110.0, 121.0, 100.0], "hs": [0.0] * 4,
                          "high": [0] * 4, "low": [0] * 4, "close": [0] * 4})
        pos = np.array([1.0, 1.0, 1.0, 1.0])
        s, ch, nl, ns = g.held_daily(d, pos, (pd.Timestamp("2017-01-02"), pd.Timestamp("2017-01-05")), "cost0")
        assert np.allclose(s.to_numpy(), [0.1, 0.1, -100 / 121 * 0 + (100 / 121 - 1), 0.0])
        assert ch == 1 and nl == 3

    def test_tsmom_uses_previous_close_only(self):
        d = pd.DataFrame({"close": [1, 2, 3, 2, 1, 0.5]})
        p = g.daily_positions_tsmom(d, 1)
        assert list(p) == [0, 0, 1, 1, -1, -1]


if __name__ == "__main__":
    unittest.main()
