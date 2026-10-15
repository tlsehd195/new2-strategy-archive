#!/usr/bin/env python3
"""Regression tests for the residual-defect fixes (2026-10-15 directive).

Every test runs the REAL implementation on small hand-checkable synthetic
data in isolated temp dirs.  Expected numbers are computed by hand in the
comments; "no exception" is never the pass condition.

Sections: P0-1 generation FDR · P0-2 portfolio MTM/MDD · P0-3 cost failures ·
P0-4 liquidation accounting · P1-6 locked-TEST lifecycle · P1-7 context id ·
P1-8 preregistration manifest · P1-9 promotion evidence · P1-10 run_cycle.sh ·
P2 status consistency · recovery.
"""
from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from data_quality import Bar  # noqa: E402
from engine_lib import (  # noqa: E402
    CandidateState, LockRegistryError, LockState, LockTestRegistry, bh_fdr_family,
    code_identity, experiment_context, normalize_strategy, strategy_id,
    verdict_from_cells, CellResult, SymbolResult,
)
from costs_bridge import (  # noqa: E402
    CandleCostModel, CostComputationError, CostConfigError, CostContext,
    CostDataMissing, CostRefused, FUTURES_COST_DEFAULTS, COST_MODEL_SCOPE,
)
from evaluator import EventRecord, cell_stats, run_event_study  # noqa: E402
from generation import (  # noqa: E402
    FdrDecisionStore, ManifestError, PreregistrationConflict, PreregistrationStore,
    build_manifest, decide_family, family_status,
)
from generator import _mk, enumerate_candidates  # noqa: E402
from portfolio import PortfolioConfig, max_drawdown, portfolio_passes, simulate_portfolio  # noqa: E402
from promotion import combo_promotion_gate, promotion_gate  # noqa: E402
from test_engine import make_bars, make_funding, write_funding_csv, write_kline_csv  # noqa: E402

MS15 = 900_000
DAY_MS = 86_400_000


def zero_cost() -> CostContext:
    return CostContext.from_params({"fee_rate": 0.0, "half_spread": 0.0,
                                    "impact_coefficient": 0.0, "max_participation": 1.0},
                                   version="TEST_ZERO", test_only=True)


def fee_cost(fee=0.001) -> CostContext:
    return CostContext.from_params({"fee_rate": fee, "half_spread": 0.0,
                                    "impact_coefficient": 0.0, "max_participation": 1.0},
                                   version="TEST_FEE", test_only=True)


def pbars(prices, *, lows=None, highs=None, opens=None, qv=1e12):
    out = []
    for i, p in enumerate(prices):
        o = opens[i] if opens and opens[i] is not None else p
        lo = lows[i] if lows and lows[i] is not None else min(o, p)
        hi = highs[i] if highs and highs[i] is not None else max(o, p)
        out.append(Bar(ts=i * MS15, open=o, high=max(hi, o, p), low=min(lo, o, p), close=p,
                       volume=1e9, quote_volume=qv))
    return out


def sim(bars, sigs, cost=None, funding=None, **cfg):
    base = dict(initial_capital=10_000.0, size_fraction=1.0, leverage=1.0, max_concurrent=2)
    base.update(cfg)
    bys = bars if isinstance(bars, dict) else {"A": bars}
    return simulate_portfolio(bys, sigs, funding or {s: [] for s in bys},
                              cost or zero_cost(), PortfolioConfig(**base))


def long_sig(decision, horizon, sym="A", side=1):
    return {"symbol": sym, "decision_index": decision, "side": side, "horizon": horizon}


def make_paths(td):
    from runner import PathConfig
    data = Path(td) / "data"
    (data / "klines").mkdir(parents=True, exist_ok=True)
    return PathConfig(root=Path(td) / "disc", data_dir=data, state_dir=Path(td) / "state",
                      results_dir=Path(td) / "results", audit_dir=Path(td) / "audit")


# ===========================================================================
# P0-1  generation-wide BH-FDR
# ===========================================================================

def small_space():
    """6 real candidate definitions (all BTC+ETH, all have locked windows)."""
    defs = [_mk("ret_rev", {"lookback": lb, "thresh_z": 1.5, "horizon": 16}, "long_on_down_move")
            for lb in (4, 8, 16, 32)] + \
           [_mk("ts_mom", {"lookback": 64, "thresh_z": tz, "horizon": 48}, "follow_sign")
            for tz in (1.0, 2.0)]
    return [(strategy_id(d), normalize_strategy(d)) for d in defs]


SPACE = small_space()
SIDS = [s for s, _ in SPACE]
#: hand-computed BH at q=0.05, m=6 (None counts in m, never rejected):
#: sorted finite p: .001 (thr .00833 ok) .008 (.01667 ok) .02 (.025 ok)
#: .035 (.0333 NO) .2 (.0417 NO)  -> largest k with p_(k) <= qk/m is 3.
#: (With the None hypothesis dropped, m=5, .035 <= .04 would survive: the
#:  untestable hypothesis is counted, not ignored.)
PVALS = dict(zip(SIDS, [0.001, 0.008, 0.02, 0.035, 0.2, None]))
EXPECTED_SURVIVORS = sorted(SIDS[:3])


def _fake_runner_cls():
    from runner import Runner

    class FakeEvalRunner(Runner):
        """Dev phase replaced by fixed p-values; everything downstream (ledger,
        state machine, FDR family, decision store, locked TEST registry,
        promotion) is the real implementation."""

        def _evaluate(self, sid, defn):
            p = PVALS[sid]
            if p is None:
                return {"status": "UNDETERMINED_POWER", "primary_dev_p": None,
                        "dev_verdict": "UNDETERMINED_POWER", "validation_verdict": "DATA_MISSING"}
            dev_pass = p < 0.05
            return {"status": "AWAITING_FDR" if dev_pass else "FAIL_STAT",
                    "primary_dev_p": p, "dev_verdict": "PASS" if dev_pass else "FAIL_STAT",
                    "validation_verdict": "PASS", "portfolio": None}
    return FakeEvalRunner


class TestGenerationFdr(unittest.TestCase):
    def _run_all(self, order, batch=2):
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        cls = _fake_runner_cls()
        r = cls(make_paths(td), candidates_fn=lambda: ([SPACE[i] for i in order], []))
        snapshots = []
        for _ in range(10):
            s = r.cycle(max_candidates=batch)
            snapshots.append(s)
            if s.fdr_family.get("complete"):
                break
        return r, snapshots

    def test_order_invariance_and_no_early_decision(self):
        r1, snaps1 = self._run_all(list(range(6)))
        r2, snaps2 = self._run_all(list(reversed(range(6))))
        # first cycle: family incomplete → no decision, no locked TEST touched
        for snaps, r in ((snaps1, r1), (snaps2, r2)):
            self.assertFalse(snaps[0].fdr_family["complete"])
            self.assertIsNone(snaps[0].fdr_family["decision"])
            self.assertEqual(snaps[0].fdr_family["m"], 6)
        self.assertEqual(snaps1[0].generation_id, snaps2[0].generation_id)
        self.assertEqual(snaps1[0].context_id, snaps2[0].context_id)
        d1 = r1.fdr_store.get(r1.generation_id, r1._ctx)
        d2 = r2.fdr_store.get(r2.generation_id, r2._ctx)
        self.assertEqual(d1["survivors"], EXPECTED_SURVIVORS)
        self.assertEqual(d2["survivors"], EXPECTED_SURVIVORS)
        self.assertEqual(d1["m"], 6)
        self.assertEqual(d1["n_without_statistic"], 1)

        def finals(r):
            return {rec["strategy_id"]: rec["status"] for rec in r.ledger.records()
                    if rec.get("phase") == "final"}
        self.assertEqual(finals(r1), finals(r2))
        # p=.035 is dev-PASS but NOT a survivor → FAIL_STAT, lock untouched
        sid4 = SIDS[3]
        self.assertEqual(finals(r1)[sid4], "FAIL_STAT")
        for r in (r1, r2):
            self.assertEqual(r.lock_registry.attempts(sid4, "BTCUSDT"), [])

    def test_incomplete_family_never_reserves_lock(self):
        cls = _fake_runner_cls()
        with tempfile.TemporaryDirectory() as td:
            r = cls(make_paths(td), candidates_fn=lambda: (SPACE, []))
            s = r.cycle(max_candidates=5)          # 5 of 6 evaluated
            self.assertFalse(s.fdr_family["complete"])
            self.assertEqual(s.fdr_family["missing"], 1)
            self.assertFalse((r.paths.state_dir / "lock_test_usage.json").exists())
            self.assertFalse(any(rec.get("phase") == "final" for rec in r.ledger.records()))
            states = r.store.all_states()
            self.assertEqual(states[SIDS[0]], "AWAITING_FDR")   # p=.001 waits, not PASS

    def test_generation_hash_changes_with_candidate_set(self):
        m1 = build_manifest(SPACE)
        m2 = build_manifest(SPACE[:-1])
        self.assertNotEqual(m1["generation_id"], m2["generation_id"])

    def test_more_candidates_never_loosen_control(self):
        rng = random.Random(7)
        for _ in range(200):
            ps = [rng.random() ** 3 for _ in range(rng.randint(1, 30))]
            m1 = len(ps)
            m2 = m1 + rng.randint(1, 800)
            s1 = {i for i, ok in enumerate(bh_fdr_family(ps, m1)) if ok}
            s2 = {i for i, ok in enumerate(bh_fdr_family(ps, m2)) if ok}
            self.assertLessEqual(s2, s1)
        # the old defect: p=0.04 "survived" as a family of one; in the real
        # 779-candidate generation it does not
        self.assertEqual(bh_fdr_family([0.04], 1), [True])
        self.assertEqual(bh_fdr_family([0.04], 779), [False])

    def test_reevaluation_does_not_overwrite_tested_value(self):
        with tempfile.TemporaryDirectory() as td:
            from store import Ledger
            led = Ledger(Path(td) / "l.jsonl")
            man = {"generation_id": "G-x", "strategy_ids": ["S1", "S2"]}
            for eid, sid, p in (("E1", "S1", 0.001), ("E2", "S2", 0.5), ("E3", "S1", 0.9)):
                led.append({"evaluation_id": eid, "strategy_id": sid, "phase": "dev",
                            "generation_id": "G-x", "context_id": "C", "status": "AWAITING_FDR",
                            "primary_dev_p": p})
            fam = family_status(led.records(), man, "C")
            self.assertEqual(fam["pvalues"]["S1"], 0.001)        # FIRST record counts
            self.assertEqual(fam["first_records"]["S1"], "E1")
            self.assertEqual(led.evaluation_count(), 3)          # history kept
            store = FdrDecisionStore(Path(td) / "fdr.jsonl")
            d = store.record(decide_family(fam))
            again = dict(d, survivors=["S2"])
            self.assertEqual(store.record(again)["survivors"], d["survivors"])
            self.assertEqual(len((Path(td) / "fdr.jsonl").read_text().splitlines()), 1)

    def test_no_pvalue_imputation(self):
        fam = {"complete": True, "generation_id": "G", "context_id": "C", "m": 3,
               "first_records": {}, "pvalues": {"A": 0.01, "B": None, "C": None}}
        d = decide_family(fam)
        self.assertEqual(d["n_with_statistic"], 1)
        self.assertEqual(d["n_without_statistic"], 2)
        # 0.01 <= 0.05*1/3 = .0167 → survives; B/C are never "rejected"
        self.assertEqual(d["survivors"], ["A"])
        with self.assertRaises(ValueError):
            bh_fdr_family([float("nan")], 3)
        with self.assertRaises(ValueError):
            decide_family(dict(fam, complete=False))


# ===========================================================================
# P0-2  portfolio mark-to-market equity, MDD
# ===========================================================================

class TestPortfolioAccounting(unittest.TestCase):
    def test_price_up_after_entry(self):
        pf = sim(pbars([100, 100, 100, 110, 110, 110, 110, 110, 110, 110]), [long_sig(1, 5)])
        # entry bar 2 @100, qty 100; close bar 3 = 110 → equity 11000
        self.assertEqual(pf.equity_curve[2], 10_000.0)
        self.assertAlmostEqual(pf.equity_curve[3], 11_000.0)
        self.assertAlmostEqual(pf.final_equity, 11_000.0)

    def test_price_down_after_entry(self):
        pf = sim(pbars([100, 100, 100, 90, 90, 90, 90, 90, 90, 90]), [long_sig(1, 5)])
        self.assertAlmostEqual(pf.equity_curve[3], 9_000.0)
        self.assertAlmostEqual(pf.final_equity, 9_000.0)

    def test_unrealized_loss_drives_mdd(self):
        prices = [100, 100, 100, 80, 50, 50, 100, 100, 100, 100]
        pf = sim(pbars(prices), [long_sig(1, 6)])
        self.assertEqual([round(v) for v in pf.equity_curve],
                         [10000, 10000, 10000, 8000, 5000, 5000, 10000, 10000, 10000, 10000])
        self.assertAlmostEqual(pf.max_drawdown, 0.5)       # old code: ~0
        self.assertAlmostEqual(pf.final_equity, 10_000.0)
        self.assertAlmostEqual(max_drawdown(pf.equity_curve, 10_000), 0.5)

    def test_partial_close(self):
        prices = [100, 100, 100, 100, 120, 130, 100, 100, 100, 100]
        sigs = [long_sig(1, 4), {"symbol": "A", "decision_index": 3, "reduce": 0.5}]
        pf = sim(pbars(prices), sigs)
        # bar4 open 120: close 50 of 100 → +1000 realized; close 120 → 11000+50*20
        self.assertAlmostEqual(pf.cash_curve[4], 11_000.0)
        self.assertAlmostEqual(pf.equity_curve[4], 12_000.0)
        self.assertAlmostEqual(pf.equity_curve[5], 12_500.0)   # 11000 + 50*30
        self.assertAlmostEqual(pf.final_equity, 11_000.0)      # rest closed @100
        self.assertEqual(pf.partial_closes, 1)
        self.assertEqual(pf.trades, 1)
        self.assertAlmostEqual(pf.realized_pnl, 1_000.0)

    def test_multi_symbol_no_double_count(self):
        a = pbars([100, 100, 100] + [110] * 7)
        b = pbars([100, 100, 100] + [90] * 7)
        pf = sim({"A": a, "B": b}, [long_sig(1, 5, "A"), long_sig(1, 5, "B")], size_fraction=0.5)
        # each 5000 notional: +500 and -500
        self.assertAlmostEqual(pf.equity_curve[3], 10_000.0)
        b_flat = pbars([100] * 10)
        pf2 = sim({"A": a, "B": b_flat}, [long_sig(1, 5, "A"), long_sig(1, 5, "B")],
                  size_fraction=0.5)
        self.assertAlmostEqual(pf2.equity_curve[3], 10_500.0)

    def test_position_open_until_last_bar_is_marked(self):
        prices = [100, 100, 100, 104, 108, 105, 103, 105]
        pf = sim(pbars(prices), [long_sig(1, 100)], cost=fee_cost())
        # entry fee 10; before the last bar the curve is MTM: 9990 + 100*(103-100)
        self.assertAlmostEqual(pf.equity_curve[-2], 9_990 + 300)

    def test_forced_close_on_last_bar_uses_open(self):
        prices = [100, 100, 100, 104, 108, 105, 103, 150]
        opens = [None] * 7 + [105]
        highs = [None] * 7 + [200]
        pf = sim(pbars(prices, opens=opens, highs=highs), [long_sig(1, 100)], cost=fee_cost())
        # forced fill at open 105 (not close 150, not high 200):
        # 10000 - 10 (entry fee) + 100*5 - 0.001*10500 (exit fee) = 10479.5
        self.assertEqual(pf.forced_end_closes, 1)
        self.assertAlmostEqual(pf.final_equity, 10_479.5)
        self.assertAlmostEqual(pf.equity_curve[-1], pf.final_equity)
        self.assertAlmostEqual(pf.final_cash, pf.final_equity)

    def test_fees_and_funding_on_close(self):
        funding = {"A": [(4 * MS15, 0.0001), (8 * MS15, 0.0001)]}
        pf = sim(pbars([100] * 10), [long_sig(1, 4)], cost=fee_cost(), funding=funding)
        # entry fee 10, funding 0.0001*100*100 = 1 (bar 4), exit fee 10 at bar 6;
        # the bar-8 settlement is after the exit → not charged
        self.assertAlmostEqual(pf.total_fees, 20.0)
        self.assertAlmostEqual(pf.total_funding, 1.0)
        self.assertAlmostEqual(pf.final_equity, 9_979.0)
        # no cost after the position is gone
        self.assertEqual(len(set(round(c, 9) for c in pf.cash_curve[6:])), 1)

    def test_accounting_identity(self):
        bars = make_bars(n=300, drift=0.0002, vol=0.004, seed=11, volume=1e9)
        fund = {"A": make_funding(bars[0].ts, bars[-1].ts, rate=5e-5)}
        sigs = [long_sig(i, 12, side=1 if (i // 40) % 2 == 0 else -1) for i in range(40, 290, 17)]
        pf = sim(bars, sigs, cost=CostContext.futures_default(), funding=fund, size_fraction=0.3)
        self.assertEqual(pf.cost_status, "OK")
        self.assertAlmostEqual(pf.final_equity, pf.equity_curve[-1])
        self.assertAlmostEqual(pf.final_equity, pf.final_cash)
        self.assertAlmostEqual(pf.final_equity, 10_000 + pf.realized_pnl - pf.total_fees
                               - pf.total_funding, places=6)
        self.assertGreater(pf.trades, 5)


# ===========================================================================
# P0-3  cost failures never become a cheaper fallback
# ===========================================================================

class _NaNModel(CandleCostModel):
    def cost_fraction(self, order_value, bar_traded_value):
        return float("nan")


class _NegModel(CandleCostModel):
    def cost_fraction(self, order_value, bar_traded_value):
        return -0.001


class TestCostFailures(unittest.TestCase):
    def setUp(self):
        self.c = CostContext.futures_default()

    def test_normal_cost_exact(self):
        # 5bp + 2bp + 0.1*sqrt(1e4/1e8)=10bp → 17bp
        self.assertAlmostEqual(self.c.quote(10_000, 1e8), 0.0005 + 0.0002 + 0.001)

    def test_participation_over_cap(self):
        with self.assertRaises(CostRefused) as cm:
            self.c.quote(10_000, 100_000)            # 10% > 5% cap
        self.assertEqual(cm.exception.status, "FAIL_COST")

    def test_traded_value_missing(self):
        # the candle model's only market input (no order book is used at all —
        # COST_MODEL_SCOPE states it); a missing/zero/NaN value is DATA_MISSING
        self.assertIn("NO order book", COST_MODEL_SCOPE["inputs"])
        for bad in (None, 0.0, float("nan"), "x"):
            with self.assertRaises(CostDataMissing):
                self.c.quote(10_000, bad)

    def test_spread_or_fee_config_missing(self):
        for k in ("half_spread", "fee_rate"):
            p = dict(FUTURES_COST_DEFAULTS)
            p.pop(k)
            with self.assertRaises(CostConfigError):
                CostContext.from_params(p, version="x")

    def test_negative_cost(self):
        p = dict(FUTURES_COST_DEFAULTS, fee_rate=-0.0001)
        with self.assertRaises(CostConfigError):
            CostContext.from_params(p, version="x")
        neg = CostContext(model=_NegModel(**FUTURES_COST_DEFAULTS), version="neg", test_only=True)
        with self.assertRaises(CostComputationError):
            neg.quote(10_000, 1e8)

    def test_numeric_exception(self):
        nan = CostContext(model=_NaNModel(**FUTURES_COST_DEFAULTS), version="nan", test_only=True)
        with self.assertRaises(CostComputationError):
            nan.quote(10_000, 1e8)
        with self.assertRaises(CostComputationError):
            self.c.quote(float("nan"), 1e8)
        bars = make_bars(n=40, seed=3, volume=1e9)
        with self.assertRaises(CostComputationError):   # ERROR, not a skipped event
            run_event_study(bars, "X", lambda v: 1, 4, nan, 10_000.0, None)

    def test_exit_cost_failure_is_not_priced_cheaply(self):
        # exit bar has traded value 1000: participation 500% → refused
        bars = [Bar(ts=i * MS15, open=100, high=100, low=100, close=100, volume=1e6,
                    quote_volume=(1e8 if i != 10 else 1000.0)) for i in range(20)]
        pf = sim(bars, [long_sig(2, 7)], cost=CostContext.futures_default(), size_fraction=0.5)
        self.assertEqual(pf.cost_status, "FAIL_COST")
        self.assertEqual(len(pf.cost_failures), 1)
        ok, why = portfolio_passes(pf.to_json())
        self.assertFalse(ok)
        self.assertTrue(any("cost status" in w for w in why))
        # only the ENTRY was priced (impact on 5000/1e8) — no fee+spread substitute
        entry_bp = (0.0005 + 0.0002 + 0.1 * math.sqrt(5000 / 1e8)) * 1e4
        self.assertAlmostEqual(pf.total_cost_bp, entry_bp)

    def test_entry_refusal_counted(self):
        bars = pbars([100] * 10, qv=1000.0)
        pf = sim(bars, [long_sig(1, 3)], cost=CostContext.futures_default())
        self.assertEqual(pf.trades, 0)
        self.assertEqual(pf.participation_refusals, 1)

    def test_cost_failure_candidate_never_passes(self):
        bars = [Bar(ts=i * MS15, open=100 + i, high=101 + i, low=100 + i, close=101 + i,
                    volume=1e6, quote_volume=(1e9 if i != 30 else 10.0)) for i in range(60)]
        pf = sim(bars, [long_sig(5, 25)], cost=CostContext.futures_default(), size_fraction=0.5)
        pfj = pf.to_json()
        pfj["context_id"] = "C2-x"
        self.assertGreater(pf.final_equity, 10_000)    # looks profitable...
        chk = combo_promotion_gate(portfolio_result=pfj, context_id="C2-x",
                                   state="PASS", preregistered=True, fdr_survived=True,
                                   independent_validation_passed=True, lock_test_passed=True,
                                   lock_test_used=1, required_symbols=["BTCUSDT"],
                                   cost_verified=True)
        self.assertFalse(chk.eligible)                 # ...but the exit was unpriceable


# ===========================================================================
# P0-4  forced-liquidation accounting
# ===========================================================================

class TestLiquidation(unittest.TestCase):
    PRICES = [100] * 10

    def _liq(self, low, cost=None, funding=None):
        lows = [None] * 4 + [low] + [None] * 5
        return sim(pbars(self.PRICES, lows=lows), [long_sig(1, 6)], cost=cost,
                   funding=funding, leverage=2.0)

    def test_equity_before_after_liquidation(self):
        pf = self._liq(50.0)
        # notional 10000, margin 5000, liq = 100*(1-0.5+0.005) = 50.5
        self.assertEqual(pf.liquidation_events, 1)
        self.assertAlmostEqual(pf.equity_curve[3], 10_000.0)
        self.assertAlmostEqual(pf.equity_curve[4], 5_000.0)
        self.assertAlmostEqual(pf.liquidation_losses, 5_000.0)
        self.assertAlmostEqual(pf.final_equity, 5_000.0)

    def test_no_double_fee(self):
        pf = self._liq(50.0, cost=fee_cost())
        # entry fee 10 once, NO exit fee at liquidation
        self.assertAlmostEqual(pf.total_fees, 10.0)
        self.assertAlmostEqual(pf.final_equity, 10_000 - 10 - 5_000)

    def test_no_double_funding(self):
        pf = self._liq(50.0, funding={"A": [(3 * MS15, 0.0001), (6 * MS15, 0.0001)]})
        # bar-3 settlement: 0.0001*100*100 = 1; bar-6 is after liquidation
        self.assertAlmostEqual(pf.total_funding, 1.0)
        self.assertAlmostEqual(pf.final_equity, 10_000 - 1 - 5_000)

    def test_position_removed_after_liquidation(self):
        pf = self._liq(50.0)
        self.assertEqual(pf.trades, 0)
        self.assertEqual(len(set(pf.equity_curve[4:])), 1)
        self.assertEqual(len(set(pf.cash_curve[4:])), 1)

    def test_maintenance_margin_trigger(self):
        self.assertEqual(self._liq(50.6).liquidation_events, 0)
        self.assertEqual(self._liq(50.5).liquidation_events, 1)
        # short side: liq = 100*(1+0.5-0.005) = 149.5
        highs = [None] * 4 + [149.5] + [None] * 5
        pf = sim(pbars(self.PRICES, highs=highs), [long_sig(1, 6, side=-1)], leverage=2.0)
        self.assertEqual(pf.liquidation_events, 1)

    def test_normal_vs_forced_close_fixed_case(self):
        normal = sim(pbars([100] * 4 + [60] * 6), [long_sig(1, 3)], leverage=2.0)
        # normal exit @60: -40*100 = -4000 → 6000
        self.assertEqual(normal.liquidation_events, 0)
        self.assertAlmostEqual(normal.final_equity, 6_000.0)
        forced = self._liq(50.0)
        # forced: margin 5000 = price loss to liq (49.5*100=4950) + mmr*notional (50)
        self.assertAlmostEqual(forced.liquidation_losses, 4_950 + 50)
        self.assertAlmostEqual(forced.final_equity, 5_000.0)


# ===========================================================================
# P1-6  locked-TEST lifecycle
# ===========================================================================

def _child_reserve(path, sid, start, q):
    reg = LockTestRegistry(Path(path))
    r = reg.reserve(sid, "BTCUSDT", "C")
    if r["ok"] and start:
        reg.start(r["token"])
    q.put(r["ok"])
    q.close()
    q.join_thread()      # flush, then die without completing
    os._exit(0)


def _child_race(path, barrier, q):
    reg = LockTestRegistry(Path(path))
    barrier.wait()
    r = reg.reserve("S-race", "BTCUSDT", "C")
    q.put(r["ok"])
    barrier.wait()       # stay alive until every racer has tried


def _write_lock_window(data: Path, *, funding=True, corrupt=False):
    import datetime as dt
    price = 100.0
    for mon in ("2024-03", "2024-04", "2024-05", "2024-06"):
        y, m = int(mon[:4]), int(mon[5:])
        d0 = dt.date(y, m, 1)
        d1 = dt.date(y + (m == 12), m % 12 + 1, 1)
        start = int(dt.datetime(y, m, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        n = (d1 - d0).days * 96
        bars = make_bars(n=n, start_ts=start, price0=price, drift=0.0005, vol=1e-5,
                         seed=m, volume=1e6)
        price = bars[-1].close
        if corrupt and mon == "2024-04":
            bars[10] = bars[9]          # duplicate timestamp → structural corruption
        write_kline_csv(data / "klines" / f"BTCUSDT-15m-{mon}.csv", bars)
        if funding:
            end = int(dt.datetime(d1.year, d1.month, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
            write_funding_csv(data / "funding" / f"BTCUSDT-fundingRate-{mon}.csv",
                              [(t, 1e-4) for t in range(start, end, 8 * 3_600_000)])


LOCK_DEFN = normalize_strategy({
    "family": "tod", "params": {"utc_start": 0, "utc_end": 24, "horizon": 16},
    "_defaults": {"utc_start": 0, "utc_end": 2, "horizon": 16},
    "symbols": ["BTCUSDT"], "timeframe": "15m", "direction": "long_in_session",
    "entry": "tod:preregistered_rule", "exit": "time_exit", "holding": 16,
    "risk": {"sizing": "vol_inverse", "max_leverage": 1.0},
    "primary_cell": {"symbol": "BTCUSDT", "horizon": 16}})
LOCK_SID = strategy_id(LOCK_DEFN)


class TestLockLifecycle(unittest.TestCase):
    def _runner(self, td, **kw):
        from runner import Runner
        paths = make_paths(td)
        _write_lock_window(paths.data_dir, **kw) if kw.get("write", True) else None
        r = Runner(paths, candidates_fn=lambda: ([(LOCK_SID, LOCK_DEFN)], []))
        r._ctx = "CTX-test"
        return r

    def _blocked(self, **kw):
        from runner import Runner
        with tempfile.TemporaryDirectory() as td:
            paths = make_paths(td)
            if kw.pop("klines", True):
                _write_lock_window(paths.data_dir, **kw)
            r = Runner(paths, candidates_fn=lambda: ([(LOCK_SID, LOCK_DEFN)], []))
            r._ctx = "CTX-test"
            status, info = r._locked_test(LOCK_SID, LOCK_DEFN, "CTX-test")
            atts = r.lock_registry.attempts(LOCK_SID, "BTCUSDT")
            return status, info, atts, r.lock_registry.uses(LOCK_SID, "BTCUSDT")

    def test_funding_missing_blocks_without_consuming(self):
        status, info, atts, uses = self._blocked(funding=False)
        self.assertEqual(status, "DATA_MISSING")
        self.assertFalse(info["consumed"])
        self.assertEqual([a["state"] for a in atts], ["BLOCKED_DATA_MISSING"])
        self.assertEqual(uses, 0)

    def test_candles_missing_blocks(self):
        status, info, atts, uses = self._blocked(klines=False)
        self.assertEqual(status, "DATA_MISSING")
        self.assertEqual(atts[-1]["state"], "BLOCKED_DATA_MISSING")
        self.assertEqual(uses, 0)

    def test_bad_quality_blocks(self):
        status, info, atts, uses = self._blocked(corrupt=True)
        self.assertEqual(status, "DATA_MISSING")
        self.assertIn("rejected", " ".join(info["blocked"]))
        self.assertEqual(uses, 0)

    def test_death_before_start_is_not_a_use(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "lock.json"
            q = mp.get_context("fork").Queue()
            pr = mp.get_context("fork").Process(target=_child_reserve, args=(str(p), "S1", False, q))
            pr.start(); pr.join()
            self.assertTrue(q.get(timeout=5))
            reg = LockTestRegistry(p)
            self.assertEqual(reg.state("S1", "BTCUSDT"), LockState.RESERVED)
            self.assertTrue(reg.reserve("S1", "BTCUSDT", "C")["ok"])   # recovered
            a = reg.attempts("S1", "BTCUSDT")
            self.assertEqual(a[0]["state"], "FAILED")
            self.assertFalse(a[0]["exposed"])
            self.assertEqual(reg.uses("S1", "BTCUSDT"), 0)

    def test_death_during_evaluation_counts_as_use(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "lock.json"
            q = mp.get_context("fork").Queue()
            pr = mp.get_context("fork").Process(target=_child_reserve, args=(str(p), "S1", True, q))
            pr.start(); pr.join()
            reg = LockTestRegistry(p)
            self.assertEqual(reg.recover_stale() and True, True)
            self.assertEqual(reg.attempts("S1", "BTCUSDT")[0]["state"], "FAILED")
            self.assertEqual(reg.uses("S1", "BTCUSDT"), 1)
            r = reg.reserve("S1", "BTCUSDT", "C")
            self.assertFalse(r["ok"])
            self.assertEqual(r["reason"], "ALREADY_USED")

    def test_duplicate_run_and_rerun_after_completion(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._runner(td)
            status, info = r._locked_test(LOCK_SID, LOCK_DEFN, "CTX-test")
            self.assertEqual(status, "PASS", info)
            self.assertTrue(info["consumed"])
            res1 = r.lock_registry.result(LOCK_SID, "BTCUSDT")
            self.assertEqual(r.lock_registry.state(LOCK_SID, "BTCUSDT"), LockState.CONSUMED)
            status2, info2 = r._locked_test(LOCK_SID, LOCK_DEFN, "CTX-test")
            self.assertEqual(status2, "FAIL_STAT")
            self.assertTrue(info2.get("reused"))
            self.assertEqual(r.lock_registry.uses(LOCK_SID, "BTCUSDT"), 1)   # no new exposure
            self.assertEqual(r.lock_registry.result(LOCK_SID, "BTCUSDT"), res1)

    def test_concurrent_reservation(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = mp.get_context("fork")
            n = 6
            barrier, q = ctx.Barrier(n), ctx.Queue()
            procs = [ctx.Process(target=_child_race, args=(str(Path(td) / "lock.json"), barrier, q))
                     for _ in range(n)]
            for pr in procs:
                pr.start()
            for pr in procs:
                pr.join(30)
            oks = [q.get(timeout=5) for _ in range(n)]
            self.assertEqual(sum(oks), 1)

    def test_result_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            reg = LockTestRegistry(Path(td) / "l.json")
            t = reg.reserve("S1", "BTCUSDT", "C")["token"]
            reg.start(t)
            reg.complete(t, {"pass": False})
            with self.assertRaises(LockRegistryError):
                reg.complete(t, {"pass": True})
            self.assertEqual(reg.result("S1", "BTCUSDT"), {"pass": False})

    def test_legacy_v1_usage_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "l.json"
            p.write_text(json.dumps({"S1": {"BTCUSDT": [{"context_id": "C-old", "ts": "t"}]}}))
            reg = LockTestRegistry(p)
            self.assertEqual(reg.uses("S1", "BTCUSDT"), 1)
            self.assertFalse(reg.reserve("S1", "BTCUSDT", "C")["ok"])
            reg.reserve("S2", "BTCUSDT", "C")               # forces a write
            doc = json.loads(p.read_text())
            self.assertEqual(doc["legacy_v1"]["S1"]["BTCUSDT"][0]["context_id"], "C-old")


# ===========================================================================
# P1-7  experiment context id
# ===========================================================================

def _ctx_data(data: Path, fng_val=25, rate=1e-4):
    import datetime as dt
    start = int(dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    bars = make_bars(n=31 * 96, start_ts=start, seed=1, volume=1e6)
    write_kline_csv(data / "klines" / "BTCUSDT-15m-2021-01.csv", bars)
    write_funding_csv(data / "funding" / "BTCUSDT-fundingRate-2021-01.csv",
                      [(t, rate) for t in range(start, start + 31 * DAY_MS, 8 * 3_600_000)])
    (data / "fng").mkdir(parents=True, exist_ok=True)
    (data / "fng" / "fng.csv").write_text("date,value\n2021-01-01,%s\n2021-01-02,30\n" % fng_val)


class TestContextId(unittest.TestCase):
    BASE = {"data": {"BTCUSDT": {"klines": {"sha256": "a"}, "funding": {"sha256": "f"}},
                     "_fng": {"sha256": "g"}},
            "data_policy": {"version": "p1", "max_gap": 2},
            "cost": {"params": {"fee_rate": 0.0005}, "source_sha256": "s"},
            "code": {"code_hash": "h1"},
            "validation": {"min_n": 30},
            "runtime": {"python_version": "3.13.0"}}

    def _mod(self, path, val):
        import copy
        c = copy.deepcopy(self.BASE)
        d = c
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = val
        return experiment_context(c)[0]

    def test_deterministic(self):
        a = experiment_context(self.BASE)[0]
        time.sleep(0.01)
        self.assertEqual(a, experiment_context(dict(reversed(list(self.BASE.items()))))[0])

    def test_each_component_changes_id(self):
        base = experiment_context(self.BASE)[0]
        for path, val in ((("data", "BTCUSDT", "funding", "sha256"), "f2"),
                          (("data", "_fng", "sha256"), "g2"),
                          (("cost", "params", "fee_rate"), 0.0006),
                          (("code", "code_hash"), "h2"),
                          (("validation", "min_n"), 31),
                          (("data_policy", "max_gap"), 3),
                          (("runtime", "python_version"), "3.12.0")):
            self.assertNotEqual(base, self._mod(path, val), path)

    def test_missing_component_refused(self):
        bad = dict(self.BASE)
        bad.pop("cost")
        with self.assertRaises(ValueError):
            experiment_context(bad)

    def test_runner_context_tracks_real_inputs(self):
        from runner import Runner
        import runner as runner_mod
        cands = lambda: ([(LOCK_SID, LOCK_DEFN)], [])

        def ctx_for(td, **kw):
            paths = make_paths(td)
            _ctx_data(paths.data_dir, **{k: v for k, v in kw.items() if k in ("fng_val", "rate")})
            r = Runner(paths, candidates_fn=cands,
                       **{k: v for k, v in kw.items() if k in ("cost", "order_value")},
                       allow_test_cost=True)
            return r._current_context()

        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            base = ctx_for(a)
            self.assertEqual(base, ctx_for(b))                         # deterministic
        variants = {
            "funding": dict(rate=2e-4),
            "fng": dict(fng_val=26),
            "cost": dict(cost=CostContext.from_params(dict(FUTURES_COST_DEFAULTS, half_spread=0.0003),
                                                      version=CostContext.futures_default().version)),
            "validation": dict(order_value=20_000.0),
        }
        for name, kw in variants.items():
            with tempfile.TemporaryDirectory() as td:
                self.assertNotEqual(base, ctx_for(td, **kw), name)
        orig_code, orig_rt, orig_pol = runner_mod.code_identity, runner_mod.runtime_identity, runner_mod.DATA_POLICY
        try:
            runner_mod.code_identity = lambda: {"code_hash": "different"}
            with tempfile.TemporaryDirectory() as td:
                self.assertNotEqual(base, ctx_for(td), "code")
            runner_mod.code_identity = orig_code
            runner_mod.runtime_identity = lambda: {"python_version": "0.0.0"}
            with tempfile.TemporaryDirectory() as td:
                self.assertNotEqual(base, ctx_for(td), "runtime")
            runner_mod.runtime_identity = orig_rt
            runner_mod.DATA_POLICY = dict(orig_pol, max_abs_logret_15m=0.3)
            with tempfile.TemporaryDirectory() as td:
                self.assertNotEqual(base, ctx_for(td), "data policy")
        finally:
            runner_mod.code_identity, runner_mod.runtime_identity, runner_mod.DATA_POLICY = \
                orig_code, orig_rt, orig_pol

    def test_code_hash_tracks_source_not_label(self):
        with tempfile.TemporaryDirectory() as td:
            for f in ("engine_lib.py", "runner.py"):
                shutil.copy(DISC / f, Path(td) / f)
            h1 = code_identity(Path(td), ("engine_lib.py", "runner.py"))["code_hash"]
            with open(Path(td) / "runner.py", "a") as fh:
                fh.write("\n# behaviour change\n")
            h2 = code_identity(Path(td), ("engine_lib.py", "runner.py"))["code_hash"]
            self.assertNotEqual(h1, h2)

    def test_old_context_records_reevaluated_not_overwritten(self):
        cls = _fake_runner_cls()
        with tempfile.TemporaryDirectory() as td:
            r = cls(make_paths(td), candidates_fn=lambda: (SPACE, []))
            r.register_all()
            sid = SIDS[4]
            r.store.set(sid, CandidateState.RUNNING)
            r.store.set(sid, CandidateState.FAIL_STAT)
            r.ledger.append({"evaluation_id": "E-old", "strategy_id": sid, "status": "FAIL_STAT",
                             "context_id": "C-legacy"})
            r._current_context()
            self.assertIn(sid, r.schedule_context_reevaluation())
            self.assertEqual(r.store.entry(sid).state, "REVERIFY_PENDING")
            self.assertTrue(any(x["evaluation_id"] == "E-old" for x in r.ledger.records()))


# ===========================================================================
# P1-8  preregistration manifest
# ===========================================================================

class TestManifest(unittest.TestCase):
    def test_one_candidate_changed(self):
        d = dict(SPACE[0][1])
        d["params"] = dict(d.get("params") or {}, thresh_z=2.5)
        changed = [(strategy_id(d), d)] + SPACE[1:]
        self.assertNotEqual(build_manifest(SPACE)["manifest_hash"],
                            build_manifest(changed)["manifest_hash"])

    def test_order_only(self):
        self.assertEqual(build_manifest(SPACE)["generation_id"],
                         build_manifest(list(reversed(SPACE)))["generation_id"])

    def test_duplicate_candidate(self):
        with self.assertRaises(ManifestError):
            build_manifest(SPACE + [SPACE[0]])

    def test_same_count_different_ids(self):
        other = SPACE[:-1] + [(strategy_id(_mk("tod", {"utc_start": 4, "utc_end": 6, "horizon": 48},
                                                "long_in_session")),
                               normalize_strategy(_mk("tod", {"utc_start": 4, "utc_end": 6,
                                                              "horizon": 48}, "long_in_session")))]
        self.assertEqual(len(other), len(SPACE))
        self.assertNotEqual(build_manifest(SPACE)["generation_id"], build_manifest(other)["generation_id"])

    def test_explicit_default_normalizes(self):
        a = _mk("ret_rev", {"lookback": 4, "thresh_z": 1.0, "horizon": 8}, "long_on_down_move")
        b = _mk("ret_rev", {"thresh_z": 1.0, "horizon": 8}, "long_on_down_move")
        self.assertEqual(build_manifest([(strategy_id(a), normalize_strategy(a))])["generation_id"],
                         build_manifest([(strategy_id(b), normalize_strategy(b))])["generation_id"])

    def test_edited_definition_detected(self):
        sid, d = SPACE[0]
        d2 = dict(d, holding=999)
        with self.assertRaises(ManifestError):
            build_manifest([(sid, d2)])

    def test_rule_change_creates_new_generation(self):
        with tempfile.TemporaryDirectory() as td:
            st = PreregistrationStore(Path(td))
            g1 = st.ensure(build_manifest(SPACE))
            raw1 = st.path_for(g1["generation_id"]).read_bytes()
            g1b = st.ensure(build_manifest(list(reversed(SPACE))))     # repeat → reuse
            self.assertFalse(g1b["created"])
            g2 = st.ensure(build_manifest(SPACE[:4]))                    # rule change
            self.assertTrue(g2["created"])
            self.assertNotEqual(g1["generation_id"], g2["generation_id"])
            self.assertEqual(st.path_for(g1["generation_id"]).read_bytes(), raw1)
            gens = st.generations()
            self.assertEqual([g["generation_id"] for g in gens],
                             [g1["generation_id"], g2["generation_id"]])
            self.assertEqual(gens[1]["previous_generation"], g1["generation_id"])

    def test_tampered_manifest_refused(self):
        with tempfile.TemporaryDirectory() as td:
            st = PreregistrationStore(Path(td))
            g = st.ensure(build_manifest(SPACE))
            p = st.path_for(g["generation_id"])
            doc = json.loads(p.read_text())
            sid, canon = doc["candidates"][0]
            doc["candidates"][0] = [sid, canon.replace('"holding":', '"holding":7,"x":')]
            p.write_text(json.dumps(doc))
            with self.assertRaises(PreregistrationConflict):
                st.ensure(build_manifest(SPACE))

    def test_legacy_file_not_reused(self):
        from runner import Runner
        with tempfile.TemporaryDirectory() as td:
            paths = make_paths(td)
            paths.state_dir.mkdir(parents=True)
            legacy = paths.state_dir / "preregistration.json"
            legacy.write_text(json.dumps({"total_candidates": 779, "note": "no hash"}))
            before = legacy.read_bytes()
            r = Runner(paths, candidates_fn=lambda: (SPACE, []))
            info = r.register_all()
            self.assertTrue(info["created"])
            doc = r.prereg.load(info["generation_id"])
            self.assertTrue(doc["legacy_preregistration_present"])
            self.assertFalse(doc["legacy_preregistration_reused"])
            self.assertEqual(legacy.read_bytes(), before)

    def test_real_space_repeatable(self):
        a = build_manifest(*enumerate_candidates())
        b = build_manifest(*enumerate_candidates())
        self.assertEqual(a["n_candidates"], 779)
        self.assertEqual(a["n_families"], 7)
        self.assertEqual(a["generation_id"], b["generation_id"])


# ===========================================================================
# P1-9  promotion evidence
# ===========================================================================

def _pf_evidence(ctx="C2-x", drift=0.001):
    bars = make_bars(n=200, drift=drift, vol=0.0, seed=21, volume=1e9)
    pf = simulate_portfolio({"A": bars}, [long_sig(40, 20)],
                            {"A": make_funding(bars[0].ts, bars[-1].ts, rate=0.0)},
                            CostContext.futures_default(),
                            PortfolioConfig(initial_capital=10_000, size_fraction=0.1),
                            context_id=ctx)
    return pf.to_json()


GATE_OK = dict(state="PASS", preregistered=True, fdr_survived=True,
               independent_validation_passed=True, lock_test_passed=True, lock_test_used=1,
               required_symbols=["BTCUSDT"], cost_verified=True)


class TestPromotionEvidence(unittest.TestCase):
    def test_combo_without_portfolio(self):
        chk = combo_promotion_gate(portfolio_result=None, context_id="C2-x", **GATE_OK)
        self.assertFalse(chk.eligible)
        self.assertIn("portfolio simulation result missing", chk.failed_steps)

    def test_combo_portfolio_fail(self):
        chk = combo_promotion_gate(portfolio_result=_pf_evidence(drift=-0.001),
                                   context_id="C2-x", **GATE_OK)
        self.assertFalse(chk.eligible)

    def test_combo_portfolio_pass(self):
        chk = combo_promotion_gate(portfolio_result=_pf_evidence(), context_id="C2-x", **GATE_OK)
        self.assertTrue(chk.eligible, chk.failed_steps)
        self.assertEqual(chk.kind, "SURVIVOR_COMBO")

    def test_combo_context_mismatch(self):
        chk = combo_promotion_gate(portfolio_result=_pf_evidence(ctx="C2-other"),
                                   context_id="C2-x", **GATE_OK)
        self.assertFalse(chk.eligible)

    def test_lock_missing_or_reused_or_unregistered(self):
        for kw in (dict(lock_test_passed=None), dict(lock_test_used=2),
                   dict(preregistered=False), dict(fdr_survived=None),
                   dict(cost_verified=False), dict(lock_test_used=0)):
            self.assertFalse(promotion_gate(**dict(GATE_OK, **kw)).eligible, kw)

    def test_single_vs_combo(self):
        self.assertTrue(promotion_gate(**GATE_OK).eligible)
        self.assertFalse(combo_promotion_gate(**GATE_OK).eligible)       # no evidence
        self.assertFalse(promotion_gate(**GATE_OK, portfolio_passed=True).eligible)

    def test_runner_dispatches_combo_gate(self):
        from runner import Runner
        combo = dict(LOCK_DEFN, family="combo", legs=["a", "b"])
        with tempfile.TemporaryDirectory() as td:
            r = Runner(make_paths(td), candidates_fn=lambda: ([(LOCK_SID, LOCK_DEFN)], []))
            r.register_all()
            r._ctx = "C2-x"
            dec = {"survivors": [LOCK_SID]}
            out = r._promote(LOCK_SID, combo, {"validation_verdict": "PASS", "portfolio": None}, dec)
            self.assertFalse(out["eligible"])
            self.assertIn("combo", out["notes"])
            self.assertTrue(any("cost model UNVERIFIED" in s for s in out["failed_steps"]))
            self.assertEqual(r.promo.entries(), [])


# ===========================================================================
# P1-10  run_cycle.sh
# ===========================================================================

class TestRunCycle(unittest.TestCase):
    def _run(self, args, *, env_extra=None, cwd=None, data=True):
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        root, ddir = Path(td) / "disc", Path(td) / "data"
        if data:
            (ddir / "klines").mkdir(parents=True)
        env = dict(os.environ, NEW2_DISCOVERY_ROOT=str(root), NEW2_DATA_DIR=str(ddir))
        env.pop("NEW2_DISC_BATCH", None)
        env.update(env_extra or {})
        r = subprocess.run(["bash", str(DISC / "run_cycle.sh"), *args], env=env, cwd=cwd or td,
                           capture_output=True, text=True, timeout=300)
        summary = None
        for line in r.stdout.splitlines():
            if line.startswith("{"):
                summary = json.loads(line)
        return r, summary, root

    def test_batch_1(self):
        r, s, _ = self._run(["1"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(s["batch_requested"], 1)
        self.assertEqual(s["evaluated"], 1)
        self.assertIn("CYCLE_OK", r.stdout)

    def test_batch_10(self):
        r, s, _ = self._run(["10"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual((s["batch_requested"], s["evaluated"]), (10, 10))

    def test_default_batch(self):
        r, s, _ = self._run([])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(s["batch_requested"], 10)

    def test_invalid_batch(self):
        for args in (["abc"], ["0"], ["-3"], ["1", "2"], ["1.5"]):
            r, s, root = self._run(args)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn("CYCLE_FAILED", r.stdout)
            self.assertNotIn("CYCLE_OK", r.stdout)
            self.assertFalse((root / "state").exists(), args)   # runner never started

    def test_python_error_exit_code(self):
        r, s, _ = self._run(["1"], data=False)       # NEW2_DATA_DIR missing → runner 1
        self.assertEqual(r.returncode, 1)
        self.assertIn("CYCLE_FAILED rc=1", r.stdout)
        self.assertNotIn("CYCLE_OK", r.stdout)

    def test_log_pipeline_failure(self):
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        logdir = Path(td) / "log_is_a_dir"
        logdir.mkdir()
        r, s, _ = self._run(["1"], env_extra={"NEW2_DISC_LOG": str(logdir)})
        self.assertEqual(r.returncode, 1)
        self.assertIn("log pipeline failed", r.stdout)

    def test_other_working_directory(self):
        r, s, root = self._run(["1"], cwd="/")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((root / "cycle_log.txt").exists())
        self.assertTrue((root / "state" / "state.json").exists())

    def test_runner_rejects_bad_args_directly(self):
        from runner import UsageError, parse_args
        self.assertEqual(parse_args(["--batch", "7"]), 7)
        self.assertEqual(parse_args(["--batch=3"]), 3)
        self.assertEqual(parse_args([]), 10)
        for bad in (["--batch", "0"], ["--batch", "-1"], ["--batch"], ["--bogus"], ["5"]):
            with self.assertRaises(UsageError):
                parse_args(bad)


# ===========================================================================
# P2  status consistency
# ===========================================================================

class TestStatusConsistency(unittest.TestCase):
    def _ev(self, nets):
        return [EventRecord(symbol="X", entry_index=i, exit_index=i + 1, side=1, entry_ts=i,
                            exit_ts=i + 1, gross_bp=x, cost_bp=0, funding_bp=0, net_bp=x)
                for i, x in enumerate(nets)]

    def test_nan_returns_are_error_not_pass(self):
        nets = [10.0] * 40 + [float("nan")]
        self.assertEqual(cell_stats(self._ev(nets), "X", "c")["status"], "ERROR")
        self.assertEqual(cell_stats(self._ev([5.0, float("inf")] * 20), "X", "c")["status"], "ERROR")

    def test_power_and_missing_never_pass(self):
        self.assertEqual(cell_stats(self._ev([50.0] * 10), "X", "c")["status"], "UNDETERMINED_POWER")
        self.assertEqual(cell_stats([], "X", "c")["status"], "DATA_MISSING")

    def test_verdict_precedence(self):
        def sr(sym, st, p=0.001):
            return SymbolResult(symbol=sym, cells=[CellResult(sym, "c", 40, 5.0, 3.0, p, st)], status=st)
        v, _ = verdict_from_cells([sr("A", "PASS"), sr("B", "ERROR")], ["A", "B"])
        self.assertEqual(v, "ERROR")
        v, _ = verdict_from_cells([sr("A", "PASS"), sr("B", "WEIRD")], ["A", "B"])
        self.assertEqual(v, "ERROR")
        v, _ = verdict_from_cells([sr("A", "PASS")], ["A", "B"])    # B not evaluated
        self.assertEqual(v, "DATA_MISSING")
        v, _ = verdict_from_cells([sr("A", "PASS"), sr("B", "FAIL_STAT")], ["A", "B"])
        self.assertEqual(v, "FAIL_STAT")
        v, _ = verdict_from_cells([sr("A", "DATA_MISSING"), sr("B", "FAIL_STAT")], ["A", "B"])
        self.assertEqual(v, "DATA_MISSING")         # missing data never laundered into FAIL

    def test_nan_metrics_cannot_pass_portfolio(self):
        pf = _pf_evidence()
        for k in ("final_equity", "max_drawdown"):
            self.assertFalse(portfolio_passes(dict(pf, **{k: float("nan")}))[0])

    def test_unverified_cost_pass_is_labelled(self):
        # the production cost context is UNVERIFIED and cannot promote
        self.assertFalse(CostContext.futures_default().verified)
        self.assertFalse(promotion_gate(**dict(GATE_OK, cost_verified=False)).eligible)

    def test_queued_unknown_candidate_is_reported(self):
        cls = _fake_runner_cls()
        with tempfile.TemporaryDirectory() as td:
            r = cls(make_paths(td), candidates_fn=lambda: (SPACE, []))
            r.register_all()
            r.store.set("S-ghost", CandidateState.RUNNING)
            r.store.set("S-ghost", CandidateState.DATA_MISSING)
            r.queue.enqueue("S-ghost", "data_refresh_check")
            s = r.cycle(max_candidates=1)
            self.assertTrue(any("S-ghost" in n for n in s.notes))
            self.assertGreaterEqual(s.errors, 1)


# ===========================================================================
# recovery: store / ledger / restart
# ===========================================================================

class TestRecovery(unittest.TestCase):
    def test_awaiting_state_survives_restart(self):
        cls = _fake_runner_cls()
        with tempfile.TemporaryDirectory() as td:
            paths = make_paths(td)
            r1 = cls(paths, candidates_fn=lambda: (SPACE, []))
            r1.cycle(max_candidates=4)
            # simulated crash mid-run: a candidate stuck RUNNING, no record
            r1.store.set(SIDS[5], CandidateState.RUNNING)
            del r1
            r2 = cls(paths, candidates_fn=lambda: (SPACE, []))
            s = r2.cycle(max_candidates=6)
            # interrupted candidate was requeued (backoff) — family not complete yet
            self.assertEqual(r2.store.entry(SIDS[5]).state, "REVERIFY_PENDING")
            self.assertFalse(s.fdr_family["complete"])
            # once the backoff elapses it re-runs and the family is decided
            for it in r2.queue._items:
                it["next_retry_after_ts"] = 0
            r2.queue.save()
            s2 = r2.cycle(max_candidates=6)
            self.assertTrue(s2.fdr_family["complete"], s2.fdr_family)
            self.assertEqual(s2.fdr_family["decision"]["survivors"], EXPECTED_SURVIVORS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
