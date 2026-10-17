"""Final residual fixes: FDR family notions, prior-context evidence, decision
immutability and experiment-context behaviour (spec sections 3-4).

Synthetic fixed p-values / synthetic klines only; isolated temp dirs; the real
ledger, state and locked TEST windows are never touched.
"""
import json
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine_lib import CandidateState  # noqa: E402
from generation import (  # noqa: E402
    FdrDecisionConflict, FdrDecisionStore, ManifestError, build_manifest,
    decide_family, family_status,
)
from test_residual_fixes import (  # noqa: E402
    EXPECTED_SURVIVORS, PVALS, SIDS, SPACE, _ctx_data, _fake_runner_cls, make_paths,
)

GEN_CTX = "C2-current"


def dev_rec(sid, man, ctx=GEN_CTX, p=0.5, status="AWAITING_FDR", eid=None, vv="PASS", **kw):
    return {"phase": "dev", "strategy_id": sid, "generation_id": man["generation_id"],
            "context_id": ctx, "status": status, "primary_dev_p": p,
            "evaluation_id": eid or f"E-{sid[:6]}-{ctx}", "validation_verdict": vv, **kw}


def full_records(man, ctx=GEN_CTX):
    out = []
    for sid in SIDS:
        p = PVALS[sid]
        out.append(dev_rec(sid, man, ctx, p=p,
                           status="UNDETERMINED_POWER" if p is None
                           else ("AWAITING_FDR" if p < 0.05 else "FAIL_STAT")))
    return out


class TestFamilyNotions(unittest.TestCase):
    def setUp(self):
        self.man = build_manifest(SPACE)

    def test_1_all_evaluated_current_context(self):
        fam = family_status(full_records(self.man), self.man, GEN_CTX)
        self.assertTrue(fam["manifest_complete"])
        self.assertTrue(fam["current_context_complete"])
        self.assertTrue(fam["fdr_decision_ready"])
        self.assertEqual(fam["complete"], fam["fdr_decision_ready"])      # compat alias
        self.assertEqual((fam["m"], fam["evaluated"], fam["unevaluated_current"]), (6, 6, []))
        d = decide_family(fam)
        self.assertEqual(d["survivors"], EXPECTED_SURVIVORS)
        self.assertEqual(d["statistical_survivors"], EXPECTED_SURVIVORS)
        self.assertEqual(d["survivors_validation_pass"], EXPECTED_SURVIVORS)   # validation PASS for all
        self.assertNotIn("locked_test_eligible", d)        # lock history is not part of the immutable decision
        self.assertEqual(d["m"], 6)
        self.assertFalse(fam["reevaluation_required"])

    def test_2_partial_evaluation_not_ready(self):
        fam = family_status(full_records(self.man)[:4], self.man, GEN_CTX)
        self.assertTrue(fam["manifest_complete"])
        self.assertFalse(fam["current_context_complete"])
        self.assertFalse(fam["fdr_decision_ready"])
        self.assertEqual(sorted(fam["unevaluated_current"]), sorted(SIDS[4:]))
        self.assertEqual(fam["m"], 6)                                      # nobody dropped from m
        self.assertEqual(sorted(fam["reevaluation_required"]), sorted(SIDS[4:]))
        with self.assertRaises(ValueError):
            decide_family(fam)

    def test_3_candidate_without_statistic_is_recorded_not_imputed(self):
        fam = family_status(full_records(self.man), self.man, GEN_CTX)
        self.assertEqual(fam["without_statistic"], [SIDS[5]])
        self.assertEqual((fam["n_with_statistic"], fam["n_without_statistic"]), (5, 1))
        self.assertIsNone(fam["pvalues"][SIDS[5]])                          # not 1.0, not 0
        d = decide_family(fam)
        self.assertEqual((d["n_with_statistic"], d["n_without_statistic"], d["m"]), (5, 1, 6))
        self.assertNotIn(SIDS[5], d["survivors"])
        self.assertEqual(d["without_statistic"], [SIDS[5]])

    def test_3b_nan_and_out_of_range_p_is_no_statistic(self):
        recs = full_records(self.man)
        recs[0] = dev_rec(SIDS[0], self.man, p=float("nan"))
        recs[1] = dev_rec(SIDS[1], self.man, p=1.5)
        fam = family_status(recs, self.man, GEN_CTX)
        self.assertEqual(sorted(fam["without_statistic"]), sorted([SIDS[0], SIDS[1], SIDS[5]]))
        self.assertTrue(fam["fdr_decision_ready"])
        d = decide_family(fam)
        self.assertNotIn(SIDS[0], d["survivors"])

    def test_4_prior_context_pass_is_not_a_current_evaluation(self):
        recs = [r for r in full_records(self.man) if r["strategy_id"] != SIDS[0]]
        recs.append(dev_rec(SIDS[0], self.man, ctx="C2-old", p=0.001, eid="E-old-dev"))
        recs.append({"phase": "final", "status": "PASS", "strategy_id": SIDS[0],
                     "generation_id": self.man["generation_id"], "context_id": "C2-old",
                     "evaluation_id": "E-old-final", "dev_evaluation_id": "E-old-dev"})
        fam = family_status(recs, self.man, GEN_CTX, terminal_pass={SIDS[0]})
        self.assertFalse(fam["current_context_complete"])
        self.assertFalse(fam["fdr_decision_ready"])
        self.assertIn(SIDS[0], fam["unevaluated_current"])
        self.assertIn(SIDS[0], fam["reevaluation_required"])
        info = fam["prior_context_pass"][SIDS[0]]
        self.assertEqual(info["context_id"], "C2-old")
        self.assertEqual(info["evaluation_id"], "E-old-final")
        self.assertEqual(info["dev_evaluation_id"], "E-old-dev")
        self.assertTrue(info["reevaluation_required"])
        self.assertEqual(fam["prior_context_only"][SIDS[0]]["context_id"], "C2-old")
        self.assertNotIn(SIDS[0], fam["pvalues"])                           # old p never reused
        with self.assertRaises(ValueError):
            decide_family(fam)
        # same-context immutable result IS reusable: once a current-context dev record exists
        recs.append(dev_rec(SIDS[0], self.man, p=0.001, eid="E-new-dev", prior_pass_reeval=True))
        fam2 = family_status(recs, self.man, GEN_CTX, terminal_pass={SIDS[0]})
        self.assertTrue(fam2["fdr_decision_ready"])
        self.assertEqual(fam2["prior_context_pass"], {})
        self.assertEqual(fam2["first_records"][SIDS[0]], "E-new-dev")

    def test_5_context_change_invalidates_everything(self):
        recs = full_records(self.man, ctx="C2-A")
        famA = family_status(recs, self.man, "C2-A")
        famB = family_status(recs, self.man, "C2-B")
        self.assertTrue(famA["fdr_decision_ready"])
        self.assertFalse(famB["fdr_decision_ready"])
        self.assertEqual(len(famB["unevaluated_current"]), 6)
        self.assertEqual(len(famB["reevaluation_required"]), 6)
        self.assertEqual(len(famB["prior_context_only"]), 6)
        self.assertEqual(famB["pvalues"], {})
        with tempfile.TemporaryDirectory() as td:
            store = FdrDecisionStore(Path(td) / "fdr.jsonl")
            store.record(decide_family(famA))
            self.assertIsNone(store.get(self.man["generation_id"], "C2-B"))  # decision not reused

    def test_6_manifest_hash_change(self):
        recs = full_records(self.man)
        smaller = build_manifest(SPACE[:-1])
        self.assertNotEqual(smaller["manifest_hash"], self.man["manifest_hash"])
        fam = family_status(recs, smaller, GEN_CTX)
        self.assertEqual(fam["evaluated"], 0)             # records belong to another generation
        self.assertEqual(fam["m"], 5)
        self.assertFalse(fam["fdr_decision_ready"])
        tampered = json.loads(json.dumps(self.man))
        tampered["candidates"][0][1] = tampered["candidates"][0][1].replace("lookback", "lookbacx")
        fam_t = family_status(recs, tampered, GEN_CTX)
        self.assertFalse(fam_t["manifest_complete"])
        self.assertFalse(fam_t["fdr_decision_ready"])
        self.assertTrue(fam_t["manifest_problems"])
        with self.assertRaises(ValueError):
            decide_family(fam_t)

    def test_7_same_context_rerun_is_idempotent(self):
        recs = full_records(self.man)
        d1 = decide_family(family_status(recs, self.man, GEN_CTX))
        d2 = decide_family(family_status(list(recs), self.man, GEN_CTX))
        self.assertEqual(d1["input_hash"], d2["input_hash"])
        self.assertEqual(d1["survivors"], d2["survivors"])
        with tempfile.TemporaryDirectory() as td:
            store = FdrDecisionStore(Path(td) / "fdr.jsonl")
            a = store.record(d1)
            b = store.record(d2)
            self.assertEqual(a, b)
            self.assertEqual(len((Path(td) / "fdr.jsonl").read_text().splitlines()), 1)

    def test_8_candidate_order_invariance(self):
        rng = random.Random(3)
        ref_man = build_manifest(SPACE)
        ref = decide_family(family_status(full_records(ref_man), ref_man, GEN_CTX))
        for _ in range(10):
            order = SPACE[:]
            rng.shuffle(order)
            man = build_manifest(order)
            self.assertEqual(man["manifest_hash"], ref_man["manifest_hash"])
            self.assertEqual(man["generation_id"], ref_man["generation_id"])
            recs = full_records(man)
            rng.shuffle(recs)
            d = decide_family(family_status(recs, man, GEN_CTX))
            self.assertEqual(d["survivors"], ref["survivors"])
            self.assertEqual(d["input_hash"], ref["input_hash"])

    def test_9_recording_the_same_decision_twice(self):
        d = decide_family(family_status(full_records(self.man), self.man, GEN_CTX))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fdr.jsonl"
            store = FdrDecisionStore(path)
            store.record(d)
            store.record(dict(d, decided_at="later"))
            self.assertEqual(len(path.read_text().splitlines()), 1)
            self.assertEqual(store.conflicts(), [])

    def test_10_conflicting_evidence_raises_and_is_logged(self):
        recs = full_records(self.man)
        d = decide_family(family_status(recs, self.man, GEN_CTX))
        changed = [dict(r, primary_dev_p=0.9) if r["strategy_id"] == SIDS[0] else r for r in recs]
        d2 = decide_family(family_status(changed, self.man, GEN_CTX))
        self.assertNotEqual(d["input_hash"], d2["input_hash"])
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fdr.jsonl"
            store = FdrDecisionStore(path)
            store.record(d)
            before = path.read_text()
            with self.assertRaises(FdrDecisionConflict):
                store.record(d2)
            self.assertEqual(path.read_text(), before)                    # never overwritten
            conf = store.conflicts()
            self.assertEqual(len(conf), 1)
            self.assertEqual(conf[0]["stored_input_hash"], d["input_hash"])
            self.assertEqual(conf[0]["new_input_hash"], d2["input_hash"])
            # a different evaluation record id for the same candidate is a conflict too
            d3 = decide_family(family_status(
                [dict(r, evaluation_id="E-other") if r["strategy_id"] == SIDS[1] else r for r in recs],
                self.man, GEN_CTX))
            with self.assertRaises(FdrDecisionConflict):
                store.record(d3)

    def test_11_missing_candidate(self):
        recs = [r for r in full_records(self.man) if r["strategy_id"] != SIDS[2]]
        fam = family_status(recs, self.man, GEN_CTX)
        self.assertEqual(fam["unevaluated_current"], [SIDS[2]])
        self.assertFalse(fam["fdr_decision_ready"])
        self.assertEqual(fam["m"], 6)                                      # not silently dropped
        # record for a candidate that is NOT in the manifest -> evidence inconsistency
        extra = recs + [dev_rec("S-unknown", self.man, p=0.001)] + [dev_rec(SIDS[2], self.man)]
        fam2 = family_status(extra, self.man, GEN_CTX)
        self.assertEqual(fam2["unregistered_records"], ["S-unknown"])
        self.assertFalse(fam2["fdr_decision_ready"])

    def test_12_duplicate_candidate_registration(self):
        with self.assertRaises(ManifestError):
            build_manifest(SPACE + [SPACE[0]])
        dup = json.loads(json.dumps(self.man))
        dup["strategy_ids"].append(dup["strategy_ids"][0])
        fam = family_status(full_records(self.man), dup, GEN_CTX)
        self.assertFalse(fam["manifest_complete"])
        self.assertFalse(fam["fdr_decision_ready"])
        # duplicate dev records: the FIRST one is the tested value
        recs = full_records(self.man) + [dev_rec(SIDS[0], self.man, p=0.9, eid="E-dup")]
        fam2 = family_status(recs, self.man, GEN_CTX)
        self.assertEqual(fam2["pvalues"][SIDS[0]], PVALS[SIDS[0]])
        self.assertEqual(fam2["duplicate_dev_records"], {SIDS[0]: 2})


class TestRunnerFinalization(unittest.TestCase):
    def _runner(self, td, space=None):
        cls = _fake_runner_cls(open_lock_gates=True)
        sp = space or SPACE
        return cls(make_paths(td), candidates_fn=lambda: (sp, []))

    def test_report_exposes_all_four_notions(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._runner(td)
            s = r.cycle(max_candidates=5)
            f = s.fdr_family
            self.assertTrue(f["manifest_complete"])
            self.assertFalse(f["current_context_complete"])
            self.assertFalse(f["fdr_decision_ready"])
            self.assertEqual((f["m"], f["evaluated"], f["missing"]), (6, 5, 1))
            s = r.cycle(max_candidates=5)
            f = s.fdr_family
            self.assertTrue(f["fdr_decision_ready"])
            self.assertEqual(f["n_without_statistic"], 1)
            self.assertEqual(f["decision"]["survivors"], EXPECTED_SURVIVORS)
            self.assertEqual(f["decision"]["statistical_survivors"], EXPECTED_SURVIVORS)
            self.assertEqual(f["decision"]["locked_test_eligible"], EXPECTED_SURVIVORS)   # no lock history yet
            self.assertEqual(f["decision"]["lock_blocked"], {})

    def test_prior_context_pass_is_reevaluated_not_trusted(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._runner(td)
            r.register_all()
            r._current_context()
            sid = SIDS[0]
            # a PASS earned under another context
            r.store.set(sid, CandidateState.RUNNING)
            r.store.set(sid, CandidateState.AWAITING_FDR, data_version="C2-old")
            r.store.set(sid, CandidateState.PASS, data_version="C2-old")
            r.ledger.append({"phase": "dev", "strategy_id": sid, "generation_id": r.generation_id,
                             "context_id": "C2-old", "evaluation_id": "E-old-dev",
                             "status": "AWAITING_FDR", "primary_dev_p": 0.0001})
            r.ledger.append({"phase": "final", "status": "PASS", "strategy_id": sid,
                             "generation_id": r.generation_id, "context_id": "C2-old",
                             "evaluation_id": "E-old-final", "dev_evaluation_id": "E-old-dev"})
            self.assertEqual(r.prior_pass_needing_reevaluation(), [sid])
            # first cycle: the other five are evaluated; the family must stay undecided
            s = r.cycle(max_candidates=3)
            self.assertFalse(s.fdr_family["fdr_decision_ready"])           # not decided while pending
            for _ in range(3):
                s = r.cycle(max_candidates=5)
            self.assertTrue(s.fdr_family["fdr_decision_ready"], s.fdr_family)
            # current-context statistic came from a fresh dev record, never the old 0.0001
            cur = [x for x in r.ledger.records() if x.get("strategy_id") == sid
                   and x.get("phase") == "dev" and x.get("context_id") == r._ctx]
            self.assertEqual(len(cur), 1)
            self.assertTrue(cur[0].get("prior_pass_reeval"))
            self.assertEqual(cur[0]["primary_dev_p"], PVALS[sid])
            self.assertEqual(r.store.entry(sid).state, "PASS")             # state untouched
            self.assertEqual(r.prior_pass_needing_reevaluation(), [])
            # old evidence preserved
            self.assertTrue(any(x.get("evaluation_id") == "E-old-final" for x in r.ledger.records()))

    def test_conflicting_stored_decision_blocks_finalization(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._runner(td)
            r.cycle(max_candidates=6)
            gid, ctx = r.generation_id, r._ctx
            path = r.paths.state_dir / "fdr_decisions.jsonl"
            rows = [json.loads(x) for x in path.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            rows[0]["input_hash"] = "0" * 64                              # corrupt stored evidence
            path.write_text(json.dumps(rows[0], sort_keys=True) + "\n")
            rep = r.finalize_fdr()
            self.assertIn("conflict", rep)
            self.assertFalse(rep["fdr_decision_ready"])
            self.assertEqual(rep["resolved"], [])
            self.assertEqual(len(r.fdr_store.conflicts()), 1)
            self.assertEqual(r.fdr_store.get(gid, ctx)["input_hash"], "0" * 64)   # untouched


def _consume(r, sid, sym, ctx):
    """Drive one locked-TEST attempt through reserve -> start -> complete."""
    res = r.lock_registry.reserve(sid, sym, ctx)
    assert res["ok"], res
    r.lock_registry.start(res["token"])
    r.lock_registry.complete(res["token"], {"pass": True, "status": "PASS"})


class TestLockEntryPolicy(unittest.TestCase):
    """Statistical survivors vs. candidates that may actually enter the locked TEST."""

    def _runner(self, td):
        r = _fake_runner_cls(open_lock_gates=True)(make_paths(td), candidates_fn=lambda: (SPACE, []))
        r.register_all()
        r._current_context()
        return r

    def _finals(self, r):
        return {x["strategy_id"]: x for x in r.ledger.records() if x.get("phase") == "final"}

    def test_consumed_candidate_is_survivor_but_not_eligible(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._runner(td)
            sid = SIDS[1]                                   # survivor (p=.008), validation PASS
            for sym in ("BTCUSDT", "ETHUSDT"):
                _consume(r, sid, sym, "C2-earlier")
            before = {sym: r.lock_registry.attempts(sid, sym) for sym in ("BTCUSDT", "ETHUSDT")}
            s = r.cycle(max_candidates=6)
            dec = s.fdr_family["decision"]
            self.assertIn(sid, dec["statistical_survivors"])
            self.assertIn(sid, dec["survivors_validation_pass"])
            self.assertNotIn(sid, dec["locked_test_eligible"])
            self.assertEqual(dec["lock_blocked"], {sid: "ALREADY_CONSUMED"})
            self.assertEqual(sorted(dec["locked_test_eligible"]), sorted(set(EXPECTED_SURVIVORS) - {sid}))
            fin = self._finals(r)[sid]
            self.assertEqual(fin["status"], "FAIL_STAT")
            self.assertTrue(fin["locked_test"]["reused"])
            self.assertIn("already consumed", fin["locked_test"]["note"])
            self.assertEqual({sym: r.lock_registry.attempts(sid, sym) for sym in ("BTCUSDT", "ETHUSDT")},
                             before)                         # no new reservation, history intact
            for sym in ("BTCUSDT", "ETHUSDT"):
                self.assertEqual(r.lock_registry.uses(sid, sym), 1)

    def test_per_symbol_consumption_blocks_the_whole_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._runner(td)
            sid = SIDS[0]
            _consume(r, sid, "BTCUSDT", "C2-earlier")       # ETH never exposed
            e = r.lock_entry_status(sid, r._defn_by_sid[sid])
            self.assertFalse(e["eligible"])
            self.assertEqual(e["reason"], "ALREADY_CONSUMED")
            self.assertFalse(e["per_symbol"]["BTCUSDT"]["enterable"])
            self.assertEqual(e["per_symbol"]["BTCUSDT"]["state"], "CONSUMED")
            self.assertTrue(e["per_symbol"]["ETHUSDT"]["enterable"])
            self.assertEqual(e["per_symbol"]["ETHUSDT"]["state"], "AVAILABLE")
            r.cycle(max_candidates=6)
            self.assertEqual(r.lock_registry.attempts(sid, "ETHUSDT"), [])   # ETH window not burnt
            self.assertEqual(r.lock_registry.uses(sid, "ETHUSDT"), 0)
            self.assertEqual(self._finals(r)[sid]["status"], "FAIL_STAT")

    def test_legacy_v1_use_counts_as_consumed(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._runner(td)
            sid = SIDS[2]
            r.lock_registry.record_use(sid, "ETHUSDT", {"note": "v1 record"})
            e = r.lock_entry_status(sid, r._defn_by_sid[sid])
            self.assertEqual(e["reason"], "ALREADY_CONSUMED")
            self.assertEqual(e["per_symbol"]["ETHUSDT"]["uses"], 1)

    def test_unexposed_blocked_attempt_does_not_consume(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._runner(td)
            sid = SIDS[2]
            toks = [r.lock_registry.reserve(sid, sym, r._ctx) for sym in ("BTCUSDT", "ETHUSDT")]
            for t in toks:
                r.lock_registry.block(t["token"], "data missing")
            e = r.lock_entry_status(sid, r._defn_by_sid[sid])
            self.assertTrue(e["eligible"], e)
            r.cycle(max_candidates=6)                       # still enters (and is BLOCKED again: no klines)
            self.assertEqual(len(r.lock_registry.attempts(sid, "BTCUSDT")), 2)
            self.assertEqual(r.lock_registry.uses(sid, "BTCUSDT"), 0)

    def test_active_attempt_is_in_progress_not_eligible(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._runner(td)
            sid = SIDS[0]
            r.lock_registry.reserve(sid, "BTCUSDT", r._ctx)
            e = r.lock_entry_status(sid, r._defn_by_sid[sid])
            self.assertEqual(e["reason"], "IN_PROGRESS")
            self.assertFalse(e["eligible"])
            s = r.cycle(max_candidates=6)
            self.assertEqual(s.fdr_family["decision"]["lock_blocked"].get(sid), "IN_PROGRESS")
            self.assertNotIn(sid, self._finals(r))                # waits, is not resolved
            self.assertEqual(r.store.entry(sid).state, "AWAITING_FDR")

    def test_prior_pass_with_consumed_test_is_never_reentered(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._runner(td)
            sid = SIDS[0]
            r.store.set(sid, CandidateState.RUNNING)
            r.store.set(sid, CandidateState.AWAITING_FDR, data_version="C2-old")
            r.store.set(sid, CandidateState.PASS, data_version="C2-old")
            for sym in ("BTCUSDT", "ETHUSDT"):
                _consume(r, sid, sym, "C2-old")
            before = [r.lock_registry.attempts(sid, sym) for sym in ("BTCUSDT", "ETHUSDT")]
            for _ in range(3):
                s = r.cycle(max_candidates=6)
            dec = s.fdr_family["decision"]
            self.assertIn(sid, dec["statistical_survivors"])
            self.assertNotIn(sid, dec["locked_test_eligible"])
            self.assertEqual(dec["lock_blocked"][sid], "ALREADY_CONSUMED")
            self.assertEqual(r.store.entry(sid).state, "PASS")
            self.assertNotIn(sid, self._finals(r))                # no second final verdict
            self.assertEqual([r.lock_registry.attempts(sid, sym) for sym in ("BTCUSDT", "ETHUSDT")], before)


class TestLegacyFdrDecisions(unittest.TestCase):
    """A stored decision without input_hash cannot prove which evidence it rested on."""

    def setUp(self):
        self.man = build_manifest(SPACE)
        self.recs = full_records(self.man)
        self.new = decide_family(family_status(self.recs, self.man, GEN_CTX))

    def _legacy(self, **over):
        d = {k: v for k, v in self.new.items()
             if k not in ("input_hash", "manifest_hash", "input_spec", "decided_at")}
        d.update(over)
        return d

    def test_same_results_same_p_still_not_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fdr.jsonl"
            path.write_text(json.dumps(self._legacy(), sort_keys=True) + "\n")
            before = path.read_text()
            store = FdrDecisionStore(path)
            with self.assertRaises(FdrDecisionConflict):
                store.record(self.new)
            self.assertEqual(path.read_text(), before)            # never overwritten
            self.assertIn("not verifiable", store.conflicts()[0]["reason"])

    def test_same_results_but_different_p_values(self):
        # identical survivors / m / first records / q / method, different underlying p-values
        shifted = [dict(r, primary_dev_p=(r["primary_dev_p"] * 1.05 if r["primary_dev_p"] else None))
                   for r in self.recs]
        other = decide_family(family_status(shifted, self.man, GEN_CTX))
        self.assertEqual(other["survivors"], self.new["survivors"])
        self.assertEqual(other["first_records"], self.new["first_records"])
        self.assertNotEqual(other["input_hash"], self.new["input_hash"])
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fdr.jsonl"
            path.write_text(json.dumps(self._legacy(), sort_keys=True) + "\n")
            before = path.read_text()
            store = FdrDecisionStore(path)
            for cand in (other, self.new):
                with self.assertRaises(FdrDecisionConflict):
                    store.record(cand)
            self.assertEqual(path.read_text(), before)
            self.assertEqual(len(store.conflicts()), 2)

    def test_new_decision_without_hash_is_not_trusted_either(self):
        with tempfile.TemporaryDirectory() as td:
            store = FdrDecisionStore(Path(td) / "fdr.jsonl")
            store.record(self.new)
            with self.assertRaises(FdrDecisionConflict):
                store.record({k: v for k, v in self.new.items() if k != "input_hash"})

    def test_runner_holds_the_family_on_a_legacy_decision(self):
        r = _fake_runner_cls()(make_paths(tempfile.mkdtemp()), candidates_fn=lambda: (SPACE, []))
        self.addCleanup(shutil.rmtree, str(r.paths.root.parent), True)
        r.register_all()
        r._current_context()
        gid, ctx = r.generation_id, r._ctx
        r.cycle(max_candidates=5)                               # 5 of 6: no decision yet
        path = r.paths.state_dir / "fdr_decisions.jsonl"
        legacy = {"generation_id": gid, "context_id": ctx, "method": "BH_full_generation_fixed_sample_v1",
                  "q": 0.05, "m": 6, "survivors": EXPECTED_SURVIVORS, "n_with_statistic": 5,
                  "n_without_statistic": 1, "first_records": {}}
        path.write_text(json.dumps(legacy, sort_keys=True) + "\n")
        before = path.read_text()
        s = r.cycle(max_candidates=5)                           # family completes -> legacy decision found
        self.assertIn("conflict", s.fdr_family)
        self.assertFalse(s.fdr_family["fdr_decision_ready"])
        self.assertIsNone(s.fdr_family["decision"])
        self.assertEqual(path.read_text(), before)
        self.assertFalse(any(x.get("phase") == "final" for x in r.ledger.records()))
        self.assertFalse((r.paths.state_dir / "lock_test_usage.json").exists())   # no TEST touched
        self.assertEqual(len(r.fdr_store.conflicts()), 1)


class TestContextBehaviour(unittest.TestCase):
    def _ctx(self, td, space=None, **patch):
        import runner as rm
        paths = make_paths(td)
        _ctx_data(paths.data_dir)
        sp = space or SPACE
        from runner import Runner
        old = {k: getattr(rm, k) for k in patch}
        try:
            for k, v in patch.items():
                setattr(rm, k, v)
            r = Runner(paths, candidates_fn=lambda: (sp, []), allow_test_cost=True)
            return r._current_context(), r
        finally:
            for k, v in old.items():
                setattr(rm, k, v)

    def test_same_inputs_same_context(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertEqual(self._ctx(a)[0], self._ctx(b)[0])

    def test_candidate_order_does_not_change_context(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertEqual(self._ctx(a)[0], self._ctx(b, space=list(reversed(SPACE)))[0])

    def test_strategy_space_change_changes_context(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertNotEqual(self._ctx(a)[0], self._ctx(b, space=SPACE[:-1])[0])

    def test_validation_window_change_changes_context(self):
        import runner as rm
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertNotEqual(self._ctx(a)[0],
                                self._ctx(b, VALIDATION_MONTHS=rm.VALIDATION_MONTHS + 1)[0])

    def test_series_hash_ignores_row_order(self):
        from runner import _series_sha
        rows = [(3, 0.3), (1, 0.1), (2, 0.2)]
        self.assertEqual(_series_sha(rows), _series_sha(sorted(rows)))
        self.assertNotEqual(_series_sha(rows), _series_sha([(1, 0.1), (2, 0.2), (3, 0.31)]))

    def test_context_json_key_order_irrelevant(self):
        from engine_lib import experiment_context
        base = {"data": {"a": 1, "b": 2}, "data_policy": {"x": 1}, "cost": {"p": 1.5},
                "code": {"h": "1"}, "validation": {"v": 1}, "runtime": {"py": "3"}}
        rev = {k: dict(reversed(list(v.items()))) for k, v in reversed(list(base.items()))}
        self.assertEqual(experiment_context(base)[0], experiment_context(rev)[0])

    def test_git_commit_is_informational_not_hashed(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            c1, r1 = self._ctx(a, git_commit=lambda: "aaa")
            c2, r2 = self._ctx(b, git_commit=lambda: "bbb")
            self.assertEqual(c1, c2)
            self.assertEqual(r1.contexts.load(c1)["informational"]["git_commit"], "aaa")

    def test_context_change_requeues_old_results(self):
        # covered end-to-end by TestContextId.test_old_context_records_reevaluated_not_overwritten;
        # here: a FAIL_STAT from another context is queued, a current-context one is not
        cls = _fake_runner_cls()
        with tempfile.TemporaryDirectory() as td:
            r = cls(make_paths(td), candidates_fn=lambda: (SPACE, []))
            r.register_all()
            r._current_context()
            for sid, ctx in ((SIDS[0], "C2-old"), (SIDS[1], r._ctx)):
                r.store.set(sid, CandidateState.RUNNING)
                r.store.set(sid, CandidateState.FAIL_STAT)
                r.ledger.append({"evaluation_id": f"E-{sid}", "strategy_id": sid, "phase": "dev",
                                 "status": "FAIL_STAT", "context_id": ctx})
            q = r.schedule_context_reevaluation()
            self.assertEqual(q, [SIDS[0]])
            self.assertEqual(r.store.entry(SIDS[1]).state, "FAIL_STAT")


if __name__ == "__main__":
    unittest.main()
