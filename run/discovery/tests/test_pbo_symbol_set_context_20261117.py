"""ADR 0087: PBO search contexts separated by symbol set.

Problem (ADR 0083 consequence): ``_pbo`` refuses a trial set whose observed
DEV calendars differ (``TRIAL_CALENDARS_DIFFER``).  With the real locked
windows (BTCUSDT 2024, ETHUSDT 2026) strategies on BTC only, ETH only and
BTC+ETH never share a calendar, so ONE strategy with another symbol set in the
experiment context made PBO not computable for EVERY strategy of the context:
PBO could never be used once multi-symbol strategies were searched.

Decision (user 동동, 2026-11-17, "전부 추천으로"): the PBO trial set of a
strategy is the trials of the same experiment context with the SAME symbol set
(its search context).  Thresholds, ``pbo_min_trials``, ``pbo_partitions`` and
``max_pbo`` are unchanged; a context with one symbol set gives exactly the
pre-ADR result (differential tests).  The multiple-testing count used by the DSR
gate / MinBTL / SR floor is NOT split: it stays every strategy searched over
every symbol set and context (conservative).  Synthetic data only.
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
DEV_START = {"BTCUSDT": "2023-07-01", "ETHUSDT": "2024-06-19"}


def wave(n, seed, d=0.0):
    return [math.sin(seed * 1.7 + i * 0.9) + 0.01 * seed + d for i in range(n)]


class TestPboSymbolSetGroups(PipelineCase):
    """``_pbo(sid)`` on hand-made trial sets (``_trial_series`` patched)."""

    def fake(self, series_by_sid):
        p = self.pipe(SyntheticProvider())
        rows = {s: {"strategy_id": s, "series": ser} for s, ser in series_by_sid.items()}
        return p, mock.patch.object(type(p), "_trial_series", lambda self_: rows)

    def test_mixed_context_gives_the_pbo_of_the_symbol_set_group(self):
        # 9 trials on symbol A, 4 on B (another DEV window): before ADR 0087 every
        # strategy of the context got TRIAL_CALENDARS_DIFFER
        a = {f"a{k}": {"A": [0, wave(160, k, d=0.002 * k)]} for k in range(9)}
        b = {f"b{k}": {"B": [300, wave(160, 30 + k)]} for k in range(4)}
        p_mix, patch_mix = self.fake({**a, **b})
        p_a, patch_a = self.fake(a)
        with patch_a:
            alone = p_a._pbo("a0")
        with patch_mix:
            whole = p_mix._pbo()
            grp = p_mix._pbo("a0")
            other = p_mix._pbo("b0")
        self.assertEqual(whole["reason"], "TRIAL_CALENDARS_DIFFER")   # ADR 0083 unchanged
        self.assertTrue(alone["computable"])
        self.assertTrue(grp["computable"])
        for k in ("pbo", "combinations", "trials", "days", "computable"):
            self.assertEqual(grp[k], alone[k], k)
        self.assertEqual(grp["trials"], 9)
        ctx = grp["search_context"]
        self.assertEqual(ctx["symbol_set"], ["A"])
        self.assertEqual(ctx["trials_in_experiment_context"], 13)
        self.assertEqual(ctx["symbol_sets_in_experiment_context"], 2)
        # a group below pbo_min_trials stays not computable (fail-closed)
        self.assertFalse(other["computable"])
        self.assertIsNone(other.get("pbo"))
        self.assertEqual(other["reason"], "TOO_FEW_TRIALS")
        self.assertEqual(other["trials"], 4)
        self.assertEqual(other["search_context"]["symbol_set"], ["B"])

    def test_single_symbol_set_is_identical_to_the_whole_context(self):
        # differential: one symbol set -> _pbo(sid) is byte-for-byte _pbo()
        rng = random.Random(7)
        for _ in range(30):
            n_tr = rng.randrange(3, 14)
            a0, na = rng.randrange(0, 50), rng.randrange(8, 120)
            two = rng.random() < 0.5
            ser = {}
            for k in range(n_tr):
                s = {"A": [a0, [rng.gauss(0, 1) for _ in range(na)]]}
                if two:
                    s["B"] = [a0 + rng.randrange(0, 3), [rng.gauss(0, 1) for _ in range(na)]]
                ser[f"t{k:02d}"] = s
            p, patch = self.fake(ser)
            with patch:
                whole = p._pbo()
                for sid in ser:
                    self.assertEqual(p._pbo(sid), whole)

    def test_calendars_differing_inside_one_symbol_set_are_still_refused(self):
        ser = {f"t{k}": {"A": [0, wave(160, k)]} for k in range(8)}
        ser["t8"] = {"A": [5, wave(155, 8)]}
        p, patch = self.fake(ser)
        with patch:
            r = p._pbo("t0")
        self.assertFalse(r["computable"])
        self.assertEqual(r["reason"], "TRIAL_CALENDARS_DIFFER")

    def test_symbol_set_is_the_set_not_the_order(self):
        ser = {f"t{k}": ({"A": [0, wave(80, k)], "B": [0, wave(80, 9 + k)]} if k % 2 else
                         {"B": [0, wave(80, 9 + k)], "A": [0, wave(80, k)]}) for k in range(8)}
        p, patch = self.fake(ser)
        with patch:
            r = p._pbo("t1")
            self.assertEqual(r, p._pbo())
        self.assertTrue(r["computable"])
        self.assertEqual(r["trials"], 8)


class TestMixedSymbolSetsFullPath(PipelineCase):
    """Real locked windows: BTC+ETH 9 (planted edge + 8 noise), BTC only 4,
    ETH only 4 in ONE experiment context."""

    @classmethod
    def setUpClass(cls):
        cls.prov = SyntheticProvider(symbols=SYMS, dev_start=DEV_START)

    def build(self, *, mixed=True):
        from pipeline import EvolutionPipeline
        state = self.td / ("mixed" if mixed else "both_only")
        p = EvolutionPipeline(state, self.prov, verified_fee_cost(), symbols=list(SYMS))
        specs = [dict(EDGE, symbols=list(SYMS))] + [dict(s, symbols=list(SYMS)) for s in NOISE[:8]]
        if mixed:
            specs += [dict(s, symbols=["BTCUSDT"]) for s in NOISE[:4]]
            specs += [dict(s, symbols=["ETHUSDT"]) for s in NOISE[4:8]]
        return p, self.register_all(p, specs)

    def test_group_pbo_equals_the_single_symbol_set_run(self):
        pm, sm = self.build(mixed=True)
        pb, sb = self.build(mixed=False)
        self.assertEqual(sm[0], sb[0])
        rm, rb = pm._pbo(sm[0]), pb._pbo(sb[0])
        self.assertEqual(pm._pbo()["reason"], "TRIAL_CALENDARS_DIFFER")
        self.assertTrue(rb["computable"])
        self.assertTrue(rm["computable"])
        for k in ("pbo", "combinations", "trials", "days"):
            self.assertEqual(rm[k], rb[k], k)
        self.assertEqual(rm["search_context"]["symbol_set"], list(SYMS))
        self.assertEqual(rm["search_context"]["trials_in_experiment_context"], 17)
        self.assertNotIn("search_context", rb)       # single symbol set: unchanged record
        # the multiple-testing count is not split: every strategy searched
        self.assertEqual(rm["trials_searched_all"], 17)
        self.assertEqual(rb["trials_searched_all"], 9)

    def test_dsr_gate_count_is_not_reduced_by_the_split(self):
        pm, sm = self.build(mixed=True)
        d = pm._dsr(sm[0])
        self.assertTrue(d["computable"])
        self.assertEqual(d["trials_searched"], 17)
        self.assertEqual(d["trials"], 17)

    def test_robustness_uses_the_group_pbo(self):
        pm, sm = self.build(mixed=True)
        pm.run_validation()
        rob = pm.evaluation("robustness", sm[0])
        self.assertIsNotNone(rob, "the planted edge reaches robustness")
        pbo = rob["checks"]["pbo"]
        self.assertTrue(pbo["computable"])
        self.assertEqual(pbo["trials"], 9)
        self.assertEqual(pbo["pass"], pbo["pbo"] <= pm.cfg["robustness"]["max_pbo"])
        self.assertEqual(rob["checks"]["dsr"]["gate_trials"], 17)


if __name__ == "__main__":
    unittest.main()
