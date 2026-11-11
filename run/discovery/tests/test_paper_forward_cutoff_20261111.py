"""ADR 0076: the evaluation cutoff of Paper Forward is the earliest bar at which
the preregistered stopping rule holds — independent of when step() runs, never
past ``max_days``, verified against the ledger — and a window the clock has
passed without its bars being observed is never concluded.

Covers the independent audit of ADR 0075 (2026-11-11): F-1 (multi-symbol
sample bound), F-2 (max_days on a stalled feed), 7.3 (anchors of the final
verdict), and the step-cadence dependence of the cutoff found while
reproducing F-2.

SYNTHETIC data and temp dirs only.  No market data, no network, no locked /
IW / sealed window.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine_lib import iter_jsonl  # noqa: E402
from test_paper_forward_positions_20261111 import MIN15, PositionCase, read_log, write_log  # noqa: E402
from test_paper_forward_funding_20261111 import DAY  # noqa: E402


class CutoffCase(PositionCase):
    """EDGE enters at 00:15 every day and holds 8 bars (exit 02:15): one trade a day."""

    def concluded(self, days_list, minutes=0):
        p, edge, feed, fp, plan = self.plan()
        for d in days_list:
            self.at(plan, d, minutes)
            r = fp.step(plan["plan_id"])
        return p, edge, fp, plan, r, p.evaluation("paper_forward", edge)


# ---------------------------------------------------------------------------
# reproductions (written first; they FAIL on main 4fd25d2)
# ---------------------------------------------------------------------------

class TestReproduction0076(CutoffCase):
    def test_repro_sample_depends_on_when_step_runs(self):
        """main: the cutoff is the last bar of the step at which the rule first
        holds: one step at day 75 concludes on 75 days / 75 trades, one at day
        61 on 61 / 61 — the operator's step time chooses the sample.  The rule
        (60 days, 30 trades) first holds at start + 60 days - 15 m."""
        p, edge, fp, plan, r, rec = self.concluded([75])
        t = plan["start_ts"] + 60 * DAY - MIN15
        self.assertEqual(rec["positions"]["evaluation_cutoff_ts"], t)
        self.assertEqual(rec["metrics"]["days_observed"], 60.0)
        self.assertEqual(rec["metrics"]["n_trades"], 60)

    def test_repro_stalled_feed_is_never_concluded_nor_blocked(self):
        """main (audit F-2): bars stop at day 20, the clock reaches day 200 (>
        max_days 180): days_observed stays 20 and the plan is PAPER_OBSERVING
        for ever.  The window is over on the clock and its bars were not
        observed: PAPER_BLOCKED_DATA (non-final), never INCONCLUSIVE."""
        p, edge, feed, fp, plan = self.plan()
        feed.bars[:] = [b for b in feed.bars if b.ts < plan["start_ts"] + 20 * DAY]
        self.at(plan, 200, 0)
        r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA", r["reasons"])
        self.assertIn("OBSERVATION_WINDOW_NOT_COVERED", r["reasons"][0])
        self.assertIsNone(p.evaluation("paper_forward", edge))
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")


class TestReproductionWindow(CutoffCase):
    CRIT = {"min_days": 60, "max_days": 70, "min_trades": 80}

    def test_repro_sample_runs_past_max_days(self):
        """main: one step at day 90 with max_days 70 evaluates 90 days; 90 >=
        min_trades 80, so the plan PASSES on bars after its own preregistered
        window.  The window ends at day 70 with 70 trades < 80: INCONCLUSIVE."""
        p, edge, fp, plan, r, rec = self.concluded([90])
        self.assertEqual(rec["verdict"], "PAPER_INCONCLUSIVE", rec["reasons"])
        self.assertEqual(rec["metrics"]["days_observed"], 70.0)
        self.assertEqual(rec["positions"]["evaluation_cutoff_ts"], plan["start_ts"] + 70 * DAY - MIN15)
        self.assertEqual(p.statuses.status(edge, p.context_id()), "INCONCLUSIVE")


# ---------------------------------------------------------------------------
# the cutoff does not depend on the step cadence
# ---------------------------------------------------------------------------

class TestCadence(CutoffCase):
    def check(self, rec, plan):
        self.assertEqual(rec["verdict"], "PAPER_PASSED", rec["reasons"])
        self.assertEqual(rec["positions"]["evaluation_cutoff_ts"], plan["start_ts"] + 60 * DAY - MIN15)
        self.assertEqual((rec["metrics"]["n_trades"], rec["metrics"]["days_observed"]), (60, 60.0))
        self.assertEqual(rec["anchors"]["cutoff_rule"], "paper-forward-cutoff-v1")

    def test_daily_steps(self):
        p, edge, fp, plan, r, rec = self.concluded(range(1, 70))
        self.check(rec, plan)

    def test_steps_every_15_minutes_around_the_rule(self):
        p, edge, feed, fp, plan = self.plan()
        self.at(plan, 59, 0)
        fp.step(plan["plan_id"])
        for m in range(0, 24 * 60 + 60, 15):
            self.now[0] = plan["start_ts"] + 59 * DAY + m * 60_000
            r = fp.step(plan["plan_id"])
            if r.get("status") == "CONCLUDED":
                break
        self.check(p.evaluation("paper_forward", edge), plan)

    def test_one_late_step(self):
        p, edge, fp, plan, r, rec = self.concluded([150])
        self.check(rec, plan)
        self.assertGreater(rec["metrics"]["trades_entered_after_bound"], 80)

    def test_metrics_only_cover_the_sample_period(self):
        """A gap after the cutoff is not part of the sample's data coverage."""
        p, edge, feed, fp, plan = self.plan()
        a, b = plan["start_ts"] + 100 * DAY, plan["start_ts"] + 101 * DAY
        feed.bars[:] = [x for x in feed.bars if not (a <= x.ts < b)]
        self.at(plan, 120, 0)
        fp.step(plan["plan_id"])
        rec = p.evaluation("paper_forward", edge)
        self.assertEqual(rec["metrics"]["missing_bar_share"], 0.0)
        self.assertEqual(rec["verdict"], "PAPER_PASSED", rec["reasons"])


class TestPendingAtTheRule(CutoffCase):
    # 60.05 days -> the rule first holds at 01:00 of day 60, inside the 00:15-02:15 hold
    CRIT = {"min_days": 60.05}

    def test_position_open_at_the_rule_is_waited_for(self):
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        t = plan["start_ts"] + 60 * DAY + 60 * 60_000
        self.at(plan, 60, 90)                       # last bar 01:15
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_OBSERVING", r["reasons"])
        self.assertIn("OPEN_POSITIONS_PENDING", r["reasons"][0])
        self.assertEqual(fp.state(pid)["evaluation_cutoff"]["ts"], t)
        self.at(plan, 61, 0)
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assertEqual(r["metrics"]["n_trades"], 61)        # days 0..60, the pending one included

    def test_same_sample_when_the_first_step_is_late(self):
        p, edge, fp, plan, r, rec = self.concluded([90])
        self.assertEqual(rec["positions"]["evaluation_cutoff_ts"], plan["start_ts"] + 60 * DAY + 60 * 60_000)
        self.assertEqual(rec["metrics"]["n_trades"], 61)

    def test_cutoff_moved_to_another_bar_where_the_rule_holds(self):
        """Audit 7.3: state edited to move the cutoff to a later bar at which the
        rule also holds -> EVALUATION_CUTOFF_INVALID (main accepted it)."""
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 60, 90)
        fp.step(pid)
        path = fp._state_path(pid)
        st = json.loads(path.read_text())
        st["evaluation_cutoff"]["ts"] += MIN15
        path.write_text(json.dumps(st))
        self.at(plan, 61, 0)
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])
        self.assertIn("EVALUATION_CUTOFF_INVALID", r["reasons"][0])
        self.assertIsNone(p.evaluation("paper_forward", edge))


class TestStalledFeed(CutoffCase):
    def test_lag_is_reported_while_the_window_is_open(self):
        p, edge, feed, fp, plan = self.plan()
        feed.bars[:] = [b for b in feed.bars if b.ts < plan["start_ts"] + 20 * DAY]
        self.at(plan, 40, 0)
        r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_OBSERVING", r["reasons"])
        self.assertTrue(any(x.startswith("DATA_LAG") for x in r["reasons"]), r["reasons"])
        self.assertAlmostEqual(r["observation_clock"]["data_days"], 20.0)

    def test_data_arriving_later_resumes_the_plan(self):
        p, edge, feed, fp, plan = self.plan()
        full = list(feed.bars)
        feed.bars[:] = [b for b in full if b.ts < plan["start_ts"] + 20 * DAY]
        self.at(plan, 200, 0)
        self.assertEqual(fp.step(plan["plan_id"])["verdict"], "PAPER_BLOCKED_DATA")
        feed.bars[:] = full                          # the archive catches up
        r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assertEqual(r["metrics"]["days_observed"], 60.0)


class TestStalledFeedFewTrades(CutoffCase):
    CRIT = {"min_trades": 500}

    def test_window_covered_without_enough_trades_is_inconclusive(self):
        p, edge, fp, plan, r, rec = self.concluded([181])
        self.assertEqual(rec["verdict"], "PAPER_INCONCLUSIVE", rec["reasons"])
        self.assertEqual(rec["metrics"]["days_observed"], 180.0)


# ---------------------------------------------------------------------------
# F-1: multi-symbol sample bound (the rule on a two-symbol ledger)
# ---------------------------------------------------------------------------

class TestMultiSymbolRule(unittest.TestCase):
    """Audit F-1 suggested a later (max / per-symbol) bound.  The bound is the
    common observation time on purpose: before it, every entry of every symbol
    is known (closed in the ledger or open at its last bar); after it, entries
    of the lagging symbol are not.  With the ADR 0076 rule the cutoff is the
    same whichever symbol lags, and the lag only delays it."""

    PLAN = {"start_ts": 0, "feed_realtime": False,
            "criteria": {"min_days": 1, "max_days": 3, "min_trades": 3}}

    def trade(self, sym, entry_h, exit_h):
        return {"symbol": sym, "entry_ts": entry_h * 3_600_000, "exit_ts": exit_h * 3_600_000,
                "exec_status": "FILLED", "late": False}

    def rule(self, last_a, last_b, ledger):
        from paper_forward import ForwardPaper
        st = {"last_ts": {"A": last_a * 3_600_000 - MIN15, "B": last_b * 3_600_000 - MIN15}}
        return ForwardPaper._rule_cutoff(self.PLAN, st, ledger)

    def test_cutoff_does_not_depend_on_which_symbol_lags(self):
        led = [self.trade("A", 1, 2), self.trade("B", 3, 5), self.trade("A", 20, 30), self.trade("B", 26, 28)]
        t = 28 * 3_600_000          # 3rd exit (B 28h) > min_days bar (24h - 15m)
        self.assertEqual(self.rule(40, 40, led), t)
        self.assertIsNone(self.rule(40, 28, led))          # B observed to 27:45 only: wait
        self.assertIsNone(self.rule(28, 40, led))          # A observed to 27:45 only: wait
        self.assertEqual(self.rule(29, 60, led), t)
        self.assertEqual(self.rule(60, 29, led), t)

    def test_lagging_symbol_entries_are_never_cut_by_a_leading_one(self):
        """A is observed to 45h, B to 24h.  A max() bound (45h) would decide on a
        sample missing B's entries in (24h, 45h], which are not known yet."""
        led = [self.trade("A", 1, 2), self.trade("A", 5, 6), self.trade("A", 30, 40)]
        self.assertIsNone(self.rule(45, 24, led))
        led_b = led + [self.trade("B", 25, 26)]                # B's trade, once B is observed
        self.assertEqual(self.rule(45, 45, led_b), 26 * 3_600_000)

    def test_sample_bound_before_the_rule_is_the_common_time_capped_at_the_window(self):
        from paper_forward import ForwardPaper
        st = {"last_ts": {"A": 50 * 3_600_000, "B": 40 * 3_600_000}}
        self.assertEqual(ForwardPaper._sample_bound(self.PLAN, st), 40 * 3_600_000)
        st = {"last_ts": {"A": 500 * 3_600_000, "B": 400 * 3_600_000}}
        self.assertEqual(ForwardPaper._sample_bound(self.PLAN, st), 72 * 3_600_000 - MIN15)

    def test_max_days_caps_the_rule(self):
        led = [self.trade("A", h, h + 1) for h in (70, 71, 80)]
        self.assertEqual(self.rule(100, 100, led), 72 * 3_600_000 - MIN15)


# ---------------------------------------------------------------------------
# 7.3: anchors of the final verdict
# ---------------------------------------------------------------------------

class TestConclusionAnchors(CutoffCase):
    def passed(self):
        p, edge, fp, plan, r, rec = self.concluded([75])
        self.assertEqual(rec["verdict"], "PAPER_PASSED", rec["reasons"])
        return p, edge, fp, plan, rec

    def test_untouched_files_verify(self):
        p, edge, fp, plan, rec = self.passed()
        a = rec["anchors"]
        self.assertEqual(a["evaluation_cutoff_ts"], rec["positions"]["evaluation_cutoff_ts"])
        self.assertEqual(a["events_logged"], len(read_log(fp.root / plan["plan_id"] / "events.jsonl")))
        self.assertEqual(fp.verify_conclusion(plan["plan_id"]), [])

    def test_ledger_and_state_edited_together(self):
        """State + ledger forged consistently (records dropped, events_logged
        adjusted, cutoff moved to where the reduced ledger puts it)."""
        p, edge, fp, plan, rec = self.passed()
        pid = plan["plan_id"]
        ev = fp.root / pid / "events.jsonl"
        rows = read_log(ev)
        keep = [x for x in rows if x["net_bp_exec"] > 0]
        write_log(ev, keep)
        path = fp._state_path(pid)
        st = json.loads(path.read_text())
        st["events_logged"] = len(keep)
        st["evaluation_cutoff"]["ts"] = fp._rule_cutoff(plan, st, keep)
        path.write_text(json.dumps(st))
        why = fp.verify_conclusion(pid)
        self.assertTrue(any("events_sha256" in x for x in why), why)
        self.assertTrue(any("metrics recomputed" in x for x in why), why)

    def test_bar_log_rewritten(self):
        p, edge, fp, plan, rec = self.passed()
        from paper_forward import _chain
        log = fp.root / plan["plan_id"] / "bars_BTCUSDT.jsonl"
        rows = read_log(log)
        prev, out = "", []
        for r in rows:                                   # a consistent, re-chained forgery
            body = {k: v for k, v in r.items() if k not in ("hash", "prev")}
            body["c"] = body["c"] * 1.001
            h = _chain(prev, body)
            out.append({**body, "prev": prev, "hash": h})
            prev = h
        write_log(log, out)
        why = fp.verify_conclusion(plan["plan_id"])
        self.assertTrue(any("rewritten" in x for x in why), why)

    def test_record_id_covers_the_anchors(self):
        p, edge, fp, plan, rec = self.passed()
        from engine_lib import canonical_json  # noqa: F401  (record_hash uses it)
        from pipeline import record_hash
        body = {k: v for k, v in rec.items() if k not in ("record_id", "at")}
        self.assertEqual(record_hash(body), rec["record_id"])
        body["anchors"] = {**body["anchors"], "events_sha256": "0" * 64}
        self.assertNotEqual(record_hash(body), rec["record_id"])

    def test_plan_without_record(self):
        p, edge, feed, fp, plan = self.plan()
        self.assertEqual(fp.verify_conclusion(plan["plan_id"]), ["no final evaluation record for this plan"])
        self.assertEqual([x for x in iter_jsonl(fp.root / "plans.jsonl")][0]["plan_id"], plan["plan_id"])


if __name__ == "__main__":
    unittest.main()
