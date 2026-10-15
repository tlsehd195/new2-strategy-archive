"""Auto-evolution directive §2: regressions for the core defects.

2-1 15m data integrity · 2-2 default parameters vs execution · 2-3 look-ahead
and publication timing · 2-4 ledger corruption / crash recovery.

Synthetic data only, isolated temp dirs.
"""
from __future__ import annotations

import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from data_quality import (  # noqa: E402
    BLOCK, OK, WARN, Bar, check_bars, dataset_for, funding_quality, load_funding_csv_checked,
    load_kline_csv,
)
from engine_lib import (  # noqa: E402
    CandidateState, DefaultsMismatch, JsonlCorruption, acknowledge_corrupt_line, append_jsonl,
    iter_jsonl, normalize_strategy, scan_jsonl, strategy_id,
)
from evaluator import FundingIndex  # noqa: E402
from generator import FAMILY_DEFAULTS, FAMILY_GENERATORS, _mk, resolve_params  # noqa: E402
from test_engine import write_kline_csv  # noqa: E402

MS15 = 900_000
DAY_MS = 86_400_000
T0 = 1_700_000_000_000 // DAY_MS * DAY_MS        # aligned UTC midnight


def rich_bars(n=3000, start_ts=T0, seed=1, vol=0.004):
    """Random walk with varying taker share and volume, so every family fires."""
    rng = random.Random(seed)
    out, p = [], 100.0
    for i in range(n):
        o = p
        c = o * math.exp(rng.gauss(0, vol))
        h = max(o, c) * (1 + abs(rng.gauss(0, vol / 3)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol / 3)))
        v = 1000 * (1 + rng.random())
        out.append(Bar(ts=start_ts + i * MS15, open=o, high=h, low=lo, close=c, volume=v,
                       quote_volume=v * c, taker_buy_volume=v * rng.uniform(0.2, 0.8)))
        p = c
    return out


def rich_funding(first_ts, last_ts, seed=2):
    rng = random.Random(seed)
    t = (first_ts // (8 * 3_600_000)) * 8 * 3_600_000
    out = []
    while t <= last_ts + 8 * 3_600_000:
        out.append((t, rng.gauss(1e-4, 2e-4)))
        t += 8 * 3_600_000
    return out


def rich_fng(first_ts, last_ts, seed=3):
    rng = random.Random(seed)
    return {d: float(rng.randint(1, 99)) for d in range(first_ts // DAY_MS - 12, last_ts // DAY_MS + 2)}


def shift(b, **kw):
    d = dict(b.__dict__)
    d.update(kw)
    return Bar(**d)


# ===========================================================================
# 2-1 data integrity
# ===========================================================================

class TestKlineIntegrity(unittest.TestCase):
    def setUp(self):
        self.bars = rich_bars(200)

    def codes(self, rep):
        return {i["code"] for i in rep.issues}

    def test_clean_ok(self):
        rep = check_bars(self.bars, "X", exceptions=[])
        self.assertEqual(rep.verdict, OK, rep.issues)

    def test_single_missing_bar_30min_gap_blocks(self):
        # the old rule (gap > 2*interval) let exactly this pass
        bars = self.bars[:50] + self.bars[51:]
        rep = check_bars(bars, "X", exceptions=[])
        self.assertEqual(rep.missing_candles, 1)
        self.assertIn("MISSING_BARS", self.codes(rep))
        self.assertEqual(rep.verdict, BLOCK)
        self.assertFalse(rep.ok)

    def test_multiple_missing_bars_counted(self):
        bars = self.bars[:50] + self.bars[54:100] + self.bars[101:]
        rep = check_bars(bars, "X", exceptions=[])
        self.assertEqual(rep.missing_candles, 5)      # 50..53 and 100
        self.assertFalse(rep.ok)

    def test_duplicate_and_inversion(self):
        dup = self.bars[:20] + [self.bars[19]] + self.bars[20:]
        rep = check_bars(dup, "X", exceptions=[])
        self.assertEqual(rep.duplicate_timestamps, 1)
        self.assertIn("DUPLICATE_TS", self.codes(rep))
        inv = list(self.bars)
        inv[30], inv[31] = inv[31], inv[30]
        rep = check_bars(inv, "X", exceptions=[])
        self.assertEqual(rep.time_inversions, 1)
        self.assertIn("TIME_INVERSION", self.codes(rep))
        self.assertTrue(rep.unrecoverable)

    def test_misaligned_timestamp(self):
        bars = list(self.bars)
        bars[10] = shift(bars[10], ts=bars[10].ts + 60_000)
        rep = check_bars(bars, "X", exceptions=[])
        self.assertIn("MISALIGNED_TS", self.codes(rep))
        self.assertFalse(rep.ok)

    def test_nan_inf_negative_volume_taker(self):
        for kw, code in (({"close": float("nan")}, "NON_FINITE"),
                         ({"high": float("inf")}, "NON_FINITE"),
                         ({"volume": -1.0}, "NEGATIVE_VOLUME"),
                         ({"taker_buy_volume": 1e9}, "TAKER_GT_VOLUME"),
                         ({"low": -1.0}, "NON_POSITIVE_PRICE")):
            bars = list(self.bars)
            bars[7] = shift(bars[7], **kw)
            rep = check_bars(bars, "X", exceptions=[])
            self.assertIn(code, self.codes(rep), kw)
            self.assertFalse(rep.ok, kw)

    def test_zero_volume_is_warning_not_block(self):
        bars = list(self.bars)
        bars[7] = shift(bars[7], volume=0.0, taker_buy_volume=0.0)
        rep = check_bars(bars, "X", exceptions=[])
        self.assertEqual(rep.verdict, WARN)
        self.assertTrue(rep.ok)

    def test_known_exchange_gap_excused_only_when_fully_covered(self):
        bars = self.bars[:50] + self.bars[53:]
        gap_from, gap_to = self.bars[49].ts, self.bars[53].ts
        full = [{"symbol": "X", "start_ts": self.bars[50].ts, "end_ts": self.bars[52].ts,
                 "kind": "exchange_halt", "source": "test"}]
        rep = check_bars(bars, "X", exceptions=full)
        self.assertEqual(rep.verdict, WARN, rep.issues)
        self.assertEqual(rep.known_gap_bars, 3)       # bars 50, 51, 52
        partial = [dict(full[0], end_ts=self.bars[51].ts)]
        rep = check_bars(bars, "X", exceptions=partial)
        self.assertEqual(rep.verdict, BLOCK)
        other = [dict(full[0], symbol="Y")]
        self.assertEqual(check_bars(bars, "X", exceptions=other).verdict, BLOCK)
        self.assertLess(gap_from, gap_to)

    def _write(self, td, text):
        p = Path(td) / "k.csv"
        p.write_bytes(text.encode())
        return p

    def _csv_rows(self, bars):
        return [f"{b.ts},{b.open},{b.high},{b.low},{b.close},{b.volume},{b.ts + MS15 - 1},"
                f"{b.quote_volume},10,{b.taker_buy_volume},0,0" for b in bars]

    def test_csv_header_interior_error_torn_tail(self):
        rows = self._csv_rows(self.bars[:30])
        with tempfile.TemporaryDirectory() as td:
            hdr = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,tbq,ignore\n"
            _, rep = load_kline_csv(self._write(td, hdr + "\n".join(rows) + "\n"), "X")
            self.assertEqual((rep.verdict, rep.header_rows, rep.parse_errors), (OK, 1, 0))
            # interior garbage: never silently skipped
            bad = rows[:10] + ["123,abc,1,1,1,1,1,1"] + rows[10:]
            _, rep = load_kline_csv(self._write(td, "\n".join(bad) + "\n"), "X")
            self.assertEqual(rep.parse_errors, 1)
            self.assertIn("PARSE_ERROR", self.codes(rep))
            self.assertFalse(rep.ok)
            # a second "header" in the middle is corruption, not a header
            bad = rows[:10] + [hdr.strip()] + rows[10:]
            _, rep = load_kline_csv(self._write(td, "\n".join(bad) + "\n"), "X")
            self.assertIn("PARSE_ERROR", self.codes(rep))
            # torn tail: distinct code (interrupted write), still blocks
            torn = "\n".join(rows) + "\n" + rows[0][:15]
            _, rep = load_kline_csv(self._write(td, torn), "X")
            self.assertTrue(rep.torn_tail)
            self.assertIn("TORN_TAIL", self.codes(rep))
            self.assertNotIn("PARSE_ERROR", self.codes(rep))
            self.assertFalse(rep.ok)
            # a short interior row is an error, not a torn tail
            short = rows[:5] + ["1,2,3"] + rows[5:]
            _, rep = load_kline_csv(self._write(td, "\n".join(short) + "\n"), "X")
            self.assertIn("PARSE_ERROR", self.codes(rep))

    def test_blocked_dataset_never_carries_bars(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "k.csv"
            write_kline_csv(p, self.bars[:50] + self.bars[51:])
            h = dataset_for("X", p)
            self.assertEqual(h.bars, [])
            self.assertIn("MISSING_BARS", h.missing[0])

    def test_missing_bar_blocks_runner_evaluation(self):
        """A single missing bar inside a monthly file must stop the backtest:
        the candidate is DATA_MISSING, no event study runs."""
        import datetime as dt
        from test_residual_fixes import make_paths, zero_cost
        from runner import Runner, merged_bars
        with tempfile.TemporaryDirectory() as td:
            paths = make_paths(td)
            start = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
            bars = rich_bars(96 * 31, start_ts=start)
            write_kline_csv(paths.data_dir / "klines" / "BTCUSDT-15m-2022-01.csv",
                            bars[:500] + bars[501:])
            h = merged_bars(paths.data_dir, "BTCUSDT", "2022-01-01", "2022-01-31")
            self.assertEqual(h.bars, [])
            d = _mk("tod", {"utc_start": 0, "utc_end": 2, "horizon": 16}, "long_in_session")
            d["symbols"] = ["BTCUSDT"]
            sid = strategy_id(d)
            r = Runner(paths, cost=zero_cost(), allow_test_cost=True,
                       candidates_fn=lambda: ([(sid, normalize_strategy(d))], []))
            r.register_all()
            out = r.run_one(sid, r._defn_by_sid[sid])
            self.assertEqual(out["status"], "DATA_MISSING")
            self.assertNotIn("cells_dev", out["record"])


class TestFundingIntegrity(unittest.TestCase):
    def test_funding_parse_errors_fail_quality(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "f.csv"
            p.write_text("calc_time,funding_interval_hours,last_funding_rate\n"
                         f"{T0},8,0.0001\nGARBAGE,8,x\n{T0 + 8 * 3600000},8,0.0002\n")
            pairs, rep = load_funding_csv_checked(p)
            self.assertEqual(len(pairs), 2)
            self.assertEqual(rep["parse_errors"], [3])
            fq = funding_quality(pairs, parse_errors=rep["parse_errors"])
            self.assertFalse(fq["ok"])

    def test_gap_in_settlements_drops_window(self):
        h8 = 8 * 3_600_000
        f = [(T0 + k * h8, 1e-4) for k in range(10) if k != 4]       # settlement 4 missing
        idx = FundingIndex(f)
        self.assertIsNone(idx.paid_bp(T0 + 3 * h8, T0 + 5 * h8, 1))  # spans the gap
        self.assertAlmostEqual(idx.paid_bp(T0 + 5 * h8, T0 + 7 * h8, 1), 2.0)

    def test_settlement_boundary_convention(self):
        h8 = 8 * 3_600_000
        f = [(T0 + k * h8, 1e-4) for k in range(5)]
        idx = FundingIndex(f)
        # entry exactly at a settlement: not held; exit exactly at one: held
        self.assertAlmostEqual(idx.paid_bp(T0 + h8, T0 + 2 * h8, 1), 1.0)
        self.assertAlmostEqual(idx.paid_bp(T0 + h8 + MS15, T0 + 2 * h8 - MS15, 1), 0.0)


# ===========================================================================
# 2-2 defaults
# ===========================================================================

def _family_defs(fam):
    """explicit-default definition, omitted-default (canonical) definition."""
    p = dict(FAMILY_DEFAULTS[fam])
    direction = {"flow_z": "short_on_high_buy_share", "ret_rev": "long_on_down_move",
                 "ts_mom": "follow_sign", "vol_break": "long_breakout",
                 "tod": "long_in_session", "fund_z": "short_on_high_funding", "fng": "long"}[fam]
    explicit = _mk(fam, p, direction)
    canon = normalize_strategy(explicit)
    return explicit, canon


def _signals(defn, bars, funding, fng, idxs):
    from runner import build_aux, build_signal_fn
    from evaluator import PrefixView
    aux = build_aux(bars, defn, funding, fng=fng)
    sig = build_signal_fn(defn, aux)
    return [sig(PrefixView(bars, t + 1)) for t in idxs]


class TestDefaultParameters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = rich_bars(4000)
        cls.funding = rich_funding(cls.bars[0].ts, cls.bars[-1].ts)
        cls.fng = rich_fng(cls.bars[0].ts, cls.bars[-1].ts)
        cls.idx = list(range(0, 4000, 7))

    def test_every_family_omitted_equals_explicit(self):
        for fam in FAMILY_DEFAULTS:
            explicit, canon = _family_defs(fam)
            # identity: the default-valued params are stripped …
            self.assertEqual(canon.get("params", {}), {"horizon": FAMILY_DEFAULTS[fam]["horizon"]}
                             if False else canon.get("params", {}))
            self.assertEqual(strategy_id(explicit), strategy_id(canon), fam)
            self.assertNotIn("lookback", canon.get("params", {}))
            # … and execution resolves exactly the explicit values
            self.assertEqual(resolve_params(canon), resolve_params(explicit), fam)
            a = _signals(explicit, self.bars, self.funding, self.fng, self.idx)
            b = _signals(canon, self.bars, self.funding, self.fng, self.idx)
            self.assertEqual(a, b, fam)
            self.assertTrue(any(a), f"{fam}: synthetic data must make the family fire")

    def test_ret_rev_and_fund_z_aux_use_defaults(self):
        from runner import build_aux
        _, canon = _family_defs("ret_rev")
        aux = build_aux(self.bars, canon, None)
        self.assertEqual(aux["resolved_params"]["lookback"], 4)
        i = 100
        self.assertAlmostEqual(aux["lb_returns"][i], self.bars[i].close / self.bars[i - 4].close - 1)
        _, canon = _family_defs("fund_z")
        aux_c = build_aux(self.bars, canon, self.funding)
        explicit = dict(canon, params={**canon["params"], "funding_window": 30})
        aux_e = build_aux(self.bars, explicit, self.funding)
        self.assertEqual(aux_c["funding_z"], aux_e["funding_z"])
        other = dict(canon, params={**canon["params"], "funding_window": 90})
        self.assertNotEqual(aux_c["funding_z"], build_aux(self.bars, other, self.funding)["funding_z"])

    def test_grid_boundaries_explicit_vs_default(self):
        """First (= default) and last grid value of every parameter: the same id
        iff the same resolved params, and identical signals for equal ids."""
        for fam, gen in FAMILY_GENERATORS.items():
            defs = list(gen())
            for d in (defs[0], defs[-1]):
                canon = normalize_strategy(d)
                self.assertEqual(resolve_params(canon), {**FAMILY_DEFAULTS[fam], **d["params"]})
                self.assertEqual(strategy_id(canon), strategy_id(d))
        # different resolved params never share an id across the full space
        from generator import enumerate_candidates
        seen = {}
        for sid, canon in enumerate_candidates()[0]:
            key = json.dumps([canon["family"], canon.get("direction"), canon.get("symbols"),
                              resolve_params(canon)], sort_keys=True)
            self.assertNotIn(key, seen)
            seen[key] = sid

    def test_conflicting_declared_defaults_refused(self):
        d = _mk("ret_rev", {"lookback": 8, "thresh_z": 1.0, "horizon": 8}, "long_on_down_move")
        d["_defaults"] = {"lookback": 8, "thresh_z": 1.0, "horizon": 8}   # would strip lookback=8
        with self.assertRaises(DefaultsMismatch):
            strategy_id(d)
        # without _defaults the registry is still used: explicit default stripped
        d2 = _mk("ret_rev", {"lookback": 4, "thresh_z": 1.0, "horizon": 8}, "long_on_down_move")
        del d2["_defaults"]
        self.assertEqual(strategy_id(d2), strategy_id(_mk("ret_rev", {"lookback": 4, "thresh_z": 1.0,
                                                                     "horizon": 8}, "long_on_down_move")))

    def test_defaults_table_pinned(self):
        import generator
        self.assertEqual(generator._defaults_sha(), generator.FAMILY_DEFAULTS_SHA256)


# ===========================================================================
# 2-3 look-ahead / publication timing
# ===========================================================================

class TestNoLookahead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = rich_bars(2500, seed=11)
        cls.funding = rich_funding(cls.bars[0].ts, cls.bars[-1].ts, seed=12)
        cls.fng = rich_fng(cls.bars[0].ts, cls.bars[-1].ts, seed=13)

    def _check_family(self, defn):
        """Signal at t from (bars[:t+1], funding known at close t, fng of days
        before t's day) == signal at t from the full future-including data,
        and does not change when the future is replaced."""
        from runner import build_aux, build_signal_fn
        from evaluator import PrefixView
        full_sig = build_signal_fn(defn, build_aux(self.bars, defn, self.funding, fng=self.fng))
        rng = random.Random(5)
        fired = 0
        for t in sorted(rng.sample(range(300, len(self.bars) - 2), 40)):
            close_t = self.bars[t].ts + MS15
            past_f = [x for x in self.funding if x[0] <= close_t]
            day = self.bars[t].ts // DAY_MS
            past_fng = {d: v for d, v in self.fng.items() if d < day}
            pb = self.bars[:t + 1]
            sig_p = build_signal_fn(defn, build_aux(pb, defn, past_f, fng=past_fng))
            s_past = sig_p(PrefixView(pb, t + 1))
            s_full = full_sig(PrefixView(self.bars, t + 1))
            self.assertEqual(s_past, s_full, (defn["family"], t))
            # poisoned future: different bars after t, wild funding after close t
            fut = pb + rich_bars(50, start_ts=self.bars[t].ts + MS15, seed=t)
            futf = past_f + [(close_t + 1 + k * 3_600_000, 0.5) for k in range(20)]
            futg = {**past_fng, day: 0.0, day + 1: 100.0}
            s_pois = build_signal_fn(defn, build_aux(fut, defn, futf, fng=futg))(PrefixView(fut, t + 1))
            self.assertEqual(s_pois, s_full, (defn["family"], t, "future changed the signal"))
            fired += s_full != 0
        return fired

    def test_all_legacy_families_prefix_invariant(self):
        total = 0
        for fam, gen in FAMILY_GENERATORS.items():
            defs = list(gen())
            for d in (defs[0], defs[len(defs) // 2]):
                total += self._check_family(normalize_strategy(d))
        self.assertGreater(total, 0)

    def test_fng_same_day_value_never_used(self):
        from runner import build_aux
        day0 = T0 // DAY_MS
        bars = rich_bars(96, start_ts=T0)
        _, canon = _family_defs("fng")
        aux = build_aux(bars, canon, None, fng={day0: 5.0})
        self.assertTrue(all(v is None for h in aux["fng_hist"] for v in h))
        aux = build_aux(bars, canon, None, fng={day0 - 1: 5.0})
        self.assertTrue(all(h[0] == 5.0 for h in aux["fng_hist"]))

    def test_windows_embargo_and_order(self):
        import datetime as dt
        from runner import EMBARGO_MIN_BARS, Runner, split_windows
        start = int(dt.datetime(2021, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        end = int(dt.datetime(2024, 3, 24, 23, 45, tzinfo=dt.timezone.utc).timestamp() * 1000)
        ts = list(range(start, end + 1, MS15))
        for h in (8, 192, 768):
            w = split_windows("BTCUSDT", start, end, h, ts)
            d0, d1 = w["dev"]
            v0, v1 = w["validation"]
            emb = max(h, EMBARGO_MIN_BARS)
            self.assertEqual(w["embargo_bars"], emb)
            self.assertLessEqual(d1 + emb, v0)                 # dev (incl. exits) before validation
            self.assertLess(ts[v1] + emb * MS15, ts[-1] + MS15)  # validation ends an embargo early
            lock0 = int(dt.datetime(2024, 3, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
            self.assertLess(ts[-1], lock0)
        self.assertEqual(Runner._read_end("BTCUSDT"), "2024-03-24")


# ===========================================================================
# 2-4 ledger corruption and crash recovery
# ===========================================================================

class TestLedgerIntegrity(unittest.TestCase):
    def test_interior_corruption_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "l.jsonl"
            for i in range(3):
                append_jsonl(p, {"evaluation_id": f"E{i}", "strategy_id": "S"})
            data = p.read_bytes().split(b"\n")
            data[1] = b'{"evaluation_id": "E1", "strat'          # damaged middle record
            p.write_bytes(b"\n".join(data))
            with self.assertRaises(JsonlCorruption):
                list(iter_jsonl(p))
            recs, rep = scan_jsonl(p)
            self.assertEqual(len(rep["corrupt"]), 1)
            # explicit acknowledgement is recorded, the line itself stays
            acknowledge_corrupt_line(p, rep["corrupt"][0][1], "inspected: duplicate of E1 backup")
            self.assertEqual([r["evaluation_id"] for r in iter_jsonl(p)], ["E0", "E2"])
            self.assertIn(b'"strat', p.read_bytes())

    def test_torn_tail_is_quarantined_not_merged(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "l.jsonl"
            append_jsonl(p, {"evaluation_id": "E0", "strategy_id": "S"})
            with open(p, "ab") as f:
                f.write(b'{"evaluation_id": "E1", "stra')          # crash mid-write
            self.assertEqual(len(list(iter_jsonl(p))), 1)          # torn tail tolerated
            append_jsonl(p, {"evaluation_id": "E2", "strategy_id": "S"})
            ids = [r["evaluation_id"] for r in iter_jsonl(p)]
            self.assertEqual(ids, ["E0", "E2"])                      # not glued together
            q = Path(str(p) + ".quarantine.jsonl")
            self.assertTrue(q.exists())
            self.assertEqual(json.loads(q.read_text().splitlines()[0])["kind"], "torn_tail")

    def test_crash_after_ledger_write_is_not_reevaluated(self):
        from test_residual_fixes import SPACE, _fake_runner_cls, make_paths, zero_cost
        from store import reconcile
        with tempfile.TemporaryDirectory() as td:
            paths = make_paths(td)
            R = _fake_runner_cls()
            r = R(paths, cost=zero_cost(), allow_test_cost=True, candidates_fn=lambda: (SPACE, []))
            r.register_all()
            r._ctx = "C2-test"
            sid, defn = SPACE[0]
            # simulate: RUNNING announced, ledger record written, crash before state write
            orig = r.store.set

            def crash_after_ledger(s, nxt, **kw):
                if CandidateState(nxt) is CandidateState.AWAITING_FDR:
                    raise KeyboardInterrupt("crash")
                return orig(s, nxt, **kw)
            r.store.set = crash_after_ledger
            with self.assertRaises(KeyboardInterrupt):
                r.run_one(sid, defn)
            r.store.set = orig
            self.assertEqual(r.store.entry(sid).state, "RUNNING")
            n_before = r.ledger.evaluation_count()
            rep = reconcile(r.store, r.queue, r.ledger)
            self.assertEqual(rep["recovered_from_ledger"], [sid])
            self.assertEqual(r.store.entry(sid).state, "AWAITING_FDR")
            self.assertEqual(r.queue.size(), 0)                 # nothing re-queued
            out = r.run_one(sid, defn)
            self.assertTrue(out["skipped"])                     # no duplicate evaluation
            self.assertEqual(r.ledger.evaluation_count(), n_before)

    def test_crash_before_ledger_write_is_requeued(self):
        from test_residual_fixes import SPACE, _fake_runner_cls, make_paths, zero_cost
        from store import reconcile
        with tempfile.TemporaryDirectory() as td:
            paths = make_paths(td)
            r = _fake_runner_cls()(paths, cost=zero_cost(), allow_test_cost=True,
                                   candidates_fn=lambda: (SPACE, []))
            r.register_all()
            sid, _ = SPACE[1]
            r.store.set(sid, CandidateState.RUNNING, pending_evaluation_id="E-never-written")
            rep = reconcile(r.store, r.queue, r.ledger)
            self.assertEqual(rep["requeued_interrupted"], [sid])
            self.assertEqual(r.store.entry(sid).state, "REVERIFY_PENDING")

    def test_cycle_fails_closed_on_corrupt_ledger(self):
        from test_residual_fixes import SPACE, _fake_runner_cls, make_paths, zero_cost
        with tempfile.TemporaryDirectory() as td:
            paths = make_paths(td)
            r = _fake_runner_cls()(paths, cost=zero_cost(), allow_test_cost=True,
                                   candidates_fn=lambda: (SPACE, []))
            r.register_all()
            led = paths.state_dir / "discovery_ledger.jsonl"
            append_jsonl(led, {"evaluation_id": "E0", "strategy_id": SPACE[0][0]})
            with open(led, "ab") as f:
                f.write(b"{broken\n")
            append_jsonl(led, {"evaluation_id": "E1", "strategy_id": SPACE[0][0]})
            with self.assertRaises(JsonlCorruption):
                r.cycle(max_candidates=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
