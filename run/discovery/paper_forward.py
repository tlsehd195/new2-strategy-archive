"""Forward paper observation of PAPER_TEST_READY strategies — no orders.

Difference to ``paper.py``: ``paper.py`` replays STORED bars that already
existed when the strategy was promoted (post-locked-window history).  This
module observes bars that did NOT exist yet when the plan was registered:
every plan starts at the first 15-minute boundary after its registration
and after the newest stored bar, and only bars that close later are ever
evaluated.  It is research simulation, not trading: no exchange client, no
API key, no order endpoint is imported or called.  A feed may use the
network only for public market data and only when explicitly allowed.

Per plan (write-once, ``forward/plans.jsonl``): strategy, context, symbols,
start, preregistered criteria (``config/paper_forward_v1.json`` + hash),
execution rules, cost identity, order value.

Per step: new CLOSED bars are pulled from the feed (an unfinished bar is
never used), checked (duplicates skipped and counted, gaps counted,
off-grid / structurally bad bars stop the plan as BLOCKED_DATA), appended
to a hash-chained bar log with the wall-clock time they were observed.  The
strategy's own causal signal and the research cost/funding rules are then
re-run over warm-up + observed bars.  Closed trades are logged once
(event id = symbol + entry time); a later run that produces a different
value for an already logged trade, or loses one, is a CAUSALITY_VIOLATION
(the plan stops with ERROR).  Execution rules (``execution_sim``) add
partial fills / refusals, liquidation and observation latency.  The state
file is written atomically, so a killed process resumes at the last
completed step and never logs a trade twice.

Verdict (preregistered, never relaxed after observation):
* ``PAPER_OBSERVING``   — fewer than ``min_days`` or ``min_trades``; status stays PAPER_TESTING
* ``PAPER_INCONCLUSIVE``— ``max_days`` reached without enough trades -> INCONCLUSIVE
* ``PAPER_PASSED``      — every criterion met (mean > 0, one-sided HAC p <= alpha,
  positive at the stressed cost, drawdown, data coverage, no liquidation,
  latency for real-time feeds) -> PAPER_PASSED
* ``PAPER_FAILED``      — enough observation, a criterion failed -> PAPER_FAILED
* ``PAPER_BLOCKED_DATA`` / ``PAPER_ERROR`` — no status change, reasons recorded

PAPER_PASSED is a research result.  It is not an approval to trade and it
says nothing about profits outside the observed period.  Forward results
are never read by the search (``search.dev_feedback`` accepts DEV only).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from data_quality import Bar, check_bars
from engine_lib import (
    append_jsonl, atomic_write_json, canonical_json, iso_now, iter_jsonl, nw_tstat, t_sf,
)
from evaluator import PrefixView, run_event_study
from execution_sim import EXECUTION_SIM_VERSION, ExecutionConfig, apply_execution
from strategy_spec import build_spec_aux, build_spec_signal, canonicalize_spec, stop_exit_rule

FORWARD_MODE = "forward_observation_no_orders"
FORWARD_VERSION = "paper-forward-v1"
DEFAULT_CRITERIA = Path(__file__).resolve().parent / "config" / "paper_forward_v1.json"
MS15 = 900_000
DAY_MS = 86_400_000
GAP_EMBARGO_BARS = 96


class ForwardRefused(RuntimeError):
    pass


class CausalityViolation(RuntimeError):
    pass


def load_criteria(path: Optional[Path] = None) -> tuple[dict, str]:
    p = Path(path or DEFAULT_CRITERIA)
    raw = p.read_bytes()
    c = json.loads(raw)
    if c.get("schema") != "paper_forward_criteria_v1":
        raise ValueError(f"{p}: not a paper_forward_criteria_v1 file")
    return c, hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# feeds
# ---------------------------------------------------------------------------

class FileFeed:
    """Closed bars / funding settlements from files in ``data_dir`` (the same
    layout as the research data: ``klines/<SYM>-15m-*.csv``,
    ``funding/<SYM>-fundingRate-*.csv``; monthly or daily public archives).
    Archive files are written after the fact, so this feed is NOT real-time:
    observation latency is not meaningful and the verdict says so."""

    realtime = False
    kind = "public_archive_files"

    def __init__(self, data_dir: Path, *, clock: Callable[[], int] = None):
        self.data_dir = Path(data_dir)
        self.clock = clock or (lambda: int(time.time() * 1000))

    def _files(self, sub: str, pattern: str) -> list[Path]:
        d = self.data_dir / sub
        return sorted(d.glob(pattern)) if d.exists() else []

    def closed_bars(self, sym: str, after_ts: int) -> tuple[list[Bar], dict]:
        from data_quality import load_kline_csv
        now = self.clock()
        out: dict[int, Bar] = {}
        info = {"files": 0, "blocked_files": [], "incomplete_skipped": 0, "duplicates_across_files": 0}
        for p in self._files("klines", f"{sym}-15m-*.csv"):
            bars, rep = load_kline_csv(p, sym)
            info["files"] += 1
            if not rep.ok:
                info["blocked_files"].append({"file": p.name, "codes": rep.blocking_codes()})
                continue
            for b in bars:
                if b.ts <= after_ts:
                    continue
                if b.ts + MS15 > now:
                    info["incomplete_skipped"] += 1
                    continue
                if b.ts in out:
                    info["duplicates_across_files"] += 1
                    continue
                out[b.ts] = b
        return [out[k] for k in sorted(out)], info

    def funding(self, sym: str, after_ts: int) -> list[tuple[int, float]]:
        from data_quality import load_funding_csv_checked
        now = self.clock()
        out = {}
        for p in self._files("funding", f"{sym}-fundingRate-*.csv"):
            pairs, rep = load_funding_csv_checked(p)
            if rep["parse_errors"] or rep["torn_tail"] or rep["source_order_errors"]:
                # a file with unparsable rows or rows out of SOURCE order (ADR 0072) is not used
                continue
            for t, r in pairs:
                if after_ts < t <= now:
                    out[t] = r
        return sorted(out.items())


class BinancePublicFeed:  # pragma: no cover - network; exercised only with explicit opt-in
    """Public USD-M endpoints (``/fapi/v1/klines``, ``/fapi/v1/fundingRate``):
    market data only, no key, no account, no order endpoint.  Disabled
    unless ``allow_network=True``.  Bounded retries with exponential backoff."""

    realtime = True
    kind = "binance_public_rest"
    BASE = "https://fapi.binance.com"

    def __init__(self, *, allow_network: bool = False, clock: Callable[[], int] = None,
                 attempts: int = 4, opener=None, sleep=time.sleep):
        if not allow_network:
            raise ForwardRefused("network feed refused: pass allow_network=True explicitly")
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.attempts, self.sleep = attempts, sleep
        self.opener = opener

    def _get(self, path: str) -> list:
        from data_fetch import fetch_with_retry
        blob = fetch_with_retry(self.BASE + path, opener=self.opener, attempts=self.attempts,
                                sleep=self.sleep)
        return json.loads(blob)

    def closed_bars(self, sym: str, after_ts: int) -> tuple[list[Bar], dict]:
        now = self.clock()
        rows = self._get(f"/fapi/v1/klines?symbol={sym}&interval=15m&startTime={after_ts + 1}&limit=1500")
        out, skipped = [], 0
        for r in rows:
            if int(r[6]) >= now:          # close time not reached: unfinished bar
                skipped += 1
                continue
            out.append(Bar.from_row([str(x) for x in r]))
        return out, {"incomplete_skipped": skipped, "rows": len(rows)}

    def funding(self, sym: str, after_ts: int) -> list[tuple[int, float]]:
        rows = self._get(f"/fapi/v1/fundingRate?symbol={sym}&startTime={after_ts + 1}&limit=1000")
        return [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows
                if int(r["fundingTime"]) <= self.clock()]


# ---------------------------------------------------------------------------
# bar log helpers
# ---------------------------------------------------------------------------

def _bar_row(b: Bar) -> dict:
    return {"ts": b.ts, "o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume,
            "qv": b.quote_volume, "tb": b.taker_buy_volume}


def _row_bar(r: Mapping) -> Bar:
    return Bar(ts=r["ts"], open=r["o"], high=r["h"], low=r["l"], close=r["c"], volume=r["v"],
               quote_volume=r["qv"], taker_buy_volume=r["tb"])


def _chain(prev: str, row: Mapping) -> str:
    return hashlib.sha256((prev + canonical_json(row)).encode()).hexdigest()


def read_bar_log(path: Path) -> list[dict]:
    """Rows of a hash-chained bar log; a broken chain raises (fail-closed)."""
    rows, prev = [], ""
    for r in iter_jsonl(path):
        body = {k: v for k, v in r.items() if k not in ("hash", "prev")}
        if r.get("prev") != prev or r.get("hash") != _chain(prev, body):
            raise CausalityViolation(f"{path}: bar log chain broken at ts={r.get('ts')}")
        rows.append(r)
        prev = r["hash"]
    return rows


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------

class ForwardPaper:
    def __init__(self, pipeline, feed, *, criteria_path: Optional[Path] = None,
                 clock: Optional[Callable[[], int]] = None):
        self.p = pipeline
        self.feed = feed
        self.crit, self.crit_hash = load_criteria(criteria_path)
        self.exec_cfg = ExecutionConfig(**self.crit["execution"])
        self.exec_cfg.validate()
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.root = Path(pipeline.dir) / "forward"
        self.root.mkdir(parents=True, exist_ok=True)
        self.plans_path = self.root / "plans.jsonl"

    # -- plans ----------------------------------------------------------------
    def plans(self) -> list[dict]:
        return list(iter_jsonl(self.plans_path))

    def plan(self, plan_id: str) -> dict:
        for r in self.plans():
            if r["plan_id"] == plan_id:
                return r
        raise KeyError(plan_id)

    def register_plan(self, sid: str) -> dict:
        ctx = self.p.context_id()
        st = self.p.statuses.status(sid, ctx)
        if st not in ("PAPER_TEST_READY", "PAPER_TESTING"):
            raise ForwardRefused(f"{sid} is {st}: forward paper needs PAPER_TEST_READY/PAPER_TESTING")
        spec = canonicalize_spec(self.p.specs.get(sid))
        ok, why = self.p.cost.verified_for(spec["symbols"], self.p.order_value)
        if not ok:
            raise ForwardRefused(f"cost model not verified for this use: {why}")
        for r in self.plans():
            if r["strategy_id"] == sid and r["context_id"] == ctx:
                raise ForwardRefused(f"{sid}: a forward plan already exists in this context ({r['plan_id']})")
        now = self.clock()
        newest = 0
        for sym in spec["symbols"]:
            for src in (self.p.provider.symbol(sym), self.p.provider.paper(sym)):
                if src.get("bars"):
                    newest = max(newest, src["bars"][-1].ts)
        start = max(-(-now // MS15) * MS15, newest + MS15)
        body = {"strategy_id": sid, "context_id": ctx, "symbols": spec["symbols"], "start_ts": start,
                "registered_ms": now, "criteria": self.crit, "criteria_hash": self.crit_hash,
                "execution": self.exec_cfg.to_json(), "execution_version": EXECUTION_SIM_VERSION,
                "cost": self.p.cost.identity(), "order_value": self.p.order_value,
                "spec_sha256": hashlib.sha256(canonical_json(spec).encode()).hexdigest(),
                "feed": self.feed.kind, "feed_realtime": bool(self.feed.realtime),
                "mode": FORWARD_MODE, "version": FORWARD_VERSION, "orders_sent": 0}
        body["plan_id"] = "FP-" + hashlib.sha256(canonical_json(body).encode()).hexdigest()[:16]
        body["at"] = iso_now()
        d = self.root / body["plan_id"]
        d.mkdir(parents=True, exist_ok=True)
        # warm-up: history strictly before the start (features only, never evaluated)
        for sym in spec["symbols"]:
            bars, _ = self.feed.closed_bars(sym, start - (self.crit["warmup_bars"] + 1) * MS15)
            warm = [b for b in bars if b.ts < start]
            tail = []                              # keep the contiguous tail ending at start-15m
            for b in reversed(warm):
                if (tail and tail[-1].ts - b.ts != MS15) or (not tail and b.ts != start - MS15):
                    break
                tail.append(b)
            tail.reverse()
            wf = [x for x in self.feed.funding(sym, start - 30 * DAY_MS) if x[0] < start]
            atomic_write_json(d / f"warmup_{sym}.json", {"rows": [_bar_row(b) for b in tail],
                                                         "funding": [list(x) for x in wf],
                                                         "contiguous_to_start": bool(tail)})
        append_jsonl(self.plans_path, body)
        if st == "PAPER_TEST_READY":
            self.p.statuses.set(sid, ctx, "PAPER_TESTING", stage="paper_forward",
                                reasons=[f"forward observation plan {body['plan_id']} registered"])
        self._save_state(body["plan_id"], self._new_state(body))
        return body

    # -- state ------------------------------------------------------------------
    def _new_state(self, plan: Mapping) -> dict:
        return {"plan_id": plan["plan_id"], "status": "OBSERVING", "steps": 0,
                "last_ts": {s: plan["start_ts"] - MS15 for s in plan["symbols"]},
                "last_funding_ts": {s: plan["start_ts"] - 8 * 3_600_000 for s in plan["symbols"]},
                "bars": {s: 0 for s in plan["symbols"]}, "missing_bars": {s: 0 for s in plan["symbols"]},
                "duplicates": {s: 0 for s in plan["symbols"]}, "gaps": {s: [] for s in plan["symbols"]},
                "incomplete_skipped": 0, "errors": [], "verdict": None, "concluded": False}

    def _state_path(self, plan_id: str) -> Path:
        return self.root / plan_id / "state.json"

    def state(self, plan_id: str) -> dict:
        return json.loads(self._state_path(plan_id).read_text())

    def _save_state(self, plan_id: str, st: Mapping) -> None:
        atomic_write_json(self._state_path(plan_id), dict(st))

    # -- one step -----------------------------------------------------------------
    def step(self, plan_id: str) -> dict:
        plan = self.plan(plan_id)
        st = self.state(plan_id)
        if st["concluded"]:
            return {"plan_id": plan_id, "status": "CONCLUDED", "verdict": st["verdict"]}
        d = self.root / plan_id
        now = self.clock()
        new_counts = {}
        if st["status"] == "OBSERVING":
            drift = self._plan_drift(plan)
            if drift:
                # the plan's preregistered spec / cost / execution rules are what is
                # being observed: anything else would be a different experiment
                st["status"] = "ERROR"
                st["errors"].append({"at": now, "kind": "PLAN_CONTEXT_CHANGED", "detail": "; ".join(drift)})
        for sym in plan["symbols"]:
            if st["status"] != "OBSERVING":
                break
            log = d / f"bars_{sym}.jsonl"
            try:
                rows = read_bar_log(log)
            except CausalityViolation as e:
                st["status"] = "ERROR"
                st["errors"].append({"at": now, "symbol": sym, "kind": "BAR_LOG_TAMPERED", "detail": str(e)})
                break
            self._recover_from_logs(plan, st, sym, rows)
            prev_hash = rows[-1]["hash"] if rows else ""
            bars, info = self.feed.closed_bars(sym, st["last_ts"][sym])
            bars = sorted(bars, key=lambda b: b.ts)          # out-of-order delivery is normalised
            st["incomplete_skipped"] += int(info.get("incomplete_skipped", 0))
            if info.get("blocked_files"):
                st["errors"].append({"at": now, "symbol": sym, "kind": "SOURCE_FILE_BLOCKED",
                                     "detail": info["blocked_files"]})
            added = 0
            for b in bars:
                if b.ts + MS15 > now:
                    st["incomplete_skipped"] += 1
                    continue
                if b.ts <= st["last_ts"][sym]:
                    st["duplicates"][sym] += 1
                    continue
                rep = check_bars([b], sym, exceptions=[])
                gap = b.ts - st["last_ts"][sym]
                if b.ts % MS15 or gap % MS15 or not rep.ok:
                    st["status"] = "BLOCKED_DATA"
                    st["errors"].append({"at": now, "symbol": sym, "kind": "BAD_BAR", "ts": b.ts,
                                         "codes": rep.blocking_codes() or ["MISALIGNED_TS"]})
                    break
                if gap > MS15:
                    miss = gap // MS15 - 1
                    st["missing_bars"][sym] += miss
                    st["gaps"][sym].append([st["last_ts"][sym], b.ts, miss])
                body = {**_bar_row(b), "observed_at": now, "delay_ms": now - (b.ts + MS15)}
                h = _chain(prev_hash, body)
                append_jsonl(log, {**body, "prev": prev_hash, "hash": h})
                prev_hash = h
                st["last_ts"][sym] = b.ts
                st["bars"][sym] += 1
                added += 1
            for t, r in self.feed.funding(sym, st["last_funding_ts"][sym]):
                if t <= st["last_funding_ts"][sym]:
                    continue
                append_jsonl(d / f"funding_{sym}.jsonl", {"ts": t, "rate": r, "observed_at": now})
                st["last_funding_ts"][sym] = t
            new_counts[sym] = added
        st["steps"] += 1
        result = {"plan_id": plan_id, "new_bars": new_counts, "status": st["status"]}
        if st["status"] == "OBSERVING":
            try:
                ev = self._evaluate(plan, st)
            except CausalityViolation as e:
                st["status"] = "ERROR"
                st["errors"].append({"at": now, "kind": "CAUSALITY_VIOLATION", "detail": str(e)})
                ev = None
            if ev is not None:
                result.update(ev)
        verdict = self._verdict(plan, st, result)
        result["verdict"] = verdict["verdict"]
        result["reasons"] = verdict["reasons"]
        st["verdict"] = verdict["verdict"]
        if verdict["final"]:
            self._conclude(plan, verdict, result)
            st["concluded"] = True
        self._save_state(plan_id, st)
        append_jsonl(d / "steps.jsonl", {"at": iso_now(), "clock_ms": now,
                                         **{k: v for k, v in result.items() if k != "trades"}})
        return result

    def _plan_drift(self, plan: Mapping) -> list[str]:
        """What differs between the registered plan and what a step would run."""
        why = []
        body = {k: v for k, v in plan.items() if k not in ("plan_id", "at")}
        if "FP-" + hashlib.sha256(canonical_json(body).encode()).hexdigest()[:16] != plan["plan_id"]:
            why.append("plan record does not match its id")
        spec = self.p.specs.get(plan["strategy_id"])
        if spec is None or hashlib.sha256(canonical_json(canonicalize_spec(spec)).encode()).hexdigest() \
                != plan["spec_sha256"]:
            why.append("strategy specification differs from the registered one")
        if canonical_json(self.p.cost.identity()) != canonical_json(plan["cost"]):
            why.append("cost model differs from the registered one")
        if plan.get("execution_version") != EXECUTION_SIM_VERSION:
            why.append(f"execution rules {EXECUTION_SIM_VERSION} != registered {plan.get('execution_version')}")
        if self.p.context_id() != plan["context_id"]:
            why.append("experiment context differs from the registered one")
        return why

    def _recover_from_logs(self, plan: Mapping, st: dict, sym: str, rows: Sequence[Mapping]) -> None:
        """A step killed after appending bars but before saving the state left
        the state behind the (hash-chained) bar log.  The log is the record:
        re-derive the counters from it so no bar is appended twice."""
        if not rows or rows[-1]["ts"] <= st["last_ts"][sym]:
            return
        prev, gaps, missing = plan["start_ts"] - MS15, [], 0
        for r in rows:
            g = r["ts"] - prev
            if g > MS15:
                gaps.append([prev, r["ts"], g // MS15 - 1])
                missing += g // MS15 - 1
            prev = r["ts"]
        st["last_ts"][sym], st["bars"][sym] = rows[-1]["ts"], len(rows)
        st["gaps"][sym], st["missing_bars"][sym] = gaps, missing
        fl = list(iter_jsonl(self.root / plan["plan_id"] / f"funding_{sym}.jsonl"))
        if fl:
            st["last_funding_ts"][sym] = max(st["last_funding_ts"][sym], max(r["ts"] for r in fl))
        st.setdefault("recovered", []).append({"symbol": sym, "last_ts": rows[-1]["ts"], "at": iso_now()})

    # -- evaluation ------------------------------------------------------------------
    def _series(self, plan: Mapping, sym: str) -> tuple[list[Bar], int, list[dict], list]:
        d = self.root / plan["plan_id"]
        wj = json.loads((d / f"warmup_{sym}.json").read_text())
        warm = [_row_bar(r) for r in wj["rows"]]
        obs = read_bar_log(d / f"bars_{sym}.jsonl")
        fund = [tuple(x) for x in wj.get("funding", [])]
        fund += [(r["ts"], r["rate"]) for r in iter_jsonl(d / f"funding_{sym}.jsonl")]
        return warm + [_row_bar(r) for r in obs], len(warm), obs, fund

    def _evaluate(self, plan: Mapping, st: Mapping) -> dict:
        spec = canonicalize_spec(self.p.specs.get(plan["strategy_id"]))
        # the plan's own (registered) rules, never the current config files
        exec_cfg = ExecutionConfig(**plan["execution"])
        mp = float(plan["cost"]["params"]["max_participation"])
        trades, open_pos, excluded = [], [], {"gap": 0, "no_funding_coverage": 0}
        for sym in plan["symbols"]:
            bars, w, obs, fund = self._series(plan, sym)
            if len(bars) - w < 2:
                continue
            gaps = [(a, b) for a, b, _ in st["gaps"][sym]]
            fng = self.p.provider.fng() if "fng" in spec["data"] else None
            funding = sorted(set(fund))
            aux = build_spec_aux(bars, spec, funding or None, fng)
            sig = build_spec_signal(spec, aux)
            res = run_event_study(bars, sym, sig, spec["exit"]["horizon"], self.p.cost,
                                  plan["order_value"], funding or None, warmup=max(1, w),
                                  exit_rule=stop_exit_rule(spec))
            excluded["no_funding_coverage"] += res.dropped_missing_funding
            delay = {r["ts"]: r["delay_ms"] for r in obs}
            for e in res.events:
                x = e.to_json()
                if x["entry_ts"] < plan["start_ts"]:
                    continue
                if any(a < x["exit_ts"] and b > x["entry_ts"] - GAP_EMBARGO_BARS * MS15 for a, b in gaps):
                    excluded["gap"] += 1
                    continue
                decision_ts = bars[x["entry_index"] - 1].ts
                y = apply_execution(x, bars, exec_cfg, order_value=plan["order_value"],
                                    max_participation=mp, observed_delay_ms=delay.get(decision_ts))
                y["event_id"] = f"{sym}:{x['entry_ts']}"
                y["holding_minutes"] = (x["exit_ts"] - x["entry_ts"]) / 60_000
                trades.append(y)
            # open position (decided, not yet exited): marked to the last close
            last_exit = max([e.exit_index for e in res.events] + [w - 1])
            for t in range(max(w, last_exit), len(bars) - 1):
                side = sig(PrefixView(bars, t + 1))
                if side in (1, -1):
                    ent = t + 1
                    if ent + spec["exit"]["horizon"] >= len(bars):
                        u = side * (bars[-1].close / bars[ent].open - 1.0) * 1e4
                        open_pos.append({"symbol": sym, "entry_ts": bars[ent].ts, "side": side,
                                         "unrealized_gross_bp": u, "marked_at": bars[-1].ts,
                                         "note": "approximate mark-to-market (costs at exit not yet known)"})
                    break
        self._reconcile_trades(plan, trades)
        return {"trades": trades, "open_positions": open_pos, "excluded": excluded}

    def _reconcile_trades(self, plan: Mapping, trades: Sequence[Mapping]) -> None:
        path = self.root / plan["plan_id"] / "events.jsonl"
        logged = {r["event_id"]: r for r in iter_jsonl(path)}
        now = {t["event_id"]: t for t in trades}
        for eid, r in logged.items():
            t = now.get(eid)
            if t is None:
                raise CausalityViolation(f"logged trade {eid} is no longer produced by the strategy")
            for k in ("side", "entry_ts", "exit_ts"):
                if t[k] != r[k]:
                    raise CausalityViolation(f"logged trade {eid} changed {k}: {r[k]} -> {t[k]}")
            if abs(t["net_bp_exec"] - r["net_bp_exec"]) > 1e-9:
                raise CausalityViolation(f"logged trade {eid} changed result")
        for eid in sorted(set(now) - set(logged)):
            append_jsonl(path, {**now[eid], "logged_at": iso_now()})

    def metrics(self, plan_id: str, st: Optional[Mapping] = None) -> dict:
        plan = self.plan(plan_id)
        st = st if st is not None else self.state(plan_id)
        trades = list(iter_jsonl(self.root / plan_id / "events.jsonl"))
        counted = [t for t in trades if t["exec_status"] in ("FILLED", "PARTIAL", "LIQUIDATED")]
        if plan["feed_realtime"]:
            counted_v = [t for t in counted if not t["late"]]
        else:
            counted_v = counted
        nets = [t["net_bp_exec"] for t in sorted(counted_v, key=lambda t: t["exit_ts"])]
        n = len(nets)
        mean = sum(nets) / n if n else None
        tstat = nw_tstat(nets)[0] if n >= 3 else float("nan")
        pval = t_sf(tstat, n - 1) if n >= 3 and math.isfinite(tstat) else None
        mult = plan["criteria"]["require_positive_at_cost_multiplier"]
        stress = [t["net_bp_exec"] - (mult - 1.0) * t["cost_bp"] * t["fill_fraction"] for t in counted_v]
        cum = peak = mdd = 0.0
        daily: dict[int, float] = {}
        for t, x in zip(sorted(counted_v, key=lambda t: t["exit_ts"]), nets):
            cum += x
            peak = max(peak, cum)
            mdd = max(mdd, peak - cum)
            daily[t["exit_ts"] // DAY_MS] = daily.get(t["exit_ts"] // DAY_MS, 0.0) + x
        last = min(st["last_ts"].values())
        days = max(0.0, (last + MS15 - plan["start_ts"]) / DAY_MS)
        expected = sum(max(0, (st["last_ts"][s] - plan["start_ts"]) // MS15 + 1) for s in plan["symbols"])
        missing = sum(st["missing_bars"].values())
        return {"n_trades": n, "mean_net_bp": mean, "hac_t": tstat if math.isfinite(tstat) else None,
                "hac_p_one_sided": pval, "stressed_mean_bp": (sum(stress) / len(stress)) if stress else None,
                "mdd_bp": mdd, "days_observed": days, "missing_bar_share": (missing / expected) if expected else None,
                "liquidations": sum(1 for t in counted if t["liquidated"]),
                "rejected_orders": sum(1 for t in trades if t["exec_status"].startswith("REJECTED")),
                "late_share": (sum(1 for t in counted if t["late"]) / len(counted)) if counted else None,
                "mean_holding_minutes": (sum(t["holding_minutes"] for t in counted) / len(counted)) if counted else None,
                "daily_net_bp": {str(k): v for k, v in sorted(daily.items())},
                "bars_observed": dict(st["bars"]), "duplicates_skipped": dict(st["duplicates"]),
                "incomplete_skipped": st["incomplete_skipped"]}

    def _verdict(self, plan: Mapping, st: Mapping, result: Mapping) -> dict:
        c = plan["criteria"]
        if st["status"] == "ERROR":
            return {"verdict": "PAPER_ERROR", "final": False, "reasons": [e["detail"] for e in st["errors"][-1:]]}
        if st["status"] == "BLOCKED_DATA":
            return {"verdict": "PAPER_BLOCKED_DATA", "final": False,
                    "reasons": [json.dumps(e, default=str) for e in st["errors"][-1:]]}
        m = self.metrics(plan["plan_id"], st)
        result_m = dict(m)
        result.setdefault("metrics", result_m)
        if m["days_observed"] < c["min_days"] or m["n_trades"] < c["min_trades"]:
            if m["days_observed"] >= c["max_days"]:
                return {"verdict": "PAPER_INCONCLUSIVE", "final": True,
                        "reasons": [f"{m['n_trades']} trades after {m['days_observed']:.1f} days "
                                    f"(< {c['min_trades']} by the {c['max_days']}-day limit)"]}
            return {"verdict": "PAPER_OBSERVING", "final": False,
                    "reasons": [f"{m['days_observed']:.1f}/{c['min_days']} days, "
                                f"{m['n_trades']}/{c['min_trades']} trades"]}
        fails = []
        if not (m["mean_net_bp"] is not None and m["mean_net_bp"] > 0):
            fails.append("mean net return after costs <= 0")
        if m["hac_p_one_sided"] is None or m["hac_p_one_sided"] > c["alpha"]:
            fails.append(f"one-sided HAC p {m['hac_p_one_sided']} > {c['alpha']}")
        if not (m["stressed_mean_bp"] is not None and m["stressed_mean_bp"] > 0):
            fails.append(f"not positive at {c['require_positive_at_cost_multiplier']}x cost")
        if m["mdd_bp"] > c["max_mdd_bp"]:
            fails.append(f"drawdown {m['mdd_bp']:.0f}bp > {c['max_mdd_bp']}bp")
        if m["missing_bar_share"] is not None and m["missing_bar_share"] > c["max_missing_bar_share"]:
            fails.append(f"missing bar share {m['missing_bar_share']:.4f} > {c['max_missing_bar_share']}")
        if m["liquidations"] > c["allow_liquidations"]:
            fails.append(f"{m['liquidations']} liquidations")
        if plan["feed_realtime"] and (m["late_share"] or 0) > c["max_late_trade_share"]:
            fails.append(f"late decision share {m['late_share']:.3f} > {c['max_late_trade_share']}")
        return {"verdict": "PAPER_FAILED" if fails else "PAPER_PASSED", "final": True, "reasons": fails}

    def _conclude(self, plan: Mapping, verdict: Mapping, result: Mapping) -> None:
        sid, ctx = plan["strategy_id"], plan["context_id"]
        to = {"PAPER_PASSED": "PAPER_PASSED", "PAPER_FAILED": "PAPER_FAILED",
              "PAPER_INCONCLUSIVE": "INCONCLUSIVE"}[verdict["verdict"]]
        labels = [] if plan["feed_realtime"] else ["OBSERVED_FROM_ARCHIVE_NOT_REALTIME"]
        prior = self.p.evaluation("paper_forward", sid, ctx)
        if prior is not None and prior.get("plan_id") == plan["plan_id"]:
            # concluded before an interruption (state not yet saved): never twice
            if self.p.statuses.status(sid, ctx) != to:
                self.p.statuses.set(sid, ctx, to, stage="paper_forward", labels=labels,
                                    reasons=list(prior.get("reasons") or []),
                                    evidence={"paper_forward": prior["record_id"]})
            return
        rec = self.p._append_eval({"stage": "paper_forward", "strategy_id": sid, "context_id": ctx,
                                   "status": "OK", "plan_id": plan["plan_id"], "verdict": verdict["verdict"],
                                   "reasons": list(verdict["reasons"]), "metrics": result.get("metrics"),
                                   "criteria_hash": plan["criteria_hash"], "labels": labels,
                                   "is_simulation": True, "orders_sent": 0})
        self.p.statuses.set(sid, ctx, to, stage="paper_forward", labels=labels,
                            reasons=list(verdict["reasons"]) + [
                                "forward paper is a research result — never approval for live trading"],
                            evidence={"paper_forward": rec["record_id"]})
        append_jsonl(self.p.dir / "research_feedback.jsonl",
                     {"strategy_id": sid, "context_id": ctx, "source": "paper_forward",
                      "verdict": verdict["verdict"], "use": "human review + failure memory only; never search ranking",
                      "at": iso_now()})
