#!/usr/bin/env python3
"""Common data-integrity rules of the daily cross-sectional research scripts (ADR 0116).

Before this module every daily DEV script (``broad_universe_dev``,
``wide_universe_dev``, ``cross_section_v2_dev``, ``hilo_volume_dev``,
``lowfreq_nonprice_dev``, ``newsrc_r1_dev``, ``newsrc_engine`` and through it
the forward record ``forward_nearmiss``) had its own copy of the same rule:

* a missing open-to-open return of a held name was counted and then used as
  0.0 (a 0 % day that was never observed);
* a missing funding sum (NaN) was charged as 0 (no cost);
* a missing 23:45 bar volume was replaced by daily / 96 or, with no volume at
  all, by 1.0 — and the substitute was not separated from observed volume.

This module is the one rule set they now share.  It never invents a value:

``PRICE_RETURN_MISSING``  the day's PnL of the position is unknown, and so is
    the position itself afterwards (its weight drifts with the unknown
    return) — the day and every later day until the next rebalance are
    INVALID for that series, and so is the next rebalance day (its turnover
    and cost start from the unknown weights).
``FUNDING_*``             the holding day ``(D, D + 1 day]`` of a held name is
    checked against the settlement schedule the archive declares, with the
    same rule as the core engine (``discovery/funding_schedule.settlement_check``):
    a missing, gapped, schedule-less or invalid funding series makes the day
    INVALID; it is never charged as 0.  A recorded rate of 0 is a real
    observation and is valid.
``IMPACT_VOLUME_*``       the impact-cost volume of a trade: OBSERVED (the 15m
    23:45 bar), ESTIMATED_DAILY_96 (previous day's volume / 96) or
    ESTIMATED_MEAN30_96 (30-day mean / 96) — estimates are counted separately
    and their share is reported; with no volume at all the cost is unknown
    and the rebalance day is INVALID (the old value 1.0 is not used).

INVALID days are excluded from every statistic and reported (count, share,
first events with symbol / date / kind).  A result whose INVALID share or
estimated-input share exceeds :data:`POLICY` is not a normal result: DEV flags
become ``INVALID_DATA`` / ``UNDETERMINED`` and the forward record cannot PASS.
The costs of these scripts are always model estimates (ADR 0041 calibration
or an assumed profile): ``COST_EVIDENCE`` says so in every output.

No I/O except what callers pass in; numpy / pandas only (DEV tools).
"""

from __future__ import annotations

import math
import sys
from bisect import bisect_right
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Optional

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "discovery"))
from funding_schedule import FUNDING_TOL_MS, series_issues, settlement_check  # noqa: E402

VERSION = "research-data-integrity-v1"
DAY_MS = 86_400_000

#: preregistered limits (ADR 0116).  They only ever withhold a result; they
#: never turn a failing result into a passing one.
POLICY = {
    "version": VERSION,
    #: share of evaluated days of a series that are INVALID above which the
    #: series gets no normal verdict (DEV: INVALID_DATA; forward: INVALID_DATA)
    "max_invalid_day_share": 0.02,
    #: share of traded name-legs whose impact volume is an estimate above which
    #: the series gets no normal verdict (DEV / forward: UNDETERMINED)
    "max_estimated_volume_share": 0.10,
    #: the forward record PASSes only with no INVALID day at all
    "forward_pass_requires_zero_invalid_days": True,
    "funding_tolerance_ms": FUNDING_TOL_MS,
}

COST_EVIDENCE = ("ESTIMATED_MODEL: calibrated / assumed cost model (ADR 0041 / ADR 0039), "
                 "not real fills; impact volume observed or estimated as counted in volume_provenance")

#: kinds of data events (all recorded with symbol and day)
KINDS = ("PRICE_RETURN_MISSING", "POSITION_STATE_UNKNOWN", "FUNDING_DATA_MISSING",
         "FUNDING_SETTLEMENT_GAP", "FUNDING_SCHEDULE_UNKNOWN", "FUNDING_DATA_INVALID",
         "IMPACT_VOLUME_MISSING", "PANEL_DUPLICATE_CONFLICT", "PANEL_INVALID_VALUE",
         "PANEL_OFF_GRID_TIMESTAMP")
VOLUME_KINDS = ("OBSERVED_BAR2345", "ESTIMATED_DAILY_96", "ESTIMATED_MEAN30_96", "MISSING")


def day_iso(t: int) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(int(t) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def funding_day(t_ms) -> "np.ndarray | int":
    """Holding day of a settlement: a settlement at ``D + 1 day`` (plus the few
    ms of archive jitter, up to the tolerance) pays for holding on day ``D``.
    The old rule ``(t - 1) // DAY`` put a settlement at 00:00:00.002 on the
    NEXT day (ADR 0116 finding F-04)."""
    return ((np.asarray(t_ms, dtype=np.int64) - 1 - FUNDING_TOL_MS) // DAY_MS) * DAY_MS


class IssueLog:
    """Counts of every data event plus the first ``cap`` events in full."""

    def __init__(self, cap: int = 500):
        self.cap = cap
        self.counts: Counter = Counter()
        self.events: list = []

    def add(self, kind: str, sym: str, t_ms: int, *, series: Optional[str] = None,
            detail: Optional[str] = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown data event kind {kind!r}")
        self.counts[kind] += 1
        if len(self.events) < self.cap:
            ev = {"kind": kind, "symbol": sym, "day": day_iso(t_ms)}
            if series is not None:
                ev["series"] = series
            if detail:
                ev["detail"] = str(detail)[:200]
            self.events.append(ev)

    def extend(self, other: "IssueLog") -> None:
        for e in other.events:
            if len(self.events) < self.cap:
                self.events.append(e)
        self.counts.update(other.counts)

    def to_json(self) -> dict:
        total = sum(self.counts.values())
        return {"counts": {k: self.counts.get(k, 0) for k in KINDS if self.counts.get(k)},
                "events_total": total, "events": list(self.events),
                "events_truncated": total > len(self.events)}


class VolumeProvenance:
    """Observed vs estimated impact volume, per traded name-leg."""

    def __init__(self):
        self.counts: Counter = Counter()

    def add(self, kind: str) -> None:
        if kind not in VOLUME_KINDS:
            raise ValueError(kind)
        self.counts[kind] += 1

    def to_json(self) -> dict:
        n = sum(self.counts.values())
        est = self.counts["ESTIMATED_DAILY_96"] + self.counts["ESTIMATED_MEAN30_96"]
        return {"legs": n, **{k: self.counts.get(k, 0) for k in VOLUME_KINDS},
                "observed_share": (self.counts["OBSERVED_BAR2345"] / n) if n else None,
                "estimated_share": (est / n) if n else None,
                "missing_share": (self.counts["MISSING"] / n) if n else None,
                "note": "the volume feeds an estimated cost model; estimated volume is never fill evidence"}


def impact_volume(bars: Mapping, qv, s: str, i: int, bar_t: int) -> tuple:
    """Impact volume of a trade at the open of day ``i`` (15m bar 23:45 of day i-1)
    and its provenance.  Returns (volume or None, kind).  Same substitutes as
    before (daily / 96, then 30-day mean / 96), now labelled; no volume at all
    is MISSING (the old code used 1.0)."""
    b = bars.get((s, bar_t))
    if b is not None and b > 0 and math.isfinite(b):
        return float(b), "OBSERVED_BAR2345"
    dq = qv.iloc[i - 1][s] if i >= 1 else float("nan")
    if dq > 0 and math.isfinite(dq):
        return float(dq) / 96.0, "ESTIMATED_DAILY_96"
    vals = qv[s].iloc[max(0, i - 30): i].values
    vals = vals[np.isfinite(vals)] if len(vals) else vals
    dq = float(np.mean(vals)) if len(vals) else float("nan")
    if dq > 0 and math.isfinite(dq):
        return dq / 96.0, "ESTIMATED_MEAN30_96"
    return None, "MISSING"


class FundingCalendar:
    """Per (symbol, holding day) funding completeness under the declared schedule.

    ``fund`` is a DataFrame with columns sym, t, rate, interval_ms (interval
    NaN / <= 0 when the archive row declares none).  Results are memoised."""

    def __init__(self, fund, syms: Iterable[str]):
        self._pairs: dict = {}
        self._ivs: dict = {}
        self._issues: dict = {}
        self._memo: dict = {}
        has_iv = "interval_ms" in getattr(fund, "columns", ())
        for s in syms:
            self._pairs[s], self._ivs[s] = [], {}
        if len(fund):
            for s, g in fund.groupby("sym", sort=False):
                if s not in self._pairs:
                    continue
                order = np.argsort(g["t"].values, kind="stable")
                ts = [int(x) for x in g["t"].values[order]]
                rs = [float(x) for x in g["rate"].values[order]]
                self._pairs[s] = list(zip(ts, rs))
                if has_iv:
                    ivs = g["interval_ms"].values[order]
                    self._ivs[s] = {t: int(iv) for t, iv in zip(ts, ivs)
                                    if isinstance(iv, (int, float, np.integer, np.floating))
                                    and math.isfinite(float(iv)) and iv > 0}
        for s, pairs in self._pairs.items():
            self._issues[s] = series_issues(pairs)

    def status(self, s: str, day_ms: int) -> str:
        key = (s, int(day_ms))
        if key not in self._memo:
            pairs = self._pairs.get(s) or []
            chk = settlement_check(pairs, self._ivs.get(s) or {}, int(day_ms), int(day_ms) + DAY_MS,
                                   issues=self._issues.get(s, []))
            self._memo[key] = chk["status"]
        return self._memo[key]

    def day_sum(self, s: str, day_ms: int) -> float:
        """Sum of the rates of the settlements that pay for holding day ``day_ms``."""
        pairs = self._pairs.get(s) or []
        times = [t for t, _ in pairs]
        lo = bisect_right(times, int(day_ms) + FUNDING_TOL_MS)
        hi = bisect_right(times, int(day_ms) + DAY_MS + FUNDING_TOL_MS)
        return float(sum(r for _, r in pairs[lo:hi]))


def clean_panel(df, value_cols: Iterable[str], positive_cols: Iterable[str] = (),
                nonneg_cols: Iterable[str] = (), log: Optional[IssueLog] = None):
    """Daily kline rows (sym, t, values...) -> rows a pivot can use.

    * a timestamp not on the UTC day grid is dropped and recorded;
    * exact duplicate rows collapse to one; duplicates with DIFFERENT values
      are a conflict: every copy is dropped (the value is unknown) and recorded
      — the old ``drop_duplicates(["sym", "t"])`` silently kept the first;
    * a non-finite or non-positive price / negative volume becomes NaN (it is
      then a missing value, never a 0) and is recorded.
    """
    import pandas as pd  # noqa: F401  (DataFrame operations)
    log = log if log is not None else IssueLog()
    if not len(df):
        return df, log
    off = df["t"] % DAY_MS != 0
    for s, t in df.loc[off, ["sym", "t"]].itertuples(index=False):
        log.add("PANEL_OFF_GRID_TIMESTAMP", s, int(t))
    df = df.loc[~off]
    df = df.drop_duplicates()
    dup = df.duplicated(["sym", "t"], keep=False)
    for s, t in df.loc[dup, ["sym", "t"]].drop_duplicates().itertuples(index=False):
        log.add("PANEL_DUPLICATE_CONFLICT", s, int(t), detail="rows with different values; all dropped")
    df = df.loc[~dup].copy()
    for c in value_cols:
        v = df[c].astype(float)
        bad = ~np.isfinite(v.values)
        if c in set(positive_cols):
            bad |= ~(v.values > 0)
        if c in set(nonneg_cols):
            bad |= ~(v.values >= 0)
        if bad.any():
            for s, t in df.loc[bad, ["sym", "t"]].itertuples(index=False):
                log.add("PANEL_INVALID_VALUE", s, int(t), detail=c)
            df.loc[bad, c] = np.nan
    return df, log


#: panel issues of the loaders, by (loader, data dir, segment) — engines copy
#: them into their outputs
PANEL_LOGS: dict = {}


def panel_log(loader: str, data, seg: str) -> IssueLog:
    key = (loader, str(data), seg)
    if key not in PANEL_LOGS:
        PANEL_LOGS[key] = IssueLog()
    return PANEL_LOGS[key]


def panel_issues(data, seg: str) -> dict:
    out = IssueLog()
    for (loader, d, sg), lg in PANEL_LOGS.items():
        if d == str(data) and sg == seg:
            out.extend(lg)
    return out.to_json()


class HeldDay:
    """Data status of one held day of one series (all names)."""
    __slots__ = ("invalid", "return_missing")

    def __init__(self):
        self.invalid = False
        self.return_missing = False


def held_name_day(ret: float, fcal: Optional[FundingCalendar], sym: str, day_ms: int,
                  log: IssueLog, series: str, st: HeldDay) -> tuple:
    """(return, funding) of one held name on one day, or None for an unknown
    value; ``st`` and ``log`` record why."""
    r = None
    if ret is not None and math.isfinite(ret):
        r = float(ret)
    else:
        log.add("PRICE_RETURN_MISSING", sym, day_ms, series=series)
        st.invalid = st.return_missing = True
    f = None
    if fcal is None:
        f = 0.0
    else:
        fs = fcal.status(sym, day_ms)
        if fs == "OK":
            f = fcal.day_sum(sym, day_ms)
        else:
            log.add(fs, sym, day_ms, series=series)
            st.invalid = True
    return r, f


def valid_mask(*arrays) -> np.ndarray:
    m = None
    for a in arrays:
        x = np.isfinite(np.asarray(a, float))
        m = x if m is None else (m & x)
    return m


def series_status(n_days: int, n_invalid: int, vol: Optional[dict] = None) -> dict:
    """Data status of one evaluated series under :data:`POLICY`."""
    share = (n_invalid / n_days) if n_days else None
    est = (vol or {}).get("estimated_share")
    if n_days == 0:
        status = "INSUFFICIENT_DATA"
    elif share > POLICY["max_invalid_day_share"]:
        status = "INVALID_DATA"
    elif est is not None and est > POLICY["max_estimated_volume_share"]:
        status = "UNDETERMINED"
    elif n_invalid:
        status = "COMPLETE_WITH_EXCLUSIONS"
    else:
        status = "COMPLETE"
    return {"status": status, "days_total": n_days, "days_invalid": n_invalid,
            "days_valid": n_days - n_invalid, "invalid_share": share,
            "estimated_volume_share": est, "policy": POLICY["version"],
            "note": "statistics use the valid days only; they are not full-period results "
                    "when days_invalid > 0"}


class SeriesState:
    """Data state of one evaluated daily series (one hypothesis x universe)."""

    def __init__(self, n_days: int, series: str):
        self.series = series
        self.invalid = np.zeros(n_days, dtype=bool)
        self.log = IssueLog()
        self.volume = VolumeProvenance()
        #: True after a missing return of a drifting position: the weights are
        #: unknown until the next rebalance
        self.unknown = False

    def rebalance(self, i: int, prev: Mapping, days) -> None:
        """Call at every rebalance day ``i`` BEFORE the turnover is computed."""
        if self.unknown:
            self.invalid[i] = True
            for name in prev:
                self.log.add("POSITION_STATE_UNKNOWN", name, int(days[i]), series=self.series,
                             detail="rebalance from weights that drifted with a missing return")
            self.unknown = False

    def cost_volume(self, i: int, name: str, days, b, kind: str, v30=None) -> bool:
        """Record the provenance of a trade's impact volume; False (and the day
        INVALID) when the cost cannot be computed."""
        self.volume.add(kind)
        if b is None or (v30 is not None and not (v30 > 0)):
            self.log.add("IMPACT_VOLUME_MISSING", name, int(days[i]), series=self.series,
                         detail="no 23:45 / daily / 30-day volume" if b is None else "no 30-day volume")
            self.invalid[i] = True
            return False
        return True

    def hold(self, cur: dict, i: int, end: int, ret, col: Mapping, days, fcal: Optional[FundingCalendar],
             add, *, drift: bool = True, flags: Optional[dict] = None) -> None:
        """Hold ``cur`` (name -> weight) over days [i, end); ``add(d, pnl)`` books
        the PnL of observable days.  ``ret`` is the (days x names) return array."""
        for d in range(i, end):
            if self.unknown:
                self.invalid[d] = True
                continue
            st = HeldDay()
            pnl = 0.0
            for name, ws in cur.items():
                r, f = held_name_day(ret[d, col[name]], fcal, name, int(days[d]), self.log, self.series, st)
                if flags is not None:
                    if r is None:
                        flags["missing_return_days"] = flags.get("missing_return_days", 0) + 1
                    if f is None:
                        flags["funding_invalid_name_days"] = flags.get("funding_invalid_name_days", 0) + 1
                if r is not None and f is not None:
                    pnl += ws * r - ws * f
                if drift and r is not None:
                    cur[name] = ws * (1.0 + r)
            if st.invalid:
                self.invalid[d] = True
            if st.return_missing and drift:
                self.unknown = True
            add(d, pnl)

    def finish(self, arrays: Iterable[np.ndarray]) -> None:
        """Unobservable days become NaN in every series array (never 0)."""
        for a in arrays:
            a[self.invalid] = np.nan


def withhold_flag(flag: str, stats_rows: Iterable[Mapping]) -> str:
    """A DEV flag on a series with unobserved days above the limit is INVALID_DATA
    (it is never DEV_PROMISING on days that were not observed)."""
    for st in stats_rows:
        if st and st.get("unobserved_share", 0.0) > POLICY["max_invalid_day_share"]:
            return "INVALID_DATA"
    return flag
