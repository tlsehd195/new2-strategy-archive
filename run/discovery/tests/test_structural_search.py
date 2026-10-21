"""Structural DEV-only search (ADR 0020): forbidden windows, no look-ahead in
the funding signal, funding credited to the holding day, CC basis left at 0.
Synthetic data and temp dirs only."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RUN = HERE.parent.parent
if str(RUN) not in sys.path:
    sys.path.insert(0, str(RUN))

import structural_search_dev as ssd  # noqa: E402

DAY = ssd.DAY_MS
BAR = ssd.BAR_MS


def _write(root: Path, sym: str, start: str, days: int, rates: list) -> None:
    (root / "klines").mkdir(parents=True, exist_ok=True)
    (root / "funding").mkdir(parents=True, exist_ok=True)
    t0 = ssd.day_ms(start)
    month = start[:7]
    with (root / "klines" / f"{sym}-15m-{month}.csv").open("w") as fh:
        for k in range(days * 96):
            t = t0 + k * BAR
            px = 100.0 + k * 0.01
            fh.write(f"{t},{px},{px},{px},{px},1,{t + BAR - 1},1e9,1,0,0,0\n")
    with (root / "funding" / f"{sym}-fundingRate-{month}.csv").open("w") as fh:
        fh.write("calc_time,funding_interval_hours,last_funding_rate\n")
        for j, r in enumerate(rates):
            fh.write(f"{t0 + j * 8 * 3_600_000 + 2},8,{r}\n")


class StructuralSearchTest(unittest.TestCase):
    def test_forbidden_windows(self):
        for sym, lo, hi in (("ADAUSDT", "2020-12-01", "2021-02-01"),
                            ("ADAUSDT", "2023-09-01", "2023-10-01"),
                            ("ADAUSDT", "2025-10-21", "2025-11-01"),
                            ("BTCUSDT", "2022-01-01", "2022-02-01"),
                            ("ETHUSDT", "2022-01-01", "2022-02-01")):
            with self.assertRaises(SystemExit):
                ssd.assert_allowed(sym, lo, hi)
        ssd.assert_allowed("ADAUSDT", "2021-01-01", "2023-09-18")
        ssd.assert_allowed("ADAUSDT", "2024-06-19", "2025-10-20")

    def test_signal_uses_only_settlements_before_the_open(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rates = [float(j + 1) * 1e-5 for j in range(30)]       # 10 days, 3 a day
            _write(root, "ADAUSDT", "2021-03-01", 10, rates)
            sg = ssd.Seg("T", {"read": ["2021-03-01", "2021-03-10"], "entry_from": "2021-03-05"},
                         ["ADAUSDT"], root)
            # day 4 open (2021-03-05 00:00): settlements strictly before it are j=0..11;
            # the one at 00:00:00.002 of that day (j=12) is not used
            self.assertAlmostEqual(sg.f_mean3[0, 4], np.mean(rates[9:12]))
            # holding day 4 earns j=13, 14 (08:00, 16:00) and j=15 (next 00:00)
            self.assertAlmostEqual(sg.dfund[0, 4], sum(rates[13:16]))

    def test_cash_and_carry_ignores_price_moves(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "ADAUSDT", "2021-03-01", 10, [1e-4] * 30)
            sg = ssd.Seg("T", {"read": ["2021-03-01", "2021-03-10"], "entry_from": "2021-03-02"},
                         ["ADAUSDT"], root)
            cost = ssd.load_cost()
            x, inv = ssd.run_cc(sg, np.ones_like(sg.dfund), cost, 0.0)
            # no cost: funding only (3 settlements a day), price trend ignored (basis = 0)
            self.assertAlmostEqual(float(x[1]), 3e-4 * 1e4)
            self.assertGreater(inv, 0.8)


if __name__ == "__main__":
    unittest.main()
