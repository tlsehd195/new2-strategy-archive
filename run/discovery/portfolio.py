"""Multi-position portfolio simulator (directive §11-2).

The event study answers "does the signal have an edge per event?"; it does
NOT answer "what does trading these signals as a portfolio earn?".  This
simulator models:
- initial capital, risk-based sizing, leverage + isolated-margin liquidation
- max concurrent positions, ONE position per symbol (duplicate/conflicting
  signals on the same symbol are skipped and counted — never averaged)
- entry at next open, time exit at open, optional partial reductions
- per-side CandleCostModel costs (entry fee at entry, exit fee at exit)
- funding settled into cash at the actual funding timestamps
- mark-to-market equity curve, max drawdown, liquidation events

Accounting identities (residual-fix P0-2/P0-4, all quantities in quote ccy):

    cash      = initial + realized PnL - fees paid - funding paid
                (isolated margin is part of cash; it is RESERVED, not spent)
    unrealized= sum_pos side * (mark - entry_price) * qty
    equity    = cash + unrealized              <- equity_curve, MDD, sizing
    used_margin = sum_pos qty_remaining * entry_price / leverage
    available = equity - used_margin           <- new entries must fit here

Bar sequence for bar i (all fills at OPEN, the engine's single convention):
  1. funding with timestamp in (ts[i-1], ts[i]] settles into cash
     (payment = side * rate * qty * open[i]; long pays when rate > 0)
  2. time exits / partial reductions at open[i]; exit fee charged once
  3. entries at open[i]; entry fee charged once, into cash, immediately
  4. intrabar liquidation check on [low, high] for positions held in bar i
  5. mark-to-market at close[i] -> equity_curve[i]
  On the LAST bar every open position is force-closed at open[n-1] with the
  normal exit cost (same fill rule as every other exit — no favourable
  "best price" assumption), so final_equity == equity_curve[-1] by
  construction.

Liquidation (ENGINE ASSUMPTION, not a claim about any exchange's rules):
isolated margin; liquidation price = entry*(1 - 1/L + mmr) for longs and
entry*(1 + 1/L - mmr) for shorts; when the bar's extreme touches it the
position's posted margin is lost in full (the maintenance margin acts as the
liquidation penalty) and NO separate exit fee is charged (it would double
count the penalty).  Entry fees and funding already paid stay paid — they
are never charged again.

Cost failures are never replaced with a cheaper number:
- entry: the trade is refused (FAIL_COST → participation_refusals;
  DATA_MISSING → cost_data_missing)
- exit: the position must still be closed, so the exit is executed WITHOUT
  a cost and the result is flagged ``cost_status != "OK"`` — such a result
  can never pass (portfolio_passes() requires cost_status == "OK").
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Mapping, Optional, Sequence

from costs_bridge import CostComputationError, CostContext, CostDataMissing, CostError, CostRefused
from data_quality import Bar

PORTFOLIO_MODEL_VERSION = "portfolio-mtm-v2"
END_POLICY = "force_close_at_last_open"


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
    qty: float            # remaining contracts (base units)
    notional0: float      # entry notional of the ORIGINAL size
    liq_price: float
    entry_fee: float = 0.0
    funding_paid: float = 0.0
    fi: int = 0   # funding pointer into the (time-sorted) funding series

    def margin(self, leverage: float) -> float:
        return self.qty * self.entry_price / leverage

    def unrealized(self, price: float) -> float:
        return self.side * (price - self.entry_price) * self.qty


@dataclass
class PortfolioResult:
    initial_capital: float = 0.0
    equity_curve: list[float] = field(default_factory=list)   # MTM at each close
    cash_curve: list[float] = field(default_factory=list)     # realized balance
    final_equity: float = 0.0
    final_cash: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0
    trades: int = 0                 # positions fully closed (time / end exit)
    partial_closes: int = 0
    forced_end_closes: int = 0
    liquidation_events: int = 0
    skipped_same_symbol_conflict: int = 0
    skipped_capacity: int = 0
    skipped_margin: int = 0
    participation_refusals: int = 0
    cost_data_missing: int = 0
    cost_status: str = "OK"         # OK | FAIL_COST | DATA_MISSING | ERROR
    cost_failures: list[dict] = field(default_factory=list)
    realized_pnl: float = 0.0
    total_fees: float = 0.0
    total_funding: float = 0.0
    liquidation_losses: float = 0.0
    total_cost_bp: float = 0.0
    total_funding_bp: float = 0.0
    bankrupt: bool = False
    notes: list[str] = field(default_factory=list)
    # evidence fields used by the promotion gate (P1-9)
    model_version: str = PORTFOLIO_MODEL_VERSION
    end_policy: str = END_POLICY
    margin_model: str = "isolated"
    costs_applied: bool = True
    funding_applied: bool = False
    config: dict = field(default_factory=dict)
    context_id: Optional[str] = None
    result_id: str = ""

    def to_json(self) -> dict:
        d = dict(self.__dict__)
        d["equity_curve"] = list(self.equity_curve)
        d["cash_curve"] = list(self.cash_curve)
        return d


def _liq_price(side: int, entry: float, leverage: float, mmr: float) -> float:
    if side == 1:
        return entry * (1.0 - 1.0 / leverage + mmr)
    return entry * (1.0 + 1.0 / leverage - mmr)


_COST_RANK = {"OK": 0, "FAIL_COST": 1, "DATA_MISSING": 2, "ERROR": 3}


def simulate_portfolio(bars_by_symbol: Mapping[str, Sequence[Bar]],
                       signals: Sequence[dict],
                       funding_by_symbol: Mapping[str, Sequence[tuple[int, float]]],
                       cost: CostContext, cfg: PortfolioConfig,
                       *, context_id: Optional[str] = None) -> PortfolioResult:
    """Run the portfolio.

    ``signals`` items: {"symbol", "decision_index", "side", "horizon"} —
    decided on the close of decision_index, executed at the NEXT bar's open
    (no lookahead).  A signal with ``"reduce": f`` (0 < f < 1) instead closes
    the fraction f of the symbol's open position at the next open.
    All symbols must share one timestamp grid (misaligned grids are a hard
    error, not a guess).
    """
    grids = {s: [b.ts for b in bars] for s, bars in bars_by_symbol.items()}
    ref = None
    for s, ts in grids.items():
        if ref is None:
            ref = ts
        elif ts != ref:
            raise ValueError("symbols do not share a common timestamp grid")
    n = len(next(iter(grids.values())))
    if cfg.leverage <= 0 or not math.isfinite(cfg.leverage):
        raise ValueError("leverage must be finite and > 0")
    res = PortfolioResult()
    res.initial_capital = cfg.initial_capital
    res.config = asdict(cfg)
    res.context_id = context_id
    res.funding_applied = any(len(funding_by_symbol.get(s, ()) or ()) for s in bars_by_symbol)
    cash = float(cfg.initial_capital)
    positions: dict[str, OpenPosition] = {}

    sig_by_entry: dict[int, list[dict]] = {}
    for s in signals:
        entry_i = int(s["decision_index"]) + 1
        if 0 <= entry_i < n:
            sig_by_entry.setdefault(entry_i, []).append(s)

    def flag_cost(kind: str, sym: str, i: int, err: Exception) -> None:
        res.cost_failures.append({"where": kind, "symbol": sym, "index": i,
                                  "status": getattr(err, "status", "ERROR"),
                                  "error": str(err)})
        st = getattr(err, "status", "ERROR")
        if _COST_RANK.get(st, 3) > _COST_RANK[res.cost_status]:
            res.cost_status = st

    def close_qty(sym: str, pos: OpenPosition, i: int, qty: float, *, end: bool = False) -> None:
        """Close ``qty`` contracts at open[i] with the normal exit cost."""
        nonlocal cash
        bar = bars_by_symbol[sym][i]
        price = bar.open
        realized = pos.side * (price - pos.entry_price) * qty
        exit_notional = qty * price
        try:
            cf = cost.quote(exit_notional, bar.quote_volume)
            fee = cf * exit_notional
            res.total_cost_bp += cf * 1e4
        except CostError as e:
            # the exit happens regardless; no substitute cost is invented,
            # the RESULT is flagged so it can never pass
            fee = 0.0
            flag_cost("exit", sym, i, e)
        cash += realized - fee
        res.realized_pnl += realized
        res.total_fees += fee
        pos.qty -= qty
        if pos.qty <= 1e-12 * max(1.0, pos.notional0 / pos.entry_price):
            res.trades += 1
            if end:
                res.forced_end_closes += 1
            res.total_funding_bp += pos.funding_paid / max(pos.notional0, 1e-9) * 1e4
            del positions[sym]
        else:
            res.partial_closes += 1

    halted = False
    for i in range(n):
        last = i == n - 1
        # 1) funding settlements in (ts[i-1], ts[i]] — settled into cash once
        for sym, pos in list(positions.items()):
            fund = funding_by_symbol.get(sym, ())
            ts_i = grids[sym][i]
            ts_prev = grids[sym][i - 1] if i > 0 else -1
            while pos.fi < len(fund) and fund[pos.fi][0] <= ts_prev:
                pos.fi += 1
            j = pos.fi
            mark = bars_by_symbol[sym][i].open
            while j < len(fund) and fund[j][0] <= ts_i:
                pay = pos.side * fund[j][1] * pos.qty * mark
                cash -= pay
                pos.funding_paid += pay
                res.total_funding += pay
                j += 1
            pos.fi = j
        # 2) time exits, reductions and the end-of-data forced close (at open)
        for sym, pos in list(positions.items()):
            if i >= pos.exit_index:
                close_qty(sym, pos, i, pos.qty)
            elif last:
                close_qty(sym, pos, i, pos.qty, end=True)
        if not last and not halted:
            for s in sig_by_entry.get(i, []):
                if "reduce" in s:
                    sym = s["symbol"]
                    f = float(s["reduce"])
                    if sym in positions and 0.0 < f < 1.0:
                        close_qty(sym, positions[sym], i, positions[sym].qty * f)
            # 3) entries at open[i]
            unreal_open = sum(p.unrealized(bars_by_symbol[s][i].open) for s, p in positions.items())
            equity_open = cash + unreal_open
            for s in sig_by_entry.get(i, []):
                if "reduce" in s:
                    continue
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
                notional = equity_open * frac
                if not (notional > 0) or not math.isfinite(notional):
                    res.skipped_margin += 1
                    continue
                used = sum(p.margin(cfg.leverage) for p in positions.values())
                if used + notional / cfg.leverage > equity_open + 1e-9:
                    res.skipped_margin += 1
                    continue
                try:
                    cf = cost.quote(notional, bar.quote_volume)
                except CostRefused:
                    res.participation_refusals += 1
                    continue
                except CostDataMissing:
                    res.cost_data_missing += 1
                    continue
                except CostComputationError as e:
                    flag_cost("entry", sym, i, e)
                    continue
                fee = cf * notional
                cash -= fee
                res.total_fees += fee
                res.total_cost_bp += cf * 1e4
                positions[sym] = OpenPosition(
                    symbol=sym, side=side, entry_index=i,
                    exit_index=i + int(s.get("horizon", 4)),
                    entry_price=bar.open, qty=notional / bar.open,
                    notional0=notional,
                    liq_price=_liq_price(side, bar.open, cfg.leverage, cfg.mmr),
                    entry_fee=fee,
                )
        # 4) intrabar liquidation for positions held during bar i
        for sym, pos in list(positions.items()):
            bar = bars_by_symbol[sym][i]
            hit = (pos.side == 1 and bar.low <= pos.liq_price) or \
                  (pos.side == -1 and bar.high >= pos.liq_price)
            if hit:
                loss = pos.margin(cfg.leverage)
                cash -= loss
                res.realized_pnl -= loss
                res.liquidation_losses += loss
                res.liquidation_events += 1
                res.total_funding_bp += pos.funding_paid / max(pos.notional0, 1e-9) * 1e4
                del positions[sym]
        # 5) mark-to-market at close[i]
        equity = cash + sum(p.unrealized(bars_by_symbol[s][i].close) for s, p in positions.items())
        res.equity_curve.append(equity)
        res.cash_curve.append(cash)
        if not halted and not (equity > 0):
            # an account at or below zero is not a state to keep trading from
            halted = True
            res.bankrupt = True
            res.notes.append(f"account equity <= 0 at bar {i} — no further entries")

    res.final_cash = cash
    res.final_equity = res.equity_curve[-1] if res.equity_curve else cash
    res.total_return = res.final_equity / cfg.initial_capital - 1.0
    res.max_drawdown = max_drawdown(res.equity_curve, cfg.initial_capital)
    if res.liquidation_events:
        res.notes.append("liquidation events occurred — results include forced losses")
    if res.cost_status != "OK":
        res.notes.append(f"cost status {res.cost_status}: some exits could not be priced")
    res.result_id = portfolio_result_id(res)
    return res


def max_drawdown(curve: Sequence[float], initial: float) -> float:
    """Peak-to-trough drawdown of the SAME (MTM) equity series, the initial
    capital being the first peak.  Equity at/below zero is a 100% drawdown;
    a non-finite point makes the drawdown NaN (never silently ignored)."""
    peak = float(initial)
    mdd = 0.0
    for v in curve:
        if not math.isfinite(v):
            return float("nan")
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, min(1.0, (peak - v) / peak))
    return mdd


def portfolio_result_id(res: PortfolioResult) -> str:
    core = {k: v for k, v in res.to_json().items() if k != "result_id"}
    raw = json.dumps(core, sort_keys=True, separators=(",", ":"), default=str)
    return "P-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


#: pre-registered portfolio pass criteria (unchanged thresholds)
PORTFOLIO_PASS_CRITERIA = {
    "final_equity_gt_initial": True,
    "max_drawdown_lt": 0.5,
    "liquidation_events_eq": 0,
    "cost_status_eq": "OK",
    "bankrupt": False,
    "min_trades": 1,
}


def portfolio_passes(res: Mapping) -> tuple[bool, list[str]]:
    """Evaluate the pass criteria from the RESULT itself (never a bool
    handed in by a caller).  Non-finite metrics fail."""
    fails: list[str] = []
    fe, ic, mdd = res.get("final_equity"), res.get("initial_capital"), res.get("max_drawdown")
    for k, v in (("final_equity", fe), ("initial_capital", ic), ("max_drawdown", mdd)):
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            fails.append(f"{k} not finite ({v!r})")
    if not fails:
        if not fe > ic:
            fails.append("final equity not above initial capital")
        if not mdd < PORTFOLIO_PASS_CRITERIA["max_drawdown_lt"]:
            fails.append(f"max drawdown {mdd:.3f} >= 0.5")
    if res.get("liquidation_events", 1) != 0:
        fails.append("liquidation events occurred")
    if res.get("cost_status") != "OK":
        fails.append(f"cost status {res.get('cost_status')!r}")
    if res.get("bankrupt", True):
        fails.append("account bankrupt")
    if int(res.get("trades", 0) or 0) < PORTFOLIO_PASS_CRITERIA["min_trades"]:
        fails.append("no completed trades")
    return (not fails), fails
