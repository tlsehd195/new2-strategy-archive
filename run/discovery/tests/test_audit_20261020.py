"""Audit 2026-10-20 (ADR 0013) — regression tests, SYNTHETIC data only.

1. robustness neighbours (evaluated because their centre passed VALIDATION)
   never enter the search pool
2. behavioural duplicates (entry AND filter == one conjunction) are not
   proposed again; ids stay stable
3. same-event before/after cost attribution and the validation failure class
4. cost calibration: unit checks, order-id duplicates, quarantine,
   INSUFFICIENT_DATA vs NOT_VERIFIED, per-symbol limits
5. cycle summary: CYCLE_RUNNING marker, CYCLE_FAILED on crash, health
   reports an interrupted cycle
6. evolution_report re-derives the stage counts from the ledgers
"""
from __future__ import annotations

import json
import sys
import unittest
from dataclasses import asdict, replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import cost_calibration as cc  # noqa: E402
from costs_bridge import CostContext  # noqa: E402
from engine_lib import iter_jsonl  # noqa: E402
from strategy_spec import SPEC_SCHEMA  # noqa: E402
from test_completion import synth_fills  # noqa: E402
from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider, hour_spec, verified_fee_cost  # noqa: E402


def _cond(feature, params, op, value):
    return {"feature": feature, "params": params, "op": op, "value": value}


A = _cond("ret_z", {"lookback": 16, "window": 64, "min_history": 0}, "gt", 1.5)
B = _cond("hour_utc", {}, "between", [0.0, 6.0])


def spec(entry, filters=(), *, logic="all", horizon=8, direction="long"):
    return {"schema": SPEC_SCHEMA, "direction": direction, "symbols": ["BTCUSDT"],
            "entry": {"logic": logic, "conditions": list(entry)}, "filters": list(filters),
            "exit": {"horizon": horizon}}


# ---------------------------------------------------------------------------
# 1. validation -> search leak through robustness neighbours
# ---------------------------------------------------------------------------

class TestNeighboursStayOutOfSearch(PipelineCase):
    def test_neighbours_are_dev_evaluated_but_not_search_feedback(self):
        from search import SearchConfig, select_elite
        p = self.pipe(SyntheticProvider())
        ctx = p.context_id()
        sids = self.register_all(p, [EDGE] + NOISE)
        before = p.dev_feedback_all()
        vb = p.run_validation()
        self.assertEqual(vb["outcome"][sids[0]], "PASSED_STEPM")      # robustness ran
        neigh = {r["strategy_id"] for r in iter_jsonl(p.specs.path)
                 if r["source"].startswith("neighbour_of:")}
        evaluated = {sid for (st, sid, c) in p._index() if st == "dev" and c == ctx}
        self.assertTrue(neigh & evaluated, "the scenario must DEV-evaluate neighbours")
        fb = p.dev_feedback_all()
        self.assertFalse(neigh & set(fb), "a neighbour leaked into the search feedback")
        self.assertEqual(set(fb), set(before))          # validation changed nothing the search sees
        self.assertEqual(set(fb), p.search_pool() & evaluated)
        self.assertFalse(neigh & set(select_elite(fb, SearchConfig())))


# ---------------------------------------------------------------------------
# 2. behavioural duplicates
# ---------------------------------------------------------------------------

class TestBehaviourKey(unittest.TestCase):
    def test_entry_and_filter_conjunction_is_one_behaviour(self):
        from evolution import behaviour_key
        from engine_lib import strategy_id
        from strategy_spec import canonicalize_spec
        x, y = canonicalize_spec(spec([A, B])), canonicalize_spec(spec([A], [B]))
        self.assertNotEqual(strategy_id(x), strategy_id(y))          # ids unchanged (stable)
        self.assertEqual(behaviour_key(x), behaviour_key(y))
        # a filter that repeats an entry condition adds nothing
        self.assertEqual(behaviour_key(spec([A])), behaviour_key(spec([A], [A])))
        # risk settings are not read by any evaluation stage: same events, same key
        # (real run R4: a change_risk child and its parent were both in one batch)
        self.assertEqual(behaviour_key(spec([A])), behaviour_key({**spec([A]), "risk": {"risk_per_trade": 0.007}}))

    def test_really_different_strategies_keep_different_keys(self):
        from evolution import behaviour_key
        base = behaviour_key(spec([A, B]))
        # one entry condition under "any" is canonicalised to "all": A AND B again
        self.assertEqual(behaviour_key(spec([A], [B], logic="any")), base)
        for other in (spec([A, B], logic="any"), spec([A, B], horizon=12), spec([A, B], direction="short"),
                      spec([A, {**B, "value": [0.0, 7.0]}]), spec([A])):
            with self.subTest(other=other):
                self.assertNotEqual(behaviour_key(other), base)
        # under "any" logic a filter is NOT an entry alternative
        self.assertNotEqual(behaviour_key(spec([A, B], logic="any")),
                            behaviour_key(spec([A], [B], logic="any")))

    def test_propose_round_skips_known_behaviour(self):
        from evolution import behaviour_key
        from search import SearchConfig, propose_round
        cfg = SearchConfig(children_per_round=40, exploration_share=1.0, seed=7)
        free, c0 = propose_round(cfg=cfg, round_index=1, elite=[], specs={}, feedback={},
                                 available_data=["klines"], known_ids=set(), symbols=["BTCUSDT"])
        self.assertEqual(c0["semantic_duplicates"], 0)
        keys = {behaviour_key(x["spec"]) for x in free}
        again, c1 = propose_round(cfg=cfg, round_index=1, elite=[], specs={}, feedback={},
                                  available_data=["klines"], known_ids=set(), symbols=["BTCUSDT"],
                                  known_keys=keys)
        self.assertFalse({behaviour_key(x["spec"]) for x in again} & keys)
        self.assertGreater(c1["semantic_duplicates"], 0)


# ---------------------------------------------------------------------------
# 3. before / after cost and failure classes
# ---------------------------------------------------------------------------

class TestCostAttribution(PipelineCase):
    def test_same_events_before_and_after_cost(self):
        from pipeline import cost_attribution
        self.assertEqual(cost_attribution([])["gross_mean_bp"], None)       # no trades: no number
        p = self.pipe(SyntheticProvider(), cost=CostContext.futures_default())
        sid, = self.register_all(p, [EDGE])
        m = p.evaluation("dev", sid)["metrics"]
        self.assertGreater(m["dev_cost_mean_bp"], 0)
        self.assertAlmostEqual(m["dev_gross_mean_bp"] - m["dev_cost_mean_bp"] - m["dev_funding_mean_bp"],
                               m["dev_net_mean_bp"], places=9)
        self.assertAlmostEqual(m["dev_net_mean_bp"], m["dev_mean_bp"], places=9)
        per = p.evaluation("dev", sid)["per_symbol"]["BTCUSDT"]
        self.assertAlmostEqual(per["net_mean_bp"], per["mean_bp"], places=9)
        from search import DEV_FEEDBACK_KEYS
        self.assertFalse({"dev_gross_mean_bp", "dev_cost_mean_bp"} & set(DEV_FEEDBACK_KEYS))

    def test_validation_record_carries_failure_class(self):
        p = self.pipe(SyntheticProvider(val_drift_hours=()), cost=CostContext.futures_default())
        sids = self.register_all(p, [EDGE] + NOISE[:7])
        vb = p.run_validation()
        if vb is None:
            self.skipTest("no candidate passed the DEV screen in this synthetic world")
        for m in vb["outcome"]:
            rec = p.evaluation("validation", m)
            self.assertIn(rec["failure_class"], ("NO_EDGE_OUT_OF_SAMPLE", "FAIL_COST", "FAIL_STAT",
                                                 "INSUFFICIENT_DATA", "DATA_MISSING", "PASSED_STEPM"))
            for x in rec["per_symbol"].values():
                if x["n"]:
                    self.assertAlmostEqual(x["gross_mean_bp"] - x["cost_mean_bp"] - x["funding_mean_bp"],
                                           x["mean_bp"], places=9)
        self.assertTrue(sids)

    def test_failure_class_rules(self):
        from pipeline import validation_failure_class as f

        def info(n, gross, net):
            return {"n": n, "gross_mean_bp": gross, "net_mean_bp": net}
        syms = ["BTCUSDT", "ETHUSDT"]
        kw = {"rejected": False, "powered": True, "min_events": 30}
        self.assertEqual(f({"BTCUSDT": info(50, 5, 1), "ETHUSDT": info(50, 5, 1)}, syms,
                           rejected=True, powered=True, min_events=30), "PASSED_STEPM")
        self.assertEqual(f({"BTCUSDT": info(50, 5, 1)}, syms, **kw), "DATA_MISSING")
        self.assertEqual(f({"BTCUSDT": info(50, 5, 1), "ETHUSDT": info(10, 5, 1)}, syms,
                           **{**kw, "powered": False}), "INSUFFICIENT_DATA")
        # the WORST symbol decides: no best-symbol selection
        self.assertEqual(f({"BTCUSDT": info(50, 9, 4), "ETHUSDT": info(50, -1, -6)}, syms, **kw),
                         "NO_EDGE_OUT_OF_SAMPLE")
        self.assertEqual(f({"BTCUSDT": info(50, 9, 4), "ETHUSDT": info(50, 3, -2)}, syms, **kw),
                         "FAIL_COST")
        self.assertEqual(f({"BTCUSDT": info(50, 9, 4), "ETHUSDT": info(50, 3, 1)}, syms, **kw),
                         "FAIL_STAT")


# ---------------------------------------------------------------------------
# 4. cost calibration evidence
# ---------------------------------------------------------------------------

def two_symbol_fills(n=1000, *, btc_ver_bias_bp=0.0, eth_ver_bias_bp=0.0):
    obs = synth_fills(n=n, symbols=("BTCUSDT", "ETHUSDT"))
    out = []
    for i, o in enumerate(obs):
        if i >= n // 2:                      # verification half (chronological split)
            b = btc_ver_bias_bp if o.symbol == "BTCUSDT" else eth_ver_bias_bp
            o = replace(o, fill_price=o.fill_price * (1 + o.side * b / 1e4))
        out.append(o)
    return out


class TestCalibrationAudit(unittest.TestCase):
    def setUp(self):
        import tempfile
        import shutil
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)

    def calib(self, obs, symbols=("BTCUSDT", "ETHUSDT"), **kw):
        return cc.calibrate_and_verify(obs, CostContext.futures_default(), required_symbols=list(symbols),
                                       order_value=10_000.0, **kw)

    def test_unit_errors_are_refused(self):
        good = synth_fills(n=1)[0]
        good.validate()
        bad = {
            "seconds_timestamp": replace(good, ts_ms=1_700_000_000),
            "future_timestamp": replace(good, ts_ms=4_102_444_800_000),
            "fee_in_bp": replace(good, fee=4.0 * good.filled_notional),       # 4 'bp' read as 400 %
            "fee_in_percent": replace(good, fee=0.04 * good.filled_notional),
            "price_unit": replace(good, fill_price=good.fill_price * 100),
            "overfill": replace(good, filled_notional=good.notional * 2),
            "partial_not_partial": replace(good, status="PARTIAL"),
            "blank_order_id": replace(good, order_id=" "),
        }
        for name, o in bad.items():
            with self.subTest(name), self.assertRaises(cc.CalibrationError):
                o.validate()
        replace(good, status="PARTIAL", filled_notional=good.notional / 2, fee=good.fee / 2).validate()

    def test_same_order_id_twice_is_a_duplicate(self):
        a = replace(synth_fills(n=1)[0], order_id="1001")
        b = replace(a, bar_volatility=0.003)          # re-export with a recomputed bar field
        p = self.td / "obs.jsonl"
        p.write_text(json.dumps(asdict(a)) + "\n" + json.dumps(asdict(b)) + "\n")
        with self.assertRaises(cc.CalibrationError) as e:
            cc.load_observations(p)
        self.assertIn("duplicate", str(e.exception))

    def test_quarantine_isolates_bad_rows_and_blocks_verified(self):
        obs = synth_fills(n=1000, symbols=("BTCUSDT", "ETHUSDT"))
        p = self.td / "obs.jsonl"
        rows = [json.dumps(asdict(o)) for o in obs]
        rows.insert(10, rows[3])                                     # duplicate
        rows.insert(20, json.dumps({**asdict(obs[0]), "ts_ms": 1_700_000_000}))   # seconds
        rows.insert(30, json.dumps({**asdict(obs[0]), "venue": "x"}))             # unknown field
        p.write_text("\n".join(rows) + "\n")
        good, bad = cc.load_observations_checked(p)
        self.assertEqual((len(good), len(bad)), (1000, 3))
        self.assertEqual([b["line"] for b in bad], [11, 21, 31])
        clean = self.calib(good)
        self.assertEqual(clean["status"], "VERIFIED", clean["reasons"])
        dirty = self.calib(good, quarantined=len(bad))
        self.assertEqual(dirty["status"], "DATA_INVALID")   # v4: was NOT_VERIFIED, still never VERIFIED
        self.assertTrue(any("quarantined" in r for r in dirty["reasons"]))

    def test_in_memory_duplicates_are_not_evidence(self):
        obs = synth_fills(n=1000, symbols=("BTCUSDT", "ETHUSDT"))
        rec = self.calib(obs + obs[:5])
        # v4 (2026-10-20): integrity problems are DATA_INVALID (was NOT_VERIFIED) — still never VERIFIED
        self.assertEqual((rec["status"], rec["n_duplicates"]), ("DATA_INVALID", 5))

    def test_too_little_data_is_insufficient_not_failed(self):
        rec = self.calib(synth_fills(n=100))                        # 50 BTC, 0 ETH verification fills
        self.assertEqual(rec["status"], "INSUFFICIENT_DATA")
        self.assertTrue(rec["sample_shortfall"])
        with self.assertRaises(Exception):
            CostContext.from_verification(cc.seal(rec))
        # a real failure on little data stays a failure (failure wins over shortfall)
        biased = two_symbol_fills(n=200, btc_ver_bias_bp=10.0, eth_ver_bias_bp=10.0)
        self.assertEqual(self.calib(biased)["status"], "NOT_VERIFIED")

    def test_pooled_error_cannot_hide_one_symbol(self):
        rec = self.calib(two_symbol_fills(btc_ver_bias_bp=1.0, eth_ver_bias_bp=-3.0))
        ve = rec["verification_errors"]
        # the pooled numbers alone are inside the preregistered limits ...
        self.assertLessEqual(ve["mean_bp"], 0.5)
        self.assertLessEqual(ve["p90_bp"], 2.0)
        # ... but BTCUSDT on its own is underestimated by ~1bp
        self.assertGreater(rec["verification_errors_by_symbol"]["BTCUSDT"]["mean_bp"], 0.5)
        self.assertEqual(rec["status"], "NOT_VERIFIED")
        self.assertTrue(any(r.startswith("BTCUSDT: mean underestimate") for r in rec["reasons"]))

    def test_uncertainty_and_period_are_recorded(self):
        rec = self.calib(synth_fills(n=1000, symbols=("BTCUSDT", "ETHUSDT")))
        lo, hi = rec["verification_errors"]["mean_ci95_bp"]
        self.assertLess(lo, rec["verification_errors"]["mean_bp"])
        self.assertGreater(hi, rec["verification_errors"]["mean_bp"])
        self.assertGreater(rec["collection_period"]["days"], 0)
        self.assertTrue(rec["coverage_notes"])


# ---------------------------------------------------------------------------
# 5. cycle summary and health
# ---------------------------------------------------------------------------

class TestCycleSummaryIntegrity(PipelineCase):
    def test_crash_overwrites_previous_ok_with_failed(self):
        from autoloop import run_evolution_cycle
        from search import SearchConfig
        import pipeline
        res = self.td / "res"
        kw = dict(symbols=["BTCUSDT"], search_cfg=SearchConfig(children_per_round=4, seed=3),
                  seed_legacy=False, rounds=1, max_dev_evals=10, results_dir=res)
        ok = run_evolution_cycle(self.td / "st", SyntheticProvider(), verified_fee_cost(), **kw)
        self.assertNotEqual(ok.status, "CYCLE_FAILED")
        summary = res / "evolution_cycle_summary.json"
        self.assertEqual(json.loads(summary.read_text())["status"], ok.status)
        orig = pipeline.EvolutionPipeline.run_validation

        def boom(self, *a, **k):
            raise RuntimeError("simulated crash")
        pipeline.EvolutionPipeline.run_validation = boom
        self.addCleanup(setattr, pipeline.EvolutionPipeline, "run_validation", orig)
        with self.assertRaises(RuntimeError):
            run_evolution_cycle(self.td / "st", SyntheticProvider(), verified_fee_cost(), **kw)
        s = json.loads(summary.read_text())
        self.assertEqual(s["status"], "CYCLE_FAILED")
        self.assertIn("simulated crash", s["notes"][0])

    def test_health_reports_a_killed_cycle(self):
        from autoloop import EvolutionCycleSummary
        from ops import health
        st = self.td / "st"
        (st / "evolution").mkdir(parents=True)
        res = self.td / "res"
        res.mkdir()
        (res / "evolution_cycle_summary.json").write_text(
            json.dumps(EvolutionCycleSummary(status="CYCLE_RUNNING").to_json()))
        h = health(st, results_dir=res, config_dir=DISC / "config")
        self.assertEqual(h["checks"]["last_cycle"]["status"], "CYCLE_INTERRUPTED")
        self.assertEqual(h["status"], "DEGRADED")


# ---------------------------------------------------------------------------
# 5b. alpha spending survives a fresh state directory
# ---------------------------------------------------------------------------

class TestValidationWindowHistory(PipelineCase):
    def test_external_batches_continue_the_spending_sequence(self):
        from stats_mc import spending_alpha
        from pipeline import EvolutionPipeline
        hist = self.td / "hist.jsonl"
        probe = self.pipe(SyntheticProvider(), state=self.td / "probe")
        key = probe._val_window_key("BTCUSDT")
        hist.write_text("".join(json.dumps({"run": f"R{i}", "batches": 1, "window_keys": [key],
                                            "batch_ids": []}) + "\n" for i in range(3)))
        p = EvolutionPipeline(self.td / "fresh", SyntheticProvider(), verified_fee_cost(),
                              symbols=["BTCUSDT"], validation_history=hist)
        edge, *_ = self.register_all(p, [EDGE] + NOISE[:7])
        b = p.preregister_batch([edge])
        w = b["windows"][key]
        self.assertEqual((w["batch_index"], w["external_prior_batches"]), (4, 3))
        self.assertAlmostEqual(w["alpha"], spending_alpha(4, 0.05 / 2))   # promo-v3: search family split over 2 windows
        # the same directory written to the history is not counted twice
        with hist.open("a") as f:
            f.write(json.dumps({"run": "this", "batches": 1, "window_keys": [key],
                                "batch_ids": [b["batch_id"]]}) + "\n")
        self.assertEqual(p.external_prior_batches(key), 3)
        # without a history the old behaviour (per directory) is unchanged
        self.assertEqual(self.pipe(SyntheticProvider(), state=self.td / "x").external_prior_batches(key), 0)

    def test_repository_history_is_well_formed(self):
        from pipeline import DEFAULT_VALIDATION_HISTORY
        rows = list(iter_jsonl(DEFAULT_VALIDATION_HISTORY))
        self.assertGreaterEqual(len(rows), 6)
        for r in rows:
            self.assertGreaterEqual(r["batches"], 1)
            self.assertTrue(r["window_keys"] and r["source"])

    def test_runner_refuses_without_history(self):
        import os
        import runner
        env = {"NEW2_VALIDATION_HISTORY": str(self.td / "absent.jsonl"),
               "NEW2_DISCOVERY_ROOT": str(self.td / "root"), "NEW2_DATA_DIR": str(self.td)}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                rc = runner.main(["--mode", "evolve"])
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(rc, 1)
        self.assertIn("validation window history", out.getvalue())


# ---------------------------------------------------------------------------
# 6. report re-derived from ledgers
# ---------------------------------------------------------------------------

class TestEvolutionReport(PipelineCase):
    def test_counts_match_ledgers(self):
        from evolution_report import build_report, to_markdown
        p = self.pipe(SyntheticProvider(val_drift_hours=()))
        self.register_all(p, [EDGE] + NOISE[:7] + [hour_spec(0, 2, horizon=9)])
        vb = p.run_validation()
        rep = build_report(self.td / "state")
        self.assertEqual(rep["context_id"], p.context_id())
        self.assertEqual(rep["candidates"]["search_pool"], 9)
        self.assertEqual(rep["dev"]["evaluated"], 9)
        n_members = len(vb["outcome"]) if vb else 0
        self.assertEqual(rep["validation"]["members"], n_members)
        self.assertEqual(sum(rep["validation"]["outcomes"].values()), n_members)
        self.assertEqual(sum(rep["statuses"].values()), len(p.statuses.all(p.context_id())))
        self.assertEqual(rep["survivors"], [])
        self.assertEqual(rep["locked"]["exposed_uses"], 0)
        self.assertIn("Validation members", to_markdown(rep))


if __name__ == "__main__":
    unittest.main()
