"""Group-level control of locked-TEST windows (auto-evolution §4-3).

The per-(strategy, symbol) :class:`engine_lib.LockTestRegistry` makes each
strategy's locked test single-use, but it does not stop MANY strategies from
being tested on the same locked window one after another — each new attempt
is another look at the same data, and the family-wise error over all looks
grows without bound.

A *campaign* is the unit of locked-window access: its final member list,
alpha and method are written here BEFORE any locked data is loaded.  Per
window (symbol + locked dates), campaign ``c`` gets

    alpha_c = alpha_total * (6 / pi^2) / c^2        (sum over c <= alpha_total)

and inside the campaign each member is tested at ``alpha_c / m_c``
(Bonferroni; valid under any dependence between members).  After
``max_campaigns_per_window`` accessed campaigns the window is RETIRED:
further candidates are BLOCKED_STATISTICS until new locked data exists.

Both the legacy generation path (survivors of one BH decision form one
campaign) and the evolution path use this ledger, so their looks at a
window are counted together.  Campaigns that were preregistered but aborted
before any locked data was exposed (data missing) do not count.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Mapping, Optional, Sequence

from engine_lib import append_jsonl, canonical_json, iso_now, iter_jsonl, locked_window_for
from stats_mc import spending_alpha

CAMPAIGN_SCHEMA = "locked_campaign_v1"


def window_key(symbol: str) -> Optional[str]:
    w = locked_window_for(symbol)
    return f"{symbol}:LOCK:{w[0]}:{w[1]}" if w else None


class WindowRetired(RuntimeError):
    pass


class CampaignLedger:
    """Append-only ``locked_window_campaigns.jsonl`` (one file per state dir,
    shared by every path that touches a locked window)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def records(self) -> list[dict]:
        return list(iter_jsonl(self.path))

    def _by_id(self) -> dict:
        out: dict = {}
        for r in self.records():
            out.setdefault(r["campaign_id"], []).append(r)
        return out

    def counted_campaigns(self, wkey: str) -> int:
        """Accessed (or possibly accessed) campaigns on a window: every
        preregistered campaign except those explicitly aborted before
        exposure."""
        n = 0
        for cid, recs in self._by_id().items():
            pre = [r for r in recs if r["kind"] == "PREREGISTERED"]
            if not pre or wkey not in pre[0]["windows"]:
                continue
            aborted = any(r["kind"] == "ABORTED_NO_EXPOSURE" and wkey in r.get("windows", [wkey])
                          for r in recs)
            if not aborted:
                n += 1
        return n

    def find(self, source: str, source_ref: str) -> Optional[dict]:
        for cid, recs in self._by_id().items():
            pre = [r for r in recs if r["kind"] == "PREREGISTERED"]
            if pre and pre[0]["source"] == source and pre[0]["source_ref"] == source_ref:
                return {"preregistered": pre[0],
                        "result": next((r for r in recs if r["kind"] == "RESULT"), None),
                        "aborted": next((r for r in recs if r["kind"] == "ABORTED_NO_EXPOSURE"), None)}
        return None

    def open_campaigns(self, source: str) -> list[dict]:
        """Preregistered campaigns of ``source`` without a RESULT / ABORT."""
        out = []
        for cid, recs in self._by_id().items():
            pre = [r for r in recs if r["kind"] == "PREREGISTERED"]
            if pre and pre[0]["source"] == source and not any(
                    r["kind"] in ("RESULT", "ABORTED_NO_EXPOSURE") for r in recs):
                out.append(pre[0])
        return out

    def window_status(self, symbol: str, *, max_campaigns: int) -> dict:
        wkey = window_key(symbol)
        if wkey is None:
            return {"symbol": symbol, "window": None, "retired": True, "reason": "NO_LOCKED_TEST",
                    "used": 0}
        used = self.counted_campaigns(wkey)
        return {"symbol": symbol, "window": wkey, "used": used,
                "retired": used >= max_campaigns,
                "reason": "WINDOW_RETIRED" if used >= max_campaigns else "AVAILABLE"}

    def preregister(self, *, source: str, source_ref: str, context_id: str,
                    members: Mapping[str, Sequence[str]], alpha_total: float,
                    max_campaigns: int, method: str, config_hash: str) -> dict:
        """Write the campaign BEFORE any locked data is touched.  ``members``
        maps strategy id -> symbols.  Raises WindowRetired if a window is
        exhausted (the caller must exclude those members first)."""
        have = self.find(source, source_ref)
        if have is not None:
            return have["preregistered"]
        per_sym: dict[str, list[str]] = {}
        for sid, syms in sorted(members.items()):
            for s in syms:
                per_sym.setdefault(s, []).append(sid)
        windows: dict = {}
        for sym, sids in sorted(per_sym.items()):
            st = self.window_status(sym, max_campaigns=max_campaigns)
            if st["retired"]:
                raise WindowRetired(f"{sym}: {st['reason']} ({st['used']} campaigns)")
            c = st["used"] + 1
            a_c = spending_alpha(c, alpha_total)
            windows[st["window"]] = {"symbol": sym, "campaign_index": c, "alpha_campaign": a_c,
                                     "m": len(sids), "alpha_member": a_c / len(sids),
                                     "members": sorted(sids)}
        body = {"schema": CAMPAIGN_SCHEMA, "kind": "PREREGISTERED", "source": source,
                "source_ref": source_ref, "context_id": context_id,
                "members": {k: sorted(v) for k, v in sorted(members.items())},
                "windows": windows, "alpha_total_per_window": alpha_total,
                "max_campaigns_per_window": max_campaigns, "method": method,
                "config_hash": config_hash}
        body["campaign_id"] = "LC-" + hashlib.sha256(canonical_json(body).encode()).hexdigest()[:16]
        body["created_at"] = iso_now()
        append_jsonl(self.path, body)
        return body

    def abort_no_exposure(self, campaign: Mapping, reason: str,
                          windows: Optional[Sequence[str]] = None) -> None:
        append_jsonl(self.path, {"schema": CAMPAIGN_SCHEMA, "kind": "ABORTED_NO_EXPOSURE",
                                 "campaign_id": campaign["campaign_id"],
                                 "windows": list(windows or campaign["windows"]),
                                 "reason": reason, "at": iso_now()})

    def record_result(self, campaign: Mapping, results: Mapping) -> None:
        append_jsonl(self.path, {"schema": CAMPAIGN_SCHEMA, "kind": "RESULT",
                                 "campaign_id": campaign["campaign_id"],
                                 "results": dict(results), "at": iso_now()})


def member_alpha(campaign: Mapping, symbol: str) -> float:
    for w in campaign["windows"].values():
        if w["symbol"] == symbol:
            return float(w["alpha_member"])
    return math.nan
