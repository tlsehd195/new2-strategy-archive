"""Funding settlement completeness for one holding window (ADR 0071).

A funding series that covers the first and the last moment of a hold can
still miss a settlement in the middle (00:00 present, 08:00 missing, 16:00
present).  Summing the recorded settlements would then charge 0 for the
missing one.  This module checks the WHOLE holding window against the
settlement schedule the data itself declares, and never fills a missing
settlement with 0.

Schedule source: the Binance USD-M public archive (data.binance.vision,
``fundingRate/<SYM>/<SYM>-fundingRate-<YYYY-MM>.zip``) has the columns
``calc_time, funding_interval_hours, last_funding_rate``.  ``calc_time`` is
the settlement time in ms (UTC, a few ms of jitter), ``funding_interval_hours``
the interval of that settlement (8 for most symbols; 4 for HYPEUSDT; 2/4/8
over time for SOLUSDT in the archived files) and ``last_funding_rate`` the
rate per settlement (not annualised).  :func:`data_quality.load_funding_csv_checked`
keeps the interval per settlement time as ``intervals`` ({ts_ms: interval_ms}).
No interval is hard-coded here: a series without a declared interval for a
settlement on the path is FUNDING_SCHEDULE_UNKNOWN, never "assumed 8h".

Holding window rule (same as ``evaluator.FundingIndex``): a position opened
at the open of ``start`` and closed at ``end`` holds the settlements t with
``start < t <= end``.

Statuses (worst first in :data:`FUNDING_STATUS_RANK`):

* ``FUNDING_DATA_INVALID``    duplicated / unsorted timestamps, non-integer
  time, non-finite rate, symbol or exchange different from the position's;
* ``FUNDING_DATA_MISSING``    no series, or the series starts after the
  entry / ends before the exit (coverage);
* ``FUNDING_SCHEDULE_UNKNOWN`` a settlement on the path has no declared
  interval (``intervals`` absent or incomplete);
* ``FUNDING_SETTLEMENT_GAP``  two consecutive settlements on the path are
  further apart than the declared interval (+ tolerance): settlements are
  missing in between;
* ``OK``                      every expected settlement is present.  A
  recorded rate of exactly 0 is a real observation and is OK.

Pure functions, no I/O.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from typing import Mapping, Optional, Sequence

FUNDING_SCHEDULE_VERSION = "funding-schedule-v2"   # v2: both interval readings per step (ADR 0078)
#: timestamp jitter accepted between two settlements (same as data_quality.FUNDING_TOL_MS)
FUNDING_TOL_MS = 60_000

FUNDING_STATUS_RANK = {"OK": 0, "FUNDING_SETTLEMENT_GAP": 1, "FUNDING_SCHEDULE_UNKNOWN": 2,
                       "FUNDING_DATA_MISSING": 3, "FUNDING_DATA_INVALID": 4}


def series_issues(pairs: Optional[Sequence], *, meta: Optional[Mapping] = None,
                  expected_symbol: Optional[str] = None,
                  expected_exchange: Optional[str] = None) -> list[str]:
    """Problems of the series itself (any one makes it FUNDING_DATA_INVALID).
    The series is checked in the order it was handed in: it is never sorted
    or de-duplicated here, so an unsorted input is reported, not repaired."""
    out: list[str] = []
    if not pairs:
        return out
    prev = None
    for t, r in pairs:
        if isinstance(t, bool) or not isinstance(t, int):
            out.append(f"non-integer settlement time {t!r}")
            break
        if not isinstance(r, (int, float)) or isinstance(r, bool) or not math.isfinite(r):
            out.append(f"non-finite funding rate at {t}")
            break
        if prev is not None:
            if t == prev:
                out.append(f"duplicate settlement time {t}")
                break
            if t < prev:
                out.append(f"settlement times not sorted at {t}")
                break
        prev = t
    if meta:
        if expected_symbol is not None and meta.get("symbol") not in (None, expected_symbol):
            out.append(f"funding series of {meta.get('symbol')!r} used for {expected_symbol!r}")
        if expected_exchange is not None and meta.get("exchange") not in (None, expected_exchange):
            out.append(f"funding series of exchange {meta.get('exchange')!r}, expected {expected_exchange!r}")
    return out


def settlement_check(pairs: Optional[Sequence], intervals: Optional[Mapping[int, int]],
                     start_ts: int, end_ts: int, *, tol_ms: int = FUNDING_TOL_MS,
                     issues: Optional[Sequence[str]] = None) -> dict:
    """Completeness of the settlements in ``(start_ts, end_ts]``.

    ``pairs`` must be sorted (``issues`` from :func:`series_issues` may be
    passed to avoid re-validating per window).  Returns a dict with
    ``status``, ``settlements`` (count in the window), ``gaps`` (list of
    [a, b, declared_interval_ms]), ``missing_estimate``, ``schedule_changes``
    and ``reason``."""
    out = {"status": "OK", "settlements": 0, "gaps": [], "missing_estimate": 0,
           "schedule_changes": 0, "reason": None, "version": FUNDING_SCHEDULE_VERSION}
    if end_ts <= start_ts:
        return out                                  # nothing held over a settlement time
    if pairs is None or len(pairs) == 0:
        out.update(status="FUNDING_DATA_MISSING", reason="no funding series")
        return out
    bad = list(issues) if issues is not None else series_issues(pairs)
    if bad:
        out.update(status="FUNDING_DATA_INVALID", reason="; ".join(bad))
        return out
    times = [t for t, _ in pairs]
    if times[0] > start_ts:
        out.update(status="FUNDING_DATA_MISSING",
                   reason=f"series starts at {times[0]} after the entry {start_ts}")
        return out
    if times[-1] < end_ts:
        out.update(status="FUNDING_DATA_MISSING",
                   reason=f"series ends at {times[-1]} before the exit {end_ts}")
        return out
    k0 = bisect_right(times, start_ts) - 1          # last settlement at or before the entry
    k1 = bisect_left(times, end_ts)                 # first settlement at or after the exit
    out["settlements"] = bisect_right(times, end_ts) - bisect_right(times, start_ts)
    if intervals is None:
        out.update(status="FUNDING_SCHEDULE_UNKNOWN", reason="no declared settlement interval")
        return out
    prev_iv = None
    unknown = []
    ambiguous = []
    for j in range(k0 + 1, k1 + 1):
        iv = intervals.get(times[j])
        if not (isinstance(iv, (int, float)) and iv > 0):
            unknown.append(times[j])
            continue
        if prev_iv is not None and iv != prev_iv:
            out["schedule_changes"] += 1
        prev_iv = iv
        # ADR 0078 (C-03): the step is judged under BOTH readings of the interval
        # column (period ending vs starting at the settlement), exactly as the
        # Paper Forward coverage rule does (_step_status, ADR 0074).  Reading it one
        # way only let a 4h -> 8h change pass with one settlement uncharged.
        st, _lo, hi = _step_status(times, intervals, j, tol_ms)
        if st == "FUNDING_SCHEDULE_UNKNOWN":
            unknown.append(times[j - 1])
        elif st == "SCHEDULE_CHANGE_AMBIGUOUS":
            ambiguous.append([times[j - 1], times[j]])
        elif st == "FUNDING_SETTLEMENT_GAP":
            step = times[j] - times[j - 1]
            out["gaps"].append([times[j - 1], times[j], int(hi)])
            out["missing_estimate"] += max(1, int(round(step / hi)) - 1)
    if unknown:
        out.update(status="FUNDING_SCHEDULE_UNKNOWN",
                   reason=f"{len(unknown)} settlements without a declared interval (first {unknown[0]})")
        return out
    if ambiguous:
        out.update(status="FUNDING_SCHEDULE_UNKNOWN",
                   reason=f"SCHEDULE_CHANGE_AMBIGUOUS: {len(ambiguous)} steps contiguous under one "
                          f"reading of the interval column only (first {ambiguous[0]})")
        return out
    if out["gaps"]:
        out.update(status="FUNDING_SETTLEMENT_GAP",
                   reason=f"{out['missing_estimate']} settlements missing in {len(out['gaps'])} gaps")
    return out


def worst(statuses: Sequence[str]) -> str:
    return max(statuses, key=lambda s: FUNDING_STATUS_RANK[s]) if statuses else "OK"


#: version of the observation-window coverage rule (ADR 0074)
COVERAGE_VERSION = "funding-coverage-v2"


def _step_status(times: Sequence[int], intervals: Mapping[int, int], j: int, tol_ms: int) -> tuple:
    """Contiguity of the step ``times[j-1] -> times[j]`` (ADR 0074).

    The archive column ``funding_interval_hours`` is stored per settlement,
    but no Binance document says whether it is the length of the period
    ENDING at that settlement or of the period STARTING there.  The step is
    therefore judged under both readings at once: with ``lo``/``hi`` the
    smaller/larger of the two declared values,

    * ``step <= lo + tol``          -> contiguous under either reading (OK)
    * ``lo + tol < step <= hi + tol`` -> contiguous under one reading only
      (a schedule change whose meaning is unproven): SCHEDULE_UNKNOWN
    * ``step > hi + tol``           -> settlements missing under both: GAP

    Returns (status, lo, hi); status None = OK."""
    a, b = times[j - 1], times[j]
    iv_b, iv_a = intervals.get(b), intervals.get(a)
    if not (isinstance(iv_b, (int, float)) and iv_b > 0) or not (isinstance(iv_a, (int, float)) and iv_a > 0):
        return "FUNDING_SCHEDULE_UNKNOWN", None, None
    lo, hi = min(iv_a, iv_b), max(iv_a, iv_b)
    step = b - a
    if step <= lo + tol_ms:
        return None, lo, hi
    if step <= hi + tol_ms:
        return "SCHEDULE_CHANGE_AMBIGUOUS", lo, hi
    return "FUNDING_SETTLEMENT_GAP", lo, hi


def coverage_check(pairs: Optional[Sequence], intervals: Optional[Mapping[int, int]],
                   start_ts: int, end_ts: int, *, tol_ms: int = FUNDING_TOL_MS) -> dict:
    """Dataset-level completeness of an OBSERVATION window [start_ts, end_ts]
    (Paper Forward; ADR 0073, boundary and schedule rules ADR 0074).

    Time definitions (ms, UTC):

    * ``start_ts``  first bar open of the plan; ``end_ts`` open of the last
      observed (closed) bar.  A trade holds the settlements t with
      ``entry < t <= exit`` (:class:`evaluator.FundingIndex`), so every
      settlement t with ``start_ts < t <= end_ts`` can be charged.
    * ``tol_ms``    timestamp jitter of a recorded settlement (calc_time is a
      few ms after the nominal time).  It is ONLY used to compare recorded
      times with each other / with a due time; it never makes a due
      settlement optional.
    * a settlement interval ``iv`` is DECLARED per settlement by the source.

    Rules:

    * head  — a settlement at or before ``start_ts`` must exist;
    * interior — every step between consecutive recorded settlements from the
      last one at or before ``start_ts`` to the last one at or before
      ``end_ts`` is judged by :func:`_step_status` (both readings of the
      declared interval, see there);
    * tail  — with ``L`` the last recorded settlement at or before ``end_ts``
      and ``iv`` its declared interval, the next settlement is DUE at
      ``D = L + iv``.  It is due inside the window iff ``D - tol_ms <= end_ts``
      (so a settlement due exactly at ``end_ts`` — or a few ms after it
      because ``L`` itself carries jitter — is due).  A due settlement must
      be recorded: the next recorded settlement after ``L`` must exist and
      its step must pass :func:`_step_status`; otherwise
      FUNDING_SETTLEMENT_GAP (``TAIL_DUE_MISSING``).  When ``D - tol_ms >
      end_ts`` nothing is due yet (OK).  Residual assumption, recorded in
      ``tail_assumption``: the interval after ``L`` is the one declared for
      ``L`` (a schedule SHORTENED right after ``L`` is only seen when the next
      settlement arrives; no trade is charged past ``L`` before that, because
      the per-trade check needs a recorded settlement at or after the exit).

    The per-trade check (:func:`settlement_check`) uses the same holding
    window and tolerance and only the interval declared for the later
    settlement; this window check is never weaker than it (``lo <= iv_b``).

    Statuses: the :data:`FUNDING_STATUS_RANK` vocabulary.  ``kind`` says
    which part failed (``NO_SERIES``, ``SERIES``, ``HEAD_NOT_COVERED``,
    ``INTERIOR``, ``SCHEDULE_CHANGE_AMBIGUOUS``, ``TAIL_DUE_MISSING``,
    ``TAIL_SCHEDULE_UNKNOWN``).  ``zero_rate_settlements`` counts recorded
    rates of exactly 0 in the window — real observations, never a stand-in
    for a missing settlement."""
    out = {"status": "OK", "kind": None, "start_ts": start_ts, "end_ts": end_ts, "settlements": 0,
           "zero_rate_settlements": 0, "first_ts": None, "last_ts": None, "gaps": [],
           "missing_estimate": 0, "schedule_changes": 0, "next_due_ts": None, "tail_assumption": None,
           "reason": None, "version": COVERAGE_VERSION}
    if pairs is None or len(pairs) == 0:
        out.update(status="FUNDING_DATA_MISSING", kind="NO_SERIES", reason="no funding series")
        return out
    bad = series_issues(pairs)
    if bad:
        out.update(status="FUNDING_DATA_INVALID", kind="SERIES", reason="; ".join(bad))
        return out
    times = [t for t, _ in pairs]
    out["first_ts"], out["last_ts"] = times[0], times[-1]
    if end_ts < start_ts:
        return out                                   # nothing observed yet
    k0 = bisect_right(times, start_ts) - 1
    if k0 < 0:
        out.update(status="FUNDING_DATA_MISSING", kind="HEAD_NOT_COVERED",
                   reason=f"first settlement {times[0]} is after the observation start {start_ts}")
        return out
    k1 = bisect_right(times, end_ts) - 1             # last settlement at or before the end
    out["settlements"] = k1 - k0
    out["zero_rate_settlements"] = sum(1 for _, r in pairs[k0 + 1:k1 + 1] if r == 0)
    ivs = intervals or {}
    if intervals is None and k1 > k0:
        out.update(status="FUNDING_SCHEDULE_UNKNOWN", kind="INTERIOR", reason="no declared settlement interval")
        return out
    prev_iv = None
    for j in range(k0 + 1, k1 + 1):
        st, lo, hi = _step_status(times, ivs, j, tol_ms)
        iv_j = ivs.get(times[j])
        if prev_iv is not None and iv_j is not None and iv_j != prev_iv:
            out["schedule_changes"] += 1
        prev_iv = iv_j if iv_j is not None else prev_iv
        if st is None:
            continue
        a, b = times[j - 1], times[j]
        if st == "FUNDING_SCHEDULE_UNKNOWN":
            out.update(status=st, kind="INTERIOR",
                       reason=f"settlement {b if not ivs.get(b) else a} has no declared interval")
        elif st == "SCHEDULE_CHANGE_AMBIGUOUS":
            out.update(status="FUNDING_SCHEDULE_UNKNOWN", kind="SCHEDULE_CHANGE_AMBIGUOUS", gaps=[[a, b, int(lo)]],
                       reason=f"step {a} -> {b} ({b - a} ms) fits the declared interval of one side only "
                              f"({int(lo)} / {int(hi)} ms): which period the value belongs to is not documented")
        else:
            n = sum(max(1, int(round((y - x) / max(1, min(ivs[x], ivs[y])))) - 1)
                    for x, y in zip(times[k0:k1], times[k0 + 1:k1 + 1])
                    if ivs.get(x) and ivs.get(y) and y - x > max(ivs[x], ivs[y]) + tol_ms)
            gaps = [[x, y, int(min(ivs[x], ivs[y]))] for x, y in zip(times[k0:k1], times[k0 + 1:k1 + 1])
                    if ivs.get(x) and ivs.get(y) and y - x > max(ivs[x], ivs[y]) + tol_ms]
            out.update(status=st, kind="INTERIOR", gaps=gaps, missing_estimate=n,
                       reason=f"{n} settlements missing in {len(gaps)} gaps")
        return out
    L = times[k1]
    iv = ivs.get(L)
    if not (isinstance(iv, (int, float)) and iv > 0):
        out.update(status="FUNDING_SCHEDULE_UNKNOWN", kind="TAIL_SCHEDULE_UNKNOWN",
                   reason=f"settlement {L} has no declared interval: the next due time is unknown")
        return out
    due = L + int(iv)
    out["next_due_ts"] = due
    out["tail_assumption"] = "interval after the last settlement = the interval declared for it"
    if due - tol_ms > end_ts:
        return out                                   # not due inside the window yet
    if k1 + 1 >= len(times):
        n = 1 + max(0, int((end_ts - due + tol_ms) // iv))
        out.update(status="FUNDING_SETTLEMENT_GAP", kind="TAIL_DUE_MISSING", gaps=[[L, end_ts, int(iv)]],
                   missing_estimate=n,
                   reason=f"settlement due at {due} (declared interval {int(iv)} ms after {L}) "
                          f"not recorded by the window end {end_ts}")
        return out
    st, lo, hi = _step_status(times, ivs, k1 + 1, tol_ms)
    if st is not None:
        b = times[k1 + 1]
        out.update(status="FUNDING_SETTLEMENT_GAP" if st == "FUNDING_SETTLEMENT_GAP" else "FUNDING_SCHEDULE_UNKNOWN",
                   kind="TAIL_DUE_MISSING" if st == "FUNDING_SETTLEMENT_GAP" else "TAIL_SCHEDULE_UNKNOWN",
                   gaps=[[L, b, int(lo or iv)]], missing_estimate=1 if st == "FUNDING_SETTLEMENT_GAP" else 0,
                   reason=f"settlement due at {due} inside the window: next recorded settlement {b} ({st})")
    return out
