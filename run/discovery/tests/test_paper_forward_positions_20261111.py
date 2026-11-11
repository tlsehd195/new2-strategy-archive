"""ADR 0075: Paper Forward open positions and the integrity of the final verdict.

An open position (decided and entered, exit bar not yet observed) at the end
of the observation must never be concluded as if it did not exist; the
closed-trade ledger (``events.jsonl``) must agree with the evaluator, and the
last gate before a final verdict re-checks both from disk.

SYNTHETIC data and temp dirs only.  No market data, no network, no locked /
IW / sealed window.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine_lib import iter_jsonl  # noqa: E402
from test_paper_forward_funding_20261111 import DAY, HOUR, FundingCase  # noqa: E402

MIN15 = 900_000


def read_log(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def write_log(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


#: ADR 0076: the cutoff is the earliest bar at which the rule holds.  With these
#: criteria it is 00:30 of day 70, inside the day-70 holding period (00:15-02:15),
#: so the open-position scenarios below keep a position of the sample open.
OPEN_AT_RULE = {"min_days": 70 + 45 / 1440}


class PositionCase(FundingCase):
    """EDGE (hour 0-2 drift) enters at 00:15 and holds 8 bars (exit 02:15)."""

    CRIT: dict = {}

    def fp(self, p, feed):
        from paper_forward import DEFAULT_CRITERIA, ForwardPaper
        path = None
        if self.CRIT:
            c = json.loads(DEFAULT_CRITERIA.read_text())
            c.update(self.CRIT)
            path = self.td / "criteria.json"
            path.write_text(json.dumps(c, indent=1))
        return ForwardPaper(p, feed, criteria_path=path, clock=lambda: self.now[0])

    def plan(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        plan = fp.register_plan(edge)
        return p, edge, feed, fp, plan

    def at(self, plan, days, minutes):
        """Clock such that the last CLOSED bar opens at start + days + minutes - 15m."""
        self.now[0] = plan["start_ts"] + days * DAY + minutes * 60_000

    def open_at_end(self, *, crash=None):
        """Observe 70 days and stop 2 bars into the day-70 position (entry
        00:15, last observed bar 00:45).  ``crash``: multiply every price after
        the entry bar by this factor (the open position runs into a loss).
        ADR 0076: criteria ``OPEN_AT_RULE`` put the rule's cutoff at 00:30."""
        self.CRIT = OPEN_AT_RULE
        p, edge, feed, fp, plan = self.plan()
        entry = plan["start_ts"] + 70 * DAY + MIN15
        if crash is not None:
            feed.bars[:] = [dataclasses.replace(b, open=b.open * crash, high=b.high * crash,
                                                low=b.low * crash, close=b.close * crash)
                            if b.ts > entry else b for b in feed.bars]
        self.at(plan, 70, 60)
        r = fp.step(plan["plan_id"])
        return p, edge, feed, fp, plan, r, entry

    def evaluated(self, fp, plan):
        """step() up to the verdict, by hand (what step does before _verdict)."""
        from paper_forward import ConclusionRefused
        # collect bars + funding through a step that cannot conclude (the last gate is held)
        with mock.patch.object(type(fp), "_conclude", side_effect=ConclusionRefused("held by the test")):
            fp.step(plan["plan_id"])
        st = fp.state(plan["plan_id"])
        result = {"plan_id": plan["plan_id"], "status": st["status"]}
        result["funding"] = fp._funding_window_status(plan, st)
        result["funding_ok"] = all(v["status"] in ("VALID", "VALID_ZERO_RATE") for v in result["funding"].values())
        result.update(fp._evaluate(plan, st))
        return st, result


# ---------------------------------------------------------------------------
# reproductions (written first; they FAIL on main 80a687d)
# ---------------------------------------------------------------------------

class TestReproduction0075(PositionCase):
    def test_repro_open_position_at_end_is_concluded(self):
        """main: the day-70 position is open (``open_positions`` has it), the
        verdict ignores it and PAPER_PASSED is recorded."""
        p, edge, feed, fp, plan, r, entry = self.open_at_end()
        self.assertTrue(any(x["entry_ts"] == entry for x in r.get("open_positions") or []), r.get("open_positions"))
        self.assertNotEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assertNotEqual(p.statuses.status(edge, p.context_id()), "PAPER_PASSED")

    def test_repro_open_loss_beyond_drawdown_limit_is_concluded(self):
        """main: the open position is 30 % under water (> max_mdd 2500 bp) when
        the plan concludes PAPER_PASSED; closed, it fails the drawdown rule."""
        p, edge, feed, fp, plan, r, entry = self.open_at_end(crash=0.7)
        op = [x for x in r.get("open_positions") or [] if x["entry_ts"] == entry]
        self.assertTrue(op and op[0]["unrealized_gross_bp"] < -2500, op)
        self.assertNotEqual(r["verdict"], "PAPER_PASSED", r["reasons"])

    def test_repro_duplicate_trade_record_is_counted_twice(self):
        """main: a duplicated line in events.jsonl is counted twice by
        metrics() and the plan concludes on the inflated sample."""
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 20, 0)
        fp.step(pid)
        ev = fp.root / pid / "events.jsonl"
        rows = read_log(ev)
        write_log(ev, rows + rows)
        self.at(plan, 70, 0)
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])
        self.assertNotEqual(p.statuses.status(edge, p.context_id()), "PAPER_PASSED")

    def test_repro_conclude_trusts_the_earlier_verdict(self):
        """main: the ledger changes after _verdict (records removed); _conclude
        records PAPER_PASSED with the stale metrics."""
        from paper_forward import ConclusionRefused
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 70, 0)
        st, result = self.evaluated(fp, plan)
        v = fp._verdict(plan, st, result)
        self.assertEqual(v["verdict"], "PAPER_PASSED", v)
        ev = fp.root / pid / "events.jsonl"
        write_log(ev, read_log(ev)[:10])
        with self.assertRaises(ConclusionRefused):
            fp._conclude(plan, v, result, st)
        self.assertIsNone(p.evaluation("paper_forward", edge))




# ---------------------------------------------------------------------------
# the policy: wait for the sample's open positions, never mark or close by force
# ---------------------------------------------------------------------------

class TestOpenPositionPolicy(PositionCase):
    def test_1_open_position_blocks_pass_until_its_exit_is_observed(self):
        """Test 1 of the directive: an entry is not closed when the stopping rule
        holds -> no PAPER_PASSED; the plan waits, then concludes on the CLOSED
        trade (realised under cost / funding / execution), not on the mark."""
        p, edge, feed, fp, plan, r, entry = self.open_at_end()
        pid, eid = plan["plan_id"], f"BTCUSDT:{entry}"
        self.assertEqual(r["verdict"], "PAPER_OBSERVING", r["reasons"])
        self.assertIn("OPEN_POSITIONS_PENDING", r["reasons"][0])
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")
        self.assertIsNone(p.evaluation("paper_forward", edge))
        st = fp.state(pid)
        cut = st["evaluation_cutoff"]["ts"]
        self.assertEqual(cut, entry + MIN15)          # ADR 0076: the earliest bar the rule holds
        self.assertNotIn(eid, {x["event_id"] for x in iter_jsonl(fp.root / pid / "events.jsonl")})
        n_closed = r["metrics"]["n_trades"]
        # two more days: the pending trade closes, later entries are outside the sample
        self.at(plan, 72, 0)
        r2 = fp.step(pid)
        self.assertEqual(r2["verdict"], "PAPER_PASSED", r2["reasons"])
        self.assertEqual(r2["metrics"]["n_trades"], n_closed + 1)
        self.assertEqual(r2["metrics"]["sample_bound_ts"], cut)
        self.assertGreaterEqual(r2["metrics"]["trades_entered_after_bound"], 1)
        rec = p.evaluation("paper_forward", edge)
        self.assertEqual(rec["positions"]["evaluation_cutoff_ts"], cut)
        self.assertEqual(rec["positions"]["policy"], "paper-forward-positions-v1")
        led = [x for x in iter_jsonl(fp.root / pid / "events.jsonl") if x["event_id"] == eid]
        self.assertEqual(len(led), 1)
        self.assertEqual(fp.state(pid)["evaluation_cutoff"]["ts"], cut)       # never moved

    def test_1b_open_loss_is_realised_and_fails(self):
        p, edge, feed, fp, plan, r, entry = self.open_at_end(crash=0.7)
        self.assertEqual(r["verdict"], "PAPER_OBSERVING", r["reasons"])
        self.at(plan, 71, 0)
        r2 = fp.step(plan["plan_id"])
        self.assertEqual(r2["verdict"], "PAPER_FAILED", r2["reasons"])
        self.assertTrue(any("drawdown" in x for x in r2["reasons"]), r2["reasons"])
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_FAILED")

    def test_holding_position_stays_open_across_steps_and_is_logged_once(self):
        self.CRIT = OPEN_AT_RULE
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        entry = plan["start_ts"] + 70 * DAY + MIN15
        for minutes in (30, 60, 90, 120):           # bars 00:15 .. 01:45 observed: still held
            self.at(plan, 70, minutes)
            r = fp.step(pid)
            self.assertEqual(r["verdict"], "PAPER_OBSERVING", (minutes, r["reasons"]))
            self.assertEqual([x["entry_ts"] for x in r["open_positions"]], [entry])
            self.assertEqual(r["open_positions"][0]["bars_held"], minutes // 15 - 1)
        self.at(plan, 70, 300)                      # exit bar 02:15 and the 04:00 settlement observed
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assertEqual(r["open_positions"], [])
        ids = [x["event_id"] for x in iter_jsonl(fp.root / pid / "events.jsonl")]
        self.assertEqual(ids.count(f"BTCUSDT:{entry}"), 1)
        self.assertEqual(len(ids), len(set(ids)))

    def test_policy_applies_to_every_open_position(self):
        from paper_forward import ForwardPaper
        res = {"open_positions": [{"event_id": "A:1", "entry_ts": 100}, {"event_id": "B:1", "entry_ts": 200},
                                  {"event_id": "C:1", "entry_ts": 300}, {"event_id": "D:1"}]}
        self.assertEqual([x["event_id"] for x in ForwardPaper._pending(res, 200)], ["A:1", "B:1", "D:1"])

    def test_open_position_found_after_a_refused_signal(self):
        """The old scan stopped (``break``) at the first signal after the last
        exit; a refused signal there hid a later open position."""
        from data_quality import Bar
        from paper_forward import ForwardPaper
        bars = [Bar(ts=k * MIN15, open=100.0, high=101.0, low=99.0, close=100.0, volume=1.0, quote_volume=1e6)
                for k in range(20)]
        sig = lambda v: 1 if len(v) - 1 in (5, 15) else 0    # noqa: E731
        op = ForwardPaper._open_positions("X", bars, 2, sig, 4, [])
        self.assertEqual([(x["entry_index"], x["status"]) for x in op], [(16, "OPEN")])
        bars[-1] = dataclasses.replace(bars[-1], close=float("nan"))
        self.assertIsNone(ForwardPaper._open_positions("X", bars, 2, sig, 4, [])[0]["unrealized_gross_bp"])


# ---------------------------------------------------------------------------
# abnormal positions / ledger: never a normal verdict
# ---------------------------------------------------------------------------

class TestPositionIntegrity(PositionCase):
    def _after_20_days(self):
        p, edge, feed, fp, plan = self.plan()
        self.at(plan, 20, 0)
        fp.step(plan["plan_id"])
        return p, edge, feed, fp, plan, fp.root / plan["plan_id"] / "events.jsonl"

    def _expect_error(self, p, edge, fp, plan, kind=None):
        self.at(plan, 70, 0)
        r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")
        self.assertIsNone(p.evaluation("paper_forward", edge))
        st = fp.state(plan["plan_id"])
        self.assertEqual(st["status"], "ERROR")
        if kind:
            self.assertEqual(st["errors"][-1]["kind"], kind, st["errors"][-1])
        self.at(plan, 75, 0)                                   # sticky
        self.assertEqual(fp.step(plan["plan_id"])["verdict"], "PAPER_ERROR")
        return st

    def _edit(self, ev, i, **kw):
        rows = read_log(ev)
        rows[i].update(kw)
        write_log(ev, rows)

    def test_duplicate_close_record(self):
        p, edge, feed, fp, plan, ev = self._after_20_days()
        rows = read_log(ev)
        write_log(ev, rows + rows[3:4])
        st = self._expect_error(p, edge, fp, plan, "POSITION_INTEGRITY")
        self.assertIn("DUPLICATE_CLOSE", st["errors"][-1]["detail"])

    def test_zero_quantity_filled(self):
        p, edge, feed, fp, plan, ev = self._after_20_days()
        self._edit(ev, 2, fill_fraction=0.0)
        self._expect_error(p, edge, fp, plan, "POSITION_INTEGRITY")

    def test_negative_quantity(self):
        p, edge, feed, fp, plan, ev = self._after_20_days()
        self._edit(ev, 2, fill_fraction=-1.0)
        self._expect_error(p, edge, fp, plan, "POSITION_INTEGRITY")

    def test_non_finite_result(self):
        p, edge, feed, fp, plan, ev = self._after_20_days()
        rows = read_log(ev)
        rows[1]["cost_bp"] = "nan"
        write_log(ev, rows)
        self._expect_error(p, edge, fp, plan, "POSITION_INTEGRITY")

    def test_ledger_flag_differs_from_evaluator(self):
        """late / exec_status are not compared by _reconcile_trades; the ledger
        check catches them (a late trade is not counted on a realtime feed)."""
        p, edge, feed, fp, plan, ev = self._after_20_days()
        self._edit(ev, 4, late=not read_log(ev)[4]["late"])
        st = self._expect_error(p, edge, fp, plan, "POSITION_INTEGRITY")
        self.assertIn("differs from the evaluator", st["errors"][-1]["detail"])

    def test_record_deleted(self):
        p, edge, feed, fp, plan, ev = self._after_20_days()
        rows = read_log(ev)
        write_log(ev, rows[:5] + rows[6:])
        self._expect_error(p, edge, fp, plan, "TRADE_LOG_TRUNCATED")

    def test_record_without_trade(self):
        p, edge, feed, fp, plan, ev = self._after_20_days()
        rows = read_log(ev)
        fake = dict(rows[-1], entry_ts=rows[-1]["entry_ts"] + 6 * HOUR, exit_ts=rows[-1]["exit_ts"] + 6 * HOUR)
        fake["event_id"] = f"BTCUSDT:{fake['entry_ts']}"
        write_log(ev, rows + [fake])
        self._expect_error(p, edge, fp, plan)              # CAUSALITY_VIOLATION (reconcile)

    def test_closed_record_after_last_observed_bar(self):
        """A ledger record that says closed although its exit bar was never
        observed (the position is still open)."""
        p, edge, feed, fp, plan = self.plan()
        self.at(plan, 70, 0)
        st, result = self.evaluated(fp, plan)
        ev = fp.root / plan["plan_id"] / "events.jsonl"
        rows = read_log(ev)
        rows[-1]["exit_ts"] = st["last_ts"]["BTCUSDT"] + MIN15
        write_log(ev, rows)
        why = fp._position_problems(plan, st, result)
        self.assertTrue(any("position still open" in x for x in why), why)
        self.assertEqual(fp._verdict(plan, st, result)["verdict"], "PAPER_ERROR")

    def test_mark_missing_blocks(self):
        p, edge, feed, fp, plan = self.plan()
        self.at(plan, 70, 60)
        st, result = self.evaluated(fp, plan)
        self.assertEqual(len(result["open_positions"]), 1)
        for change in ({"unrealized_gross_bp": None}, {"unrealized_gross_bp": float("nan")},
                       {"marked_at": None}, {"marked_at": st["last_ts"]["BTCUSDT"] - MIN15}):
            res = dict(result, open_positions=[dict(result["open_positions"][0], **change)])
            v = fp._verdict(plan, st, res)
            self.assertEqual(v["verdict"], "PAPER_ERROR", (change, v))
            self.assertIn("MARK_MISSING", v["reasons"][0])

    def test_duplicate_open_position(self):
        p, edge, feed, fp, plan = self.plan()
        self.at(plan, 70, 60)
        st, result = self.evaluated(fp, plan)
        res = dict(result, open_positions=result["open_positions"] * 2)
        v = fp._verdict(plan, st, res)
        self.assertEqual(v["verdict"], "PAPER_ERROR")
        self.assertIn("DUPLICATE_POSITION", v["reasons"][0])

    def test_open_position_also_recorded_closed(self):
        p, edge, feed, fp, plan = self.plan()
        self.at(plan, 70, 60)
        st, result = self.evaluated(fp, plan)
        ev = fp.root / plan["plan_id"] / "events.jsonl"
        op = result["open_positions"][0]
        rows = read_log(ev)
        write_log(ev, rows + [dict(rows[-1], event_id=op["event_id"], entry_ts=op["entry_ts"])])
        v = fp._verdict(plan, st, result)
        self.assertEqual(v["verdict"], "PAPER_ERROR", v)

    def test_positions_not_evaluated(self):
        """A step whose evaluator did not complete (no trades / open positions in
        the result) never reaches a verdict that could conclude."""
        p, edge, feed, fp, plan = self.plan()
        self.at(plan, 70, 0)
        st, result = self.evaluated(fp, plan)
        res = {k: v for k, v in result.items() if k != "open_positions"}
        v = fp._verdict(plan, st, res)
        self.assertEqual(v["verdict"], "PAPER_ERROR")
        self.assertIn("POSITIONS_NOT_EVALUATED", v["reasons"][0])
        self.assertTrue(fp._conclusion_problems(plan, res, st, {"verdict": "PAPER_PASSED"}))

    def test_cutoff_edited_to_where_the_rule_does_not_hold(self):
        p, edge, feed, fp, plan, r, entry = self.open_at_end()
        sp = fp._state_path(plan["plan_id"])
        st = json.loads(sp.read_text())
        st["evaluation_cutoff"]["ts"] = plan["start_ts"] + 10 * DAY
        sp.write_text(json.dumps(st))
        self.at(plan, 72, 0)
        r2 = fp.step(plan["plan_id"])
        self.assertEqual(r2["verdict"], "PAPER_ERROR", r2["reasons"])
        self.assertIn("EVALUATION_CUTOFF_INVALID", r2["reasons"][0])

    def test_cutoff_after_the_observation_is_a_corrupt_state(self):
        p, edge, feed, fp, plan, r, entry = self.open_at_end()
        sp = fp._state_path(plan["plan_id"])
        st = json.loads(sp.read_text())
        st["evaluation_cutoff"]["ts"] = st["last_ts"]["BTCUSDT"] + 10 * DAY
        sp.write_text(json.dumps(st))
        r2 = fp.step(plan["plan_id"])
        self.assertEqual(r2["verdict"], "PAPER_ERROR")
        self.assertIn("STATE_CORRUPT", r2["reasons"][0])


# ---------------------------------------------------------------------------
# Test 2 of the directive: the last gate re-checks the latest state
# ---------------------------------------------------------------------------

class TestConclusionRecheck(PositionCase):
    def _passed(self):
        p, edge, feed, fp, plan = self.plan()
        self.at(plan, 70, 0)
        st, result = self.evaluated(fp, plan)
        v = fp._verdict(plan, st, result)
        self.assertEqual(v["verdict"], "PAPER_PASSED", v)
        st["evaluation_cutoff"] = {"ts": v["evaluation_cutoff"], "step": st["steps"],
                                   "policy": "paper-forward-positions-v1"}
        return p, edge, fp, plan, st, result, v, fp.root / plan["plan_id"] / "events.jsonl"

    def _refused(self, p, edge, fp, plan, v, result, st):
        from paper_forward import ConclusionRefused
        with self.assertRaises(ConclusionRefused):
            fp._conclude(plan, v, result, st)
        self.assertIsNone(p.evaluation("paper_forward", edge))
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")

    def test_unchanged_concludes(self):
        p, edge, fp, plan, st, result, v, ev = self._passed()
        fp._conclude(plan, v, result, st)
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_PASSED")

    def test_position_opened_after_the_verdict(self):
        p, edge, fp, plan, st, result, v, ev = self._passed()
        last, cut = st["last_ts"]["BTCUSDT"], v["evaluation_cutoff"]
        result["open_positions"].append({"event_id": f"BTCUSDT:{cut}", "symbol": "BTCUSDT", "side": 1,
                                         "entry_ts": cut, "status": "OPEN", "unrealized_gross_bp": 1.0,
                                         "marked_at": last})
        self._refused(p, edge, fp, plan, v, result, st)

    def test_duplicate_record_after_the_verdict(self):
        p, edge, fp, plan, st, result, v, ev = self._passed()
        rows = read_log(ev)
        write_log(ev, rows + rows[-1:])
        self._refused(p, edge, fp, plan, v, result, st)

    def test_records_removed_after_the_verdict(self):
        p, edge, fp, plan, st, result, v, ev = self._passed()
        write_log(ev, read_log(ev)[:-3])
        self._refused(p, edge, fp, plan, v, result, st)

    def test_flag_changed_after_the_verdict(self):
        p, edge, fp, plan, st, result, v, ev = self._passed()
        rows = read_log(ev)
        rows[0]["late"] = not rows[0]["late"]
        write_log(ev, rows)
        self._refused(p, edge, fp, plan, v, result, st)

    def test_stale_metrics(self):
        p, edge, fp, plan, st, result, v, ev = self._passed()
        result["metrics"] = dict(result["metrics"], mean_net_bp=999.0)
        self._refused(p, edge, fp, plan, v, result, st)

    def test_verdict_not_what_the_ledger_gives(self):
        p, edge, fp, plan, st, result, v, ev = self._passed()
        self._refused(p, edge, fp, plan, dict(v, verdict="PAPER_FAILED"), result, st)

    def test_no_cutoff(self):
        p, edge, fp, plan, st, result, v, ev = self._passed()
        st.pop("evaluation_cutoff")
        self._refused(p, edge, fp, plan, v, result, st)

    def test_step_maps_a_refusal_to_blocked(self):
        """Through step(): a ledger change between _verdict and _conclude (here:
        injected by wrapping _verdict) ends non-final, nothing recorded."""
        from paper_forward import ForwardPaper
        p, edge, feed, fp, plan = self.plan()
        ev = fp.root / plan["plan_id"] / "events.jsonl"
        orig = ForwardPaper._verdict

        def verdict_then_tamper(self_, plan_, st_, result_):
            v = orig(self_, plan_, st_, result_)
            if v["final"]:
                rows = read_log(ev)
                write_log(ev, rows + rows[-1:])
            return v
        self.at(plan, 70, 0)
        with mock.patch.object(ForwardPaper, "_verdict", verdict_then_tamper):
            r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA", r["reasons"])
        self.assertIn("CONCLUSION_REFUSED", r["reasons"][0])
        self.assertIsNone(p.evaluation("paper_forward", edge))
        self.assertFalse(fp.state(plan["plan_id"])["concluded"])


# ---------------------------------------------------------------------------
# exceptions, normal data, ADR 0073 / 0074 interplay
# ---------------------------------------------------------------------------

class TestExceptionsAndCompatibility(PositionCase):
    def test_exception_during_evaluation_never_passes(self):
        import paper_forward
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        before = fp.state(pid)
        calls = {"n": 0}
        real = paper_forward.apply_execution

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 30:
                raise RuntimeError("synthetic failure inside the evaluation")
            return real(*a, **k)
        self.at(plan, 70, 0)
        with mock.patch.object(paper_forward, "apply_execution", flaky):
            with self.assertRaises(RuntimeError):
                fp.step(pid)
        self.assertEqual(fp.state(pid), before)                  # nothing saved
        self.assertIsNone(p.evaluation("paper_forward", edge))
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")
        self.assertFalse((fp.root / pid / "events.jsonl").exists())
        r = fp.step(pid)                                          # resumes normally
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])

    def test_3_normal_closed_positions_conclude_as_before(self):
        """Test 3 of the directive: nothing open at the cutoff -> concluded at once.
        ADR 0076: the sample is every trade entered by the earliest bar at which
        the rule holds (start + 60 days - 15 m), not every trade observed."""
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 70, 0)
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assertEqual(r["open_positions"], [])
        st = fp.state(pid)
        cut = plan["start_ts"] + 60 * DAY - MIN15
        self.assertEqual(st["evaluation_cutoff"]["ts"], cut)
        led = list(iter_jsonl(fp.root / pid / "events.jsonl"))
        self.assertEqual(r["metrics"]["trades_entered_after_bound"], sum(1 for t in led if t["entry_ts"] > cut))
        self.assertEqual(r["metrics"]["n_trades"],
                         sum(1 for t in led if t["exec_status"] in ("FILLED", "PARTIAL", "LIQUIDATED")
                             and t["entry_ts"] <= cut))
        self.assertEqual(st["events_logged"], len(led))
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_PASSED")

    def test_funding_gap_with_open_position_stays_blocked(self):
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = self.fp(p, feed)
        plan = fp.register_plan(edge)
        start = plan["start_ts"]
        feed.rows = [x for x in feed.rows if x[0] != start + 30 * DAY + 4 * HOUR]
        self.at(plan, 70, 60)
        r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA", r["reasons"])
        self.assertIsNone(fp.state(plan["plan_id"]).get("evaluation_cutoff"))

    def test_evidence_tamper_with_open_position_is_error(self):
        p, edge, feed, fp, plan, r, entry = self.open_at_end()
        q = fp.root / plan["plan_id"] / "funding_quality_BTCUSDT.jsonl"
        q.write_text(q.read_text().replace('"VALID"', '"VALID_ZERO_RATE"', 1))
        self.at(plan, 72, 0)
        r2 = fp.step(plan["plan_id"])
        self.assertEqual(r2["verdict"], "PAPER_ERROR", r2["reasons"])
        self.assertIsNone(p.evaluation("paper_forward", edge))

    def test_old_state_without_new_keys_still_steps(self):
        """A state saved before ADR 0075 (no events_logged / evaluation_cutoff)
        is valid and gets both keys on its next step."""
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 20, 0)
        fp.step(pid)
        sp = fp._state_path(pid)
        st = json.loads(sp.read_text())
        st.pop("events_logged")
        sp.write_text(json.dumps(st))
        self.at(plan, 70, 0)
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assertIn("events_logged", fp.state(pid))


if __name__ == "__main__":
    unittest.main()
