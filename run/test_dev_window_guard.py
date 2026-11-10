"""DEV-window guard of the shared loaders (ADR 0066): funding and 2345-bar
reads refuse a range that touches a never-read window (validation gap,
pre-2021, after 2025-10-20).  Synthetic files only.  Needs pandas."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "discovery"))

import broad_universe_dev as bu  # noqa: E402


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "funding").mkdir()
        (self.d / "bar2345").mkdir()
        t = bu.ms("2023-09-18")
        (self.d / "funding" / "AUSDT-fundingRate-2023-09.csv").write_text(
            "calc_time,funding_interval_hours,last_funding_rate\n"
            f"{t},8,0.0001\n{t + bu.DAY_MS},8,0.0002\n{t + 5 * bu.DAY_MS},8,0.0003\n")

    def test_dev_segment_reads(self):
        lo, hi, _ = bu.SEGMENTS["S1"]
        f = bu.load_funding(self.d, ["AUSDT"], bu.ms(lo), bu.ms(hi))
        # the settlement at 00:00 after the last DEV day is the only one past hi
        self.assertEqual(sorted(f["t"]), [bu.ms("2023-09-18"), bu.ms("2023-09-19")])
        bu.load_bar2345(self.d, ["AUSDT"], bu.ms(lo), bu.ms(hi))

    def test_never_read_ranges_refused(self):
        for lo, hi in (("2021-01-01", "2025-10-20"), ("2023-09-19", "2023-10-01"),
                       ("2020-12-01", "2021-02-01"), ("2025-10-01", "2025-10-21")):
            with self.subTest(lo=lo, hi=hi):
                with self.assertRaises(SystemExit):
                    bu.load_funding(self.d, ["AUSDT"], bu.ms(lo), bu.ms(hi))
                with self.assertRaises(SystemExit):
                    bu.load_bar2345(self.d, ["AUSDT"], bu.ms(lo), bu.ms(hi))

    def test_newlisting_loads_funding_per_segment(self):
        src = (Path(__file__).resolve().parent / "newlisting_short_dev.py").read_text()
        self.assertNotIn('bu.load_funding(data, [sym], bu.ms("2021-01-01"), bu.ms("2025-10-20"))', src)


if __name__ == "__main__":
    unittest.main()
