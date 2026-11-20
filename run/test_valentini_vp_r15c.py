"""Tests for round 15c (ADR 0113): volume-profile approximation of Valentini rules. Needs numpy."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import valentini_vp_r15c_dev as vp  # noqa: E402


class ValentiniVpR15cTest(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(vp.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["n_trials"], len(vp.CONFIGS))
        self.assertEqual(p["context_id"], vp.CONTEXT_ID)
        self.assertEqual(sorted(p["trials"]), sorted(vp.CONFIGS))

    def test_profile(self):
        price = np.concatenate([np.full(80, 100.0), np.linspace(90, 110, 16)])
        vol = np.ones(96)
        poc, val, vah = vp.profile(price, vol)
        self.assertTrue(val <= 100.0 <= vah)
        self.assertTrue(val < poc < vah)
        self.assertGreater(vah, val)
        self.assertIsNone(vp.profile(price[:80], vol[:80]))

    def _seg(self, n_days=3):
        nb = n_days * 96
        base = np.full((nb, 15), np.nan)
        arr = {k: base.copy() for k in ("open", "close", "qv", "tbq")}
        rng = np.random.default_rng(0)
        p = 100 + rng.normal(0, 0.2, nb)
        p[:96] = np.linspace(95, 105, 96)  # day 0: flat profile, VA about 96.5..103.5
        for k in ("open", "close"):
            arr[k][:, 0] = p
        arr["qv"][:, 0] = 1.0
        arr["tbq"][:, 0] = 0.5
        return {"arr": arr, "nb": nb, "n_days": n_days}

    def test_vp1_long_trigger_and_exit_inside(self):
        seg = self._seg()
        a = seg["arr"]
        j = 96 + 56
        a["close"][j, 0], a["qv"][j, 0], a["tbq"][j, 0] = 105.0, 10.0, 8.0
        a["close"][j + 1: j + 3, 0] = 106.0  # above VAH, below VAH + width
        a["close"][j + 3, 0] = 100.0  # back inside -> exit next open
        ev = vp.events_for("VP1_NY_IMBALANCE", seg)
        self.assertEqual([(e[0], e[2], e[4]) for e in ev if e[0] // 96 == 1], [(j, 1.0, 3)])

    def test_vp2_short_after_outside(self):
        seg = self._seg()
        a = seg["arr"]
        d = 96 + 30
        a["close"][d - 2, 0] = 110.0          # traded above VAH earlier in the day
        a["close"][d, 0], a["tbq"][d, 0] = 100.5, 0.3  # back inside with selling
        ev = vp.events_for("VP2_LONDON_REVERSION", seg)
        day1 = [e for e in ev if e[0] // 96 == 1]
        self.assertEqual(len(day1), 1)
        self.assertEqual(day1[0][2], -1.0)
        self.assertLessEqual(day1[0][0] + 1 + day1[0][4], 96 + 48)


if __name__ == "__main__":
    unittest.main()
