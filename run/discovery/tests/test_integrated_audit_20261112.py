"""ADR 0078: regression tests of the integrated full audit (2026-11-12).

One test (or more) per finding fixed in ADR 0078.  Each test was first a
reproduction against main 2d6616a (scratch repro scripts of the audit) and
asserts the CORRECT behaviour, so it fails on 2d6616a and passes after the fix.

SYNTHETIC data, mocks and temp dirs only.  No market data, no network, no
exchange account, no locked / IW / sealed window, no existing state file.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import json
import math
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for _p in (str(DISC), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_pipeline as tp  # noqa: E402
from data_quality import Bar  # noqa: E402
from engine_lib import append_jsonl, iter_jsonl  # noqa: E402

MS15 = 900_000
H = 3_600_000
H8 = 8 * H


class Crash(BaseException):
    """A simulated kill -9 (not an Exception: nothing may catch it)."""


# ---------------------------------------------------------------------------
# synthetic Binance-format files
# ---------------------------------------------------------------------------

def _month_bounds(y, m):
    a = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
    b = dt.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=dt.timezone.utc)
    return int(a.timestamp() * 1000), int(b.timestamp() * 1000)


def _write_month(d: Path, sym, y, m, *, seed=0, px=100.0):
    a, b = _month_bounds(y, m)
    rnd = random.Random(seed)
    rows, t = [], a
    while t < b:
        o = px
        c = px * math.exp(rnd.gauss(0, 0.003))
        hi, lo = max(o, c) * 1.001, min(o, c) * 0.999
        v = 1e6 + rnd.random() * 5e5
        tb = v * rnd.random()
        rows.append(",".join(str(x) for x in (t, f"{o:.6f}", f"{hi:.6f}", f"{lo:.6f}", f"{c:.6f}",
                                              f"{v:.4f}", t + MS15 - 1, f"{v * c:.4f}", 100,
                                              f"{tb:.4f}", f"{tb * c:.4f}", 0)))
        px, t = c, t + MS15
    p = d / "klines" / f"{sym}-15m-{y:04d}-{m:02d}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(rows) + "\n")
    return px


def _write_funding_month(d: Path, sym, y, m, *, hours=8, rate=0.0001, jitter_ms=0):
    a, b = _month_bounds(y, m)
    lines, t = ["calc_time,funding_interval_hours,last_funding_rate"], a
    while t < b:
        lines.append(f"{t + jitter_ms},{hours},{rate}")
        t += hours * H
    p = d / "funding" / f"{sym}-fundingRate-{y:04d}-{m:02d}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n")
    return p


def _ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


class PCase(tp.PipelineCase):
    """PipelineCase with one synthetic provider per test."""

    def setUp(self):
        super().setUp()
        self.prov = tp.SyntheticProvider()

    def pipe(self, provider=None, **kw):
        return super().pipe(provider or self.prov, **kw)


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, True)


# ---------------------------------------------------------------------------
# A — data
# ---------------------------------------------------------------------------

class TestDataWindows(TmpCase):
    def test_a01_window_cut_starts_at_start_iso_not_month_start(self):
        """A-01: the locked window (BTC from 2024-03-25) started on 2024-03-01."""
        import runner
        px = 100.0
        for m in (3, 4):
            px = _write_month(self.td, "BTCUSDT", 2024, m, seed=m, px=px)
        h = runner.merged_bars(self.td, "BTCUSDT", "2024-03-25", "2024-04-10")
        self.assertEqual(h.first_ts, _ms("2024-03-25"))
        self.assertTrue(all(b.ts >= _ms("2024-03-25") for b in h.bars))
        self.assertEqual(h.last_ts, _ms("2024-04-11") - MS15)

    def test_a03_bad_bar_outside_the_window_does_not_block_it(self):
        """A-03: a bad bar on a day OUTSIDE the cut window rejected the window
        (the whole month file was gated first: one bit of out-of-window data)."""
        import runner
        _write_month(self.td, "BTCUSDT", 2024, 3, seed=1)
        p = self.td / "klines" / "BTCUSDT-15m-2024-03.csv"
        rows = p.read_text().splitlines()
        k = (_ms("2024-03-28") - _ms("2024-03-01")) // MS15
        f = rows[k].split(",")
        f[2] = str(float(f[3]) * 0.5)          # high < low on 2024-03-28 (outside the window)
        rows[k] = ",".join(f)
        p.write_text("\n".join(rows) + "\n")
        ok = runner.merged_bars(self.td, "BTCUSDT", "2024-03-01", "2024-03-20")
        self.assertTrue(ok.quality.ok, ok.missing)
        bad = runner.merged_bars(self.td, "BTCUSDT", "2024-03-20", "2024-03-30")
        self.assertFalse(bad.quality.ok)

    def test_a02_flat_window_has_no_z(self):
        """A-02: a constant series gave a large finite z (sd ~ 1e-20) instead of None."""
        import runner
        import strategy_spec as ss
        vals = [0.1] * 200
        self.assertIsNone(runner._rolling_z(vals, 150, 96))
        self.assertIsNone(ss._rolling_z(vals, 150, 96))
        for rate in (0.0001, 0.00005, 0.0003, 0.000125, -0.0001):
            for w in (8, 10, 30, 90):
                fund = [(k, rate) for k in range(w + 2)]
                self.assertIsNone(runner.funding_settlement_z(fund, [w + 1], w)[0], (rate, w))
        # a real (non-degenerate) change still has a z
        fund = [(k, 0.0001 + (k % 3) * 1e-5) for k in range(40)] + [(40, 0.001)]
        self.assertIsNotNone(runner.funding_settlement_z(fund, [41], 30)[0])

    def test_a09_settlement_at_the_fill_open_is_not_known_at_the_decision(self):
        """A-09: a settlement stamped at the next bar's open (= the fill time) was
        treated as known at the decision bar's close."""
        import runner
        t0 = 1_704_067_200_000
        bars = [Bar(t0 + i * MS15, 1, 1, 1, 1, 1, 1, 0) for i in range(40)]
        fund = [(t0 - k * H8, 0.0001) for k in range(12, 0, -1)] + [(t0, 0.0001), (t0 + H8, 0.01)]
        known = runner.funding_known_series(bars, fund)
        i = 31                                   # bar 07:45-08:00; its entry fills at 08:00
        self.assertEqual(fund[known[i] - 1][0], t0)
        self.assertEqual(fund[known[32] - 1][0], t0 + H8)


class TestFngGate(TmpCase):
    def _fng(self, name, rows):
        d = self.td / "fng"
        d.mkdir(exist_ok=True)
        (d / name).write_text("date,value\n" + "\n".join(rows) + "\n")

    def test_a06_out_of_range_value_blocks(self):
        import runner
        self._fng("a.csv", ["2024-01-01,20", "2024-01-02,150"])
        s, rep = runner.load_fng_series_checked(self.td)
        self.assertIsNone(s)
        self.assertEqual(rep["status"], "BLOCKED")
        self.assertIsNone(runner.load_fng_series(self.td))

    def test_a06_conflicting_duplicate_day_blocks(self):
        import runner
        self._fng("a.csv", ["2024-01-01,20", "2024-01-02,30"])
        self._fng("b.csv", ["2024-01-02,31"])
        s, rep = runner.load_fng_series_checked(self.td)
        self.assertIsNone(s)
        self.assertEqual(rep["status"], "BLOCKED")

    def test_a06_unparsable_value_blocks_and_gaps_are_reported(self):
        import runner
        self._fng("a.csv", ["2024-01-01,20", "2024-01-02,abc"])
        self.assertEqual(runner.load_fng_series_checked(self.td)[1]["status"], "BLOCKED")
        shutil.rmtree(self.td / "fng")
        self._fng("a.csv", ["2024-01-01,20", "2024-01-04,30"])
        s, rep = runner.load_fng_series_checked(self.td)
        self.assertEqual(rep["status"], "OK")
        self.assertEqual(rep["gap_days"], 2)
        self.assertEqual(len(s), 2)


class TestFileFeed(TmpCase):
    T0 = 1_730_419_200_000          # 2024-11-01 00:00 UTC

    def _day(self, day, *, corrupt=False, close=100):
        rows = []
        for k in range(96):
            ts = self.T0 + day * 86_400_000 + k * MS15
            rows.append(f"{ts},100,101,99,{close},10,{ts + MS15 - 1},1000000,5,5,5,0")
        if corrupt:
            rows[40] = "garbage,row"
        d = self.td / "klines"
        d.mkdir(exist_ok=True)
        (d / f"BTCUSDT-15m-2024-11-0{day + 1}.csv").write_text("\n".join(rows) + "\n")

    def test_g02_corrupt_file_holds_back_later_bars(self):
        """G-02: a blocked file was skipped and LATER files delivered: a permanent hole."""
        from paper_forward import FileFeed
        self._day(0)
        self._day(1, corrupt=True)
        self._day(2)
        feed = FileFeed(self.td, clock=lambda: self.T0 + 4 * 86_400_000)
        bars, info = feed.closed_bars("BTCUSDT", self.T0 - MS15)
        ts = [b.ts for b in bars]
        self.assertEqual(len(bars), 96)
        self.assertTrue(all(b - a == MS15 for a, b in zip(ts, ts[1:])))
        self.assertEqual(info["held_back_from_ts"], self.T0 + 86_400_000)
        self.assertEqual(len(info["blocked_files"]), 1)

    def test_a08_conflicting_files_hold_back_from_the_conflict(self):
        """A-08: two files disagreeing on one bar: the first was kept silently."""
        from paper_forward import FileFeed
        self._day(0)
        d = self.td / "klines"
        rows = []
        for k in range(48, 96):
            ts = self.T0 + k * MS15
            rows.append(f"{ts},100,101,99,{100.5 if k == 60 else 100},10,{ts + MS15 - 1},1000000,5,5,5,0")
        (d / "BTCUSDT-15m-2024-11-01-x.csv").write_text("\n".join(rows) + "\n")
        feed = FileFeed(self.td, clock=lambda: self.T0 + 2 * 86_400_000)
        bars, info = feed.closed_bars("BTCUSDT", self.T0 - MS15)
        self.assertTrue(info["conflicts"])
        self.assertEqual(info["held_back_from_ts"], self.T0 + 60 * MS15)
        self.assertTrue(all(b.ts < self.T0 + 60 * MS15 for b in bars))


# ---------------------------------------------------------------------------
# C — cost / funding
# ---------------------------------------------------------------------------

class TestFunding(TmpCase):
    def test_c01_calc_time_jitter_is_snapped_to_the_hour(self):
        """C-01: +3 ms calc_time moved a settlement into / out of (entry, exit]."""
        from data_quality import load_funding_csv_checked
        p = _write_funding_month(self.td, "BTCUSDT", 2024, 1, jitter_ms=3)
        pairs, rep = load_funding_csv_checked(p)
        self.assertTrue(all(t % H == 0 for t, _ in pairs))
        self.assertEqual(rep["calc_time_snapped"]["n"], len(pairs))

    def test_c01_snapped_settlement_charged_by_the_holding_rule(self):
        from costs_bridge import CostContext
        from data_quality import load_funding_csv_checked
        from evaluator import run_event_study
        p = _write_funding_month(self.td, "X", 2024, 1, jitter_ms=3, rate=0.0)
        pairs, _ = load_funding_csv_checked(p)
        t0 = _ms("2024-01-02")
        rates = [(t, 0.003 if t == t0 + H8 else 0.0) for t, _ in pairs]
        bars = [Bar(ts=t0 + i * MS15, open=100, high=100.1, low=99.9, close=100, volume=1e7,
                    quote_volume=1e9) for i in range(110)]
        cost = CostContext.from_params({"fee_rate": 0, "half_spread": 0, "impact_coefficient": 0,
                                        "max_participation": 1.0}, version="t", test_only=True)
        es = run_event_study(bars, "X", lambda v: -1 if len(v) == 32 else 0, 32, cost, 1000.0, rates,
                             funding_intervals={t: H8 for t, _ in rates})
        e = es.events[0]
        self.assertEqual(e.entry_ts, t0 + H8)
        self.assertEqual(e.funding_bp, 0.0)        # the T0+8h settlement is not held

    def test_c03_schedule_change_judged_under_both_readings(self):
        """C-03: a 4h -> 8h change contiguous under ONE reading passed per trade."""
        from funding_schedule import coverage_check, settlement_check
        a = 1_700_006_400_000 - (1_700_006_400_000 % H8)
        fund = [(a - 4 * H, 0.001), (a, 0.001), (a + 8 * H, 0.001), (a + 16 * H, 0.001)]
        ivs = {a - 4 * H: 4 * H, a: 4 * H, a + 8 * H: 8 * H, a + 16 * H: 8 * H}
        pt = settlement_check(fund, ivs, a - 1, a + 8 * H)
        cv = coverage_check(fund, ivs, a - 1, a + 8 * H)
        self.assertEqual(pt["status"], "FUNDING_SCHEDULE_UNKNOWN")
        self.assertIn("SCHEDULE_CHANGE_AMBIGUOUS", pt["reason"])
        self.assertNotEqual(cv["status"], "VALID")

    def test_c05_decision_log_is_symbol_scoped(self):
        """C-05: an ETH rejection with the id of a BTC fill was dropped."""
        from fills_import import load_import_policy, orders_to_observations
        pol, _ = load_import_policy()
        now = int(time.time() * 1000) - 86_400_000
        fills = [{"symbol": "BTCUSDT", "order_id": "1", "side": "BUY", "price": "100", "qty": "10",
                  "ts_ms": now, "fee_quote": 0.5, "liquidity": "TAKER"}]
        decisions = {"1": {"order_id": "1", "symbol": "ETHUSDT", "decision_ts_ms": now - 1000,
                           "ref_price": 100.0, "requested_notional": 1000.0, "status": "REJECTED",
                           "side": "BUY"}}
        obs, _why = orders_to_observations(fills, policy=pol, decisions=decisions)
        self.assertTrue(any(o.symbol == "ETHUSDT" and o.status == "REJECTED" for o in obs))
        # the BTC fill is never priced against the ETH decision's reference
        self.assertTrue(all(o.status != "REJECTED" for o in obs if o.symbol == "BTCUSDT"))

    def test_c06_partial_fill_predicted_on_filled_notional(self):
        """C-06: the prediction used the requested notional (larger impact)."""
        from cost_calibration import Observation, predicted_bp
        from costs_bridge import CostContext
        ctx = CostContext.from_params({"fee_rate": 0.0005, "half_spread": 0.0002, "impact_coefficient": 0.1,
                                       "max_participation": 0.5}, version="t")
        ts = int(time.time() * 1000) - 86_400_000
        o = Observation(ts_ms=ts, symbol="BTCUSDT", side=1, order_type="taker", notional=100_000.0,
                        ref_price=100.0, status="PARTIAL", source_kind="exchange_fills", fill_price=100.05,
                        filled_notional=1_000.0, fee=0.5, bar_traded_value=1_000_000.0)
        o.validate()
        self.assertAlmostEqual(predicted_bp(ctx, o), ctx.quote(1_000.0, 1_000_000.0) * 1e4, places=9)


class TestCostSensitivity(unittest.TestCase):
    def test_c09_volatility_label_is_per_symbol(self):
        """C-09: two symbols entering on one bar overwrote each other's label."""
        import cost_sensitivity as cs
        z = {"gross_bp": 0.0, "cost_bp": 0.0, "funding_bp": 0.0}
        ev = [dict(z, symbol="A", entry_ts=1, net_bp=10.0), dict(z, symbol="B", entry_ts=1, net_bp=-4.0)]
        row = cs._row(ev, 0, {("A", 1): "high_vol", ("B", 1): "low_vol"})
        self.assertEqual((row["n_high_vol"], row["n_low_vol"]), (1, 1))
        self.assertEqual((row["net_bp_high_vol"], row["net_bp_low_vol"]), (10.0, -4.0))


class TestPipelineFundingSchedule(PCase):
    def test_me01_locked_and_paper_inputs_carry_the_declared_schedule(self):
        """ME-01 / C-02: the locked study merged pre+locked funding into a NEW list
        and the paper replay had no intervals -> NOMINAL_8H_LEGACY silently."""
        p = self.pipe()
        for kind in ("locked", "paper"):
            d = getattr(p.provider, kind)("BTCUSDT")
            self.assertTrue(d.get("funding_intervals"), kind)
        from strategy_spec import canonicalize_spec
        spec = canonicalize_spec(tp.EDGE)
        d = dict(p.provider.symbol("BTCUSDT"))
        d.pop("funding_intervals", None)
        with mock.patch.object(p.provider, "symbol", return_value=d):
            _data, _f, missing = p._inputs(spec)
        self.assertTrue(any("NOMINAL_8H_LEGACY refused" in m for m in missing), missing)


# ---------------------------------------------------------------------------
# D — statistics
# ---------------------------------------------------------------------------

class TestStatistics(unittest.TestCase):
    def test_d01_stepm_refuses_unresolvable_alpha(self):
        """D-01: alpha*(B+1) < 1 clamped the critical value to the max draw
        (real level ~1/(B+1), x4 the nominal alpha at batch k >= 4)."""
        from stats_mc import (InsufficientBootstrapResolution, bootstrap_B_for, default_q,
                              spending_alpha, stepm)
        rng = random.Random(1)
        series = {f"s{j}": [rng.gauss(0.25 if j == 0 else 0, 1) for _ in range(175)] for j in range(3)}
        a = spending_alpha(8, 0.025)
        with self.assertRaises(InsufficientBootstrapResolution):
            stepm(series, alpha=a, B=1000, q=default_q(175), seed=7)
        B = bootstrap_B_for(a, 1000)
        self.assertGreaterEqual(a * (B + 1), 5)
        r = stepm(series, alpha=a, B=B, q=default_q(175), seed=7)
        self.assertIn("rejected", r)
        self.assertEqual(bootstrap_B_for(0.05, 1000), 1000)

    def test_d04_pbo_uses_every_row(self):
        """D-04: the last T mod S rows never entered CSCV."""
        from engine_lib import pbo_cscv
        rng = random.Random(5)
        T, N, S = 47, 6, 16
        mat = [[rng.gauss(0, 1) for _ in range(N)] for _ in range(T)]
        mat2 = [row[:] for row in mat]
        for r in range(32, T):
            mat2[r] = [1e6 if c == 0 else -1e6 for c in range(N)]
        self.assertNotEqual(pbo_cscv(mat, n_partitions=S), pbo_cscv(mat2, n_partitions=S))


class TestPipelineStatistics(PCase):
    def _validated(self, state="state", history=None):
        from pipeline import EvolutionPipeline
        p = EvolutionPipeline(self.td / state, self.prov, tp.verified_fee_cost(), symbols=["BTCUSDT"],
                              validation_history=history)
        for sp in [tp.EDGE] + tp.NOISE:
            sid, _ = p.register(sp, source="test")
            p.dev_evaluate(sid)
        p.run_validation()
        return p

    def test_d01_batch_records_resolving_B(self):
        p = self._validated()
        from stats_mc import MIN_TAIL_DRAWS
        b = next(x for x in p._batches() if x["kind"] == "PREREGISTERED")
        for w in b["windows"].values():
            self.assertGreaterEqual(w["alpha"] * (w["B"] + 1), MIN_TAIL_DRAWS)

    def test_d03_alpha_spending_shared_through_the_history(self):
        """D-03: a second state directory did not see the first one's batches."""
        hist = self.td / "validation_history.jsonl"
        p1 = self._validated("s1", hist)
        rows = list(iter_jsonl(hist))
        self.assertEqual(len(rows), 1)
        b1 = next(x for x in p1._batches() if x["kind"] == "PREREGISTERED")
        self.assertEqual(rows[0]["batch_ids"], [b1["batch_id"]])
        self.assertTrue(os.path.exists(str(hist) + ".lock"))
        from pipeline import EvolutionPipeline
        p2 = EvolutionPipeline(self.td / "s2", self.prov, tp.verified_fee_cost(), symbols=["BTCUSDT"],
                               validation_history=hist)
        for key in b1["windows"]:
            self.assertEqual(p2.external_prior_batches(key), 1)
            self.assertEqual(p1.external_prior_batches(key), 0)     # own batch not double counted

    def test_d02_d06_dsr_counts_every_trial_searched(self):
        """D-02/D-06: DSR deflated by the computable / current-context trials only."""
        p = self._validated()
        edge = p.register(tp.EDGE, source="test")[0]
        rec = p.evaluation("robustness", edge)
        self.assertIsNotNone(rec)
        dsr = rec["checks"]["dsr"]
        self.assertGreaterEqual(dsr["trials_searched"], len(p.search_pool()))
        self.assertGreaterEqual(dsr["trials_searched"], dsr.get("trials", 0))
        if dsr.get("gating"):
            self.assertEqual(dsr["gate_value"], dsr.get("dsr_at_trials_searched"))

    def test_d05_ci_spans_the_validation_window(self):
        """D-05: the CI series ran from the first to the last exit day only, so
        flat days of the validation window before / after the trades were left out."""
        from pipeline import DAY_MS
        p = self.pipe()
        edge = self.register_all(p, [tp.EDGE] + tp.NOISE)[0]
        orig = p._validation_events
        _ev, v0, v1 = orig(edge, "BTCUSDT")
        bars = p.provider.symbol("BTCUSDT")["bars"]
        window_days = bars[v1].ts // DAY_MS - bars[v0].ts // DAY_MS + 1
        lo = bars[v0].ts + (bars[v1].ts - bars[v0].ts) // 4
        hi = bars[v0].ts + 3 * (bars[v1].ts - bars[v0].ts) // 4

        def sparse(sid, sym):           # trades only in the middle half of the window
            ev, a, b = orig(sid, sym)
            return [x for x in ev if lo <= x["exit_ts"] <= hi], a, b
        with mock.patch.object(p, "_validation_events", sparse):
            p.run_validation()
        rec = p.evaluation("robustness", edge)
        self.assertIsNotNone(rec)
        self.assertGreaterEqual(rec["checks"]["bootstrap_ci"]["n_days"], window_days)


# ---------------------------------------------------------------------------
# B — evolution / search
# ---------------------------------------------------------------------------

def _retz_spec(mh):
    from strategy_spec import SPEC_SCHEMA
    return {"schema": SPEC_SCHEMA, "direction": "long", "symbols": ["BTCUSDT"],
            "entry": {"logic": "all", "conditions": [
                {"feature": "hour_utc", "params": {}, "op": "between", "value": [0, 2]}]},
            "filters": [{"feature": "ret_z", "params": {"lookback": 4, "window": 64, "min_history": mh},
                         "op": "gt", "value": -8.0}],
            "exit": {"horizon": 8}}


def _fng_spec(feat):
    from strategy_spec import SPEC_SCHEMA
    return {"schema": SPEC_SCHEMA, "direction": "long", "symbols": ["BTCUSDT"],
            "entry": {"logic": "all", "conditions": [{"feature": feat, "params": {"days": 1},
                                                      "op": "le", "value": 20.0}]},
            "filters": [], "exit": {"horizon": 16}}


class TestEvolution(unittest.TestCase):
    def test_b01_min_history_twins_share_behaviour(self):
        from evolution import behaviour_key, mutate
        a, b = _retz_spec(0), _retz_spec(40)
        self.assertEqual(behaviour_key(a), behaviour_key(b))
        for seed in range(200):
            rng = random.Random(seed)
            r = mutate(a, rng.choice(["tweak_param", "resample_param"]), rng, {"available_data": ["klines"]})
            if r:
                self.assertNotEqual(r[1].get("param"), "min_history")

    def test_b02_fng_days1_twins_share_behaviour(self):
        from evolution import behaviour_key
        self.assertEqual(behaviour_key(_fng_spec("fng_max")), behaviour_key(_fng_spec("fng_min")))

    def test_b06_negative_zero_canonical(self):
        from strategy_spec import _round
        self.assertEqual(json.dumps(_round(-0.0)), "0.0")


class TestSearchBudget(PCase):
    def _setup(self, name, cfg):
        from pipeline import EvolutionPipeline
        d = self.td / name
        p = EvolutionPipeline(d, self.prov, tp.verified_fee_cost(), symbols=["BTCUSDT"], search_cfg=cfg)
        for sp in [tp.EDGE] + tp.NOISE:
            sid, _ = p.register(sp, source="test")
            p.dev_evaluate(sid)
        return d

    def test_b05_budget_cut_never_drops_half_a_round(self):
        """B-05: the rest of a round was dropped and its index advanced."""
        from autoloop import run_evolution_cycle
        from search import SearchConfig
        cfg = SearchConfig(children_per_round=6, seed=3)
        cost = tp.verified_fee_cost()

        def r1(d):
            return sorted(r["child_id"] for r in iter_jsonl(d / "evolution" / "lineage.jsonl")
                          if r.get("round_index") == 1)
        a = self._setup("a", cfg)
        run_evolution_cycle(a, self.prov, cost, symbols=["BTCUSDT"], search_cfg=cfg, seed_legacy=False,
                            rounds=1, max_dev_evals=100, confirmatory=False)
        b = self._setup("b", cfg)
        run_evolution_cycle(b, self.prov, cost, symbols=["BTCUSDT"], search_cfg=cfg, seed_legacy=False,
                            rounds=1, max_dev_evals=2, confirmatory=False)
        run_evolution_cycle(b, self.prov, cost, symbols=["BTCUSDT"], search_cfg=cfg, seed_legacy=False,
                            rounds=1, max_dev_evals=100, confirmatory=False)
        self.assertTrue(r1(a))
        self.assertEqual(r1(a), r1(b))

    def test_b07_reseed_writes_no_duplicate_lineage(self):
        from collections import Counter
        from autoloop import run_evolution_cycle
        from search import SearchConfig
        d = self.td / "s"
        for seed in (3, 4):
            run_evolution_cycle(d, self.prov, tp.verified_fee_cost(), symbols=["BTCUSDT"],
                                search_cfg=SearchConfig(seed=seed), seed_legacy=True, rounds=0,
                                max_dev_evals=0, confirmatory=False)
        lin = list(iter_jsonl(d / "evolution" / "lineage.jsonl"))
        c = Counter((r["child_id"], tuple(r["parents"]), r["operator"]) for r in lin)
        self.assertTrue(lin)
        self.assertEqual([k for k, v in c.items() if v > 1], [])

    def test_b03_neighbours_do_not_use_the_search_budget(self):
        """B-03: robustness neighbours (which exist only because their centre passed
        VALIDATION) consumed max_specs_total: the search stopped after a validation."""
        from autoloop import run_evolution_cycle
        from search import SearchConfig
        cfg = SearchConfig(children_per_round=4, seed=3, max_specs_total=12)
        out = {}
        for with_validation in (False, True):
            d = self._setup(f"v{int(with_validation)}", cfg)
            if with_validation:
                from pipeline import EvolutionPipeline
                q = EvolutionPipeline(d, self.prov, tp.verified_fee_cost(), symbols=["BTCUSDT"], search_cfg=cfg)
                q.run_validation()
                self.assertGreater(len(q.specs.all()), len(q.search_pool()))   # neighbours exist
            s = run_evolution_cycle(d, self.prov, tp.verified_fee_cost(), symbols=["BTCUSDT"], search_cfg=cfg,
                                    seed_legacy=False, rounds=1, confirmatory=False)
            out[with_validation] = (s.search_status, s.rounds)
        self.assertEqual(out[True], out[False])
        self.assertNotEqual(out[True][0], "BUDGET_EXHAUSTED")

    def test_b04_child_equal_to_a_neighbour_is_screened(self):
        """B-04: the search proposing a spec that already exists as a neighbour
        registered it (created=False) without a DEV status: never screened."""
        import autoloop
        from autoloop import run_evolution_cycle
        from pipeline import EvolutionPipeline
        from search import SearchConfig
        cfg = SearchConfig(children_per_round=4, seed=3)
        d = self._setup("n", cfg)
        p = EvolutionPipeline(d, self.prov, tp.verified_fee_cost(), symbols=["BTCUSDT"], search_cfg=cfg)
        p.run_validation()
        nb = [r["strategy_id"] for r in iter_jsonl(p.specs.path) if r["source"].startswith("neighbour_of:")]
        self.assertTrue(nb)
        n = nb[0]
        parent = p.register(tp.EDGE, source="test")[0]

        def fake(**kw):
            return [{"spec": p.specs.get(n), "sid": n, "parents": [parent], "operator": "tweak_threshold",
                     "detail": {}}], {"attempts": 1, "invalid": 0, "duplicates": 0, "semantic_duplicates": 0,
                                      "near_duplicates": 0, "dedup_log": []}
        with mock.patch.object(autoloop, "propose_round", fake):
            run_evolution_cycle(d, self.prov, tp.verified_fee_cost(), symbols=["BTCUSDT"], search_cfg=cfg,
                                seed_legacy=False, rounds=1, confirmatory=False)
        p2 = EvolutionPipeline(d, self.prov, tp.verified_fee_cost(), symbols=["BTCUSDT"], search_cfg=cfg)
        if n in p2.search_pool():
            self.assertIsNotNone(p2.statuses.status(n, p2.context_id()))
            self.assertIsNotNone(p2.evaluation("dev", n))


# ---------------------------------------------------------------------------
# E — portfolio
# ---------------------------------------------------------------------------

def _pm_bars(rows, t0, step=MS15, qv=1e9):
    return [Bar(ts=t0 + i * step, open=o, high=h, low=lo, close=c, volume=1, quote_volume=qv)
            for i, (o, h, lo, c) in enumerate(rows)]


class TestPortfolioMulti(unittest.TestCase):
    T0 = 1_700_000_000_000 // H8 * H8

    def _f(self, bars):
        t, out = bars[0].ts - H8, []
        while t <= bars[-1].ts + H8:
            out.append((t, 0.0))
            t += H8
        return out

    def _cost(self):
        from costs_bridge import CostContext
        return CostContext.from_params({"fee_rate": 0.0, "half_spread": 0.0, "impact_coefficient": 0.0,
                                        "max_participation": 1.0}, version="TEST_FEE", verified=True,
                                       test_only=True)

    def _tr(self, m, side, d, e, x, px=None, why="time"):
        T0 = self.T0
        return {"strategy": m, "symbol": "A", "side": side, "decision_ts": T0 + d * MS15,
                "entry_ts": T0 + e * MS15, "exit_ts": T0 + x * MS15, "exit_price": px, "exit_reason": why}

    def test_e04_stop_beyond_liquidation_is_a_liquidation(self):
        import portfolio_multi as pm
        flat = (100, 100, 100, 100)
        bars = _pm_bars([flat, flat, (100, 100, 60, 61), flat], self.T0)
        f = self._f(bars)
        res = pm.simulate_multi({"A": bars}, [self._tr("m", 1, 0, 1, 2, px=62.0, why="stop")], {"A": f},
                                self._cost(), pm.MultiConfig(leverage=3.0, max_gross_exposure=3.0),
                                members=["m"], funding_intervals_by_symbol={"A": {t: H8 for t, _ in f}})
        self.assertEqual(res["liquidation_events"], 1)

    def test_e05_remainder_of_a_refused_primary_never_enters(self):
        import portfolio_multi as pm
        flat = (100, 100, 100, 100)
        bars = _pm_bars([flat] * 8, self.T0)
        f = self._f(bars)
        trades = [self._tr("a", 1, 0, 1, 2), self._tr("b", 1, 0, 1, 6)]
        res = pm.simulate_multi({"A": bars}, trades, {"A": f}, self._cost(),
                                pm.MultiConfig(max_concurrent=1, fill_ratio=0.5), members=["a", "b"],
                                funding_intervals_by_symbol={"A": {t: H8 for t, _ in f}})
        acc = res["signal_accounting"]
        self.assertEqual(res["metrics"]["strategy_contribution"]["b"]["entries"], 0)
        self.assertGreaterEqual(acc["remainder_legs"]["skipped_by_reason"]["primary_not_entered"], 1)

    def test_e06_missing_refusal_share_is_undetermined(self):
        """E-06: a refusal-share limit was skipped when the accounting lacked the rate."""
        import portfolio_multi as pm
        ok = {"status": "OK", "value": 0.01}
        res = {"trades_closed": 50, "cost_status": "OK", "final_equity": 11_000.0, "initial_capital": 10_000.0,
               "liquidation_events": 0, "signal_accounting": {"status": "OK"},
               "metrics": {"span_days": 120, "max_drawdown": {"value": 0.01}, "max_drawdown_close": ok}}
        self.assertEqual(pm.judge(res, {"min_trades": 1})[0], "PASS_PORTFOLIO")       # control
        verdict, why = pm.judge(res, {"min_trades": 1, "max_capital_refusal_share": 0.1})
        self.assertEqual(verdict, "UNDETERMINED_PORTFOLIO")
        self.assertTrue(any("refusal share unknown" in w for w in why), why)


class TestLegacyPortfolio(unittest.TestCase):
    def _run(self, prices, opens=None, funding=None, ivs=None, leverage=1.0, horizon=60):
        from costs_bridge import CostContext
        from portfolio import PortfolioConfig, simulate_portfolio
        bars = [Bar(ts=_ms("2024-01-01") + i * MS15, open=(opens[i] if opens and opens[i] else p),
                    high=max(p, opens[i] if opens and opens[i] else p) * 1.0001,
                    low=min(p, opens[i] if opens and opens[i] else p) * 0.9999, close=p,
                    volume=1e9, quote_volume=1e12) for i, p in enumerate(prices)]
        cost = CostContext.from_params({"fee_rate": 0.0, "half_spread": 0.0, "impact_coefficient": 0.0,
                                        "max_participation": 1.0}, version="TEST_ZERO", test_only=True)
        kw = {} if ivs is None else {"funding_intervals_by_symbol": ivs}
        return simulate_portfolio({"A": bars}, [{"symbol": "A", "decision_index": 4, "side": 1, "horizon": horizon}],
                                  {"A": funding if funding is not None else []}, cost,
                                  PortfolioConfig(initial_capital=10_000.0, size_fraction=1.0,
                                                  leverage=leverage, max_concurrent=2), **kw)

    def test_e02_missing_funding_is_not_charged_zero_silently(self):
        """E-02: an empty funding series charged 0 and the portfolio could pass."""
        from portfolio import portfolio_passes
        res = self._run([100.0] * 80)
        self.assertNotEqual(getattr(res, "funding_status", "OK"), "OK")
        ok, why = portfolio_passes(res.to_json())
        self.assertFalse(ok)
        self.assertTrue(any("funding status" in w for w in why), why)
        t0 = _ms("2024-01-01")
        fund = [(t0 + k * H8, 0.0) for k in range(-2, 8)]
        res2 = self._run([100.0] * 80, funding=fund, ivs={"A": {t: H8 for t, _ in fund}})
        self.assertEqual(res2.funding_status, "OK")

    def test_e03_open_beyond_liquidation_liquidates(self):
        """E-03: a bar OPENING beyond the liquidation price was exited at that open
        (loss larger than the margin, no liquidation)."""
        prices = [100.0] * 10 + [40.0] * 70
        opens = [None] * 10 + [40.0] + [None] * 69
        for horizon in (5, 60):           # time exit AT the gap open, and a later one
            res = self._run(prices, opens=opens, leverage=2.0, horizon=horizon)
            self.assertEqual(res.liquidation_events, 1, horizon)
            self.assertGreaterEqual(res.final_equity if hasattr(res, "final_equity") else 0.0, 0.0)


# ---------------------------------------------------------------------------
# F — state / ledger / recovery / concurrency
# ---------------------------------------------------------------------------

class TestLegacyRunnerRecovery(TmpCase):
    def test_f01_crash_after_locked_test_keeps_the_real_result(self):
        """F-01: restart finalised the candidate as FAIL_STAT 'already consumed'."""
        import ledger_check
        import test_engine
        T = test_engine.TestPromotion
        data = self.td / "data"
        T._write_btc_dataset(T, data, lock_drift=0.0005)
        r, sid = T._runner_one(T, str(self.td), data)
        r.register_all()
        r.run_one(sid, r._defn_by_sid[sid])
        orig = r.ledger.append

        def append(rec):
            if rec.get("phase") == "final":
                raise Crash("kill between registry.complete and ledger.append")
            return orig(rec)
        r.ledger.append = append
        with self.assertRaises(Crash):
            r.finalize_fdr()
        truth = r.lock_registry.result(sid, "BTCUSDT")
        r2, _ = T._runner_one(T, str(self.td), data)
        r2.cycle(max_candidates=5)
        rec = r2.ledger.latest_by_strategy()[sid]
        self.assertEqual(rec["status"], truth["status"])
        self.assertEqual(r2.store.entry(sid).state, truth["status"])
        self.assertEqual([x["kind"] for x in r2.campaigns.records()].count("RESULT"), 1)
        self.assertEqual(ledger_check.check(self.td / "state")["status"], "CONSISTENT")

    def test_f05_reconcile_requeues_a_recovered_reverify_outcome(self):
        """F-05: a DATA_MISSING outcome recovered from the ledger was not queued."""
        import test_engine
        from costs_bridge import FUTURES_COST_DEFAULTS, CostContext
        from engine_lib import CandidateState, normalize_strategy, strategy_id
        from runner import PathConfig, Runner
        from store import reconcile
        T = test_engine.TestPromotion
        defn = dict(T.TOD_DEFN, symbols=["ETHUSDT"], primary_cell={"symbol": "ETHUSDT", "horizon": 16})
        td = self.td
        (td / "data" / "klines").mkdir(parents=True)

        def runner():
            paths = PathConfig(root=td / "disc", data_dir=td / "data", state_dir=td / "state",
                               results_dir=td / "results", audit_dir=td / "audit")
            cost = CostContext.from_params(FUTURES_COST_DEFAULTS, version="T", verified=True, test_only=True)
            sid = strategy_id(defn)
            return Runner(paths, cost=cost, allow_test_cost=True,
                          candidates_fn=lambda: ([(sid, normalize_strategy(defn))], [])), sid
        r, sid = runner()
        r.register_all()
        orig = r.store.set

        def set_(s, nxt, **kw):
            if CandidateState(nxt) is not CandidateState.RUNNING:
                raise Crash("kill")
            return orig(s, nxt, **kw)
        r.store.set = set_
        with self.assertRaises(Crash):
            r.run_one(sid, r._defn_by_sid[sid])
        r.restore_signal_handlers()
        r2, _ = runner()
        r2.register_all()
        rep = reconcile(r2.store, r2.queue, r2.ledger)
        self.assertIn(sid, rep["recovered_from_ledger"])
        self.assertIn(sid, [i["strategy_id"] for i in r2.queue.items()])
        r2.restore_signal_handlers()


class TestPipelineRecovery(PCase):
    def _validated(self):
        p = self.pipe()
        ids = []
        for sp in [tp.EDGE] + tp.NOISE:
            sid, _ = p.register(sp, source="test")
            p.dev_evaluate(sid)
            ids.append(sid)
        return p, ids[0]

    def test_f02_campaign_resume_after_crash_is_idempotent(self):
        """F-02: duplicate locked record + review, InvalidStatusTransition on every restart."""
        import ledger_check
        p, edge = self._validated()
        p.run_validation()
        p.campaigns.record_result = mock.Mock(side_effect=Crash("kill"))
        with self.assertRaises(Crash):
            p.run_locked_campaign()
        for _ in (1, 2):
            self.pipe().run_locked_campaign()
        locked = [r for r in iter_jsonl(p.dir / "evaluations.jsonl") if r["stage"] == "locked"]
        reviews = list(iter_jsonl(p.dir / "promotion_reviews.jsonl"))
        self.assertEqual(len(locked), 1)
        self.assertEqual(len(reviews), 1)
        kinds = [r["kind"] for r in iter_jsonl(self.td / "state" / "locked_window_campaigns.jsonl")]
        self.assertEqual(kinds.count("RESULT"), 1)
        self.assertEqual(ledger_check.check(self.td / "state")["status"], "CONSISTENT")

    def test_f04_robustness_status_resumed_after_crash(self):
        """F-04: crash between the robustness record and the status: RESEARCH_ONLY forever."""
        p, edge = self._validated()
        ctx = p.context_id()
        orig = p.statuses.set

        def set_(sid, c, status, **kw):
            if status == "VALIDATION_SURVIVOR" and kw.get("stage") == "robustness":
                raise Crash("kill")
            return orig(sid, c, status, **kw)
        p.statuses.set = set_
        with self.assertRaises(Crash):
            p.run_validation()
        p2 = self.pipe()
        self.assertIn(edge, p2.resume_pending_robustness())
        self.assertEqual(p2.statuses.status(edge, ctx), "VALIDATION_SURVIVOR")

    def test_f03_degraded_replay_survives_a_crash(self):
        """F-03: crash after paper_runs append: PAPER_TESTING stayed, forward plan possible."""
        import paper
        from paper_forward import ForwardPaper, ForwardRefused
        p, edge = self._validated()
        ctx = p.context_id()
        p.run_validation()
        p.run_locked_campaign()
        if p.statuses.status(edge, ctx) != "PAPER_TEST_READY":
            self.skipTest("edge not PAPER_TEST_READY on this fixture")
        lock = self.prov._sym["BTCUSDT"]["lock"]
        bad = tp.synth_bars("2024-06-20", "2024-08-17", seed=999, drift_hours=(0, 1, 2), mu=-0.004,
                            price0=lock[-1].close)
        self.prov._sym["BTCUSDT"]["paper"] = bad
        orig = p._append_eval

        def ae(rec):
            if rec["stage"] == "paper":
                raise Crash("kill")
            return orig(rec)
        p._append_eval = ae
        with self.assertRaises(Crash):
            paper.run_paper(p, edge)
        p2 = self.pipe()
        with self.assertRaises(paper.PaperRefused):
            paper.run_paper(p2, edge)
        self.assertEqual(p2.statuses.status(edge, ctx), "PAPER_FAILED")
        self.assertEqual(p2.evaluation("paper", edge)["verdict"], "PAPER_DEGRADED")
        fp = ForwardPaper(p2, mock.Mock(), root=self.td / "fp") if "root" in \
            ForwardPaper.__init__.__code__.co_varnames else ForwardPaper(p2, mock.Mock())
        with self.assertRaises(ForwardRefused):
            fp.register_plan(edge)


class TestRegistries(TmpCase):
    REG = dict(window_key="K", prereg_sha256="p", members_sha256="m", members={"S1": "h"},
               code_hash="c", config={"a": 1}, cost={"v": 1})

    def test_f07_validation_registry_tolerates_a_torn_tail(self):
        import validation_registry as vr
        p = self.td / "validation_registry.jsonl"
        r = vr.ValidationRegistry(p)
        r.register("R1", **self.REG)
        r.start("R1", members_sha256="m", code_hash="c")
        with open(p, "a") as f:
            f.write('{"schema": "validation_registry_v1", "run_id": "R1", "event": "COMPL')
        self.assertEqual(r.state("R1"), "RUNNING")
        r.complete("R1", members_sha256="m", code_hash="c", result_sha256="x", outcomes={"S1": "REJECTED"})
        self.assertEqual(vr.ValidationRegistry(p).state("R1"), "COMPLETED")

    def test_f07_validation_registry_interior_corruption_fails_closed(self):
        import validation_registry as vr
        p = self.td / "validation_registry.jsonl"
        r = vr.ValidationRegistry(p)
        r.register("R1", **self.REG)
        r.register("R2", **dict(self.REG, window_key="K2"))
        lines = p.read_text().splitlines()
        lines[0] = lines[0][:-5] + "xx"
        p.write_text("\n".join(lines) + "\n")
        with self.assertRaises(vr.RegistryCorrupt):
            vr.ValidationRegistry(p).runs()

    def test_f07_validation_registry_writes_are_serialised(self):
        """Two writers: the second waits for the lock (a child process holds it)."""
        import validation_registry as vr
        p = self.td / "validation_registry.jsonl"
        vr.ValidationRegistry(p).register("R1", **self.REG)
        code = ("import fcntl,sys,time;f=open(sys.argv[1],'a+');fcntl.flock(f,fcntl.LOCK_EX);"
                "print('held',flush=True);time.sleep(1.5)")
        lock = Path(str(p) + ".lock")
        child = subprocess.Popen([sys.executable, "-c", code, str(lock)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            t = time.monotonic()
            vr.ValidationRegistry(p).start("R1", members_sha256="m", code_hash="c")
            self.assertGreater(time.monotonic() - t, 0.5)
        finally:
            child.wait()
        self.assertEqual(vr.ValidationRegistry(p).state("R1"), "RUNNING")

    def test_f06_iw_open_crash_after_history_row_needs_resume(self):
        """F-06: a crash between the history row and reg.start burned the tranche."""
        import independent_window as iw
        import prereg_gate
        import validation_registry as vr
        pre = {"window_id": "IWX", "period": {"entry_start": "2020-01-01"}, "tranches": [{"id": "A"}]}
        members = {"members_sha256": "m", "members": [], "context_id": "ctx"}
        reg = vr.ValidationRegistry(self.td / "reg.jsonl")
        rid = iw.run_id_for(pre, "sha", "A", members)
        reg.register(rid, **self.REG)
        append_jsonl(self.td / "hist.jsonl", {"run": "IWX-A", "window_keys": [iw.window_key(pre, "A")],
                                               "batch_ids": [rid]})
        with mock.patch.object(iw, "check_members", lambda *a, **k: None), \
                mock.patch.object(prereg_gate, "research_code_identity", lambda: {"code_hash": "c"}):
            with self.assertRaises(iw.WindowRefused) as cm:
                iw.open_tranche(pre, "sha", "A", self.td, members, self.td / "hist.jsonl", self.td / "out",
                                None, gate={"lifecycle": "ACTIVE", "prereg_sha256": "sha"},
                                registry_path=self.td / "reg.jsonl", resume=False)
        self.assertIn("--resume", str(cm.exception))
        self.assertEqual(len(list(iter_jsonl(self.td / "hist.jsonl"))), 1)
        self.assertIn(rid, reg.summary()["registered_not_started"])

    def test_f08_promotion_queue_tolerates_a_torn_tail(self):
        from promotion import PromotionQueue
        q = self.td / "promotion_queue.jsonl"
        q.write_text('{"strategy_id": "A", "eligible": true}\n{"strategy_id": "B", "elig')
        self.assertEqual(PromotionQueue(q).duplicates("C"), 0)

    def test_f09_process_lock_liveness(self):
        from engine_lib import ProcessLock, process_lock_status
        lk = self.td / "run.lock"
        p = subprocess.Popen(["true"])
        p.wait()
        lk.write_text(str(p.pid))
        a = ProcessLock(lk)
        self.assertTrue(a.acquire())
        a.release()
        lk.write_text(str(os.getpid()))          # leftover of an earlier run with our PID
        b = ProcessLock(lk)
        self.assertTrue(b.acquire())
        self.assertEqual(process_lock_status(lk)["status"], "HELD")
        self.assertFalse(ProcessLock(lk).acquire())   # a held lock is never taken twice
        b.release()
        self.assertEqual(process_lock_status(lk)["status"], "FREE")
        with self.assertRaises(RuntimeError):
            with ProcessLock(lk):
                raise RuntimeError("boom")
        self.assertFalse(lk.exists())

    def test_f10_prior_pass_reeval_is_not_the_latest_outcome(self):
        import ledger_check
        from engine_lib import CandidateState as C
        from store import CandidateStore, Ledger
        st = CandidateStore(self.td / "state.json")
        for s in (C.PENDING, C.RUNNING, C.AWAITING_FDR, C.PASS):
            st.set("S1", s)
        L = Ledger(self.td / "discovery_ledger.jsonl")
        L.append({"evaluation_id": "E1", "strategy_id": "S1", "phase": "dev", "status": "AWAITING_FDR",
                  "context_id": "C1"})
        L.append({"evaluation_id": "E2", "strategy_id": "S1", "phase": "final", "status": "PASS",
                  "context_id": "C1"})
        L.append({"evaluation_id": "E3", "strategy_id": "S1", "phase": "dev", "status": "FAIL_STAT",
                  "context_id": "C2", "prior_pass_reeval": True})
        self.assertEqual(L.latest_by_strategy()["S1"]["status"], "PASS")
        self.assertEqual(ledger_check.check(self.td)["status"], "CONSISTENT")


# ---------------------------------------------------------------------------
# G — Paper Forward
# ---------------------------------------------------------------------------

from test_paper_forward_positions_20261111 import MIN15, PositionCase  # noqa: E402


class TestPaperForwardGap(PositionCase):
    def test_g01_gap_after_cutoff_never_passes(self):
        """G-01 (CRITICAL): a bar missing after the cutoff inside a pending losing
        position removed it from the sample -> PAPER_PASSED."""
        p, edge, feed, fp, plan, r, entry = self.open_at_end(crash=0.7)
        self.assertEqual(r["verdict"], "PAPER_OBSERVING")
        gone = entry + 4 * MIN15
        feed.bars[:] = [b for b in feed.bars if b.ts != gone]
        self.at(plan, 71, 0)
        r2 = fp.step(plan["plan_id"])
        self.assertNotEqual(r2["verdict"], "PAPER_PASSED")
        self.assertEqual(r2["verdict"], "PAPER_BLOCKED_DATA")
        self.assertTrue(any("UNOBSERVABLE_SAMPLE_TRADES" in x for x in r2["reasons"]), r2["reasons"])
        self.assertNotEqual(p.statuses.status(edge, p.context_id()), "PAPER_PASSED")

    def test_g01_control_without_gap_fails(self):
        p, edge, feed, fp, plan, r, entry = self.open_at_end(crash=0.7)
        self.at(plan, 71, 0)
        self.assertEqual(fp.step(plan["plan_id"])["verdict"], "PAPER_FAILED")


# ---------------------------------------------------------------------------
# H — ops / CI
# ---------------------------------------------------------------------------

class TestOps(TmpCase):
    def test_h01_loop_stops_on_sigterm_between_cycles(self):
        import ops
        calls = []

        def main(args):
            calls.append(1)
            return 0

        def sleep(sec):
            os.kill(os.getpid(), signal.SIGTERM)
        before = signal.getsignal(signal.SIGTERM)
        res = ops.run_loop(main, ["--mode", "legacy"], state_dir=self.td, max_cycles=3, interval=60, sleep=sleep)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res.get("stopped") or res.get("stop") or res.get("status"), "INTERRUPTED")
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    def test_h01_runner_restores_signal_handlers(self):
        import test_engine
        T = test_engine.TestPromotion
        data = self.td / "data"
        (data / "klines").mkdir(parents=True)
        before = signal.getsignal(signal.SIGTERM)
        r, _sid = T._runner_one(T, str(self.td), data)
        self.assertIsNot(signal.getsignal(signal.SIGTERM), before)
        r.restore_signal_handlers()
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    def test_h05_discover_never_ok_is_a_failure(self):
        import newlisting_collect as nc

        class E(Exception):
            code = 403

        def opener(url):
            raise E("HTTP Error 403: Forbidden")
        t = [0.0]

        def sleep(s):
            t[0] += max(s, 1.0)
        r = nc.collect(self.td, hours=1, allow_network=True, opener=opener, sleep=sleep, clock=lambda: t[0])
        self.assertIn(r["stopped"], ("DISCOVER_FAILING", "NO_SUCCESSFUL_DISCOVER"))

    def test_h09_fetch_rejects_path_like_inputs(self):
        import data_fetch
        for sym, a, b in (("../x", "2024-01", "2024-02"), ("BTC/USDT", "2024-01", "2024-02"),
                          ("BTCUSDT", "2024-01/../", "2024-02"), ("BTCUSDT", "2024-01", "2024-2")):
            with self.assertRaises(ValueError):
                data_fetch.fetch_missing(self.td, [sym], a, b)
        self.assertEqual(data_fetch.fetch_missing(self.td, ["BTCUSDT"], "2024-01", "2024-02")["status"],
                         "DISABLED")


class TestRunnerExitCodes(TmpCase):
    def setUp(self):
        super().setUp()
        (self.td / "data").mkdir()
        vh = self.td / "vh.jsonl"
        vh.write_text("")
        env = dict(NEW2_DATA_DIR=str(self.td / "data"), NEW2_DISCOVERY_ROOT=str(self.td),
                   NEW2_VALIDATION_HISTORY=str(vh))
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        self._sig = signal.getsignal(signal.SIGTERM)

    def tearDown(self):
        signal.signal(signal.SIGTERM, self._sig)
        self._env.stop()
        super().tearDown()

    def _main(self, cycle, evo_status):
        import autoloop
        import runner
        ran = []

        def fake_evo(state_dir, provider, cost, **kw):
            ran.append(1)
            return autoloop.EvolutionCycleSummary(status=evo_status)
        with mock.patch.object(runner.Runner, "cycle", cycle), \
                mock.patch.object(autoloop, "run_evolution_cycle", fake_evo), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = runner.main(["--mode", "full"])
        return rc, bool(ran)

    def test_h02_interrupted_legacy_stops_full_mode(self):
        import runner

        def cyc(self, max_candidates=10):
            os.kill(os.getpid(), signal.SIGTERM)
            return runner.CycleSummary(status="INTERRUPTED" if self._interrupted else "SUCCESS")
        rc, ran = self._main(cyc, "CYCLE_OK")
        self.assertEqual(rc, 1)
        self.assertFalse(ran)
        self.assertIs(signal.getsignal(signal.SIGTERM), self._sig)

    def test_h03_failure_is_not_masked_by_a_higher_code(self):
        import runner

        def cyc(self, max_candidates=10):
            return runner.CycleSummary(status="FAILED")
        rc, ran = self._main(cyc, "CYCLE_BLOCKED_DATA")
        self.assertEqual(rc, 1)


class TestSourceAuditFreshness(TmpCase):
    def _mod(self):
        path = DISC.parent / "audit" / "source_audit_20261110.py"
        spec = importlib.util.spec_from_file_location("source_audit_t", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_h06_changed_file_must_be_marked(self):
        m = self._mod()
        f = self.td / "x.py"
        f.write_text("a = 1\n")
        row = json.loads(m.OUT_JSONL.read_text(encoding="utf-8").splitlines()[0])
        row.pop("changed_since_review", None)
        row.update(file="x.py", lines_read=1, lines_total=1,
                   reviewed_against={"commit": "c", "sha256": m._sha(f.read_bytes())})
        out = self.td / "t.jsonl"
        out.write_text(json.dumps(row) + "\n")
        with mock.patch.object(m, "OUT_JSONL", out), mock.patch.object(m, "_path", lambda name: self.td / name):
            self.assertFalse([e for e in m.check() if "x.py" in e and "changed since the review" in e])
            f.write_text("a = 2\n")
            self.assertTrue([e for e in m.check() if "x.py" in e and "changed since the review" in e])


if __name__ == "__main__":
    unittest.main()
