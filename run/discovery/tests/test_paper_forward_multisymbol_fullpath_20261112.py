"""ADR 0080 §5 (directive "ADR 0078" §5, audit r2 P4): the forward paper step
path end to end with TWO symbols whose bars DIFFER.

Until now multi-symbol behaviour was fixed only by ``_rule_cutoff`` /
``_sample_bound`` unit tests and by ``TestTwoSymbols`` (ADR 0077), which feeds
the SAME synthetic bars to both symbols.  Here a two-symbol strategy (BTCUSDT
+ ETHUSDT, each with its own synthetic history) goes through registration,
DEV, validation, the locked campaign and the replay paper stage, and then
through ``step()``:

* ETHUSDT: its own synthetic bars on the 15-minute grid;
* BTCUSDT: other synthetic bars, delivered in 30-minute batches (a bar that
  closes at :15 / :45 appears only at the next :30 / :00 — recorded in its
  ``delay_ms``) and the last delivered bar sent again with every batch
  (counted in ``duplicates``);
* ETHUSDT lags 3 days behind, then delivers up to the bar after its day-70
  entry (position open at the cutoff), then catches up.

Asserted: (a) the cutoff waits for the common observation time (min over the
symbols); (b) after the catch-up the cutoff is the same bar T as without a lag
and the sample is the same; (c) ETH's open position is waited for and closed
without OPEN_POSITION_MISSING; (d) trades of both symbols are in the sample.
Plus the reproduction of the ADR 0076 §3 argument against a max / per-symbol
bound (audit finding F-1): at the lagging step a max bound would let the rule
hold while ETH's entries since its last bar are not known yet — trades that
are in the final sample.

Test configuration (synthetic only): the locked TEST window of ETHUSDT is set,
for the duration of a test, to the BTCUSDT one, so that both synthetic
histories cover the same calendar and the per-symbol DEV series are
contiguous (see the report: DEV series with a calendar gap between symbols
make ``EvolutionPipeline._dsr`` raise KeyError — out of scope here).  No
market data, no network, no exchange account; the real locked / IW / sealed
windows and ledgers are never touched (temp dirs only).
"""
from __future__ import annotations

import os
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
from test_paper_forward_funding_20261111 import DAY, HOUR, declared_settlements  # noqa: E402
from test_paper_forward_positions_20261111 import MIN15, OPEN_AT_RULE, read_log  # noqa: E402
from test_pipeline import EDGE, NOISE, PipelineCase, SyntheticProvider, _ms, synth_bars, verified_fee_cost  # noqa: E402

SYMS = ("BTCUSDT", "ETHUSDT")
MIN30 = 2 * MIN15
#: OPEN_AT_RULE: min_days = 70 days 45 minutes -> T = start + 70 d + 30 min, inside the
#: day-70 holding period (entry 00:15, exit 02:15) of both symbols
T_OFFSET = 70 * DAY + MIN30
ENTRY70 = 70 * DAY + MIN15
EXIT70 = 70 * DAY + 9 * MIN15


class TwoStreamFeed:
    """Per-symbol synthetic bars and declared funding.

    ``batched`` symbols see a bar only once its close is at or before the last
    30-minute boundary of the clock, and receive the last bar they already have
    again with every call (a duplicate).  ``limits[sym]`` = last bar the feed
    has delivered for ``sym`` (a lagging symbol), None = no limit."""

    kind = "synthetic_test_feed"
    realtime = False

    def __init__(self, bars: dict, clock, rows, *, batched=()):
        self.bars, self.clock, self.rows = bars, clock, list(rows)
        self.batched = set(batched)
        self.limits: dict = {}

    def closed_bars(self, sym, after_ts):
        now = self.clock()
        horizon = now - now % MIN30 if sym in self.batched else now
        lim = self.limits.get(sym)
        out = [b for b in self.bars[sym] if b.ts > after_ts and b.ts + MIN15 <= horizon
               and (lim is None or b.ts <= lim)]
        if sym in self.batched and out:
            prior = [b for b in self.bars[sym] if b.ts <= after_ts]
            if prior:
                out = [prior[-1]] + out              # sent again: counted as a duplicate
        return out, {}

    def funding(self, sym, after_ts):
        return [(t, r) for t, r, _ in self.rows if after_ts < t <= self.clock()]

    def funding_checked(self, sym, after_ts):
        from paper_forward import funding_fetch_result
        rows = [(t, r, h) for t, r, h in self.rows if after_ts < t <= self.clock()]
        return funding_fetch_result(
            [(t, r) for t, r, _ in rows], {t: int(h * HOUR) for t, _, h in rows},
            source_files=[{"name": f"synthetic-{sym}", "sha256": "0" * 64, "rows": len(rows),
                           "status": "VALID", "errors": []}],
            schedule_source="declared_by_test", query={"after_ts": after_ts, "until_ts": self.clock()})


class MultiSymbolCase(PipelineCase):
    def setUp(self):
        super().setUp()
        import engine_lib
        self.enterContext(mock.patch.dict(engine_lib.LOCKED_TEST_PERIODS,
                                          {"ETHUSDT": engine_lib.LOCKED_TEST_PERIODS["BTCUSDT"]}))

    def build(self, tag="a"):
        """A two-symbol strategy through every stage before the forward paper,
        and its forward plan.  Returns (p, edge, feed, fp, plan)."""
        from paper import run_paper
        from paper_forward import DEFAULT_CRITERIA, ForwardPaper
        from pipeline import EvolutionPipeline
        root = self.td / tag
        # ADR 0085 (L-1): each build is an independent universe (own state dir AND own
        # shared locked-TEST record), exactly like two separate repositories
        self.enterContext(mock.patch.dict(os.environ, {"NEW2_LOCK_SHARED_DIR": str(root / "shared")}))
        prov = SyntheticProvider(symbols=SYMS)
        p = EvolutionPipeline(root / "state", prov, verified_fee_cost(), symbols=list(SYMS))
        sids = self.register_all(p, [dict(s, symbols=list(SYMS)) for s in [EDGE] + NOISE[:7]])
        edge, ctx = sids[0], p.context_id()
        p.run_validation()
        self.assertEqual(p.statuses.status(edge, ctx), "VALIDATION_SURVIVOR")
        p.run_locked_campaign()
        self.assertEqual(p.statuses.status(edge, ctx), "PAPER_TEST_READY")
        run_paper(p, edge)
        self.assertEqual(p.statuses.status(edge, ctx), "PAPER_TESTING")
        # the two symbols have their own synthetic histories
        self.assertNotEqual([b.close for b in prov.paper("BTCUSDT")["bars"][:50]],
                            [b.close for b in prov.paper("ETHUSDT")["bars"][:50]])
        bars = {sym: synth_bars("2024-07-01", "2025-03-31", seed=77 + k, drift_hours=(0, 1, 2),
                                price0=prov.paper(sym)["bars"][-1].close) for k, sym in enumerate(SYMS)}
        self.assertNotEqual([b.close for b in bars["BTCUSDT"][:50]], [b.close for b in bars["ETHUSDT"][:50]])
        self.now = [_ms("2024-08-25")]
        lo, hi = bars["BTCUSDT"][0].ts, bars["BTCUSDT"][-1].ts + DAY
        feed = TwoStreamFeed(bars, lambda: self.now[0], declared_settlements(lo, hi, 4), batched=("BTCUSDT",))
        c = json.loads(DEFAULT_CRITERIA.read_text())
        c.update(OPEN_AT_RULE)
        crit = root / "criteria.json"
        crit.write_text(json.dumps(c, indent=1))
        fp = ForwardPaper(p, feed, criteria_path=crit, clock=lambda: self.now[0])
        plan = fp.register_plan(edge)
        self.assertEqual(plan["symbols"], list(SYMS))
        return p, edge, feed, fp, plan

    def at(self, plan, offset_ms):
        self.now[0] = plan["start_ts"] + offset_ms

    def ledger(self, fp, plan):
        return list(iter_jsonl(fp.root / plan["plan_id"] / "events.jsonl"))

    def sample(self, fp, plan, t):
        return sorted(r["event_id"] for r in fp._counted(plan, self.ledger(fp, plan)) if r["entry_ts"] <= t)

    def steps_text(self, fp, plan):
        path = fp.root / plan["plan_id"] / "steps.jsonl"
        return path.read_text() if path.exists() else ""

    def conclude_without_lag(self, tag):
        p, edge, feed, fp, plan = self.build(tag)
        self.at(plan, EXIT70 + 2 * HOUR)
        r = fp.step(plan["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        return p, edge, feed, fp, plan


class TestMultiSymbolFullPath(MultiSymbolCase):
    def test_lagging_symbol_end_to_end(self):
        p, edge, feed, fp, plan = self.build()
        pid, s0 = plan["plan_id"], plan["start_ts"]
        T = s0 + T_OFFSET
        self.assertEqual(fp._grid_bar(plan, plan["criteria"]["min_days"]), T)

        # -- phase 1: ETH three days behind ------------------------------------
        feed.limits["ETHUSDT"] = s0 + 67 * DAY + MIN30
        for day in (20, 45):
            self.at(plan, day * DAY)
            rr = fp.step(pid); self.assertEqual(rr["verdict"], "PAPER_OBSERVING", rr["reasons"])
        self.at(plan, EXIT70 + 2 * HOUR)                  # 04:15: funding of 04:00 is known
        r1 = fp.step(pid)
        st1 = fp.state(pid)
        self.assertEqual(r1["verdict"], "PAPER_OBSERVING", r1["reasons"])
        self.assertEqual(st1["last_ts"]["ETHUSDT"], s0 + 67 * DAY + MIN30)
        self.assertEqual(st1["last_ts"]["BTCUSDT"], s0 + 70 * DAY + 4 * HOUR - MIN15)   # batched: 03:45
        # (a) the cutoff waits for the common observation time
        self.assertIsNone(st1.get("evaluation_cutoff"))
        self.assertIsNone(fp._rule_cutoff(plan, st1, self.ledger(fp, plan)))
        self.assertEqual(fp._sample_bound(plan, st1), min(st1["last_ts"].values()))
        self.assertEqual(fp._sample_bound(plan, st1), st1["last_ts"]["ETHUSDT"])
        # F-1 (ADR 0076 §3): with a max (or per-symbol) bound the rule would already
        # hold at T, while ETH's entries after its last bar are not known yet
        st_max = dict(st1, last_ts={s: max(st1["last_ts"].values()) for s in SYMS})
        self.assertEqual(fp._rule_cutoff(plan, st_max, self.ledger(fp, plan)), T)
        unseen_window = (st1["last_ts"]["ETHUSDT"], T)
        self.assertEqual([r for r in self.ledger(fp, plan) if r["symbol"] == "ETHUSDT"
                          and unseen_window[0] < r["entry_ts"] <= T], [])

        # -- phase 2: ETH up to the bar after its day-70 entry (position open) ---
        feed.limits["ETHUSDT"] = s0 + 70 * DAY + 3 * MIN15                    # 00:45
        r2 = fp.step(pid)
        st2 = fp.state(pid)
        self.assertEqual(r2["verdict"], "PAPER_OBSERVING", r2["reasons"])
        self.assertEqual(st2["evaluation_cutoff"]["ts"], T)
        eth_open = f"ETHUSDT:{s0 + ENTRY70}"
        self.assertIn("OPEN_POSITIONS_PENDING", r2["reasons"][0])
        self.assertIn(eth_open, r2["reasons"][0])
        self.assertNotIn(f"BTCUSDT:{s0 + ENTRY70}", r2["reasons"][0])      # BTC's exit 02:15 is observed
        self.assertIn(f"BTCUSDT:{s0 + ENTRY70}", self.sample(fp, plan, T))
        self.assertNotIn(eth_open, self.sample(fp, plan, T))

        # -- phase 3: ETH catches up ------------------------------------------
        feed.limits["ETHUSDT"] = None
        self.at(plan, EXIT70 + 2 * HOUR + MIN15)
        r3 = fp.step(pid)
        self.assertEqual(r3["verdict"], "PAPER_PASSED", r3["reasons"])
        st3 = fp.state(pid)
        self.assertTrue(st3["concluded"])
        rec = p.evaluation("paper_forward", edge)
        # (b) the same T as recorded while ETH was still open, and in the record
        self.assertEqual(st3["evaluation_cutoff"]["ts"], T)
        self.assertEqual(rec["anchors"]["evaluation_cutoff_ts"], T)
        self.assertEqual(rec["positions"]["evaluation_cutoff_ts"], T)
        # (c) ETH's open position was waited for and closed; never reported missing
        led = self.ledger(fp, plan)
        eth70 = [r for r in led if r["event_id"] == eth_open]
        self.assertEqual(len(eth70), 1)
        self.assertEqual(eth70[0]["exit_ts"], s0 + EXIT70)
        self.assertNotIn("OPEN_POSITION_MISSING", self.steps_text(fp, plan))
        self.assertEqual(rec["positions"]["open_replayed"], [])
        # (d) both symbols' trades are in the sample, the ETH ones of the lag included
        sample = self.sample(fp, plan, T)
        self.assertEqual(rec["metrics"]["n_trades"], len(sample))
        by_sym = {s: [e for e in sample if e.startswith(s)] for s in SYMS}
        self.assertGreaterEqual(len(by_sym["BTCUSDT"]), 60)
        self.assertGreaterEqual(len(by_sym["ETHUSDT"]), 60)
        self.assertIn(eth_open, sample)
        lagged = [e for e in by_sym["ETHUSDT"] if unseen_window[0] < int(e.split(":")[1]) <= T]
        self.assertGreaterEqual(len(lagged), 3, lagged)      # what a max bound would have left out
        # the two streams: different prices, BTC's delays and duplicates recorded
        bl = {s: read_log(fp.root / pid / f"bars_{s}.jsonl") for s in SYMS}
        self.assertEqual([r["ts"] for r in bl["BTCUSDT"]][:100], [r["ts"] for r in bl["ETHUSDT"]][:100])
        self.assertNotEqual([r["c"] for r in bl["BTCUSDT"]][:100], [r["c"] for r in bl["ETHUSDT"]][:100])
        self.assertGreater(st3["duplicates"]["BTCUSDT"], 0)
        self.assertEqual(st3["duplicates"]["ETHUSDT"], 0)
        self.assertEqual(st3["gaps"], {s: [] for s in SYMS})
        self.assertTrue(all(r["delay_ms"] >= 0 and r["ts"] % MIN15 == 0 for s in SYMS for r in bl[s]))
        # the last BTC batch: the bar closing at :15 waited for the :30 boundary
        last_btc = bl["BTCUSDT"][-1]
        self.assertEqual((last_btc["ts"] + MIN15) % MIN30, 0)
        self.assertEqual(fp.verify_conclusion(pid), [])
        self.assertEqual(fp.step(pid)["status"], "CONCLUDED")

    def test_catch_up_gives_the_same_T_and_sample_as_no_lag(self):
        """(b): the conclusion does not depend on which symbol lagged or for how long."""
        pa, ea, fa, fpa, pla = self.conclude_without_lag("control")
        pb, eb, fb, fpb, plb = self.build("lagged")
        s0 = plb["start_ts"]
        self.assertEqual(s0, pla["start_ts"])
        fb.limits["ETHUSDT"] = s0 + 50 * DAY
        self.at(plb, EXIT70 + 2 * HOUR)
        r0 = fpb.step(plb["plan_id"])
        self.assertEqual(r0["verdict"], "PAPER_OBSERVING", r0["reasons"])
        self.assertIsNone(fpb.state(plb["plan_id"]).get("evaluation_cutoff"))
        fb.limits["ETHUSDT"] = None
        self.at(plb, EXIT70 + 2 * HOUR + MIN15)
        r = fpb.step(plb["plan_id"])
        self.assertEqual(r["verdict"], "PAPER_PASSED", r["reasons"])
        ra, rb = pa.evaluation("paper_forward", ea), pb.evaluation("paper_forward", eb)
        T = s0 + T_OFFSET
        self.assertEqual(ra["anchors"]["evaluation_cutoff_ts"], T)
        self.assertEqual(rb["anchors"]["evaluation_cutoff_ts"], T)
        self.assertEqual(self.sample(fpa, pla, T), self.sample(fpb, plb, T))
        for k in ("n_trades", "mean_net_bp", "hac_p_one_sided", "stressed_mean_bp", "mdd_bp"):
            self.assertEqual(ra["metrics"][k], rb["metrics"][k], k)

    def test_appended_row_of_one_symbol_is_found(self):
        """ADR 0080 §4.1 on the two-symbol plan: a row appended after the
        conclusion to one symbol's log names that log only."""
        from test_paper_forward_evidence_final_20261112 import append_chained, bump_funding
        p, edge, feed, fp, plan = self.conclude_without_lag("a")
        self.assertEqual(fp.verify_conclusion(plan["plan_id"]), [])
        append_chained(fp.root / plan["plan_id"] / "funding_ETHUSDT.jsonl", bump_funding)
        why = fp.verify_conclusion(plan["plan_id"])
        self.assertTrue(any("funding_ETHUSDT.jsonl" in x and "rows" in x for x in why), why)
        self.assertFalse(any("BTCUSDT" in x for x in why), why)


if __name__ == "__main__":
    unittest.main()
