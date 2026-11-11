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

FUNDING_SCHEDULE_VERSION = "funding-schedule-v1"
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
    for j in range(k0 + 1, k1 + 1):
        iv = intervals.get(times[j])
        if not (isinstance(iv, (int, float)) and iv > 0):
            unknown.append(times[j])
            continue
        if prev_iv is not None and iv != prev_iv:
            out["schedule_changes"] += 1
        prev_iv = iv
        step = times[j] - times[j - 1]
        if step > iv + tol_ms:
            out["gaps"].append([times[j - 1], times[j], int(iv)])
            out["missing_estimate"] += max(1, int(round(step / iv)) - 1)
    if unknown:
        out.update(status="FUNDING_SCHEDULE_UNKNOWN",
                   reason=f"{len(unknown)} settlements without a declared interval (first {unknown[0]})")
        return out
    if out["gaps"]:
        out.update(status="FUNDING_SETTLEMENT_GAP",
                   reason=f"{out['missing_estimate']} settlements missing in {len(out['gaps'])} gaps")
    return out


def worst(statuses: Sequence[str]) -> str:
    return max(statuses, key=lambda s: FUNDING_STATUS_RANK[s]) if statuses else "OK"
