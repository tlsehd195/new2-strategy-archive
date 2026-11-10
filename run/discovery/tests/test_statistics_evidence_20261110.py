"""ADR 0069: DSR trial count diagnostic, power-analysis simulation uncertainty,
real-fill evidence guards.  Synthetic data only."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import fills_import as fi  # noqa: E402
from test_fills_import_20261020 import ImportCase, synth_market  # noqa: E402
from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider  # noqa: E402


class TestWilson(unittest.TestCase):
    def test_interval_brackets_the_rate_and_shrinks_with_n(self):
        from power_analysis import wilson
        lo, hi = wilson(8, 40)
        self.assertTrue(lo < 0.2 < hi)
        lo2, hi2 = wilson(80, 400)
        self.assertLess(hi2 - lo2, hi - lo)
        self.assertEqual(wilson(0, 40)[0], 0.0)
        self.assertIsNone(wilson(0, 0))

    def test_power_rows_carry_the_interval_and_the_untested_gates(self):
        import json
        import power_analysis as pa
        cfg = json.loads((HERE.parent / "config" / "power_analysis_v1.json").read_text())
        cfg.update(n_sims=4, sr_grid=[0.0, 6.0], stepm_B=20, ci_B=20, dev_days=60, dev_trials=5,
                   scenarios=[{"name": "t", "validation_days": 40, "batch_index": 1}])
        promo, _ = __import__("pipeline").load_promotion_config()
        sc = pa.simulate(cfg, promo)["scenarios"][0]
        for r in sc["rows"]:
            for k, (lo, hi) in r["mc_ci95"].items():
                self.assertTrue(0.0 <= lo <= hi <= 1.0, k)
        self.assertIn("portfolio_validation", sc["gates_not_simulated"])
        self.assertEqual(sc["bootstrap_B_used"]["pipeline_ci"], promo["robustness"]["ci_bootstrap_B"])


class TestDsrTrialCount(PipelineCase):
    def test_full_trial_count_recorded_and_never_more_lenient(self):
        p = self.pipe(SyntheticProvider())
        ids = self.register_all(p, [EDGE] + NOISE[:7])
        d = p._dsr(ids[0])
        self.assertGreaterEqual(d["trials_searched"], d["trials"])
        if d["computable"] and d["dsr_at_trials_searched"] is not None:
            self.assertLessEqual(d["dsr_at_trials_searched"], d["dsr"] + 1e-12)


class TestRealFillGuards(ImportCase):
    def test_declared_real_but_export_says_demo_is_refused(self):
        rows, _, _ = synth_market(3)
        rows[0] = dict(rows[0], Symbol=rows[0]["Symbol"])
        p = self.d / "trades.csv"
        from test_fills_import_20261020 import write_csv
        write_csv(p, rows)
        p.write_text(p.read_text() + "# exported from Demo Trading\n")
        with self.assertRaisesRegex(fi.FillImportError, "contradicts"):
            fi.import_fills(p, data_origin="synthetic_test", account_kind="real")
        # declared demo: imported, but never real evidence
        res = fi.import_fills(p, data_origin="synthetic_test", account_kind="demo")
        self.assertEqual(res["report"]["account_kind"], "demo")

    def test_korean_demo_marker(self):
        p = self.d / "t.csv"
        p.write_text("모의투자 내역\n", encoding="utf-8")
        with self.assertRaises(fi.FillImportError):
            fi.import_fills(p, data_origin="synthetic_test", account_kind="real")

    def test_evidence_status(self):
        self.assertEqual(fi.real_fill_evidence_status([])["status"], "NO_REAL_FILL_EVIDENCE")
        demo = [{"import": {"account_kind": "demo", "valid_fills": 5}}]
        st = fi.real_fill_evidence_status(demo)
        self.assertEqual((st["status"], st["fills_by_account_kind"]), ("NO_REAL_FILL_EVIDENCE", {"demo": 5}))
        real = demo + [{"import": {"account_kind": "real", "valid_fills": 3}}]
        self.assertEqual(fi.real_fill_evidence_status(real)["status"], "REAL_FILLS_DECLARED")


if __name__ == "__main__":
    unittest.main()
