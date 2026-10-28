"""Tests for the wide-universe DEV check (ADR 0044). Needs numpy and pandas."""

import itertools
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu
import wide_universe_dev as w


def frame(a: np.ndarray, names: list) -> pd.DataFrame:
    return pd.DataFrame(a, index=np.arange(a.shape[0]), columns=names)


class WideUniverseTest(unittest.TestCase):
    def test_prereg_is_the_committed_one(self):
        p = json.loads(w.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual([h["id"] for h in p["hypotheses"]], list(w.HYPS))
        for h in p["hypotheses"]:
            self.assertEqual(h["long"] == "highest", w.HYPS[h["id"]][0])
            self.assertEqual(h["hold_days"], w.HYPS[h["id"]][1])
        self.assertEqual(p["universe"]["FLOOR_usdt_per_day"], w.FLOOR)
        self.assertEqual(p["universe"]["min_universe_size"], w.MIN_UNIVERSE)
        self.assertEqual(w.N_TRIALS, 15)

    def test_never_read_ranges_refused(self):
        for lo, hi in (("2020-12-01", "2021-01-31"), ("2023-09-01", "2023-09-19"), ("2025-10-01", "2025-10-21")):
            with self.assertRaises(SystemExit):
                bu.assert_allowed(bu.ms(lo), bu.ms(hi))

    def test_rebalance_days_follow_the_hold(self):
        lo, hi, first = w.SEGMENTS["S1"]
        days = np.arange(bu.ms(lo), bu.ms(hi) + 1, bu.DAY_MS)
        r7, r28 = w.rebalance_days(days, first, 7), w.rebalance_days(days, first, 28)
        self.assertEqual(int(days[r7[0]]), bu.ms("2021-03-01"))  # a Monday
        self.assertEqual(r7[0], r28[0])
        self.assertTrue(all(b - a == 7 for a, b in itertools.pairwise(r7)))
        self.assertTrue(all(b - a == 28 for a, b in itertools.pairwise(r28)))

    def test_eligibility_floor_and_ranking(self):
        n = 100
        names = ["A", "B", "C", "D"]
        close = np.ones((n, 4))
        qv = np.tile([5e6, 9e6, 1.9e6, 3e6], (n, 1))  # C is under the 2M floor
        piv = {"open": frame(close, names), "close": frame(close, names), "qv": frame(qv, names)}
        nb = piv["close"].notna().cumsum()
        self.assertEqual(w.eligible(piv, nb, 90), ["B", "A", "D"])
        short = nb.copy()
        short.iloc[88, 0] = 10  # A has fewer than 60 bars at the cut-off
        self.assertNotIn("A", w.eligible(piv, short, 90))

    def test_universe_slices(self):
        ranked = [f"S{k:03d}" for k in range(260)]
        self.assertEqual(len(w.slice_universe(ranked, "U50")), 50)
        self.assertEqual(len(w.slice_universe(ranked, "W200")), 200)
        t = w.slice_universe(ranked, "T150")
        self.assertEqual((t[0], len(t)), ("S050", 150))
        self.assertEqual(w.slice_universe(ranked[:60], "T150"), [])  # 10 names < 30
        self.assertEqual(w.slice_universe(ranked[:20], "U50"), [])

    def test_pick_quintiles(self):
        for n, k in ((50, 10), (200, 40), (150, 30)):
            names = [f"S{i:03d}" for i in range(n)]
            sig = pd.Series(np.arange(n, dtype=float), index=names)
            wt = w.pick(sig, names, True)
            self.assertEqual(sum(1 for v in wt.values() if v > 0), k)
            self.assertEqual(sum(1 for v in wt.values() if v < 0), k)
            self.assertAlmostEqual(sum(abs(v) for v in wt.values()), 1.0)
            self.assertAlmostEqual(sum(wt.values()), 0.0)
            self.assertTrue(all(wt[f"S{n - 1 - i:03d}"] > 0 for i in range(k)))  # highest are long
            wl = w.pick(sig, names, False)
            self.assertTrue(all(wl[f"S{i:03d}"] > 0 for i in range(k)))  # lowest are long
        names = [f"S{i:03d}" for i in range(50)]
        sig = pd.Series(np.arange(50, dtype=float), index=names)
        sig[names[:35]] = np.nan  # fewer valid names than 2K -> skip
        self.assertEqual(w.pick(sig, names, True), {})

    def test_taker_flow_hand_checked(self):
        n = 40
        qv = np.full((n, 2), 100.0)
        tbq = qv * np.array([0.6, 0.4])
        piv = {"qv": frame(qv, ["A", "B"]), "tbq": frame(tbq, ["A", "B"])}
        f = w.taker_flow(piv, n - 1)
        self.assertAlmostEqual(f["A"], 0.6)
        self.assertAlmostEqual(f["B"], 0.4)
        piv["tbq"].iloc[-1, 0] = np.nan
        self.assertTrue(math.isnan(w.taker_flow(piv, n - 1)["A"]))

    def test_cost_rows(self):
        cal = {**json.loads(w.CALIBRATION.read_text()), "maxp": 0.05}
        thin, thick = 50_000.0, 2_000_000.0
        for q in (thin, thick):
            c = w.side_cost("cal", cal, q, q)
            self.assertGreater(c, 0.0005)
            self.assertAlmostEqual(w.side_cost("cal_x2", cal, q, q), 2 * c)
            self.assertEqual(w.side_cost("cost0", cal, q, q), 0.0)
        self.assertAlmostEqual(w.side_cost("cal_tail_x2", cal, thin, thin), 2 * w.side_cost("cal", cal, thin, thin))
        self.assertAlmostEqual(w.side_cost("cal_tail_x2", cal, thick, thick), w.side_cost("cal", cal, thick, thick))
        self.assertGreater(w.side_cost("cal", cal, thin, thin), w.side_cost("cal", cal, thick, thick))

    def test_effective_names_independent_vs_one_factor(self):
        rng = np.random.default_rng(1)
        n_days, n = 200, 40
        names = [f"S{i}" for i in range(n)]
        px = np.cumprod(1.0 + rng.normal(0, 0.02, (n_days, n)), axis=0)
        e_ind = w.effective_names(frame(px, names), n_days - 1, names)
        factor = rng.normal(0, 0.02, (n_days, 1))
        px2 = np.cumprod(1.0 + 0.95 * factor + 0.05 * rng.normal(0, 0.02, (n_days, n)), axis=0)
        e_fac = w.effective_names(frame(px2, names), n_days - 1, names)
        self.assertIsNotNone(e_ind)
        self.assertGreater(e_ind["n_eff"], 0.6 * n)  # near n for independent names
        self.assertLess(e_fac["n_eff"], 3.0)  # a single common factor ~ 1-2 names
        self.assertGreater(e_fac["rho"], 0.9)
        self.assertLess(abs(e_ind["rho"]), 0.15)

    def test_effective_names_drops_short_history(self):
        names = [f"S{i}" for i in range(20)]
        rng = np.random.default_rng(2)
        px = np.cumprod(1.0 + rng.normal(0, 0.02, (150, 20)), axis=0)
        px[:100, :5] = np.nan  # only ~50 returns: below 60
        e = w.effective_names(frame(px, names), 149, names)
        self.assertEqual(e["n"], 15)

    def test_min_detectable_sr(self):
        self.assertGreater(w.min_detectable_sr(2.6, 15), w.min_detectable_sr(2.6, 1))
        self.assertAlmostEqual(w.min_detectable_sr(4.0, 1), (1.6449 + 0.8416) / 2.0, places=3)

    def test_flag_requires_every_condition(self):
        def st(sr, at=3.0):
            return {"sr_ann": sr, "alpha_t": at}
        row = {"S1": {"cal": st(2.0), "cal_x2": st(1.0), "cal_tail_x2": st(0.5), "halves_sr_ann": [1.0, 1.0]},
               "S2": {"cal": st(1.0)}}
        self.assertEqual(w.flag(row, 1.5), "DEV_PROMISING")
        for mut in (lambda r: r["S1"].update(cal=st(1.0)), lambda r: r["S1"].update(cal=st(2.0, 1.0)),
                    lambda r: r["S1"].update(halves_sr_ann=[1.0, -0.1]), lambda r: r["S2"].update(cal=st(0.4)),
                    lambda r: r["S1"].update(cal_x2=st(-0.1)), lambda r: r["S1"].update(cal_tail_x2=st(-0.1))):
            r2 = json.loads(json.dumps(row))
            mut(r2)
            self.assertEqual(w.flag(r2, 1.5), "NOT_PROMISING")


if __name__ == "__main__":
    unittest.main()
