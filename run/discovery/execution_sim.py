"""Execution realism for SIMULATED trades: partial fills, order refusal,
observation latency, margin and forced liquidation.

Scope.  These are model rules applied to simulated trades in the forward
paper engine (``paper_forward.py``).  They are IMPLEMENTED and unit-tested;
none of them is empirically validated — that needs the user's own execution
reports (see ``cost_calibration.py``).  Nothing here sends an order.

Rules (all conservative — a rule never makes a trade better):

* participation: the cost model refuses an order above
  ``max_participation`` of the bar's traded value.  Policy ``refuse``
  (default, same as research) drops the order and counts it REJECTED;
  policy ``cap`` fills only ``max_participation * bar_value`` and scales the
  P&L by the fill fraction (PARTIAL).
* latency: a decision bar that was OBSERVED later than
  ``max_observation_delay_ms`` after its close could not have been traded at
  the next open in real time — the trade is marked LATE and excluded from
  the paper verdict (it is still logged).
* liquidation (isolated margin, linear USDT-M contract): with leverage L and
  maintenance margin rate mm the position is liquidated when the adverse
  move from entry reaches ``1/L - mm``.  The bar path is scanned with the
  WORST intrabar price (low for longs, high for shorts); the loss is the
  whole initial margin (``1e4 / L`` bp of notional) plus the entry cost and
  a liquidation fee; it replaces the strategy's own exit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

EXECUTION_SIM_VERSION = "exec-sim-v1"


@dataclass(frozen=True)
class ExecutionConfig:
    leverage: float = 1.0
    maintenance_margin: float = 0.004       # Binance USDT-M tier-1 order of magnitude; ASSUMPTION
    liquidation_fee: float = 0.0125         # charged on notional at liquidation; ASSUMPTION
    partial_fill_policy: str = "refuse"     # "refuse" | "cap"
    max_observation_delay_ms: int = 120_000

    def validate(self) -> None:
        if not (self.leverage >= 1.0 and math.isfinite(self.leverage)):
            raise ValueError("leverage must be >= 1")
        if not (0.0 <= self.maintenance_margin < 1.0 / self.leverage):
            raise ValueError("maintenance margin must be in [0, 1/leverage)")
        if self.partial_fill_policy not in ("refuse", "cap"):
            raise ValueError("partial_fill_policy must be 'refuse' or 'cap'")
        if self.max_observation_delay_ms < 0:
            raise ValueError("max_observation_delay_ms must be >= 0")

    def to_json(self) -> dict:
        return dict(self.__dict__)


def fill_fraction(order_value: float, bar_traded_value: Optional[float], max_participation: float,
                  policy: str) -> tuple[float, str]:
    """(fraction filled, status) for an order against one bar."""
    if bar_traded_value is None or not math.isfinite(bar_traded_value) or bar_traded_value <= 0:
        return 0.0, "REJECTED_NO_LIQUIDITY"
    cap = max_participation * bar_traded_value
    if order_value <= cap:
        return 1.0, "FILLED"
    if policy == "cap":
        return cap / order_value, "PARTIAL"
    return 0.0, "REJECTED_PARTICIPATION"


def liquidation_price(entry_px: float, side: int, cfg: ExecutionConfig) -> float:
    move = 1.0 / cfg.leverage - cfg.maintenance_margin
    return entry_px * (1.0 - side * move)


def scan_liquidation(bars: Sequence, entry_i: int, exit_i: int, side: int,
                     cfg: ExecutionConfig) -> Optional[int]:
    """Index of the first bar in [entry_i, exit_i] whose worst price crosses
    the liquidation price, or None.  (An exit at the open of exit_i happens
    before that bar trades, so exit_i itself is only checked at its open.)"""
    lp = liquidation_price(bars[entry_i].open, side, cfg)
    for j in range(entry_i, exit_i + 1):
        b = bars[j]
        if j == exit_i:
            worst = b.open
        else:
            worst = b.low if side == 1 else b.high
        if (side == 1 and worst <= lp) or (side == -1 and worst >= lp):
            return j
    return None


def apply_execution(event: Mapping, bars: Sequence, cfg: ExecutionConfig, *,
                    order_value: float, max_participation: float,
                    observed_delay_ms: Optional[int]) -> dict:
    """Execution-adjusted copy of a research event dict (keys of
    ``evaluator.EventRecord``).  Adds ``exec_status``, ``fill_fraction``,
    ``net_bp_exec`` (bp of the REQUESTED notional)."""
    cfg.validate()
    out = dict(event)
    entry_i, exit_i, side = event["entry_index"], event["exit_index"], event["side"]
    frac, st = fill_fraction(order_value, bars[entry_i].quote_volume, max_participation,
                             cfg.partial_fill_policy)
    out.update({"fill_fraction": frac, "exec_status": st, "liquidated": False,
                "late": False, "exec_version": EXECUTION_SIM_VERSION})
    if frac == 0.0:
        out["net_bp_exec"] = 0.0
        return out
    if observed_delay_ms is not None and observed_delay_ms > cfg.max_observation_delay_ms:
        out["late"] = True
    net = event["net_bp"]
    j = scan_liquidation(bars, entry_i, exit_i, side, cfg)
    if j is not None:
        entry_cost = event["cost_bp"] / 2.0
        net = -(1e4 / cfg.leverage) - entry_cost - cfg.liquidation_fee * 1e4
        out.update({"liquidated": True, "liquidation_index": j, "exec_status": "LIQUIDATED",
                    "liquidation_ts": bars[j].ts})
    out["net_bp_exec"] = net * frac
    return out
