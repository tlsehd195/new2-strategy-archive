"""ADR 0077: completeness of the open positions and the state the final verdict is built on.

A. The final verdict must not depend on the evaluator reporting every open
   position: ``open_positions=[]`` (or a list missing / misnaming one) while a
   position entered by the evaluation cutoff is still open must never conclude.
   The positions are re-derived independently (registered spec + warm-up file +
   bar log + funding log + ledger, never the evaluator's output) and compared.
B. The last gate must check the state the verdict is recorded with against the
   state on disk (a change of state.json during the step refuses the conclusion),
   and a crash at any point must neither record a wrong verdict nor a second one.

SYNTHETIC data and temp dirs only.  No market data, no network, no exchange
account, no locked / IW / sealed window.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine_lib import iter_jsonl  # noqa: E402
from test_paper_forward_funding_20261111 import DAY, DeclaredFeed  # noqa: E402
from test_paper_forward_positions_20261111 import MIN15, OPEN_AT_RULE, PositionCase, read_log, write_log  # noqa: E402


def no_open_positions(*a, **k):
    """Evaluator defect injected by the tests: every open position is lost."""
    return []


class CompletenessCase(PositionCase):
    def evaluations(self, p):
        return [r for r in iter_jsonl(p.dir / "evaluations.jsonl") if r.get("stage") == "paper_forward"]

    def assert_nothing_recorded(self, p, edge):
        self.assertIsNone(p.evaluation("paper_forward", edge))
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")

    def open_case(self):
        """Day-70 position (entry 00:15) open at the last observed bar 00:45;
        evaluation cutoff 00:30 (OPEN_AT_RULE): the position is in the sample."""
        self.CRIT = OPEN_AT_RULE
        p, edge, feed, fp, plan = self.plan()
        self.at(plan, 70, 60)
        st, result = self.evaluated(fp, plan)
        self.assertEqual(len(result["open_positions"]), 1, result["open_positions"])
        return p, edge, feed, fp, plan, st, result

    def passed_case(self):
        """Nothing open: _verdict gives PAPER_PASSED (the cutoff is recorded in st)."""
        p, edge, feed, fp, plan = self.plan()
        self.at(plan, 70, 0)
        st, result = self.evaluated(fp, plan)
        v = fp._verdict(plan, st, result)
        self.assertEqual(v["verdict"], "PAPER_PASSED", v)
        self.assertEqual(fp.state(plan["plan_id"])["evaluation_cutoff"]["ts"], v["evaluation_cutoff"])
        return p, edge, feed, fp, plan, st, result, v


# ---------------------------------------------------------------------------
# reproductions (written first; they FAIL on main 34f0e9f)
# ---------------------------------------------------------------------------

class TestReproduction0077(CompletenessCase):
    def test_repro_A_empty_open_positions_concludes_passed(self):
        """main: the evaluator loses the open day-70 position (open_positions=[]):
        _position_problems sees a valid (empty) list, _pending is empty, the
        verdict is PAPER_PASSED and _conclude records it."""
        from paper_forward import ForwardPaper
        self.CRIT = OPEN_AT_RULE
        p, edge, feed, fp, plan = self.plan()
        self.at(plan, 70, 60)
        with mock.patch.object(ForwardPaper, "_open_positions", staticmethod(no_open_positions)):
            r = fp.step(plan["plan_id"])
        self.assertEqual(r["open_positions"], [])
        self.assertNotEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assert_nothing_recorded(p, edge)

    def test_repro_A_direct_verdict_and_conclude(self):
        from paper_forward import ConclusionRefused
        p, edge, feed, fp, plan, st, result = self.open_case()
        res = dict(result, open_positions=[])
        v = fp._verdict(plan, st, res)
        self.assertNotEqual(v["verdict"], "PAPER_PASSED", v)
        self.assertFalse(v["final"])
        st["evaluation_cutoff"] = fp.state(plan["plan_id"])["evaluation_cutoff"]
        res["metrics"] = fp.metrics(plan["plan_id"], st)
        with self.assertRaises(ConclusionRefused):
            fp._conclude(plan, {"verdict": "PAPER_PASSED", "final": True, "reasons": []}, res, st)
        self.assert_nothing_recorded(p, edge)

    def test_repro_A_entry_moved_past_the_cutoff(self):
        """main: the open position is reported with an entry after the cutoff
        (event id renamed to match): it is no longer 'pending' and PASS follows."""
        p, edge, feed, fp, plan, st, result = self.open_case()
        x = dict(result["open_positions"][0])
        x["entry_ts"] += 2 * MIN15
        x["event_id"] = f"BTCUSDT:{x['entry_ts']}"
        v = fp._verdict(plan, st, dict(result, open_positions=[x]))
        self.assertNotEqual(v["verdict"], "PAPER_PASSED", v)

    def test_repro_B_state_changed_before_conclude_is_overwritten(self):
        """main: state.json is changed on disk between _verdict and _conclude;
        _conclude trusts the in-memory state, records PAPER_PASSED and step()
        then overwrites the changed file (the change is never seen)."""
        from paper_forward import ForwardPaper
        p, edge, feed, fp, plan = self.plan()
        sp = fp._state_path(plan["plan_id"])
        orig = ForwardPaper._verdict

        def verdict_then_tamper(self_, plan_, st_, result_):
            v = orig(self_, plan_, st_, result_)
            if v["final"]:
                disk = json.loads(sp.read_text())
                disk["funding_head"]["BTCUSDT"]["seq"] += 1
                sp.write_text(json.dumps(disk))
            return v
        self.at(plan, 70, 0)
        with mock.patch.object(ForwardPaper, "_verdict", verdict_then_tamper):
            r = fp.step(plan["plan_id"])
        self.assertNotEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assert_nothing_recorded(p, edge)



# ---------------------------------------------------------------------------
# A. completeness of the open positions
# ---------------------------------------------------------------------------

class TwoSymbolFeed(DeclaredFeed):
    """Same synthetic bars for every symbol; ``limits[sym]`` = last bar the feed
    has delivered for it (a lagging symbol)."""

    def __init__(self, bars, clock, rows, limits):
        super().__init__(bars, clock, rows)
        self.limits = dict(limits)

    def closed_bars(self, sym, after_ts):
        out, info = super().closed_bars(sym, after_ts)
        lim = self.limits.get(sym)
        return [b for b in out if lim is None or b.ts <= lim], info


class TestOpenPositionCompleteness(CompletenessCase):
    def test_1_empty_list_is_error_through_step(self):
        """Reproduction A through step(): the replay finds the open position the
        evaluator lost -> PAPER_ERROR (POSITION_INTEGRITY, sticky), nothing recorded."""
        from paper_forward import ForwardPaper
        self.CRIT = OPEN_AT_RULE
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        entry = plan["start_ts"] + 70 * DAY + MIN15
        self.at(plan, 70, 60)
        with mock.patch.object(ForwardPaper, "_open_positions", staticmethod(no_open_positions)):
            r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])
        st = fp.state(pid)
        self.assertEqual(st["errors"][-1]["kind"], "POSITION_INTEGRITY")
        self.assertIn(f"OPEN_POSITION_MISSING: BTCUSDT:{entry}", st["errors"][-1]["detail"])
        self.assert_nothing_recorded(p, edge)
        self.at(plan, 72, 0)                                   # sticky: later steps never conclude
        self.assertEqual(fp.step(pid)["verdict"], "PAPER_ERROR")
        self.assert_nothing_recorded(p, edge)

    def test_1b_empty_list_verdict_and_last_gate_name_the_position(self):
        from paper_forward import ConclusionRefused
        p, edge, feed, fp, plan, st, result = self.open_case()
        eid = result["open_positions"][0]["event_id"]
        res = dict(result, open_positions=[])
        v = fp._verdict(plan, st, res)
        self.assertEqual(v["verdict"], "PAPER_ERROR", v)
        self.assertIn(f"OPEN_POSITION_MISSING: {eid}", v["reasons"][0])
        st["evaluation_cutoff"] = fp.state(plan["plan_id"])["evaluation_cutoff"]
        res["metrics"] = fp.metrics(plan["plan_id"], st)
        why = fp._conclusion_problems(plan, res, st, {"verdict": "PAPER_PASSED"})
        self.assertTrue(any(f"OPEN_POSITION_MISSING: {eid}" in x for x in why), why)
        self.assertTrue(any("still open" in x and eid in x for x in why), why)    # the replay's pending
        with self.assertRaises(ConclusionRefused):
            fp._conclude(plan, {"verdict": "PAPER_PASSED", "final": True, "reasons": []}, res, st)
        self.assert_nothing_recorded(p, edge)

    def test_3_event_id_changed(self):
        p, edge, feed, fp, plan, st, result = self.open_case()
        x = dict(result["open_positions"][0], event_id="BTCUSDT:1")
        v = fp._verdict(plan, st, dict(result, open_positions=[x]))
        self.assertEqual(v["verdict"], "PAPER_ERROR", v)
        self.assertIn("OPEN_POSITION_MISSING", v["reasons"][0])
        self.assertIn("OPEN_POSITION_UNEXPECTED", v["reasons"][0])

    def test_4_entry_time_changed(self):
        p, edge, feed, fp, plan, st, result = self.open_case()
        for shift in (-MIN15, MIN15, 2 * MIN15):
            x = dict(result["open_positions"][0])
            x["entry_ts"] += shift
            x["event_id"] = f"BTCUSDT:{x['entry_ts']}"
            v = fp._verdict(plan, st, dict(result, open_positions=[x]))
            self.assertEqual(v["verdict"], "PAPER_ERROR", (shift, v))
            self.assertIn("OPEN_POSITION_MISSING", v["reasons"][0])

    def test_4b_decision_exit_side_status_changed(self):
        p, edge, feed, fp, plan, st, result = self.open_case()
        for change in ({"decision_ts": result["open_positions"][0]["decision_ts"] - MIN15},
                       {"planned_exit_ts": result["open_positions"][0]["planned_exit_ts"] + MIN15},
                       {"side": -1}, {"status": "CLOSED"}):
            x = dict(result["open_positions"][0], **change)
            v = fp._verdict(plan, st, dict(result, open_positions=[x]))
            self.assertEqual(v["verdict"], "PAPER_ERROR", (change, v))
            self.assertIn("OPEN_POSITION_MISMATCH", v["reasons"][0], change)

    def test_5_open_position_written_to_the_ledger_as_closed(self):
        """The ledger gets a closed record for the open position (and the
        evaluator list is emptied to match): the replay says it is open."""
        p, edge, feed, fp, plan, st, result = self.open_case()
        ev = fp.root / plan["plan_id"] / "events.jsonl"
        op = result["open_positions"][0]
        rows = read_log(ev)
        fake = dict(rows[-1], event_id=op["event_id"], entry_ts=op["entry_ts"], exit_ts=st["last_ts"]["BTCUSDT"],
                    entry_index=op["entry_index"])
        write_log(ev, rows + [fake])
        st["events_logged"] = len(rows) + 1
        rec = fp._reconstruct_positions(plan, st)
        self.assertTrue(any("OPEN_POSITION_RECORDED_CLOSED" in x for x in rec["problems"]), rec["problems"])
        v = fp._verdict(plan, st, dict(result, open_positions=[], trades=result["trades"] + [fake]))
        self.assertEqual(v["verdict"], "PAPER_ERROR", v)
        self.assertIn("OPEN_POSITION_RECORDED_CLOSED", v["reasons"][0])

    def test_7a_unexpected_extra_position(self):
        p, edge, feed, fp, plan, st, result = self.open_case()
        x = dict(result["open_positions"][0])
        y = dict(x, entry_ts=x["entry_ts"] - 2 * DAY, event_id=f"BTCUSDT:{x['entry_ts'] - 2 * DAY}")
        v = fp._verdict(plan, st, dict(result, open_positions=[x, y]))
        self.assertEqual(v["verdict"], "PAPER_ERROR", v)
        self.assertIn("OPEN_POSITION_UNEXPECTED", v["reasons"][0])

    def test_7b_reported_open_while_nothing_is_open(self):
        p, edge, feed, fp, plan, st, result, v = self.passed_case()
        last = st["last_ts"]["BTCUSDT"]
        x = {"event_id": f"BTCUSDT:{last}", "symbol": "BTCUSDT", "side": 1, "entry_ts": last, "status": "OPEN",
             "decision_ts": last - MIN15, "planned_exit_ts": last + 8 * MIN15, "unrealized_gross_bp": 0.0,
             "marked_at": last}
        v2 = fp._verdict(plan, st, dict(result, open_positions=[x]))
        self.assertEqual(v2["verdict"], "PAPER_ERROR", v2)
        self.assertIn("OPEN_POSITION_UNEXPECTED", v2["reasons"][0])

    def test_7c_closed_trade_missing_from_ledger_and_evaluator(self):
        """A closed trade dropped from BOTH the evaluator's list and the ledger
        (with the state count adjusted, so ADR 0075's checks agree): the replay
        still finds it."""
        p, edge, feed, fp, plan, st, result, v = self.passed_case()
        ev = fp.root / plan["plan_id"] / "events.jsonl"
        rows = read_log(ev)
        gone = rows[5]
        write_log(ev, rows[:5] + rows[6:])
        st["events_logged"] = len(rows) - 1
        res = dict(result, trades=[t for t in result["trades"] if t["event_id"] != gone["event_id"]])
        v2 = fp._verdict(plan, st, res)
        self.assertEqual(v2["verdict"], "PAPER_ERROR", v2)
        self.assertIn(f"CLOSED_TRADE_MISSING: {gone['event_id']}", v2["reasons"][0])

    def test_8_all_closed_concludes_as_before_with_replay_record(self):
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 70, 0)
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        rec = p.evaluation("paper_forward", edge)
        self.assertEqual(rec["positions"]["completeness"], "paper-forward-completeness-v1")
        self.assertEqual(rec["positions"]["open_replayed"], [])
        self.assertIn("state_fields_sha256", rec["anchors"])
        self.assertEqual(fp.verify_conclusion(pid), [])
        self.assertEqual(len(self.evaluations(p)), 1)

    def test_8b_waits_then_concludes_when_the_replayed_position_closes(self):
        """Normal path with a pending position: the replay agrees with the
        evaluator, the plan waits, then concludes once the exit is observed."""
        self.CRIT = OPEN_AT_RULE
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 70, 60)
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_OBSERVING", r["reasons"])
        self.assertIn("OPEN_POSITIONS_PENDING", r["reasons"][0])
        self.at(plan, 72, 0)
        r2 = fp.step(pid)
        self.assertEqual(r2["verdict"], "PAPER_PASSED", r2["reasons"])
        self.assertEqual(fp.verify_conclusion(pid), [])

    def test_replay_does_not_use_the_evaluator(self):
        """The replay is a separate path: with the evaluator's event study and
        open-position scan disabled it still finds the open position."""
        import paper_forward
        from paper_forward import ForwardPaper
        p, edge, feed, fp, plan, st, result = self.open_case()
        boom = mock.Mock(side_effect=AssertionError("evaluator path used by the replay"))
        fp.__dict__.pop("_replay_cache", None)
        with mock.patch.object(paper_forward, "run_event_study", boom), \
                mock.patch.object(ForwardPaper, "_open_positions", boom), \
                mock.patch.object(ForwardPaper, "_evaluate", boom):
            rec = fp._reconstruct_positions(plan, st)
        self.assertEqual(rec["problems"], [])
        self.assertEqual([x["event_id"] for x in rec["open"]], [result["open_positions"][0]["event_id"]])
        x, y = rec["open"][0], result["open_positions"][0]
        for k in ("symbol", "side", "entry_ts", "decision_ts", "planned_exit_ts", "status"):
            self.assertEqual(x[k], y[k], k)

    def test_replay_input_change_is_a_new_replay(self):
        """The memo is keyed on the sha256 of every input file: a changed bar log
        (here: one bar appended by hand, chain intact) is replayed again."""
        p, edge, feed, fp, plan, st, result = self.open_case()
        first = fp._reconstruct_positions(plan, st)
        self.assertEqual(first["problems"], [])
        log = fp.root / plan["plan_id"] / "bars_BTCUSDT.jsonl"
        log.write_text(log.read_text() + log.read_text().splitlines()[-1] + "\n")
        again = fp._reconstruct_positions(plan, st)
        self.assertTrue(again["problems"], again)

    def test_replay_finds_open_position_after_a_refused_signal(self):
        """Pure replay: a signal refused by the rules (cost) does not hold the
        position; the later one that runs past the bars is the open position."""
        from data_quality import Bar
        from paper_forward import ForwardPaper
        bars = [Bar(ts=k * MIN15, open=100.0, high=101.0, low=99.0, close=100.0, volume=1.0, quote_volume=1e6)
                for k in range(20)]
        sig = lambda v: 1 if len(v) - 1 in (5, 15) else 0    # noqa: E731
        refuse = lambda ent, x, side: "participation cap" if ent == 6 else None   # noqa: E731
        rep = ForwardPaper._replay_positions("X", bars, 2, sig, 4, None, {}, refuse, [], 0)
        self.assertEqual([(x["entry_ts"], x["decision_ts"]) for x in rep["open"]], [(16 * MIN15, 15 * MIN15)])
        self.assertEqual(rep["counts"]["refused"], 1)
        # not refused: a closed trade the ledger must hold
        rep2 = ForwardPaper._replay_positions("X", bars, 2, sig, 4, None, {}, lambda *a: None, [], 0)
        self.assertTrue(any("CLOSED_TRADE_MISSING: X:5400000" in x for x in rep2["problems"]), rep2["problems"])
        # a ledger trade the rules do not give
        rep3 = ForwardPaper._replay_positions("X", bars, 2, sig, 4, None,
                                              {6 * MIN15: {"side": 1, "exit_ts": 10 * MIN15, "entry_index": 6,
                                                           "exit_index": 10},
                                               9 * MIN15: {"side": 1}}, lambda *a: None, [], 0)
        self.assertEqual(rep3["closed"], ["X:5400000"])
        self.assertTrue(any("LEDGER_TRADE_NOT_REPLAYED: X:8100000" in x for x in rep3["problems"]), rep3)

    def test_compare_open_positions_kinds(self):
        from paper_forward import ForwardPaper
        a = {"event_id": "A:1", "symbol": "A", "side": 1, "entry_ts": 1, "decision_ts": 0, "planned_exit_ts": 9,
             "status": "OPEN"}
        b = dict(a, event_id="B:2", symbol="B", entry_ts=2)
        cmp = ForwardPaper._compare_open_positions
        self.assertEqual(cmp([a, b], [a, b]), [])
        self.assertTrue(cmp([a, b], [a])[0].startswith("OPEN_POSITION_MISSING: B:2"))
        self.assertTrue(cmp([a], [a, b])[0].startswith("OPEN_POSITION_UNEXPECTED"))
        self.assertTrue(cmp([a], [a, a])[0].startswith("OPEN_POSITION_DUPLICATE"))
        self.assertTrue(cmp([a], [dict(a, side=-1)])[0].startswith("OPEN_POSITION_MISMATCH"))
        self.assertTrue(cmp([a], []))


class TestTwoSymbols(CompletenessCase):
    """Two symbols (same synthetic bars, ETH delivered only up to day 70 00:30):
    ETH holds the day-70 position (entry 00:15, entered by the cutoff 00:30),
    BTC the day-71 position (entry day 71 00:15, after the cutoff)."""

    def two(self):
        from paper_forward import ForwardPaper
        self.CRIT = OPEN_AT_RULE
        p, edge, feed0 = self.setup_feed(iv_h=4)
        spec = p.specs.get(edge)
        two = dict(spec, symbols=["BTCUSDT", "ETHUSDT"])
        orig_get, orig_paper = p.specs.get, p.provider.paper
        self.enterContext(mock.patch.object(p.specs, "get", lambda sid: two if sid == edge else orig_get(sid)))
        self.enterContext(mock.patch.object(p.provider, "paper",
                                            lambda sym: orig_paper("BTCUSDT") if sym == "ETHUSDT" else orig_paper(sym)))
        feed = TwoSymbolFeed(feed0.bars, feed0.clock, feed0.rows, {})
        fp = ForwardPaper(p, feed, criteria_path=self._crit_path(), clock=lambda: self.now[0])
        plan = fp.register_plan(edge)
        self.assertEqual(plan["symbols"], ["BTCUSDT", "ETHUSDT"])
        feed.limits["ETHUSDT"] = plan["start_ts"] + 70 * DAY + 2 * MIN15
        self.at(plan, 71, 60)
        st, result = self.evaluated(fp, plan)
        ops = sorted(result["open_positions"], key=lambda x: x["entry_ts"])
        self.assertEqual([(x["symbol"], x["entry_ts"] - plan["start_ts"]) for x in ops],
                         [("ETHUSDT", 70 * DAY + MIN15), ("BTCUSDT", 71 * DAY + MIN15)])
        self.assertEqual(st["evaluation_cutoff"]["ts"], plan["start_ts"] + 70 * DAY + 2 * MIN15)
        return p, edge, feed, fp, plan, st, result, ops

    def _crit_path(self):
        from paper_forward import DEFAULT_CRITERIA
        c = json.loads(DEFAULT_CRITERIA.read_text())
        c.update(self.CRIT)
        path = self.td / "criteria.json"
        path.write_text(json.dumps(c, indent=1))
        return path

    def test_B_partial_omission_of_two_open_positions(self):
        """Reproduction test B: two open positions entered at different times; the
        evaluator keeps only one -> the other is named as missing (either way)."""
        p, edge, feed, fp, plan, st, result, ops = self.two()
        for keep, lost in ((ops[0], ops[1]), (ops[1], ops[0])):
            res = dict(result, open_positions=[keep])
            v = fp._verdict(plan, st, res)
            self.assertEqual(v["verdict"], "PAPER_ERROR", v)
            self.assertIn(f"OPEN_POSITION_MISSING: {lost['event_id']}", v["reasons"][0])
            why = fp._conclusion_problems(plan, dict(res, metrics=fp.metrics(plan["plan_id"], st)), st,
                                          {"verdict": "PAPER_PASSED"})
            self.assertTrue(any(f"OPEN_POSITION_MISSING: {lost['event_id']}" in x for x in why), why)
        self.assert_nothing_recorded(p, edge)

    def test_6_positions_before_and_after_the_cutoff(self):
        """ETH (entered by the cutoff) is pending; BTC (entered after) is outside
        the sample but still checked: the full list is required."""
        p, edge, feed, fp, plan, st, result, ops = self.two()
        v = fp._verdict(plan, st, dict(result))
        self.assertEqual(v["verdict"], "PAPER_OBSERVING", v)
        self.assertIn("OPEN_POSITIONS_PENDING: 1 position", v["reasons"][0])
        self.assertIn(ops[0]["event_id"], v["reasons"][0])
        self.assertNotIn(ops[1]["event_id"], v["reasons"][0])
        res = dict(result)
        fp._verdict(plan, st, res)
        self.assertEqual([x["event_id"] for x in res["positions"]["open_after_bound"]], [ops[1]["event_id"]])
        # the evaluator reports only the pending one: the after-cutoff position is missing -> error
        v2 = fp._verdict(plan, st, dict(result, open_positions=[ops[0]]))
        self.assertEqual(v2["verdict"], "PAPER_ERROR", v2)
        self.assertIn(f"OPEN_POSITION_MISSING: {ops[1]['event_id']}", v2["reasons"][0])
        # both lost through step(): error, never PASS
        from paper_forward import ForwardPaper
        with mock.patch.object(ForwardPaper, "_open_positions", staticmethod(no_open_positions)):
            r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])
        self.assert_nothing_recorded(p, edge)


# ---------------------------------------------------------------------------
# B. the state a conclusion is recorded with
# ---------------------------------------------------------------------------

class TestFinalStateConsistency(CompletenessCase):
    def tamper_after_verdict(self, change):
        """step() with ``change(state_dict)`` applied to state.json on disk right
        after _verdict returned a final verdict."""
        from paper_forward import ForwardPaper
        p, edge, feed, fp, plan = self.plan()
        sp = fp._state_path(plan["plan_id"])
        orig = ForwardPaper._verdict
        seen = {}

        def verdict_then_tamper(self_, plan_, st_, result_):
            v = orig(self_, plan_, st_, result_)
            if v["final"]:
                disk = json.loads(sp.read_text())
                change(disk, v)
                sp.write_text(json.dumps(disk))
                seen["disk"] = sp.read_bytes()
            return v
        self.at(plan, 70, 0)
        with mock.patch.object(ForwardPaper, "_verdict", verdict_then_tamper):
            r = fp.step(plan["plan_id"])
        self.assertIn("disk", seen)
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA", r["reasons"])
        self.assertIn("CONCLUSION_REFUSED: STATE_CHANGED_DURING_STEP", r["reasons"][0])
        self.assert_nothing_recorded(p, edge)
        self.assertEqual(sp.read_bytes(), seen["disk"])          # the changed file is kept as evidence
        return p, edge, fp, plan

    def test_1_any_change_after_the_verdict(self):
        self.tamper_after_verdict(lambda d, v: d.update(steps=d["steps"] + 7))

    def test_2_cutoff(self):
        def f(d, v):
            d["evaluation_cutoff"] = {"ts": v["evaluation_cutoff"] - 4 * MIN15, "step": 1,
                                      "policy": "paper-forward-positions-v1"}
        self.tamper_after_verdict(f)

    def test_3_last_ts(self):
        self.tamper_after_verdict(lambda d, v: d["last_ts"].update(BTCUSDT=d["last_ts"]["BTCUSDT"] + 8 * MIN15))

    def test_4_events_logged(self):
        self.tamper_after_verdict(lambda d, v: d.update(events_logged=(d.get("events_logged") or 0) + 1))

    def test_5_funding_head(self):
        self.tamper_after_verdict(lambda d, v: d["funding_head"]["BTCUSDT"].update(
            hash="0" * 64))

    def direct_conclude_with_disk_change(self, change):
        """Without step(): the in-memory state given to _conclude must equal the
        state on disk on every anchored field."""
        from paper_forward import ConclusionRefused
        p, edge, feed, fp, plan, st, result, v = self.passed_case()
        st["evaluation_cutoff"] = fp.state(plan["plan_id"])["evaluation_cutoff"]
        sp = fp._state_path(plan["plan_id"])
        disk = json.loads(sp.read_text())
        change(disk)
        sp.write_text(json.dumps(disk))
        with self.assertRaises(ConclusionRefused) as cm:
            fp._conclude(plan, v, result, st)
        self.assertIn("STATE", str(cm.exception))
        self.assert_nothing_recorded(p, edge)

    def test_direct_conclude_last_ts_on_disk_differs(self):
        self.direct_conclude_with_disk_change(lambda d: d["last_ts"].update(BTCUSDT=d["last_ts"]["BTCUSDT"] - MIN15))

    def test_direct_conclude_events_logged_on_disk_differs(self):
        self.direct_conclude_with_disk_change(lambda d: d.update(events_logged=d["events_logged"] - 1))

    def test_direct_conclude_funding_head_on_disk_differs(self):
        self.direct_conclude_with_disk_change(lambda d: d["funding_head"]["BTCUSDT"].update(seq=999))

    def test_direct_conclude_cutoff_on_disk_differs(self):
        self.direct_conclude_with_disk_change(lambda d: d["evaluation_cutoff"].update(
            ts=d["evaluation_cutoff"]["ts"] + MIN15))

    def test_snapshot_sha_must_be_on_disk(self):
        from paper_forward import ConclusionRefused
        p, edge, feed, fp, plan, st, result, v = self.passed_case()
        st = fp.state(plan["plan_id"])
        with self.assertRaises(ConclusionRefused) as cm:
            fp._conclude(plan, v, result, st, snapshot_sha="0" * 64)
        self.assertIn("STATE_SNAPSHOT_MISMATCH", str(cm.exception))
        self.assert_nothing_recorded(p, edge)

    def test_6_normal_save_then_conclusion(self):
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 70, 0)
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        st = fp.state(pid)
        self.assertTrue(st["concluded"])
        rec = p.evaluation("paper_forward", edge)
        self.assertEqual(rec["anchors"]["state_fields_sha256"], fp._anchors(plan, st)["state_fields_sha256"])
        self.assertEqual(fp.verify_conclusion(pid), [])
        self.assertEqual(fp.step(pid)["status"], "CONCLUDED")
        self.assertEqual(len(self.evaluations(p)), 1)
        # a later change of an anchored state field is found by verify_conclusion
        sp = fp._state_path(pid)
        d = json.loads(sp.read_text())
        d["gaps"]["BTCUSDT"].append([0, 0, 0])
        sp.write_text(json.dumps(d))
        self.assertTrue(any("state_fields_sha256" in x for x in fp.verify_conclusion(pid)))

    def test_non_final_step_never_overwrites_a_changed_state(self):
        from paper_forward import ForwardPaper
        p, edge, feed, fp, plan = self.plan()
        sp = fp._state_path(plan["plan_id"])
        orig = ForwardPaper._verdict

        def verdict_then_tamper(self_, plan_, st_, result_):
            v = orig(self_, plan_, st_, result_)
            d = json.loads(sp.read_text())
            d["steps"] += 100
            sp.write_text(json.dumps(d))
            return v
        self.at(plan, 20, 0)
        with mock.patch.object(ForwardPaper, "_verdict", verdict_then_tamper):
            r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_ERROR", r["reasons"])
        self.assertIn("STATE_CHANGED_DURING_STEP", r["reasons"][0])
        self.assertEqual(json.loads(sp.read_text())["steps"], 100)

    def test_7_crash_before_the_record(self):
        """Killed in _conclude before the evaluation record: nothing recorded; the
        state on disk is the step's snapshot (cutoff kept, not concluded); the next
        step concludes once."""
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 70, 0)
        with mock.patch.object(p, "_append_eval", side_effect=RuntimeError("killed")):
            with self.assertRaises(RuntimeError):
                fp.step(pid)
        self.assert_nothing_recorded(p, edge)
        st = fp.state(pid)
        self.assertFalse(st["concluded"])
        self.assertIsNotNone(st["evaluation_cutoff"])
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assertEqual(len(self.evaluations(p)), 1)
        self.assertEqual(fp.verify_conclusion(pid), [])

    def _crash_after_record(self, p, fp, pid):
        from paper_forward import ForwardPaper
        real = ForwardPaper._save_state

        def save(self_, plan_id, st):
            if st.get("concluded"):
                raise RuntimeError("killed before the state was saved")
            return real(self_, plan_id, st)
        with mock.patch.object(ForwardPaper, "_save_state", save):
            with self.assertRaises(RuntimeError):
                fp.step(pid)

    def test_8_9_crash_after_the_record_resumes_without_a_second_record(self):
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 70, 0)
        self._crash_after_record(p, fp, pid)
        self.assertEqual(len(self.evaluations(p)), 1)
        self.assertFalse(fp.state(pid)["concluded"])
        rid = p.evaluation("paper_forward", edge)["record_id"]
        self.at(plan, 75, 0)                  # resumed later: nothing new is observed
        r = fp.step(pid)
        self.assertEqual((r["status"], r["verdict"], r["resumed_conclusion"]), ("CONCLUDED", "PAPER_PASSED", rid))
        self.assertEqual(len(self.evaluations(p)), 1)
        self.assertTrue(fp.state(pid)["concluded"])
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_PASSED")
        fb = [x for x in iter_jsonl(p.dir / "research_feedback.jsonl") if x.get("plan_id") == pid]
        self.assertEqual(len(fb), 1)
        self.assertEqual(fp.verify_conclusion(pid), [])
        self.assertEqual(fp.step(pid)["status"], "CONCLUDED")
        self.assertEqual(len(self.evaluations(p)), 1)

    def test_8b_crash_between_record_and_status(self):
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 70, 0)
        with mock.patch.object(p.statuses, "set", side_effect=RuntimeError("killed")):
            with self.assertRaises(RuntimeError):
                fp.step(pid)
        self.assertEqual(len(self.evaluations(p)), 1)
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_TESTING")
        r = fp.step(pid)
        self.assertEqual(r["status"], "CONCLUDED", r)
        self.assertEqual(p.statuses.status(edge, p.context_id()), "PAPER_PASSED")
        self.assertEqual(len(self.evaluations(p)), 1)

    def test_9b_resume_refuses_a_record_the_evidence_does_not_give(self):
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 70, 0)
        self._crash_after_record(p, fp, pid)
        sp = fp._state_path(pid)
        before = sp.read_bytes()
        ev = fp.root / pid / "events.jsonl"
        rows = read_log(ev)
        rows[0]["net_bp_exec"] = rows[0]["net_bp_exec"] + 50.0
        write_log(ev, rows)
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR", r)
        self.assertIn("EVALUATION_RECORD_CONFLICT", r["reasons"][0])
        self.assertEqual(sp.read_bytes(), before)                       # nothing changed
        self.assertEqual(len(self.evaluations(p)), 1)
        self.assertFalse(fp.state(pid)["concluded"])
        self.assertEqual(fp.step(pid)["verdict"], "PAPER_ERROR")         # never completed
        self.assertEqual(len(self.evaluations(p)), 1)

    def test_10a_step_while_another_holds_the_lock(self):
        import fcntl
        import os
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        sp = fp._state_path(pid)
        before = sp.read_bytes()
        fd = os.open(str(fp.root / pid / "step.lock"), os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.at(plan, 70, 0)
            r = fp.step(pid)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual((r["status"], r["verdict"]), ("STEP_IN_PROGRESS", "PAPER_BLOCKED_DATA"))
        self.assertEqual(sp.read_bytes(), before)
        self.assertFalse((fp.root / pid / "bars_BTCUSDT.jsonl").exists())
        self.assert_nothing_recorded(p, edge)
        self.assertEqual(fp.step(pid)["verdict"], "PAPER_PASSED")        # lock released

    def test_10b_two_concurrent_steps_conclude_once(self):
        """Two engines on one plan: the second step while the first is inside its
        verdict does nothing; the first concludes; one record."""
        from paper_forward import ForwardPaper
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        fp2 = self.fp(p, feed)
        inside, go = threading.Event(), threading.Event()
        orig = ForwardPaper._verdict
        out = {}

        def slow_verdict(self_, plan_, st_, result_):
            if threading.current_thread().name == "first":
                inside.set()
                go.wait(60)
            return orig(self_, plan_, st_, result_)
        self.at(plan, 70, 0)
        with mock.patch.object(ForwardPaper, "_verdict", slow_verdict):
            t = threading.Thread(target=lambda: out.update(r=fp.step(pid)), name="first")
            t.start()
            self.assertTrue(inside.wait(120))
            r2 = fp2.step(pid)
            go.set()
            t.join(120)
        self.assertEqual(r2["status"], "STEP_IN_PROGRESS")
        self.assertEqual(out["r"]["verdict"], "PAPER_PASSED", out["r"]["reasons"])
        self.assertEqual(len(self.evaluations(p)), 1)
        self.assertEqual(fp2.step(pid)["status"], "CONCLUDED")

if __name__ == "__main__":
    unittest.main()
