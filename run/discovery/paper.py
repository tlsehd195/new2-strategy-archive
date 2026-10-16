"""Isolated paper replay of PAPER_TEST_READY strategies (auto-evolution §6).

This is NOT live or exchange paper trading: it replays the strategy bar by
bar on stored market data that lies strictly AFTER the symbol's locked TEST
window (data no research stage has used), with the same causal signal,
cost model and funding treatment as the research engine.  No network, no
exchange client, no order API is imported or called — the module refuses
to run if it is asked for anything but replay.

Each strategy gets ONE paper run per context (the paper window is then
consumed, like a locked window).  The replay can FAIL a strategy (mean <= 0
after costs) but never pass it: ``PAPER_PASSED`` is reserved for the
preregistered forward observation on bars that did not exist at promotion
(``paper_forward.py``).  Results are written as ``paper`` stage
evidence and as a research-feedback record.  The search never reads them
(search.dev_feedback only accepts DEV records): paper outcomes inform the
human review and the failure memory, not the generator's ranking.
"""

from __future__ import annotations

import math

from engine_lib import append_jsonl, iso_now, iter_jsonl
from strategy_spec import canonicalize_spec

PAPER_MODE = "isolated_replay_only"


class PaperRefused(RuntimeError):
    pass


def paper_runs(pipeline) -> list[dict]:
    return list(iter_jsonl(pipeline.dir / "paper_runs.jsonl"))


def run_paper(pipeline, sid: str, *, mode: str = PAPER_MODE) -> dict:
    """Replay ``sid`` on post-locked data.  Only PAPER_TEST_READY strategies,
    only in replay mode, only once per (strategy, context)."""
    if mode != PAPER_MODE:
        raise PaperRefused(f"paper mode {mode!r} refused: only {PAPER_MODE} exists "
                           "(no live / exchange execution is implemented)")
    ctx = pipeline.context_id()
    st = pipeline.statuses.status(sid, ctx)
    # PAPER_TESTING without a recorded run = a replay that was interrupted: resumable
    if st not in ("PAPER_TEST_READY", "PAPER_TESTING"):
        raise PaperRefused(f"{sid} is {st}, not PAPER_TEST_READY")
    if any(r["strategy_id"] == sid and r["context_id"] == ctx for r in paper_runs(pipeline)):
        raise PaperRefused(f"{sid}: paper window already used in this context")
    cfg = pipeline.cfg["paper"]
    spec = canonicalize_spec(pipeline.specs.get(sid))
    fng = pipeline.provider.fng() if "fng" in spec["data"] else None
    per, missing = {}, []
    for sym in spec["symbols"]:
        d = pipeline.provider.paper(sym)
        if d.get("missing") or not d.get("bars"):
            missing += [f"{sym}: {m}" for m in (d.get("missing") or ["no bars"])]
            continue
        ev = pipeline._study(spec, sym, d["bars"], d["funding"], fng, 1, len(d["bars"]) - 1)
        nets = [x["net_bp"] for x in ev]
        per[sym] = {"n": len(nets), "mean_bp": sum(nets) / len(nets) if nets else None,
                    "sum_bp": sum(nets), "first_ts": d["bars"][0].ts, "last_ts": d["bars"][-1].ts}
    run = {"strategy_id": sid, "context_id": ctx, "mode": PAPER_MODE, "is_simulation": True,
           "orders_sent": 0, "at": iso_now()}
    if missing:
        run.update({"status": "BLOCKED_DATA", "missing": missing})
        # a blocked replay does not consume the paper window
        append_jsonl(pipeline.dir / "paper_blocked.jsonl", run)
        return run
    if st == "PAPER_TEST_READY":
        pipeline.statuses.set(sid, ctx, "PAPER_TESTING", stage="paper",
                              reasons=["isolated replay started"])
    n = sum(v["n"] for v in per.values())
    tot = sum(v["sum_bp"] for v in per.values())
    mean = tot / n if n else None
    if n < cfg["min_events"]:
        verdict = "PAPER_INCONCLUSIVE"
    elif mean is not None and mean > 0:
        verdict = "PAPER_CONSISTENT"
    else:
        verdict = "PAPER_DEGRADED"
    lock = pipeline.evaluation("locked", sid) or {}
    lmeans = [v.get("mean_bp") for v in (lock.get("per_symbol") or {}).values()
              if v.get("mean_bp") is not None]
    run.update({"status": "OK", "per_symbol": per, "n": n, "mean_bp": mean, "verdict": verdict,
                "locked_mean_bp": (sum(lmeans) / len(lmeans)) if lmeans else None})
    append_jsonl(pipeline.dir / "paper_runs.jsonl", run)
    pipeline._append_eval({"stage": "paper", "strategy_id": sid, "context_id": ctx, "status": "OK",
                           "verdict": verdict, "n": n,
                           "mean_bp": mean if mean is None or math.isfinite(mean) else None,
                           "is_simulation": True})
    # A replay on stored history is never enough for PAPER_PASSED (a positive mean is
    # not a preregistered paper criterion): PAPER_CONSISTENT keeps PAPER_TESTING and
    # the strategy waits for the forward observation (paper_forward.py).  A degraded
    # replay fails closed.
    if verdict == "PAPER_DEGRADED":
        pipeline.statuses.set(sid, ctx, "PAPER_FAILED", stage="paper",
                              reasons=["replay mean <= 0 after costs"])
    # PAPER_INCONCLUSIVE (too few events): stays PAPER_TESTING, the window is consumed
    append_jsonl(pipeline.dir / "research_feedback.jsonl",
                 {"strategy_id": sid, "context_id": ctx, "source": "paper_replay",
                  "verdict": verdict, "mean_bp": mean, "n": n,
                  "use": "human review + failure memory only; never search ranking",
                  "at": iso_now()})
    return run
