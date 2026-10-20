"""Cost model bridge — the SINGLE cost implementation.

Directive §10 requires one shared cost model.  It lives in this repository
(``cost_model.CandleCostModel``); the archive no longer imports, checks out
or assumes a path of the main trading repository (2026-10-20 directive §3;
the provenance of the formula is recorded in ``cost_model.py``).  Its identity is
the sha256 of that file.

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
import math
from dataclasses import dataclass, field
from typing import Optional

import cost_model as _costs
from cost_model import CandleCostModel

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
COST_MODEL_VERSION = ("archive.cost_model.CandleCostModel@sha256:"
                      + COST_MODEL_SOURCE_SHA256[:16])

#: every parameter the candle model needs.  A context MUST state all of
#: them explicitly (the archive's CandleCostModel has no defaults at all: the
#: former import carried Upbit-KRW spot defaults that must never leak into a
#: futures evaluation).
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
#: a documented assumption profile (public fee schedule + conservative
#: slippage margin) — still unverified, never promotable (ADR 0007)
ASSUMED_COST_MODEL = "ASSUMED_COST_MODEL"
#: spread/impact estimated from public order books (fee still assumed) —
#: evidence, not a verification; never promotable (ADR 0007)
ESTIMATED_FROM_PUBLIC_DATA = "ESTIMATED_FROM_PUBLIC_DATA"
ASSUMED_PROFILE_SCHEMA = "cost_assumed_profile_v1"
ASSUMED_PROFILES = {"assumed_public_v1": "cost_assumed_public_v1.json"}

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

    A caller can NOT make a context verified by passing ``verified=True``
    (or a config boolean): outside ``test_only`` a verified context must
    carry a sealed ``cost_calibration`` VERIFIED record whose parameters are
    exactly the ones priced here and whose cost-model source hash is the
    imported one (:meth:`from_verification`).  Anything else raises
    :class:`CostConfigError` at construction time.
    """

    model: CandleCostModel
    version: str
    verified: bool = False  # False → UNVERIFIED_COST_MODEL in records
    test_only: bool = False
    params: dict = field(default_factory=dict, compare=False, hash=False)
    verification: Optional[dict] = field(default=None, compare=False, hash=False)
    assumption: Optional[dict] = field(default=None, compare=False, hash=False)

    def __post_init__(self):
        # the params actually used are always the model's own fields — the
        # record can never claim a different configuration than was priced
        object.__setattr__(self, "params", _validate_params(
            {k: getattr(self.model, k) for k in REQUIRED_COST_PARAMS}))
        if self.assumption is not None and self.verified:
            raise CostConfigError("an assumed cost profile is not a verification — "
                                  "it can never be verified=True")
        if self.verified and not self.test_only:
            from cost_calibration import CalibrationError, check_record
            if not self.verification:
                raise CostConfigError("verified=True without a sealed cost verification record "
                                      "— a flag is not evidence")
            try:
                check_record(self.verification)
            except CalibrationError as e:
                raise CostConfigError(f"cost verification record rejected: {e}") from e
            rp = {k: float(v) for k, v in (self.verification.get("params") or {}).items()}
            if rp != self.params:
                raise CostConfigError("cost verification record was made for different "
                                      f"parameters {rp} than {self.params}")

    @classmethod
    def from_params(cls, params: dict, *, version: str, verified: bool = False,
                    test_only: bool = False) -> "CostContext":
        p = _validate_params(dict(params))
        return cls(model=CandleCostModel(**p), version=version,
                   verified=verified, test_only=test_only)

    @classmethod
    def from_verification(cls, record: dict) -> "CostContext":
        """The only production path to a verified context: a sealed VERIFIED
        record from ``cost_calibration`` (checked again here)."""
        p = _validate_params(dict(record.get("params") or {}))
        return cls(model=CandleCostModel(**p),
                   version=f"{COST_MODEL_VERSION}#verified:{record.get('record_id')}",
                   verified=True, test_only=False, verification=dict(record))

    @classmethod
    def from_assumed_profile(cls, name_or_path: str, *, config_dir=None) -> "CostContext":
        """A documented ASSUMED cost profile (unverified, research only)."""
        prof, sha = load_assumed_profile(name_or_path, config_dir=config_dir)
        p = {"fee_rate": prof["fee_rate"],
             "half_spread": prof["base_half_spread"] + prof["slippage_margin_per_side"],
             "impact_coefficient": prof["impact_coefficient"],
             "max_participation": prof["max_participation"]}
        p = _validate_params(p)
        return cls(model=CandleCostModel(**p),
                   version=f"{COST_MODEL_VERSION}#assumed:{prof['profile_id']}:{sha[:12]}",
                   verified=False, test_only=False,
                   assumption={"profile_id": prof["profile_id"], "sha256": sha,
                               "fee_source": prof["fee_source"],
                               "base_half_spread": prof["base_half_spread"],
                               "slippage_margin_per_side": prof["slippage_margin_per_side"]})

    @classmethod
    def from_public_estimate(cls, record: dict) -> "CostContext":
        """Parameters from a sealed ``public_cost_estimate`` record
        (ESTIMATED_FROM_PUBLIC_DATA — unverified, research only)."""
        from public_cost_estimate import EstimateError, check_estimate
        try:
            check_estimate(record)
        except EstimateError as e:
            raise CostConfigError(f"public cost estimate rejected: {e}") from e
        p = _validate_params(dict(record["params"]))
        return cls(model=CandleCostModel(**p),
                   version=f"{COST_MODEL_VERSION}#public_estimate:{record['record_id']}",
                   verified=False, test_only=False,
                   assumption={"kind": "public_estimate", "status": ESTIMATED_FROM_PUBLIC_DATA,
                               "profile_id": record["record_id"], "sha256": record["record_id"],
                               "fee_source": record["fee_basis"]["fee_source"]})

    @property
    def status(self) -> str:
        if self.verified:
            return "VERIFIED"
        if self.assumption is None:
            return UNVERIFIED_COST_MODEL
        return self.assumption.get("status", ASSUMED_COST_MODEL)

    def verified_for(self, symbols, order_value: float) -> tuple[bool, list[str]]:
        """Does the verification cover this use (symbols, order size)?"""
        if not self.verified:
            return False, [self.status]
        if self.test_only:
            return True, []
        sc = (self.verification or {}).get("scope") or {}
        why = [f"{s} not in verified symbols {sc.get('symbols')}"
               for s in symbols if s not in (sc.get("symbols") or [])]
        lo, hi = (sc.get("notional_range") or [math.inf, -math.inf])
        if not (lo <= order_value <= hi):
            why.append(f"order value {order_value} outside verified range [{lo}, {hi}]")
        return not why, why

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
            "verification_record": (self.verification or {}).get("record_id"),
            # assumed profiles only: the default cost identity keeps its shape
            **({"status": self.status, "assumption_profile": self.assumption["profile_id"],
                "assumption_sha256": self.assumption["sha256"]} if self.assumption else {}),
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


def load_assumed_profile(name_or_path: str, *, config_dir=None) -> tuple[dict, str]:
    """Read and validate an assumed-cost profile; returns (profile, sha256 of the file)."""
    from pathlib import Path
    cdir = Path(config_dir) if config_dir else Path(__file__).resolve().parent / "config"
    path = cdir / ASSUMED_PROFILES[name_or_path] if name_or_path in ASSUMED_PROFILES else Path(name_or_path)
    try:
        raw = path.read_bytes()
        prof = __import__("json").loads(raw)
    except (OSError, ValueError) as e:
        raise CostConfigError(f"assumed cost profile {name_or_path!r} unreadable: {e}") from e
    if not isinstance(prof, dict) or prof.get("schema") != ASSUMED_PROFILE_SCHEMA:
        raise CostConfigError(f"{path}: schema must be {ASSUMED_PROFILE_SCHEMA!r}")
    if prof.get("status") != ASSUMED_COST_MODEL:
        raise CostConfigError(f"{path}: status must be {ASSUMED_COST_MODEL!r} — a profile is never verified")
    for k in ("profile_id", "fee_source"):
        if not isinstance(prof.get(k), str) or not prof[k].strip():
            raise CostConfigError(f"{path}: {k} required")
    for k in ("fee_rate", "base_half_spread", "slippage_margin_per_side",
              "impact_coefficient", "max_participation"):
        v = prof.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise CostConfigError(f"{path}: {k} must be a finite number >= 0, got {v!r}")
    if any(k in prof for k in ("verified", "verification")):
        raise CostConfigError(f"{path}: a profile cannot carry verification fields")
    return prof, hashlib.sha256(raw).hexdigest()


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
