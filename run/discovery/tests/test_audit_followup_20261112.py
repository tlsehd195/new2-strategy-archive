"""ADR 0079: follow-up audit of ADR 0078 (user directive 2026-11-12 14:00Z).

D-01 bootstrap resolution, G-03/G-04 Paper Forward, F-11 writers outside the
lock, runner.py / engine_lib.py author review.  SYNTHETIC data, mocks and temp
directories only: no real window, no network, no real record is read for a
decision or written.
"""
from __future__ import annotations

import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for _p in (str(DISC), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stats_mc  # noqa: E402
import test_pipeline as tp  # noqa: E402
from engine_lib import append_jsonl, iter_jsonl  # noqa: E402
from stats_mc import InsufficientBootstrapResolution  # noqa: E402


def _series(m=2, n=60, seed=3, edge=0.4):
    rng = random.Random(seed)
    return {f"k{j}": [rng.gauss(edge if j == 0 else 0.0, 1.0) for _ in range(n)] for j in range(m)}


def _call(fn, alpha, B):
    return fn(_series(), alpha=alpha, B=B, seed=1)


# ---------------------------------------------------------------------------
# D-01 — bootstrap resolution alpha * (B + 1) >= MIN_TAIL_DRAWS
# ---------------------------------------------------------------------------

#: (alpha, B, alpha*(B+1)) — every product is exact in binary floating point
#: or lies on the stated side of the boundary
BLOCKED = [
    (0.01, 50, 0.51),        # < 1
    (0.01, 99, 1.0),         # == 1
    (0.05, 19, 1.0),         # == 1
    (0.01, 299, 3.0),        # 1 < x < 5
    (0.05, 49, 2.5),         # 1 < x < 5
    (0.05, 98, 4.95),        # 1 < x < 5, just below
    (0.10, 48, 4.9),         # 1 < x < 5, just below
]
ALLOWED = [
    (0.01, 499, 5.0),        # == 5
    (0.05, 99, 5.0),         # == 5
    (0.10, 49, 5.0),         # == 5
    (0.05, 199, 10.0),       # > 5
    (0.10, 150, 15.1),       # > 5
]
FUNCS = ("stepm", "step_spa", "mcs")


class TestD01Boundary(unittest.TestCase):
    """Each test function is called directly at every boundary class."""

    def test_products_are_on_the_stated_side(self):
        for a, B, x in BLOCKED:
            self.assertLess(a * (B + 1), stats_mc.MIN_TAIL_DRAWS, (a, B))
            self.assertAlmostEqual(a * (B + 1), x)
        for a, B, x in ALLOWED:
            self.assertGreaterEqual(a * (B + 1), stats_mc.MIN_TAIL_DRAWS, (a, B))
            self.assertAlmostEqual(a * (B + 1), x)

    def test_blocked_below_min_tail_draws(self):
        for name in FUNCS:
            fn = getattr(stats_mc, name)
            for a, B, x in BLOCKED:
                with self.subTest(fn=name, alpha=a, B=B, product=x):
                    with self.assertRaises(InsufficientBootstrapResolution):
                        _call(fn, a, B)

    def test_one_to_five_band_is_blocked(self):
        """The band the ADR 0078 code let through (it checked < 1 only)."""
        for name in FUNCS:
            fn = getattr(stats_mc, name)
            for a, B in ((0.01, 100), (0.01, 250), (0.01, 498), (0.05, 20), (0.05, 60), (0.025, 150)):
                with self.subTest(fn=name, alpha=a, B=B):
                    self.assertTrue(1.0 <= a * (B + 1) < 5.0)
                    with self.assertRaises(InsufficientBootstrapResolution):
                        _call(fn, a, B)

    def test_allowed_at_and_above_min_tail_draws(self):
        for name in FUNCS:
            fn = getattr(stats_mc, name)
            for a, B, x in ALLOWED:
                with self.subTest(fn=name, alpha=a, B=B, product=x):
                    out = _call(fn, a, B)
                    self.assertEqual(out["B"], B)
                    self.assertEqual(out["alpha"], a)

    def test_stepm_studentize_modes_share_the_check(self):
        for mode in ("fixed", "bootstrap_t", "gk_block"):
            with self.subTest(mode=mode):
                with self.assertRaises(InsufficientBootstrapResolution):
                    stats_mc.stepm(_series(), alpha=0.05, B=49, seed=1, studentize=mode)
                stats_mc.stepm(_series(), alpha=0.05, B=99, seed=1, studentize=mode)

    def test_mcs_single_model_is_checked_too(self):
        with self.assertRaises(InsufficientBootstrapResolution):
            stats_mc.mcs({"a": [0.1, -0.2, 0.3] * 10}, alpha=0.10, B=10)

    def test_threshold_has_a_single_source(self):
        """_require_resolution reads MIN_TAIL_DRAWS (not a literal)."""
        with mock.patch.object(stats_mc, "MIN_TAIL_DRAWS", 7):
            with self.assertRaises(InsufficientBootstrapResolution):
                _call(stats_mc.stepm, 0.05, 119)            # 6.0 < 7
            self.assertGreaterEqual(0.05 * (stats_mc.bootstrap_B_for(0.05, 10) + 1), 7)
            _call(stats_mc.stepm, 0.05, stats_mc.bootstrap_B_for(0.05, 10))
        src = Path(stats_mc.__file__).read_text()
        body = src[src.index("def _require_resolution"):src.index("def stepm(")]
        self.assertIn("MIN_TAIL_DRAWS", body)
        self.assertNotIn("< 1.0", body)

    def test_message_names_the_rule(self):
        with self.assertRaises(InsufficientBootstrapResolution) as cm:
            _call(stats_mc.stepm, 0.01, 299)
        msg = str(cm.exception)
        self.assertIn("alpha*(B+1)", msg)
        self.assertIn("499", msg)                           # the B that would resolve it

    def test_invalid_alpha_refused(self):
        for bad in (0.0, 1.0, -0.1, 1.5, float("nan"), float("inf"), -float("inf"), True, False,
                    "0.05", None, [0.05]):
            for name in FUNCS:
                with self.subTest(fn=name, alpha=bad):
                    with self.assertRaises(ValueError):
                        _call(getattr(stats_mc, name), bad, 1000)

    def test_invalid_B_refused(self):
        for bad in (0, -1, -1000, True, False, 1000.0, 99.5, float("nan"), float("inf"), "1000", None):
            for name in FUNCS:
                with self.subTest(fn=name, B=bad):
                    with self.assertRaises(ValueError):
                        _call(getattr(stats_mc, name), 0.05, bad)

    def test_bootstrap_B_for_always_resolves(self):
        alphas = [stats_mc.spending_alpha(k, tot) for tot in (0.1, 0.05, 0.025, 0.0213)
                  for k in range(1, 60)] + [0.5, 0.1, 0.05, 1e-3, 1e-5]
        for a in alphas:
            for base in (1, 49, 1000, 10_000):
                B = stats_mc.bootstrap_B_for(a, base)
                with self.subTest(alpha=a, base=base):
                    self.assertIsInstance(B, int)
                    self.assertGreaterEqual(B, base)
                    self.assertGreaterEqual(a * (B + 1), stats_mc.MIN_TAIL_DRAWS)
                    stats_mc._require_resolution(a, B)        # must not raise

    def test_bootstrap_B_for_refuses_bad_input(self):
        for a, base in ((0.0, 1000), (1.0, 1000), (float("nan"), 1000), (True, 1000),
                        (0.05, 0), (0.05, True), (0.05, 99.5), (0.05, -5)):
            with self.subTest(alpha=a, base=base):
                with self.assertRaises(ValueError):
                    stats_mc.bootstrap_B_for(a, base)

    def test_no_test_without_alpha_is_affected(self):
        """reality_check / spa_test return p-values (no alpha): unchanged."""
        s = _series()
        self.assertIn("p", stats_mc.reality_check(s, B=49, seed=1))
        self.assertIn("p_consistent", stats_mc.spa_test(s, B=49, seed=1))


class TestD01Pipeline(tp.PipelineCase):
    """A batch PREREGISTERED by an older version with too few draws for its
    alpha: nobody is rejected, nobody passes, the block is recorded, the
    stored B / alpha / batch index and the alpha history are left as they are."""

    def _legacy_batch(self, hist=None):
        from pipeline import EvolutionPipeline
        p = EvolutionPipeline(self.td / "state", tp.SyntheticProvider(), tp.verified_fee_cost(),
                              symbols=["BTCUSDT"], validation_history=hist)
        sids = self.register_all(p, [tp.EDGE] + tp.NOISE)

        def legacy_B(alpha, base):               # 1 <= alpha*(B+1) < 5, as old code allowed
            return max(1, int(2.5 / alpha))
        cands = p.validation_candidates()
        with mock.patch("pipeline.bootstrap_B_for", legacy_B):
            batch = p.preregister_batch(cands)
        return p, sids, batch

    def test_legacy_small_B_batch_is_blocked_not_rejected(self):
        hist = self.td / "history.jsonl"
        p, sids, batch = self._legacy_batch(hist)
        pre_rows = [dict(r) for r in iter_jsonl(p.batch_path)]
        hist_rows = list(iter_jsonl(hist))
        for w in batch["windows"].values():
            self.assertTrue(1.0 <= w["alpha"] * (w["B"] + 1) < stats_mc.MIN_TAIL_DRAWS)
        res = p.run_validation()                  # resumes the open (legacy) batch
        self.assertEqual(res["batch_id"], batch["batch_id"])
        ctx = p.context_id()
        for m in batch["members"]:
            with self.subTest(member=m):
                self.assertEqual(res["outcome"][m], "BLOCKED_STATISTICS")
                st = p.statuses.get(m, ctx)
                self.assertEqual(st["status"], "BLOCKED_STATISTICS")
                self.assertEqual(st["stage"], "validation")
                rec = p.evaluation("validation", m)
                self.assertFalse(rec["stepm_rejected"])
                self.assertTrue(rec["stepm_blocked"])
                self.assertEqual(rec["failure_class"], "BLOCKED_STATISTICS")
                self.assertIsNone(p.evaluation("robustness", m))
        for key, w in batch["windows"].items():
            t = res["tests"][key]
            self.assertIn("stepm_blocked", t)
            self.assertEqual(t["rejected"], [])
            self.assertEqual(t["B"], w["B"])                       # stored B, not raised
            self.assertEqual(t["alpha"], w["alpha"])
            self.assertEqual(t["stepm_method"], "BLOCKED_STATISTICS")
            self.assertIn("step_spa_blocked", t)                   # not hidden as spa_error
            self.assertNotIn("step_spa_rejected", t)
        # the ledger before the RESULT row is byte-for-byte what it was
        rows = list(iter_jsonl(p.batch_path))
        self.assertEqual(rows[:len(pre_rows)], pre_rows)
        self.assertEqual(rows[-1]["kind"], "RESULT")
        # alpha spending kept: the history row stays, the batch still counts
        self.assertEqual(list(iter_jsonl(hist)), hist_rows)
        # no member is offered to a new batch (no second look at the window)
        self.assertEqual([c for c in p.validation_candidates() if c in batch["members"]], [])
        from pipeline import outcome_label
        for m in batch["members"]:
            self.assertEqual(outcome_label(p.statuses.get(m, ctx),
                                           validation=p.evaluation("validation", m),
                                           dev=p.evaluation("dev", m)), "UNDETERMINED_POWER")

    def test_resumed_blocked_batch_is_idempotent(self):
        p, sids, batch = self._legacy_batch()
        r1 = p.run_validation()
        n_eval = len(list(iter_jsonl(p.dir / "evaluations.jsonl")))
        self.assertIsNone(p.run_validation())
        self.assertEqual(len(list(iter_jsonl(p.dir / "evaluations.jsonl"))), n_eval)
        self.assertEqual(r1["outcome"][sids[0]], "BLOCKED_STATISTICS")

    def test_current_batch_is_not_blocked(self):
        """Control: a batch preregistered by the current code resolves its alpha."""
        p = self.pipe(tp.SyntheticProvider())
        self.register_all(p, [tp.EDGE] + tp.NOISE)
        res = p.run_validation()
        for t in res["tests"].values():
            self.assertNotIn("stepm_blocked", t)
        self.assertIn("PASSED_STEPM", res["outcome"].values())


class TestD01IndependentWindow(unittest.TestCase):
    """The independent window refuses to open (nothing registered, no history
    row) when its frozen B cannot resolve the tranche alpha."""

    def setUp(self):
        import test_independent_window as tiw
        self.tiw = tiw
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.data = self.td / "data"
        months = ["2020-01", "2020-02", "2020-03"]
        tiw.write_symbol(self.data, "BTCUSDT", months, seed=1)
        tiw.write_symbol(self.data, "BBBUSDT", months, seed=40)
        self.hist = self.td / "history.jsonl"
        for r in iter_jsonl(tiw.REAL_HISTORY):        # read-only copy, as test_independent_window
            append_jsonl(self.hist, r)
        self.hist_before = list(iter_jsonl(self.hist))
        self.pre = tiw.small_prereg()
        self.sha = "f" * 64
        self.members = tiw.members_of(self.pre, self.sha,
                                      [tp.hour_spec(0, 2)] + [tp.hour_spec(h, h + 2) for h in (6, 12)])

    def _open(self, out="out"):
        import independent_window as iw
        return iw.open_tranche(self.pre, self.sha, "A", self.data, self.members, self.hist,
                               self.td / out, tp.verified_fee_cost(),
                               gate={"lifecycle": "ACTIVE", "prereg_sha256": self.sha},
                               registry_path=self.td / "registry.jsonl")

    def test_unresolvable_B_refuses_before_opening(self):
        import independent_window as iw
        self.pre["test"]["bootstrap_B"] = 100      # alpha_1 = 0.0304: 0.0304*101 = 3.07 < 5
        with self.assertRaises(iw.WindowRefused) as cm:
            self._open()
        self.assertIn("alpha*(B+1)", str(cm.exception))
        self.assertEqual(list(iter_jsonl(self.hist)), self.hist_before)
        self.assertFalse((self.td / "registry.jsonl").exists()
                         and list(iter_jsonl(self.td / "registry.jsonl")))
        self.assertFalse((self.td / "out").exists())

    def test_blocked_stepm_inside_a_run_is_recorded_not_rejected(self):
        """Defence in depth: should StepM refuse inside an opened run, every member
        is BLOCKED_STATISTICS (never REJECTED, never PASSED)."""
        import independent_window as iw

        def refuse(*a, **k):
            raise InsufficientBootstrapResolution("synthetic refusal")
        with mock.patch.object(iw, "stepm", refuse):
            r = self._open()
        self.assertEqual(set(r["outcome"].values()), {"BLOCKED_STATISTICS"})
        self.assertEqual(r["test"]["rejected"], [])
        self.assertIn("stepm_blocked", r["test"])


class TestD01PowerAnalysis(unittest.TestCase):
    def test_power_analysis_uses_a_resolving_B(self):
        src = (DISC / "power_analysis.py").read_text()
        self.assertIn("bootstrap_B_for(a_w", src)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# G-04 — Fear & Greed data of a forward plan
# ---------------------------------------------------------------------------

def _fng_edge_spec():
    """EDGE (hour 0-2) gated by an FNG filter that is true whenever a value
    exists (fng_max over 1 day <= 100): it trades like EDGE where FNG is known
    and not at all where it is not."""
    sp = tp.hour_spec(0, 2)
    sp["filters"] = [{"feature": "fng_max", "params": {"days": 1}, "op": "le", "value": 100.0}]
    return sp


def _fng_map(lo_ms, hi_ms, value=10.0):
    return {d: value for d in range(lo_ms // 86_400_000 - 30, hi_ms // 86_400_000 + 1)}


from test_paper_forward_funding_20261111 import DAY, FundingCase  # noqa: E402


class TestG04ForwardFng(FundingCase):
    def _fng_ready(self):
        """A PAPER_TEST_READY strategy that needs FNG; the provider's FNG series
        ends at the registration time (what the archive holds then)."""
        p, edge, feed = self.setup_feed(iv_h=4)
        self.fng = _fng_map(feed.bars[0].ts, self.now[0])
        p.provider.fng = lambda: self.fng
        sid, _ = p.register(_fng_edge_spec(), source="test")
        ctx = p.context_id()
        for s in ("RESEARCH_ONLY", "VALIDATION_SURVIVOR", "PAPER_TEST_READY"):
            p.statuses.set(sid, ctx, s, stage="test", reasons=["synthetic G-04 fixture"])
        return p, sid, feed

    def test_fng_strategy_plan_is_refused(self):
        """HEAD 757114f registered the plan: FNG is read from the live provider at
        every evaluation, nothing about it is in the plan (no hash, no snapshot)."""
        from paper_forward import ForwardPaper, ForwardRefused
        p, sid, feed = self._fng_ready()
        fp = ForwardPaper(p, feed, clock=lambda: self.now[0])
        with self.assertRaises(ForwardRefused) as cm:
            fp.register_plan(sid)
        self.assertIn("FNG", str(cm.exception))
        self.assertEqual(fp.plans(), [])
        self.assertEqual(p.statuses.status(sid, p.context_id()), "PAPER_TEST_READY")

    def test_fng_changed_mid_plan_never_passes(self):
        """A plan for an FNG strategy registered by an older version (the refusal
        bypassed): the FNG source is extended / revised after registration.  The
        plan must never conclude on FNG values it did not pin (HEAD: PAPER_PASSED
        on the new FNG values, unnoticed — the synthetic provider's identity, like
        any provider that omits FNG from it, does not change the context)."""
        import paper_forward as pf
        p, sid, feed = self._fng_ready()
        fp = pf.ForwardPaper(p, feed, clock=lambda: self.now[0])
        with mock.patch.object(pf.ForwardPaper, "_fng_refusal", return_value=None, create=True):
            plan = fp.register_plan(sid)
        self.now[0] += 20 * DAY
        fp.step(plan["plan_id"])
        self.fng = _fng_map(feed.bars[0].ts, feed.bars[-1].ts)       # source revised / extended
        self.now[0] += 55 * DAY
        r = fp.step(plan["plan_id"])
        self.assertNotEqual(r["verdict"], "PAPER_PASSED", r.get("reasons"))
        self.assertEqual(r["verdict"], "PAPER_BLOCKED_DATA", r.get("reasons"))
        self.assertTrue(any("FNG" in x for x in r.get("reasons", [])), r.get("reasons"))
        self.assertNotEqual(p.statuses.status(sid, p.context_id()), "PAPER_PASSED")
        self.assertIsNone(p.evaluation("paper_forward", sid))

    def test_non_fng_plan_unaffected(self):
        """Control: the FNG rule does not touch a strategy without FNG."""
        from paper_forward import ForwardPaper
        p, edge, feed = self.setup_feed(iv_h=4)
        fp = ForwardPaper(p, feed, clock=lambda: self.now[0])
        plan = fp.register_plan(edge)
        self.now[0] += 75 * DAY
        self.assertEqual(fp.step(plan["plan_id"])["verdict"], "PAPER_PASSED")

    def test_old_fng_conclusion_is_not_confirmed(self):
        """A conclusion an older version recorded for an FNG plan is left as it is
        (no record rewritten) but verify_conclusion no longer confirms it."""
        import paper_forward as pf
        p, sid, feed = self._fng_ready()
        self.fng = _fng_map(feed.bars[0].ts, feed.bars[-1].ts)
        fp = pf.ForwardPaper(p, feed, clock=lambda: self.now[0])
        with mock.patch.object(pf.ForwardPaper, "_fng_refusal", return_value=None), \
                mock.patch.object(pf.ForwardPaper, "_plan_fng_problem", return_value=None):
            plan = fp.register_plan(sid)
            self.now[0] += 75 * DAY
            r = fp.step(plan["plan_id"])
            self.assertEqual(r["verdict"], "PAPER_PASSED")              # what old code did
            self.assertEqual(fp.verify_conclusion(plan["plan_id"]), [])
        evals = (p.dir / "evaluations.jsonl").read_bytes()
        probs = fp.verify_conclusion(plan["plan_id"])
        self.assertTrue(any("FNG_NOT_PINNED" in x for x in probs), probs)
        self.assertEqual((p.dir / "evaluations.jsonl").read_bytes(), evals)   # nothing rewritten


# ---------------------------------------------------------------------------
# F-11 — read-decide-write of shared records under one lock
# ---------------------------------------------------------------------------

import threading  # noqa: E402


class TestF11Locks(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)

    def test_status_transition_decided_on_the_file_not_a_stale_cache(self):
        """HEAD: a second store instance (another process) with a stale cache
        wrote RESEARCH_ONLY -> VALIDATION_SURVIVOR over a REJECTED (terminal)."""
        from pipeline import InvalidStatusTransition, StatusStore
        path = self.td / "statuses.jsonl"
        a, b = StatusStore(path), StatusStore(path)
        a.set("S1", "C", "RESEARCH_ONLY", stage="dev")
        self.assertEqual(b.status("S1", "C"), "RESEARCH_ONLY")          # b caches the file now
        a.set("S1", "C", "REJECTED", stage="validation")
        with self.assertRaises(InvalidStatusTransition):
            b.set("S1", "C", "VALIDATION_SURVIVOR", stage="validation")
        rows = list(iter_jsonl(path))
        self.assertEqual([r["status"] for r in rows], ["RESEARCH_ONLY", "REJECTED"])
        self.assertEqual(b.status("S1", "C"), "REJECTED")
        self.assertTrue(path.with_name(path.name + ".lock").exists())

    def test_campaign_index_is_taken_once(self):
        """HEAD: two writers read the window count before either appended and both
        preregistered campaign 1 (alpha spent twice) on the same locked window."""
        import campaigns as cmod
        from engine_lib import LOCKED_TEST_PERIODS
        sym = sorted(LOCKED_TEST_PERIODS)[0]
        path = self.td / "locked_window_campaigns.jsonl"
        read_done, release = threading.Event(), threading.Event()
        orig = cmod.CampaignLedger.window_status

        def slow_status(self_, symbol, *, max_campaigns):
            st = orig(self_, symbol, max_campaigns=max_campaigns)
            if threading.current_thread().name == "A":
                read_done.set()
                release.wait(1.0)          # B runs here (or waits on the lock)
            return st
        out = {}

        def run(name, ref):
            out[name] = cmod.CampaignLedger(path).preregister(
                source="test", source_ref=ref, context_id="C", members={"S" + name: [sym]},
                alpha_total=0.05, max_campaigns=5, method="m", config_hash="h")
        with mock.patch.object(cmod.CampaignLedger, "window_status", slow_status):
            ta = threading.Thread(target=run, args=("A", "ref-A"), name="A")
            ta.start()
            self.assertTrue(read_done.wait(5))
            tb = threading.Thread(target=run, args=("B", "ref-B"), name="B")
            tb.start()
            tb.join(0.5)
            release.set()
            ta.join(10)
            tb.join(10)
        idx = sorted(w["campaign_index"] for r in out.values() for w in r["windows"].values())
        self.assertEqual(idx, [1, 2])
        rows = [r for r in iter_jsonl(path) if r["kind"] == "PREREGISTERED"]
        self.assertEqual(sorted(w["campaign_index"] for r in rows for w in r["windows"].values()), [1, 2])

    def test_lock_file_helper_serialises_threads(self):
        """exclusive_file_lock (flock on a fresh descriptor) excludes a second
        holder in the same process — what the tests above rely on."""
        from engine_lib import exclusive_file_lock
        p = self.td / "x.jsonl"
        got = []
        with exclusive_file_lock(p):
            t = threading.Thread(target=lambda: (exclusive_file_lock(p).__enter__(), got.append(1)))
            t.daemon = True
            t.start()
            t.join(0.3)
            self.assertEqual(got, [])
        t.join(5)
        self.assertEqual(got, [1])


class TestF11ForwardPlan(FundingCase):
    def test_one_plan_per_strategy_under_concurrent_registration(self):
        """HEAD: two ForwardPaper instances both passed the "plan exists" check and
        appended two plans for one strategy (two observations of one hypothesis)."""
        import paper_forward as pf
        p, edge, feed = self.setup_feed(iv_h=4)
        read_done, release = threading.Event(), threading.Event()
        orig = pf.ForwardPaper._fetch_funding

        def slow_fetch(self_, sym, after_ts):
            r = orig(self_, sym, after_ts)
            if threading.current_thread().name == "A":
                read_done.set()
                release.wait(2.0)
            return r
        errs, plans = [], []

        def run():
            try:
                plans.append(pf.ForwardPaper(p, feed, clock=lambda: self.now[0]).register_plan(edge))
            except pf.ForwardRefused as e:
                errs.append(str(e))
        with mock.patch.object(pf.ForwardPaper, "_fetch_funding", slow_fetch):
            ta = threading.Thread(target=run, name="A")
            ta.start()
            self.assertTrue(read_done.wait(30))
            tb = threading.Thread(target=run, name="B")
            tb.start()
            tb.join(1.0)
            release.set()
            ta.join(60)
            tb.join(60)
        fp = pf.ForwardPaper(p, feed, clock=lambda: self.now[0])
        self.assertEqual(len([x for x in fp.plans() if x["strategy_id"] == edge]), 1)
        self.assertEqual((len(plans), len(errs)), (1, 1), errs)
        self.assertIn("already exists", errs[0])
