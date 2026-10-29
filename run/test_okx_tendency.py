"""Tests for the OKX-tendency DEV rules (ADR 0052). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import okx_tendency_dev as x  # noqa: E402

NS = len(x.SYMBOLS)


def synth(nb=96 * 4):
    a = {k: np.full((nb, NS), np.nan) for k in ("open", "high", "low", "close", "qv", "tbq")}
    for k in ("open", "high", "low", "close"):
        a[k][:] = 100.0
    a["qv"][:] = 1_000_000.0
    a["tbq"][:] = 500_000.0
    return {"T0": 0, "n_days": nb // 96, "nb": nb, "arr": a, "fund": [], "start_bar": 0}


class OkxTendencyTest(unittest.TestCase):
    def test_prereg_is_the_committed_one(self):
        p = json.loads(x.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        cfgs = x.make_configs()
        self.assertEqual(len(cfgs), 26)
        self.assertEqual(p["rules"]["T1_short_upper"]["configs"] + p["rules"]["T2_long_mid_low"]["configs"], 12)
        u = json.loads(x.PREREG_U.read_text())
        self.assertEqual(u["grid_U"]["configs"], len(x.make_u_configs()))
        self.assertEqual(p["universe"]["symbols"], x.SYMBOLS)
        self.assertEqual(len({c["id"] for c in cfgs}), 26)

    def test_never_read_ranges_refused(self):
        with self.assertRaises(SystemExit):
            bu.assert_allowed(bu.ms("2023-09-01"), bu.ms("2023-09-19"))

    def test_pos24_uses_bars_up_to_j_only(self):
        s = synth()
        a = s["arr"]
        a["high"][200:, 0] = 110.0   # range top appears at bar 200
        a["low"][:, 0] = 90.0
        a["close"][250, 0] = 108.0
        f = x.features(a)
        self.assertAlmostEqual(f["pos"][250, 0], (108.0 - 90.0) / (110.0 - 90.0))
        self.assertAlmostEqual(f["pos"][199, 0], (100.0 - 90.0) / (100.0 - 90.0))  # later high not seen at 199
        self.assertTrue(np.isnan(f["pos"][10, 1]))  # lookback incomplete

    def test_t1_short_after_drop_and_low_volume(self):
        s = synth()
        a = s["arr"]
        a["low"][:, 0] = 90.0
        a["high"][:, 0] = 110.0
        j = 243  # hourly decision bar
        a["close"][j, 0] = 108.0           # pos .9
        a["open"][j - 15, 0] = 109.0       # ret4h < 0
        a["qv"][j - 3:j + 1, 0] = 100_000.0  # quiet volume
        cfgs = {c["id"]: c for c in x.make_configs()}
        for cid, expect in (("T1_d0_v0_H16", True), ("T1_d1_v0_H16", True), ("T1_d1_v1_H16", True)):
            ev = [e for e in x.events_for(cfgs[cid], dict(s)) if e[0] == j and e[1] == 0]
            self.assertEqual(bool(ev), expect, cid)
            self.assertEqual(ev[0][2], -1.0)
        a["open"][j - 15, 0] = 100.0       # price rose in the last 4h -> after-drop filter off
        s.pop("feat", None)
        self.assertFalse([e for e in x.events_for(cfgs["T1_d1_v0_H16"], dict(s)) if e[0] == j and e[1] == 0])
        self.assertTrue([e for e in x.events_for(cfgs["T1_d0_v0_H16"], dict(s)) if e[0] == j and e[1] == 0])

    def test_t2_band_edges_and_long_side(self):
        s = synth()
        a = s["arr"]
        a["low"][:, 1] = 90.0
        a["high"][:, 1] = 110.0
        j = 243
        cfg = [c for c in x.make_configs() if c["id"] == "T2_v0_H16"][0]
        for close, inside in ((96.0, True), (99.0, True), (99.5, False), (95.9, False)):
            a["close"][j, 1] = close
            s.pop("feat", None)
            ev = [e for e in x.events_for(cfg, dict(s)) if e[0] == j and e[1] == 1]
            self.assertEqual(bool(ev), inside, close)
            if ev:
                self.assertEqual(ev[0][2], 1.0)

    def _u_seg(self):
        s = synth()
        a = s["arr"]
        a["high"][:] = 100.5
        a["low"][:] = 99.5
        a["open"][:] = 100.0
        a["close"][:] = 100.2
        return s, a

    def test_u_long_bull_candle_near_high_shorts_with_mild_volume(self):
        s, a = self._u_seg()
        j = 300
        a["open"][j, 0], a["close"][j, 0] = 100.0, 100.9   # body .9 vs mean .2 -> >= 3x; body share 1
        a["high"][j, 0], a["low"][j, 0] = 101.0, 99.95
        a["qv"][j, 0] = 1_100_000.0                      # 1.1x the last 4h
        cfg = {c["id"]: c for c in x.make_configs()}["U_N96_m3_H4"]
        ev = [e for e in x.events_for(cfg, s) if e[1] == 0]
        self.assertEqual([(e[0], e[2]) for e in ev], [(j, -1.0)])
        a["qv"][j, 0] = 1_000_000.0                      # not strictly more
        s2, a2 = self._u_seg()
        a2["open"][j, 0], a2["close"][j, 0], a2["high"][j, 0], a2["low"][j, 0] = 100.0, 100.9, 101.0, 99.95
        a2["qv"][j, 0] = 1_000_000.0
        self.assertFalse([e for e in x.events_for(cfg, s2) if e[1] == 0])

    def test_u_bear_candle_near_low_goes_long_and_follow_mirrors(self):
        s, a = self._u_seg()
        j = 300
        a["open"][j, 1], a["close"][j, 1] = 100.0, 99.1
        a["high"][j, 1], a["low"][j, 1] = 100.05, 99.0
        a["qv"][j, 1] = 2_000_000.0
        cfgs = {c["id"]: c for c in x.make_configs()}
        ev = [e for e in x.events_for(cfgs["U_N96_m2_H4"], s) if e[1] == 1]
        self.assertEqual([(e[0], e[2]) for e in ev], [(j, 1.0)])
        evf = [e for e in x.events_for(cfgs["Uf_N96_m2_H4"], s) if e[1] == 1]
        self.assertEqual([(e[0], e[2]) for e in evf], [(j, -1.0)])

    def test_u_line_excludes_current_bar(self):
        s, a = self._u_seg()
        j = 300
        a["open"][j, 0], a["close"][j, 0], a["high"][j, 0], a["low"][j, 0] = 100.0, 100.9, 101.0, 99.95
        f = x.u_features(a, 96)
        # the candle's own high 101.0 must not enter its line: p = (100.9 - 99.5) / (100.5 - 99.5) = 1.4
        self.assertAlmostEqual(f["p"][j, 0], 1.4)

    def test_limit_ratio_row_is_70_percent_of_measured(self):
        costs = {"measured": bu.load_cost_record(x.fs.COST_RECORD), "assumed": bu.load_cost(),
                 "volume_scaled": bu.load_cost_calibration(x.fs.CALIBRATION)}
        fns = x.make_cost_fns(costs)
        m = fns["one"]("measured", 1e6, 1e6, True)
        self.assertAlmostEqual(fns["one"]("limit_ratio070", 1e6, 1e6, True), 0.70 * m)


if __name__ == "__main__":
    unittest.main()
