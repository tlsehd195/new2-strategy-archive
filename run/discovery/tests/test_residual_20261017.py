"""Residual audit 2026-10-17 (ADR 0008): public cost estimate hardening and
offline re-verification, cost-calibration evidence rules, look-ahead-free
cost pricing, conservative liquidation on intrabar exits, forward-paper plan
integrity / crash recovery / idempotent conclusion, bounded-loop exit code.

SYNTHETIC data and temp dirs only.  Nothing here is market data or evidence
about real costs or profitability.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import cost_calibration as cc  # noqa: E402
import functools  # noqa: E402
#: these synthetic processes stand for a REAL account's fills (ADR 0063 makes the
#: account kind explicit; demo / undeclared accounts are tested separately)
_cav = functools.partial(cc.calibrate_and_verify, account_kind="real")
import public_cost_estimate as pce  # noqa: E402
import public_market_collect as pmc  # noqa: E402
from costs_bridge import CostConfigError, CostContext, FUTURES_COST_DEFAULTS  # noqa: E402
from data_quality import Bar  # noqa: E402
from engine_lib import iter_jsonl  # noqa: E402
from test_completion import ForwardCase, synth_fills  # noqa: E402
from test_pipeline import MS15  # noqa: E402
from test_public_estimate import synth_book, synth_lines, write_dir  # noqa: E402

SYMS = ["BTCUSDT", "ETHUSDT"]


def tmpdir(case) -> Path:
    td = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, td, True)
    return td


# ---------------------------------------------------------------------------
# public cost estimate
# ---------------------------------------------------------------------------

class TestEstimateRefusals(unittest.TestCase):
    def setUp(self):
        self.td = tmpdir(self)

    def _clean(self, **kw):
        return {s: synth_lines(s, hours=80, seed=i + 1, **kw) for i, s in enumerate(SYMS)}

    def test_bad_snapshots_are_invalid_and_counted(self):
        data = self._clean()
        bt = data["BTCUSDT"]
        bt[10]["depth"]["bids"][0][0] = "NaN"                  # non-finite price
        bt[11]["depth"]["E"] = bt[11]["fetched_at_ms"] - 60_000   # book a minute old
        bt[12]["kline_closed"]["close_time"] = bt[12]["fetched_at_ms"] - 3_600_000   # stale kline
        bt[13]["kline_closed"]["close_time"] = bt[13]["fetched_at_ms"] + 1           # unfinished bar
        bt[14]["symbol"] = "ETHUSDT"                            # written to the wrong folder
        bt.append(dict(bt[20]))                                 # duplicate time
        del bt[15]["depth"]["E"]
        rec = pce.estimate(write_dir(self.td, data))
        why = rec["per_symbol"]["BTCUSDT"]["invalid_reasons"]
        self.assertEqual(why, {"non-finite price, size or volume": 1, "stale book": 1,
                               "stale or unfinished kline": 2, "symbol does not match its folder": 1,
                               "duplicate snapshot time": 1, "book without event time": 1})
        self.assertEqual(rec["status"], pce.ESTIMATED, rec["reasons"])   # 7 of 4800 < 1 %

    def test_manifest_required_and_hashes_checked(self):
        rec = pce.estimate(write_dir(self.td / "a", self._clean(), manifest=False))
        self.assertEqual(rec["status"], pce.INSUFFICIENT)
        self.assertTrue(any("collect_manifest.json missing" in r for r in rec["reasons"]))
        d = write_dir(self.td / "b", self._clean())
        f = d / "ETHUSDT" / "public_20261016.jsonl"
        f.write_bytes(f.read_bytes().replace(b'"OK"', b'"OK" ', 1))    # one byte changed in transit
        rec = pce.estimate(d)
        self.assertEqual(rec["status"], pce.INSUFFICIENT)
        self.assertIn("input integrity: ETHUSDT/public_20261016.jsonl: SHA-256 differs from "
                      "collect_manifest.json", rec["reasons"])
        extra = d / "BTCUSDT" / "public_20261017.jsonl"
        extra.write_text("")
        pmc.write_manifest(d)                                    # re-sealed: consistent again
        extra.unlink()
        rec = pce.estimate(d)
        self.assertTrue(any("listed in collect_manifest.json but missing" in r for r in rec["reasons"]))

    def test_manifest_only_cli_reseals_without_network(self):
        d = write_dir(self.td, self._clean(), manifest=False)
        with mock.patch("public_market_collect.fetch_with_retry", side_effect=AssertionError("network")):
            self.assertEqual(pmc.main(["--manifest-only", "--out", str(d)]), 0)
        self.assertEqual(pce.estimate(d)["status"], pce.ESTIMATED)

    def test_thin_notional_is_excluded_as_a_whole_not_cherry_picked(self):
        # 10 levels x 200k = 2M visible per side: 1M fits.  In 5 % of the snapshots
        # the book is only 600k deep, so the 1M walk runs out of depth there.
        data = self._clean(levels=10)
        for s in SYMS:
            for x in data[s][::20]:
                x["depth"]["bids"], x["depth"]["asks"] = x["depth"]["bids"][:3], x["depth"]["asks"][:3]
        rec = pce.estimate(write_dir(self.td, data))
        bt = rec["per_symbol"]["BTCUSDT"]
        self.assertEqual(bt["unsupported_notionals"], ["1000000"])
        self.assertAlmostEqual(bt["thin_share_by_notional"]["1000000"], 0.05, places=3)
        self.assertEqual(rec["status"], pce.ESTIMATED, rec["reasons"])      # 100k still supports the fit
        self.assertEqual(rec["supported_max_notional"], 100_000.0)
        n100 = bt["cost_per_side_bp_by_notional"]["100000"]["n"]
        self.assertEqual(bt["impact_fit_n"], n100)                          # only the 100k walks

    def test_thin_at_every_fit_notional_is_insufficient(self):
        data = self._clean(level_notional=1_000.0, levels=50)                  # 50k visible
        rec = pce.estimate(write_dir(self.td, data))
        self.assertEqual(rec["status"], pce.INSUFFICIENT)
        self.assertTrue(any("book too thin" in r for r in rec["reasons"]))

    def test_worse_side_is_priced(self):
        data = self._clean()
        for s in SYMS:
            for x in data[s]:
                mid = (float(x["depth"]["bids"][0][0]) + float(x["depth"]["asks"][0][0])) / 2
                bids, _ = synth_book(mid, level_notional=50_000.0)           # bid side 4x thinner
                x["depth"]["bids"] = bids
        rec = pce.estimate(write_dir(self.td, data))
        c = rec["per_symbol"]["BTCUSDT"]["cost_per_side_bp_by_notional"]["1000000"]
        self.assertGreater(c["sell_median"], c["buy_median"])
        self.assertAlmostEqual(c["median"], c["sell_median"], places=9)
        self.assertGreater(rec["per_symbol"]["BTCUSDT"]["buy_minus_sell_median_bp"]["1000000"], -1e9)
        sym = pce.estimate(write_dir(self.td / "sym", self._clean()))
        self.assertGreater(rec["params"]["impact_coefficient"], sym["params"]["impact_coefficient"])

    def test_superseded_criteria_refused(self):
        with self.assertRaises(pce.EstimateError) as e:
            pce.load_criteria(DISC / "config" / "cost_public_estimate_v1.json")
        self.assertIn("superseded", str(e.exception))
        bad = json.loads((DISC / "config" / "cost_public_estimate_v2.json").read_text())
        bad["side_aggregation"] = "mean"
        p = self.td / "c.json"
        p.write_text(json.dumps(bad))
        with self.assertRaises(pce.EstimateError):
            pce.load_criteria(p)


class TestEstimateVerification(unittest.TestCase):
    def setUp(self):
        self.td = tmpdir(self)
        self.d = write_dir(self.td / "pm", {s: synth_lines(s, hours=80, seed=i + 1)
                                            for i, s in enumerate(SYMS)})
        self.rec = json.loads(json.dumps(pce.estimate(self.d)))

    def test_reproduces_and_detects_changes(self):
        r = pce.verify_estimate(self.rec, self.d)
        self.assertTrue(r["ok"], r["problems"])
        f = self.d / "BTCUSDT" / "public_20261016.jsonl"
        lines = f.read_text().splitlines()
        f.write_text("\n".join(lines[:-500]) + "\n")
        r = pce.verify_estimate(self.rec, self.d)
        self.assertFalse(r["ok"])
        self.assertIn("BTCUSDT/public_20261016.jsonl: source file changed (SHA-256)", r["problems"])

    def test_tampered_or_failed_records(self):
        bad = dict(self.rec, params=dict(self.rec["params"], half_spread=0.0))
        self.assertFalse(pce.verify_estimate(bad, self.d)["ok"])
        forged = dict(self.rec, reasons=["x"])
        forged["record_id"] = pce.record_hash(forged)
        with self.assertRaises(pce.EstimateError):
            pce.check_estimate(forged)
        forged = json.loads(json.dumps(self.rec))
        forged["fee_basis"]["observed"] = True                   # "we saw the fees"
        forged["record_id"] = pce.record_hash(forged)
        with self.assertRaises(pce.EstimateError):
            pce.check_estimate(forged)
        with self.assertRaises(CostConfigError):
            CostContext.from_public_estimate(forged)

    def test_cli_verify(self):
        out = self.td / "e.json"
        out.write_text(json.dumps(self.rec))
        r = subprocess.run([sys.executable, str(DISC / "public_cost_estimate.py"), "--verify", str(out),
                            "--in", str(self.d)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (self.d / "ETHUSDT" / "public_20261016.jsonl").write_text("")
        r = subprocess.run([sys.executable, str(DISC / "public_cost_estimate.py"), "--verify", str(out),
                            "--in", str(self.d)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


# ---------------------------------------------------------------------------
# cost calibration evidence rules
# ---------------------------------------------------------------------------

class TestCalibrationEvidence(unittest.TestCase):
    def setUp(self):
        self.td = tmpdir(self)

    def test_virtual_or_public_rows_cannot_carry_fills(self):
        for kind in ("paper_fill_monitor", "quote_snapshots"):
            o = cc.Observation(ts_ms=1, symbol="BTCUSDT", side=1, order_type="maker", notional=1.0,
                               ref_price=100.0, status="FILLED", source_kind=kind,
                               fill_price=100.0, filled_notional=1.0, fee=0.0)
            with self.subTest(kind=kind), self.assertRaises(cc.CalibrationError):
                o.validate()

    def test_bad_values_and_duplicates_refused(self):
        good = synth_fills(n=3)[0]
        from dataclasses import asdict, replace
        for bad in (replace(good, bar_traded_value=math.nan), replace(good, ts_ms=1.5),
                    replace(good, order_type="market"), replace(good, symbol=""),
                    replace(good, fee=math.inf)):
            with self.subTest(bad=bad), self.assertRaises(cc.CalibrationError):
                bad.validate()
        p = self.td / "obs.jsonl"
        row = json.dumps(asdict(good))
        p.write_text(row + "\n" + row + "\n")
        with self.assertRaises(cc.CalibrationError) as e:
            cc.load_observations(p)
        self.assertIn("duplicate", str(e.exception))

    def test_scope_and_rates_come_from_real_fills_only(self):
        fills = synth_fills(n=600, symbols=("BTCUSDT",))
        # one stray ETH fill and thousands of quote snapshots must not widen the scope
        stray = synth_fills(n=1, symbols=("ETHUSDT",), seed=9)[0]
        from dataclasses import replace
        stray = replace(stray, ts_ms=fills[-1].ts_ms + 1)
        quotes = [cc.Observation(ts_ms=1_600_000_000_000 + i, symbol="SOLUSDT", side=1, order_type="maker",
                                 notional=1.0, ref_price=100.0, status="EXPIRED",
                                 source_kind="quote_snapshots", bid=99.99, ask=100.01) for i in range(2000)]
        rec = _cav(fills + [stray] + quotes, CostContext.futures_default(),
                                      required_symbols=["BTCUSDT"], order_value=10_000.0)
        self.assertEqual(rec["status"], "VERIFIED", rec["reasons"])
        self.assertEqual(rec["scope"]["symbols"], ["BTCUSDT"])
        self.assertGreaterEqual(rec["scope"]["notional_range"][0], 5_000.0)    # not the 1.0 quotes
        self.assertEqual(rec["n_orders"], 601)
        self.assertEqual(rec["fill_rates"]["EXPIRED"], 0.0)
        ctx = CostContext.from_verification(cc.seal(rec))
        self.assertFalse(ctx.verified_for(["ETHUSDT"], 10_000.0)[0])
        self.assertFalse(ctx.verified_for(["SOLUSDT"], 10_000.0)[0])
        self.assertFalse(ctx.verified_for(["BTCUSDT"], 1.0)[0])

    def test_rehashed_record_with_reasons_is_not_verified(self):
        rec = _cav(synth_fills(), CostContext.futures_default(),
                                      required_symbols=["BTCUSDT"], order_value=10_000.0)
        forged = cc.seal({**rec, "reasons": ["p90 underestimate"]})
        with self.assertRaises(cc.CalibrationError):
            cc.check_record(forged)
        with self.assertRaises(CostConfigError):
            CostContext.from_verification(forged)


# ---------------------------------------------------------------------------
# look-ahead-free cost pricing and execution rules
# ---------------------------------------------------------------------------

def flat_bars(n, qv=1e9):
    return [Bar(ts=i * MS15, open=100.0, high=100.5, low=99.5, close=100.0, volume=1.0, quote_volume=qv)
            for i in range(n)]


class TestNoLookaheadPricing(unittest.TestCase):
    cost = CostContext.from_params({**FUTURES_COST_DEFAULTS, "impact_coefficient": 0.1,
                                    "max_participation": 0.05}, version="t")

    def run_one(self, bars):
        from evaluator import run_event_study
        sig = lambda v: 1 if len(v) == 5 else 0      # noqa: E731 — decide on bar 4, enter at 5
        return run_event_study(bars, "BTCUSDT", sig, 3, self.cost, 10_000.0)

    def test_fill_bar_volume_is_not_used(self):
        bars = flat_bars(20)
        # the ENTRY bar (5) itself traded almost nothing: unknown when the order
        # is sent at its open, so it neither refuses nor prices the entry
        bars[5] = Bar(ts=5 * MS15, open=100, high=100.5, low=99.5, close=100, volume=1, quote_volume=1.0)
        r = self.run_one(bars)
        self.assertEqual((r.participation_refusals, len(r.events)), (0, 1))
        # the bar before the exit open (7) traded 1000 USDT: the exit is refused
        bars = flat_bars(20)
        bars[7] = Bar(ts=7 * MS15, open=100, high=100.5, low=99.5, close=100, volume=1, quote_volume=1e3)
        r = self.run_one(bars)
        self.assertEqual((r.participation_refusals, len(r.events)), (1, 0))

    def test_cost_uses_decision_bar_volume(self):
        bars = flat_bars(20, qv=1e8)
        bars[4] = Bar(ts=4 * MS15, open=100, high=100.5, low=99.5, close=100, volume=1, quote_volume=4e5)
        bars[5] = Bar(ts=5 * MS15, open=100, high=100.5, low=99.5, close=100, volume=1, quote_volume=1e12)
        e = self.run_one(bars).events[0]
        want = (self.cost.quote(10_000.0, 4e5) + self.cost.quote(10_000.0, 1e8)) * 1e4
        self.assertAlmostEqual(e.cost_bp, want, places=9)

    def test_execution_fill_uses_last_closed_bar(self):
        from execution_sim import ExecutionConfig, apply_execution
        bars = flat_bars(10)
        bars[2] = Bar(ts=2 * MS15, open=100, high=100.5, low=99.5, close=100, volume=1, quote_volume=1e3)
        ev = {"entry_index": 3, "exit_index": 6, "side": 1, "net_bp": 5.0, "cost_bp": 10.0,
              "gross_bp": 0.0, "entry_ts": 3 * MS15, "exit_ts": 6 * MS15}
        out = apply_execution(ev, bars, ExecutionConfig(), order_value=1000, max_participation=0.05,
                              observed_delay_ms=0)
        self.assertEqual(out["exec_status"], "REJECTED_PARTICIPATION")      # 1000 > 5% of 1000

    def test_intrabar_exit_checks_the_exit_bar_for_liquidation(self):
        from execution_sim import ExecutionConfig, apply_execution
        cfg = ExecutionConfig(leverage=10.0, maintenance_margin=0.005)
        bars = flat_bars(8)
        bars[5] = Bar(ts=5 * MS15, open=100, high=104, low=90.0, close=100, volume=1, quote_volume=1e9)
        base = {"entry_index": 1, "exit_index": 5, "side": 1, "net_bp": 300.0, "cost_bp": 10.0,
                "entry_ts": MS15, "exit_ts": 5 * MS15}
        at_open = apply_execution({**base, "gross_bp": 0.0}, bars, cfg, order_value=1000,
                                  max_participation=0.05, observed_delay_ms=0)
        self.assertFalse(at_open["liquidated"])                  # exit at the open, before the low
        tp = apply_execution({**base, "gross_bp": 300.0}, bars, cfg, order_value=1000,
                             max_participation=0.05, observed_delay_ms=0)
        self.assertTrue(tp["liquidated"])                        # +3% take-profit and -10% low in one bar
        self.assertLess(tp["net_bp_exec"], -1000)


class TestPortfolioNoLookahead(unittest.TestCase):
    def test_sizing_ignores_the_entry_bar_close(self):
        from portfolio import PortfolioConfig, simulate_portfolio
        cost = CostContext.from_params({**FUTURES_COST_DEFAULTS, "max_participation": 1.0}, version="t")
        import random
        rnd = random.Random(5)
        px, bars = 100.0, []
        for i in range(80):
            px *= 1 + rnd.gauss(0, 0.002)
            bars.append(Bar(ts=i * MS15, open=px, high=px * 1.001, low=px * 0.999, close=px, volume=1,
                            quote_volume=1e9))
        sig = [{"symbol": "BTCUSDT", "decision_index": 49, "side": 1, "horizon": 4}]
        cfg = PortfolioConfig(leverage=5.0)
        a = simulate_portfolio({"BTCUSDT": bars}, sig, {}, cost, cfg)
        shock = list(bars)
        b50 = shock[50]
        shock[50] = Bar(ts=b50.ts, open=b50.open, high=b50.open * 1.2, low=b50.open * 0.999,
                        close=b50.open * 1.2, volume=1, quote_volume=1e9)   # entry bar closes +20%
        b = simulate_portfolio({"BTCUSDT": shock}, sig, {}, cost, cfg)
        self.assertAlmostEqual(a.total_fees, b.total_fees, places=9)       # same size at entry


# ---------------------------------------------------------------------------
# forward paper: plan integrity, crash recovery, idempotent conclusion
# ---------------------------------------------------------------------------

class TestForwardIntegrity(ForwardCase):
    def test_cost_change_after_registration_stops_the_plan(self):
        p, edge, feed = self.ready()
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        self.now[0] += 5 * 86_400_000
        fp.step(pid)
        other = CostContext.from_params({"fee_rate": 0.0001, "half_spread": 0.0, "impact_coefficient": 0.0,
                                         "max_participation": 1.0}, version="TEST_OTHER",
                                        verified=True, test_only=True)
        with mock.patch.object(p, "cost", other), mock.patch.object(p, "_ctx", p.context_id()):
            r = self.fp(p, feed).step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR")
        self.assertIn("PLAN_CONTEXT_CHANGED", json.dumps(fp.state(pid)["errors"]))

    def test_execution_rules_come_from_the_plan_not_the_current_file(self):
        p, edge, feed = self.ready()
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        crit = json.loads((DISC / "config" / "paper_forward_v1.json").read_text())
        crit["execution"]["partial_fill_policy"] = "cap"
        crit["execution"]["max_observation_delay_ms"] = 10 ** 12   # would make nothing late
        cp = self.td / "pf.json"
        cp.write_text(json.dumps(crit))
        from paper_forward import ForwardPaper
        fp2 = ForwardPaper(p, feed, criteria_path=cp, clock=lambda: self.now[0])
        self.now[0] += 20 * 86_400_000
        fp2.step(pid)
        ev = list(iter_jsonl(fp.root / pid / "events.jsonl"))
        self.assertTrue(ev)
        # archive bars observed days after their close: late under the REGISTERED 120 s limit
        self.assertTrue(all(e["late"] for e in ev))

    def test_crash_between_log_append_and_state_save_resumes_without_duplicates(self):
        p, edge, feed = self.ready()
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        self.now[0] += 3 * 86_400_000
        fp.step(pid)
        before = json.loads((fp.root / pid / "state.json").read_text())
        self.now[0] += 2 * 86_400_000
        with mock.patch("paper_forward.ForwardPaper._save_state", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.fp(p, feed).step(pid)                   # bars appended, state not saved
        self.assertEqual(json.loads((fp.root / pid / "state.json").read_text()), before)
        fp3 = self.fp(p, feed)
        fp3.step(pid)
        rows = list(iter_jsonl(fp.root / pid / "bars_BTCUSDT.jsonl"))
        ts = [r["ts"] for r in rows]
        self.assertEqual(len(ts), len(set(ts)))
        self.assertEqual(ts, sorted(ts))
        st = fp3.state(pid)
        self.assertEqual(st["bars"]["BTCUSDT"], len(rows))
        self.assertTrue(st.get("recovered"))
        self.assertEqual(st["status"], "OBSERVING")

    def test_conclusion_is_recorded_once_after_an_interruption(self):
        p, edge, feed = self.ready()
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        self.now[0] += 70 * 86_400_000
        with mock.patch("paper_forward.ForwardPaper._save_state", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                fp.step(pid)
        r = self.fp(p, feed).step(pid)
        self.assertIn(r["verdict"], ("PAPER_PASSED", "PAPER_FAILED"))
        n = sum(1 for x in iter_jsonl(p.eval_path) if x["stage"] == "paper_forward")
        self.assertEqual(n, 1)
        self.assertEqual(self.fp(p, feed).step(pid)["status"], "CONCLUDED")


# ---------------------------------------------------------------------------
# ops loop exit code
# ---------------------------------------------------------------------------

class TestOpsLoop(unittest.TestCase):
    def test_partial_cycles_are_not_reported_as_success(self):
        from ops import run_loop
        td = tmpdir(self)
        rcs = iter([0, 2, 0])

        def main(args):
            rc = next(rcs)
            print(json.dumps({"status": "CYCLE_OK" if rc == 0 else "PARTIAL_FAILURE"}))
            return rc
        r = run_loop(main, [], state_dir=td, max_cycles=3)
        self.assertEqual(r["stopped"], "MAX_CYCLES")
        self.assertEqual(r["partial_cycles"], 1)
        self.assertEqual(r["exit"], 2)


if __name__ == "__main__":
    unittest.main()
