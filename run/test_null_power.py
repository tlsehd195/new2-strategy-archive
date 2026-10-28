"""Tests for the null/planted-edge simulator (ADR 0045). Needs numpy and pandas."""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import null_power_sim as N  # noqa: E402
import strong_search_dev as S  # noqa: E402


def fake_sd(n=6000, seed=0, sym="BTCUSDT"):
    rng = np.random.default_rng(seed)
    r = rng.standard_t(4, n) * 0.002
    c = 100 * np.exp(np.cumsum(r))
    o = np.concatenate([[c[0]], c[:-1]]) * np.exp(rng.normal(0, 0.0003, n))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.001, n)))
    l = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.001, n)))
    sd = object.__new__(S.SymData)
    sd.sym, sd.bars = sym, []
    sd.ts = (N.GRID0 + np.arange(n)) * S.BAR_MS
    sd.o, sd.h, sd.l, sd.c = o, h, l, c
    sd.v = np.full(n, 1e6)
    sd.qv = sd.v * c
    sd.tbv = rng.uniform(3e5, 7e5, n)
    sd.f_ts = sd.ts[0] + np.arange(n // 32 + 2, dtype=np.int64) * 8 * 3_600_000
    sd.f_rate = np.zeros(len(sd.f_ts))
    sd.f_cum = np.concatenate([[0.0], np.cumsum(sd.f_rate)])
    sd.entry_lo, sd.hi, sd.feat = 100, n - 1, {}
    return sd


class NullPowerTest(unittest.TestCase):
    def test_prereg_is_the_committed_one(self):
        p = json.loads(N.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        jobs = N.job_list(p)
        self.assertEqual(len(jobs), 60 + 40 + 30 + 40 + 24 + 5 * 16 + 2 * 12)
        self.assertEqual(len({(j["group"], j["rep"]) for j in jobs}), len(jobs))

    def test_flip_keeps_absolute_moves_and_bar_shape(self):
        sd = fake_sd()
        s = np.random.default_rng(1).choice([-1.0, 1.0], len(sd.c))
        f = N.flip(sd, s)
        self.assertTrue(np.allclose(np.abs(np.log(f.c[1:] / f.c[:-1])), np.abs(np.log(sd.c[1:] / sd.c[:-1])), atol=1e-9))
        self.assertTrue((f.h >= np.maximum(f.o, f.c) * (1 - 1e-12)).all())
        self.assertTrue((f.l <= np.minimum(f.o, f.c) * (1 + 1e-12)).all())
        neg = s < 0
        self.assertTrue(np.allclose(f.tbv[neg], sd.v[neg] - sd.tbv[neg]))
        back = N.flip(f, s)                      # flipping twice restores
        self.assertTrue(np.allclose(back.c, sd.c) and np.allclose(back.h, sd.h))

    def test_surrogate_signs_shared_or_independent(self):
        a, b = N.sign_grid("bar", 3, 0), N.sign_grid("bar", 3, 4)
        self.assertTrue((a == b).all())
        self.assertFalse((N.sign_grid("bar_ind", 3, 0) == N.sign_grid("bar_ind", 3, 1)).all())
        d = N.sign_grid("day", 3, 0)
        self.assertTrue((d[:96] == d[0]).all())
        self.assertTrue(set(np.unique(d)) <= {-1.0, 1.0})

    def test_sign_flip_removes_drift(self):
        sd = fake_sd(n=30000)
        sd.c = sd.c * np.exp(np.arange(len(sd.c)) * 1e-4)    # strong trend
        sd.o = sd.o * np.exp(np.arange(len(sd.c)) * 1e-4)
        sd.h = sd.h * np.exp(np.arange(len(sd.c)) * 1e-4)
        sd.l = sd.l * np.exp(np.arange(len(sd.c)) * 1e-4)
        f = N.flip(sd, N.sign_grid("bar", 5, 0, 30000))
        r = np.diff(np.log(f.c))
        self.assertLess(abs(r.mean()) / (r.std() / np.sqrt(len(r))), 4.0)

    def test_plant_adds_the_edge_on_the_rules_own_trades(self):
        sd = fake_sd(n=40000, seed=2)
        data = {"BTCUSDT": {"pieces": [sd], "gap_days": []}}
        spec = N.rule_spec({"feature": "ret_z", "params": {"lookback": 16, "window": 192, "min_history": 0},
                            "op": "gt", "value": 2.0, "direction": "long", "horizon": 48})
        cost = S.load_cost()
        before = S.events(sd, S.spec_side(sd, spec), 48, cost)
        planted = N.plant(data, spec, 80.0, cost)["BTCUSDT"]["pieces"][0]
        after = S.events(planted, S.spec_side(planted, spec), 48, cost)
        self.assertGreater(len(before), 50)
        g0 = np.mean([e[5] for e in before])
        g1 = np.mean([e[5] for e in after])
        self.assertAlmostEqual(g1 - g0, 80.0, delta=25.0)


if __name__ == "__main__":
    unittest.main()
