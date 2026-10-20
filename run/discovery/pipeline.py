"""Staged evaluation of evolved strategies (auto-evolution §4, §5, §6).

Stages and what each may read:

=============  ==================================  =============================
stage          data                                used for
=============  ==================================  =============================
dev            dev window (before the embargo)     search feedback, DEV screen
validation     independent validation window       preregistered batch test
robustness     validation (+ dev for neighbours)   filters only, never ranking
locked         locked TEST window (single use)     preregistered campaign test
paper          data after the locked window        isolated replay (paper.py)
=============  ==================================  =============================

Multiple testing under adaptive generation.  The search looks at DEV results
of earlier rounds before proposing new strategies, so the set of hypotheses
is chosen adaptively and "BH over each generation" is not valid.  Here:

* DEV results are never a test — only a search / screening signal.
* The confirmatory test runs on the validation window in PREREGISTERED
  batches (members, alpha, method and seed are written before any
  validation data is evaluated).  Inside a batch: Romano–Wolf studentized
  StepM per symbol (strong FWER under arbitrary dependence between
  strategies).  Across batches on the same window: alpha spending
  alpha_k = alpha_total * 6/pi^2 / k^2, so the FWER over ALL batches ever
  run on that window stays <= alpha_total no matter how adaptively the
  later batches were chosen (union bound).  SPA / Reality Check and BH / BY
  over HAC p-values are recorded as diagnostics.
* The locked TEST is entered only through a campaign (campaigns.py).

Statuses (per strategy and experiment context, append-only, transition
table enforced): RESEARCH_ONLY, VALIDATION_SURVIVOR, PAPER_TEST_READY,
REJECTED, INCONCLUSIVE, BLOCKED_DATA, BLOCKED_COST, BLOCKED_STATISTICS.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path
from typing import Mapping, Optional, Sequence

from campaigns import CampaignLedger, WindowRetired, member_alpha
from costs_bridge import COST_MODEL_SCOPE, CostComputationError, CostConfigError, UNVERIFIED_COST_MODEL
from engine_lib import (
    CODE_FILES, LockTestRegistry, append_jsonl, bh_fdr_family, canonical_json,
    code_identity, experiment_context, iso_now, iter_jsonl, locked_window_for, nw_tstat,
    pbo_cscv, runtime_identity, t_sf,
)
from evaluator import cell_stats, run_event_study
from evolution import EVOLUTION_GENERATOR_VERSION, LineageStore, SpecStore
from search import SearchConfig, dev_feedback
from stats_mc import (
    MC_METHOD_VERSION, by_fdr, daily_series, default_q, dm_test, mcs, reality_check, spa_test, step_spa,
    spending_alpha, stationary_bootstrap_indices, stepm,
)
from strategy_spec import (
    FEATURES, HORIZON_RANGE, build_spec_aux, build_spec_signal, canonicalize_spec,
    registry_identity, stop_exit_rule,
)

PIPELINE_VERSION = "evo-pipeline-v1"
EVO_CODE_FILES = tuple(sorted(set(CODE_FILES) | {"public_cost_estimate.py", "portable_util.py", "strategy_spec.py", "evolution.py", "search.py",
                                                 "pipeline.py", "autoloop.py", "paper.py",
                                                 "paper_forward.py", "execution_sim.py",
                                                 "config/paper_forward_v1.json"}))
DEFAULT_PROMOTION_CONFIG = Path(__file__).resolve().parent / "config" / "promotion_v3.json"
#: append-only record of validation batches run on the real validation windows
#: in state directories that no longer exist (ADR 0013).  Alpha spending is per
#: window, not per state directory: a fresh directory must not restart at k = 1.
DEFAULT_VALIDATION_HISTORY = Path(__file__).resolve().parent / "validation_window_history.jsonl"
DAY_MS = 86_400_000

STATUSES = ("RESEARCH_ONLY", "VALIDATION_SURVIVOR", "PAPER_TEST_READY", "REJECTED",
            "INCONCLUSIVE", "BLOCKED_DATA", "BLOCKED_COST", "BLOCKED_STATISTICS",
            "PAPER_TESTING", "PAPER_PASSED", "PAPER_FAILED")
_BLOCKED = ("BLOCKED_DATA", "BLOCKED_COST", "BLOCKED_STATISTICS")
TRANSITIONS = {
    None: {"RESEARCH_ONLY", "REJECTED", "INCONCLUSIVE", "BLOCKED_DATA", "BLOCKED_COST"},
    "RESEARCH_ONLY": {"VALIDATION_SURVIVOR", "REJECTED", "INCONCLUSIVE", *_BLOCKED},
    "VALIDATION_SURVIVOR": {"PAPER_TEST_READY", "REJECTED", "INCONCLUSIVE", *_BLOCKED},
    # a blocked candidate resumes exactly where it was blocked (``resume_to``)
    "BLOCKED_DATA": {"RESEARCH_ONLY", "VALIDATION_SURVIVOR", "REJECTED", "INCONCLUSIVE", *_BLOCKED},
    "BLOCKED_COST": {"RESEARCH_ONLY", "VALIDATION_SURVIVOR", "REJECTED", "INCONCLUSIVE", *_BLOCKED},
    "BLOCKED_STATISTICS": {"RESEARCH_ONLY", "VALIDATION_SURVIVOR", "REJECTED", "INCONCLUSIVE",
                           *_BLOCKED},
    "REJECTED": set(),
    "INCONCLUSIVE": set(),
    # isolated paper replay only (no live trading): PAPER_PASSED / PAPER_FAILED never
    # authorise real orders; a human approval outside this repository would be required
    "PAPER_TEST_READY": {"PAPER_TESTING"},
    # PAPER_PASSED only from the preregistered forward observation (paper_forward.py);
    # INCONCLUSIVE when its maximum observation period ends without enough trades
    "PAPER_TESTING": {"PAPER_PASSED", "PAPER_FAILED", "INCONCLUSIVE"},
    "PAPER_PASSED": set(),
    "PAPER_FAILED": set(),
}


def window_alpha_total(section: Mapping) -> float:
    """FWER budget of ONE validation / locked window.

    promo-v3: one family for the whole search — ``alpha_total_per_search`` is
    split equally over the windows of every SUPPORTED symbol (a fixed list, so
    a run on fewer symbols cannot claim a larger share), and the union bound
    over windows x batches keeps the probability of ANY false survivor in the
    whole search at or below ``alpha_total_per_search``.  promo-v1/v2 configs
    (``alpha_total_per_window``) keep their recorded meaning."""
    from strategy_spec import SUPPORTED_SYMBOLS
    if "alpha_total_per_search" in section:
        return float(section["alpha_total_per_search"]) / len(SUPPORTED_SYMBOLS)
    return float(section["alpha_total_per_window"])


def alpha_family(section: Mapping) -> dict:
    from strategy_spec import SUPPORTED_SYMBOLS
    if "alpha_total_per_search" in section:
        return {"scope": "search", "alpha_total": section["alpha_total_per_search"],
                "windows": len(SUPPORTED_SYMBOLS), "per_window": window_alpha_total(section)}
    return {"scope": "window", "alpha_total": section["alpha_total_per_window"]}


class InvalidStatusTransition(RuntimeError):
    pass


class EvidenceTampered(RuntimeError):
    """An append-only evaluation record fails its own content hash."""


def load_promotion_config(path: Optional[Path] = None) -> tuple[dict, str]:
    p = Path(path or DEFAULT_PROMOTION_CONFIG)
    raw = p.read_bytes()
    cfg = json.loads(raw)
    if cfg.get("schema") != "promotion_config_v1":
        raise ValueError(f"{p}: not a promotion_config_v1 file")
    return cfg, hashlib.sha256(raw).hexdigest()[:16]


def record_hash(rec: Mapping) -> str:
    body = {k: v for k, v in rec.items() if k not in ("record_id", "at")}
    return "EV-" + hashlib.sha256(canonical_json(body).encode()).hexdigest()[:20]


# ---------------------------------------------------------------------------
# statuses
# ---------------------------------------------------------------------------

class StatusStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._cur: Optional[dict] = None

    def _load(self) -> dict:
        if self._cur is None:
            self._cur = {}
            for r in iter_jsonl(self.path):
                self._cur[(r["strategy_id"], r["context_id"])] = r
        return self._cur

    def get(self, sid: str, ctx: str) -> Optional[dict]:
        return self._load().get((sid, ctx))

    def status(self, sid: str, ctx: str) -> Optional[str]:
        r = self.get(sid, ctx)
        return r["status"] if r else None

    def all(self, ctx: str) -> dict:
        return {s: r["status"] for (s, c), r in self._load().items() if c == ctx}

    def set(self, sid: str, ctx: str, status: str, *, stage: str, reasons: Sequence[str] = (),
            evidence: Optional[Mapping] = None, resume_to: Optional[str] = None,
            labels: Sequence[str] = ()) -> dict:
        if status not in STATUSES:
            raise InvalidStatusTransition(f"unknown status {status}")
        cur = self.get(sid, ctx)
        prev = cur["status"] if cur else None
        if status not in TRANSITIONS[prev]:
            raise InvalidStatusTransition(f"{sid}: {prev} -> {status} not allowed")
        if prev in _BLOCKED and status not in _BLOCKED and status not in ("REJECTED", "INCONCLUSIVE"):
            want = cur.get("resume_to")
            if want and status != want:
                raise InvalidStatusTransition(f"{sid}: blocked at {want}, cannot resume as {status}")
        rec = {"strategy_id": sid, "context_id": ctx, "status": status, "previous": prev,
               "stage": stage, "reasons": list(reasons), "evidence": dict(evidence or {}),
               "labels": list(labels), "at": iso_now()}
        if status in _BLOCKED:
            rec["resume_to"] = resume_to or (cur or {}).get("resume_to") or prev or "RESEARCH_ONLY"
        append_jsonl(self.path, rec)
        self._load()[(sid, ctx)] = rec
        return rec


# ---------------------------------------------------------------------------
# data provider (production: files under NEW2_DATA_DIR)
# ---------------------------------------------------------------------------

class RunnerDataProvider:
    """Reads the same files, windows and quality gates as runner.Runner.
    The dev/validation read window ends the day before the symbol's locked
    window; locked bars are only returned by :meth:`locked` (called after a
    campaign reserved the window) and paper bars by :meth:`paper`."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._cache: dict = {}
        self._fng_loaded = False
        self._fng = None

    def available_data(self) -> list[str]:
        out = ["klines"]
        if (self.data_dir / "funding").exists():
            out.append("funding")
        if self.fng() is not None:
            out.append("fng")
        return out

    def symbol(self, sym: str) -> dict:
        if sym in self._cache:
            return self._cache[sym]
        import datetime as dt
        from runner import (READ_START, PathConfigError, Runner, checked_funding_quality,
                            load_funding_window_checked, merged_bars)
        read_end = Runner._read_end(sym)
        try:
            h = merged_bars(self.data_dir, sym, READ_START, read_end)
        except PathConfigError as e:          # no klines directory at all: fail closed
            self._cache[sym] = {"bars": [], "funding": None, "missing": [str(e)], "identity": {}}
            return self._cache[sym]
        e = {"bars": h.bars, "funding": None, "missing": [], "identity": h.identity() if h.bars else {}}
        if not h.bars:
            e["missing"] = list(h.missing or [f"{sym}: no data"])
        else:
            first = dt.datetime.fromtimestamp(h.first_ts / 1000, tz=dt.timezone.utc).date().isoformat()
            f, rep = load_funding_window_checked(self.data_dir, sym, first, read_end)
            fq = checked_funding_quality(f, rep)
            if fq["ok"]:
                e["funding"] = f
                e["identity"]["funding_sha256"] = hashlib.sha256(
                    repr(sorted(f)).encode()).hexdigest()
            else:
                e["missing"] = [f"{sym}: funding unavailable/quality {fq['issues']}"]
        self._cache[sym] = e
        return e

    def fng(self):
        if not self._fng_loaded:
            from runner import load_fng_series
            self._fng = load_fng_series(self.data_dir)
            self._fng_loaded = True
        return self._fng

    def windows(self, sym: str, horizon: int) -> dict:
        from runner import split_windows
        bars = self.symbol(sym)["bars"]
        return split_windows(sym, bars[0].ts, bars[-1].ts, horizon, [b.ts for b in bars])

    def _range(self, sym: str, lo: str, hi: str) -> dict:
        from runner import checked_funding_quality, load_funding_window_checked, merged_bars
        h = merged_bars(self.data_dir, sym, lo, hi)
        if not h.bars:
            return {"bars": [], "funding": None, "missing": list(h.missing or ["no bars"]), "sha256": ""}
        f, rep = load_funding_window_checked(self.data_dir, sym, lo, hi)
        fq = checked_funding_quality(f, rep)
        if not fq["ok"]:
            return {"bars": [], "funding": None, "missing": [f"funding {fq['issues']}"], "sha256": ""}
        return {"bars": h.bars, "funding": f, "missing": [], "sha256": h.sha256}

    def locked_ready(self, sym: str) -> bool:
        """File-presence check only (no locked content is read): does every
        month of the locked window have a kline and a funding file?"""
        import datetime as dt
        w = locked_window_for(sym)
        if not w:
            return False
        cur, end = dt.date.fromisoformat(w[0]).replace(day=1), dt.date.fromisoformat(w[1])
        while cur <= end:
            if not (self.data_dir / "klines" / f"{sym}-15m-{cur:%Y-%m}.csv").exists() or \
                    not (self.data_dir / "funding" / f"{sym}-fundingRate-{cur:%Y-%m}.csv").exists():
                return False
            cur = (cur.replace(day=28) + dt.timedelta(days=6)).replace(day=1)
        return True

    def locked(self, sym: str) -> dict:
        w = locked_window_for(sym)
        if not w:
            return {"bars": [], "funding": None, "missing": ["NO_LOCKED_TEST"], "sha256": ""}
        return self._range(sym, w[0], w[1])

    def paper(self, sym: str) -> dict:
        import datetime as dt
        w = locked_window_for(sym)
        if not w:
            return {"bars": [], "funding": None, "missing": ["no locked window: paper start undefined"]}
        start = (dt.date.fromisoformat(w[1]) + dt.timedelta(days=2)).isoformat()
        return self._range(sym, start, dt.date.today().isoformat())

    def identity(self, symbols: Sequence[str]) -> dict:
        out = {}
        for s in sorted(symbols):
            e = self.symbol(s)
            out[s] = e["identity"] if not e["missing"] else {"missing": e["missing"]}
        f = self.fng()
        out["_fng"] = ({"n": len(f), "sha256": hashlib.sha256(repr(sorted(f.items())).encode()).hexdigest()}
                       if f else {"missing": "FNG dataset absent"})
        return out


# ---------------------------------------------------------------------------
# metrics helpers
# ---------------------------------------------------------------------------

def _mdd(nets: Sequence[float]) -> float:
    peak = cur = 0.0
    dd = 0.0
    for x in nets:
        cur += x
        peak = max(peak, cur)
        dd = max(dd, peak - cur)
    return dd


def _hac(nets: Sequence[float]) -> tuple[float, float]:
    t, n = nw_tstat(list(nets))
    if not math.isfinite(t):
        return float("nan"), float("nan")
    return t, t_sf(t, n - 1)


def _folds(events: Sequence[Mapping], t0: int, t1: int, k: int) -> list[Optional[float]]:
    span = max(1, t1 - t0 + 1)
    acc: list[list[float]] = [[] for _ in range(k)]
    for e in events:
        i = min(k - 1, max(0, (e["entry_ts"] - t0) * k // span))
        acc[i].append(e["net_bp"])
    return [sum(a) / len(a) if a else None for a in acc]


def complexity(spec: Mapping) -> int:
    s = canonicalize_spec(spec)
    return (len(s["entry"]["conditions"]) + len(s["filters"])
            + (s["exit"]["stop_loss_bp"] is not None) + (s["exit"]["take_profit_bp"] is not None))


def neighbours(spec: Mapping, limit: int = 12) -> list[dict]:
    """Deterministic one-step neighbours (each parameter x1.4 / ÷1.4, each
    threshold ± one step, horizon to both grid neighbours)."""
    s = canonicalize_spec(spec)
    out: dict[str, dict] = {}

    def _try(d):
        try:
            c = canonicalize_spec(d)
        except Exception:  # noqa: BLE001 — out-of-bounds neighbour: skipped
            return
        if canonical_json(c) != canonical_json(s):
            out.setdefault(canonical_json(c), c)
    base = json.loads(json.dumps({k: v for k, v in s.items()
                                  if k not in ("family", "data", "primary_cell")}))
    for where in ("entry", "filters"):
        conds = base["entry"]["conditions"] if where == "entry" else base["filters"]
        for i, c in enumerate(conds):
            for pn, v in c["params"].items():
                ps = FEATURES[c["feature"]].params[pn]
                for f in (1.4, 1 / 1.4):
                    d = json.loads(json.dumps(base))
                    cc = (d["entry"]["conditions"] if where == "entry" else d["filters"])[i]
                    nv = v * f if v else ps.hi / 10
                    cc["params"][pn] = int(round(nv)) if ps.kind is int else round(nv, 4)
                    _try(d)
            lo, hi = FEATURES[c["feature"]].value_range
            step = 1.0 if c["feature"] == "hour_utc" else (5.0 if hi >= 100 else 0.25)
            for dlt in (-step, step):
                d = json.loads(json.dumps(base))
                cc = (d["entry"]["conditions"] if where == "entry" else d["filters"])[i]
                cc["value"] = ([round(cc["value"][0] + dlt, 4), round(cc["value"][1] + dlt, 4)]
                               if cc["op"] == "between" else round(cc["value"] + dlt, 4))
                _try(d)
    h = base["exit"]["horizon"]
    for nh in (int(round(h / 1.5)), int(round(h * 1.5))):
        if HORIZON_RANGE[0] <= nh <= HORIZON_RANGE[1]:
            d = json.loads(json.dumps(base))
            d["exit"]["horizon"] = nh
            _try(d)
    return [out[k] for k in sorted(out)][:limit]


# ---------------------------------------------------------------------------
# the pipeline
# ---------------------------------------------------------------------------

class EvolutionPipeline:
    def __init__(self, state_dir: Path, provider, cost, *, symbols: Sequence[str] = ("BTCUSDT", "ETHUSDT"),
                 search_cfg: Optional[SearchConfig] = None,
                 promotion_config: Optional[Path] = None, order_value: float = 10_000.0,
                 validation_history: Optional[Path] = None):
        self.state_dir = Path(state_dir)
        self.dir = self.state_dir / "evolution"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.provider = provider
        self.cost = cost
        self.symbols = list(symbols)
        self.search_cfg = search_cfg or SearchConfig()
        self.cfg, self.cfg_hash = load_promotion_config(promotion_config)
        self.order_value = order_value
        self.specs = SpecStore(self.dir / "specs.jsonl")
        self.lineage = LineageStore(self.dir / "lineage.jsonl")
        self.statuses = StatusStore(self.dir / "statuses.jsonl")
        self.eval_path = self.dir / "evaluations.jsonl"
        self.batch_path = self.dir / "validation_batches.jsonl"
        self.review_path = self.dir / "promotion_reviews.jsonl"
        self.series_path = self.dir / "dev_series.jsonl"
        self.campaigns = CampaignLedger(self.state_dir / "locked_window_campaigns.jsonl")
        self.lock_registry = LockTestRegistry(self.state_dir / "lock_test_usage.json")
        self._evals: Optional[dict] = None
        self._ctx: Optional[str] = None
        self._feat_cache: dict = {}
        self.validation_history = Path(validation_history) if validation_history else None

    # -- identity ---------------------------------------------------------------
    def context_id(self) -> str:
        if self._ctx is None:
            from runner import data_policy
            comps = {
                "data": self.provider.identity(self.symbols),
                "data_policy": data_policy(),
                "cost": self.cost.identity(),
                "code": {"code_hash": code_identity(files=EVO_CODE_FILES)["code_hash"]},
                "validation": {"pipeline": PIPELINE_VERSION, "promotion_config": self.cfg_hash,
                               "search_config": self.search_cfg.config_hash(),
                               "mc_method": MC_METHOD_VERSION, "spec_registry": registry_identity(),
                               "order_value": self.order_value, "symbols": sorted(self.symbols)},
                "runtime": runtime_identity(),
            }
            ctx, norm = experiment_context(comps)
            p = self.dir / "contexts.jsonl"
            if not any(r.get("context_id") == ctx for r in iter_jsonl(p)):
                append_jsonl(p, {"context_id": ctx, "components": norm, "at": iso_now()})
            self._ctx = ctx
        return self._ctx

    def reverification_pending(self) -> list[dict]:
        """Strategies that reached a confirmatory status under ANOTHER
        experiment context and have no status under the current one.  Their
        old evidence is never reused (statuses are per context); they wait for
        a fresh evaluation.  ``changed`` names the context components that
        differ (e.g. ``cost`` after a cost-model change)."""
        ctx = self.context_id()
        comps = {r["context_id"]: r.get("components", {}) for r in iter_jsonl(self.dir / "contexts.jsonl")}
        cur = comps.get(ctx, {})
        advanced = {"VALIDATION_SURVIVOR", "PAPER_TEST_READY", "PAPER_TESTING", "PAPER_PASSED",
                    "BLOCKED_COST", "BLOCKED_STATISTICS"}
        now = self.statuses.all(ctx)
        out: dict[str, dict] = {}
        for (sid, c), r in sorted(self.statuses._load().items()):
            if c == ctx or sid in now or r["status"] not in advanced:
                continue
            if r["status"] in _BLOCKED and r.get("resume_to") != "VALIDATION_SURVIVOR":
                continue
            old = comps.get(c, {})
            changed = sorted(k for k in set(cur) | set(old) if canonical_json(cur.get(k)) != canonical_json(old.get(k)))
            out[sid] = {"strategy_id": sid, "previous_context": c, "previous_status": r["status"],
                        "changed": changed, "status": "REVERIFY_PENDING"}
        return list(out.values())

    # -- evaluation records -------------------------------------------------------
    def _index(self) -> dict:
        if self._evals is None:
            self._evals = {}
            for r in iter_jsonl(self.eval_path):
                if r.get("record_id") != record_hash(r):
                    # an evidence record whose content no longer matches its id is
                    # never promotable evidence: stop instead of reading around it
                    raise EvidenceTampered(f"{self.eval_path}: record {r.get('record_id')} "
                                           f"does not match its content hash")
                self._evals[(r["stage"], r["strategy_id"], r["context_id"])] = r
        return self._evals

    def evaluation(self, stage: str, sid: str, ctx: Optional[str] = None) -> Optional[dict]:
        return self._index().get((stage, sid, ctx or self.context_id()))

    def _append_eval(self, rec: dict) -> dict:
        rec = {**rec, "pipeline": PIPELINE_VERSION}
        rec["record_id"] = record_hash(rec)
        rec["at"] = iso_now()
        append_jsonl(self.eval_path, rec)
        self._index()[(rec["stage"], rec["strategy_id"], rec["context_id"])] = rec
        return rec

    def search_pool(self) -> set:
        """Strategies the SEARCH may see: those registered through
        :meth:`register` (seeds, search proposals — they have a lineage
        record).  Robustness neighbours are DEV-evaluated only because their
        centre passed the VALIDATION StepM; letting them into the search pool
        would make validation outcomes steer the search (ADR 0013)."""
        return {r["child_id"] for r in self.lineage.records()}

    def dev_feedback_all(self) -> dict:
        ctx = self.context_id()
        pool = self.search_pool()
        return {sid: dev_feedback(r) for (st, sid, c), r in self._index().items()
                if st == "dev" and c == ctx and r.get("status") == "OK" and sid in pool}

    # -- low-level study ------------------------------------------------------------
    def _study(self, spec: Mapping, sym: str, bars, funding, fng, lo: int, hi: int,
               cost=None) -> list[dict]:
        """Events with entry decisions in [lo, hi) and exits <= hi; features
        computed causally on bars[:hi+1] (history before ``lo`` is warm-up)."""
        sub = bars[:hi + 1]
        # features on the FULL list (causal => identical on every prefix), cached
        # per input identity so strategies sharing a feature compute it once
        # (only for the provider-held dev/validation lists: their identity is stable)
        stable = bars is self.provider.symbol(sym)["bars"]
        ns = (sym, id(bars), len(bars), id(funding), id(fng))
        if len(self._feat_cache) > 400:
            self._feat_cache.clear()
        aux = build_spec_aux(bars, spec, funding, fng, cache=self._feat_cache if stable else None,
                             cache_ns=ns)
        es = run_event_study(sub, sym, build_spec_signal(spec, aux), spec["exit"]["horizon"],
                             cost or self.cost, self.order_value, funding,
                             warmup=max(1, lo), exit_rule=stop_exit_rule(spec))
        return [e.to_json() for e in es.events]

    def _inputs(self, spec: Mapping) -> tuple[dict, Optional[dict], list[str]]:
        data, missing = {}, []
        for sym in spec["symbols"]:
            e = self.provider.symbol(sym)
            if e["missing"]:
                missing += e["missing"]
            else:
                data[sym] = e
        fng = None
        if "fng" in spec["data"]:
            fng = self.provider.fng()
            if fng is None:
                missing.append("FNG dataset not present")
        return data, fng, missing

    def _cost_block(self) -> dict:
        b = {"version": self.cost.version, "status": self.cost.status,
             "test_only": self.cost.test_only, "scope": COST_MODEL_SCOPE}
        if self.cost.assumption is not None:
            b["assumption"] = {k: self.cost.assumption[k] for k in ("profile_id", "sha256")}
            b["claim"] = f"비용 미검증({self.cost.status}): research only, never promotable"
        return b

    # -- stage 1: DEV ------------------------------------------------------------------
    def register(self, spec: Mapping, *, source: str, parents: Sequence[str] = (),
                 operator: str = "seed", detail: Optional[Mapping] = None, round_index: int = 0,
                 seed: int = 0, budget: Optional[Mapping] = None) -> tuple[str, bool]:
        sid, created = self.specs.put(spec, source=source)
        self.lineage.record(child_id=sid, parents=list(parents), operator=operator,
                            detail=dict(detail or {}), generator_version=EVOLUTION_GENERATOR_VERSION,
                            seed=seed, round_index=round_index, context_id=self.context_id(),
                            policy={"search_config": self.search_cfg.config_hash(),
                                    "promotion_config": self.cfg_hash},
                            budget=dict(budget or {}),
                            parent_dev={p: (self.evaluation("dev", p) or {}).get("metrics")
                                        for p in parents},
                            created=created,
                            identity=self.lineage.identity_for(spec, parents, operator))
        return sid, created

    def dev_evaluate(self, sid: str, *, record_status: bool = True) -> dict:
        ctx = self.context_id()
        have = self.evaluation("dev", sid)
        if have is not None:
            return have
        spec = canonicalize_spec(self.specs.get(sid))
        data, fng, missing = self._inputs(spec)
        base = {"stage": "dev", "strategy_id": sid, "context_id": ctx,
                "cost_model": self._cost_block()}
        if missing:
            rec = self._append_eval({**base, "status": "BLOCKED_DATA", "missing": missing})
            if record_status and self.statuses.status(sid, ctx) is None:
                self.statuses.set(sid, ctx, "BLOCKED_DATA", stage="dev", reasons=missing,
                                  resume_to="RESEARCH_ONLY")
            return rec
        sc = self.cfg["dev_screen"]
        try:
            per, events, span, day_series = {}, [], [None, None], {}
            for sym, e in data.items():
                win = self.provider.windows(sym, spec["exit"]["horizon"])
                d1 = win["dev"][1]
                ev = self._study(spec, sym, e["bars"], e["funding"], fng, 1, d1)
                cs = cell_stats([_Ev(x) for x in ev], sym, f"{sid}|dev")
                per[sym] = {"n": cs["n"], "mean_bp": cs.get("mean_bp"), "t": cs.get("t"),
                            "p": cs.get("p"), "status": cs["status"],
                            "dev_end_ts": e["bars"][d1].ts, **cost_attribution(ev)}
                events += ev
                t0, t1 = e["bars"][0].ts, e["bars"][d1].ts
                span = [t0 if span[0] is None else min(span[0], t0),
                        t1 if span[1] is None else max(span[1], t1)]
                day_series[sym] = (t0 // DAY_MS, daily_series(ev, t0, t1))
        except (CostComputationError, CostConfigError) as err:
            rec = self._append_eval({**base, "status": "BLOCKED_COST", "error": str(err)})
            if record_status and self.statuses.status(sid, ctx) is None:
                self.statuses.set(sid, ctx, "BLOCKED_COST", stage="dev", reasons=[str(err)],
                                  resume_to="RESEARCH_ONLY")
            return rec
        events.sort(key=lambda x: x["entry_ts"])
        nets = [x["net_bp"] for x in events]
        n = len(nets)
        t, p = _hac(nets) if n >= 3 else (float("nan"), float("nan"))
        folds = _folds(events, span[0], span[1], int(sc["dev_folds"]))
        gross_abs = sum(abs(x["gross_bp"]) for x in events)
        costs = sum(x["cost_bp"] + x["funding_bp"] for x in events)
        metrics = {
            "dev_n": n, "dev_mean_bp": (sum(nets) / n) if n else None,
            "dev_t": t if math.isfinite(t) else None, "dev_p": p if math.isfinite(p) else None,
            "dev_folds_mean_bp": folds,
            "dev_fold_pos_share": sum(1 for f in folds if f is not None and f > 0) / len(folds),
            "dev_mdd_bp": _mdd(nets), "dev_cost_share": (costs / gross_abs) if gross_abs > 0 else 1.0,
            "per_symbol_mean_bp": {s: v["mean_bp"] for s, v in per.items()},
            "per_symbol_n": {s: v["n"] for s, v in per.items()},
            "complexity": complexity(spec), "family": spec["family"],
            # same events, same window: before / after trading cost and funding
            # (reporting only — not in DEV_FEEDBACK_KEYS, never read by the search)
            **{"dev_" + k: v for k, v in cost_attribution(events).items()},
        }
        fails = []
        if any(v["n"] < sc["min_events_per_symbol"] for v in per.values()):
            fails.append("dev_underpowered")
        if not (metrics["dev_mean_bp"] or 0) > sc["min_mean_bp"]:
            fails.append("dev_mean_not_positive")
        if not (metrics["dev_t"] or -1e9) >= sc["min_t"]:
            fails.append("dev_t_below_screen")
        if metrics["dev_fold_pos_share"] < sc["min_fold_positive_share"]:
            fails.append("dev_folds_unstable")
        if metrics["dev_cost_share"] > sc["max_cost_share"]:
            fails.append("dev_cost_share_too_high")
        metrics["screen_pass"] = not fails
        rec = self._append_eval({**base, "status": "OK", "metrics": metrics, "per_symbol": per,
                                 "screen": {"pass": not fails, "failures": fails,
                                            "config_hash": self.cfg_hash},
                                 "dataset": {s: data[s]["identity"] for s in data}})
        append_jsonl(self.series_path, {"strategy_id": sid, "context_id": ctx,
                                        "series": {s: [d0, [round(v, 6) for v in vals]]
                                                   for s, (d0, vals) in day_series.items()}})
        if record_status:
            cur = self.statuses.status(sid, ctx)
            if cur in (None, "BLOCKED_DATA", "BLOCKED_COST"):
                if not fails:
                    st, why = "RESEARCH_ONLY", []
                elif "dev_underpowered" in fails and (metrics["dev_mean_bp"] or 0) > 0:
                    st, why = "INCONCLUSIVE", fails
                else:
                    st, why = "REJECTED", fails
                self.statuses.set(sid, ctx, st, stage="dev_screen", reasons=why,
                                  evidence={"dev": rec["record_id"]})
        return rec

    # -- stage 2: preregistered validation batches -------------------------------------
    def _batches(self) -> list[dict]:
        return list(iter_jsonl(self.batch_path))

    def _val_window_key(self, sym: str) -> str:
        win = self.provider.windows(sym, 4)
        return f"{sym}:VAL:{win['validation_start_ms']}"

    def validation_candidates(self) -> list[str]:
        ctx = self.context_id()
        in_batch = {m for b in self._batches() if b["kind"] == "PREREGISTERED"
                    and b["context_id"] == ctx for m in b["members"]}
        fb = self.dev_feedback_all()
        out = [s for s, st in self.statuses.all(ctx).items()
               if st == "RESEARCH_ONLY" and s not in in_batch and fb.get(s, {}).get("screen_pass")]
        # dev-only priority: capped dev t, then id (deterministic)
        cap = self.search_cfg.max_t_objective
        ranked = sorted(out, key=lambda s: (-min(fb[s]["dev_t"] or 0, cap), s))
        # at most one member per near-duplicate group (thresholds within half a
        # mutation step, evolution.near_duplicate), counting strategies already
        # submitted to validation in this context: a twin is a second look at
        # the same hypothesis, not a new one.  The skipped ones stay RESEARCH_ONLY
        # and are listed by :meth:`near_duplicate_skips` (dedup report).
        from evolution import near_duplicate, near_signature
        groups: dict = {}
        for m in sorted(in_batch):
            d = self.specs.get(m)
            if d is not None:
                groups.setdefault(near_signature(d)[0], []).append(m)
        picked, self._near_skips = [], {}
        for sid in ranked:
            d = self.specs.get(sid)
            ns = near_signature(d)[0]
            twin = next((o for o in groups.get(ns, []) if near_duplicate(d, self.specs.get(o)) is not None), None)
            if twin is not None:
                self._near_skips[sid] = {"twin": twin, "reason": near_duplicate(d, self.specs.get(twin))}
                continue
            groups.setdefault(ns, []).append(sid)
            picked.append(sid)
        return picked

    def near_duplicate_skips(self) -> dict:
        """{strategy id: {twin, reason}} left out of validation by the last
        :meth:`validation_candidates` call."""
        if not hasattr(self, "_near_skips"):
            self.validation_candidates()
        return dict(self._near_skips)

    def preregister_batch(self, members: Sequence[str]) -> dict:
        ctx = self.context_id()
        vc = self.cfg["validation"]
        prior = [b for b in self._batches() if b["kind"] == "PREREGISTERED"]
        syms = sorted({s for m in members for s in self.specs.get(m)["symbols"]})
        windows = {}
        for sym in syms:
            key = self._val_window_key(sym)
            ext = self.external_prior_batches(key)
            k = 1 + sum(1 for b in prior if key in b["windows"]) + ext
            windows[key] = {"symbol": sym, "batch_index": k, "external_prior_batches": ext,
                            "alpha": spending_alpha(k, window_alpha_total(vc)),
                            "alpha_family": alpha_family(vc),
                            "members": sorted(m for m in members if sym in self.specs.get(m)["symbols"])}
        body = {"kind": "PREREGISTERED", "context_id": ctx, "members": sorted(members),
                "windows": windows, "method": vc["method"], "mc_method": MC_METHOD_VERSION,
                "B": vc["bootstrap_B"], "seed": vc["bootstrap_seed"],
                "config_hash": self.cfg_hash,
                "dev_records": {m: self.evaluation("dev", m)["record_id"] for m in members}}
        body["batch_id"] = "VB-" + hashlib.sha256(canonical_json(body).encode()).hexdigest()[:16]
        body["created_at"] = iso_now()
        append_jsonl(self.batch_path, body)
        return body

    def external_prior_batches(self, key: str) -> int:
        """Batches already run on validation window ``key`` in OTHER state
        directories (``validation_history``), minus those recorded from this
        directory's own batch ledger (no double count when a directory that
        was written to the history is resumed)."""
        if self.validation_history is None:
            return 0
        own = {b["batch_id"] for b in self._batches() if b["kind"] == "PREREGISTERED"}
        n = 0
        for r in iter_jsonl(self.validation_history):
            if key in (r.get("window_keys") or []) and not (set(r.get("batch_ids") or []) & own):
                n += int(r.get("batches", 1))
        return n

    def run_validation(self, max_batch: Optional[int] = None) -> Optional[dict]:
        """Complete an open preregistered batch, else preregister and run a new
        one from the screened candidates.  Returns the batch result or None."""
        ctx = self.context_id()
        batches = self._batches()
        done = {b["batch_id"] for b in batches if b["kind"] == "RESULT"}
        open_ = [b for b in batches if b["kind"] == "PREREGISTERED" and b["context_id"] == ctx
                 and b["batch_id"] not in done]
        if open_:
            batch = open_[0]
        else:
            cands = self.validation_candidates()[:max_batch or self.cfg["validation"]["max_batch_size"]]
            if not cands:
                return None
            batch = self.preregister_batch(cands)
        return self._evaluate_batch(batch)

    def _validation_events(self, sid: str, sym: str) -> tuple[list[dict], int, int]:
        spec = canonicalize_spec(self.specs.get(sid))
        e = self.provider.symbol(sym)
        fng = self.provider.fng() if "fng" in spec["data"] else None
        win = self.provider.windows(sym, spec["exit"]["horizon"])
        v0, v1 = win["validation"]
        if not (0 <= v0 < v1 < len(e["bars"])):
            return [], 0, -1
        return self._study(spec, sym, e["bars"], e["funding"], fng, v0, v1), v0, v1

    def _evaluate_batch(self, batch: Mapping) -> dict:
        ctx = self.context_id()
        vc = self.cfg["validation"]
        per_member: dict[str, dict] = {m: {} for m in batch["members"]}
        tests: dict = {}
        try:
            for key, w in sorted(batch["windows"].items()):
                sym = w["symbol"]
                series, excluded = {}, {}
                bars = self.provider.symbol(sym)["bars"]
                grid0, grid1 = None, None
                evs = {}
                for m in w["members"]:
                    ev, v0, v1 = self._validation_events(m, sym)
                    evs[m] = (ev, v0, v1)
                    if v1 > v0:
                        grid0 = bars[v0].ts if grid0 is None else min(grid0, bars[v0].ts)
                        grid1 = bars[v1].ts if grid1 is None else max(grid1, bars[v1].ts)
                for m in w["members"]:
                    ev, v0, v1 = evs[m]
                    nets = [x["net_bp"] for x in ev]
                    t, p = _hac(nets) if len(nets) >= 3 else (float("nan"), float("nan"))
                    info = {"n": len(nets), "mean_bp": sum(nets) / len(nets) if nets else None,
                            "hac_t": t if math.isfinite(t) else None,
                            "hac_p": p if math.isfinite(p) else None,
                            "window": [bars[v0].ts, bars[v1].ts] if v1 > v0 else None,
                            **cost_attribution(ev)}
                    if grid0 is not None and nets:
                        s = daily_series(ev, grid0, grid1)
                        if any(abs(x) > 0 for x in s) and len(s) >= vc["min_days"]:
                            series[m] = s
                        else:
                            excluded[m] = "no variation"
                    else:
                        excluded[m] = "no validation events"
                    per_member[m][sym] = info
                res = {"alpha": w["alpha"], "m": len(w["members"]), "excluded": excluded}
                if series:
                    if len(set(len(v) for v in series.values())) != 1:
                        raise RuntimeError("validation series misaligned")
                    q = default_q(len(next(iter(series.values()))))
                    sm = stepm(series, alpha=w["alpha"], B=vc["bootstrap_B"], q=q, seed=batch["seed"])
                    res.update({"rejected": sm["rejected"], "steps": sm["steps"], "q": q,
                                "n_days": sm["n"]})
                    try:
                        res["spa"] = {k: v for k, v in spa_test(series, B=vc["bootstrap_B"], q=q,
                                                                 seed=batch["seed"]).items()
                                      if k.startswith("p_") or k == "statistic"}
                        res["step_spa_rejected"] = step_spa(series, alpha=w["alpha"], B=vc["bootstrap_B"],
                                                            q=q, seed=batch["seed"])["rejected"]
                        res["reality_check_p"] = reality_check(series, B=vc["bootstrap_B"], q=q,
                                                               seed=batch["seed"])["p"]
                    except ValueError as err:
                        res["spa_error"] = str(err)
                else:
                    res["rejected"] = []
                if len(series) >= 2:
                    try:
                        m_ = mcs(series, alpha=0.10, B=vc["bootstrap_B"], q=q, seed=batch["seed"])
                        res["mcs"] = {"set": m_["set"], "p": m_["p"], "alpha": m_["alpha"]}
                    except ValueError as err:
                        res["mcs_error"] = str(err)
                hp = {m: per_member[m][sym]["hac_p"] for m in w["members"]}
                ids = sorted(k for k, v in hp.items() if v is not None)
                res["diag_bh"] = sorted(k for k, ok in zip(ids, bh_fdr_family(
                    [hp[k] for k in ids], len(w["members"]), w["alpha"])) if ok)
                res["diag_by"] = sorted(k for k, ok in zip(ids, by_fdr(
                    [hp[k] for k in ids], len(w["members"]), w["alpha"])) if ok)
                tests[key] = res
        except (CostComputationError, CostConfigError) as err:
            for m in batch["members"]:
                self.statuses.set(m, ctx, "BLOCKED_COST", stage="validation", reasons=[str(err)],
                                  resume_to="RESEARCH_ONLY")
            append_jsonl(self.batch_path, {"kind": "RESULT", "batch_id": batch["batch_id"],
                                           "blocked": "BLOCKED_COST", "error": str(err), "at": iso_now()})
            return {"batch_id": batch["batch_id"], "blocked": "BLOCKED_COST"}
        outcome = {}
        for m in batch["members"]:
            syms = self.specs.get(m)["symbols"]
            keys = {w["symbol"]: k for k, w in batch["windows"].items()}
            rejected = all(m in tests[keys[s]]["rejected"] for s in syms)
            powered = all((per_member[m][s]["n"] or 0) >= vc["min_events_per_symbol"] for s in syms)
            fclass = validation_failure_class(per_member[m], syms, rejected=rejected, powered=powered,
                                              min_events=vc["min_events_per_symbol"])
            rec = self._append_eval({"stage": "validation", "strategy_id": m, "context_id": ctx,
                                     "status": "OK", "batch_id": batch["batch_id"],
                                     "per_symbol": per_member[m], "stepm_rejected": rejected,
                                     "powered": powered, "failure_class": fclass,
                                     "cost_model": self._cost_block()})
            if rejected and powered:
                outcome[m] = "PASSED_STEPM"
            elif powered:
                outcome[m] = "REJECTED"
                self.statuses.set(m, ctx, "REJECTED", stage="validation",
                                  reasons=["validation StepM did not reject H0 (mean <= 0)",
                                           f"failure_class={fclass}"],
                                  evidence={"validation": rec["record_id"]})
            else:
                outcome[m] = "INCONCLUSIVE"
                self.statuses.set(m, ctx, "INCONCLUSIVE", stage="validation",
                                  reasons=["validation underpowered", f"failure_class={fclass}"],
                                  evidence={"validation": rec["record_id"]})
        result = {"kind": "RESULT", "batch_id": batch["batch_id"], "tests": tests,
                  "outcome": outcome, "at": iso_now()}
        append_jsonl(self.batch_path, result)
        for m, o in outcome.items():
            if o == "PASSED_STEPM":
                self.robustness(m)
        return result

    def resume_pending_robustness(self) -> list[str]:
        """StepM passers whose robustness record is missing (interrupted run)
        or that were blocked only because PBO was not yet computable."""
        ctx = self.context_id()
        out = []
        for b in self._batches():
            if b["kind"] != "RESULT":
                continue
            for m, o in (b.get("outcome") or {}).items():
                st = self.statuses.get(m, ctx)
                if o != "PASSED_STEPM" or st is None:
                    continue
                if (st["status"] == "RESEARCH_ONLY" and self.evaluation("robustness", m) is None) or (
                        st["status"] == "BLOCKED_STATISTICS" and st["stage"] == "robustness"
                        and self._pbo()["computable"]):
                    self.robustness(m)
                    out.append(m)
        return out

    def variation_report(self) -> list[dict]:
        """Parent -> child DEV comparisons (Diebold-Mariano on the daily
        return differential).  DEV data only; informs human review and the
        failure memory, never the search ranking or any gate.

        Alignment (review of PR #4): the differential is formed only on the
        symbols BOTH strategies trade and only on the days BOTH series cover
        — a day outside one series' DEV window is not observed, it is not a
        zero return.  The comparison parent is the best parent by its DEV
        mean on that same common grid (chosen before the test is computed;
        the choice is recorded).  Too little overlap -> NOT_COMPUTED."""
        ctx = self.context_id()
        latest = {r["strategy_id"]: r for r in iter_jsonl(self.series_path) if r["context_id"] == ctx}

        def per_symbol(sid):
            r = latest.get(sid)
            if r is None:
                return None
            return {sym: {d0 + i: v for i, v in enumerate(vals)}
                    for sym, (d0, vals) in r["series"].items()}

        def aligned(a, b):
            syms = sorted(set(a) & set(b))
            days = sorted(set().union(*(set(a[s]) & set(b[s]) for s in syms))) if syms else []
            xa = [sum(a[s].get(d, 0.0) for s in syms if d in a[s] and d in b[s]) for d in days]
            xb = [sum(b[s].get(d, 0.0) for s in syms if d in a[s] and d in b[s]) for d in days]
            return syms, days, xa, xb
        out = []
        for rec in self.lineage.records():
            c = per_symbol(rec["child_id"])
            row = {"child": rec["child_id"], "parents": rec["parents"], "operator": rec["operator"],
                   "data": "dev", "use": "diagnostic only (never ranking or gating)"}
            ps = {p_: per_symbol(p_) for p_ in rec["parents"]}
            if c is None or not ps or any(v is None for v in ps.values()):
                out.append({**row, "status": "UNTESTED"})
                continue
            grids = {p_: aligned(c, v) for p_, v in ps.items()}
            usable = {p_: g for p_, g in grids.items() if g[1]}
            if not usable:
                out.append({**row, "status": "NOT_COMPUTED",
                            "reason": "no common symbol/day between child and parents"})
                continue
            best = max(sorted(usable), key=lambda p_: sum(usable[p_][3]) / len(usable[p_][3]))
            syms, days, xc, xp = usable[best]
            try:
                res = dm_test([a - b for a, b in zip(xc, xp)])
            except ValueError as err:
                out.append({**row, "status": "NOT_COMPUTED", "reason": str(err),
                            "compared_parent": best, "n_days": len(days)})
                continue
            out.append({**row, "status": "OK", "z": res["z"], "p_better": res["p_one_sided_better"],
                        "n_days": res["n"], "compared_parent": best, "symbols": syms,
                        "first_day": days[0], "last_day": days[-1]})
        return out

    # -- stage 3: robustness (filters only) ---------------------------------------------
    def _pbo(self) -> dict:
        ctx = self.context_id()
        rows = [r for r in iter_jsonl(self.series_path) if r["context_id"] == ctx]
        latest = {r["strategy_id"]: r for r in rows}
        if len(latest) < self.cfg["robustness"]["pbo_min_trials"]:
            return {"computable": False, "trials": len(latest)}
        pooled: dict[str, dict[int, float]] = {}
        for sid, r in latest.items():
            acc: dict[int, float] = {}
            for sym, (d0, vals) in r["series"].items():
                for i, v in enumerate(vals):
                    acc[d0 + i] = acc.get(d0 + i, 0.0) + v
            pooled[sid] = acc
        days = sorted({d for a in pooled.values() for d in a})
        sids = sorted(pooled)
        mat = [[pooled[s].get(d, 0.0) for s in sids] for d in days]
        pbo, n = pbo_cscv(mat, n_partitions=self.cfg["robustness"]["pbo_partitions"])
        return {"computable": math.isfinite(pbo), "pbo": pbo if math.isfinite(pbo) else None,
                "combinations": n, "trials": len(sids), "days": len(days)}

    def _dsr(self, sid: str) -> dict:
        """Deflated Sharpe Ratio (Bailey & López de Prado 2014) of the strategy's
        DEV daily series against ALL strategies with a DEV series in this
        context (the selection set).  Diagnostic only (non-gating): the
        number of effectively independent trials is unknowable here and the
        daily series of one search are strongly dependent."""
        from engine_lib import deflated_sharpe
        ctx = self.context_id()
        latest = {r["strategy_id"]: r for r in iter_jsonl(self.series_path) if r["context_id"] == ctx}

        def pooled(r):
            acc: dict[int, float] = {}
            for _sym, (d0, vals) in r["series"].items():
                for i, v in enumerate(vals):
                    acc[d0 + i] = acc.get(d0 + i, 0.0) + v
            days = sorted(acc)
            return [acc[d] for d in range(days[0], days[-1] + 1)] if days else []
        srs = {}
        for s_, r in latest.items():
            x = pooled(r)
            if len(x) < 30:
                continue
            mu = sum(x) / len(x)
            sd = (sum((v - mu) ** 2 for v in x) / (len(x) - 1)) ** 0.5
            if sd > 0:
                srs[s_] = (mu / sd, x)
        if sid not in srs or len(srs) < 2:
            return {"computable": False, "trials": len(srs)}
        sr, x = srs[sid]
        n = len(x)
        mu = sum(x) / n
        sd = (sum((v - mu) ** 2 for v in x) / (n - 1)) ** 0.5
        skew = sum(((v - mu) / sd) ** 3 for v in x) / n
        kurt = sum(((v - mu) / sd) ** 4 for v in x) / n          # Pearson (normal = 3)
        all_sr = [v[0] for v in srs.values()]
        m_ = sum(all_sr) / len(all_sr)
        var_sr = sum((v - m_) ** 2 for v in all_sr) / (len(all_sr) - 1)
        d = deflated_sharpe(sr, n, skew, kurt, len(srs), var_sr)
        # MinBTL (Bailey et al. 2014): raw trial count, 365 d/yr (24/7 market).
        from engine_lib import expected_max_sr, min_backtest_years
        sr_ann = sr * math.sqrt(365.0)
        floor_ann = expected_max_sr(len(srs)) / math.sqrt(n / 365.0)
        return {"minbtl_years": min_backtest_years(len(srs), sr_ann),
                "sr_annual": sr_ann, "sr_floor_annual": floor_ann,
                "exceeds_sr_floor": sr_ann > floor_ann,
                "computable": math.isfinite(d), "dsr": d if math.isfinite(d) else None,
                "sr_daily": sr, "n_days": n, "trials": len(srs), "var_sr": var_sr,
                "skew": skew, "kurtosis": kurt}

    def robustness(self, sid: str) -> dict:
        ctx = self.context_id()
        rc = self.cfg["robustness"]
        spec = canonicalize_spec(self.specs.get(sid))
        checks: dict[str, dict] = {}
        all_ev: list[dict] = []
        regime_ev: dict[str, list[float]] = {}
        val_halves: list[Optional[float]] = []
        for sym in spec["symbols"]:
            ev, v0, v1 = self._validation_events(sid, sym)
            all_ev += ev
            bars = self.provider.symbol(sym)["bars"]
            closes = [b.close for b in bars]
            vols = {}
            for x in ev:
                i = x["entry_index"] - 1
                r = [math.log(closes[j] / closes[j - 1]) for j in range(max(1, i - 95), i + 1)]
                vols[x["entry_index"]] = (sum(v * v for v in r) / max(1, len(r))) ** 0.5
                trend = closes[i] / closes[max(0, i - 672)] - 1.0
                x["_trend"] = trend
            med = sorted(vols.values())[len(vols) // 2] if vols else 0.0
            for x in ev:
                key = ("hivol" if vols[x["entry_index"]] > med else "lovol") + \
                      ("_up" if x.pop("_trend") > 0 else "_down")
                regime_ev.setdefault(key, []).append(x["net_bp"])
            if v1 > v0:
                mid = bars[(v0 + v1) // 2].ts
                for half in (lambda e: e["entry_ts"] < mid, lambda e: e["entry_ts"] >= mid):
                    hs = [x["net_bp"] for x in ev if half(x)]
                    val_halves.append(sum(hs) / len(hs) if hs else None)
        all_ev.sort(key=lambda x: x["entry_ts"])
        nets = [x["net_bp"] for x in all_ev]
        # cost scenarios
        stress = {}
        for mult in rc["cost_multipliers"]:
            xs = [x["gross_bp"] - mult * x["cost_bp"] - x["funding_bp"] for x in all_ev]
            stress[str(mult)] = sum(xs) / len(xs) if xs else None
        need = stress.get(str(rc["require_positive_at_cost_multiplier"]))
        checks["cost_stress"] = {"pass": need is not None and need > 0, "mean_bp": stress,
                                 "breakeven_multiplier": cost_breakeven(all_ev),
                                 # a multiple of the MODELLED cost: says nothing about real
                                 # execution cost while the model is unverified
                                 "breakeven_basis": ("TEST_ONLY_COST" if self.cost.verified and self.cost.test_only
                                                     else self._cost_block()["status"])}
        # regimes
        reg = {k: {"n": len(v), "mean_bp": sum(v) / len(v)} for k, v in regime_ev.items()}
        usable = [v for v in reg.values() if v["n"] >= rc["regime_min_events"]]
        share = (sum(1 for v in usable if v["mean_bp"] > 0) / len(usable)) if usable else 0.0
        checks["regimes"] = {"pass": bool(usable) and share >= rc["regime_min_positive_share"],
                             "positive_share": share, "regimes": reg}
        # drawdown
        mdd = _mdd(nets)
        checks["drawdown"] = {"pass": mdd <= rc["max_validation_mdd_bp"], "mdd_bp": mdd}
        # concentration
        total = sum(nets)
        pos = sorted((x for x in nets if x > 0), reverse=True)
        top = pos[:max(1, int(math.ceil(0.05 * len(nets))))] if pos else []
        top_share = (sum(top) / total) if total > 0 else math.inf
        by_day: dict[int, float] = {}
        for x in all_ev:
            by_day[x["exit_ts"] // DAY_MS] = by_day.get(x["exit_ts"] // DAY_MS, 0.0) + x["net_bp"]
        pos_days = [v for v in by_day.values() if v > 0]
        day_share = (max(pos_days) / sum(pos_days)) if pos_days else math.inf
        checks["concentration"] = {
            "pass": top_share <= rc["max_top5pct_event_profit_share"]
            and day_share <= rc["max_top_day_profit_share"],
            "top5pct_event_share": top_share if math.isfinite(top_share) else None,
            "top_day_share": day_share if math.isfinite(day_share) else None}
        # bootstrap CI of the mean daily validation return (recorded)
        days = sorted(by_day)
        ser = [by_day.get(d, 0.0) for d in range(days[0], days[-1] + 1)] if days else []
        ci = None
        if len(ser) >= 10:
            rng = random.Random(self.cfg["validation"]["bootstrap_seed"])
            q = default_q(len(ser))
            ms = sorted(sum(ser[i] for i in stationary_bootstrap_indices(len(ser), q, rng)) / len(ser)
                        for _ in range(500))
            a = (1 - rc["ci_level"]) / 2
            ci = [ms[int(a * 500)], ms[int((1 - a) * 500) - 1]]
        checks["bootstrap_ci"] = {"pass": True, "ci_mean_daily_bp": ci, "gating": False}
        # parameter neighbourhood (DEV data only)
        nb = neighbours(spec)
        nb_means = []
        for c in nb:
            nsid, _ = self.specs.put(c, source=f"neighbour_of:{sid}")
            r = self.dev_evaluate(nsid, record_status=False)
            nb_means.append((r.get("metrics") or {}).get("dev_mean_bp"))
        good = [m for m in nb_means if m is not None]
        nshare = (sum(1 for m in good if m > 0) / len(good)) if good else 0.0
        checks["parameter_neighbourhood"] = {"pass": bool(good) and
                                             nshare >= rc["min_param_neighbour_positive_share"],
                                             "positive_share": nshare, "n": len(good)}
        # walk-forward: dev folds + validation halves
        dev = self.evaluation("dev", sid)
        wf = list((dev.get("metrics") or {}).get("dev_folds_mean_bp") or []) + val_halves
        wf_known = [x for x in wf if x is not None]
        wshare = sum(1 for x in wf_known if x > 0) / len(wf) if wf else 0.0
        checks["walk_forward"] = {"pass": wshare >= rc["walk_forward_min_positive_share"],
                                  "positive_share": wshare, "segments_mean_bp": wf}
        # sample size
        checks["sample_size"] = {"pass": all(
            sum(1 for x in all_ev if x["symbol"] == s) >= self.cfg["validation"]["min_events_per_symbol"]
            for s in spec["symbols"]), "n": len(nets)}
        # overfitting probability of the whole search (CSCV on DEV series)
        pbo = self._pbo()
        checks["pbo"] = {"pass": bool(pbo["computable"]) and pbo["pbo"] <= rc["max_pbo"], **pbo}
        checks["dsr"] = {"pass": True, "gating": False, **self._dsr(sid)}
        failed = sorted(k for k, v in checks.items() if not v["pass"])
        rec = self._append_eval({"stage": "robustness", "strategy_id": sid, "context_id": ctx,
                                 "status": "OK", "checks": checks, "failed": failed,
                                 "config_hash": self.cfg_hash})
        ev = {"dev": dev["record_id"], "validation": self.evaluation("validation", sid)["record_id"],
              "robustness": rec["record_id"]}
        if not failed:
            self.statuses.set(sid, ctx, "VALIDATION_SURVIVOR", stage="robustness", evidence=ev)
        elif failed == ["pbo"] and not pbo["computable"]:
            self.statuses.set(sid, ctx, "BLOCKED_STATISTICS", stage="robustness",
                              reasons=[f"PBO not computable ({pbo['trials']} trials)"], evidence=ev,
                              resume_to="VALIDATION_SURVIVOR")
        else:
            self.statuses.set(sid, ctx, "REJECTED", stage="robustness",
                              reasons=[f"robustness failed: {', '.join(failed)}"], evidence=ev)
        return rec

    # -- stage 4: locked TEST campaigns ------------------------------------------------------
    def run_locked_campaign(self) -> Optional[dict]:
        ctx = self.context_id()
        lc = self.cfg["locked"]
        open_ = [c for c in self.campaigns.open_campaigns("evolution") if c["context_id"] == ctx]
        if open_:
            camp = open_[0]
        else:
            aborted = {r["campaign_id"] for r in self.campaigns.records()
                       if r["kind"] == "ABORTED_NO_EXPOSURE"}
            in_camp = {m for r in self.campaigns.records() if r["kind"] == "PREREGISTERED"
                       and r["campaign_id"] not in aborted for m in r["members"]}
            survivors = []
            for s, st in sorted(self.statuses.all(ctx).items()):
                rec = self.statuses.get(s, ctx)
                waiting = (st in ("BLOCKED_DATA", "BLOCKED_COST") and rec.get("stage") == "locked"
                           and rec.get("resume_to") == "VALIDATION_SURVIVOR")
                if (st == "VALIDATION_SURVIVOR" or waiting) and s not in in_camp:
                    survivors.append((s, st))
            members: dict[str, list[str]] = {}
            for sid, st in survivors:
                syms = self.specs.get(sid)["symbols"]
                blocked = None
                # The locked window is single-use per strategy+symbol. A result on an
                # unverified cost model can only end in BLOCKED_COST at promotion, so
                # the window is not spent until the cost model covers this use
                # (ADR 0009). Checked before any window lookup: nothing is reserved.
                cost_ok, cost_why = self.cost.verified_for(syms, self.order_value)
                if not cost_ok:
                    blocked = ("BLOCKED_COST",
                               f"cost not verified for this use ({'; '.join(cost_why)}) — "
                               "locked window preserved, not touched")
                if blocked is None:
                    # ADR 0010: another symbol's selection data inside this symbol's
                    # locked window means the window is not an independent test
                    from runner import locked_independence_issues
                    overlap = locked_independence_issues(syms)
                    if overlap:
                        blocked = ("BLOCKED_STATISTICS",
                                   "LOCKED_WINDOW_NOT_INDEPENDENT: " + "; ".join(overlap)
                                   + " — locked window not touched")
                for sym in syms if blocked is None else ():
                    ws = self.campaigns.window_status(sym, max_campaigns=lc["max_campaigns_per_window"])
                    es = self.lock_registry.entry_status(sid, sym)
                    if ws["retired"]:
                        blocked = ("BLOCKED_STATISTICS",
                                   f"{sym}: {ws['reason']} — new locked data required")
                    elif not es["enterable"]:
                        blocked = ("BLOCKED_STATISTICS",
                                   f"{sym}: locked window {es['reason']} for this strategy")
                    elif not self.provider.locked_ready(sym):
                        blocked = ("BLOCKED_DATA", f"{sym}: locked-window files not present "
                                                   "(window not touched)")
                if blocked:
                    if st != blocked[0]:
                        self.statuses.set(sid, ctx, blocked[0], stage="locked", reasons=[blocked[1]],
                                          resume_to="VALIDATION_SURVIVOR")
                else:
                    members[sid] = list(syms)
            if not members:
                return None
            ref = hashlib.sha256(canonical_json(members).encode()).hexdigest()[:16]
            try:
                camp = self.campaigns.preregister(
                    source="evolution", source_ref=f"{ctx}:{ref}", context_id=ctx, members=members,
                    alpha_total=window_alpha_total(lc),
                    max_campaigns=lc["max_campaigns_per_window"],
                    method="one-sided HAC t per symbol, Bonferroni alpha_c/m_c, c-th campaign alpha "
                           "spending", config_hash=self.cfg_hash)
            except WindowRetired as e:          # pragma: no cover - filtered above
                return {"blocked": str(e)}
        return self._run_campaign(camp)

    def _run_campaign(self, camp: Mapping) -> dict:
        ctx = self.context_id()
        members = camp["members"]
        tokens: dict = {}
        refused: dict = {}
        recovered: dict = {}
        for sid, syms in sorted(members.items()):
            for sym in syms:
                prior = self.lock_registry.result(sid, sym)
                if prior and prior.get("campaign_id") == camp["campaign_id"]:
                    recovered[(sid, sym)] = prior        # completed before an interruption
                    continue
                r = self.lock_registry.reserve(sid, sym, ctx)
                if r["ok"]:
                    tokens[(sid, sym)] = r["token"]
                else:
                    refused[sid] = r["reason"]
        syms_all = sorted({s for v in members.values() for s in v})
        loaded, missing = {}, {}
        for sym in syms_all:
            if all((sid, sym) in recovered for sid in members if sym in members[sid]):
                continue
            d = self.provider.locked(sym)
            if d["missing"] or not d["bars"]:
                missing[sym] = d["missing"] or ["no locked bars"]
            else:
                loaded[sym] = d
        if missing:
            for (sid, sym), tok in tokens.items():
                self.lock_registry.block(tok, f"locked data missing: {missing}")
            self.campaigns.abort_no_exposure(camp, f"locked data missing {missing}")
            for sid in members:
                self.statuses.set(sid, ctx, "BLOCKED_DATA", stage="locked",
                                  reasons=[f"locked data missing for {sorted(missing)} "
                                           "(window not consumed)"], resume_to="VALIDATION_SURVIVOR")
            return {"campaign_id": camp["campaign_id"], "blocked": "BLOCKED_DATA", "missing": missing}
        for sid in members:
            cur = self.statuses.get(sid, ctx)
            if cur and cur["status"] in _BLOCKED and cur.get("resume_to") == "VALIDATION_SURVIVOR":
                self.statuses.set(sid, ctx, "VALIDATION_SURVIVOR", stage="locked",
                                  reasons=["resumed: locked data now available"])
        results: dict = {}
        lmin = self.cfg["locked"]["min_events_per_symbol"]
        for sid, syms in sorted(members.items()):
            if sid in refused:
                for sym in syms:
                    if (sid, sym) in tokens:
                        self.lock_registry.fail(tokens[(sid, sym)], f"sibling refused: {refused[sid]}")
                results[sid] = {"status": "REFUSED", "reason": refused[sid]}
                self.statuses.set(sid, ctx, "BLOCKED_STATISTICS", stage="locked",
                                  reasons=[f"locked reservation refused ({refused[sid]})"],
                                  resume_to="VALIDATION_SURVIVOR")
                continue
            spec = canonicalize_spec(self.specs.get(sid))
            fng = self.provider.fng() if "fng" in spec["data"] else None
            per = {}
            try:
                for sym in syms:
                    if (sid, sym) in recovered:
                        per[sym] = recovered[(sid, sym)]
                    else:
                        self.lock_registry.start(tokens[(sid, sym)])
                for sym in syms:
                    if sym in per:
                        continue
                    d = loaded[sym]
                    pre = self.provider.symbol(sym)
                    tail = pre["bars"][-4096:]
                    bars = list(tail) + list(d["bars"])
                    if tail and d["bars"][0].ts - tail[-1].ts != 900_000:
                        bars, warm = list(d["bars"]), 1        # no contiguous warm-up history
                    else:
                        warm = len(tail)
                    fund = sorted({*(pre["funding"] or []), *d["funding"]})
                    ev = self._study(spec, sym, bars, fund, fng, warm, len(bars) - 1)
                    nets = [x["net_bp"] for x in ev]
                    t, p = _hac(nets) if len(nets) >= 3 else (float("nan"), float("nan"))
                    a = member_alpha(camp, sym)
                    mean = sum(nets) / len(nets) if nets else None
                    ok = (len(nets) >= lmin and mean is not None and mean > 0
                          and math.isfinite(p) and p <= a)
                    res = {"n": len(nets), "mean_bp": mean, "hac_t": t if math.isfinite(t) else None,
                           "hac_p": p if math.isfinite(p) else None, "alpha_member": a, "pass": ok,
                           "powered": len(nets) >= lmin, "lock_data_sha256": d.get("sha256"),
                           "campaign_id": camp["campaign_id"]}
                    self.lock_registry.complete(tokens[(sid, sym)], res)
                    per[sym] = res
            except Exception as err:   # noqa: BLE001 — exposure is recorded, never undone
                for sym in syms:
                    if sym not in per:
                        try:
                            self.lock_registry.fail(tokens[(sid, sym)], f"{type(err).__name__}: {err}")
                        except Exception:  # noqa: BLE001
                            pass
                results[sid] = {"status": "ERROR", "error": f"{type(err).__name__}: {err}"}
                st = "BLOCKED_COST" if isinstance(err, (CostComputationError, CostConfigError)) \
                    else "BLOCKED_STATISTICS"
                self.statuses.set(sid, ctx, st, stage="locked",
                                  reasons=[f"locked evaluation failed after exposure: {err}"],
                                  resume_to="VALIDATION_SURVIVOR")
                continue
            rec = self._append_eval({"stage": "locked", "strategy_id": sid, "context_id": ctx,
                                     "status": "OK", "campaign_id": camp["campaign_id"],
                                     "per_symbol": per, "cost_model": self._cost_block()})
            passed = all(v["pass"] for v in per.values())
            powered = all(v["powered"] for v in per.values())
            results[sid] = {"pass": passed, "powered": powered, "record_id": rec["record_id"]}
            if passed:
                self.promotion_review(sid)
            elif powered:
                self.statuses.set(sid, ctx, "REJECTED", stage="locked",
                                  reasons=["locked TEST not significant at the campaign alpha"],
                                  evidence={"locked": rec["record_id"]})
            else:
                self.statuses.set(sid, ctx, "INCONCLUSIVE", stage="locked",
                                  reasons=["locked TEST underpowered (window consumed)"],
                                  evidence={"locked": rec["record_id"]})
        self.campaigns.record_result(camp, results)
        return {"campaign_id": camp["campaign_id"], "results": results}

    # -- stage 5: promotion review ---------------------------------------------------------
    def promotion_review(self, sid: str) -> dict:
        ctx = self.context_id()
        pc = self.cfg["promotion"]
        evidence, failures = {}, []
        for stage in pc["required_evidence"]:
            r = self.evaluation(stage, sid)
            if r is None:
                failures.append(f"missing {stage} evidence")
                continue
            evidence[stage] = r["record_id"]
        dev = self.evaluation("dev", sid) or {}
        val = self.evaluation("validation", sid) or {}
        rob = self.evaluation("robustness", sid) or {}
        lock = self.evaluation("locked", sid) or {}
        if not (dev.get("screen") or {}).get("pass"):
            failures.append("dev screen not passed")
        if not (val.get("stepm_rejected") and val.get("powered")):
            failures.append("validation StepM not passed")
        if rob.get("failed"):
            failures.append(f"robustness failed {rob['failed']}")
        if not lock or not all(v.get("pass") for v in (lock.get("per_symbol") or {}).values()) \
                or not lock.get("per_symbol"):
            failures.append("locked campaign not passed")
        for s in canonicalize_spec(self.specs.get(sid))["symbols"]:
            if self.lock_registry.uses(sid, s) != 1:
                failures.append(f"{s}: locked window not used exactly once")
        labels = []
        # PAPER_TEST_READY needs a VERIFIED cost model (calibrated against recorded
        # fills/books). The old config switch ``allow_unverified_cost_for_paper`` is
        # no longer honoured: an unverified model leaves the candidate research-only.
        # ``verified`` itself can only be True with a sealed calibration record
        # (costs_bridge.CostContext); the record must also cover this use.
        cost_ok, cost_why = self.cost.verified_for(
            canonicalize_spec(self.specs.get(sid))["symbols"], self.order_value)
        if not cost_ok:
            labels.append(pc.get("unverified_cost_label", UNVERIFIED_COST_MODEL))
            if self.cost.status != UNVERIFIED_COST_MODEL and not self.cost.verified:
                labels.append(self.cost.status)       # ASSUMED_COST_MODEL / ESTIMATED_FROM_PUBLIC_DATA
        if self.cost.test_only:
            labels.append("TEST_ONLY_COST")
        review = {"strategy_id": sid, "context_id": ctx, "config_hash": self.cfg_hash,
                  "evidence": evidence, "failures": failures, "labels": labels,
                  "cost_verified": cost_ok, "cost_scope_issues": cost_why,
                  "cost_verification_record": (self.cost.verification or {}).get("record_id"),
                  "decision": ("PAPER_TEST_READY" if not failures and cost_ok
                               else "BLOCKED_COST" if not failures else "NOT_ELIGIBLE"),
                  "scope": "paper trading / isolated simulation only — never live orders",
                  "at": iso_now()}
        append_jsonl(self.review_path, review)
        if review["decision"] == "PAPER_TEST_READY":
            self.statuses.set(sid, ctx, "PAPER_TEST_READY", stage="promotion", evidence=evidence,
                              labels=labels)
        elif review["decision"] == "BLOCKED_COST":
            self.statuses.set(sid, ctx, "BLOCKED_COST", stage="promotion",
                              reasons=[f"{self.cost.status}: research candidate only, not promotable to PAPER_TEST_READY"],
                              evidence=evidence, resume_to="VALIDATION_SURVIVOR")
        else:
            self.statuses.set(sid, ctx, "BLOCKED_STATISTICS", stage="promotion", reasons=failures,
                              evidence=evidence, resume_to="VALIDATION_SURVIVOR")
        return review


def cost_attribution(events: Sequence[Mapping]) -> dict:
    """Before / after cost on the SAME events (same rule, same window): mean
    gross move, mean trading cost, mean funding and mean net, in bp per
    trade.  Events the cost model refused are not in ``events`` — the
    comparison is between pricing and not pricing the trades that were
    actually taken, never a re-optimised strategy."""
    n = len(events)
    if not n:
        return {"gross_mean_bp": None, "cost_mean_bp": None, "funding_mean_bp": None,
                "net_mean_bp": None}
    return {"gross_mean_bp": sum(x["gross_bp"] for x in events) / n,
            "cost_mean_bp": sum(x["cost_bp"] for x in events) / n,
            "funding_mean_bp": sum(x["funding_bp"] for x in events) / n,
            "net_mean_bp": sum(x["net_bp"] for x in events) / n}


#: primary reason a validation member did not pass (reporting, never a gate)
FAILURE_CLASSES = ("PASSED_STEPM", "DATA_MISSING", "INSUFFICIENT_DATA", "NO_EDGE_OUT_OF_SAMPLE",
                   "FAIL_COST", "FAIL_STAT")


def validation_failure_class(per_symbol: Mapping, symbols: Sequence[str], *, rejected: bool,
                             powered: bool, min_events: int) -> str:
    """Why a member ended where it did, in the order a reader should act on:

    * DATA_MISSING          – a required symbol produced no validation event
    * INSUFFICIENT_DATA     – fewer events than the preregistered minimum (UNDETERMINED)
    * NO_EDGE_OUT_OF_SAMPLE – mean GROSS move <= 0 on a symbol: the DEV edge did
      not carry over (overfitting / decay), costs are not the reason
    * FAIL_COST             – gross > 0 but net <= 0 on a symbol: trading cost or
      funding removed the edge
    * FAIL_STAT             – net > 0 everywhere but StepM did not reject (too weak
      or too unstable for the batch-level FWER test)
    """
    if rejected and powered:
        return "PASSED_STEPM"
    infos = [per_symbol.get(s) or {} for s in symbols]
    if any(not (i.get("n") or 0) for i in infos):
        return "DATA_MISSING"
    if any((i.get("n") or 0) < min_events for i in infos):
        return "INSUFFICIENT_DATA"
    if any((i.get("gross_mean_bp") or 0.0) <= 0 for i in infos):
        return "NO_EDGE_OUT_OF_SAMPLE"
    if any((i.get("net_mean_bp") or 0.0) <= 0 for i in infos):
        return "FAIL_COST"
    return "FAIL_STAT"


#: the outcome vocabulary of the 2026-10-20 directive.  A VIEW of the stored
#: status + evidence (STATISTICS_AND_STATES.md has the table); nothing stores it.
#: RUNNING exists only for a cycle (``CYCLE_RUNNING`` in the summary file): a
#: single evaluation is one append and is never left half-written.
OUTCOME_LABELS = ("PENDING", "RUNNING", "PASS", "FAIL_COST", "FAIL_STAT", "UNDETERMINED_POWER",
                  "DATA_MISSING", "ERROR", "REVERIFY_PENDING")


def outcome_label(status: Optional[Mapping], *, dev: Optional[Mapping] = None,
                  validation: Optional[Mapping] = None, reverify: bool = False) -> str:
    """Map one strategy's latest status record (and its DEV / validation
    evaluation records) to :data:`OUTCOME_LABELS`."""
    if reverify:
        return "REVERIFY_PENDING"
    st = (status or {}).get("status")
    if st is None:
        if dev is not None and dev.get("status") not in (None, "OK"):
            return {"BLOCKED_DATA": "DATA_MISSING", "BLOCKED_COST": "ERROR"}.get(dev["status"], "ERROR")
        return "PENDING"
    if st in ("VALIDATION_SURVIVOR", "PAPER_TEST_READY", "PAPER_TESTING", "PAPER_PASSED"):
        return "PASS"
    if st == "RESEARCH_ONLY":
        return "PENDING"                      # passed the DEV screen, waits for validation
    if st == "BLOCKED_DATA":
        return "DATA_MISSING"
    if st == "BLOCKED_COST":
        # a cost the model refused or could not compute; a priced-but-too-expensive
        # trade is FAIL_COST below
        return "ERROR"
    if st == "BLOCKED_STATISTICS":
        return "UNDETERMINED_POWER"
    if st == "PAPER_FAILED":
        return "FAIL_STAT"
    fc = (validation or {}).get("failure_class") if (status or {}).get("stage") == "validation" else None
    if fc is not None:
        return {"DATA_MISSING": "DATA_MISSING", "INSUFFICIENT_DATA": "UNDETERMINED_POWER",
                "NO_EDGE_OUT_OF_SAMPLE": "FAIL_STAT", "FAIL_COST": "FAIL_COST",
                "FAIL_STAT": "FAIL_STAT", "PASSED_STEPM": "FAIL_STAT"}.get(fc, "FAIL_STAT")
    if st == "INCONCLUSIVE":
        return "UNDETERMINED_POWER"
    # DEV screen rejection: positive before cost, not after -> the cost removed it
    m = (dev or {}).get("metrics") or {}
    g, n = m.get("dev_gross_mean_bp"), m.get("dev_mean_bp")
    if g is not None and n is not None and g > 0 >= n:
        return "FAIL_COST"
    return "FAIL_STAT"


def cost_breakeven(events: Sequence[Mapping]) -> Optional[float]:
    """Multiple of the modelled trading cost at which the mean net result is 0:
    mean(gross - funding) / mean(cost).  Cost sensitivity as a sanity check (a
    strategy that dies at 1.1x the assumed cost has no margin); informational,
    never a gate.  None when it cannot be computed (no events / no cost)."""
    if not events:
        return None
    n = len(events)
    cost = sum(x["cost_bp"] for x in events) / n
    if not cost > 0:
        return None
    return sum(x["gross_bp"] - x["funding_bp"] for x in events) / n / cost


class _Ev:
    """Adapter: event dict -> attribute access for evaluator.cell_stats."""
    __slots__ = ("net_bp",)

    def __init__(self, d: Mapping):
        self.net_bp = d["net_bp"]


def seed_specs_from_legacy(symbols: Sequence[str], available_data: Sequence[str]) -> list[tuple[str, dict]]:
    """(legacy strategy id, translated spec) for every legacy candidate whose
    data is available; symbols restricted to ``symbols``."""
    from generator import enumerate_candidates
    from strategy_spec import SpecError, legacy_to_spec
    out = []
    for lsid, d in enumerate_candidates()[0]:
        try:
            sp = legacy_to_spec({**d, "symbols": [s for s in d["symbols"] if s in symbols]})
        except SpecError:
            continue
        if set(sp["data"]) <= set(available_data):
            out.append((lsid, sp))
    return out
