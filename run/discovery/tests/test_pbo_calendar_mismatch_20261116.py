"""ADR 0083: PBO (CSCV) of a trial set whose DEV calendars differ.

Reproduction (found in ADR 0081 §6.1): ``EvolutionPipeline._pbo`` built the
CSCV T x N matrix on the UNION of the trials' DEV days and filled every day a
trial did not observe with ``.get(d, 0.0)``.  When strategies with different
symbol sets (so different DEV windows) share an experiment context, a trial
was given zero returns on days it never observed.  ADR 0006: a day outside a
series' DEV window is not observed, it is not a zero return.  The zeros move
the in-sample / out-of-sample means of the CSCV halves, so the PBO value (a
gate, ``max_pbo``) was computed on made-up data and could pass wrongly.

Decision (user, 2026-11-16, "추천으로"): no zero fill.  CSCV ranks all N
trials on the SAME T periods (Bailey et al. 2017); when the trials' observed
DEV days are not one common calendar the matrix does not exist, so PBO is NOT
COMPUTABLE with an explicit reason (fail-closed: the PBO check fails, the
strategy is BLOCKED_STATISTICS, never a pass).  It is not narrowed to the
common days either: choosing a sub-window after the fact would change what the
statistic measures (a criterion change, out of scope).  When every trial has
the same observed days the matrix and the value are exactly the pre-ADR ones.
Synthetic data only; no network, no market data, no locked / validation / IW1
window is opened.
"""
from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider, verified_fee_cost  # noqa: E402

SYMS = ("BTCUSDT", "ETHUSDT")
#: ETH selection data starts the day after the BTCUSDT lock ends (real windows, ADR 0081)
DEV_START = {"BTCUSDT": "2023-07-01", "ETHUSDT": "2024-06-19"}


def wave(n, seed, d=0.0):
    return [math.sin(seed * 1.7 + i * 0.9) + 0.01 * seed + d for i in range(n)]


def pbo_before(latest, n_partitions=16):
    """``_pbo`` of a441ba7, verbatim (union of days, unobserved day -> 0.0)."""
    from engine_lib import pbo_cscv
    pooled = {}
    for sid, r in latest.items():
        acc = {}
        for _sym, (d0, vals) in r["series"].items():
            for i, v in enumerate(vals):
                acc[d0 + i] = acc.get(d0 + i, 0.0) + v
        pooled[sid] = acc
    days = sorted({d for a in pooled.values() for d in a})
    sids = sorted(pooled)
    mat = [[pooled[s].get(d, 0.0) for s in sids] for d in days]
    return pbo_cscv(mat, n_partitions=n_partitions), len(days)


class TestPboTrialCalendars(PipelineCase):
    """``_pbo`` on hand-made trial sets (``_trial_series`` patched)."""

    def fake(self, series_by_sid):
        p = self.pipe(SyntheticProvider())
        rows = {s: {"strategy_id": s, "series": ser} for s, ser in series_by_sid.items()}
        return p, rows, mock.patch.object(type(p), "_trial_series", lambda self_: rows)

    def test_different_calendars_are_not_zero_filled(self):
        # 4 trials on symbol A days 0..199, 4 on symbol B days 100..299: a441ba7
        # computed a PBO on 300 "days", 100 of them zeros for every trial
        ser = {f"a{k}": {"A": [0, wave(200, k)]} for k in range(4)}
        ser.update({f"b{k}": {"B": [100, wave(200, 10 + k)]} for k in range(4)})
        p, rows, patch = self.fake(ser)
        (old, _n), old_days = pbo_before(rows)
        self.assertTrue(math.isfinite(old))            # a441ba7: a value, on zeros
        self.assertEqual(old_days, 300)
        with patch:
            r = p._pbo()
        self.assertFalse(r["computable"])
        self.assertIsNone(r.get("pbo"))
        self.assertEqual(r["reason"], "TRIAL_CALENDARS_DIFFER")
        self.assertEqual(r["trials"], 8)
        self.assertEqual(r["calendars"], 2)
        self.assertEqual(r["days_union"], 300)
        self.assertEqual(r["days_common"], 100)
        self.assertEqual(sum(g["trials"] for g in r["calendar_groups"]), 8)

    def test_zero_fill_can_turn_an_overfit_set_into_a_pass(self):
        # 8 trials observed on days 0..159: PBO 0.574 (fails max_pbo 0.05 and 0.5).
        # One more trial observed on days 0..319 gave the other 8 160 zero-filled
        # days in a441ba7, and the same 8 trials then showed PBO 0.0 (a pass).
        ser = {}
        for k in range(8):
            ser[f"s{k}"] = {"A": [0, wave(160, k, d=0.002 * k)]}
        p0, rows0, patch0 = self.fake(ser)
        with patch0:
            r0 = p0._pbo()
        self.assertTrue(r0["computable"])
        self.assertAlmostEqual(r0["pbo"], 0.5741258741258741, places=12)
        self.assertGreater(r0["pbo"], 0.5)
        ser["late"] = {"A": [0, wave(160, 99)], "B": [160, wave(160, 98)]}
        p1, rows1, patch1 = self.fake(ser)
        (old, _n), old_days = pbo_before(rows1)
        self.assertEqual(old_days, 320)
        self.assertEqual(old, 0.0)                     # a441ba7: a wrong pass
        with patch1:
            r1 = p1._pbo()
        self.assertFalse(r1["computable"])
        self.assertEqual(r1["reason"], "TRIAL_CALENDARS_DIFFER")
        self.assertEqual(r1["days_common"], 160)

    def test_same_calendar_gives_the_pre_adr_0083_value(self):
        rng = random.Random(11)
        for _ in range(40):
            n_tr = rng.randrange(8, 14)
            a0, na = rng.randrange(0, 50), rng.randrange(40, 120)
            # each trial: symbol A on [a0, a0+na) plus optionally symbol B inside it
            ser = {}
            for k in range(n_tr):
                s = {"A": [a0, [rng.gauss(0, 1) for _ in range(na)]]}
                if rng.random() < 0.5:
                    b0 = rng.randrange(a0, a0 + na)
                    s["B"] = [b0, [rng.gauss(0, 1) for _ in range(rng.randrange(1, a0 + na - b0 + 1))]]
                ser[f"t{k:02d}"] = s
            p, rows, patch = self.fake(ser)
            (old, n_old), days_old = pbo_before(rows)
            with patch:
                r = p._pbo()
            self.assertEqual(r["computable"], math.isfinite(old))
            if math.isfinite(old):
                self.assertEqual(r["pbo"], old)
            self.assertEqual((r["combinations"], r["days"], r["trials"]), (n_old, days_old, n_tr))

    def test_gapped_but_identical_calendars_are_computable(self):
        # every trial has the same two DEV windows (ADR 0081 gap): one calendar,
        # the observed days in order, no zero, no gap day
        ser = {f"t{k}": {"A": [0, wave(80, k)], "B": [300, wave(80, 20 + k)]} for k in range(8)}
        p, rows, patch = self.fake(ser)
        with patch:
            r = p._pbo()
        self.assertTrue(r["computable"])
        self.assertEqual(r["days"], 160)
        from engine_lib import pbo_cscv
        sids = sorted(ser)
        mat = [[(ser[s]["A"][1] + ser[s]["B"][1])[i] for s in sids] for i in range(160)]
        self.assertEqual(r["pbo"], pbo_cscv(mat, n_partitions=16)[0])

    def test_too_few_trials_has_a_reason(self):
        p, rows, patch = self.fake({f"t{k}": {"A": [0, wave(80, k)]} for k in range(3)})
        with patch:
            r = p._pbo()
        self.assertFalse(r["computable"])
        self.assertEqual(r["reason"], "TOO_FEW_TRIALS")
        self.assertEqual(r["trials"], 3)

    def test_non_finite_value_is_refused(self):
        ser = {f"t{k}": {"A": [0, wave(80, k)]} for k in range(8)}
        ser["t0"] = {"A": [0, wave(79, 0) + [float("nan")]]}
        p, rows, patch = self.fake(ser)
        with patch:
            r = p._pbo()
        self.assertFalse(r["computable"])
        self.assertEqual(r["reason"], "DEV_SERIES_INVALID")

    def test_too_few_days_has_a_reason(self):
        p, rows, patch = self.fake({f"t{k}": {"A": [0, wave(10, k)]} for k in range(8)})
        with patch:
            r = p._pbo()
        self.assertFalse(r["computable"])
        self.assertEqual(r["reason"], "CSCV_NOT_COMPUTABLE")


class TestMixedSymbolSetsFullPath(PipelineCase):
    """One context, strategies on BTCUSDT only / ETHUSDT only / both, with the
    real locked windows (DEV windows that do not touch, ADR 0081)."""

    @classmethod
    def setUpClass(cls):
        cls.prov = SyntheticProvider(symbols=SYMS, dev_start=DEV_START)

    def build(self):
        from pipeline import EvolutionPipeline
        p = EvolutionPipeline(self.td / "state", self.prov, verified_fee_cost(), symbols=list(SYMS))
        specs = [dict(EDGE, symbols=list(SYMS))]
        specs += [dict(s, symbols=["BTCUSDT"]) for s in NOISE[:4]]
        specs += [dict(s, symbols=["ETHUSDT"]) for s in NOISE[4:8]]
        return p, self.register_all(p, specs)

    def test_pbo_is_refused_not_zero_filled(self):
        p, sids = self.build()
        rows = p._trial_series()
        self.assertGreaterEqual(len(rows), 8)
        (old, _n), old_days = pbo_before(rows)
        r = p._pbo()
        self.assertFalse(r["computable"])
        self.assertEqual(r["reason"], "TRIAL_CALENDARS_DIFFER")
        self.assertGreaterEqual(r["calendars"], 2)
        self.assertEqual(r["days_union"], old_days)
        self.assertLess(r["days_common"], r["days_union"])

    def test_robustness_blocks_and_never_passes(self):
        p, sids = self.build()
        p.run_validation()
        rob = p.evaluation("robustness", sids[0])
        self.assertIsNotNone(rob, "the planted edge reaches robustness")
        pbo = rob["checks"]["pbo"]
        self.assertFalse(pbo["pass"])
        self.assertFalse(pbo["computable"])
        # ADR 0087: the PBO trial set is the strategy's symbol-set group; the
        # BTC+ETH edge is the only BTC+ETH trial -> still not computable
        # (fail-closed), now because the group is below pbo_min_trials
        self.assertEqual(pbo["reason"], "TOO_FEW_TRIALS")
        self.assertEqual(pbo["trials"], 1)
        self.assertEqual(pbo["search_context"]["symbol_set"], list(SYMS))
        self.assertIn("pbo", rob["not_computable"])
        self.assertIn("pbo", rob["failed"])
        st = p.statuses.get(sids[0], p.context_id())
        self.assertNotIn(st["status"], ("VALIDATION_SURVIVOR", "PASS_INDIVIDUAL"))
        if rob["failed"] == rob["not_computable"]:
            self.assertEqual(st["status"], "BLOCKED_STATISTICS")
            self.assertTrue(any("TOO_FEW_TRIALS" in x for x in st["reasons"]))
        # still not computable on a resume: no BTC+ETH trial was added
        self.assertFalse(p._blocked_now_computable(sids[0]))


if __name__ == "__main__":
    unittest.main()
