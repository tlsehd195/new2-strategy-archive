"""Auto-evolution §7-2: opt-in data acquisition with checksum, quality gate
and atomic commit.  A fake opener serves SYNTHETIC archives — no network."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from data_fetch import BASE, fetch_missing  # noqa: E402
from test_engine import make_bars, write_kline_csv  # noqa: E402


def month_csv(ym: str, *, drop: int = -1) -> bytes:
    y, m = map(int, ym.split("-"))
    d0 = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
    d1 = dt.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=dt.timezone.utc)
    n = int((d1 - d0).total_seconds() // 900)
    bars = make_bars(n=n, start_ts=int(d0.timestamp() * 1000), seed=m)
    if drop >= 0:
        bars = bars[:drop] + bars[drop + 1:]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.csv"
        write_kline_csv(p, bars)
        return p.read_bytes()


def zip_of(name: str, payload: bytes) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(name, payload)
    return b.getvalue()


class FakeOpener:
    def __init__(self, files):
        self.files, self.calls = files, []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.files:
            raise OSError("404")
        return self.files[url]


def serve(ym, payload, *, bad_checksum=False, name=None):
    z = zip_of(name or f"BTCUSDT-15m-{ym}.csv", payload)
    url = f"{BASE}/klines/BTCUSDT/15m/BTCUSDT-15m-{ym}.zip"
    sha = "0" * 64 if bad_checksum else hashlib.sha256(z).hexdigest()
    return {url: z, url + ".CHECKSUM": f"{sha}  BTCUSDT-15m-{ym}.zip\n".encode()}


TODAY = dt.date(2024, 4, 15)


class TestFetch(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.dd = Path(self.td.name)

    def run_fetch(self, files, **kw):
        op = FakeOpener(files)
        return fetch_missing(self.dd, ["BTCUSDT"], "2024-01", "2024-12", allow=True,
                             kinds=("klines",), opener=op, today=TODAY, sleep=lambda _s: None, **kw), op

    def test_disabled_by_default(self):
        r = fetch_missing(self.dd, ["BTCUSDT"], "2024-01", "2024-02")
        self.assertEqual(r["status"], "DISABLED")
        self.assertEqual(list(self.dd.iterdir()), [])

    def test_good_month_committed_with_provenance_running_month_skipped(self):
        r, op = self.run_fetch({**serve("2024-01", month_csv("2024-01")),
                                **serve("2024-02", month_csv("2024-02")),
                                **serve("2024-03", month_csv("2024-03"))})
        self.assertEqual((r["status"], r["fetched"]), ("OK", 3))
        self.assertTrue((self.dd / "klines" / "BTCUSDT-15m-2024-03.csv").exists())
        self.assertFalse(any("2024-04" in u for u in op.calls))      # running month
        self.assertEqual(len((self.dd / "_provenance.jsonl").read_text().splitlines()), 3)

    def test_checksum_gap_and_bad_archive_leave_nothing(self):
        files = {**serve("2024-01", month_csv("2024-01"), bad_checksum=True),
                 **serve("2024-02", month_csv("2024-02", drop=500)),
                 **serve("2024-03", month_csv("2024-03"), name="../evil.csv")}
        r, _ = self.run_fetch(files)
        self.assertEqual((r["status"], r["fetched"], r["blocked"]), ("BLOCKED_DATA", 0, 3))
        by_month = {x["month"]: x["reason"] for x in r["results"]}
        self.assertEqual(by_month["2024-01"], "checksum mismatch")
        self.assertTrue(by_month["2024-02"].startswith("kline quality"), by_month["2024-02"])
        self.assertTrue(by_month["2024-03"].startswith("archive rejected"), by_month["2024-03"])
        self.assertFalse((self.dd / "klines").exists() and any((self.dd / "klines").iterdir()))

    def test_existing_file_never_overwritten(self):
        p = self.dd / "klines" / "BTCUSDT-15m-2024-01.csv"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"user data\n")
        r, op = self.run_fetch({**serve("2024-02", month_csv("2024-02")),
                                **serve("2024-03", month_csv("2024-03"))})
        self.assertEqual(p.read_bytes(), b"user data\n")
        self.assertFalse(any("2024-01" in u for u in op.calls))

    def test_transport_failure_is_blocked_data(self):
        r, _ = self.run_fetch({})
        self.assertEqual(r["status"], "BLOCKED_DATA")
        self.assertTrue(all(x["reason"].startswith("download failed") for x in r["results"]))


if __name__ == "__main__":
    unittest.main()
