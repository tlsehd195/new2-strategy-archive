"""Directive 2026-10-22 (ADR 0028): DEV -> independent validation flow,
preregistration lifecycle gate, near-duplicate cases, cost sensitivity,
ledger / report consistency, restart, reproducibility, criteria pinning and
repository independence.  Synthetic data and temp dirs only — no real window
is opened, no network, no account."""
from __future__ import annotations

import copy
import hashlib
import json
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

import independent_window as iw  # noqa: E402
import prereg_gate as pg  # noqa: E402
from engine_lib import append_jsonl, canonical_json, iter_jsonl, strategy_id  # noqa: E402
from strategy_spec import SPEC_SCHEMA, canonicalize_spec  # noqa: E402
from test_independent_window import REAL_HISTORY, small_prereg, write_symbol  # noqa: E402
from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider, hour_spec, verified_fee_cost  # noqa: E402
from validation_registry import RegistryCorrupt, RegistryRefused, ValidationRegistry, members_pin  # noqa: E402

LOOSE_DEV = {"dev_screen": {"min_t": -99, "min_mean_bp": -1e9, "min_fold_positive_share": 0,
                            "max_cost_share": 1e9, "min_events_per_symbol": 1}}


def members_file(pre, sha, specs, *, ctx="C2-test", code=True) -> dict:
    ms = [{"strategy_id": strategy_id(canonicalize_spec(s)), "spec": canonicalize_spec(s)} for s in specs]
    body = {"schema": iw.MEMBERS_SCHEMA, "window_id": pre.get("window_id", "FW2"), "prereg_sha256": sha,
            "context_id": ctx, "state_dir": "", "rule": "test", "members": ms, "excluded_before_test": {}}
    if code:
        body["code"] = {"code_hash": pg.research_code_identity()["code_hash"]}
    body["members_sha256"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return body


# ---------------------------------------------------------------------------
# 2 / 7: DEV -> independent validation flow, restart, reproducibility
# ---------------------------------------------------------------------------

class TestValidationBatchRestart(PipelineCase):
    def test_crash_between_member_status_and_result_row_resumes_without_duplicates(self):
        import pipeline
        p = self.pipe(SyntheticProvider(), cfg_patch=LOOSE_DEV)
        self.register_all(p, [EDGE] + NOISE[:7])
        p.preregister_batch(p.validation_candidates())
        real = pipeline.append_jsonl

        def boom(path, rec):
            if rec.get("kind") == "RESULT":
                raise KeyboardInterrupt("stopped before the RESULT row")
            return real(path, rec)
        pipeline.append_jsonl = boom
        try:
            with self.assertRaises(KeyboardInterrupt):
                p.run_validation()
        finally:
            pipeline.append_jsonl = real
        p2 = self.pipe(SyntheticProvider(), cfg_patch=LOOSE_DEV)
        out = p2.run_validation()              # used to raise InvalidStatusTransition (REJECTED -> REJECTED)
        self.assertIn("outcome", out)
        vals = [r["strategy_id"] for r in iter_jsonl(p2.eval_path) if r["stage"] == "validation"]
        self.assertEqual(len(vals), len(set(vals)))
        self.assertEqual(sum(1 for r in iter_jsonl(p2.batch_path) if r["kind"] == "RESULT"), 1)
        from evolution_report import build_report
        rep = build_report(self.td / "state", registry_path=self.td / "none.jsonl")
        self.assertTrue(rep["consistency"]["ok"], rep["consistency"])

    def test_status_write_failure_after_dev_record_is_repaired_on_rerun(self):
        import pipeline
        p = self.pipe(SyntheticProvider())
        sid, _ = p.register(EDGE, source="test")
        real = pipeline.StatusStore.set

        def fail(self_, *a, **k):
            raise OSError("disk full while writing the status")
        pipeline.StatusStore.set = fail
        try:
            with self.assertRaises(OSError):
                p.dev_evaluate(sid)
        finally:
            pipeline.StatusStore.set = real
        p2 = self.pipe(SyntheticProvider())
        self.assertIsNone(p2.statuses.status(sid, p2.context_id()))
        p2.dev_evaluate(sid)
        self.assertEqual(p2.statuses.status(sid, p2.context_id()), "RESEARCH_ONLY")
        self.assertEqual(sum(1 for r in iter_jsonl(p2.eval_path) if r["stage"] == "dev"), 1)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.reg = ValidationRegistry(self.td / "reg.jsonl")
        self.ms = [{"strategy_id": strategy_id(canonicalize_spec(EDGE)), "spec": canonicalize_spec(EDGE)}]
        self.body = dict(window_key="W:POOLED:1", prereg_sha256="p" * 64, members_sha256="m" * 64,
                         members=members_pin(self.ms), code_hash="c" * 64, config={"a": 1},
                         cost={"version": "v"})

    def test_lifecycle_and_refusals(self):
        r = self.reg
        r.register("R1", **self.body)
        self.assertEqual(r.register("R1", **self.body)["event"], "REGISTERED")     # idempotent
        with self.assertRaises(RegistryRefused):
            r.register("R1", **{**self.body, "config": {"a": 2}})               # never replaced
        with self.assertRaises(RegistryRefused):
            r.complete("R1", members_sha256="m" * 64, code_hash="c" * 64, result_sha256="x", outcomes={})
        with self.assertRaises(RegistryRefused):
            r.start("R1", members_sha256="m" * 64, code_hash="d" * 64)            # code changed
        r.start("R1", members_sha256="m" * 64, code_hash="c" * 64)
        self.assertEqual(r.summary()["incomplete_runs"], ["R1"])
        self.assertEqual(r.summary()["completed_members"], 0)                     # running != completed
        sid = self.ms[0]["strategy_id"]
        with self.assertRaises(RegistryRefused):
            r.complete("R1", members_sha256="m" * 64, code_hash="c" * 64, result_sha256="x",
                       outcomes={"S-other": "REJECTED"})
        with self.assertRaises(RegistryRefused):
            r.complete("R1", members_sha256="m" * 64, code_hash="c" * 64, result_sha256="x",
                       outcomes={sid: "PASSED"})                                   # not the vocabulary
        r.complete("R1", members_sha256="m" * 64, code_hash="c" * 64, result_sha256="x",
                   outcomes={sid: "INCONCLUSIVE"})
        with self.assertRaises(RegistryRefused):
            r.complete("R1", members_sha256="m" * 64, code_hash="c" * 64, result_sha256="y",
                       outcomes={sid: "PASSED_INDEPENDENT_WINDOW"})               # no overwrite
        self.assertEqual(r.summary()["outcomes"], {"INCONCLUSIVE": 1})
        self.assertEqual(r.summary()["independent_passes"], [])

    def test_member_drift_and_invalidation(self):
        r = self.reg
        r.register("R1", **self.body)
        changed = copy.deepcopy(self.ms)
        from evolution import _strip_derived
        sp = _strip_derived(changed[0]["spec"])
        sp["exit"]["horizon"] = 9
        changed[0]["spec"] = canonicalize_spec(sp)
        self.assertEqual(r.member_drift("R1", changed), [f"{self.ms[0]['strategy_id']}: definition changed"])
        r.start("R1", members_sha256="m" * 64, code_hash="c" * 64)
        sid = self.ms[0]["strategy_id"]
        r.complete("R1", members_sha256="m" * 64, code_hash="c" * 64, result_sha256="x",
                   outcomes={sid: "PASSED_INDEPENDENT_WINDOW"})
        self.assertEqual(r.summary()["independent_passes"], [sid])
        r.invalidate("R1", "definition changed after the run")
        self.assertEqual(r.summary()["independent_passes"], [])
        self.assertEqual(r.state("R1"), "INVALIDATED")

    def test_corrupt_ledger_fails_closed(self):
        self.reg.register("R1", **self.body)
        self.reg.start("R1", members_sha256="m" * 64, code_hash="c" * 64)
        lines = self.reg.path.read_text().splitlines()
        self.reg.path.write_text(lines[0] + "\n{not json\n")
        with self.assertRaises(RegistryCorrupt):
            self.reg.runs()
        rec = json.loads(lines[1])
        rec["code_hash"] = "e" * 64
        self.reg.path.write_text(lines[0] + "\n" + json.dumps(rec) + "\n")
        with self.assertRaises(RegistryCorrupt):                                   # tampered
            self.reg.runs()
        self.reg.path.write_text(lines[1] + "\n")
        with self.assertRaises(RegistryCorrupt):                                   # chain broken
            self.reg.runs()


class TestIndependentFlow(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.data = self.td / "data"
        for sym, seed in (("BTCUSDT", 1), ("BBBUSDT", 40)):
            write_symbol(self.data, sym, ["2020-01", "2020-02", "2020-03"], seed=seed)
        self.pre = small_prereg()
        self.sha = "f" * 64
        self.members = members_file(self.pre, self.sha, [hour_spec(0, 2), hour_spec(6, 8), hour_spec(12, 14)])
        self.gate = {"lifecycle": "ACTIVE", "prereg_sha256": self.sha}

    def hist(self, name="h.jsonl"):
        p = self.td / name
        if not p.exists():
            for r in iter_jsonl(REAL_HISTORY):
                append_jsonl(p, r)
        return p

    def open(self, **kw):
        kw.setdefault("gate", self.gate)
        kw.setdefault("registry_path", self.td / "reg.jsonl")
        hist = kw.pop("hist", None) or self.hist()
        out = kw.pop("out", self.td / "out")
        return iw.open_tranche(self.pre, self.sha, "A", self.data, self.members, hist, out,
                               verified_fee_cost(), **kw)

    def test_draft_gate_opens_nothing(self):
        h = self.hist()
        n0 = len(list(iter_jsonl(h)))
        for gate in (None, {"lifecycle": "DRAFT", "prereg_sha256": self.sha},
                     {"lifecycle": "ACTIVE", "prereg_sha256": "0" * 64}):
            with self.assertRaises(iw.WindowRefused):
                self.open(gate=gate)
        self.assertEqual(len(list(iter_jsonl(h))), n0)
        self.assertFalse((self.td / "reg.jsonl").exists())

    def test_completed_run_is_recorded_and_never_replaced(self):
        r = self.open()
        reg = ValidationRegistry(self.td / "reg.jsonl")
        self.assertEqual(reg.state(r["run_id"]), "COMPLETED")
        self.assertEqual(reg.runs()[r["run_id"]]["completed"]["outcomes"], r["outcome"])
        self.assertEqual(reg.runs()[r["run_id"]]["completed"]["result_sha256"], r["record_sha256"])
        self.assertEqual(r["repro"]["result"]["record_sha256"], r["record_sha256"])
        self.assertIn("code", r["repro"])
        with self.assertRaises(iw.WindowRefused):
            self.open()
        with self.assertRaises(iw.WindowRefused):
            self.open(resume=True)

    def test_stop_after_window_row_resumes_once_and_reproduces(self):
        real = iw.member_events
        calls = {"n": 0}

        def boom(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise KeyboardInterrupt("stopped while evaluating")
            return real(*a, **k)
        iw.member_events = boom
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.open()
        finally:
            iw.member_events = real
        reg = ValidationRegistry(self.td / "reg.jsonl")
        rid = iw.run_id_for(self.pre, self.sha, "A", self.members)
        self.assertEqual(reg.state(rid), "RUNNING")
        self.assertEqual(reg.summary()["completed_members"], 0)
        with self.assertRaises(iw.WindowRefused):
            self.open()                                     # not without --resume
        changed = copy.deepcopy(self.members)
        changed["members"] = changed["members"][:2]
        r = self.open(resume=True)
        self.assertEqual(reg.state(rid), "COMPLETED")
        key = iw.window_key(self.pre, "A")
        self.assertEqual(sum(1 for x in iter_jsonl(self.hist()) if key in x.get("window_keys", [])), 1)
        # same code, data and members in a fresh history / registry: identical record hash
        clean = self.open(hist=self.hist("h2.jsonl"), registry_path=self.td / "reg2.jsonl", out=self.td / "o2")
        from repro_bundle import verify_rerun
        self.assertEqual(verify_rerun(r["repro"], clean), [])
        self.assertEqual(clean["record_sha256"], r["record_sha256"])

    def test_report_keeps_dev_and_independent_apart(self):
        from evolution_report import build_report
        st = self.td / "state"
        from pipeline import EvolutionPipeline
        p = EvolutionPipeline(st, SyntheticProvider(), verified_fee_cost(), symbols=["BTCUSDT"])
        for sp in [EDGE] + NOISE[:3]:
            sid, _ = p.register(sp, source="test")
            p.dev_evaluate(sid)
        rep = build_report(st, registry_path=self.td / "reg.jsonl")
        self.assertGreaterEqual(rep["dev"]["screen_pass"], 1)
        self.assertEqual(rep["independent"]["completed_members"], 0)
        self.assertEqual(rep["independent"]["independent_passes"], [])
        self.assertEqual(rep["outcomes"]["total"].get("PASS", 0), 0)


# ---------------------------------------------------------------------------
# 3: preregistration lifecycle
# ---------------------------------------------------------------------------

class FakeGit:
    def __init__(self, t=None, clean=True):
        self.t, self.clean = t, clean

    def committed_clean(self, path):
        return self.clean

    def last_commit_time(self, path):
        return self.t


def forward_prereg() -> dict:
    return {"schema": "forward_window_prereg_v2", "status": "final text", "family": "fw-test",
            "window_id": "FW2",
            "window": {"window_start": "2030-11-01", "window_end": "2031-10-31", "single_look": "once"},
            "basket": {"symbols": ["BTCUSDT", "ETHUSDT"]},
            "members": {"max": 5, "rule": "first 5"},
            "cost": {"profile": "assumed_public_v1", "order_value": 10000.0},
            "test": {"method": "stepm K=1", "alpha": 0.0304, "bootstrap_B": 10000, "pass": "rule"},
            "single_use": "history row first"}


class TestPreregGate(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.pre_p = self.td / "prereg.json"
        self.write_pre(forward_prereg())
        self.mem_p = self.td / "members.json"
        self.mem_p.write_text(json.dumps(members_file(forward_prereg(), self.sha, [EDGE])))
        self.act_p = self.td / "activation.json"
        self.ok_git = FakeGit(t=pg._day_s_utc("2030-10-30"))

    def write_pre(self, pre):
        raw = json.dumps(pre).encode()
        self.pre_p.write_bytes(raw)
        self.sha = hashlib.sha256(raw).hexdigest()

    def activation(self, **over):
        rec = {"prereg_sha256": self.sha,
               "members_sha256": json.loads(self.mem_p.read_text())["members_sha256"],
               "reviewed_by": "동동", "approved_by": "동동", "approved_at": "2030-10-30",
               "checklist": {k: True for k in pg.CHECKLIST}, **over}
        self.act_p.write_text(json.dumps(pg.seal_activation(rec)))

    def ev(self, git=None):
        return pg.evaluate(self.pre_p, activation_path=self.act_p, members_path=self.mem_p, git=git or self.ok_git)

    def test_no_activation_is_draft_and_window_not_started(self):
        rep = pg.evaluate(self.pre_p, members_path=self.mem_p, git=self.ok_git)
        self.assertEqual(rep["lifecycle"], "DRAFT")
        self.assertTrue(rep["human_items"])
        import datetime as dt
        self.assertTrue(pg.window_status(rep, forward_prereg(), dt.date(2031, 1, 1)).startswith("NOT_STARTED"))
        with self.assertRaises(pg.GateRefused):
            pg.require_active(self.pre_p, activation_path=None, members_path=self.mem_p, git=self.ok_git)

    def test_complete_activation_is_active(self):
        self.activation()
        rep = self.ev()
        self.assertEqual(rep["lifecycle"], "ACTIVE", rep["issues"])
        import datetime as dt
        self.assertEqual(pg.window_status(rep, forward_prereg(), dt.date(2030, 10, 31)), "ACTIVE_BEFORE_WINDOW")
        self.assertEqual(pg.window_status(rep, forward_prereg(), dt.date(2031, 2, 1)), "IN_WINDOW")

    def test_reviewed_only_and_open_checklist(self):
        self.activation(approved_by="", approved_at="")
        self.assertEqual(self.ev()["lifecycle"], "REVIEWED")
        ck = {k: True for k in pg.CHECKLIST}
        ck["final_read_through_by_user"] = False
        self.activation(checklist=ck)
        rep = self.ev()
        self.assertEqual(rep["lifecycle"], "REVIEWED")
        self.assertIn("checklist: final_read_through_by_user", rep["human_items"])

    def test_dates_alone_cannot_activate_or_disguise(self):
        self.activation()
        pre = forward_prereg()
        pre["window"]["window_start"] = "2030-12-01"           # edit a date after activation
        self.write_pre(pre)
        self.assertEqual(self.ev()["lifecycle"], "INVALID")
        self.write_pre(forward_prereg())
        rec = json.loads(self.act_p.read_text())
        rec["approved_at"] = "2030-01-01"                       # back-date the approval text
        self.act_p.write_text(json.dumps(rec))
        self.assertEqual(self.ev()["lifecycle"], "INVALID")
        self.activation()
        late = FakeGit(t=pg._day_s_utc("2030-11-02"))           # committed after window_start
        self.assertEqual(self.ev(late)["lifecycle"], "INVALID")
        self.assertEqual(self.ev(FakeGit(t=self.ok_git.t, clean=False))["lifecycle"], "INVALID")

    def test_draft_schema_never_active(self):
        pre = forward_prereg()
        pre["schema"] = "forward_window_prereg_v2_DRAFT"
        self.write_pre(pre)
        self.mem_p.write_text(json.dumps(members_file(pre, self.sha, [EDGE])))
        self.activation()
        rep = self.ev()
        self.assertEqual(rep["lifecycle"], "INVALID")
        self.assertTrue(rep["draft_marks"])

    def test_change_after_freeze_is_detected(self):
        m = json.loads(self.mem_p.read_text())
        m["members"][0]["spec"]["exit"]["horizon"] = 16        # definition edited after freeze
        self.mem_p.write_text(json.dumps(m))
        self.activation()
        self.assertTrue(any("modified after freeze" in x for x in self.ev()["issues"]))
        m = members_file(forward_prereg(), self.sha, [EDGE])
        m["code"]["code_hash"] = "0" * 64                       # frozen against other code
        body = {k: v for k, v in m.items() if k != "members_sha256"}
        m["members_sha256"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
        self.mem_p.write_text(json.dumps(m))
        self.activation()
        self.assertTrue(any("FROZEN_CODE_CHANGED" in x for x in self.ev()["issues"]))

    def test_required_fields(self):
        pre = forward_prereg()
        del pre["test"]["alpha"]
        self.write_pre(pre)
        self.mem_p.write_text(json.dumps(members_file(pre, self.sha, [EDGE])))
        self.activation()
        rep = self.ev()
        self.assertEqual(rep["lifecycle"], "INVALID")
        self.assertIn("test.alpha", rep["required_missing"])

    def test_real_repository_state_has_nothing_active(self):
        v2 = DISC.parent / "audit" / "evidence_prereg_v2_20261021" / "forward_window_v2_DRAFT.json"
        rep = pg.evaluate(v2)
        self.assertEqual(rep["lifecycle"], "DRAFT")
        self.assertTrue(rep["draft_marks"])
        iw1 = pg.evaluate(DISC / "config" / "independent_window_v1.json",
                          members_path=DISC.parent / "audit" / "evidence_independent_window_20261021" / "IW1_members.json")
        self.assertEqual(iw1["lifecycle"], "DRAFT")              # no activation record exists
        self.assertEqual(iw1["required_missing"], [])
        self.assertEqual(list(DISC.parent.rglob("*activation*.json")), [])
        self.assertFalse((DISC / "validation_registry.jsonl").exists())


# ---------------------------------------------------------------------------
# 4: duplicates (the seven directive cases)
# ---------------------------------------------------------------------------

def zspec(window=96, thr=1.0, *, horizon=8, stop=None, extra=None, note=None):
    s = {"schema": SPEC_SCHEMA, "direction": "long", "symbols": ["BTCUSDT"],
         "entry": {"logic": "all", "conditions": [
             {"feature": "ret_z", "params": {"window": window}, "op": "gt", "value": thr}]},
         "filters": [extra] if extra else [], "exit": {"horizon": horizon}}
    if stop:
        s["exit"]["stop_loss_bp"] = stop
    if note:
        s["_note"] = note
    return s


class TestDuplicates(PipelineCase):
    def test_1_identical_and_2_name_only(self):
        from evolution import behaviour_key, definition_fingerprint
        a, b = zspec(), zspec(note="a different name for the same rule")
        self.assertEqual(strategy_id(canonicalize_spec(a)), strategy_id(canonicalize_spec(b)))
        self.assertEqual(definition_fingerprint(a), definition_fingerprint(b))
        self.assertEqual(behaviour_key(a), behaviour_key(b))

    def test_3_small_parameter_change_same_behaviour(self):
        from evolution import behaviour_key, near_duplicate
        # syntactic: hour bounds between the same integers are one rule
        self.assertEqual(behaviour_key(hour_spec(0, 2)), behaviour_key(hour_spec(0, 1.5)))
        # threshold within half a mutation step: kept but flagged, and on DEV data
        # the two trade identically -> the overlap diagnostic groups them
        never = {"feature": "volume_z", "params": {"window": 96}, "op": "gt", "value": -7.9}
        never2 = {**never, "value": -7.8}
        a, b = hour_spec(0, 2), hour_spec(0, 2)
        a["filters"], b["filters"] = [never], [never2]
        self.assertNotEqual(behaviour_key(a), behaviour_key(b))
        self.assertIsNotNone(near_duplicate(a, b))
        import behaviour_overlap as bo
        p = self.pipe(SyntheticProvider())
        ids = self.register_all(p, [a, b])
        evs = {s: bo.dev_events(p, s) for s in ids}
        self.assertEqual(bo.overlap(evs[ids[0]], evs[ids[1]])["kind"], "IDENTICAL_TRADES")
        self.assertEqual(bo.report(evs)["distinct_behaviours"], 1)

    def test_4_other_holding_or_stop_is_a_different_strategy(self):
        from evolution import behaviour_key, near_duplicate
        base = hour_spec(0, 2)
        longer = hour_spec(0, 2, horizon=16)
        stopped = copy.deepcopy(base)
        stopped["exit"]["stop_loss_bp"] = 20.0
        for other in (longer, stopped):
            self.assertNotEqual(behaviour_key(base), behaviour_key(other))
            self.assertIsNone(near_duplicate(base, other))
        import behaviour_overlap as bo
        p = self.pipe(SyntheticProvider())
        ids = self.register_all(p, [base, longer])
        o = bo.overlap(bo.dev_events(p, ids[0]), bo.dev_events(p, ids[1]))
        self.assertEqual(o["kind"], "DIFFERENT")                # same entries are not enough
        self.assertNotEqual(o["mean_hold_bars_a"], o["mean_hold_bars_b"])

    def _round(self, known_specs, elite, specs, seed=1):
        from search import SearchConfig, propose_round
        cfg = SearchConfig(seed=seed, children_per_round=30, exploration_share=0.0, crossover_share=0.0,
                           operator_weights={"change_risk": 1})
        return propose_round(cfg=cfg, round_index=0, elite=elite, specs=specs, feedback={},
                             available_data=["klines"], known_ids=set(known_specs), symbols=["BTCUSDT"],
                             known_specs=known_specs)

    def test_5_repeated_lineage_and_6_existing_vs_evolved(self):
        parent = canonicalize_spec(hour_spec(0, 2))
        pid = strategy_id(parent)
        out, c = self._round({pid: parent}, [pid], {pid: parent})
        # change_risk children trade exactly like the known parent: refused with
        # the parent as representative, every refusal logged
        self.assertEqual(out, [])
        self.assertGreater(c["semantic_duplicates"], 0)
        self.assertTrue(all(x["representative"] == pid for x in c["dedup_log"]))
        self.assertTrue(all(x["kind"] == "behaviour_equal" for x in c["dedup_log"]))
        # the same parent proposed again in a later generation: exact duplicate
        from search import SearchConfig, propose_round
        cfg = SearchConfig(seed=2, children_per_round=4, exploration_share=0.0, crossover_share=0.0,
                           operator_weights={"tweak_threshold": 1})
        out1, _ = propose_round(cfg=cfg, round_index=0, elite=[pid], specs={pid: parent}, feedback={},
                                available_data=["klines"], known_ids={pid}, symbols=["BTCUSDT"],
                                known_specs={pid: parent})
        known = {pid: parent, **{o["sid"]: o["spec"] for o in out1}}
        out2, c2 = propose_round(cfg=cfg, round_index=0, elite=[pid], specs={pid: parent}, feedback={},
                                 available_data=["klines"], known_ids=set(known), symbols=["BTCUSDT"],
                                 known_specs=known)
        self.assertEqual({o["sid"] for o in out2} & set(known), set())
        self.assertEqual(c2["duplicates"] + c2["semantic_duplicates"] + c2["invalid"] >= len(out1), True)

    def test_7_boundary_of_the_near_duplicate_rule(self):
        from evolution import _near_tolerance, near_duplicate
        tol = _near_tolerance("ret_z")
        self.assertEqual(tol, 0.125)
        self.assertIsNotNone(near_duplicate(zspec(thr=1.0), zspec(thr=1.0 + tol - 1e-6)))
        self.assertIsNone(near_duplicate(zspec(thr=1.0), zspec(thr=1.0 + tol)))   # exactly half a step: different
        self.assertIsNone(near_duplicate(zspec(window=96), zspec(window=97)))     # parameter: structure differs


# ---------------------------------------------------------------------------
# 5: cost model accuracy and sensitivity
# ---------------------------------------------------------------------------

class TestCostSensitivity(PipelineCase):
    def test_sensitivity_is_monotone_and_labelled(self):
        import cost_sensitivity as cs
        from costs_bridge import CostContext
        prov = SyntheticProvider()
        p = self.pipe(prov)
        spec = canonicalize_spec(EDGE)
        cost = CostContext.from_assumed_profile("assumed_public_v1")
        rep = cs.sensitivity(spec, cs.dev_inputs(p, spec), cost, 10_000.0)
        sc = rep["scenarios"]
        self.assertEqual(rep["monotone_issues"], [])
        self.assertLess(sc["fee_x2"]["net_bp"], sc["base"]["net_bp"])
        self.assertAlmostEqual(sc["fee_x2"]["cost_bp"] - sc["base"]["cost_bp"], 2 * 0.0005 * 1e4, places=6)
        self.assertGreater(sc["size_x25"]["cost_bp"] if sc["size_x25"]["n"] else 1e9, sc["base"]["cost_bp"])
        self.assertLessEqual(sc["maker_fee_optimistic_bound"]["cost_bp"], sc["base"]["cost_bp"])
        self.assertIn("not observed", rep["basis"]["claim"])
        self.assertEqual(rep["basis"]["cost_status"], "ASSUMED_COST_MODEL")
        self.assertFalse(rep["gate"])
        self.assertIn("net_bp_high_vol", sc["base"])

    def test_formula_rounding_and_refusals(self):
        from costs_bridge import (CostConfigError, CostContext, CostDataMissing, CostRefused)
        c = CostContext.from_params({"fee_rate": 0.0005, "half_spread": 0.0002, "impact_coefficient": 0.1,
                                     "max_participation": 0.05}, version="t")
        self.assertAlmostEqual(c.quote(10_000, 1e8), 0.0005 + 0.0002 + 0.1 * (1e-4) ** 0.5, places=15)
        self.assertAlmostEqual(c.round_trip_bp(10_000, 1e8, 4e8),
                               (0.0014 + 0.1 * (1e-4 ** 0.5 + 2.5e-5 ** 0.5)) * 1e4, places=9)
        with self.assertRaises(CostRefused):
            c.quote(10_000, 1e5)                         # 10% of the bar: refused, not priced
        for bad in (None, 0, float("nan"), "x"):
            with self.assertRaises(CostDataMissing):
                c.quote(10_000, bad)
        with self.assertRaises(CostConfigError):          # no silent default for a missing parameter
            CostContext.from_params({"fee_rate": 0.0005, "half_spread": 0.0002, "max_participation": 0.05},
                                    version="t")
        import cost_sensitivity as cs
        rows = {"base": {"n": 10, "net_bp": 5.0, "cost_bp": 10.0},
                "fee_x2": {"n": 10, "net_bp": 6.0, "cost_bp": 9.0}}
        self.assertTrue(cs.monotone_issues(rows))         # a pricing bug would be caught

    def test_every_dev_record_names_its_cost_model(self):
        p = self.pipe(SyntheticProvider())
        sid, _ = p.register(EDGE, source="test")
        rec = p.dev_evaluate(sid)
        self.assertIn("version", rec["cost_model"])
        self.assertIn("status", rec["cost_model"])
        comps = list(iter_jsonl(p.dir / "contexts.jsonl"))[-1]["components"]
        self.assertIn("params", comps["cost"])


# ---------------------------------------------------------------------------
# 6: ledger / report consistency
# ---------------------------------------------------------------------------

class TestReportConsistency(PipelineCase):
    def test_injected_defects_are_reported_and_corruption_fails_closed(self):
        from engine_lib import JsonlCorruption
        from evolution_report import build_report
        p = self.pipe(SyntheticProvider())
        self.register_all(p, [EDGE] + NOISE[:3])
        st = self.td / "state"
        rep = build_report(st, registry_path=self.td / "r.jsonl")
        self.assertTrue(rep["consistency"]["ok"])
        self.assertEqual(rep["dev"]["evaluated"], 4)
        dev = [r for r in iter_jsonl(p.eval_path) if r["stage"] == "dev"][0]
        append_jsonl(p.eval_path, dev)                                      # duplicate record
        p.preregister_batch([dev["strategy_id"]])                           # batch without RESULT
        rep = build_report(st, registry_path=self.td / "r.jsonl")
        self.assertFalse(rep["consistency"]["ok"])
        self.assertEqual(rep["consistency"]["duplicate_dev_records"], [dev["strategy_id"]])
        self.assertEqual(len(rep["consistency"]["incomplete_batches"]), 1)
        self.assertEqual(rep["validation"]["outcomes"], {"NOT_RUN": 1})     # not counted as done
        with (p.dir / "statuses.jsonl").open("a") as f:
            f.write("{broken\n")
            f.write(json.dumps({"x": 1}) + "\n")
        with self.assertRaises(JsonlCorruption):
            build_report(st, registry_path=self.td / "r.jsonl")


# ---------------------------------------------------------------------------
# 8 / 9: criteria pinned, repository independence, CI coverage
# ---------------------------------------------------------------------------

#: sha256 of every file that holds a statistical or cost criterion.  A change
#: is allowed only with a new versioned file and an ADR; this test makes a
#: silent edit (e.g. a lowered alpha or a cheaper cost) fail CI.
PINNED = {
    "config/promotion_v3.json": "01fbef5c4946",
    "config/independent_window_v1.json": "49721cc7daad",
    "config/cost_assumed_public_v1.json": "b6478c34e5f1",
    "config/cost_verification_v1.json": "d0bdb7586c4a",
}


class TestCriteriaPinned(unittest.TestCase):
    def test_criteria_files_unchanged(self):
        for rel, prefix in PINNED.items():
            sha = hashlib.sha256((DISC / rel).read_bytes()).hexdigest()
            self.assertTrue(sha.startswith(prefix) and prefix, f"{rel}: sha256 {sha} (pin {prefix!r})")

    def test_promotion_numbers(self):
        cfg = json.loads((DISC / "config" / "promotion_v3.json").read_text())
        self.assertEqual(cfg["validation"]["alpha_total_per_search"], 0.05)
        self.assertEqual(cfg["dev_screen"]["min_t"], 1.0)
        self.assertEqual(cfg["dev_screen"]["min_events_per_symbol"], 30)


class TestIndependence(unittest.TestCase):
    ROOT = DISC.parent.parent

    def test_ci_checks_out_only_this_repository(self):
        wf = (self.ROOT / ".github" / "workflows" / "research-ci.yml").read_text()
        self.assertEqual(wf.count("actions/checkout@"), 1)
        self.assertNotIn("repository:", wf)
        self.assertNotIn("secrets.", wf)
        used = "\n".join(x for x in wf.splitlines() if "grep -nE" not in x)   # the independence check itself
        for bad in ("NEW2_READ_TOKEN", "COINTRADER_SRC", "tlsehd195/NEW2"):
            self.assertNotIn(bad, used)
        self.assertIn("run/test_", wf)                      # root-level research tests run too

    def test_runtime_has_no_path_into_another_repository(self):
        bad = []
        for p in DISC.glob("*.py"):
            t = p.read_text(encoding="utf-8")
            for needle in ("import cointrader", "from cointrader", "COINTRADER_SRC", "/home/z/my-project"):
                if needle in t and p.name != "cost_model.py":
                    bad.append(f"{p.name}: {needle}")
        self.assertEqual(bad, [])
        import costs_bridge
        self.assertTrue(Path(costs_bridge.COST_MODEL_SOURCE_PATH).resolve().is_relative_to(DISC.resolve()))

    def test_legacy_scripts_are_listed(self):
        manifest = (DISC.parent / "LEGACY_SCRIPTS.md").read_text(encoding="utf-8")
        for p in sorted(DISC.parent.glob("*.py")) + sorted(DISC.parent.glob("*.sh")):
            t = p.read_text(encoding="utf-8", errors="replace")
            if any(n in t for n in ("cointrader", "COINTRADER_SRC", "/home/z/my-project")):
                self.assertIn(f"`{p.name}`", manifest, f"{p.name} uses a retired path but is not listed")


if __name__ == "__main__":
    unittest.main()
