#!/usr/bin/env python3
"""Write the ADR 0116 data-integrity addendum of the forward record CL1/FIP1/AF1.

The addendum is committed before the first forward evaluation (the first
monthly run is 2026-12-03; no forward result exists when it is written).  It
records the code versions that compute the record from now on.  It changes no
candidate, parameter, universe, cost or statistic of
prereg_forward_nearmiss_v1.json; it only withholds verdicts on data that are
missing, substituted beyond a limit or failed a check.  Re-run after any
change of the listed files (the test checks the hashes).

    python3 run/audit/adr0116/write_forward_addendum.py
"""

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "run"))
import research_data_integrity as rdi  # noqa: E402

EVID = REPO / "run" / "audit" / "evidence_forward_nearmiss_20261120"
PREREG = EVID / "prereg_forward_nearmiss_v1.json"
OUT = EVID / "prereg_forward_nearmiss_v1_addendum_data_integrity.json"
FILES = ("run/forward_nearmiss.py", "run/newsrc_engine.py", "run/research_data_integrity.py",
         "run/broad_universe_dev.py", "run/cross_section_v2_dev.py", "run/wide_universe_dev.py",
         "run/newsrc_r3_dev.py", "run/newsrc_r11_dev.py", "run/newsrc_r12_dev.py",
         "run/broad_universe_fetch.py", "run/discovery/funding_schedule.py")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def body() -> dict:
    return {
        "schema": "forward_nearmiss_prereg_addendum_v1",
        "adr": "0116",
        "prereg": "prereg_forward_nearmiss_v1.json",
        "prereg_sha256": sha(PREREG),
        "written_before_any_forward_result": True,
        "first_forward_evaluation": "2026-12-03 (monthly workflow, ADR 0115); no evaluation has run when this file is committed",
        "requested_by": "동동, 2026-11-27 00:13 UTC, directive §3 (P1 forward data integrity)",
        "changes_candidates_parameters_universe_cost_or_statistics": False,
        "what_changes": [
            "A held name's missing open-to-open return is no longer a 0 % day: the day, the rest of that holding period and the next rebalance day are not observable (NaN) and excluded; v1 used 0.0.",
            "A held name's funding for the holding day (D, D+1] is checked against the archive's declared settlement schedule (funding_schedule.settlement_check, the core engine rule); missing / gapped / schedule-less / invalid funding makes the day not observable; v1 charged NaN as 0.",
            "Settlement-time jitter (00:00:00.002): a settlement up to 60 s after 00:00 pays for the previous day (v1 put it on the next day: one settlement misattributed at every jittered boundary).",
            "Impact volume is labelled OBSERVED_BAR2345 / ESTIMATED_DAILY_96 / ESTIMATED_MEAN30_96; with no volume at all the trade's cost is unknown and the day not observable (v1 used volume 1.0).",
            "Conflicting duplicate daily candles, off-grid timestamps and non-positive prices are recorded and dropped (v1 kept the first duplicate silently).",
            "Each candidate gets status PASS / FAIL / INVALID_DATA / INSUFFICIENT_DATA / UNDETERMINED / ERROR (final_status). FORWARD_PASS becomes PASS only with zero unobserved days, estimated-volume share <= limit, no panel issue on a held name and finite statistics; otherwise the verdict is withheld (WITHHELD_*). A candidate with INVALID data enters Holm (m = 3 unchanged) with p = 1.",
        ],
        "unchanged": "candidates, signal code, universe (U50 without BTC/ETH), rebalance grid, cost model and rows, NW lag 14, Holm m = 3, alpha 0.0213, interim 182 / final 364 days, FORWARD_PASS rule; on complete data the daily series are identical to v1 (test_forward_nearmiss GOLDEN_V1)",
        "data_policy": rdi.POLICY,
        "code_sha256": {f: sha(REPO / f) for f in FILES},
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(body(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", OUT.relative_to(REPO))
