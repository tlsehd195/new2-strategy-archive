"""Cost model bridge — the SINGLE cost implementation.

Directive §10 requires one shared cost model.  The public repo
``tlsehd195/NEW2`` already ships a tested ``CandleCostModel`` (stdlib-only
package), so instead of reimplementing cost math we import it directly and
pin its identity.  If the source is unavailable the engine fails closed.

Cost model version changes are one of the explicit re-verification
triggers (FAIL_COST → REVERIFY_PENDING).

Failure classification (residual-fix P0-3).  ``CandleCostModel`` raises a
bare ``ValueError`` for very different situations (participation above the
cap, a bar without traded value, bad inputs).  Callers used to catch that
ValueError and fall back to ``fee + half_spread`` — a cost LOWER than the
model would charge, i.e. a fallback that flatters the strategy.  Every
quote now goes through :meth:`CostContext.quote`, which classifies the
failure BEFORE calling the model and never substitutes a cheaper number:

- :class:`CostRefused`          → FAIL_COST     (order outside the model's range)
- :class:`CostDataMissing`      → DATA_MISSING  (no traded value / missing input)
- :class:`CostComputationError` → ERROR         (non-finite / negative result)
- :class:`CostConfigError`      → the context itself is invalid (missing fee,
  spread, impact or cap parameter) — raised at construction time.

All four subclass ``ValueError`` so pre-existing ``except ValueError``
callers still refuse the trade instead of crashing; none of them prices it.
"""

from __future__ import annotations

import hashlib
import importlib
import math
import os
import sys
from dataclasses import dataclass, field

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


def _source_sha256(mod) -> str:
    try:
        with open(mod.__file__, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except (OSError, TypeError, AttributeError):  # pragma: no cover
        return "UNKNOWN"


#: content hash of the imported cost implementation.  The identity of the
#: cost model is its SOURCE, not the directory it was installed in (the old
#: version string embedded the checkout path, so moving the checkout changed
#: the experiment context while a real source change did not).
COST_MODEL_SOURCE_SHA256 = _source_sha256(_costs)
COST_MODEL_SOURCE_PATH = getattr(_costs, "__file__", "")

#: pinned identity of the cost implementation for experiment_context_id
COST_MODEL_VERSION = ("cointrader.backtest.costs.CandleCostModel@sha256:"
                      + COST_MODEL_SOURCE_SHA256[:16])

#: every parameter the candle model needs.  A context MUST state all of
#: them explicitly — CandleCostModel's own defaults are Upbit-KRW spot
#: assumptions and must never leak silently into a futures evaluation.
REQUIRED_COST_PARAMS = ("fee_rate", "half_spread", "impact_coefficient",
                        "max_participation")

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

#: applicability range of the candle model, recorded with every evaluation
COST_MODEL_SCOPE = {
    "model": "per-side fee + half_spread + impact*sqrt(order/bar_traded_value)",
    "inputs": "candle quote volume only (NO order book, NO measured spread)",
    "valid_range": "0 < order_value/bar_traded_value <= max_participation",
    "outside_range": "FAIL_COST (trade refused), never priced",
    "calibration": "NOT calibrated against recorded books — UNVERIFIED",
}


class CostError(ValueError):
    status = "ERROR"


class CostRefused(CostError):
    """The order is outside the model's validity range (participation cap)."""
    status = "FAIL_COST"


class CostDataMissing(CostError):
    """A required market input (traded value) is missing or zero."""
    status = "DATA_MISSING"


class CostComputationError(CostError):
    """Bad numeric input or a non-finite / negative model output."""
    status = "ERROR"


class CostConfigError(CostError):
    """The cost configuration itself is incomplete or invalid."""
    status = "ERROR"


def _validate_params(params: dict) -> dict:
    missing = [k for k in REQUIRED_COST_PARAMS if k not in params or params[k] is None]
    if missing:
        raise CostConfigError(f"cost configuration missing {missing} — "
                              f"no implicit default is applied")
    out = {}
    for k in REQUIRED_COST_PARAMS:
        try:
            v = float(params[k])
        except (TypeError, ValueError) as e:
            raise CostConfigError(f"cost parameter {k}={params[k]!r} is not numeric") from e
        if not math.isfinite(v) or v < 0:
            raise CostConfigError(f"cost parameter {k}={v!r} must be finite and >= 0")
        out[k] = v
    if out["max_participation"] <= 0:
        raise CostConfigError("max_participation must be > 0")
    unknown = sorted(set(params) - set(REQUIRED_COST_PARAMS))
    if unknown:
        raise CostConfigError(f"unknown cost parameters {unknown}")
    return out


@dataclass(frozen=True)
class CostContext:
    """One cost configuration bound to a version string.

    ``verified`` stays False (→ UNVERIFIED_COST_MODEL) until the parameters
    are calibrated against real fee schedules / recorded books.  ``test_only``
    marks fake contexts used by tests; the production runner refuses them.
    """

    model: CandleCostModel
    version: str
    verified: bool = False  # False → UNVERIFIED_COST_MODEL in records
    test_only: bool = False
    params: dict = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self):
        # the params actually used are always the model's own fields — the
        # record can never claim a different configuration than was priced
        object.__setattr__(self, "params", _validate_params(
            {k: getattr(self.model, k) for k in REQUIRED_COST_PARAMS}))

    @classmethod
    def from_params(cls, params: dict, *, version: str, verified: bool = False,
                    test_only: bool = False) -> "CostContext":
        p = _validate_params(dict(params))
        return cls(model=CandleCostModel(**p), version=version,
                   verified=verified, test_only=test_only)

    @classmethod
    def futures_default(cls) -> "CostContext":
        return cls.from_params(FUTURES_COST_DEFAULTS,
                               version=COST_MODEL_VERSION + "#futures_default",
                               verified=False)

    def identity(self) -> dict:
        """Everything about the cost model that can change a result."""
        return {
            "version": self.version,
            "source_sha256": COST_MODEL_SOURCE_SHA256,
            "params": dict(self.params),
            "verified": bool(self.verified),
            "test_only": bool(self.test_only),
        }

    def quote(self, order_value: float, bar_traded_value) -> float:
        """Per-side cost fraction, or a classified CostError — never a guess."""
        try:
            ov = float(order_value)
        except (TypeError, ValueError) as e:
            raise CostComputationError(f"order_value {order_value!r} is not numeric") from e
        if not math.isfinite(ov) or ov < 0:
            raise CostComputationError(f"order_value {ov!r} must be finite and >= 0")
        if bar_traded_value is None:
            raise CostDataMissing("bar traded value missing")
        try:
            bv = float(bar_traded_value)
        except (TypeError, ValueError) as e:
            raise CostDataMissing(f"bar traded value {bar_traded_value!r} unusable") from e
        if not math.isfinite(bv) or bv <= 0:
            raise CostDataMissing(f"bar traded value {bv!r} — cannot price the order")
        if ov == 0:
            return 0.0
        part = ov / bv
        if part > self.model.max_participation:
            raise CostRefused(
                f"participation {part:.4%} > max_participation "
                f"{self.model.max_participation:.4%} — order refused, not priced")
        try:
            c = self.model.cost_fraction(ov, bv)
        except ValueError as e:  # anything the pre-checks did not explain
            raise CostComputationError(f"cost model rejected inputs: {e}") from e
        if not isinstance(c, (int, float)) or not math.isfinite(c):
            raise CostComputationError(f"non-finite cost {c!r}")
        if c < 0:
            raise CostComputationError(f"negative cost {c!r}")
        return float(c)

    def cost_fraction(self, order_value: float, bar_traded_value: float) -> float:
        return self.quote(order_value, bar_traded_value)

    def round_trip_bp(self, order_value: float, entry_bar_value: float,
                      exit_bar_value: float) -> float:
        """Per-side cost at entry and exit, in basis points of notional."""
        c = (self.quote(order_value, entry_bar_value)
             + self.quote(order_value, exit_bar_value))
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
    return CostContext.from_params(params, version=SCENARIO_VERSIONS[name],
                                   verified=False)
