# Evidence: ADR 0031 (one candidate per entry signal, conservative stop fill)

R7 = the R6 DEV-only command rerun on the ADR 0031 code (commit dc14813, same file contents as dcd24b2):

    env -u COINTRADER_SRC NEW2_DATA_DIR=<unzipped upload f6379f02> NEW2_DISCOVERY_ROOT=<fresh> NEW2_DISC_MODE=evolve NEW2_COST_PROFILE=public_estimate:data/public_market_estimate/estimate_v1.json python3 run/discovery/runner.py --mode evolve --dev-only --rounds 8 --evals 4000
    # 2026-10-22T13:12:17Z..14:09:48Z (3450 s), exit 0, CYCLE_OK (= the pipeline ran; not a strategy verdict)
    # context C2-dc89a2574236479535ef (R6: C2-bf2caecb65585a8d89c7)

- `r7_cycle_summary.json`: runner summary. 894 evaluated, RESEARCH_ONLY (DEV pass) 63, INCONCLUSIVE 93, REJECTED 738.
- `r7_validation_candidates.json`: `EvolutionPipeline.validation_candidates()` on the R7 state. 33 candidates, 30 skipped as "same entry signal, different exit", each with its representative id.
- No validation, locked or IW1 window was opened. DEV only.

Note: this decision was first numbered ADR 0030 and then renumbered to 0031 (0030 is used by PR #29). The renumber edited only the ADR references inside the two config notes and the code comments. That changes the code hash, so a rerun now gets a different context id. The evaluation logic is unchanged. The skip reasons in `r7_validation_candidates.json` still say "ADR 0030", because that is the text the run produced.

## R8 (2026-10-23, ADR 0033): reproduction on PR #30 merged with main

Same command as R7 on commit e5b016a (this branch merged with main 6291e27, plus candidate_selection_v2).
2026-10-22T23:22:17Z..23:58:50Z (2193 s), exit 0, CYCLE_OK, context C2-a488c80b29e95ba2c425 (differs from R7
because main changed validation-stage code in pipeline.py/stats_mc.py and v2 was added; the DEV logic is the same).

- `r8_summary.json`: 894 evaluated, RESEARCH_ONLY (DEV pass) 63, INCONCLUSIVE 93, REJECTED 738 — identical to R7.
  With the v1 selection the 33 validation candidates are exactly R7's list, in the same order.
  With v2 (default) also 33: no near-entry twin with a different exit exists in this run (30 skips, all exact-entry).
- `r8_validation_candidates_v2.json`: candidates and every skip with both exits.
- `r8_dev_repro_bundle.json`: repro bundle (registered-candidate list sha256 ea04d733… = R6's).
- Independent validation completed: 0. No validation, locked or IW1 window was opened. DEV only.
