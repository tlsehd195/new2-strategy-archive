"""Declarative strategy specifications (auto-evolution §3-1).

A strategy is DATA, never code: a JSON document validated against an
explicit feature/operator registry.  Nothing here evaluates strings or
builds Python source.  Unknown features, unknown operators, wrong types and
out-of-range parameters are refused before a candidate is registered.

Schema ``strategy_spec_v1``::

    {"schema": "strategy_spec_v1",
     "direction": "long" | "short" | "symmetric",
     "symbols": ["BTCUSDT", ...],          # sorted on canonicalisation
     "timeframe": "15m",
     "entry":   {"logic": "all" | "any", "conditions": [COND, ...]},
     "filters": [COND, ...],              # always AND-ed with the entry
     "exit":    {"horizon": int, "stop_loss_bp": float|null, "take_profit_bp": float|null},
     "risk":    {"sizing": "vol_inverse"|"fixed_fraction", "risk_per_trade": float,
                 "max_leverage": float, "max_concurrent": int}}
    COND = {"feature": NAME, "params": {...}, "op": "gt"|"ge"|"lt"|"le"|"between",
            "value": float | [lo, hi]}

Derived (never free): ``family`` (niche label from the entry features and
direction), ``data`` (required datasets), ``primary_cell``.

Canonical form = every parameter filled with its registry default, floats
rounded, conditions of a commutative AND/OR sorted and de-duplicated.  The
strategy id is the hash of that FULLY RESOLVED form, so "same id, different
computation" cannot happen and parameter order/explicit defaults cannot
create new ids.

"symmetric" direction: long when the conditions hold; short when the
MIRRORED conditions hold (signed features are negated, unsigned ones are
unchanged).  Requires at least one signed entry feature.

Look-ahead: every feature value at bar i is computed from bars[:i+1] (and
funding settled by the close of bar i / FNG of earlier UTC days) only.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

SPEC_SCHEMA = "strategy_spec_v1"
SPEC_REGISTRY_VERSION = "spec-registry-v1"
SUPPORTED_SYMBOLS = ["BTCUSDT", "ETHUSDT"]      # extend here (plus data + locked windows)
TIMEFRAME = "15m"

MAX_ENTRY_CONDITIONS = 3
MAX_FILTERS = 2
MAX_TOTAL_CONDITIONS = 4
HORIZON_RANGE = (4, 768)
STOP_RANGE_BP = (10.0, 3000.0)
OPS = ("gt", "ge", "lt", "le", "between")


class SpecError(ValueError):
    """The specification is not a valid, registrable strategy."""


# ---------------------------------------------------------------------------
# feature registry (plugin point)
# ---------------------------------------------------------------------------

@dataclass
class Param:
    kind: type                      # int or float
    lo: float
    hi: float
    default: object                 # value or callable(params) -> value

    def resolve_default(self, params: Mapping) -> object:
        return self.default(params) if callable(self.default) else self.default


@dataclass
class Feature:
    name: str
    params: dict                    # name -> Param
    value_range: tuple[float, float]
    signed: bool                    # mirror-able (value -> -value) for symmetric specs
    data: tuple[str, ...]           # datasets required
    kind: str                       # semantic group used by the swap operator
    compute: Callable = field(repr=False, default=None)
    ops: tuple[str, ...] = OPS


FEATURES: dict[str, Feature] = {}


def register_feature(f: Feature) -> Feature:
    """Plugin hook: add a feature (new strategy building block).  Registering
    changes ``registry_identity()`` and therefore the experiment context."""
    if f.name in FEATURES:
        raise SpecError(f"feature {f.name} already registered")
    FEATURES[f.name] = f
    return f


def registry_identity() -> dict:
    return {"version": SPEC_REGISTRY_VERSION, "symbols": list(SUPPORTED_SYMBOLS),
            "features": {n: {"params": {p: [v.kind.__name__, v.lo, v.hi] for p, v in f.params.items()},
                             "range": list(f.value_range), "signed": f.signed, "data": list(f.data),
                             "ops": list(f.ops)}
                         for n, f in sorted(FEATURES.items())},
            # only when an opt-in set is enabled: default identities are unchanged
            **({"feature_sets": sorted(_ENABLED_SETS)} if _ENABLED_SETS else {})}


# --- causal helpers -----------------------------------------------------------

def _rolling_z(vals: Sequence[Optional[float]], i: int, window: int) -> Optional[float]:
    """Identical to runner._rolling_z (legacy semantics: window of PRIOR
    values, current value standardised)."""
    if i < window:
        return None
    xs = [v for v in vals[i - window:i] if v is not None and v == v]
    if len(xs) < window * 0.9:
        return None
    mu = sum(xs) / len(xs)
    var = sum((x - mu) ** 2 for x in xs) / max(1, len(xs) - 1)
    sd = var ** 0.5
    if sd <= 0:
        return None
    cur = vals[i]
    if cur is None or cur != cur:
        return None
    return (cur - mu) / sd


def _f_ret_z(ctx: dict, p: Mapping) -> list:
    closes = ctx["closes"]
    n, lb, w, mh = len(closes), p["lookback"], p["window"], p["min_history"]
    rets: list = [None] * n
    for i in range(lb, n):
        if closes[i - lb] > 0:
            rets[i] = closes[i] / closes[i - lb] - 1.0
    return [None if i < mh else _rolling_z(rets, i, w) for i in range(n)]


def _f_taker_share_z(ctx: dict, p: Mapping) -> list:
    share = ctx["taker_share"]
    return [_rolling_z(share, i, p["window"]) for i in range(len(share))]


def _f_breakout_k(ctx: dict, p: Mapping) -> list:
    closes, vw = ctx["closes"], p["window"]
    out: list = [None] * len(closes)
    for i in range(vw + 1, len(closes)):
        w = closes[i - vw:i]
        mu = sum(w) / vw
        sd = (sum((x - mu) ** 2 for x in w) / max(1, vw - 1)) ** 0.5
        if sd > 0:
            out[i] = (closes[i] - max(w)) / sd
    return out


def _f_hour_utc(ctx: dict, p: Mapping) -> list:
    return [float((b.ts // 3_600_000) % 24) for b in ctx["bars"]]


def _f_funding_z(ctx: dict, p: Mapping) -> list:
    if ctx.get("funding") is None:
        raise LookupError("funding data required by funding_z")
    from runner import funding_known_series, funding_settlement_z
    known = funding_known_series(ctx["bars"], ctx["funding"])
    return funding_settlement_z(ctx["funding"], known, p["window"])


def _fng_hist(ctx: dict) -> list:
    if ctx.get("fng") is None:
        raise LookupError("FNG data required")
    if "fng_hist" not in ctx:
        from runner import FNG_PUBLICATION_LAG_DAYS
        lag, day_ms = FNG_PUBLICATION_LAG_DAYS, 86_400_000
        ctx["fng_hist"] = [[ctx["fng"].get(b.ts // day_ms - k) for k in range(lag, lag + 10)]
                           for b in ctx["bars"]]
    return ctx["fng_hist"]


def _f_fng_max(ctx: dict, p: Mapping) -> list:
    out = []
    for h in _fng_hist(ctx):
        days = h[:p["days"]]
        out.append(None if any(v is None for v in days) else max(days))
    return out


def _f_fng_min(ctx: dict, p: Mapping) -> list:
    out = []
    for h in _fng_hist(ctx):
        days = h[:p["days"]]
        out.append(None if any(v is None for v in days) else min(days))
    return out


def _f_vol_ratio(ctx: dict, p: Mapping) -> list:
    closes = ctx["closes"]
    n, s, l_ = len(closes), p["short"], p["long"]
    lr = [None] + [math.log(closes[i] / closes[i - 1]) if closes[i - 1] > 0 and closes[i] > 0 else None
                   for i in range(1, n)]

    def sd(i, w):
        xs = [x for x in lr[i - w + 1:i + 1] if x is not None]
        if len(xs) < w * 0.9 or len(xs) < 2:
            return None
        mu = sum(xs) / len(xs)
        return (sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
    out: list = [None] * n
    for i in range(l_, n):
        a, b = sd(i, s), sd(i, l_)
        if a is not None and b:
            out[i] = a / b
    return out


def _f_rsi(ctx: dict, p: Mapping) -> list:
    closes, per = ctx["closes"], p["period"]
    out: list = [None] * len(closes)
    for i in range(per, len(closes)):
        gains = losses = 0.0
        for j in range(i - per + 1, i + 1):
            d = closes[j] - closes[j - 1]
            if d > 0:
                gains += d
            else:
                losses -= d
        out[i] = 100.0 if losses == 0 else 100.0 - 100.0 / (1.0 + gains / losses)
    return out


def _f_volume_z(ctx: dict, p: Mapping) -> list:
    vols = [b.quote_volume for b in ctx["bars"]]
    return [_rolling_z(vols, i, p["window"]) for i in range(len(vols))]


register_feature(Feature("ret_z", {
    "lookback": Param(int, 1, 384, 4),
    "window": Param(int, 32, 2048, lambda p: max(64, 2 * p["lookback"])),
    "min_history": Param(int, 0, 4096, 0)}, (-8.0, 8.0), True, ("klines",), "momentum", _f_ret_z))
register_feature(Feature("taker_share_z", {"window": Param(int, 16, 2048, 24)},
                         (-8.0, 8.0), True, ("klines",), "flow", _f_taker_share_z))
register_feature(Feature("breakout_k", {"window": Param(int, 8, 1024, 32)},
                         (-8.0, 8.0), False, ("klines",), "breakout", _f_breakout_k))
register_feature(Feature("hour_utc", {}, (0.0, 24.0), False, ("klines",), "calendar", _f_hour_utc,
                         ops=("between",)))
register_feature(Feature("funding_z", {"window": Param(int, 8, 1000, 30)},
                         (-8.0, 8.0), True, ("klines", "funding"), "carry", _f_funding_z))
register_feature(Feature("fng_max", {"days": Param(int, 1, 10, 1)},
                         (0.0, 100.0), False, ("klines", "fng"), "sentiment", _f_fng_max))
register_feature(Feature("fng_min", {"days": Param(int, 1, 10, 1)},
                         (0.0, 100.0), False, ("klines", "fng"), "sentiment", _f_fng_min))
register_feature(Feature("vol_ratio", {"short": Param(int, 4, 256, 16),
                                       "long": Param(int, 32, 2048, 192)},
                         (0.0, 10.0), False, ("klines",), "volatility", _f_vol_ratio))
register_feature(Feature("rsi", {"period": Param(int, 2, 256, 14)},
                         (0.0, 100.0), False, ("klines",), "oscillator", _f_rsi))
register_feature(Feature("volume_z", {"window": Param(int, 16, 2048, 96)},
                         (-8.0, 8.0), True, ("klines",), "flow", _f_volume_z))

# --- opt-in feature set v2 (ADR 0066) -------------------------------------------
# Built only from the klines already used (OHLC + quote volume), causal (value i
# uses bars <= i), never registered by default: enabling a set changes
# ``registry_identity()`` and therefore the experiment context, so runs made
# without it keep their context ids and DEV results.

def _f_range_z(ctx: dict, p: Mapping) -> list:
    """z of the bar range (high - low) / close against the prior window."""
    rng = [((b.high - b.low) / b.close) if b.close > 0 else None for b in ctx["bars"]]
    return [_rolling_z(rng, i, p["window"]) for i in range(len(rng))]


def _f_illiq_z(ctx: dict, p: Mapping) -> list:
    """z of the Amihud ratio |r_1| / quote volume (bp per million quote) against the prior window."""
    closes, bars = ctx["closes"], ctx["bars"]
    am: list = [None] * len(bars)
    for i in range(1, len(bars)):
        if closes[i - 1] > 0 and bars[i].quote_volume > 0:
            am[i] = abs(closes[i] / closes[i - 1] - 1.0) * 1e4 / (bars[i].quote_volume / 1e6)
    return [_rolling_z(am, i, p["window"]) for i in range(len(am))]


def _f_ma_dist_z(ctx: dict, p: Mapping) -> list:
    """(close - SMA of the prior window) / sd of the prior window."""
    closes, w = ctx["closes"], p["window"]
    out: list = [None] * len(closes)
    for i in range(w, len(closes)):
        xs = closes[i - w:i]
        mu = sum(xs) / w
        sd = (sum((x - mu) ** 2 for x in xs) / max(1, w - 1)) ** 0.5
        if sd > 0:
            out[i] = max(-8.0, min(8.0, (closes[i] - mu) / sd))
    return out


def _f_skew_z(ctx: dict, p: Mapping) -> list:
    """z of the rolling skewness of the last ``length`` 15m log returns (bars <= i)
    against its prior ``window`` values (legacy X12 rule, PREREG_x12_skewness)."""
    closes, L = ctx["closes"], p["length"]
    lr = [None] + [math.log(closes[i] / closes[i - 1]) if closes[i - 1] > 0 and closes[i] > 0 else None
                   for i in range(1, len(closes))]
    sk: list = [None] * len(closes)
    for i in range(L, len(closes)):
        xs = [x for x in lr[i - L + 1:i + 1] if x is not None]
        if len(xs) < max(3, int(L * 0.9)):
            continue
        n = len(xs)
        mu = sum(xs) / n
        m2 = sum((x - mu) ** 2 for x in xs) / n
        if m2 <= 0:
            continue
        sk[i] = (sum((x - mu) ** 3 for x in xs) / n) / m2 ** 1.5
    return [_rolling_z(sk, i, p["window"]) for i in range(len(sk))]


def _f_dow_utc(ctx: dict, p: Mapping) -> list:
    """UTC day of week of the bar open, Monday = 0 (1970-01-01 was a Thursday)."""
    return [float((b.ts // 86_400_000 + 3) % 7) for b in ctx["bars"]]


FEATURE_SETS: dict[str, tuple] = {
    "v1": (),
    "v2": (
        Feature("range_z", {"window": Param(int, 16, 2048, 96)}, (-8.0, 8.0), False, ("klines",),
                "volatility", _f_range_z),
        Feature("illiq_z", {"window": Param(int, 16, 2048, 96)}, (-8.0, 8.0), False, ("klines",),
                "liquidity", _f_illiq_z),
        Feature("ma_dist_z", {"window": Param(int, 8, 2048, 96)}, (-8.0, 8.0), True, ("klines",),
                "mean_reversion", _f_ma_dist_z),
        Feature("skew_z", {"length": Param(int, 16, 1024, 96), "window": Param(int, 64, 2048, 960)},
                (-8.0, 8.0), True, ("klines",), "distribution", _f_skew_z),
        Feature("dow_utc", {}, (0.0, 7.0), False, ("klines",), "calendar", _f_dow_utc,
                ops=("between",)),
    ),
}
_ENABLED_SETS: list = []


def enable_feature_set(name: str) -> list:
    """Register the features of an opt-in set (idempotent); returns their names."""
    if name not in FEATURE_SETS:
        raise SpecError(f"unknown feature set {name!r} (have {sorted(FEATURE_SETS)})")
    for f in FEATURE_SETS[name]:
        if f.name not in FEATURES:
            register_feature(f)
    if FEATURE_SETS[name] and name not in _ENABLED_SETS:
        _ENABLED_SETS.append(name)
    return [f.name for f in FEATURE_SETS[name]]


def disable_feature_set(name: str) -> None:
    """Remove an opt-in set again (tests; one process = one registry)."""
    for f in FEATURE_SETS.get(name, ()):
        FEATURES.pop(f.name, None)
    if name in _ENABLED_SETS:
        _ENABLED_SETS.remove(name)


RISK_DEFAULTS = {"sizing": "vol_inverse", "risk_per_trade": 0.01, "max_leverage": 1.0,
                 "max_concurrent": 2}
RISK_RANGES = {"risk_per_trade": (0.001, 0.05), "max_leverage": (0.5, 3.0),
               "max_concurrent": (1, 4)}


# ---------------------------------------------------------------------------
# validation + canonicalisation
# ---------------------------------------------------------------------------

def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SpecError(f"{what}: number required, got {v!r}")
    if not math.isfinite(v):
        raise SpecError(f"{what}: non-finite value")
    return float(v)


def _round(x: float) -> float:
    return float(f"{x:.10g}")


def _canon_cond(c: Mapping, where: str) -> dict:
    if not isinstance(c, Mapping):
        raise SpecError(f"{where}: condition must be an object")
    extra = set(c) - {"feature", "params", "op", "value"}
    if extra:
        raise SpecError(f"{where}: unknown keys {sorted(extra)}")
    name = c.get("feature")
    if name not in FEATURES:
        raise SpecError(f"{where}: unsupported feature {name!r}")
    f = FEATURES[name]
    raw = dict(c.get("params") or {})
    unknown = set(raw) - set(f.params)
    if unknown:
        raise SpecError(f"{where}: {name} has no parameter(s) {sorted(unknown)}")
    params: dict = {}
    for pn in sorted(f.params, key=lambda k: (callable(f.params[k].default), k)):
        spec = f.params[pn]
        v = raw.get(pn, spec.resolve_default(params))
        if spec.kind is int:
            if isinstance(v, bool) or not (isinstance(v, int) or (isinstance(v, float) and v.is_integer())):
                raise SpecError(f"{where}: {name}.{pn} must be an integer, got {v!r}")
            v = int(v)
        else:
            v = _round(_num(v, f"{where}: {name}.{pn}"))
        if not spec.lo <= v <= spec.hi:
            raise SpecError(f"{where}: {name}.{pn}={v} outside [{spec.lo}, {spec.hi}]")
        params[pn] = v
    if name == "vol_ratio" and params["short"] >= params["long"]:
        raise SpecError(f"{where}: vol_ratio short window must be < long window")
    op = c.get("op")
    if op not in f.ops:
        raise SpecError(f"{where}: operator {op!r} not allowed for {name} (allowed {list(f.ops)})")
    lo_r, hi_r = f.value_range
    if op == "between":
        v = c.get("value")
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            raise SpecError(f"{where}: 'between' needs [lo, hi]")
        lo, hi = _round(_num(v[0], where)), _round(_num(v[1], where))
        if not lo < hi:
            raise SpecError(f"{where}: empty interval [{lo}, {hi})")
        if lo < lo_r or hi > hi_r:
            raise SpecError(f"{where}: interval outside the feature range {f.value_range}")
        value: object = [lo, hi]
    else:
        value = _round(_num(c.get("value"), where))
        if not lo_r <= value <= hi_r:
            raise SpecError(f"{where}: threshold {value} outside the feature range {f.value_range}")
    return {"feature": name, "params": params, "op": op, "value": value}


def _cond_key(c: Mapping) -> str:
    return json.dumps(c, sort_keys=True, separators=(",", ":"))


def _interval(c: Mapping) -> tuple[float, float]:
    v = c["value"]
    if c["op"] in ("gt", "ge"):
        return v, math.inf
    if c["op"] in ("lt", "le"):
        return -math.inf, v
    return v[0], v[1]


def _check_satisfiable(conds: Sequence[Mapping], where: str) -> None:
    """AND-ed conditions on the same feature+params must leave a non-empty set."""
    groups: dict = {}
    for c in conds:
        groups.setdefault(_cond_key({"feature": c["feature"], "params": c["params"]}), []).append(c)
    for g in groups.values():
        lo = max(_interval(c)[0] for c in g)
        hi = min(_interval(c)[1] for c in g)
        if not lo < hi:
            raise SpecError(f"{where}: contradictory conditions on {g[0]['feature']} "
                            f"(no value satisfies all of them)")


def canonicalize_spec(spec: Mapping) -> dict:
    """Validate and return the canonical (fully resolved) specification."""
    if not isinstance(spec, Mapping):
        raise SpecError("specification must be an object")
    if spec.get("schema") != SPEC_SCHEMA:
        raise SpecError(f"schema must be {SPEC_SCHEMA!r}")
    allowed = {"schema", "direction", "symbols", "timeframe", "entry", "filters", "exit",
               "risk", "family", "data", "primary_cell", "_note"}
    extra = set(spec) - allowed
    if extra:
        raise SpecError(f"unknown top-level keys {sorted(extra)}")
    direction = spec.get("direction")
    if direction not in ("long", "short", "symmetric"):
        raise SpecError(f"direction {direction!r} not supported")
    syms = spec.get("symbols")
    if not isinstance(syms, (list, tuple)) or not syms:
        raise SpecError("symbols must be a non-empty list")
    bad = [s for s in syms if s not in SUPPORTED_SYMBOLS]
    if bad:
        raise SpecError(f"unsupported symbols {bad} (supported {SUPPORTED_SYMBOLS})")
    if len(set(syms)) != len(syms):
        raise SpecError("duplicate symbols")
    if spec.get("timeframe", TIMEFRAME) != TIMEFRAME:
        raise SpecError("only the 15m timeframe is supported")
    entry = spec.get("entry") or {}
    logic = entry.get("logic", "all")
    if logic not in ("all", "any"):
        raise SpecError(f"entry logic {logic!r} not supported")
    raw_conds = entry.get("conditions") or []
    if not 1 <= len(raw_conds) <= MAX_ENTRY_CONDITIONS:
        raise SpecError(f"entry needs 1..{MAX_ENTRY_CONDITIONS} conditions")
    conds = [_canon_cond(c, f"entry[{i}]") for i, c in enumerate(raw_conds)]
    raw_f = spec.get("filters") or []
    if len(raw_f) > MAX_FILTERS:
        raise SpecError(f"at most {MAX_FILTERS} filters")
    filters = [_canon_cond(c, f"filters[{i}]") for i, c in enumerate(raw_f)]
    # semantic dedup of commutative clauses
    conds = [json.loads(k) for k in sorted({_cond_key(c) for c in conds})]
    filters = [json.loads(k) for k in sorted({_cond_key(c) for c in filters})]
    if len(conds) + len(filters) > MAX_TOTAL_CONDITIONS:
        raise SpecError(f"at most {MAX_TOTAL_CONDITIONS} conditions in total")
    if logic == "all":
        _check_satisfiable(conds + filters, "entry+filters")
    else:
        _check_satisfiable(filters, "filters")
        if len(conds) == 1:
            logic = "all"                     # any(x) == all(x): one canonical form
    if direction == "symmetric" and not any(FEATURES[c["feature"]].signed for c in conds):
        raise SpecError("symmetric direction needs at least one signed entry feature")
    ex = spec.get("exit") or {}
    extra = set(ex) - {"horizon", "stop_loss_bp", "take_profit_bp"}
    if extra:
        raise SpecError(f"unknown exit keys {sorted(extra)}")
    h = ex.get("horizon")
    if isinstance(h, bool) or not isinstance(h, int) or not HORIZON_RANGE[0] <= h <= HORIZON_RANGE[1]:
        raise SpecError(f"exit.horizon must be an integer in {HORIZON_RANGE}")
    stops = {}
    for k in ("stop_loss_bp", "take_profit_bp"):
        v = ex.get(k)
        if v is None:
            stops[k] = None
            continue
        v = _round(_num(v, f"exit.{k}"))
        if not STOP_RANGE_BP[0] <= v <= STOP_RANGE_BP[1]:
            raise SpecError(f"exit.{k}={v} outside {STOP_RANGE_BP}")
        stops[k] = v
    rk = {**RISK_DEFAULTS, **(spec.get("risk") or {})}
    extra = set(rk) - set(RISK_DEFAULTS)
    if extra:
        raise SpecError(f"unknown risk keys {sorted(extra)}")
    if rk["sizing"] not in ("vol_inverse", "fixed_fraction"):
        raise SpecError(f"risk.sizing {rk['sizing']!r} not supported")
    for k, (lo, hi) in RISK_RANGES.items():
        v = _num(rk[k], f"risk.{k}")
        if not lo <= v <= hi:
            raise SpecError(f"risk.{k}={v} outside [{lo}, {hi}]")
        rk[k] = int(v) if k == "max_concurrent" else _round(v)
    symbols = sorted(syms)
    data = sorted({d for c in conds + filters for d in FEATURES[c["feature"]].data} | {"klines"})
    family = "spec:" + "+".join(sorted({c["feature"] for c in conds})) + ":" + direction
    out = {
        "schema": SPEC_SCHEMA, "direction": direction, "symbols": symbols, "timeframe": TIMEFRAME,
        "entry": {"logic": logic, "conditions": conds}, "filters": filters,
        "exit": {"horizon": h, **stops}, "risk": rk,
        "family": family, "data": data,
        "primary_cell": {"symbol": symbols[0], "horizon": h},
    }
    for k in ("family", "data", "primary_cell"):
        if k in spec and spec[k] != out[k]:
            raise SpecError(f"{k} is derived; supplied {spec[k]!r} != derived {out[k]!r}")
    return out


def validate_spec(spec: Mapping) -> tuple[bool, str]:
    try:
        canonicalize_spec(spec)
        return True, ""
    except SpecError as e:
        return False, str(e)


def spec_requirements(spec: Mapping) -> list[str]:
    return list(canonicalize_spec(spec)["data"])


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------

def build_spec_aux(bars, spec: Mapping, funding, fng, *, cache: Optional[dict] = None,
                   cache_ns: object = None) -> dict:
    """Feature series for every condition (computed once per bar list).

    ``cache`` (with a namespace identifying the exact bars / funding / FNG
    inputs) shares series between strategies that use the same feature.
    Every feature is causal (value i uses bars <= i only — tested), so a
    series computed on a longer bar list is valid for any prefix of it."""
    s = canonicalize_spec(spec)
    ctx = None
    series = {}
    for c in s["entry"]["conditions"] + s["filters"]:
        key = _cond_key({"feature": c["feature"], "params": c["params"]})
        if key in series:
            continue
        ck = (cache_ns, key)
        if cache is not None and ck in cache:
            series[key] = cache[ck]
            continue
        if ctx is None:
            ctx = {"bars": bars, "closes": [b.close for b in bars], "funding": funding, "fng": fng,
                   "taker_share": [b.taker_buy_volume / b.volume if b.volume > 0 else None
                                   for b in bars]}
        series[key] = FEATURES[c["feature"]].compute(ctx, c["params"])
        if cache is not None:
            cache[ck] = series[key]
    return {"spec": s, "series": series}


def _holds(c: Mapping, x: Optional[float]) -> bool:
    if x is None or x != x:
        return False
    op, v = c["op"], c["value"]
    if op == "gt":
        return x > v
    if op == "ge":
        return x >= v
    if op == "lt":
        return x < v
    if op == "le":
        return x <= v
    return v[0] <= x < v[1]


def _side_ok(s: Mapping, series: Mapping, i: int, mirror: bool) -> bool:
    def val(c):
        x = series[_cond_key({"feature": c["feature"], "params": c["params"]})][i]
        if mirror and x is not None and FEATURES[c["feature"]].signed:
            return -x
        return x
    ent = [_holds(c, val(c)) for c in s["entry"]["conditions"]]
    ok = all(ent) if s["entry"]["logic"] == "all" else any(ent)
    return ok and all(_holds(c, val(c)) for c in s["filters"])


def build_spec_signal(spec: Mapping, aux: Mapping) -> Callable:
    s, series = aux["spec"], aux["series"]

    def sig(view) -> int:
        i = len(view) - 1
        if s["direction"] == "long":
            return 1 if _side_ok(s, series, i, False) else 0
        if s["direction"] == "short":
            return -1 if _side_ok(s, series, i, False) else 0
        if _side_ok(s, series, i, False):
            return 1
        if _side_ok(s, series, i, True):
            return -1
        return 0
    return sig


#: how an intrabar stop is filled (versioned config, ADR 0031)
EXIT_FILL_CONFIG = Path(__file__).resolve().parent / "config" / "exit_fill_v1.json"
STOP_FILLS = ("stop_price", "bar_extreme")


def default_stop_fill(path: Path = EXIT_FILL_CONFIG) -> str:
    """The stop fill mode of the versioned exit-fill config (refuses an unknown one)."""
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    mode = cfg.get("stop_fill")
    if cfg.get("schema") != "exit_fill_v1" or mode not in STOP_FILLS:
        raise ValueError(f"{path}: not an exit_fill_v1 config with a known stop_fill ({mode!r})")
    return mode


def stop_exit_rule(spec: Mapping, stop_fill: Optional[str] = None) -> Optional[Callable]:
    """Intrabar stop-loss / take-profit exit for the event study, or None.

    Conservative fills: a bar that touches both levels counts as a stop; a
    bar that OPENS beyond a level fills at its open (gap), never at the
    better level.  ``stop_fill`` (default: ``config/exit_fill_v1.json``):
    ``"bar_extreme"`` fills a stop touched inside a bar at that bar's worst
    price (low for a long, high for a short) — stop slippage bounded by the
    bar, never better than the stop price; ``"stop_price"`` is the earlier
    exact fill at the stop level (an optimistic stop-market fill)."""
    s = canonicalize_spec(spec)
    sl, tp = s["exit"]["stop_loss_bp"], s["exit"]["take_profit_bp"]
    if sl is None and tp is None:
        return None
    mode = default_stop_fill() if stop_fill is None else stop_fill
    if mode not in STOP_FILLS:
        raise ValueError(f"unknown stop_fill {mode!r} (one of {STOP_FILLS})")

    def rule(bars, entry_i: int, side: int, horizon: int):
        entry = bars[entry_i].open
        sl_px = entry * (1 - side * sl / 1e4) if sl is not None else None
        tp_px = entry * (1 + side * tp / 1e4) if tp is not None else None
        last = entry_i + horizon
        for j in range(entry_i, min(last, len(bars))):
            b = bars[j]
            if sl_px is not None:
                gap = (b.open <= sl_px) if side == 1 else (b.open >= sl_px)
                touch = (b.low <= sl_px) if side == 1 else (b.high >= sl_px)
                if j > entry_i and gap:
                    return j, b.open, "stop_gap"
                if touch:
                    if mode == "bar_extreme":
                        return j, (min(b.low, sl_px) if side == 1 else max(b.high, sl_px)), "stop"
                    return j, sl_px, "stop"
            if tp_px is not None:
                gap = (b.open >= tp_px) if side == 1 else (b.open <= tp_px)
                touch = (b.high >= tp_px) if side == 1 else (b.low <= tp_px)
                if j > entry_i and gap:
                    return j, b.open, "take_profit_gap"
                if touch:
                    return j, tp_px, "take_profit"
        if last >= len(bars):
            return None
        return last, bars[last].open, "time"
    return rule


# ---------------------------------------------------------------------------
# legacy family -> spec translation (lineage of the 7 rule-based families)
# ---------------------------------------------------------------------------

def legacy_to_spec(defn: Mapping) -> dict:
    """Semantically identical spec for a legacy-family definition (tested to
    give identical signals).  Used to seed evolution from existing
    candidates; the legacy id stays the parent id in the lineage."""
    from generator import resolve_params
    fam = defn["family"]
    p = resolve_params(defn)
    syms = list(defn.get("symbols") or SUPPORTED_SYMBOLS)
    h = int(p["horizon"])
    d = str(defn.get("direction", ""))

    def cond(feature, params, op, value):
        return {"feature": feature, "params": params, "op": op, "value": value}
    if fam == "ret_rev":
        lb = p["lookback"]
        c = [cond("ret_z", {"lookback": lb, "window": max(64, 2 * lb), "min_history": 4 * lb},
                  "lt", -p["thresh_z"])]
        direction = "long"
    elif fam == "ts_mom":
        lb = p["lookback"]
        c = [cond("ret_z", {"lookback": lb, "window": max(64, 2 * lb), "min_history": 0},
                  "gt", p["thresh_z"])]
        direction = "symmetric"
    elif fam == "flow_z":
        c = [cond("taker_share_z", {"window": p["z_window"]}, "gt", p["entry_z"])]
        direction = "short"
    elif fam == "vol_break":
        c = [cond("breakout_k", {"window": p["vol_window"]}, "gt", p["k"])]
        direction = "long"
    elif fam == "tod":
        c = [cond("hour_utc", {}, "between", [p["utc_start"], p["utc_end"]])]
        direction = "long" if d.startswith("long") else "short"
    elif fam == "fund_z":
        c = [cond("funding_z", {"window": p["funding_window"]}, "gt", p["thresh_z"])]
        direction = "short"
    elif fam == "fng":
        if d.startswith("long"):
            c = [cond("fng_max", {"days": p["confirm_days"]}, "le", p["threshold"])]
            direction = "long"
        else:
            c = [cond("fng_min", {"days": p["confirm_days"]}, "ge", p["threshold"])]
            direction = "short"
    else:
        raise SpecError(f"no spec translation for family {fam!r}")
    return canonicalize_spec({"schema": SPEC_SCHEMA, "direction": direction, "symbols": syms,
                              "entry": {"logic": "all", "conditions": c}, "filters": [],
                              "exit": {"horizon": h}})
