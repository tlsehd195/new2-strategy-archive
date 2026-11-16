"""Multi-strategy portfolio validation for evolved strategies (ADR 0068, P-01).

The evolve pipeline judges every strategy on its own event returns.  That
answers "does this signal have an edge per trade?", not "what happens to one
account that runs these strategies at the same time?".  This module answers
the second question in time order, bar by bar, on ONE account:

* capital: initial capital, per-strategy allocation, per-position size,
  gross-exposure cap, concurrent-position cap, margin reserved per position
  (never used twice), realized / unrealized PnL, no entry without free margin;
* leverage and margin: isolated (each position posts its own margin) or cross
  (one account; a shortfall of equity below the summed maintenance margin at
  the bar's adverse extremes liquidates every position at once);
* costs: the shared :class:`costs_bridge.CostContext` re-prices every fill at
  the ACTUAL notional (fee and spread/impact split), funding is settled into
  cash at its timestamps, and what the model cannot price is never zero —
  partial fills and fill latency are ``NOT_MODELLED`` unless a stress knob is
  set, and an unpriced fill makes the result UNDETERMINED;
* several strategies at once: same-direction stacking, opposite positions on
  one symbol (hedge-mode: both legs pay their costs, nothing is netted),
  allocation conflicts, margin shortfall on simultaneous entries, simultaneous
  liquidations, inactive members and members without data;
* time integrity: a trade's decision bar must be strictly before its entry
  bar, the entry fills at the entry bar's OPEN, a trade decided after the
  evaluation's ``as_of`` is look-ahead (hard error), unsorted or duplicated
  bars are a hard error, an exit price outside its bar's [low, high] is a
  hard error, and when a liquidation and a stop / take-profit are both
  reachable inside one bar the LARGER loss is booked.

ENGINE ASSUMPTIONS (not claims about any exchange's rule book): liquidation
price ``entry*(1 - 1/L + mmr)`` (long) / ``entry*(1 + 1/L - mmr)`` (short); an
isolated liquidation loses the posted margin; maintenance-margin rate,
leverage and the gap rule are configuration values recorded with the result
(``assumptions``).  Exchange tiers, insurance funds and ADL are not modelled.

The input trades are the evaluator's own events (``evaluator.run_event_study``
with the strategy's exit rule), so entry, stop / take-profit and time exits
are exactly the ones the individual-strategy tests used; the portfolio adds
sizing, margin, liquidation, re-priced cost and the interaction between
strategies.  Strategy returns are never summed: equity is one state that every
fill, funding payment and liquidation updates in time order.

Pure functions, no I/O.  A synthetic-data test result says nothing about
markets.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Mapping, Optional, Sequence

from costs_bridge import CostContext, CostDataMissing, CostError, CostRefused
from data_quality import Bar

PORTFOLIO_MULTI_VERSION = "portfolio-multi-v4"  # ADR 0078 (E-04/E-05/E-06)
DAY_MS = 86_400_000

#: result statuses (ADR 0068).  PASS_INDIVIDUAL is the individual pipeline's
#: VALIDATION_SURVIVOR; it is never written by this module.
PORTFOLIO_STATUSES = ("PASS_PORTFOLIO", "FAIL_PORTFOLIO", "INSUFFICIENT_PORTFOLIO_DATA",
                      "UNDETERMINED_PORTFOLIO", "PORTFOLIO_SIMULATION_ERROR")


#: why a scheduled leg did not become a position (ADR 0071 adds cost_error,
#: previously counted only per strategy)
SKIP_REASONS = ("capacity", "margin", "gross_exposure", "same_symbol", "cost_refused",
                "cost_data_missing", "cost_error", "inactive", "halted", "zero_allocation",
                "latency_beyond_data", "duplicate_signal", "account_valuation_incomplete",
                "primary_not_entered")
#: classification of every exclusion (ADR 0071).  Strategy-rule exclusions and
#: the window end are not rejections and are outside the rejection denominator.
SKIP_CLASS = {"duplicate_signal": "strategy_rule", "inactive": "strategy_rule",
              "latency_beyond_data": "window_end",
              "cost_refused": "cost_refused", "cost_data_missing": "data_missing",
              "cost_error": "data_missing",
              "margin": "capital", "gross_exposure": "capital", "capacity": "capital",
              "same_symbol": "capital", "zero_allocation": "capital", "halted": "halted",
              # ADR 0072: cross margin, an open position's symbol has no bar at the entry
              # timestamp, so the account (equity, margin, liquidation) cannot be valued
              # on prices of one moment: a data fact, the signal's outcome is unknown
              "account_valuation_incomplete": "data_missing",
              # ADR 0078 (E-05): the rest of a refused order — the refusal is counted
              # once, on the primary leg
              "primary_not_entered": "strategy_rule"}


class PortfolioInputError(ValueError):
    """Inputs that would make the simulation wrong (look-ahead, unsorted bars,
    impossible prices).  Always a PORTFOLIO_SIMULATION_ERROR, never skipped."""


@dataclass
class MultiConfig:
    initial_capital: float = 10_000.0
    leverage: float = 1.0
    margin_mode: str = "isolated"            # isolated | cross
    mmr: float = 0.005                       # maintenance margin rate (assumption)
    max_concurrent: int = 4                  # open positions, all strategies
    max_gross_exposure: float = 1.0          # sum |notional| / equity after the entry
    position_fraction: float = 1.0           # of a strategy's per-symbol allocation
    same_symbol_policy: str = "hedge"        # hedge | one_per_symbol
    fill_ratio: float = 1.0                  # < 1: deterministic partial-fill stress
    latency_bars: int = 0                    # > 0: entries and time exits k bars later
    weights: Optional[dict] = None           # {strategy: weight}; None = equal

    def validate(self) -> None:
        if not (self.initial_capital > 0 and math.isfinite(self.initial_capital)):
            raise PortfolioInputError("initial_capital must be finite and > 0")
        if not (self.leverage > 0 and math.isfinite(self.leverage)):
            raise PortfolioInputError("leverage must be finite and > 0")
        if self.margin_mode not in ("isolated", "cross"):
            raise PortfolioInputError(f"margin_mode {self.margin_mode!r}")
        if not (0 <= self.mmr < 1.0 / self.leverage):
            raise PortfolioInputError("mmr must be in [0, 1/leverage)")
        if self.same_symbol_policy not in ("hedge", "one_per_symbol"):
            raise PortfolioInputError(f"same_symbol_policy {self.same_symbol_policy!r}")
        if not (0 < self.fill_ratio <= 1):
            raise PortfolioInputError("fill_ratio must be in (0, 1]")
        if self.latency_bars < 0 or int(self.latency_bars) != self.latency_bars:
            raise PortfolioInputError("latency_bars must be an integer >= 0")
        if self.max_concurrent < 1 or not (self.max_gross_exposure > 0) or not (0 < self.position_fraction <= 1):
            raise PortfolioInputError("max_concurrent >= 1, max_gross_exposure > 0, "
                                      "position_fraction in (0, 1]")


@dataclass
class _Pos:
    strategy: str
    symbol: str
    side: int
    entry_ts: int
    exit_ts: int
    exit_price: Optional[float]     # intrabar stop / take-profit fill; None = open of exit bar
    exit_reason: str
    entry_price: float
    qty: float
    notional: float
    margin: float
    liq_price: float
    fees: float = 0.0
    slippage: float = 0.0
    funding: float = 0.0
    decision_ts: int = 0
    fi: int = 0
    uid: int = -1
    hold_to_end: bool = False
    leg: str = "primary"
    closed: bool = False


def liq_price(side: int, entry: float, leverage: float, mmr: float) -> float:
    if side == 1:
        return entry * (1.0 - 1.0 / leverage + mmr)
    return entry * (1.0 + 1.0 / leverage - mmr)


def _check_bars(sym: str, bars: Sequence[Bar]) -> None:
    prev = None
    for b in bars:
        if prev is not None and b.ts <= prev:
            raise PortfolioInputError(f"{sym}: bars not strictly increasing in time at ts={b.ts} "
                                      "(unsorted or duplicated data)")
        vals = (b.open, b.high, b.low, b.close)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in vals) \
                or b.low > min(b.open, b.close) or b.high < max(b.open, b.close):
            raise PortfolioInputError(f"{sym}: abnormal bar at ts={b.ts} {vals}")
        prev = b.ts


def _split_cost(cost: CostContext, frac: float, notional: float) -> tuple[float, float]:
    """(fee, spread+impact) in quote currency of one priced fill."""
    fee = min(frac, float(cost.params.get("fee_rate", 0.0))) * notional
    return fee, max(0.0, frac * notional - fee)


def simulate_multi(bars_by_symbol: Mapping[str, Sequence[Bar]],
                   trades: Sequence[Mapping],
                   funding_by_symbol: Mapping[str, Optional[Sequence[tuple[int, float]]]],
                   cost: CostContext, cfg: MultiConfig, *,
                   members: Sequence[str], as_of_ts: Optional[int] = None,
                   inactive_from: Optional[Mapping[str, int]] = None,
                   member_symbols: Optional[Mapping[str, Sequence[str]]] = None,
                   exit_rules: Optional[Mapping[str, tuple]] = None,
                   funding_intervals_by_symbol: Optional[Mapping[str, Optional[Mapping[int, int]]]] = None,
                   funding_meta_by_symbol: Optional[Mapping[str, Mapping]] = None,
                   signal_diagnostics: Optional[Mapping[str, Mapping]] = None) -> dict:
    """Run the account.  ``trades`` items: {strategy, symbol, side, decision_ts,
    entry_ts, exit_ts, exit_price (None = exit bar open), exit_reason}.

    ``member_symbols``: each member's spec symbols; its allocation is split over
    them whether or not it trades each one (without it the split falls back to
    the symbols seen in ``trades`` — whole-window knowledge, tests only).
    ``exit_rules``: {member: (rule, horizon)} with ``rule`` the strategy's own
    exit rule (``strategy_spec.stop_exit_rule``); the latency stress re-derives
    the exit from the DELAYED entry with it and never drops a trade.

    Raises :class:`PortfolioInputError` for inputs that make a result wrong;
    the caller records PORTFOLIO_SIMULATION_ERROR (never a pass).

    ADR 0071 (portfolio-multi-v2):
    ``funding_intervals_by_symbol``: declared settlement interval per
    settlement ({ts: ms}); every closed trade's holding window is checked
    against it (funding_schedule.settlement_check) and the trade carries its
    ``funding_status`` — a missing middle settlement is never charged as 0
    silently.  None for a symbol = FUNDING_SCHEDULE_UNKNOWN.
    ``funding_meta_by_symbol``: {symbol, exchange} of each series (mismatch =
    FUNDING_DATA_INVALID).
    ``signal_diagnostics``: {"member/symbol": evaluator diagnostics} of the
    studies that produced ``trades``; with it the result carries the full
    signal accounting from the first signal to the executed position.

    ADR 0072 (portfolio-multi-v3): cross margin never confirms an account
    check on a stale mark.  When an open position's symbol has no bar at a
    timestamp, the open-gap and intrabar account liquidation checks of that
    timestamp are deferred (nothing booked; ``cross_account_valuation``
    records what the stale-mark check would have said) and no entry is made
    there (``account_valuation_incomplete``, class data_missing).  A result
    with any deferred check is INCOMPLETE -> UNDETERMINED_PORTFOLIO.  Isolated
    margin is unchanged (each position on its own bar)."""
    from funding_schedule import series_issues, settlement_check
    cfg.validate()
    members = list(members)
    if len(set(members)) != len(members) or not members:
        raise PortfolioInputError("members must be a non-empty list without duplicates")
    for sym, bars in bars_by_symbol.items():
        _check_bars(sym, bars)
    idx = {s: {b.ts: i for i, b in enumerate(bars)} for s, bars in bars_by_symbol.items()}
    data_end = max((bars[-1].ts for bars in bars_by_symbol.values() if bars), default=None)
    inactive_from = dict(inactive_from or {})
    weights = cfg.weights or {m: 1.0 / len(members) for m in members}
    if set(weights) != set(members) or any(not (w >= 0) for w in weights.values()) \
            or abs(sum(weights.values()) - 1.0) > 1e-9:
        raise PortfolioInputError("weights must cover exactly the members, be >= 0 and sum to 1")

    # -- validate and schedule the trades ------------------------------------------------
    syms_of: dict[str, set] = {m: set(member_symbols[m]) if member_symbols and m in member_symbols
                               else set() for m in members}
    from_trades = not member_symbols
    sched: dict[int, list[dict]] = {}
    n_latency_skip = 0
    for k, t in enumerate(trades):
        m, sym = t["strategy"], t["symbol"]
        if m not in syms_of:
            raise PortfolioInputError(f"trade {k}: strategy {m!r} is not a member")
        if sym not in idx:
            raise PortfolioInputError(f"trade {k}: no bars for {sym}")
        side = int(t["side"])
        if side not in (1, -1):
            raise PortfolioInputError(f"trade {k}: side {side!r}")
        d, e, x = int(t["decision_ts"]), int(t["entry_ts"]), int(t["exit_ts"])
        if as_of_ts is not None and d > as_of_ts:
            raise PortfolioInputError(f"trade {k}: decided at {d} after as_of {as_of_ts} (look-ahead)")
        if not d < e:
            raise PortfolioInputError(f"trade {k}: decision {d} not strictly before entry {e} "
                                      "(fill on the signal bar)")
        if e not in idx[sym] or x not in idx[sym]:
            raise PortfolioInputError(f"trade {k}: entry/exit timestamp not a bar of {sym}")
        if not e <= x:
            raise PortfolioInputError(f"trade {k}: exit before entry")
        if e == x and t.get("exit_price") is None:
            raise PortfolioInputError(f"trade {k}: exit at the entry open (zero holding)")
        if t.get("exit_price") is not None:
            xb = bars_by_symbol[sym][idx[sym][x]]
            px = float(t["exit_price"])
            if not (xb.low - 1e-9 * xb.low <= px <= xb.high + 1e-9 * xb.high):
                raise PortfolioInputError(f"trade {k}: exit price {px} outside its bar "
                                          f"[{xb.low}, {xb.high}] (impossible fill)")
        if from_trades:
            syms_of[m].add(sym)
        elif sym not in syms_of[m]:
            raise PortfolioInputError(f"trade {k}: {sym} is not a symbol of {m}")
        tt = dict(t, side=side, decision_ts=d, entry_ts=e, exit_ts=x, uid=k, size_mult=1.0)
        bars = bars_by_symbol[sym]
        if cfg.latency_bars:
            ei = idx[sym][e] + cfg.latency_bars
            if ei >= len(bars):
                # the order would fill after the data ends: recorded, not a result
                n_latency_skip += 1
                continue
            tt["entry_ts"] = bars[ei].ts
            rule = (exit_rules or {}).get(m)
            if rule is not None:
                ex = rule[0](bars, ei, side, rule[1])
                if ex is None:          # horizon beyond the data: held to the end
                    tt["exit_ts"], tt["exit_price"], tt["exit_reason"] = bars[-1].ts, None, "end_of_data"
                    tt["hold_to_end"] = True
                else:
                    xi, px, why = ex
                    tt["exit_ts"] = bars[xi].ts
                    tt["exit_price"] = None if (px == bars[xi].open and xi > ei) else px
                    tt["exit_reason"] = why
            elif t.get("exit_price") is None:
                xi = idx[sym][x] + cfg.latency_bars
                if xi >= len(bars):
                    tt["exit_ts"], tt["hold_to_end"] = bars[-1].ts, True
                else:
                    tt["exit_ts"] = bars[xi].ts
            else:
                raise PortfolioInputError(f"trade {k}: latency stress on an intrabar exit needs the "
                                          "strategy's exit rule (exit_rules)")
        if cfg.fill_ratio < 1:
            # partial fill: the remainder fills at the NEXT open (its own price and
            # cost) as a second leg with the same exit; if that open is at or after
            # the exit, the remainder is cancelled and recorded as unfilled
            tt["size_mult"] = cfg.fill_ratio
            ri = idx[sym][tt["entry_ts"]] + 1
            rem = dict(tt, size_mult=1.0 - cfg.fill_ratio, leg="remainder")
            if ri < len(bars) and bars[ri].ts < tt["exit_ts"]:
                rem["entry_ts"] = bars[ri].ts
                sched.setdefault(rem["entry_ts"], []).append(rem)
            else:
                tt["remainder_cancelled"] = True
        sched.setdefault(tt["entry_ts"], []).append(tt)
    order = {m: i for i, m in enumerate(members)}
    for v in sched.values():
        # fixed priority for simultaneous signals: member order, then symbol
        v.sort(key=lambda t: (order[t["strategy"]], t["symbol"]))

    # -- state ----------------------------------------------------------------------------
    grid = sorted({b.ts for bars in bars_by_symbol.values() for b in bars})
    cash = float(cfg.initial_capital)
    open_: list[_Pos] = []
    last_px: dict[str, float] = {}
    prev_ts: dict[str, int] = {}
    equity_curve: list[tuple[int, float]] = []
    closed: list[dict] = []
    skips = {k: 0 for k in SKIP_REASONS}
    skips["latency_beyond_data"] = n_latency_skip
    # per leg: a primary leg is one evaluator event (= one signal that reached
    # the account); a remainder leg is the partial-fill stress's second fill
    skips_leg = {"primary": {k: 0 for k in SKIP_REASONS}, "remainder": {k: 0 for k in SKIP_REASONS}}
    primary_entered: set = set()
    skips_leg["primary"]["latency_beyond_data"] = n_latency_skip
    legs = {"primary_scheduled": sum(1 for v in sched.values() for t in v if t.get("leg") != "remainder"),
            "remainder_scheduled": sum(1 for v in sched.values() for t in v if t.get("leg") == "remainder"),
            "primary_entered": 0, "remainder_entered": 0, "partial_primary": 0,
            "remainder_cancelled": 0}
    liq_records: list[dict] = []
    funding_status_counts: dict[str, int] = {}
    dd = {"trough": [], "peak": [], "stale_marks": 0, "min_eq_to_maint": None}
    per = {m: {"entries": 0, "pnl": 0.0, "fees": 0.0, "slippage": 0.0, "funding": 0.0,
               "liquidations": 0, "skipped": 0, "daily": {}} for m in members}
    fund_issues = {s: series_issues(funding_by_symbol.get(s),
                                    meta=(funding_meta_by_symbol or {}).get(s), expected_symbol=s)
                   for s in bars_by_symbol}
    fund_idx = {s: sorted(funding_by_symbol.get(s) or []) for s in bars_by_symbol}
    fund_iv = {s: (None if funding_intervals_by_symbol is None else funding_intervals_by_symbol.get(s))
               for s in bars_by_symbol}
    tot = {"fees": 0.0, "slippage": 0.0, "funding": 0.0, "liq_loss": 0.0}
    cost_status = "OK"
    cost_failures: list[dict] = []
    refused_notional: dict[str, float] = {}
    liq_events = account_liqs = 0
    halted = bankrupt = False
    exposure_hist: list[float] = []
    margin_hist: list[float] = []
    stack_same = stack_opposite = 0
    unfilled_notional = 0.0
    # ADR 0072: cross-margin account checks that could not be made on prices of one
    # moment (an open position's symbol has no bar at ts -> its mark is a stale close)
    xval = {"checks_deferred": 0, "would_liquidate_with_stale_marks": 0, "entries_blocked": 0,
            "events": [], "stale_symbols": set()}
    XVAL_EVENTS_KEPT = 200

    def _xdefer(ts: int, phase: str, stale: Sequence[str], eq: float, maint: float) -> None:
        xval["checks_deferred"] += 1
        would = eq <= maint
        xval["would_liquidate_with_stale_marks"] += int(would)
        xval["stale_symbols"].update(stale)
        if len(xval["events"]) < XVAL_EVENTS_KEPT:
            xval["events"].append({"ts": ts, "phase": phase, "stale_symbols": sorted(set(stale)),
                                   "equity_with_stale_marks": eq, "maintenance_with_stale_marks": maint,
                                   "would_liquidate_with_stale_marks": would,
                                   "action": "account liquidation check NOT confirmed (deferred)"})

    def _skip(reason: str, t: Mapping) -> None:
        skips[reason] += 1
        skips_leg["remainder" if t.get("leg") == "remainder" else "primary"][reason] += 1
        per[t["strategy"]]["skipped"] += 1

    def _book(p: _Pos, pnl_gross: float, exit_fee: float, exit_slip: float, ts: int, reason: str,
              liquidated: bool = False, liq: Optional[dict] = None) -> None:
        nonlocal cash
        if p.closed:
            # a position is closed exactly once (a liquidated position is never
            # exited again, an exited one never liquidated)
            raise RuntimeError(f"internal: position {p.strategy}/{p.symbol}/{p.uid} closed twice")
        p.closed = True
        cash += pnl_gross - exit_fee - exit_slip
        p.fees += exit_fee
        p.slippage += exit_slip
        tot["fees"] += exit_fee
        tot["slippage"] += exit_slip
        net = pnl_gross - p.fees - p.slippage - p.funding
        a = per[p.strategy]
        a["pnl"] += net
        a["fees"] += p.fees
        a["slippage"] += p.slippage
        a["funding"] += p.funding
        a["liquidations"] += int(liquidated)
        day = ts // DAY_MS
        a["daily"][day] = a["daily"].get(day, 0.0) + net
        # funding completeness of THIS holding window (entry, exit] against the
        # declared schedule: the settlements charged above are only trusted when
        # none is missing (ADR 0071)
        fchk = settlement_check(fund_idx[p.symbol] if funding_by_symbol.get(p.symbol) is not None else None,
                                fund_iv[p.symbol], p.entry_ts, ts, issues=fund_issues[p.symbol])
        funding_status_counts[fchk["status"]] = funding_status_counts.get(fchk["status"], 0) + 1
        closed.append({"strategy": p.strategy, "symbol": p.symbol, "side": p.side,
                       "decision_ts": p.decision_ts, "entry_ts": p.entry_ts, "exit_ts": ts,
                       "reason": reason, "net": net, "notional": p.notional,
                       "fees": p.fees, "slippage": p.slippage, "funding": p.funding,
                       "liquidated": liquidated, "leg": p.leg,
                       "funding_status": fchk["status"],
                       "funding_reason": fchk["reason"]})
        open_.remove(p)
        if liquidated:
            liq_records.append({"strategy": p.strategy, "symbol": p.symbol, "side": p.side,
                                "entry_ts": p.entry_ts, "entry_price": p.entry_price,
                                "liq_price": p.liq_price, "ts": ts, "trigger": reason,
                                **(liq or {}), "loss_booked": -pnl_gross,
                                "equity_cash_after": cash,
                                "margin": p.margin,
                                "margin_treatment": ("posted margin forfeited (isolated)"
                                                     if cfg.margin_mode == "isolated" else
                                                     "account equity (cross)"),
                                "exit_fee_slippage": "NOT_CHARGED (liquidation fee NOT_MODELLED)",
                                "funding_paid_before": p.funding})

    def _exit_cost(p: _Pos, price: float, bar_i: int, *, qv_index: Optional[int] = None) -> tuple[float, float]:
        """Exit cost priced on the traded value of the last bar CLOSED before
        the fill (``bar_i - 1``; the end-of-data close at close[i] uses bar i)."""
        nonlocal cost_status
        bars = bars_by_symbol[p.symbol]
        qi = bar_i - 1 if qv_index is None else qv_index
        try:
            prev_qv = bars[qi].quote_volume if qi >= 0 else None
            frac = cost.quote(p.qty * price, prev_qv)
            return _split_cost(cost, frac, p.qty * price)
        except CostError as err:
            # the position is closed regardless; no substitute number is invented,
            # the RESULT is flagged and can never pass
            cost_failures.append({"where": "exit", "strategy": p.strategy, "symbol": p.symbol,
                                  "ts": bars[bar_i].ts, "status": err.status, "error": str(err)})
            cost_status = "UNPRICED_EXIT"
            return 0.0, 0.0

    def _key(q: _Pos) -> tuple:
        return (order[q.strategy], q.symbol, q.uid, q.leg)

    def _mark(p: _Pos, ts: int, what: str) -> tuple[float, bool]:
        """(price, stale): the bar's open / adverse / favourable extreme, or the
        last known close when the symbol has no bar at ``ts`` (stale)."""
        if ts in idx[p.symbol]:
            b = bars_by_symbol[p.symbol][idx[p.symbol][ts]]
            if what == "open":
                return b.open, False
            adverse = what == "adverse"
            return ((b.low if adverse else b.high) if p.side == 1 else (b.high if adverse else b.low)), False
        return last_px.get(p.symbol, p.entry_price), True

    for ts in grid:
        active_syms = [s for s in bars_by_symbol if ts in idx[s]]
        # 1) funding settlements in (previous bar, this bar], marked at this open.
        #    Charged once here; a liquidation at this open does not charge them again.
        for p in list(open_):
            if p.symbol not in active_syms:
                continue
            b = bars_by_symbol[p.symbol][idx[p.symbol][ts]]
            fl = fund_idx[p.symbol]
            lo = prev_ts.get(p.symbol, -1)
            while p.fi < len(fl) and fl[p.fi][0] <= lo:
                p.fi += 1
            while p.fi < len(fl) and fl[p.fi][0] <= ts:
                pay = p.side * fl[p.fi][1] * p.qty * b.open
                cash -= pay
                p.funding += pay
                tot["funding"] += pay
                p.fi += 1
        # 2) open-gap liquidation (ADR 0071): checked BEFORE any exit at the open.
        #    A position whose bar opens at or beyond its liquidation price is
        #    liquidated, even when its time exit / gapped stop is at this open.
        if cfg.margin_mode == "isolated":
            for p in sorted(open_, key=_key):
                if p.symbol not in active_syms:
                    continue
                o = bars_by_symbol[p.symbol][idx[p.symbol][ts]].open
                if (o <= p.liq_price) if p.side == 1 else (o >= p.liq_price):
                    price_loss = -(p.side * (o - p.entry_price) * p.qty)
                    tot["liq_loss"] += p.margin
                    liq_events += 1
                    _book(p, -p.margin, 0.0, 0.0, ts, "liquidation_open_gap", liquidated=True,
                          liq={"condition": "bar opened at or beyond the liquidation price",
                               "reference_price": o, "price_loss_at_reference": price_loss,
                               "uncapped_excess_loss": max(0.0, price_loss - p.margin),
                               "loss_rule": "isolated loss = posted margin (ADR 0068 model "
                                            "assumption, not an exchange rule)"})
        elif open_:
            eq_o, maint_o, stale_o = cash, 0.0, []
            for p in open_:
                px, st_ = _mark(p, ts, "open")
                eq_o += p.side * (px - p.entry_price) * p.qty
                maint_o += cfg.mmr * p.qty * px
                if st_:
                    stale_o.append(p.symbol)
            if stale_o:
                # ADR 0072: a mixed-time valuation (this open for some symbols, an older
                # close for others) is not the account at any real moment: no account
                # liquidation is confirmed or booked from it
                _xdefer(ts, "open", stale_o, eq_o, maint_o)
            elif eq_o <= maint_o:
                account_liqs += 1
                for p in sorted(open_, key=_key):
                    px, st_ = _mark(p, ts, "open")
                    loss = -(p.side * (px - p.entry_price) * p.qty) + cfg.mmr * p.qty * px
                    tot["liq_loss"] += loss
                    liq_events += 1
                    _book(p, -loss, 0.0, 0.0, ts, "account_liquidation_open_gap", liquidated=True,
                          liq={"condition": "account equity at the open <= summed maintenance margin",
                               "reference_price": px, "stale_mark": st_,
                               "account_equity_at_reference": eq_o, "maintenance_at_reference": maint_o,
                               "loss_rule": "loss to the open plus mmr*notional (model assumption)"})
        # 3) exits at the open (time exits, gap fills of stops/take-profits)
        for p in sorted(open_, key=_key):
            if p.symbol in active_syms and ts == p.exit_ts and p.exit_price is None:
                bi = idx[p.symbol][ts]
                px = bars_by_symbol[p.symbol][bi].open
                f, s_ = _exit_cost(p, px, bi)
                _book(p, p.side * (px - p.entry_price) * p.qty, f, s_, ts, p.exit_reason)
        # 4) entries at the open, fixed priority
        if not halted:
            equity_open = cash + sum(p.side * (_mark(p, ts, "open")[0] - p.entry_price) * p.qty
                                     for p in open_)
        # ADR 0072: cross margin sizes and admits an entry on the WHOLE account; with a
        # stale mark the equity / free margin are unknown, so no entry at this ts
        x_incomplete = cfg.margin_mode == "cross" and any(p.symbol not in active_syms for p in open_)
        for t in sched.get(ts, []):
            m, sym = t["strategy"], t["symbol"]
            if halted:
                _skip("halted", t)
                continue
            if t.get("leg") == "remainder" and t["uid"] not in primary_entered:
                # ADR 0078 (E-05): the rest of an order the account refused never fills
                _skip("primary_not_entered", t)
                continue
            if m in inactive_from and t["decision_ts"] >= inactive_from[m]:
                _skip("inactive", t)
                continue
            same = [p for p in open_ if p.symbol == sym]
            if any(p.strategy == m and p.uid != t["uid"] for p in same):
                # one strategy never holds two positions on one symbol: a repeated
                # signal while the first trade is open is a duplicate, not new size
                _skip("duplicate_signal", t)
                continue
            if x_incomplete:
                # after the strategy's own rules (inactive, duplicate), before any sizing
                _skip("account_valuation_incomplete", t)
                xval["entries_blocked"] += 1
                continue
            bi = idx[sym][ts]
            bar = bars_by_symbol[sym][bi]
            alloc = equity_open * weights[m] / max(1, len(syms_of[m]))
            full = alloc * cfg.position_fraction * cfg.leverage
            desired = full * t["size_mult"]

            def _refuse(reason: str) -> None:
                _skip(reason, t)
                if desired > 0:
                    refused_notional[reason] = refused_notional.get(reason, 0.0) + desired
            if same and cfg.same_symbol_policy == "one_per_symbol" and not any(p.uid == t["uid"] for p in same):
                _refuse("same_symbol")
                continue
            if len(open_) >= cfg.max_concurrent:
                _refuse("capacity")
                continue
            if not (desired > 0):
                _skip("zero_allocation" if weights[m] == 0 else "margin", t)
                continue
            notional = desired
            margin = notional / cfg.leverage
            used = sum(p.margin for p in open_)
            if used + margin > equity_open + 1e-9:
                _refuse("margin")
                continue
            gross = sum(p.notional for p in open_) + notional
            if gross > cfg.max_gross_exposure * equity_open + 1e-9:
                _refuse("gross_exposure")
                continue
            try:
                frac = cost.quote(notional, bars_by_symbol[sym][bi - 1].quote_volume if bi > 0 else None)
            except CostRefused:
                _refuse("cost_refused")
                continue
            except CostDataMissing:
                _refuse("cost_data_missing")
                continue
            except CostError as err:
                cost_failures.append({"where": "entry", "strategy": m, "symbol": sym, "ts": ts,
                                      "status": err.status, "error": str(err)})
                cost_status = "UNPRICED_EXIT" if cost_status == "UNPRICED_EXIT" else "COST_ERROR"
                _refuse("cost_error")
                continue
            fee, slip = _split_cost(cost, frac, notional)
            cash -= fee + slip
            tot["fees"] += fee
            tot["slippage"] += slip
            if any(p.side == t["side"] for p in same):
                stack_same += 1
            if any(p.side != t["side"] for p in same):
                stack_opposite += 1
            leg = "remainder" if t.get("leg") == "remainder" else "primary"
            open_.append(_Pos(strategy=m, symbol=sym, side=t["side"], entry_ts=ts,
                              exit_ts=t["exit_ts"], exit_price=t.get("exit_price"),
                              exit_reason=t.get("exit_reason") or "time", entry_price=bar.open,
                              qty=notional / bar.open, notional=notional, margin=margin,
                              liq_price=liq_price(t["side"], bar.open, cfg.leverage, cfg.mmr),
                              fees=fee, slippage=slip, decision_ts=t["decision_ts"], uid=t["uid"],
                              hold_to_end=bool(t.get("hold_to_end")), leg=leg))
            per[m]["entries"] += 1
            legs[leg + "_entered"] += 1
            if leg == "primary":
                primary_entered.add(t["uid"])
            if leg == "primary" and t["size_mult"] < 1:
                legs["partial_primary"] += 1
            if t.get("remainder_cancelled"):
                # the unfilled rest of an ENTERED partial fill (never re-tried)
                legs["remainder_cancelled"] += 1
                unfilled_notional += full - desired
            # the funding pointer starts after the entry open (settlement at the
            # entry timestamp is not held, evaluator.FundingIndex rule)
            fl = fund_idx[sym]
            p_ = open_[-1]
            while p_.fi < len(fl) and fl[p_.fi][0] <= ts:
                p_.fi += 1
        # 5a) intrabar worst / best case of the positions that exist during this
        #     bar (ADR 0071): long at the low, short at the high (best: the other
        #     extreme); an isolated position cannot lose more than its margin
        #     (model assumption).  Extremes of different symbols are taken at
        #     one moment: a conservative stress assumption, not an observation.
        worst_eq = best_eq = cash
        maint_w = 0.0
        for p in open_:
            adv, st_ = _mark(p, ts, "adverse")
            fav, _ = _mark(p, ts, "favourable")
            if st_:
                dd["stale_marks"] += 1
            w = p.side * (adv - p.entry_price) * p.qty
            if cfg.margin_mode == "isolated":
                w = max(w, -p.margin)
            worst_eq += w
            best_eq += p.side * (fav - p.entry_price) * p.qty
            maint_w += cfg.mmr * p.qty * adv
        dd["trough"].append(worst_eq)
        dd["peak"].append(best_eq)
        if open_ and maint_w > 0:
            r_ = worst_eq / maint_w
            dd["min_eq_to_maint"] = r_ if dd["min_eq_to_maint"] is None else min(dd["min_eq_to_maint"], r_)
        # 5b) intrabar: liquidation vs stop / take-profit (larger loss wins)
        if cfg.margin_mode == "isolated":
            for p in sorted(open_, key=_key):
                if p.symbol not in active_syms:
                    continue
                bi = idx[p.symbol][ts]
                b = bars_by_symbol[p.symbol][bi]
                hit = (b.low <= p.liq_price) if p.side == 1 else (b.high >= p.liq_price)
                stop_here = ts == p.exit_ts and p.exit_price is not None
                if hit:
                    liq_loss = p.margin
                    if stop_here:
                        # compare the price losses first: the stop's exit cost is priced
                        # only when the stop is the fill that is booked
                        stop_loss = -(p.side * (p.exit_price - p.entry_price) * p.qty)
                        if stop_loss >= liq_loss:
                            # ADR 0078 (E-04): the stop lies beyond the liquidation
                            # price, so the price crossed the liquidation level first:
                            # the larger (stop) loss is booked AND it counts as a
                            # liquidation (``liquidation_events_eq: 0`` must see it)
                            f, s_ = _exit_cost(p, p.exit_price, bi)
                            liq_events += 1
                            _book(p, p.side * (p.exit_price - p.entry_price) * p.qty, f, s_, ts,
                                  p.exit_reason, liquidated=True,
                                  liq={"condition": "stop price beyond the liquidation price in one bar",
                                       "reference_price": p.exit_price,
                                       "price_loss_at_reference": stop_loss,
                                       "uncapped_excess_loss": max(0.0, stop_loss - liq_loss),
                                       "loss_rule": "larger loss booked (stop), counted as liquidation"})
                            continue
                    adv = b.low if p.side == 1 else b.high
                    tot["liq_loss"] += liq_loss
                    liq_events += 1
                    _book(p, -liq_loss, 0.0, 0.0, ts, "liquidation", liquidated=True,
                          liq={"condition": "bar extreme reached the liquidation price",
                               "reference_price": adv,
                               "price_loss_at_reference": -(p.side * (adv - p.entry_price) * p.qty),
                               "uncapped_excess_loss": max(0.0, -(p.side * (adv - p.entry_price) * p.qty)
                                                           - liq_loss),
                               "loss_rule": "isolated loss = posted margin (ADR 0068 model "
                                            "assumption, not an exchange rule)"})
                elif stop_here:
                    f, s_ = _exit_cost(p, p.exit_price, bi)
                    _book(p, p.side * (p.exit_price - p.entry_price) * p.qty, f, s_, ts, p.exit_reason)
        else:
            # cross: one account; all adverse extremes at the same moment (conservative)
            worst = cash
            maint = 0.0
            stale_w = []
            for p in open_:
                adv, st_ = _mark(p, ts, "adverse")
                worst += p.side * (adv - p.entry_price) * p.qty
                maint += cfg.mmr * p.qty * adv
                if st_:
                    stale_w.append(p.symbol)
            if open_ and stale_w:
                # ADR 0072: the simultaneous adverse extremes of the account cannot be
                # formed without every open symbol's bar: not confirmed (deferred)
                _xdefer(ts, "intrabar", stale_w, worst, maint)
            elif open_ and worst <= maint:
                account_liqs += 1
                for p in sorted(open_, key=_key):
                    adv, st_ = _mark(p, ts, "adverse")
                    loss = -(p.side * (adv - p.entry_price) * p.qty) + cfg.mmr * p.qty * adv
                    tot["liq_loss"] += loss
                    liq_events += 1
                    _book(p, -loss, 0.0, 0.0, ts, "account_liquidation", liquidated=True,
                          liq={"condition": "account equity at the bar extremes <= summed maintenance",
                               "reference_price": adv, "stale_mark": st_,
                               "account_equity_at_reference": worst, "maintenance_at_reference": maint,
                               "loss_rule": "loss to the extreme plus mmr*notional (model assumption)"})
            for p in sorted(open_, key=_key):
                if p.symbol in active_syms and ts == p.exit_ts and p.exit_price is not None:
                    bi = idx[p.symbol][ts]
                    f, s_ = _exit_cost(p, p.exit_price, bi)
                    _book(p, p.side * (p.exit_price - p.entry_price) * p.qty, f, s_, ts, p.exit_reason)
        # 6) mark to market at the close
        for s in active_syms:
            last_px[s] = bars_by_symbol[s][idx[s][ts]].close
            prev_ts[s] = ts
        unreal = sum(p.side * (last_px.get(p.symbol, p.entry_price) - p.entry_price) * p.qty for p in open_)
        eq = cash + unreal
        equity_curve.append((ts, eq))
        exposure_hist.append(sum(p.notional for p in open_) / eq if eq > 0 else math.inf)
        margin_hist.append(sum(p.margin for p in open_))
        if not halted and not eq > 0:
            halted = bankrupt = True
    # 6) end of data: every open position closes at its last close (normal exit cost)
    forced_end = 0
    for p in sorted(open_, key=_key):
        bars = bars_by_symbol[p.symbol]
        bi = len(bars) - 1
        px = bars[bi].close
        f, s_ = _exit_cost(p, px, bi, qv_index=bi)
        _book(p, p.side * (px - p.entry_price) * p.qty, f, s_, bars[bi].ts, "end_of_data")
        forced_end += 1
    final_equity = cash
    if equity_curve:
        equity_curve[-1] = (equity_curve[-1][0], final_equity)
    # a held window whose settlements are not all present (coverage, a missing
    # middle settlement, unknown schedule, invalid series): the cost is unknown
    funding_uncovered = sum(1 for t in closed if t["funding_status"] != "OK")
    accounting = signal_accounting(skips_leg, legs, signal_diagnostics, n_trades_in=len(trades),
                                   refused_notional=refused_notional, unfilled_notional=unfilled_notional)

    res = {
        "model_version": PORTFOLIO_MULTI_VERSION,
        "config": asdict(cfg), "members": members, "weights": weights,
        "symbols": sorted(bars_by_symbol), "as_of_ts": as_of_ts, "data_end_ts": data_end,
        "inactive_from": inactive_from,
        "initial_capital": cfg.initial_capital, "final_equity": final_equity,
        "bankrupt": bankrupt, "halted": halted,
        "liquidation_events": liq_events, "account_liquidations": account_liqs,
        "forced_end_closes": forced_end,
        "skipped": skips, "stacked_same_direction": stack_same,
        "stacked_opposite_direction": stack_opposite,
        "unfilled_notional": unfilled_notional,
        "cost_status": cost_status, "cost_failures": cost_failures,
        "funding_uncovered_trades": funding_uncovered,
        "funding_completeness": {"trades_by_status": dict(sorted(funding_status_counts.items())),
                                 "series_issues": {k: v for k, v in fund_issues.items() if v},
                                 "schedule_declared": {k: v is not None for k, v in fund_iv.items()},
                                 "rule": "settlements in (entry, exit] against the declared "
                                         "interval per settlement (funding_schedule-v1)"},
        "signal_accounting": accounting,
        "cross_account_valuation": (
            {"status": "NOT_APPLICABLE", "reason": "isolated margin: each position is valued on its own bar"}
            if cfg.margin_mode != "cross" else
            {"status": "INCOMPLETE" if (xval["checks_deferred"] or xval["entries_blocked"]) else "COMPLETE",
             "checks_deferred": xval["checks_deferred"],
             "would_liquidate_with_stale_marks": xval["would_liquidate_with_stale_marks"],
             "entries_blocked": xval["entries_blocked"],
             "stale_symbols": sorted(xval["stale_symbols"]),
             "events": xval["events"], "events_kept_max": XVAL_EVENTS_KEPT,
             "rule": "an account check (open-gap or intrabar liquidation, entry sizing) is made only "
                     "when every open position's symbol has a bar at that timestamp; otherwise it is "
                     "deferred (no liquidation booked, no entry) and the result is INCOMPLETE "
                     "(UNDETERMINED_PORTFOLIO), ADR 0072"}),
        "liquidations": liq_records,
        "totals": tot,
        "cost_components": cost_completeness(cost, cfg),
        "assumptions": {"liquidation_price": "entry*(1-1/L+mmr) long, entry*(1+1/L-mmr) short",
                        "isolated_liquidation_loss": "posted margin (also when the bar OPENS beyond "
                                                     "the liquidation price: the excess is recorded as "
                                                     "uncapped_excess_loss, not booked)",
                        "liquidation_check_order": "funding at the open -> open-gap liquidation -> "
                                                   "exits at the open -> entries -> intrabar",
                        "liquidation_at_price": "open or extreme exactly AT the liquidation price "
                                                "liquidates (<= long, >= short)",
                        "cross_liquidation": "all adverse bar extremes at one moment; loss to the "
                                             "extreme plus mmr*notional; never on a stale mark "
                                             "(deferred, ADR 0072)",
                        "intrabar_conflict": "liquidation vs stop/take-profit in one bar: larger loss",
                        "simultaneous_signals": "member order, then symbol",
                        "same_symbol": cfg.same_symbol_policy,
                        "end_of_data": "close at the last close with the normal exit cost",
                        "exchange_rules": "NOT_MODELLED (tiers, insurance fund, ADL)"},
    }
    res["metrics"] = portfolio_metrics(equity_curve, closed, cfg.initial_capital,
                                       exposure_hist, margin_hist, per, members)
    res["metrics"].update(drawdown_metrics(equity_curve, dd, cfg.initial_capital, final_equity))
    res["equity_daily"] = _daily_close(equity_curve)
    res["trades_closed"] = len(closed)
    res["result_id"] = result_id(res)
    return res


def cost_completeness(cost: CostContext, cfg: MultiConfig) -> dict:
    """Which cost components this result charged and on what evidence.  A
    component that is not charged is NOT_MODELLED, never 0 (ADR 0063 table)."""
    from costs_bridge import cost_component_evidence
    comp = cost_component_evidence(cost)
    out = {k: {"treatment": v["treatment"], "evidence": v["evidence"]} for k, v in comp.items()}
    if cfg.fill_ratio < 1:
        out["partial_fill"] = {"treatment": f"STRESS fill_ratio={cfg.fill_ratio} (deterministic, "
                               "not calibrated)", "evidence": "NOT_MODELLED"}
    if cfg.latency_bars:
        out["fill_latency"] = {"treatment": f"STRESS latency_bars={cfg.latency_bars} (entries and "
                               "time exits later; not calibrated)", "evidence": "NOT_MODELLED"}
    out["liquidation_fee"] = {"treatment": "NOT_MODELLED (a liquidation books the posted margin / "
                                            "the loss to the reference price; no exchange liquidation "
                                            "fee or insurance-fund charge)", "evidence": "NOT_MODELLED"}
    missing = sorted(k for k, v in out.items() if v["evidence"] == "NOT_MODELLED")
    return {"components": out, "not_modelled": missing,
            "complete": not missing and cost.status == "VERIFIED"}


def _daily_close(curve: Sequence[tuple[int, float]]) -> list[list]:
    out: dict[int, float] = {}
    for ts, v in curve:
        out[ts // DAY_MS] = v
    return [[d, out[d]] for d in sorted(out)]


def _nc(reason: str) -> dict:
    return {"value": None, "status": "NOT_COMPUTABLE", "reason": reason}


def _v(x: float) -> dict:
    return {"value": x, "status": "OK"} if math.isfinite(x) else _nc("non-finite")


def max_drawdown(values: Sequence[float], initial: float) -> float:
    peak, mdd = float(initial), 0.0
    for v in values:
        if not math.isfinite(v):
            return float("nan")
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, min(1.0, (peak - v) / peak))
    return mdd


def _corr(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


#: minimum sample for the daily-return metrics; below it they are NOT_COMPUTABLE
MIN_DAYS_RATIO = 30
MIN_DAYS_CAGR = 365


def portfolio_metrics(curve: Sequence[tuple[int, float]], closed: Sequence[Mapping],
                      initial: float, exposure: Sequence[float], margin: Sequence[float],
                      per: Mapping[str, Mapping], members: Sequence[str]) -> dict:
    """Every metric has a status: OK or NOT_COMPUTABLE with the reason.  Nothing
    is filled with 0 when it cannot be computed."""
    m: dict = {}
    if not curve:
        return {"status": "NO_DATA"}
    final = curve[-1][1]
    m["total_return"] = _v(final / initial - 1.0)
    span_days = (curve[-1][0] - curve[0][0]) / DAY_MS
    m["span_days"] = span_days
    daily = _daily_close(curve)
    eqs = [initial] + [v for _, v in daily]
    rets = [eqs[i] / eqs[i - 1] - 1.0 for i in range(1, len(eqs)) if eqs[i - 1] > 0]
    if span_days >= MIN_DAYS_CAGR and final > 0:
        cagr = (final / initial) ** (365.0 / span_days) - 1.0
        m["cagr"] = _v(cagr)
    else:
        m["cagr"] = _nc(f"span {span_days:.1f} d < {MIN_DAYS_CAGR} d (annualising a short "
                        "period overstates it)" if final > 0 else "equity <= 0")
        cagr = None
    mdd = max_drawdown([v for _, v in curve], initial)
    m["max_drawdown"] = _v(mdd)
    if len(rets) >= MIN_DAYS_RATIO:
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        m["sharpe_annual"] = _v(mu / sd * math.sqrt(365)) if sd > 0 else _nc("zero variance")
        dn = [min(r, 0.0) for r in rets]
        dsd = math.sqrt(sum(r * r for r in dn) / len(dn))
        m["sortino_annual"] = _v(mu / dsd * math.sqrt(365)) if dsd > 0 else _nc("no losing day")
    else:
        m["sharpe_annual"] = m["sortino_annual"] = _nc(f"{len(rets)} daily returns < {MIN_DAYS_RATIO}")
    m["calmar"] = (_v(cagr / mdd) if cagr is not None and mdd > 0 else
                   _nc("CAGR not computable" if cagr is None else "no drawdown"))
    nets = [t["net"] for t in closed]
    m["trades"] = len(closed)
    if nets:
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x <= 0]
        m["win_rate"] = _v(len(wins) / len(nets))
        m["avg_pnl"] = _v(sum(nets) / len(nets))
        m["profit_factor"] = (_v(sum(wins) / -sum(losses)) if losses and sum(losses) < 0
                              else _nc("no losing trade"))
        run = best = 0
        for x in sorted(closed, key=lambda t: (t["exit_ts"], t["entry_ts"])):
            run = run + 1 if x["net"] <= 0 else 0
            best = max(best, run)
        m["max_consecutive_losses"] = best
        m["avg_holding_hours"] = _v(sum((t["exit_ts"] - t["entry_ts"]) for t in closed) / len(closed) / 3.6e6)
    else:
        for k in ("win_rate", "avg_pnl", "profit_factor", "avg_holding_hours"):
            m[k] = _nc("no closed trade")
        m["max_consecutive_losses"] = None
    fin = [x for x in exposure if math.isfinite(x)]
    m["exposure_gross_avg"] = _v(sum(fin) / len(fin)) if fin else _nc("no finite equity")
    m["exposure_gross_max"] = _v(max(exposure)) if exposure else _nc("no bar")
    m["used_margin_avg"] = _v(sum(margin) / len(margin)) if margin else _nc("no bar")
    m["used_margin_max"] = _v(max(margin)) if margin else _nc("no bar")
    m["liquidations"] = sum(1 for t in closed if t["liquidated"])
    m["funding_total"] = sum(t["funding"] for t in closed)
    m["fees_total"] = sum(t["fees"] for t in closed)
    m["slippage_total"] = sum(t["slippage"] for t in closed)
    # per-strategy contribution and daily-PnL correlation
    contrib = {}
    total_pnl = sum(per[s]["pnl"] for s in members)
    for s in members:
        a = per[s]
        contrib[s] = {"entries": a["entries"], "skipped": a["skipped"], "net_pnl": a["pnl"],
                      "return_on_initial": a["pnl"] / initial, "fees": a["fees"],
                      "slippage": a["slippage"], "funding": a["funding"],
                      "liquidations": a["liquidations"],
                      "share_of_total_pnl": (a["pnl"] / total_pnl) if total_pnl != 0 else None}
    m["strategy_contribution"] = contrib
    corr: dict = {}
    if len(members) >= 2 and daily:
        days = [d for d, _ in daily]
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                xa = [per[a]["daily"].get(d, 0.0) for d in days]
                xb = [per[b]["daily"].get(d, 0.0) for d in days]
                key = f"{a}|{b}"
                if len(days) < MIN_DAYS_RATIO:
                    corr[key] = _nc(f"{len(days)} days < {MIN_DAYS_RATIO}")
                else:
                    c = _corr(xa, xb)
                    corr[key] = _v(c) if c is not None else _nc("a strategy has no daily variation")
    m["strategy_correlation_daily_pnl"] = corr if len(members) >= 2 else _nc("one strategy")
    return m


#: evaluator buckets (evaluator.EventStudyResult.diagnostics) -> class
EVAL_CLASS = {"skipped_overlapped": "strategy_rule", "truncated_end_of_window": "window_end",
              "participation_refusals": "cost_refused", "cost_data_missing": "data_missing",
              "dropped_missing_funding": "data_missing"}
CLASSES = ("strategy_rule", "window_end", "cost_refused", "data_missing", "capital", "halted")
REJECTION_CLASSES = ("cost_refused", "data_missing", "capital", "halted")


def signal_accounting(skips_leg: Mapping[str, Mapping[str, int]], legs: Mapping[str, int],
                      diagnostics: Optional[Mapping[str, Mapping]], *, n_trades_in: int,
                      refused_notional: Mapping[str, float], unfilled_notional: float) -> dict:
    """Every signal from the evaluator to the account in exactly one bucket
    (ADR 0071).  Denominator of every rejection rate:

        execution_candidates = signals_total - strategy_rule - window_end

    (strategy_rule = the strategy's own non-overlap / duplicate / inactive
    rule; window_end = exit or delayed fill beyond the evaluated data).  The
    numerator classes are cost_refused (participation cap, evaluator at the
    research order size + account at the actual notional), data_missing
    (no traded value, unpriceable fill, funding coverage / settlement gap /
    unknown schedule), capital (margin, gross exposure, concurrent-position
    cap, same-symbol policy, zero allocation) and halted (after bankruptcy).
    Partial fills are counted on ENTERED primary legs.  The identities are
    checked and reported, never forced."""
    prim = skips_leg["primary"]
    pf = {c: 0 for c in CLASSES}
    for k, v in prim.items():
        pf[SKIP_CLASS[k]] += v
    out: dict = {"portfolio_primary_legs": {"scheduled_plus_latency_skip": legs["primary_scheduled"]
                                            + prim["latency_beyond_data"],
                                            "entered": legs["primary_entered"],
                                            "skipped_by_reason": dict(prim),
                                            "skipped_by_class": pf},
                 "remainder_legs": {"scheduled": legs["remainder_scheduled"],
                                    "entered": legs["remainder_entered"],
                                    "skipped_by_reason": {k: v for k, v in skips_leg["remainder"].items() if v}},
                 "partial_fills": legs["partial_primary"],
                 "remainders_cancelled": legs["remainder_cancelled"],
                 "unfilled_notional": unfilled_notional,
                 "refused_notional_by_reason": dict(sorted(refused_notional.items())),
                 "denominator": "execution_candidates = signals_total - strategy_rule - window_end"}
    portfolio_ok = (legs["primary_scheduled"] + prim["latency_beyond_data"] == n_trades_in
                    and legs["primary_entered"] + sum(v for k, v in prim.items()
                                                      if k != "latency_beyond_data")
                    == legs["primary_scheduled"])
    out["portfolio_identity_ok"] = portfolio_ok
    if diagnostics is None:
        out.update(status="EVALUATOR_DIAGNOSTICS_NOT_PROVIDED", identity_ok=None,
                   note="signals refused or dropped before the account are unknown")
        return out
    ev = {c: 0 for c in CLASSES}
    totals = {k: 0 for k in ("signals_total", "accepted_events", *EVAL_CLASS)}
    bad = []
    for key, d in sorted(diagnostics.items()):
        if d is None:
            bad.append(key)
            continue
        for k in totals:
            totals[k] += int(d.get(k) or 0)
        for k, c in EVAL_CLASS.items():
            ev[c] += int(d.get(k) or 0)
        if not d.get("accounting_ok", False):
            bad.append(key)
    cls = {c: ev[c] + pf[c] for c in CLASSES}
    sig = totals["signals_total"]
    cand = sig - cls["strategy_rule"] - cls["window_end"]
    entered = legs["primary_entered"]
    rejected = sum(cls[c] for c in REJECTION_CLASSES)
    ident = (not bad and portfolio_ok and totals["accepted_events"] == n_trades_in
             and cand == entered + rejected)

    def rate(x):
        return (x / cand) if cand > 0 else None
    out.update(status="OK" if ident else "ACCOUNTING_MISMATCH", identity_ok=ident,
               evaluator=totals, evaluator_by_class=ev, by_class=cls,
               signals_total=sig, execution_candidates=cand, entered=entered,
               rejected=rejected, rejection_rate=rate(rejected),
               data_missing_rate=rate(cls["data_missing"]), cost_refusal_rate=rate(cls["cost_refused"]),
               capital_refusal_rate=rate(cls["capital"]),
               margin_refusal_rate=rate(prim["margin"]), exposure_refusal_rate=rate(prim["gross_exposure"]),
               capacity_refusal_rate=rate(prim["capacity"]), halted_rate=rate(cls["halted"]),
               partial_fill_rate=(legs["partial_primary"] / entered) if entered else None,
               cancel_rate=(legs["remainder_cancelled"] / entered) if entered else None,
               diagnostics_problems=bad)
    return out


def _mdd_path(points: Sequence[tuple[str, float]], initial: float) -> float:
    """Peak-to-trough on a sequence of ("peak"|"trough"|"both", equity):
    peaks update the running maximum, troughs are measured against it."""
    peak, mdd = float(initial), 0.0
    for kind, v in points:
        if not math.isfinite(v):
            return float("nan")
        if kind in ("trough", "both") and peak > 0:
            mdd = max(mdd, min(1.0, (peak - v) / peak))
        if kind in ("peak", "both"):
            peak = max(peak, v)
    return mdd


def drawdown_metrics(curve: Sequence[tuple[int, float]], dd: Mapping, initial: float,
                     final_equity: float) -> dict:
    """Three drawdowns, never mixed (ADR 0071):

    * ``max_drawdown_close``: peak and trough on the equity at each bar close
      (the v1 ``max_drawdown``);
    * ``max_drawdown_intrabar_worst_case``: peaks on closes, troughs at each
      bar's worst case (every open long at its low, short at its high, at one
      moment; an isolated position floored at -margin);
    * ``max_drawdown_equity_peak_to_trough``: the envelope — peaks also at each
      bar's best case (taken before the same bar's worst case).

    Status: OK; INSUFFICIENT_DATA below 2 bars; NOT_COMPUTABLE on a
    non-finite value; UNDETERMINED when an open position had no bar of its
    own at some time (marked at its last close: ``stale_marks``)."""
    n = len(curve)
    closes = [v for _, v in curve]
    if closes:
        closes[-1] = final_equity
    out: dict = {"max_drawdown_calculation_method": {
        "close": "equity at each bar close",
        "intrabar_worst_case": "close peaks; trough per bar = cash + open positions at the adverse "
                               "extreme (isolated floored at -margin); extremes of all symbols at one "
                               "moment (conservative stress assumption, not an observation)",
        "equity_peak_to_trough": "as intrabar_worst_case plus peaks at each bar's favourable extreme"},
        "stale_marks": dd["stale_marks"],
        "min_equity_to_maintenance_ratio": dd["min_eq_to_maint"]}
    if n < 2 or len(dd["trough"]) != n:
        st = "INSUFFICIENT_DATA" if n < 2 else "NOT_COMPUTABLE"
        for k in ("max_drawdown_close", "max_drawdown_intrabar_worst_case",
                  "max_drawdown_equity_peak_to_trough"):
            out[k] = {"value": None, "status": st, "reason": f"{n} bars"}
        out["max_drawdown_status"] = st
        return out
    close_pts = [("both", v) for v in closes]
    intra = [p for i in range(n) for p in (("trough", dd["trough"][i]), ("both", closes[i]))]
    env = [p for i in range(n) for p in (("peak", dd["peak"][i]), ("trough", dd["trough"][i]),
                                           ("both", closes[i]))]
    stat = "UNDETERMINED" if dd["stale_marks"] else "OK"
    for k, pts, s_ in (("max_drawdown_close", close_pts, "OK"),
                       ("max_drawdown_intrabar_worst_case", intra, stat),
                       ("max_drawdown_equity_peak_to_trough", env, stat)):
        v = _mdd_path(pts, initial)
        out[k] = ({"value": v, "status": s_} if math.isfinite(v) else
                  {"value": None, "status": "NOT_COMPUTABLE", "reason": "non-finite equity"})
        if s_ == "UNDETERMINED" and math.isfinite(v):
            out[k]["reason"] = f"{dd['stale_marks']} position-bars marked at a stale price"
    sts = {out[k]["status"] for k in ("max_drawdown_close", "max_drawdown_intrabar_worst_case",
                                      "max_drawdown_equity_peak_to_trough")}
    out["max_drawdown_status"] = ("OK" if sts == {"OK"} else "NOT_COMPUTABLE" if "NOT_COMPUTABLE" in sts
                                  else "UNDETERMINED")
    return out


def result_id(res: Mapping) -> str:
    core = {k: v for k, v in res.items() if k != "result_id"}
    raw = json.dumps(core, sort_keys=True, separators=(",", ":"), default=str)
    return "PM-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------

#: check keys a portfolio_validation config may use (v1 keys + ADR 0071 v2 keys)
JUDGE_KEYS_V1 = frozenset({"final_equity_gt_initial", "max_drawdown_lt", "liquidation_events_eq",
                           "no_bankruptcy", "min_trades", "min_days", "max_margin_skip_share"})
JUDGE_KEYS_V2 = JUDGE_KEYS_V1 | {"max_drawdown_close_lt", "max_drawdown_intrabar_lt",
                                 "max_drawdown_equity_peak_to_trough_lt",
                                 "require_signal_accounting", "max_data_missing_signals",
                                 "max_cost_refusal_share", "max_capital_refusal_share"}
_FUNDING_INSUFFICIENT = ("FUNDING_DATA_MISSING", "FUNDING_SETTLEMENT_GAP", "FUNDING_DATA_INVALID")


def judge(res: Mapping, criteria: Mapping, *, data_issues: Sequence[str] = ()) -> tuple[str, list[str]]:
    """Status of one simulation result against preregistered ``criteria``
    (a scenario's ``checks`` in config/portfolio_validation_v*.json).  Order:

    1. data issues, funding missing / settlement gap / invalid series, too few
       trades or days, more data-missing signals than allowed
       -> INSUFFICIENT_PORTFOLIO_DATA;
    2. unknown funding schedule, signal accounting absent or inconsistent,
       unpriced fills, a cross account valuation with stale marks (ADR 0072),
       a gated drawdown (close / intrabar worst case / envelope) that is not
       OK / not finite -> UNDETERMINED_PORTFOLIO;
    3. every numeric check -> FAIL_PORTFOLIO with the reasons;
    4. refusals of a class whose share limit is ``null`` (not preregistered,
       ADR 0071) -> UNDETERMINED_PORTFOLIO: no threshold is invented after
       the fact, and a result with refused signals is not passed without one;
    5. else PASS_PORTFOLIO."""
    if data_issues:
        return "INSUFFICIENT_PORTFOLIO_DATA", list(data_issues)
    why: list[str] = []
    met = res.get("metrics") or {}
    fstat = ((res.get("funding_completeness") or {}).get("trades_by_status") or {})
    nf = {k: v for k, v in fstat.items() if k in _FUNDING_INSUFFICIENT and v}
    if nf or (res.get("funding_uncovered_trades") and not fstat):
        detail = ", ".join(f"{k}: {v}" for k, v in sorted(nf.items())) or \
            f"{res.get('funding_uncovered_trades')} trades"
        return "INSUFFICIENT_PORTFOLIO_DATA", [f"trades without complete funding settlements ({detail})"]
    if int(res.get("trades_closed") or 0) < int(criteria.get("min_trades", 1)):
        return "INSUFFICIENT_PORTFOLIO_DATA", [f"{res.get('trades_closed')} closed trades < "
                                                f"{criteria.get('min_trades', 1)}"]
    if (met.get("span_days") or 0) < float(criteria.get("min_days", 0)):
        return "INSUFFICIENT_PORTFOLIO_DATA", [f"span {met.get('span_days')} d < {criteria.get('min_days')} d"]
    acc = res.get("signal_accounting") or {}
    if "max_data_missing_signals" in criteria and acc.get("status") == "OK":
        n_dm = int((acc.get("by_class") or {}).get("data_missing", 0))
        if n_dm > int(criteria["max_data_missing_signals"]):
            return "INSUFFICIENT_PORTFOLIO_DATA", [
                f"{n_dm} signals could not be priced or funded (data missing) > "
                f"{criteria['max_data_missing_signals']}: their outcome is unknown"]
    if fstat.get("FUNDING_SCHEDULE_UNKNOWN"):
        return "UNDETERMINED_PORTFOLIO", [f"{fstat['FUNDING_SCHEDULE_UNKNOWN']} trades: funding "
                                          "settlement schedule unknown (completeness not checkable)"]
    if criteria.get("require_signal_accounting") and acc.get("status") != "OK":
        return "UNDETERMINED_PORTFOLIO", [f"signal accounting {acc.get('status')}: refused / dropped "
                                          "signals cannot be traced"]
    if res.get("cost_status") != "OK":
        return "UNDETERMINED_PORTFOLIO", [f"cost status {res.get('cost_status')}: fills could not be priced"]
    xv = res.get("cross_account_valuation") or {}
    if xv.get("status") == "INCOMPLETE":
        return "UNDETERMINED_PORTFOLIO", [
            f"cross account valuation incomplete: {xv.get('checks_deferred')} account checks deferred "
            f"({xv.get('would_liquidate_with_stale_marks')} would liquidate on stale marks), "
            f"{xv.get('entries_blocked')} entries blocked; stale symbols {xv.get('stale_symbols')} (ADR 0072)"]
    fe, ic = res.get("final_equity"), res.get("initial_capital")
    mdd = (met.get("max_drawdown") or {}).get("value")
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (fe, ic, mdd)):
        return "UNDETERMINED_PORTFOLIO", ["final equity / drawdown not finite"]
    for key, metric in (("max_drawdown_close_lt", "max_drawdown_close"),
                        ("max_drawdown_intrabar_lt", "max_drawdown_intrabar_worst_case"),
                        # ADR 0072: the envelope (favourable extreme taken before the
                        # adverse one inside a bar) gates with the same account limit
                        ("max_drawdown_equity_peak_to_trough_lt", "max_drawdown_equity_peak_to_trough")):
        if key in criteria:
            d = met.get(metric) or {}
            v_ = d.get("value")
            if d.get("status") != "OK" or isinstance(v_, bool) or not isinstance(v_, (int, float)) \
                    or not math.isfinite(v_):
                # NaN / inf / None / stale marks are never read as a number
                return "UNDETERMINED_PORTFOLIO", [f"{metric} {d.get('status')}: "
                                                  f"{d.get('reason') or f'value {v_!r}'}"]
            if not d["value"] < float(criteria[key]):
                why.append(f"{metric} {d['value']:.4f} >= {criteria[key]}")
    if criteria.get("final_equity_gt_initial") and not fe > ic:
        why.append(f"final equity {fe:.2f} <= initial {ic:.2f}")
    if "max_drawdown_lt" in criteria and not mdd < float(criteria["max_drawdown_lt"]):
        why.append(f"max drawdown {mdd:.4f} >= {criteria['max_drawdown_lt']}")
    if "liquidation_events_eq" in criteria and res.get("liquidation_events") != criteria["liquidation_events_eq"]:
        why.append(f"{res.get('liquidation_events')} liquidation events")
    if criteria.get("no_bankruptcy") and res.get("bankrupt"):
        why.append("account bankrupt")
    if "max_margin_skip_share" in criteria:
        sk = res.get("skipped") or {}
        n_try = sum((met.get("strategy_contribution") or {}).get(s, {}).get("entries", 0)
                    for s in res.get("members", [])) + sum(sk.values())
        share = (sk.get("margin", 0) + sk.get("gross_exposure", 0)) / n_try if n_try else 0.0
        if share > float(criteria["max_margin_skip_share"]):
            why.append(f"margin/exposure refusals {share:.3f} > {criteria['max_margin_skip_share']} "
                       "of signals (the account cannot carry the strategies)")
    undefined: list[str] = []
    for key, rate_key, label in (("max_cost_refusal_share", "cost_refusal_rate", "cost (participation)"),
                                 ("max_capital_refusal_share", "capital_refusal_rate", "capital")):
        if key not in criteria:
            continue
        r = acc.get(rate_key)
        if r is None:
            # ADR 0078 (E-06): without signal accounting the refusal share is unknown;
            # a limit (also "no refusal allowed" = null) can then not be checked
            undefined.append(f"{label} refusal share unknown (no signal accounting) — "
                             "criterion cannot be checked")
            continue
        if criteria[key] is None:
            if r > 0:
                undefined.append(f"{label} refusals {r:.4f} of execution candidates and no "
                                 "preregistered limit (criterion undefined, ADR 0071)")
        elif r > float(criteria[key]):
            why.append(f"{label} refusals {r:.4f} > {criteria[key]}")
    if why:
        return "FAIL_PORTFOLIO", why
    if undefined:
        return "UNDETERMINED_PORTFOLIO", undefined
    return "PASS_PORTFOLIO", why


def combine(statuses: Sequence[str]) -> str:
    """Overall status of several scenario / composition results: the worst."""
    rank = {"PASS_PORTFOLIO": 0, "FAIL_PORTFOLIO": 3, "UNDETERMINED_PORTFOLIO": 2,
            "INSUFFICIENT_PORTFOLIO_DATA": 1, "PORTFOLIO_SIMULATION_ERROR": 4}
    if not statuses:
        return "INSUFFICIENT_PORTFOLIO_DATA"
    return max(statuses, key=lambda s: rank[s])


def trades_from_events(sid: str, events: Sequence[Mapping], bars: Sequence[Bar]) -> list[dict]:
    """Evaluator events -> portfolio trades.  The exit price is recovered from
    the event's gross return (the evaluator's own exit rule); a time exit is
    the exit bar's open."""
    out = []
    for e in events:
        ei, xi = int(e["entry_index"]), int(e["exit_index"])
        entry = bars[ei].open
        px = entry * (1.0 + e["side"] * e["gross_bp"] / 1e4)
        xb = bars[xi]
        at_open = abs(px - xb.open) <= 1e-9 * xb.open and xi > ei
        if not at_open:
            # float round-off only; a real mismatch reaches the simulator's
            # "outside its bar" check instead of being repaired here
            tol = 1e-9 * xb.open
            if xb.low - tol <= px < xb.low:
                px = xb.low
            elif xb.high < px <= xb.high + tol:
                px = xb.high
        out.append({"strategy": sid, "symbol": e["symbol"], "side": int(e["side"]),
                    "decision_ts": bars[ei - 1].ts, "entry_ts": bars[ei].ts, "exit_ts": xb.ts,
                    "exit_price": None if at_open else px,
                    "exit_reason": "exit_at_open" if at_open else "intrabar"})
    return out
