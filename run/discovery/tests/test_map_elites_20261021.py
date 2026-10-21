"""ADR 0019 — MAP-Elites parent selection (DEV-only option).  Synthetic only."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from search import SearchConfig, map_cell, select_elite, select_elite_map  # noqa: E402
from strategy_spec import SPEC_SCHEMA  # noqa: E402


def _spec(direction, feature, params, op, value, horizon):
    return {"schema": SPEC_SCHEMA, "direction": direction, "symbols": ["BTCUSDT"],
            "entry": {"logic": "all", "conditions": [
                {"feature": feature, "params": params, "op": op, "value": value}]},
            "filters": [], "exit": {"horizon": horizon}}


def _fb(t, mean=5.0, fold=0.75, mdd=100.0, passed=True, family="f"):
    return {"dev_t": t, "dev_mean_bp": mean, "dev_fold_pos_share": fold, "dev_mdd_bp": mdd,
            "dev_cost_share": 0.3, "complexity": 1, "screen_pass": passed, "family": family}


class TestMapElites(unittest.TestCase):
    def test_default_hash_unchanged_and_map_hash_differs(self):
        d = SearchConfig()
        self.assertNotIn("selection", d.to_json())
        self.assertNotEqual(d.config_hash(), SearchConfig(selection="map_elites").config_hash())
        self.assertEqual(SearchConfig.from_json(SearchConfig(selection="map_elites").to_json()).selection,
                         "map_elites")

    def test_cell(self):
        s = _spec("short", "taker_share_z", {"window": 24}, "gt", 3.0, 64)
        self.assertEqual(map_cell(s), "short|h<=128|flow")
        self.assertEqual(map_cell(_spec("long", "breakout_k", {"window": 32}, "gt", 1.0, 768)),
                         "long|h<=768|breakout")

    def test_one_champion_per_cell_and_shorts_kept(self):
        specs = {
            "L1": _spec("long", "breakout_k", {"window": 32}, "gt", 1.0, 96),
            "L2": _spec("long", "breakout_k", {"window": 48}, "gt", 1.0, 96),
            "L3": _spec("long", "breakout_k", {"window": 64}, "gt", 1.0, 96),
            "S1": _spec("short", "taker_share_z", {"window": 24}, "gt", 3.0, 64),
            "NEG": _spec("short", "funding_z", {"window": 30}, "gt", 2.0, 64),
        }
        fb = {"L1": _fb(5.0), "L2": _fb(4.0), "L3": _fb(3.0), "S1": _fb(1.2, passed=False),
              "NEG": _fb(2.0, mean=-1.0)}
        elite, cells = select_elite_map(fb, SearchConfig(selection="map_elites"), specs.get)
        self.assertEqual(sorted(elite), ["L1", "S1"])          # weak short kept, negative mean never
        self.assertEqual(cells["long|h<=128|breakout"], "L1")
        # the Pareto elite with a family cap keeps three longs here
        pareto = select_elite(fb, SearchConfig(niche_cap=3))
        self.assertIn("L2", pareto)


if __name__ == "__main__":
    unittest.main()
