"""Estimate spread and book impact from PUBLIC order-book snapshots (ADR 0007).

Input: the JSONL files written by ``public_market_collect.py`` (any
machine).  No network.  For every valid snapshot it walks the book for a
set of order sizes on both sides and measures the per-side cost against
the mid price; then it fits the candle model's impact coefficient
(``excess_over_half_spread = k * sqrt(order / last closed 15m quote volume)``).

The result is a sealed ``PE-…`` record with status

- ``ESTIMATED_FROM_PUBLIC_DATA`` — enough clean data under the preregistered
  criteria (``config/cost_public_estimate_v1.json``); usable by the engine
  through ``NEW2_COST_PROFILE=public_estimate:<record.json>``, but it is NOT a
  verification: a public book is not a fill, so the strategy stays
  research-only (``PAPER_TEST_READY`` needs ``cost_calibration`` VERIFIED);
- ``INSUFFICIENT_PUBLIC_DATA`` — written for the record, refused by the engine.

Conservative choices, all fixed before data: the 90th percentile of the half
spread, plus the median mid move over one collection interval as a latency
allowance; the larger of the least-squares and the 90th-percentile impact
coefficient; the maximum over symbols (one parameter set prices every
symbol).  Fees cannot be seen in public data and stay the public-schedule
assumption of the ASSUMED profile.

    python public_cost_estimate.py --in ../../data/public_market --out estimate.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from portable_util import canonical_json, iso_now

RECORD_SCHEMA = "cost_public_estimate_v1"
CRITERIA_SCHEMA = "cost_public_estimate_criteria_v1"
ESTIMATED = "ESTIMATED_FROM_PUBLIC_DATA"
INSUFFICIENT = "INSUFFICIENT_PUBLIC_DATA"
DEFAULT_CRITERIA = Path(__file__).resolve().parent / "config" / "cost_public_estimate_v1.json"
LIMITATIONS = [
    "public order book snapshots, not executions: hidden liquidity, queue position, "
    "latency and adverse selection are only approximated",
    "fees are the public-schedule assumption (not observable in public data)",
    "estimated over the collection period only; another regime can be costlier",
]


class EstimateError(ValueError):
    pass


def _fee_profile(name: str) -> tuple[dict, str]:
    """The public-schedule fee assumption (fees are not observable in public data).
    Read directly so this script needs no NEW2 checkout; the engine re-validates
    every parameter when it loads the record."""
    raw = (DEFAULT_CRITERIA.parent / f"cost_{name}.json").read_bytes()
    prof = json.loads(raw)
    if prof.get("schema") != "cost_assumed_profile_v1" or prof.get("status") != "ASSUMED_COST_MODEL":
        raise EstimateError(f"fee profile {name!r} is not an ASSUMED cost profile")
    fee = prof.get("fee_rate")
    if isinstance(fee, bool) or not isinstance(fee, (int, float)) or not math.isfinite(fee) or fee < 0:
        raise EstimateError(f"fee profile {name!r}: bad fee_rate {fee!r}")
    return prof, hashlib.sha256(raw).hexdigest()


def load_criteria(path: Optional[Path] = None) -> tuple[dict, str]:
    p = Path(path or DEFAULT_CRITERIA)
    raw = p.read_bytes()
    c = json.loads(raw.decode("utf-8"))
    if c.get("schema") != CRITERIA_SCHEMA:
        raise EstimateError(f"{p}: not a {CRITERIA_SCHEMA} file")
    return c, hashlib.sha256(raw).hexdigest()


def quantile(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return math.nan
    pos = q * (len(s) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _book(side) -> list[tuple[float, float]]:
    return [(float(p), float(q)) for p, q in side]


def valid_snapshot(rec: Mapping) -> Optional[str]:
    """None if usable, else the reason."""
    d, k = rec.get("depth"), rec.get("kline_closed")
    if not d or not k:
        return "missing depth or closed kline"
    try:
        bids, asks = _book(d["bids"]), _book(d["asks"])
        qv = float(k["quote_volume"])
    except (KeyError, TypeError, ValueError):
        return "unparseable"
    if not bids or not asks:
        return "empty book"
    if any(p <= 0 or q <= 0 for p, q in bids + asks) or qv <= 0:
        return "non-positive price, size or volume"
    if any(bids[i][0] <= bids[i + 1][0] for i in range(len(bids) - 1)) or \
       any(asks[i][0] >= asks[i + 1][0] for i in range(len(asks) - 1)):
        return "book not sorted"
    if bids[0][0] >= asks[0][0]:
        return "crossed book"
    return None


def walk(levels: Sequence[tuple[float, float]], notional: float) -> Optional[float]:
    """Average fill price for ``notional`` quote units, or None if the book is too thin."""
    left, qty = notional, 0.0
    for p, q in levels:
        take = min(left, p * q)
        qty += take / p
        left -= take
        if left <= 1e-9:
            return notional / qty
    return None


def per_symbol(lines: Iterable[Mapping], crit: Mapping) -> dict:
    ok, invalid, failed, reasons = [], 0, 0, {}
    for r in lines:
        if r.get("status") == "BAD_LINE":
            invalid += 1
            reasons["not valid JSON"] = reasons.get("not valid JSON", 0) + 1
            continue
        if r.get("status") != "OK":
            failed += 1
            continue
        why = valid_snapshot(r)
        if why:
            invalid += 1
            reasons[why] = reasons.get(why, 0) + 1
            continue
        ok.append(r)
    ok.sort(key=lambda r: r["fetched_at_ms"])
    n = len(ok)
    out = {"snapshots_ok": n, "snapshots_invalid": invalid, "fetch_failed": failed,
           "invalid_reasons": reasons}
    if n < 2:
        return {**out, "hours": 0.0, "usable": False}
    ts = [r["fetched_at_ms"] for r in ok]
    span = (ts[-1] - ts[0]) / 3.6e6
    diffs = [b - a for a, b in zip(ts, ts[1:])]
    gap_ms = crit["max_gap_minutes"] * 60_000
    gaps = [d for d in diffs if d > gap_ms]
    med_dt = quantile(diffs, 0.5)
    hs, moves, ratios, fit_x, fit_y, thin = [], [], [], [], [], 0
    prev_mid, prev_t = None, None
    by_size = {str(int(s)): [] for s in crit["notionals"]}
    for r in ok:
        bids, asks = _book(r["depth"]["bids"]), _book(r["depth"]["asks"])
        mid = (bids[0][0] + asks[0][0]) / 2
        h = (asks[0][0] - bids[0][0]) / (2 * mid)
        hs.append(h)
        if prev_mid is not None and r["fetched_at_ms"] - prev_t <= 2 * med_dt:
            moves.append(abs(mid / prev_mid - 1))
        prev_mid, prev_t = mid, r["fetched_at_ms"]
        qv = float(r["kline_closed"]["quote_volume"])
        for size in crit["notionals"]:
            b, s = walk(asks, size), walk(bids, size)
            if b is None or s is None:
                thin += 1
                continue
            cost = ((b / mid - 1) + (1 - s / mid)) / 2
            by_size[str(int(size))].append(cost)
            part = size / qv
            if size >= crit["impact_fit_min_notional"] and part > 0:
                ex = max(cost - h, 0.0)
                x = math.sqrt(part)
                fit_x.append(x)
                fit_y.append(ex)
                ratios.append(ex / x)
    sxx = sum(x * x for x in fit_x)
    k_ols = sum(x * y for x, y in zip(fit_x, fit_y)) / sxx if sxx > 0 else math.nan
    k_q = quantile(ratios, crit["impact_ratio_quantile"])
    lat = quantile(moves, crit["latency_move_quantile"]) if moves else math.nan
    hs_q = quantile(hs, crit["half_spread_quantile"])
    usable_reasons = []
    if span < crit["min_hours_per_symbol"]:
        usable_reasons.append(f"{span:.1f}h < {crit['min_hours_per_symbol']}h")
    if n < crit["min_snapshots_per_symbol"]:
        usable_reasons.append(f"{n} snapshots < {crit['min_snapshots_per_symbol']}")
    if span > 0 and sum(gaps) / 3.6e6 / span > crit["max_gap_share"]:
        usable_reasons.append("too much time in collection gaps")
    if invalid / max(1, n + invalid) > crit["max_invalid_share"]:
        usable_reasons.append("too many invalid snapshots")
    if not fit_x or not math.isfinite(k_ols):
        usable_reasons.append("no observation for the impact fit (book too thin or sizes too small)")
    return {**out, "first_ms": ts[0], "last_ms": ts[-1], "hours": round(span, 3),
            "gaps": len(gaps), "median_interval_s": med_dt / 1000,
            "half_spread_bp": {"median": quantile(hs, 0.5) * 1e4, "q": hs_q * 1e4},
            "latency_move_bp": lat * 1e4 if math.isfinite(lat) else None,
            "cost_per_side_bp_by_notional": {k: {"median": quantile(v, 0.5) * 1e4,
                                                 "p90": quantile(v, 0.9) * 1e4, "n": len(v)}
                                             for k, v in by_size.items() if v},
            "book_too_thin": thin, "impact_fit_n": len(fit_x),
            "impact_k_ols": k_ols, "impact_k_quantile": k_q,
            "half_spread_param": (hs_q + (lat if math.isfinite(lat) else 0.0)),
            "impact_param": max(k_ols, k_q) if fit_x else math.nan,
            "usable": not usable_reasons, "not_usable_because": usable_reasons}


def record_hash(rec: Mapping) -> str:
    body = {k: v for k, v in rec.items() if k not in ("record_id", "sealed_at")}
    return "PE-" + hashlib.sha256(canonical_json(body).encode()).hexdigest()[:24]


def read_lines(paths: Iterable[Path]) -> dict[str, list[dict]]:
    """Group snapshot lines by symbol.  A line that is not valid JSON (for
    example the torn last line after a crash) is never skipped silently: it
    becomes a ``BAD_LINE`` entry for the symbol named by its folder and is
    counted as an invalid snapshot."""
    out: dict[str, list[dict]] = {}
    for p in paths:
        p = Path(p)
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if not isinstance(r, dict):
                    raise ValueError("not an object")
            except ValueError:
                r = {"status": "BAD_LINE", "symbol": p.parent.name}
            out.setdefault(r.get("symbol", p.parent.name), []).append(r)
    return out


def estimate(in_dir: Path, *, criteria_path: Optional[Path] = None) -> dict:
    crit, crit_sha = load_criteria(criteria_path)
    in_dir = Path(in_dir)
    files = sorted(in_dir.rglob("public_*.jsonl"))
    lines = read_lines(files)
    per = {s: per_symbol(lines.get(s, []), crit) for s in crit["required_symbols"]}
    reasons = [f"{s}: {w}" for s, d in per.items() for w in d.get("not_usable_because", ["no data"])]
    prof, prof_sha = _fee_profile(crit["fee_profile"])
    ok = all(d.get("usable") for d in per.values())
    params = None
    if ok:
        params = {"fee_rate": float(prof["fee_rate"]),
                  "half_spread": max(d["half_spread_param"] for d in per.values()),
                  "impact_coefficient": max(d["impact_param"] for d in per.values()),
                  "max_participation": float(crit["max_participation"])}
    rec = {"schema": RECORD_SCHEMA, "status": ESTIMATED if ok else INSUFFICIENT,
           "params": params, "per_symbol": per, "reasons": reasons,
           "fee_basis": {"profile": prof["profile_id"], "profile_sha256": prof_sha,
                         "fee_source": prof["fee_source"], "observed": False},
           "criteria": crit, "criteria_sha256": crit_sha,
           "source_files": [{"path": str(p.relative_to(in_dir)),
                             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
           "limitations": LIMITATIONS,
           "claim": "추정 근거 있음(ESTIMATED_FROM_PUBLIC_DATA) — not verified, never promotable"}
    rec["record_id"] = record_hash(rec)
    rec["sealed_at"] = iso_now()
    return rec


def check_estimate(rec: Mapping) -> None:
    """Raise unless ``rec`` is an intact, usable public estimate."""
    if rec.get("schema") != RECORD_SCHEMA:
        raise EstimateError(f"not a {RECORD_SCHEMA} record")
    if rec.get("record_id") != record_hash(rec):
        raise EstimateError("public estimate record was altered (hash mismatch)")
    if rec.get("status") != ESTIMATED:
        raise EstimateError(f"public estimate status is {rec.get('status')} — not usable")
    p = rec.get("params") or {}
    for k in ("fee_rate", "half_spread", "impact_coefficient", "max_participation"):
        v = p.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise EstimateError(f"public estimate parameter {k}={v!r} unusable")


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="in_dir", required=True)
    ap.add_argument("--out", required=True, help="estimate record (never overwritten)")
    ap.add_argument("--criteria")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        print(f"REFUSED: {out} exists — records are never overwritten; choose a new name", file=sys.stderr)
        return 1
    try:
        rec = estimate(Path(a.in_dir), criteria_path=Path(a.criteria) if a.criteria else None)
    except (EstimateError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    out.write_text(json.dumps(rec, indent=1, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({"status": rec["status"], "record_id": rec["record_id"], "params": rec["params"],
                      "reasons": rec["reasons"]}, indent=1))
    return 0 if rec["status"] == ESTIMATED else 4


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
