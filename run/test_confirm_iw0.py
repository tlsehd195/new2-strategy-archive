"""Tests for the IW0 confirmation tool (ADR 0111). Synthetic data only; needs numpy and pandas."""

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import confirm_iw0 as cf  # noqa: E402
import broad_universe_dev as bu  # noqa: E402


def synthetic_zip(path: Path, n_sym: int = 12, edge: float = 0.0, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    t0, t1 = bu.ms("2017-08-01"), bu.ms("2020-01-03")  # includes 2020 rows that must be dropped
    ts = np.arange(t0, t1, 4 * 3_600_000)  # 4h bars are enough for daily aggregation
    with zipfile.ZipFile(path, "w") as zf:
        for k in range(n_sym):
            p = 10.0 * np.exp(np.cumsum(rng.normal(0, 0.01, len(ts))))
            lines = []
            for t, c in zip(ts, p):
                lines.append(f"{t},{c},{c * 1.01},{c * 0.99},{c},100,{t + 1},{5e6},{50}")
            zf.writestr(f"klines/S{k}USDT-15m-all.csv", "\n".join(lines))


class ConfirmIW0Test(unittest.TestCase):
    def test_holm_and_nw(self):
        r = cf.holm({"a": 0.001, "b": 0.015, "c": 0.03, "d": 0.5})
        self.assertEqual(r, {"a": True, "b": True, "c": False, "d": False})
        rng = np.random.default_rng(3)
        self.assertLess(abs(cf.nw_t(rng.normal(0, 1, 2000))), 4)
        self.assertGreater(cf.nw_t(rng.normal(0.2, 1, 2000)), 4)

    def test_pipeline_never_reads_2020_and_runs(self):
        with tempfile.TemporaryDirectory() as d:
            z = Path(d) / "s.zip"
            synthetic_zip(z)
            piv = cf.load_daily(z)
            self.assertLessEqual(int(piv["open"].index.max()), bu.ms("2019-12-31"))
            sim = cf.simulate(piv)
            res = cf.decide(sim)
            self.assertEqual(sorted(res), sorted(cf.TESTS))
            for v in res.values():
                self.assertIn(v["verdict"], ("PASS", "FAIL"))
            self.assertGreater(sim["n_rebalances"], 30)

    def test_relative_weights_are_dollar_neutral(self):
        with tempfile.TemporaryDirectory() as d:
            z = Path(d) / "s.zip"
            synthetic_zip(z, seed=1)
            piv = cf.load_daily(z)
            cret = piv["close"].pct_change(fill_method=None)
            nb = piv["close"].notna().cumsum()
            cut = 400
            u = cf.universe(piv, nb, cut)
            tg = cf.targets(piv, cret, cut, u)
            self.assertAlmostEqual(sum(tg["CL1_IW0"].values()), 0.0)
            self.assertAlmostEqual(sum(tg["FIP1_IW0"].values()), 0.0)


if __name__ == "__main__":
    unittest.main()
