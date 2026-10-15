"""Multi-position portfolio simulator (directive §11-2) — NEW module.

The event study answers "does the signal have an edge per event?"; it does
NOT answer "what does trading these signals as a portfolio earn?".  This
simulator models:
- initial capital, risk-based sizing, leverage + isolated-margin liquidation
- max concurrent positions, ONE position per symbol (duplicate/conflicting
  signals on the same symbol are skipped and counted — never averaged)
- entry at next open, time exit, per-side CandleCostModel costs
- funding charged on the notional at actual funding timestamps (fail-closed
  on missing funding coverage)
- equity curve, max drawdown, liquidation events, capacity refusals
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from costs_bridge import CostContext
from data_quality import Bar


@dataclass
class PortfolioConfig:
    initial_capital: float = 10_000.0
    risk_per_trade: float = 0.01          # equity fraction risked per trade
    max_concurrent: int = 2
    leverage: float = 1.0
    mmr: float = 0.005                    # maintenance margin rate
    vol_window: int = 32
    size_fraction: Optional[float] = None # override sizing (tests); notional = equity * fraction


@dataclass
class OpenPosition:
    symbol: str
    side: int
    entry_index: int
    exit_index: int
    entry_price: float
    notional0: float
    liq_price: float
    funding_paid: float = 0.0


@dataclass
class PortfolioResult:
    equity_curve: list[float] = field(default_factory=list)
    final_equity: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0
    trades: int = 0
    liquidation_events: int = 0
    skipped_same_symbol_conflict: int = 0
    skipped_capacity: int = 0
    participation_refusals: int = 0
    total_cost_bp: float = 0.0
    total_funding_bp: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        d = dict(self.__dict__)
        d["equity_curve"] = self.equity_curve
        return d


def _liq_price(side: int, entry: float, leverage: float, mmr: float) -> float:
    if side == 1:
        return entry * (1.0 - 1.0 / leverage + mmr)
    return entry * (1.0 + 1.0 / leverage - mmr)


def simulate_portfolio(bars_by_symbol: Mapping[str, Sequence[Bar]],
                       signals: Sequence[dict],
                       funding_by_symbol: Mapping[str, Sequence[tuple[int, float]]],
                       cost: CostContext, cfg: PortfolioConfig) -> PortfolioResult:
    """Run the portfolio.

    ``signals`` items: {"symbol", "decision_index", "side"} — decided on the
    close of decision_index, executed at the NEXT bar's open (no lookahead).
    All symbols must share one timestamp grid (the engine evaluates on one
    grid per timeframe; misaligned grids are a hard error, not a guess).
    """
    grids = {s: [b.ts for b in bars] for s, bars in bars_by_symbol.items()}
    ref = None
    for s, ts in grids.items():
        if ref is None:
            ref = ts
        elif ts != ref:
            raise ValueError("symbols do not share a common timestamp grid")
    n = len(next(iter(grids.values())))
    res = PortfolioResult()
    equity = cfg.initial_capital
    positions: dict[str, OpenPosition] = {}

    sig_by_entry: dict[int, list[dict]] = {}
    for s in signals:
        entry_i = int(s["decision_index"]) + 1
        if 0 <= entry_i < n:
            sig_by_entry.setdefault(entry_i, []).append(s)

    def close_position(sym: str, pos: OpenPosition, i: int, price: float,
                       forced: bool = False) -> None:
        nonlocal equity
        bar = bars_by_symbol[sym][i]
        gross = pos.side * (price / pos.entry_price - 1.0) * pos.notional0
        try:
            cf = cost.cost_fraction(pos.notional0, bar.quote_volume) \
                 + cost.cost_fraction(pos.notional0,
                                      bars_by_symbol[sym][pos.entry_index].quote_volume)
        except ValueError:
            cf = cost.model.fee_rate + cost.model.half_spread
        fees = cf * pos.notional0
        equity += gross - fees - pos.funding_paid  # funding charged on close
        res.trades += 1
        res.total_cost_bp += cf * 1e4
        res.total_funding_bp += pos.funding_paid / max(pos.notional0, 1e-9) * 1e4
        if forced:
            res.liquidation_events += 1
        del positions[sym]

    for i in range(n):
        # 1) funding on open positions for events in (prev_ts, ts]
        for sym, pos in list(positions.items()):
            fund = funding_by_symbol.get(sym, ())
            ts_i = grids[sym][i]
            ts_prev = grids[sym][i - 1] if i > 0 else -1
            for t, r in fund:
                if ts_prev < t <= ts_i:
                    pos.funding_paid += pos.side * r * pos.notional0
        # 2) mark-to-market equity (for sizing/dd)
        mtm = equity
        for sym, pos in positions.items():
            price = bars_by_symbol[sym][i].open
            mtm += pos.side * (price / pos.entry_price - 1.0) * pos.notional0
        # 3) liquidation (intrabar extremes, isolated margin)
        for sym, pos in list(positions.items()):
            bar = bars_by_symbol[sym][i]
            hit = (pos.side == 1 and bar.low <= pos.liq_price) or \
                  (pos.side == -1 and bar.high >= pos.liq_price)
            if hit:
                # full posted margin is lost (isolated margin, ADR-0004:
                # no same-bar re-entry — the slot simply stays free)
                equity -= pos.notional0 / cfg.leverage
                del positions[sym]
                res.liquidation_events += 1
        # 4) exits
        for sym, pos in list(positions.items()):
            if i >= pos.exit_index:
                close_position(sym, pos, i, bars_by_symbol[sym][i].open)
        # 5) entries
        for s in sig_by_entry.get(i, []):
            sym = s["symbol"]
            side = int(s["side"])
            if sym in positions:
                res.skipped_same_symbol_conflict += 1
                continue
            if len(positions) >= cfg.max_concurrent:
                res.skipped_capacity += 1
                continue
            bar = bars_by_symbol[sym][i]
            if cfg.size_fraction is not None:
                frac = cfg.size_fraction
            else:
                # vol-inverse sizing: risk fixed, size shrinks with vol
                if i < cfg.vol_window:
                    res.skipped_capacity += 1
                    continue
                rets = [bars_by_symbol[sym][j].close / bars_by_symbol[sym][j - 1].close - 1.0
                        for j in range(i - cfg.vol_window + 1, i + 1)]
                mu = sum(rets) / len(rets)
                var = sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)
                vol = math.sqrt(var)
                if vol <= 0:
                    res.skipped_capacity += 1
                    continue
                frac = min(cfg.risk_per_trade / (vol * math.sqrt(4.0)), cfg.leverage)
            notional = mtm * frac
            try:
                cf = cost.cost_fraction(notional, bar.quote_volume)
            except ValueError:
                res.participation_refusals += 1
                continue
            pos = OpenPosition(
                symbol=sym, side=side, entry_index=i,
                exit_index=i + int(s.get("horizon", 4)),
                entry_price=bar.open, notional0=notional,
                liq_price=_liq_price(side, bar.open, cfg.leverage, cfg.mmr),
            )
            positions[sym] = pos
        res.equity_curve.append(equity)

    # force-close anything left at the end
    for sym, pos in list(positions.items()):
        close_position(sym, pos, n - 1, bars_by_symbol[sym][n - 1].open)
    res.final_equity = equity
    res.total_return = equity / cfg.initial_capital - 1.0
    peak = -1e300
    for v in res.equity_curve:
        peak = max(peak, v)
        res.max_drawdown = max(res.max_drawdown, (peak - v) / peak if peak > 0 else 0.0)
    if res.liquidation_events:
        res.notes.append("liquidation events occurred — results include forced losses")
    return res
