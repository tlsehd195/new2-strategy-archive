"""Cost model bridge — the SINGLE cost implementation.

Directive §10 requires one shared cost model.  The public repo
``tlsehd195/NEW2`` already ships a tested ``CandleCostModel`` (stdlib-only
package), so instead of reimplementing cost math we import it directly and
pin its identity.  If the source is unavailable the engine fails closed.

Cost model version changes are one of the explicit re-verification
triggers (FAIL_COST → REVERIFY_PENDING).
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass

_COINTRADER_SRC = os.environ.get(
    "COINTRADER_SRC", "/home/z/my-project/NEW2_public_clone/src"
)

try:  # pragma: no cover - import guard
    if _COINTRADER_SRC not in sys.path:
        sys.path.insert(0, _COINTRADER_SRC)
    _costs = importlib.import_module("cointrader.backtest.costs")
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "cointrader cost model not available — set COINTRADER_SRC to the "
        "public NEW2 checkout (fail-closed, no local cost re-implementation "
        "is permitted)"
    ) from e

CandleCostModel = _costs.CandleCostModel
CostModel = CandleCostModel

#: pinned identity of the cost implementation for experiment_context_id
COST_MODEL_VERSION = f"cointrader.backtest.costs.CandleCostModel@{_COINTRADER_SRC}"

#: Binance USDT-M futures defaults.  NOTE: the exchange fee tier for this
#: account is NOT verifiable in this environment, so the fee below is an
#: explicit ASSUMPTION (taker 5bp) — every evaluation record marks the cost
#: model UNVERIFIED_COST_MODEL until real fee schedules are configured.
FUTURES_COST_DEFAULTS: dict = {
    "fee_rate": 0.0005,
    "half_spread": 0.0002,
    "impact_coefficient": 0.1,
    "max_participation": 0.05,
}

UNVERIFIED_COST_MODEL = "UNVERIFIED_COST_MODEL"


@dataclass(frozen=True)
class CostContext:
    """One cost configuration bound to a version string."""

    model: CandleCostModel
    version: str
    verified: bool = False  # False → UNVERIFIED_COST_MODEL in records

    @classmethod
    def futures_default(cls) -> "CostContext":
        return cls(model=CandleCostModel(**FUTURES_COST_DEFAULTS),
                   version=COST_MODEL_VERSION + "#futures_default",
                   verified=False)

    def cost_fraction(self, order_value: float, bar_traded_value: float) -> float:
        return self.model.cost_fraction(order_value, bar_traded_value)

    def round_trip_bp(self, order_value: float, entry_bar_value: float,
                      exit_bar_value: float) -> float:
        """Per-side cost at entry and exit, in basis points of notional."""
        c = (self.cost_fraction(order_value, entry_bar_value)
             + self.cost_fraction(order_value, exit_bar_value))
        return c * 1e4


#: named sensitivity scenarios for the cost sensitivity analysis (§10)
SENSITIVITY_SCENARIOS: dict[str, dict] = {
    "base": {},
    "cost_up_2x": {"fee_rate": 0.0010},
    "wider_spread_2x": {"half_spread": 0.0004},
    "impact_up_2x": {"impact_coefficient": 0.2},
    "low_liquidity_part_cap": {"max_participation": 0.01},
}

SCENARIO_VERSIONS = {name: COST_MODEL_VERSION + "#" + name for name in SENSITIVITY_SCENARIOS}


def scenario_model(name: str) -> CostContext:
    params = dict(FUTURES_COST_DEFAULTS)
    params.update(SENSITIVITY_SCENARIOS[name])
    return CostContext(model=CandleCostModel(**params),
                       version=SCENARIO_VERSIONS[name], verified=False)
