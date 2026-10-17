"""Re-audit 2026-10-17 (ADR 0010): the legacy locked TEST keeps the cost gate of
ADR 0009, a multi-symbol candidate whose locked window is covered by another
symbol's selection data never enters the locked TEST (legacy and evolution),
the cycle mode is explicit and its state is isolated, and causality checks
for funding and the portfolio equity curve.

SYNTHETIC data and temp dirs only.  Nothing here is market data or evidence
about real costs or profitability.
"""
from __future__ import annotations

import io
import os
import random
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from costs_bridge import CostContext, FUTURES_COST_DEFAULTS  # noqa: E402
from data_quality import Bar  # noqa: E402
from test_pipeline import EDGE, NOISE, MS15, PipelineCase, SyntheticProvider  # noqa: E402
from test_residual_fixes import (  # noqa: E402
    EXPECTED_SURVIVORS, LOCK_DEFN, LOCK_SID, SPACE, SIDS, _fake_runner_cls, _write_lock_window,
    make_paths, test_verified_cost,
)

BOTH = ["BTCUSDT", "ETHUSDT"]


def tmpdir(case) -> Path:
    td = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, td, True)
    return td


def _no_lock_activity(case, r, sids):
    for sid in sids:
        for sym in BOTH:
            case.assertEqual(r.lock_registry.attempts(sid, sym), [])
            case.assertEqual(r.lock_registry.uses(sid, sym), 0)
    case.assertEqual(r.campaigns.records(), [])


def _finals(r):
    return {x["strategy_id"]: x for x in r.ledger.records() if x.get("phase") == "final"}


# ---------------------------------------------------------------------------
# cross-symbol independence of the locked window
# ---------------------------------------------------------------------------
class TestIndependenceRule(unittest.TestCase):
    def test_eth_selection_covers_the_btc_locked_window(self):
        from runner import locked_independence_issues, selection_read_end
        issues = locked_independence_issues(BOTH)
        self.assertEqual(len(issues), 1)
        self.assertIn("ETHUSDT selection data", issues[0])
        self.assertIn("BTCUSDT locked window [2024-03-25,2024-06-18]", issues[0])
        self.assertEqual(locked_independence_issues(["ETHUSDT", "BTCUSDT"]), issues)  # order-free
        # BTC selection ends the day before its own window: it never covers ETH's
        self.assertEqual(selection_read_end("BTCUSDT"), "2024-03-24")
        self.assertEqual(selection_read_end("ETHUSDT"), "2026-04-27")

    def test_single_symbol_is_independent(self):
        from runner import locked_independence_issues
        for syms in (["BTCUSDT"], ["ETHUSDT"], ["BTCUSDT", "BTCUSDT"]):
            self.assertEqual(locked_independence_issues(syms), [])


# ---------------------------------------------------------------------------
# legacy runner: cost gate (ADR 0009 parity) and independence gate
# ---------------------------------------------------------------------------
class TestLegacyLockedGates(unittest.TestCase):
    def test_unverified_cost_never_reserves_or_reads_a_window(self):
        r = _fake_runner_cls()(make_paths(tmpdir(self)), candidates_fn=lambda: (SPACE, []))
        self.assertFalse(r.cost.verified)                      # production default
        for _ in range(3):                                     # idempotent across cycles
            s = r.cycle(max_candidates=6)
        dec = s.fdr_family["decision"]
        self.assertEqual(dec["statistical_survivors"], EXPECTED_SURVIVORS)
        self.assertEqual(dec["locked_test_eligible"], [])
        self.assertEqual(dec["lock_blocked"], {sid: "COST_NOT_VERIFIED" for sid in EXPECTED_SURVIVORS})
        _no_lock_activity(self, r, SIDS)
        fin = _finals(r)
        for sid in EXPECTED_SURVIVORS:                         # survivors wait, no verdict
            self.assertNotIn(sid, fin)
            self.assertEqual(r.store.entry(sid).state, "AWAITING_FDR")
        self.assertEqual(fin[SIDS[3]]["status"], "FAIL_STAT")  # dev-pass non-survivor still resolves

    def test_out_of_scope_verified_cost_is_blocked_too(self):
        r = _fake_runner_cls(open_lock_gates=True)(make_paths(tmpdir(self)),
                                                    candidates_fn=lambda: (SPACE, []))
        with mock.patch.object(CostContext, "verified_for",
                               return_value=(False, ["ETHUSDT not in verified symbols"])):
            s = r.cycle(max_candidates=6)
        self.assertEqual(set(s.fdr_family["decision"]["lock_blocked"].values()), {"COST_NOT_VERIFIED"})
        _no_lock_activity(self, r, SIDS)

    def test_locked_test_refuses_unverified_cost_directly(self):
        from runner import Runner
        paths = make_paths(tmpdir(self))
        _write_lock_window(paths.data_dir)
        r = Runner(paths, candidates_fn=lambda: ([(LOCK_SID, LOCK_DEFN)], []))
        r._ctx = "CTX-test"
        self.assertEqual(r.lock_entry_status(LOCK_SID, LOCK_DEFN)["reason"], "COST_NOT_VERIFIED")
        self.assertIsNone(r._locked_test(LOCK_SID, LOCK_DEFN, "CTX-test"))
        self.assertEqual(r.lock_registry.attempts(LOCK_SID, "BTCUSDT"), [])

    def test_btc_eth_candidate_is_not_independent(self):
        cls = _fake_runner_cls(open_lock_gates=True)

        class Gated(cls):                                       # cost open, real overlap rule
            from runner import locked_independence_issues as _ind
            _lock_independence = staticmethod(_ind)

        r = Gated(make_paths(tmpdir(self)), candidates_fn=lambda: (SPACE, []))
        self.assertTrue(r.cost.verified)
        for _ in range(2):
            r.cycle(max_candidates=6)
        _no_lock_activity(self, r, SIDS)
        fin = _finals(r)
        for sid in EXPECTED_SURVIVORS:
            self.assertEqual(fin[sid]["status"], "DATA_MISSING")
            self.assertTrue(fin[sid]["locked_test"]["note"].startswith("LOCKED_WINDOW_NOT_INDEPENDENT"))
            self.assertFalse(fin[sid]["locked_test"]["consumed"])
            self.assertIsNone(fin[sid]["promotion_check"])
            self.assertEqual(r.store.entry(sid).state, "DATA_MISSING")   # not re-queued
            self.assertNotIn(sid, [i["strategy_id"] for i in r.queue._items])

    def test_single_symbol_verified_candidate_still_enters(self):
        from runner import Runner
        paths = make_paths(tmpdir(self))
        _write_lock_window(paths.data_dir)
        r = Runner(paths, candidates_fn=lambda: ([(LOCK_SID, LOCK_DEFN)], []),
                   cost=test_verified_cost(), allow_test_cost=True)
        self.assertEqual(r.lock_entry_status(LOCK_SID, LOCK_DEFN)["reason"], "AVAILABLE")


# ---------------------------------------------------------------------------
# evolution pipeline: independence gate
# ---------------------------------------------------------------------------
class TestEvolutionIndependenceGate(PipelineCase):
    def test_multi_symbol_survivor_is_blocked_before_any_window_lookup(self):
        prov = SyntheticProvider()
        p = self.pipe(prov)
        ctx = p.context_id()
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        self.assertEqual(p.statuses.status(edge, ctx), "VALIDATION_SURVIVOR")
        real_get = p.specs.get
        with mock.patch.object(p.specs, "get",
                               side_effect=lambda s: {**real_get(s), "symbols": BOTH}), \
                mock.patch.object(p.lock_registry, "entry_status",
                                  side_effect=AssertionError("window looked up")):
            self.assertIsNone(p.run_locked_campaign())
            self.assertIsNone(p.run_locked_campaign())
        rec = p.statuses.get(edge, ctx)
        self.assertEqual((rec["status"], rec["stage"]), ("BLOCKED_STATISTICS", "locked"))
        self.assertIn("LOCKED_WINDOW_NOT_INDEPENDENT", rec["reasons"][0])
        self.assertEqual(prov.locked_loads, 0)
        self.assertEqual(p.lock_registry.uses(edge, "BTCUSDT"), 0)
        self.assertEqual(p.campaigns.records(), [])

    def test_unverified_cost_is_reported_before_independence(self):
        p = self.pipe(SyntheticProvider())
        ctx = p.context_id()
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        real_get = p.specs.get
        with mock.patch.object(p.specs, "get", side_effect=lambda s: {**real_get(s), "symbols": BOTH}), \
                mock.patch.object(CostContext, "verified_for", return_value=(False, ["x"])):
            p.run_locked_campaign()
        self.assertEqual(p.statuses.status(edge, ctx), "BLOCKED_COST")


# ---------------------------------------------------------------------------
# cycle mode: explicit default, dispatch and state isolation
# ---------------------------------------------------------------------------
class TestModeDispatch(unittest.TestCase):
    def _main(self, td, *args):
        import runner
        (td / "data" / "klines").mkdir(parents=True, exist_ok=True)
        env = {"NEW2_DISCOVERY_ROOT": str(td / "disc"), "NEW2_DATA_DIR": str(td / "data")}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), redirect_stdout(out), redirect_stderr(err):
            for k in ("NEW2_STATE_DIR", "NEW2_RESULTS_DIR", "NEW2_COST_PROFILE", "REFRESH"):
                os.environ.pop(k, None)
            rc = runner.main(list(args))
        return rc, out.getvalue(), err.getvalue(), td / "disc"

    def test_default_is_legacy_and_touches_only_legacy_state(self):
        rc, out, err, root = self._main(tmpdir(self), "--batch", "2")
        self.assertIn("RUN_MODE legacy", err)
        self.assertTrue((root / "state" / "state.json").exists())
        self.assertTrue((root / "results" / "cycle_summary.json").exists())
        self.assertFalse((root / "state" / "evolution").exists())
        self.assertFalse((root / "results" / "evolution_cycle_summary.json").exists())

    def test_evolve_touches_only_evolution_state(self):
        rc, out, err, root = self._main(tmpdir(self), "--mode", "evolve")
        self.assertEqual(rc, 4)                                 # no market data: BLOCKED_DATA
        self.assertIn("RUN_MODE evolve", err)
        self.assertTrue((root / "results" / "evolution_cycle_summary.json").exists())
        for legacy in ("state/state.json", "state/discovery_ledger.jsonl", "state/queue.json",
                       "results/cycle_summary.json"):
            self.assertFalse((root / legacy).exists(), legacy)

    def test_help_names_the_default(self):
        rc, out, _, _ = self._main(tmpdir(self), "--help")
        self.assertEqual(rc, 0)
        self.assertIn("--mode legacy  (default)", out)


class TestRunCycleMode(unittest.TestCase):
    def _run(self, mode=None):
        td = tmpdir(self)
        fake = td / "fake_py.sh"
        fake.write_text('#!/bin/sh\necho "runner-out args: $*"\nexit 0\n')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        env = dict(os.environ, NEW2_DISCOVERY_ROOT=str(td / "disc"), PYTHON=str(fake))
        env.pop("NEW2_DISC_MODE", None)
        if mode:
            env["NEW2_DISC_MODE"] = mode
        return subprocess.run(["bash", str(DISC / "run_cycle.sh"), "2"], env=env, cwd=str(td),
                              capture_output=True, text=True, timeout=60), td

    def test_unset_mode_warns_and_says_legacy(self):
        r, td = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CYCLE_WARNING NEW2_DISC_MODE not set", r.stderr)
        self.assertIn("CYCLE_OK batch=2 mode=legacy", r.stdout)
        self.assertIn("--mode legacy", (td / "disc" / "cycle_log.txt").read_text())
        self.assertIn("event=RUN mode=legacy", (td / "disc" / "cycle_audit.log").read_text())

    def test_explicit_mode_is_passed_and_not_warned(self):
        r, td = self._run("evolve")
        self.assertNotIn("CYCLE_WARNING", r.stderr)
        self.assertIn("CYCLE_OK batch=2 mode=evolve", r.stdout)
        self.assertIn("--mode evolve", (td / "disc" / "cycle_log.txt").read_text())


# ---------------------------------------------------------------------------
# causality: future bars / funding never change past results
# ---------------------------------------------------------------------------
def walk(n, seed, qv=1e9):
    rnd = random.Random(seed)
    px, out = 100.0, []
    for i in range(n):
        o = px
        px *= 1 + rnd.gauss(0, 0.003)
        out.append(Bar(ts=i * MS15, open=o, high=max(o, px) * 1.001, low=min(o, px) * 0.999,
                       close=px, volume=1.0, quote_volume=qv))
    return out


def perturb_from(bars, k):
    out = list(bars[:k])
    for b in bars[k:]:
        out.append(Bar(ts=b.ts, open=b.open * 1.3, high=b.high * 1.5, low=b.low * 0.6,
                       close=b.close * 0.7, volume=b.volume * 50, quote_volume=b.quote_volume / 1e4))
    return out


def funding(n, rate):
    return [(i * MS15, rate) for i in range(0, n, 32)]


class TestFutureDataCausality(unittest.TestCase):
    N, K, H = 400, 250, 8
    cost = CostContext.from_params({**FUTURES_COST_DEFAULTS, "impact_coefficient": 0.1,
                                    "max_participation": 0.5}, version="t")

    @staticmethod
    def sig(v):
        return 1 if v[-1].close > v[-2].close else -1

    def _events(self, bars, fund):
        from evaluator import run_event_study
        r = run_event_study(bars, "BTCUSDT", self.sig, self.H, self.cost, 10_000.0, fund, warmup=2)
        return [e for e in r.events if e.exit_index < self.K]

    def test_event_costs_and_funding_ignore_the_future(self):
        bars = walk(self.N, 1)
        a = self._events(bars, funding(self.N, 0.0001))
        fut_fund = [(t, r if t < self.K * MS15 else 0.05) for t, r in funding(self.N, 0.0001)]
        b = self._events(perturb_from(bars, self.K), fut_fund)
        self.assertGreater(len(a), 5)
        self.assertEqual([(e.entry_index, e.exit_index, e.side) for e in a],
                         [(e.entry_index, e.exit_index, e.side) for e in b])
        for x, y in zip(a, b):
            self.assertEqual((x.gross_bp, x.cost_bp, x.funding_bp, x.net_bp),
                             (y.gross_bp, y.cost_bp, y.funding_bp, y.net_bp))

    def test_funding_inside_the_holding_window_is_charged(self):
        bars = walk(self.N, 1)
        a = self._events(bars, funding(self.N, 0.0001))
        b = self._events(bars, funding(self.N, 0.0005))
        self.assertTrue(any(x.funding_bp != y.funding_bp for x, y in zip(a, b)))

    def test_portfolio_equity_curve_before_k_ignores_the_future(self):
        from portfolio import PortfolioConfig, simulate_portfolio
        bars = {"BTCUSDT": walk(self.N, 2), "ETHUSDT": walk(self.N, 3)}
        sigs = [{"symbol": s, "decision_index": i, "side": 1 if i % 40 else -1, "horizon": self.H}
                for s in bars for i in range(20, self.N - 20, 17)]
        fund = {s: funding(self.N, 0.0001) for s in bars}
        cfg = PortfolioConfig(leverage=3.0)
        a = simulate_portfolio(bars, sigs, fund, self.cost, cfg)
        shocked = {"BTCUSDT": bars["BTCUSDT"], "ETHUSDT": perturb_from(bars["ETHUSDT"], self.K)}
        b = simulate_portfolio(shocked, sigs, fund, self.cost, cfg)
        self.assertEqual(a.equity_curve[:self.K], b.equity_curve[:self.K])
        self.assertNotEqual(a.equity_curve[self.K:], b.equity_curve[self.K:])


if __name__ == "__main__":
    unittest.main()
