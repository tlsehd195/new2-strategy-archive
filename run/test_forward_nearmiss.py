"""Tests for the forward paper record of CL1/FIP1/AF1 (ADR 0112). Synthetic data only."""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import forward_nearmiss as fw  # noqa: E402


def synthetic(data: Path, n_sym: int = 60, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    for d in ("daily", "funding", "bar2345"):
        (data / d).mkdir(parents=True)
    days = np.arange(bu.ms("2026-05-01"), bu.ms("2027-01-01"), bu.DAY_MS)
    for k in range(n_sym):
        sym = f"S{k:02d}USDT"
        c = 10 * np.exp(np.cumsum(rng.normal(0, 0.03, len(days))))
        o = np.r_[c[0], c[:-1]]
        qv = rng.uniform(5e6, 5e7, len(days)) * (1 + k)
        by = {}
        for t, oo, cc, q in zip(days, o, c, qv):
            ym = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m")
            hi, lo = max(oo, cc) * 1.02, min(oo, cc) * 0.98
            by.setdefault(ym, []).append(f"{t},{oo},{hi},{lo},{cc},1,{t + 1},{q},{int(q / 1e4)},1,{q / 2},0")
            (data / "funding" / f"{sym}-fundingRate-{ym}.csv").open("a").write(
                f"{t},8,{rng.normal(1e-4, 1e-4)}\n")
            (data / "bar2345" / f"{sym}-2345-{ym}.csv").open("a").write(f"{t + 85_500_000},{q / 96}\n")
        for ym, rows in by.items():
            (data / "daily" / f"{sym}-1d-{ym}.csv").write_text("\n".join(rows) + "\n")


class ForwardNearMissTest(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(fw.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["forward_start"], fw.FORWARD_START)
        self.assertEqual(sorted(p["candidates"]), sorted(h for h, _ in fw.KEYS))
        self.assertEqual(p["alpha_family"], fw.ALPHA_FAMILY)

    def test_guard_blocks_outside_forward_reads(self):
        g = fw.guard("2026-12-31")
        with mock.patch.object(bu, "NEVER", g):
            bu.assert_allowed(bu.ms("2026-05-01"), bu.ms("2026-12-31"))
            for lo, hi in (("2026-04-30", "2026-05-02"), ("2027-01-01", "2027-01-02"),
                           ("2020-06-01", "2020-06-02"), ("2024-01-01", "2024-01-02")):
                with self.assertRaises(SystemExit):
                    bu.assert_allowed(bu.ms(lo), bu.ms(hi))
        with self.assertRaises(SystemExit):  # the real DEV guard is untouched afterwards
            bu.assert_allowed(bu.ms("2026-06-01"), bu.ms("2026-06-02"))

    def test_holm_and_stages(self):
        self.assertEqual(fw.holm({"a": 0.001, "b": 0.5}, 0.0213), {"a": True, "b": False})
        rng = np.random.default_rng(1)
        s = {"k": {c: rng.normal(0, 0.01, 100) for c in ("cal", "cal_x2", "cost0")}}
        self.assertEqual(fw.verdicts(s, 100)["stage"], "OBSERVING")
        s = {"k": {c: rng.normal(-0.003, 0.01, 200) for c in ("cal", "cal_x2", "cost0")}}
        self.assertEqual(fw.verdicts(s, 200)["tests"]["k"]["verdict"], "FORWARD_FAIL_FUTILITY")

    def test_end_to_end_synthetic(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            synthetic(data)
            fw.r11._EXTRA.clear()
            with mock.patch.object(fw, "prereg_commit_time",
                                   return_value=dt.datetime.fromisoformat("2026-11-20T12:00:00+00:00")):
                out = fw.evaluate(data, dt.date(2027, 1, 5))
            self.assertEqual(out["read"], ["2026-05-01", "2026-12-31"])
            self.assertEqual(out["stage"], "OBSERVING")
            self.assertGreater(out["forward_days"], 30)
            self.assertEqual(sorted(out["tests"]), sorted(f"{u}:{h}" for h, u in fw.KEYS))
            with self.assertRaises(SystemExit):
                fw.evaluate(data, dt.date(2026, 11, 25))


if __name__ == "__main__":
    unittest.main()
