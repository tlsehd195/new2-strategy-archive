"""Public-market cost estimate (ADR 0007): collector safety, estimator
recovery on a synthetic book, record integrity, and the engine's handling of
ESTIMATED_FROM_PUBLIC_DATA (labelled, never promotable).  No network."""

from __future__ import annotations

import json
import math
import os
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

import public_cost_estimate as pce  # noqa: E402
import public_market_collect as pmc  # noqa: E402
from costs_bridge import (CostConfigError, CostContext, ESTIMATED_FROM_PUBLIC_DATA,  # noqa: E402
                          UNVERIFIED_COST_MODEL)
from engine_lib import iter_jsonl  # noqa: E402
from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider  # noqa: E402

HALF = 0.00005          # true half spread (0.5bp)
QV = 50_000_000.0       # last closed 15m quote volume


def synth_book(mid, *, half=HALF, level_notional=200_000.0, tick=0.0001, levels=50):
    bid0, ask0 = mid * (1 - half), mid * (1 + half)
    bids = [[f"{bid0 * (1 - tick * i):.6f}", f"{level_notional / (bid0 * (1 - tick * i)):.6f}"]
            for i in range(levels)]
    asks = [[f"{ask0 * (1 + tick * i):.6f}", f"{level_notional / (ask0 * (1 + tick * i)):.6f}"]
            for i in range(levels)]
    return bids, asks


def synth_lines(symbol, *, hours, interval_s=60, seed=1, start_ms=1_790_000_000_000, **kw):
    rnd = random.Random(seed)
    mid, out = 50_000.0, []
    for i in range(int(hours * 3600 / interval_s)):
        mid *= 1 + rnd.gauss(0, 0.0002)
        bids, asks = synth_book(mid, **kw)
        t = start_ms + i * interval_s * 1000
        out.append({"schema": pmc.SCHEMA, "symbol": symbol, "fetched_at_ms": t, "status": "OK",
                    "depth": {"bids": bids, "asks": asks},
                    "kline_closed": {"quote_volume": str(QV), "close_time": t - 1}})
    return out


def write_dir(td: Path, per_symbol: dict) -> Path:
    for sym, lines in per_symbol.items():
        d = td / sym
        d.mkdir(parents=True, exist_ok=True)
        (d / "public_20261016.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    return td


class TestCollector(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)

    def test_only_public_endpoints_and_no_auth(self):
        self.assertTrue(pmc.public_url("/fapi/v1/depth", symbol="BTCUSDT", limit=100)
                        .startswith("https://fapi.binance.com/fapi/v1/depth?"))
        for path in ("/fapi/v1/order", "/fapi/v2/account", "/fapi/v1/userTrades", "/sapi/v1/x"):
            with self.subTest(path=path), self.assertRaises(pmc.CollectRefused):
                pmc.public_url(path)
        for k in ("signature", "timestamp", "apiKey", "recvWindow"):
            with self.subTest(k=k), self.assertRaises(pmc.CollectRefused):
                pmc.public_url("/fapi/v1/depth", symbol="BTCUSDT", **{k: "x"})
        src = (DISC / "public_market_collect.py").read_text()
        self.assertNotIn("X-MBX-APIKEY", src)

    def test_network_is_opt_in_and_run_is_bounded(self):
        with self.assertRaises(pmc.CollectRefused):
            pmc.collect(self.td, ["BTCUSDT"], hours=1)
        with self.assertRaises(ValueError):
            pmc.collect(self.td, ["BTCUSDT"], hours=0, allow_network=True)
        with self.assertRaises(ValueError):
            pmc.collect(self.td, ["BTCUSDT"], hours=1, interval=1, allow_network=True)

    def test_collect_with_fake_exchange(self):
        now = [1_790_000_000.0]
        bids, asks = synth_book(50_000.0, levels=100)
        urls = []

        def opener(url):
            urls.append(url)
            if "/depth" in url:
                return json.dumps({"E": 1, "T": 1, "lastUpdateId": 7, "bids": bids, "asks": asks}).encode()
            if "/klines" in url:
                t = int(now[0] * 1000)
                return json.dumps([[t - 1_800_000, "1", "2", "0.5", "1.5", "9", t - 900_001, "123.0"],
                                   [t - 900_000, "1", "2", "0.5", "1.5", "9", t + 899_999, "55.0"]]).encode()
            return json.dumps([{"a": 1, "p": "50000", "q": "0.1", "f": 1, "l": 1, "T": 5, "m": True}]).encode()

        def sleep(s):
            now[0] += max(s, 1.0)

        r = pmc.collect(self.td, ["BTCUSDT", "ETHUSDT"], hours=0.05, interval=30, allow_network=True,
                        opener=opener, sleep=sleep, clock=lambda: now[0])
        self.assertEqual(r["stopped"], "DURATION_REACHED")
        self.assertGreater(r["counts"]["BTCUSDT"]["OK"], 3)
        recs = list(iter_jsonl(next((self.td / "BTCUSDT").glob("public_*.jsonl"))))
        self.assertEqual(len(recs[0]["depth"]["bids"]), pmc.DEPTH_LEVELS)
        self.assertEqual(recs[0]["kline_closed"]["quote_volume"], "123.0")   # the CLOSED bar only
        self.assertTrue(all("fapi.binance.com" in u for u in urls))
        man = json.loads((self.td / "collect_manifest.json").read_text())
        self.assertEqual(len(man["files"]), 2)

    def test_exchange_refusal_is_recorded_not_invented(self):
        class E(Exception):
            code = 451

        def opener(url):
            raise E("restricted location")

        rec = pmc.snapshot("BTCUSDT", opener=opener, sleep=lambda s: None, now_ms=1)
        self.assertEqual((rec["status"], rec["failure_kind"]), ("FETCH_FAILED", "EXCHANGE_ERROR"))
        self.assertNotIn("depth", rec)


class TestEstimator(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)

    def test_walk(self):
        self.assertAlmostEqual(pce.walk([(100.0, 1.0), (101.0, 10.0)], 100.0), 100.0)
        self.assertGreater(pce.walk([(100.0, 1.0), (101.0, 10.0)], 302.0), 100.0)
        self.assertIsNone(pce.walk([(100.0, 1.0)], 1_000.0))

    def test_recovers_spread_and_is_conservative(self):
        d = write_dir(self.td, {s: synth_lines(s, hours=80, seed=i + 1)
                                for i, s in enumerate(["BTCUSDT", "ETHUSDT"])})
        rec = pce.estimate(d)
        self.assertEqual(rec["status"], pce.ESTIMATED, rec["reasons"])
        bt = rec["per_symbol"]["BTCUSDT"]
        self.assertAlmostEqual(bt["half_spread_bp"]["median"], HALF * 1e4, places=2)
        # parameter = spread quantile + latency allowance >= the true half spread
        self.assertGreater(rec["params"]["half_spread"], HALF)
        self.assertGreater(rec["params"]["impact_coefficient"], 0)
        # 1M notional walks 5 levels of 200k: cost clearly above 1k notional
        c = bt["cost_per_side_bp_by_notional"]
        self.assertGreater(c["1000000"]["median"], c["1000"]["median"])
        self.assertEqual(rec["fee_basis"]["observed"], False)
        self.assertEqual(rec["params"]["fee_rate"], 0.0005)
        pce.check_estimate(json.loads(json.dumps(rec)))         # survives a JSON round trip

    def test_insufficient_data(self):
        d = write_dir(self.td, {"BTCUSDT": synth_lines("BTCUSDT", hours=10)})
        rec = pce.estimate(d)
        self.assertEqual(rec["status"], pce.INSUFFICIENT)
        self.assertIsNone(rec["params"])
        self.assertTrue(any("ETHUSDT" in r for r in rec["reasons"]))
        self.assertTrue(any("10.0h < 72h" in r for r in rec["reasons"]))
        with self.assertRaises(pce.EstimateError):
            pce.check_estimate(rec)

    def test_invalid_books_and_corrupt_lines(self):
        lines = synth_lines("BTCUSDT", hours=80)
        for x in lines[:200]:
            x["depth"]["bids"], x["depth"]["asks"] = x["depth"]["asks"], x["depth"]["bids"]  # crossed
        d = write_dir(self.td, {"BTCUSDT": lines, "ETHUSDT": synth_lines("ETHUSDT", hours=80)})
        rec = pce.estimate(d)
        self.assertEqual(rec["per_symbol"]["BTCUSDT"]["snapshots_invalid"], 200)
        self.assertEqual(rec["status"], pce.INSUFFICIENT)           # 200/4800 > 1 %
        with open(d / "ETHUSDT" / "public_20261016.jsonl", "a") as f:
            f.write("{torn\n")
        with self.assertRaises(pce.EstimateError):
            pce.estimate(d)

    def test_tampered_record_refused(self):
        d = write_dir(self.td, {s: synth_lines(s, hours=80) for s in ["BTCUSDT", "ETHUSDT"]})
        rec = pce.estimate(d)
        rec["params"]["half_spread"] = 0.0
        with self.assertRaises(pce.EstimateError):
            pce.check_estimate(rec)
        with self.assertRaises(CostConfigError):
            CostContext.from_public_estimate(rec)


class TestEngineUse(PipelineCase):
    def _record(self, **params):
        d = write_dir(self.td / "pm", {s: synth_lines(s, hours=80) for s in ["BTCUSDT", "ETHUSDT"]})
        rec = pce.estimate(d)
        if params:                     # a cheap estimate so the synthetic edge survives statistics
            rec["params"].update(params)
            rec["record_id"] = pce.record_hash(rec)
        return rec

    def test_context_is_estimated_and_never_paper_ready(self):
        rec = self._record(fee_rate=0.0002, half_spread=0.0, impact_coefficient=0.0)
        cost = CostContext.from_public_estimate(rec)
        self.assertEqual(cost.status, ESTIMATED_FROM_PUBLIC_DATA)
        self.assertFalse(cost.verified)
        self.assertEqual(cost.verified_for(["BTCUSDT"], 10_000), (False, [ESTIMATED_FROM_PUBLIC_DATA]))
        self.assertEqual(cost.identity()["assumption_profile"], rec["record_id"])
        p = self.pipe(SyntheticProvider(), cost=cost)
        ctx = p.context_id()
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        self.assertEqual(p.evaluation("dev", edge)["cost_model"]["status"], ESTIMATED_FROM_PUBLIC_DATA)
        p.run_validation()
        p.run_locked_campaign()
        self.assertEqual(p.statuses.status(edge, ctx), "BLOCKED_COST")
        rv = [r for r in iter_jsonl(p.review_path) if r["strategy_id"] == edge][-1]
        self.assertIn(ESTIMATED_FROM_PUBLIC_DATA, rv["labels"])
        self.assertIn(UNVERIFIED_COST_MODEL, rv["labels"])

    def test_runner_profile(self):
        import runner
        rec = self._record()
        path = self.td / "estimate.json"
        path.write_text(json.dumps(rec))
        with mock.patch.dict(os.environ, {"NEW2_COST_PROFILE": f"public_estimate:{path}"}):
            c = runner.cost_from_env()
        self.assertEqual(c.status, ESTIMATED_FROM_PUBLIC_DATA)
        self.assertTrue(math.isclose(c.params["half_spread"], rec["params"]["half_spread"]))
        with mock.patch.dict(os.environ, {"NEW2_COST_PROFILE": f"public_estimate:{self.td / 'none.json'}"}):
            with self.assertRaises(CostConfigError):
                runner.cost_from_env()


if __name__ == "__main__":
    unittest.main()
