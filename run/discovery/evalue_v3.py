"""Walk-forward e-value v3: members freeze, block P&L and e-process ledger (ADR 0025, 0030, 0042).

The v3 preregistration (``walkforward_evalue_prereg_v3``) tests ONE
hypothesis: the equal-weight bundle the frozen engine picks makes money after
cost, block by block.  This module holds the parts that decide a result and
need no market data, so they can be tested on synthetic numbers:

* ``members_record``   first ``bundle.members`` strategies of
  ``pipeline.validation_candidates()`` (no entry bar: v3 has none), bound to
  the prereg sha256 and the evaluation code, same integrity fields as a v2
  members file so ``prereg_gate.check_members_file`` checks it;
* ``vol_scale``        k such that the bundle's DEV daily volatility is the
  prereg's vol target (1 % of block capital);
* ``slot_value_path``  per-day value of one (member, symbol) slot: closed
  events at their net bp, open events marked to the day-end close;
* ``apply_stop``       in-block stop at -L (first day-end at or below), flat
  afterwards, margin floor -B;
* ``e_path``           WS-R hedged betting product with the eq. (15)/(26) bet
  (the rule of ``evalue_walkforward_sim.log_e_hedged_prpl_bet``);
* ``Ledger``           append-only hash-chained JSONL of block records.

Booking a real block needs an ACTIVE prereg (``prereg_gate``); nothing here
activates anything.  Market-data reading is in ``run/evalue_v3_block.py``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping, Optional, Sequence

from engine_lib import canonical_json

PREREG_SCHEMAS = ("walkforward_evalue_prereg_v3", "walkforward_evalue_prereg_v3_DRAFT")
MEMBERS_SCHEMA = "walkforward_evalue_members_v3"
LEDGER_SCHEMA = "walkforward_evalue_ledger_v3"
DAY_MS = 86_400_000
HERE = Path(__file__).resolve().parent


class V3Refused(RuntimeError):
    """An input does not belong to this preregistration, or a booking rule is broken."""


def v3_code_sha256() -> str:
    """Hash of this file and the block tool: pinned at freeze, checked when booking."""
    h = hashlib.sha256()
    for p in (HERE / "evalue_v3.py", HERE.parent / "evalue_v3_block.py"):
        data = p.read_bytes() if p.exists() else b"<MISSING>"
        h.update(p.name.encode() + b"\0" + hashlib.sha256(data).hexdigest().encode() + b"\n")
    return h.hexdigest()


def load_prereg(path: Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    pre = json.loads(raw)
    if pre.get("schema") not in PREREG_SCHEMAS:
        raise V3Refused(f"{path}: not a walk-forward e-value v3 preregistration")
    return pre, hashlib.sha256(raw).hexdigest()


def params(pre: Mapping) -> dict:
    """The numbers that decide a block, read from the prereg (never hard-coded)."""
    sb, ep = pre["sizing_and_bounds"], pre["e_process"]
    return {"L": float(sb["stop_L"]), "B": float(sb["floor_B"]), "c": float(sb["gap_allowance_c"]),
            "block_sd": float(sb["block_sd"]), "vol_target": float(sb["vol_target_daily"]),
            "k_max": float(sb["scale_max"]), "alpha": float(pre["alpha"]), "lam_max": float(ep["lam_max"]),
            "members": int(pre["pipeline"]["bundle"]["members"])}


# ---------------------------------------------------------------------------
# members
# ---------------------------------------------------------------------------

def members_record(pre: Mapping, prereg_sha: str, *, candidate_ids: Sequence[str], specs: Mapping,
                   context_id: str, state_dir: str = "", code_hash: Optional[str] = None,
                   v3_code: Optional[str] = None) -> dict:
    """First n candidates in validation_candidates order (v3 has no entry bar)."""
    from strategy_spec import canonicalize_spec
    n = params(pre)["members"]
    picked = list(candidate_ids)[:n]
    if code_hash is None:
        from prereg_gate import research_code_identity
        code_hash = research_code_identity()["code_hash"]
    body = {"schema": MEMBERS_SCHEMA, "family": pre["family"], "prereg_sha256": prereg_sha,
            "context_id": context_id, "state_dir": state_dir, "rule": pre["pipeline"]["members_rule"],
            "candidates_seen": len(candidate_ids),
            "members": [{"strategy_id": s, "spec": canonicalize_spec(specs[s])} for s in picked],
            "flat_if_empty": "no candidate -> every block is flat (X_b = 0)",
            "code": {"code_hash": code_hash, "v3_code_sha256": v3_code or v3_code_sha256()}}
    body["members_sha256"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return body


def with_scale(members: Mapping, scale: Mapping) -> dict:
    """Add the DEV vol-scale record to a members file and re-seal it."""
    body = {k: v for k, v in members.items() if k not in ("members_sha256", "frozen_at")}
    body["scale"] = dict(scale)
    body["members_sha256"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return body


def check_members(members: Mapping, prereg_sha: str) -> list[str]:
    out = []
    body = {k: v for k, v in members.items() if k not in ("members_sha256", "frozen_at")}
    if hashlib.sha256(canonical_json(body).encode()).hexdigest() != members.get("members_sha256"):
        out.append("members file content does not match its hash")
    if members.get("schema") != MEMBERS_SCHEMA:
        out.append("not a v3 members file")
    if members.get("prereg_sha256") != prereg_sha:
        out.append("members were frozen for a different preregistration")
    if (members.get("code") or {}).get("v3_code_sha256") != v3_code_sha256():
        out.append("v3 block/e-value code changed after the freeze (FROZEN_CODE_CHANGED)")
    if members.get("members") and not (members.get("scale") or {}).get("k"):
        out.append("members file has no DEV vol scale (run evalue_v3_block.py dev-scale)")
    return out


# ---------------------------------------------------------------------------
# sizing and block P&L
# ---------------------------------------------------------------------------

def vol_scale(dev_bundle_daily_bp: Sequence[float], *, target: float, k_max: float) -> dict:
    """k with sd(k * bundle daily bp / 1e4) = target on DEV, capped at k_max."""
    x = [float(v) for v in dev_bundle_daily_bp]
    n = len(x)
    if n < 30:
        raise V3Refused(f"DEV bundle series too short for a vol scale ({n} days)")
    m = sum(x) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in x) / (n - 1))
    if not sd > 0:
        raise V3Refused("DEV bundle series has zero volatility")
    k_raw = target * 1e4 / sd
    return {"dev_days": n, "dev_daily_sd_bp": sd, "target": target, "k_raw": k_raw,
            "k": min(k_raw, k_max), "capped": k_raw > k_max}


def slot_value_path(events: Sequence[Mapping], day_ends: Sequence[int], close_idx: Sequence[int],
                    opens: Sequence[float], closes: Sequence[float]) -> list[float]:
    """Value (bp of the slot notional) at each block day-end.

    events: dicts with entry_index, exit_index, side, entry_ts, exit_ts,
    cost_bp, funding_bp, net_bp (evaluator.EventRecord fields; the evaluator
    fills at bar opens).  day_ends[d] is the day-end time (ms, exclusive) and
    close_idx[d] the index of the last bar before it.  A closed event
    (exit_ts < day end) counts net_bp; an open one (entry_ts < day end <=
    exit_ts) is marked at that bar's close, minus the whole round-trip cost
    and the funding pro rata (closing it there would pay the exit cost)."""
    out = []
    for d, end in enumerate(day_ends):
        v = 0.0
        for e in events:
            if e["exit_ts"] < end:
                v += e["net_bp"]
            elif e["entry_ts"] < end:
                px0, px = opens[e["entry_index"]], closes[close_idx[d]]
                frac = (end - e["entry_ts"]) / max(1, e["exit_ts"] - e["entry_ts"])
                v += e["side"] * (px / px0 - 1.0) * 1e4 - e["cost_bp"] - e["funding_bp"] * min(1.0, frac)
        out.append(v)
    return out


def bundle_path(slot_paths: Mapping[str, Mapping[str, Sequence[float]]], k: float) -> list[float]:
    """Block-capital fraction per day-end: k * mean over members of mean over symbols / 1e4."""
    members = list(slot_paths)
    if not members:
        return []
    days = len(next(iter(next(iter(slot_paths.values())).values())))
    out = []
    for d in range(days):
        per_m = [sum(p[d] for p in slot_paths[m].values()) / len(slot_paths[m]) for m in members]
        out.append(k * (sum(per_m) / len(per_m)) / 1e4)
    return out


def apply_stop(path: Sequence[float], *, L: float, B: float) -> dict:
    """First day-end at or below -L closes everything (value of that day-end);
    otherwise the last day-end.  The margin floor caps the loss at -B."""
    if not path:
        return {"x_raw": 0.0, "x": 0.0, "stop_day": None, "floored": False}
    stop = next((d for d, v in enumerate(path) if v <= -L), None)
    raw = float(path[stop if stop is not None else len(path) - 1])
    return {"x_raw": raw, "x": max(raw, -B), "stop_day": stop, "floored": raw < -B}


# ---------------------------------------------------------------------------
# e-process (same rule as evalue_walkforward_sim.log_e_hedged_prpl_bet)
# ---------------------------------------------------------------------------

def z_of(x: float, *, c: float, B: float) -> float:
    return (x - c) / (B + c)


def e_path(Z: Sequence[float], *, s0: float, alpha: float, lam_max: float) -> list[dict]:
    """lam_b = min(sqrt(2 ln(2/alpha) / (s2_{b-1} b ln(1+b))), lam_max) with the
    WS-R regularised predictable variance s2 (s2_0 = s0^2)."""
    out, logE = [], 0.0
    s1 = ss = 0.0
    mu, s2 = 0.0, s0 * s0
    for b0, z in enumerate(Z):
        t = b0 + 1
        lam = min(math.sqrt(2 * math.log(2 / alpha) / (s2 * t * math.log1p(t))), lam_max)
        logE += math.log1p(lam * z)
        out.append({"block": t, "z": z, "lam": lam, "log_e": logE, "e": math.exp(logE)})
        s1 += z
        mu = s1 / (t + 1)
        ss += (z - mu) ** 2
        s2 = (s0 * s0 + ss) / (t + 1)
    return out


# ---------------------------------------------------------------------------
# blocks and the ledger
# ---------------------------------------------------------------------------

def block_bounds(block_id: str) -> tuple[str, str]:
    """'YYYY-MM' -> first and last calendar day of that month."""
    d0 = dt.date.fromisoformat(block_id + "-01")
    d1 = (d0.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    return d0.isoformat(), d1.isoformat()


def next_block(block_id: str) -> str:
    d0 = dt.date.fromisoformat(block_id + "-01")
    return (d0.replace(day=28) + dt.timedelta(days=4)).strftime("%Y-%m")


class Ledger:
    """Append-only JSONL; each line carries the sha256 of the line before it."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows = [json.loads(s) for s in self.path.read_text(encoding="utf-8").splitlines() if s.strip()]
        prev = "0" * 64
        for r in rows:
            body = {k: v for k, v in r.items() if k != "row_sha256"}
            if r.get("prev_sha256") != prev or hashlib.sha256(canonical_json(body).encode()).hexdigest() != r.get("row_sha256"):
                raise V3Refused(f"ledger chain broken at block {r.get('block_id')}: rows were edited")
            prev = r["row_sha256"]
        return rows

    def append(self, rec: Mapping) -> dict:
        rows = self.rows()
        body = {"schema": LEDGER_SCHEMA, **dict(rec),
                "prev_sha256": rows[-1]["row_sha256"] if rows else "0" * 64}
        body["row_sha256"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(body, sort_keys=True) + "\n")
        return body


def book_block(ledger: Ledger, pre: Mapping, prereg_sha: str, members_sha: str, block_id: str,
               result: Mapping, *, today: Optional[dt.date] = None) -> dict:
    """Book one block once, in order, after it ended; update E; flag rejection.

    ``result`` comes from ``apply_stop`` (plus diagnostics).  After a booked
    rejection or a VOID the family is over and nothing more is booked."""
    p = params(pre)
    rows = ledger.rows()
    first = pre["blocks"]["first_block_id"]
    want = next_block(rows[-1]["block_id"]) if rows else first
    if block_id != want:
        raise V3Refused(f"next block to book is {want}, not {block_id} (one booking per block, in order)")
    if rows and (rows[-1]["rejected"] or rows[-1]["void"]):
        raise V3Refused("the family already ended (rejection or VOID); nothing more is booked")
    _, last_day = block_bounds(block_id)
    today = today or dt.datetime.now(dt.timezone.utc).date()
    if today <= dt.date.fromisoformat(last_day):
        raise V3Refused(f"block {block_id} has not ended yet (ends {last_day})")
    x = float(result["x"])
    zs = [r["z"] for r in rows] + [z_of(x, c=p["c"], B=p["B"])]
    ep = e_path(zs, s0=p["block_sd"] / (p["B"] + p["c"]), alpha=p["alpha"], lam_max=p["lam_max"])[-1]
    rec = {"block_id": block_id, "prereg_sha256": prereg_sha, "members_sha256": members_sha,
           "x_raw": float(result["x_raw"]), "x": x, "stop_day": result.get("stop_day"),
           "floored": bool(result.get("floored")), "z": ep["z"], "lam": ep["lam"],
           "log_e": ep["log_e"], "e": ep["e"], "reject_at": 1.0 / p["alpha"],
           "rejected": ep["e"] >= 1.0 / p["alpha"], "void": bool(result.get("void", False)),
           "diagnostics": dict(result.get("diagnostics") or {})}
    return ledger.append(rec)
