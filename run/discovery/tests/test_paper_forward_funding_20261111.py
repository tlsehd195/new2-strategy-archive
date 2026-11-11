"""ADR 0073: Paper Forward funding integrity.

Missing, unparsable, out-of-order, gapped or schedule-less funding data must
never be charged as 0 and must never reach PAPER_PASSED.

SYNTHETIC data and temp dirs only.  No market data, no network (the public
REST feed is driven by a mock opener), no locked / IW / sealed window.  Every
settlement interval used here is DECLARED by the test (no 8h default).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import hashlib  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from unittest import mock  # noqa: E402

from engine_lib import iter_jsonl  # noqa: E402
from test_completion import ForwardCase, SynthFeed  # noqa: E402
from test_engine import make_bars  # noqa: E402
from test_residual_fixes import fee_cost  # noqa: E402

HOUR = 3_600_000
DAY = 86_400_000


def _ms(d):
    return int(dt.datetime.fromisoformat(d + "T00:00:00+00:00").timestamp() * 1000)


def declared_settlements(t0, t1, iv_h, rate=1e-5):
    """Settlements every ``iv_h`` hours (declared by the caller) in [t0, t1]."""
    step = iv_h * HOUR
    t = t0 // step * step
    out = []
    while t <= t1:
        out.append((t, rate, iv_h))
        t += step
    return out


def write_funding_month(path: Path, rows, *, header=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["calc_time,funding_interval_hours,last_funding_rate"] if header else []
    lines += [f"{t},{h},{r}" for t, r, h in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_months(d: Path, sym, rows):
    by = {}
    for t, r, h in rows:
        m = dt.datetime.fromtimestamp(t / 1000, tz=dt.timezone.utc).strftime("%Y-%m")
        by.setdefault(m, []).append((t, r, h))
    for m, rs in by.items():
        write_funding_month(d / "funding" / f"{sym}-fundingRate-{m}.csv", rs)
    return sorted(by)


class DeclaredFeed(SynthFeed):
    """Synthetic bars + synthetic funding with a DECLARED interval per row.
    ``funding()`` is the pre-ADR-0073 list interface (what the old engine
    called); ``funding_checked()`` the new checked interface."""

    def __init__(self, bars, clock, rows, **kw):
        super().__init__(bars, clock, **kw)
        self.rows = list(rows)            # (ts, rate, interval_h)

    def funding(self, sym, after_ts):
        return [(t, r) for t, r, _ in self.rows if after_ts < t <= self.clock()]

    def funding_checked(self, sym, after_ts):
        from paper_forward import funding_fetch_result
        rows = [(t, r, h) for t, r, h in self.rows if after_ts < t <= self.clock()]
        return funding_fetch_result(
            [(t, r) for t, r, _ in rows], {t: int(h * HOUR) for t, _, h in rows if h},
            source_files=[{"name": "synthetic", "sha256": "0" * 64, "rows": len(rows), "status": "VALID",
                           "errors": []}],
            schedule_source="declared_by_test", query={"after_ts": after_ts, "until_ts": self.clock()})


class FileFundingFeed(SynthFeed):
    """Synthetic bars, funding read by the real FileFeed from temp CSV files."""

    def __init__(self, bars, clock, data_dir, **kw):
        super().__init__(bars, clock, **kw)
        from paper_forward import FileFeed
        self.files = FileFeed(data_dir, clock=clock)

    def funding(self, sym, after_ts):
        return self.files.funding(sym, after_ts)

    def funding_checked(self, sym, after_ts):
        return self.files.funding_checked(sym, after_ts)

    def funding_source_hashes(self, sym):
        return self.files.funding_source_hashes(sym)


class FundingCase(ForwardCase):
    START = "2024-08-25"

    def setup_feed(self, rows=None, *, iv_h=4, kind="declared", drop=(), mutate=None):
        p, edge, feed = self.ready(start=self.START)
        lo, hi = feed.bars[0].ts, feed.bars[-1].ts + DAY
        rows = list(rows) if rows is not None else declared_settlements(lo, hi, iv_h)
        rows = [x for x in rows if x[0] not in set(drop)]
        if kind == "declared":
            f = DeclaredFeed(feed.bars, feed.clock, rows)
        else:
            dd = self.td / "fdata"
            write_months(dd, "BTCUSDT", rows)
            if mutate:
                mutate(dd)
            f = FileFundingFeed(feed.bars, feed.clock, dd)
        return p, edge, f

    def run_plan(self, p, edge, feed, days=75):
        fp = self.fp(p, feed)
        plan = fp.register_plan(edge)
        self.now[0] += days * DAY
        return fp, plan, fp.step(plan["plan_id"])


# ---------------------------------------------------------------------------
# reproductions of the defect (written first; they FAIL on main 0725f86)
# ---------------------------------------------------------------------------

class TestReproduction(FundingCase):
    def test_repro_no_funding_at_all_is_not_zero_cost_pass(self):
        """main: ``funding or None`` -> evaluator charges 0 -> PAPER_PASSED."""
        p, edge, feed = self.setup_feed(rows=[])
        fp = self.fp(p, feed)
        try:
            plan = fp.register_plan(edge)
        except Exception as e:                          # refused up front: fail-closed
            self.assertIn("funding", str(e).lower())
            return
        self.now[0] += 75 * DAY
        r = fp.step(plan["plan_id"])
        self.assertNotEqual(r["verdict"], "PAPER_PASSED")
        self.assertNotEqual(p.statuses.status(edge, p.context_id()), "PAPER_PASSED")

    def test_repro_missing_4h_settlement_hidden_by_nominal_8h(self):
        """main: the forward path passes no intervals (NOMINAL_8H_LEGACY); one
        missing 4h settlement is an 8h step, which the nominal rule accepts, so
        the trade is charged one settlement short and the plan passes."""
        start = _ms(self.START)
        p, edge, feed = self.setup_feed(iv_h=4, drop=[start + 10 * DAY + 4 * HOUR])
        fp, plan, r = self.run_plan(p, edge, feed)
        self.assertNotEqual(r["verdict"], "PAPER_PASSED", r.get("reasons"))

    def test_repro_corrupt_middle_month_file_is_not_silently_skipped(self):
        """main: FileFeed.funding() skips the inverted September file and
        returns August + October as if nothing happened."""
        from paper_forward import FileFeed
        dd = self.td / "f"
        rows = declared_settlements(_ms("2024-08-01"), _ms("2024-10-31"), 8)
        write_months(dd, "BTCUSDT", rows)
        sep = dd / "funding" / "BTCUSDT-fundingRate-2024-09.csv"
        lines = sep.read_text().splitlines()
        lines[3], lines[4] = lines[4], lines[3]          # source time inversion
        sep.write_text("\n".join(lines) + "\n")
        feed = FileFeed(dd, clock=lambda: _ms("2024-11-01"))
        try:
            got = feed.funding("BTCUSDT", _ms("2024-08-01") - 1)
        except Exception as e:
            self.assertIn("DATA_INVALID", str(e))
            return
        self.fail(f"partial series of {len(got)} settlements returned without an error")

    def test_repro_corrupt_month_file_end_to_end(self):
        """main: same corrupt file through the whole forward path -> PAPER_PASSED."""
        def corrupt(dd):
            f = dd / "funding" / "BTCUSDT-fundingRate-2024-09.csv"
            lines = f.read_text().splitlines()
            lines[5], lines[6] = lines[6], lines[5]
            f.write_text("\n".join(lines) + "\n")
        p, edge, feed = self.setup_feed(iv_h=8, kind="files", mutate=corrupt)
        fp = self.fp(p, feed)
        try:
            plan = fp.register_plan(edge)
        except Exception as e:
            self.assertIn("funding", str(e).lower())
            return
        self.now[0] += 75 * DAY
        r = fp.step(plan["plan_id"])
        self.assertNotEqual(r["verdict"], "PAPER_PASSED", r.get("reasons"))


# ---------------------------------------------------------------------------
# A/B. FileFeed: checked funding files
# ---------------------------------------------------------------------------

class FileFundingCase(unittest.TestCase):
    SYM = "BTCUSDT"

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.rows = declared_settlements(_ms("2024-08-01"), _ms("2024-10-31") + 16 * HOUR, 8)
        write_months(self.td, self.SYM, self.rows)

    def f(self, month):
        return self.td / "funding" / f"{self.SYM}-fundingRate-{month}.csv"

    def edit(self, month, fn):
        lines = self.f(month).read_text().splitlines()
        lines = fn(lines)
        self.f(month).write_text("\n".join(lines) + "\n")

    def fetch(self, after="2024-08-01", now="2024-11-01"):
        from paper_forward import FileFeed
        feed = FileFeed(self.td, clock=lambda: _ms(now))
        return feed, feed.funding_checked(self.SYM, _ms(after) - 1)


class TestFileFeedFunding(FileFundingCase):
    def test_a1_a3_a5_valid_months_in_order_with_declared_intervals(self):
        _, fr = self.fetch()
        self.assertEqual(fr["status"], "VALID")
        self.assertEqual(len(fr["rows"]), sum(1 for t, _, _ in self.rows if t <= _ms("2024-11-01")))
        self.assertTrue(all(fr["intervals"][t] == 8 * HOUR for t, _ in fr["rows"]))
        names = [x["name"] for x in fr["source_files"]]
        self.assertEqual(names, [f"BTCUSDT-fundingRate-2024-{m}.csv" for m in ("08", "09", "10")])
        for x in fr["source_files"]:
            self.assertEqual(x["sha256"], hashlib.sha256((self.td / "funding" / x["name"]).read_bytes()).hexdigest())
            self.assertEqual(x["status"], "VALID")
        self.assertIn("funding_interval_hours", fr["schedule_source"])

    def test_a4_zero_rates_are_valid_observations(self):
        rows = [(t, 0.0, h) for t, _, h in self.rows]
        write_months(self.td, self.SYM, rows)
        _, fr = self.fetch()
        self.assertEqual(fr["status"], "VALID_ZERO_RATE")
        self.assertEqual(fr["zero_rate_rows"], len(fr["rows"]))

    def test_b1_inversion_inside_a_file_invalidates_the_whole_answer(self):
        self.edit("2024-09", lambda ls: ls[:3] + [ls[4], ls[3]] + ls[5:])
        feed, fr = self.fetch()
        self.assertEqual(fr["status"], "DATA_INVALID")
        st = {x["name"][-11:-4]: x["status"] for x in fr["source_files"]}
        self.assertEqual(st, {"2024-08": "VALID", "2024-09": "DATA_INVALID", "2024-10": "VALID"})
        self.assertTrue(any("SOURCE_TIME_INVERSION" in i for i in fr["issues"]))
        from paper_forward import FundingSourceInvalid
        with self.assertRaises(FundingSourceInvalid):
            feed.funding(self.SYM, _ms("2024-08-01") - 1)

    def test_b2_duplicate_settlement_in_a_file(self):
        self.edit("2024-09", lambda ls: ls[:4] + [ls[3]] + ls[4:])
        _, fr = self.fetch()
        self.assertEqual(fr["status"], "DATA_INVALID")
        self.assertTrue(any("SOURCE_DUPLICATE_TS" in i for i in fr["issues"]))

    def test_b3_unparsable_row(self):
        self.edit("2024-09", lambda ls: ls[:4] + ["garbage,row"] + ls[4:])
        _, fr = self.fetch()
        self.assertEqual(fr["status"], "DATA_INVALID")
        self.assertTrue(any("PARSE_ERROR" in i for i in fr["issues"]))

    def test_b4_torn_last_row(self):
        txt = self.f("2024-10").read_text().rstrip("\n")
        self.f("2024-10").write_text(txt[:-3])          # cut inside the last row, no newline
        _, fr = self.fetch()
        self.assertEqual(fr["status"], "DATA_INVALID")
        self.assertTrue(any("TORN_TAIL" in i for i in fr["issues"]))

    def test_b5_b6_cross_file_inversion_and_duplicate(self):
        last_aug = [t for t, _, _ in self.rows if t < _ms("2024-09-01")][-1]
        for t_new, kind in ((last_aug - 8 * HOUR, "CROSS_FILE_TIME_INVERSION"), (last_aug, "CROSS_FILE_DUPLICATE_TS")):
            with self.subTest(kind=kind):
                write_months(self.td, self.SYM, self.rows)
                self.edit("2024-09", lambda ls: [ls[0], f"{t_new},8,0.0001"] + ls[1:])
                _, fr = self.fetch()
                self.assertEqual(fr["status"], "DATA_INVALID")
                bad = {x["name"][-11:-4] for x in fr["source_files"] if x["status"] == "DATA_INVALID"}
                self.assertEqual(bad, {"2024-08", "2024-09"})        # both files of the pair
                self.assertTrue(any(kind in i for i in fr["issues"]))

    def test_b7_one_bad_file_among_many_never_returns_the_others(self):
        self.edit("2024-08", lambda ls: ls[:2] + ["1,x"] + ls[2:])
        feed, fr = self.fetch()
        self.assertEqual(fr["status"], "DATA_INVALID")
        from paper_forward import FundingSourceInvalid
        with self.assertRaises(FundingSourceInvalid):
            feed.funding(self.SYM, _ms("2024-08-01") - 1)

    def test_b8_no_file_at_all_is_data_missing_not_empty(self):
        shutil.rmtree(self.td / "funding")
        feed, fr = self.fetch()
        self.assertEqual((fr["status"], fr["rows"]), ("DATA_MISSING", []))
        from paper_forward import FundingSourceInvalid
        with self.assertRaises(FundingSourceInvalid):
            feed.funding(self.SYM, 0)

    def test_b9_missing_month_is_listed_and_coverage_decides(self):
        from funding_schedule import coverage_check
        self.f("2024-09").unlink()
        _, fr = self.fetch()
        self.assertIn("BTCUSDT-fundingRate-2024-09.csv", fr["missing_files"])
        cov = coverage_check(fr["rows"], fr["intervals"], _ms("2024-08-10"), _ms("2024-10-20"))
        self.assertEqual((cov["status"], cov["kind"]), ("FUNDING_SETTLEMENT_GAP", "INTERIOR"))
        # the same missing file does NOT matter for a window it cannot touch
        cov = coverage_check(fr["rows"], fr["intervals"], _ms("2024-10-05"), _ms("2024-10-20"))
        self.assertEqual(cov["status"], "OK")

    def test_b10_empty_files_are_no_data(self):
        from funding_schedule import coverage_check
        for m in ("2024-08", "2024-09", "2024-10"):
            write_funding_month(self.f(m), [])
        _, fr = self.fetch()
        self.assertEqual(fr["rows"], [])
        cov = coverage_check(fr["rows"], fr["intervals"], _ms("2024-08-10"), _ms("2024-08-20"))
        self.assertEqual(cov["status"], "FUNDING_DATA_MISSING")

    def test_rows_without_declared_interval_are_schedule_unknown(self):
        self.edit("2024-09", lambda ls: [ls[0]] + [",".join([x.split(",")[0], "", x.split(",")[2]]) for x in ls[1:]])
        _, fr = self.fetch()
        self.assertEqual(fr["status"], "SCHEDULE_UNKNOWN")

    def test_corrupt_file_outside_the_queried_period_is_not_read(self):
        self.edit("2024-08", lambda ls: ls[:2] + ["1,x"] + ls[2:])
        _, fr = self.fetch(after="2024-10-02", now="2024-10-20")
        self.assertEqual(fr["status"], "VALID")
        self.assertEqual([x["name"][-11:-4] for x in fr["source_files"]], ["2024-10"])

    def test_runner_window_report_is_unchanged_by_the_shared_loader(self):
        import runner
        pairs, rep = runner.load_funding_window_checked(self.td, self.SYM, "2024-08-01", "2024-10-31")
        self.assertEqual([sorted(f) for f in rep["files"]], [["name", "rows", "sha256"]] * 3)
        for k in ("parse_errors", "torn_tail", "intervals", "source_order_errors", "interval_missing_rows"):
            self.assertIn(k, rep)
        self.assertTrue(runner.checked_funding_quality(pairs, rep)["ok"])


# ---------------------------------------------------------------------------
# C. settlement completeness (declared schedules only)
# ---------------------------------------------------------------------------

def series(t0, n, iv_h, rate=1e-4, drop=()):
    pairs = [(t0 + k * iv_h * HOUR, rate) for k in range(n) if k not in drop]
    return pairs, {t: iv_h * HOUR for t, _ in pairs}


class TestCoverage(unittest.TestCase):
    T0 = _ms("2024-01-01")

    def cov(self, pairs, ivs, a, b):
        from funding_schedule import coverage_check
        return coverage_check(pairs, ivs, a, b)

    def test_c6_complete_1h_4h_8h(self):
        for iv in (1, 4, 8):
            with self.subTest(iv=iv):
                pairs, ivs = series(self.T0, 60, iv)
                c = self.cov(pairs, ivs, self.T0 + HOUR // 2, self.T0 + 40 * iv * HOUR + HOUR // 2)
                self.assertEqual(c["status"], "OK", c)
                self.assertEqual(c["settlements"], 40)

    def test_c1_c2_c3_missing_first_last_middle(self):
        for iv in (1, 4, 8):
            for k, kind in ((1, "INTERIOR"), (20, "INTERIOR"), (40, "TAIL_DUE_MISSING")):
                with self.subTest(iv=iv, k=k):
                    pairs, ivs = series(self.T0, 41, iv, drop={k})
                    c = self.cov(pairs, ivs, self.T0 + 60_000 * 5, self.T0 + 40 * iv * HOUR + 60_000 * 5)
                    self.assertEqual((c["status"], c["kind"]), ("FUNDING_SETTLEMENT_GAP", kind), c)

    def test_c4_unknown_schedule(self):
        pairs, _ = series(self.T0, 30, 4)
        self.assertEqual(self.cov(pairs, None, self.T0 + 1, self.T0 + 20 * 4 * HOUR)["status"],
                         "FUNDING_SCHEDULE_UNKNOWN")
        pairs, ivs = series(self.T0, 30, 4)
        del ivs[pairs[10][0]]
        self.assertEqual(self.cov(pairs, ivs, self.T0 + 1, self.T0 + 20 * 4 * HOUR)["status"],
                         "FUNDING_SCHEDULE_UNKNOWN")

    def test_c5_schedule_change_inside_the_window(self):
        a, ia = series(self.T0, 10, 8)                         # 8h until T0 + 72h
        t = a[-1][0]
        b = [(t + k * 4 * HOUR, 1e-4) for k in range(1, 20)]   # then 4h, declared on each settlement
        ib = {x: 4 * HOUR for x, _ in b}
        c = self.cov(a + b, {**ia, **ib}, self.T0 + 1, b[-2][0])
        self.assertEqual(c["status"], "OK", c)
        self.assertEqual(c["schedule_changes"], 1)
        del ib[b[5][0]]
        b2 = [x for x in b if x[0] != b[6][0]]                 # a 4h settlement missing after the change
        c = self.cov(a + b2, {**ia, **ib}, self.T0 + 1, b[-2][0])
        self.assertNotEqual(c["status"], "OK")

    def test_c7_zero_rate_is_paid_zero_missing_is_none(self):
        from evaluator import FundingIndex
        zero, ivs = series(self.T0, 20, 4, rate=0.0)
        fi = FundingIndex(zero, intervals=ivs)
        self.assertEqual(fi.paid_bp(self.T0 + 1, self.T0 + 12 * 4 * HOUR, 1), 0.0)
        self.assertEqual(fi.last_status, "OK")
        c = self.cov(zero, ivs, self.T0 + 1, self.T0 + 12 * 4 * HOUR)
        self.assertEqual((c["status"], c["zero_rate_settlements"]), ("OK", 12))
        gap, ivg = series(self.T0, 20, 4, rate=0.0, drop={5})
        fi = FundingIndex(gap, intervals=ivg)
        self.assertIsNone(fi.paid_bp(self.T0 + 1, self.T0 + 12 * 4 * HOUR, 1))
        self.assertEqual(fi.last_status, "FUNDING_SETTLEMENT_GAP")
        self.assertEqual(self.cov(gap, ivg, self.T0 + 1, self.T0 + 12 * 4 * HOUR)["status"],
                         "FUNDING_SETTLEMENT_GAP")

    def test_c8_gap_outside_the_window_vs_inside(self):
        pairs, ivs = series(self.T0, 60, 4, drop={3})                    # gap early
        self.assertEqual(self.cov(pairs, ivs, self.T0 + 20 * 4 * HOUR, self.T0 + 50 * 4 * HOUR)["status"], "OK")
        self.assertEqual(self.cov(pairs, ivs, self.T0 + 1, self.T0 + 50 * 4 * HOUR)["status"],
                         "FUNDING_SETTLEMENT_GAP")
        c = self.cov(pairs, ivs, self.T0 - 1, self.T0 + 10 * HOUR)       # starts before the series
        self.assertEqual((c["status"], c["kind"]), ("FUNDING_DATA_MISSING", "HEAD_NOT_COVERED"))

    def test_nominal_8h_rule_hides_a_missing_4h_settlement(self):
        """Why the forward path must pass declared intervals: the legacy rule
        accepts a 4h series with one settlement missing and charges it short."""
        from evaluator import FundingIndex
        pairs, ivs = series(self.T0, 20, 4, drop={6})
        a, b = self.T0 + 1, self.T0 + 12 * 4 * HOUR
        self.assertIsNotNone(FundingIndex(pairs).paid_bp(a, b, 1))           # NOMINAL_8H_LEGACY
        self.assertIsNone(FundingIndex(pairs, intervals=ivs).paid_bp(a, b, 1))


# ---------------------------------------------------------------------------
# evaluator: explicit funding semantics
# ---------------------------------------------------------------------------

class TestEvaluatorFunding(unittest.TestCase):
    def setUp(self):
        self.bars = make_bars(n=1200, start_ts=_ms("2024-01-01"), seed=5, drift=0.0002)

    def study(self, funding, ivs=None, **kw):
        from evaluator import run_event_study
        return run_event_study(self.bars, "BTCUSDT", lambda v: 1 if len(v) % 7 == 0 else 0, 48,
                               fee_cost(), 1000.0, funding, funding_intervals=ivs, **kw)

    def test_require_funding_refuses_none_and_nominal_8h(self):
        from evaluator import FundingRequired
        pairs, ivs = series(_ms("2023-12-31"), 200, 4)
        with self.assertRaises(FundingRequired):
            self.study(None, ivs, require_funding=True)
        with self.assertRaises(FundingRequired):
            self.study(pairs, None, require_funding=True)
        self.assertEqual(self.study(pairs, ivs, require_funding=True).funding_schedule, "DECLARED")

    def test_empty_series_drops_every_event_instead_of_charging_zero(self):
        res = self.study([], {}, require_funding=True)
        self.assertEqual(res.events, [])
        self.assertGreater(res.dropped_missing_funding, 0)
        self.assertEqual(set(res.funding_drop_reasons), {"FUNDING_DATA_MISSING"})
        self.assertEqual(res.diagnostics()["accounting_ok"], True)

    def test_series_starting_after_entry_or_ending_before_exit(self):
        pairs, ivs = series(_ms("2024-01-03"), 200, 4)               # starts after the first entries
        res = self.study(pairs, ivs, require_funding=True)
        self.assertGreater(res.funding_drop_reasons.get("FUNDING_DATA_MISSING", 0), 0)
        self.assertTrue(all(e.entry_ts >= pairs[0][0] for e in res.events))
        pairs, ivs = series(_ms("2023-12-31"), 30, 4)                # ends before the last exits
        res = self.study(pairs, ivs, require_funding=True)
        self.assertGreater(res.funding_drop_reasons.get("FUNDING_DATA_MISSING", 0), 0)
        self.assertTrue(all(e.exit_ts <= pairs[-1][0] for e in res.events))

    def test_a7_valid_complete_series_declared_equals_legacy(self):
        """On a complete series the declared schedule changes nothing: same
        events, same funding, same net (1h, 4h and 8h, non-zero funding)."""
        for iv in (1, 4, 8):
            with self.subTest(iv=iv):
                pairs, ivs = series(_ms("2023-12-31"), 400 * 8 // iv, iv, rate=3e-4)
                old = self.study(pairs)                       # NOMINAL_8H_LEGACY path of main
                new = self.study(pairs, ivs, require_funding=True)
                self.assertEqual([e.to_json() for e in old.events], [e.to_json() for e in new.events])
                self.assertTrue(any(e.funding_bp != 0 for e in new.events))


# ---------------------------------------------------------------------------
# F. public REST feed (mock opener only — no network)
# ---------------------------------------------------------------------------

class MockApi:
    def __init__(self, info, pages, *, fail=None):
        self.info, self.pages, self.fail, self.urls = info, list(pages), fail, []

    def __call__(self, url):
        self.urls.append(url)
        if self.fail and self.fail in url:
            import urllib.error
            raise urllib.error.HTTPError(url, 503, "unavailable", None, None)
        if "fundingInfo" in url:
            return json.dumps(self.info).encode()
        return json.dumps(self.pages.pop(0) if self.pages else []).encode()


class TestBinanceFeedFunding(unittest.TestCase):
    NOW = _ms("2024-05-02")
    AFTER = _ms("2024-05-01") - 1

    def feed(self, api, **kw):
        from paper_forward import BinancePublicFeed
        f = BinancePublicFeed(allow_network=True, opener=api, clock=lambda: self.NOW, sleep=lambda s: None,
                              attempts=2)
        for k, v in kw.items():
            setattr(f, k, v)
        return f

    def rows(self, iv_h=4, n=6, rate="0.0001", sym="BTCUSDT"):
        return [{"symbol": sym, "fundingTime": _ms("2024-05-01") + k * iv_h * HOUR, "fundingRate": rate}
                for k in range(n)]

    INFO4 = [{"symbol": "BTCUSDT", "fundingIntervalHours": 4}]

    def test_f1_normal_answer(self):
        # ADR 0074: was VALID with today's fundingInfo interval copied onto every past
        # settlement (the defect).  The snapshot is kept as evidence, never back-dated.
        fr = self.feed(MockApi(self.INFO4, [self.rows()])).funding_checked("BTCUSDT", self.AFTER)
        self.assertEqual(fr["status"], "SCHEDULE_UNKNOWN")
        self.assertEqual(len(fr["rows"]), 6)
        self.assertEqual(fr["intervals"], {})
        self.assertEqual(fr["schedule_observation"]["interval_ms"], 4 * HOUR)
        self.assertTrue(all(x["sha256"] for x in fr["source_files"]))

    def test_f2_empty_answer_is_not_an_error_and_not_coverage(self):
        from funding_schedule import coverage_check
        fr = self.feed(MockApi(self.INFO4, [[]])).funding_checked("BTCUSDT", self.AFTER)
        self.assertEqual((fr["status"], fr["rows"]), ("SCHEDULE_UNKNOWN", []))     # ADR 0074 (was VALID)
        self.assertEqual(coverage_check(fr["rows"], fr["intervals"], self.AFTER, self.NOW)["status"],
                         "FUNDING_DATA_MISSING")

    def test_f3_api_errors_are_source_errors(self):
        fr = self.feed(MockApi(self.INFO4, [], fail="fundingRate")).funding_checked("BTCUSDT", self.AFTER)
        self.assertEqual(fr["status"], "SOURCE_ERROR")
        fr = self.feed(MockApi(self.INFO4, [{"code": -1121, "msg": "Invalid symbol."}])).funding_checked(
            "BTCUSDT", self.AFTER)
        self.assertEqual(fr["status"], "SOURCE_ERROR")
        fr = self.feed(MockApi(self.INFO4, [], fail="fundingInfo")).funding_checked("BTCUSDT", self.AFTER)
        self.assertEqual(fr["status"], "SOURCE_ERROR")

    def test_f4_truncated_answer_is_coverage_incomplete(self):
        api = MockApi(self.INFO4, [self.rows(n=3)])
        fr = self.feed(api, FUNDING_LIMIT=3, MAX_FUNDING_PAGES=1).funding_checked("BTCUSDT", self.AFTER)
        self.assertEqual(fr["status"], "COVERAGE_INCOMPLETE")
        self.assertEqual(len(fr["rows"]), 3)
        api = MockApi(self.INFO4, [self.rows(n=3), self.rows(n=6)[3:]])          # paged: complete
        fr = self.feed(api, FUNDING_LIMIT=3).funding_checked("BTCUSDT", self.AFTER)
        self.assertEqual((fr["status"], len(fr["rows"])), ("SCHEDULE_UNKNOWN", 6))   # ADR 0074 (was VALID)
        self.assertIn(f"startTime={self.rows(n=3)[-1]['fundingTime'] + 1}&", api.urls[2])

    def test_f5_f6_duplicate_and_inverted_settlements(self):
        r = self.rows()
        for bad in (r[:3] + [r[2]] + r[3:], r[:2] + [r[3], r[2]] + r[4:]):
            fr = self.feed(MockApi(self.INFO4, [bad])).funding_checked("BTCUSDT", self.AFTER)
            self.assertEqual(fr["status"], "DATA_INVALID")
        r2 = self.rows()
        r2[0]["fundingTime"] = self.AFTER - HOUR                                   # outside the query
        fr = self.feed(MockApi(self.INFO4, [r2])).funding_checked("BTCUSDT", self.AFTER)
        self.assertEqual(fr["status"], "DATA_INVALID")

    def test_f7_no_declared_interval_is_schedule_unknown(self):
        fr = self.feed(MockApi([{"symbol": "ETHUSDT", "fundingIntervalHours": 4}], [self.rows()])).funding_checked(
            "BTCUSDT", self.AFTER)
        self.assertEqual(fr["status"], "SCHEDULE_UNKNOWN")
        self.assertEqual(fr["intervals"], {})
        self.assertIsNone(fr["schedule_source"])

    def test_f8_partial_settlements_cannot_prove_coverage(self):
        from funding_schedule import coverage_check
        r = self.rows(n=6)
        del r[3]
        fr = self.feed(MockApi(self.INFO4, [r])).funding_checked("BTCUSDT", self.AFTER)
        self.assertEqual(fr["status"], "SCHEDULE_UNKNOWN")                          # ADR 0074 (was VALID)
        c = coverage_check(fr["rows"], fr["intervals"], _ms("2024-05-01"), r[-1]["fundingTime"])
        self.assertNotEqual(c["status"], "OK")                                      # (was GAP via back-dating)


# ---------------------------------------------------------------------------
# D/E. the whole forward path: feed -> quality -> evaluator -> verdict -> state
# ---------------------------------------------------------------------------

class TestForwardVerdict(FundingCase):
    def test_d1_valid_funding_passes_with_evidence(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp, plan, r = self.run_plan(p, edge, feed)
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assertEqual(r["funding_schedule"], {"BTCUSDT": "DECLARED"})
        self.assertEqual(r["funding"]["BTCUSDT"]["status"], "VALID")
        rec = p.evaluation("paper_forward", edge)
        ev = rec["funding_evidence"]
        self.assertEqual(ev["policy"], "paper-forward-funding-v1")
        self.assertEqual(ev["symbols"]["BTCUSDT"]["coverage"]["status"], "OK")
        self.assertEqual(plan["funding_policy"], "paper-forward-funding-v1")
        logged = list(iter_jsonl(fp.root / plan["plan_id"] / "funding_BTCUSDT.jsonl"))
        self.assertTrue(logged and all(x["interval_ms"] == 4 * HOUR for x in logged))
        ts = [x["ts"] for x in logged]
        self.assertEqual(ts, sorted(set(ts)))

    def test_a7_results_equal_the_pre_fix_baseline(self):
        """Forward events of VALID data are those main 0725f86 produced (fixture)."""
        base = json.loads((HERE / "data" / "adr0073_forward_baseline_0725f86.json").read_text())
        F = base["fields"]
        for name, case in base["cases"].items():
            with self.subTest(case=name):
                self.setUp()
                if name == "zero":
                    rows = declared_settlements(_ms("2024-06-01"), _ms("2025-04-30"), 8, rate=0.0)
                    p, edge, feed = self.setup_feed(rows=rows)
                else:
                    iv = int(name[2])
                    p, edge, feed = self.setup_feed(iv_h=iv, kind="files" if name.endswith("files") else "declared")
                fp, plan, r = self.run_plan(p, edge, feed)
                self.assertEqual(r["verdict"], case["verdict"])
                evs = [[e[f] for f in F] for e in iter_jsonl(fp.root / plan["plan_id"] / "events.jsonl")]
                self.assertEqual(evs, case["events"])

    def test_d3_no_funding_registration_refused_nothing_written(self):
        from paper_forward import ForwardRefused
        p, edge, feed = self.setup_feed(rows=[])
        fp = self.fp(p, feed)
        with self.assertRaises(ForwardRefused):
            fp.register_plan(edge)
        self.assertEqual(fp.plans(), [])
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")

    def test_d4_unknown_schedule(self):
        from paper_forward import ForwardRefused
        p, edge, feed = self.ready(start=self.START)
        # a feed with only the list interface declares no schedule: refused up front
        legacy = type("LegacyFeed", (), {"kind": "legacy_list_feed", "realtime": False,
                                         "closed_bars": feed.closed_bars, "funding": feed.funding})()
        with self.assertRaisesRegex(ForwardRefused, "SCHEDULE_UNKNOWN"):
            self.fp(p, legacy).register_plan(edge)
        # schedule declared up to the start, not declared afterwards
        start = _ms(self.START)
        rows = [(t, r, (h if t <= start else None))
                for t, r, h in declared_settlements(feed.bars[0].ts, feed.bars[-1].ts, 4)]
        fp, plan, r = self.run_plan(p, edge, DeclaredFeed(feed.bars, feed.clock, rows))
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA")
        self.assertTrue(any("SCHEDULE_UNKNOWN" in x for x in r["reasons"]), r["reasons"])
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")

    def test_d5_missing_middle_settlement_blocks(self):
        start = _ms(self.START)
        p, edge, feed = self.setup_feed(iv_h=4, drop=[start + 10 * DAY + 4 * HOUR])
        fp, plan, r = self.run_plan(p, edge, feed)
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA")
        self.assertTrue(any("COVERAGE_INCOMPLETE" in x for x in r["reasons"]), r["reasons"])
        self.assertFalse(fp.state(plan["plan_id"])["concluded"])
        self.assertIsNone(p.evaluation("paper_forward", edge))

    def test_d2_corrupt_file_blocks_for_good(self):
        def corrupt(dd):
            f = dd / "funding" / "BTCUSDT-fundingRate-2024-09.csv"
            lines = f.read_text().splitlines()
            lines[5], lines[6] = lines[6], lines[5]
            f.write_text("\n".join(lines) + "\n")
        p, edge, feed = self.setup_feed(iv_h=8, kind="files", mutate=corrupt)
        fp, plan, r = self.run_plan(p, edge, feed)
        pid = plan["plan_id"]
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA")
        st = fp.state(pid)
        self.assertEqual(st["status"], "BLOCKED_DATA")
        self.assertEqual(st["errors"][-1]["kind"], "FUNDING_DATA_INVALID")
        q = list(iter_jsonl(fp.root / pid / "funding_quality_BTCUSDT.jsonl"))
        self.assertEqual(q[-1]["status"], "DATA_INVALID")
        # E2/E6/E7: a restart keeps it blocked, and repairing the file is no evidence
        write_months(self.td / "fdata", "BTCUSDT",
                     declared_settlements(feed.bars[0].ts, feed.bars[-1].ts + DAY, 8))
        self.now[0] += DAY
        r = self.fp(p, feed).step(pid)
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA")
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")

    def test_d6_source_error_blocks_the_step_and_is_retried(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        plan = fp.register_plan(edge)
        real = feed.funding_checked
        feed.funding_checked = lambda sym, after: (_ for _ in ()).throw(OSError("connection reset"))
        self.now[0] += 75 * DAY
        r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA")
        self.assertTrue(any("SOURCE_ERROR" in x or "DATA_MISSING" in x or "COVERAGE" in x for x in r["reasons"]),
                        r["reasons"])
        self.assertEqual(fp.state(plan["plan_id"])["status"], "OBSERVING")       # not sticky
        feed.funding_checked = real
        self.now[0] += HOUR
        r = fp.step(plan["plan_id"])
        # ADR 0076: the retry is accepted (funding confirmed).  The sample ends at the
        # earliest bar at which the rule holds (start + 60 days - 15 m), so the position
        # open at day 75 is outside it and the plan concludes on this step
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assertEqual(r["evaluation_cutoff"], plan["start_ts"] + 60 * DAY - 900_000)
        self.assertEqual(r["positions"]["pending"], [])

    def test_d7_e5_changed_or_removed_source_file(self):
        for action in ("change", "remove"):
            with self.subTest(action=action):
                self.setUp()
                p, edge, feed = self.setup_feed(iv_h=8, kind="files")
                fp = self.fp(p, feed)
                plan = fp.register_plan(edge)
                pid = plan["plan_id"]
                self.now[0] += 10 * DAY
                fp.step(pid)
                f = self.td / "fdata" / "funding" / "BTCUSDT-fundingRate-2024-08.csv"
                self.assertIn(f.name, fp.state(pid)["funding_sources"]["BTCUSDT"])
                plans_before = fp.plans_path.read_bytes()
                if action == "change":
                    f.write_text(f.read_text().replace("1e-05", "2e-05", 1))
                else:
                    f.unlink()
                self.now[0] += 65 * DAY
                r = fp.step(pid)
                self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA")
                self.assertEqual(fp.state(pid)["errors"][-1]["kind"], "FUNDING_SOURCE_CHANGED")
                self.assertTrue(any(("REMOVED" if action == "remove" else "CHANGED") in x
                                    for x in fp.state(pid)["errors"][-1]["detail"]))
                self.assertEqual(fp.plans_path.read_bytes(), plans_before)       # plan record untouched
                from paper_forward import ForwardRefused
                with self.assertRaises(ForwardRefused):                          # never a 2nd plan in this context
                    fp.register_plan(edge)

    def test_legacy_plan_is_never_passed(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        with mock.patch("paper_forward.FUNDING_POLICY", "pre-adr-0073"):
            fp = self.fp(p, feed)
            plan = fp.register_plan(edge)
        self.now[0] += 75 * DAY
        r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA")
        self.assertTrue(r["reasons"][0].startswith("LEGACY_PLAN_FUNDING_UNVERIFIED"))
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")

    def test_conclude_refuses_unconfirmed_funding(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        plan = fp.register_plan(edge)
        with self.assertRaises(RuntimeError):
            fp._conclude(plan, {"verdict": "PAPER_PASSED", "reasons": []}, {"funding_ok": False})


class TestForwardRestart(FundingCase):
    def test_e1_restart_after_normal_collection(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        self.now[0] += 20 * DAY
        fp.step(pid)
        st = fp.state(pid)
        fp2 = self.fp(p, feed)
        self.now[0] += 55 * DAY
        r = fp2.step(pid)
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        ts = [x["ts"] for x in iter_jsonl(fp.root / pid / "funding_BTCUSDT.jsonl")]
        self.assertEqual(ts, sorted(set(ts)))
        self.assertGreater(fp2.state(pid)["last_funding_ts"]["BTCUSDT"], st["last_funding_ts"]["BTCUSDT"])

    def test_e3_killed_after_funding_append_before_state_save(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        old_state = fp._state_path(pid).read_bytes()
        self.now[0] += 20 * DAY
        fp.step(pid)
        fp._state_path(pid).write_bytes(old_state)          # the state save never happened
        r = self.fp(p, feed).step(pid)
        ts = [x["ts"] for x in iter_jsonl(fp.root / pid / "funding_BTCUSDT.jsonl")]
        self.assertEqual(ts, sorted(set(ts)))                 # nothing appended twice
        self.assertEqual(r["funding"]["BTCUSDT"]["status"], "VALID")
        self.assertTrue(any(x.get("last_funding_ts") for x in fp.state(pid).get("recovered", [])))

    def test_e2_e6_rolled_back_state_cannot_unblock(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        good = fp._state_path(pid).read_bytes()
        bad_rows = [(t, r, h) for t, r, h in feed.rows]
        i = next(k for k, x in enumerate(bad_rows) if x[0] > _ms(self.START) + 2 * DAY)
        bad_rows[i], bad_rows[i + 1] = bad_rows[i + 1], bad_rows[i]          # inverted answer
        feed.rows = bad_rows
        self.now[0] += 5 * DAY
        r = fp.step(pid)
        self.assertEqual((r["verdict"], fp.state(pid)["status"]), ("PAPER_BLOCKED_DATA", "BLOCKED_DATA"))
        fp._state_path(pid).write_bytes(good)                 # an older OBSERVING state is put back
        feed.rows = sorted(bad_rows)
        self.now[0] += 70 * DAY
        r = self.fp(p, feed).step(pid)
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA")
        self.assertEqual(fp.state(pid)["errors"][-1]["kind"], "FUNDING_BLOCK_RESTORED")
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")

    def test_e4_corrupt_state_is_not_overwritten_or_recovered(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        self.now[0] += 5 * DAY
        fp.step(pid)
        path = fp._state_path(pid)
        st = json.loads(path.read_text())
        for damage in ("bad_status", "no_sources", "not_json"):
            with self.subTest(damage=damage):
                d = json.loads(json.dumps(st))
                if damage == "bad_status":
                    d["funding_status"]["BTCUSDT"] = "PROBABLY_FINE"
                    raw = json.dumps(d)
                elif damage == "no_sources":
                    del d["funding_sources"]
                    raw = json.dumps(d)
                else:
                    raw = json.dumps(d)[:-40]
                path.write_text(raw)
                self.now[0] += 70 * DAY
                r = fp.step(pid)
                self.assertEqual(r["verdict"], "PAPER_ERROR")
                self.assertTrue(r["reasons"][0].startswith("STATE_CORRUPT"))
                self.assertEqual(path.read_text(), raw)                  # kept as evidence
                self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")

    def test_tampered_funding_log_stops_with_error(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        self.now[0] += 5 * DAY
        fp.step(pid)
        log = fp.root / pid / "funding_BTCUSDT.jsonl"
        lines = log.read_text().splitlines()
        row = json.loads(lines[2])
        row["rate"] = 0.0
        lines[2] = json.dumps(row)
        log.write_text("\n".join(lines) + "\n")
        self.now[0] += 70 * DAY
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR")
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")


if __name__ == "__main__":
    unittest.main()
