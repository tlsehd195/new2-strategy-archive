"""Cost-model calibration and verification against observed execution data.

The engine prices every trade with ``costs_bridge.CostContext`` (NEW2's
``CandleCostModel``: fee + half spread + impact * sqrt(participation)).  Its
default parameters are ASSUMPTIONS.  This module is the only way a cost
context can become ``verified``:

1. observations (one per order) are loaded from a declared source;
2. the sample is split chronologically — the first part calibrates the
   parameters, the second part (never seen by the fit) verifies them;
3. a preregistered criteria file (``config/cost_verification_v1.json``)
   decides VERIFIED / NOT_VERIFIED / BLOCKED_EXTERNAL_EVIDENCE;
4. the decision is sealed in an append-only record whose content hash is
   checked again whenever a cost context claims to be verified
   (``CostContext.from_verification``).

What each source can and cannot prove (never inferred upward):

=====================  ==================================================
source kind            verifiable components
=====================  ==================================================
exchange_fills         fee, spread, impact/slippage, fill/reject rates
                       (account execution reports: fill price, qty, fee,
                       order type, plus the reference quote at decision)
quote_snapshots        spread only (observed best bid / ask, no fills)
paper_fill_monitor     spread only (NEW2 maker-fill monitor: virtual quotes
                       against public trades — no real order was sent)
ohlcv_only             nothing (candles carry no quote or fill)
=====================  ==================================================

``verified`` requires every model component (fee, spread, impact) to be
verifiable from the data — so only ``exchange_fills`` can ever produce a
VERIFIED record.  Account fee tiers and fills are user data: nothing here
assumes them, and nothing here sends an order.

Observed per-side cost of a fill (bp of filled notional):
    side * (fill_price - ref_price) / ref_price * 1e4 + fee / filled_notional * 1e4
with ``ref_price`` the mid at decision time.  Predicted cost: the cost
context's quote for the same order value and the bar's traded value.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from engine_lib import append_jsonl, canonical_json, code_identity, iso_now, iter_jsonl

COST_CALIBRATION_VERSION = "cost-calib-v2"
RECORD_SCHEMA = "cost_verification_v1"
DEFAULT_CRITERIA = Path(__file__).resolve().parent / "config" / "cost_verification_v1.json"

SOURCE_COMPONENTS = {
    "exchange_fills": ("fee", "spread", "impact", "fill_rates"),
    "quote_snapshots": ("spread",),
    "paper_fill_monitor": ("spread",),
    "ohlcv_only": (),
}
MODEL_COMPONENTS = ("fee", "spread", "impact")
FILL_STATUSES = ("FILLED", "PARTIAL", "REJECTED", "EXPIRED")
ORDER_TYPES = ("taker", "maker")


class CalibrationError(ValueError):
    pass


@dataclass(frozen=True)
class Observation:
    ts_ms: int
    symbol: str
    side: int                    # +1 buy / -1 sell
    order_type: str              # "taker" | "maker"
    notional: float              # requested order value (quote currency)
    ref_price: float             # mid at decision time
    status: str                  # FILLED | PARTIAL | REJECTED | EXPIRED
    source_kind: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    fill_price: Optional[float] = None
    filled_notional: Optional[float] = None
    fee: Optional[float] = None              # quote currency, as charged
    bar_traded_value: Optional[float] = None  # quote volume of the decision bar
    bar_volatility: Optional[float] = None

    def validate(self) -> None:
        if self.source_kind not in SOURCE_COMPONENTS:
            raise CalibrationError(f"unknown source kind {self.source_kind!r}")
        if isinstance(self.ts_ms, bool) or not isinstance(self.ts_ms, int) or self.ts_ms <= 0:
            raise CalibrationError(f"ts_ms={self.ts_ms!r} must be a positive integer (epoch ms)")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise CalibrationError(f"symbol={self.symbol!r} required")
        if self.order_type not in ORDER_TYPES:
            raise CalibrationError(f"order_type must be one of {ORDER_TYPES}, got {self.order_type!r}")
        for k in ("bid", "ask", "fill_price", "filled_notional", "fee", "bar_traded_value",
                  "bar_volatility"):
            v = getattr(self, k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))
                                  or not math.isfinite(v)):
                raise CalibrationError(f"{k}={v!r} must be a finite number when given")
        if self.bar_traded_value is not None and self.bar_traded_value <= 0:
            raise CalibrationError(f"bar_traded_value={self.bar_traded_value!r} must be > 0 when given")
        if self.source_kind != "exchange_fills" and any(
                getattr(self, k) is not None for k in ("fill_price", "filled_notional", "fee")):
            # a public quote or a virtual (paper / monitor) order is not an execution:
            # it must never carry a fill price or a fee that could be read as one
            raise CalibrationError(f"{self.source_kind} rows cannot carry fill_price / filled_notional / "
                                   "fee — only account execution reports are execution evidence")
        if self.side not in (1, -1):
            raise CalibrationError(f"side must be +1/-1, got {self.side!r}")
        if self.status not in FILL_STATUSES:
            raise CalibrationError(f"status must be one of {FILL_STATUSES}, got {self.status!r}")
        for k in ("notional", "ref_price"):
            v = getattr(self, k)
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
                raise CalibrationError(f"{k}={v!r} must be finite and > 0")
        if self.bid is not None and self.ask is not None and not (0 < self.bid <= self.ask):
            raise CalibrationError(f"crossed / invalid quote bid={self.bid} ask={self.ask}")
        if self.status in ("FILLED", "PARTIAL"):
            if self.source_kind == "exchange_fills":
                for k in ("fill_price", "filled_notional", "fee"):
                    v = getattr(self, k)
                    if v is None or not math.isfinite(v) or v < 0 or (k != "fee" and v == 0):
                        raise CalibrationError(f"filled exchange record needs {k} (got {v!r})")

    # -- derived quantities -----------------------------------------------------
    def half_spread_frac(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        mid = 0.5 * (self.bid + self.ask)
        return (self.ask - self.bid) / 2.0 / mid

    def observed_cost_bp(self) -> Optional[float]:
        if self.source_kind != "exchange_fills" or self.status not in ("FILLED", "PARTIAL"):
            return None
        slip = self.side * (self.fill_price - self.ref_price) / self.ref_price
        return (slip + self.fee / self.filled_notional) * 1e4

    def participation(self) -> Optional[float]:
        if not self.bar_traded_value or self.bar_traded_value <= 0:
            return None
        return self.notional / self.bar_traded_value


def data_sha256(obs: Sequence[Observation]) -> str:
    rows = [asdict(o) for o in sorted(obs, key=lambda o: (o.ts_ms, o.symbol, o.side, o.notional))]
    return hashlib.sha256(canonical_json(rows).encode()).hexdigest()


def load_observations(path: Path) -> list[Observation]:
    """JSONL, one :class:`Observation` per line.  Unknown keys or invalid
    rows raise — a calibration never silently drops evidence."""
    out = []
    fields = set(Observation.__dataclass_fields__)
    seen: dict[str, int] = {}
    for i, r in enumerate(iter_jsonl(Path(path))):
        extra = set(r) - fields
        if extra:
            raise CalibrationError(f"{path}:{i + 1}: unknown fields {sorted(extra)}")
        try:
            o = Observation(**r)
        except TypeError as e:
            raise CalibrationError(f"{path}:{i + 1}: {e}") from e
        o.validate()
        key = canonical_json(asdict(o))
        if key in seen:
            # the same report twice would count one execution as two pieces of evidence
            raise CalibrationError(f"{path}:{i + 1}: duplicate of line {seen[key]}")
        seen[key] = i + 1
        out.append(o)
    return out


def observations_from_fill_monitor(path: Path) -> list[Observation]:
    """Adapter for NEW2's maker-fill monitor ledger (``data/fill_ledger.jsonl``).
    Only ``placed`` events carry an observed quote (mid + spread at quote
    time); the monitor's "fills" are virtual (a public trade crossed a
    virtual limit), so no fill price / fee is taken from it."""
    out = []
    for r in iter_jsonl(Path(path)):
        if r.get("ev") != "placed":
            continue
        mid, spread_bp = float(r["mid_at_quote"]), float(r["spread_bp"])
        half = mid * spread_bp / 1e4 / 2.0
        out.append(Observation(
            ts_ms=int(r["placed_ms"]), symbol=r["symbol"], side=1 if r["side"] == "long" else -1,
            order_type="maker", notional=1.0, ref_price=mid, status="EXPIRED",
            source_kind="paper_fill_monitor", bid=mid - half, ask=mid + half))
    for o in out:
        o.validate()
    return out


# ---------------------------------------------------------------------------
# statistics helpers (stdlib only)
# ---------------------------------------------------------------------------

def _quantile(xs: Sequence[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    pos = q * (len(s) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def error_summary(errors: Sequence[float]) -> dict:
    """errors = observed - predicted (bp).  Positive = the model UNDER-estimates."""
    n = len(errors)
    if not n:
        return {"n": 0}
    return {"n": n, "mean_bp": sum(errors) / n, "median_bp": _quantile(errors, 0.5),
            "p90_bp": _quantile(errors, 0.90), "p95_bp": _quantile(errors, 0.95),
            "p99_bp": _quantile(errors, 0.99), "max_bp": max(errors),
            "underestimated_share": sum(1 for e in errors if e > 0) / n}


def _buckets(obs: Sequence[Observation], errs: Sequence[float], key, edges: Sequence[float]) -> list[dict]:
    out = []
    vals = [key(o) for o in obs]
    for lo, hi in zip([-math.inf, *edges], [*edges, math.inf]):
        es = [e for v, e in zip(vals, errs) if v is not None and lo <= v < hi]
        if es:
            out.append({"lo": lo if math.isfinite(lo) else None, "hi": hi if math.isfinite(hi) else None,
                        **error_summary(es)})
    return out


# ---------------------------------------------------------------------------
# calibration (fit) and verification (out-of-sample)
# ---------------------------------------------------------------------------

def fit_parameters(obs: Sequence[Observation], base_params: Mapping[str, float]) -> dict:
    """Per-component estimates from the CALIBRATION part only.  A component
    the data cannot identify keeps the base value and is marked unfitted."""
    fitted, how = dict(base_params), {}
    fills = [o for o in obs if o.observed_cost_bp() is not None]
    fee_rates = [o.fee / o.filled_notional for o in fills if o.filled_notional]
    if fee_rates:
        fitted["fee_rate"] = _quantile(fee_rates, 0.5)
        how["fee_rate"] = {"method": "median charged fee / filled notional", "n": len(fee_rates)}
    hs = [o.half_spread_frac() for o in obs if o.half_spread_frac() is not None]
    if hs:
        fitted["half_spread"] = _quantile(hs, 0.5)
        how["half_spread"] = {"method": "median quoted (ask-bid)/2/mid", "n": len(hs)}
    # impact: least squares through the origin of the residual slippage on sqrt(participation)
    pts = []
    for o in fills:
        p = o.participation()
        if p is None:
            continue
        resid = o.observed_cost_bp() / 1e4 - fitted["fee_rate"] - fitted["half_spread"]
        pts.append((math.sqrt(p), resid))
    if len(pts) >= 3 and sum(x * x for x, _ in pts) > 0:
        k = sum(x * y for x, y in pts) / sum(x * x for x, _ in pts)
        fitted["impact_coefficient"] = max(0.0, k)
        how["impact_coefficient"] = {"method": "OLS through origin on sqrt(participation)",
                                     "n": len(pts), "raw": k}
    return {"params": fitted, "fitted": how}


def predicted_bp(cost_ctx, o: Observation) -> Optional[float]:
    if o.bar_traded_value is None:
        return None
    return cost_ctx.quote(o.notional, o.bar_traded_value) * 1e4


def load_criteria(path: Optional[Path] = None) -> tuple[dict, str]:
    p = Path(path or DEFAULT_CRITERIA)
    raw = p.read_bytes()
    c = json.loads(raw)
    if c.get("schema") != "cost_verification_criteria_v1":
        raise CalibrationError(f"{p}: not a cost_verification_criteria_v1 file")
    return c, hashlib.sha256(raw).hexdigest()[:16]


def calibrate_and_verify(obs: Sequence[Observation], base_ctx, *, required_symbols: Sequence[str],
                         order_value: float, criteria_path: Optional[Path] = None,
                         source_ref: str = "") -> dict:
    """Fit on the first ``calibration_fraction`` (by time), verify the fitted
    context on the rest.  Returns an UNSEALED decision record (see :func:`seal`)."""
    from costs_bridge import COST_MODEL_SOURCE_SHA256, CostContext, CostError, REQUIRED_COST_PARAMS
    crit, crit_hash = load_criteria(criteria_path)
    for o in obs:
        o.validate()
    obs = sorted(obs, key=lambda o: o.ts_ms)
    kinds = sorted({o.source_kind for o in obs})
    components = {c for k in kinds for c in SOURCE_COMPONENTS[k]}
    reasons: list[str] = []
    rec: dict = {"schema": RECORD_SCHEMA, "calibration_version": COST_CALIBRATION_VERSION,
                 "criteria_hash": crit_hash, "source_kinds": kinds, "source_ref": source_ref,
                 "data_sha256": data_sha256(obs), "n_observations": len(obs),
                 "cost_source_sha256": COST_MODEL_SOURCE_SHA256,
                 "code_hash": code_identity(files=("cost_calibration.py", "costs_bridge.py"))["code_hash"],
                 "base_params": dict(base_ctx.params)}
    if not obs:
        rec.update({"status": "BLOCKED_EXTERNAL_EVIDENCE",
                    "reasons": ["no execution observations supplied"]})
        return rec
    missing = [c for c in MODEL_COMPONENTS if c not in components]
    if missing:
        reasons.append(f"components not verifiable from {kinds}: {missing} "
                       "(account execution reports with fees are required)")
    cut = int(len(obs) * crit["calibration_fraction"])
    cal, ver = obs[:cut], obs[cut:]
    fit = fit_parameters(cal, base_ctx.params)
    rec["fit"] = fit
    rec["period"] = {"calibration": [cal[0].ts_ms, cal[-1].ts_ms] if cal else None,
                     "verification": [ver[0].ts_ms, ver[-1].ts_ms] if ver else None}
    params = {k: fit["params"][k] for k in REQUIRED_COST_PARAMS}
    try:
        ctx = CostContext.from_params(params, version="calibration-candidate", verified=False)
    except CostError as e:
        rec.update({"status": "NOT_VERIFIED", "reasons": reasons + [f"fitted parameters invalid: {e}"]})
        return rec
    rec["params"] = params
    # spread comparison is possible for every quote-bearing source
    hs_ver = [o.half_spread_frac() * 1e4 for o in ver if o.half_spread_frac() is not None]
    rec["spread_check"] = {"observed_half_spread_bp": error_summary(hs_ver) if hs_ver else {"n": 0},
                           "model_half_spread_bp": params["half_spread"] * 1e4,
                           "base_half_spread_bp": base_ctx.params["half_spread"] * 1e4}
    # out-of-sample execution cost errors
    errs, used, failures = [], [], 0
    for o in ver:
        oc = o.observed_cost_bp()
        if oc is None:
            continue
        try:
            pb = predicted_bp(ctx, o)
        except CostError:
            failures += 1
            continue
        if pb is None:
            continue
        errs.append(oc - pb)
        used.append(o)
    rec["verification_errors"] = error_summary(errs)
    rec["by_participation"] = _buckets(used, errs, Observation.participation, crit["participation_edges"])
    rec["by_volatility"] = _buckets(used, errs, lambda o: o.bar_volatility, crit["volatility_edges"])
    rec["by_hour_utc"] = _buckets(used, errs, lambda o: (o.ts_ms // 3_600_000) % 24, [6, 12, 18])
    rec["model_refusals_in_verification"] = failures
    # fill / reject rates are properties of real orders: quote snapshots and
    # virtual monitor orders are not orders and must not dilute them
    orders = [o for o in obs if o.source_kind == "exchange_fills"]
    n_ord = len(orders)
    rec["fill_rates"] = {s: (sum(1 for o in orders if o.status == s) / n_ord if n_ord else None)
                         for s in FILL_STATUSES}
    rec["n_orders"] = n_ord
    # coverage = what the OUT-OF-SAMPLE execution evidence covers (not quotes,
    # not a symbol seen once): a symbol is in scope only with enough fills
    min_fills = crit["min_verification_fills_per_symbol"]
    per_sym = {s: sum(1 for o in used if o.symbol == s) for s in sorted({o.symbol for o in used})}
    syms = sorted(s for s, k in per_sym.items() if k >= min_fills)
    lo, hi = ((min(o.notional for o in used), max(o.notional for o in used)) if used
              else (math.inf, -math.inf))
    rec["scope"] = {"symbols": syms, "notional_range": [lo, hi] if used else None,
                    "order_types": sorted({o.order_type for o in used}),
                    "verification_fills_per_symbol": per_sym}
    for s in required_symbols:
        n_s = per_sym.get(s, 0)
        if n_s < min_fills:
            reasons.append(f"{s}: {n_s} verification fills < {min_fills}")
    if not (lo <= order_value <= hi):
        reasons.append(f"order value {order_value} outside the verified notional range "
                       f"{[lo, hi] if used else 'none'}")
    ve = rec["verification_errors"]
    if ve.get("n"):
        if ve["p90_bp"] > crit["max_p90_underestimate_bp"]:
            reasons.append(f"p90 underestimate {ve['p90_bp']:.3f}bp > {crit['max_p90_underestimate_bp']}bp")
        if ve["mean_bp"] > crit["max_mean_underestimate_bp"]:
            reasons.append(f"mean underestimate {ve['mean_bp']:.3f}bp > {crit['max_mean_underestimate_bp']}bp")
    if n_ord and rec["fill_rates"]["REJECTED"] > crit["max_reject_rate"]:
        reasons.append(f"reject rate {rec['fill_rates']['REJECTED']:.3f} > {crit['max_reject_rate']}")
    if missing:
        status = "BLOCKED_EXTERNAL_EVIDENCE"
    else:
        status = "VERIFIED" if not reasons else "NOT_VERIFIED"
    rec.update({"status": status, "reasons": reasons})
    return rec


def record_hash(rec: Mapping) -> str:
    body = {k: v for k, v in rec.items() if k not in ("record_id", "sealed_at")}
    return "CV-" + hashlib.sha256(canonical_json(body).encode()).hexdigest()[:24]


def seal(rec: Mapping) -> dict:
    out = dict(rec)
    out["record_id"] = record_hash(out)
    out["sealed_at"] = iso_now()
    return out


def append_record(ledger: Path, rec: Mapping) -> dict:
    sealed = seal(rec) if "record_id" not in rec else dict(rec)
    if sealed["record_id"] != record_hash(sealed):
        raise CalibrationError("record content does not match its id")
    append_jsonl(Path(ledger), sealed)
    return sealed


def check_record(rec: Mapping) -> None:
    """Raise unless ``rec`` is an intact VERIFIED record for the current cost
    implementation.  Used by ``CostContext.from_verification``."""
    from costs_bridge import COST_MODEL_SOURCE_SHA256
    if rec.get("schema") != RECORD_SCHEMA:
        raise CalibrationError("not a cost_verification_v1 record")
    if rec.get("record_id") != record_hash(rec):
        raise CalibrationError("cost verification record was altered (hash mismatch)")
    if rec.get("status") != "VERIFIED":
        raise CalibrationError(f"cost verification status is {rec.get('status')}, not VERIFIED")
    if rec.get("reasons"):
        raise CalibrationError(f"VERIFIED record carries failure reasons {rec['reasons']}")
    if not (rec.get("scope") or {}).get("symbols") or not (rec.get("scope") or {}).get("notional_range"):
        raise CalibrationError("VERIFIED record without a verified scope (symbols, notional range)")
    if rec.get("cost_source_sha256") != COST_MODEL_SOURCE_SHA256:
        raise CalibrationError("verification was made against a different cost-model source")
    if "exchange_fills" not in rec.get("source_kinds", []):
        raise CalibrationError("only account execution reports can verify the cost model")


def records(ledger: Path) -> list[dict]:
    return list(iter_jsonl(Path(ledger)))


def main(argv: Optional[Iterable[str]] = None) -> int:  # pragma: no cover - CLI
    import argparse
    from costs_bridge import CostContext
    ap = argparse.ArgumentParser(description="calibrate / verify the cost model on observed executions")
    ap.add_argument("--observations", help="JSONL of Observation rows")
    ap.add_argument("--fill-monitor", help="NEW2 maker-fill monitor ledger (quotes only)")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--order-value", type=float, default=10_000.0)
    ap.add_argument("--ledger", help="append the sealed decision record here")
    a = ap.parse_args(list(argv) if argv is not None else None)
    obs: list[Observation] = []
    ref = []
    if a.observations:
        obs += load_observations(Path(a.observations))
        ref.append(a.observations)
    if a.fill_monitor:
        obs += observations_from_fill_monitor(Path(a.fill_monitor))
        ref.append(a.fill_monitor)
    rec = calibrate_and_verify(obs, CostContext.futures_default(), required_symbols=a.symbols,
                               order_value=a.order_value, source_ref=";".join(ref))
    rec = seal(rec)
    if a.ledger:
        append_record(Path(a.ledger), rec)
    print(json.dumps(rec, indent=1, sort_keys=True, default=str))
    return 0 if rec["status"] == "VERIFIED" else (4 if rec["status"] == "BLOCKED_EXTERNAL_EVIDENCE" else 2)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
