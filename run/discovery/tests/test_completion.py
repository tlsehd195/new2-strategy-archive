"""Completion work (ADR 0006): cost calibration gate, data acquisition retry
and manifest, execution simulation, forward paper observation, ops health
and bounded loop, PR #4 review fixes and synthetic scenario matrix.

SYNTHETIC data and temp dirs only.  Nothing here is market data, nothing
here says anything about real profitability, and the real ledgers / locked
TEST windows are never touched.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import random
import shutil
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

from costs_bridge import (CostConfigError, CostContext, FUTURES_COST_DEFAULTS,  # noqa: E402
                          UNVERIFIED_COST_MODEL)
from data_quality import Bar  # noqa: E402
from engine_lib import iter_jsonl  # noqa: E402
from test_engine import make_bars, write_funding_csv, write_kline_csv  # noqa: E402
from test_pipeline import (EDGE, NOISE, MS15, PipelineCase, SyntheticProvider, hour_spec,  # noqa: E402
                           synth_bars, synth_funding, verified_fee_cost)
from test_residual_fixes import fee_cost  # noqa: E402

import cost_calibration as cc  # noqa: E402
import functools  # noqa: E402
#: these synthetic processes stand for a REAL account's fills (ADR 0063 makes the
#: account kind explicit; demo / undeclared accounts are tested separately)
_cav = functools.partial(cc.calibrate_and_verify, account_kind="real")


# ---------------------------------------------------------------------------
# cost calibration / verification gate
# ---------------------------------------------------------------------------

def synth_fills(n=600, *, fee=0.0004, half=0.0001, k=0.05, seed=3, symbols=("BTCUSDT",),
                notional=(5_000.0, 20_000.0), bias_bp=0.0):
    """Executions generated from a KNOWN cost process (unit test of the tool)."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        sym = symbols[i % len(symbols)]
        side = rng.choice((1, -1))
        nv = rng.uniform(*notional)
        bar = rng.uniform(5e6, 5e7)
        mid = 100.0
        slip = half + k * math.sqrt(nv / bar) + rng.gauss(0, 0.00002) + bias_bp / 1e4
        fill = mid * (1 + side * slip)
        out.append(cc.Observation(ts_ms=1_700_000_000_000 + i * 60_000, symbol=sym, side=side,
                                  order_type="taker", notional=nv, ref_price=mid, status="FILLED",
                                  source_kind="exchange_fills", bid=mid * (1 - half), ask=mid * (1 + half),
                                  fill_price=fill, filled_notional=nv, fee=fee * nv,
                                  bar_traded_value=bar, bar_volatility=0.002))
    return out


class TestCostVerificationGate(unittest.TestCase):
    def test_flag_alone_never_verifies(self):
        with self.assertRaises(CostConfigError):
            CostContext.from_params(FUTURES_COST_DEFAULTS, version="x", verified=True)
        self.assertFalse(CostContext.futures_default().verified)
        # the test-only stand-in still works, and is labelled as such
        self.assertTrue(verified_fee_cost().verified and verified_fee_cost().test_only)

    def test_known_process_is_verified_out_of_sample_and_sealed(self):
        rec = _cav(synth_fills(), CostContext.futures_default(),
                                      required_symbols=["BTCUSDT"], order_value=10_000.0)
        self.assertEqual(rec["status"], "VERIFIED", rec["reasons"])
        self.assertAlmostEqual(rec["params"]["fee_rate"], 0.0004, places=6)
        self.assertAlmostEqual(rec["params"]["half_spread"], 0.0001, places=6)
        self.assertAlmostEqual(rec["params"]["impact_coefficient"], 0.05, delta=0.01)
        sealed = cc.seal(rec)
        ctx = CostContext.from_verification(sealed)
        self.assertTrue(ctx.verified and not ctx.test_only)
        self.assertEqual(ctx.identity()["verification_record"], sealed["record_id"])
        self.assertEqual(ctx.verified_for(["BTCUSDT"], 10_000.0), (True, []))
        ok, why = ctx.verified_for(["ETHUSDT"], 50_000.0)
        self.assertFalse(ok)
        self.assertEqual(len(why), 2)                     # symbol + notional outside scope

    def test_tampered_or_mismatched_record_is_refused(self):
        sealed = cc.seal(_cav(synth_fills(), CostContext.futures_default(),
                                                 required_symbols=["BTCUSDT"], order_value=10_000.0))
        bad = dict(sealed, params={**sealed["params"], "fee_rate": 0.0})
        with self.assertRaises(CostConfigError):
            CostContext.from_verification(bad)            # hash no longer matches
        other = dict(sealed)
        with self.assertRaises(CostConfigError):          # record for different params
            CostContext(model=CostContext.futures_default().model, version="v", verified=True,
                        verification=other)
        nv = cc.seal({**{k: v for k, v in sealed.items() if k not in ("record_id", "sealed_at")},
                      "status": "NOT_VERIFIED"})
        with self.assertRaises(CostConfigError):
            CostContext.from_verification(nv)

    def test_underestimating_model_is_not_verified(self):
        obs = synth_fills()
        # the second half (verification) is systematically worse than the first: the fit
        # from the first half underestimates out of sample
        obs = obs[:300] + [cc.Observation(**{**o.__dict__, "fill_price": o.fill_price * (1 + o.side * 0.0005)})
                           for o in obs[300:]]
        rec = _cav(obs, CostContext.futures_default(), required_symbols=["BTCUSDT"],
                                      order_value=10_000.0)
        self.assertEqual(rec["status"], "NOT_VERIFIED")
        self.assertTrue(any("underestimate" in r for r in rec["reasons"]))

    def test_insufficient_or_quote_only_data_blocks(self):
        rec = _cav(synth_fills(n=100), CostContext.futures_default(),
                                      required_symbols=["BTCUSDT", "ETHUSDT"], order_value=10_000.0)
        # too few fills is a sample shortfall, not a failed model (ADR 0013)
        self.assertEqual(rec["status"], "INSUFFICIENT_DATA")
        self.assertTrue(any("ETHUSDT" in r for r in rec["reasons"]))
        empty = _cav([], CostContext.futures_default(), required_symbols=["BTCUSDT"],
                                        order_value=1.0)
        self.assertEqual(empty["status"], "BLOCKED_EXTERNAL_EVIDENCE")
        # the repository's own maker-fill monitor ledger: quotes only, virtual fills
        ledger = DISC.parent.parent / "data" / "fill_ledger.jsonl"
        obs = cc.observations_from_fill_monitor(ledger)
        self.assertTrue(obs)
        rec = _cav(obs, CostContext.futures_default(),
                                      required_symbols=["BTCUSDT", "ETHUSDT"], order_value=10_000.0)
        self.assertEqual(rec["status"], "BLOCKED_EXTERNAL_EVIDENCE")
        self.assertTrue(any("not verifiable" in r for r in rec["reasons"]))
        with self.assertRaises(CostConfigError):
            CostContext.from_verification(cc.seal(rec))

    def test_invalid_observations_raise(self):
        o = synth_fills(n=1)[0]
        for patch in ({"side": 0}, {"status": "DONE"}, {"ref_price": 0.0}, {"bid": 101.0, "ask": 100.0},
                      {"fee": None}, {"source_kind": "rumour"}):
            with self.assertRaises(cc.CalibrationError):
                cc.Observation(**{**o.__dict__, **patch}).validate()


# ---------------------------------------------------------------------------
# data acquisition: retry + classification
# ---------------------------------------------------------------------------

class _HTTPErr(Exception):
    def __init__(self, code, msg=""):
        super().__init__(msg or f"HTTP {code}")
        self.code = code


class TestFetchRetry(unittest.TestCase):
    def test_classification_and_bounded_backoff(self):
        from data_fetch import FetchFailure, classify_error, fetch_with_retry
        self.assertEqual(classify_error(_HTTPErr(404)), "NOT_PUBLISHED")
        self.assertEqual(classify_error(_HTTPErr(503)), "TRANSIENT")
        self.assertEqual(classify_error(_HTTPErr(429)), "TRANSIENT")
        self.assertEqual(classify_error(_HTTPErr(400)), "EXCHANGE_ERROR")
        self.assertEqual(classify_error(OSError("Tunnel connection failed: 403 Forbidden")), "PROXY_ERROR")
        self.assertEqual(classify_error(TimeoutError("timed out")), "TRANSIENT")
        delays, calls = [], []

        def flaky(url):
            calls.append(url)
            raise _HTTPErr(503)
        with self.assertRaises(FetchFailure) as cm:
            fetch_with_retry("u", opener=flaky, attempts=4, base_delay=2.0, sleep=delays.append)
        self.assertEqual((cm.exception.kind, cm.exception.attempts, len(calls)), ("TRANSIENT", 4, 4))
        self.assertEqual(delays, [2.0, 4.0, 8.0])           # exponential, finite
        calls.clear()
        with self.assertRaises(FetchFailure) as cm:
            fetch_with_retry("u", opener=lambda u: (calls.append(u), (_ for _ in ()).throw(_HTTPErr(404))),
                             sleep=delays.append)
        self.assertEqual((cm.exception.kind, len(calls)), ("NOT_PUBLISHED", 1))   # never retried
        seq = iter([_HTTPErr(502), b"ok"])

        def recover(url):
            x = next(seq)
            if isinstance(x, Exception):
                raise x
            return x
        self.assertEqual(fetch_with_retry("u", opener=recover, sleep=lambda s: None), b"ok")

    def test_fetch_missing_reports_failure_kind(self):
        from data_fetch import fetch_missing
        with tempfile.TemporaryDirectory() as td:
            r = fetch_missing(Path(td), ["BTCUSDT"], "2024-01", "2024-01", allow=True, kinds=("klines",),
                              opener=lambda u: (_ for _ in ()).throw(_HTTPErr(404)),
                              today=dt.date(2024, 4, 1), sleep=lambda s: None)
            self.assertEqual(r["blocked_by_kind"], {"NOT_PUBLISHED": 1})
            self.assertEqual(list(Path(td).rglob("*.csv")), [])


# ---------------------------------------------------------------------------
# dataset manifest
# ---------------------------------------------------------------------------

def _month_bars(y, m, seed=1):
    a = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
    b = dt.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=dt.timezone.utc)
    n = int((b - a).total_seconds() // 900)
    return make_bars(n=n, start_ts=int(a.timestamp() * 1000), seed=seed)


def _funding_for(bars):
    t0 = bars[0].ts
    return [(t0 + k * 8 * 3_600_000 + 2, 1e-4) for k in range((bars[-1].ts - t0) // (8 * 3_600_000) + 1)]


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        for sym in ("BTCUSDT", "ETHUSDT"):
            for m in (1, 2):
                b = _month_bars(2024, m, seed=m)
                write_kline_csv(self.td / "klines" / f"{sym}-15m-2024-{m:02d}.csv", b)
                write_funding_csv(self.td / "funding" / f"{sym}-fundingRate-2024-{m:02d}.csv", _funding_for(b))
        self.now = int(dt.datetime(2024, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)

    def build(self, **kw):
        from data_manifest import build_manifest
        return build_manifest(self.td, now_ms=self.now, **kw)

    def codes(self, man):
        return {i["code"] for i in man["issues"] if i["severity"] == "BLOCK"}

    def test_clean_dir_is_ok_and_reproducible(self):
        a, b = self.build(), self.build()
        self.assertTrue(a["ok"], a["issues"])
        self.assertEqual(a["dataset_sha256"], b["dataset_sha256"])
        self.assertEqual(set(a["symbols"]), {"BTCUSDT", "ETHUSDT"})
        self.assertTrue(any(i["code"] == "NO_PROVENANCE" for i in a["issues"]))   # reported, not hidden

    def test_detects_shift_close_time_incomplete_and_future_funding(self):
        p = self.td / "klines" / "BTCUSDT-15m-2024-02.csv"
        lines = p.read_text().splitlines()
        parts = lines[5].split(",")
        parts[6] = str(int(parts[6]) + 60_000)
        lines[5] = ",".join(parts)
        p.write_text("\n".join(lines) + "\n")
        shifted = [Bar(**{**b.__dict__, "ts": b.ts + 9 * 3_600_000}) for b in _month_bars(2024, 1)]
        write_kline_csv(self.td / "klines" / "ETHUSDT-15m-2024-01.csv", shifted)
        man = self.build()
        self.assertFalse(man["ok"])
        self.assertTrue({"CLOSE_TIME_MISMATCH", "MONTH_BOUNDARY_MISMATCH", "CROSS_MONTH_GAP"} <= self.codes(man))
        self.now = _month_bars(2024, 2)[-40].ts            # "now" inside February
        man = self.build()
        self.assertIn("INCOMPLETE_BAR", self.codes(man))
        self.assertIn("FUTURE_FUNDING", self.codes(man))

    def test_provenance_and_reference_manifest_conflicts(self):
        import hashlib
        ref = self.build()
        p = self.td / "klines" / "BTCUSDT-15m-2024-01.csv"
        prov = {"path": "/x/data/klines/BTCUSDT-15m-2024-01.csv",
                "csv_sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "url": "u", "status": "FETCHED"}
        (self.td / "_provenance.jsonl").write_text(json.dumps(prov) + "\n")
        self.assertTrue(self.build(reference=ref)["ok"])
        b = _month_bars(2024, 1, seed=99)                    # same name, different content
        write_kline_csv(p, b)
        man = self.build(reference=ref)
        self.assertTrue({"PROVENANCE_MISMATCH", "CHANGED_SINCE_MANIFEST"} <= self.codes(man))
        (self.td / "funding" / "ETHUSDT-fundingRate-2024-02.csv").unlink()
        self.assertIn("REMOVED_SINCE_MANIFEST", self.codes(self.build(reference=ref)))

    def test_symbol_range_mismatch_is_reported(self):
        (self.td / "klines" / "ETHUSDT-15m-2024-02.csv").unlink()
        man = self.build()
        self.assertIn("SYMBOL_RANGE_MISMATCH", {i["code"] for i in man["issues"]})


# ---------------------------------------------------------------------------
# execution simulation
# ---------------------------------------------------------------------------

class TestExecutionSim(unittest.TestCase):
    def test_fill_rules(self):
        from execution_sim import fill_fraction
        self.assertEqual(fill_fraction(100, 1e6, 0.05, "refuse"), (1.0, "FILLED"))
        self.assertEqual(fill_fraction(1e5, 1e6, 0.05, "refuse"), (0.0, "REJECTED_PARTICIPATION"))
        f, st = fill_fraction(1e5, 1e6, 0.05, "cap")
        self.assertEqual(st, "PARTIAL")
        self.assertAlmostEqual(f, 0.5)
        self.assertEqual(fill_fraction(1, 0.0, 0.05, "cap"), (0.0, "REJECTED_NO_LIQUIDITY"))

    def test_liquidation_uses_worst_intrabar_price(self):
        from execution_sim import ExecutionConfig, apply_execution, liquidation_price
        cfg = ExecutionConfig(leverage=10.0, maintenance_margin=0.005)
        self.assertAlmostEqual(liquidation_price(100.0, 1, cfg), 90.5)
        bars = [Bar(ts=i * MS15, open=100, high=101, low=99, close=100, volume=1, quote_volume=1e9)
                for i in range(6)]
        bars[3] = Bar(ts=3 * MS15, open=100, high=100.5, low=90.0, close=99.5, volume=1, quote_volume=1e9)
        ev = {"entry_index": 1, "exit_index": 5, "side": 1, "net_bp": 50.0, "cost_bp": 10.0,
              "entry_ts": MS15, "exit_ts": 5 * MS15}
        out = apply_execution(ev, bars, cfg, order_value=1000, max_participation=0.05, observed_delay_ms=0)
        self.assertTrue(out["liquidated"])
        self.assertEqual(out["liquidation_index"], 3)
        self.assertLess(out["net_bp_exec"], -1000)        # margin lost, never the strategy's +50
        short = apply_execution({**ev, "side": -1}, bars, cfg, order_value=1000, max_participation=0.05,
                                observed_delay_ms=10 ** 9)
        self.assertFalse(short["liquidated"])
        self.assertTrue(short["late"])

    def test_invalid_config(self):
        from execution_sim import ExecutionConfig
        for kw in ({"leverage": 0.5}, {"leverage": 10, "maintenance_margin": 0.2},
                   {"partial_fill_policy": "magic"}):
            with self.assertRaises(ValueError):
                ExecutionConfig(**kw).validate()


# ---------------------------------------------------------------------------
# forward paper observation
# ---------------------------------------------------------------------------

def _ms(d):
    return int(dt.datetime.fromisoformat(d + "T00:00:00+00:00").timestamp() * 1000)


class SynthFeed:
    """Bars appear when their close time has passed on the test clock."""

    kind = "synthetic_test_feed"

    def __init__(self, bars, clock, *, realtime=False):
        self.bars, self.fund, self.clock, self.realtime = bars, synth_funding(bars), clock, realtime
        self.extra: list = []

    def closed_bars(self, sym, after_ts):
        now = self.clock()
        out = [b for b in self.bars if b.ts > after_ts and b.ts + MS15 <= now] + self.extra
        return out, {}

    def funding(self, sym, after_ts):
        return [(t, r) for t, r in self.fund if after_ts < t <= self.clock()]

    #: synth_funding() writes one settlement every 8 hours; the fixture DECLARES
    #: that interval per settlement (ADR 0073: a feed must declare its schedule)
    FUNDING_INTERVAL_MS = 8 * 3_600_000

    def funding_checked(self, sym, after_ts):
        from paper_forward import funding_fetch_result
        rows = self.funding(sym, after_ts)
        return funding_fetch_result(
            rows, {t: self.FUNDING_INTERVAL_MS for t, _ in rows},
            source_files=[{"name": "synth_funding", "sha256": None, "rows": len(rows), "status": "VALID",
                           "errors": []}],
            schedule_source="declared by the synthetic fixture", query={"after_ts": after_ts})


class ForwardCase(PipelineCase):
    def ready(self, *, drift=(0, 1, 2), start="2024-08-25"):
        from paper import run_paper
        prov = SyntheticProvider()
        p = self.pipe(prov)
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        p.run_locked_campaign()
        run_paper(p, edge)
        ctx = p.context_id()
        self.assertEqual(p.statuses.status(edge, ctx), "PAPER_TESTING")   # replay never passes
        last = prov.paper("BTCUSDT")["bars"][-1]
        feed_bars = synth_bars("2024-07-01", "2025-03-31", seed=77, drift_hours=drift, price0=last.close)
        self.now = [_ms(start)]
        feed = SynthFeed(feed_bars, lambda: self.now[0])
        return p, edge, feed

    def fp(self, p, feed):
        from paper_forward import ForwardPaper
        return ForwardPaper(p, feed, clock=lambda: self.now[0])


class TestForwardPaper(ForwardCase):
    def test_full_observation_passes_only_after_preregistered_period(self):
        p, edge, feed = self.ready()
        fp = self.fp(p, feed)
        plan = fp.register_plan(edge)
        self.assertGreaterEqual(plan["start_ts"], self.now[0])
        self.assertEqual(plan["orders_sent"], 0)
        self.now[0] += 20 * 86_400_000
        r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_OBSERVING")             # too early, however good
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")
        self.now[0] += 50 * 86_400_000
        r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_PASSED")
        rec = p.evaluation("paper_forward", edge)
        self.assertIn("OBSERVED_FROM_ARCHIVE_NOT_REALTIME", rec["labels"])
        self.assertEqual(fp.step(plan["plan_id"])["status"], "CONCLUDED")
        from handoff import HANDOFF_SCHEMA, build_handoff
        doc = build_handoff(p, edge)
        self.assertEqual((doc["schema"], doc["paper_forward_verdict"], doc["live_registration"]),
                         (HANDOFF_SCHEMA, "PAPER_PASSED", False))
        self.assertEqual(doc["paper_forward_record_id"], rec["record_id"])
        # the search never sees it
        from search import FeedbackLeak, dev_feedback
        with self.assertRaises(FeedbackLeak):
            dev_feedback(rec)

    def test_no_edge_in_the_future_fails(self):
        p, edge, feed = self.ready(drift=())
        fp = self.fp(p, feed)
        plan = fp.register_plan(edge)
        self.now[0] += 75 * 86_400_000
        r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_FAILED")
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_FAILED")

    def test_resume_is_idempotent_and_unfinished_bars_are_ignored(self):
        p, edge, feed = self.ready()
        fp = self.fp(p, feed)
        plan = fp.register_plan(edge)
        pid = plan["plan_id"]
        self.now[0] += 10 * 86_400_000
        forming = self.now[0] // MS15 * MS15                      # opened, not yet closed
        feed.extra = [Bar(ts=forming, open=1, high=1, low=1, close=1, volume=1, quote_volume=1)]
        fp.step(pid)
        st = fp.state(pid)
        self.assertGreaterEqual(st["incomplete_skipped"], 1)
        feed.extra = []
        n_ev = len(list(iter_jsonl(fp.root / pid / "events.jsonl")))
        # killed process: a fresh engine on the same state, same clock
        fp2 = self.fp(p, feed)
        fp2.step(pid)
        self.assertEqual(len(list(iter_jsonl(fp.root / pid / "events.jsonl"))), n_ev)
        self.assertEqual(fp2.state(pid)["bars"], st["bars"])
        # a feed that re-sends old bars: counted as duplicates, never re-logged
        feed.extra = [b for b in feed.bars if plan["start_ts"] <= b.ts < plan["start_ts"] + 10 * MS15]
        fp2.step(pid)
        self.assertGreaterEqual(fp2.state(pid)["duplicates"]["BTCUSDT"], 0)
        self.assertEqual(len(list(iter_jsonl(fp.root / pid / "events.jsonl"))), n_ev)

    def test_tampered_bar_log_stops_with_error(self):
        p, edge, feed = self.ready()
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        self.now[0] += 5 * 86_400_000
        fp.step(pid)
        log = fp.root / pid / "bars_BTCUSDT.jsonl"
        lines = log.read_text().splitlines()
        row = json.loads(lines[3])
        row["c"] *= 1.01
        lines[3] = json.dumps(row)
        log.write_text("\n".join(lines) + "\n")
        self.now[0] += 86_400_000
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR")
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")

    def test_bad_bar_blocks_and_gap_is_counted(self):
        p, edge, feed = self.ready()
        fp = self.fp(p, feed)
        plan = fp.register_plan(edge)
        drop = {plan["start_ts"] + k * MS15 for k in range(100, 104)}
        feed.bars = [b for b in feed.bars if b.ts not in drop]
        self.now[0] += 3 * 86_400_000
        fp.step(plan["plan_id"])
        st = fp.state(plan["plan_id"])
        self.assertEqual(st["missing_bars"]["BTCUSDT"], 4)
        t = st["last_ts"]["BTCUSDT"] + MS15
        feed.bars = [b for b in feed.bars if b.ts != t]
        feed.extra = [Bar(ts=t, open=10, high=9, low=11, close=10, volume=1, quote_volume=1)]
        self.now[0] = t + 2 * MS15
        r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA")

    def test_refusals(self):
        from paper_forward import BinancePublicFeed, ForwardRefused
        p, edge, feed = self.ready()
        fp = self.fp(p, feed)
        with self.assertRaises(ForwardRefused):
            fp.register_plan(next(s for s, st in p.statuses.all(p.context_id()).items() if st == "REJECTED"))
        with mock.patch.object(type(p.cost), "verified_for", lambda self, s, o: (False, ["x"])):
            with self.assertRaises(ForwardRefused):
                fp.register_plan(edge)
        fp.register_plan(edge)
        with self.assertRaises(ForwardRefused):
            fp.register_plan(edge)                      # one plan per strategy and context
        with self.assertRaises(ForwardRefused):
            BinancePublicFeed()                         # network is opt-in

    def test_realtime_feed_flags_late_decisions(self):
        p, edge, feed = self.ready()
        feed.realtime = True
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        self.now[0] += 3 * 86_400_000                    # observed days after the fact
        fp.step(pid)
        evs = list(iter_jsonl(fp.root / pid / "events.jsonl"))
        self.assertTrue(evs and all(e["late"] for e in evs))
        self.assertEqual(fp.metrics(pid)["n_trades"], 0)  # late decisions never count


# ---------------------------------------------------------------------------
# PR #4 review: diagnostics never gate, DM alignment, re-verification queue
# ---------------------------------------------------------------------------

class TestPR4Review(PipelineCase):
    def test_step_spa_and_rc_cannot_change_the_validation_outcome(self):
        def run(patches):
            td = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, td, True)
            p = self.pipe(SyntheticProvider(), state=td)
            self.register_all(p, [EDGE] + NOISE[:7])
            import contextlib
            with (mock.patch.multiple("pipeline", **patches) if patches else contextlib.nullcontext()):
                return p.run_validation()["outcome"]
        base = run({})
        everything = run({"step_spa": lambda s, **k: {"rejected": sorted(s)},
                          "reality_check": lambda s, **k: {"p": 0.0},
                          "mcs": lambda s, **k: {"set": [], "p": {}, "alpha": 0.1}})
        nothing = run({"step_spa": lambda s, **k: {"rejected": []},
                       "reality_check": lambda s, **k: {"p": 1.0}})
        self.assertEqual(base, everything)
        self.assertEqual(base, nothing)

    def test_dm_uses_common_days_and_symbols_only(self):
        p = self.pipe(SyntheticProvider())
        parent, _ = p.register(NOISE[0], source="test")
        p.dev_evaluate(parent)
        child, _ = p.register(EDGE, source="test", parents=[parent], operator="param_change")
        p.dev_evaluate(child)
        ctx = p.context_id()
        from engine_lib import append_jsonl
        rows = {r["strategy_id"]: r for r in iter_jsonl(p.series_path)}
        d0, vals = rows[parent]["series"]["BTCUSDT"]
        # parent covers only the second half of the days, plus an ETH series the child lacks
        half = len(vals) // 2
        append_jsonl(p.series_path, {"strategy_id": parent, "context_id": ctx,
                                     "series": {"BTCUSDT": [d0 + half, vals[half:]],
                                                "ETHUSDT": [d0, [5.0] * len(vals)]}})
        row = {r["child"]: r for r in p.variation_report()}[child]
        self.assertEqual(row["status"], "OK")
        self.assertEqual(row["symbols"], ["BTCUSDT"])
        self.assertEqual(row["n_days"], len(vals) - half)
        self.assertEqual(row["first_day"], d0 + half)

    def test_cost_breakeven_is_labelled_with_cost_status(self):
        p = self.pipe(SyntheticProvider(), cost=fee_cost(0.0002))
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        rob = p.evaluation("robustness", edge)
        self.assertEqual(rob["checks"]["cost_stress"]["breakeven_basis"], UNVERIFIED_COST_MODEL)

    def test_cost_model_change_queues_reverification(self):
        st = self.td / "shared"
        p1 = self.pipe(SyntheticProvider(), state=st)
        edge, *_ = self.register_all(p1, [EDGE] + NOISE[:7])
        p1.run_validation()
        p1.run_locked_campaign()
        self.assertEqual(p1.statuses.status(edge, p1.context_id()), "PAPER_TEST_READY")
        p2 = self.pipe(SyntheticProvider(), state=st, cost=verified_fee_cost(0.0003))
        self.assertNotEqual(p1.context_id(), p2.context_id())
        pend = {r["strategy_id"]: r for r in p2.reverification_pending()}
        self.assertIn(edge, pend)
        self.assertIn("cost", pend[edge]["changed"])
        self.assertIsNone(p2.statuses.status(edge, p2.context_id()))      # old PASS not reused


# ---------------------------------------------------------------------------
# synthetic scenario matrix + locked TEST discipline
# ---------------------------------------------------------------------------

class TestScenarioMatrix(PipelineCase):
    def test_cost_fragile_edge_is_rejected_by_cost_stress(self):
        p = self.pipe(SyntheticProvider(), cost=CostContext.from_params(
            {"fee_rate": 0.0026, "half_spread": 0.0, "impact_coefficient": 0.0, "max_participation": 1.0},
            version="TEST_FRAGILE", verified=True, test_only=True))
        ctx = p.context_id()
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        vb = p.run_validation()
        rob = p.evaluation("robustness", edge)
        if vb["outcome"].get(edge) == "PASSED_STEPM":
            self.assertIn("cost_stress", rob["failed"])
            self.assertEqual(p.statuses.status(edge, ctx), "REJECTED")
        else:                                       # net edge too thin to pass validation at all
            self.assertIn(p.statuses.status(edge, ctx), ("REJECTED", "INCONCLUSIVE"))
        p.run_locked_campaign()
        self.assertNotIn("PAPER_TEST_READY", p.statuses.all(ctx).values())

    def test_no_survivor_means_locked_window_never_loaded(self):
        prov = SyntheticProvider(drift_hours=())
        p = self.pipe(prov)
        self.register_all(p, [hour_spec(h, h + 2) for h in range(0, 24, 2)])
        p.run_validation()
        self.assertIsNone(p.run_locked_campaign())
        self.assertEqual(prov.locked_loads, 0)
        self.assertFalse((self.td / "state" / "lock_test_usage.json").exists()
                         and "CONSUMED" in (self.td / "state" / "lock_test_usage.json").read_text())

    def test_leaky_feature_is_caught_by_the_prefix_guard(self):
        """The guard used in TestSpecCausality must detect a feature that reads
        the next bar (a deliberately leaky feature, never registered)."""
        bars = make_bars(n=300, seed=4)

        def leaky(bs):
            return [bs[i + 1].close / bs[i].close - 1 if i + 1 < len(bs) else 0.0 for i in range(len(bs))]
        cut = 200
        poisoned = bars[:cut + 1] + [Bar(**{**b.__dict__, "close": b.close * 2}) for b in bars[cut + 1:]]
        self.assertNotEqual(leaky(bars)[:cut + 1], leaky(poisoned)[:cut + 1])

    def test_peeking_signal_raises_instead_of_trading(self):
        from evaluator import run_event_study
        bars = make_bars(n=200, seed=2)

        def peek(view):
            return 1 if view[len(view)].close > view[len(view) - 1].close else 0   # the next bar
        with self.assertRaises(IndexError):
            run_event_study(bars, "BTCUSDT", peek, 4, fee_cost(), 1000.0)


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------

class TestOps(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        (self.td / "state" / "evolution").mkdir(parents=True)

    def test_health_ok_stale_lock_and_corruption(self):
        from ops import health
        self.assertEqual(health(self.td / "state")["status"], "OK")
        (self.td / "state" / "run.lock").write_text("999999999")
        h = health(self.td / "state")
        self.assertEqual((h["status"], h["checks"]["lock"]["status"]), ("DEGRADED", "STALE"))
        (self.td / "state" / "run.lock").unlink()
        led = self.td / "state" / "evolution" / "statuses.jsonl"
        led.write_text('{"a": 1}\n{broken\n{"b": 2}\n')
        h = health(self.td / "state")
        self.assertEqual((h["status"], h["checks"]["ledgers"]["status"]), ("FAILED", "CORRUPT"))

    def test_health_detects_tampered_evidence_and_bad_config(self):
        from ops import health
        from pipeline import record_hash
        r = {"stage": "dev", "strategy_id": "S", "context_id": "C", "status": "OK"}
        r["record_id"] = record_hash(r)
        r2 = dict(r, status="PASS")
        (self.td / "state" / "evolution" / "evaluations.jsonl").write_text(
            json.dumps(r) + "\n" + json.dumps(r2) + "\n")
        self.assertEqual(health(self.td / "state")["checks"]["evidence"]["status"], "TAMPERED")
        cfg = self.td / "cfg"
        shutil.copytree(DISC / "config", cfg)
        (cfg / "paper_forward_v1.json").write_text("{not json")
        h = health(self.td / "state", config_dir=cfg, data_dir=self.td / "nope")
        self.assertEqual(h["status"], "FAILED")
        self.assertEqual(h["checks"]["data_dir"]["status"], "MISSING")
        self.assertEqual(h["checks"]["config"]["status"], "INVALID")

    def test_loop_is_bounded_and_stops_on_failure(self):
        from ops import run_loop
        seq = iter([0, 0, 1, 0])

        def fake_main(args):
            rc = next(seq)
            print(json.dumps({"status": "CYCLE_OK" if rc == 0 else "FAILED"}))
            return rc
        r = run_loop(fake_main, [], state_dir=self.td / "state", max_cycles=10, sleep=lambda s: None)
        self.assertEqual((r["stopped"], r["cycles"]), ("CYCLE_FAILED", 3))

        def crash(args):
            raise PermissionError("state dir not writable")
        r = run_loop(crash, [], state_dir=self.td / "state", max_cycles=5, sleep=lambda s: None)
        self.assertEqual((r["stopped"], r["last_exit"]), ("CYCLE_FAILED", 1))

        def idle(args):
            print(json.dumps({"status": "CYCLE_NO_PROGRESS"}))
            return 0
        r = run_loop(idle, [], state_dir=self.td / "state", max_cycles=10, max_no_progress=2,
                     sleep=lambda s: None)
        self.assertEqual((r["stopped"], r["cycles"]), ("NO_PROGRESS", 2))
        with self.assertRaises(ValueError):
            run_loop(idle, [], state_dir=self.td / "state", max_cycles=0)
        log = list(iter_jsonl(self.td / "state" / "ops_loop.jsonl"))
        self.assertEqual(sum(1 for x in log if "loop_end" in x), 3)

    def test_permission_error_in_cycle_is_failed_exit(self):
        import contextlib
        import io
        import os
        import runner
        (self.td / "data").mkdir()
        env = {"NEW2_DISCOVERY_ROOT": str(self.td / "root"), "NEW2_DATA_DIR": str(self.td / "data")}
        with mock.patch.dict(os.environ, env), \
                mock.patch("autoloop.run_evolution_cycle", side_effect=PermissionError(13, "Permission denied")), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            rc = runner.main(["--mode", "evolve"])
        self.assertEqual(rc, 1)
        self.assertIn("Permission denied", out.getvalue())

    def test_invalid_promotion_config_fails_closed(self):
        from pipeline import load_promotion_config
        bad = self.td / "p.json"
        bad.write_text(json.dumps({"schema": "something_else"}))
        with self.assertRaises(ValueError):
            load_promotion_config(bad)
        bad.write_text("{")
        with self.assertRaises(ValueError):
            load_promotion_config(bad)


if __name__ == "__main__":
    unittest.main()
