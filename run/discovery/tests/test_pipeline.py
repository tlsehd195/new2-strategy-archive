"""Auto-evolution §4–§8: staged pipeline, campaigns, promotion, paper replay,
search budgets / resume and the cycle — SYNTHETIC data only, temp dirs.

The synthetic world is laid on the real calendar of the BTCUSDT locked
window (dev+validation up to 2024-03-24, locked 2024-03-25..06-18, paper
after it) because windows are derived from the locked dates.  Nothing here
is market data and no result here says anything about real markets.
"""
from __future__ import annotations

import datetime as dt
import json
import math
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

from data_quality import Bar  # noqa: E402
from engine_lib import iter_jsonl  # noqa: E402
from strategy_spec import SPEC_SCHEMA, canonicalize_spec  # noqa: E402
from test_residual_fixes import fee_cost  # noqa: E402
from costs_bridge import CostContext  # noqa: E402


def verified_fee_cost(fee=0.0002):
    """Synthetic VERIFIED stand-in so the happy path can be exercised in tests.
    ``test_only`` keeps the production runner from ever accepting it."""
    return CostContext.from_params({"fee_rate": fee, "half_spread": 0.0, "impact_coefficient": 0.0,
                                    "max_participation": 1.0}, version="TEST_VERIFIED_FEE",
                                   verified=True, test_only=True)


MS15 = 900_000
DAY_MS = 86_400_000


def _ms(d: str) -> int:
    return int(dt.datetime.fromisoformat(d + "T00:00:00+00:00").timestamp() * 1000)


def synth_bars(start: str, end: str, *, seed: int, drift_hours=(), mu=0.0008, vol=0.002,
               price0=100.0):
    """Random walk; bars whose UTC hour is in ``drift_hours`` get drift ``mu``."""
    rng = random.Random(seed)
    out, p, ts, stop = [], price0, _ms(start), _ms(end) + DAY_MS
    while ts < stop:
        h = (ts // 3_600_000) % 24
        c = p * math.exp((mu if h in drift_hours else 0.0) + rng.gauss(0, vol))
        hi, lo = max(p, c) * (1 + abs(rng.gauss(0, vol / 4))), min(p, c) * (1 - abs(rng.gauss(0, vol / 4)))
        v = 1e6 * (1 + rng.random())
        out.append(Bar(ts=ts, open=p, high=hi, low=lo, close=c, volume=v, quote_volume=v * c,
                       taker_buy_volume=v * rng.uniform(0.3, 0.7)))
        p, ts = c, ts + MS15
    return out


def synth_funding(bars):
    t = bars[0].ts // (8 * 3_600_000) * 8 * 3_600_000
    out = []
    while t <= bars[-1].ts + 8 * 3_600_000:
        out.append((t, 1e-5))
        t += 8 * 3_600_000
    return out


class SyntheticProvider:
    """Same interface as pipeline.RunnerDataProvider, synthetic content."""

    def __init__(self, *, drift_hours=(0, 1, 2), seed=1, locked_present=True, paper_present=True,
                 symbols=("BTCUSDT",), dev_start="2023-07-01", missing=False,
                 val_drift_hours=None):
        self.locked_present, self.paper_present, self.missing = locked_present, paper_present, missing
        self._sym = {}
        self.locked_loads = 0
        for k, sym in enumerate(symbols):
            from engine_lib import locked_window_for
            lo, hi = locked_window_for(sym)
            read_end = (dt.date.fromisoformat(lo) - dt.timedelta(days=1)).isoformat()
            pre = synth_bars(dev_start, read_end, seed=seed + k, drift_hours=drift_hours)
            if val_drift_hours is not None:      # different validation world, same dev world
                vstart = _ms(lo) - 180 * DAY_MS
                tail = synth_bars(dt.datetime.fromtimestamp(vstart / 1000, dt.timezone.utc).date().isoformat(),
                                  read_end, seed=seed + 50 + k, drift_hours=val_drift_hours,
                                  price0=[b for b in pre if b.ts < vstart][-1].close)
                pre = [b for b in pre if b.ts < vstart] + tail
            lock = synth_bars(lo, hi, seed=seed + 100 + k, drift_hours=drift_hours,
                              price0=pre[-1].close)
            pap0 = (dt.date.fromisoformat(hi) + dt.timedelta(days=2)).isoformat()
            pap1 = (dt.date.fromisoformat(hi) + dt.timedelta(days=60)).isoformat()
            pap = synth_bars(pap0, pap1, seed=seed + 200 + k, drift_hours=drift_hours,
                             price0=lock[-1].close)
            self._sym[sym] = {"pre": pre, "lock": lock, "paper": pap}
        self._entries = {}

    def available_data(self):
        return ["klines", "funding"]

    def symbol(self, sym):
        if sym not in self._entries:
            if self.missing or sym not in self._sym:
                self._entries[sym] = {"bars": [], "funding": None, "missing": [f"{sym}: no data"],
                                      "identity": {}}
            else:
                b = self._sym[sym]["pre"]
                self._entries[sym] = {"bars": b, "funding": synth_funding(b), "missing": [],
                                      "identity": {"synthetic": True, "n": len(b), "last": b[-1].ts}}
        return self._entries[sym]

    def fng(self):
        return None

    def windows(self, sym, horizon):
        from runner import split_windows
        b = self.symbol(sym)["bars"]
        return split_windows(sym, b[0].ts, b[-1].ts, horizon, [x.ts for x in b])

    def locked_ready(self, sym):
        return self.locked_present

    def locked(self, sym):
        self.locked_loads += 1
        if not self.locked_present:
            return {"bars": [], "funding": None, "missing": ["synthetic locked absent"], "sha256": ""}
        b = self._sym[sym]["lock"]
        return {"bars": b, "funding": synth_funding(b), "missing": [], "sha256": "synthetic"}

    def paper(self, sym):
        if not self.paper_present:
            return {"bars": [], "funding": None, "missing": ["no post-lock data"]}
        b = self._sym[sym]["paper"]
        return {"bars": b, "funding": synth_funding(b), "missing": []}

    def identity(self, symbols):
        return {s: self.symbol(s)["identity"] for s in sorted(symbols)}


def hour_spec(lo, hi, *, horizon=8, direction="long"):
    return {"schema": SPEC_SCHEMA, "direction": direction, "symbols": ["BTCUSDT"],
            "entry": {"logic": "all", "conditions": [
                {"feature": "hour_utc", "params": {}, "op": "between", "value": [lo, hi]}]},
            "filters": [], "exit": {"horizon": horizon}}


EDGE = hour_spec(0, 2)
NOISE = [hour_spec(h, h + 2) for h in range(4, 24, 2)]          # 10 hours without drift


class PipelineCase(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)

    def pipe(self, provider, *, cfg_patch=None, state=None, cost=None):
        from pipeline import DEFAULT_PROMOTION_CONFIG, EvolutionPipeline
        cfgp = None
        if cfg_patch:
            cfg = json.loads(DEFAULT_PROMOTION_CONFIG.read_text())
            for sec, kv in cfg_patch.items():
                cfg[sec].update(kv)
            cfgp = self.td / "promo.json"
            cfgp.write_text(json.dumps(cfg))
        return EvolutionPipeline(state or self.td / "state", provider, cost or verified_fee_cost(),
                                 symbols=["BTCUSDT"], promotion_config=cfgp)

    def register_all(self, p, specs):
        out = []
        for sp in specs:
            sid, _ = p.register(sp, source="test")
            p.dev_evaluate(sid)
            out.append(sid)
        return out


class TestStagedPipeline(PipelineCase):
    def test_true_edge_reaches_paper_ready_with_linked_evidence(self):
        from paper import run_paper
        prov = SyntheticProvider()
        p = self.pipe(prov)
        ctx = p.context_id()
        edge, *noise = self.register_all(p, [EDGE] + NOISE)
        self.assertEqual(p.statuses.status(edge, ctx), "RESEARCH_ONLY")
        self.assertTrue(all(p.statuses.status(s, ctx) in ("REJECTED", "INCONCLUSIVE") for s in noise))
        self.assertEqual(prov.locked_loads, 0)                      # nothing read before a campaign
        vb = p.run_validation()
        self.assertEqual(vb["outcome"], {edge: "PASSED_STEPM"})
        self.assertEqual(p.statuses.status(edge, ctx), "VALIDATION_SURVIVOR")
        rob = p.evaluation("robustness", edge)
        self.assertEqual(rob["failed"], [])
        self.assertTrue(rob["checks"]["pbo"]["computable"])
        camp = p.run_locked_campaign()
        self.assertTrue(camp["results"][edge]["pass"])
        self.assertEqual(p.statuses.status(edge, ctx), "PAPER_TEST_READY")
        self.assertEqual(p.lock_registry.uses(edge, "BTCUSDT"), 1)
        review = list(iter_jsonl(p.review_path))[-1]
        self.assertEqual(set(review["evidence"]), {"dev", "validation", "robustness", "locked"})
        self.assertIn("TEST_ONLY_COST", review["labels"])
        self.assertNotIn("UNVERIFIED_COST_MODEL", review["labels"])
        # every evidence id points at a stored evaluation record
        ids = {r["record_id"] for r in iter_jsonl(p.eval_path)}
        self.assertTrue(set(review["evidence"].values()) <= ids)
        run = run_paper(p, edge)
        self.assertEqual((run["verdict"], run["orders_sent"], run["is_simulation"]),
                         ("PAPER_CONSISTENT", 0, True))

    def test_null_world_promotes_nothing(self):
        prov = SyntheticProvider(drift_hours=())
        p = self.pipe(prov)
        ctx = p.context_id()
        rng = random.Random(4)
        from evolution import random_spec
        specs = [hour_spec(h, h + 2) for h in range(0, 24, 2)]
        specs += [s for s in (random_spec(rng, ["klines"], ["BTCUSDT"]) for _ in range(6)) if s]
        self.register_all(p, specs)
        p.run_validation()
        p.run_locked_campaign()
        st = p.statuses.all(ctx)
        self.assertNotIn("PAPER_TEST_READY", st.values())
        self.assertNotIn("VALIDATION_SURVIVOR", st.values())

    def test_batch_is_preregistered_before_results_and_spends_alpha(self):
        from stats_mc import spending_alpha
        p = self.pipe(SyntheticProvider())
        self.register_all(p, [EDGE, hour_spec(0, 2, horizon=9)] + NOISE[:7])
        p.run_validation(max_batch=1)
        p.run_validation(max_batch=1)
        recs = list(iter_jsonl(p.batch_path))
        kinds = [r["kind"] for r in recs]
        self.assertEqual(kinds, ["PREREGISTERED", "RESULT", "PREREGISTERED", "RESULT"])
        w1 = next(iter(recs[0]["windows"].values()))
        w2 = next(iter(recs[2]["windows"].values()))
        self.assertEqual((w1["batch_index"], w2["batch_index"]), (1, 2))
        self.assertAlmostEqual(w2["alpha"], spending_alpha(2, 0.05))
        self.assertNotEqual(recs[0]["members"], recs[2]["members"])

    def test_interrupted_batch_is_completed_not_replaced(self):
        p = self.pipe(SyntheticProvider())
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        b = p.preregister_batch([edge])                 # crash before evaluation
        out = p.run_validation()
        self.assertEqual(out["batch_id"], b["batch_id"])
        self.assertEqual(sum(1 for r in iter_jsonl(p.batch_path) if r["kind"] == "PREREGISTERED"), 1)

    def test_validation_world_cannot_be_overfit_by_dev(self):
        """Same dev data, validation without the effect: the dev winner fails."""
        p = self.pipe(SyntheticProvider(val_drift_hours=()))
        ctx = p.context_id()
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        self.assertEqual(p.statuses.status(edge, ctx), "RESEARCH_ONLY")
        vb = p.run_validation()
        self.assertNotEqual(vb["outcome"][edge], "PASSED_STEPM")
        self.assertIn(p.statuses.status(edge, ctx), ("REJECTED", "INCONCLUSIVE"))


class TestLockedCampaigns(PipelineCase):
    def test_window_retires_after_campaign_budget(self):
        p = self.pipe(SyntheticProvider(), cfg_patch={"locked": {"max_campaigns_per_window": 1}})
        ctx = p.context_id()
        e1, e2, *_ = self.register_all(p, [EDGE, hour_spec(0, 2, horizon=9)] + NOISE[:7])
        p.run_validation(max_batch=1)
        first = [s for s in (e1, e2) if p.statuses.status(s, ctx) == "VALIDATION_SURVIVOR"]
        self.assertEqual(len(first), 1)
        p.run_locked_campaign()
        p.run_validation(max_batch=1)
        second = [s for s in (e1, e2) if s not in first][0]
        self.assertEqual(p.statuses.status(second, ctx), "VALIDATION_SURVIVOR")
        self.assertIsNone(p.run_locked_campaign())
        st = p.statuses.get(second, ctx)
        self.assertEqual(st["status"], "BLOCKED_STATISTICS")
        self.assertIn("WINDOW_RETIRED", st["reasons"][0])
        self.assertEqual(p.lock_registry.uses(second, "BTCUSDT"), 0)

    def test_bonferroni_inside_campaign(self):
        from campaigns import member_alpha
        from stats_mc import spending_alpha
        p = self.pipe(SyntheticProvider())
        e1, e2, *_ = self.register_all(p, [EDGE, hour_spec(0, 2, horizon=9)] + NOISE[:7])
        p.run_validation()
        camp = p.run_locked_campaign()
        pre = [r for r in p.campaigns.records() if r["kind"] == "PREREGISTERED"][0]
        self.assertAlmostEqual(member_alpha(pre, "BTCUSDT"), spending_alpha(1, 0.05) / 2)
        self.assertEqual(sorted(camp["results"]), sorted([e1, e2]))

    def test_missing_locked_data_never_consumes_and_resumes(self):
        prov = SyntheticProvider(locked_present=False)
        p = self.pipe(prov)
        ctx = p.context_id()
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        self.assertIsNone(p.run_locked_campaign())
        st = p.statuses.get(edge, ctx)
        self.assertEqual((st["status"], st["resume_to"]), ("BLOCKED_DATA", "VALIDATION_SURVIVOR"))
        self.assertEqual((p.lock_registry.uses(edge, "BTCUSDT"), prov.locked_loads), (0, 0))
        self.assertEqual(p.campaigns.records(), [])
        p.run_locked_campaign()                          # still missing: no duplicate status line
        self.assertEqual(sum(1 for r in iter_jsonl(p.statuses.path) if r["strategy_id"] == edge
                             and r["status"] == "BLOCKED_DATA"), 1)
        prov.locked_present = True                       # data arrives
        out = p.run_locked_campaign()
        self.assertTrue(out["results"][edge]["pass"])
        self.assertEqual(p.statuses.status(edge, ctx), "PAPER_TEST_READY")

    def test_quality_failure_after_reservation_aborts_without_exposure(self):
        prov = SyntheticProvider()
        p = self.pipe(prov)
        ctx = p.context_id()
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        prov.locked = lambda sym: {"bars": [], "funding": None, "missing": ["quality BLOCK"], "sha256": ""}
        out = p.run_locked_campaign()
        self.assertEqual(out["blocked"], "BLOCKED_DATA")
        self.assertEqual(p.lock_registry.uses(edge, "BTCUSDT"), 0)
        self.assertEqual(p.campaigns.counted_campaigns("BTCUSDT:LOCK:2024-03-25:2024-06-18"), 0)
        self.assertEqual(p.statuses.status(edge, ctx), "BLOCKED_DATA")


class TestPromotionAndStatus(PipelineCase):
    def test_promotion_requires_every_evidence(self):
        p = self.pipe(SyntheticProvider())
        ctx = p.context_id()
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        r = p.promotion_review(edge)                     # no validation / locked evidence yet
        self.assertEqual(r["decision"], "NOT_ELIGIBLE")
        self.assertIn("missing locked evidence", r["failures"])
        self.assertNotEqual(p.statuses.status(edge, ctx), "PAPER_TEST_READY")

    def test_unverified_cost_is_never_paper_ready(self):
        # a legacy config that still says allow_unverified_cost_for_paper=true is ignored
        p = self.pipe(SyntheticProvider(), cost=fee_cost(0.0002),
                      cfg_patch={"promotion": {"allow_unverified_cost_for_paper": True}})
        ctx = p.context_id()
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        self.assertIsNone(p.run_locked_campaign())
        # ADR 0009: an unverified cost model never spends the single-use locked window
        self.assertEqual(p.statuses.status(edge, ctx), "BLOCKED_COST")
        self.assertEqual(p.statuses.get(edge, ctx)["stage"], "locked")
        self.assertEqual(p.lock_registry.uses(edge, "BTCUSDT"), 0)
        self.assertEqual(p.campaigns.counted_campaigns("BTCUSDT:LOCK:2024-03-25:2024-06-18"), 0)
        rv = p.promotion_review(edge)
        self.assertFalse(rv["cost_verified"])
        self.assertIn("UNVERIFIED_COST_MODEL", rv["labels"])
        self.assertNotEqual(rv["decision"], "PAPER_TEST_READY")

    def test_tampered_evidence_record_blocks_promotion(self):
        from pipeline import EvidenceTampered
        p = self.pipe(SyntheticProvider())
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        lines = p.eval_path.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["status"] = "OK" if rec.get("status") != "OK" else "OK "
        lines[0] = json.dumps(rec, sort_keys=True)
        p.eval_path.write_text("\n".join(lines) + "\n")
        with self.assertRaises(EvidenceTampered):
            self.pipe(SyntheticProvider(), state=p.state_dir).promotion_review(edge)

    def test_transition_table(self):
        from pipeline import InvalidStatusTransition, StatusStore
        s = StatusStore(self.td / "st.jsonl")
        s.set("S", "C", "REJECTED", stage="dev_screen")
        with self.assertRaises(InvalidStatusTransition):
            s.set("S", "C", "RESEARCH_ONLY", stage="x")
        s.set("T", "C", "RESEARCH_ONLY", stage="dev_screen")
        with self.assertRaises(InvalidStatusTransition):
            s.set("T", "C", "PAPER_TEST_READY", stage="x")       # skips validation
        s.set("T", "C", "BLOCKED_DATA", stage="locked", resume_to="VALIDATION_SURVIVOR")
        with self.assertRaises(InvalidStatusTransition):
            s.set("T", "C", "RESEARCH_ONLY", stage="x")          # must resume where blocked
        s.set("T", "C", "VALIDATION_SURVIVOR", stage="locked")
        self.assertEqual(StatusStore(self.td / "st.jsonl").status("T", "C"), "VALIDATION_SURVIVOR")


class TestPaper(PipelineCase):
    def test_paper_refusals(self):
        from paper import PaperRefused, run_paper
        p = self.pipe(SyntheticProvider())
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        with self.assertRaises(PaperRefused):
            run_paper(p, edge)                            # not PAPER_TEST_READY
        p.run_validation()
        p.run_locked_campaign()
        with self.assertRaises(PaperRefused):
            run_paper(p, edge, mode="live")
        r = run_paper(p, edge)
        ctx = p.context_id()
        self.assertEqual(p.statuses.status(edge, ctx),
                         {"PAPER_CONSISTENT": "PAPER_TESTING", "PAPER_DEGRADED": "PAPER_FAILED",
                          "PAPER_INCONCLUSIVE": "PAPER_TESTING"}[r["verdict"]])
        self.assertEqual(r["orders_sent"], 0)
        with self.assertRaises(PaperRefused):
            run_paper(p, edge)                            # paper window used once

    def test_paper_blocked_data_does_not_consume(self):
        from paper import run_paper
        prov = SyntheticProvider(paper_present=False)
        p = self.pipe(prov)
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        p.run_locked_campaign()
        self.assertEqual(run_paper(p, edge)["status"], "BLOCKED_DATA")
        prov.paper_present = True
        self.assertEqual(run_paper(p, edge)["status"], "OK")

    def test_no_network_or_order_code_in_research_modules(self):
        import re
        pat = re.compile(r"^\s*(import|from)\s+(socket|urllib|http|requests|aiohttp|websocket|ccxt|"
                         r"binance)\b", re.M)
        for name in ("paper.py", "pipeline.py", "autoloop.py", "search.py", "evolution.py",
                     "strategy_spec.py", "stats_mc.py", "campaigns.py", "paper_forward.py",
                     "execution_sim.py", "cost_calibration.py", "public_cost_estimate.py"):
            self.assertIsNone(pat.search((DISC / name).read_text()), name)


class TestSearch(PipelineCase):
    def test_feedback_gate_rejects_non_dev(self):
        from search import FeedbackLeak, dev_feedback
        for stage in ("validation", "robustness", "locked", "paper"):
            with self.assertRaises(FeedbackLeak):
                dev_feedback({"stage": stage, "metrics": {"dev_t": 99}})

    def test_validation_results_do_not_change_proposals(self):
        """Poisoning: inject a spectacular validation / locked record for a
        DEV loser — elite and proposals stay identical."""
        from search import SearchConfig, propose_round, select_elite
        p = self.pipe(SyntheticProvider())
        sids = self.register_all(p, [EDGE] + NOISE)
        cfg = SearchConfig(children_per_round=10)

        def proposals():
            fb = p.dev_feedback_all()
            el = select_elite(fb, cfg)
            props, _ = propose_round(cfg=cfg, round_index=1, elite=el,
                                     specs={s: p.specs.get(s) for s in el}, feedback=fb,
                                     available_data=["klines"], known_ids=set(p.specs.all()),
                                     symbols=["BTCUSDT"])
            return el, [x["sid"] for x in props]
        before = proposals()
        loser = sids[-1]
        for stage in ("validation", "robustness", "locked", "paper"):
            p._append_eval({"stage": stage, "strategy_id": loser, "context_id": p.context_id(),
                            "status": "OK", "metrics": {"dev_t": 50, "dev_mean_bp": 999,
                                                        "dev_fold_pos_share": 1.0}})
        self.assertEqual(proposals(), before)

    def test_niche_cap_and_pareto(self):
        from search import SearchConfig, pareto_fronts, select_elite
        fronts = pareto_fronts({"a": (2, 1), "b": (1, 2), "c": (1, 1), "d": (0, 0)})
        self.assertEqual(fronts, [["a", "b"], ["c"], ["d"]])
        fb = {f"s{i}": {"dev_t": 5 - i * 0.1, "dev_fold_pos_share": 1, "dev_mdd_bp": 0,
                        "dev_cost_share": 0.1, "complexity": 1, "dev_mean_bp": 1, "family": "F",
                        "screen_pass": True} for i in range(6)}
        fb["x"] = {**fb["s5"], "dev_t": 0.5, "family": "G"}
        el = select_elite(fb, SearchConfig(niche_cap=2, elite_size=5))
        self.assertEqual(el, ["s0", "s1", "x"])


class TestCycle(PipelineCase):
    def cycle(self, state, prov, **kw):
        from autoloop import run_evolution_cycle
        from search import SearchConfig
        cfg = kw.pop("cfg", SearchConfig(children_per_round=6, seed=3))
        return run_evolution_cycle(state, prov, verified_fee_cost(), symbols=["BTCUSDT"],
                                   search_cfg=cfg, seed_legacy=False, **kw)

    def test_blocked_data_cycle(self):
        from autoloop import CYCLE_EXIT
        s = self.cycle(self.td / "st", SyntheticProvider(missing=True), rounds=1)
        self.assertEqual((s.status, CYCLE_EXIT[s.status]), ("CYCLE_BLOCKED_DATA", 4))
        self.assertEqual(s.dev_evaluated, 0)
        self.assertFalse((self.td / "st" / "evolution" / "specs.jsonl").exists())

    def test_resume_gives_same_children_as_uninterrupted(self):
        prov = SyntheticProvider()
        a = self.cycle(self.td / "a", prov, rounds=2, max_dev_evals=100)
        self.cycle(self.td / "b", prov, rounds=1, max_dev_evals=100)
        b = self.cycle(self.td / "b", prov, rounds=1, max_dev_evals=100)
        from evolution import SpecStore
        ids_a = set(SpecStore(self.td / "a" / "evolution" / "specs.jsonl").all())
        ids_b = set(SpecStore(self.td / "b" / "evolution" / "specs.jsonl").all())
        self.assertEqual(ids_a, ids_b)
        self.assertEqual((a.rounds, b.rounds), (2, 1))
        # every evolved spec has a lineage record naming registered parents
        from evolution import LineageStore
        ls = LineageStore(self.td / "a" / "evolution" / "lineage.jsonl")
        for r in ls.records():
            self.assertTrue(set(r["parents"]) <= ids_a, r)

    def test_round_budget_exhaustion(self):
        from search import SearchConfig
        cfg = SearchConfig(children_per_round=4, seed=3, max_rounds=1)
        prov = SyntheticProvider()
        self.cycle(self.td / "c", prov, rounds=3, cfg=cfg)
        s = self.cycle(self.td / "c", prov, rounds=3, cfg=cfg)
        self.assertEqual((s.status, s.rounds), ("CYCLE_BUDGET_EXHAUSTED", 0))


class TestRunnerCli(unittest.TestCase):
    def test_modes(self):
        from runner import UsageError, parse_options
        self.assertEqual(parse_options(["--mode", "evolve", "--rounds=0"])["mode"], "evolve")
        self.assertEqual(parse_options([])["mode"], "legacy")
        for bad in (["--mode", "live"], ["--rounds", "-1"], ["--evals", "0"], ["--order", "1"]):
            with self.assertRaises(UsageError):
                parse_options(bad)


class TestSpecCausality(unittest.TestCase):
    def test_every_feature_is_prefix_invariant(self):
        """No look-ahead: a feature value at i is identical when every bar
        after i is replaced by garbage."""
        from strategy_spec import FEATURES, build_spec_aux
        from test_core_defects import rich_bars, rich_fng, rich_funding
        bars = rich_bars(1200)
        fund = rich_funding(bars[0].ts, bars[-1].ts)
        fng = rich_fng(bars[0].ts, bars[-1].ts)
        cut = 900
        poisoned = bars[:cut + 1] + [Bar(ts=b.ts, open=b.open * 3, high=b.high * 3, low=b.low * 3,
                                         close=b.close * 0.2, volume=b.volume * 9,
                                         quote_volume=b.quote_volume * 9,
                                         taker_buy_volume=b.volume * 9) for b in bars[cut + 1:]]
        for name, f in FEATURES.items():
            params = {pn: (p.resolve_default({}) if not callable(p.default) else 64)
                      for pn, p in f.params.items()}
            sp = canonicalize_spec({"schema": SPEC_SCHEMA, "direction": "long",
                                    "symbols": ["BTCUSDT"], "exit": {"horizon": 8},
                                    "entry": {"conditions": [{
                                        "feature": name, "params": params,
                                        "op": f.ops[0],
                                        "value": [0, 1] if f.ops[0] == "between" else 0.0}]}})
            a = build_spec_aux(bars, sp, fund, fng)["series"]
            b = build_spec_aux(poisoned, sp, fund, fng)["series"]
            for k in a:
                self.assertEqual(a[k][:cut + 1], b[k][:cut + 1], name)


if __name__ == "__main__":
    unittest.main()


class TestVariationReport(PipelineCase):
    def test_parent_child_dm_rows(self):
        p = self.pipe(SyntheticProvider())
        parent, _ = p.register(NOISE[0], source="test")
        p.dev_evaluate(parent)
        child, _ = p.register(EDGE, source="test", parents=[parent], operator="param_change")
        p.dev_evaluate(child)
        lone, _ = p.register(NOISE[1], source="test", parents=["S-missing"], operator="x")
        rows = {r["child"]: r for r in p.variation_report()}
        self.assertEqual(rows[child]["status"], "OK")
        self.assertIn("p_better", rows[child])
        self.assertEqual(rows[lone]["status"], "UNTESTED")           # parent never evaluated: nothing invented


class TestHandoff(PipelineCase):
    def test_handoff_is_sealed_and_never_live(self):
        from handoff import HandoffRefused, build_handoff, export_handoff
        p = self.pipe(SyntheticProvider())
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        with self.assertRaises(HandoffRefused):
            build_handoff(p, edge)                        # not promoted yet
        p.run_validation()
        p.run_locked_campaign()
        path = export_handoff(p, edge, self.td / "out")
        doc = json.loads(path.read_text())
        self.assertFalse(doc["live_registration"])
        self.assertTrue(doc["requires_human_approval"])
        self.assertEqual(set(doc["evidence_record_ids"]), {"dev", "validation", "robustness", "locked"})
        self.assertEqual(export_handoff(p, edge, self.td / "out"), path)   # idempotent, no overwrite
        self.assertEqual(len(list((self.td / "out").iterdir())), 1)

    def test_handoff_refuses_when_review_evidence_differs(self):
        from handoff import HandoffRefused, build_handoff
        p = self.pipe(SyntheticProvider())
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        p.run_validation()
        p.run_locked_campaign()
        lines = [json.loads(x) for x in p.review_path.read_text().splitlines()]
        lines[-1]["evidence"]["locked"] = "EV-forged"
        p.review_path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        with self.assertRaises(HandoffRefused):
            build_handoff(p, edge)
