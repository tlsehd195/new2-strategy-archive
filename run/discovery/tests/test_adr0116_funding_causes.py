"""ADR 0116 §8: every unusable funding fetch records WHY (network outage, API
error, unsupported symbol, changed schema, data never collected, local read
error, bad rows, incomplete coverage, unknown schedule), and that no cache is
used.  ``status`` (the gate) is unchanged.  Mock openers / temp dirs only."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (str(HERE.parent), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from paper_forward import (  # noqa: E402
    FUNDING_CACHE, FUNDING_CAUSES, BinancePublicFeed, FileFeed, funding_fetch_result,
)

HOUR = 3_600_000
NOW = 1_714_608_000_000          # 2024-05-02
AFTER = NOW - 24 * HOUR - 1
INFO = [{"symbol": "BTCUSDT", "fundingIntervalHours": 8}]


def http_error(url, code, body=b""):
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body))


class Api:
    def __init__(self, *, info=INFO, rate=None, fail=None):
        self.info, self.rate, self.fail = info, rate if rate is not None else [], fail

    def __call__(self, url):
        if self.fail and self.fail[0] in url:
            raise self.fail[1](url)
        if "fundingInfo" in url:
            return self.info if isinstance(self.info, bytes) else json.dumps(self.info).encode()
        return self.rate if isinstance(self.rate, bytes) else json.dumps(self.rate).encode()


def fetch(api, sym="BTCUSDT"):
    f = BinancePublicFeed(allow_network=True, opener=api, clock=lambda: NOW, sleep=lambda s: None, attempts=2)
    return f.funding_checked(sym, AFTER)


def rows(n=3, sym="BTCUSDT"):
    return [{"symbol": sym, "fundingTime": AFTER + 1 + k * 8 * HOUR, "fundingRate": "0.0001"} for k in range(n)]


class RestCauseTest(unittest.TestCase):
    def check(self, fr, status, cause):
        self.assertEqual((fr["status"], fr["cause"]), (status, cause), fr["issues"])
        self.assertEqual(fr["cache"], FUNDING_CACHE)

    def test_network_outage(self):
        self.check(fetch(Api(fail=("fundingRate", lambda u: urllib.error.URLError("timed out")))),
                   "SOURCE_ERROR", "NETWORK_ERROR")
        self.check(fetch(Api(fail=("fundingInfo", lambda u: http_error(u, 503)))), "SOURCE_ERROR", "NETWORK_ERROR")

    def test_api_error_vs_unsupported_symbol(self):
        self.check(fetch(Api(fail=("fundingRate", lambda u: http_error(u, 400, b'{"code":-1121,"msg":"Invalid symbol."}')))),
                   "SOURCE_ERROR", "SYMBOL_UNSUPPORTED")
        # HTTP 400 without a readable exchange code is not guessed to be an unknown symbol
        self.check(fetch(Api(fail=("fundingRate", lambda u: http_error(u, 400)))), "SOURCE_ERROR", "API_ERROR")
        self.check(fetch(Api(rate={"code": -1121, "msg": "Invalid symbol."})), "SOURCE_ERROR", "SYMBOL_UNSUPPORTED")
        self.check(fetch(Api(rate={"code": -1003, "msg": "Too many requests"})), "SOURCE_ERROR", "API_ERROR")

    def test_schema_changed(self):
        self.check(fetch(Api(rate=b"<html>maintenance</html>")), "SOURCE_ERROR", "SCHEMA_CHANGED")
        self.check(fetch(Api(rate={"rows": []})), "SOURCE_ERROR", "SCHEMA_CHANGED")
        self.check(fetch(Api(rate=[{"time": 1, "rate": "0.1"}])), "DATA_INVALID", "SCHEMA_CHANGED")
        self.check(fetch(Api(info={"symbols": []})), "SOURCE_ERROR", "SCHEMA_CHANGED")

    def test_bad_rows_and_schedule(self):
        dup = rows()
        self.check(fetch(Api(rate=dup[:2] + [dup[1]])), "DATA_INVALID", "DATA_INVALID")
        # complete answer: still no provable interval for past settlements (ADR 0074)
        self.check(fetch(Api(rate=rows())), "SCHEDULE_UNKNOWN", "SCHEDULE_UNKNOWN")

    def test_status_gate_unchanged_by_cause(self):
        for cause in FUNDING_CAUSES:
            self.assertIsInstance(cause, str)
        with self.assertRaisesRegex(ValueError, "unknown funding cause"):
            funding_fetch_result([], {}, status="SOURCE_ERROR", extra={"cause": "GUESSED"})


class LocalCauseTest(unittest.TestCase):
    def test_not_collected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "funding").mkdir()
            feed = FileFeed(Path(d), clock=lambda: NOW)
            fr = feed.funding_checked("BTCUSDT", AFTER)
            self.assertEqual((fr["status"], fr["cause"], fr["cache"]), ("DATA_MISSING", "NOT_COLLECTED", FUNDING_CACHE))


if __name__ == "__main__":
    unittest.main()
