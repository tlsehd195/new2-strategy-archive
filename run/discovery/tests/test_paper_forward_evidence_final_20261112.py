"""ADR 0080 (directive "ADR 0078" of 2026-11-12, audit r2 finding F-3): the
evidence logs of a concluded plan must have exactly the rows the verdict was
computed from.

``verify_conclusion()`` compared the bar log only for rows REMOVED (shorter)
or REWRITTEN (head hash at the anchored row), and the funding / funding
quality logs only through the state heads (never against the files).  A valid,
chained row APPENDED to any of the three after the conclusion was silent.

SYNTHETIC data and temp dirs only.  No market data, no network, no exchange
account, no locked / IW / sealed window.  ``verify_conclusion`` stays read
only (asserted: nothing on disk changes).
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

from test_paper_forward_completeness_20261111 import CompletenessCase  # noqa: E402
from test_paper_forward_positions_20261111 import MIN15, read_log, write_log  # noqa: E402

HOUR4 = 4 * 3_600_000


def append_chained(path: Path, change) -> dict:
    """Append one valid row after the last one of a hash-chained log: a copy
    of the last row's body changed by ``change(body)``, with ``seq`` + 1 when
    the log is sequenced, ``prev`` = last hash, ``hash`` = chain."""
    from paper_forward import _chain
    rows = read_log(path)
    last = rows[-1]
    body = {k: v for k, v in last.items() if k not in ("hash", "prev")}
    change(body)
    if "seq" in body:
        body["seq"] = last["seq"] + 1
    row = {**body, "prev": last["hash"], "hash": _chain(last["hash"], body)}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def bump_bar(b):
    b["ts"] += MIN15
    b["observed_at"] = b["ts"] + MIN15


def bump_funding(b):
    b["ts"] += HOUR4
    b["observed_at"] = b["ts"]


def bump_quality(b):
    b["at"] = b.get("at", 0) + HOUR4


def tree_digest(root: Path) -> dict:
    return {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(root.rglob("*")) if f.is_file()}


class EvidenceFinalCase(CompletenessCase):
    def concluded(self):
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 70, 0)
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        self.assertTrue(fp.state(pid)["concluded"])
        self.assertEqual(fp.verify_conclusion(pid), [])
        return p, edge, fp, plan, fp.root / pid

    def verify_read_only(self, fp, pid):
        """verify_conclusion() never writes (ADR 0076 / 0080 §4.2)."""
        before = tree_digest(fp.p.dir)
        why = fp.verify_conclusion(pid)
        self.assertEqual(tree_digest(fp.p.dir), before)
        return why


# ---------------------------------------------------------------------------
# reproductions (written first; they FAIL on main 2d6616a)
# ---------------------------------------------------------------------------

class TestReproduction0078(EvidenceFinalCase):
    def test_repro_bar_row_appended_after_conclusion(self):
        """main: one chained, grid-aligned synthetic bar appended to the bar log of
        a PASSED plan -> verify_conclusion() returned [] (nothing found)."""
        p, edge, fp, plan, d = self.concluded()
        append_chained(d / "bars_BTCUSDT.jsonl", bump_bar)
        why = self.verify_read_only(fp, plan["plan_id"])
        self.assertTrue(any("bars_BTCUSDT.jsonl" in x for x in why), why)

    def test_repro_funding_row_appended_after_conclusion(self):
        p, edge, fp, plan, d = self.concluded()
        append_chained(d / "funding_BTCUSDT.jsonl", bump_funding)
        why = self.verify_read_only(fp, plan["plan_id"])
        self.assertTrue(any("funding_BTCUSDT.jsonl" in x for x in why), why)

    def test_repro_quality_log_row_appended_after_conclusion(self):
        p, edge, fp, plan, d = self.concluded()
        append_chained(d / "funding_quality_BTCUSDT.jsonl", bump_quality)
        why = self.verify_read_only(fp, plan["plan_id"])
        self.assertTrue(any("funding_quality_BTCUSDT.jsonl" in x for x in why), why)


# ---------------------------------------------------------------------------
# evidence-log-final-state-v1: row counts equal, heads equal
# ---------------------------------------------------------------------------

class TestEvidenceLogFinalState(EvidenceFinalCase):

    def test_policy_constant(self):
        from paper_forward import EVIDENCE_LOG_POLICY
        self.assertEqual(EVIDENCE_LOG_POLICY, "evidence-log-final-state-v1")

    def test_untouched_plan_verifies_and_nothing_is_written(self):
        p, edge, fp, plan, d = self.concluded()
        self.assertEqual(self.verify_read_only(fp, plan["plan_id"]), [])

    # one concluded plan per test (the fixture promotes the strategy once)
    def appended(self, name, bump):
        p, edge, fp, plan, d = self.concluded()
        n = len(read_log(d / name))
        append_chained(d / name, bump)
        append_chained(d / name, bump)
        why = self.verify_read_only(fp, plan["plan_id"])
        hit = [x for x in why if name in x]
        self.assertEqual(len(hit), 1, why)
        self.assertIn(f"has {n + 2} rows", hit[0])
        self.assertIn(f"computed from {n}", hit[0])
        self.assertIn("appended to or removed from a concluded plan", hit[0])

    def test_appended_bar_rows_name_both_counts(self):
        self.appended("bars_BTCUSDT.jsonl", bump_bar)

    def test_appended_funding_rows_name_both_counts(self):
        self.appended("funding_BTCUSDT.jsonl", bump_funding)

    def test_appended_quality_rows_name_both_counts(self):
        self.appended("funding_quality_BTCUSDT.jsonl", bump_quality)

    def removed(self, name):
        """The previous 'shorter' finding is kept (now in the same message)."""
        p, edge, fp, plan, d = self.concluded()
        rows = read_log(d / name)
        write_log(d / name, rows[:-1])
        why = self.verify_read_only(fp, plan["plan_id"])
        hit = [x for x in why if name in x]
        self.assertEqual(len(hit), 1, why)
        self.assertIn(f"has {len(rows) - 1} rows", hit[0])
        self.assertIn(f"computed from {len(rows)}", hit[0])

    def test_removed_bar_row(self):
        self.removed("bars_BTCUSDT.jsonl")

    def test_removed_funding_row(self):
        self.removed("funding_BTCUSDT.jsonl")

    def test_removed_quality_row(self):
        self.removed("funding_quality_BTCUSDT.jsonl")

    def rewritten(self, name, field):
        """A consistent, re-chained forgery of the same length: the head differs."""
        from paper_forward import _chain
        p, edge, fp, plan, d = self.concluded()
        rows = read_log(d / name)
        prev, out = "", []
        for r in rows:
            body = {k: v for k, v in r.items() if k not in ("hash", "prev")}
            body[field] = body[field] * 2 + 1
            h = _chain(prev, body)
            out.append({**body, "prev": prev, "hash": h})
            prev = h
        write_log(d / name, out)
        why = self.verify_read_only(fp, plan["plan_id"])
        self.assertTrue(any(name in x and "rewritten" in x for x in why), why)

    def test_rewritten_bar_log_same_count(self):
        self.rewritten("bars_BTCUSDT.jsonl", "c")

    def test_rewritten_funding_log_same_count(self):
        self.rewritten("funding_BTCUSDT.jsonl", "rate")

    def test_rewritten_quality_log_same_count(self):
        self.rewritten("funding_quality_BTCUSDT.jsonl", "rows")

    def test_broken_chain_is_reported_not_raised(self):
        p, edge, fp, plan, d = self.concluded()
        for name in ("funding_BTCUSDT.jsonl", "funding_quality_BTCUSDT.jsonl"):
            rows = read_log(d / name)
            rows[-1] = {**rows[-1], "hash": "0" * 64}
            write_log(d / name, rows)
        why = self.verify_read_only(fp, plan["plan_id"])
        self.assertTrue(why)
        self.assertTrue(any("evidence unreadable" in x or "funding_BTCUSDT.jsonl" in x for x in why), why)

    def test_legacy_anchor_without_row_count_falls_back_to_hash(self):
        """§4.3: an anchored funding / quality head without ``seq`` (no concluded
        plan has one: fixed by this test only) -> no row-count finding is made up;
        the head hash is compared with the last row of the log."""
        p, edge, fp, plan, d = self.concluded()
        rec = p.evaluation("paper_forward", edge)
        st = fp.state(plan["plan_id"])
        a = dict(rec["anchors"])
        a["funding_head"] = {s: {"hash": h["hash"]} for s, h in a["funding_head"].items()}
        a["quality_head"] = {s: {"hash": h["hash"]} for s, h in a["quality_head"].items()}
        legacy = dict(rec, anchors=a)
        why = fp._record_problems(plan, legacy, st)
        self.assertFalse(any("rows" in x and "jsonl" in x for x in why), why)
        self.assertFalse(any("rewritten" in x for x in why), why)
        append_chained(d / "funding_BTCUSDT.jsonl", bump_funding)
        why = fp._record_problems(plan, legacy, st)
        self.assertTrue(any("funding_BTCUSDT.jsonl" in x and "rewritten" in x for x in why), why)

    def test_resume_after_appended_row_is_refused(self):
        """A record appended, the state not yet saved (ADR 0077 resume path), and a
        row appended to the bar log in between: never completed."""
        from paper_forward import ForwardPaper
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        orig = ForwardPaper._commit_state
        calls = {"n": 0}

        def crash_on_conclusion(self_, plan_id, st, base_sha):
            if st.get("concluded"):
                calls["n"] += 1
                raise KeyboardInterrupt("killed after the record")
            return orig(self_, plan_id, st, base_sha)
        self.at(plan, 70, 0)
        from unittest import mock
        with mock.patch.object(ForwardPaper, "_commit_state", crash_on_conclusion):
            with self.assertRaises(KeyboardInterrupt):
                fp.step(pid)
        self.assertEqual(calls["n"], 1)
        self.assertIsNotNone(p.evaluation("paper_forward", edge))
        self.assertFalse(fp.state(pid)["concluded"])
        append_chained(fp.root / pid / "bars_BTCUSDT.jsonl", bump_bar)
        r = fp.step(pid)
        self.assertEqual(r["verdict"], "PAPER_ERROR", r)
        self.assertIn("EVALUATION_RECORD_CONFLICT", r["reasons"][0])
        self.assertIn("bars_BTCUSDT.jsonl", r["reasons"][0])
        self.assertFalse(fp.state(pid)["concluded"])

    def test_observing_plan_is_unaffected(self):
        """Before the conclusion rows are appended by every step as before."""
        p, edge, feed, fp, plan = self.plan()
        pid = plan["plan_id"]
        self.at(plan, 20, 0)
        r1 = fp.step(pid)
        n1 = len(read_log(fp.root / pid / "bars_BTCUSDT.jsonl"))
        self.at(plan, 21, 0)
        r2 = fp.step(pid)
        n2 = len(read_log(fp.root / pid / "bars_BTCUSDT.jsonl"))
        self.assertEqual(r1["verdict"], "PAPER_OBSERVING", r1)
        self.assertEqual(r2["verdict"], "PAPER_OBSERVING", r2)
        self.assertEqual(n2 - n1, 96)
        self.assertEqual(fp.verify_conclusion(pid), ["no final evaluation record for this plan"])


if __name__ == "__main__":
    unittest.main()
