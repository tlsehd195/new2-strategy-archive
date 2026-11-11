"""ADR 0074: Paper Forward temporal funding integrity.

Boundary of the observation window, settlement intervals that are only valid
at a point in time, and integrity of the evidence files (funding quality log,
warm-up file, funding log vs state).

SYNTHETIC data and temp dirs only.  No market data, no network (the public
REST feed is driven by a mock opener), no locked / IW / sealed window.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine_lib import canonical_json, iter_jsonl  # noqa: E402
from test_paper_forward_funding_20261111 import (  # noqa: E402
    DAY, HOUR, FundingCase, MockApi, _ms, declared_settlements, series,
)

MIN15 = 900_000


def rewrite_chain(path: Path, edit):
    """Rewrite a hash-chained log with ``edit(rows)`` and RECOMPUTE every hash,
    as a tamperer who knows the (unkeyed) chain rule would."""
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    rows = edit(rows)
    prev, out = "", []
    for r in rows:
        body = {k: v for k, v in r.items() if k not in ("hash", "prev")}
        h = hashlib.sha256((prev + canonical_json(body)).encode()).hexdigest()
        out.append(json.dumps({**body, "prev": prev, "hash": h}, sort_keys=True))
        prev = h
    path.write_text("\n".join(out) + ("\n" if out else ""))


# ---------------------------------------------------------------------------
# reproductions (written first; they FAIL on main ead5e17)
# ---------------------------------------------------------------------------

class TestReproduction0074(FundingCase):
    def test_repro_p0_1_due_exactly_at_window_end_is_missing(self):
        """main: last settlement 08:00, declared 4h, window ends 12:00, no 12:00
        row -> ``end - last > iv + tol`` is false -> OK."""
        from funding_schedule import coverage_check
        t0 = _ms("2024-01-01")
        pairs, ivs = series(t0, 3, 4)                     # 00:00, 04:00, 08:00
        c = coverage_check(pairs, ivs, t0 + 1, t0 + 12 * HOUR)
        self.assertNotEqual(c["status"], "OK", c)

    def test_repro_p0_1_full_path_boundary(self):
        """main: the same boundary through the forward path: the window
        status is VALID although the settlement due at the last bar is absent."""
        boundary = _ms(self.START) + 75 * DAY
        p, edge, feed = self.setup_feed(iv_h=4, drop=[boundary])
        fp = self.fp(p, feed)
        plan = fp.register_plan(edge)
        self.now[0] = boundary + MIN15                       # last closed bar opens at the boundary
        r = fp.step(plan["plan_id"])
        self.assertEqual(fp.state(plan["plan_id"])["last_ts"]["BTCUSDT"], boundary)
        self.assertNotIn(r["funding"]["BTCUSDT"]["status"], ("VALID", "VALID_ZERO_RATE"), r["funding"])
        self.assertNotEqual(r["verdict"], "PAPER_PASSED")

    def test_repro_p0_2_rest_current_interval_applied_to_history(self):
        """main: fundingInfo says 4h NOW; the history was 1h with one settlement
        missing (a 2h step).  Every row gets 4h -> VALID and coverage OK."""
        from funding_schedule import coverage_check
        from paper_forward import BinancePublicFeed
        t0 = _ms("2024-05-01")
        rows = [{"symbol": "BTCUSDT", "fundingTime": t0 + k * HOUR, "fundingRate": "0.0001"}
                for k in range(20) if k != 7]
        f = BinancePublicFeed(allow_network=True, opener=MockApi([{"symbol": "BTCUSDT",
                                                                    "fundingIntervalHours": 4}], [rows]),
                              clock=lambda: t0 + DAY, sleep=lambda s: None, attempts=1)
        fr = f.funding_checked("BTCUSDT", t0 - 1)
        cov = coverage_check(fr["rows"], fr["intervals"], t0, t0 + 19 * HOUR)
        self.assertNotEqual(cov["status"], "OK", cov)
        self.assertNotIn(fr["status"], ("VALID", "VALID_ZERO_RATE"))

    def _blocked_plan(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        bad = list(feed.rows)
        i = next(k for k, x in enumerate(bad) if x[0] > _ms(self.START) + 2 * DAY)
        bad[i], bad[i + 1] = bad[i + 1], bad[i]
        feed.rows = bad
        self.now[0] += 5 * DAY
        fp.step(pid)
        feed.rows = sorted(bad)
        return p, edge, feed, fp, pid

    def test_repro_p0_3_quality_log_edit_is_not_detected(self):
        """main: the DATA_INVALID quality record is edited to VALID: no error."""
        p, edge, feed, fp, pid = self._blocked_plan()
        q = fp.root / pid / "funding_quality_BTCUSDT.jsonl"
        q.write_text(q.read_text().replace('"DATA_INVALID"', '"VALID"'))
        self.now[0] += 70 * DAY
        r = self.fp(p, feed).step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])

    def test_repro_p0_3_quality_log_last_record_deleted(self):
        """main: the last quality record (the DATA_INVALID one) is removed: no error."""
        p, edge, feed, fp, pid = self._blocked_plan()
        q = fp.root / pid / "funding_quality_BTCUSDT.jsonl"
        lines = q.read_text().splitlines()
        q.write_text("\n".join(lines[:-1]) + ("\n" if lines[:-1] else ""))
        self.now[0] += 70 * DAY
        r = self.fp(p, feed).step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])

    def test_repro_p0_4_warmup_funding_edit_is_evaluated(self):
        """main: a warm-up funding rate is changed after registration; the plan
        is evaluated on it (no integrity check of warmup_<SYM>.json)."""
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        w = fp.root / pid / "warmup_BTCUSDT.json"
        j = json.loads(w.read_text())
        j["funding"][-1][1] = 0.05
        w.write_text(json.dumps(j))
        self.now[0] += 75 * DAY
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])

    def test_repro_p0_5_state_last_funding_ts_in_the_future(self):
        """main: last_funding_ts moved past the evidence is only type-checked."""
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        self.now[0] += 5 * DAY
        fp.step(pid)
        path = fp._state_path(pid)
        st = json.loads(path.read_text())
        st["last_funding_ts"]["BTCUSDT"] += 3 * DAY
        path.write_text(json.dumps(st))
        self.now[0] += 70 * DAY
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])

    def test_repro_p0_5_funding_log_rewritten_with_valid_chain(self):
        """main: a funding rate is changed and the chain recomputed; the state
        does not anchor the log, so the changed rate is charged."""
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        self.now[0] += 5 * DAY
        fp.step(pid)
        log = fp.root / pid / "funding_BTCUSDT.jsonl"

        def edit(rows):
            rows[3]["rate"] = 0.0
            return rows
        rewrite_chain(log, edit)
        self.now[0] += 70 * DAY
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])


# ---------------------------------------------------------------------------
# A. boundary of the observation window (funding_schedule.coverage_check)
# ---------------------------------------------------------------------------

class TestBoundary(unittest.TestCase):
    T0 = _ms("2024-01-01")
    TOL = 60_000

    def cov(self, pairs, ivs, a, b):
        from funding_schedule import coverage_check
        return coverage_check(pairs, ivs, a, b)

    def test_due_exactly_at_end_1h_4h_8h(self):
        for iv in (1, 4, 8):
            with self.subTest(iv=iv):
                pairs, ivs = series(self.T0, 10, iv)            # last at T0 + 9 iv
                due = self.T0 + 10 * iv * HOUR
                early = self.cov(pairs, ivs, self.T0 + 1, due - 15 * 60_000)
                self.assertEqual(early["status"], "OK", early)
                self.assertEqual(early["next_due_ts"], due)
                at = self.cov(pairs, ivs, self.T0 + 1, due)
                self.assertEqual((at["status"], at["kind"]), ("FUNDING_SETTLEMENT_GAP", "TAIL_DUE_MISSING"), at)
                past = self.cov(pairs, ivs, self.T0 + 1, due + 15 * 60_000)
                self.assertEqual(past["status"], "FUNDING_SETTLEMENT_GAP")
                full, fivs = series(self.T0, 11, iv)            # the due row exists
                self.assertEqual(self.cov(full, fivs, self.T0 + 1, due)["status"], "OK")

    def test_tolerance_edges(self):
        pairs, ivs = series(self.T0, 3, 4)
        due = self.T0 + 12 * HOUR
        # due iff due - tol <= end: inside the tolerance before the due time is already due
        self.assertEqual(self.cov(pairs, ivs, self.T0 + 1, due - self.TOL)["status"], "FUNDING_SETTLEMENT_GAP")
        self.assertEqual(self.cov(pairs, ivs, self.T0 + 1, due - self.TOL - 1)["status"], "OK")
        # the last settlement itself carries jitter: due a few ms after the end is still due
        jit = pairs[:-1] + [(pairs[-1][0] + 5, 1e-4)]
        jivs = {t: 4 * HOUR for t, _ in jit}
        self.assertEqual(self.cov(jit, jivs, self.T0 + 1, due)["status"], "FUNDING_SETTLEMENT_GAP")
        # a due row recorded with jitter after the end is found (it is in the series)
        late = pairs + [(due + 3, 1e-4)]
        livs = {t: 4 * HOUR for t, _ in late}
        self.assertEqual(self.cov(late, livs, self.T0 + 1, due)["status"], "OK")
        # ... but more than the tolerance late is a missing settlement
        late2 = pairs + [(due + self.TOL + 1, 1e-4)]
        l2 = {t: 4 * HOUR for t, _ in late2}
        self.assertEqual(self.cov(late2, l2, self.T0 + 1, due)["status"], "FUNDING_SETTLEMENT_GAP")

    def test_due_row_present_without_interval(self):
        pairs, ivs = series(self.T0, 4, 4)
        del ivs[pairs[-1][0]]
        c = self.cov(pairs, ivs, self.T0 + 1, pairs[-1][0])
        self.assertEqual(c["status"], "FUNDING_SCHEDULE_UNKNOWN", c)
        c = self.cov(pairs, ivs, self.T0 + 1, pairs[-1][0] - 30_000)  # due within the tolerance: tail
        self.assertEqual((c["status"], c["kind"]), ("FUNDING_SCHEDULE_UNKNOWN", "TAIL_SCHEDULE_UNKNOWN"), c)
        c = self.cov(pairs, ivs, self.T0 + 1, pairs[-1][0] - HOUR)    # not due yet inside the window
        self.assertEqual(c["status"], "OK", c)

    def test_schedule_change_just_before_the_end(self):
        a, ia = series(self.T0, 6, 8)                                  # 8h ... until T0 + 40h
        x = a[-1][0] + 4 * HOUR                                        # first 4h settlement
        pairs, ivs = a + [(x, 1e-4)], {**ia, x: 4 * HOUR}
        c = self.cov(pairs, ivs, self.T0 + 1, x + 4 * HOUR)            # next 4h one due at the end, absent
        self.assertEqual((c["status"], c["kind"]), ("FUNDING_SETTLEMENT_GAP", "TAIL_DUE_MISSING"), c)
        self.assertEqual(self.cov(pairs, ivs, self.T0 + 1, x + 3 * HOUR)["status"], "OK")

    def test_lengthening_change_is_ambiguous_not_ok(self):
        """4h -> 8h: 16:00 (4) then 00:00 (8).  Under 'interval ending at the
        row' nothing is missing; under 'interval starting at the row' 20:00 is
        missing.  The meaning is undocumented: SCHEDULE_UNKNOWN, never OK."""
        a, ia = series(self.T0, 5, 4)
        y = a[-1][0] + 8 * HOUR
        c = self.cov(a + [(y, 1e-4)], {**ia, y: 8 * HOUR}, self.T0 + 1, y)
        self.assertEqual((c["status"], c["kind"]), ("FUNDING_SCHEDULE_UNKNOWN", "SCHEDULE_CHANGE_AMBIGUOUS"), c)

    def test_window_check_is_never_weaker_than_the_trade_check(self):
        """Same holding rule (entry, exit] and tolerance: whenever the window is
        OK, every trade window inside it is OK too."""
        import random
        from funding_schedule import settlement_check
        rng = random.Random(7)
        for case in range(60):
            iv = rng.choice((1, 4, 8))
            n = 40
            drop = {rng.randrange(n)} if case % 2 else set()
            pairs, ivs = series(self.T0, n, iv, drop=drop)
            end = self.T0 + rng.randrange(1, (n - 1) * iv * 4) * 15 * 60_000
            c = self.cov(pairs, ivs, self.T0 + 1, end)
            if c["status"] != "OK":
                continue
            last = max(t for t, _ in pairs if t <= end)
            for _ in range(20):
                e0 = self.T0 + rng.randrange(0, max(1, (last - self.T0) // 900_000)) * 900_000
                e1 = min(last, e0 + rng.randrange(1, 40) * 900_000)
                self.assertEqual(settlement_check(pairs, ivs, e0, e1)["status"], "OK", (case, e0, e1))


class TestBoundaryForward(FundingCase):
    def test_full_path_boundary_8h_and_1h(self):
        for iv in (8, 1):
            with self.subTest(iv=iv):
                self.setUp()
                boundary = _ms(self.START) + 75 * DAY
                p, edge, feed = self.setup_feed(iv_h=iv, drop=[boundary])
                fp = self.fp(p, feed)
                plan = fp.register_plan(edge)
                self.now[0] = boundary + MIN15
                r = fp.step(plan["plan_id"])
                self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA", r["reasons"])
                self.assertEqual(r["funding"]["BTCUSDT"]["coverage"]["kind"], "TAIL_DUE_MISSING")
                self.assertFalse(fp.state(plan["plan_id"])["concluded"])
                self.assertIsNone(p.evaluation("paper_forward", edge))
                self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")
                # the settlement arrives late: the next step sees it and the plan can conclude
                feed.rows.append((boundary, 1e-5, iv))
                feed.rows.sort()
                self.now[0] = boundary + HOUR
                r = fp.step(plan["plan_id"])
                self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])


# ---------------------------------------------------------------------------
# B. public REST: no interval of the present is applied to the past
# ---------------------------------------------------------------------------

class RangeApi:
    """Mock opener answering /fapi/v1/fundingRate from a synthetic history by
    startTime/endTime/limit and /fapi/v1/fundingInfo from a schedule that
    depends on the (test) clock.  Records every URL; no network."""

    def __init__(self, rows, interval_at, clock, *, fail=None, listed=True):
        self.rows, self.interval_at, self.clock, self.fail, self.listed = rows, interval_at, clock, fail, listed
        self.urls = []

    def __call__(self, url):
        import urllib.error
        import urllib.parse
        self.urls.append(url)
        if self.fail and self.fail in url:
            raise urllib.error.HTTPError(url, 503, "unavailable", None, None)
        if "fundingInfo" in url:
            info = [{"symbol": "ETHUSDT", "fundingIntervalHours": 8}]
            if self.listed:
                info.append({"symbol": "BTCUSDT", "fundingIntervalHours": self.interval_at(self.clock())})
            return json.dumps(info).encode()
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        a, b, lim = int(q["startTime"]), int(q["endTime"]), int(q["limit"])
        out = [{"symbol": "BTCUSDT", "fundingTime": t, "fundingRate": f"{r:.8f}"}
               for t, r in self.rows if a <= t <= b][:lim]
        return json.dumps(out).encode()


class TestRestTemporal(unittest.TestCase):
    T0 = _ms("2024-05-01")

    def feed(self, api, clock, **kw):
        from paper_forward import BinancePublicFeed
        f = BinancePublicFeed(allow_network=True, opener=api, clock=clock, sleep=lambda s: None, attempts=1)
        for k, v in kw.items():
            setattr(f, k, v)
        return f

    def test_current_interval_is_never_back_dated(self):
        rows = [(self.T0 + k * HOUR, 1e-4) for k in range(48)]            # 1h history, complete
        now = [self.T0 + 2 * DAY]
        api = RangeApi(rows, lambda t: 4, lambda: now[0])
        fr = self.feed(api, lambda: now[0]).funding_checked("BTCUSDT", self.T0 - 1)
        self.assertEqual(fr["status"], "SCHEDULE_UNKNOWN")
        self.assertEqual(fr["intervals"], {})
        self.assertEqual(len(fr["rows"]), 48)
        obs = fr["schedule_observation"]
        self.assertEqual((obs["interval_ms"], obs["observed_at"], obs["listed"]), (4 * HOUR, now[0], True))
        self.assertEqual(obs["sha256"], fr["source_files"][0]["sha256"])
        self.assertTrue(all(u.startswith("https://fapi.binance.com/fapi/v1/funding") for u in api.urls))

    def test_schedule_changes_between_polls_never_valid(self):
        for a, b in ((8, 4), (4, 1), (1, 4)):
            with self.subTest(change=(a, b)):
                change = self.T0 + DAY
                rows, t = [], self.T0
                while t < self.T0 + 2 * DAY:
                    rows.append((t, 1e-4))
                    t += (a if t < change else b) * HOUR
                rows = [x for x in rows if x[0] != change + b * HOUR]       # one missing after the change
                now = [self.T0 + DAY - HOUR]
                api = RangeApi(rows, lambda c: a if c < change else b, lambda: now[0])
                f = self.feed(api, lambda: now[0])
                r1 = f.funding_checked("BTCUSDT", self.T0 - 1)
                now[0] = self.T0 + 2 * DAY
                r2 = f.funding_checked("BTCUSDT", r1["rows"][-1][0])
                for fr in (r1, r2):
                    self.assertEqual((fr["status"], fr["intervals"]), ("SCHEDULE_UNKNOWN", {}))
                self.assertEqual((r1["schedule_observation"]["interval_ms"],
                                  r2["schedule_observation"]["interval_ms"]), (a * HOUR, b * HOUR))

    def test_symbol_absent_info_ok_rate_failed_and_empty(self):
        now = self.T0 + DAY
        rows = [(self.T0 + k * 4 * HOUR, 1e-4) for k in range(6)]
        fr = self.feed(RangeApi(rows, lambda t: 4, lambda: now, listed=False), lambda: now).funding_checked(
            "BTCUSDT", self.T0 - 1)
        self.assertEqual(fr["status"], "SCHEDULE_UNKNOWN")
        self.assertFalse(fr["schedule_observation"]["listed"])
        fr = self.feed(RangeApi(rows, lambda t: 4, lambda: now, fail="fundingRate"), lambda: now).funding_checked(
            "BTCUSDT", self.T0 - 1)
        self.assertEqual(fr["status"], "SOURCE_ERROR")
        self.assertEqual(fr["schedule_observation"]["interval_ms"], 4 * HOUR)        # the snapshot is kept
        fr = self.feed(RangeApi([], lambda t: 4, lambda: now), lambda: now).funding_checked("BTCUSDT", self.T0 - 1)
        self.assertEqual((fr["status"], fr["rows"]), ("SCHEDULE_UNKNOWN", []))       # empty != error
        fr = self.feed(RangeApi(rows, lambda t: 4, lambda: now, fail="fundingInfo"), lambda: now).funding_checked(
            "BTCUSDT", self.T0 - 1)
        self.assertEqual(fr["status"], "SOURCE_ERROR")

    def test_pages_truncated_duplicate_inverted_symbol_zero(self):
        now = self.T0 + 2 * DAY
        rows = [(self.T0 + k * HOUR, 0.0 if k % 5 == 0 else 1e-4) for k in range(40)]
        api = RangeApi(rows, lambda t: 1, lambda: now)
        fr = self.feed(api, lambda: now, FUNDING_LIMIT=10, MAX_FUNDING_PAGES=2).funding_checked("BTCUSDT", self.T0 - 1)
        self.assertEqual((fr["status"], len(fr["rows"])), ("COVERAGE_INCOMPLETE", 20))
        fr = self.feed(api, lambda: now, FUNDING_LIMIT=10).funding_checked("BTCUSDT", self.T0 - 1)
        self.assertEqual((fr["status"], len(fr["rows"]), fr["zero_rate_rows"]), ("SCHEDULE_UNKNOWN", 40, 8))
        for bad in ("dup", "inv", "sym"):
            with self.subTest(bad=bad):
                pages = [self._page(rows[:10])]
                if bad == "dup":
                    pages.append(self._page(rows[9:19]))
                elif bad == "inv":
                    pages.append(self._page([rows[11], rows[10]] + rows[12:19]))
                else:
                    pages.append(self._page(rows[10:19], sym="ETHUSDT"))
                fr = self.feed(MockApi([{"symbol": "BTCUSDT", "fundingIntervalHours": 1}], pages), lambda: now,
                               FUNDING_LIMIT=10).funding_checked("BTCUSDT", self.T0 - 1)
                self.assertEqual(fr["status"], "DATA_INVALID", fr["issues"])

    @staticmethod
    def _page(rows, sym="BTCUSDT"):
        return [{"symbol": sym, "fundingTime": t, "fundingRate": f"{r:.8f}"} for t, r in rows]


class RestFeed:
    """Synthetic bars + funding through the real BinancePublicFeed (mock opener)."""

    realtime = True
    kind = "binance_public_rest"

    def __init__(self, bars_feed, rest):
        self.bars_feed, self.rest = bars_feed, rest

    def closed_bars(self, sym, after_ts):
        return self.bars_feed.closed_bars(sym, after_ts)

    def funding_checked(self, sym, after_ts):
        return self.rest.funding_checked(sym, after_ts)


class TestRestForward(FundingCase):
    def test_rest_registration_is_refused_nothing_written(self):
        from paper_forward import BinancePublicFeed, ForwardRefused
        p, edge, feed = self.ready(start=self.START)
        rows = [(t, r) for t, r, _ in declared_settlements(feed.bars[0].ts, feed.bars[-1].ts, 4)]
        api = RangeApi(rows, lambda t: 4, feed.clock)
        rest = BinancePublicFeed(allow_network=True, opener=api, clock=feed.clock, sleep=lambda s: None, attempts=1)
        fp = self.fp(p, RestFeed(feed, rest))
        with self.assertRaisesRegex(ForwardRefused, "SCHEDULE_UNKNOWN"):
            fp.register_plan(edge)
        self.assertEqual(fp.plans(), [])
        self.assertEqual([x.name for x in fp.root.iterdir() if x.is_dir()], [])
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")



# ---------------------------------------------------------------------------
# C/D/E. evidence files: quality log, warm-up file, funding log vs state
# ---------------------------------------------------------------------------

class EvidenceCase(FundingCase):
    def plan_with_steps(self, n=3, *, invalid_at=None):
        """A 4h plan observed in ``n`` steps of 2 days (quality records 1..n).
        ``invalid_at``: the step (1-based) whose answer is inverted (DATA_INVALID)."""
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        good = list(feed.rows)
        for k in range(1, n + 1):
            if k == invalid_at:
                bad = list(good)
                i = next(j for j, x in enumerate(bad) if x[0] > self.now[0] + 4 * HOUR)
                bad[i], bad[i + 1] = bad[i + 1], bad[i]
                feed.rows = bad
            self.now[0] += 2 * DAY
            fp.step(pid)
            feed.rows = good
        return p, edge, feed, fp, pid

    def finish(self, p, feed, pid, days=70):
        self.now[0] += days * DAY
        return self.fp(p, feed).step(pid)

    def assert_error(self, r, p, edge, kind=None):
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])
        if kind:
            self.assertTrue(any(kind in x for x in r["reasons"]), (kind, r["reasons"]))
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")
        self.assertIsNone(p.evaluation("paper_forward", edge))


class TestQualityLogIntegrity(EvidenceCase):
    def test_normal_log_is_chained_and_anchored(self):
        p, edge, feed, fp, pid = self.plan_with_steps(3)
        from paper_forward import read_seq_chain
        q = read_seq_chain(fp.root / pid / "funding_quality_BTCUSDT.jsonl", "QUALITY_LOG")
        self.assertEqual([x["seq"] for x in q], [1, 2, 3])
        for k in ("seq", "prev", "hash", "at", "symbol", "status", "source_files", "query", "issues", "policy"):
            self.assertIn(k, q[0])
        st = fp.state(pid)
        self.assertEqual(st["quality_head"]["BTCUSDT"], {"seq": 3, "hash": q[-1]["hash"]})
        self.assertEqual(self.finish(p, feed, pid)["verdict"], "PAPER_PASSED")

    def test_tampering_variants(self):
        def lines_edit(fn):
            def go(path):
                ls = path.read_text().splitlines()
                path.write_text("\n".join(fn(ls)) + "\n" if fn(ls) else "")
            return go

        def field(i, k, v):
            def fn(ls):
                r = json.loads(ls[i])
                r[k] = v
                return ls[:i] + [json.dumps(r, sort_keys=True)] + ls[i + 1:]
            return fn
        cases = {
            "content": (lines_edit(field(1, "rows", 999)), "QUALITY_LOG_CHAIN"),
            "middle_status": (lines_edit(field(1, "status", "SOURCE_ERROR")), "QUALITY_LOG_CHAIN"),
            "delete_middle": (lines_edit(lambda ls: ls[:1] + ls[2:]), "QUALITY_LOG_SEQUENCE"),
            "delete_last": (lines_edit(lambda ls: ls[:-1]), "QUALITY_LOG_TRUNCATED"),
            "reorder": (lines_edit(lambda ls: [ls[1], ls[0]] + ls[2:]), "QUALITY_LOG_SEQUENCE"),
            "torn_last": (lambda path: path.write_text(path.read_text()[:-30]), "QUALITY_LOG_TRUNCATED"),
            "delete_file": (lambda path: path.unlink(), "QUALITY_LOG_TRUNCATED"),
            "hash": (lines_edit(field(2, "hash", "0" * 64)), "QUALITY_LOG_CHAIN"),
            "prev": (lines_edit(field(1, "prev", "0" * 64)), "QUALITY_LOG_CHAIN"),
            "dup_seq": (lines_edit(field(2, "seq", 2)), "QUALITY_LOG_SEQUENCE"),
            "seq_reversed": (lines_edit(lambda ls: ls[:1] + [ls[2], ls[1]]), "QUALITY_LOG_SEQUENCE"),
            "rehashed_rewrite": (lambda path: rewrite_chain(path, lambda rows: [
                {**r, "issues": ["edited"]} if r["seq"] == 2 else r for r in rows]), "QUALITY_LOG_REWRITTEN"),
            "corrupt_line": (lines_edit(lambda ls: ls[:1] + ["{not json"] + ls[2:]), "QUALITY_LOG_CORRUPT"),
        }
        for name, (tamper, kind) in cases.items():
            with self.subTest(case=name):
                self.setUp()
                p, edge, feed, fp, pid = self.plan_with_steps(3)
                q = fp.root / pid / "funding_quality_BTCUSDT.jsonl"
                tamper(q)
                after = q.read_bytes() if q.exists() else None
                r = self.finish(p, feed, pid)
                self.assert_error(r, p, edge, kind)
                self.assertEqual(q.read_bytes() if q.exists() else None, after)      # never repaired

    def test_data_invalid_record_deleted_or_state_rolled_back(self):
        # DATA_INVALID at step 2 of 3: deleting it breaks the sequence
        p, edge, feed, fp, pid = self.plan_with_steps(1, invalid_at=1)
        self.assertEqual(fp.state(pid)["status"], "BLOCKED_DATA")
        q = fp.root / pid / "funding_quality_BTCUSDT.jsonl"
        q.write_text("")
        self.assert_error(self.finish(p, feed, pid), p, edge, "QUALITY_LOG_TRUNCATED")
        # an older OBSERVING state put back: the log is ahead, the block is restored
        self.setUp()
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        old = fp._state_path(pid).read_bytes()
        good = list(feed.rows)
        self.now[0] += 2 * DAY
        fp.step(pid)                                   # VALID
        bad = list(good)
        i = next(j for j, x in enumerate(bad) if x[0] > self.now[0] + 4 * HOUR)
        bad[i], bad[i + 1] = bad[i + 1], bad[i]
        feed.rows = bad
        self.now[0] += 2 * DAY
        fp.step(pid)                                   # DATA_INVALID
        feed.rows = good
        fp._state_path(pid).write_bytes(old)
        r = self.finish(p, feed, pid)
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA", r["reasons"])
        st = fp.state(pid)
        self.assertEqual(st["errors"][-1]["kind"], "FUNDING_BLOCK_RESTORED")
        self.assertEqual(st["quality_head"]["BTCUSDT"]["seq"], 2)
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")

    def test_state_and_log_hash_disagree(self):
        p, edge, feed, fp, pid = self.plan_with_steps(2)
        path = fp._state_path(pid)
        st = json.loads(path.read_text())
        st["quality_head"]["BTCUSDT"]["hash"] = "f" * 64
        path.write_text(json.dumps(st))
        self.assert_error(self.finish(p, feed, pid), p, edge, "QUALITY_LOG_REWRITTEN")

    def test_killed_after_quality_append_before_state_save(self):
        p, edge, feed, fp, pid = self.plan_with_steps(2)
        saved = fp._state_path(pid).read_bytes()
        self.now[0] += 2 * DAY
        fp.step(pid)                                   # appends quality #3 + funding rows
        fp._state_path(pid).write_bytes(saved)         # ... but the state save "never happened"
        r = self.finish(p, feed, pid)
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        st = fp.state(pid)
        self.assertTrue(any(x.get("quality_seq") == 3 for x in st.get("recovered", [])), st.get("recovered"))
        ts = [x["ts"] for x in iter_jsonl(fp.root / pid / "funding_BTCUSDT.jsonl")]
        self.assertEqual(ts, sorted(set(ts)))


class TestWarmupIntegrity(EvidenceCase):
    def test_plan_fixes_the_warmup_sha256(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        plan = fp.register_plan(edge)
        w = fp.root / plan["plan_id"] / "warmup_BTCUSDT.json"
        self.assertEqual(plan["warmup_sha256"]["BTCUSDT"], hashlib.sha256(w.read_bytes()).hexdigest())
        body = {k: v for k, v in plan.items() if k not in ("plan_id", "at")}
        self.assertEqual(plan["plan_id"], "FP-" + hashlib.sha256(canonical_json(body).encode()).hexdigest()[:16])
        self.assertEqual(plan["funding_integrity"], "paper-forward-integrity-v1")

    def test_tampering_variants(self):
        def j_edit(fn):
            def go(path):
                j = json.loads(path.read_text())
                fn(j)
                path.write_text(json.dumps(j))
            return go

        def swap(j):
            j["funding"][0], j["funding"][1] = j["funding"][1], j["funding"][0]
        cases = {
            "rate": j_edit(lambda j: j["funding"][-1].__setitem__(1, 0.05)),
            "timestamp": j_edit(lambda j: j["funding"][-2].__setitem__(0, j["funding"][-2][0] + HOUR)),
            "interval": j_edit(lambda j: j["funding_intervals"].__setitem__(
                next(iter(j["funding_intervals"])), 8 * HOUR)),
            "add_row": j_edit(lambda j: j["funding"].append([j["funding"][-1][0] + 1, 0.0])),
            "delete_row": j_edit(lambda j: j["funding"].pop(3)),
            "reorder": j_edit(swap),
            "bar": j_edit(lambda j: j["rows"][-1].__setitem__("c", j["rows"][-1]["c"] * 1.01)),
            "same_content_reformatted": j_edit(lambda j: None),
            "truncated": lambda path: path.write_bytes(path.read_bytes()[:-100]),
            "not_json": lambda path: path.write_text("{"),
            "deleted": lambda path: path.unlink(),
            "other_symbol": j_edit(lambda j: j.__setitem__("symbol", "ETHUSDT")),
        }
        for name, tamper in cases.items():
            with self.subTest(case=name):
                self.setUp()
                p, edge, feed = self.setup_feed(iv_h=4)
                fp = self.fp(p, feed)
                pid = fp.register_plan(edge)["plan_id"]
                w = fp.root / pid / "warmup_BTCUSDT.json"
                if name != "deleted":
                    self.now[0] += 2 * DAY
                    fp.step(pid)
                tamper(w)
                after = w.read_bytes() if w.exists() else None
                r = self.finish(p, feed, pid, days=73)
                self.assert_error(r, p, edge, "WARMUP_MISSING" if name == "deleted" else "WARMUP_TAMPERED")
                self.assertEqual(w.read_bytes() if w.exists() else None, after)   # never rewritten

    def test_plan_record_sha_changed(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        rec = json.loads(fp.plans_path.read_text())
        w = fp.root / pid / "warmup_BTCUSDT.json"
        j = json.loads(w.read_text())
        j["funding"][-1][1] = 0.05
        from paper_forward import _warmup_bytes
        w.write_bytes(_warmup_bytes(j))
        rec["warmup_sha256"]["BTCUSDT"] = hashlib.sha256(w.read_bytes()).hexdigest()   # plan edited to match
        fp.plans_path.write_text(json.dumps(rec) + "\n")
        r = self.finish(p, feed, pid, days=75)
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])
        self.assertIn("plan record does not match its id", " ".join(r["reasons"]))
        self.assertIsNone(p.evaluation("paper_forward", edge))


class TestStateFundingLog(EvidenceCase):
    def _state(self, fp, pid, fn):
        path = fp._state_path(pid)
        st = json.loads(path.read_text())
        fn(st)
        path.write_text(json.dumps(st))

    def test_state_behind_log_recovers(self):
        p, edge, feed, fp, pid = self.plan_with_steps(1)
        saved = fp._state_path(pid).read_bytes()
        self.now[0] += 2 * DAY
        fp.step(pid)
        fp._state_path(pid).write_bytes(saved)
        r = self.finish(p, feed, pid)
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])

    def test_state_ahead_or_inconsistent(self):
        cases = {
            "future_ts": (lambda st: st["last_funding_ts"].__setitem__("BTCUSDT", st["last_funding_ts"]["BTCUSDT"]
                                                                       + 3 * DAY), "STATE_AHEAD_OF_EVIDENCE"),
            "earlier_ts_same_head": (lambda st: st["last_funding_ts"].__setitem__(
                "BTCUSDT", st["last_funding_ts"]["BTCUSDT"] - 4 * HOUR), "STATE_FUNDING_TS_MISMATCH"),
            "head_beyond_log": (lambda st: st["funding_head"]["BTCUSDT"].__setitem__(
                "seq", st["funding_head"]["BTCUSDT"]["seq"] + 1), "FUNDING_LOG_TRUNCATED"),
        }
        for name, (fn, kind) in cases.items():
            with self.subTest(case=name):
                self.setUp()
                p, edge, feed, fp, pid = self.plan_with_steps(2)
                self._state(fp, pid, fn)
                self.assert_error(self.finish(p, feed, pid), p, edge, kind)

    def test_funding_log_tampering(self):
        def drop_middle(rows):
            return rows[:3] + rows[4:]

        def dup(rows):
            return rows[:3] + [{**rows[3], "ts": rows[2]["ts"], "seq": rows[3]["seq"]}] + rows[4:]

        def inv(rows):
            a, b = dict(rows[3]), dict(rows[4])
            a["ts"], b["ts"] = b["ts"], a["ts"]
            return rows[:3] + [a, b] + rows[5:]

        def overlap(rows):
            return [{**rows[0], "ts": rows[0]["ts"] - 4 * HOUR}] + rows[1:]

        def rate(rows):
            return rows[:2] + [{**rows[2], "rate": 0.0}] + rows[3:]
        for name, edit, kind, also_state in (
                ("delete_middle_rehashed", drop_middle, "FUNDING_LOG_SEQUENCE", False),
                ("rate_rehashed", rate, "FUNDING_LOG_REWRITTEN", False),
                ("duplicate_ts_state_also_edited", dup, "FUNDING_LOG_ORDER", True),
                ("inverted_ts_state_also_edited", inv, "FUNDING_LOG_ORDER", True),
                ("overlaps_warmup_state_also_edited", overlap, "FUNDING_LOG_ORDER", True)):
            with self.subTest(case=name):
                self.setUp()
                p, edge, feed, fp, pid = self.plan_with_steps(2)
                log = fp.root / pid / "funding_BTCUSDT.jsonl"
                rewrite_chain(log, edit)
                if also_state:
                    rows = [json.loads(x) for x in log.read_text().splitlines()]
                    self._state(fp, pid, lambda st: st["funding_head"].__setitem__(
                        "BTCUSDT", {"seq": len(rows), "hash": rows[-1]["hash"]}))
                self.assert_error(self.finish(p, feed, pid), p, edge, kind)
        # a broken (not recomputed) chain and a deleted log
        for name in ("broken_chain", "deleted"):
            with self.subTest(case=name):
                self.setUp()
                p, edge, feed, fp, pid = self.plan_with_steps(2)
                log = fp.root / pid / "funding_BTCUSDT.jsonl"
                if name == "deleted":
                    log.unlink()
                else:
                    ls = log.read_text().splitlines()
                    r = json.loads(ls[2])
                    r["rate"] = 1.0
                    ls[2] = json.dumps(r)
                    log.write_text("\n".join(ls) + "\n")
                self.assert_error(self.finish(p, feed, pid), p, edge,
                                  "FUNDING_LOG_TRUNCATED" if name == "deleted" else "FUNDING_LOG_CHAIN")

    def test_restart_keeps_data_invalid(self):
        p, edge, feed, fp, pid = self.plan_with_steps(2, invalid_at=2)
        r = self.finish(p, feed, pid)
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA")
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")


# ---------------------------------------------------------------------------
# F. final gate: verdict and conclusion re-check everything
# ---------------------------------------------------------------------------

class TestFinalGate(EvidenceCase):
    def test_conclusion_rechecks_evidence_after_the_verdict(self):
        """Evidence changed between the verdict and the record: the conclusion
        gate refuses on its own; no record, no status change, not concluded."""
        from paper_forward import ForwardPaper
        p, edge, feed, fp, pid = self.plan_with_steps(1)
        orig = ForwardPaper._verdict

        def verdict_then_tamper(self_, plan, st, result):
            v = orig(self_, plan, st, result)
            w = self_.root / plan["plan_id"] / "warmup_BTCUSDT.json"
            w.write_bytes(w.read_bytes().replace(b'"start_ts"', b'"start_ts" ', 1))
            return v
        from unittest import mock
        with mock.patch.object(ForwardPaper, "_verdict", verdict_then_tamper):
            r = self.finish(p, feed, pid)
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA", r["reasons"])
        self.assertTrue(r["reasons"][0].startswith("CONCLUSION_REFUSED"), r["reasons"])
        self.assertIn("WARMUP_TAMPERED", r["reasons"][0])
        self.assertFalse(fp.state(pid)["concluded"])
        self.assertIsNone(p.evaluation("paper_forward", edge))
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")
        self.assertFalse((p.dir / "research_feedback.jsonl").exists()
                         and any(x.get("source") == "paper_forward"
                                 for x in iter_jsonl(p.dir / "research_feedback.jsonl")))

    def test_conclusion_refuses_drops_and_coverage_by_itself(self):
        from paper_forward import ConclusionRefused
        p, edge, feed, fp, pid = self.plan_with_steps(1)
        plan = fp.plan(pid)
        st = fp.state(pid)
        for result in ({"funding_ok": True, "excluded": {"no_funding_coverage": 1}},
                       {"funding_ok": True, "funding_dropped": {"events": 1}},
                       {"funding_ok": None}):
            with self.assertRaises(ConclusionRefused):
                fp._conclude(plan, {"verdict": "PAPER_PASSED", "reasons": []}, result, st)
        # coverage re-computed from disk: the state claims bars the funding does not cover
        st2 = json.loads(json.dumps(st))
        st2["last_ts"]["BTCUSDT"] += 30 * DAY
        with self.assertRaisesRegex(ConclusionRefused, "funding"):
            fp._conclude(plan, {"verdict": "PAPER_PASSED", "reasons": []},
                         {"funding_ok": True, "excluded": {"no_funding_coverage": 0}}, st2)
        self.assertIsNone(p.evaluation("paper_forward", edge))

    def test_pre_adr0074_plan_is_never_concluded(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        plan = fp.register_plan(edge)
        rec = {k: v for k, v in plan.items() if k not in ("funding_integrity", "warmup_sha256", "warmup_hash_basis")}
        fp.plans_path.write_text(json.dumps(rec) + "\n")
        r = self.finish(p, feed, plan["plan_id"], days=75)
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA")
        self.assertTrue(r["reasons"][0].startswith("LEGACY_PLAN_INTEGRITY_UNVERIFIED"))
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")

    def test_no_defect_reaches_passed_after_restart(self):
        """Each defect, then a restart with a fresh engine: still not PASSED."""
        boundary = _ms(self.START) + 75 * DAY
        p, edge, feed = self.setup_feed(iv_h=4, drop=[boundary])
        fp = self.fp(p, feed)
        pid = fp.register_plan(edge)["plan_id"]
        self.now[0] = boundary + MIN15
        self.assertNotEqual(fp.step(pid)["verdict"], "PAPER_PASSED")
        self.assertNotEqual(self.fp(p, feed).step(pid)["verdict"], "PAPER_PASSED")
        self.assertIsNone(p.evaluation("paper_forward", edge))


if __name__ == "__main__":
    unittest.main()
