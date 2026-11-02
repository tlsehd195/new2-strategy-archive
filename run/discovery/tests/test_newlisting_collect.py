"""Offline tests for newlisting_collect (ADR 0061): fake exchange, no network."""
import json
import sys
import tempfile
from pathlib import Path

import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import newlisting_collect as nc

T0 = 1_800_000_000_000


def sym(name, onboard):
    return {"symbol": name, "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING", "onboardDate": onboard}


class Fake:
    def __init__(self):
        self.symbols = [sym("BTCUSDT", T0 - 99 * nc.DAY_MS)]
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if "exchangeInfo" in url:
            return json.dumps({"symbols": self.symbols}).encode()
        if "depth" in url:
            return json.dumps({"E": 1, "T": 1, "lastUpdateId": 5, "bids": [["1", "2"]] * 60, "asks": [["2", "2"]] * 60}).encode()
        if "premiumIndex" in url:
            return json.dumps({"markPrice": "1", "indexPrice": "1", "lastFundingRate": "0.0001", "nextFundingTime": 9, "time": 1}).encode()
        if "klines" in url:
            return json.dumps([[T0 - 120000, "1", "2", "0.5", "1.5", "10", T0 - 60001, "15", 3]]).encode()
        if "aggTrades" in url:
            return json.dumps([{"a": 7, "p": "1", "q": "2", "T": T0 - 5, "m": True}]).encode()
        raise AssertionError(url)


def _raises(exc):
    return unittest.TestCase().assertRaises(exc)


class NewListingTests(unittest.TestCase):
    def test_url_allowlist_and_auth_refused(self):
        with _raises(nc.CollectRefused):
            nc.public_url("/fapi/v1/order")
        with _raises(nc.CollectRefused):
            nc.public_url("/fapi/v1/depth", signature="x")


    def test_baseline_not_tracked_then_new_listing_tracked(self):
        f, st = Fake(), {}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            assert nc.discover(out, st, T0, opener=f) == []
            assert st["known"] == ["BTCUSDT"] and st["tracked"] == {}
            f.symbols.append(sym("NEWUSDT", T0 - 1000))
            assert nc.discover(out, st, T0 + 60000, opener=f) == ["NEWUSDT"]
            ev = [json.loads(x) for x in (out / "listing_events.jsonl").read_text().splitlines()]
            assert ev[0]["event"] == "NEW_LISTING" and ev[0]["late_start"] is False
            assert nc.discover(out, st, T0 + 120000, opener=f) == []     # not detected twice


    def test_track_recent_days_marks_late_start(self):
        f = Fake()
        f.symbols.append(sym("OLDISHUSDT", T0 - 2 * nc.DAY_MS))
        st = {}
        with tempfile.TemporaryDirectory() as d:
            assert nc.discover(Path(d), st, T0, opener=f, track_recent_days=5) == ["OLDISHUSDT"]
            assert st["tracked"]["OLDISHUSDT"]["late_start"] is True


    def test_sample_writes_book_klines_trades_and_keeps_cursor(self):
        f = Fake()
        st = {"onboardDate": T0 - 1000, "first_sample_ms": None, "last_trade_id": None, "last_kline_ms": None}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            assert nc.sample(out, "NEWUSDT", st, T0, opener=f)
            book = [json.loads(x) for x in nc._file(out, "book", "NEWUSDT", T0).read_text().splitlines()]
            assert book[0]["status"] == "OK" and book[0]["first_sample"] and len(book[0]["depth"]["bids"]) == 50
            assert st["last_trade_id"] == 7 and st["last_kline_ms"] == T0 - 120000
            assert any("fromId" not in u and "startTime" in u for u in f.urls if "aggTrades" in u)
            nc.sample(out, "NEWUSDT", st, T0 + 30000, opener=f)
            assert any("fromId=8" in u for u in f.urls)


    def test_failed_fetch_is_recorded_not_invented(self):
        def bad(url):
            raise ValueError("boom")
        st = {"onboardDate": T0, "first_sample_ms": None, "last_trade_id": None, "last_kline_ms": None}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            assert not nc.sample(out, "XUSDT", st, T0, opener=bad, sleep=lambda s: None)
            rec = json.loads(nc._file(out, "book", "XUSDT", T0).read_text().splitlines()[0])
            assert rec["status"] == "FETCH_FAILED" and "depth" not in rec


    def test_interval_schedule(self):
        assert nc.interval_for_age(0) == 30 and nc.interval_for_age(2 * nc.DAY_MS) == 300
        assert nc.interval_for_age(10 * nc.DAY_MS) == 900


    def test_collect_refuses_without_flag_and_unbounded(self):
        with _raises(nc.CollectRefused):
            nc.collect(Path("x"), hours=1)
        with _raises(ValueError):
            nc.collect(Path("x"), hours=1000, allow_network=True)


    def test_collect_loop_runs_and_seals_manifest(self):
        f, now = Fake(), [float(T0) / 1000]
        f.symbols.append(sym("NEWUSDT", T0 - 1000))
        state = {"known": ["BTCUSDT"], "tracked": {}}
        def clock():
            return now[0]
        def sleep(s):
            now[0] += max(s, 5.0)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            nc.save_state(out, state)
            r = nc.collect(out, hours=0.01, allow_network=True, opener=f, sleep=sleep, clock=clock)
            assert r["stopped"] == "DURATION_REACHED" and r["samples"] >= 2 and r["active"] == ["NEWUSDT"]
            man = json.loads((out / "listing_manifest.json").read_text())
            assert man["files"] and all(len(x["sha256"]) == 64 for x in man["files"])

if __name__ == "__main__":
    unittest.main()
