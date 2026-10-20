"""Candle cost model — the archive's own, self-contained implementation.

Per-side cost of an order, as a fraction of its traded notional::

    fee_rate + half_spread + impact_coefficient * sqrt(order_value / bar_traded_value)

An order whose participation ``order_value / bar_traded_value`` exceeds
``max_participation`` is refused (``ValueError``), never priced.

Provenance.  Until 2026-10-20 the engine imported this model from the main
trading repository (``tlsehd195/NEW2``, ``src/cointrader/backtest/costs.py``
at commit ``8ec637153b4375143cbb9b34c00eb32053feca5e``, file sha256
``27ec57bb…``), which made the archive's tests and CI depend on checking
out that repository.  The formula above is re-implemented here from its
documented definition (same owner; the source repository carries no licence
file, and nothing but this one formula and its refusal rule is reproduced).
The order-book walker of that file (``simulate_market_fill``) is not used by
the archive and is not carried over.  ``tests/test_cost_model.py`` pins the
numbers so a change here cannot go unnoticed.

Assumptions (unchanged, stated, not verified):

- square-root impact is an empirical approximation, not a claim about this
  market; ``half_spread`` is a constant per side; fees are a fixed rate of
  notional (no tiers, no rebates);
- the bar's quote volume stands in for the liquidity available to the order;
- no parameter has a default here: every caller states all four values
  (``costs_bridge.REQUIRED_COST_PARAMS``), so an exchange- or market-specific
  default can never leak into an evaluation unnoticed.

The cost-model identity used in experiment contexts is the sha256 of THIS
file (``costs_bridge.COST_MODEL_SOURCE_SHA256``): editing it changes every
context id and sends earlier evidence to re-verification.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: the identity of the formula (not of the file): recorded in the provenance note
FORMULA = "fee_rate + half_spread + impact_coefficient * sqrt(order_value / bar_traded_value)"


@dataclass(frozen=True)
class CandleCostModel:
    """Per-side cost fraction for candle-only history (no recorded books)."""

    fee_rate: float
    half_spread: float
    impact_coefficient: float
    max_participation: float

    def cost_fraction(self, order_value: float, bar_traded_value: float) -> float:
        if order_value < 0:
            raise ValueError("order_value must be >= 0")
        if order_value == 0:
            return 0.0
        if bar_traded_value <= 0:
            raise ValueError("cannot price an order against a bar with no traded value")
        participation = order_value / bar_traded_value
        if participation > self.max_participation:
            raise ValueError(
                f"order is {participation:.2%} of the bar's traded value, above max_participation "
                f"{self.max_participation:.2%}"
            )
        return self.fee_rate + self.half_spread + self.impact_coefficient * math.sqrt(participation)
