"""ADR 0068 (P-01): multi-strategy portfolio validation of evolved strategies.

Deterministic SYNTHETIC bars only.  These tests check the accounting and the
pipeline wiring; no result here says anything about real markets."""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from costs_bridge import CostContext  # noqa: E402
from data_quality import Bar  # noqa: E402
from portfolio_multi import (  # noqa: E402
    MultiConfig, PortfolioInputError, combine, judge, liq_price, simulate_multi, trades_from_events,
)

M15 = 900_000
T0 = 1_700_000_000_000 // M15 * M15


def fee_cost(fee=0.0005, part=1.0):
    return CostContext.from_params({"fee_rate": fee, "half_spread": 0.0, "impact_coefficient": 0.0,
                                    "max_participation": part}, version="TEST_FEE",
                                   verified=True, test_only=True)


def bars(prices, *, lows=None, highs=None, qv=1e9, t0=T0):
    """Bar i opens at prices[i] and closes at prices[i+1] (last bar: flat)."""
    out = []
    for i, o in enumerate(prices):
        c = prices[i + 1] if i + 1 < len(prices) else o
        lo = min(o, c) if lows is None or lows[i] is None else lows[i]
        hi = max(o, c) if highs is None or highs[i] is None else highs[i]
        out.append(Bar(ts=t0 + i * M15, open=o, high=hi, low=lo, close=c, volume=1.0,
                       quote_volume=qv, taker_buy_volume=0.5))
    return out


def tr(m, sym, side, d, e, x, px=None, reason="time"):
    return {"strategy": m, "symbol": sym, "side": side, "decision_ts": T0 + d * M15,
            "entry_ts": T0 + e * M15, "exit_ts": T0 + x * M15, "exit_price": px, "exit_reason": reason}


def declared(funding):
    """Declared settlement interval of each settlement = the step from the
    previous one (what the CSV column funding_interval_hours states for a
    complete series; ADR 0071).  The first settlement gets the next step."""
    out = {}
    for sym, f in funding.items():
        ts = [t for t, _ in f]
        out[sym] = {t: (ts[i] - ts[i - 1] if i else (ts[1] - ts[0] if len(ts) > 1 else 8 * 3_600_000))
                    for i, t in enumerate(ts)}
    return out


def run(bars_by, trades, members, cfg=None, cost=None, funding=None, **kw):
    if funding and "funding_intervals_by_symbol" not in kw:
        kw["funding_intervals_by_symbol"] = declared(funding)
    return simulate_multi(bars_by, trades, funding or {}, cost or fee_cost(), cfg or MultiConfig(),
                          members=members, **kw)


class TestSingleAndMulti(unittest.TestCase):
    def test_single_strategy_exact_accounting(self):
        b = {"A": bars([100, 100, 105, 110, 110])}
        r = run(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cost=fee_cost(0.001))
        qty = 10_000 / 100
        fee_in, fee_out = 10_000 * 0.001, qty * 110 * 0.001
        self.assertAlmostEqual(r["final_equity"], 10_000 + qty * 10 - fee_in - fee_out, 6)
        self.assertEqual(r["trades_closed"], 1)
        self.assertAlmostEqual(r["totals"]["fees"], fee_in + fee_out, 9)
        # equity identity: initial + sum of trade nets == final equity (no summed returns)
        net = r["metrics"]["strategy_contribution"]["s1"]["net_pnl"]
        self.assertAlmostEqual(10_000 + net, r["final_equity"], 6)

    def test_two_strategies_share_one_account_by_weight(self):
        b = {"A": bars([100] * 3 + [110] * 3), "B": bars([50] * 6)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 4), tr("s2", "B", 1, 0, 1, 4)], ["s1", "s2"], cost=fee_cost(0.0))
        # each strategy gets half the equity: 5000 notional on A -> +10% = +500
        self.assertAlmostEqual(r["final_equity"], 10_500, 6)
        c = r["metrics"]["strategy_contribution"]
        self.assertAlmostEqual(c["s1"]["net_pnl"], 500, 6)
        self.assertAlmostEqual(c["s2"]["net_pnl"], 0, 6)
        self.assertIn("s1|s2", r["metrics"]["strategy_correlation_daily_pnl"])

    def test_all_enter_at_once_capacity_and_fixed_priority(self):
        b = {"A": bars([100] * 6), "B": bars([100] * 6), "C": bars([100] * 6)}
        ts = [tr("s3", "C", 1, 0, 1, 3), tr("s1", "A", 1, 0, 1, 3), tr("s2", "B", 1, 0, 1, 3)]
        r = run(b, ts, ["s1", "s2", "s3"], cfg=MultiConfig(max_concurrent=2))
        self.assertEqual(r["skipped"]["capacity"], 1)
        c = r["metrics"]["strategy_contribution"]
        # member order decides, not the order trades were handed in
        self.assertEqual((c["s1"]["entries"], c["s2"]["entries"], c["s3"]["entries"]), (1, 1, 0))

    def test_same_direction_stacking_is_counted_and_exposure_capped(self):
        b = {"A": bars([100] * 6)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 3), tr("s2", "A", 1, 0, 1, 3)], ["s1", "s2"], cost=fee_cost(0.0))
        self.assertEqual(r["stacked_same_direction"], 1)
        self.assertLessEqual(r["metrics"]["exposure_gross_max"]["value"], 1.0 + 1e-9)

    def test_opposite_positions_both_pay_costs_hedge_mode(self):
        b = {"A": bars([100, 100, 120, 120, 120])}
        r = run(b, [tr("s1", "A", 1, 0, 1, 3), tr("s2", "A", -1, 0, 1, 3)], ["s1", "s2"],
                cost=fee_cost(0.001))
        self.assertEqual(r["stacked_opposite_direction"], 1)
        # +1000 and -1000 cancel; four fills pay fees -> net loss = fees, nothing netted
        self.assertAlmostEqual(r["final_equity"], 10_000 - r["totals"]["fees"], 6)
        # entry 2 x 5000 x 0.001, exit 2 x 50 qty x 120 x 0.001
        self.assertAlmostEqual(r["totals"]["fees"], 5 + 5 + 6 + 6, 6)

    def test_one_per_symbol_policy_skips_the_second(self):
        b = {"A": bars([100] * 5)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 3), tr("s2", "A", -1, 0, 1, 3)], ["s1", "s2"],
                cfg=MultiConfig(same_symbol_policy="one_per_symbol"))
        self.assertEqual(r["skipped"]["same_symbol"], 1)

    def test_duplicate_signal_of_one_strategy_never_adds_size(self):
        b = {"A": bars([100] * 8)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 5), tr("s1", "A", 1, 1, 2, 5)], ["s1"])
        self.assertEqual(r["skipped"]["duplicate_signal"], 1)
        self.assertEqual(r["trades_closed"], 1)

    def test_inactive_member_is_skipped(self):
        b = {"A": bars([100] * 10)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 2), tr("s1", "A", 1, 4, 5, 6)], ["s1"],
                inactive_from={"s1": T0 + 3 * M15})
        self.assertEqual((r["trades_closed"], r["skipped"]["inactive"]), (1, 1))


class TestCapitalMarginLiquidation(unittest.TestCase):
    def test_capital_shrinks_sizing_and_margin_shortage_refuses(self):
        # s1 loses 60% on half the equity; s2's entry needs margin it no longer has
        p = [100, 100, 40, 40, 40, 40, 40, 40]
        b = {"A": bars(p), "B": bars([10] * 8)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 6), tr("s2", "B", 1, 2, 3, 5)], ["s1", "s2"],
                cfg=MultiConfig(max_gross_exposure=5.0), cost=fee_cost(0.0))
        # equity at open[3] = 10000 - 3000 = 7000; used 5000; s2 wants 3500 -> refused
        self.assertEqual(r["skipped"]["margin"], 1)
        self.assertEqual(r["metrics"]["strategy_contribution"]["s2"]["entries"], 0)

    def test_isolated_liquidation_loses_the_margin(self):
        lows = [None, 80.0, None, None]
        b = {"A": bars([100, 100, 95, 95], lows=lows)}
        cfg = MultiConfig(leverage=5.0, max_gross_exposure=5.0)
        self.assertAlmostEqual(liq_price(1, 100, 5.0, 0.005), 80.5)
        r = run(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cfg=cfg, cost=fee_cost(0.0))
        self.assertEqual(r["liquidation_events"], 1)
        # notional 50000 at 5x, margin 10000 lost in full
        self.assertAlmostEqual(r["final_equity"], 0.0, 6)
        self.assertTrue(r["bankrupt"])
        st, why = judge(r, {"liquidation_events_eq": 0, "no_bankruptcy": True, "min_trades": 1})
        self.assertEqual(st, "FAIL_PORTFOLIO")

    def test_cross_margin_simultaneous_losses_liquidate_the_account(self):
        lows = [None, 66.0, None, None]
        b = {"A": bars([100, 100, 90, 90], lows=lows), "B": bars([100, 100, 90, 90], lows=lows)}
        cfg = MultiConfig(leverage=3.0, margin_mode="cross", max_gross_exposure=3.0)
        r = run(b, [tr("s1", "A", 1, 0, 1, 3), tr("s2", "B", 1, 0, 1, 3)], ["s1", "s2"], cfg=cfg,
                cost=fee_cost(0.0))
        # 2 x 15000 notional long, both touch 66 in the same bar: -10200 > equity 10000
        # (one position alone would survive: -5100 against 10000)
        self.assertEqual((r["account_liquidations"], r["liquidation_events"]), (1, 2))
        self.assertLess(r["final_equity"], 0)
        self.assertTrue(r["bankrupt"])

    def test_same_bar_stop_and_liquidation_books_the_larger_loss(self):
        # stop fill at the bar low 79 (bar_extreme) loses 21% x 50000 > margin 10000
        lows = [None, 79.0, None, None]
        b = {"A": bars([100, 100, 95, 95], lows=lows)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 1, px=79.0, reason="stop")], ["s1"],
                cfg=MultiConfig(leverage=5.0, max_gross_exposure=5.0), cost=fee_cost(0.0))
        # ADR 0078 (E-04): the stop lies beyond the liquidation price (80.5), so the
        # price crossed the liquidation level first: the larger (stop) loss is still
        # booked, and it now also counts as a liquidation (was 0: invisible to the
        # ``liquidation_events_eq: 0`` gate)
        self.assertEqual(r["liquidation_events"], 1)
        self.assertAlmostEqual(r["final_equity"], 10_000 - 500 * 21, 6)
        # stop at 85 loses less than the margin -> the liquidation is booked
        lows = [None, 80.0, None, None]
        b = {"A": bars([100, 100, 95, 95], lows=lows)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 1, px=85.0, reason="stop")], ["s1"],
                cfg=MultiConfig(leverage=5.0, max_gross_exposure=5.0), cost=fee_cost(0.0))
        self.assertEqual(r["liquidation_events"], 1)

    def test_gap_move_at_open_exits_at_the_open(self):
        b = {"A": bars([100, 100, 60, 60, 60])}
        r = run(b, [tr("s1", "A", 1, 0, 1, 2, reason="stop_gap")], ["s1"], cost=fee_cost(0.0))
        self.assertAlmostEqual(r["final_equity"], 6_000, 6)


class TestCostsFundingFills(unittest.TestCase):
    def test_funding_paid_by_long_received_by_short(self):
        b = {"A": bars([100] * 6)}
        # ADR 0078 (C-03): a regular 30-minute schedule (the old fixture changed
        # 30m -> 2h, which is contiguous under one reading of the interval column
        # only and is now SCHEDULE_UNKNOWN); the held settlements are the same
        fund = {"A": [(T0 + k * 2 * M15, 0.001 if k == 1 else 0.0) for k in range(6)]}
        long_ = run(b, [tr("s1", "A", 1, 0, 1, 4)], ["s1"], cost=fee_cost(0.0), funding=fund)
        short = run(b, [tr("s1", "A", -1, 0, 1, 4)], ["s1"], cost=fee_cost(0.0), funding=fund)
        self.assertAlmostEqual(long_["totals"]["funding"], 10.0, 6)
        self.assertAlmostEqual(long_["final_equity"], 9_990, 6)
        self.assertAlmostEqual(short["final_equity"], 10_010, 6)
        self.assertEqual(long_["funding_uncovered_trades"], 0)

    def test_funding_not_covering_the_hold_is_insufficient_data(self):
        b = {"A": bars([100] * 40)}
        trades = [tr("s1", "A", 1, i, i + 1, i + 2) for i in range(0, 36, 3)]
        r = run(b, trades, ["s1"], funding={"A": [(T0, 0.0)]})
        self.assertGreater(r["funding_uncovered_trades"], 0)
        st, _ = judge(r, {"min_trades": 1})
        self.assertEqual(st, "INSUFFICIENT_PORTFOLIO_DATA")

    def test_partial_fill_and_latency_stress(self):
        b = {"A": bars([100, 100, 102, 110, 110, 110, 110])}
        r = run(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cfg=MultiConfig(fill_ratio=0.5, latency_bars=1),
                cost=fee_cost(0.0))
        # entry one bar later at 102 for half; the remainder fills at the next open
        # (110) with its own price; time exit one bar later at open[4] = 110
        self.assertAlmostEqual(r["final_equity"], 10_000 + 5_000 / 102 * 8, 6)
        self.assertEqual(r["metrics"]["strategy_contribution"]["s1"]["entries"], 2)
        self.assertAlmostEqual(r["unfilled_notional"], 0.0, 6)
        cc = r["cost_components"]
        self.assertIn("partial_fill", cc["not_modelled"])
        self.assertIn("STRESS", cc["components"]["partial_fill"]["treatment"])
        self.assertFalse(cc["complete"])

    def test_partial_fill_remainder_cancelled_at_exit(self):
        b = {"A": bars([100, 100, 110, 110])}
        r = run(b, [tr("s1", "A", 1, 0, 1, 2)], ["s1"], cfg=MultiConfig(fill_ratio=0.5), cost=fee_cost(0.0))
        self.assertAlmostEqual(r["final_equity"], 10_500, 6)
        self.assertAlmostEqual(r["unfilled_notional"], 5_000, 6)

    def test_partial_fill_is_not_just_a_smaller_position(self):
        # the remainder buys at the next (higher) open: worse than the full fill
        b = {"A": bars([100, 100, 104, 104, 100, 100])}
        full = run(b, [tr("s1", "A", 1, 0, 1, 4)], ["s1"], cost=fee_cost(0.001))
        part = run(b, [tr("s1", "A", 1, 0, 1, 4)], ["s1"], cfg=MultiConfig(fill_ratio=0.5),
                   cost=fee_cost(0.001))
        self.assertLess(part["final_equity"], full["final_equity"])

    def test_latency_never_drops_a_fast_stop_out(self):
        from strategy_spec import stop_exit_rule
        spec = {"schema": "strategy_spec_v1", "direction": "long", "symbols": ["A"],
                "entry": {"logic": "all", "conditions": []}, "filters": [],
                "exit": {"horizon": 4, "stop_loss_bp": 500.0}}
        try:
            rule = stop_exit_rule(spec)
        except Exception:  # noqa: BLE001 — spec schema details; build the same rule by hand
            rule = None
        if rule is None:
            def rule(bars_, i, side, h):
                for j in range(i, min(i + h, len(bars_))):
                    if bars_[j].low <= bars_[i].open * 0.95:
                        return j, min(bars_[j].low, bars_[i].open * 0.95), "stop"
                return (i + h, bars_[i + h].open, "time") if i + h < len(bars_) else None
        lows = [None, None, 90.0, None, None, None, None, None]
        b = {"A": bars([100, 100, 100, 92, 92, 92, 92, 92], lows=lows)}
        ts = [tr("s1", "A", 1, 0, 1, 2, px=90.0, reason="stop")]
        base = run(b, ts, ["s1"], cost=fee_cost(0.0))
        late = run(b, ts, ["s1"], cfg=MultiConfig(latency_bars=1), cost=fee_cost(0.0),
                   exit_rules={"s1": (rule, 4)})
        self.assertAlmostEqual(base["final_equity"], 9_000, 6)
        self.assertEqual(late["trades_closed"], 1)              # entered late, still stopped out
        self.assertLess(late["final_equity"], 10_000)
        self.assertEqual(late["skipped"]["latency_beyond_data"], 0)
        with self.assertRaisesRegex(PortfolioInputError, "exit rule"):
            run(b, ts, ["s1"], cfg=MultiConfig(latency_bars=1))

    def test_participation_refusal_and_missing_traded_value(self):
        b = {"A": bars([100] * 6, qv=1_000.0)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cost=fee_cost(0.0, part=0.01))
        self.assertEqual(r["skipped"]["cost_refused"], 1)
        b = {"A": bars([100] * 6, qv=0.0)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"])
        self.assertEqual(r["skipped"]["cost_data_missing"], 1)

    def test_unpriced_exit_is_undetermined_never_pass(self):
        qv = [1e9, 1e9, 0.0, 1e9, 1e9]
        b = {"A": [Bar(ts=x.ts, open=x.open, high=x.high, low=x.low, close=x.close, volume=1.0,
                       quote_volume=qv[i], taker_buy_volume=0.5) for i, x in enumerate(bars([100] * 5))]}
        r = run(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], funding={"A": [(T0, 0.0), (T0 + 9 * M15, 0.0)]})
        self.assertEqual(r["cost_status"], "UNPRICED_EXIT")
        self.assertEqual(judge(r, {"min_trades": 1})[0], "UNDETERMINED_PORTFOLIO")

    def test_unmodelled_components_are_listed_not_zero(self):
        r = run({"A": bars([100] * 5)}, [tr("s1", "A", 1, 0, 1, 3)], ["s1"])
        self.assertTrue({"partial_fill", "fill_latency"} <= set(r["cost_components"]["not_modelled"]))


class TestReviewFixes(unittest.TestCase):
    """Defects found by the independent review (ADR 0068)."""

    def test_gross_exposure_cap_is_not_multiplied_by_leverage(self):
        b = {"A": bars([100] * 6), "B": bars([100] * 6)}
        cfg = MultiConfig(leverage=3.0, max_gross_exposure=1.0)
        r = run(b, [tr("s1", "A", 1, 0, 1, 3), tr("s2", "B", 1, 0, 1, 3)], ["s1", "s2"], cfg=cfg,
                cost=fee_cost(0.0))
        # each wants 1.5x equity: the first already exceeds a 1.0 cap
        self.assertEqual(r["skipped"]["gross_exposure"], 2)
        r = run(b, [tr("s1", "A", 1, 0, 1, 3), tr("s2", "B", 1, 0, 1, 3)], ["s1", "s2"],
                cfg=MultiConfig(leverage=3.0, max_gross_exposure=3.0), cost=fee_cost(0.0))
        self.assertEqual(r["skipped"]["gross_exposure"], 0)
        self.assertLessEqual(r["metrics"]["exposure_gross_max"]["value"], 3.0 + 1e-9)

    def test_zero_holding_open_exit_is_rejected(self):
        with self.assertRaisesRegex(PortfolioInputError, "zero holding"):
            run({"A": bars([100] * 4)}, [tr("s1", "A", 1, 0, 1, 1)], ["s1"])

    def test_allocation_split_by_spec_symbols_not_by_traded_symbols(self):
        b = {"A": bars([100, 100, 110, 110, 110]), "B": bars([10] * 5)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cost=fee_cost(0.0),
                member_symbols={"s1": ["A", "B"]})
        self.assertAlmostEqual(r["final_equity"], 10_500, 6)       # half of the equity on A
        with self.assertRaisesRegex(PortfolioInputError, "not a symbol"):
            run(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], member_symbols={"s1": ["B"]})

    def test_event_bar_mismatch_is_not_repaired(self):
        b = bars([100, 100, 100, 100])
        ev = [{"symbol": "A", "entry_index": 1, "exit_index": 1, "side": 1, "gross_bp": -2000.0}]
        t = trades_from_events("s1", ev, b)
        with self.assertRaisesRegex(PortfolioInputError, "outside its bar"):
            run({"A": b}, t, ["s1"])

    def test_liquidation_wins_without_pricing_the_stop(self):
        # stop fill would be refused by the cost model; liquidation is the booked fill
        lows = [None, None, 80.0, None]
        qv = [1e9, 1.0, 1e9, 1e9]          # the bar before the stop has no depth
        b0 = bars([100, 100, 100, 95], lows=lows)
        b = {"A": [Bar(ts=x.ts, open=x.open, high=x.high, low=x.low, close=x.close, volume=1.0,
                       quote_volume=qv[i], taker_buy_volume=0.5) for i, x in enumerate(b0)]}
        r = run(b, [tr("s1", "A", 1, 0, 1, 2, px=85.0, reason="stop")], ["s1"],
                cfg=MultiConfig(leverage=5.0, max_gross_exposure=5.0), cost=fee_cost(0.0, part=0.5),
                funding={"A": [(T0, 0.0), (T0 + 9 * M15, 0.0)]})
        self.assertEqual(r["liquidation_events"], 1)
        self.assertEqual(r["cost_status"], "OK")
        self.assertEqual(judge(r, {"liquidation_events_eq": 0, "min_trades": 1})[0], "FAIL_PORTFOLIO")


class TestTimeIntegrity(unittest.TestCase):
    def test_fill_on_the_signal_bar_is_rejected(self):
        with self.assertRaises(PortfolioInputError):
            run({"A": bars([100] * 5)}, [tr("s1", "A", 1, 1, 1, 3)], ["s1"])

    def test_future_decision_is_lookahead(self):
        with self.assertRaisesRegex(PortfolioInputError, "look-ahead"):
            run({"A": bars([100] * 6)}, [tr("s1", "A", 1, 4, 5, 5)], ["s1"], as_of_ts=T0 + 3 * M15)

    def test_unsorted_or_duplicate_bars_are_an_error(self):
        b = bars([100] * 5)
        with self.assertRaisesRegex(PortfolioInputError, "strictly increasing"):
            run({"A": [b[0], b[2], b[1], b[3]]}, [], ["s1"])
        with self.assertRaisesRegex(PortfolioInputError, "strictly increasing"):
            run({"A": [b[0], b[1], b[1], b[3]]}, [], ["s1"])

    def test_impossible_exit_price_is_an_error(self):
        with self.assertRaisesRegex(PortfolioInputError, "outside its bar"):
            run({"A": bars([100] * 5)}, [tr("s1", "A", 1, 0, 1, 2, px=150.0)], ["s1"])

    def test_abnormal_bar_is_an_error(self):
        b = bars([100] * 4)
        bad = Bar(ts=b[2].ts, open=100, high=90, low=80, close=100, volume=1, quote_volume=1e9,
                  taker_buy_volume=0.5)
        with self.assertRaisesRegex(PortfolioInputError, "abnormal"):
            run({"A": [b[0], b[1], bad, b[3]]}, [], ["s1"])

    def test_missing_bar_during_hold_marks_at_last_known_price(self):
        b = bars([100, 100, 104, 108, 108, 108])
        a = {"A": [b[0], b[1], b[3], b[4], b[5]], "B": bars([10] * 6)}  # bar 2 of A missing
        r = run(a, [tr("s1", "A", 1, 0, 1, 4)], ["s1"], cost=fee_cost(0.0))
        self.assertEqual(r["trades_closed"], 1)
        self.assertTrue(all(math.isfinite(v) for _, v in r["equity_daily"]))

    def test_deterministic(self):
        b = {"A": bars([100, 101, 99, 103, 102, 104] * 5)}
        ts = [tr("s1", "A", 1, i, i + 1, i + 3) for i in range(0, 24, 4)]
        self.assertEqual(run(b, ts, ["s1"])["result_id"], run(b, ts, ["s1"])["result_id"])


class TestMetricsAndVerdict(unittest.TestCase):
    def test_short_sample_metrics_are_not_computable_not_zero(self):
        r = run({"A": bars([100, 100, 101, 102])}, [tr("s1", "A", 1, 0, 1, 2)], ["s1"])
        m = r["metrics"]
        for k in ("cagr", "sharpe_annual", "sortino_annual", "calmar"):
            self.assertEqual((m[k]["status"], m[k]["value"]), ("NOT_COMPUTABLE", None), k)
        self.assertEqual(m["profit_factor"]["status"], "NOT_COMPUTABLE")   # no losing trade
        st, why = judge(r, {"min_trades": 30, "min_days": 30})
        self.assertEqual(st, "INSUFFICIENT_PORTFOLIO_DATA")

    def test_combine_takes_the_worst(self):
        self.assertEqual(combine(["PASS_PORTFOLIO", "FAIL_PORTFOLIO"]), "FAIL_PORTFOLIO")
        self.assertEqual(combine(["PASS_PORTFOLIO", "PORTFOLIO_SIMULATION_ERROR"]), "PORTFOLIO_SIMULATION_ERROR")
        self.assertEqual(combine([]), "INSUFFICIENT_PORTFOLIO_DATA")

    def test_trades_from_events_keep_the_evaluator_exit(self):
        b = bars([100, 100, 100, 100], lows=[None, 95.0, None, None])
        ev = [{"symbol": "A", "entry_index": 1, "exit_index": 1, "side": 1, "gross_bp": -500.0},
              {"symbol": "A", "entry_index": 1, "exit_index": 3, "side": 1, "gross_bp": 0.0}]
        t = trades_from_events("s1", ev, b)
        self.assertAlmostEqual(t[0]["exit_price"], 95.0)
        self.assertIsNone(t[1]["exit_price"])
        self.assertEqual(t[0]["decision_ts"], b[0].ts)


# ---------------------------------------------------------------------------
# pipeline wiring
# ---------------------------------------------------------------------------
from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider  # noqa: E402


class TestPipelineLayer(PipelineCase):
    def _pf_cfg(self, **checks):
        cfg = json.loads((HERE.parent / "config" / "portfolio_validation_v1.json").read_text())
        cfg["scenarios"]["base"]["checks"].update(checks)
        p = self.td / "pf.json"
        p.write_text(json.dumps(cfg))
        return p

    def _pipe(self, prov=None, pf=None, state=None):
        from pipeline import EvolutionPipeline
        from test_pipeline import verified_fee_cost
        return EvolutionPipeline(state or self.td / "state", prov or SyntheticProvider(), verified_fee_cost(),
                                 symbols=["BTCUSDT"], portfolio_config=pf)

    def test_survivor_gets_separate_portfolio_record_and_passes(self):
        p = self._pipe()
        ids = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        ctx = p.context_id()
        self.assertEqual(p.statuses.status(ids[0], ctx), "VALIDATION_SURVIVOR")
        rec = p.evaluation("portfolio", ids[0])
        self.assertEqual(rec["portfolio_status"], "PASS_PORTFOLIO")
        self.assertEqual(rec["individual_status"], "PASS_INDIVIDUAL")
        self.assertEqual(rec["data_window"], "validation")
        self.assertEqual(set(rec["compositions"]), {"standalone"})          # empty book
        self.assertEqual(p.portfolio_report()[0]["portfolio_status"], "PASS_PORTFOLIO")

    def test_fail_portfolio_keeps_individual_status_and_never_enters_locked(self):
        prov = SyntheticProvider()
        p = self._pipe(prov, pf=self._pf_cfg(max_drawdown_lt=0.0))
        ids = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        ctx = p.context_id()
        self.assertEqual(p.portfolio_status(ids[0]), "FAIL_PORTFOLIO")
        self.assertEqual(p.statuses.status(ids[0], ctx), "VALIDATION_SURVIVOR")   # not merged
        self.assertIsNone(p.run_locked_campaign())
        self.assertEqual(prov.locked_loads, 0)
        self.assertEqual(p.lock_registry.uses(ids[0], "BTCUSDT"), 0)

    def test_member_with_missing_data_is_insufficient(self):
        p = self._pipe()
        sid, _ = p.register({**EDGE, "symbols": ["ETHUSDT"]}, source="t")
        out = p._portfolio_run([sid])
        self.assertEqual(out["status"], "INSUFFICIENT_PORTFOLIO_DATA")

    def test_member_that_raises_is_simulation_error(self):
        p = self._pipe()
        ids = self.register_all(p, [EDGE])

        def boom(*_a, **_k):
            raise RuntimeError("strategy failed")
        p._validation_events = boom
        p._validation_study = boom        # the portfolio inputs read events + diagnostics (ADR 0071)
        out = p._portfolio_run(ids)
        self.assertEqual(out["status"], "PORTFOLIO_SIMULATION_ERROR")

    def test_interrupted_before_portfolio_record_resumes_once(self):
        p = self._pipe()
        orig = p.portfolio_validation

        def crash(_sid):
            raise KeyboardInterrupt
        p.portfolio_validation = crash
        ids = self.register_all(p, [EDGE] + NOISE[:7])
        with self.assertRaises(KeyboardInterrupt):
            p.run_validation()
        self.assertIsNone(p.evaluation("portfolio", ids[0]))
        p2 = self._pipe()
        self.assertEqual(p2.resume_pending_portfolio(), [ids[0]])
        r1 = p2.evaluation("portfolio", ids[0])
        self.assertEqual(p2.resume_pending_portfolio(), [])                # never twice
        p2.portfolio_validation(ids[0])
        n = sum(1 for line in (p2.eval_path.read_text().splitlines()) if '"stage": "portfolio"' in line
                or '"stage":"portfolio"' in line)
        self.assertEqual(n, 1)
        del orig
        # a fresh state directory reproduces the same simulation result
        p3 = self._pipe(state=self.td / "state3")
        ids3 = self.register_all(p3, [EDGE] + NOISE[:7])
        p3.run_validation()
        r3 = p3.evaluation("portfolio", ids3[0])
        self.assertEqual(r1["compositions"]["standalone"]["scenarios"]["base"]["result"]["result_id"],
                         r3["compositions"]["standalone"]["scenarios"]["base"]["result"]["result_id"])

    def test_joint_composition_includes_the_book(self):
        p = self._pipe()
        ids = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        sid2, _ = p.register({**EDGE, "exit": {"horizon": 6}}, source="t")
        p.dev_evaluate(sid2, record_status=False)
        rec = p.portfolio_validation(sid2)
        self.assertEqual(rec["book"], [ids[0]])
        self.assertEqual(set(rec["compositions"]), {"standalone", "joint"})
        self.assertEqual(rec["selection_record"]["portfolio_checks_before_in_context"], 1)
        joint = rec["compositions"]["joint"]["scenarios"]["base"]["result"]
        self.assertEqual(sorted(joint["members"]), sorted([ids[0], sid2]))

    def test_promotion_requires_pass_portfolio(self):
        p = self._pipe(pf=self._pf_cfg(max_drawdown_lt=0.0))
        ids = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        review = p.promotion_review(ids[0])
        self.assertIn("portfolio validation FAIL_PORTFOLIO", review["failures"])
        self.assertNotEqual(review["decision"], "PAPER_TEST_READY")

    def test_unknown_check_key_is_refused(self):
        from pipeline import load_portfolio_config
        p = self._pf_cfg(max_drawdwn_lt=0.1)
        with self.assertRaisesRegex(ValueError, "unknown checks"):
            load_portfolio_config(p)

    def test_portfolio_config_is_part_of_the_context(self):
        a = self._pipe().context_id()
        b = self._pipe(pf=self._pf_cfg(max_drawdown_lt=0.4)).context_id()
        self.assertNotEqual(a, b)


class TestConfig(unittest.TestCase):
    def test_default_config_and_promo_v5_unchanged(self):
        from pipeline import DEFAULT_PROMOTION_CONFIG, load_portfolio_config
        # ADR 0071: v2 is the default; v1 stays loadable and unchanged (checked below)
        cfg, h = load_portfolio_config(HERE.parent / "config" / "portfolio_validation_v1.json")
        self.assertEqual((cfg["version"], len(h)), ("portfolio-validation-v1", 16))
        v2, _ = load_portfolio_config()
        self.assertEqual(v2["version"], "portfolio-validation-v2")
        self.assertTrue(cfg["gating"])
        self.assertEqual(DEFAULT_PROMOTION_CONFIG.name, "promotion_v5.json")
        base = cfg["scenarios"]["base"]["checks"]
        from portfolio import PORTFOLIO_PASS_CRITERIA
        self.assertEqual(base["max_drawdown_lt"], PORTFOLIO_PASS_CRITERIA["max_drawdown_lt"])
        self.assertEqual(base["liquidation_events_eq"], 0)
        self.assertFalse(cfg["scenarios"]["stress_partial_latency"]["gating"])


if __name__ == "__main__":
    unittest.main()
