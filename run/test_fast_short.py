"""Tests for the fast short-horizon DEV check (ADR 0045). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import fast_short_dev as x  # noqa: E402

NS = len(x.SYMBOLS)


def synth(nb=96 * 3, price=100.0, rates=None):
    arr = {k: np.full((nb, NS), np.nan) for k in ("open", "close", "qv", "tbq")}
    arr["open"][:] = price
    arr["close"][:] = price
    arr["qv"][:] = 1_000_000.0
    arr["tbq"][:] = 500_000.0
    fund = [(np.array([], dtype=np.int64), np.array([])) for _ in range(NS)]
    if rates is not None:
        fund[0] = (np.array([i * 8 * 3_600_000 for i in range(len(rates))], dtype=np.int64), np.array(rates))
    return {"T0": 0, "n_days": nb // 96, "nb": nb, "arr": arr, "fund": fund, "start_bar": 0}


def ctx_for(seg):
    qv = seg["arr"]["qv"]
    costs = {"measured": bu.load_cost_record(x.COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(x.CALIBRATION)}
    cum = [(ts, np.concatenate([[0.0], np.cumsum(r)])) for ts, r in seg["fund"]]
    return {"qf": qv.copy(), "q30": qv.copy(), "fund_cum": cum, "fns": x.make_cost_fns(costs)}


class FastShortTest(unittest.TestCase):
    def test_prereg_is_the_committed_one(self):
        p = json.loads(x.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        cfgs = x.make_configs()
        self.assertEqual(len(cfgs), 20)
        fam = p["families"]
        self.assertEqual(sum(fam[k]["configs"] for k in fam), 20)
        self.assertEqual(p["universe"]["symbols"], x.SYMBOLS)
        self.assertEqual(len({c["id"] for c in cfgs}), 20)

    def test_never_read_ranges_refused(self):
        with self.assertRaises(SystemExit):
            bu.assert_allowed(bu.ms("2023-09-01"), bu.ms("2023-09-19"))

    def test_hourly_z_excludes_current_hour(self):
        rng = np.random.default_rng(1)
        v = rng.standard_normal((1000, 2))
        idx = np.arange(3, 1000, 4)
        z = x.hourly_z(v, idx, centred=False)
        m = 200
        self.assertAlmostEqual(z[m, 0], v[idx[m], 0] / v[idx[m - 168:m], 0].std(ddof=1))
        self.assertTrue(np.isnan(z[100, 0]))
        zc = x.hourly_z(v, idx, centred=True)
        w = v[idx[m - 168:m], 1]
        self.assertAlmostEqual(zc[m, 1], (v[idx[m], 1] - w.mean()) / w.std(ddof=1))

    def test_funding_credit_and_side(self):
        # settlements at 0h, 8h, 16h; prev rate +10bp -> SHORT, settled 5bp -> short receives +5bp on flat prices
        seg = synth(rates=[0.0010, 0.0005, 0.0])
        cfg = [c for c in x.make_configs() if c["id"] == "F3_th3_w15"][0]
        ev = x.events_for(cfg, seg)
        self.assertEqual(len(ev), 2)  # i=1 (prev .0010) and i=2 (prev .0005 >= 3bp)
        j, k, side, _, hold = ev[0]
        self.assertEqual((k, side, hold), (0, -1.0, 2))
        self.assertEqual((j + 1) * x.BAR_MS, 8 * 3_600_000 - 15 * 60_000)
        res = x.simulate(ev[:1], seg, ctx_for(seg))
        self.assertEqual(len(res["trades"]), 1)
        self.assertAlmostEqual(res["trades"][0]["gross"], 0.0005)

    def test_negative_prev_rate_goes_long_and_pays_when_wrong(self):
        seg = synth(rates=[-0.0010, 0.0005])
        cfg = [c for c in x.make_configs() if c["id"] == "F3_th3_w15"][0]
        ev = x.events_for(cfg, seg)
        self.assertEqual(ev[0][2], 1.0)
        res = x.simulate(ev, seg, ctx_for(seg))
        self.assertAlmostEqual(res["trades"][0]["gross"], -0.0005)  # a long pays positive funding

    def test_one_position_per_symbol_and_cost_split(self):
        seg = synth()
        ctx = ctx_for(seg)
        ev = [(10, 0, 1.0, 1.0, 8), (12, 0, 1.0, 1.0, 8), (18, 0, -1.0, 1.0, 4)]
        res = x.simulate(ev, seg, ctx)
        # second event ignored (still held to bar 19); third enters at bar 19, free at the exit open
        self.assertEqual([(t["e"], t["x"]) for t in res["trades"]], [(11, 19), (19, 23)])
        t = res["trades"][0]
        for r in x.COST_ROWS:
            self.assertAlmostEqual(t["cost"][r], t["cost_e"][r] + t["cost_x"][r])
        self.assertEqual(t["cost"]["cost0"], 0.0)
        self.assertAlmostEqual(t["cost"]["maker_both"], 2 * x.MAKER_FEE)
        self.assertGreater(t["cost"]["measured"], 4 * 0.0005)  # fee + spread both sides
        self.assertGreater(t["cost"]["measured_x2"], t["cost"]["measured"])

    def test_capacity_and_window_end(self):
        seg = synth()
        ctx = ctx_for(seg)
        ev = [(10, k, 1.0, float(NS - k), 8) for k in range(NS)]
        res = x.simulate(ev, seg, ctx)
        self.assertEqual(len(res["trades"]), NS)  # 15 < MAX_OPEN
        res2 = x.simulate([(seg["nb"] - 3, 0, 1.0, 1.0, 8)], seg, ctx)
        self.assertEqual((len(res2["trades"]), res2["skipped_invalid"]), (0, 1))

    def test_daily_booking_exit_day_and_entry_cost_day(self):
        seg = synth()
        res = x.simulate([(90, 0, 1.0, 1.0, 16)], seg, ctx_for(seg))  # entry bar 91 (day 0), exit bar 107 (day 1)
        g, c = x.daily_series(res["trades"], seg)
        self.assertEqual(g[0], 0.0)
        self.assertLess(c["measured"][0], 0.0)
        self.assertLess(c["measured"][1], 0.0)
        self.assertAlmostEqual(c["measured"].sum(), -x.WEIGHT * res["trades"][0]["cost"]["measured"])

    def test_f4_picks_three_and_three(self):
        seg = synth()
        base = np.linspace(1.0, 1.1, NS)
        seg["arr"]["close"][15] = 100.0 * base  # symbol 0 lowest return, symbol 14 highest
        cfg = [c for c in x.make_configs() if c["id"] == "F4_reversal_L4"][0]
        ev = [e for e in x.events_for(cfg, seg) if e[0] == 15]
        self.assertEqual(sorted((e[1], e[2]) for e in ev), [(0, 1.0), (1, 1.0), (2, 1.0), (12, -1.0), (13, -1.0), (14, -1.0)])
        mom = [c for c in x.make_configs() if c["id"] == "F4_momentum_L4"][0]
        ev2 = [e for e in x.events_for(mom, seg) if e[0] == 15]
        self.assertEqual({e[1]: e[2] for e in ev2}[0], -1.0)


if __name__ == "__main__":
    unittest.main()
