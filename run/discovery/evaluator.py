"""Event-study evaluator — no-look-ahead, cost + funding aware (directive §9-1, §11).

Construction (identical in spirit to the public repo's futures engine):
- a signal decided on bar t uses ONLY bars[:t+1] (a physical prefix view —
  peeking raises IndexError);
- entry fills at bar t+1 open, exit at bar t+1+H open;
- per-side costs from the shared CandleCostModel with sqrt-participation
  impact, fail-closed when participation is exceeded.  Participation is
  measured against the traded value of the last bar CLOSED before each fill
  (bar t for the entry at open t+1, bar exit-1 for the exit): the fill bar's
  own volume is not known when the order is sent (no look-ahead in pricing
  or in the refusal decision);
- funding is charged for the actual holding window from the caller-supplied
  funding series — never estimated (missing funding for a held period is
  recorded, and the event is dropped, NOT filled with a guess).
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from costs_bridge import CostContext, CostDataMissing, CostRefused
from data_quality import Bar


class PrefixView:
    """Read-only view of bars[:end] — strategies cannot touch the future."""

    __slots__ = ("_bars", "_end")

    def __init__(self, bars: Sequence[Bar], end: int):
        self._bars = bars
        self._end = end

    def __len__(self) -> int:
        return self._end

    def __getitem__(self, i: int) -> Bar:
        if i < 0:
            i += self._end
        if not 0 <= i < self._end:
            raise IndexError("index outside the visible (closed) history")
        return self._bars[i]


SignalFn = Callable[[PrefixView], int]
"""Return +1 (long) / -1 (short) / 0 (flat) for the decision at bar t.
The function receives the prefix view ending at t."""


@dataclass
class EventRecord:
    symbol: str
    entry_index: int
    exit_index: int
    side: int
    entry_ts: int
    exit_ts: int
    gross_bp: float
    cost_bp: float
    funding_bp: float
    net_bp: float
    partial_fill_skipped: bool = False

    def to_json(self) -> dict:
        return dict(self.__dict__)


@dataclass
class EventStudyResult:
    symbol: str
    events: list[EventRecord] = field(default_factory=list)
    skipped_overlapped: int = 0
    dropped_missing_funding: int = 0
    participation_refusals: int = 0
    cost_data_missing: int = 0
    signals_total: int = 0

    def to_json(self) -> dict:
        return {
            "symbol": self.symbol,
            "n_events": len(self.events),
            "skipped_overlapped": self.skipped_overlapped,
            "dropped_missing_funding": self.dropped_missing_funding,
            "participation_refusals": self.participation_refusals,
            "cost_data_missing": self.cost_data_missing,
            "signals_total": self.signals_total,
            "events": [e.to_json() for e in self.events],
        }


def funding_paid_bp(funding: Sequence[tuple[int, float]], start_ts: int, end_ts: int,
                    side: int) -> Optional[float]:
    """Funding cost in bp of notional over (start_ts, end_ts] for `side`.

    Long pays when rate > 0.  Returns None when the funding series does not
    COVER the window (fail-closed: the caller drops the event instead of
    guessing, directive §11-3).
    """
    if not funding:
        return None
    times = [t for t, _ in funding]
    if times[0] > start_ts or times[-1] < end_ts:
        return None
    total = 0.0
    for t, r in funding:
        if start_ts < t <= end_ts:
            total += side * r
    return total * 1e4


class FundingIndex:
    """Precomputed time index over a funding series — O(log F) coverage
    checks and O(gap) summation per event instead of O(F) scans (needed
    because validation/locked windows produce thousands of events).

    Gap-aware (auto-evolution §2-3): a holding window that overlaps a gap in
    the settlement series (settlements missing between two recorded ones) is
    NOT covered — the sum over the recorded settlements would understate the
    cost, so ``paid_bp`` returns None and the event is dropped."""

    __slots__ = ("funding", "times", "gaps")

    def __init__(self, funding: Sequence[tuple[int, float]],
                 gaps: Optional[Sequence[tuple[int, int]]] = None):
        from data_quality import funding_gaps
        self.funding = list(funding)
        self.times = [t for t, _ in self.funding]
        self.gaps = list(gaps) if gaps is not None else funding_gaps(self.funding)

    def paid_bp(self, start_ts: int, end_ts: int, side: int) -> Optional[float]:
        """Funding in bp for a position opened at the open of ``start_ts`` and
        closed at the open of ``end_ts``: settlements t with start < t <= end
        (fills happen just after the bar open, so a settlement stamped exactly
        at the entry open is not held, one stamped at the exit open is)."""
        if not self.times or self.times[0] > start_ts or self.times[-1] < end_ts:
            return None
        for a, b in self.gaps:
            if a < end_ts and b > start_ts:
                return None
        lo = bisect_right(self.times, start_ts)
        hi = bisect_right(self.times, end_ts)   # events in (start, end]
        total = 0.0
        for k in range(lo, hi):
            total += side * self.funding[k][1]
        return total * 1e4


def run_event_study(bars: Sequence[Bar], symbol: str, signal_fn: SignalFn,
                    horizon: int, cost: CostContext, order_value: float,
                    funding: Optional[Sequence[tuple[int, float]]] = None,
                    *, warmup: int = 1, exit_rule: Optional[Callable] = None) -> EventStudyResult:
    """Evaluate one strategy definition on one symbol.

    Events whose holding window would overlap a previously accepted event
    are skipped and COUNTED — they are not independent observations.
    """
    res = EventStudyResult(symbol=symbol)
    fidx = FundingIndex(funding) if funding is not None else None
    n = len(bars)
    last_exit_index = -1
    for t in range(warmup, n - 1):
        view = PrefixView(bars, t + 1)          # decide with closed bars only
        side = signal_fn(view)
        if side not in (1, -1):
            continue
        res.signals_total += 1
        entry_i = t + 1
        exit_i = entry_i + horizon
        if entry_i <= last_exit_index:
            res.skipped_overlapped += 1
            continue
        if exit_i >= n:
            break
        exit_px = None
        if exit_rule is not None:
            # intrabar stop / take-profit (spec strategies): (index, price, reason)
            ex = exit_rule(bars, entry_i, side, horizon)
            if ex is None:
                break
            exit_i, exit_px, _reason = ex
        entry_bar, exit_bar = bars[entry_i], bars[exit_i]
        try:
            # last CLOSED bar before each fill (entry_i - 1 = the decision bar t)
            cost_bp = cost.round_trip_bp(order_value,
                                         bars[entry_i - 1].quote_volume,
                                         bars[exit_i - 1].quote_volume)
        except CostRefused:
            # participation cap exceeded → the trade is refused, counted
            res.participation_refusals += 1
            continue
        except CostDataMissing:
            # no traded value to price against → not a tradable event, counted
            # separately (it is a data fact, not a cost-model verdict)
            res.cost_data_missing += 1
            continue
        # CostComputationError propagates: a numeric failure of the cost
        # model is an ERROR of the evaluation, never a skipped event
        if funding is not None:
            fp = fidx.paid_bp(entry_bar.ts, exit_bar.ts, side)
            if fp is None:
                res.dropped_missing_funding += 1
                continue
        else:
            fp = 0.0
        px_out = exit_bar.open if exit_px is None else exit_px
        gross = side * (px_out / entry_bar.open - 1.0) * 1e4
        net = gross - cost_bp - fp
        res.events.append(EventRecord(
            symbol=symbol, entry_index=entry_i, exit_index=exit_i, side=side,
            entry_ts=entry_bar.ts, exit_ts=exit_bar.ts,
            gross_bp=gross, cost_bp=cost_bp, funding_bp=fp, net_bp=net,
        ))
        last_exit_index = exit_i
    return res


# ---------------------------------------------------------------------------
# per-cell statistics from events
# ---------------------------------------------------------------------------


def cell_stats(events: Sequence[EventRecord], symbol: str, cell_id: str,
               *, min_n: int = 30) -> dict:
    """Statistics for one pre-registered cell (symbol, direction, horizon).

    Uses non-overlapping events only (the evaluator already enforces this)
    and a HAC t-statistic because event returns can still be autocorrelated
    through shared regimes.  n < min_n → UNDETERMINED_POWER, never PASS.
    """
    from engine_lib import MIN_VALID_N, nw_tstat, t_sf
    min_n = max(min_n, MIN_VALID_N)
    nets = [e.net_bp for e in events]
    n = len(nets)
    if any(not math.isfinite(x) for x in nets):
        # P2: NaN/inf returns are a computation error, never a test input
        return {"symbol": symbol, "cell_id": cell_id, "n": n, "status": "ERROR",
                "mean_bp": float("nan"), "t": float("nan"), "p": float("nan"),
                "note": "non-finite event return"}
    if n == 0:
        return {"symbol": symbol, "cell_id": cell_id, "n": 0,
                "status": "DATA_MISSING", "mean_bp": float("nan"),
                "t": float("nan"), "p": float("nan")}
    mu = sum(nets) / n
    if n < min_n:
        return {"symbol": symbol, "cell_id": cell_id, "n": n,
                "status": "UNDETERMINED_POWER", "mean_bp": mu,
                "t": float("nan"), "p": float("nan")}
    t, _ = nw_tstat(nets)
    if not math.isfinite(t):
        return {"symbol": symbol, "cell_id": cell_id, "n": n,
                "status": "UNDETERMINED_POWER", "mean_bp": mu,
                "t": t, "p": float("nan")}
    p = t_sf(t, n - 1)
    if not math.isfinite(p) or not 0.0 <= p <= 1.0:
        return {"symbol": symbol, "cell_id": cell_id, "n": n, "status": "ERROR",
                "mean_bp": mu, "t": t, "p": float("nan"),
                "note": f"p-value computation failed ({p!r})"}
    status = "PASS" if (p < 0.05 and mu > 0) else "FAIL_STAT"
    return {"symbol": symbol, "cell_id": cell_id, "n": n, "mean_bp": mu,
            "t": t, "p": p, "status": status}
