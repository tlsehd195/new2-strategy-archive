"""Auto-evolution §3 / §4: declarative specs, variation operators, lineage and
the data-snooping-robust statistics (synthetic data only, temp dirs)."""
from __future__ import annotations

import math
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from data_quality import Bar  # noqa: E402
from engine_lib import strategy_id  # noqa: E402
from evolution import (  # noqa: E402
    OPERATORS, LineageStore, SpecStore, crossover, mutate, random_spec,
)
from strategy_spec import (  # noqa: E402
    SPEC_SCHEMA, SpecError, build_spec_aux, build_spec_signal, canonicalize_spec,
    legacy_to_spec, stop_exit_rule,
)
from test_core_defects import rich_bars, rich_fng, rich_funding  # noqa: E402
import stats_mc  # noqa: E402

ALL_DATA = ["klines", "funding", "fng"]


def spec(conds, *, direction="long", symbols=("BTCUSDT",), horizon=16, filters=(), logic="all",
         **exit_kw):
    return {"schema": SPEC_SCHEMA, "direction": direction, "symbols": list(symbols),
            "entry": {"logic": logic, "conditions": list(conds)}, "filters": list(filters),
            "exit": {"horizon": horizon, **exit_kw}}


RZ = {"feature": "ret_z", "params": {"lookback": 8}, "op": "lt", "value": -1.5}
RSI = {"feature": "rsi", "params": {"period": 14}, "op": "lt", "value": 30}


class TestSpecValidation(unittest.TestCase):
    def test_valid_and_defaults_filled(self):
        c = canonicalize_spec(spec([RZ]))
        self.assertEqual(c["entry"]["conditions"][0]["params"],
                         {"lookback": 8, "window": 64, "min_history": 0})
        self.assertEqual(c["data"], ["klines"])

    def test_rejections(self):
        bad = [
            spec([{**RZ, "feature": "no_such"}]),                              # unknown feature
            spec([{**RZ, "params": {"lookback": 0}}]),                         # param out of range
            spec([{**RZ, "params": {"lookbak": 4}}]),                          # unknown param
            spec([{**RZ, "op": "eq"}]),                                        # operator
            spec([{**RZ, "value": 99.0}]),                                     # threshold range
            spec([RZ, {**RZ, "op": "gt", "value": 1.0}]),                      # contradiction
            spec([RZ, RSI, {**RSI, "params": {"period": 7}}, {**RZ, "value": -2.0}]),  # > 3 entry
            spec([RZ], symbols=("DOGEUSDT",)),                                 # unsupported symbol
            spec([RZ], horizon=2),                                             # horizon range
            spec([RZ], stop_loss_bp=1.0),                                      # stop range
            spec([RSI], direction="symmetric"),                                # unsigned symmetric
            spec([{"feature": "hour_utc", "params": {}, "op": "between", "value": [5, 5]}]),
            {**spec([RZ]), "family": "made_up"},                               # derived key forged
            {**spec([RZ]), "code": "import os"},                               # free-form code
        ]
        for s in bad:
            with self.assertRaises(SpecError, msg=str(s)):
                canonicalize_spec(s)

    def test_any_logic_allows_disjoint_entry(self):
        c = canonicalize_spec(spec([RZ, {**RZ, "op": "gt", "value": 1.0}], logic="any"))
        self.assertEqual(c["entry"]["logic"], "any")

    def test_identity_ignores_order_defaults_and_duplicates(self):
        a = spec([RZ, RSI])
        b = spec([RSI, {**RZ, "params": {"lookback": 8, "window": 64, "min_history": 0}}, RSI])
        self.assertEqual(strategy_id(canonicalize_spec(a)), strategy_id(canonicalize_spec(b)))
        c = spec([RZ, {**RSI, "value": 31}])
        self.assertNotEqual(strategy_id(canonicalize_spec(a)), strategy_id(canonicalize_spec(c)))

    def test_legacy_translation_gives_identical_signals(self):
        from generator import FAMILY_GENERATORS
        from evaluator import PrefixView  # noqa: F401
        from runner import build_aux, build_signal_fn, PrefixShim
        bars = rich_bars(1500)
        fund = rich_funding(bars[0].ts, bars[-1].ts)
        fng = rich_fng(bars[0].ts, bars[-1].ts)
        rng = random.Random(5)
        for fam, gen in FAMILY_GENERATORS.items():
            defs = list(gen())
            for d in rng.sample(defs, 3):
                sp = legacy_to_spec(d)
                s_leg = build_signal_fn(d, build_aux(bars, d, fund, fng=fng))
                s_sp = build_spec_signal(sp, build_spec_aux(bars, sp, fund, fng))
                for i in range(1, len(bars), 7):
                    v = PrefixShim(bars, i + 1)
                    self.assertEqual(s_leg(v), s_sp(v), f"{fam} {d['params']} @ {i}")


class TestStopExit(unittest.TestCase):
    def _b(self, i, o, h, lo, c):
        return Bar(ts=i * 900_000, open=o, high=h, low=lo, close=c, volume=1, quote_volume=1,
                   taker_buy_volume=0.5)

    def test_both_levels_touched_counts_as_stop(self):
        bars = [self._b(0, 100, 103, 97, 100), self._b(1, 100, 100, 100, 100)]
        r = stop_exit_rule(spec([RZ], stop_loss_bp=200, take_profit_bp=200))
        self.assertEqual(r(bars, 0, 1, 1)[1:], (98.0, "stop"))

    def test_gap_fills_at_open_not_at_stop(self):
        bars = [self._b(0, 100, 100.5, 99.5, 100), self._b(1, 95, 96, 94, 95),
                self._b(2, 95, 95, 95, 95)]
        r = stop_exit_rule(spec([RZ], stop_loss_bp=200))
        self.assertEqual(r(bars, 0, 1, 2), (1, 95, "stop_gap"))

    def test_no_stop_returns_none_rule(self):
        self.assertIsNone(stop_exit_rule(spec([RZ])))


class TestOperators(unittest.TestCase):
    def test_children_always_valid_and_bounded(self):
        rng = random.Random(11)
        ctx = {"available_data": ALL_DATA}
        pop = [random_spec(rng, ALL_DATA) for _ in range(20)]
        made = 0
        for _ in range(1500):
            out = mutate(rng.choice(pop), rng.choice(OPERATORS), rng, ctx)
            if out:
                child, detail = out
                self.assertEqual(canonicalize_spec(child), child)
                pop.append(child)
                made += 1
        self.assertGreater(made, 1000)

    def test_structural_operators_change_structure(self):
        rng = random.Random(2)
        base = canonicalize_spec(spec([RZ, RSI], symbols=("BTCUSDT", "ETHUSDT")))
        ctx = {"available_data": ["klines"]}
        c, _ = mutate(base, "remove_condition", rng, ctx)
        self.assertEqual(len(c["entry"]["conditions"]) + len(c["filters"]), 1)
        c, _ = mutate(base, "change_logic", rng, ctx)
        self.assertEqual(c["entry"]["logic"], "any")
        c, _ = mutate(base, "flip_direction", rng, ctx)
        self.assertEqual(c["direction"], "short")
        c, d = mutate(base, "split_symbol", rng,
                      {**ctx, "parent_dev": {"per_symbol_mean_bp": {"BTCUSDT": -3, "ETHUSDT": 4}}})
        self.assertEqual((c["symbols"], d["guided"]), (["ETHUSDT"], True))
        # data the environment lacks is never introduced
        for _ in range(200):
            out = mutate(base, "add_filter", rng, ctx)
            if out:
                self.assertEqual(out[0]["data"], ["klines"])

    def test_seed_reproducible(self):
        def run(seed):
            rng = random.Random(seed)
            pop = [random_spec(rng, ALL_DATA) for _ in range(5)]
            out = []
            for _ in range(100):
                r = mutate(rng.choice(pop), rng.choice(OPERATORS), rng, {"available_data": ALL_DATA})
                if r:
                    out.append(strategy_id(r[0]))
                    pop.append(r[0])
            return out
        self.assertEqual(run(7), run(7))
        self.assertNotEqual(run(7), run(8))

    def test_crossover_compatibility(self):
        rng = random.Random(3)
        a = canonicalize_spec(spec([RZ]))
        b = canonicalize_spec(spec([RSI]))
        child, d = crossover(a, b, rng)
        feats = {c["feature"] for c in child["entry"]["conditions"] + child["filters"]}
        self.assertEqual(feats, {"ret_z", "rsi"})
        self.assertIsNone(crossover(a, canonicalize_spec(spec([RSI], direction="short")), rng))
        self.assertIsNone(crossover(a, canonicalize_spec(spec([RSI], symbols=("ETHUSDT",))), rng))


class TestStores(unittest.TestCase):
    def test_dedup_and_multipath_lineage_survive_reload(self):
        with tempfile.TemporaryDirectory() as td:
            ss, ls = SpecStore(Path(td) / "specs.jsonl"), LineageStore(Path(td) / "lineage.jsonl")
            p1, _ = ss.put(spec([RZ]), source="seed")
            p2, _ = ss.put(spec([RSI]), source="seed")
            child = spec([RZ, RSI])
            kw = dict(operator="add_entry_condition", detail={}, generator_version="t", seed=1,
                      round_index=1, context_id="C", policy={}, budget={}, parent_dev={})
            c1, new1 = ss.put(child, source="evo")
            ls.record(child_id=c1, parents=[p1], created=new1, **kw)
            c2, new2 = ss.put(spec([RSI, RZ]), source="evo")     # same child, other path
            ls.record(child_id=c2, parents=[p2], created=new2, **kw)
            self.assertEqual((c1 == c2, new1, new2), (True, True, False))
            ss2, ls2 = SpecStore(Path(td) / "specs.jsonl"), LineageStore(Path(td) / "lineage.jsonl")
            self.assertEqual(len(ss2.all()), 3)
            self.assertEqual(sorted(map(tuple, ls2.parents_of(c1))), [(p1,), (p2,)])
            self.assertEqual(ls2.children_of(p1), [c1])
            outs = ls2.outcomes({p1: 1.0, p2: 3.0, c1: 2.0})
            self.assertEqual([o["verdict"] for o in outs], ["improved", "worse_or_equal"])


class TestMultipleComparison(unittest.TestCase):
    """Synthetic null / alternative behaviour of RC, SPA and StepM."""

    def _null(self, rng, m=5, n=150):
        return {f"k{i}": [rng.gauss(0, 1) for _ in range(n)] for i in range(m)}

    def test_null_false_positive_rates(self):
        rng = random.Random(1)
        reps, alpha = 40, 0.10
        rc = spa = sm = 0
        for r in range(reps):
            s = self._null(rng)
            rc += stats_mc.reality_check(s, B=150, seed=r)["p"] <= alpha
            spa += stats_mc.spa_test(s, B=150, seed=r)["p_consistent"] <= alpha
            sm += bool(stats_mc.stepm(s, alpha=alpha, B=150, seed=r)["rejected"])
        # FWER-type error must stay near alpha (binomial noise allowance at 40 reps)
        for name, k in (("rc", rc), ("spa", spa), ("stepm", sm)):
            self.assertLessEqual(k / reps, 0.25, name)

    def test_power_and_stepdown(self):
        rng = random.Random(2)
        s = self._null(rng, m=4, n=300)
        s["good"] = [rng.gauss(0.4, 1) for _ in range(300)]
        out = stats_mc.stepm(s, alpha=0.05, B=300, seed=1)
        self.assertEqual(out["rejected"], ["good"])
        self.assertLess(stats_mc.spa_test(s, B=300, seed=1)["p_consistent"], 0.05)

    def test_by_is_more_conservative_than_bh(self):
        from engine_lib import bh_fdr_family
        ps = [0.001, 0.008, 0.012, 0.03, 0.2]
        bh = bh_fdr_family(ps, 5, 0.05)
        by = stats_mc.by_fdr(ps, 5, 0.05)
        self.assertTrue(all(b <= a for a, b in zip(bh, by)))
        self.assertLess(sum(by), sum(bh))

    def test_alpha_spending_sums_below_total(self):
        self.assertLessEqual(sum(stats_mc.spending_alpha(k, 0.05) for k in range(1, 10000)), 0.05)
        with self.assertRaises(ValueError):
            stats_mc.spending_alpha(0)

    def test_daily_series_books_on_exit_day(self):
        d = 86_400_000
        ev = [{"exit_ts": 10 * d + 5, "net_bp": 3.0}, {"exit_ts": 10 * d + 9, "net_bp": -1.0},
              {"exit_ts": 12 * d, "net_bp": 2.0}, {"exit_ts": 20 * d, "net_bp": 9.0}]
        self.assertEqual(stats_mc.daily_series(ev, 10 * d, 12 * d + 1), [2.0, 0.0, 2.0])

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            stats_mc.stepm({"a": [1.0] * 5}, alpha=0.05)
        with self.assertRaises(ValueError):
            stats_mc.reality_check({"a": [0.0] * 20, "b": [0.0] * 19})
        with self.assertRaises(ValueError):
            stats_mc.spa_test({"a": [1.0, math.nan] * 10})


if __name__ == "__main__":
    unittest.main()


class TestMCSAndDSR(unittest.TestCase):
    def test_mcs_eliminates_clearly_inferior_keeps_equals(self):
        rng = random.Random(5)
        s = {f"a{i}": [rng.gauss(0.3, 1) for _ in range(250)] for i in range(3)}
        s["bad"] = [rng.gauss(-0.8, 1) for _ in range(250)]
        out = stats_mc.mcs(s, alpha=0.10, B=300, seed=1)
        self.assertEqual(out["eliminated"][0], "bad")
        self.assertNotIn("bad", out["set"])
        self.assertTrue(set(out["set"]) <= {"a0", "a1", "a2"} and len(out["set"]) >= 1)
        self.assertEqual(max(out["p"].values()), 1.0)

    def test_mcs_null_keeps_most_models(self):
        keep = []
        for r in range(15):
            rng = random.Random(100 + r)
            s = {f"k{i}": [rng.gauss(0, 1) for _ in range(200)] for i in range(4)}
            keep.append(len(stats_mc.mcs(s, alpha=0.10, B=150, seed=r)["set"]))
        self.assertGreaterEqual(sum(keep) / len(keep), 3.0)   # indistinguishable => not forced out

    def test_mcs_single_and_bad_input(self):
        self.assertEqual(stats_mc.mcs({"a": [0.1] * 20 + [-0.1] * 20})["set"], ["a"])
        with self.assertRaises(ValueError):
            stats_mc.mcs({"a": [1.0] * 30, "b": [1.0] * 30})      # zero variance

    def test_dsr_matches_reference_values(self):
        from engine_lib import deflated_sharpe
        # reference formulas (Bailey & López de Prado 2014; cross-checked against two
        # independent implementations): SR0 = sqrt(V)*((1-g)Phi^-1(1-1/N) + g Phi^-1(1-1/(Ne)))
        from engine_lib import norm_ppf
        g, N, V, sr, n = 0.5772156649015329, 50, 0.04, 0.5, 250
        sr0 = math.sqrt(V) * ((1 - g) * norm_ppf(1 - 1 / N) + g * norm_ppf(1 - 1 / (N * math.e)))
        z = (sr - sr0) * math.sqrt(n - 1) / math.sqrt(1 - 0.0 * sr + (3.0 - 1.0) / 4.0 * sr * sr)
        expect = 0.5 * math.erfc(-z / math.sqrt(2))
        self.assertAlmostEqual(deflated_sharpe(sr, n, 0.0, 3.0, N, V), expect, places=9)
        self.assertLess(deflated_sharpe(sr, n, 0.0, 3.0, 500, V), deflated_sharpe(sr, n, 0.0, 3.0, 5, V))


class TestCostBreakeven(unittest.TestCase):
    def test_breakeven_hand_example(self):
        from pipeline import cost_breakeven
        ev = [{"gross_bp": 30.0, "cost_bp": 10.0, "funding_bp": 0.0},
              {"gross_bp": 10.0, "cost_bp": 10.0, "funding_bp": 0.0}]
        self.assertAlmostEqual(cost_breakeven(ev), 2.0)        # (20 gross)/(10 cost)
        ev[0]["funding_bp"] = 10.0
        self.assertAlmostEqual(cost_breakeven(ev), 1.5)        # funding eats margin
        self.assertIsNone(cost_breakeven([]))
        self.assertIsNone(cost_breakeven([{"gross_bp": 1.0, "cost_bp": 0.0, "funding_bp": 0.0}]))


class TestStepSPA(unittest.TestCase):
    @staticmethod
    def _world(seed, n=250, drifts=(0.0,)):
        r = random.Random(seed)
        return {f"m{i}": [r.gauss(mu, 1.0) for _ in range(n)] for i, mu in enumerate(drifts)}

    def test_finds_true_edge_and_no_more_than_stepm_misses(self):
        ser = self._world(1, drifts=(0.45, 0.4, 0.0, -0.5, -0.5, -0.5, -0.5, -0.5))
        sm = stats_mc.stepm(ser, alpha=0.05, B=400, seed=2)
        sp = stats_mc.step_spa(ser, alpha=0.05, B=400, seed=2)
        self.assertTrue({"m0", "m1"} <= set(sp["rejected"]))
        self.assertNotIn("m2", sp["rejected"])
        self.assertTrue(set(sm["rejected"]) <= set(sp["rejected"]))     # more (or equally) powerful
        self.assertEqual(sp["method"], "hsu_hsu_kuan2010_step_spa_studentized")

    def test_null_world_fwe_is_controlled(self):
        hits = 0
        for r in range(60):
            ser = self._world(100 + r, n=200, drifts=(0.0,) * 6)
            hits += bool(stats_mc.step_spa(ser, alpha=0.10, B=150, seed=r)["rejected"])
        self.assertLessEqual(hits / 60, 0.10 + 0.12)      # alpha + Monte-Carlo slack (60 reps)

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            stats_mc.step_spa({"a": [1.0] * 30}, alpha=0.05)         # zero variance
        with self.assertRaises(ValueError):
            stats_mc.step_spa({"a": [0.1, -0.1] * 20}, alpha=1.5)


class TestDM(unittest.TestCase):
    def test_hand_example_iid(self):
        # d = +1,-1 alternating plus a shift: mean 0.2, lag 0 variance by hand
        d = [1.2, -0.8] * 10
        r = stats_mc.dm_test(d, lag=0)
        var = sum((x - 0.2) ** 2 for x in d) / 20            # = 1.0
        self.assertAlmostEqual(r["z"], 0.2 / math.sqrt(var / 20), places=12)
        self.assertAlmostEqual(r["p_one_sided_better"], 0.5 * math.erfc(r["z"] / math.sqrt(2)), places=12)

    def test_detects_shift_not_noise(self):
        rg = random.Random(5)
        better = [rg.gauss(0.3, 1.0) for _ in range(400)]
        same = [rg.gauss(0.0, 1.0) for _ in range(400)]
        self.assertLess(stats_mc.dm_test(better)["p_one_sided_better"], 0.01)
        self.assertGreater(stats_mc.dm_test(same)["p_one_sided_better"], 0.01)

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            stats_mc.dm_test([0.1] * 5)
        with self.assertRaises(ValueError):
            stats_mc.dm_test([0.5] * 40)          # zero variance
        with self.assertRaises(ValueError):
            stats_mc.dm_test([0.1, float("nan")] * 10)
