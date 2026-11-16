"""ADR 0081: DSR of a DEV series whose symbols' DEV windows do not touch.

Reproduction (found in ADR 0080 §6.1): with the real locked TEST windows
(BTCUSDT 2024-03-25..06-18, ETHUSDT 2026-04-28..08-31) a BTCUSDT+ETHUSDT
strategy whose ETHUSDT selection data starts after the BTCUSDT lock (as it must
for the ETH data not to cover the BTC lock) has two DEV windows with calendar
days between them.  ``EvolutionPipeline._dsr`` pooled the per-symbol series
over ``range(first day, last day)`` and indexed the dict with every day of that
range, so it raised KeyError and stopped the validation batch.

Decision (see ADR 0081): a day outside every symbol's DEV window is NOT
OBSERVED — it is not a zero return (ADR 0006 DM alignment, ``variation_report``).
The DSR (Bailey & López de Prado 2014) is a statistic of the T observed
returns; T, mean, standard deviation, skewness and kurtosis are defined on the
observed days whatever lies between them.  So the gapped case is computed on
the observed days only (no zero fill, no interpolation); a series whose
observed days are too few, or without variation, stays not computable with an
explicit reason.  Synthetic data only; no network, no market data, no
locked / validation / IW1 window is opened.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider, verified_fee_cost  # noqa: E402

SYMS = ("BTCUSDT", "ETHUSDT")
#: ETH selection data starts the day after the BTCUSDT lock ends (real windows)
DEV_START = {"BTCUSDT": "2023-07-01", "ETHUSDT": "2024-06-19"}


def _sr_stats(x):
    n = len(x)
    mu = sum(x) / n
    sd = (sum((v - mu) ** 2 for v in x) / (n - 1)) ** 0.5
    return n, mu / sd


class TestGappedDevSeriesFullPath(PipelineCase):
    """The real locked windows, two symbols, no patching of LOCKED_TEST_PERIODS."""

    @classmethod
    def setUpClass(cls):
        cls.prov = SyntheticProvider(symbols=SYMS, dev_start=DEV_START)

    def build(self):
        from pipeline import EvolutionPipeline
        p = EvolutionPipeline(self.td / "state", self.prov, verified_fee_cost(), symbols=list(SYMS))
        sids = self.register_all(p, [dict(s, symbols=list(SYMS)) for s in [EDGE] + NOISE[:7]])
        return p, sids

    def series_days(self, p, sid):
        from engine_lib import iter_jsonl
        ctx = p.context_id()
        row = [r for r in iter_jsonl(p.series_path)
               if r["context_id"] == ctx and r["strategy_id"] == sid][-1]
        return {sym: (d0, vals) for sym, (d0, vals) in row["series"].items()}

    def test_fixture_has_a_calendar_gap_between_the_symbols(self):
        p, sids = self.build()
        s = self.series_days(p, sids[0])
        (b0, bv), (e0, ev) = s["BTCUSDT"], s["ETHUSDT"]
        self.assertGreater(e0, b0 + len(bv), "ETH DEV must start after BTC DEV ends")
        self.assertGreater(len(bv), 30)
        self.assertGreater(len(ev), 30)

    def test_dsr_is_computed_on_the_observed_days_only(self):
        p, sids = self.build()
        d = p._dsr(sids[0])                       # main: KeyError
        s = self.series_days(p, sids[0])
        x = list(s["BTCUSDT"][1]) + list(s["ETHUSDT"][1])   # disjoint: concatenated
        n, sr = _sr_stats(x)
        self.assertTrue(d["computable"])
        self.assertEqual(d["n_days"], n)
        self.assertAlmostEqual(d["sr_daily"], sr, places=12)
        gap = s["ETHUSDT"][0] - (s["BTCUSDT"][0] + len(s["BTCUSDT"][1]))
        self.assertEqual(d["dev_calendar"]["unobserved_days"], gap)
        self.assertEqual(d["dev_calendar"]["segments"], 2)
        # never the zero-filled span
        span = s["ETHUSDT"][0] + len(s["ETHUSDT"][1]) - s["BTCUSDT"][0]
        self.assertEqual(d["dev_calendar"]["span_days"], span)
        self.assertLess(d["n_days"], span)

    def test_validation_batch_runs_through_robustness(self):
        p, sids = self.build()
        p.run_validation()                        # main: KeyError inside robustness
        rob = p.evaluation("robustness", sids[0])
        self.assertIsNotNone(rob, "the planted edge reaches robustness")
        dsr = rob["checks"]["dsr"]
        self.assertTrue(dsr["computable"])
        self.assertNotIn("dsr", rob["not_computable"])
        self.assertEqual(dsr["dev_calendar"]["segments"], 2)
        self.assertGreater(dsr["dev_calendar"]["unobserved_days"], 0)
        self.assertEqual(dsr["n_days"], dsr["dev_calendar"]["observed_days"])


class TestPooledSeries(unittest.TestCase):
    """The pooling rule itself, on hand-made series."""

    def pool(self, series):
        from pipeline import pooled_dev_days
        return pooled_dev_days(series)

    def test_overlapping_days_are_summed_and_gap_days_are_left_out(self):
        x, cal = self.pool({"A": [10, [1.0, 2.0, 3.0]], "B": [12, [10.0, 20.0]],
                            "C": [20, [5.0, 6.0]]})
        # days 10,11,12(+B),13(B) ; 14..19 unobserved ; 20,21
        self.assertEqual(x, [1.0, 2.0, 13.0, 20.0, 5.0, 6.0])
        self.assertEqual(cal, {"observed_days": 6, "span_days": 12, "unobserved_days": 6,
                               "segments": 2, "first_day": 10, "last_day": 21})

    def test_contiguous_series_unchanged(self):
        x, cal = self.pool({"A": [0, [1.0, -1.0, 2.0]], "B": [3, [4.0]]})
        self.assertEqual(x, [1.0, -1.0, 2.0, 4.0])
        self.assertEqual((cal["unobserved_days"], cal["segments"]), (0, 1))

    def test_a_flat_day_inside_a_window_is_observed(self):
        # 0.0 inside a DEV window = no exit that day (observed, flat), kept
        x, cal = self.pool({"A": [0, [0.0, 0.0, 1.0]]})
        self.assertEqual(x, [0.0, 0.0, 1.0])
        self.assertEqual(cal["observed_days"], 3)

    def test_contiguous_or_overlapping_windows_give_the_pre_adr_0081_series(self):
        import random

        def before(series):                       # the pooling of 2d6616a, verbatim
            acc = {}
            for _sym, (d0, vals) in series.items():
                for i, v in enumerate(vals):
                    acc[d0 + i] = acc.get(d0 + i, 0.0) + v
            days = sorted(acc)
            return [acc[d] for d in range(days[0], days[-1] + 1)] if days else []
        rng = random.Random(7)
        for _ in range(200):
            a0, na = rng.randrange(0, 50), rng.randrange(1, 60)
            b0 = rng.randrange(max(0, a0 - 40), a0 + na + 1)      # overlaps or touches A
            nb = rng.randrange(max(1, a0 - b0), 60 + max(1, a0 - b0))   # B reaches A
            ser = {"A": [a0, [rng.gauss(0, 1) for _ in range(na)]],
                   "B": [b0, [rng.gauss(0, 1) for _ in range(nb)]]}
            x, cal = self.pool(ser)
            self.assertEqual(x, before(ser))
            self.assertEqual(cal["unobserved_days"], 0)

    def test_empty(self):
        x, cal = self.pool({})
        self.assertEqual(x, [])
        self.assertEqual(cal["observed_days"], 0)

    def test_non_finite_value_is_refused(self):
        from pipeline import DevSeriesError
        with self.assertRaises(DevSeriesError):
            self.pool({"A": [0, [1.0, float("nan")]]})


class TestNotComputableReasons(PipelineCase):
    """Fail-closed cases carry an explicit reason (never a pass, never zeros)."""

    def fake(self, series_by_sid):
        p = self.pipe(SyntheticProvider())
        rows = {s: {"strategy_id": s, "series": ser} for s, ser in series_by_sid.items()}
        return p, mock.patch.object(type(p), "_trial_series", lambda self_: rows)

    @staticmethod
    def wave(n, seed):
        return [math.sin(seed * 1.7 + i * 0.9) + 0.01 * seed for i in range(n)]

    def test_too_few_observed_days_across_a_gap(self):
        # 20 + 20 observed days with 300 unobserved between them: 40 >= 30 -> computable;
        # 10 + 10 -> 20 < 30 -> not computable, reason given (a zero fill would have
        # made it 320 "days")
        p, patch = self.fake({"s1": {"A": [0, self.wave(10, 1)], "B": [310, self.wave(10, 2)]},
                              "s2": {"A": [0, self.wave(40, 3)]},
                              "s3": {"A": [0, self.wave(40, 4)]}})
        with patch:
            d = p._dsr("s1")
        self.assertFalse(d["computable"])
        self.assertEqual(d["reason"], "DEV_SERIES_TOO_SHORT")
        self.assertEqual(d["n_days"], 20)
        p, patch = self.fake({"s1": {"A": [0, self.wave(20, 1)], "B": [320, self.wave(20, 2)]},
                              "s2": {"A": [0, self.wave(40, 3)]},
                              "s3": {"A": [0, self.wave(40, 4)]}})
        with patch:
            d = p._dsr("s1")
        self.assertTrue(d["computable"])
        self.assertEqual(d["n_days"], 40)
        self.assertEqual(d["dev_calendar"]["unobserved_days"], 300)

    def test_no_variation(self):
        p, patch = self.fake({"s1": {"A": [0, [0.0] * 40]},
                              "s2": {"A": [0, self.wave(40, 3)]},
                              "s3": {"A": [0, self.wave(40, 4)]}})
        with patch:
            d = p._dsr("s1")
        self.assertFalse(d["computable"])
        self.assertEqual(d["reason"], "DEV_SERIES_NO_VARIATION")

    def test_too_few_trials(self):
        p, patch = self.fake({"s1": {"A": [0, self.wave(40, 1)]}})
        with patch:
            d = p._dsr("s1")
        self.assertFalse(d["computable"])
        self.assertEqual(d["reason"], "TOO_FEW_COMPUTABLE_TRIALS")

    def test_non_finite_series_is_not_computable_with_reason(self):
        p, patch = self.fake({"s1": {"A": [0, self.wave(39, 1) + [float("inf")]]},
                              "s2": {"A": [0, self.wave(40, 3)]},
                              "s3": {"A": [0, self.wave(40, 4)]}})
        with patch:
            d = p._dsr("s1")
        self.assertFalse(d["computable"])
        self.assertEqual(d["reason"], "DEV_SERIES_INVALID")


if __name__ == "__main__":
    unittest.main()
