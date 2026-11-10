"""ADR 0064: promo-v5 is the default; DSR and the bootstrap CI gate robustness.

Before: ``robustness()`` recorded DSR and the bootstrap CI with
``"pass": True, "gating": False`` — a candidate with DSR 0.59 or a CI that
includes 0 reached VALIDATION_SURVIVOR.  Now a gate failure is REJECTED and a
check that cannot be computed is BLOCKED_STATISTICS (INSUFFICIENT_DATA).
Synthetic data only."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pipeline  # noqa: E402
from test_pipeline import EDGE, EDGE2, NOISE, PipelineCase, SyntheticProvider  # noqa: E402

DISC = Path(__file__).resolve().parent.parent
V4 = json.loads((DISC / "config" / "promotion_v4.json").read_text())
V5 = json.loads((DISC / "config" / "promotion_v5.json").read_text())


class TestV5Config(unittest.TestCase):
    def test_default_is_v5_and_only_adds_gates_to_v4(self):
        self.assertEqual(pipeline.DEFAULT_PROMOTION_CONFIG.name, "promotion_v5.json")
        cfg, h = pipeline.load_promotion_config()
        self.assertEqual((cfg["version"], len(h)), ("promo-v5", 16))
        for sec in ("dev_screen", "validation", "locked", "promotion", "paper"):
            self.assertEqual(V5[sec], V4[sec], sec)
        added = {k: v for k, v in V5["robustness"].items() if k not in V4["robustness"]}
        self.assertEqual({k: v for k, v in V5["robustness"].items() if k in V4["robustness"]},
                         V4["robustness"])
        self.assertTrue(added["dsr_gating"] and added["ci_gating"])
        self.assertEqual(added["min_dsr"], 0.95)
        self.assertEqual(added["ci_lower_bound_min_bp"], 0.0)


class TestGates(PipelineCase):
    def _run(self, specs, **kw):
        p = self.pipe(SyntheticProvider(), **kw)
        ids = self.register_all(p, specs)
        p.run_validation()
        return p, p.context_id(), ids

    def test_dsr_below_threshold_is_rejected(self):
        p, ctx, (e1, e2, *_) = self._run([EDGE, EDGE2] + NOISE[:7])
        self.assertEqual(p.statuses.status(e1, ctx), "VALIDATION_SURVIVOR")
        r2 = p.evaluation("robustness", e2)
        self.assertEqual(r2["failed"], ["dsr"])
        self.assertTrue(r2["checks"]["dsr"]["gating"])
        self.assertLess(r2["checks"]["dsr"]["dsr"], 0.95)
        self.assertEqual(p.statuses.status(e2, ctx), "REJECTED")
        self.assertEqual(r2["config_version"], "promo-v5")

    def test_same_candidates_pass_under_v3_where_dsr_was_informational(self):
        p, ctx, (e1, e2, *_) = self._run([EDGE, EDGE2] + NOISE[:7], promo="promotion_v3.json")
        self.assertEqual(p.statuses.status(e2, ctx), "VALIDATION_SURVIVOR")
        self.assertFalse(p.evaluation("robustness", e2)["checks"]["dsr"]["gating"])

    def test_ci_lower_bound_gate(self):
        p, ctx, (e1, *_) = self._run([EDGE] + NOISE[:7],
                                     cfg_patch={"robustness": {"ci_lower_bound_min_bp": 1e6}})
        r = p.evaluation("robustness", e1)
        self.assertIn("bootstrap_ci", r["failed"])
        self.assertTrue(r["checks"]["bootstrap_ci"]["computable"])
        self.assertEqual(p.statuses.status(e1, ctx), "REJECTED")

    def test_not_computable_is_insufficient_data_never_pass(self):
        # one strategy in the trial set: DSR and PBO cannot be computed
        p, ctx, (e1,) = self._run([EDGE])
        r = p.evaluation("robustness", e1)
        self.assertTrue(set(r["not_computable"]) >= {"dsr", "pbo"})
        self.assertEqual(r["failed"], r["not_computable"])
        st = p.statuses.get(e1, ctx)
        self.assertEqual((st["status"], st["resume_to"]), ("BLOCKED_STATISTICS", "VALIDATION_SURVIVOR"))
        self.assertTrue(all(x.startswith("INSUFFICIENT_DATA") for x in st["reasons"]))

    def test_ci_days_shortfall_never_resumes(self):
        p, ctx, (e1, *_) = self._run([EDGE] + NOISE[:7],
                                     cfg_patch={"robustness": {"ci_min_days": 10_000}})
        r = p.evaluation("robustness", e1)
        self.assertIn("bootstrap_ci", r["not_computable"])
        self.assertEqual(p.statuses.status(e1, ctx), "BLOCKED_STATISTICS")
        self.assertFalse(p._blocked_now_computable(e1))

    def test_trial_set_is_the_search_pool_not_the_neighbours(self):
        p, ctx, ids = self._run([EDGE, EDGE2] + NOISE[:7])
        pool = p.search_pool()
        series = {r["strategy_id"] for r in pipeline.iter_jsonl(p.series_path)}
        self.assertTrue(series - pool, "robustness neighbours write DEV series too")
        self.assertEqual(set(p._trial_series()), series & pool)
        dsr = p.evaluation("robustness", ids[0])["checks"]["dsr"]
        self.assertEqual(dsr["trials"], len(series & pool))


    def test_interrupted_dev_evaluation_never_leaves_a_record_without_series(self):
        p = self.pipe(SyntheticProvider())
        sid, _ = p.register(EDGE, source="test")
        real = p._append_eval

        def crash(rec):
            if rec.get("status") == "OK":
                raise KeyboardInterrupt
            return real(rec)
        p._append_eval = crash
        with self.assertRaises(KeyboardInterrupt):
            p.dev_evaluate(sid)
        p._append_eval = real
        self.assertIsNone(p.evaluation("dev", sid))           # resume re-evaluates it
        self.assertIn(sid, {r["strategy_id"] for r in pipeline.iter_jsonl(p.series_path)})
        self.assertEqual(p.dev_evaluate(sid)["status"], "OK")

if __name__ == "__main__":
    unittest.main()
