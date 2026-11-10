"""ADR 0066: opt-in feature set v2 (klines only) — causal, off by default,
and a new experiment context when enabled.  Synthetic bars only."""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import strategy_spec as ss  # noqa: E402
from data_quality import Bar  # noqa: E402
from evolution import random_condition  # noqa: E402
from test_core_defects import rich_bars  # noqa: E402

V2 = [f.name for f in ss.FEATURE_SETS["v2"]]


class TestFeatureSetV2(unittest.TestCase):
    def tearDown(self):
        ss.disable_feature_set("v2")

    def test_off_by_default_and_identity_unchanged(self):
        base = ss.registry_identity()
        self.assertFalse(set(V2) & set(ss.FEATURES))
        self.assertNotIn("feature_sets", base)
        self.assertEqual(ss.enable_feature_set("v2"), V2)
        ident = ss.registry_identity()
        self.assertEqual(ident["feature_sets"], ["v2"])
        self.assertTrue(set(V2) <= set(ident["features"]))
        ss.disable_feature_set("v2")
        self.assertEqual(ss.registry_identity(), base)
        with self.assertRaises(ss.SpecError):
            ss.enable_feature_set("v9")

    def test_v2_features_are_prefix_invariant(self):
        ss.enable_feature_set("v2")
        bars = rich_bars(1200)
        cut = 900
        poisoned = bars[:cut + 1] + [Bar(ts=b.ts, open=b.open * 3, high=b.high * 5, low=b.low * 0.1,
                                         close=b.close * 0.2, volume=b.volume * 9,
                                         quote_volume=b.quote_volume * 9,
                                         taker_buy_volume=b.volume * 9) for b in bars[cut + 1:]]
        for name in V2:
            f = ss.FEATURES[name]
            # small windows so that values exist well before the cut
            params = {pn: max(int(p.lo), 32) if p.kind is int else p.lo for pn, p in f.params.items()}
            if name == "skew_z":
                params = {"length": 32, "window": 128}
            sp = ss.canonicalize_spec({"schema": ss.SPEC_SCHEMA, "direction": "long",
                                       "symbols": ["BTCUSDT"], "exit": {"horizon": 8},
                                       "entry": {"conditions": [{
                                           "feature": name, "params": params, "op": f.ops[0],
                                           "value": [0, 1] if f.ops[0] == "between" else 0.0}]}})
            a = ss.build_spec_aux(bars, sp, None, None)["series"]
            b = ss.build_spec_aux(poisoned, sp, None, None)["series"]
            for k in a:
                self.assertEqual(a[k][:cut + 1], b[k][:cut + 1], name)
                self.assertTrue(any(v is not None for v in a[k][:cut + 1]), f"{name}: empty before the cut")
                vals = [v for v in a[k] if v is not None]
                self.assertTrue(vals, f"{name}: no value computed")
                lo, hi = f.value_range
                self.assertTrue(all(lo <= v <= hi for v in vals), name)

    def test_dow_is_monday_zero(self):
        ss.enable_feature_set("v2")
        mon = 1_699_833_600_000      # 2023-11-13 00:00 UTC, a Monday
        b = [Bar(ts=mon + k * 86_400_000, open=1, high=1, low=1, close=1, volume=1, quote_volume=1)
             for k in range(7)]
        self.assertEqual(ss.FEATURES["dow_utc"].compute({"bars": b}, {}), [0.0, 1, 2, 3, 4, 5, 6])

    def test_generator_draws_v2_only_when_enabled(self):
        def drawn():
            rng = random.Random(3)
            return {random_condition(rng, ["klines"])["feature"] for _ in range(400)}
        self.assertFalse(drawn() & set(V2))
        ss.enable_feature_set("v2")
        got = drawn()
        self.assertTrue(set(V2) <= got)
        rng = random.Random(5)
        for _ in range(300):
            c = random_condition(rng, ["klines"])
            if c["feature"] == "dow_utc":
                ss.canonicalize_spec({"schema": ss.SPEC_SCHEMA, "direction": "long",
                                      "symbols": ["BTCUSDT"], "exit": {"horizon": 8},
                                      "entry": {"conditions": [c]}})


    def test_runner_option(self):
        from runner import UsageError, parse_options
        self.assertEqual(parse_options(["--mode", "evolve", "--feature-set", "v2"])["feature-set"], "v2")
        self.assertEqual(parse_options([])["feature-set"], "v1")
        for bad in (["--feature-set", "v2"], ["--mode", "evolve", "--feature-set", "v9"]):
            with self.assertRaises(UsageError):
                parse_options(bad)


if __name__ == "__main__":
    unittest.main()
