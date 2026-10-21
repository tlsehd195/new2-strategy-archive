import hashlib
import json
import sys
import unittest
from pathlib import Path

_P = str(Path(__file__).resolve().parents[1])
if _P not in sys.path:
    sys.path.insert(0, _P)

import bundle_stable as B  # noqa: E402

D = B.DAY_MS


def _ev(entry_day, exit_day, net, gross=None):
    return {"entry_ts": entry_day * D, "exit_ts": exit_day * D + 1, "net_bp": net,
            "gross_bp": net if gross is None else gross}


class BundleStableTests(unittest.TestCase):
    def test_sizing_never_above_one_capital(self):
        self.assertEqual(B.max_concurrent([_ev(0, 2, 1), _ev(1, 3, 1), _ev(4, 5, 1)]), 2)
        # two symbols, one with two overlapping events: weight 1/(2*2) there
        d = B.sized_daily({"A": [_ev(0, 2, 100), _ev(1, 3, 100)], "B": [_ev(0, 1, 100)]})
        self.assertAlmostEqual(d[2], 0.25)
        self.assertAlmostEqual(d[3], 0.25)
        self.assertAlmostEqual(d[1], 0.5)

    def test_perf(self):
        p = B.perf([1.0, -2.0, 1.0, 1.0], [0, 1, 2, 3])
        self.assertAlmostEqual(p["mdd_pct"], 2.0)
        self.assertAlmostEqual(p["total_pct"], 1.0)
        self.assertAlmostEqual(p["ann_return_pct"], 0.25 * 365)

    def test_cluster_pick_one_per_correlated_group(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0, 1.0]
        s = {"a1": a, "a2": [v * 2 + 0.1 for v in a], "b": [5.0, 1.0, 4.0, 1.0, 3.0, 2.0]}
        pick = B.cluster_pick(s)
        self.assertEqual(len(pick), 2)
        self.assertIn("b", pick)

    def test_weights(self):
        w = B.invvol_weights({"x": [1.0, -1.0, 1.0], "y": [2.0, -2.0, 2.0], "z": [0.0, 0.0, 0.0]})
        self.assertAlmostEqual(w["x"], 2 / 3)
        self.assertEqual(w["z"], 0.0)
        t = B.dir_thirds({"l1": "long", "l2": "long", "s": "short"})
        self.assertEqual(t, {"l1": 0.25, "l2": 0.25, "s": 0.5})

    def test_label(self):
        good = {"sharpe_ann": 1.0, "ann_return_pct": 5.0, "mdd_pct": 5.0}
        self.assertEqual(B.stable_label(good, {"ann_return_pct": 1.0}, {"ann_return_pct": 1.0}), "DEV_STABLE")
        self.assertEqual(B.stable_label(good, {"ann_return_pct": -1.0}, {"ann_return_pct": 1.0}), "NOT_STABLE")
        self.assertEqual(B.stable_label({**good, "mdd_pct": 20.0}, {"ann_return_pct": 1.0},
                                        {"ann_return_pct": 1.0}), "NOT_STABLE")

    def test_prereg_matches_code(self):
        cfg = json.loads(B.PREREG.read_text())
        self.assertEqual(cfg["id"], "bundle-stable-v1")
        self.assertEqual(cfg["configurations_counted"], 6)
        self.assertEqual([b["id"] for b in cfg["bundles"]],
                         ["B1_EW_ALL", "B2_DIR_THIRDS", "B3_CLUSTER", "B4_INVVOL"])
        self.assertIn("1 - corr = 0.5", cfg["bundles"][2]["rule"])
        self.assertIn("Sharpe >= 0.5", cfg["stable_label"]["rule"])
        self.assertIn("<= 15%", cfg["stable_label"]["rule"])
        self.assertEqual(hashlib.sha256(B.PREREG.read_bytes()).hexdigest()[:8], "fda77643")


if __name__ == "__main__":
    unittest.main()
