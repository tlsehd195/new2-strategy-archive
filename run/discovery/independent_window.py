"""Preregistered independent validation window (ADR 0016).

The BTC/ETH validation windows have been looked at seven times.  This module
opens a NEW, time-disjoint window: Binance USD-M 15m data of 2020, the year
before ``runner.READ_START``, for BTC/ETH and the largest other coins by
market cap (``config/independent_window_v1.json``, fixed before any strategy
saw these data).

Three steps, each a CLI subcommand:

``check``           overlap with every window already used + data readiness
                    per symbol (quality gate only, no strategy is evaluated)
``freeze-members``  write the members file of one DEV-only state directory
                    (ids, canonical specs, context) before anything is opened
``power``           power precheck from the preregistered numbers (diagnostic)
``open``            open ONE tranche, once: needs an ACTIVE preregistration
                    (``prereg_gate``, ADR 0029); the run is registered, the
                    history row is written, the run is started, every member
                    is evaluated, StepM decides, the result file is written
                    (never over an existing one) and the run is completed

A tranche whose key is already in ``validation_window_history.jsonl`` is
never opened again.  An outcome is research evidence only: the cost stays
unverified, so locked TEST / paper / promotion rules are unchanged.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from engine_lib import (  # noqa: E402
    LOCKED_TEST_PERIODS, append_jsonl, canonical_json, iso_now, iter_jsonl, strategy_id,
)
from stats_mc import by_fdr, daily_series, default_q, spending_alpha, stepm  # noqa: E402

DEFAULT_PREREG = HERE / "config" / "independent_window_v1.json"
DEFAULT_HISTORY = HERE / "validation_window_history.jsonl"
#: lifecycle ledger of independent runs (ADR 0029): REGISTERED -> STARTED -> COMPLETED
DEFAULT_REGISTRY = HERE / "validation_registry.jsonl"
MEMBERS_SCHEMA = "independent_window_members_v1"
RESULT_SCHEMA = "independent_window_result_v1"
#: datasets that exist for 2020 in the public archive
AVAILABLE_DATA = ("klines", "funding")
DAY_MS = 86_400_000


class WindowRefused(RuntimeError):
    """The window may not be opened (reuse, overlap, missing data, bad members)."""


def _ms(day: str) -> int:
    return int(dt.datetime.fromisoformat(day + "T00:00:00+00:00").timestamp() * 1000)


def _end_ms(day: str) -> int:
    return _ms(day) + DAY_MS - 1


def load_prereg(path: Optional[Path] = None) -> tuple[dict, str]:
    p = Path(path or DEFAULT_PREREG)
    raw = p.read_bytes()
    pre = json.loads(raw)
    if pre.get("schema") != "independent_window_prereg_v1":
        raise WindowRefused(f"{p}: unknown schema {pre.get('schema')!r}")
    return pre, hashlib.sha256(raw).hexdigest()


def window_key(pre: Mapping, tranche: str) -> str:
    return f"{pre['window_id']}-{tranche}:POOLED:{_ms(pre['period']['entry_start'])}"


def _tranche(pre: Mapping, tranche: str) -> dict:
    for t in pre["tranches"]:
        if t["id"] == tranche:
            return t
    raise WindowRefused(f"tranche {tranche!r} not preregistered")


def tranche_index(pre: Mapping, tranche: str, history: Sequence[Mapping]) -> int:
    """1-based order in which tranches of this window are opened (alpha spending)."""
    prefix = f"{pre['window_id']}-"
    opened = {k.split(":")[0] for r in history for k in (r.get("window_keys") or [])
              if k.startswith(prefix)}
    opened.discard(f"{pre['window_id']}-{tranche}")
    return len(opened) + 1


# ---------------------------------------------------------------------------
# step 1: overlap and data readiness
# ---------------------------------------------------------------------------

def overlap_issues(pre: Mapping, history: Sequence[Mapping]) -> list[str]:
    """Every reason the window is NOT disjoint from what was already used."""
    from runner import READ_START
    per = pre["period"]
    lo, hi = _ms(per["read_start"]), _end_ms(per["read_end"])
    out = []
    if pre["independence"]["selection_read_start"] != READ_START:
        out.append(f"runner.READ_START is {READ_START}, the preregistration assumed "
                   f"{pre['independence']['selection_read_start']}")
    if hi >= _ms(READ_START):
        out.append(f"read_end {per['read_end']} reaches selection data (READ_START {READ_START})")
    if not (lo <= _ms(per["entry_start"]) <= hi):
        out.append("entry_start outside the read period")
    for sym, (a, b) in sorted(LOCKED_TEST_PERIODS.items()):
        if _ms(a) <= hi and lo <= _end_ms(b):
            out.append(f"overlaps the {sym} locked TEST window [{a},{b}]")
    for r in history:
        for k in r.get("window_keys") or []:
            parts = k.split(":")
            if len(parts) == 3 and parts[1] == "VAL" and parts[2].isdigit() and int(parts[2]) <= hi:
                out.append(f"validation window {k} ({r.get('run')}) starts inside the period")
    return out


def _months(pre: Mapping) -> list[str]:
    a = dt.date.fromisoformat(pre["period"]["read_start"])
    b = dt.date.fromisoformat(pre["period"]["read_end"])
    out, cur = [], a.replace(day=1)
    while cur <= b:
        out.append(f"{cur:%Y-%m}")
        cur = (cur.replace(day=28) + dt.timedelta(days=6)).replace(day=1)
    return out


def load_symbol(data_dir: Path, sym: str, pre: Mapping) -> dict:
    """Bars and funding of the read period, hard-cut at read_end; quality gated."""
    from runner import checked_funding_quality, load_funding_window_checked, merged_bars
    per = pre["period"]
    h = merged_bars(Path(data_dir), sym, per["read_start"], per["read_end"])
    if not h.bars:
        return {"bars": [], "funding": None, "missing": list(h.missing or ["no bars"])}
    f, rep = load_funding_window_checked(Path(data_dir), sym, per["read_start"], per["read_end"])
    fq = checked_funding_quality(f, rep)
    if not fq["ok"]:
        return {"bars": [], "funding": None, "missing": [f"funding quality {fq['issues']}"]}
    hi = _end_ms(per["read_end"])
    return {"bars": [b for b in h.bars if b.ts <= hi], "funding": [x for x in f if x[0] <= hi],
            "missing": [], "kline_sha256": h.sha256,
            "funding_sha256": hashlib.sha256(repr(sorted(f)).encode()).hexdigest()}


def data_check(data_dir: Path, pre: Mapping) -> dict:
    """Per symbol: complete months with both files, quality verdict, eligibility."""
    syms = sorted({s for t in pre["tranches"] for s in t["fixed"]}) + list(pre["market_cap_order"]["order_ex_btc_eth"])
    out = {}
    for sym in syms:
        months = [m for m in _months(pre)
                  if (Path(data_dir) / "klines" / f"{sym}-15m-{m}.csv").exists()
                  and (Path(data_dir) / "funding" / f"{sym}-fundingRate-{m}.csv").exists()]
        e = load_symbol(data_dir, sym, pre) if months else {"bars": [], "missing": ["no 2020 files"]}
        ok = not e["missing"]
        out[sym] = {"months": months, "n_months": len(months), "quality_ok": ok,
                    "missing": e["missing"], "n_bars": len(e["bars"]),
                    "first_ts": e["bars"][0].ts if e["bars"] else None,
                    "eligible": ok and len(months) >= int(pre["eligibility"]["min_months"])}
    return out


def resolve_tranches(pre: Mapping, check: Mapping) -> dict:
    """Tranche -> symbols, by the preregistered rule (market-cap order, skip ineligible)."""
    order = [s for s in pre["market_cap_order"]["order_ex_btc_eth"] if check.get(s, {}).get("eligible")]
    out, pos = {}, 0
    for t in pre["tranches"]:
        fixed = [s for s in t["fixed"] if check.get(s, {}).get("eligible")]
        alts = order[pos:pos + int(t["alts_from_order"])]
        pos += int(t["alts_from_order"])
        out[t["id"]] = {"symbols": fixed + alts,
                        "fixed_ineligible": [s for s in t["fixed"] if s not in fixed],
                        "short": len(alts) < int(t["alts_from_order"])}
    return out


# ---------------------------------------------------------------------------
# step 2: freeze the members
# ---------------------------------------------------------------------------

def members_record(pipe, pre: Mapping, prereg_sha: str, *, state_dir: str = "") -> dict:
    from strategy_spec import canonicalize_spec
    n = int(pre["members"]["max_batch_size"])
    ids = pipe.validation_candidates()[:n]
    members, excluded = [], {}
    for sid in ids:
        spec = canonicalize_spec(pipe.specs.get(sid))
        need = sorted(set(spec.get("data") or []) - set(AVAILABLE_DATA))
        if need:
            excluded[sid] = f"needs {need} (not in the 2020 archive)"
            continue
        members.append({"strategy_id": sid, "spec": spec})
    from prereg_gate import research_code_identity
    body = {"schema": MEMBERS_SCHEMA, "window_id": pre["window_id"], "prereg_sha256": prereg_sha,
            "context_id": pipe.context_id(), "state_dir": state_dir, "rule": pre["members"]["rule"],
            "members": members, "excluded_before_test": excluded,
            # the evaluation code the members are frozen against (ADR 0029)
            "code": {"code_hash": research_code_identity()["code_hash"]}}
    body["members_sha256"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return body


def check_members(rec: Mapping, pre: Mapping, prereg_sha: str) -> None:
    if rec.get("schema") != MEMBERS_SCHEMA:
        raise WindowRefused("not a members file")
    body = {k: v for k, v in rec.items() if k not in ("members_sha256", "frozen_at")}
    if hashlib.sha256(canonical_json(body).encode()).hexdigest() != rec.get("members_sha256"):
        raise WindowRefused("members file content does not match its hash")
    if rec["prereg_sha256"] != prereg_sha:
        raise WindowRefused("members were frozen for a different preregistration")
    from strategy_spec import canonicalize_spec
    for m in rec["members"]:
        if canonicalize_spec(m["spec"]) != m["spec"]:
            raise WindowRefused(f"{m['strategy_id']}: spec is not canonical")
        if strategy_id(m["spec"]) != m["strategy_id"]:
            raise WindowRefused(f"{m['strategy_id']}: id does not match the spec")
    if not rec["members"]:
        raise WindowRefused("no members")
    if len(rec["members"]) > int(pre["members"]["max_batch_size"]):
        raise WindowRefused("more members than max_batch_size")


# ---------------------------------------------------------------------------
# step 3: open one tranche, once
# ---------------------------------------------------------------------------

def _hac(nets: Sequence[float]) -> tuple[Optional[float], Optional[float]]:
    from pipeline import _hac as hac
    if len(nets) < 3:
        return None, None
    t, p = hac(nets)
    return (t if math.isfinite(t) else None), (p if math.isfinite(p) else None)


def member_events(spec: Mapping, sym: str, e: Mapping, pre: Mapping, cost) -> list[dict]:
    from evaluator import run_event_study
    from strategy_spec import build_spec_aux, build_spec_signal, stop_exit_rule
    bars = e["bars"]
    start = max(_ms(pre["period"]["entry_start"]),
                bars[0].ts + int(pre["period"]["warmup_days_after_first_bar"]) * DAY_MS)
    lo = next((i for i, b in enumerate(bars) if b.ts >= start), len(bars))
    if lo >= len(bars) - 1:
        return []
    aux = build_spec_aux(bars, spec, e["funding"], None)
    es = run_event_study(bars, sym, build_spec_signal(spec, aux), spec["exit"]["horizon"], cost,
                         float(pre["cost"]["order_value"]), e["funding"], warmup=max(1, lo),
                         exit_rule=stop_exit_rule(spec))
    return [x.to_json() for x in es.events]


def run_id_for(pre: Mapping, prereg_sha: str, tranche: str, members: Mapping) -> str:
    return "IWR-" + hashlib.sha256(canonical_json(
        {"key": window_key(pre, tranche), "members": members["members_sha256"],
         "prereg": prereg_sha}).encode()).hexdigest()[:16]


def open_tranche(pre: Mapping, prereg_sha: str, tranche: str, data_dir: Path, members: Mapping,
                 history_path: Path, out_dir: Path, cost, *, check: Optional[Mapping] = None,
                 gate: Optional[Mapping] = None, registry_path: Optional[Path] = None,
                 resume: bool = False, command: Sequence[str] = ()) -> dict:
    """``gate`` is the report of :func:`prereg_gate.require_active` for this
    prereg (the CLI computes it); without an ACTIVE gate nothing is opened.
    ``resume`` completes a run that was STARTED and stopped before its result
    was written (the window row exists, nothing was reported); it never
    re-opens a COMPLETED, ABORTED or INVALIDATED run."""
    from prereg_gate import research_code_identity
    from validation_registry import RegistryRefused, ValidationRegistry, members_pin
    _tranche(pre, tranche)
    if not gate or gate.get("lifecycle") != "ACTIVE" or gate.get("prereg_sha256") != prereg_sha:
        raise WindowRefused("preregistration is not ACTIVE for this prereg sha256 (prereg_gate, "
                            "ADR 0029): a draft or unapproved prereg cannot start a validation")
    history = list(iter_jsonl(history_path)) if Path(history_path).exists() else None
    if history is None:
        raise WindowRefused(f"validation window history {history_path} missing")
    key = window_key(pre, tranche)
    check_members(members, pre, prereg_sha)
    reg = ValidationRegistry(registry_path or DEFAULT_REGISTRY)
    run_id = run_id_for(pre, prereg_sha, tranche, members)
    code_hash = research_code_identity()["code_hash"]
    state = reg.state(run_id)
    spent = any(key in (r.get("window_keys") or []) for r in history)
    out_dir = Path(out_dir)
    result_path = out_dir / f"{pre['window_id']}-{tranche}_result.json"
    if state in ("COMPLETED", "ABORTED", "INVALIDATED"):
        raise WindowRefused(f"{run_id} is {state} — a tranche is single-use, its result is never replaced")
    if state == "RUNNING":
        if not (resume and spent):
            raise WindowRefused(f"{run_id} was started and not completed (window spent, no result): "
                                "use --resume to finish it with the same members and code")
        if result_path.exists():
            raise WindowRefused(f"{result_path} exists but the run is not completed — inspect by hand")
        drift = reg.member_drift(run_id, members["members"])
        if drift:
            raise WindowRefused(f"{run_id}: members differ from the registration: {drift}")
    elif spent:
        raise WindowRefused(f"{key} was already opened — a tranche is single-use")
    elif state == "REGISTERED":
        pass                                  # registered, stopped before the history row: continue
    issues = overlap_issues(pre, [r for r in history if not (state == "RUNNING" and key in (r.get("window_keys") or []))])
    if issues:
        raise WindowRefused("; ".join(issues))
    check = check or data_check(data_dir, pre)
    syms = resolve_tranches(pre, check)[tranche]["symbols"]
    if not syms:
        raise WindowRefused(f"tranche {tranche}: no eligible symbol")
    k = tranche_index(pre, tranche, history)
    alpha = spending_alpha(k, float(pre["test"]["alpha_total_family"]))
    data = {s: load_symbol(data_dir, s, pre) for s in syms}
    bad = {s: e["missing"] for s, e in data.items() if e["missing"]}
    if bad:
        raise WindowRefused(f"data not usable: {bad}")
    ids = [m["strategy_id"] for m in members["members"]]
    try:
        if state is None:
            reg.register(run_id, window_key=key, prereg_sha256=prereg_sha,
                         members_sha256=members["members_sha256"], members=members_pin(members["members"]),
                         code_hash=code_hash, context_id=members["context_id"], symbols=syms,
                         config={"test": pre["test"], "period": pre["period"], "tranche": tranche},
                         cost={"version": cost.version, "status": getattr(cost, "status", None)})
    except RegistryRefused as e:
        raise WindowRefused(str(e)) from e
    if state != "RUNNING":
        # the window counts as used from here on, whatever happens next
        append_jsonl(history_path, {
        "run": f"{pre['window_id']}-{tranche}", "batches": 1, "batch_ids": [run_id],
        "context_id": members["context_id"], "date": dt.date.today().isoformat(),
        "window_keys": [key], "symbols": syms, "alpha": alpha, "tranche_index": k,
        "members": len(ids), "prereg_sha256": prereg_sha,
        "note": "independent window (ADR 0016): written BEFORE evaluation; result in the run's out dir",
        "source": "run/discovery/independent_window.py open"})
        reg.start(run_id, members_sha256=members["members_sha256"], code_hash=code_hash)
    g0, g1 = _ms(pre["period"]["entry_start"]), _end_ms(pre["period"]["read_end"])
    per_member, series, excluded = {}, {}, {}
    for m in members["members"]:
        sid, spec = m["strategy_id"], m["spec"]
        pooled = None
        info = {"per_symbol": {}, "n_events": 0}
        for s in syms:
            ev = member_events(spec, s, data[s], pre, cost)
            nets = [x["net_bp"] for x in ev]
            t, p = _hac(nets)
            info["per_symbol"][s] = {"n": len(nets), "mean_bp": (sum(nets) / len(nets)) if nets else None,
                                     "hac_t": t, "hac_p": p}
            info["n_events"] += len(nets)
            ds = daily_series(ev, g0, g1)
            pooled = ds if pooled is None else [a + b for a, b in zip(pooled, ds)]
        pooled = [x / len(syms) for x in pooled]
        info["pooled_mean_bp_per_day"] = sum(pooled) / len(pooled)
        info["nonzero_days"] = sum(1 for x in pooled if x != 0)
        info["pooled_hac_t"], info["pooled_hac_p"] = _hac(pooled)
        if info["nonzero_days"] == 0:
            excluded[sid] = "no events"
        else:
            series[sid] = pooled
        per_member[sid] = info
    tc = pre["test"]
    res: dict = {"alpha": alpha, "m": len(ids), "excluded": excluded, "rejected": []}
    if series:
        n = len(next(iter(series.values())))
        q = default_q(n)
        sm = stepm(series, alpha=alpha, B=int(tc["bootstrap_B"]), q=q, seed=int(tc["bootstrap_seed"]),
                   studentize=tc.get("studentize", "fixed"))
        res.update({"rejected": sorted(sm["rejected"]), "steps": sm["steps"], "q": q, "n_days": n,
                    "stepm_method": sm["method"]})
    hp = {s: v["pooled_hac_p"] for s, v in per_member.items() if v["pooled_hac_p"] is not None}
    hk = sorted(hp)
    res["diag_by"] = sorted(s for s, ok in zip(hk, by_fdr([hp[s] for s in hk], len(ids), alpha)) if ok)
    outcome = {}
    for sid in ids:
        v = per_member[sid]
        powered = v["n_events"] >= 60 and v["nonzero_days"] >= 30
        rej = sid in res["rejected"]
        outcome[sid] = ("PASSED_INDEPENDENT_WINDOW" if rej and powered
                        else "REJECTED" if powered else "INCONCLUSIVE")
    result = {"schema": RESULT_SCHEMA, "run_id": run_id, "window_key": key, "tranche": tranche,
              "tranche_index": k, "symbols": syms, "prereg_sha256": prereg_sha,
              "members_sha256": members["members_sha256"], "context_id": members["context_id"],
              "cost": {"version": cost.version, "status": getattr(cost, "status", None)},
              "test": res, "per_member": per_member, "outcome": outcome,
              "data": {s: {"kline_sha256": data[s]["kline_sha256"],
                           "funding_sha256": data[s]["funding_sha256"],
                           "n_bars": len(data[s]["bars"])} for s in syms},
              "claim": pre["outcome_meaning"], "at": iso_now()}
    result["record_sha256"] = hashlib.sha256(canonical_json(
        {k_: v for k_, v in result.items() if k_ != "at"}).encode()).hexdigest()
    from repro_bundle import independent_bundle
    # outside record_sha256: the commit changes with every doc commit, the result does not
    result["repro"] = independent_bundle(result, pre=pre, members=members, cost=cost, command=command)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(result_path, "x", encoding="utf-8") as f:        # never over an existing result
        f.write(json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    reg.complete(run_id, members_sha256=members["members_sha256"], code_hash=code_hash,
                 result_sha256=result["record_sha256"], outcomes=result["outcome"],
                 result_path=str(result_path))
    return result


# ---------------------------------------------------------------------------
# power precheck (diagnostic only: no data read, no criterion changed)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _norm_ppf(p: float) -> float:
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def power_precheck(pre: Mapping, *, m: Optional[int] = None, k: int = 1,
                   annual_sr: Sequence[float] = (1.0, 2.0, 3.0), target: float = 0.8) -> dict:
    """Probability that ONE member with true annualised Sharpe ``sr`` (net of
    cost, daily returns, 365 days a year) is rejected by the tranche test,
    from the preregistered numbers alone.  StepM lies between two bounds:
    the single-test critical value z(1-alpha) (best case: every other member
    already rejected or perfectly correlated) and the Bonferroni value
    z(1-alpha/m) (worst case: independent members).  Normal approximation of
    the studentised mean; serial correlation and sparse trading days lower
    the real power further.  Changes no criterion."""
    n = (_end_ms(pre["period"]["read_end"]) + 1 - _ms(pre["period"]["entry_start"])) // DAY_MS
    m = int(m or pre["members"]["max_batch_size"])
    alpha = spending_alpha(k, float(pre["test"]["alpha_total_family"]))
    z_best, z_worst = _norm_ppf(1 - alpha), _norm_ppf(1 - alpha / m)
    zt = _norm_ppf(target)
    root = math.sqrt(n / 365.0)
    rows = {f"{sr:g}": {"power_best": _norm_cdf(sr * root - z_best),
                        "power_worst": _norm_cdf(sr * root - z_worst)} for sr in annual_sr}
    return {"n_days": n, "m": m, "tranche_index": k, "alpha": alpha,
            "z_best": z_best, "z_worst": z_worst, "power_by_annual_sr": rows,
            f"annual_sr_for_power_{target:g}": {"best": (z_best + zt) / root, "worst": (z_worst + zt) / root},
            "method": "normal approximation, bounds z(1-alpha) and z(1-alpha/m); diagnostic only"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_check(a) -> int:
    pre, sha = load_prereg(a.prereg)
    hist = list(iter_jsonl(Path(a.history)))
    issues = overlap_issues(pre, hist)
    chk = data_check(Path(a.data_dir), pre) if a.data_dir else {}
    tr = resolve_tranches(pre, chk) if chk else {}
    opened = {t["id"]: any(window_key(pre, t["id"]) in (r.get("window_keys") or []) for r in hist)
              for t in pre["tranches"]}
    rep = {"prereg_sha256": sha, "overlap_issues": issues, "opened": opened,
           "symbols": {s: {k: v for k, v in c.items() if k != "months"} for s, c in chk.items()},
           "tranches": tr, "status": "READY" if not issues and chk else ("BLOCKED" if issues else "NO_DATA_DIR")}
    print(json.dumps(rep, indent=1, sort_keys=True))
    return 0 if rep["status"] == "READY" else 4


def _cmd_freeze(a) -> int:
    from pipeline import EvolutionPipeline, RunnerDataProvider
    from runner import cost_from_env
    pre, sha = load_prereg(a.prereg)
    provider = RunnerDataProvider(Path(a.dev_data_dir))
    usable = [s for s in ("BTCUSDT", "ETHUSDT") if not provider.symbol(s)["missing"]]
    pipe = EvolutionPipeline(Path(a.state_dir), provider, cost_from_env(), symbols=usable)
    rec = members_record(pipe, pre, sha, state_dir=str(a.state_dir))
    if a.expect_context and rec["context_id"] != a.expect_context:
        print(f"REFUSED: context {rec['context_id']} != expected {a.expect_context}", file=sys.stderr)
        return 4
    rec["frozen_at"] = iso_now()
    Path(a.out).write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"members": len(rec["members"]), "excluded": rec["excluded_before_test"],
                      "context_id": rec["context_id"], "members_sha256": rec["members_sha256"]}))
    return 0


def _cmd_power(a) -> int:
    pre, sha = load_prereg(a.prereg)
    print(json.dumps({"prereg_sha256": sha, **power_precheck(pre, m=a.m, k=a.k)}, indent=1, sort_keys=True))
    return 0


def _cmd_open(a) -> int:
    from costs_bridge import CostContext
    if not a.confirm_single_use:
        print("REFUSED: --confirm-single-use is required (a tranche can be opened once)", file=sys.stderr)
        return 1
    from prereg_gate import GateRefused, require_active
    pre, sha = load_prereg(a.prereg)
    members = json.loads(Path(a.members).read_text())
    cost = CostContext.from_assumed_profile(pre["cost"]["profile"])
    try:
        gate = require_active(Path(a.prereg), activation_path=Path(a.activation) if a.activation else None,
                              members_path=Path(a.members))
    except GateRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 4
    try:
        r = open_tranche(pre, sha, a.tranche, Path(a.data_dir), members, Path(a.history), Path(a.out_dir), cost,
                         gate=gate, registry_path=Path(a.registry), resume=a.resume, command=sys.argv)
    except WindowRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 4
    print(json.dumps({"window_key": r["window_key"], "alpha": r["test"]["alpha"],
                      "symbols": r["symbols"], "rejected": r["test"]["rejected"],
                      "outcome": r["outcome"]}, indent=1))
    return 0


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prereg", default=str(DEFAULT_PREREG))
    ap.add_argument("--history", default=os.environ.get("NEW2_VALIDATION_HISTORY") or str(DEFAULT_HISTORY))
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--data-dir")
    f = sub.add_parser("freeze-members")
    f.add_argument("--state-dir", required=True)
    f.add_argument("--dev-data-dir", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--expect-context")
    pw = sub.add_parser("power")
    pw.add_argument("--m", type=int)
    pw.add_argument("--k", type=int, default=1)
    o = sub.add_parser("open")
    o.add_argument("--tranche", required=True)
    o.add_argument("--data-dir", required=True)
    o.add_argument("--members", required=True)
    o.add_argument("--out-dir", required=True)
    o.add_argument("--confirm-single-use", action="store_true")
    o.add_argument("--activation", help="prereg_activation_v1 record (prereg_gate, ADR 0029)")
    o.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    o.add_argument("--resume", action="store_true",
                   help="finish a STARTED run whose result was never written")
    a = ap.parse_args(argv)
    return {"check": _cmd_check, "freeze-members": _cmd_freeze, "power": _cmd_power,
            "open": _cmd_open}[a.cmd](a)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
