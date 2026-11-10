"""Directive 2026-10-20 §5 — the multiple-testing family is the whole search.

promo-v3 splits one FWER budget over the windows of every supported symbol;
promo-v2 records keep their per-window meaning.  Synthetic only.
"""
from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from pipeline import DEFAULT_PROMOTION_CONFIG, alpha_family, load_promotion_config, window_alpha_total  # noqa: E402
from stats_mc import spending_alpha  # noqa: E402
from strategy_spec import SUPPORTED_SYMBOLS  # noqa: E402
from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider  # noqa: E402


class TestSearchWideFamily(unittest.TestCase):
    def test_v3_only_tightens_v2(self):
        # the default is promotion_v5.json since ADR 0064 (tested there); v3 keeps its meaning
        v3, _ = load_promotion_config(DISC / "config" / "promotion_v3.json")
        self.assertEqual(DEFAULT_PROMOTION_CONFIG.name, "promotion_v5.json")
        v2 = json.loads((DISC / "config" / "promotion_v2.json").read_text())
        for sec in ("validation", "locked"):
            self.assertLessEqual(window_alpha_total(v3[sec]), window_alpha_total(v2[sec]))
            same = {k: v for k, v in v3[sec].items() if not k.startswith("alpha")}
            self.assertEqual(same, {k: v for k, v in v2[sec].items() if not k.startswith("alpha")})
        for sec in ("dev_screen", "robustness", "promotion", "paper"):
            self.assertEqual(v3[sec], v2[sec])

    def test_union_bound_over_every_window_and_batch(self):
        v3, _ = load_promotion_config()
        per = window_alpha_total(v3["validation"])
        total = len(SUPPORTED_SYMBOLS) * sum(spending_alpha(k, per) for k in range(1, 20000))
        self.assertLessEqual(total, v3["validation"]["alpha_total_per_search"] + 1e-12)
        self.assertEqual(alpha_family(v3["validation"])["scope"], "search")
        self.assertEqual(alpha_family({"alpha_total_per_window": 0.05})["scope"], "window")

    def test_history_wide_bound_is_reported_honestly(self):
        """7 earlier batches per window were spent under v2 (0.05 per window)."""
        c = 6 / math.pi ** 2
        first7 = sum(1 / k ** 2 for k in range(1, 8))
        per_window = 0.05 * c * first7 + 0.025 * c * (math.pi ** 2 / 6 - first7)
        self.assertAlmostEqual(2 * per_window, 0.0959, places=3)


class TestBatchRecordsTheFamily(PipelineCase):
    def test_preregistered_batch_carries_family_and_alpha(self):
        p = self.pipe(SyntheticProvider())
        self.register_all(p, [EDGE] + NOISE[:3])
        b = p.preregister_batch(p.validation_candidates()[:2] or [p.validation_candidates()[0]])
        for w in b["windows"].values():
            self.assertEqual(w["alpha_family"]["scope"], "search")
            self.assertAlmostEqual(w["alpha"], spending_alpha(w["batch_index"], 0.05 / len(SUPPORTED_SYMBOLS)))


if __name__ == "__main__":
    unittest.main()
