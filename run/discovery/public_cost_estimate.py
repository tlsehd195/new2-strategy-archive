"""Estimate spread and book impact from PUBLIC order-book snapshots (ADR 0007).

Input: the JSONL files written by ``public_market_collect.py`` (any
machine).  No network.  For every valid snapshot it walks the book for a
set of order sizes on both sides and measures the per-side cost against
the mid price; then it fits the candle model's impact coefficient
(``excess_over_half_spread = k * sqrt(order / last closed 15m quote volume)``).

The result is a sealed ``PE-…`` record with status

- ``ESTIMATED_FROM_PUBLIC_DATA`` — enough clean data under the preregistered
  criteria (``config/cost_public_estimate_v2.json``); usable by the engine
  through ``NEW2_COST_PROFILE=public_estimate:<record.json>``, but it is NOT a
  verification: a public book is not a fill, so the strategy stays
  research-only (``PAPER_TEST_READY`` needs ``cost_calibration`` VERIFIED);
- ``INSUFFICIENT_PUBLIC_DATA`` — written for the record, refused by the engine.

Conservative choices, all fixed before data (``config/cost_public_estimate_v2.json``,
which supersedes v1 with a stricter estimator): the 90th percentile of the
half spread, plus the median mid move over one collection interval as a
latency allowance; per snapshot the WORSE of the buy walk (asks) and the sell
walk (bids); the larger of the least-squares and the 90th-percentile impact
coefficient; the maximum over symbols (one parameter set prices every
symbol).  Fees cannot be seen in public data and stay the public-schedule
assumption of the ASSUMED profile.

Units.  Prices in quote currency (USDT); order sizes are quote notionals;
costs are fractions of the traded notional per side (x 1e4 = bp); the
participation is ``notional / quote volume of the last CLOSED 15m bar`` —
the same bar the engine prices against (``evaluator``), so the fitted
coefficient plugs into ``fee + half_spread + k*sqrt(participation)``.  The
spread is charged once: impact is the walk cost IN EXCESS of the snapshot's
own half spread.

Refused, never repaired: missing / unparseable / non-finite numbers, crossed
or unsorted books, stale books or klines, duplicate timestamps, lines whose
symbol does not match their folder, torn lines (all counted as invalid);
files missing from or not matching ``collect_manifest.json`` (the whole
estimate becomes INSUFFICIENT); a notional for which the visible book was
too thin in more than ``max_thin_share`` of the snapshots (excluded from the
fit as a whole — keeping only its deep-book snapshots would bias the
coefficient down).

    python public_cost_estimate.py --in ../../data/public_market --out estimate.json
    python public_cost_estimate.py --verify estimate.json --in ../../data/public_market
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

RECORD_SCHEMA = "cost_public_estimate_v2"
CRITERIA_SCHEMA = "cost_public_estimate_criteria_v2"
#: v1 criteria (averaged buy/sell, thin books silently dropped) are superseded
SUPERSEDED_CRITERIA = {"cost_public_estimate_criteria_v1": "cost_public_estimate_v2.json"}
ESTIMATOR_VERSION = "public-estimate-v2"
ESTIMATED = "ESTIMATED_FROM_PUBLIC_DATA"
INSUFFICIENT = "INSUFFICIENT_PUBLIC_DATA"
DEFAULT_CRITERIA = Path(__file__).resolve().parent / "config" / "cost_public_estimate_v2.json"
MANIFEST = "collect_manifest.json"
CRITERIA_KEYS = ("required_symbols", "min_hours_per_symbol", "min_snapshots_per_symbol",
                 "max_gap_minutes", "max_gap_share", "max_invalid_share", "notionals",
                 "impact_fit_min_notional", "half_spread_quantile", "impact_ratio_quantile",
                 "latency_move_quantile", "fee_profile", "max_participation", "side_aggregation",
                 "max_thin_share", "max_book_age_ms", "max_kline_age_ms", "require_manifest")
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
    if c.get("schema") in SUPERSEDED_CRITERIA:
        raise EstimateError(f"{p}: {c['schema']} is superseded by "
                            f"{SUPERSEDED_CRITERIA[c['schema']]} (stricter estimator, ADR 0008)")
    if c.get("schema") != CRITERIA_SCHEMA:
        raise EstimateError(f"{p}: not a {CRITERIA_SCHEMA} file")
    missing = [k for k in CRITERIA_KEYS if k not in c]
    if missing:
        raise EstimateError(f"{p}: missing criteria {missing}")
    if c["side_aggregation"] != "max":
        raise EstimateError(f"{p}: side_aggregation must be 'max' (the worse side)")
    if not c["notionals"] or any(not _finite_pos(x) for x in c["notionals"]):
        raise EstimateError(f"{p}: notionals must be positive numbers")
    if not any(x >= c["impact_fit_min_notional"] for x in c["notionals"]):
        raise EstimateError(f"{p}: no notional reaches impact_fit_min_notional")
    for k in ("half_spread_quantile", "impact_ratio_quantile", "latency_move_quantile",
              "max_gap_share", "max_invalid_share", "max_thin_share", "max_participation"):
        if not (isinstance(c[k], (int, float)) and not isinstance(c[k], bool) and 0 <= c[k] <= 1):
            raise EstimateError(f"{p}: {k} must be in [0, 1]")
    return c, hashlib.sha256(raw).hexdigest()


def _finite_pos(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def quantile(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return math.nan
    pos = q * (len(s) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _book(side) -> list[tuple[float, float]]:
    return [(float(p), float(q)) for p, q in side]


def valid_snapshot(rec: Mapping, crit: Optional[Mapping] = None,
                   symbol: Optional[str] = None) -> Optional[str]:
    """None if usable, else the reason.  With ``crit`` the book and kline
    timestamps are checked for staleness; with ``symbol`` the line must belong
    to that symbol (its folder)."""
    d, k = rec.get("depth"), rec.get("kline_closed")
    if not d or not k:
        return "missing depth or closed kline"
    if symbol is not None and rec.get("symbol") != symbol:
        return "symbol does not match its folder"
    t = rec.get("fetched_at_ms")
    if isinstance(t, bool) or not isinstance(t, int) or t <= 0:
        return "missing or bad fetched_at_ms"
    try:
        bids, asks = _book(d["bids"]), _book(d["asks"])
        qv = float(k["quote_volume"])
    except (KeyError, TypeError, ValueError):
        return "unparseable"
    if not bids or not asks:
        return "empty book"
    if not all(math.isfinite(x) for lv in bids + asks for x in lv) or not math.isfinite(qv):
        return "non-finite price, size or volume"
    if any(p <= 0 or q <= 0 for p, q in bids + asks) or qv <= 0:
        return "non-positive price, size or volume"
    if crit is not None:
        e = d.get("E")
        if isinstance(e, bool) or not isinstance(e, int):
            return "book without event time"
        if abs(t - e) > crit["max_book_age_ms"]:
            return "stale book"
        ct = k.get("close_time")
        if isinstance(ct, bool) or not isinstance(ct, int):
            return "kline without close time"
        if not (ct < t and t - ct <= crit["max_kline_age_ms"]):
            return "stale or unfinished kline"
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


def per_symbol(lines: Iterable[Mapping], crit: Mapping, symbol: Optional[str] = None) -> dict:
    ok, invalid, failed, reasons = [], 0, 0, {}

    def bad(why: str) -> None:
        nonlocal invalid
        invalid += 1
        reasons[why] = reasons.get(why, 0) + 1

    seen_t: set = set()
    for r in lines:
        if r.get("status") == "BAD_LINE":
            bad("not valid JSON")
            continue
        if r.get("status") != "OK":
            failed += 1
            continue
        why = valid_snapshot(r, crit, symbol)
        if why:
            bad(why)
            continue
        if r["fetched_at_ms"] in seen_t:
            bad("duplicate snapshot time")
            continue
        seen_t.add(r["fetched_at_ms"])
        ok.append(r)
    ok.sort(key=lambda r: r["fetched_at_ms"])
    n = len(ok)
    out = {"snapshots_ok": n, "snapshots_invalid": invalid, "fetch_failed": failed,
           "invalid_reasons": reasons}
    if n < 2:
        return {**out, "hours": 0.0, "usable": False,
                "not_usable_because": [f"{n} valid snapshots"]}
    ts = [r["fetched_at_ms"] for r in ok]
    span = (ts[-1] - ts[0]) / 3.6e6
    diffs = [b - a for a, b in zip(ts, ts[1:])]
    gap_ms = crit["max_gap_minutes"] * 60_000
    gaps = [d for d in diffs if d > gap_ms]
    med_dt = quantile(diffs, 0.5)
    sizes = [str(int(s)) for s in crit["notionals"]]
    hs, moves = [], []
    walks = {k: [] for k in sizes}           # (part, h, buy, sell) per valid walk
    thin = {k: 0 for k in sizes}
    prev_mid, prev_t = None, None
    for r in ok:
        bids, asks = _book(r["depth"]["bids"]), _book(r["depth"]["asks"])
        mid = (bids[0][0] + asks[0][0]) / 2
        h = (asks[0][0] - bids[0][0]) / (2 * mid)
        hs.append(h)
        if prev_mid is not None and r["fetched_at_ms"] - prev_t <= 2 * med_dt:
            moves.append(abs(mid / prev_mid - 1))
        prev_mid, prev_t = mid, r["fetched_at_ms"]
        qv = float(r["kline_closed"]["quote_volume"])
        for size, key in zip(crit["notionals"], sizes):
            b, s_ = walk(asks, size), walk(bids, size)
            if b is None or s_ is None:
                thin[key] += 1
                continue
            # per-side cost against the mid: buying lifts the asks (pays above mid),
            # selling hits the bids (receives below mid); both positive
            walks[key].append((size / qv, h, b / mid - 1.0, 1.0 - s_ / mid))
    thin_share = {k: thin[k] / n for k in sizes}
    unsupported = sorted((k for k in sizes if thin_share[k] > crit["max_thin_share"]), key=float)
    fit_x, fit_y, ratios, by_size, side_gap = [], [], [], {}, {}
    for size, key in zip(crit["notionals"], sizes):
        rows = walks[key]
        if not rows:
            continue
        worse = [max(bu, se) for _, _, bu, se in rows]
        by_size[key] = {"median": quantile(worse, 0.5) * 1e4, "p90": quantile(worse, 0.9) * 1e4,
                        "n": len(rows), "thin_share": thin_share[key],
                        "buy_median": quantile([bu for *_, bu, _ in rows], 0.5) * 1e4,
                        "sell_median": quantile([se for *_, se in rows], 0.5) * 1e4}
        side_gap[key] = by_size[key]["buy_median"] - by_size[key]["sell_median"]
        if size < crit["impact_fit_min_notional"] or key in unsupported:
            continue
        for (part, h, bu, se), c in zip(rows, worse):
            ex = max(c - h, 0.0)                 # spread is charged once, by half_spread
            x = math.sqrt(part)
            fit_x.append(x)
            fit_y.append(ex)
            ratios.append(ex / x)
    sxx = sum(x * x for x in fit_x)
    k_ols = sum(x * y for x, y in zip(fit_x, fit_y)) / sxx if sxx > 0 else math.nan
    k_q = quantile(ratios, crit["impact_ratio_quantile"]) if ratios else math.nan
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
    if not math.isfinite(lat):
        usable_reasons.append("no latency allowance (no consecutive snapshots)")
    if not fit_x or not math.isfinite(k_ols) or not math.isfinite(k_q):
        usable_reasons.append("no observation for the impact fit (book too thin at every fit "
                              f"notional: thin shares {thin_share})")
    parts = [x * x for x in fit_x]
    return {**out, "first_ms": ts[0], "last_ms": ts[-1], "hours": round(span, 3),
            "gaps": len(gaps), "median_interval_s": med_dt / 1000,
            "half_spread_bp": {"median": quantile(hs, 0.5) * 1e4, "q": hs_q * 1e4},
            "latency_move_bp": lat * 1e4 if math.isfinite(lat) else None,
            "cost_per_side_bp_by_notional": by_size,
            "buy_minus_sell_median_bp": side_gap,
            "book_too_thin": sum(thin.values()), "thin_share_by_notional": thin_share,
            "unsupported_notionals": unsupported,
            "impact_fit_n": len(fit_x),
            "fit_participation_range": [min(parts), max(parts)] if parts else None,
            "impact_k_ols": k_ols, "impact_k_quantile": k_q,
            "half_spread_param": hs_q + lat if math.isfinite(lat) else None,
            "impact_param": max(k_ols, k_q) if fit_x and math.isfinite(k_ols) and math.isfinite(k_q) else None,
            "usable": not usable_reasons, "not_usable_because": usable_reasons}


def check_manifest(in_dir: Path, files: Sequence[Path]) -> tuple[Optional[str], list[str]]:
    """(manifest sha256, problems).  Every input file must be listed with a
    matching SHA-256; a listed file must exist.  A file changed after the
    collector sealed the manifest (transfer corruption, edits) is refused."""
    mp = Path(in_dir) / MANIFEST
    if not mp.exists():
        return None, [f"{MANIFEST} missing — run public_market_collect.py --manifest-only "
                      "on the collecting machine (no network) to seal the files"]
    raw = mp.read_bytes()
    try:
        man = json.loads(raw.decode("utf-8"))
        listed = {f["path"].replace("\\", "/"): f["sha256"] for f in man["files"]}
    except (ValueError, KeyError, TypeError) as e:
        return hashlib.sha256(raw).hexdigest(), [f"{MANIFEST} unreadable: {e}"]
    problems = []
    have = {p.relative_to(in_dir).as_posix(): p for p in files}
    for rel, p in sorted(have.items()):
        if rel not in listed:
            problems.append(f"{rel}: not in {MANIFEST}")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != listed[rel]:
            problems.append(f"{rel}: SHA-256 differs from {MANIFEST}")
    problems += [f"{rel}: listed in {MANIFEST} but missing" for rel in sorted(set(listed) - set(have))]
    return hashlib.sha256(raw).hexdigest(), problems


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


def read_symbol_lines(paths: Iterable[Path]) -> dict[str, list[dict]]:
    """Lines grouped by the FOLDER they were written to (``<out>/<SYMBOL>/``):
    a line naming another symbol is kept there and later refused."""
    out: dict[str, list[dict]] = {}
    for p in paths:
        p = Path(p)
        for r in read_lines([p]).values():
            out.setdefault(p.parent.name, []).extend(r)
    return out


def estimate(in_dir: Path, *, criteria_path: Optional[Path] = None) -> dict:
    crit, crit_sha = load_criteria(criteria_path)
    in_dir = Path(in_dir)
    files = sorted(in_dir.rglob("public_*.jsonl"))
    lines = read_symbol_lines(files)
    per = {s: per_symbol(lines.get(s, []), crit, s) for s in crit["required_symbols"]}
    reasons = [f"{s}: {w}" for s, d in per.items() for w in d.get("not_usable_because", ["no data"])]
    man_sha, man_problems = check_manifest(in_dir, files)
    if crit["require_manifest"]:
        reasons += [f"input integrity: {x}" for x in man_problems]
    prof, prof_sha = _fee_profile(crit["fee_profile"])
    ok = all(d.get("usable") for d in per.values()) and not (crit["require_manifest"] and man_problems)
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
           "estimator_version": ESTIMATOR_VERSION,
           "estimator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "manifest_sha256": man_sha, "manifest_problems": man_problems,
           "supported_max_notional": (min(max(float(k) for k in d["cost_per_side_bp_by_notional"]
                                              if k not in d["unsupported_notionals"])
                                          for d in per.values()) if ok else None),
           "source_files": [{"path": p.relative_to(in_dir).as_posix(),
                             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
           "limitations": LIMITATIONS,
           "claim": "추정 근거 있음(ESTIMATED_FROM_PUBLIC_DATA) — not verified, never promotable; "
                    "a public book is not a fill"}
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
    if rec.get("reasons"):
        raise EstimateError(f"public estimate carries failure reasons {rec['reasons'][:3]}")
    if (rec.get("criteria") or {}).get("schema") != CRITERIA_SCHEMA:
        raise EstimateError(f"public estimate was made under {(rec.get('criteria') or {}).get('schema')!r}, "
                            f"not {CRITERIA_SCHEMA}")
    if not all((d or {}).get("usable") for d in (rec.get("per_symbol") or {}).values()) \
            or not rec.get("per_symbol"):
        raise EstimateError("public estimate has an unusable symbol")
    if (rec.get("fee_basis") or {}).get("observed") is not False:
        raise EstimateError("public data cannot observe fees: fee_basis.observed must be false")
    p = rec.get("params") or {}
    if set(p) != {"fee_rate", "half_spread", "impact_coefficient", "max_participation"}:
        raise EstimateError(f"public estimate parameters {sorted(p)} are not the cost model's")
    for k in ("fee_rate", "half_spread", "impact_coefficient", "max_participation"):
        v = p.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise EstimateError(f"public estimate parameter {k}={v!r} unusable")
    if not 0 < p["max_participation"] <= 1:
        raise EstimateError("max_participation must be in (0, 1]")


def verify_estimate(rec: Mapping, in_dir: Path, *, rel_tol: float = 1e-9) -> dict:
    """Offline re-check of a sealed estimate against its input folder: the
    record is intact, every source file still has its recorded SHA-256, and
    re-running the estimator with the record's OWN criteria reproduces the
    status and the parameters.  Returns {"ok": bool, "problems": [...]}."""
    problems: list[str] = []
    if rec.get("schema") != RECORD_SCHEMA:
        return {"ok": False, "problems": [f"not a {RECORD_SCHEMA} record"]}
    if rec.get("record_id") != record_hash(rec):
        problems.append("record was altered (hash mismatch)")
    in_dir = Path(in_dir)
    files = sorted(in_dir.rglob("public_*.jsonl"))
    now = {p.relative_to(in_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    then = {f["path"].replace("\\", "/"): f["sha256"] for f in rec.get("source_files") or []}
    for rel in sorted(set(then) | set(now)):
        if rel not in now:
            problems.append(f"{rel}: source file missing")
        elif rel not in then:
            problems.append(f"{rel}: file not part of the estimate")
        elif now[rel] != then[rel]:
            problems.append(f"{rel}: source file changed (SHA-256)")
    if rec.get("estimator_sha256") != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        problems.append("estimator code differs from the one that made the record (re-run compared anyway)")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cp = Path(td) / "criteria.json"
        cp.write_text(json.dumps(rec.get("criteria") or {}), encoding="utf-8")
        try:
            again = estimate(in_dir, criteria_path=cp)
        except (EstimateError, OSError, KeyError) as e:
            return {"ok": False, "problems": problems + [f"re-run failed: {e}"]}
    if again["status"] != rec.get("status"):
        problems.append(f"re-run status {again['status']} != recorded {rec.get('status')}")
    a, b = again.get("params") or {}, rec.get("params") or {}
    for k in sorted(set(a) | set(b)):
        x, y = a.get(k), b.get(k)
        if x is None or y is None or not math.isclose(x, y, rel_tol=rel_tol, abs_tol=1e-15):
            problems.append(f"re-run parameter {k}={x!r} != recorded {y!r}")
    blocking = [x for x in problems if not x.startswith("estimator code differs")]
    return {"ok": not blocking, "problems": problems, "rerun_status": again["status"]}


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="in_dir", required=True)
    ap.add_argument("--out", help="estimate record (never overwritten)")
    ap.add_argument("--verify", help="re-check an existing estimate record against --in (no new record)")
    ap.add_argument("--criteria")
    a = ap.parse_args(argv)
    if a.verify:
        try:
            rec = json.loads(Path(a.verify).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        r = verify_estimate(rec, Path(a.in_dir))
        print(json.dumps(r, indent=1))
        return 0 if r["ok"] else 2
    if not a.out:
        ap.error("--out is required unless --verify is given")
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
