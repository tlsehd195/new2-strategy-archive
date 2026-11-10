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

PORTFOLIO_MULTI_VERSION = "portfolio-multi-v1"
DAY_MS = 86_400_000

#: result statuses (ADR 0068).  PASS_INDIVIDUAL is the individual pipeline's
#: VALIDATION_SURVIVOR; it is never written by this module.
PORTFOLIO_STATUSES = ("PASS_PORTFOLIO", "FAIL_PORTFOLIO", "INSUFFICIENT_PORTFOLIO_DATA",
                      "UNDETERMINED_PORTFOLIO", "PORTFOLIO_SIMULATION_ERROR")


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
                   exit_rules: Optional[Mapping[str, tuple]] = None) -> dict:
    """Run the account.  ``trades`` items: {strategy, symbol, side, decision_ts,
    entry_ts, exit_ts, exit_price (None = exit bar open), exit_reason}.

    ``member_symbols``: each member's spec symbols; its allocation is split over
    them whether or not it trades each one (without it the split falls back to
    the symbols seen in ``trades`` — whole-window knowledge, tests only).
    ``exit_rules``: {member: (rule, horizon)} with ``rule`` the strategy's own
    exit rule (``strategy_spec.stop_exit_rule``); the latency stress re-derives
    the exit from the DELAYED entry with it and never drops a trade.

    Raises :class:`PortfolioInputError` for inputs that make a result wrong;
    the caller records PORTFOLIO_SIMULATION_ERROR (never a pass)."""
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
    skips = {k: 0 for k in ("capacity", "margin", "gross_exposure", "same_symbol", "cost_refused",
                            "cost_data_missing", "inactive", "halted", "zero_allocation",
                            "latency_beyond_data", "duplicate_signal")}
    skips["latency_beyond_data"] = n_latency_skip
    per = {m: {"entries": 0, "pnl": 0.0, "fees": 0.0, "slippage": 0.0, "funding": 0.0,
               "liquidations": 0, "skipped": 0, "daily": {}} for m in members}
    fund_idx = {s: sorted(funding_by_symbol.get(s) or []) for s in bars_by_symbol}
    tot = {"fees": 0.0, "slippage": 0.0, "funding": 0.0, "liq_loss": 0.0}
    cost_status = "OK"
    cost_failures: list[dict] = []
    funding_uncovered = 0
    liq_events = account_liqs = 0
    halted = bankrupt = False
    exposure_hist: list[float] = []
    margin_hist: list[float] = []
    stack_same = stack_opposite = 0
    unfilled_notional = 0.0

    def _book(p: _Pos, pnl_gross: float, exit_fee: float, exit_slip: float, ts: int, reason: str,
              liquidated: bool = False) -> None:
        nonlocal cash
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
        closed.append({"strategy": p.strategy, "symbol": p.symbol, "side": p.side,
                       "decision_ts": p.decision_ts, "entry_ts": p.entry_ts, "exit_ts": ts,
                       "reason": reason, "net": net, "notional": p.notional,
                       "fees": p.fees, "slippage": p.slippage, "funding": p.funding,
                       "liquidated": liquidated})
        open_.remove(p)

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

    for ts in grid:
        active_syms = [s for s in bars_by_symbol if ts in idx[s]]
        # 1) funding settlements in (previous bar, this bar], marked at this open
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
        # 2) exits at the open (time exits, gap fills of stops/take-profits)
        for p in sorted(open_, key=lambda q: (order[q.strategy], q.symbol)):
            if p.symbol in active_syms and ts == p.exit_ts and p.exit_price is None:
                bi = idx[p.symbol][ts]
                px = bars_by_symbol[p.symbol][bi].open
                f, s_ = _exit_cost(p, px, bi)
                _book(p, p.side * (px - p.entry_price) * p.qty, f, s_, ts, p.exit_reason)
        # 3) entries at the open, fixed priority
        if not halted:
            equity_open = cash + sum(p.side * (bars_by_symbol[p.symbol][idx[p.symbol][ts]].open - p.entry_price)
                                     * p.qty if p.symbol in active_syms else
                                     p.side * (last_px.get(p.symbol, p.entry_price) - p.entry_price) * p.qty
                                     for p in open_)
        for t in sched.get(ts, []):
            m, sym = t["strategy"], t["symbol"]
            if halted:
                skips["halted"] += 1
                per[m]["skipped"] += 1
                continue
            if m in inactive_from and t["decision_ts"] >= inactive_from[m]:
                skips["inactive"] += 1
                per[m]["skipped"] += 1
                continue
            same = [p for p in open_ if p.symbol == sym]
            if any(p.strategy == m and p.uid != t["uid"] for p in same):
                # one strategy never holds two positions on one symbol: a repeated
                # signal while the first trade is open is a duplicate, not new size
                skips["duplicate_signal"] += 1
                per[m]["skipped"] += 1
                continue
            if same and cfg.same_symbol_policy == "one_per_symbol" and not any(p.uid == t["uid"] for p in same):
                skips["same_symbol"] += 1
                per[m]["skipped"] += 1
                continue
            if len(open_) >= cfg.max_concurrent:
                skips["capacity"] += 1
                per[m]["skipped"] += 1
                continue
            bi = idx[sym][ts]
            bar = bars_by_symbol[sym][bi]
            alloc = equity_open * weights[m] / max(1, len(syms_of[m]))
            full = alloc * cfg.position_fraction * cfg.leverage
            desired = full * t["size_mult"]
            if t.get("remainder_cancelled"):
                unfilled_notional += full - desired
            if not (desired > 0):
                skips["zero_allocation" if weights[m] == 0 else "margin"] += 1
                per[m]["skipped"] += 1
                continue
            notional = desired
            margin = notional / cfg.leverage
            used = sum(p.margin for p in open_)
            if used + margin > equity_open + 1e-9:
                skips["margin"] += 1
                per[m]["skipped"] += 1
                continue
            gross = sum(p.notional for p in open_) + notional
            if gross > cfg.max_gross_exposure * equity_open + 1e-9:
                skips["gross_exposure"] += 1
                per[m]["skipped"] += 1
                continue
            try:
                frac = cost.quote(notional, bars_by_symbol[sym][bi - 1].quote_volume if bi > 0 else None)
            except CostRefused:
                skips["cost_refused"] += 1
                per[m]["skipped"] += 1
                continue
            except CostDataMissing:
                skips["cost_data_missing"] += 1
                per[m]["skipped"] += 1
                continue
            except CostError as err:
                cost_failures.append({"where": "entry", "strategy": m, "symbol": sym, "ts": ts,
                                      "status": err.status, "error": str(err)})
                cost_status = "UNPRICED_EXIT" if cost_status == "UNPRICED_EXIT" else "COST_ERROR"
                per[m]["skipped"] += 1
                continue
            fee, slip = _split_cost(cost, frac, notional)
            cash -= fee + slip
            tot["fees"] += fee
            tot["slippage"] += slip
            if any(p.side == t["side"] for p in same):
                stack_same += 1
            if any(p.side != t["side"] for p in same):
                stack_opposite += 1
            open_.append(_Pos(strategy=m, symbol=sym, side=t["side"], entry_ts=ts,
                              exit_ts=t["exit_ts"], exit_price=t.get("exit_price"),
                              exit_reason=t.get("exit_reason") or "time", entry_price=bar.open,
                              qty=notional / bar.open, notional=notional, margin=margin,
                              liq_price=liq_price(t["side"], bar.open, cfg.leverage, cfg.mmr),
                              fees=fee, slippage=slip, decision_ts=t["decision_ts"], uid=t["uid"],
                              hold_to_end=bool(t.get("hold_to_end"))))
            per[m]["entries"] += 1
            # the funding pointer starts after the entry open (settlement at the
            # entry timestamp is not held, evaluator.FundingIndex rule)
            fl = fund_idx[sym]
            p_ = open_[-1]
            while p_.fi < len(fl) and fl[p_.fi][0] <= ts:
                p_.fi += 1
        # 4) intrabar: liquidation vs stop / take-profit (larger loss wins)
        if cfg.margin_mode == "isolated":
            for p in sorted(open_, key=lambda q: (order[q.strategy], q.symbol)):
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
                            f, s_ = _exit_cost(p, p.exit_price, bi)
                            _book(p, p.side * (p.exit_price - p.entry_price) * p.qty, f, s_, ts,
                                  p.exit_reason)
                            continue
                    tot["liq_loss"] += liq_loss
                    liq_events += 1
                    _book(p, -liq_loss, 0.0, 0.0, ts, "liquidation", liquidated=True)
                elif stop_here:
                    f, s_ = _exit_cost(p, p.exit_price, bi)
                    _book(p, p.side * (p.exit_price - p.entry_price) * p.qty, f, s_, ts, p.exit_reason)
        else:
            # cross: one account; all adverse extremes at the same moment (conservative)
            worst = cash
            maint = 0.0
            for p in open_:
                if p.symbol in active_syms:
                    b = bars_by_symbol[p.symbol][idx[p.symbol][ts]]
                    adv = b.low if p.side == 1 else b.high
                else:
                    adv = last_px.get(p.symbol, p.entry_price)
                worst += p.side * (adv - p.entry_price) * p.qty
                maint += cfg.mmr * p.qty * adv
            if open_ and worst <= maint:
                account_liqs += 1
                for p in sorted(open_, key=lambda q: (order[q.strategy], q.symbol)):
                    if p.symbol in active_syms:
                        b = bars_by_symbol[p.symbol][idx[p.symbol][ts]]
                        adv = b.low if p.side == 1 else b.high
                    else:
                        adv = last_px.get(p.symbol, p.entry_price)
                    loss = -(p.side * (adv - p.entry_price) * p.qty) + cfg.mmr * p.qty * adv
                    tot["liq_loss"] += loss
                    liq_events += 1
                    _book(p, -loss, 0.0, 0.0, ts, "account_liquidation", liquidated=True)
            for p in sorted(open_, key=lambda q: (order[q.strategy], q.symbol)):
                if p.symbol in active_syms and ts == p.exit_ts and p.exit_price is not None:
                    bi = idx[p.symbol][ts]
                    f, s_ = _exit_cost(p, p.exit_price, bi)
                    _book(p, p.side * (p.exit_price - p.entry_price) * p.qty, f, s_, ts, p.exit_reason)
        # 5) mark to market at the close
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
    for p in sorted(open_, key=lambda q: (order[q.strategy], q.symbol)):
        bars = bars_by_symbol[p.symbol]
        bi = len(bars) - 1
        px = bars[bi].close
        f, s_ = _exit_cost(p, px, bi, qv_index=bi)
        _book(p, p.side * (px - p.entry_price) * p.qty, f, s_, bars[bi].ts, "end_of_data")
        forced_end += 1
    final_equity = cash
    if equity_curve:
        equity_curve[-1] = (equity_curve[-1][0], final_equity)
    for p in closed:
        fl = fund_idx[p["symbol"]]
        # a held window the funding history does not cover: the cost is unknown
        if p["exit_ts"] > p["entry_ts"] and (not fl or fl[0][0] > p["entry_ts"] or fl[-1][0] < p["exit_ts"]):
            funding_uncovered += 1

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
        "totals": tot,
        "cost_components": cost_completeness(cost, cfg),
        "assumptions": {"liquidation_price": "entry*(1-1/L+mmr) long, entry*(1+1/L-mmr) short",
                        "isolated_liquidation_loss": "posted margin",
                        "cross_liquidation": "all adverse bar extremes at one moment; loss to the "
                                             "extreme plus mmr*notional",
                        "intrabar_conflict": "liquidation vs stop/take-profit in one bar: larger loss",
                        "simultaneous_signals": "member order, then symbol",
                        "same_symbol": cfg.same_symbol_policy,
                        "end_of_data": "close at the last close with the normal exit cost",
                        "exchange_rules": "NOT_MODELLED (tiers, insurance fund, ADL)"},
    }
    res["metrics"] = portfolio_metrics(equity_curve, closed, cfg.initial_capital,
                                       exposure_hist, margin_hist, per, members)
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


def result_id(res: Mapping) -> str:
    core = {k: v for k, v in res.items() if k != "result_id"}
    raw = json.dumps(core, sort_keys=True, separators=(",", ":"), default=str)
    return "PM-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------

def judge(res: Mapping, criteria: Mapping, *, data_issues: Sequence[str] = ()) -> tuple[str, list[str]]:
    """Status of one simulation result against preregistered ``criteria``
    (config/portfolio_validation_v1.json, scenario's ``checks``).  Order:
    data issues -> INSUFFICIENT_PORTFOLIO_DATA; unpriced fills / non-finite
    metrics -> UNDETERMINED_PORTFOLIO; else every check -> PASS or FAIL."""
    if data_issues:
        return "INSUFFICIENT_PORTFOLIO_DATA", list(data_issues)
    why: list[str] = []
    met = res.get("metrics") or {}
    if res.get("funding_uncovered_trades"):
        return "INSUFFICIENT_PORTFOLIO_DATA", [f"{res['funding_uncovered_trades']} trades without "
                                                "funding coverage"]
    if int(res.get("trades_closed") or 0) < int(criteria.get("min_trades", 1)):
        return "INSUFFICIENT_PORTFOLIO_DATA", [f"{res.get('trades_closed')} closed trades < "
                                                f"{criteria.get('min_trades', 1)}"]
    if (met.get("span_days") or 0) < float(criteria.get("min_days", 0)):
        return "INSUFFICIENT_PORTFOLIO_DATA", [f"span {met.get('span_days')} d < {criteria.get('min_days')} d"]
    if res.get("cost_status") != "OK":
        return "UNDETERMINED_PORTFOLIO", [f"cost status {res.get('cost_status')}: fills could not be priced"]
    fe, ic = res.get("final_equity"), res.get("initial_capital")
    mdd = (met.get("max_drawdown") or {}).get("value")
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (fe, ic, mdd)):
        return "UNDETERMINED_PORTFOLIO", ["final equity / drawdown not finite"]
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
    return ("FAIL_PORTFOLIO" if why else "PASS_PORTFOLIO"), why


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
