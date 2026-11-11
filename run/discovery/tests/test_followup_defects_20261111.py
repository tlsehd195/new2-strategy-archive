"""ADR 0072: reproduction and regression tests for the defects found after
ADR 0071 (portfolio-multi-v2 / funding-schedule-v1).

* P0-1 the SOURCE order of a funding CSV is checked before any sort; an
  inversion or duplicate inside a file or across monthly files is a data
  error all the way to the pipeline provider (never repaired by sorting);
* P0-2 every signal up to the window end is classified (the evaluator used to
  ``break`` at the first truncated signal and never looked at the rest);
* P1-3 the envelope drawdown (max_drawdown_equity_peak_to_trough) gates the
  verdict with the existing 0.5 account limit (portfolio-validation-v2);
* P1-4 cross margin: an account check with a stale mark of any open position
  is never confirmed (no liquidation, no entry) and the verdict is
  UNDETERMINED_PORTFOLIO.

Deterministic SYNTHETIC data and temp files only: nothing here says anything
about markets.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from data_quality import Bar, funding_quality, load_funding_csv_checked  # noqa: E402
from evaluator import run_event_study  # noqa: E402
from portfolio_multi import JUDGE_KEYS_V2, MultiConfig, judge, simulate_multi  # noqa: E402
from runner import checked_funding_quality, load_funding_window_checked  # noqa: E402
from test_portfolio_core_defects_20261111 import diag, flat, ohlc, run2  # noqa: E402
from test_portfolio_multi_20261110 import M15, T0, bars, fee_cost, tr  # noqa: E402

H = 3_600_000
D0 = 1_609_459_200_000            # 2021-01-01T00:00:00Z
V2_PATH = HERE.parent / "config" / "portfolio_validation_v2.json"
V2 = json.loads(V2_PATH.read_text())
HEADER = "calc_time,funding_interval_hours,last_funding_rate"


def csv_rows(rows, header=True, tail_newline=True):
    body = "\n".join(([HEADER] if header else []) + rows)
    return body + ("\n" if tail_newline else "")


def row(ts, hours="8", rate="0.0001"):
    return f"{ts},{hours},{rate}"


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def write(self, name, text):
        p = self.td / "funding" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def quality_of_file(self, name, text):
        """load_funding_csv_checked -> funding_quality, exactly as the
        manifest / fetch paths call it."""
        p = self.write(name, text)
        pairs, rep = load_funding_csv_checked(p)
        fq = funding_quality(pairs, parse_errors=rep["parse_errors"], torn_tail=rep["torn_tail"],
                             intervals=rep["intervals"],
                             source_order_errors=rep.get("source_order_errors", ()),
                             interval_missing_rows=rep.get("interval_missing_rows", ()))
        return pairs, rep, fq

    def quality_of_window(self, sym="TESTUSDT", lo="2021-01-01", hi="2021-03-31"):
        """load_funding_window_checked -> checked_funding_quality (runner,
        pipeline provider, independent window, locked loads)."""
        pairs, rep = load_funding_window_checked(self.td, sym, lo, hi)
        return pairs, rep, checked_funding_quality(pairs, rep)


# ---------------------------------------------------------------------------
# P0-1 funding CSV source order
# ---------------------------------------------------------------------------
class TestFundingSourceOrderFile(_Tmp):
    NAME = "TESTUSDT-fundingRate-2021-01.csv"

    def test_sorted_file_is_ok(self):
        pairs, rep, fq = self.quality_of_file(self.NAME, csv_rows([row(D0), row(D0 + 8 * H),
                                                                   row(D0 + 16 * H)]))
        self.assertTrue(fq["ok"], fq)
        self.assertEqual(rep["source_order_errors"], [])
        self.assertEqual(fq["status"], "OK")

    def test_reproduction_00_16_08_is_a_data_error_after_the_sort(self):
        # v-ADR0071: the loader sorted first, so the quality check saw 00/08/16 and passed
        pairs, rep, fq = self.quality_of_file(self.NAME, csv_rows([row(D0), row(D0 + 16 * H),
                                                                   row(D0 + 8 * H)]))
        self.assertEqual([t for t, _ in pairs], [D0, D0 + 8 * H, D0 + 16 * H])   # sorted for computation
        self.assertFalse(fq["ok"])
        self.assertEqual(fq["status"], "FUNDING_DATA_INVALID")
        self.assertTrue(any(i.startswith("SOURCE_ORDER_INVALID") for i in fq["issues"]), fq["issues"])
        (e,) = rep["source_order_errors"]
        self.assertEqual((e["file"], e["line"], e["prev_ts"], e["ts"], e["kind"]),
                         (self.NAME, 4, D0 + 16 * H, D0 + 8 * H, "SOURCE_TIME_INVERSION"))
        self.assertIn("FUNDING_DATA_INVALID", e["effect"])

    def test_fully_reversed_16_08_00(self):
        _, rep, fq = self.quality_of_file(self.NAME, csv_rows([row(D0 + 16 * H), row(D0 + 8 * H), row(D0)]))
        self.assertFalse(fq["ok"])
        self.assertEqual([e["line"] for e in rep["source_order_errors"]], [3, 4])
        # each inversion is measured against the previous VALID row
        self.assertEqual([e["prev_ts"] for e in rep["source_order_errors"]], [D0 + 16 * H, D0 + 8 * H])

    def test_duplicate_timestamp(self):
        _, rep, fq = self.quality_of_file(self.NAME, csv_rows([row(D0), row(D0 + 8 * H), row(D0 + 8 * H)]))
        self.assertFalse(fq["ok"])
        self.assertEqual([e["kind"] for e in rep["source_order_errors"]], ["SOURCE_DUPLICATE_TS"])

    def test_parse_error_row_is_not_the_reference_of_the_order_check(self):
        _, rep, fq = self.quality_of_file(self.NAME, csv_rows([row(D0), "garbage,8,x", row(D0 + 8 * H)]))
        self.assertFalse(fq["ok"])                         # the parse error itself blocks
        self.assertEqual(rep["source_order_errors"], [])  # but is no order error
        self.assertEqual(rep["parse_errors"], [3])

    def test_torn_last_row_keeps_its_handling(self):
        _, rep, fq = self.quality_of_file(self.NAME, csv_rows([row(D0), row(D0 + 8 * H), "1609516"],
                                                              tail_newline=False))
        self.assertTrue(rep["torn_tail"])
        self.assertTrue(any(i.startswith("TORN_TAIL") for i in fq["issues"]))
        self.assertEqual(rep["source_order_errors"], [])

    def test_zero_rate_is_a_normal_observation(self):
        pairs, rep, fq = self.quality_of_file(self.NAME, csv_rows([row(D0, rate="0"), row(D0 + 8 * H, rate="0.0")]))
        self.assertTrue(fq["ok"])
        self.assertEqual([r for _, r in pairs], [0.0, 0.0])

    def test_missing_interval_is_reported_and_leaves_the_schedule_unknown(self):
        from funding_schedule import settlement_check
        pairs, rep, fq = self.quality_of_file(self.NAME, csv_rows([row(D0), row(D0 + 8 * H, hours=""),
                                                                   row(D0 + 16 * H)]))
        self.assertEqual(rep["interval_missing_rows"], [3])
        self.assertNotIn(D0 + 8 * H, rep["intervals"])
        self.assertTrue(any("interval" in w for w in fq["warnings"]), fq["warnings"])
        chk = settlement_check(pairs, rep["intervals"], D0, D0 + 16 * H)
        self.assertEqual(chk["status"], "FUNDING_SCHEDULE_UNKNOWN")

    def test_file_sha_follows_the_content(self):
        p = self.write(self.NAME, csv_rows([row(D0), row(D0 + 8 * H)]))
        sha1 = load_funding_csv_checked(p)[1]["sha256"]
        p.write_text(csv_rows([row(D0), row(D0 + 8 * H, rate="0.0002")]), encoding="utf-8")
        sha2 = load_funding_csv_checked(p)[1]["sha256"]
        self.assertNotEqual(sha1, sha2)
        _, rep, _ = self.quality_of_window(hi="2021-01-31")
        self.assertEqual(rep["files"][0]["sha256"], sha2)


class TestFundingSourceOrderWindow(_Tmp):
    JAN, FEB = "TESTUSDT-fundingRate-2021-01.csv", "TESTUSDT-fundingRate-2021-02.csv"
    FEB0 = D0 + 31 * 24 * H

    def _jan(self, extra=()):
        return [row(D0 + k * 8 * H) for k in range(93)] + list(extra)

    def _feb(self, extra=()):
        return list(extra) + [row(self.FEB0 + k * 8 * H) for k in range(84)]

    def test_monthly_files_joined_in_order_is_ok(self):
        self.write(self.JAN, csv_rows(self._jan()))
        self.write(self.FEB, csv_rows(self._feb()))
        pairs, rep, fq = self.quality_of_window()
        self.assertTrue(fq["ok"], fq)
        self.assertEqual(len(pairs), 93 + 84)
        self.assertEqual([f["name"] for f in rep["files"]], [self.JAN, self.FEB])
        self.assertEqual(rep["source_order_errors"], [])

    def test_reproduction_inversion_inside_one_monthly_file_reaches_the_window_quality(self):
        jan = self._jan()
        jan[10], jan[11] = jan[11], jan[10]
        self.write(self.JAN, csv_rows(jan))
        self.write(self.FEB, csv_rows(self._feb()))
        _, rep, fq = self.quality_of_window()
        self.assertFalse(fq["ok"])
        self.assertEqual(fq["status"], "FUNDING_DATA_INVALID")
        (e,) = rep["source_order_errors"]
        self.assertEqual((e["file"], e["line"], e["kind"]), (self.JAN, 13, "SOURCE_TIME_INVERSION"))

    def test_duplicate_timestamp_across_monthly_files(self):
        # the January file also carries Feb 1st 00:00
        self.write(self.JAN, csv_rows(self._jan([row(self.FEB0)])))
        self.write(self.FEB, csv_rows(self._feb()))
        _, rep, fq = self.quality_of_window()
        self.assertFalse(fq["ok"])
        kinds = [e["kind"] for e in rep["source_order_errors"]]
        self.assertEqual(kinds, ["CROSS_FILE_DUPLICATE_TS"])
        self.assertEqual(rep["source_order_errors"][0]["file"], self.FEB)
        self.assertEqual(rep["source_order_errors"][0]["prev_file"], self.JAN)

    def test_reproduction_later_file_filling_an_earlier_gap_is_not_hidden_by_the_sort(self):
        # January misses 15 Jan 08:00; the FEBRUARY file carries it as its first row.
        # Merged and sorted, the series looked complete.
        miss = D0 + (14 * 3 + 1) * 8 * H
        jan = [r for r in self._jan() if not r.startswith(f"{miss},")]
        self.write(self.JAN, csv_rows(jan))
        self.write(self.FEB, csv_rows(self._feb([row(miss)])))
        pairs, rep, fq = self.quality_of_window()
        self.assertIn(miss, [t for t, _ in pairs])
        self.assertFalse(fq["ok"])
        (e,) = rep["source_order_errors"]
        self.assertEqual((e["kind"], e["file"], e["prev_file"]),
                         ("CROSS_FILE_TIME_INVERSION", self.FEB, self.JAN))

    def test_file_internal_error_survives_the_merge_sort(self):
        jan = self._jan()
        jan[0], jan[1] = jan[1], jan[0]
        self.write(self.JAN, csv_rows(jan))
        pairs, rep, fq = self.quality_of_window(hi="2021-01-31")
        self.assertEqual([t for t, _ in pairs], sorted(t for t, _ in pairs))
        self.assertFalse(fq["ok"])


class TestFundingSourceOrderProvider(_Tmp):
    """The whole path: CSV -> load_funding_csv_checked -> load_funding_window_checked
    -> checked_funding_quality -> RunnerDataProvider.symbol() -> pipeline
    data-missing (no FundingIndex, no settlement_check, no portfolio input)."""

    def _klines(self):
        from test_engine import make_bars, write_kline_csv
        write_kline_csv(self.td / "klines" / "TESTUSDT-15m-2021-01.csv",
                        make_bars(n=96 * 5, start_ts=D0, seed=3))

    def _provider(self):
        from pipeline import RunnerDataProvider
        return RunnerDataProvider(self.td)

    def test_provider_accepts_sorted_and_refuses_reordered_funding(self):
        self._klines()
        rows = [row(D0 + k * 8 * H) for k in range(15)]
        self.write("TESTUSDT-fundingRate-2021-01.csv", csv_rows(rows))
        e = self._provider().symbol("TESTUSDT")
        self.assertEqual(e["missing"], [])
        self.assertIsNotNone(e["funding"])
        self.assertEqual(e["funding_quality"]["status"], "OK")
        rows[3], rows[7] = rows[7], rows[3]
        self.write("TESTUSDT-fundingRate-2021-01.csv", csv_rows(rows))
        e = self._provider().symbol("TESTUSDT")
        self.assertIsNone(e["funding"])                    # never handed to the evaluator
        self.assertTrue(any("SOURCE_ORDER_INVALID" in m for m in e["missing"]), e["missing"])
        self.assertEqual(e["funding_quality"]["status"], "FUNDING_DATA_INVALID")
        self.assertEqual(len(e["funding_quality"]["source_order_errors"]), 2)


    def test_pipeline_dev_evaluation_is_blocked_data_and_never_reaches_validation(self):
        from pipeline import EvolutionPipeline, RunnerDataProvider
        from test_engine import make_bars, write_kline_csv
        from test_evolution_spec import RZ, spec
        from test_pipeline import verified_fee_cost
        write_kline_csv(self.td / "klines" / "BTCUSDT-15m-2021-01.csv",
                        make_bars(n=96 * 20, start_ts=D0, seed=5))
        rows = [row(D0 + k * 8 * H) for k in range(60)]
        rows[20], rows[21] = rows[21], rows[20]
        self.write("BTCUSDT-fundingRate-2021-01.csv", csv_rows(rows))
        p = EvolutionPipeline(self.td / "state", RunnerDataProvider(self.td), verified_fee_cost(),
                              symbols=["BTCUSDT"])
        sid, _ = p.register(spec([RZ]), source="test")
        rec = p.dev_evaluate(sid)
        self.assertEqual(rec["status"], "BLOCKED_DATA")
        self.assertTrue(any("SOURCE_ORDER_INVALID" in m for m in rec["missing"]), rec["missing"])
        self.assertEqual(p.statuses.status(sid, p.context_id()), "BLOCKED_DATA")
        self.assertIsNone(p.evaluation("validation", sid))
        self.assertIsNone(p.evaluation("portfolio", sid))


# ---------------------------------------------------------------------------
# P0-2 signals at the window end
# ---------------------------------------------------------------------------
def flat_bars(n, qv=1e9):
    return bars([100.0] * n, qv=qv, t0=0)


def buckets(d):
    return (d["accepted_events"] + d["skipped_overlapped"] + d["truncated_end_of_window"]
            + d["participation_refusals"] + d["cost_data_missing"] + d["dropped_missing_funding"])


class TestWindowEndSignals(unittest.TestCase):
    def test_reproduction_every_bar_signal_horizon_3_counts_all_end_signals(self):
        # decisions t = 1..38 (entry t+1 = 2..39, 40 bars).  Accepted entries 2, 6, .., 34
        # (exit 37); entries 35, 36, 37 overlap it; entries 38 and 39 run past the end.
        # The break stopped at entry 38 and never saw entry 39 (signals_total 37).
        es = run_event_study(flat_bars(40), "A", lambda v: 1, 3, fee_cost(0.0), 1000.0)
        d = es.diagnostics()
        self.assertEqual(d["signals_total"], 38)
        self.assertEqual(d["accepted_events"], 9)
        self.assertEqual(d["skipped_overlapped"], 27)
        self.assertEqual(d["truncated_end_of_window"], 2)
        self.assertEqual(buckets(d), d["signals_total"])
        self.assertTrue(d["accounting_ok"])
        self.assertTrue(all(e.exit_index < 40 for e in es.events))
        j = es.to_json()
        for k in ("signals_total", "skipped_overlapped", "truncated_end_of_window",
                  "participation_refusals", "cost_data_missing", "dropped_missing_funding"):
            self.assertEqual(j[k], d[k])
        self.assertEqual(j["n_events"], d["accepted_events"])

    def test_several_truncated_signals_after_the_last_trade(self):
        # signals only at decisions 30..38 with horizon 5: entry 31 accepted (exit 36),
        # 32..36 overlap, 37..39 truncated (exit >= 40)
        es = run_event_study(flat_bars(40), "A", lambda v: 1 if len(v) - 1 >= 30 else 0, 5,
                             fee_cost(0.0), 1000.0)
        d = es.diagnostics()
        self.assertEqual((d["signals_total"], d["accepted_events"], d["skipped_overlapped"],
                          d["truncated_end_of_window"]), (9, 1, 5, 3))
        self.assertTrue(d["accounting_ok"])

    def test_signal_right_after_the_last_trade_exit_is_classified_by_its_own_window(self):
        # trade entry 30 exit 33 (h=3); decision 33 -> entry 34 is after the exit: not an overlap.
        # entry 34 exit 37 accepted; decision 37 -> entry 38, exit 41 >= 40: truncated
        sig = {29, 32, 33, 37}
        es = run_event_study(flat_bars(40), "A", lambda v: 1 if len(v) - 1 in sig else 0, 3,
                             fee_cost(0.0), 1000.0)
        d = es.diagnostics()
        self.assertEqual([(e.entry_index, e.exit_index) for e in es.events], [(30, 33), (34, 37)])
        self.assertEqual((d["skipped_overlapped"], d["truncated_end_of_window"]), (1, 1))
        self.assertEqual(d["signals_total"], 4)

    def test_truncated_signal_does_not_move_the_overlap_reference(self):
        # an accepted trade 20->23 and only end-of-window signals later: a truncated
        # signal is not a trade, so the next one is truncated, not "overlapped"
        sig = {19, 37, 38}
        es = run_event_study(flat_bars(40), "A", lambda v: 1 if len(v) - 1 in sig else 0, 3,
                             fee_cost(0.0), 1000.0)
        d = es.diagnostics()
        self.assertEqual((d["accepted_events"], d["skipped_overlapped"], d["truncated_end_of_window"]),
                         (1, 0, 2))

    def test_exit_rule_returning_none_does_not_stop_the_scan(self):
        calls = []

        def rule(bars_, entry_i, side, horizon):
            calls.append(entry_i)
            if entry_i in (10, 11):
                return None                      # e.g. data insufficient for the rule
            return entry_i + horizon, bars_[entry_i + horizon].open, "time"

        es = run_event_study(flat_bars(40), "A", lambda v: 1 if len(v) - 1 in (9, 10, 20) else 0, 3,
                             fee_cost(0.0), 1000.0, exit_rule=rule)
        d = es.diagnostics()
        # the v-ADR0071 break stopped at entry 10 and never evaluated entry 21
        self.assertEqual([e.entry_index for e in es.events], [21])
        self.assertEqual((d["signals_total"], d["truncated_end_of_window"]), (3, 2))
        self.assertTrue(d["accounting_ok"])
        self.assertEqual(calls, [10, 11, 21])

    def test_stop_exit_rule_near_the_end(self):
        # spec stop rule: entries whose full horizon reaches past the data are truncated
        # BEFORE the rule is asked (no outcome-dependent inclusion of an early stop)
        from strategy_spec import SPEC_SCHEMA, stop_exit_rule
        b = bars([100.0] * 36 + [80.0, 80.0, 80.0, 80.0], t0=0)
        spec = {"schema": SPEC_SCHEMA, "direction": "long", "symbols": ["BTCUSDT"],
                "entry": {"logic": "all", "conditions": [
                    {"feature": "ret_z", "params": {"lookback": 8}, "op": "lt", "value": -1.5}]},
                "filters": [], "exit": {"horizon": 6, "stop_loss_bp": 100.0}}
        rule = stop_exit_rule(spec, "stop_price")
        es = run_event_study(b, "A", lambda v: 1 if len(v) - 1 >= 31 else 0, 6, fee_cost(0.0), 1000.0,
                             exit_rule=rule)
        d = es.diagnostics()
        # entry 32: stop touched in bar 35 (low 80) -> accepted (32, 35); entries 33..35
        # overlap; entries 36..39 truncated (entry + 6 >= 40) although bar 35's drop is
        # visible to them: inclusion never depends on whether a stop happens to fire
        self.assertEqual([(e.entry_index, e.exit_index) for e in es.events], [(32, 35)])
        self.assertEqual((d["signals_total"], d["skipped_overlapped"], d["truncated_end_of_window"]),
                         (8, 3, 4))
        self.assertTrue(d["accounting_ok"])

    def test_refusals_and_missing_funding_unchanged_and_one_bucket_each(self):
        qv = [1e9] * 40
        qv[9] = 0.0
        b = [Bar(ts=x.ts, open=x.open, high=x.high, low=x.low, close=x.close, volume=1.0,
                 quote_volume=qv[i], taker_buy_volume=0.5) for i, x in enumerate(flat_bars(40))]
        es = run_event_study(b, "A", lambda v: 1 if len(v) - 1 in (9, 20, 37, 38) else 0, 2,
                             fee_cost(0.0), 1000.0, funding=[(0, 0.0), (21 * M15, 0.0)])
        d = es.diagnostics()
        # decision 9 -> entry 10 priced on bar 9 (no traded value); decision 20 -> exit 23
        # after the last settlement (funding coverage); decisions 37, 38 -> past the end
        self.assertEqual((d["cost_data_missing"], d["dropped_missing_funding"], d["truncated_end_of_window"],
                          d["accepted_events"]), (1, 1, 2, 0))
        self.assertEqual(buckets(d), d["signals_total"])
        self.assertTrue(d["accounting_ok"])

    def test_accepted_events_and_returns_unchanged_by_the_fix(self):
        # the fix only adds classification after the window end: accepted trades and their
        # returns are the same as an evaluation that stops at the first truncated signal
        import random
        rng = random.Random(7)
        px = [100.0]
        for _ in range(299):
            px.append(px[-1] * (1 + rng.gauss(0, 0.003)))
        b = bars(px, t0=0)
        sig = lambda v: (1 if v[len(v) - 1].close > v[len(v) - 2].close else -1) if len(v) % 3 == 0 else 0  # noqa: E731
        es = run_event_study(b, "A", sig, 4, fee_cost(0.0004), 1000.0)
        ref = []
        last = -1
        for t in range(1, 299):
            s = sig(_prefix(b, t + 1))
            if s not in (1, -1):
                continue
            e, x = t + 1, t + 5
            if e <= last:
                continue
            if x >= 300:
                break
            ref.append((e, x, s))
            last = x
        self.assertEqual([(e.entry_index, e.exit_index, e.side) for e in es.events], ref)


def _prefix(b, end):
    from evaluator import PrefixView
    return PrefixView(b, end)


# ---------------------------------------------------------------------------
# P1-3 envelope drawdown in the verdict
# ---------------------------------------------------------------------------
def envelope_case():
    """10000 capital, long 10000 notional at 100; the next bar has high 130,
    low 60 and closes at 100 (OHLC: the order of high and low is unknown)."""
    b = {"A": ohlc([flat(100), flat(100), (100, 130, 60, 100), flat(100), flat(100)])}
    return run2(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cost=fee_cost(0.0),
                signal_diagnostics={"s1/A": diag("A", 1)})


def checks_for(scenario="base", **over):
    c = dict(V2["scenarios"][scenario]["checks"])
    c.update(min_trades=1, min_days=0)
    c.pop("final_equity_gt_initial", None)      # the case ends flat by construction
    c.update(over)
    return c


class TestEnvelopeDrawdownGate(unittest.TestCase):
    def test_three_drawdowns_of_the_case(self):
        m = envelope_case()["metrics"]
        self.assertAlmostEqual(m["max_drawdown_close"]["value"], 0.0, 9)
        self.assertAlmostEqual(m["max_drawdown_intrabar_worst_case"]["value"], 0.4, 9)
        self.assertAlmostEqual(m["max_drawdown_equity_peak_to_trough"]["value"], 7_000 / 13_000, 9)

    def test_reproduction_envelope_alone_above_the_limit_fails(self):
        r = envelope_case()
        c = checks_for()
        self.assertIn("max_drawdown_equity_peak_to_trough_lt", c)
        self.assertEqual(c["max_drawdown_equity_peak_to_trough_lt"], 0.5)
        st, why = judge(r, c)
        self.assertEqual(st, "FAIL_PORTFOLIO")
        self.assertEqual(len(why), 1)
        self.assertIn("max_drawdown_equity_peak_to_trough", why[0])
        # the same result passes without the envelope key: the hole ADR 0071 left
        c.pop("max_drawdown_equity_peak_to_trough_lt")
        self.assertEqual(judge(r, c)[0], "PASS_PORTFOLIO")

    def test_cross_3x_scenario_gates_the_envelope_too(self):
        c = checks_for("stress_cross_3x")
        self.assertEqual(c["max_drawdown_equity_peak_to_trough_lt"], 0.5)
        self.assertEqual(judge(envelope_case(), c)[0], "FAIL_PORTFOLIO")

    def test_all_three_below_passes(self):
        b = {"A": ohlc([flat(100), flat(100), (100, 110, 95, 102), flat(102), flat(102)])}
        r = run2(b, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cost=fee_cost(0.0),
                 signal_diagnostics={"s1/A": diag("A", 1)})
        self.assertEqual(judge(r, checks_for(final_equity_gt_initial=True)), ("PASS_PORTFOLIO", []))

    def _with(self, r, metric, val):
        r = json.loads(json.dumps(r, default=str))
        r["metrics"][metric] = val
        return r

    def test_each_drawdown_alone_fails(self):
        base = {"A": ohlc([flat(100), flat(100), (100, 110, 95, 102), flat(102), flat(102)])}
        r = run2(base, [tr("s1", "A", 1, 0, 1, 3)], ["s1"], cost=fee_cost(0.0),
                 signal_diagnostics={"s1/A": diag("A", 1)})
        for metric in ("max_drawdown_close", "max_drawdown_intrabar_worst_case",
                       "max_drawdown_equity_peak_to_trough"):
            st, why = judge(self._with(r, metric, {"value": 0.6, "status": "OK"}), checks_for())
            self.assertEqual(st, "FAIL_PORTFOLIO", metric)
            self.assertTrue(any(metric in w for w in why), why)

    def test_envelope_not_ok_or_not_finite_is_undetermined(self):
        r = envelope_case()
        for val in ({"value": 0.1, "status": "UNDETERMINED", "reason": "stale"},
                    {"value": None, "status": "NOT_COMPUTABLE", "reason": "non-finite equity"},
                    {"value": float("nan"), "status": "OK"},
                    {"value": float("inf"), "status": "OK"},
                    {"value": None, "status": "OK"},
                    None):
            rr = self._with(r, "max_drawdown_equity_peak_to_trough", val)
            if val is not None and isinstance(val.get("value"), float) and not math.isfinite(val["value"]):
                rr["metrics"]["max_drawdown_equity_peak_to_trough"]["value"] = val["value"]
            self.assertEqual(judge(rr, checks_for())[0], "UNDETERMINED_PORTFOLIO", val)

    def test_config_key_known_and_required_for_gating_v2_scenarios(self):
        from pipeline import load_portfolio_config
        self.assertIn("max_drawdown_equity_peak_to_trough_lt", JUDGE_KEYS_V2)
        cfg, _ = load_portfolio_config()
        for name in ("base", "stress_cross_3x", "stress_partial_latency"):
            self.assertEqual(cfg["scenarios"][name]["checks"]["max_drawdown_equity_peak_to_trough_lt"], 0.5)
        self.assertFalse(cfg["scenarios"]["stress_partial_latency"]["gating"])
        bad = json.loads(V2_PATH.read_text())
        del bad["scenarios"]["base"]["checks"]["max_drawdown_equity_peak_to_trough_lt"]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, "max_drawdown_equity_peak_to_trough_lt"):
                load_portfolio_config(p)

    def test_v1_config_unchanged_and_still_loads(self):
        from pipeline import load_portfolio_config
        v1 = HERE.parent / "config" / "portfolio_validation_v1.json"
        cfg, h = load_portfolio_config(v1)
        self.assertNotIn("max_drawdown_equity_peak_to_trough_lt", json.dumps(cfg))
        self.assertEqual(cfg["version"], "portfolio-validation-v1")


# ---------------------------------------------------------------------------
# P1-4 cross margin with stale marks
# ---------------------------------------------------------------------------
CROSS = MultiConfig(leverage=3.0, margin_mode="cross", max_gross_exposure=3.0)


def gap(rows_a, rows_b, drop_b):
    a = ohlc(rows_a)
    b = [x for i, x in enumerate(ohlc(rows_b)) if i not in drop_b]
    return {"A": a, "B": b}


class TestCrossStaleMarks(unittest.TestCase):
    def test_all_symbols_have_the_bar_complete_and_unchanged(self):
        g = [flat(100), flat(100), flat(60), flat(60)]
        r = run2({"A": ohlc(g), "B": ohlc(g)}, [tr("s1", "A", 1, 0, 1, 2), tr("s2", "B", 1, 0, 1, 2)],
                 ["s1", "s2"], cfg=CROSS, cost=fee_cost(0.0))
        self.assertEqual((r["account_liquidations"], r["liquidation_events"]), (1, 2))
        self.assertEqual(r["cross_account_valuation"]["status"], "COMPLETE")
        self.assertEqual(r["cross_account_valuation"]["checks_deferred"], 0)

    def test_reproduction_stale_mark_makes_a_liquidation_that_is_not_observed(self):
        # A gaps to 30 at bar 2; B has NO bar 2 (its last close 100 is stale).
        # Equity with B at 100: 10000 - 150*70 = -500 <= maintenance -> v2 liquidated
        # the account on a mixed-time valuation; B's price at bar 2 is unknown.
        rows = [flat(100), flat(100), flat(30), flat(30), flat(30)]
        b = gap(rows, [flat(100)] * 5, drop_b={2})
        r = run2(b, [tr("s1", "A", 1, 0, 1, 3), tr("s2", "B", 1, 0, 1, 3)], ["s1", "s2"], cfg=CROSS,
                 cost=fee_cost(0.0), signal_diagnostics={"s1/A": diag("A", 1), "s2/B": diag("B", 1)})
        stale_liq = [x for x in r["liquidations"] if x["ts"] == T0 + 2 * M15]
        self.assertEqual(stale_liq, [])                       # nothing confirmed on a stale mark
        v = r["cross_account_valuation"]
        self.assertEqual(v["status"], "INCOMPLETE")
        self.assertGreaterEqual(v["checks_deferred"], 1)
        self.assertGreaterEqual(v["would_liquidate_with_stale_marks"], 1)
        ev = v["events"][0]
        self.assertEqual((ev["ts"], ev["phase"], ev["stale_symbols"]), (T0 + 2 * M15, "open", ["B"]))
        # once B is back (bar 3) the account is checked on observed prices again: A at 30, B at 100
        self.assertEqual({x["ts"] for x in r["liquidations"]}, {T0 + 3 * M15})
        st, why = judge(r, checks_for("stress_cross_3x"))
        self.assertEqual(st, "UNDETERMINED_PORTFOLIO")
        self.assertTrue(any("cross" in w for w in why), why)

    def test_stale_mark_that_would_hide_a_liquidation_is_undetermined_not_pass(self):
        # B is missing at bar 2 (its true price there is unknown); A is flat. With B
        # stale at 100 the account looks fine; the verdict must not be PASS
        b = gap([flat(100)] * 6, [flat(100)] * 6, drop_b={2})
        r = run2(b, [tr("s1", "A", 1, 0, 1, 4), tr("s2", "B", 1, 0, 1, 4)], ["s1", "s2"], cfg=CROSS,
                 cost=fee_cost(0.0), signal_diagnostics={"s1/A": diag("A", 1), "s2/B": diag("B", 1)})
        self.assertEqual(r["liquidation_events"], 0)
        self.assertEqual(r["cross_account_valuation"]["status"], "INCOMPLETE")
        self.assertEqual(r["cross_account_valuation"]["would_liquidate_with_stale_marks"], 0)
        self.assertEqual(judge(r, checks_for("stress_cross_3x"))[0], "UNDETERMINED_PORTFOLIO")

    def test_intrabar_check_with_a_stale_symbol_is_deferred(self):
        # A's bar 2 has a deep low (30) but opens at 100; B has no bar 2.
        rows_a = [flat(100), flat(100), (100, 100, 30, 100), flat(100), flat(100)]
        b = gap(rows_a, [flat(100)] * 5, drop_b={2})
        r = run2(b, [tr("s1", "A", 1, 0, 1, 3), tr("s2", "B", 1, 0, 1, 3)], ["s1", "s2"], cfg=CROSS,
                 cost=fee_cost(0.0))
        self.assertEqual(r["liquidation_events"], 0)
        phases = {e["phase"] for e in r["cross_account_valuation"]["events"]}
        self.assertIn("intrabar", phases)
        self.assertGreaterEqual(r["cross_account_valuation"]["would_liquidate_with_stale_marks"], 1)
        self.assertAlmostEqual(r["final_equity"], 10_000.0, 6)   # no loss booked from a mixed-time mark

    def test_new_entry_at_an_incomplete_timestamp_is_refused_and_accounted(self):
        # s1 holds B; at bar 2 B has no bar while s2 wants to enter A
        b = gap([flat(100)] * 6, [flat(100)] * 6, drop_b={2})
        trades = [tr("s1", "B", 1, 0, 1, 4), tr("s2", "A", 1, 1, 2, 4)]
        r = run2(b, trades, ["s1", "s2"], cfg=CROSS, cost=fee_cost(0.0),
                 signal_diagnostics={"s1/B": diag("B", 1), "s2/A": diag("A", 1)})
        self.assertEqual(r["skipped"]["account_valuation_incomplete"], 1)
        self.assertEqual(r["metrics"]["strategy_contribution"]["s2"]["entries"], 0)
        acc = r["signal_accounting"]
        self.assertTrue(acc["identity_ok"], acc)
        self.assertEqual(acc["by_class"]["data_missing"], 1)
        self.assertEqual(r["cross_account_valuation"]["entries_blocked"], 1)
        self.assertNotEqual(judge(r, checks_for("stress_cross_3x"))[0], "PASS_PORTFOLIO")

    def test_valuation_resumes_when_every_symbol_is_back(self):
        b = gap([flat(100)] * 7, [flat(100)] * 7, drop_b={2})
        trades = [tr("s1", "B", 1, 0, 1, 5), tr("s2", "A", 1, 2, 3, 5)]
        r = run2(b, trades, ["s1", "s2"], cfg=CROSS, cost=fee_cost(0.0))
        self.assertEqual(r["skipped"]["account_valuation_incomplete"], 0)   # bar 3 is complete
        self.assertEqual(r["metrics"]["strategy_contribution"]["s2"]["entries"], 1)
        self.assertEqual([e["ts"] for e in r["cross_account_valuation"]["events"]],
                         [T0 + 2 * M15, T0 + 2 * M15])                       # open + intrabar of bar 2 only

    def test_no_open_position_is_never_incomplete(self):
        b = gap([flat(100)] * 6, [flat(100)] * 6, drop_b={2})
        r = run2(b, [tr("s1", "A", 1, 2, 3, 5)], ["s1"], cfg=CROSS, cost=fee_cost(0.0))
        self.assertEqual(r["cross_account_valuation"]["status"], "COMPLETE")
        self.assertEqual(r["skipped"]["account_valuation_incomplete"], 0)

    def test_isolated_keeps_its_per_position_valuation(self):
        # isolated 3x: liq price 67.17; A gaps to 30 at bar 2 while B has no bar 2:
        # A alone is liquidated on its own observed open, B is untouched (unchanged rule)
        cfg = MultiConfig(leverage=3.0, max_gross_exposure=3.0)
        b2 = gap([flat(100), flat(100), flat(30), flat(30), flat(30)], [flat(100)] * 5, drop_b={2})
        r2 = run2(b2, [tr("s1", "A", 1, 0, 1, 3), tr("s2", "B", 1, 0, 1, 3)], ["s1", "s2"], cfg=cfg,
                  cost=fee_cost(0.0))
        self.assertEqual([(x["strategy"], x["trigger"]) for x in r2["liquidations"]],
                         [("s1", "liquidation_open_gap")])
        self.assertEqual(r2["cross_account_valuation"]["status"], "NOT_APPLICABLE")
        self.assertEqual(r2["skipped"]["account_valuation_incomplete"], 0)

    def test_simulate_multi_signature_unchanged(self):
        # direct call without the run helpers still works (no new required argument)
        g = [flat(100)] * 4
        r = simulate_multi({"A": ohlc(g)}, [tr("s1", "A", 1, 0, 1, 2)], {}, fee_cost(0.0), CROSS,
                           members=["s1"])
        self.assertEqual(r["cross_account_valuation"]["status"], "COMPLETE")


if __name__ == "__main__":
    unittest.main()
