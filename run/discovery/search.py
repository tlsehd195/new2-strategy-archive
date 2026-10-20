"""Adaptive multi-objective strategy search (auto-evolution §3-4).

The search ONLY sees DEV-window feedback.  :func:`dev_feedback` is the single
gate between evaluation records and the search: it accepts records of stage
``dev`` and returns a whitelisted set of keys; a validation, locked-TEST or
paper record raises :class:`FeedbackLeak`.  Validation / locked / paper
outcomes therefore cannot steer which strategies are generated next (they
would turn the confirmatory windows into search data).

Ranking: Pareto non-dominated sorting over DEV objectives (statistical
strength, fold stability, drawdown, cost share, complexity) with a niche cap
per strategy family so one family cannot fill the elite.  New proposals mix
exploitation (mutating / crossing elite parents) and exploration (fresh
random specs) in configured shares.

Budgets: rounds, total specs, evaluations per round, invalid-proposal ratio
and patience (rounds without a change of the first Pareto front).  The
state is persisted after every round; a resumed search continues with the
same per-round seeds, so an interrupted run and an uninterrupted run
propose identical children.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

from engine_lib import atomic_write_json, canonical_json, load_json, strategy_id
from evolution import (
    EVOLUTION_GENERATOR_VERSION, FINGERPRINT_VERSION, OPERATOR_WEIGHTS, OPERATORS, behaviour_key,
    crossover, definition_fingerprint, mutate, near_duplicate, near_signature, random_spec,
)

SEARCH_VERSION = "search-v1(pareto+niche,dev-only)"

#: the ONLY evaluation fields the search may read (all computed on DEV data)
DEV_FEEDBACK_KEYS = ("dev_n", "dev_mean_bp", "dev_t", "dev_fold_pos_share", "dev_mdd_bp",
                     "dev_cost_share", "per_symbol_mean_bp", "complexity", "screen_pass",
                     "family")


class FeedbackLeak(RuntimeError):
    """A non-DEV result was offered to the search."""


def dev_feedback(record: Mapping) -> dict:
    if record.get("stage") != "dev":
        raise FeedbackLeak(f"search may only read DEV results, got stage {record.get('stage')!r}")
    m = record.get("metrics") or {}
    return {k: m.get(k) for k in DEV_FEEDBACK_KEYS}


@dataclass
class SearchConfig:
    seed: int = 20261015
    children_per_round: int = 24
    exploration_share: float = 0.25
    crossover_share: float = 0.15
    elite_size: int = 12
    niche_cap: int = 3                 # max elite members per family
    max_rounds: int = 50
    max_specs_total: int = 2000
    max_invalid_ratio: float = 0.9     # a round producing almost only invalid children stops
    patience: int = 8                  # rounds without a first-front change
    max_t_objective: float = 6.0       # dev t is capped: beyond it, more t is not "better"
    operator_weights: dict = field(default_factory=lambda: dict(OPERATOR_WEIGHTS))

    def to_json(self) -> dict:
        return asdict(self)

    def config_hash(self) -> str:
        return hashlib.sha256(canonical_json({"version": SEARCH_VERSION,
                                              "generator": EVOLUTION_GENERATOR_VERSION,
                                              **self.to_json()}).encode()).hexdigest()[:16]

    @classmethod
    def from_json(cls, d: Mapping) -> "SearchConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Pareto ranking on DEV objectives
# ---------------------------------------------------------------------------

def objectives(fb: Mapping, cfg: SearchConfig) -> Optional[tuple]:
    """Objective vector, every component to be MAXIMISED.  None when the
    candidate has no usable DEV result (it cannot be ranked)."""
    t, fold = fb.get("dev_t"), fb.get("dev_fold_pos_share")
    if t is None or fold is None or t != t:
        return None
    return (min(float(t), cfg.max_t_objective),
            float(fold),
            -float(fb.get("dev_mdd_bp") or 0.0),
            -float(fb.get("dev_cost_share") if fb.get("dev_cost_share") is not None else 1.0),
            -float(fb.get("complexity") or 0))


def _dominates(a: Sequence[float], b: Sequence[float]) -> bool:
    return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))


def pareto_fronts(items: Mapping[str, tuple]) -> list[list[str]]:
    """Non-dominated sorting; ids inside a front are sorted (deterministic)."""
    remaining = dict(items)
    fronts: list[list[str]] = []
    while remaining:
        front = sorted(k for k, v in remaining.items()
                       if not any(_dominates(w, v) for j, w in remaining.items() if j != k))
        fronts.append(front)
        for k in front:
            remaining.pop(k)
    return fronts


def select_elite(feedback: Mapping[str, Mapping], cfg: SearchConfig) -> list[str]:
    """Elite parents: Pareto fronts in order, niche-capped per family, only
    candidates that passed the DEV screen or at least have a positive mean."""
    objs = {}
    for sid, fb in feedback.items():
        o = objectives(fb, cfg)
        if o is None or (fb.get("dev_mean_bp") or 0) <= 0:
            continue
        objs[sid] = o
    elite: list[str] = []
    per_family: dict[str, int] = {}
    for front in pareto_fronts(objs):
        # inside a front: screen passers first, then by capped t (ties by id)
        front = sorted(front, key=lambda s: (not feedback[s].get("screen_pass"), -objs[s][0], s))
        for sid in front:
            fam = feedback[sid].get("family") or "?"
            if per_family.get(fam, 0) >= cfg.niche_cap:
                continue
            elite.append(sid)
            per_family[fam] = per_family.get(fam, 0) + 1
            if len(elite) >= cfg.elite_size:
                return elite
    return elite


def first_front_hash(feedback: Mapping[str, Mapping], cfg: SearchConfig) -> str:
    objs = {s: o for s, fb in feedback.items() if (o := objectives(fb, cfg)) is not None}
    fronts = pareto_fronts(objs)
    return hashlib.sha256(json.dumps(fronts[0] if fronts else []).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# persisted state
# ---------------------------------------------------------------------------

SEARCH_STATUSES = ("ACTIVE", "BUDGET_EXHAUSTED", "CONVERGED", "NO_VALID_PROPOSALS",
                   "BLOCKED_DATA")


@dataclass
class SearchState:
    config_hash: str
    context_id: str
    round_index: int = 0
    proposed: int = 0
    invalid: int = 0
    duplicates: int = 0
    semantic_duplicates: int = 0
    registered: int = 0
    rounds_without_front_change: int = 0
    front_hash: str = ""
    status: str = "ACTIVE"
    history: list = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, *, config_hash: str, context_id: str) -> "SearchState":
        """A state belongs to one (search config, experiment context); a
        different pair starts a NEW search (the old state file is kept under
        its own name, never overwritten)."""
        d = load_json(path, None)
        if d and d.get("config_hash") == config_hash and d.get("context_id") == context_id:
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        if d:
            old = path.with_name(f"search_state.{d.get('config_hash')}.{d.get('context_id')}.json")
            if not old.exists():
                atomic_write_json(old, d)
        return cls(config_hash=config_hash, context_id=context_id)

    def save(self, path: Path) -> None:
        atomic_write_json(path, asdict(self))


def round_rng(cfg: SearchConfig, round_index: int) -> random.Random:
    """Per-round generator derived from (seed, round): resume-safe."""
    return random.Random(f"{cfg.seed}:{round_index}")


def propose_round(*, cfg: SearchConfig, round_index: int, elite: Sequence[str],
                  specs: Mapping[str, Mapping], feedback: Mapping[str, Mapping],
                  available_data: Sequence[str], known_ids: set,
                  symbols: Sequence[str], known_keys: Optional[set] = None,
                  known_specs: Optional[Mapping[str, Mapping]] = None) -> tuple[list[dict], dict]:
    """Proposals for one round: [{spec, sid, parents, operator, detail}] plus
    counters.  Known ids (any earlier evaluation, any status — including
    rejected ones, any earlier generation) are never proposed again, and
    neither is a definition that behaves like a known one
    (:func:`evolution.behaviour_key`; ``semantic_duplicates`` counts those).

    A proposal whose thresholds are within half a mutation step of a known or
    already proposed spec of the same structure (:func:`evolution.near_duplicate`)
    is KEPT — it may differ on real data — but carries ``near_duplicate_of``;
    ``near_duplicates`` counts them and the validation batch takes at most one
    member per near group.  ``counters["dedup_log"]`` lists every decision with
    the representative and the reason (the audit trail of the dedup)."""
    rng = round_rng(cfg, round_index)
    n = cfg.children_per_round
    n_explore = int(round(n * cfg.exploration_share)) if elite else n
    n_cross = int(round(n * cfg.crossover_share)) if len(elite) >= 2 else 0
    n_mut = n - n_explore - n_cross
    ops = [o for o in OPERATORS if cfg.operator_weights.get(o, 0) > 0]
    weights = [cfg.operator_weights[o] for o in ops]
    out: list[dict] = []
    seen = set(known_ids)
    seen_keys = set(known_keys or ())
    rep_of: dict = {}                     # behaviour key -> representative id
    near: dict = {}                       # near structure -> [(id, spec)]
    for ksid, ksp in sorted((known_specs or {}).items()):
        try:
            k = behaviour_key(ksp)
        except Exception:  # noqa: BLE001 — a legacy definition, not a spec
            continue
        seen_keys.add(k)
        rep_of.setdefault(k, ksid)
        near.setdefault(near_signature(ksp)[0], []).append((ksid, ksp))
    counters = {"attempts": 0, "invalid": 0, "duplicates": 0, "semantic_duplicates": 0,
                "near_duplicates": 0, "dedup_log": []}

    def _log(kind, spec, sid, rep_sid, op, parents, reason):
        counters["dedup_log"].append({"kind": kind, "proposed_id": sid,
                                      "proposed_fingerprint": definition_fingerprint(spec),
                                      "representative": rep_sid, "operator": op,
                                      "parents": list(parents), "reason": reason,
                                      "fingerprint_version": FINGERPRINT_VERSION})

    def _add(spec, parents, op, detail):
        sid = strategy_id(spec)
        if sid in seen:
            counters["duplicates"] += 1
            _log("exact_definition", spec, sid, sid, op, parents,
                 "same canonical definition (strategy id) as a known strategy")
            return
        bk = behaviour_key(spec)
        if bk in seen_keys:
            counters["semantic_duplicates"] += 1
            rsid = rep_of.get(bk)
            rspec = (known_specs or {}).get(rsid) or next(
                (o["spec"] for o in out if o["sid"] == rsid), None)
            why = "behaviourally identical (normal form of the conditions)"
            if rspec is not None and {k: v for k, v in rspec.items() if k != "risk"} == \
                    {k: v for k, v in spec.items() if k != "risk"}:
                why = "differs only in risk settings, which no evaluation stage reads"
            _log("behaviour_equal", spec, sid, rsid, op, parents, why)
            return
        seen.add(sid)
        seen_keys.add(bk)
        rep_of[bk] = sid
        prop = {"spec": spec, "sid": sid, "parents": list(parents), "operator": op, "detail": detail}
        ns = near_signature(spec)[0]
        for osid, ospec in near.get(ns, []):
            why = near_duplicate(spec, ospec)
            if why is not None:
                counters["near_duplicates"] += 1
                prop["detail"] = {**dict(detail), "near_duplicate_of": osid, "near_reason": why}
                _log("near_duplicate_kept", spec, sid, osid, op, parents, why)
                break
        near.setdefault(ns, []).append((sid, spec))
        out.append(prop)

    for kind, target in (("mutate", n_mut), ("crossover", n_cross), ("explore", n_explore)):
        made, tries = 0, 0
        while made < target and tries < target * 8:
            tries += 1
            counters["attempts"] += 1
            before = len(out)
            if kind == "explore":
                s = random_spec(rng, available_data, symbols)
                if s is None:
                    counters["invalid"] += 1
                    continue
                _add(s, [], "random_spec", {})
            elif kind == "mutate":
                p = rng.choice(list(elite))
                op = rng.choices(ops, weights)[0]
                r = mutate(specs[p], op, rng, {"available_data": available_data,
                                               "parent_dev": feedback.get(p) or {}})
                if r is None:
                    counters["invalid"] += 1
                    continue
                _add(r[0], [p], op, r[1])
            else:
                a, b = rng.sample(list(elite), 2)
                r = crossover(specs[a], specs[b], rng)
                if r is None:
                    counters["invalid"] += 1
                    continue
                _add(r[0], [a, b], "crossover", r[1])
            made += len(out) > before
    return out, counters
