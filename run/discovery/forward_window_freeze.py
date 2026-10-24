"""Members freeze for the forward-window preregistration v2 (ADR 0023, 0036).

The v2 draft fixes the members as the first ``members.max`` strategies of
``pipeline.validation_candidates()`` (a DEV-only evolve state directory, in
its own order) whose DEV entry bar passes: annual SR of the basket-average
daily net series on the DEV selection stretch S1 >= ``members.entry_bar_s1_sr``
(scored by ``run/forward_v2_entry_bar_dev.py`` with the prereg's basket and
cost).  Fewer passing candidates than ``members.max`` means the window is NOT
registered (decision D5): the record says ``registrable: false`` and nothing
is spent.

The members file has the same integrity fields as an IW1 members file, so
``prereg_gate.check_members_file`` checks it (content hash, prereg binding,
canonical specs, ids, evaluation-code pin).  Freezing reads DEV data only and
opens nothing; activation stays with ``prereg_gate`` and a human.

    python3 run/discovery/forward_window_freeze.py --prereg <v2.json> \\
        --state-dir <dev-only state> --dev-data-dir <dir> --entry-bar <scores.json> --out <members.json>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from engine_lib import canonical_json, iso_now  # noqa: E402

MEMBERS_SCHEMA = "forward_window_members_v2"
PREREG_SCHEMAS = ("forward_window_prereg_v2", "forward_window_prereg_v2_DRAFT")


class FreezeRefused(RuntimeError):
    """The entry-bar scores do not belong to this preregistration."""


def load_prereg(path: Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    pre = json.loads(raw)
    if pre.get("schema") not in PREREG_SCHEMAS:
        raise FreezeRefused(f"{path}: not a forward-window v2 preregistration")
    return pre, hashlib.sha256(raw).hexdigest()


def check_scores(pre: Mapping, scores: Mapping) -> None:
    """The scores must use exactly the prereg's basket and cost profile."""
    if list(scores.get("symbols") or []) != list(pre["basket"]["symbols"]):
        raise FreezeRefused("entry-bar scores were computed on another symbol list than basket.symbols")
    want = pre["cost"]["profile"]
    if scores.get("cost") != want:
        raise FreezeRefused(f"entry-bar scores use cost {scores.get('cost')!r}, the prereg says {want!r}")


def select_members(candidate_ids: Sequence[str], scores: Mapping, *, n: int, bar: float) -> dict:
    """First ``n`` candidates (in validation_candidates order) at or above the bar.

    A candidate without a score is a finding, never silently skipped."""
    rows = {r["strategy_id"]: r for r in scores["candidates"]}
    picked, considered, unscored = [], [], []
    for sid in candidate_ids:
        if len(picked) == n:
            break
        r = rows.get(sid)
        if r is None:
            unscored.append(sid)
            continue
        s1 = (r.get("S1") or {}).get("sr_ann")
        ok = s1 is not None and s1 == s1 and s1 >= bar
        considered.append({"strategy_id": sid, "s1_sr_ann": s1, "s2_sr_ann": (r.get("S2") or {}).get("sr_ann"),
                           "entry_bar_pass": ok})
        if ok:
            picked.append(sid)
    return {"picked": picked, "considered": considered, "unscored": unscored,
            "registrable": len(picked) == n and not unscored}


def members_record(pre: Mapping, prereg_sha: str, *, candidate_ids: Sequence[str], specs: Mapping,
                   context_id: str, scores: Mapping, scores_sha: str, state_dir: str = "",
                   code_hash: Optional[str] = None) -> dict:
    from strategy_spec import canonicalize_spec
    check_scores(pre, scores)
    n, bar = int(pre["members"]["max"]), float(pre["members"]["entry_bar_s1_sr"])
    sel = select_members(candidate_ids, scores, n=n, bar=bar)
    if code_hash is None:
        from prereg_gate import research_code_identity
        code_hash = research_code_identity()["code_hash"]
    body = {"schema": MEMBERS_SCHEMA, "family": pre["family"], "prereg_sha256": prereg_sha,
            "context_id": context_id, "state_dir": state_dir, "rule": pre["members"]["rule"],
            "entry_bar": {"s1_sr_ann_min": bar, "scores_sha256": scores_sha, "cost": scores["cost"],
                          "symbols": scores["symbols"]},
            "members": [{"strategy_id": s, "spec": canonicalize_spec(specs[s])} for s in sel["picked"]],
            "considered": sel["considered"], "unscored": sel["unscored"],
            "registrable": sel["registrable"],
            "registrable_meaning": ("true: members.max candidates passed the entry bar; the window may be "
                                    "activated by a human. false: decision D5, the window is NOT registered "
                                    "and nothing is spent"),
            "code": {"code_hash": code_hash}}
    body["members_sha256"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return body


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="forward-window v2 members freeze (ADR 0036)")
    ap.add_argument("--prereg", required=True)
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--dev-data-dir", required=True)
    ap.add_argument("--entry-bar", required=True, help="output of run/forward_v2_entry_bar_dev.py")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    from pipeline import EvolutionPipeline, RunnerDataProvider
    from runner import cost_from_env
    pre, sha = load_prereg(Path(a.prereg))
    raw = Path(a.entry_bar).read_bytes()
    provider = RunnerDataProvider(Path(a.dev_data_dir))
    usable = [s for s in ("BTCUSDT", "ETHUSDT") if not provider.symbol(s)["missing"]]
    pipe = EvolutionPipeline(Path(a.state_dir), provider, cost_from_env(), symbols=usable)
    ids = pipe.validation_candidates()
    try:
        rec = members_record(pre, sha, candidate_ids=ids, specs={s: pipe.specs.get(s) for s in ids},
                             context_id=pipe.context_id(), scores=json.loads(raw),
                             scores_sha=hashlib.sha256(raw).hexdigest(), state_dir=str(a.state_dir))
    except FreezeRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 4
    rec["frozen_at"] = iso_now()
    Path(a.out).write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"members": len(rec["members"]), "registrable": rec["registrable"],
                      "context_id": rec["context_id"], "members_sha256": rec["members_sha256"]}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
