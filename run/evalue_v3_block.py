#!/usr/bin/env python3
"""Walk-forward e-value v3 block tool: freeze, DEV vol scale, block P&L, booking (ADR 0042).

    freeze     members = first 5 of validation_candidates() of a DEV-only state
               directory (or a candidate list for a rehearsal), bound to the prereg
    dev-scale  bundle daily net bp on the DEV stretch S1 (basket, prereg cost) ->
               vol-target scale k, sealed into the members file
    block      one block: the members' rules on every basket symbol, entries only
               inside the block, day-end marking, in-block stop -L, floor -B.
               --rehearsal: a DEV month only, nothing booked.  Otherwise the
               prereg must be ACTIVE (prereg_gate) and the block is booked once
               in the ledger after it ended.
    status     the ledger, chain-checked

Pure rules are in ``discovery/evalue_v3.py``.  This tool never reads IW1,
BTC/ETH validation or locked TEST data: a rehearsal is checked against the
DEV refused reads of ``strong_search_dev``; a real block reads only forward
data (block month + warm-up) after the first block start.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))
import evalue_v3 as V  # noqa: E402
import forward_v2_entry_bar_dev as F  # noqa: E402
import strong_search_dev as T  # noqa: E402
from costs_bridge import CostContext  # noqa: E402
from engine_lib import iso_now  # noqa: E402
from evaluator import run_event_study  # noqa: E402
from strategy_spec import build_spec_aux, build_spec_signal, canonicalize_spec, stop_exit_rule  # noqa: E402

BLOCK_WARMUP_DAYS = 60     # feature warm-up read strictly before the block start


def cost_context(pre: dict, repo: Path) -> tuple[CostContext, str]:
    c = pre["cost"]
    if c["profile"] == "assumed_public_v1":
        return CostContext.from_assumed_profile("assumed_public_v1"), "assumed_public_v1"
    raw = (repo / c["record_file"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != c["record_sha256"]:
        raise V.V3Refused("cost record differs from the one sealed in the prereg")
    return CostContext.from_public_estimate(json.loads(raw)), c["profile"]


def events_of(spec: dict, bars, funding, sym: str, entry_lo: int, cost_ctx) -> list[dict]:
    aux = build_spec_aux(bars, spec, funding, None)
    res = run_event_study(bars, sym, build_spec_signal(spec, aux), spec["exit"]["horizon"], cost_ctx,
                          T.ORDER_VALUE, funding, warmup=max(1, entry_lo - 1), exit_rule=stop_exit_rule(spec))
    return [e.to_json() for e in res.events]


# ---------------------------------------------------------------------------
# DEV vol scale
# ---------------------------------------------------------------------------

def dev_bundle_series(members: list[dict], symbols: list[str], data_dir: Path, cost_ctx) -> np.ndarray:
    """Equal-weight bundle of the members' basket-average daily net bp on S1
    (exit-day booking, the v2 statistic), same reads as the v2 entry bar."""
    T.SEGMENTS = F.segments_for(symbols)
    seg = T.SEGMENTS["S1"]
    for s in seg["symbols"]:
        T.assert_allowed(s, seg["read_start"], seg["read_end"])
    data = T.load_segment("S1", data_dir)
    d0, d1 = T.day_grid(seg)
    out = np.zeros(d1 - d0 + 1)
    for m in members:
        spec = canonicalize_spec(m["spec"])
        pooled = np.zeros_like(out)
        for sym, entry in data.items():
            evs = []
            for sd in entry["pieces"]:
                evs += [(e["entry_index"], e["exit_index"], e["side"], e["entry_ts"], e["exit_ts"],
                         e["gross_bp"], e["cost_bp"], e["funding_bp"], e["net_bp"])
                        for e in events_of(spec, sd.bars, sd.funding, sym, sd.entry_lo, cost_ctx)]
            pooled += T.daily(evs, d0, d1)
        out += pooled / len(data)
    return out / len(members)


# ---------------------------------------------------------------------------
# one block
# ---------------------------------------------------------------------------

def load_block_symbol(data_dir: Path, sym: str, read_start: str, block_start: str, block_end: str):
    import runner
    h = runner.merged_bars(data_dir, sym, read_start, block_end)
    if not h.bars or h.missing:
        raise V.V3Refused(f"{sym} [{read_start},{block_end}]: incomplete bars ({h.missing}); re-download")
    fund, rep = runner.load_funding_window_checked(data_dir, sym, read_start, block_end)
    lo, hi = T.day_ms(read_start), T.day_ms(block_end) + T.DAY_MS - 1
    fund = [(t, r) for t, r in fund if lo <= t <= hi]
    ts = np.array([b.ts for b in h.bars], dtype=np.int64)
    return {"bars": h.bars, "funding": fund, "ts": ts, "dataset_sha256": h.sha256,
            "entry_lo": int(np.searchsorted(ts, T.day_ms(block_start))),
            "funding_quality": runner.checked_funding_quality(fund, rep)}


def evaluate_block(pre: dict, members: dict, data_dir: Path, block_id: str, cost_ctx) -> dict:
    p = V.params(pre)
    b0, b1 = V.block_bounds(block_id)
    read_start = (dt.date.fromisoformat(b0) - dt.timedelta(days=BLOCK_WARMUP_DAYS)).isoformat()
    symbols = list(pre["universe"]["symbols"])
    k = float(members["scale"]["k"]) if members["members"] else 0.0
    d_first = T.day_ms(b0) // T.DAY_MS
    day_ends = [(d_first + i + 1) * T.DAY_MS for i in range(T.day_ms(b1) // T.DAY_MS - d_first + 1)]
    slots, diag = {}, {"symbols": {}, "events": {}}
    data = {s: load_block_symbol(data_dir, s, read_start, b0, b1) for s in symbols}
    for s, dd in data.items():
        diag["symbols"][s] = {"dataset_sha256": dd["dataset_sha256"], "bars": len(dd["bars"])}
    for m in members["members"]:
        spec = canonicalize_spec(m["spec"])
        slots[m["strategy_id"]] = {}
        for s, dd in data.items():
            evs = [e for e in events_of(spec, dd["bars"], dd["funding"], s, dd["entry_lo"], cost_ctx)
                   if e["entry_ts"] >= T.day_ms(b0)]
            idx = [int(np.searchsorted(dd["ts"], end)) - 1 for end in day_ends]
            slots[m["strategy_id"]][s] = V.slot_value_path(
                evs, day_ends, idx, [b.open for b in dd["bars"]], [b.close for b in dd["bars"]])
            diag["events"][f"{m['strategy_id']}:{s}"] = len(evs)
    path = V.bundle_path(slots, k)
    res = V.apply_stop(path, L=p["L"], B=p["B"])
    if res["stop_day"] is not None:
        diag["note"] = "stopped: entries after the stop day-end are not taken (value frozen there)"
    diag["path"] = path
    diag["k"] = k
    return {**res, "block_id": block_id, "block": [b0, b1], "read_start": read_start, "diagnostics": diag}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--prereg", type=Path, required=True)
    g = f.add_mutually_exclusive_group(required=True)
    g.add_argument("--state-dir", type=Path)
    g.add_argument("--candidates", type=Path, help="rehearsal: [{strategy_id, spec}] in validation order")
    f.add_argument("--dev-data-dir", type=Path)
    f.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("dev-scale")
    s.add_argument("--prereg", type=Path, required=True)
    s.add_argument("--members", type=Path, required=True)
    s.add_argument("--data-dir", type=Path, required=True)
    b = sub.add_parser("block")
    b.add_argument("--prereg", type=Path, required=True)
    b.add_argument("--members", type=Path, required=True)
    b.add_argument("--data-dir", type=Path, required=True)
    b.add_argument("--block", required=True, help="YYYY-MM")
    b.add_argument("--rehearsal", action="store_true")
    b.add_argument("--activation", type=Path)
    b.add_argument("--ledger", type=Path)
    b.add_argument("--out", type=Path)
    st = sub.add_parser("status")
    st.add_argument("--ledger", type=Path, required=True)
    a = ap.parse_args(argv)
    repo = HERE.parent

    if a.cmd == "status":
        print(json.dumps(V.Ledger(a.ledger).rows(), indent=1))
        return 0
    pre, sha = V.load_prereg(a.prereg)
    if a.cmd == "freeze":
        if a.state_dir:
            from pipeline import EvolutionPipeline, RunnerDataProvider
            from runner import cost_from_env
            provider = RunnerDataProvider(a.dev_data_dir)
            usable = [x for x in ("BTCUSDT", "ETHUSDT") if not provider.symbol(x)["missing"]]
            pipe = EvolutionPipeline(a.state_dir, provider, cost_from_env(), symbols=usable)
            ids = pipe.validation_candidates()
            specs, ctx, sd = {i: pipe.specs.get(i) for i in ids}, pipe.context_id(), str(a.state_dir)
        else:
            cands = json.loads(a.candidates.read_text())
            ids = [c["strategy_id"] for c in cands]
            specs, ctx, sd = {c["strategy_id"]: c["spec"] for c in cands}, f"REHEARSAL:{a.candidates.name}", ""
        rec = V.members_record(pre, sha, candidate_ids=ids, specs=specs, context_id=ctx, state_dir=sd)
        rec["frozen_at"] = iso_now()
        a.out.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"members": [m["strategy_id"] for m in rec["members"]], "sha": rec["members_sha256"]}))
        return 0
    members = json.loads(a.members.read_text())
    cost_ctx, cost_name = cost_context(pre, repo)
    if a.cmd == "dev-scale":
        p = V.params(pre)
        x = dev_bundle_series(members["members"], list(pre["universe"]["symbols"]), a.data_dir, cost_ctx)
        sc = V.vol_scale(x, target=p["vol_target"], k_max=p["k_max"])
        sc.update({"cost": cost_name, "stretch": "S1 " + T.SEGMENTS["S1"]["entry_start"] + ".." +
                   T.SEGMENTS["S1"]["read_end"], "dev_sr_ann": T.sr_ann(x)})
        rec = V.with_scale(members, sc)
        rec["frozen_at"] = members.get("frozen_at")
        a.members.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(sc))
        return 0
    # block
    issues = V.check_members(members, sha)
    if issues:
        print("REFUSED: " + "; ".join(issues), file=sys.stderr)
        return 4
    b0, b1 = V.block_bounds(a.block)
    if a.rehearsal:
        read_start = (dt.date.fromisoformat(b0) - dt.timedelta(days=BLOCK_WARMUP_DAYS)).isoformat()
        for sym in pre["universe"]["symbols"]:
            T.assert_allowed(sym, read_start, b1)        # DEV months only
    else:
        import prereg_gate
        prereg_gate.require_active(a.prereg, activation_path=a.activation, members_path=a.members)
        if a.block < pre["blocks"]["first_block_id"]:
            raise SystemExit(f"refused: block {a.block} is before the first block")
    res = evaluate_block(pre, members, a.data_dir, a.block, cost_ctx)
    res["cost"] = cost_name
    if a.out:
        a.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
    print(json.dumps({k: res[k] for k in ("block_id", "x_raw", "x", "stop_day", "floored")}))
    if not a.rehearsal:
        row = V.book_block(V.Ledger(a.ledger), pre, sha, members["members_sha256"], a.block, res)
        print(json.dumps({k: row[k] for k in ("block_id", "x", "z", "lam", "e", "rejected")}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
