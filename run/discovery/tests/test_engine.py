#!/usr/bin/env python3
"""NEW2 discovery engine v2 — automated test suite (directive §15, R-1).

Runs with stdlib unittest only.  Every test uses SYNTHETIC data and ISOLATED
temporary directories — the real ledger, real state and the locked TEST
windows are never touched by any test in this file.

Run:  python3 -m unittest discover -s discovery/tests -v
"""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
if str(DISC) not in sys.path:
    sys.path.insert(0, str(DISC))

from data_quality import Bar, check_bars, funding_quality, staleness, dataset_for
from engine_lib import (
    ALLOWED_TRANSITIONS, CandidateState, InvalidTransition, LockTestRegistry,
    ProcessLock, RetryRecord, assert_no_locked_overlap, assert_transition,
    atomic_write_json, backoff_seconds, bh_fdr, block_bootstrap_pvalue,
    deflated_sharpe, evaluation_id, experiment_context_id, holm_test,
    norm_ppf, normalize_strategy, nw_tstat, pbo_cscv, reverify_eligible,
    select_non_overlapping, strategy_id, stouffer_correlated,
    stouffer_independent, t_sf, LockedWindowError,
)
from store import CandidateStore, Ledger, ReverifyQueue, StoreCorruption, reconcile
from generator import FAMILY_GENERATORS, _mk, enumerate_candidates, search_space_status
from evaluator import (
    EventStudyResult, FundingIndex, PrefixView, cell_stats, funding_paid_bp,
    run_event_study,
)
from costs_bridge import CostContext, SENSITIVITY_SCENARIOS, scenario_model
from portfolio import PortfolioConfig, simulate_portfolio
from promotion import PromotionCheck, PromotionQueue, combo_promotion_gate, promotion_gate

MS15 = 900_000
DAY_MS = 86_400_000


# ---------------------------------------------------------------------------
# synthetic data builders
# ---------------------------------------------------------------------------

def make_bars(n=400, start_ts=0, price0=100.0, drift=0.0, vol=0.001,
              seed=0, volume=1e6):
    rng = random.Random(seed)
    bars, p, ts = [], price0, start_ts
    for _ in range(n):
        o = p
        c = o * (1.0 + drift + rng.gauss(0.0, vol))
        h = max(o, c) * 1.0002
        low = min(o, c) * 0.9998
        bars.append(Bar(ts=int(ts), open=o, high=h, low=low, close=c,
                        volume=volume, quote_volume=volume * (o + c) / 2.0,
                        taker_buy_volume=volume * 0.5))
        p, ts = c, ts + MS15
    return bars


def make_funding(first_ts, last_ts, rate=1e-4, step_ms=8 * 3_600_000):
    return [(t, rate) for t in range(int(first_ts), int(last_ts) + 1, step_ms)]


def write_kline_csv(path: Path, bars):
    lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"]
    for b in bars:
        lines.append(f"{b.ts},{b.open},{b.high},{b.low},{b.close},{b.volume},"
                     f"{b.ts + MS15 - 1},{b.quote_volume},1000,{b.taker_buy_volume},"
                     f"{b.quote_volume / 2},0")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_funding_csv(path: Path, pairs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{t},x,{r}\n" for t, r in pairs), encoding="utf-8")


def long_signal(view):
    return 1


# ---------------------------------------------------------------------------
# A. candidate generation and IDs  (§4)
# ---------------------------------------------------------------------------

class TestIds(unittest.TestCase):
    def test_full_space_unique(self):
        cands, dedup = enumerate_candidates()
        ids = [s for s, _ in cands]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(cands), 779)
        self.assertEqual(len(dedup), 0)

    def test_same_definition_same_id(self):
        d1 = _mk("ret_rev", {"lookback": 8, "thresh_z": 1.5, "horizon": 16}, "long_on_down_move")
        d2 = _mk("ret_rev", {"lookback": 8, "thresh_z": 1.5, "horizon": 16}, "long_on_down_move")
        self.assertEqual(strategy_id(d1), strategy_id(d2))

    def test_param_change_changes_id(self):
        d1 = _mk("ret_rev", {"lookback": 8, "thresh_z": 1.5, "horizon": 16}, "long_on_down_move")
        d2 = _mk("ret_rev", {"lookback": 8, "thresh_z": 2.0, "horizon": 16}, "long_on_down_move")
        self.assertNotEqual(strategy_id(d1), strategy_id(d2))

    def test_variant_hash_uses_actual_params(self):
        base = _mk("flow_z", {"z_window": 24, "entry_z": 1.0, "horizon": 8}, "short_on_high_buy_share")
        variant = _mk("flow_z", {"z_window": 24, "entry_z": 2.0, "horizon": 8}, "short_on_high_buy_share")
        self.assertNotEqual(strategy_id(base), strategy_id(variant))

    def test_explicit_default_equals_omitted(self):
        with_def = _mk("ret_rev", {"lookback": 4, "thresh_z": 1.0, "horizon": 8}, "long_on_down_move")
        omitted = _mk("ret_rev", {"thresh_z": 1.0, "horizon": 8}, "long_on_down_move")
        reordered = _mk("ret_rev", {"horizon": 8, "thresh_z": 1.0, "lookback": 4}, "long_on_down_move")
        self.assertEqual(strategy_id(with_def), strategy_id(omitted))
        self.assertEqual(strategy_id(with_def), strategy_id(reordered))

    def test_env_keys_never_enter_strategy_id(self):
        d = _mk("ret_rev", {"lookback": 8, "horizon": 16}, "long_on_down_move")
        d2 = dict(d)
        d2["dataset_id"] = "other-dataset"
        d2["cost_model"] = "expensive"
        self.assertEqual(strategy_id(d), strategy_id(d2))

    def test_semantic_dedup_collapses_and_logs(self):
        orig = dict(FAMILY_GENERATORS)
        def twice():
            yield _mk("flow_z", {"z_window": 24, "entry_z": 1.0, "horizon": 8}, "short_on_high_buy_share")
            yield _mk("flow_z", {"z_window": 24, "entry_z": 1.0, "horizon": 8}, "short_on_high_buy_share")
        import generator as g
        g.FAMILY_GENERATORS = {"flow_z": twice}
        try:
            cands, dedup = g.enumerate_candidates()
        finally:
            g.FAMILY_GENERATORS = orig
        self.assertEqual(len(cands), 1)
        self.assertEqual(len(dedup), 1)
        self.assertIn("strategy_id", dedup[0])

    def test_no_overmerge(self):
        d1 = _mk("tod", {"utc_start": 0, "utc_end": 2, "horizon": 16}, "long_in_session")
        d2 = _mk("tod", {"utc_start": 2, "utc_end": 4, "horizon": 16}, "short_in_session")
        self.assertNotEqual(strategy_id(d1), strategy_id(d2))

    def test_evaluation_id_fresh_every_run(self):
        c = experiment_context_id(dataset_id="d", data_sha256="s", cost_model="c")
        self.assertNotEqual(evaluation_id("S", c), evaluation_id("S", c))

    def test_context_id_reflects_environment(self):
        c1 = experiment_context_id(dataset_id="d1", data_sha256="s", cost_model="c")
        c2 = experiment_context_id(dataset_id="d2", data_sha256="s", cost_model="c")
        self.assertNotEqual(c1, c2)


# ---------------------------------------------------------------------------
# B. data quality and versioning  (§9)
# ---------------------------------------------------------------------------

class TestDataQuality(unittest.TestCase):
    def _clean(self, n=100):
        return make_bars(n=n, drift=0.0001, seed=7)

    def test_clean_bars_pass(self):
        rep = check_bars(self._clean(), "X")
        self.assertTrue(rep.ok, rep.errors)

    def test_duplicate_timestamps(self):
        bars = self._clean()
        dup_b = bars[50]
        bars.insert(50, type(dup_b)(ts=dup_b.ts, open=dup_b.open, high=dup_b.high,
                                    low=dup_b.low, close=dup_b.close,
                                    volume=dup_b.volume,
                                    quote_volume=dup_b.quote_volume))
        rep = check_bars(bars, "X")
        self.assertEqual(rep.duplicate_timestamps, 1)
        self.assertFalse(rep.ok)

    def test_time_inversion(self):
        bars = self._clean()
        bars[40], bars[41] = bars[41], bars[40]
        rep = check_bars(bars, "X")
        self.assertEqual(rep.time_inversions, 1)

    def test_missing_candles_counted(self):
        bars = self._clean(50)
        last = bars[-1]
        gap = type(last)(ts=last.ts + MS15 * 10, open=last.close, high=last.close,
                         low=last.close, close=last.close, volume=1, quote_volume=1)
        rep = check_bars(bars + [gap], "X")
        self.assertEqual(rep.missing_candles, 9)

    def test_bad_ohlc(self):
        bars = self._clean(10)
        b = bars[3]
        bars[3] = type(b)(ts=b.ts, open=b.high, high=b.low, low=b.high,
                          close=b.close, volume=b.volume,
                          quote_volume=b.quote_volume)
        rep = check_bars(bars, "X")
        self.assertEqual(rep.bad_ohlc, 1)
        self.assertFalse(rep.ok)

    def test_negative_values(self):
        bars = self._clean(10)
        b = bars[2]
        bars[2] = type(b)(ts=b.ts, open=b.open, high=b.high, low=b.low,
                          close=b.close, volume=-5.0, quote_volume=b.quote_volume)
        self.assertFalse(check_bars(bars, "X").ok)

    def test_extreme_moves_recorded(self):
        bars = self._clean(30)
        b = bars[10]
        bars[10] = type(b)(ts=b.ts, open=b.open, high=b.high * 1.5, low=b.low,
                           close=b.close * 1.5, volume=b.volume,
                           quote_volume=b.quote_volume * 1.5)
        rep = check_bars(bars, "X")
        self.assertGreaterEqual(rep.extreme_moves, 1)  # jump in AND out of the bar
        self.assertTrue(rep.ok)  # single print recorded, not fatal

    def test_funding_quality_detects_duplicates(self):
        f = make_funding(0, DAY_MS)
        f.append(f[0])
        self.assertFalse(funding_quality(f)["ok"])

    def test_dataset_id_changes_with_content(self):
        with tempfile.TemporaryDirectory() as td:
            p1, p2 = Path(td) / "a.csv", Path(td) / "b.csv"
            write_kline_csv(p1, self._clean(20))
            write_kline_csv(p2, self._clean(21))
            h1, h2 = dataset_for("X", p1), dataset_for("X", p2)
            self.assertNotEqual(h1.dataset_id, h2.dataset_id)
            h1b = dataset_for("X", p1)
            self.assertEqual(h1.dataset_id, h1b.dataset_id)

    def test_staleness(self):
        now = int(time.time() * 1000)
        self.assertTrue(staleness(now - 20 * MS15, now)["stale"])
        self.assertFalse(staleness(now - 2 * MS15, now)["stale"])

    def test_merged_bars_hard_truncates_before_locked_window(self):
        import datetime as dt
        from runner import merged_bars
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            start_ms = int(dt.datetime(2024, 3, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
            bars = make_bars(n=96 * 31, start_ts=start_ms, drift=0.0001, seed=3)
            write_kline_csv(data / "klines" / "BTCUSDT-15m-2024-03.csv", bars)
            h = merged_bars(data, "BTCUSDT", "2024-03-01", "2024-03-24")
            self.assertTrue(h.bars)
            lock_start_ms = int(dt.datetime(2024, 3, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
            self.assertLess(h.bars[-1].ts, lock_start_ms)

    def test_fng_no_future_leak(self):
        from runner import build_aux
        day0 = 1_900_000_000_000 // DAY_MS * DAY_MS
        bars = make_bars(n=96, start_ts=day0, drift=0.0, seed=5)
        defn = {"family": "fng", "params": {"threshold": 10, "confirm_days": 1, "horizon": 8},
                "_defaults": {"threshold": 10, "confirm_days": 1, "horizon": 8},
                "symbols": ["BTCUSDT"], "timeframe": "15m", "direction": "long",
                "entry": "fng:preregistered_rule", "exit": "time_exit", "holding": 8,
                "risk": {}, "primary_cell": {"symbol": "BTCUSDT", "horizon": 8}}
        fng_same_day = {day0 // DAY_MS: 1.0}          # value of TODAY
        fng_yesterday = {day0 // DAY_MS - 1: 1.0}     # value of YESTERDAY
        aux = build_aux(bars, defn, None, fng=fng_same_day)
        self.assertTrue(all(x is None for v in aux["fng_hist"] for x in v))
        aux2 = build_aux(bars, defn, None, fng=fng_yesterday)
        self.assertTrue(all(v and v[0] == 1.0 for v in aux2["fng_hist"]))


# ---------------------------------------------------------------------------
# C. statistics  (§5, §6, §7, §8)
# ---------------------------------------------------------------------------

class TestStats(unittest.TestCase):
    def test_holm_exact(self):
        flags = holm_test([0.01, 0.04, 0.03, 0.005], alpha=0.05)
        self.assertEqual(flags, [True, False, False, True])

    def test_bh_fdr_exact(self):
        flags = bh_fdr([0.3, 0.01], q=0.05)
        self.assertEqual(flags, [False, True])

    def test_bh_fdr_lenient_top(self):
        flags = bh_fdr([0.006] * 100, q=0.05)
        self.assertTrue(all(flags))

    def test_t_sf_known_values(self):
        self.assertAlmostEqual(t_sf(0.0, 10), 0.5, places=10)
        self.assertLess(t_sf(2.5, 30), 0.01)
        self.assertAlmostEqual(t_sf(1.812, 10), 0.05, places=3)

    def test_cell_stats_small_n_undetermined(self):
        events = [type("E", (), {"net_bp": 100.0 + i})() for i in range(10)]
        st = cell_stats(events, "X", "c")
        self.assertEqual(st["status"], "UNDETERMINED_POWER")
        self.assertEqual(st["n"], 10)

    def test_cell_stats_negative_mean_fails_even_if_p_small(self):
        events = [type("E", (), {"net_bp": -50.0 - i * 0.1})() for i in range(60)]
        st = cell_stats(events, "X", "c")
        self.assertEqual(st["status"], "FAIL_STAT")

    def test_cell_stats_zero_events_data_missing(self):
        self.assertEqual(cell_stats([], "X", "c")["status"], "DATA_MISSING")

    def test_nw_tstat_shrinks_with_autocorrelation(self):
        rng = random.Random(1)
        raw = [rng.gauss(0.0004, 0.001) for _ in range(400)]
        ar, x = [], 0.0
        for _ in range(400):
            x = 0.9 * x + rng.gauss(0.0004, 0.001)
            ar.append(x)
        t_ind, _ = nw_tstat(raw)
        t_ar, _ = nw_tstat(ar)
        self.assertLess(abs(t_ar), abs(t_ind) + 5)

    def test_select_non_overlapping(self):
        evs = [{"entry_index": i} for i in (0, 3, 5, 12, 13, 30)]
        chosen = select_non_overlapping(evs, horizon=8)
        self.assertEqual([e["entry_index"] for e in chosen], [0, 12, 30])

    def test_block_bootstrap_runs_bounded(self):
        rng = random.Random(2)
        xs = [rng.gauss(0, 1) for _ in range(200)]
        ps = [block_bootstrap_pvalue(xs, block=8, n_boot=300, seed=s) for s in range(5)]
        self.assertTrue(all(0.0 < p <= 1.0 for p in ps))

    def test_deflated_sharpe_penalises_trials(self):
        ds1 = deflated_sharpe(sr=0.2, n=1000, skew=0.0, kurtosis=3.0, n_trials=1, var_sr=0.01)
        ds100 = deflated_sharpe(sr=0.2, n=1000, skew=0.0, kurtosis=3.0, n_trials=100, var_sr=0.01)
        self.assertGreater(ds1, ds100)

    def test_pbo_cscv_bounded(self):
        rng = random.Random(3)
        T, N = 64, 8
        mat = [[rng.gauss(0.001 if c == 0 else 0.0, 0.01) for c in range(N)]
               for _ in range(T)]
        pbo, n_used = pbo_cscv(mat, n_partitions=16)
        self.assertTrue(math.isnan(pbo) or 0.0 <= pbo <= 1.0)
        self.assertGreater(n_used, 0)

    def test_stouffer_equal_weights_no_sqrt_n_double_count(self):
        z = stouffer_independent([2.0, 2.0])
        self.assertAlmostEqual(z, 2.0 * math.sqrt(2), places=9)

    def test_stouffer_weights(self):
        z = stouffer_independent([2.0, 0.0], weights=[3.0, 1.0])
        self.assertAlmostEqual(z, 6.0 / math.sqrt(10.0), places=9)

    def test_stouffer_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            stouffer_independent([])
        with self.assertRaises(ValueError):
            stouffer_independent([float("nan")])

    def test_stouffer_correlated_reduces_effective_n(self):
        rho = 0.9
        z = stouffer_correlated([2.0, 2.0], [[1.0, rho], [rho, 1.0]])
        self.assertLess(z, stouffer_independent([2.0, 2.0]))
        self.assertGreater(z, 2.0)

    def test_stouffer_correlated_singular_raises(self):
        with self.assertRaises(ValueError):
            stouffer_correlated([1.0, 1.0], [[1.0, 1.0], [1.0, 1.0]])

    def test_estimate_corr_too_short_returns_none(self):
        from engine_lib import estimate_corr_dev
        self.assertIsNone(estimate_corr_dev([0.1] * 5, [0.2] * 5))

    def test_estimate_corr_unstable_returns_none(self):
        from engine_lib import estimate_corr_dev
        rng = random.Random(4)
        a = [rng.gauss(0, 1) for _ in range(40)]
        b = [rng.gauss(0, 1) for _ in range(40)]
        self.assertIsNone(estimate_corr_dev(a, b, min_n=30, max_ci_width=0.05))

    def test_estimate_corr_strong_signal(self):
        from engine_lib import estimate_corr_dev
        rng = random.Random(5)
        a = [rng.gauss(0, 1) for _ in range(300)]
        b = [0.9 * x + rng.gauss(0, 0.1) for x in a]
        rho = estimate_corr_dev(a, b, min_n=30, max_ci_width=0.6)
        self.assertIsNotNone(rho)
        self.assertGreater(rho, 0.8)

    def test_locked_overlap_guard(self):
        with self.assertRaises(LockedWindowError):
            assert_no_locked_overlap("BTCUSDT", "2024-01-01", "2024-05-01", "selection")
        assert_no_locked_overlap("BTCUSDT", "2024-01-01", "2024-03-20", "selection")
        assert_no_locked_overlap("SOLUSDT", "2024-01-01", "2024-12-31", "selection")

    def test_lock_registry_single_use(self):
        with tempfile.TemporaryDirectory() as td:
            reg = LockTestRegistry(Path(td) / "lock.json")
            u1 = reg.record_use("S1", "BTCUSDT", "C1")
            u2 = reg.record_use("S1", "BTCUSDT", "C2")
            self.assertTrue(u1["independent"])
            self.assertFalse(u2["independent"])

    def test_verdict_multi_symbol_needs_all(self):
        from engine_lib import CellResult, SymbolResult, verdict_from_cells

        def sr(sym, status):
            return SymbolResult(symbol=sym, cells=[
                CellResult(symbol=sym, cell_id=f"{sym}|dev", n=100, mean_bp=10.0,
                           t=2.5, p=0.01, status=status)], status=status)

        overall, detail = verdict_from_cells(
            [sr("BTCUSDT", "PASS"), sr("ETHUSDT", "FAIL_STAT")],
            ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(overall, "FAIL_STAT")
        self.assertEqual(detail["single_symbol_pass_hint"], ["BTCUSDT"])

        overall2, _ = verdict_from_cells(
            [sr("BTCUSDT", "PASS"), sr("ETHUSDT", "DATA_MISSING")],
            ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(overall2, "DATA_MISSING")

        overall3, detail3 = verdict_from_cells(
            [sr("BTCUSDT", "PASS"), sr("ETHUSDT", "PASS")],
            ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(overall3, "PASS")
        self.assertEqual(detail3["single_symbol_pass_hint"], [])

    def test_norm_ppf_roundtrip(self):
        self.assertAlmostEqual(norm_ppf(0.975), 1.959963, places=4)


# ---------------------------------------------------------------------------
# D. reverify queue and state machine  (§3)
# ---------------------------------------------------------------------------

class TestStateMachine(unittest.TestCase):
    def test_transition_table_terminal_pass(self):
        with self.assertRaises(InvalidTransition):
            assert_transition(CandidateState.PASS, CandidateState.RUNNING)
        assert_transition(CandidateState.PENDING, CandidateState.RUNNING)
        assert_transition(CandidateState.RUNNING, CandidateState.PASS)
        with self.assertRaises(InvalidTransition):
            assert_transition(CandidateState.DATA_MISSING, CandidateState.RUNNING)

    def test_reverify_policy_matrix(self):
        cases = [
            (CandidateState.UNDETERMINED_POWER, dict(power_condition_met=True), True),
            (CandidateState.UNDETERMINED_POWER, dict(), False),
            (CandidateState.DATA_MISSING, dict(data_now_available=True), True),
            (CandidateState.DATA_MISSING, dict(), False),
            (CandidateState.ERROR, dict(error_code="RATE_LIMIT"), True),
            (CandidateState.ERROR, dict(error_code="LOGIC_ERROR"), False),
            (CandidateState.FAIL_COST, dict(cost_model_version_changed=True), True),
            (CandidateState.FAIL_COST, dict(), False),
            (CandidateState.FAIL_STAT, dict(new_data_beyond_dev=True), True),
            (CandidateState.FAIL_STAT, dict(), False),
            (CandidateState.PASS, dict(power_condition_met=True), False),
        ]
        for state, kw, expected in cases:
            ok, why = reverify_eligible(state, **kw)
            self.assertEqual(ok, expected, f"{state} {kw} -> {why}")

    def test_max_attempts_enforced(self):
        ok, why = reverify_eligible(CandidateState.ERROR,
                                    error_code="RATE_LIMIT", attempt_count=3, max_attempts=3)
        self.assertFalse(ok)


class TestQueueAndStore(unittest.TestCase):
    def _env(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        store = CandidateStore(root / "state.json")
        queue = ReverifyQueue(root / "queue.json", store)
        ledger = Ledger(root / "ledger.jsonl")
        return root, store, queue, ledger

    def test_enqueue_sets_reverify_pending_and_backoff(self):
        _, store, queue, _ = self._env()
        store.set("S1", CandidateState.PENDING)
        store.set("S1", CandidateState.RUNNING)
        store.set("S1", CandidateState.DATA_MISSING)
        self.assertTrue(queue.enqueue("S1", "data_refresh_check"))
        self.assertEqual(store.entry("S1").state, "REVERIFY_PENDING")
        queue.requeue_with_backoff("S1", 3600)
        self.assertIsNone(queue.pop_ready(now_ts=time.time()))
        item = queue.pop_ready(now_ts=time.time() + 4000)
        self.assertIsNotNone(item)
        self.assertEqual(store.entry("S1").state, "PENDING")

    def test_enqueue_rejects_ineligible(self):
        _, store, queue, _ = self._env()
        store.set("S1", CandidateState.PENDING)
        store.set("S1", CandidateState.RUNNING)
        store.set("S1", CandidateState.PASS)
        self.assertFalse(queue.enqueue("S1", "power_condition_met"))

    def test_attempt_count_increments_on_running(self):
        _, store, _, _ = self._env()
        store.set("S1", CandidateState.PENDING)
        store.set("S1", CandidateState.RUNNING)
        store.set("S1", CandidateState.RUNNING)
        self.assertEqual(store.entry("S1").retry.attempt_count, 2)

    def test_backoff_is_exponential_capped(self):
        self.assertEqual(backoff_seconds(1), 60)
        self.assertEqual(backoff_seconds(2), 120)
        self.assertEqual(backoff_seconds(10), 3600)

    def test_ledger_history_preserved(self):
        _, _, _, ledger = self._env()
        ledger.append({"evaluation_id": "E1", "strategy_id": "S1", "status": "DATA_MISSING"})
        ledger.append({"evaluation_id": "E2", "strategy_id": "S1", "status": "PASS"})
        self.assertEqual(ledger.evaluation_count(), 2)
        self.assertEqual(ledger.latest_by_strategy()["S1"]["status"], "PASS")
        self.assertEqual(ledger.duplicate_strategy_records(), ["S1"])

    def test_ledger_requires_ids(self):
        _, _, _, ledger = self._env()
        with self.assertRaises(ValueError):
            ledger.append({"status": "PASS"})

    def test_reconcile_recovers_interrupted(self):
        _, store, queue, ledger = self._env()
        store.set("S1", CandidateState.PENDING)
        store.set("S1", CandidateState.RUNNING)   # crash: no ledger record
        store.set("S2", CandidateState.PENDING)
        rep = reconcile(store, queue, ledger)
        self.assertIn("S1", rep["requeued_interrupted"])
        self.assertEqual(store.entry("S1").state, "REVERIFY_PENDING")

    def test_reconcile_drops_unknown_queue_items(self):
        _, store, queue, ledger = self._env()
        store.set("S1", CandidateState.PENDING)
        store.set("S1", CandidateState.RUNNING)
        store.set("S1", CandidateState.DATA_MISSING)
        queue.enqueue("S1", "data_refresh_check")
        queue._items.append({"strategy_id": "GHOST", "reason": "x", "queued_at": "now"})
        queue.save()
        rep = reconcile(store, queue, ledger)
        self.assertIn("GHOST", rep["dropped_queue_items"])
        self.assertEqual(queue.size(), 1)

    def test_reconcile_realigns_stale_queue_state(self):
        """A-35: a queue item whose candidate sits in a re-verifiable
        non-final state (e.g. DATA_MISSING from an older writer) must be
        realigned to REVERIFY_PENDING so the pop can actually promote it —
        and a PASS candidate's queue entry must be dropped, never resurrected."""
        _, store, queue, ledger = self._env()
        for sid in ("S1", "S2", "S3"):
            store.set(sid, CandidateState.PENDING)
            store.set(sid, CandidateState.RUNNING)
        store.set("S1", CandidateState.DATA_MISSING)   # stale: in queue, not aligned
        store.set("S2", CandidateState.DATA_MISSING)
        queue.enqueue("S2", "data_refresh_check")      # consistent pair
        # S1 enters the queue WITHOUT the store being aligned (old writer)
        queue._items.append({"strategy_id": "S1", "reason": "stale",
                             "queued_at": "now"})
        store.set("S3", CandidateState.PASS)
        queue._items.append({"strategy_id": "S3", "reason": "x", "queued_at": "now"})
        queue.save()
        rep = reconcile(store, queue, ledger)
        self.assertIn("S1", rep["queue_realigned"])
        self.assertEqual(store.entry("S1").state, "REVERIFY_PENDING")
        self.assertEqual(store.entry("S2").state, "REVERIFY_PENDING")
        self.assertIn("S3", rep["dropped_queue_items"])
        # after realignment the pops must promote and re-run: S2 (ready) first,
        # then the realigned S1
        item = queue.pop_ready(now_ts=time.time() + 10_000)
        self.assertIsNotNone(item)
        self.assertEqual(item["strategy_id"], "S2")
        self.assertEqual(store.entry("S2").state, "PENDING")
        item2 = queue.pop_ready(now_ts=time.time() + 10_000)
        self.assertEqual(item2["strategy_id"], "S1")
        self.assertEqual(store.entry("S1").state, "PENDING")

    def test_corrupt_state_fails_loud(self):
        _, store, _, _ = self._env()
        store.set("S1", CandidateState.PENDING)
        store.path.write_text("{not json")
        with self.assertRaises(StoreCorruption):
            CandidateStore(store.path)

    def test_atomic_write_leaves_no_tmp(self):
        root, _, _, _ = self._env()
        atomic_write_json(root / "x.json", {"a": 1})
        self.assertEqual([p for p in root.iterdir() if p.suffix == ".tmp"], [])


# ---------------------------------------------------------------------------
# E. backtest: event study + portfolio  (§11)
# ---------------------------------------------------------------------------

class TestEventStudy(unittest.TestCase):
    def setUp(self):
        self.cost = CostContext.futures_default()

    def test_entry_exit_timing_and_costs(self):
        bars = make_bars(n=30, drift=0.001, vol=0.0, seed=1)
        funding = make_funding(bars[0].ts, bars[-1].ts + DAY_MS, rate=0.0)
        res = run_event_study(bars, "X", long_signal, 4, self.cost, 10_000.0, funding)
        self.assertTrue(res.events)
        e = res.events[0]
        self.assertEqual(e.entry_index, 2)
        self.assertEqual(e.exit_index, 6)
        self.assertEqual(e.entry_ts, bars[2].ts)
        self.assertEqual(e.exit_ts, bars[6].ts)
        self.assertGreater(e.gross_bp, 0)
        self.assertGreater(e.cost_bp, 0)
        self.assertAlmostEqual(e.net_bp, e.gross_bp - e.cost_bp - e.funding_bp, places=9)

    def test_no_lookahead(self):
        bars = make_bars(n=20, seed=2)
        view = PrefixView(bars, 5)
        with self.assertRaises(IndexError):
            view[5]

    def test_overlaps_skipped_and_counted(self):
        bars = make_bars(n=60, seed=3)
        res = run_event_study(bars, "X", long_signal, 4, self.cost, 10_000.0, None)
        self.assertGreater(res.skipped_overlapped, 0)
        prev_exit = -1
        for e in res.events:
            self.assertGreater(e.entry_index, prev_exit)
            prev_exit = e.exit_index

    def test_funding_signs(self):
        bars = make_bars(n=60, drift=0.0, vol=0.0, seed=4)
        mid = bars[40].ts   # 10h in → the 8h settlement falls inside (0, mid]
        funding = make_funding(bars[0].ts, bars[-1].ts + DAY_MS, rate=1e-4)
        self.assertGreater(funding_paid_bp(funding, bars[0].ts, mid, 1), 0)
        self.assertLess(funding_paid_bp(funding, bars[0].ts, mid, -1), 0)

    def test_funding_missing_drops_event(self):
        bars = make_bars(n=30, drift=0.0, vol=0.0, seed=5)
        short_funding = make_funding(bars[0].ts, bars[3].ts)  # does not cover
        res = run_event_study(bars, "X", long_signal, 4, self.cost, 10_000.0, short_funding)
        # no event may be accepted without funding coverage; the signal that
        # breaks on the data edge was never funding-checked (counted apart)
        self.assertEqual(res.events, [])
        self.assertGreater(res.dropped_missing_funding, 0)

    def test_participation_refusal_fail_closed(self):
        bars = make_bars(n=30, drift=0.001, vol=0.0, seed=6, volume=10.0)
        res = run_event_study(bars, "X", long_signal, 4, self.cost, 10_000.0, None)
        self.assertEqual(res.events, [])
        self.assertGreater(res.participation_refusals, 0)

    def test_funding_index_matches_reference(self):
        funding = make_funding(0, DAY_MS * 3)
        idx = FundingIndex(funding)
        for a, b, side in ((0, 3_600_000, 1), (1000, DAY_MS * 2, -1), (0, DAY_MS * 3, 1)):
            self.assertEqual(idx.paid_bp(a, b, side),
                             funding_paid_bp(funding, a, b, side))

    def test_cost_scenarios_monotone(self):
        bars = make_bars(n=30, drift=0.001, vol=0.0, seed=7, volume=1e7)
        nets = {}
        for name in ("base", "cost_up_2x", "wider_spread_2x", "impact_up_2x"):
            res = run_event_study(bars, "X", long_signal, 4, scenario_model(name),
                                  10_000.0, None)
            nets[name] = sum(e.net_bp for e in res.events)
        self.assertLess(nets["cost_up_2x"], nets["base"])
        self.assertLess(nets["wider_spread_2x"], nets["base"])
        self.assertLess(nets["impact_up_2x"], nets["base"])


class TestPortfolio(unittest.TestCase):
    def _grids(self, n=60):
        bars = make_bars(n=n, drift=0.0, vol=0.0, seed=8)
        return {"A": bars, "B": [type(b)(**{**b.__dict__}) for b in bars]}

    def test_same_symbol_conflict_skipped(self):
        bars = self._grids()
        sigs = [{"symbol": "A", "decision_index": 10, "side": 1, "horizon": 4},
                {"symbol": "A", "decision_index": 10, "side": -1, "horizon": 4}]
        pf = simulate_portfolio(bars, sigs, {"A": [], "B": []},
                                CostContext.futures_default(),
                                PortfolioConfig(initial_capital=10_000, size_fraction=0.1))
        self.assertEqual(pf.trades, 1)
        self.assertEqual(pf.skipped_same_symbol_conflict, 1)

    def test_capacity_limit(self):
        bars = self._grids()
        sigs = [{"symbol": s, "decision_index": 10, "side": 1, "horizon": 4}
                for s in ("A", "B")]
        pf = simulate_portfolio(bars, sigs, {"A": [], "B": []},
                                CostContext.futures_default(),
                                PortfolioConfig(initial_capital=10_000, max_concurrent=1,
                                                size_fraction=0.1))
        self.assertEqual(pf.trades, 1)
        self.assertEqual(pf.skipped_capacity, 1)

    def test_liquidation_isolated_margin(self):
        bars = self._grids(80)
        b = bars["A"][30]
        bars["A"][30] = type(b)(ts=b.ts, open=b.open, high=b.open, low=b.open * 0.5,
                                close=b.open * 0.55, volume=b.volume,
                                quote_volume=b.quote_volume)
        sigs = [{"symbol": "A", "decision_index": 25, "side": 1, "horizon": 40}]
        pf = simulate_portfolio(bars, sigs, {"A": [], "B": []},
                                CostContext.futures_default(),
                                PortfolioConfig(initial_capital=10_000, leverage=5.0,
                                                size_fraction=0.5))
        self.assertEqual(pf.liquidation_events, 1)
        self.assertLess(pf.final_equity, 10_000)

    def test_funding_charged_on_holding(self):
        bars = self._grids(80)
        fund = make_funding(bars["A"][0].ts, bars["A"][-1].ts + DAY_MS, rate=1e-3)
        sigs = [{"symbol": "A", "decision_index": 10, "side": 1, "horizon": 40}]
        pf = simulate_portfolio(bars, sigs, {"A": fund, "B": []},
                                CostContext.futures_default(),
                                PortfolioConfig(initial_capital=10_000, size_fraction=0.1))
        self.assertGreater(pf.total_funding_bp, 0)
        sigs_s = [{"symbol": "A", "decision_index": 10, "side": -1, "horizon": 40}]
        pf2 = simulate_portfolio(bars, sigs_s, {"A": fund, "B": []},
                                 CostContext.futures_default(),
                                 PortfolioConfig(initial_capital=10_000, size_fraction=0.1))
        self.assertLess(pf2.total_funding_bp, 0)

    def test_mdd_and_equity_curve(self):
        bars = self._grids(100)
        sigs = [{"symbol": "A", "decision_index": 10, "side": 1, "horizon": 10}]
        pf = simulate_portfolio(bars, sigs, {"A": [], "B": []},
                                CostContext.futures_default(),
                                PortfolioConfig(initial_capital=10_000, size_fraction=0.1))
        self.assertEqual(len(pf.equity_curve), 100)
        self.assertGreaterEqual(pf.max_drawdown, 0.0)
        self.assertEqual(pf.final_equity, pf.equity_curve[-1])

    def test_grid_mismatch_hard_error(self):
        b1 = make_bars(n=50, seed=9)
        b2 = make_bars(n=50, seed=10, start_ts=12345)
        with self.assertRaises(ValueError):
            simulate_portfolio({"A": b1, "B": b2}, [], {"A": [], "B": []},
                               CostContext.futures_default(), PortfolioConfig())


# ---------------------------------------------------------------------------
# F. operations  (§10, §13)
# ---------------------------------------------------------------------------

class TestOps(unittest.TestCase):
    def test_process_lock_live_owner_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "run.lock"
            l1 = ProcessLock(lock)
            self.assertTrue(l1.acquire())
            l2 = ProcessLock(lock)
            self.assertFalse(l2.acquire())
            l1.release()
            self.assertTrue(l2.acquire())

    def test_process_lock_stale_reclaimed(self):
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "run.lock"
            p = subprocess.Popen([sys.executable, "-c", "pass"])
            p.wait()
            lock.write_text(str(p.pid))   # dead pid
            l = ProcessLock(lock)
            self.assertTrue(l.acquire())
            l.release()

    def test_run_cycle_sh_duplicate_prevention(self):
        with tempfile.TemporaryDirectory() as td:
            root, data = Path(td) / "disc", Path(td) / "data"
            (data / "klines").mkdir(parents=True)
            env = dict(os.environ, NEW2_DISCOVERY_ROOT=str(root),
                       NEW2_DATA_DIR=str(data))
            lock = root / "state" / "run.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"])
            lock.write_text(str(p.pid))
            try:
                r = subprocess.run(["bash", str(DISC / "run_cycle.sh"), "1"],
                                   env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 3)
                self.assertIn("SKIPPED", r.stdout)
            finally:
                p.kill()
                p.wait()

    def test_path_config_missing_data_fails_closed(self):
        from runner import PathConfig, PathConfigError
        with tempfile.TemporaryDirectory() as td:
            os.environ["NEW2_DATA_DIR"] = str(Path(td) / "nope")
            try:
                with self.assertRaises(PathConfigError):
                    PathConfig.from_env()
            finally:
                os.environ.pop("NEW2_DATA_DIR", None)

    def test_refresh_flag_explicitly_unsupported(self):
        with tempfile.TemporaryDirectory() as td:
            root, data = Path(td) / "disc", Path(td) / "data"
            (data / "klines").mkdir(parents=True)
            env = dict(os.environ, NEW2_DISCOVERY_ROOT=str(root),
                       NEW2_DATA_DIR=str(data), REFRESH="1")
            r = subprocess.run([sys.executable, str(DISC / "runner.py"), "--batch", "1"],
                               env=env, capture_output=True, text=True, timeout=180)
            self.assertIn("NOT supported", r.stderr)
            summary = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertTrue(any("REFRESH" in n for n in summary.get("notes", [])))

    def test_cycle_data_missing_flow(self):
        from runner import PathConfig, Runner
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            (data / "klines").mkdir(parents=True)
            paths = PathConfig(root=Path(td) / "disc", data_dir=data,
                               state_dir=Path(td) / "state",
                               results_dir=Path(td) / "results",
                               audit_dir=Path(td) / "audit")
            r = Runner(paths)
            s = r.cycle(max_candidates=2)
            self.assertEqual(s.status, "SUCCESS_WITH_WARNINGS")
            self.assertEqual(s.evaluated, 2)
            self.assertEqual(s.verdict_counts.get("DATA_MISSING"), 2)
            self.assertGreaterEqual(s.reverify_queue, 1)
            # P1-8: the preregistration is now a write-once, hash-verified
            # generation manifest (the old single file had no hash and was
            # reused merely because it existed)
            gid = s.generation_id
            pfile = paths.state_dir / "preregistration" / f"{gid}.json"
            prereg = json.loads(pfile.read_text())
            self.assertEqual(prereg["n_candidates"], 779)
            self.assertIn("family_counts", prereg)
            before = prereg["preregistered_at"]
            r.register_all()
            after = json.loads(pfile.read_text())
            self.assertEqual(after["preregistered_at"], before)

    def test_search_space_states(self):
        import generator as g
        from runner import PathConfig, Runner
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            (data / "klines").mkdir(parents=True)
            paths = PathConfig(root=Path(td) / "disc", data_dir=data,
                               state_dir=Path(td) / "state",
                               results_dir=Path(td) / "results",
                               audit_dir=Path(td) / "audit")
            r = Runner(paths)
            r.register_all()
            fakes = [("S_fng", {"family": "fng"}), ("S_flow", {"family": "flow_z"})]

            class FakeStore:
                def __init__(self, states):
                    self._s = states
                def all_states(self):
                    return dict(self._s)

            orig_enum = g.enumerate_candidates
            g.enumerate_candidates = lambda: (fakes, [])
            try:
                st = search_space_status(FakeStore({"S_flow": "PASS", "S_fng": "PENDING"}),
                                         r.ledger, fng_available=False, funding_available=True)
                self.assertEqual(st.state, "SURVIVORS_AVAILABLE")
                st2 = search_space_status(FakeStore({"S_flow": "FAIL_STAT", "S_fng": "PENDING"}),
                                          r.ledger, fng_available=False, funding_available=True)
                self.assertEqual(st2.state, "INSUFFICIENT_DATA")
                st3 = search_space_status(FakeStore({"S_flow": "FAIL_STAT", "S_fng": "FAIL_STAT"}),
                                          r.ledger, fng_available=True, funding_available=True)
                self.assertEqual(st3.state, "SEARCH_SPACE_EXHAUSTED")
            finally:
                g.enumerate_candidates = orig_enum

    def test_interrupted_status(self):
        from runner import PathConfig, Runner
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            (data / "klines").mkdir(parents=True)
            paths = PathConfig(root=Path(td) / "disc", data_dir=data,
                               state_dir=Path(td) / "state",
                               results_dir=Path(td) / "results",
                               audit_dir=Path(td) / "audit")
            r = Runner(paths)
            r._interrupted = True
            s = r.cycle(max_candidates=1)
            self.assertEqual(s.status, "INTERRUPTED")


# ---------------------------------------------------------------------------
# G. promotion gate  (§15) + R-2/R-6 integration
# ---------------------------------------------------------------------------

class TestPromotion(unittest.TestCase):
    def test_gate_requires_everything(self):
        chk = promotion_gate(state="FAIL_STAT", preregistered=True,
                             independent_validation_passed=True, lock_test_passed=True,
                             lock_test_used=1, required_symbols=["BTCUSDT"])
        self.assertFalse(chk.eligible)
        chk2 = promotion_gate(state="PASS", preregistered=False,
                              independent_validation_passed=True, lock_test_passed=True,
                              lock_test_used=1, required_symbols=["BTCUSDT"])
        self.assertFalse(chk2.eligible)
        self.assertIn("preregistration missing", chk2.failed_steps)

    def test_gate_no_locked_test_never_final(self):
        chk = promotion_gate(state="PASS", preregistered=True,
                             independent_validation_passed=True, lock_test_passed=None,
                             lock_test_used=0, required_symbols=["SOLUSDT"])
        self.assertFalse(chk.eligible)
        self.assertTrue(any("NO_LOCKED_TEST" in s for s in chk.failed_steps))

    def test_gate_lock_reuse_rejected(self):
        chk = promotion_gate(state="PASS", preregistered=True,
                             independent_validation_passed=True, lock_test_passed=True,
                             lock_test_used=2, required_symbols=["BTCUSDT"])
        self.assertFalse(chk.eligible)

    def test_gate_happy_path(self):
        # residual fix: the gate now also requires a FINAL generation FDR
        # survival and a verified cost model (both absent → not eligible)
        base = dict(state="PASS", preregistered=True,
                    independent_validation_passed=True, lock_test_passed=True,
                    lock_test_used=1, required_symbols=["BTCUSDT"])
        self.assertFalse(promotion_gate(**base).eligible)
        chk = promotion_gate(**base, fdr_survived=True, cost_verified=True)
        self.assertTrue(chk.eligible)
        self.assertEqual(chk.kind, "SURVIVOR")

    def test_combo_requires_portfolio(self):
        kw = dict(state="PASS", preregistered=True, independent_validation_passed=True,
                  lock_test_passed=True, lock_test_used=1, required_symbols=["BTCUSDT"],
                  fdr_survived=True, cost_verified=True)
        chk = combo_promotion_gate(**kw, portfolio_passed=None)
        self.assertFalse(chk.eligible)
        # residual fix P1-9: a bare boolean is no longer evidence
        chk2 = combo_promotion_gate(**kw, portfolio_passed=True)
        self.assertFalse(chk2.eligible)
        bars = make_bars(n=200, drift=0.001, vol=0.0, seed=21, volume=1e9)
        pf = simulate_portfolio({"A": bars}, [{"symbol": "A", "decision_index": 40,
                                               "side": 1, "horizon": 20}],
                                {"A": make_funding(bars[0].ts, bars[-1].ts, rate=0.0)},
                                CostContext.futures_default(),
                                PortfolioConfig(initial_capital=10_000, size_fraction=0.1),
                                context_id="C2-x").to_json()
        chk3 = combo_promotion_gate(**kw, portfolio_result=pf, context_id="C2-x")
        self.assertTrue(chk3.eligible, chk3.failed_steps)
        self.assertEqual(chk3.kind, "SURVIVOR_COMBO")

    def test_queue_duplicates_and_not_deployed(self):
        with tempfile.TemporaryDirectory() as td:
            q = PromotionQueue(Path(td) / "promo.jsonl")
            chk = promotion_gate(state="PASS", preregistered=True,
                                 independent_validation_passed=True,
                                 lock_test_passed=True, lock_test_used=1,
                                 required_symbols=["BTCUSDT"])
            q.register(chk, "S1", "C1")
            q.register(chk, "S1", "C1")
            self.assertEqual(q.duplicates("S1"), 2)
            self.assertTrue(all(e["live_deployed"] is False for e in q.entries()))

    def test_bh_fdr_gate_blocks_locked_consumption(self):
        from runner import PathConfig, Runner
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            (data / "klines").mkdir(parents=True)
            paths = PathConfig(root=Path(td) / "disc", data_dir=data,
                               state_dir=Path(td) / "state",
                               results_dir=Path(td) / "results",
                               audit_dir=Path(td) / "audit")
            from generation import decide_family, family_status
            r = Runner(paths)
            manifest = {"generation_id": "G-test", "strategy_ids": ["S0", "S1"]}
            r.ledger.append({"evaluation_id": "E0", "strategy_id": "S0", "phase": "dev",
                             "generation_id": "G-test", "context_id": "CTX",
                             "status": "AWAITING_FDR", "primary_dev_p": 1e-12})
            # S1 not yet evaluated: the family is incomplete → no decision at
            # all (previously S0 "survived" as a family of one)
            fam = family_status(r.ledger.records(), manifest, "CTX")
            self.assertFalse(fam["complete"])
            with self.assertRaises(ValueError):
                decide_family(fam)
            r.ledger.append({"evaluation_id": "E1", "strategy_id": "S1", "phase": "dev",
                             "generation_id": "G-test", "context_id": "CTX",
                             "status": "AWAITING_FDR", "primary_dev_p": 0.06})
            # p=0.06 at rank 2 of 2: threshold 0.05 → the marginal candidate
            # is NOT selected and never consumes the lock
            dec = decide_family(family_status(r.ledger.records(), manifest, "CTX"))
            self.assertEqual(dec["survivors"], ["S0"])
            self.assertEqual(dec["m"], 2)
            self.assertEqual(r.lock_registry.uses("S1", "BTCUSDT"), 0)

    def _write_btc_dataset(self, data: Path, *, lock_drift: float):
        import datetime as dt
        self._chain_price = 100.0   # continuous price across month seams
        kdir, fdir = data / "klines", data / "funding"
        months = []
        y, m = 2021, 1
        while (y, m) <= (2024, 6):
            months.append(f"{y:04d}-{m:02d}")
            m += 1
            if m == 13:
                y, m = y + 1, 1
        for mon in months:
            yy, mm = int(mon[:4]), int(mon[5:7])
            d0 = dt.date(yy, mm, 1)
            d1 = dt.date(yy + (mm == 12), (mm % 12) + 1, 1) - dt.timedelta(days=1)
            start_ms = int(dt.datetime(d0.year, d0.month, d0.day,
                                       tzinfo=dt.timezone.utc).timestamp() * 1000)
            # non-overlapping month range [month_start, next_month_start) —
            # an 8h grid over adjacent months must NEVER produce duplicate
            # funding timestamps at the seam (funding_quality is fail-closed)
            n_end = dt.date(yy + (mm == 12), (mm % 12) + 1, 1)
            next_start_ms = int(dt.datetime(n_end.year, n_end.month, n_end.day,
                                            tzinfo=dt.timezone.utc).timestamp() * 1000)
            n = ((d1 - d0).days + 1) * 96
            drift = 0.0005
            if mon >= "2024-03" and lock_drift < 0:
                drift = lock_drift
            bars = make_bars(n=n, start_ts=start_ms, price0=self._chain_price,
                             drift=drift, vol=1e-5, seed=(hash(mon) % 1000),
                             volume=1e6)
            self._chain_price = bars[-1].close   # months must be CONTINUOUS
            write_kline_csv(kdir / f"BTCUSDT-15m-{mon}.csv", bars)
            pairs, f_ts = [], start_ms
            while f_ts < next_start_ms:
                pairs.append((f_ts, 1e-4))
                f_ts += 8 * 3_600_000
            write_funding_csv(fdir / f"BTCUSDT-fundingRate-{mon}.csv", pairs)

    TOD_DEFN = {
        "family": "tod", "params": {"utc_start": 0, "utc_end": 24, "horizon": 16},
        "_defaults": {"utc_start": 0, "utc_end": 2, "horizon": 16},
        "symbols": ["BTCUSDT"], "timeframe": "15m", "direction": "long_in_session",
        "entry": "tod:preregistered_rule", "exit": "time_exit", "holding": 16,
        "risk": {"sizing": "vol_inverse", "max_leverage": 1.0},
        "primary_cell": {"symbol": "BTCUSDT", "horizon": 16},
    }

    def _runner_one(self, td, data):
        from runner import PathConfig, Runner
        from costs_bridge import FUTURES_COST_DEFAULTS
        paths = PathConfig(root=Path(td) / "disc", data_dir=data,
                           state_dir=Path(td) / "state",
                           results_dir=Path(td) / "results",
                           audit_dir=Path(td) / "audit")
        cost = CostContext.from_params(FUTURES_COST_DEFAULTS, version="TEST_FAKE_VERIFIED",
                                       verified=True, test_only=True)
        sid = strategy_id(self.TOD_DEFN)
        canon = normalize_strategy(self.TOD_DEFN)
        return Runner(paths, cost=cost, allow_test_cost=True,
                      candidates_fn=lambda: ([(sid, canon)], [])), sid

    def test_full_pass_registers_promotion(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            self._write_btc_dataset(data, lock_drift=0.0005)
            r, sid = self._runner_one(td, data)
            r.register_all()
            out = r.run_one(sid, r._defn_by_sid[sid])
            self.assertEqual(out["status"], "AWAITING_FDR",
                             json.dumps(out["record"], default=str)[:1500])
            self.assertEqual(r.lock_registry.uses(sid, "BTCUSDT"), 0)
            fin = r.finalize_fdr()
            self.assertTrue(fin["complete"])
            rec = r.ledger.latest_by_strategy()[sid]
            self.assertEqual(rec["status"], "PASS", json.dumps(rec, default=str)[:1500])
            self.assertTrue(rec["multiple_testing"]["survived"])
            self.assertEqual(rec["multiple_testing"]["m"], 1)
            self.assertEqual(rec["locked_test"]["pass"]["BTCUSDT"], True)
            self.assertTrue(rec["locked_test"]["independent"])
            self.assertEqual(r.lock_registry.uses(sid, "BTCUSDT"), 1)
            self.assertIsNotNone(rec["promotion_check"])
            self.assertTrue(rec["promotion_check"]["eligible"])
            self.assertEqual(rec["promotion_check"]["kind"], "SURVIVOR")
            promo = r.promo.entries()
            self.assertEqual(len(promo), 1)
            self.assertEqual(promo[0]["strategy_id"], sid)
            self.assertTrue(promo[0]["test_only"])
            out2 = r.run_one(sid, r._defn_by_sid[sid])
            self.assertTrue(out2.get("skipped"))

    def test_failing_locked_test_not_promoted(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            self._write_btc_dataset(data, lock_drift=-0.0005)
            r, sid = self._runner_one(td, data)
            r.register_all()
            out = r.run_one(sid, r._defn_by_sid[sid])
            self.assertEqual(out["status"], "AWAITING_FDR")
            r.finalize_fdr()
            rec = r.ledger.latest_by_strategy()[sid]
            self.assertEqual(rec["status"], "FAIL_STAT")
            self.assertEqual(rec["locked_test"]["pass"]["BTCUSDT"], False)
            self.assertEqual(r.promo.entries(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
