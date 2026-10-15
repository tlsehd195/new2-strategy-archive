"""One automatic research cycle (auto-evolution §7).

data check -> seed (legacy translations + exploration) -> pending DEV
evaluations -> search rounds (propose, register with lineage, DEV evaluate)
-> preregistered validation batch -> robustness -> locked campaign ->
promotion review -> isolated paper replay -> summary.

Cycle statuses: CYCLE_OK, CYCLE_NO_PROGRESS, CYCLE_BUDGET_EXHAUSTED,
CYCLE_BLOCKED_DATA (no usable market data — nothing was evaluated; exit 4),
CYCLE_FAILED.  The search state, every specification, lineage edge,
evaluation and status are persisted as they happen, so an interrupted cycle
resumes where it stopped.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from engine_lib import append_jsonl, atomic_write_json, iso_now
from search import (
    SearchConfig, SearchState, first_front_hash, propose_round, select_elite,
)

CYCLE_EXIT = {"CYCLE_OK": 0, "CYCLE_NO_PROGRESS": 0, "CYCLE_BUDGET_EXHAUSTED": 0,
              "CYCLE_FAILED": 1, "CYCLE_BLOCKED_DATA": 4}


@dataclass
class EvolutionCycleSummary:
    status: str = "CYCLE_OK"
    started_at: str = ""
    finished_at: str = ""
    context_id: str = ""
    symbols_used: list = field(default_factory=list)
    symbols_blocked: dict = field(default_factory=dict)
    seeded: int = 0
    dev_evaluated: int = 0
    rounds: int = 0
    proposed: int = 0
    invalid: int = 0
    duplicates: int = 0
    search_status: str = ""
    validation_batch: Optional[dict] = None
    robustness_resumed: list = field(default_factory=list)
    campaign: Optional[dict] = None
    paper: list = field(default_factory=list)
    status_counts: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    runtime_seconds: float = 0.0

    def to_json(self) -> dict:
        return asdict(self)


def run_evolution_cycle(state_dir: Path, provider, cost, *, symbols: Sequence[str] = ("BTCUSDT", "ETHUSDT"),
                        search_cfg: Optional[SearchConfig] = None, rounds: int = 2,
                        max_dev_evals: int = 60, seed_legacy: bool = True,
                        promotion_config: Optional[Path] = None,
                        results_dir: Optional[Path] = None) -> EvolutionCycleSummary:
    from paper import PaperRefused, run_paper
    from pipeline import EvolutionPipeline, seed_specs_from_legacy
    t0 = time.time()
    s = EvolutionCycleSummary(started_at=iso_now())
    cfg = search_cfg or SearchConfig()
    # 1) data check — fail closed, never synthesise
    usable = []
    for sym in symbols:
        e = provider.symbol(sym)
        if e["missing"]:
            s.symbols_blocked[sym] = e["missing"]
        else:
            usable.append(sym)
    s.symbols_used = usable
    if not usable:
        s.status = "CYCLE_BLOCKED_DATA"
        s.notes.append("no usable market data for any symbol — nothing evaluated (BLOCKED_DATA)")
        return _finish(s, t0, results_dir)
    pipe = EvolutionPipeline(state_dir, provider, cost, symbols=usable, search_cfg=cfg,
                             promotion_config=promotion_config)
    ctx = pipe.context_id()
    s.context_id = ctx
    avail = provider.available_data()
    state_path = pipe.dir / "search_state.json"
    st = SearchState.load(state_path, config_hash=cfg.config_hash(), context_id=ctx)
    budget = {"max_rounds": cfg.max_rounds, "max_specs_total": cfg.max_specs_total,
              "children_per_round": cfg.children_per_round}
    # 2) seed once per (search config, context)
    if st.round_index == 0 and not st.history:
        if seed_legacy:
            for lsid, sp in seed_specs_from_legacy(usable, avail):
                sid, created = pipe.register(sp, source=f"legacy:{lsid}", parents=[lsid],
                                             operator="legacy_translation", seed=cfg.seed,
                                             budget=budget)
                s.seeded += created
        st.history.append({"event": "seeded", "n": s.seeded, "at": iso_now()})
        st.save(state_path)
    evaluated = 0

    def _pending() -> list[str]:
        from strategy_spec import canonicalize_spec
        out = []
        for sid, d in sorted(pipe.specs.all().items()):
            if pipe.evaluation("dev", sid) is not None:
                continue
            src_ok = pipe.statuses.status(sid, ctx) is None
            try:
                ok = src_ok and set(canonicalize_spec(d)["symbols"]) <= set(usable)
            except Exception:  # noqa: BLE001 — not a spec (never registered here)
                ok = False
            if ok:
                out.append(sid)
        return out

    # 3) pending DEV evaluations (seeds, interrupted rounds) — neighbours excluded
    neighbour_ids = {r["strategy_id"] for r in _spec_sources(pipe) if r["source"].startswith("neighbour_of:")}
    seed_budget = max_dev_evals if rounds <= 0 else max(1, max_dev_evals // 2)
    for sid in _pending():
        if evaluated >= seed_budget:
            break
        if sid in neighbour_ids:
            continue
        pipe.dev_evaluate(sid)
        evaluated += 1
    # 4) search rounds
    for _ in range(rounds):
        if st.status != "ACTIVE":
            break
        if st.round_index >= cfg.max_rounds or len(pipe.specs.all()) >= cfg.max_specs_total:
            st.status = "BUDGET_EXHAUSTED"
            break
        if evaluated >= max_dev_evals:
            s.notes.append("per-cycle DEV evaluation budget reached")
            break
        fb = pipe.dev_feedback_all()
        elite = select_elite(fb, cfg)
        specs = {sid: pipe.specs.get(sid) for sid in elite}
        round_index = st.round_index + 1
        props, cnt = propose_round(cfg=cfg, round_index=round_index, elite=elite, specs=specs,
                                   feedback=fb, available_data=avail,
                                   known_ids=set(pipe.specs.all()), symbols=usable)
        for p in props:
            if evaluated >= max_dev_evals:
                break
            sid, created = pipe.register(p["spec"], source="evolution", parents=p["parents"],
                                         operator=p["operator"], detail=p["detail"],
                                         round_index=round_index, seed=cfg.seed, budget=budget)
            if created:
                pipe.dev_evaluate(sid)
                evaluated += 1
        st.round_index = round_index
        st.proposed += len(props)
        st.invalid += cnt["invalid"]
        st.duplicates += cnt["duplicates"]
        st.registered = len(pipe.specs.all())
        fh = first_front_hash(pipe.dev_feedback_all(), cfg)
        st.rounds_without_front_change = 0 if fh != st.front_hash else st.rounds_without_front_change + 1
        st.front_hash = fh
        if cnt["attempts"] and cnt["invalid"] / cnt["attempts"] > cfg.max_invalid_ratio:
            st.status = "NO_VALID_PROPOSALS"
        elif st.rounds_without_front_change >= cfg.patience:
            st.status = "CONVERGED"
        rec = {"round_index": round_index, "elite": elite, "proposed": len(props), **cnt,
               "front_hash": fh, "status": st.status, "context_id": ctx,
               "config_hash": cfg.config_hash(), "at": iso_now()}
        append_jsonl(pipe.dir / "rounds.jsonl", rec)
        st.history.append({k: rec[k] for k in ("round_index", "proposed", "invalid", "status")})
        st.history = st.history[-200:]
        st.save(state_path)
        s.rounds += 1
        s.proposed += len(props)
        s.invalid += cnt["invalid"]
        s.duplicates += cnt["duplicates"]
    st.save(state_path)
    s.dev_evaluated = evaluated
    s.search_status = st.status
    # 5) confirmatory stages
    s.robustness_resumed = pipe.resume_pending_robustness()
    vb = pipe.run_validation()
    if vb is not None:
        s.validation_batch = {"batch_id": vb.get("batch_id"), "outcome": vb.get("outcome"),
                              "blocked": vb.get("blocked")}
    s.campaign = pipe.run_locked_campaign()
    # 6) isolated paper replay of newly ready strategies
    for sid, stt in sorted(pipe.statuses.all(ctx).items()):
        if stt == "PAPER_TEST_READY":
            try:
                r = run_paper(pipe, sid)
                s.paper.append({"strategy_id": sid, "status": r["status"], "verdict": r.get("verdict")})
            except PaperRefused:
                pass
    counts: dict = {}
    for v in pipe.statuses.all(ctx).values():
        counts[v] = counts.get(v, 0) + 1
    s.status_counts = counts
    if st.status == "BUDGET_EXHAUSTED":
        s.status = "CYCLE_BUDGET_EXHAUSTED"
    elif evaluated == 0 and vb is None and not s.campaign:
        s.status = "CYCLE_NO_PROGRESS"
    if s.symbols_blocked:
        s.notes.append(f"symbols blocked by data: {sorted(s.symbols_blocked)}")
    return _finish(s, t0, results_dir or (Path(state_dir) / "evolution"))


def _spec_sources(pipe) -> list[dict]:
    from engine_lib import iter_jsonl
    return list(iter_jsonl(pipe.specs.path))


def _finish(s: EvolutionCycleSummary, t0: float, results_dir: Optional[Path]) -> EvolutionCycleSummary:
    s.runtime_seconds = time.time() - t0
    s.finished_at = iso_now()
    if results_dir is not None:
        Path(results_dir).mkdir(parents=True, exist_ok=True)
        atomic_write_json(Path(results_dir) / "evolution_cycle_summary.json", s.to_json())
    return s
