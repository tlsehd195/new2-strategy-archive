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

Funding (ADR 0073, ``FUNDING_POLICY``): every funding fetch is checked
(``funding_checked``: per-file and cross-file source order, parse errors,
torn rows, sha256 per file, declared interval per settlement) and recorded in
``funding_quality_<SYM>.jsonl``; settlements go to a hash-chained
``funding_<SYM>.jsonl`` with their declared interval.  Before any evaluation
the observation window [start, last bar] must be covered without a missing
settlement under the DECLARED schedule (``funding_schedule.coverage_check``).
Missing, invalid, gapped or schedule-less funding is never charged as 0: the
evaluator is not run, the verdict is ``PAPER_BLOCKED_DATA`` (non-final, no
status change).  A corrupt or changed source file blocks the plan for good;
a state file that does not validate gives ``PAPER_ERROR`` and is not
overwritten.  Plans registered before ADR 0073 are never concluded.

Evidence integrity (ADR 0074, ``FUNDING_INTEGRITY``): the warm-up file's
sha256 is part of the plan id; the funding log and the funding quality log
are sequenced hash chains whose last committed record ({seq, hash}) is kept
in the state.  Every step, every verdict and the conclusion re-check them: a
changed / missing warm-up, an edited, reordered, truncated or deleted log, or
a state ahead of its evidence is PAPER_ERROR; a log ahead of the state (killed
step, older state put back) is replayed and keeps any DATA_INVALID.  A
settlement due at the end of the observation window must be recorded, and no
interval observed today (``/fapi/v1/fundingInfo``) is applied to a past
settlement.  Plans registered before ADR 0074 are never concluded.

Positions (ADR 0075, ``POSITION_POLICY``): the verdict is computed on CLOSED
trades only.  The evaluation cutoff (kept in the state) is the earliest bar
at which the stopping rule holds (ADR 0076, ``CUTOFF_POLICY``: derived from
the ledger, independent of the step cadence, capped at ``max_days``); a
position entered by the
cutoff that is still open keeps the verdict at PAPER_OBSERVING
(OPEN_POSITIONS_PENDING) until its exit is observed — it is neither marked to
market into the result nor closed by force — and trades entered after the
cutoff are outside the sample.  The closed-trade ledger is checked against
the evaluator every step, by the verdict and again by the last gate
(duplicate, overlapping, malformed, unobserved or missing records, a state
whose ledger count is ahead of the file): any mismatch is PAPER_ERROR.
PAPER_PASSED therefore means: every trade entered in [start, cutoff] was
observed to its exit and realised under the plan's cost, funding and
execution rules, and the ledger the verdict was computed from is the one on
disk at the moment of the conclusion.

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
    JsonlCorruption, append_jsonl, atomic_write_json, canonical_json, iso_now, iter_jsonl, nw_tstat, t_sf,
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


#: ADR 0073: funding of a plan must be real, complete and under a declared schedule
FUNDING_POLICY = "paper-forward-funding-v1"
#: status of one funding fetch and of a symbol's funding over the observation window
FUNDING_SOURCE_STATUSES = ("VALID", "VALID_ZERO_RATE", "DATA_MISSING", "DATA_INVALID",
                           "COVERAGE_INCOMPLETE", "SCHEDULE_UNKNOWN", "SOURCE_ERROR")
#: the only statuses an evaluation may use
FUNDING_USABLE = ("VALID", "VALID_ZERO_RATE")
#: a fetch with one of these stops the plan for good (BLOCKED_DATA): the source itself is bad.
#: The other non-usable statuses block the verdict of the step and are re-checked next step.
FUNDING_STICKY = ("DATA_INVALID",)
FUNDING_TOL_MS = 60_000
#: ADR 0074: evidence integrity of a plan (warm-up sha256 in the plan id, hash-chained and
#: sequenced funding / funding-quality logs anchored in the state).  A plan without it
#: (registered under ADR 0073 only) is never concluded.
FUNDING_INTEGRITY = "paper-forward-integrity-v1"
#: ADR 0075: positions of a plan.  The sample of a final verdict is every trade ENTERED
#: in [start, evaluation cutoff]; the cutoff is the last observed bar of the step at which
#: the preregistered stopping rule (min_days + min_trades, or max_days) first held.  A
#: position entered by the cutoff and still open is waited for (non-final), never marked to
#: market into the verdict, never closed by force; trades entered after the cutoff are not
#: part of the sample.  The closed-trade ledger (events.jsonl) must agree with the
#: evaluator and with itself (no duplicate / overlapping / unobserved / malformed record).
POSITION_POLICY = "paper-forward-positions-v1"
#: ADR 0076: where the sample ends.  The evaluation cutoff is the EARLIEST bar time T of the
#: preregistered window at which the stopping rule holds, derived from the closed-trade ledger
#: alone: T = max(min_days bar, exit of the min_trades-th counted trade), capped at the last
#: bar of the max_days window, and only once every symbol is observed through T.  It does not
#: depend on how often or when step() runs, it never lies past start + max_days, and a cutoff
#: in the state that is not this T is PAPER_ERROR.  A window that the clock has passed but the
#: data does not cover is PAPER_BLOCKED_DATA (never INCONCLUSIVE on bars nobody observed).
CUTOFF_POLICY = "paper-forward-cutoff-v1"
#: execution statuses of a closed-trade record and the fill fraction each one allows
_EXEC_FILL = {"FILLED": "one", "PARTIAL": "part", "LIQUIDATED": "positive",
              "REJECTED_NO_LIQUIDITY": "zero", "REJECTED_PARTICIPATION": "zero"}
#: fields of a closed-trade record that must equal the evaluator's re-computation
_TRADE_KEYS = ("symbol", "side", "entry_ts", "exit_ts", "entry_index", "exit_index", "exec_status",
               "fill_fraction", "net_bp_exec", "cost_bp", "liquidated", "late", "holding_minutes")
#: what a declared per-settlement interval rests on (recorded with every fetch)
INTERVAL_BASIS_DECLARED = "DECLARED_PER_SETTLEMENT_MEANING_UNDOCUMENTED"
INTERVAL_BASIS_NONE = "NO_PER_SETTLEMENT_INTERVAL"
#: funding_schedule status -> funding status of the observation window
_COVERAGE_STATUS = {"OK": "VALID", "FUNDING_DATA_INVALID": "DATA_INVALID",
                    "FUNDING_SCHEDULE_UNKNOWN": "SCHEDULE_UNKNOWN",
                    "FUNDING_SETTLEMENT_GAP": "COVERAGE_INCOMPLETE"}


class ForwardRefused(RuntimeError):
    pass


class FundingSourceInvalid(RuntimeError):
    """A funding source could not be confirmed (ADR 0073).  Raised by the
    list interface ``FileFeed.funding`` instead of returning a partial or
    empty series that a caller could charge as 0."""


class CausalityViolation(RuntimeError):
    pass


class EvidenceIntegrityError(RuntimeError):
    """An evidence file of a plan (warm-up, funding log, funding quality log)
    does not match what the plan / state recorded (ADR 0074).  ``kind`` is
    the error kind recorded in the state; the plan stops with PAPER_ERROR and
    no file is repaired or overwritten."""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind


class ConclusionRefused(RuntimeError):
    """The last gate before a final verdict is recorded found a problem."""


def load_criteria(path: Optional[Path] = None) -> tuple[dict, str]:
    p = Path(path or DEFAULT_CRITERIA)
    raw = p.read_bytes()
    c = json.loads(raw)
    if c.get("schema") != "paper_forward_criteria_v1":
        raise ValueError(f"{p}: not a paper_forward_criteria_v1 file")
    return c, hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# funding fetch results (ADR 0073)
# ---------------------------------------------------------------------------

def funding_fetch_result(rows: Sequence, intervals: Mapping, *, source_files: Sequence[Mapping] = (),
                         schedule_source: Optional[str] = None, query: Optional[Mapping] = None,
                         status: Optional[str] = None, issues: Sequence[str] = (),
                         extra: Optional[Mapping] = None) -> dict:
    """The checked answer of a feed's ``funding_checked(sym, after_ts)``.

    ``rows`` are the settlements in the queried range in SOURCE order (never
    sorted or de-duplicated here), ``intervals`` the DECLARED interval in ms
    per settlement time (absent = not declared), ``source_files`` one entry
    per source read (name, sha256, rows, status, errors).  When ``status`` is
    not given it is derived: series problems -> DATA_INVALID, a row without a
    declared interval -> SCHEDULE_UNKNOWN, no source at all -> DATA_MISSING,
    every rate exactly 0 -> VALID_ZERO_RATE (real observations), else VALID.
    An empty ``rows`` from a source that answered is VALID: "no settlement in
    the range" — whether one was DUE is decided by the coverage check of the
    observation window, never here."""
    from funding_schedule import series_issues
    rows = [(t, r) for t, r in rows]
    iv = {int(t): int(v) for t, v in (intervals or {}).items() if isinstance(v, (int, float)) and v > 0}
    issues = list(issues)
    if status is None:
        bad = series_issues(rows)
        if bad:
            status, issues = "DATA_INVALID", issues + bad
        elif not source_files:
            status = "DATA_MISSING"
            issues.append("no funding source for the queried period")
        elif any(t not in iv for t, _ in rows):
            status = "SCHEDULE_UNKNOWN"
            issues.append(f"{sum(1 for t, _ in rows if t not in iv)} settlements without a declared interval")
        elif rows and all(r == 0 for _, r in rows):
            status = "VALID_ZERO_RATE"
        else:
            status = "VALID"
    if status not in FUNDING_SOURCE_STATUSES:
        raise ValueError(f"unknown funding status {status!r}")
    out = {"status": status, "rows": [[t, r] for t, r in rows], "intervals": iv,
           "issues": issues, "source_files": [dict(f) for f in source_files],
           "schedule_source": schedule_source, "query": dict(query or {}),
           "zero_rate_rows": sum(1 for _, r in rows if r == 0), "policy": FUNDING_POLICY,
           "interval_basis": INTERVAL_BASIS_DECLARED if iv else INTERVAL_BASIS_NONE,
           "schedule_observation": None}
    out.update(extra or {})
    return out


def _fetch_summary(fr: Mapping) -> dict:
    """What is kept on disk about one fetch (no rows)."""
    return {"status": fr["status"], "issues": list(fr.get("issues") or [])[:20],
            "rows": len(fr.get("rows") or []), "query": fr.get("query"),
            "schedule_source": fr.get("schedule_source"),
            "source_files": [{k: f.get(k) for k in ("name", "sha256", "rows", "status", "errors")}
                             for f in fr.get("source_files") or []],
            "interval_basis": fr.get("interval_basis"), "schedule_observation": fr.get("schedule_observation"),
            **{k: fr[k] for k in ("missing_files", "future_rows") if k in fr}}


def _month_span(name: str, prefix: str) -> Optional[tuple[int, int]]:
    """[start, end) in ms of a ``<prefix>YYYY-MM.csv`` / ``<prefix>YYYY-MM-DD.csv`` file."""
    import datetime as dt
    stem = name[len(prefix):-4] if name.startswith(prefix) and name.endswith(".csv") else ""
    try:
        if len(stem) == 7:
            d0 = dt.datetime.strptime(stem, "%Y-%m").replace(tzinfo=dt.timezone.utc)
            d1 = (d0.replace(day=28) + dt.timedelta(days=6)).replace(day=1)
        elif len(stem) == 10:
            d0 = dt.datetime.strptime(stem, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
            d1 = d0 + dt.timedelta(days=1)
        else:
            return None
    except ValueError:
        return None
    return int(d0.timestamp() * 1000), int(d1.timestamp() * 1000)


def _months_between(a_ms: int, b_ms: int) -> list[str]:
    import datetime as dt
    cur = dt.datetime.fromtimestamp(a_ms / 1000, tz=dt.timezone.utc).date().replace(day=1)
    end = dt.datetime.fromtimestamp(b_ms / 1000, tz=dt.timezone.utc).date()
    out = []
    while cur <= end:
        out.append(f"{cur:%Y-%m}")
        cur = (cur.replace(day=28) + dt.timedelta(days=6)).replace(day=1)
    return out


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

    def funding_checked(self, sym: str, after_ts: int) -> dict:
        """Funding settlements in ``(after_ts, now]`` with their quality (ADR 0073).

        Every file whose period overlaps the queried range is read with the
        shared checks (``data_quality.load_funding_files_checked``: parse
        errors, torn last row, source order inside a file and across files,
        sha256 per file).  If ANY of these files is bad the whole answer is
        DATA_INVALID — a corrupt month is never skipped while its neighbours
        are returned as if the series were complete.  Months of the range
        without a file are listed in ``missing_files``; whether a settlement
        was due in them is decided by the coverage check of the observation
        window (declared intervals), not by the file list."""
        from data_quality import load_funding_files_checked
        now = self.clock()
        prefix = f"{sym}-fundingRate-"
        query = {"after_ts": after_ts, "until_ts": now, "source": str(self.data_dir / "funding")}
        # ADR 0074: the column is stored per settlement; whether it is the period ending or
        # starting at calc_time is not documented by Binance, so coverage_check judges each
        # step under both readings (funding_schedule._step_status)
        sched = ("csv column funding_interval_hours (public archive fundingRate files; "
                 "period ending vs starting at calc_time undocumented)")
        paths = self._files("funding", f"{prefix}*.csv")
        rel = []
        for p in paths:
            span = _month_span(p.name, prefix)
            if span is None or (span[1] > after_ts and span[0] <= now):
                rel.append(p)                     # an unrecognised name is never assumed irrelevant
        present = {p.name for p in paths}
        missing = [f"{prefix}{m}.csv" for m in _months_between(after_ts + 1, now)
                   if f"{prefix}{m}.csv" not in present]
        if not rel:
            return funding_fetch_result([], {}, status="DATA_MISSING", schedule_source=sched, query=query,
                                        issues=["no funding file for the queried period"],
                                        extra={"missing_files": missing, "future_rows": 0})
        try:
            pairs, rep = load_funding_files_checked(rel)
        except OSError as e:
            return funding_fetch_result([], {}, status="SOURCE_ERROR", schedule_source=sched, query=query,
                                        issues=[f"{type(e).__name__}: {e}"],
                                        extra={"missing_files": missing, "future_rows": 0})
        rows = [(t, r) for t, r in pairs if after_ts < t <= now]
        ivs = {t: rep["intervals"][t] for t, _ in rows if t in rep["intervals"]}
        issues = [f"{fs['name']}: {e}" for fs in rep["file_status"] for e in fs["errors"]]
        return funding_fetch_result(rows, ivs, source_files=rep["file_status"], schedule_source=sched,
                                    query=query, status="DATA_INVALID" if issues else None, issues=issues,
                                    extra={"missing_files": missing,
                                           "future_rows": sum(1 for t, _ in pairs if t > now)})

    def funding(self, sym: str, after_ts: int) -> list[tuple[int, float]]:
        """List interface (pre-ADR 0073 callers).  Fail-closed: a fetch that is
        not VALID / VALID_ZERO_RATE raises :class:`FundingSourceInvalid`
        instead of returning a partial or empty series."""
        fr = self.funding_checked(sym, after_ts)
        if fr["status"] not in FUNDING_USABLE:
            raise FundingSourceInvalid(f"{sym}: funding {fr['status']}: {'; '.join(fr['issues'][:5])}")
        return [(t, r) for t, r in fr["rows"]]

    def funding_source_hashes(self, sym: str) -> dict:
        """sha256 of every funding file of ``sym`` now on disk (a file read
        earlier by a plan must not change or vanish, ADR 0073)."""
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self._files("funding", f"{sym}-fundingRate-*.csv")}


class BinancePublicFeed:
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

    FUNDING_LIMIT = 1000
    MAX_FUNDING_PAGES = 20

    def _get_raw(self, path: str) -> bytes:
        from data_fetch import fetch_with_retry
        return fetch_with_retry(self.BASE + path, opener=self.opener, attempts=self.attempts,
                                sleep=self.sleep)

    def _get(self, path: str) -> list:
        return json.loads(self._get_raw(path))

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

    def funding_checked(self, sym: str, after_ts: int) -> dict:
        """Settlements in ``(after_ts, now]`` from ``/fapi/v1/fundingRate``
        (paged, bounded) plus one snapshot of ``/fapi/v1/fundingInfo``.

        ADR 0074: the Binance documentation describes ``fundingInfo`` as the
        funding info of "symbols that had FundingRateCap/FundingRateFloor /
        fundingIntervalHours adjustment" — a CURRENT snapshot without any
        effective time or history — and ``fundingRate`` rows carry no interval
        at all.  So no settlement of this answer has a proven interval: the
        snapshot is recorded as ``schedule_observation`` (time, value, sha256
        of the raw answer) and NEVER copied onto past settlements (ADR 0073
        did that: a 1h history with a missing settlement looked complete
        under today's 4h).  ``intervals`` is always empty and a successful,
        complete answer is SCHEDULE_UNKNOWN, so Paper Forward refuses a plan
        on this feed (the warm-up schedule cannot be proven) and never
        evaluates REST rows.  Other statuses as before: a request failure or
        an error object is SOURCE_ERROR (the snapshot, if taken, is kept); an
        unparsable / out-of-range / duplicated / unsorted / foreign-symbol row
        is DATA_INVALID; a still-full last page is COVERAGE_INCOMPLETE."""
        from data_fetch import FetchFailure
        now = self.clock()
        query = {"after_ts": after_ts, "until_ts": now, "source": self.BASE}
        sources: list[dict] = []
        obs: dict = {}

        def err(msg):
            return funding_fetch_result([], {}, status="SOURCE_ERROR", source_files=sources, query=query,
                                        issues=[msg], schedule_source=None,
                                        extra={"schedule_observation": dict(obs) or None,
                                               "interval_basis": INTERVAL_BASIS_NONE})
        try:
            blob = self._get_raw("/fapi/v1/fundingInfo")
            info = json.loads(blob)
        except (FetchFailure, ValueError) as e:
            return err(f"/fapi/v1/fundingInfo: {type(e).__name__}: {e}")
        if not isinstance(info, list):
            return err(f"/fapi/v1/fundingInfo: unexpected response {str(info)[:200]}")
        sources.append({"name": "/fapi/v1/fundingInfo", "sha256": hashlib.sha256(blob).hexdigest(),
                        "rows": len(info), "status": "VALID", "errors": []})
        iv, listed = None, False
        for x in info:
            if isinstance(x, dict) and x.get("symbol") == sym:
                listed = True
                h = x.get("fundingIntervalHours")
                if isinstance(h, (int, float)) and not isinstance(h, bool) and h > 0:
                    iv = int(round(h * 3_600_000))
        obs.update(source="/fapi/v1/fundingInfo", observed_at=now, symbol=sym, listed=listed,
                   interval_ms=iv, sha256=sources[-1]["sha256"],
                   meaning="current snapshot; not valid for any past settlement (ADR 0074)")
        rows: list[tuple[int, float]] = []
        issues: list[str] = []
        start, full = after_ts + 1, False
        for _ in range(self.MAX_FUNDING_PAGES):
            path = (f"/fapi/v1/fundingRate?symbol={sym}&startTime={start}&endTime={now}"
                    f"&limit={self.FUNDING_LIMIT}")
            try:
                blob = self._get_raw(path)
                data = json.loads(blob)
            except (FetchFailure, ValueError) as e:
                return err(f"{path}: {type(e).__name__}: {e}")
            if not isinstance(data, list):
                return err(f"{path}: unexpected response {str(data)[:200]}")
            errs = []
            for x in data:
                try:
                    t, r = int(x["fundingTime"]), float(x["fundingRate"])
                except (KeyError, TypeError, ValueError):
                    errs.append(f"unparsable row {str(x)[:80]}")
                    continue
                if not math.isfinite(r):
                    errs.append(f"non-finite rate at {t}")
                elif x.get("symbol", sym) != sym:
                    errs.append(f"row of {x.get('symbol')!r} in the {sym} answer")
                elif t <= after_ts or t > now:
                    errs.append(f"settlement {t} outside the queried range ({after_ts}, {now}]")
                elif rows and t <= rows[-1][0]:
                    errs.append(f"{'SOURCE_DUPLICATE_TS' if t == rows[-1][0] else 'SOURCE_TIME_INVERSION'}: "
                                f"{t} after {rows[-1][0]}")
                else:
                    rows.append((t, r))
            sources.append({"name": path, "sha256": hashlib.sha256(blob).hexdigest(), "rows": len(data),
                            "status": "DATA_INVALID" if errs else "VALID", "errors": errs})
            issues += errs
            full = len(data) >= self.FUNDING_LIMIT
            if not full or not rows:
                break
            start = rows[-1][0] + 1
        if issues:
            status = "DATA_INVALID"
        elif full:
            status = "COVERAGE_INCOMPLETE"
            issues.append(f"answer still full after {self.MAX_FUNDING_PAGES} pages: later settlements not fetched")
        else:
            status = "SCHEDULE_UNKNOWN"
            issues.append("no per-settlement interval: /fapi/v1/fundingRate rows carry none and "
                          + (f"/fapi/v1/fundingInfo gives only the current {iv} ms (observed at {now}), "
                             "not the interval of past settlements" if iv is not None else
                             f"{sym} is not declared in /fapi/v1/fundingInfo")
                          + " (ADR 0074: no interval is assumed or back-dated)")
        return funding_fetch_result(rows, {}, source_files=sources, schedule_source=None, query=query,
                                    status=status, issues=issues,
                                    extra={"schedule_observation": dict(obs),
                                           "interval_basis": INTERVAL_BASIS_NONE})

    def funding(self, sym: str, after_ts: int) -> list[tuple[int, float]]:
        fr = self.funding_checked(sym, after_ts)
        if fr["status"] not in FUNDING_USABLE:
            raise FundingSourceInvalid(f"{sym}: funding {fr['status']}: {'; '.join(fr['issues'][:5])}")
        return [(t, r) for t, r in fr["rows"]]


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


def read_chained_log(path: Path, what: str) -> list[dict]:
    """Rows of a hash-chained log; a broken chain raises (fail-closed)."""
    rows, prev = [], ""
    for r in iter_jsonl(path):
        body = {k: v for k, v in r.items() if k not in ("hash", "prev")}
        if r.get("prev") != prev or r.get("hash") != _chain(prev, body):
            raise CausalityViolation(f"{path}: {what} chain broken at ts={r.get('ts')}")
        rows.append(r)
        prev = r["hash"]
    return rows


def read_bar_log(path: Path) -> list[dict]:
    """Rows of a hash-chained bar log; a broken chain raises (fail-closed)."""
    return read_chained_log(path, "bar log")


def read_seq_chain(path: Path, what: str) -> list[dict]:
    """Records of a sequenced hash-chained log (ADR 0074: funding log and
    funding quality log).  Record i (from 1) must carry ``seq == i``,
    ``prev`` = hash of record i-1 ("" for the first) and ``hash`` =
    sha256(prev + canonical JSON of the record without prev/hash).  Any
    edit, reordering, inserted / removed interior record or corrupt interior
    line raises :class:`EvidenceIntegrityError` (``<what>_SEQUENCE`` /
    ``<what>_CHAIN`` / ``<what>_CORRUPT``).  A torn LAST line (interrupted
    write) is not a record; whether a missing tail was already committed is
    decided by the state head (:func:`check_log_head`)."""
    rows, prev = [], ""
    try:
        recs = list(iter_jsonl(path))
    except JsonlCorruption as e:
        raise EvidenceIntegrityError(f"{what}_CORRUPT", str(e)) from e
    for i, r in enumerate(recs, 1):
        if r.get("seq") != i:
            raise EvidenceIntegrityError(f"{what}_SEQUENCE",
                                         f"{Path(path).name}: record {i} carries seq {r.get('seq')!r}")
        body = {k: v for k, v in r.items() if k not in ("hash", "prev")}
        if r.get("prev") != prev or r.get("hash") != _chain(prev, body):
            raise EvidenceIntegrityError(f"{what}_CHAIN", f"{Path(path).name}: chain broken at seq {i}")
        rows.append(r)
        prev = r["hash"]
    return rows


def append_seq_chain(path: Path, head: dict, body: Mapping) -> dict:
    """Append ``body`` after ``head`` ({seq, hash}) and return the new head."""
    rec = {**body, "seq": head["seq"] + 1}
    h = _chain(head["hash"], rec)
    append_jsonl(path, {**rec, "prev": head["hash"], "hash": h})
    return {"seq": rec["seq"], "hash": h}


def check_log_head(rows: Sequence[Mapping], head: Mapping, what: str) -> bool:
    """Cross-check a verified log with the head the state remembers.

    * head beyond the log            -> ``<what>_TRUNCATED`` (committed records gone)
    * record at head.seq has another hash -> ``<what>_REWRITTEN``
    * log longer than the head       -> True: records appended after the last
      state save (a step killed before saving, or an older state put back);
      the log is the record and the caller re-derives the state from it.
    Returns whether the log is ahead of the state."""
    n, hs, hh = len(rows), head["seq"], head["hash"]
    if hs > n:
        raise EvidenceIntegrityError(f"{what}_TRUNCATED",
                                     f"state remembers record {hs} ({hh[:16]}), the log holds {n}")
    if hs and rows[hs - 1]["hash"] != hh:
        raise EvidenceIntegrityError(f"{what}_REWRITTEN",
                                     f"record {hs} hash {rows[hs - 1]['hash'][:16]} != state {hh[:16]}")
    return n > hs


def _warmup_bytes(obj: Mapping) -> bytes:
    """The exact bytes of a warm-up file; its sha256 is fixed in the plan id."""
    return (canonical_json(obj) + "\n").encode("utf-8")


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
        # warm-up: history strictly before the start (features only, never evaluated).
        # ADR 0073: computed BEFORE anything is written, so a plan whose funding cannot
        # be confirmed is refused without leaving a half-registered plan behind
        warm = {}
        for sym in spec["symbols"]:
            bars, _ = self.feed.closed_bars(sym, start - (self.crit["warmup_bars"] + 1) * MS15)
            wb = [b for b in bars if b.ts < start]
            tail = []                              # keep the contiguous tail ending at start-15m
            for b in reversed(wb):
                if (tail and tail[-1].ts - b.ts != MS15) or (not tail and b.ts != start - MS15):
                    break
                tail.append(b)
            tail.reverse()
            fr = self._fetch_funding(sym, start - 30 * DAY_MS)
            if fr["status"] not in FUNDING_USABLE:
                raise ForwardRefused(f"{sym}: warm-up funding {fr['status']} "
                                     f"({'; '.join(fr['issues'][:3])}): funding cannot be confirmed")
            wf = [(t, r) for t, r in fr["rows"] if t < start]
            if not wf:
                raise ForwardRefused(f"{sym}: no funding settlement before the start (DATA_MISSING): "
                                     "the first holding window's funding cannot be proven complete")
            warm[sym] = {"bars": tail, "funding": wf,
                         "intervals": {t: v for t, v in fr["intervals"].items() if t < start},
                         "fetch": _fetch_summary(fr), "sources": self._consumed_sources(fr)}
        # ADR 0074: the warm-up file bytes are built first, their sha256 goes into the
        # plan body (and so into the plan id); files are written and read back before
        # the plan record exists, so a plan never points at a file it was not built from
        blobs = {}
        for sym, w in warm.items():
            blobs[sym] = _warmup_bytes({
                "symbol": sym, "start_ts": start,
                "rows": [_bar_row(b) for b in w["bars"]],
                "funding": [list(x) for x in w["funding"]],
                "funding_intervals": {str(t): v for t, v in w["intervals"].items()},
                "funding_fetch": w["fetch"], "funding_sources": w["sources"],
                "contiguous_to_start": bool(w["bars"])})
        body = {"strategy_id": sid, "context_id": ctx, "symbols": spec["symbols"], "start_ts": start,
                "registered_ms": now, "criteria": self.crit, "criteria_hash": self.crit_hash,
                "execution": self.exec_cfg.to_json(), "execution_version": EXECUTION_SIM_VERSION,
                "cost": self.p.cost.identity(), "order_value": self.p.order_value,
                "spec_sha256": hashlib.sha256(canonical_json(spec).encode()).hexdigest(),
                "feed": self.feed.kind, "feed_realtime": bool(self.feed.realtime),
                "funding_policy": FUNDING_POLICY, "funding_integrity": FUNDING_INTEGRITY,
                "warmup_sha256": {sym: hashlib.sha256(b).hexdigest() for sym, b in blobs.items()},
                "warmup_hash_basis": "sha256 of the exact file bytes (canonical JSON + newline)",
                "mode": FORWARD_MODE, "version": FORWARD_VERSION, "orders_sent": 0}
        body["plan_id"] = "FP-" + hashlib.sha256(canonical_json(body).encode()).hexdigest()[:16]
        body["at"] = iso_now()
        d = self.root / body["plan_id"]
        d.mkdir(parents=True, exist_ok=True)
        for sym, raw in blobs.items():
            _atomic_write_bytes(d / f"warmup_{sym}.json", raw)
            if hashlib.sha256((d / f"warmup_{sym}.json").read_bytes()).hexdigest() != body["warmup_sha256"][sym]:
                raise ForwardRefused(f"{sym}: warm-up file does not read back with its sha256")
        append_jsonl(self.plans_path, body)
        if st == "PAPER_TEST_READY":
            self.p.statuses.set(sid, ctx, "PAPER_TESTING", stage="paper_forward",
                                reasons=[f"forward observation plan {body['plan_id']} registered"])
        self._save_state(body["plan_id"], self._new_state(body, warm))
        return body

    # -- state ------------------------------------------------------------------
    def _new_state(self, plan: Mapping, warm: Optional[Mapping] = None) -> dict:
        syms = plan["symbols"]
        warm = warm or {}
        return {"plan_id": plan["plan_id"], "status": "OBSERVING", "steps": 0,
                "last_ts": {s: plan["start_ts"] - MS15 for s in syms},
                # ADR 0073: funding is collected right after the last warm-up settlement
                # (no "start - 8h" rule: the schedule is whatever the source declares)
                "last_funding_ts": {s: (warm[s]["funding"][-1][0] if s in warm else plan["start_ts"] - 1)
                                    for s in syms},
                "funding_sources": {s: dict(warm[s]["sources"]) if s in warm else {} for s in syms},
                "funding_status": {s: (warm[s]["fetch"]["status"] if s in warm else None) for s in syms},
                # ADR 0074: last committed record of each evidence log ({seq, hash})
                "quality_head": {s: {"seq": 0, "hash": ""} for s in syms},
                "funding_head": {s: {"seq": 0, "hash": ""} for s in syms},
                "bars": {s: 0 for s in syms}, "missing_bars": {s: 0 for s in syms},
                "duplicates": {s: 0 for s in syms}, "gaps": {s: [] for s in syms},
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
        try:
            st = self.state(plan_id)
        except (OSError, ValueError) as e:
            return self._state_unusable(plan, [f"state file unreadable: {type(e).__name__}: {e}"])
        bad = self._state_problems(plan, st)
        if bad:
            return self._state_unusable(plan, bad)
        if st["concluded"]:
            return {"plan_id": plan_id, "status": "CONCLUDED", "verdict": st["verdict"]}
        d = self.root / plan_id
        now = self.clock()
        new_counts = {}
        policy = self._funding_policy(plan)
        if policy and not self._integrity(plan):
            # ADR 0074: registered under ADR 0073 only (no warm-up sha256, unchained
            # quality log): nothing is collected, evaluated or concluded
            res = {"plan_id": plan_id, "status": st["status"], "verdict": "PAPER_BLOCKED_DATA",
                   "reasons": ["LEGACY_PLAN_INTEGRITY_UNVERIFIED: plan registered before ADR 0074 "
                               "(no warm-up sha256 / evidence chain); re-verify with a new plan"]}
            append_jsonl(d / "steps.jsonl", {"at": iso_now(), "clock_ms": now, **res})
            return res
        if st["status"] == "OBSERVING":
            drift = self._plan_drift(plan)
            if drift:
                # the plan's preregistered spec / cost / execution rules are what is
                # being observed: anything else would be a different experiment
                st["status"] = "ERROR"
                st["errors"].append({"at": now, "kind": "PLAN_CONTEXT_CHANGED", "detail": "; ".join(drift)})
        if policy and st["status"] != "ERROR":
            # ADR 0074: warm-up sha256, both evidence logs and their state heads are
            # verified (and a state left behind the logs is re-derived) before anything
            try:
                self._evidence(plan, st, recover=True, now=now)
            except EvidenceIntegrityError as e:
                st["status"] = "ERROR"
                st["errors"].append({"at": now, "kind": e.kind, "detail": str(e)})
        for sym in plan["symbols"]:
            if st["status"] != "OBSERVING":
                break
            log = d / f"bars_{sym}.jsonl"
            try:
                rows = read_bar_log(log)
            except (CausalityViolation, JsonlCorruption) as e:
                st["status"] = "ERROR"
                st["errors"].append({"at": now, "symbol": sym, "kind": "LOG_TAMPERED", "detail": str(e)})
                break
            if st["status"] != "OBSERVING":
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
            if policy:
                if st["status"] == "OBSERVING":
                    self._collect_funding(plan, st, sym, now)
            else:
                # plan registered before ADR 0073 (no declared schedule, no provenance):
                # collected as before, but its verdict is always PAPER_BLOCKED_DATA
                try:
                    legacy = list(self.feed.funding(sym, st["last_funding_ts"][sym]))
                except Exception as e:  # noqa: BLE001 - recorded; the verdict is blocked anyway
                    st["errors"].append({"at": now, "symbol": sym, "kind": "FUNDING_SOURCE_ERROR",
                                         "detail": f"{type(e).__name__}: {e}"})
                    legacy = []
                for t, r in legacy:
                    if t <= st["last_funding_ts"][sym]:
                        continue
                    append_jsonl(d / f"funding_{sym}.jsonl", {"ts": t, "rate": r, "observed_at": now})
                    st["last_funding_ts"][sym] = t
            new_counts[sym] = added
        st["steps"] += 1
        result = {"plan_id": plan_id, "new_bars": new_counts, "status": st["status"]}
        if st["status"] == "OBSERVING" and policy:
            try:
                fund = self._funding_window_status(plan, st)
            except EvidenceIntegrityError as e:
                st["status"] = "ERROR"
                st["errors"].append({"at": now, "kind": e.kind, "detail": str(e)})
            else:
                result["funding"] = fund
                result["funding_ok"] = all(v["status"] in FUNDING_USABLE for v in fund.values())
        if st["status"] == "OBSERVING" and (result.get("funding_ok") or not policy):
            # ADR 0073: the evaluator is only run on funding confirmed for the whole
            # observation window; otherwise nothing is evaluated (never charged 0)
            try:
                # ADR 0075: a ledger shorter than the state recorded lost closed trades
                n_log = self._trade_log_len(plan)
                if n_log < int(st.get("events_logged") or 0):
                    raise EvidenceIntegrityError("TRADE_LOG_TRUNCATED",
                                                 f"events.jsonl has {n_log} records, the state recorded "
                                                 f"{st['events_logged']}")
                ev = self._evaluate(plan, st)
            except EvidenceIntegrityError as e:
                st["status"] = "ERROR"
                st["errors"].append({"at": now, "kind": e.kind, "detail": str(e)})
                ev = None
            except CausalityViolation as e:
                st["status"] = "ERROR"
                st["errors"].append({"at": now, "kind": "CAUSALITY_VIOLATION", "detail": str(e)})
                ev = None
            if ev is not None:
                result.update(ev)
                # ADR 0075: the closed-trade ledger now on disk is the evaluator's output;
                # a ledger that disagrees with it or with itself stops the plan
                try:
                    st["events_logged"] = self._trade_log_len(plan)
                except EvidenceIntegrityError as e:
                    pos = [str(e)]
                else:
                    pos = self._position_problems(plan, st, result)
                if pos:
                    st["status"] = "ERROR"
                    st["errors"].append({"at": now, "kind": "POSITION_INTEGRITY", "detail": "; ".join(pos[:10])})
                if policy and ev["excluded"]["no_funding_coverage"]:
                    result["funding_ok"] = False
                    result["funding_dropped"] = {"events": ev["excluded"]["no_funding_coverage"],
                                                 "reasons": ev.get("funding_drop_reasons", {})}
        verdict = self._verdict(plan, st, result)
        cut = verdict.get("evaluation_cutoff")
        if cut is not None and st.get("evaluation_cutoff") is None:
            # ADR 0075/0076: the earliest bar at which the stopping rule holds: the sample of
            # the final verdict is every trade entered up to there (kept, never moved)
            st["evaluation_cutoff"] = {"ts": cut, "step": st["steps"], "policy": POSITION_POLICY,
                                       "rule": CUTOFF_POLICY}
        if st.get("evaluation_cutoff") is not None:
            result["evaluation_cutoff"] = st["evaluation_cutoff"]["ts"]
        if verdict["final"]:
            try:
                self._conclude(plan, verdict, result, st)
            except ConclusionRefused as e:
                # ADR 0074: the last gate found a problem: nothing was recorded
                verdict = {"verdict": "PAPER_BLOCKED_DATA", "final": False, "reasons": [f"CONCLUSION_REFUSED: {e}"]}
            else:
                st["concluded"] = True
        result["verdict"] = verdict["verdict"]
        result["reasons"] = verdict["reasons"]
        st["verdict"] = verdict["verdict"]
        self._save_state(plan_id, st)
        append_jsonl(d / "steps.jsonl", {"at": iso_now(), "clock_ms": now,
                                         **{k: v for k, v in result.items() if k != "trades"}})
        return result

    # -- funding (ADR 0073) ----------------------------------------------------------
    @staticmethod
    def _funding_policy(plan: Mapping) -> bool:
        return plan.get("funding_policy") == FUNDING_POLICY

    @staticmethod
    def _integrity(plan: Mapping) -> bool:
        return (plan.get("funding_policy") == FUNDING_POLICY and plan.get("funding_integrity") == FUNDING_INTEGRITY
                and isinstance(plan.get("warmup_sha256"), dict))

    def _fetch_funding(self, sym: str, after_ts: int) -> dict:
        """One checked funding fetch.  A feed without ``funding_checked`` (a
        plain list, no declared schedule) is SCHEDULE_UNKNOWN; any exception of
        the feed is SOURCE_ERROR — never an empty series."""
        q = {"after_ts": after_ts, "until_ts": self.clock(), "feed": self.feed.kind}
        try:
            if hasattr(self.feed, "funding_checked"):
                fr = self.feed.funding_checked(sym, after_ts)
                if not isinstance(fr, Mapping) or fr.get("status") not in FUNDING_SOURCE_STATUSES:
                    return funding_fetch_result([], {}, status="SOURCE_ERROR", query=q,
                                                issues=[f"feed returned no valid funding result: {str(fr)[:200]}"])
                return dict(fr)
            pairs = list(self.feed.funding(sym, after_ts))
        except Exception as e:  # noqa: BLE001 - any feed failure is a SOURCE_ERROR, never "no funding"
            return funding_fetch_result([], {}, status="SOURCE_ERROR", query=q,
                                        issues=[f"{type(e).__name__}: {e}"])
        from funding_schedule import series_issues
        bad = series_issues(pairs)
        return funding_fetch_result(pairs, {}, query=q, status="DATA_INVALID" if bad else "SCHEDULE_UNKNOWN",
                                    source_files=[{"name": f"legacy_feed:{self.feed.kind}", "sha256": None,
                                                   "rows": len(pairs), "status": "SCHEDULE_UNKNOWN",
                                                   "errors": []}],
                                    issues=bad + ["feed declares no settlement interval (list interface)"])

    def _consumed_sources(self, fr: Mapping) -> dict:
        """{file: sha256} of a fetch from a file feed (re-checked every step)."""
        if not hasattr(self.feed, "funding_source_hashes"):
            return {}
        return {f["name"]: f["sha256"] for f in fr.get("source_files") or [] if f.get("sha256")}

    def _source_changes(self, st: Mapping, sym: str) -> list[str]:
        known = st["funding_sources"].get(sym) or {}
        if not known or not hasattr(self.feed, "funding_source_hashes"):
            return []
        cur = self.feed.funding_source_hashes(sym)
        out = []
        for name, sha in sorted(known.items()):
            if name not in cur:
                out.append(f"FUNDING_SOURCE_REMOVED: {name} (read earlier with sha256 {sha[:16]}) is gone")
            elif cur[name] != sha:
                out.append(f"FUNDING_SOURCE_CHANGED: {name} sha256 {sha[:16]} -> {cur[name][:16]}")
        return out

    def _collect_funding(self, plan: Mapping, st: dict, sym: str, now: int) -> None:
        """Fetch, record and append the funding of one symbol.

        The fetch summary goes to ``funding_quality_<sym>.jsonl`` BEFORE the
        state changes (it is the durable record a restart re-reads).  A
        DATA_INVALID source (corrupt file, a file read earlier that changed
        or vanished, a bad API answer) stops the plan: BLOCKED_DATA, which no
        later step undoes.  SOURCE_ERROR appends nothing and is retried.
        Otherwise the settlements are appended to the hash-chained funding log
        up to the first one that would leave a gap under its declared
        interval; the rest is fetched again next step."""
        d = self.root / plan["plan_id"]
        try:
            changed = self._source_changes(st, sym)
        except OSError as e:
            changed = []
            fr = funding_fetch_result([], {}, status="SOURCE_ERROR", issues=[f"re-hash failed: {e}"])
        else:
            fr = self._fetch_funding(sym, st["last_funding_ts"][sym])
        summ = _fetch_summary(fr)
        if changed:
            summ.update(status="DATA_INVALID", kind="FUNDING_SOURCE_CHANGED", issues=changed + summ["issues"])
        summ.update(at=now, symbol=sym, last_funding_ts=st["last_funding_ts"][sym],
                    policy=FUNDING_POLICY, integrity=FUNDING_INTEGRITY)
        # ADR 0074: sequenced + hash-chained, anchored by state["quality_head"]
        st["quality_head"][sym] = append_seq_chain(d / f"funding_quality_{sym}.jsonl",
                                                   st["quality_head"][sym], summ)
        before = st["funding_status"].get(sym)
        st["funding_status"][sym] = summ["status"]
        if summ["status"] in FUNDING_STICKY:
            st["status"] = "BLOCKED_DATA"
            st["errors"].append({"at": now, "symbol": sym, "kind": summ.get("kind") or "FUNDING_DATA_INVALID",
                                 "detail": summ["issues"][:10]})
            return
        if summ["status"] == "SOURCE_ERROR":
            if before != "SOURCE_ERROR":
                st["errors"].append({"at": now, "symbol": sym, "kind": "FUNDING_SOURCE_ERROR",
                                     "detail": summ["issues"][:5]})
            return
        log = d / f"funding_{sym}.jsonl"
        head = st["funding_head"][sym]               # verified against the log at step start
        prev = st["last_funding_ts"][sym]
        srcs = self._consumed_sources(fr)
        stop = None
        for t, r in fr["rows"]:
            if t <= prev:
                continue
            iv = fr["intervals"].get(t)
            if iv and t - prev > iv + FUNDING_TOL_MS:
                stop = [prev, t, iv]
                break
            body = {"ts": t, "rate": r, "interval_ms": iv, "observed_at": now, "fetch_status": summ["status"],
                    "sources": srcs, "interval_basis": fr.get("interval_basis"),
                    "quality_seq": st["quality_head"][sym]["seq"]}
            head = append_seq_chain(log, head, body)
            prev = t
        st["funding_head"][sym] = head
        st["last_funding_ts"][sym] = prev
        if srcs:
            st["funding_sources"][sym].update(srcs)
        if stop is not None:
            st["funding_status"][sym] = "COVERAGE_INCOMPLETE"
            if before != "COVERAGE_INCOMPLETE":
                st["errors"].append({"at": now, "symbol": sym, "kind": "FUNDING_SETTLEMENT_GAP",
                                     "detail": f"no settlement between {stop[0]} and {stop[1]} "
                                               f"(declared interval {stop[2]} ms): not appended past it"})

    def _warmup(self, plan: Mapping, sym: str) -> dict:
        """The warm-up file of ``sym``.  ADR 0074 plans: its bytes must have the
        sha256 fixed in the plan (and so in the plan id); a missing, changed,
        truncated, unparsable or foreign-symbol file raises
        :class:`EvidenceIntegrityError` and is never rewritten."""
        path = self.root / plan["plan_id"] / f"warmup_{sym}.json"
        if not self._integrity(plan):
            return json.loads(path.read_text())
        want = plan["warmup_sha256"].get(sym)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise EvidenceIntegrityError("WARMUP_MISSING", f"{path.name} is gone") from None
        got = hashlib.sha256(raw).hexdigest()
        if not want or got != want:
            raise EvidenceIntegrityError("WARMUP_TAMPERED",
                                         f"{path.name} sha256 {got[:16]} != plan {str(want)[:16]}")
        wj = json.loads(raw)
        if wj.get("symbol") != sym or wj.get("start_ts") != plan["start_ts"]:
            raise EvidenceIntegrityError("WARMUP_MISMATCH", f"{path.name} is not the warm-up of {sym} at "
                                                            f"{plan['start_ts']}")
        return wj

    def _evidence(self, plan: Mapping, st: dict, *, recover: bool, now: Optional[int] = None) -> list[str]:
        """Cross-check of every evidence file with the plan and the state (ADR 0074).

        Per symbol: warm-up sha256; funding quality log and funding log
        (sequence, chain, head in the state); funding log content (integer,
        strictly increasing times after the last warm-up settlement, finite
        rates; a chain recomputed by hand is caught by the head); quality
        records belong to the symbol and carry a known status; the state's
        ``last_funding_ts`` equals the time of the record at its funding head
        (or the last warm-up settlement) — a state ahead of its evidence is
        STATE_AHEAD_OF_EVIDENCE, any other mismatch STATE_FUNDING_TS_MISMATCH.

        A log AHEAD of the head (records appended after the last state save:
        a killed step, or an older state put back) is the record: with
        ``recover`` the heads, ``last_funding_ts`` and consumed sources are
        re-derived from it, and a DATA_INVALID quality record anywhere in the
        log re-blocks an OBSERVING state (FUNDING_BLOCK_RESTORED).  Without
        ``recover`` (final gate) a log ahead is a problem.  Integrity errors
        raise with ``recover`` and are returned as problems without it."""
        d = self.root / plan["plan_id"]
        problems: list[str] = []
        for sym in plan["symbols"]:
            try:
                wj = self._warmup(plan, sym)
                q = read_seq_chain(d / f"funding_quality_{sym}.jsonl", "QUALITY_LOG")
                f = read_seq_chain(d / f"funding_{sym}.jsonl", "FUNDING_LOG")
                q_ahead = check_log_head(q, st["quality_head"][sym], "QUALITY_LOG")
                f_ahead = check_log_head(f, st["funding_head"][sym], "FUNDING_LOG")
                for r in q:
                    if r.get("symbol") != sym or r.get("status") not in FUNDING_SOURCE_STATUSES:
                        raise EvidenceIntegrityError("QUALITY_LOG_INVALID",
                                                     f"record {r.get('seq')}: symbol {r.get('symbol')!r} "
                                                     f"status {r.get('status')!r}")
                wf = wj.get("funding") or []
                w_last = int(wf[-1][0]) if wf else None
                prev = w_last
                for r in f:
                    t, rate = r.get("ts"), r.get("rate")
                    if isinstance(t, bool) or not isinstance(t, int) or isinstance(rate, bool) \
                            or not isinstance(rate, (int, float)) or not math.isfinite(rate):
                        raise EvidenceIntegrityError("FUNDING_LOG_INVALID", f"record {r.get('seq')}: {t!r} {rate!r}")
                    if prev is not None and t <= prev:
                        what = "the last warm-up settlement" if prev == w_last and r["seq"] == 1 else "the previous record"
                        raise EvidenceIntegrityError("FUNDING_LOG_ORDER",
                                                     f"record {r['seq']} ts {t} not after {what} ({prev})")
                    prev = t
                hs = st["funding_head"][sym]["seq"]
                head_ts = f[hs - 1]["ts"] if hs else w_last
                lf = st["last_funding_ts"][sym]
                tip_ts = f[-1]["ts"] if f else w_last
                if tip_ts is not None and lf > tip_ts:
                    raise EvidenceIntegrityError("STATE_AHEAD_OF_EVIDENCE",
                                                 f"{sym}: state last_funding_ts {lf} after the last recorded "
                                                 f"settlement {tip_ts}")
                if head_ts is not None and lf != head_ts:
                    raise EvidenceIntegrityError("STATE_FUNDING_TS_MISMATCH",
                                                 f"{sym}: state last_funding_ts {lf} != {head_ts} at its funding head")
            except EvidenceIntegrityError as e:
                if recover:
                    raise
                problems.append(f"{sym}: {e}")
                continue
            except (OSError, ValueError, KeyError, TypeError) as e:
                err = EvidenceIntegrityError("EVIDENCE_UNREADABLE", f"{sym}: {type(e).__name__}: {e}")
                if recover:
                    raise err from e
                problems.append(str(err))
                continue
            if not recover:
                if q_ahead or f_ahead:
                    problems.append(f"{sym}: state behind its evidence logs")
                continue
            if q_ahead or f_ahead:
                st["quality_head"][sym] = {"seq": len(q), "hash": q[-1]["hash"] if q else ""}
                st["funding_head"][sym] = {"seq": len(f), "hash": f[-1]["hash"] if f else ""}
                if tip_ts is not None:
                    st["last_funding_ts"][sym] = tip_ts
                if q:
                    st["funding_status"][sym] = q[-1]["status"]
                st.setdefault("recovered", []).append({"symbol": sym, "last_funding_ts": st["last_funding_ts"][sym],
                                                       "quality_seq": len(q), "funding_seq": len(f),
                                                       "at": iso_now()})
            for r in f:
                for name, sha in (r.get("sources") or {}).items():
                    st["funding_sources"][sym].setdefault(name, sha)
            sticky = [x for x in q if x.get("status") in FUNDING_STICKY]
            if sticky and st["status"] == "OBSERVING":
                st["status"] = "BLOCKED_DATA"
                st["errors"].append({"at": now, "symbol": sym, "kind": "FUNDING_BLOCK_RESTORED",
                                     "detail": list(sticky[-1].get("issues") or [])[:5]})
        return problems

    def _funding_series(self, plan: Mapping, sym: str) -> tuple[list, dict]:
        """Warm-up settlements + the funding log, with the declared interval per
        settlement (legacy plans: no intervals)."""
        d = self.root / plan["plan_id"]
        wj = self._warmup(plan, sym)
        fund = [(int(t), r) for t, r in wj.get("funding", [])]
        ivs = {int(t): v for t, v in (wj.get("funding_intervals") or {}).items() if v}
        if self._integrity(plan):
            logged = read_seq_chain(d / f"funding_{sym}.jsonl", "FUNDING_LOG")
        elif self._funding_policy(plan):
            logged = read_chained_log(d / f"funding_{sym}.jsonl", "funding log")
        else:
            logged = list(iter_jsonl(d / f"funding_{sym}.jsonl"))
        for r in logged:
            fund.append((r["ts"], r["rate"]))
            if r.get("interval_ms"):
                ivs[r["ts"]] = r["interval_ms"]
        return fund, ivs

    def _funding_window_status(self, plan: Mapping, st: Mapping) -> dict:
        """Funding of each symbol over the observation window [start, last
        observed bar] (``funding_schedule.coverage_check``) combined with the
        status of this step's fetch.  Anything but VALID / VALID_ZERO_RATE
        keeps the verdict at PAPER_BLOCKED_DATA."""
        from funding_schedule import coverage_check
        out = {}
        for sym in plan["symbols"]:
            fund, ivs = self._funding_series(plan, sym)
            cov = coverage_check(fund, ivs, plan["start_ts"], st["last_ts"][sym])
            status = _COVERAGE_STATUS.get(cov["status"])
            if cov["status"] == "FUNDING_DATA_MISSING":
                status = "DATA_MISSING" if cov["kind"] == "NO_SERIES" else "COVERAGE_INCOMPLETE"
            if status == "VALID" and cov["settlements"] and cov["zero_rate_settlements"] == cov["settlements"]:
                status = "VALID_ZERO_RATE"
            fetch = st["funding_status"].get(sym)
            if status in FUNDING_USABLE and fetch not in FUNDING_USABLE + ("DATA_MISSING",):
                # complete so far, but this step's fetch could not be confirmed
                status = fetch if fetch in FUNDING_SOURCE_STATUSES else "SOURCE_ERROR"
            out[sym] = {"status": status, "fetch_status": fetch, "coverage": cov,
                        "sources": dict(st["funding_sources"].get(sym) or {})}
        return out

    # -- state integrity (ADR 0073) ------------------------------------------------
    def _state_problems(self, plan: Mapping, st) -> list[str]:
        if not isinstance(st, dict):
            return ["state is not an object"]
        out = []
        if st.get("status") not in ("OBSERVING", "BLOCKED_DATA", "ERROR"):
            out.append(f"state status {st.get('status')!r}")
        if not isinstance(st.get("concluded"), bool) or not isinstance(st.get("errors"), list):
            out.append("state 'concluded' / 'errors' missing")
        keys = ["last_ts", "last_funding_ts", "bars", "missing_bars", "duplicates", "gaps"]
        if self._funding_policy(plan):
            keys += ["funding_sources", "funding_status"]
        if self._integrity(plan):
            keys += ["quality_head", "funding_head"]
        # ADR 0075 (optional keys: states saved before ADR 0075 have neither)
        n = st.get("events_logged")
        if n is not None and (not isinstance(n, int) or isinstance(n, bool) or n < 0):
            out.append("state 'events_logged' is not a record count")
        cut = st.get("evaluation_cutoff")
        if cut is not None:
            lt = st.get("last_ts")
            ts = cut.get("ts") if isinstance(cut, dict) else None
            if not isinstance(ts, int) or isinstance(ts, bool) or ts < plan["start_ts"] or ts % MS15 \
                    or not isinstance(lt, dict) or not lt or any(not isinstance(x, int) or ts > x for x in lt.values()):
                out.append(f"state 'evaluation_cutoff' {cut!r} is not a bar time within the observation")
        for k in keys:
            v = st.get(k)
            if not isinstance(v, dict) or set(v) != set(plan["symbols"]):
                out.append(f"state '{k}' missing or not per symbol")
                continue
            if k in ("last_ts", "last_funding_ts") and not all(
                    isinstance(x, int) and not isinstance(x, bool) for x in v.values()):
                out.append(f"state '{k}' is not an integer time per symbol")
            if k == "funding_sources" and not all(isinstance(x, dict) for x in v.values()):
                out.append("state 'funding_sources' is not a file map per symbol")
            if k == "funding_status" and not all(x is None or x in FUNDING_SOURCE_STATUSES for x in v.values()):
                out.append("state 'funding_status' holds an unknown status")
            if k in ("quality_head", "funding_head") and not all(
                    isinstance(x, dict) and set(x) == {"seq", "hash"} and isinstance(x["seq"], int)
                    and not isinstance(x["seq"], bool) and x["seq"] >= 0 and isinstance(x["hash"], str)
                    and (x["seq"] == 0) == (x["hash"] == "") for x in v.values()):
                out.append(f"state '{k}' is not a {{seq, hash}} head per symbol")
        return out

    def _state_unusable(self, plan: Mapping, why: Sequence[str]) -> dict:
        """The state file cannot be trusted: nothing is observed, evaluated or
        overwritten (the damaged file stays as evidence), no status changes."""
        d = self.root / plan["plan_id"]
        res = {"plan_id": plan["plan_id"], "status": "ERROR", "verdict": "PAPER_ERROR",
               "reasons": ["STATE_CORRUPT: " + "; ".join(why)]}
        append_jsonl(d / "steps.jsonl", {"at": iso_now(), "clock_ms": self.clock(), **res})
        return res

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
        if not self._funding_policy(plan):     # policy plans: re-derived by _evidence (verified chain)
            fl = list(iter_jsonl(self.root / plan["plan_id"] / f"funding_{sym}.jsonl"))
            if fl:
                st["last_funding_ts"][sym] = max(st["last_funding_ts"][sym], max(r["ts"] for r in fl))
        st.setdefault("recovered", []).append({"symbol": sym, "last_ts": rows[-1]["ts"], "at": iso_now()})

    # -- evaluation ------------------------------------------------------------------
    def _series(self, plan: Mapping, sym: str) -> tuple[list[Bar], int, list[dict], list, dict]:
        d = self.root / plan["plan_id"]
        wj = self._warmup(plan, sym)
        warm = [_row_bar(r) for r in wj["rows"]]
        obs = read_bar_log(d / f"bars_{sym}.jsonl")
        fund, ivs = self._funding_series(plan, sym)
        return warm + [_row_bar(r) for r in obs], len(warm), obs, fund, ivs

    def _evaluate(self, plan: Mapping, st: Mapping) -> dict:
        spec = canonicalize_spec(self.p.specs.get(plan["strategy_id"]))
        # the plan's own (registered) rules, never the current config files
        exec_cfg = ExecutionConfig(**plan["execution"])
        mp = float(plan["cost"]["params"]["max_participation"])
        policy = self._funding_policy(plan)
        trades, open_pos, excluded = [], [], {"gap": 0, "no_funding_coverage": 0}
        drop_reasons: dict = {}
        schedules: dict = {}
        for sym in plan["symbols"]:
            bars, w, obs, fund, ivs = self._series(plan, sym)
            if len(bars) - w < 2:
                continue
            gaps = [(a, b) for a, b, _ in st["gaps"][sym]]
            fng = self.p.provider.fng() if "fng" in spec["data"] else None
            funding = sorted(set(fund))
            if policy:
                # ADR 0073: confirmed funding with its DECLARED schedule — never None
                # (= 0 bp) and never the nominal-8h legacy rule
                aux = build_spec_aux(bars, spec, funding, fng)
                sig = build_spec_signal(spec, aux)
                res = run_event_study(bars, sym, sig, spec["exit"]["horizon"], self.p.cost,
                                      plan["order_value"], funding, warmup=max(1, w),
                                      exit_rule=stop_exit_rule(spec), funding_intervals=ivs,
                                      require_funding=True)
            else:   # pre-ADR 0073 plan: evaluated as before, its verdict is blocked
                aux = build_spec_aux(bars, spec, funding or None, fng)
                sig = build_spec_signal(spec, aux)
                res = run_event_study(bars, sym, sig, spec["exit"]["horizon"], self.p.cost,
                                      plan["order_value"], funding or None, warmup=max(1, w),
                                      exit_rule=stop_exit_rule(spec))
            excluded["no_funding_coverage"] += res.dropped_missing_funding
            for k, v in res.funding_drop_reasons.items():
                drop_reasons[k] = drop_reasons.get(k, 0) + v
            schedules[sym] = res.funding_schedule
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
            open_pos += self._open_positions(sym, bars, w, sig, spec["exit"]["horizon"], res.events)
        self._reconcile_trades(plan, trades)
        return {"trades": trades, "open_positions": open_pos, "excluded": excluded,
                "funding_drop_reasons": drop_reasons, "funding_schedule": schedules}

    @staticmethod
    def _open_positions(sym: str, bars: Sequence[Bar], w: int, sig, horizon: int, events) -> list[dict]:
        """The position the strategy holds at the last observed bar, if any (ADR 0075).

        Mirrors ``run_event_study``: after the last ACCEPTED exit, a signal whose
        holding period fits the bars and is not an event was refused (participation,
        cost data) or dropped (funding) — no position, the scan continues (the
        earlier ``break`` here stopped at it and missed a later open position); the
        first signal whose holding period runs past the bars is the open position
        (every later signal overlaps it).  The mark is informational only: it is
        never part of a verdict."""
        last_exit = max([e.exit_index for e in events] + [w - 1])
        for t in range(max(w, last_exit), len(bars) - 1):
            side = sig(PrefixView(bars, t + 1))
            if side not in (1, -1):
                continue
            ent = t + 1
            if ent + horizon < len(bars):
                continue
            p0, p1 = bars[ent].open, bars[-1].close
            ok = all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in (p0, p1))
            return [{"event_id": f"{sym}:{bars[ent].ts}", "symbol": sym, "status": "OPEN", "side": side,
                     "entry_ts": bars[ent].ts, "entry_index": ent, "decision_ts": bars[t].ts,
                     "planned_exit_ts": bars[ent].ts + horizon * MS15, "bars_held": len(bars) - ent,
                     "unrealized_gross_bp": side * (p1 / p0 - 1.0) * 1e4 if ok else None,
                     "marked_at": bars[-1].ts,
                     "note": "approximate gross mark-to-market (no cost / funding / execution); "
                             "informational, never part of a verdict"}]
        return []

    def _trade_log_len(self, plan: Mapping) -> int:
        try:
            return len(list(iter_jsonl(self.root / plan["plan_id"] / "events.jsonl")))
        except (JsonlCorruption, OSError, ValueError) as e:
            raise EvidenceIntegrityError("TRADE_LOG_UNREADABLE", f"events.jsonl: {type(e).__name__}: {e}") from e

    @staticmethod
    def _grid_bar(plan: Mapping, days: float) -> int:
        """Bar time T at which ``days`` of observation are complete: (T + 15m - start) >= days."""
        span = -(-int(math.ceil(float(days) * DAY_MS)) // MS15) * MS15
        return plan["start_ts"] + max(span, MS15) - MS15

    @classmethod
    def _window_end(cls, plan: Mapping) -> int:
        """Last bar of the preregistered observation window (start + max_days)."""
        return cls._grid_bar(plan, plan["criteria"]["max_days"])

    @staticmethod
    def _counted(plan: Mapping, trades: Sequence[Mapping]) -> list:
        """Closed trades that count for the verdict (filled, and in time for a real-time feed)."""
        out = [t for t in trades if t.get("exec_status") in ("FILLED", "PARTIAL", "LIQUIDATED")]
        return [t for t in out if not t.get("late")] if plan["feed_realtime"] else out

    @classmethod
    def _rule_cutoff(cls, plan: Mapping, st: Mapping, ledger: Sequence[Mapping]) -> Optional[int]:
        """ADR 0076: the earliest bar T at which the preregistered stopping rule holds,
        or None while it does not hold on what every symbol has observed.

        At bar T the rule holds when min_days are complete and min_trades counted
        trades have EXITED by T (a trade is known closed once its exit bar is
        observed), or when T is the last bar of the max_days window.  T only
        depends on the ledger (which is checked against the evaluator), never on
        when or how often step() ran.  The common observation time is the minimum
        over the symbols: a later bound would cut the sample at a time some symbol
        has not been observed to, whose entries before it (closed or open) are not
        known yet."""
        c = plan["criteria"]
        wend = cls._window_end(plan)
        k = int(c["min_trades"])
        exits = sorted(int(t["exit_ts"]) for t in cls._counted(plan, ledger))
        t_days = cls._grid_bar(plan, c["min_days"])
        if k <= 0:
            cand = t_days
        else:
            cand = max(t_days, exits[k - 1]) if len(exits) >= k else wend
        t = min(cand, wend)
        return t if t <= min(st["last_ts"].values()) else None

    @classmethod
    def _sample_bound(cls, plan: Mapping, st: Mapping) -> int:
        """Last entry time of the verdict's sample: the recorded evaluation cutoff,
        or (before the stopping rule held) the last bar observed for every symbol,
        never past the max_days window (ADR 0076)."""
        cut = st.get("evaluation_cutoff")
        return int(cut["ts"]) if cut else min(min(st["last_ts"].values()), cls._window_end(plan))

    @staticmethod
    def _pending(result: Mapping, bound: int) -> list[dict]:
        return [x for x in result.get("open_positions") or [] if x.get("entry_ts") is None or x["entry_ts"] <= bound]

    def _position_problems(self, plan: Mapping, st: Mapping, result: Mapping) -> list[str]:
        """Consistency of the closed-trade ledger on disk, the evaluator's trades and
        open positions of ``result`` and the state (ADR 0075).  Read from disk on
        every call: nothing an earlier call saw is trusted."""
        syms, start = set(plan["symbols"]), plan["start_ts"]
        try:
            logged = list(iter_jsonl(self.root / plan["plan_id"] / "events.jsonl"))
        except (JsonlCorruption, OSError, ValueError) as e:
            return [f"trade ledger unreadable: {type(e).__name__}: {e}"]
        out: list[str] = []
        n_state = st.get("events_logged")
        if n_state is not None and n_state != len(logged):
            out.append(f"trade ledger has {len(logged)} records, the state recorded {n_state}")

        def num(x):
            return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

        def integer(x):
            return isinstance(x, int) and not isinstance(x, bool)

        seen: dict = {}
        by_sym: dict = {}
        for i, r in enumerate(logged):
            sym, eid = r.get("symbol"), r.get("event_id")
            tag = f"trade record {i + 1} ({eid!r})"
            if sym not in syms:
                out.append(f"{tag}: symbol {sym!r} not in the plan")
                continue
            if eid in seen:
                out.append(f"{tag}: DUPLICATE_CLOSE: event {eid} recorded {seen[eid] + 1} times")
                seen[eid] += 1
                continue
            seen[eid] = 1
            e0, e1 = r.get("entry_ts"), r.get("exit_ts")
            if not all(integer(r.get(k)) for k in ("entry_ts", "exit_ts", "entry_index", "exit_index")) \
                    or r.get("side") not in (1, -1):
                out.append(f"{tag}: side / entry / exit missing or not integer")
                continue
            if eid != f"{sym}:{e0}":
                out.append(f"{tag}: event id does not name its symbol and entry")
            if e0 % MS15 or e1 % MS15 or e0 < start or e1 <= e0:
                out.append(f"{tag}: entry {e0} / exit {e1} off the grid, before the start or not ordered")
            elif e1 > st["last_ts"][sym]:
                out.append(f"{tag}: recorded as closed at {e1}, but the last observed bar is "
                           f"{st['last_ts'][sym]} (position still open)")
            status, ff = r.get("exec_status"), r.get("fill_fraction")
            rule = _EXEC_FILL.get(status)
            if rule is None or not num(ff) or not (
                    (rule == "one" and ff == 1.0) or (rule == "zero" and ff == 0.0)
                    or (rule == "part" and 0.0 < ff < 1.0) or (rule == "positive" and 0.0 < ff <= 1.0)):
                out.append(f"{tag}: execution status {status!r} with fill fraction {ff!r}")
            if not (num(r.get("net_bp_exec")) and num(r.get("cost_bp"))):
                out.append(f"{tag}: net / cost not a finite number")
            if not (isinstance(r.get("liquidated"), bool) and isinstance(r.get("late"), bool)) \
                    or r.get("liquidated") != (status == "LIQUIDATED"):
                out.append(f"{tag}: liquidated / late flags inconsistent")
            by_sym.setdefault(sym, []).append((e0, e1, eid))
        last_exit = {}
        for sym, xs in by_sym.items():
            xs.sort()
            for (a0, a1, ea), (b0, b1, eb) in zip(xs, xs[1:]):
                if b0 <= a1:
                    out.append(f"DUPLICATE_POSITION: {eb} entered at {b0} while {ea} was held until {a1}")
            last_exit[sym] = xs[-1][1]
        trades, opens = result.get("trades"), result.get("open_positions")
        if not isinstance(trades, list) or not isinstance(opens, list):
            return out + ["POSITIONS_NOT_EVALUATED: no trades / open positions from the evaluator in this step"]
        now = {}
        for t in trades:
            if t.get("event_id") in now:
                out.append(f"evaluator produced trade {t.get('event_id')} twice")
            now[t.get("event_id")] = t
        led = {r.get("event_id"): r for r in logged}
        for eid in sorted(set(now) - set(led), key=str):
            out.append(f"closed trade {eid} of the evaluator is not in the ledger")
        for eid in sorted(set(led) - set(now), key=str):
            out.append(f"ledger record {eid} is not a trade of the evaluator")
        for eid in sorted(set(now) & set(led), key=str):
            a, b = now[eid], led[eid]
            diff = [k for k in _TRADE_KEYS if not (
                a.get(k) == b.get(k) or (num(a.get(k)) and num(b.get(k)) and abs(a[k] - b[k]) <= 1e-9))]
            if diff:
                out.append(f"ledger record {eid} differs from the evaluator in {diff}")
        per_sym: dict = {}
        for x in opens:
            sym, e0 = x.get("symbol"), x.get("entry_ts")
            tag = f"open position {x.get('event_id')!r}"
            if sym not in syms or x.get("side") not in (1, -1) or not integer(e0) or e0 % MS15:
                out.append(f"{tag}: symbol / side / entry missing or invalid")
                continue
            per_sym[sym] = per_sym.get(sym, 0) + 1
            if per_sym[sym] > 1:
                out.append(f"DUPLICATE_POSITION: {per_sym[sym]} open positions on {sym}")
            if not (start <= e0 <= st["last_ts"][sym]):
                out.append(f"{tag}: entry {e0} outside [start, last observed bar]")
            if x.get("marked_at") != st["last_ts"][sym] or not num(x.get("unrealized_gross_bp")):
                out.append(f"{tag}: MARK_MISSING: no valid mark at the last observed bar "
                           f"({x.get('marked_at')!r}, {x.get('unrealized_gross_bp')!r})")
            if x.get("event_id") != f"{sym}:{e0}" or x.get("event_id") in led:
                out.append(f"{tag}: also recorded as a closed trade (or misnamed)")
            if sym in last_exit and e0 <= last_exit[sym]:
                out.append(f"{tag}: entered at {e0}, before the last recorded exit {last_exit[sym]}")
        return out

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

    def metrics(self, plan_id: str, st: Optional[Mapping] = None, *, bound: Optional[int] = None) -> dict:
        plan = self.plan(plan_id)
        st = st if st is not None else self.state(plan_id)
        # ADR 0075: the sample is every closed trade ENTERED by the sample bound (the
        # evaluation cutoff once recorded); open positions are never part of it.
        # ADR 0076: ``bound`` is given by the verdict (the rule's cutoff before it is recorded)
        bound = self._sample_bound(plan, st) if bound is None else int(bound)
        allt = list(iter_jsonl(self.root / plan_id / "events.jsonl"))
        trades = [t for t in allt if t["entry_ts"] <= bound]
        counted = [t for t in trades if t["exec_status"] in ("FILLED", "PARTIAL", "LIQUIDATED")]
        counted_v = self._counted(plan, trades)
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
        days = max(0.0, (bound + MS15 - plan["start_ts"]) / DAY_MS)
        # ADR 0076: data coverage of the sample period [start, bound] only (bars observed
        # after the cutoff, and their gaps, are not part of the verdict)
        expected = sum(max(0, (min(st["last_ts"][s], bound) - plan["start_ts"]) // MS15 + 1)
                       for s in plan["symbols"])
        missing = 0
        for s in plan["symbols"]:
            for a, b, _ in st["gaps"][s]:
                missing += max(0, (min(b - MS15, bound) - a) // MS15)
        return {"n_trades": n, "mean_net_bp": mean, "hac_t": tstat if math.isfinite(tstat) else None,
                "hac_p_one_sided": pval, "stressed_mean_bp": (sum(stress) / len(stress)) if stress else None,
                "mdd_bp": mdd, "days_observed": days, "missing_bar_share": (missing / expected) if expected else None,
                "liquidations": sum(1 for t in counted if t["liquidated"]),
                "rejected_orders": sum(1 for t in trades if t["exec_status"].startswith("REJECTED")),
                "late_share": (sum(1 for t in counted if t["late"]) / len(counted)) if counted else None,
                "mean_holding_minutes": (sum(t["holding_minutes"] for t in counted) / len(counted)) if counted else None,
                "daily_net_bp": {str(k): v for k, v in sorted(daily.items())},
                "bars_observed": dict(st["bars"]), "duplicates_skipped": dict(st["duplicates"]),
                "incomplete_skipped": st["incomplete_skipped"],
                "sample_bound_ts": bound, "window_end_ts": self._window_end(plan),
                "evaluation_cutoff_ts": (st.get("evaluation_cutoff") or {}).get("ts"),
                "trades_entered_after_bound": len(allt) - len(trades)}

    def _verdict(self, plan: Mapping, st: Mapping, result: Mapping) -> dict:
        if st["status"] == "ERROR":
            return {"verdict": "PAPER_ERROR", "final": False, "reasons": [e["detail"] for e in st["errors"][-1:]]}
        if st["status"] == "BLOCKED_DATA":
            return {"verdict": "PAPER_BLOCKED_DATA", "final": False,
                    "reasons": [json.dumps(e, default=str) for e in st["errors"][-1:]]}
        # ADR 0073: funding not confirmed -> no verdict at all (neither PASSED nor FAILED)
        if not self._funding_policy(plan):
            return {"verdict": "PAPER_BLOCKED_DATA", "final": False,
                    "reasons": ["LEGACY_PLAN_FUNDING_UNVERIFIED: plan registered before ADR 0073 "
                                "(no declared funding schedule / source record); re-verify with a new plan"]}
        if not self._integrity(plan):
            return {"verdict": "PAPER_BLOCKED_DATA", "final": False,
                    "reasons": ["LEGACY_PLAN_INTEGRITY_UNVERIFIED: plan registered before ADR 0074"]}
        # ADR 0074: the evidence is re-verified against the state right before any verdict
        problems = self._evidence(plan, st, recover=False) + self._plan_drift(plan)
        if problems:
            return {"verdict": "PAPER_ERROR", "final": False,
                    "reasons": ["EVIDENCE_INTEGRITY: " + "; ".join(problems[:10])]}
        if result.get("funding_ok") is not True:
            why = [f"FUNDING_{v['status']}: {sym}: {v['coverage'].get('reason') or v['fetch_status']}"
                   for sym, v in sorted((result.get("funding") or {}).items())
                   if v["status"] not in FUNDING_USABLE]
            if result.get("funding_dropped"):
                why.append(f"FUNDING_COVERAGE_INCOMPLETE: {result['funding_dropped']['events']} events "
                           f"without funding coverage {result['funding_dropped']['reasons']}")
            return {"verdict": "PAPER_BLOCKED_DATA", "final": False,
                    "reasons": why or ["FUNDING_UNVERIFIED: funding was not checked in this step"]}
        # ADR 0075: positions — the ledger, the evaluator's trades and open positions agree
        pos = self._position_problems(plan, st, result)
        if pos:
            return {"verdict": "PAPER_ERROR", "final": False, "reasons": ["POSITION_INTEGRITY: " + "; ".join(pos[:10])]}
        # ADR 0076: the cutoff is the earliest bar at which the rule holds (from the ledger)
        ledger = list(iter_jsonl(self.root / plan["plan_id"] / "events.jsonl"))
        rule_t = self._rule_cutoff(plan, st, ledger)
        observed = min(st["last_ts"].values())
        wend = self._window_end(plan)
        now = self.clock()
        result["observation_clock"] = {"clock_ms": now, "data_through_ts": observed, "window_end_ts": wend,
                                       "clock_days": (now - plan["start_ts"]) / DAY_MS,
                                       "data_days": max(0.0, (observed + MS15 - plan["start_ts"]) / DAY_MS)}
        cut = st.get("evaluation_cutoff")
        if cut is not None and cut.get("ts") != rule_t:
            return {"verdict": "PAPER_ERROR", "final": False,
                    "reasons": [f"EVALUATION_CUTOFF_INVALID: recorded cutoff {cut.get('ts')} is not the earliest "
                                f"bar at which the stopping rule holds ({rule_t}; {CUTOFF_POLICY})"]}
        bound = rule_t if rule_t is not None else self._sample_bound(plan, st)
        m = self.metrics(plan["plan_id"], st, bound=bound)
        result["metrics"] = dict(m)          # always this call's metrics, never an earlier one
        d = self._decide(plan, m)
        if rule_t is None:
            if now >= wend + MS15:
                # the preregistered window is over on the clock but its bars were not observed
                return {"verdict": "PAPER_BLOCKED_DATA", "final": False,
                        "reasons": [f"OBSERVATION_WINDOW_NOT_COVERED: the {plan['criteria']['max_days']}-day window "
                                    f"ended at {wend} on the clock ({result['observation_clock']['clock_days']:.1f} "
                                    f"days), data observed through {observed} "
                                    f"({result['observation_clock']['data_days']:.1f} days): no verdict on "
                                    f"bars that were not observed"]}
            c = plan["criteria"]
            n_closed = sum(1 for t in self._counted(plan, ledger) if t["exit_ts"] <= bound)
            why = [f"{m['days_observed']:.1f}/{c['min_days']} days, {n_closed}/{c['min_trades']} trades"]
            lag = result["observation_clock"]["clock_days"] - result["observation_clock"]["data_days"]
            if lag > 1.0:
                why.append(f"DATA_LAG: data observed through {result['observation_clock']['data_days']:.1f} "
                           f"days at {result['observation_clock']['clock_days']:.1f} days on the clock")
            return {"verdict": "PAPER_OBSERVING", "final": False, "reasons": why}
        if not d["final"]:      # cannot happen: the rule's cutoff always gives a final decision
            return {"verdict": "PAPER_ERROR", "final": False,
                    "reasons": [f"EVALUATION_CUTOFF_INVALID: the criteria are not final at the rule's cutoff "
                                f"{bound} ({'; '.join(d['reasons'])})"]}
        pending = self._pending(result, bound)
        result["positions"] = {"policy": POSITION_POLICY, "sample_bound_ts": bound,
                               "closed_in_sample": m["n_trades"], "pending": pending,
                               "open_after_bound": [x for x in result["open_positions"] if x not in pending]}
        if pending:
            # the stopping rule holds, but positions of the sample are still open: their
            # result is not known yet, so nothing is concluded (no mark, no forced exit)
            return {"verdict": "PAPER_OBSERVING", "final": False, "evaluation_cutoff": bound,
                    "reasons": [f"OPEN_POSITIONS_PENDING: {len(pending)} position(s) entered by the evaluation "
                                f"cutoff {bound} not closed yet: "
                                + ", ".join(f"{x['event_id']} (planned exit {x.get('planned_exit_ts')})"
                                            for x in pending)]}
        return {**d, "evaluation_cutoff": bound}

    @staticmethod
    def _decide(plan: Mapping, m: Mapping) -> dict:
        """The preregistered criteria on the metrics of the sample (shared by the
        verdict and the last gate, ADR 0075)."""
        c = plan["criteria"]
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

    def _conclusion_problems(self, plan: Mapping, result: Mapping, st: Optional[Mapping],
                             verdict: Optional[Mapping] = None) -> list[str]:
        """Every precondition of a final verdict, checked again from disk and
        independently of :meth:`_verdict` (ADR 0073 / 0074)."""
        why = []
        if not self._funding_policy(plan):
            why.append("plan has no funding policy (registered before ADR 0073)")
        if not self._integrity(plan):
            why.append("plan has no evidence integrity record (registered before ADR 0074)")
        if result.get("funding_ok") is not True:
            why.append("funding of this step not confirmed")
        if (result.get("excluded") or {}).get("no_funding_coverage") or result.get("funding_dropped"):
            why.append("events were dropped for missing funding coverage")
        if why:
            return why
        if st is None:
            st = self.state(plan["plan_id"])
        try:
            fund = self._funding_window_status(plan, st)
        except (EvidenceIntegrityError, CausalityViolation, JsonlCorruption, OSError, ValueError) as e:
            return [f"funding evidence unreadable: {e}"]
        why += [f"{sym}: funding {v['status']} ({v['coverage'].get('reason')})"
                for sym, v in sorted(fund.items()) if v["status"] not in FUNDING_USABLE]
        why += self._evidence(plan, dict(st), recover=False)
        why += self._plan_drift(plan)
        # ADR 0075: positions and the sample, again from disk
        why += self._position_problems(plan, st, result)
        cut = st.get("evaluation_cutoff")
        if not isinstance(cut, Mapping) or not isinstance(cut.get("ts"), int):
            why.append("no evaluation cutoff recorded for a final verdict")
            return why
        try:
            rule_t = self._rule_cutoff(plan, st, list(iter_jsonl(self.root / plan["plan_id"] / "events.jsonl")))
        except (JsonlCorruption, OSError, ValueError, KeyError, TypeError) as e:
            why.append(f"cutoff cannot be derived from the ledger: {type(e).__name__}: {e}")
            return why
        if rule_t != cut["ts"]:
            why.append(f"recorded cutoff {cut['ts']} is not the earliest bar at which the rule holds ({rule_t})")
        pend = self._pending(result, cut["ts"])
        if pend:
            why.append(f"{len(pend)} position(s) of the sample still open: " + ", ".join(
                str(x.get("event_id")) for x in pend))
        try:
            fresh = self.metrics(plan["plan_id"], st)
        except (JsonlCorruption, OSError, ValueError, KeyError, TypeError) as e:
            why.append(f"metrics cannot be recomputed from the ledger: {type(e).__name__}: {e}")
            return why
        # the verdict computed them before the cutoff was recorded: the bound is the
        # same (sample_bound_ts is compared), only the cutoff field differs
        def same(m):
            return canonical_json({k: v for k, v in dict(m or {}).items() if k != "evaluation_cutoff_ts"})
        if not isinstance(result.get("metrics"), Mapping) or same(fresh) != same(result.get("metrics")):
            why.append("metrics of the verdict differ from the ledger on disk")
        if verdict is not None:
            d = self._decide(plan, fresh)
            if not d["final"] or d["verdict"] != verdict.get("verdict"):
                why.append(f"the criteria on the ledger give {d['verdict']}, not {verdict.get('verdict')}")
        return why

    def _conclude(self, plan: Mapping, verdict: Mapping, result: Mapping, st: Optional[Mapping] = None) -> None:
        why = self._conclusion_problems(plan, result, st, verdict)
        if why:
            # unreachable through _verdict; a second, independent gate so no path records a
            # final paper verdict on unconfirmed funding or unverified evidence (ADR 0073/0074)
            raise ConclusionRefused(f"{plan['plan_id']}: final verdict refused: " + "; ".join(why[:10]))
        st_now = st if st is not None else self.state(plan["plan_id"])
        cut_ts = (st_now.get("evaluation_cutoff") or {}).get("ts")
        anchors = self._anchors(plan, st_now)
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
                                   "positions": {"policy": POSITION_POLICY,
                                                 "evaluation_cutoff_ts": cut_ts,
                                                 "closed_in_sample": (result.get("metrics") or {}).get("n_trades"),
                                                 "open_after_cutoff": (result.get("positions") or {}).get(
                                                     "open_after_bound", [])},
                                   # ADR 0076: what the verdict was computed from, for a later audit
                                   "anchors": anchors,
                                   "funding_evidence": {"policy": FUNDING_POLICY,
                                                        "schedule": result.get("funding_schedule"),
                                                        "symbols": result.get("funding")},
                                   "is_simulation": True, "orders_sent": 0})
        self.p.statuses.set(sid, ctx, to, stage="paper_forward", labels=labels,
                            reasons=list(verdict["reasons"]) + [
                                "forward paper is a research result — never approval for live trading"],
                            evidence={"paper_forward": rec["record_id"]})
        append_jsonl(self.p.dir / "research_feedback.jsonl",
                     {"strategy_id": sid, "context_id": ctx, "source": "paper_forward",
                      "verdict": verdict["verdict"], "use": "human review + failure memory only; never search ranking",
                      "at": iso_now()})

    # -- conclusion anchors (ADR 0076) ------------------------------------------------
    def _anchors(self, plan: Mapping, st: Mapping) -> dict:
        """sha256 / length / chain heads of every file the final verdict was computed from.
        Part of the evaluation record (and so of its record id)."""
        d = self.root / plan["plan_id"]
        ev = d / "events.jsonl"
        raw = ev.read_bytes() if ev.exists() else b""
        bars = {}
        for sym in plan["symbols"]:
            rows = read_bar_log(d / f"bars_{sym}.jsonl")
            bars[sym] = {"rows": len(rows), "head": rows[-1]["hash"] if rows else ""}
        cut = st.get("evaluation_cutoff") or {}
        return {"policy": CUTOFF_POLICY, "evaluation_cutoff_ts": cut.get("ts"), "cutoff_rule": cut.get("rule"),
                "events_logged": len(list(iter_jsonl(ev))) if ev.exists() else 0,
                "events_sha256": hashlib.sha256(raw).hexdigest(), "bar_logs": bars,
                "quality_head": dict(st.get("quality_head") or {}), "funding_head": dict(st.get("funding_head") or {}),
                "last_ts": dict(st["last_ts"]), "warmup_sha256": dict(plan.get("warmup_sha256") or {})}

    def verify_conclusion(self, plan_id: str) -> list[str]:
        """Re-check a concluded plan against the anchors of its evaluation record
        (ADR 0076).  Returns the problems found ([] = the files on disk are the ones
        the verdict was computed from, and they still give the same cutoff, metrics
        and verdict).  Read only: nothing is repaired or written."""
        plan = self.plan(plan_id)
        rec = self.p.evaluation("paper_forward", plan["strategy_id"], plan["context_id"])
        if rec is None or rec.get("plan_id") != plan_id:
            return ["no final evaluation record for this plan"]
        a = rec.get("anchors")
        if not isinstance(a, Mapping):
            return ["evaluation record has no anchors (concluded before ADR 0076)"]
        out = []
        try:
            st = self.state(plan_id)
        except (OSError, ValueError) as e:
            return [f"state unreadable: {type(e).__name__}: {e}"]
        try:
            now = self._anchors(plan, st)
        except (CausalityViolation, JsonlCorruption, OSError, ValueError, KeyError) as e:
            return [f"evidence unreadable: {type(e).__name__}: {e}"]
        for k in ("events_sha256", "events_logged", "evaluation_cutoff_ts", "quality_head", "funding_head",
                  "last_ts", "warmup_sha256"):
            if canonical_json(now.get(k)) != canonical_json(a.get(k)):
                out.append(f"{k} differs from the evaluation record")
        for sym, b in (a.get("bar_logs") or {}).items():
            got = now["bar_logs"].get(sym) or {}
            if got.get("rows", 0) < b.get("rows", 0):
                out.append(f"bars_{sym}.jsonl is shorter than when the verdict was computed")
                continue
            rows = read_bar_log(self.root / plan_id / f"bars_{sym}.jsonl")
            if b.get("rows") and rows[b["rows"] - 1]["hash"] != b.get("head"):
                out.append(f"bars_{sym}.jsonl was rewritten after the verdict")
        ledger = list(iter_jsonl(self.root / plan_id / "events.jsonl"))
        try:
            rule_t = self._rule_cutoff(plan, st, ledger)
        except (KeyError, TypeError, ValueError) as e:
            return out + [f"cutoff cannot be derived from the ledger: {type(e).__name__}: {e}"]
        if rule_t != a.get("evaluation_cutoff_ts"):
            out.append(f"the ledger gives cutoff {rule_t}, the record {a.get('evaluation_cutoff_ts')}")
        if rule_t is not None:
            m = self.metrics(plan_id, st, bound=rule_t)
            same = lambda x: canonical_json({k: v for k, v in dict(x or {}).items()  # noqa: E731
                                             if k not in ("evaluation_cutoff_ts",)})
            if same(m) != same(rec.get("metrics")):
                out.append("metrics recomputed from the ledger differ from the evaluation record")
            d = self._decide(plan, m)
            if d["verdict"] != rec.get("verdict"):
                out.append(f"the ledger gives {d['verdict']}, the record {rec.get('verdict')}")
        return out
