# Evidence: ADR 0036 (IW1 refreeze draft, prereg v2 final-form draft, freeze rehearsal)

Everything here is DRAFT. No prereg is activated, no validation / locked / IW1 / paper window was opened.
Market data read: DEV stretches only (strong_search_dev refused-read check).

R9 = the R7/R8 DEV-only command on main 491d073:

    env -u COINTRADER_SRC NEW2_DATA_DIR=<unzipped upload f6379f02> NEW2_DISCOVERY_ROOT=<fresh> NEW2_DISC_MODE=evolve NEW2_COST_PROFILE=public_estimate:data/public_market_estimate/estimate_v1.json python3 run/discovery/runner.py --mode evolve --dev-only --rounds 8 --evals 4000
    # 2026-10-24T06:36:26Z..07:38:39Z, exit 0, CYCLE_OK, context C2-ccfa2ef38101803483e1, 894 / 63 DEV pass / 33 candidates (= R8 list and order)

| file | what |
|---|---|
| `10-31_판단요약.md` | plain-Korean summary for the user's 10-31 decision |
| `r9_dev_repro_bundle.json`, `r9_validation_candidates.json` | R9 repro bundle and the 33 candidates in order |
| `IW1_members_refreeze_DRAFT.json` | `independent_window.py --prereg ../evidence_paper_faithful_20261022/independent_window_v2_DRAFT.json freeze-members` on R9 (20 members, code hash pinned) |
| `IW1_refreeze_members_dev_scores.json` | `run/iw1_statistic_dev.py` on those 20 (IW1 statistic, tranche-A 6 symbols, assumed_public_v1, DEV S1/S2) |
| `IW1_refreeze_power_precheck.json` | power from those DEV SRs (normal approx., best bound, alpha 0.0304) |
| `forward_window_v2_FINALFORM_DRAFT.json` | prereg v2 in the final schema, still DRAFT; Q1/Q2 marked ALT_COST_DEPENDENT |
| `cost_public_estimate_v3_alts_DRAFT.json` | alternative alt-cost criteria (7 liquid alts the PC collection covers), not in force |
| `rehearsal_v2_entry_bar_basket15_assumed.json` | `run/forward_v2_entry_bar_dev.py` on the 33 candidates, 15-symbol basket (two shards merged) |
| `rehearsal_v2_entry_bar_liquid9_assumed.json` | same on the liquid 9 (preview of Q1-B, still assumed cost) |
| `rehearsal_v2_members_DRAFT.json` | `run/discovery/forward_window_freeze.py` on the basket-15 scores: 0 members, registrable false (D5) |
| `forward_window_v2_FINALFORM_DRAFT_r2.json` | ADR 0037: Q1-B applied (liquid 9, cost criteria v3_alts config), still DRAFT; only Q2 open |
