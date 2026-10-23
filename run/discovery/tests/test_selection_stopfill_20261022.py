"""ADR 0031: one validation candidate per entry signal, and the conservative
intrabar stop fill.  Synthetic data and temp dirs only — no real window is
opened, no network, no account."""
from __future__ import annotations

import hashlib
import json
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
from evolution import behaviour_key, entry_signal_key  # noqa: E402
from strategy_spec import STOP_FILLS, default_stop_fill, stop_exit_rule  # noqa: E402
from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider, hour_spec  # noqa: E402

PINNED_NEW = {
    "config/exit_fill_v1.json": "42f7f95dd610",
    "config/candidate_selection_v1.json": "07320d5c835e",
    "config/candidate_selection_v2.json": "8e7042292b4f",
}


def with_exit(spec, **ex):
    s = json.loads(json.dumps(spec))
    s["exit"].update(ex)
    return s


class TestEntrySignalKey(unittest.TestCase):
    def test_exit_only_variants_share_the_entry_key(self):
        a = hour_spec(0, 2)
        for b in (with_exit(a, horizon=9), with_exit(a, stop_loss_bp=50.0),
                  with_exit(a, take_profit_bp=80.0, horizon=4)):
            self.assertEqual(entry_signal_key(a), entry_signal_key(b))
            self.assertNotEqual(behaviour_key(a), behaviour_key(b))

    def test_entry_changes_change_the_key(self):
        a = hour_spec(0, 2)
        for b in (hour_spec(0, 3), hour_spec(0, 2, direction="short"),
                  dict(a, symbols=["ETHUSDT"]),
                  dict(a, filters=[{"feature": "hour_utc", "params": {}, "op": "between", "value": [1, 5]}])):
            self.assertNotEqual(entry_signal_key(a), entry_signal_key(b))

    def test_filter_in_entry_or_filters_is_the_same_entry(self):
        cond = {"feature": "hour_utc", "params": {}, "op": "between", "value": [0, 2]}
        a = dict(hour_spec(0, 2), entry={"logic": "all", "conditions": [cond]}, filters=[])
        b = dict(hour_spec(0, 2), entry={"logic": "all", "conditions": [cond, cond]}, filters=[cond])
        self.assertEqual(entry_signal_key(a), entry_signal_key(b))


def zspec(thr=1.0, *, window=96, horizon=8, stop=None):
    s = {"schema": "strategy_spec_v1", "direction": "long", "symbols": ["BTCUSDT"],
         "entry": {"logic": "all", "conditions": [
             {"feature": "ret_z", "params": {"window": window}, "op": "gt", "value": thr}]},
         "filters": [], "exit": {"horizon": horizon}}
    if stop:
        s["exit"]["stop_loss_bp"] = stop
    return s


class TestEntryBoundaries(unittest.TestCase):
    """ADR 0033: boundary cases of the exact entry key and the near-entry rule."""

    def test_exact_equivalences_share_the_key(self):
        a = hour_spec(10, 14)
        b = json.loads(json.dumps(a))
        b["entry"]["conditions"][0]["value"] = [9.5, 14]            # integer feature: same hours
        c = json.loads(json.dumps(a))
        c["entry"]["logic"] = "any"                                 # one condition: any == all
        d = dict(json.loads(json.dumps(a)), risk={"risk_per_trade": 0.02})   # risk is not read
        for x in (b, c, d):
            self.assertEqual(entry_signal_key(a), entry_signal_key(x))

    def test_near_entry_boundary_ignores_the_exit(self):
        from evolution import _near_tolerance, near_duplicate, near_entry_duplicate
        tol = _near_tolerance("ret_z")
        a = zspec(1.0)
        inside = zspec(1.0 + tol - 1e-6, horizon=24, stop=80.0)
        edge = zspec(1.0 + tol, horizon=24, stop=80.0)
        self.assertNotEqual(entry_signal_key(a), entry_signal_key(inside))   # not exact
        self.assertIsNone(near_duplicate(a, inside))                         # exit differs: v1 misses it
        self.assertIsNotNone(near_entry_duplicate(a, inside))                # v2 catches it
        self.assertIsNone(near_entry_duplicate(a, edge))                     # exactly half a step: different
        self.assertIsNone(near_entry_duplicate(a, zspec(1.0, window=97)))    # other parameter: different
        self.assertIsNone(near_entry_duplicate(a, dict(zspec(1.0), direction="short")))
        self.assertIsNone(near_entry_duplicate(a, dict(zspec(1.0), symbols=["ETHUSDT"])))

    def test_integer_feature_has_no_near_band(self):
        from evolution import near_entry_duplicate
        self.assertIsNone(near_entry_duplicate(hour_spec(0, 2), hour_spec(0, 3, horizon=9)))
        self.assertIsNotNone(near_entry_duplicate(hour_spec(0, 2), hour_spec(0, 2, horizon=9)))


class TestOnePerEntrySignal(PipelineCase):
    def test_near_entry_variant_with_other_exit_is_skipped_with_evidence(self):
        from unittest import mock
        p = self.pipe(SyntheticProvider())
        e1, e2, *_ = self.register_all(p, [EDGE, hour_spec(0, 3)] + NOISE[:7])
        self.assertTrue({e1, e2} <= set(p.validation_candidates()))
        # same two DEV-pass ids, read as near-entry twins with different exits
        fake = {e1: zspec(1.0), e2: zspec(1.1, horizon=24, stop=80.0)}
        real = p.specs.get
        with mock.patch.object(p.specs, "get", side_effect=lambda s: fake.get(s) or real(s)):
            cands = set(p.validation_candidates())
            self.assertEqual(len({e1, e2} & cands), 1)
            skipped = ({e1, e2} - cands).pop()
            skip = p.near_duplicate_skips()[skipped]
        self.assertIn("exit ignored", skip["reason"])
        self.assertNotEqual(skip["exit"], skip["twin_exit"])
        self.assertEqual(skip["twin"], ({e1, e2} & cands).pop())

    def test_exact_entry_skip_records_both_exits(self):
        p = self.pipe(SyntheticProvider())
        e1, e2, *_ = self.register_all(p, [EDGE, with_exit(EDGE, horizon=9)] + NOISE[:7])
        cands = set(p.validation_candidates())
        skipped = ({e1, e2} - cands).pop()
        skip = p.near_duplicate_skips()[skipped]
        self.assertEqual({skip["exit"]["horizon"], skip["twin_exit"]["horizon"]}, {8, 9})
        self.assertIn("different exit", skip["reason"])
        self.assertTrue(skip["entry_signal_key"].startswith("EK-"))

    def test_exit_variant_is_skipped_and_stays_research_only(self):
        p = self.pipe(SyntheticProvider())
        ctx = p.context_id()
        e1, e2, *_ = self.register_all(p, [EDGE, with_exit(EDGE, horizon=9)] + NOISE[:7])
        cands = p.validation_candidates()
        self.assertEqual(len({e1, e2} & set(cands)), 1)
        skipped = (set([e1, e2]) - set(cands)).pop()
        kept = (set([e1, e2]) & set(cands)).pop()
        skip = p.near_duplicate_skips()[skipped]
        self.assertEqual(skip["twin"], kept)
        self.assertIn("same entry signal", skip["reason"])
        p.run_validation()
        self.assertEqual(p.statuses.status(skipped, ctx), "RESEARCH_ONLY")
        members = {m for r in p._batches() if r["kind"] == "PREREGISTERED" for m in r["members"]}
        self.assertNotIn(skipped, members)
        # a later batch does not take the variant either: the entry signal was already tested
        self.assertNotIn(skipped, p.validation_candidates())

    def test_highest_ranked_member_is_kept(self):
        p = self.pipe(SyntheticProvider())
        ids = self.register_all(p, [EDGE, with_exit(EDGE, horizon=9), with_exit(EDGE, horizon=4)] + NOISE[:7])
        fb = p.dev_feedback_all()
        cap = p.search_cfg.max_t_objective
        group = [s for s in ids[:3] if fb.get(s, {}).get("screen_pass")]
        self.assertTrue(group)
        best = sorted(group, key=lambda s: (-min(fb[s]["dev_t"] or 0, cap), s))[0]
        cands = set(p.validation_candidates())
        self.assertIn(best, cands)
        self.assertEqual(cands & set(ids[:3]), {best})

    def test_different_entries_are_both_kept(self):
        p = self.pipe(SyntheticProvider())
        e1, e2, *_ = self.register_all(p, [EDGE, hour_spec(0, 3)] + NOISE[:7])
        self.assertTrue({e1, e2} <= set(p.validation_candidates()))

    def test_bad_selection_config_is_refused(self):
        from pipeline import load_selection_config
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "sel.json"
            bad.write_text(json.dumps({"schema": "candidate_selection_v1", "one_per_entry_signal": "yes",
                                       "one_per_near_duplicate": True}))
            with self.assertRaises(ValueError):
                load_selection_config(bad)
        self.assertTrue(load_selection_config()["one_per_entry_signal"])
        self.assertTrue(load_selection_config()["one_per_near_entry_signal"])     # v2 default (ADR 0033)
        v1 = load_selection_config(DISC / "config" / "candidate_selection_v1.json")
        self.assertFalse(v1["one_per_near_entry_signal"])                         # v1: rule absent


def bar(o, h, lo, c, ts=0):
    return Bar(ts=ts, open=o, high=h, low=lo, close=c, volume=1.0, quote_volume=1.0)


class TestStopFill(unittest.TestCase):
    SPEC = with_exit(hour_spec(0, 2), stop_loss_bp=100.0, horizon=5)

    def test_default_is_bar_extreme(self):
        self.assertEqual(default_stop_fill(), "bar_extreme")
        cfg = json.loads((DISC / "config" / "exit_fill_v1.json").read_text())
        self.assertEqual(set(cfg["allowed_stop_fills"]), set(STOP_FILLS))

    def test_long_touch_fills_at_bar_low(self):
        bars = [bar(100, 100.5, 99.8, 100.2), bar(100.2, 100.3, 98.5, 98.9), bar(99, 99, 99, 99)]
        self.assertEqual(stop_exit_rule(self.SPEC, "bar_extreme")(bars, 0, 1, 5), (1, 98.5, "stop"))
        self.assertEqual(stop_exit_rule(self.SPEC, "stop_price")(bars, 0, 1, 5), (1, 99.0, "stop"))
        self.assertEqual(stop_exit_rule(self.SPEC)(bars, 0, 1, 5), (1, 98.5, "stop"))

    def test_short_touch_fills_at_bar_high(self):
        bars = [bar(100, 100.2, 99.8, 100.1), bar(100.1, 101.7, 100.0, 101.2), bar(101, 101, 101, 101)]
        self.assertEqual(stop_exit_rule(self.SPEC, "bar_extreme")(bars, 0, -1, 5), (1, 101.7, "stop"))
        self.assertEqual(stop_exit_rule(self.SPEC, "stop_price")(bars, 0, -1, 5), (1, 101.0, "stop"))

    def test_gap_still_fills_at_open(self):
        bars = [bar(100, 100.5, 99.8, 100.2), bar(98.0, 98.4, 97.0, 97.5), bar(97, 97, 97, 97)]
        for mode in STOP_FILLS:
            self.assertEqual(stop_exit_rule(self.SPEC, mode)(bars, 0, 1, 5), (1, 98.0, "stop_gap"))

    def test_take_profit_unchanged(self):
        spec = with_exit(hour_spec(0, 2), take_profit_bp=100.0, horizon=5)
        bars = [bar(100, 100.5, 99.8, 100.2), bar(100.2, 101.9, 100.1, 101.5), bar(101, 101, 101, 101)]
        for mode in STOP_FILLS:
            self.assertEqual(stop_exit_rule(spec, mode)(bars, 0, 1, 5), (1, 101.0, "take_profit"))

    def test_unknown_mode_refused(self):
        with self.assertRaises(ValueError):
            stop_exit_rule(self.SPEC, "mid")

    def test_bar_extreme_is_never_better(self):
        """Random walks: same exit bar and reason in both modes, and the
        bar_extreme fill is never more favourable than the stop-price fill."""
        rng = random.Random(7)
        spec = with_exit(hour_spec(0, 2), stop_loss_bp=60.0, take_profit_bp=90.0, horizon=12)
        worse = 0
        for trial in range(300):
            px, bars = 100.0, []
            for i in range(20):
                c = px * (1 + rng.gauss(0, 0.006))
                bars.append(bar(px, max(px, c) * (1 + abs(rng.gauss(0, 0.003))),
                                min(px, c) * (1 - abs(rng.gauss(0, 0.003))), c, ts=i))
                px = c
            side = rng.choice((1, -1))
            a = stop_exit_rule(spec, "stop_price")(bars, 0, side, 12)
            b = stop_exit_rule(spec, "bar_extreme")(bars, 0, side, 12)
            self.assertEqual((a[0], a[2]), (b[0], b[2]))
            self.assertLessEqual(side * b[1], side * a[1] + 1e-12)
            worse += side * b[1] < side * a[1]
        self.assertGreater(worse, 0)


class TestPinnedAndHashed(unittest.TestCase):
    def test_new_configs_pinned(self):
        for rel, prefix in PINNED_NEW.items():
            sha = hashlib.sha256((DISC / rel).read_bytes()).hexdigest()
            self.assertTrue(sha.startswith(prefix), f"{rel}: sha256 {sha} (pin {prefix!r})")

    def test_configs_are_part_of_the_code_identity(self):
        import prereg_gate as pg
        from pipeline import EVO_CODE_FILES
        for rel in PINNED_NEW:
            self.assertIn(rel, EVO_CODE_FILES)
            self.assertIn(rel, pg.research_code_identity()["files"])


if __name__ == "__main__":
    unittest.main()
