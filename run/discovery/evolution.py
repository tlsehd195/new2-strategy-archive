"""Strategy variation operators, spec store and lineage (auto-evolution §3-2, §3-3).

Every operator is a typed, bounded edit of a canonical specification —
never a free-form random number change and never generated code:

=====================  =====================================================
operator               edit
=====================  =====================================================
tweak_param            one feature parameter one step up/down (x/÷ ~1.4)
resample_param         a parameter redrawn log-uniformly in its registry range
                       (expands the legacy grids within declared bounds)
tweak_threshold        a condition threshold / interval shifted one step
change_horizon         holding period to a neighbouring grid value
change_exit            add / remove / rescale stop-loss or take-profit
add_entry_condition    add a registry feature to the entry clause
add_filter             add a registry feature as an AND filter
remove_condition       drop a filter or an entry condition (>= 1 entry kept)
swap_feature           replace a feature by another of the same kind/sign
flip_direction         long <-> short
change_logic           entry all <-> any (needs >= 2 entry conditions)
change_risk            risk_per_trade / max_concurrent / leverage one step
split_symbol           multi-symbol strategy -> one-symbol strategy
crossover              conditions of two compatible parents combined
=====================  =====================================================

Every child is canonicalised (registry bounds, contradiction check,
complexity caps).  Invalid children are counted, never registered.

Identity vs history: ``specs.jsonl`` holds each canonical definition ONCE
(id = hash of the definition only); ``lineage.jsonl`` holds one record per
creation path (parents, operator, diff, generator version, seed, the
parents' DEV snapshot, context, policy, budget).  The same child reached
from two parents is one strategy with two lineage records.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Mapping, Optional, Sequence

from engine_lib import append_jsonl, canonical_json, iso_now, iter_jsonl, strategy_id
from strategy_spec import (
    FEATURES, RISK_RANGES, SPEC_SCHEMA, STOP_RANGE_BP, SUPPORTED_SYMBOLS,
    SpecError, canonicalize_spec,
)

EVOLUTION_GENERATOR_VERSION = "evo-gen-v1"
HORIZON_GRID = [4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 768]

OPERATORS = ("tweak_param", "resample_param", "tweak_threshold", "change_horizon",
             "change_exit", "add_entry_condition", "add_filter", "remove_condition",
             "swap_feature", "flip_direction", "change_logic", "change_risk", "split_symbol")
#: default operator weights (structural edits are not rarer than numeric ones:
#: the search must not degenerate into random number changes)
OPERATOR_WEIGHTS = {"tweak_param": 3, "resample_param": 1, "tweak_threshold": 3,
                    "change_horizon": 2, "change_exit": 1, "add_entry_condition": 1,
                    "add_filter": 2, "remove_condition": 1, "swap_feature": 2,
                    "flip_direction": 1, "change_logic": 1, "change_risk": 1,
                    "split_symbol": 1}


# ---------------------------------------------------------------------------
# random building blocks (all within registry bounds)
# ---------------------------------------------------------------------------

def _log_uniform_int(rng: random.Random, lo: float, hi: float) -> int:
    lo = max(1, int(lo))
    return int(round(math.exp(rng.uniform(math.log(lo), math.log(hi)))))


def _threshold_for(rng: random.Random, name: str, op: str):
    f = FEATURES[name]
    lo, hi = f.value_range
    if name == "hour_utc":
        h0 = rng.randrange(0, 23)
        return [float(h0), float(min(24, h0 + rng.choice([1, 2, 3, 4, 6])))]
    if name in ("rsi", "fng_max", "fng_min"):
        return float(rng.choice([10, 15, 20, 25, 30]) if op in ("lt", "le")
                     else rng.choice([70, 75, 80, 85, 90]))
    if name == "vol_ratio":
        return round(rng.uniform(1.1, 2.0), 2) if op in ("gt", "ge") else round(rng.uniform(0.4, 0.9), 2)
    if name == "breakout_k":
        return round(rng.uniform(0.0, 3.0), 2)
    if op == "between":
        a = round(rng.uniform(lo / 4, hi / 4), 2)
        return [a, round(a + rng.uniform(0.5, 2.0), 2)]
    mag = round(rng.uniform(0.5, 3.0), 2)
    return mag if op in ("gt", "ge") else -mag


def random_condition(rng: random.Random, available_data: Sequence[str],
                     exclude: Sequence[str] = ()) -> Optional[dict]:
    names = [n for n, f in sorted(FEATURES.items())
             if set(f.data) <= set(available_data) and n not in exclude]
    if not names:
        return None
    name = rng.choice(names)
    f = FEATURES[name]
    params = {}
    for pn, spec in f.params.items():
        if spec.kind is int:
            params[pn] = _log_uniform_int(rng, spec.lo if spec.lo > 0 else 1, spec.hi)
        else:
            params[pn] = round(rng.uniform(spec.lo, spec.hi), 4)
    if name == "ret_z":
        params["min_history"] = 0
        params["window"] = max(32, min(2048, max(64, 2 * params["lookback"])))
    if name == "vol_ratio" and params["short"] >= params["long"]:
        params["short"], params["long"] = 16, 192
    op = rng.choice([o for o in f.ops])
    if name != "hour_utc" and op == "between":
        op = "gt"
    return {"feature": name, "params": params, "op": op, "value": _threshold_for(rng, name, op)}


def random_spec(rng: random.Random, available_data: Sequence[str],
                symbols: Sequence[str] = None) -> Optional[dict]:
    """Exploration: a new strategy assembled from registry blocks."""
    symbols = list(symbols or SUPPORTED_SYMBOLS)
    for _ in range(20):
        conds = [random_condition(rng, available_data)]
        if rng.random() < 0.4:
            conds.append(random_condition(rng, available_data, exclude=[conds[0]["feature"]]))
        filt = [random_condition(rng, available_data)] if rng.random() < 0.3 else []
        spec = {"schema": SPEC_SCHEMA, "direction": rng.choice(["long", "short", "symmetric"]),
                "symbols": symbols if rng.random() < 0.7 else [rng.choice(symbols)],
                "entry": {"logic": "all", "conditions": [c for c in conds if c]},
                "filters": [c for c in filt if c],
                "exit": {"horizon": rng.choice(HORIZON_GRID)}}
        try:
            return canonicalize_spec(spec)
        except SpecError:
            continue
    return None


# ---------------------------------------------------------------------------
# operators: (spec, rng, ctx) -> (child, detail) or None (not applicable)
# ---------------------------------------------------------------------------

def _all_conds(s: Mapping) -> list[tuple[str, int]]:
    return [("entry", i) for i in range(len(s["entry"]["conditions"]))] + \
           [("filters", i) for i in range(len(s["filters"]))]


def _get(s: dict, where: tuple[str, int]) -> dict:
    return s["entry"]["conditions"][where[1]] if where[0] == "entry" else s["filters"][where[1]]


def op_tweak_param(s, rng, ctx):
    slots = [(w, pn) for w in _all_conds(s) for pn in FEATURES[_get(s, w)["feature"]].params]
    if not slots:
        return None
    w, pn = rng.choice(slots)
    c = _get(s, w)
    spec = FEATURES[c["feature"]].params[pn]
    old = c["params"][pn]
    f = rng.choice([1 / 1.4, 1.4])
    new = old * f if old else spec.hi / 10
    new = int(round(new)) if spec.kind is int else round(new, 4)
    if new == old:
        new = old + (1 if f > 1 else -1)
    new = min(max(new, spec.lo), spec.hi)
    if new == old:
        return None
    c["params"][pn] = new
    return s, {"where": list(w), "param": pn, "from": old, "to": new}


def op_resample_param(s, rng, ctx):
    slots = [(w, pn) for w in _all_conds(s) for pn in FEATURES[_get(s, w)["feature"]].params]
    if not slots:
        return None
    w, pn = rng.choice(slots)
    c = _get(s, w)
    spec = FEATURES[c["feature"]].params[pn]
    old = c["params"][pn]
    new = (_log_uniform_int(rng, max(1, spec.lo), spec.hi) if spec.kind is int
           else round(rng.uniform(spec.lo, spec.hi), 4))
    if new == old:
        return None
    c["params"][pn] = new
    return s, {"where": list(w), "param": pn, "from": old, "to": new}


def op_tweak_threshold(s, rng, ctx):
    w = rng.choice(_all_conds(s))
    c = _get(s, w)
    lo, hi = FEATURES[c["feature"]].value_range
    step = 1.0 if c["feature"] == "hour_utc" else (5.0 if hi >= 100 else 0.25)
    d = rng.choice([-step, step])
    old = copy.deepcopy(c["value"])
    if c["op"] == "between":
        a, b = c["value"]
        if rng.random() < 0.5:
            a, b = a + d, b + d            # shift the interval
        else:
            b = b + d                      # widen / narrow
        if a < lo or b > hi or not a < b:
            return None
        c["value"] = [round(a, 4), round(b, 4)]
    else:
        v = round(c["value"] + d, 4)
        if not lo <= v <= hi:
            return None
        c["value"] = v
    return s, {"where": list(w), "from": old, "to": c["value"]}


def op_change_horizon(s, rng, ctx):
    h = s["exit"]["horizon"]
    grid = sorted(set(HORIZON_GRID) | {h})
    i = grid.index(h)
    opts = [grid[j] for j in (i - 1, i + 1) if 0 <= j < len(grid)]
    new = rng.choice(opts)
    s["exit"]["horizon"] = new
    return s, {"from": h, "to": new}


def op_change_exit(s, rng, ctx):
    k = rng.choice(["stop_loss_bp", "take_profit_bp"])
    old = s["exit"].get(k)
    if old is None:
        new = float(rng.choice([25, 50, 75, 100, 150, 200, 300]))
    elif rng.random() < 0.4:
        new = None
    else:
        new = round(min(max(old * rng.choice([0.7, 1.4]), STOP_RANGE_BP[0]), STOP_RANGE_BP[1]), 2)
    if new == old:
        return None
    s["exit"][k] = new
    return s, {"exit": k, "from": old, "to": new}


def op_add_entry_condition(s, rng, ctx):
    c = random_condition(rng, ctx["available_data"],
                         exclude=[x["feature"] for x in s["entry"]["conditions"]])
    if c is None:
        return None
    s["entry"]["conditions"].append(c)
    return s, {"added": c}


def op_add_filter(s, rng, ctx):
    c = random_condition(rng, ctx["available_data"])
    if c is None:
        return None
    s["filters"].append(c)
    return s, {"added_filter": c}


def op_remove_condition(s, rng, ctx):
    slots = [("filters", i) for i in range(len(s["filters"]))]
    if len(s["entry"]["conditions"]) > 1:
        slots += [("entry", i) for i in range(len(s["entry"]["conditions"]))]
    if not slots:
        return None
    w = rng.choice(slots)
    removed = _get(s, w)
    (s["entry"]["conditions"] if w[0] == "entry" else s["filters"]).pop(w[1])
    return s, {"removed": removed, "where": w[0]}


def op_swap_feature(s, rng, ctx):
    w = rng.choice(_all_conds(s))
    c = _get(s, w)
    f = FEATURES[c["feature"]]
    cands = [n for n, g in sorted(FEATURES.items())
             if n != c["feature"] and set(g.data) <= set(ctx["available_data"])
             and (g.kind == f.kind or g.signed == f.signed) and "between" not in g.ops[:1]]
    if not cands:
        return None
    new = random_condition(rng, ctx["available_data"],
                           exclude=[n for n in FEATURES if n not in cands])
    if new is None:
        return None
    old = copy.deepcopy(c)
    _get(s, w).clear()
    _get(s, w).update(new)
    return s, {"where": list(w), "from": old["feature"], "to": new["feature"]}


def op_flip_direction(s, rng, ctx):
    d = s["direction"]
    if d == "symmetric":
        return None
    s["direction"] = "short" if d == "long" else "long"
    return s, {"from": d, "to": s["direction"]}


def op_change_logic(s, rng, ctx):
    if len(s["entry"]["conditions"]) < 2:
        return None
    old = s["entry"]["logic"]
    s["entry"]["logic"] = "any" if old == "all" else "all"
    return s, {"from": old, "to": s["entry"]["logic"]}


def op_change_risk(s, rng, ctx):
    k = rng.choice(sorted(RISK_RANGES))
    lo, hi = RISK_RANGES[k]
    old = s["risk"][k]
    if k == "max_concurrent":
        new = min(max(old + rng.choice([-1, 1]), lo), hi)
    else:
        new = round(min(max(old * rng.choice([0.7, 1.4]), lo), hi), 4)
    if new == old:
        return None
    s["risk"][k] = new
    return s, {"risk": k, "from": old, "to": new}


def op_split_symbol(s, rng, ctx):
    if len(s["symbols"]) < 2:
        return None
    # guided by the parent's DEV per-symbol result when available
    per = (ctx.get("parent_dev") or {}).get("per_symbol_mean_bp") or {}
    good = [sym for sym, m in per.items() if m is not None and m > 0 and sym in s["symbols"]]
    sym = rng.choice(good or s["symbols"])
    old = list(s["symbols"])
    s["symbols"] = [sym]
    return s, {"from": old, "to": [sym], "guided": bool(good)}


OPS = {
    "tweak_param": op_tweak_param, "resample_param": op_resample_param,
    "tweak_threshold": op_tweak_threshold, "change_horizon": op_change_horizon,
    "change_exit": op_change_exit, "add_entry_condition": op_add_entry_condition,
    "add_filter": op_add_filter, "remove_condition": op_remove_condition,
    "swap_feature": op_swap_feature, "flip_direction": op_flip_direction,
    "change_logic": op_change_logic, "change_risk": op_change_risk,
    "split_symbol": op_split_symbol,
}


#: version of the strategy fingerprints below; recorded with every lineage edge
FINGERPRINT_VERSION = "fp-v2"
#: features whose value is an integer by construction (UTC hour of the bar), so
#: thresholds between two integers select the same bars
INTEGER_FEATURES = frozenset({"hour_utc"})
_INF = float("inf")


def definition_fingerprint(spec: Mapping) -> str:
    """Hash of the COMPLETE canonical definition — entry/exit, direction,
    features with their computation windows, thresholds and operators, risk
    and sizing, symbols, timeframe, schema and feature-registry version.  Two
    specs that differ only in key order, explicit defaults or the order of
    commutative conditions get the same fingerprint (``canonicalize_spec``)."""
    from strategy_spec import SPEC_REGISTRY_VERSION
    c = canonicalize_spec(spec)
    body = {"fingerprint": FINGERPRINT_VERSION, "registry": SPEC_REGISTRY_VERSION,
            **{k: c[k] for k in ("schema", "direction", "symbols", "timeframe", "entry", "filters",
                                 "exit", "risk")}}
    return "DF-" + hashlib.sha256(canonical_json(body).encode()).hexdigest()[:20]


def _bounds(c: Mapping) -> list:
    """[lo, lo_closed, hi, hi_closed] of the values a condition accepts."""
    v, op = c["value"], c["op"]
    if op == "gt":
        return [v, False, _INF, False]
    if op == "ge":
        return [v, True, _INF, False]
    if op == "lt":
        return [-_INF, False, v, False]
    if op == "le":
        return [-_INF, False, v, True]
    return [v[0], True, v[1], False]                         # between: [lo, hi)


def _intersect(a: list, b: list) -> list:
    lo = (a[0], a[1]) if a[0] > b[0] else (b[0], b[1]) if b[0] > a[0] else (a[0], a[1] and b[1])
    hi = (a[2], a[3]) if a[2] < b[2] else (b[2], b[3]) if b[2] < a[2] else (a[2], a[3] and b[3])
    return [lo[0], lo[1], hi[0], hi[1]]


def _integer_bounds(b: list) -> list:
    """The same set of INTEGER values as closed bounds: ``> 9.5``, ``>= 10``
    and ``> 9`` all become ``[10, +inf)``."""
    lo, lc, hi, hc = b
    if math.isfinite(lo):
        lo = float(math.ceil(lo)) if lc else float(math.floor(lo) + 1)
    if math.isfinite(hi):
        hi = float(math.floor(hi)) if hc else float(math.ceil(hi) - 1)
    return [lo, True, hi, True]


def _normal_conditions(conds: Sequence[Mapping], *, conjunction: bool) -> list[str]:
    """Normal form of a set of conditions.  Under a conjunction, every group
    of conditions on the same feature WITH the same computation parameters is
    one interval (``x > 1 AND x > 2`` is ``x > 2``); an integer feature is
    written with closed integer bounds.  Operators that accept the same set
    therefore give the same text; operators that differ on some value
    (``>`` vs ``>=`` on a continuous feature) stay different."""
    groups: dict = {}
    for c in conds:
        key = canonical_json({"feature": c["feature"], "params": c["params"]})
        b = _bounds(c)
        if conjunction and key in groups:
            groups[key] = [_intersect(groups[key][0], b)]
        else:
            groups.setdefault(key, []).append(b)
    out = []
    for key, bs in groups.items():
        feat = json.loads(key)["feature"]
        for b in bs:
            if feat in INTEGER_FEATURES:
                b = _integer_bounds(b)
            out.append(canonical_json({"cond": json.loads(key),
                                       "bounds": [None if not math.isfinite(b[0]) else b[0], b[1],
                                                  None if not math.isfinite(b[2]) else b[2], b[3]]}))
    return sorted(set(out))


def _behaviour_body(spec: Mapping) -> dict:
    c = canonicalize_spec(spec)
    # ``risk`` is left out: no evaluation stage of the evolution pipeline reads it
    # (fixed order value per event), so a ``change_risk`` child trades exactly like
    # its parent — real run R4 put such a pair into one validation batch (ADR 0013).
    # It is part of ``definition_fingerprint``; the dedup report shows such pairs.
    body = {"fingerprint": FINGERPRINT_VERSION, "direction": c["direction"],
            "symbols": c["symbols"], "timeframe": c["timeframe"], "exit": c["exit"]}
    if c["entry"]["logic"] == "all":
        body["all"] = _normal_conditions(c["entry"]["conditions"] + c["filters"], conjunction=True)
    else:
        body["any"] = _normal_conditions(c["entry"]["conditions"], conjunction=False)
        body["filters"] = _normal_conditions(c["filters"], conjunction=True)
    return body


def behaviour_key(spec: Mapping) -> str:
    """Hash of what a strategy DOES (exact behavioural equivalence).

    ``strategy_id`` hashes the canonical definition, so two definitions that
    trade identically can still get different ids.  Equivalences folded here
    (each one exact — the two definitions produce the same signal on every
    possible input):

    * under ``entry.logic == "all"`` the signal is entry AND filters, so a
      condition in ``entry`` and the same condition in ``filters`` are the
      same rule (``add_filter`` / ``crossover`` create exactly that);
    * conditions on the same feature and parameters inside one conjunction
      are one interval (``x > 1 AND x > 2`` == ``x > 2``);
    * thresholds of an integer-valued feature (``hour_utc``) between the same
      two integers (``[9.5, 14)`` == ``[10, 14)``);
    * risk settings (not read by any evaluation stage, see above).

    The stored id is unchanged (ids stay stable); the key stops the search
    from spending budget and multiplicity on a behavioural duplicate.
    Thresholds that are merely CLOSE are not folded here — see
    :func:`near_duplicate`."""
    return "BK-" + hashlib.sha256(canonical_json(_behaviour_body(spec)).encode()).hexdigest()[:20]


def entry_signal_key(spec: Mapping) -> str:
    """Hash of WHEN a strategy enters (the behaviour key without the exit).

    Two strategies with the same key take the same entries on every input and
    differ only in how they leave (horizon, stop-loss, take-profit); a
    validation batch treats them as one hypothesis (ADR 0031)."""
    body = {k: v for k, v in _behaviour_body(spec).items() if k != "exit"}
    return "EK-" + hashlib.sha256(canonical_json(body).encode()).hexdigest()[:20]


def near_entry_duplicate(a: Mapping, b: Mapping) -> Optional[str]:
    """None, or why the ENTRIES of two specs are near duplicates, whatever their
    exits: ``near_duplicate`` with ``b``'s exit replaced by ``a``'s.  Closes the
    gap between the exact entry key and the near-duplicate rule (which keeps
    the exit in its structure): entries within half a mutation step that also
    differ in the exit are one hypothesis too (ADR 0033)."""
    ca, cb = canonicalize_spec(a), _strip_derived(canonicalize_spec(b))
    cb["exit"] = copy.deepcopy(ca["exit"])
    why = near_duplicate(ca, cb)
    return None if why is None else "entries " + why + " (exit ignored)"


def _near_tolerance(feature: str) -> float:
    """Half of the smallest deliberate threshold step of ``op_tweak_threshold``
    (0.25 on z-like scales, 5 on 0-100 scales): one mutation step is always a
    real difference; less than half a step is "effectively the same"."""
    if feature in INTEGER_FEATURES:
        return 0.0                                   # integers: only exact equality
    hi = FEATURES[feature].value_range[1]
    return (5.0 if hi >= 100 else 0.25) / 2.0


def near_signature(spec: Mapping) -> tuple[str, list[tuple[str, float]]]:
    """(structure key, [(feature, finite bound), ...]): two specs are near
    duplicates when the structure keys are equal and every bound differs by
    less than the feature's tolerance.  Structure = everything except the
    numeric thresholds (direction, symbols, exit, features + parameters, the
    open / closed side of every bound)."""
    body = _behaviour_body(spec)
    struct, vec = dict(body), []
    for part in ("all", "any", "filters"):
        if part not in body:
            continue
        items = []
        for txt in body[part]:
            d = json.loads(txt)
            lo, lc, hi, hc = d["bounds"]
            shape = canonical_json({"cond": d["cond"], "shape": [lo is None, lc, hi is None, hc]})
            items.append((shape, [x for x in (lo, hi) if x is not None], d["cond"]["feature"]))
        items.sort(key=lambda t: (t[0], t[1]))          # align by structure, not by value
        struct[part] = [t[0] for t in items]
        vec += [(f, x) for _, xs, f in items for x in xs]
    return "NS-" + hashlib.sha256(canonical_json(struct).encode()).hexdigest()[:20], vec


def near_duplicate(a: Mapping, b: Mapping) -> Optional[str]:
    """None, or the reason two specs are near duplicates (behaviourally
    similar, NOT identical: they are both kept, see ``search.propose_round``)."""
    ka, va = near_signature(a)
    kb, vb = near_signature(b)
    if ka != kb or len(va) != len(vb):
        return None
    diffs = []
    for (f, x), (_, y) in zip(va, vb):
        tol = _near_tolerance(f)
        if abs(x - y) > 0 and not abs(x - y) < tol:
            return None
        if x != y:
            diffs.append(f"{f} {x} vs {y} (|d|={abs(x - y):.4g} < {tol})")
    return "thresholds within half a mutation step: " + "; ".join(diffs) if diffs else "identical thresholds"


def _strip_derived(s: Mapping) -> dict:
    d = copy.deepcopy(dict(s))
    for k in ("family", "data", "primary_cell"):
        d.pop(k, None)
    return d


def mutate(parent: Mapping, op: str, rng: random.Random, ctx: Mapping) -> Optional[tuple[dict, dict]]:
    """Apply one operator.  Returns (canonical child, detail) or None when the
    operator does not apply or the child is invalid (counted by the caller)."""
    base = _strip_derived(canonicalize_spec(parent))
    out = OPS[op](base, rng, ctx)
    if out is None:
        return None
    child, detail = out
    try:
        canon = canonicalize_spec(child)
    except SpecError as e:
        return None if not ctx.get("raise_invalid") else (_ for _ in ()).throw(e)
    if canonical_json(canon) == canonical_json(canonicalize_spec(parent)):
        return None                      # semantic no-op (e.g. reordering)
    return canon, detail


def crossover(a: Mapping, b: Mapping, rng: random.Random) -> Optional[tuple[dict, dict]]:
    """Combine compatible parents: a's clause plus one of b's conditions (as an
    entry condition or a filter).  Compatible = shared symbols and the same
    direction (or one symmetric side)."""
    ca, cb = canonicalize_spec(a), canonicalize_spec(b)
    syms = sorted(set(ca["symbols"]) & set(cb["symbols"]))
    if not syms:
        return None
    if ca["direction"] != cb["direction"] and "symmetric" not in (ca["direction"], cb["direction"]):
        return None
    child = _strip_derived(ca)
    child["symbols"] = syms
    if child["direction"] == "symmetric" and cb["direction"] != "symmetric":
        child["direction"] = cb["direction"]
    pick = copy.deepcopy(rng.choice(cb["entry"]["conditions"] + cb["filters"]))
    how = rng.choice(["entry", "filter"])
    if how == "entry":
        child["entry"]["conditions"].append(pick)
    else:
        child["filters"].append(pick)
    try:
        canon = canonicalize_spec(child)
    except SpecError:
        return None
    if canonical_json(canon) in (canonical_json(ca), canonical_json(cb)):
        return None
    return canon, {"took": pick, "as": how, "symbols": syms}


# ---------------------------------------------------------------------------
# stores
# ---------------------------------------------------------------------------

class SpecStore:
    """Write-once canonical definitions (``specs.jsonl``)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._cache: Optional[dict] = None

    def all(self) -> dict:
        if self._cache is None:
            self._cache = {}
            for r in iter_jsonl(self.path):
                self._cache[r["strategy_id"]] = r["definition"]
        return self._cache

    def get(self, sid: str) -> Optional[dict]:
        return self.all().get(sid)

    def put(self, definition: Mapping, *, source: str) -> tuple[str, bool]:
        """Register a definition; returns (strategy_id, created).  An existing
        id must carry the identical canonical definition."""
        canon = canonicalize_spec(definition) if definition.get("schema") == SPEC_SCHEMA \
            else dict(definition)
        sid = strategy_id(canon)
        have = self.all().get(sid)
        if have is not None:
            if canonical_json(have) != canonical_json(canon):  # pragma: no cover - collision
                raise RuntimeError(f"{sid}: stored definition differs")
            return sid, False
        append_jsonl(self.path, {"strategy_id": sid, "definition": canon, "source": source,
                                 "registered_at": iso_now()})
        self.all()[sid] = canon
        return sid, True


class LineageStore:
    """Append-only creation paths (``lineage.jsonl``).  Nothing is deleted:
    children that turned out worse than their parents keep their record."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def record(self, *, child_id: str, parents: Sequence[str], operator: str, detail: Mapping,
               generator_version: str, seed: int, round_index: int, context_id: Optional[str],
               policy: Mapping, budget: Mapping, parent_dev: Mapping, created: bool,
               identity: Optional[Mapping] = None) -> dict:
        """``identity``: fingerprints and generation of the child (see
        :meth:`identity_for`) — kept with the edge so lineage stays readable
        when the fingerprint definition changes later."""
        rec = {"child_id": child_id, "parents": list(parents), "operator": operator,
               "detail": detail, "generator_version": generator_version, "seed": seed,
               "round_index": round_index, "context_id": context_id,
               "policy": dict(policy), "budget": dict(budget),
               "parent_dev_snapshot": dict(parent_dev),
               "parent_dev_snapshot_hash": hashlib.sha256(
                   canonical_json(dict(parent_dev)).encode()).hexdigest()[:16],
               "child_was_new": created, **dict(identity or {}), "created_at": iso_now()}
        append_jsonl(self.path, rec)
        return rec

    def generation_of(self, sid: str) -> int:
        """0 for a seed / exploration spec, else 1 + the deepest parent's
        generation (the smallest over the creation paths of ``sid``)."""
        memo: dict = {}
        recs = self.records()
        paths: dict = {}
        for r in recs:
            paths.setdefault(r["child_id"], []).append(r)

        def gen(x: str, depth: int = 0) -> int:
            if x in memo:
                return memo[x]
            ps = [r for r in paths.get(x, []) if r["parents"] and r["operator"] != "legacy_translation"]
            if not ps or depth > 500:
                memo[x] = 0
                return 0
            memo[x] = min(1 + max(gen(q, depth + 1) for q in r["parents"]) for r in ps)
            return memo[x]
        return gen(sid)

    def identity_for(self, spec: Mapping, parents: Sequence[str], operator: str) -> dict:
        parent_gen = [self.generation_of(q) for q in parents] if operator != "legacy_translation" else []
        return {"fingerprint_version": FINGERPRINT_VERSION,
                "definition_fingerprint": definition_fingerprint(spec),
                "behaviour_key": behaviour_key(spec),
                "near_signature": near_signature(spec)[0],
                "generation": (1 + max(parent_gen)) if parent_gen else 0}

    def records(self) -> list[dict]:
        return list(iter_jsonl(self.path))

    def parents_of(self, sid: str) -> list[list[str]]:
        return [r["parents"] for r in self.records() if r["child_id"] == sid]

    def children_of(self, sid: str) -> list[str]:
        return sorted({r["child_id"] for r in self.records() if sid in r["parents"]})

    def paths(self, sid: str) -> list[dict]:
        return [r for r in self.records() if r["child_id"] == sid]

    def outcomes(self, dev_metric: Mapping[str, Optional[float]]) -> list[dict]:
        """Every variation edge with the parent's and child's DEV metric (when
        both exist): improved / worse / untested.  Failures stay visible."""
        out = []
        for r in self.records():
            c = dev_metric.get(r["child_id"])
            ps = [dev_metric.get(p) for p in r["parents"]]
            if c is None or any(p is None for p in ps):
                verdict = "untested"
            else:
                verdict = "improved" if c > max(ps) else "worse_or_equal"
            out.append({"child": r["child_id"], "parents": r["parents"], "operator": r["operator"],
                        "child_dev": c, "parents_dev": ps, "verdict": verdict})
        return out
