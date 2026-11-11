"""ADR 0071: reproduction and regression tests for the portfolio-engine core
defects found after ADR 0068-0070.

* open-gap liquidation (isolated and cross) checked before any exit at the open;
* funding settlement completeness against the DECLARED schedule (a missing
  middle settlement is detected; never charged as 0);
* evaluator signal diagnostics carried to the pipeline records;
* signal accounting / refusal and unfilled statistics reach the verdict;
* close / intrabar worst-case / envelope drawdowns kept apart;
* cross 3x stress has an account drawdown limit (portfolio-validation-v2).

Deterministic SYNTHETIC data only: nothing here says anything about markets.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from data_quality import Bar  # noqa: E402
from evaluator import FundingIndex, run_event_study  # noqa: E402
from funding_schedule import series_issues, settlement_check  # noqa: E402
from portfolio_multi import (  # noqa: E402
    MultiConfig, judge, liq_price, signal_accounting,
)
from test_portfolio_multi_20261110 import M15, T0, bars, fee_cost, run, tr  # noqa: E402

H = 3_600_000
V2 = json.loads((HERE.parent / "config" / "portfolio_validation_v2.json").read_text())


def ohlc(rows, qv=1e9, t0=T0):
    """Bars from explicit (open, high, low, close): a real gap between one
    bar's close and the next bar's open (the ``bars`` helper has none)."""
    return [Bar(ts=t0 + i * M15, open=o, high=h, low=lo, close=c, volume=1.0, quote_volume=qv,
                taker_buy_volume=0.5) for i, (o, h, lo, c) in enumerate(rows)]


def flat(o):
    return (o, o, o, o)


def full_funding(*syms):
    """Two zero-rate settlements around the whole test with a declared interval
    covering the gap: a complete schedule with nothing settled in between."""
    f = {s: [(T0 - M15, 0.0), (T0 + 10_000 * M15, 0.0)] for s in syms}
    return f, {s: {t: 10_001 * M15 for t, _ in v} for s, v in f.items()}


def run2(bars_by, trades, members, **kw):
    """``run`` with complete (zero) funding for every symbol."""
    if "funding" not in kw:
        f, ivs = full_funding(*bars_by)
        kw.update(funding=f, funding_intervals_by_symbol=ivs)
    return run(bars_by, trades, members, **kw)


def x5(**kw):
    """5x isolated, half the equity per position (margin 5000 of 10000)."""
    return MultiConfig(leverage=5.0, max_gross_exposure=5.0, position_fraction=0.5, **kw)


def diag(sym, accepted, **kw):
    """Evaluator diagnostics as EventStudyResult.diagnostics() writes them."""
    d = {"symbol": sym, "signals_total": 0, "skipped_overlapped": 0, "truncated_end_of_window": 0,
         "participation_refusals": 0, "cost_data_missing": 0, "dropped_missing_funding": 0,
         "accepted_events": accepted}
    d.update(kw)
    d["signals_total"] = d["signals_total"] or (accepted + d["skipped_overlapped"]
                                                + d["truncated_end_of_window"] + d["participation_refusals"]
                                                + d["cost_data_missing"] + d["dropped_missing_funding"])
    cand = d["signals_total"] - d["skipped_overlapped"] - d["truncated_end_of_window"]
    d["accounting_ok"] = cand == accepted + d["participation_refusals"] + d["cost_data_missing"] \
        + d["dropped_missing_funding"]
    return d


def checks(scenario="base", **over):
    c = dict(V2["scenarios"][scenario]["checks"])
    c.update(over)
    return c


# ---------------------------------------------------------------------------
# P0-1 open-gap liquidation
# ---------------------------------------------------------------------------
class TestIsolatedOpenGap(unittest.TestCase):
    def test_reproduction_long_gap_below_liq_with_time_exit_at_that_open(self):
        # entry 100 at 5x: liq 80.5; the time exit is at bar 2 whose open is 60.
        # v1 booked a normal exit at 60 (loss 10000 on 25000 notional, 0 liquidations).
        self.assertAlmostEqual(liq_price(1, 100, 5.0, 0.005), 80.5)
        b = {"A": ohlc([flat(100), flat(100), flat(60), flat(60)])}
        r = run2(b, [tr("s1", "A", 1, 0, 1, 2)], ["s1"], cfg=x5(), cost=fee_cost(0.0))
        self.assertEqual(r["liquidation_events"], 1)
        self.assertEqual(r["trades_closed"], 1)
        rec = r["liquidations"][0]
        self.assertEqual((rec["trigger"], rec["side"], rec["symbol"], rec["strategy"]),
                         ("liquidation_open_gap", 1, "A", "s1"))
        self.assertEqual(rec["ts"], T0 + 2 * M15)
        self.assertAlmostEqual(rec["loss_booked"], 5_000, 6)               # posted margin
        self.assertAlmostEqual(rec["price_loss_at_reference"], 250 * 40, 6)
        self.assertAlmostEqual(rec["uncapped_excess_loss"], 10_000 - 5_000, 6)
        self.assertAlmostEqual(rec["equity_cash_after"], 5_000, 6)
        self.assertIn("NOT_CHARGED", rec["exit_fee_slippage"])
        self.assertAlmostEqual(r["final_equity"], 5_000, 6)
        self.assertEqual(judge(r, {"liquidation_events_eq": 0, "min_trades": 1})[0], "FAIL_PORTFOLIO")

    def test_short_gap_above_liq(self):
        self.assertAlmostEqual(liq_price(-1, 100, 5.0, 0.005), 119.5)
        b = {"A": ohlc([flat(100), flat(100), flat(140), flat(140)])}
        r = run(b, [tr("s1", "A", -1, 0, 1, 2)], ["s1"], cfg=x5(), cost=fee_cost(0.0))
        self.assertEqual(r["liquidation_events"], 1)
        self.assertEqual(r["liquidations"][0]["trigger"], "liquidation_open_gap")
        self.assertEqual(r["liquidations"][0]["side"], -1)
        self.assertAlmostEqual(r["final_equity"], 5_000, 6)

    def test_open_exactly_at_liq_price_liquidates_just_above_does_not(self):
        b = {"A": ohlc([flat(100), flat(100), flat(80.5), flat(80.5)])}
        r = run(b, [tr("s1", "A", 1, 0, 1, 2)], ["s1"], cfg=x5(), cost=fee_cost(0.0))
        self.assertEqual(r["liquidation_events"], 1)
        b = {"A": ohlc([flat(100), flat(100), flat(80.6), flat(80.6)])}
        r = run(b, [tr("s1", "A", 1, 0, 1, 2)], ["s1"], cfg=x5(), cost=fee_cost(0.0))
        self.assertEqual(r["liquidation_events"], 0)
        self.assertAlmostEqual(r["final_equity"], 10_000 - 250 * 19.4, 6)

    def test_extreme_gap_loss_capped_at_margin_and_excess_recorded(self):
        for side, px in ((1, 1.0), (-1, 900.0)):
            b = {"A": ohlc([flat(100), flat(100), flat(px), flat(px)])}
            r = run(b, [tr("s1", "A", side, 0, 1, 3)], ["s1"], cfg=x5(), cost=fee_cost(0.0))
            rec = r["liquidations"][0]
            self.assertAlmostEqual(rec["loss_booked"], 5_000, 6)
            self.assertGreater(rec["uncapped_excess_loss"], 5_000)
            self.assertIn("ADR 0068 model assumption", rec["loss_rule"])
            self.assertAlmostEqual(r["final_equity"], 5_000, 6)

    def test_gapped_stop_at_open_and_intrabar_stop_lose_to_the_gap_liquidation(self):
        b = {"A": ohlc([flat(100), flat(100), flat(60), flat(60)])}
        r = run(b, [tr("s1", "A", 1, 0, 1, 2, reason="stop_gap")], ["s1"], cfg=x5(), cost=fee_cost(0.0))
        self.assertEqual(r["liquidations"][0]["trigger"], "liquidation_open_gap")
        # intrabar stop at 59 in the gapped bar: the open already liquidated it
        b = {"A": ohlc([flat(100), flat(100), (60, 60, 59, 60), flat(60)])}
        r = run(b, [tr("s1", "A", 1, 0, 1, 2, px=59.0, reason="stop")], ["s1"], cfg=x5(),
                cost=fee_cost(0.0))
        self.assertEqual((r["liquidation_events"], r["trades_closed"]), (1, 1))
        self.assertEqual(r["liquidations"][0]["trigger"], "liquidation_open_gap")

    def test_normal_open_exit_unchanged(self):
        b = {"A": bars([100, 100, 105, 110, 110])}
        r = run(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cfg=x5(), cost=fee_cost(0.001))
        qty = 25_000 / 100
        self.assertEqual(r["liquidation_events"], 0)
        self.assertAlmostEqual(r["final_equity"], 10_000 + qty * 10 - 25.0 - qty * 110 * 0.001, 6)

    def test_funding_at_the_gap_timestamp_charged_once(self):
        b = {"A": ohlc([flat(100), flat(100), flat(60), flat(60), flat(60)])}
        fund = {"A": [(T0, 0.0), (T0 + 2 * M15, 0.001), (T0 + 4 * M15, 0.0)]}
        r = run(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cfg=x5(), cost=fee_cost(0.0), funding=fund)
        self.assertEqual(r["liquidation_events"], 1)
        pay = 0.001 * 250 * 60                              # one settlement, at the gap open
        self.assertAlmostEqual(r["totals"]["funding"], pay, 9)
        self.assertAlmostEqual(r["liquidations"][0]["funding_paid_before"], pay, 9)
        self.assertAlmostEqual(r["final_equity"], 10_000 - 5_000 - pay, 6)
        self.assertEqual(r["funding_completeness"]["trades_by_status"], {"OK": 1})

    def test_liquidated_position_is_never_closed_again(self):
        # liquidated at bar 2; its scheduled exit at bar 4 must not book anything
        b = {"A": ohlc([flat(100), flat(100)] + [flat(60)] * 4)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 4)], ["s1"], cfg=x5(), cost=fee_cost(0.0))
        self.assertEqual((r["trades_closed"], r["liquidation_events"], r["forced_end_closes"]), (1, 1, 0))
        self.assertAlmostEqual(r["final_equity"], 5_000, 6)


class TestCrossOpenGap(unittest.TestCase):
    cfg = MultiConfig(leverage=3.0, margin_mode="cross", max_gross_exposure=3.0)

    def test_simultaneous_gap_on_two_symbols_liquidates_account_before_exits(self):
        # 2 x 15000 long; both open at 60 where the time exits are: -12000 < maintenance
        g = [flat(100), flat(100), flat(60), flat(60)]
        b = {"A": ohlc(g), "B": ohlc(g)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 2), tr("s2", "B", 1, 0, 1, 2)], ["s1", "s2"], cfg=self.cfg,
                cost=fee_cost(0.0))
        self.assertEqual((r["account_liquidations"], r["liquidation_events"]), (1, 2))
        self.assertEqual({x["trigger"] for x in r["liquidations"]}, {"account_liquidation_open_gap"})
        self.assertTrue(all(x["account_equity_at_reference"] <= x["maintenance_at_reference"]
                            for x in r["liquidations"]))

    def test_single_position_gap_that_the_account_survives_is_a_normal_exit(self):
        b = {"A": ohlc([flat(100), flat(100), flat(60), flat(60)]), "B": bars([100] * 4)}
        cfg = MultiConfig(leverage=3.0, margin_mode="cross", max_gross_exposure=3.0, position_fraction=0.5)
        r = run(b, [tr("s1", "A", 1, 0, 1, 2)], ["s1"], cfg=cfg, cost=fee_cost(0.0))
        # 15000 notional, -6000 at the open, equity 4000 > maintenance 0.005*150*60
        self.assertEqual(r["liquidation_events"], 0)
        self.assertAlmostEqual(r["final_equity"], 4_000, 6)

    def test_profit_on_one_symbol_offsets_loss_on_another(self):
        b = {"A": ohlc([flat(100), flat(100), flat(40), flat(40)]),
             "B": ohlc([flat(100), flat(100), flat(160), flat(160)])}
        r = run(b, [tr("s1", "A", 1, 0, 1, 2), tr("s2", "B", 1, 0, 1, 2)], ["s1", "s2"], cfg=self.cfg,
                cost=fee_cost(0.0))
        self.assertEqual(r["liquidation_events"], 0)

    def test_adverse_moves_on_different_symbols_long_and_short(self):
        b = {"A": ohlc([flat(100), flat(100), flat(60), flat(60)]),
             "B": ohlc([flat(100), flat(100), flat(140), flat(140)])}
        r = run(b, [tr("s1", "A", 1, 0, 1, 2), tr("s2", "B", -1, 0, 1, 2)], ["s1", "s2"], cfg=self.cfg,
                cost=fee_cost(0.0))
        self.assertEqual((r["account_liquidations"], r["liquidation_events"]), (1, 2))

    def test_new_entry_at_the_liquidation_open_is_not_part_of_the_liquidation(self):
        # each member 1/3 of the equity at 3x: 10000 notional each; A and B open at 45
        g = [flat(100), flat(100), flat(45), flat(45), flat(45)]
        b = {"A": ohlc(g), "B": ohlc(g), "C": bars([50] * 5)}
        r = run(b, [tr("s1", "A", 1, 0, 1, 3), tr("s2", "B", 1, 0, 1, 3), tr("s3", "C", 1, 1, 2, 4)],
                ["s1", "s2", "s3"], cfg=self.cfg, cost=fee_cost(0.0))
        self.assertEqual(r["liquidation_events"], 2)
        self.assertEqual({x["strategy"] for x in r["liquidations"]}, {"s1", "s2"})


# ---------------------------------------------------------------------------
# P0-2 funding settlement completeness
# ---------------------------------------------------------------------------
def series(hours, rate=0.0001, t0=0):
    return [(t0 + h * H, rate) for h in hours]


def iv(pairs, hours):
    return {t: hours * H for t, _ in pairs}


class TestFundingSchedule(unittest.TestCase):
    def test_complete_series_ok(self):
        f = series([0, 8, 16, 24])
        c = settlement_check(f, iv(f, 8), 1, 24 * H)
        self.assertEqual((c["status"], c["settlements"]), ("OK", 3))

    def test_reproduction_one_middle_settlement_missing(self):
        f = series([0, 16, 24])                     # 08:00 missing
        c = settlement_check(f, iv(f, 8), 1, 20 * H)
        self.assertEqual(c["status"], "FUNDING_SETTLEMENT_GAP")
        self.assertEqual(c["missing_estimate"], 1)
        self.assertEqual(c["gaps"], [[0, 16 * H, 8 * H]])

    def test_several_missing(self):
        f = series([0, 32, 40])
        c = settlement_check(f, iv(f, 8), 1, 40 * H)
        self.assertEqual((c["status"], c["missing_estimate"]), ("FUNDING_SETTLEMENT_GAP", 3))

    def test_first_and_last_record_missing_is_coverage(self):
        f = series([8, 16, 24])
        self.assertEqual(settlement_check(f, iv(f, 8), 1, 20 * H)["status"], "FUNDING_DATA_MISSING")
        f = series([0, 8, 16])
        self.assertEqual(settlement_check(f, iv(f, 8), 1, 20 * H)["status"], "FUNDING_DATA_MISSING")
        self.assertEqual(settlement_check([], {}, 1, 20 * H)["status"], "FUNDING_DATA_MISSING")
        self.assertEqual(settlement_check(None, None, 1, 20 * H)["status"], "FUNDING_DATA_MISSING")

    def test_boundaries_entry_excluded_exit_included(self):
        f = series([0, 8, 16, 24])
        self.assertEqual(settlement_check(f, iv(f, 8), 8 * H, 16 * H)["settlements"], 1)  # only 16
        self.assertEqual(settlement_check(f, iv(f, 8), 8 * H, 15 * H)["settlements"], 0)

    def test_zero_rate_is_a_real_observation(self):
        f = series([0, 8, 16], rate=0.0)
        self.assertEqual(settlement_check(f, iv(f, 8), 1, 16 * H)["status"], "OK")

    def test_duplicate_unsorted_nonfinite_are_invalid(self):
        for f in ([(0, 0.0), (0, 0.0), (8 * H, 0.0)], [(8 * H, 0.0), (0, 0.0)],
                  [(0, 0.0), (8 * H, float("nan"))]):
            self.assertTrue(series_issues(f))
            self.assertEqual(settlement_check(f, {0: 8 * H, 8 * H: 8 * H}, 1, 8 * H)["status"],
                             "FUNDING_DATA_INVALID")

    def test_symbol_or_exchange_mismatch_is_invalid(self):
        f = series([0, 8])
        self.assertTrue(series_issues(f, meta={"symbol": "ETHUSDT"}, expected_symbol="BTCUSDT"))
        self.assertTrue(series_issues(f, meta={"exchange": "okx"}, expected_exchange="binance_usdm"))

    def test_four_hour_series_gap_missed_by_the_legacy_8h_rule(self):
        f = series([0, 4, 12, 16])                  # 4h schedule, 08:00 missing
        c = settlement_check(f, iv(f, 4), 1, 16 * H)
        self.assertEqual(c["status"], "FUNDING_SETTLEMENT_GAP")
        # the legacy nominal-8h rule (no intervals) does not see it — the reason
        # the pipeline now always passes the declared intervals
        self.assertIsNotNone(FundingIndex(f).paid_bp(1, 16 * H, 1))
        self.assertIsNone(FundingIndex(f, intervals=iv(f, 4)).paid_bp(1, 16 * H, 1))

    def test_schedule_change_8h_to_4h(self):
        f = series([0, 8, 16, 20, 24])
        ivs = {0: 8 * H, 8 * H: 8 * H, 16 * H: 8 * H, 20 * H: 4 * H, 24 * H: 4 * H}
        c = settlement_check(f, ivs, 1, 24 * H)
        self.assertEqual((c["status"], c["schedule_changes"]), ("OK", 1))
        f2 = series([0, 8, 16, 24])                 # 20:00 of the new 4h schedule missing
        ivs2 = {0: 8 * H, 8 * H: 8 * H, 16 * H: 8 * H, 24 * H: 4 * H}
        self.assertEqual(settlement_check(f2, ivs2, 1, 24 * H)["status"], "FUNDING_SETTLEMENT_GAP")

    def test_unknown_schedule(self):
        f = series([0, 8, 16])
        self.assertEqual(settlement_check(f, None, 1, 16 * H)["status"], "FUNDING_SCHEDULE_UNKNOWN")
        self.assertEqual(settlement_check(f, {0: 8 * H}, 1, 16 * H)["status"], "FUNDING_SCHEDULE_UNKNOWN")

    def test_evaluator_drops_and_classifies_a_gap_and_charges_long_short(self):
        n = 60
        b = bars([100.0] * n, t0=0)
        f = series(range(0, 16, 8), rate=0.001) + series(range(16 + 8 * 2, 16 + 8 * 4, 8), rate=0.001)
        sig = lambda v: 1 if len(v) in (2, 40) else 0          # noqa: E731
        es = run_event_study(b, "A", sig, 4, fee_cost(0.0), 1000.0, f, funding_intervals=iv(f, 8))
        d = es.diagnostics()
        self.assertTrue(d["accounting_ok"])
        self.assertEqual(d["funding_schedule"], "DECLARED")
        # complete funding: long pays, short receives
        f = series(range(0, 24, 8), rate=0.001)
        for side in (1, -1):
            es = run_event_study(b, "A", lambda v, s=side: s if len(v) == 30 else 0, 4, fee_cost(0.0),
                                 1000.0, f, funding_intervals=iv(f, 8))
            self.assertEqual(len(es.events), 1)
            self.assertAlmostEqual(es.events[0].funding_bp, side * 10.0, 9)   # settlement at 8h
        # middle settlement missing during the hold: dropped, reason recorded
        f = series([0, 16, 24], rate=0.001)
        es = run_event_study(b, "A", lambda v: 1 if len(v) == 30 else 0, 4, fee_cost(0.0), 1000.0, f,
                             funding_intervals=iv(f, 8))
        self.assertEqual((len(es.events), es.dropped_missing_funding), (0, 1))
        self.assertEqual(es.funding_drop_reasons, {"FUNDING_SETTLEMENT_GAP": 1})

    def test_portfolio_middle_gap_is_insufficient_unknown_schedule_undetermined(self):
        b = {"A": bars([100] * 80)}
        trades = [tr("s1", "A", 1, i, i + 1, i + 40) for i in (0,)]
        f = {"A": [(T0, 0.0), (T0 + 64 * M15, 0.0001), (T0 + 96 * M15, 0.0)]}      # 8h (32 bars) gap
        ivs = {"A": {t: 32 * M15 for t, _ in f["A"]}}
        r = run(b, trades, ["s1"], funding=f, funding_intervals_by_symbol=ivs)
        self.assertEqual(r["funding_completeness"]["trades_by_status"], {"FUNDING_SETTLEMENT_GAP": 1})
        self.assertEqual(judge(r, {"min_trades": 1})[0], "INSUFFICIENT_PORTFOLIO_DATA")
        r = run(b, trades, ["s1"], funding=f, funding_intervals_by_symbol={"A": None})
        self.assertEqual(judge(r, {"min_trades": 1})[0], "UNDETERMINED_PORTFOLIO")
        f2 = {"A": [(T0, 0.0), (T0 + 32 * M15, 0.0), (T0 + 64 * M15, 0.0)]}
        r = run(b, trades, ["s1"], funding=f2)
        self.assertEqual(r["funding_completeness"]["trades_by_status"], {"OK": 1})


# ---------------------------------------------------------------------------
# P0-3 evaluator signal diagnostics
# ---------------------------------------------------------------------------
class TestEvaluatorDiagnostics(unittest.TestCase):
    def _bars(self, n=40, qv=1e9):
        return bars([100.0] * n, qv=qv, t0=0)

    def test_every_signal_in_one_bucket(self):
        b = self._bars()
        # signal every bar, horizon 3: overlaps skipped, last ones truncated
        es = run_event_study(b, "A", lambda v: 1, 3, fee_cost(0.0), 1000.0)
        d = es.diagnostics()
        self.assertTrue(d["accounting_ok"])
        self.assertGreater(d["skipped_overlapped"], 0)
        # ADR 0072: entries 38 and 39 both run past the 40 bars (the ADR 0071 version
        # asserted 1 because the evaluator stopped at the first truncated signal)
        self.assertEqual(d["truncated_end_of_window"], 2)
        self.assertEqual(d["signals_total"], d["accepted_events"] + d["skipped_overlapped"]
                         + d["truncated_end_of_window"] + d["rejected"])
        self.assertEqual(d["rejection_rate"], 0.0)

    def test_participation_refusal_and_missing_traded_value(self):
        b = self._bars(qv=10.0)                      # tiny depth: every order refused
        es = run_event_study(b, "A", lambda v: 1 if len(v) % 5 == 0 else 0, 2,
                             fee_cost(0.0, part=0.01), 1000.0)
        d = es.diagnostics()
        self.assertEqual(d["accepted_events"], 0)
        self.assertEqual(d["participation_refusals"], d["execution_candidates"])
        self.assertEqual(d["rejection_rate"], 1.0)
        self.assertTrue(d["accounting_ok"])
        b = [Bar(ts=x.ts, open=x.open, high=x.high, low=x.low, close=x.close, volume=1.0,
                 quote_volume=(0.0 if i == 9 else 1e9), taker_buy_volume=0.5)
             for i, x in enumerate(self._bars())]
        es = run_event_study(b, "A", lambda v: 1 if len(v) == 10 else 0, 2, fee_cost(0.0), 1000.0)
        d = es.diagnostics()
        self.assertEqual((d["cost_data_missing"], d["accepted_events"]), (1, 0))
        self.assertAlmostEqual(d["data_missing_rate"], 1.0)

    def test_funding_missing_counted(self):
        b = self._bars()
        es = run_event_study(b, "A", lambda v: 1 if len(v) == 10 else 0, 2, fee_cost(0.0), 1000.0,
                             funding=[(10 ** 15, 0.0)])
        d = es.diagnostics()
        self.assertEqual((d["dropped_missing_funding"], d["accepted_events"]), (1, 0))
        self.assertEqual(d["funding_drop_reasons"], {"FUNDING_DATA_MISSING": 1})


# ---------------------------------------------------------------------------
# P0-4 signal accounting and refusals in the verdict
# ---------------------------------------------------------------------------
class TestSignalAccounting(unittest.TestCase):
    def _two(self):
        b = {"A": bars([100 + i for i in range(10)])}
        return b, [tr("s1", "A", 1, 0, 1, 3), tr("s1", "A", 1, 4, 5, 8)]

    def test_identity_and_rates_with_evaluator_diagnostics(self):
        b, t = self._two()
        d = {"s1/A": diag("A", 2, skipped_overlapped=3, participation_refusals=1, truncated_end_of_window=1)}
        r = run2(b, t, ["s1"], signal_diagnostics=d)
        a = r["signal_accounting"]
        self.assertEqual(a["status"], "OK")
        self.assertEqual((a["signals_total"], a["execution_candidates"], a["entered"], a["rejected"]),
                         (7, 3, 2, 1))
        self.assertAlmostEqual(a["cost_refusal_rate"], 1 / 3)
        self.assertEqual(a["by_class"]["strategy_rule"], 3)
        # a cost refusal without a preregistered limit is never a pass (v2: null)
        st, why = judge(r, checks(min_trades=1, min_days=0))
        self.assertEqual(st, "UNDETERMINED_PORTFOLIO")
        self.assertIn("criterion undefined", why[0])
        st, _ = judge(r, checks(min_trades=1, min_days=0, max_cost_refusal_share=0.0))
        self.assertEqual(st, "FAIL_PORTFOLIO")

    def test_no_refusal_passes_and_missing_diagnostics_is_undetermined(self):
        b = {"A": bars([100, 100, 101, 102, 103, 104, 105, 106, 107, 108])}
        t = [tr("s1", "A", 1, 0, 1, 3), tr("s1", "A", 1, 4, 5, 8)]
        ok = run2(b, t, ["s1"], signal_diagnostics={"s1/A": diag("A", 2)}, cost=fee_cost(0.0))
        self.assertEqual(judge(ok, checks(min_trades=1, min_days=0))[0], "PASS_PORTFOLIO")
        no = run2(b, t, ["s1"], cost=fee_cost(0.0))
        self.assertEqual(no["signal_accounting"]["status"], "EVALUATOR_DIAGNOSTICS_NOT_PROVIDED")
        self.assertEqual(judge(no, checks(min_trades=1, min_days=0))[0], "UNDETERMINED_PORTFOLIO")

    def test_mismatch_between_events_and_trades_is_reported(self):
        b, t = self._two()
        r = run2(b, t, ["s1"], signal_diagnostics={"s1/A": diag("A", 3)})
        self.assertEqual(r["signal_accounting"]["status"], "ACCOUNTING_MISMATCH")

    def test_data_missing_signals_are_insufficient(self):
        b, t = self._two()
        r = run2(b, t, ["s1"], signal_diagnostics={"s1/A": diag("A", 2, dropped_missing_funding=1)})
        st, why = judge(r, checks(min_trades=1, min_days=0))
        self.assertEqual(st, "INSUFFICIENT_PORTFOLIO_DATA")
        self.assertIn("data missing", why[0])

    def test_account_side_refusals_counted_by_reason(self):
        up = [100 + i for i in range(6)]
        b = {"A": bars(up), "B": bars(up), "C": bars(up)}
        t = [tr("s1", "A", 1, 0, 1, 3), tr("s2", "B", 1, 0, 1, 3), tr("s3", "C", 1, 0, 1, 3)]
        d = {f"s{i}/{s}": diag(s, 1) for i, s in ((1, "A"), (2, "B"), (3, "C"))}
        r = run2(b, t, ["s1", "s2", "s3"], cfg=MultiConfig(max_concurrent=2), signal_diagnostics=d)
        a = r["signal_accounting"]
        self.assertEqual(a["status"], "OK")
        self.assertEqual(a["portfolio_primary_legs"]["skipped_by_reason"]["capacity"], 1)
        self.assertAlmostEqual(a["capacity_refusal_rate"], 1 / 3)
        self.assertGreater(a["refused_notional_by_reason"]["capacity"], 0)
        self.assertEqual(judge(r, checks(min_trades=1, min_days=0))[0], "UNDETERMINED_PORTFOLIO")

    def test_cost_error_is_counted_not_hidden(self):
        class Boom:
            """Delegates to a real context; every quote is a numeric failure."""
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, k):
                return getattr(self._inner, k)

            def quote(self, order_value, bar_traded_value):
                from costs_bridge import CostComputationError
                raise CostComputationError("numeric failure")
        c = Boom(fee_cost(0.0))
        b, t = self._two()
        r = run2(b, t, ["s1"], cost=c, signal_diagnostics={"s1/A": diag("A", 2)})
        self.assertEqual(r["skipped"]["cost_error"], 2)
        self.assertEqual(r["signal_accounting"]["by_class"]["data_missing"], 2)

    def test_partial_fill_and_cancel_counts(self):
        b = {"A": bars([100, 100, 102, 110, 110, 110, 110])}
        r = run2(b, [tr("s1", "A", 1, 0, 1, 2)], ["s1"], cfg=MultiConfig(fill_ratio=0.5),
                signal_diagnostics={"s1/A": diag("A", 1)})
        a = r["signal_accounting"]
        self.assertEqual((a["partial_fills"], a["remainders_cancelled"]), (1, 1))
        self.assertAlmostEqual(a["partial_fill_rate"], 1.0)
        self.assertAlmostEqual(a["unfilled_notional"], 5_000, 6)

    def test_signal_accounting_function_direct(self):
        legs = {"primary_scheduled": 1, "remainder_scheduled": 0, "primary_entered": 0,
                "remainder_entered": 0, "partial_primary": 0, "remainder_cancelled": 0}
        from portfolio_multi import SKIP_REASONS
        sk = {"primary": {k: int(k == "margin") for k in SKIP_REASONS}, "remainder": {}}
        a = signal_accounting(sk, legs, {"s/A": diag("A", 1)}, n_trades_in=1, refused_notional={},
                              unfilled_notional=0.0)
        self.assertEqual((a["status"], a["margin_refusal_rate"]), ("OK", 1.0))


# ---------------------------------------------------------------------------
# P1 drawdowns
# ---------------------------------------------------------------------------
class TestDrawdowns(unittest.TestCase):
    def test_intrabar_dip_recovered_by_the_close(self):
        b = {"A": bars([100, 100, 99, 99, 99], lows=[None, 70.0, None, None, None])}
        r = run2(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cost=fee_cost(0.0))
        m = r["metrics"]
        self.assertAlmostEqual(m["max_drawdown_close"]["value"], 0.01, 9)
        self.assertAlmostEqual(m["max_drawdown_intrabar_worst_case"]["value"], 0.30, 9)
        self.assertEqual(m["max_drawdown_status"], "OK")
        self.assertGreaterEqual(m["max_drawdown_equity_peak_to_trough"]["value"],
                                m["max_drawdown_intrabar_worst_case"]["value"])
        self.assertEqual(judge(r, {"max_drawdown_intrabar_lt": 0.25, "min_trades": 1})[0], "FAIL_PORTFOLIO")
        self.assertEqual(judge(r, {"max_drawdown_close_lt": 0.25, "min_trades": 1})[0], "PASS_PORTFOLIO")

    def test_short_spike_then_close_lower(self):
        b = {"A": bars([100, 100, 99, 99, 99], highs=[None, 130.0, None, None, None])}
        r = run2(b, [tr("s1", "A", -1, 0, 1, 3)], ["s1"], cost=fee_cost(0.0))
        self.assertAlmostEqual(r["metrics"]["max_drawdown_intrabar_worst_case"]["value"], 0.30, 9)
        self.assertAlmostEqual(r["metrics"]["max_drawdown_close"]["value"], 0.0, 9)

    def test_two_symbols_dip_at_once(self):
        lows = [None, 80.0, None, None]
        b = {"A": bars([100, 100, 100, 100], lows=lows), "B": bars([100, 100, 100, 100], lows=lows)}
        r = run2(b, [tr("s1", "A", 1, 0, 1, 3), tr("s2", "B", 1, 0, 1, 3)], ["s1", "s2"], cost=fee_cost(0.0))
        self.assertAlmostEqual(r["metrics"]["max_drawdown_intrabar_worst_case"]["value"], 0.20, 9)

    def test_symbol_without_a_bar_is_undetermined_not_assumed(self):
        a = bars([100] * 6)
        bb = [x for i, x in enumerate(bars([100] * 6)) if i != 2]
        r = run2({"A": a, "B": bb}, [tr("s1", "B", 1, 0, 1, 4)], ["s1"], cost=fee_cost(0.0))
        m = r["metrics"]
        self.assertGreater(m["stale_marks"], 0)
        self.assertEqual(m["max_drawdown_intrabar_worst_case"]["status"], "UNDETERMINED")
        self.assertEqual(judge(r, {"max_drawdown_intrabar_lt": 0.5, "min_trades": 1})[0],
                         "UNDETERMINED_PORTFOLIO")

    def test_isolated_liquidation_floor_and_bankruptcy(self):
        b = {"A": bars([100, 100, 95, 95], lows=[None, 10.0, None, None])}
        r = run2(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cfg=x5(), cost=fee_cost(0.0))
        self.assertEqual(r["liquidation_events"], 1)
        self.assertAlmostEqual(r["metrics"]["max_drawdown_intrabar_worst_case"]["value"], 0.5, 9)
        b = {"A": bars([100, 100, 95, 95], lows=[None, 80.0, None, None])}
        r = run2(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cfg=MultiConfig(leverage=5.0, max_gross_exposure=5.0),
                cost=fee_cost(0.0))
        self.assertTrue(r["bankrupt"])
        self.assertAlmostEqual(r["metrics"]["max_drawdown_close"]["value"], 1.0, 9)

    def test_one_bar_is_insufficient(self):
        r = run2({"A": bars([100])}, [], ["s1"])
        self.assertEqual(r["metrics"]["max_drawdown_status"], "INSUFFICIENT_DATA")


# ---------------------------------------------------------------------------
# P1 cross 3x risk limit and config v2
# ---------------------------------------------------------------------------
class TestCross3xAndConfig(unittest.TestCase):
    def test_cross_3x_deep_intrabar_drawdown_fails_without_liquidation(self):
        sc = V2["scenarios"]["stress_cross_3x"]
        cfg = MultiConfig(**sc["config"])
        lows = [None, 82.0, None, None, None]
        b = {"A": bars([100, 100, 100, 100, 100], lows=lows)}
        r = run2(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cfg=cfg, cost=fee_cost(0.0),
                signal_diagnostics={"s1/A": diag("A", 1)})
        self.assertEqual(r["liquidation_events"], 0)
        c = checks("stress_cross_3x", min_trades=1, min_days=0)
        v1c = dict(json.loads((HERE.parent / "config" / "portfolio_validation_v1.json").read_text())
                   ["scenarios"]["stress_cross_3x"]["checks"], min_trades=1, min_days=0)
        self.assertEqual(judge(r, v1c)[0], "PASS_PORTFOLIO")          # the v1 hole
        st, why = judge(r, c)
        self.assertEqual(st, "FAIL_PORTFOLIO")                          # 54% intrabar loss
        self.assertTrue(any("intrabar" in w for w in why))
        self.assertIsNotNone(r["metrics"]["min_equity_to_maintenance_ratio"])

    def test_v2_config_loads_and_has_no_new_numeric_threshold(self):
        from pipeline import DEFAULT_PORTFOLIO_CONFIG, load_portfolio_config
        cfg, _ = load_portfolio_config()
        self.assertEqual(DEFAULT_PORTFOLIO_CONFIG.name, "portfolio_validation_v2.json")
        for name in ("base", "stress_cross_3x"):
            c = cfg["scenarios"][name]["checks"]
            self.assertEqual((c["max_drawdown_close_lt"], c["max_drawdown_intrabar_lt"]), (0.5, 0.5))
            self.assertIsNone(c["max_cost_refusal_share"])
            self.assertIsNone(c["max_capital_refusal_share"])
            self.assertEqual(c["max_data_missing_signals"], 0)
            self.assertTrue(c["require_signal_accounting"])
            self.assertEqual((c["liquidation_events_eq"], c["min_trades"], c["min_days"]), (0, 30, 30))

    def test_v2_rejects_out_of_range_refusal_limit(self):
        import tempfile
        from pipeline import load_portfolio_config
        bad = json.loads(json.dumps(V2))
        bad["scenarios"]["base"]["checks"]["max_cost_refusal_share"] = 2
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, "null or in"):
                load_portfolio_config(p)


# ---------------------------------------------------------------------------
# P1 pipeline wiring (synthetic provider; locked / IW windows never opened)
# ---------------------------------------------------------------------------
from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider  # noqa: E402


class TestPipelineWiring(PipelineCase):
    def _pipe(self, prov=None, state=None):
        from pipeline import EvolutionPipeline
        from test_pipeline import verified_fee_cost
        return EvolutionPipeline(state or self.td / "state", prov or SyntheticProvider(), verified_fee_cost(),
                                 symbols=["BTCUSDT"])

    def test_diagnostics_in_dev_validation_and_portfolio_records_and_after_restart(self):
        prov = SyntheticProvider()
        p = self._pipe(prov)
        ids = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        ctx = p.context_id()
        dev = p.evaluation("dev", ids[0])
        d = dev["per_symbol"]["BTCUSDT"]["signal_diagnostics"]
        self.assertTrue(d["accounting_ok"])
        self.assertEqual(d["funding_schedule"], "DECLARED")
        self.assertEqual(d["accepted_events"], dev["per_symbol"]["BTCUSDT"]["n"])
        val = p.evaluation("validation", ids[0])
        self.assertTrue(val["per_symbol"]["BTCUSDT"]["signal_diagnostics"]["accounting_ok"])
        rec = p.evaluation("portfolio", ids[0])
        self.assertEqual(rec["portfolio_config_version"], "portfolio-validation-v2")
        self.assertEqual(rec["model_version"], "portfolio-multi-v3")   # ADR 0072 (was v2, ADR 0071)
        base = rec["compositions"]["standalone"]["scenarios"]["base"]["result"]
        self.assertEqual(base["signal_accounting"]["status"], "OK")
        self.assertEqual(base["funding_completeness"]["trades_by_status"].keys(), {"OK"})
        self.assertEqual(rec["portfolio_status"], "PASS_PORTFOLIO")
        # only PASS_INDIVIDUAL strategies reach the portfolio stage
        for s in ids[1:]:
            if p.statuses.status(s, ctx) != "VALIDATION_SURVIVOR":
                self.assertIsNone(p.evaluation("portfolio", s))
        # restart: the stored records carry the same diagnostics, nothing re-evaluated
        p2 = self._pipe(prov)
        self.assertEqual(p2.evaluation("dev", ids[0])["per_symbol"]["BTCUSDT"]["signal_diagnostics"], d)
        self.assertEqual(p2.resume_pending_portfolio(), [])
        # _study keeps its list interface for the existing callers
        spec = p.specs.get(ids[0])
        e = prov.symbol("BTCUSDT")
        self.assertIsInstance(p._study(spec, "BTCUSDT", e["bars"], e["funding"], None, 1, 500), list)

    def test_undeclared_schedule_never_passes_the_portfolio(self):
        prov = SyntheticProvider()
        e = prov.symbol("BTCUSDT")
        e.pop("funding_intervals")
        p = self._pipe(prov)
        ids = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        st = p.portfolio_status(ids[0])
        if st is not None:                       # only a survivor has a record
            self.assertNotEqual(st, "PASS_PORTFOLIO")
            self.assertIsNone(p.run_locked_campaign())
        self.assertEqual(prov.locked_loads, 0)


if __name__ == "__main__":
    unittest.main()
