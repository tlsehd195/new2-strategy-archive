"""promo-v4 DRAFT (ADR 0028): bootstrap-t StepM and max_pbo 0.05; never looser
than promo-v3 and not the default.  IW1 v2 DRAFT changes only studentize.

    cd run && python3 -m unittest discovery.tests.test_promo_v4_20261022
"""

import hashlib
import json
import sys
import unittest
from pathlib import Path

DISC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISC))

import pipeline  # noqa: E402

V3 = json.loads((DISC / "config" / "promotion_v3.json").read_text())
V4 = json.loads((DISC / "config" / "promotion_v4.json").read_text())
IW1 = DISC / "config" / "independent_window_v1.json"
IW2 = DISC.parent / "audit" / "evidence_paper_faithful_20261022" / "independent_window_v2_DRAFT.json"


class PromoV4Test(unittest.TestCase):
    def test_v3_is_still_the_default(self):
        self.assertEqual(pipeline.DEFAULT_PROMOTION_CONFIG.name, "promotion_v3.json")
        self.assertNotIn("studentize", V3["validation"])

    def test_v4_loads_and_is_a_draft(self):
        cfg, h = pipeline.load_promotion_config(DISC / "config" / "promotion_v4.json")
        self.assertEqual(cfg["version"], "promo-v4")
        self.assertTrue(cfg["status"].startswith("DRAFT_NOT_DEFAULT"))
        self.assertEqual(len(h), 16)

    def test_only_the_two_changes_and_both_stricter(self):
        self.assertEqual(V4["validation"]["studentize"], "bootstrap_t")
        self.assertLess(V4["robustness"]["max_pbo"], V3["robustness"]["max_pbo"])
        self.assertEqual(V4["robustness"]["max_pbo"], 0.05)
        for sec in ("dev_screen", "locked", "promotion", "paper"):
            self.assertEqual(V4[sec], V3[sec], sec)
        rob = {k: v for k, v in V4["robustness"].items() if k != "max_pbo"}
        self.assertEqual(rob, {k: v for k, v in V3["robustness"].items() if k != "max_pbo"})
        val = {k: v for k, v in V4["validation"].items() if k not in ("studentize", "method")}
        self.assertEqual(val, {k: v for k, v in V3["validation"].items() if k != "method"})


class IW1DraftTest(unittest.TestCase):
    def test_v1_untouched_and_draft_changes_only_studentize(self):
        raw = IW1.read_bytes()
        v1 = json.loads(raw)
        v2 = json.loads(IW2.read_text())
        self.assertNotIn("studentize", v1["test"])
        self.assertEqual(v2["supersedes"]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertTrue(v2["status"].startswith("DRAFT_NOT_IN_FORCE"))
        self.assertEqual(v2["test"]["studentize"], "bootstrap_t")
        skip = {"status", "supersedes", "change_from_v1", "test"}
        self.assertEqual({k: v for k, v in v2.items() if k not in skip},
                         {k: v for k, v in v1.items() if k not in skip})
        t2 = {k: v for k, v in v2["test"].items() if k not in ("studentize", "method")}
        self.assertEqual(t2, {k: v for k, v in v1["test"].items() if k != "method"})


if __name__ == "__main__":
    unittest.main()
