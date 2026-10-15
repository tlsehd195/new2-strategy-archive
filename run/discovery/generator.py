"""Candidate generator — 7 rule-based families (directive §4, §14).

This is a RULE-BASED generator, not an infinite strategy creator and not an
LLM generator (directive §14: 표현을 혼동하지 마 것).  The parameter grids
below are NEW v2 definitions; they intentionally keep the documented family
structure (flow_z / ret_rev / ts_mom / vol_break / tod / fund_z / fng) so
search-space semantics stay comparable, but grid values are new and the
resulting counts are recorded in the NEW ledger only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from engine_lib import canonical_json, normalize_strategy, strategy_id

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
TIMEFRAME = "15m"

_H12 = [8, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 768]
_H5A = [16, 48, 96, 192, 384]
_H5B = [8, 16, 24, 48, 96]


#: family defaults — explicit-vs-omitted must not change identity (§4-3).
#: These are the FIRST grid value of each family, so every generated
#: definition normalizes to the same canonical form whether or not the
#: default value is written out.
FAMILY_DEFAULTS: dict[str, dict] = {
    "flow_z": {"z_window": 24, "entry_z": 1.0, "horizon": 8},
    "ret_rev": {"lookback": 4, "thresh_z": 1.0, "horizon": 8},
    "ts_mom": {"lookback": 32, "thresh_z": 0.5, "horizon": 16},
    "vol_break": {"vol_window": 32, "k": 1.0, "horizon": 8},
    "tod": {"utc_start": 0, "utc_end": 2, "horizon": 16},
    "fund_z": {"funding_window": 30, "thresh_z": 1.5, "horizon": 16},
    "fng": {"threshold": 10, "confirm_days": 1, "horizon": 8},
}


import hashlib as _hashlib

#: pin of FAMILY_DEFAULTS.  Canonical definitions OMIT default-valued
#: parameters, so changing a default would silently change what an existing
#: strategy id computes.  Changing the table requires a new GENERATOR_VERSION
#: (generation.py) and a new pin — the import fails otherwise.
FAMILY_DEFAULTS_SHA256 = "d5881add55a5196c769b0d8ad475cf98c293d5616c20aa95b094d5bd46a1d174"


def _defaults_sha() -> str:
    return _hashlib.sha256(canonical_json(FAMILY_DEFAULTS).encode("utf-8")).hexdigest()


if _defaults_sha() != FAMILY_DEFAULTS_SHA256:  # pragma: no cover - guarded by tests
    raise RuntimeError("FAMILY_DEFAULTS changed without updating FAMILY_DEFAULTS_SHA256 "
                       "(and the generator version): default-omitted strategy ids would "
                       "silently change meaning")


def resolve_params(defn: Mapping) -> dict:
    """Execution parameters of a legacy-family definition: registry defaults
    overlaid with the (possibly default-stripped) canonical params.  EVERY
    consumer (signal builder, auxiliary-series builder, spec translation)
    must use this — never ``defn["params"]`` directly."""
    fam = defn["family"]
    if fam not in FAMILY_DEFAULTS:
        raise KeyError(f"unknown legacy family {fam!r}")
    return {**FAMILY_DEFAULTS[fam], **(defn.get("params") or {})}


def _mk(family: str, params: dict, direction: str, note: str = "") -> dict:
    return {
        "family": family,
        "params": dict(params),
        "_defaults": dict(FAMILY_DEFAULTS[family]),
        "symbols": list(SYMBOLS),
        "timeframe": TIMEFRAME,
        "direction": direction,
        "entry": f"{family}:preregistered_rule",
        "exit": "time_exit",
        "holding": params["horizon"],
        "risk": {"sizing": "vol_inverse", "max_leverage": 1.0},
        "primary_cell": {"symbol": SYMBOLS[0], "horizon": params["horizon"]},
        "_note": note,
    }


def family_flow_z() -> Iterator[dict]:
    # taker-buy share z-score; preregistered contrarian: buy panic → short
    for zw in (24, 48, 96, 192, 384):
        for ez in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
            for h in _H12:
                yield _mk("flow_z", {"z_window": zw, "entry_z": ez, "horizon": h},
                          "short_on_high_buy_share")


def family_ret_rev() -> Iterator[dict]:
    # short-horizon return reversal: down-threshold → long
    for lb in (4, 8, 16, 32):
        for tz in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
            for h in (8, 16, 24, 48, 96):
                yield _mk("ret_rev", {"lookback": lb, "thresh_z": tz, "horizon": h},
                          "long_on_down_move")


def family_ts_mom() -> Iterator[dict]:
    # time-series momentum, both signs by return sign
    for lb in (32, 64, 192):
        for tz in (0.5, 1.0, 2.0, 2.5):
            for h in _H5A:
                yield _mk("ts_mom", {"lookback": lb, "thresh_z": tz, "horizon": h},
                          "follow_sign")


def family_vol_break() -> Iterator[dict]:
    # volatility breakout: close above roll-max + k*sigma → long
    for vw in (32, 64, 96):
        for k in (1.0, 1.5, 2.0, 2.5):
            for h in (8, 16, 48, 96, 192):
                yield _mk("vol_break", {"vol_window": vw, "k": k, "horizon": h},
                          "long_breakout")


def family_tod() -> Iterator[dict]:
    # time-of-day effects on 2-hour UTC windows (12 windows × 5 horizons);
    # direction preregistered by window parity — a v2 design choice, recorded
    for h0 in range(0, 24, 2):
        d = "long" if (h0 // 2) % 2 == 0 else "short"
        for h in _H5A:
            yield _mk("tod", {"utc_start": h0, "utc_end": h0 + 2, "horizon": h},
                      f"{d}_in_session")


def family_fund_z() -> Iterator[dict]:
    # extreme funding z → contrarian (crowd pays → take other side)
    for w in (30, 90, 180):
        for tz in (1.5, 2.0, 3.0):
            for h in _H5A:
                yield _mk("fund_z", {"funding_window": w, "thresh_z": tz, "horizon": h},
                          "short_on_high_funding")


def family_fng() -> Iterator[dict]:
    # Fear&Greed extremes → contrarian; requires the FNG dataset (may be
    # DATA_MISSING at runtime — that path must stay honest).
    for th, d in ((10, "long"), (20, "long"), (80, "short"), (90, "short")):
        for conf in (1, 2, 3):
            for h in (8, 16, 24, 48, 96, 192):
                yield _mk("fng", {"threshold": th, "confirm_days": conf, "horizon": h}, d)
    # two pre-registered extreme-cluster variants (74 total)
    yield _mk("fng", {"threshold": 5, "confirm_days": 2, "horizon": 48}, "long")
    yield _mk("fng", {"threshold": 95, "confirm_days": 2, "horizon": 96}, "short")


FAMILY_GENERATORS = {
    "flow_z": family_flow_z,
    "ret_rev": family_ret_rev,
    "ts_mom": family_ts_mom,
    "vol_break": family_vol_break,
    "tod": family_tod,
    "fund_z": family_fund_z,
    "fng": family_fng,
}

FAMILY_DATA_REQUIREMENTS = {
    "flow_z": ["klines"],
    "ret_rev": ["klines"],
    "ts_mom": ["klines"],
    "vol_break": ["klines"],
    "tod": ["klines"],
    "fund_z": ["klines", "funding"],
    "fng": ["klines", "fng"],
}


def enumerate_candidates() -> tuple[list[tuple[str, dict]], list[dict]]:
    """Full deterministic search space with semantic dedup.

    Returns ([(strategy_id, canonical_def)], dedup_log).  Semantically
    identical definitions (identical canonical JSON after normalization)
    collapse to one candidate and every collapse is logged (directive §4-3):
    over-merging is impossible by construction because the key IS the full
    canonical strategy definition, not a subset of it.
    """
    seen: dict[str, dict] = {}
    out: list[tuple[str, dict]] = []
    dedup_log: list[dict] = []
    for fam, gen in FAMILY_GENERATORS.items():
        for defn in gen():
            canon = normalize_strategy(defn)
            sid = strategy_id(defn)
            if sid in seen:
                dedup_log.append({
                    "family": fam,
                    "strategy_id": sid,
                    "collapsed_definition": canon,
                })
                continue
            seen[sid] = canon
            out.append((sid, canon))
    return out, dedup_log


def split_single_symbol_candidate(defn: Mapping, symbol: str) -> tuple[str, dict]:
    """Directive §8-4: an effect that only exists for one symbol becomes a
    symbol-restricted candidate — a NEW strategy, never a retro-edited one."""
    d = dict(defn)
    d["symbols"] = [symbol]
    # horizon lives top-level (canonical form may drop default params)
    horizon = d.get("holding", (d.get("params") or {}).get("horizon"))
    d["primary_cell"] = {"symbol": symbol, "horizon": horizon}
    d.pop("_note", None)
    return strategy_id(d), normalize_strategy(d)


@dataclass
class SearchSpaceStatus:
    state: str
    total: int
    by_family: dict
    detail: dict


def search_space_status(store, ledger, *, fng_available: bool,
                        funding_available: bool, cands=None) -> SearchSpaceStatus:
    """Directive §14 states (SEARCH_SPACE_* etc.).

    INSUFFICIENT_DATA means: the only remaining untested candidates are
    blocked by missing datasets — the engine must not spin on empty batches.
    """
    if cands is None:
        cands, _ = enumerate_candidates()
    by_family: dict[str, int] = {}
    for _, d in cands:
        by_family[d["family"]] = by_family.get(d["family"], 0) + 1
    states = store.all_states()
    untested = [s for s, _ in cands if states.get(s, "PENDING") == "PENDING"]
    blocked_families = set()
    if not fng_available:
        blocked_families.add("fng")
    if not funding_available:
        blocked_families.add("fund_z")
    untestable = [s for s, d in cands
                  if d["family"] in blocked_families]
    untested_actionable = [s for s in untested if s not in set(untestable)]
    reverify = sum(1 for v in states.values() if v == "REVERIFY_PENDING")
    survivors = sum(1 for v in states.values() if v == "PASS")
    data_blocked = len([s for s in untested if s in set(untestable)])
    if not cands:
        st = "SEARCH_SPACE_EXHAUSTED"
    elif reverify > 0:
        st = "REVERIFY_PENDING"
    elif untested_actionable:
        st = "UNTESTED_CANDIDATES_AVAILABLE"
    elif survivors > 0:
        st = "SURVIVORS_AVAILABLE"
    elif untested:
        st = "INSUFFICIENT_DATA"
    else:
        st = "SEARCH_SPACE_EXHAUSTED"
    return SearchSpaceStatus(
        state=st,
        total=len(cands),
        by_family=by_family,
        detail={"untested": len(untested), "reverify": reverify,
                "survivors": survivors, "data_blocked": data_blocked,
                "awaiting_fdr": sum(1 for v in states.values() if v == "AWAITING_FDR")},
    )


def dump_space_manifest(path: Path) -> dict:
    cands, dedup = enumerate_candidates()
    by_family: dict[str, int] = {}
    for _, d in cands:
        by_family[d["family"]] = by_family.get(d["family"], 0) + 1
    manifest = {
        "generator": "rule_based_v2",
        "families": sorted(by_family),
        "counts": by_family,
        "total_unique": len(cands),
        "duplicates_collapsed": len(dedup),
        "note": "NEW v2 grid definitions; NOT the original archive space",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest
