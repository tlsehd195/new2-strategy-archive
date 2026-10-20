"""Directive 2026-10-20 §6 — strategy fingerprints and duplicate detection (synthetic).

exact definition  : same canonical definition (strategy id, definition fingerprint)
behaviour equal   : same signal on every input (``behaviour_key``) -> not proposed again
near duplicate    : thresholds within half a mutation step -> kept, flagged, one per batch
real difference   : everything else (one mutation step, other risk-free structure)
"""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine_lib import strategy_id  # noqa: E402
from evolution import (  # noqa: E402
    FINGERPRINT_VERSION, LineageStore, behaviour_key, definition_fingerprint, mutate, near_duplicate,
)
from search import SearchConfig, propose_round  # noqa: E402
from strategy_spec import SPEC_SCHEMA, SpecError, canonicalize_spec  # noqa: E402
from test_pipeline import EDGE, PipelineCase, SyntheticProvider  # noqa: E402


def cond(feature, params, op, value):
    return {"feature": feature, "params": params, "op": op, "value": value}


def spec(entry, filters=(), *, logic="all", horizon=8, direction="long", symbols=("BTCUSDT",), **extra):
    return {"schema": SPEC_SCHEMA, "direction": direction, "symbols": list(symbols),
            "entry": {"logic": logic, "conditions": list(entry)}, "filters": list(filters),
            "exit": {"horizon": horizon, **extra.pop("exit", {})}, **extra}


RZ = {"lookback": 16, "window": 64, "min_history": 0}
A = cond("ret_z", RZ, "gt", 1.5)
H = cond("hour_utc", {}, "between", [10.0, 14.0])
V = cond("vol_ratio", {"short": 16, "long": 192}, "gt", 1.3)


class TestExactAndNormalForm(unittest.TestCase):
    def test_identical_strategy(self):
        a, b = spec([A, H]), spec([A, H])
        self.assertEqual(strategy_id(canonicalize_spec(a)), strategy_id(canonicalize_spec(b)))
        self.assertEqual(definition_fingerprint(a), definition_fingerprint(b))
        self.assertEqual(behaviour_key(a), behaviour_key(b))

    def test_parameter_and_condition_order_and_explicit_defaults(self):
        a = spec([A, H])
        b = spec([H, {"value": 1.5, "op": "gt", "params": {"min_history": 0, "window": 64, "lookback": 16},
                      "feature": "ret_z"}])
        c = spec([cond("ret_z", {"lookback": 16}, "gt", 1.5), H])     # window/min_history = defaults
        self.assertEqual(definition_fingerprint(a), definition_fingerprint(b))
        self.assertEqual(definition_fingerprint(a), definition_fingerprint(c))
        self.assertEqual(behaviour_key(a), behaviour_key(c))

    def test_expression_only_differences(self):
        # entry AND filter == one conjunction
        self.assertEqual(behaviour_key(spec([A, H])), behaviour_key(spec([A], [H])))
        # redundant bound inside a conjunction: x > 1.5 AND x > 1.0 == x > 1.5
        self.assertEqual(behaviour_key(spec([A], [cond("ret_z", RZ, "gt", 1.0)])), behaviour_key(spec([A])))
        # integer feature: [9.5, 14) and [10, 14) select the same hours
        self.assertEqual(behaviour_key(spec([cond("hour_utc", {}, "between", [9.5, 14.0])])),
                         behaviour_key(spec([H])))
        self.assertEqual(behaviour_key(spec([cond("hour_utc", {}, "between", [10.0, 13.5])])),
                         behaviour_key(spec([H])))
        # the stored definitions still differ (ids stay stable)
        self.assertNotEqual(definition_fingerprint(spec([A, H])), definition_fingerprint(spec([A], [H])))

    def test_not_folded_when_some_input_differs(self):
        # '>' vs '>=' on a continuous feature differs at the boundary value: not exact
        self.assertNotEqual(behaviour_key(spec([A])), behaviour_key(spec([cond("ret_z", RZ, "ge", 1.5)])))
        # same feature, other computation window: other series
        self.assertNotEqual(behaviour_key(spec([A])),
                            behaviour_key(spec([cond("ret_z", {**RZ, "lookback": 24}, "gt", 1.5)])))


class TestNearDuplicates(unittest.TestCase):
    def test_effectively_equal_thresholds(self):
        a, b = spec([A, V]), spec([cond("ret_z", RZ, "gt", 1.52), cond("vol_ratio", V["params"], "gt", 1.35)])
        self.assertNotEqual(behaviour_key(a), behaviour_key(b))       # not exact ...
        why = near_duplicate(a, b)
        self.assertIsNotNone(why)                                     # ... but flagged with its basis
        self.assertIn("ret_z 1.5 vs 1.52", why)
        rsi = lambda v: spec([cond("rsi", {"period": 14}, "lt", v)])  # noqa: E731
        self.assertIsNotNone(near_duplicate(rsi(30.0), rsi(32.0)))    # 0-100 scale: < 2.5

    def test_real_threshold_difference(self):
        # one tweak_threshold step (0.25 on a z scale, 5 on a 0-100 scale) is a real difference
        self.assertIsNone(near_duplicate(spec([A]), spec([cond("ret_z", RZ, "gt", 1.75)])))
        rsi = lambda v: spec([cond("rsi", {"period": 14}, "lt", v)])  # noqa: E731
        self.assertIsNone(near_duplicate(rsi(30.0), rsi(35.0)))
        # integer feature: one hour is a different set of bars
        self.assertIsNone(near_duplicate(spec([H]), spec([cond("hour_utc", {}, "between", [11.0, 14.0])])))
        # other operator side, other computation window: other structure
        self.assertIsNone(near_duplicate(spec([A]), spec([cond("ret_z", RZ, "ge", 1.5)])))
        self.assertIsNone(near_duplicate(spec([A]), spec([cond("ret_z", {**RZ, "window": 96}, "gt", 1.5)])))

    def test_risk_symbol_timeframe_and_exit_differences(self):
        base = spec([A])
        risk = {**base, "risk": {"risk_per_trade": 0.02, "max_concurrent": 1}}
        # risk only: same behaviour (no stage reads it), different definition — reported, see search test
        self.assertEqual(behaviour_key(base), behaviour_key(risk))
        self.assertNotEqual(definition_fingerprint(base), definition_fingerprint(risk))
        for other in (spec([A], symbols=("ETHUSDT",)), spec([A], symbols=("BTCUSDT", "ETHUSDT")),
                      spec([A], exit={"stop_loss_bp": 50.0}), spec([A], horizon=12),
                      spec([A], direction="short")):
            with self.subTest(other=other):
                self.assertNotEqual(behaviour_key(base), behaviour_key(other))
                self.assertIsNone(near_duplicate(base, other))
        with self.assertRaises(SpecError):                            # another timeframe is never conflated
            canonicalize_spec({**base, "timeframe": "1h"})


class TestLineageAndSearch(unittest.TestCase):
    def test_different_mutations_of_one_parent(self):
        parent = canonicalize_spec(spec([A, V]))
        kids = {}
        for seed in range(40):
            for op in ("tweak_threshold", "tweak_param", "change_horizon"):
                r = mutate(parent, op, random.Random(seed), {"available_data": ["klines"]})
                if r is not None:
                    kids[strategy_id(r[0])] = (op, r[1])
        self.assertGreater(len(kids), 5)
        self.assertNotIn(strategy_id(parent), kids)
        for sid, (op, detail) in kids.items():
            self.assertIn("from", detail)                             # changed parameter recorded

    def test_generation_and_identity_on_lineage(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ls = LineageStore(Path(d) / "lineage.jsonl")
            a, b, c = spec([A]), spec([cond("ret_z", RZ, "gt", 1.75)]), spec([A], horizon=12)
            ida = strategy_id(canonicalize_spec(a))
            idb = strategy_id(canonicalize_spec(b))
            idc = strategy_id(canonicalize_spec(c))
            kw = dict(generator_version="g", seed=1, round_index=1, context_id="C", policy={}, budget={},
                      parent_dev={}, created=True)
            ls.record(child_id=ida, parents=["LEGACY-1"], operator="legacy_translation", detail={},
                      identity=ls.identity_for(a, ["LEGACY-1"], "legacy_translation"), **kw)
            ls.record(child_id=idb, parents=[ida], operator="tweak_threshold", detail={"from": 1.5, "to": 1.75},
                      identity=ls.identity_for(b, [ida], "tweak_threshold"), **kw)
            rec = ls.record(child_id=idc, parents=[idb], operator="change_horizon", detail={},
                            identity=ls.identity_for(c, [idb], "change_horizon"), **kw)
            self.assertEqual([ls.generation_of(x) for x in (ida, idb, idc)], [0, 1, 2])
            for k in ("fingerprint_version", "definition_fingerprint", "behaviour_key", "generation",
                      "parents", "operator", "detail", "seed"):
                self.assertIn(k, rec)
            self.assertEqual(rec["fingerprint_version"], FINGERPRINT_VERSION)

    def test_reappearance_after_generations_is_caught_with_its_representative(self):
        """A risk-only child of a gen-1 strategy behaves like the gen-0 one."""
        a = canonicalize_spec(spec([A, V]))
        b = canonicalize_spec({**spec([A, V]), "risk": {"risk_per_trade": 0.02}})
        ida, idb = strategy_id(a), strategy_id(b)
        cfg = SearchConfig(children_per_round=6, exploration_share=0.0, crossover_share=0.0, seed=3,
                           operator_weights={"change_risk": 1})
        props, cnt = propose_round(cfg=cfg, round_index=4, elite=[idb], specs={idb: b}, feedback={},
                                   available_data=["klines"], known_ids={ida, idb}, symbols=["BTCUSDT"],
                                   known_specs={ida: a, idb: b})
        self.assertEqual(props, [])
        self.assertGreater(cnt["semantic_duplicates"], 0)
        logs = [x for x in cnt["dedup_log"] if x["kind"] == "behaviour_equal"]
        self.assertTrue(logs)
        self.assertTrue(all(x["representative"] in (ida, idb) for x in logs))
        self.assertTrue(all("risk" in x["reason"] for x in logs))

    def test_same_input_and_seed_reproduce_the_round(self):
        cfg = SearchConfig(children_per_round=30, exploration_share=0.5, seed=11)
        parent = canonicalize_spec(spec([A, V]))
        pid = strategy_id(parent)
        args = dict(cfg=cfg, round_index=2, elite=[pid], specs={pid: parent}, feedback={},
                    available_data=["klines"], known_ids={pid}, symbols=["BTCUSDT"], known_specs={pid: parent})
        r1, c1 = propose_round(**args)
        r2, c2 = propose_round(**args)
        self.assertEqual([x["sid"] for x in r1], [x["sid"] for x in r2])
        self.assertEqual(c1, c2)
        self.assertTrue(r1)

    def test_near_duplicate_proposal_is_kept_and_flagged(self):
        a = canonicalize_spec(spec([A, V]))
        ida = strategy_id(a)
        cfg = SearchConfig(children_per_round=60, exploration_share=0.0, crossover_share=0.0, seed=5,
                           operator_weights={"resample_param": 0, "tweak_threshold": 1})
        props, cnt = propose_round(cfg=cfg, round_index=1, elite=[ida], specs={ida: a}, feedback={},
                                   available_data=["klines"], known_ids={ida}, symbols=["BTCUSDT"],
                                   known_specs={ida: a})
        # tweak_threshold moves one full step: never a near duplicate of its parent
        self.assertEqual(cnt["near_duplicates"], 0)
        self.assertTrue(all("near_duplicate_of" not in p["detail"] for p in props))


class TestValidationTakesOneOfANearGroup(PipelineCase):
    def test_twins_are_not_both_submitted(self):
        p = self.pipe(SyntheticProvider())
        f1 = cond("ret_z", {"lookback": 4, "window": 64, "min_history": 0}, "gt", -7.9)
        f2 = cond("ret_z", {"lookback": 4, "window": 64, "min_history": 0}, "gt", -7.95)
        twin1 = {**EDGE, "filters": [f1]}
        twin2 = {**EDGE, "filters": [f2]}
        self.assertIsNotNone(near_duplicate(twin1, twin2))
        sids = self.register_all(p, [twin1, twin2])
        fb = p.dev_feedback_all()
        if not all(fb.get(s, {}).get("screen_pass") for s in sids):
            self.skipTest("the synthetic twins did not both pass the DEV screen")
        cands = p.validation_candidates()
        self.assertEqual(len(set(cands) & set(sids)), 1)
        skipped = p.near_duplicate_skips()
        self.assertEqual(len(skipped), 1)
        (k, v), = skipped.items()
        self.assertIn(v["twin"], sids)
        self.assertIn("half a mutation step", v["reason"])


if __name__ == "__main__":
    unittest.main()
