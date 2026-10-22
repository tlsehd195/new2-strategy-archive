# Evidence: ADR 0030 (one candidate per entry signal, conservative stop fill)

R7 = the R6 DEV-only command rerun on the ADR 0030 code (commit dc14813, same file contents as dcd24b2):

    env -u COINTRADER_SRC NEW2_DATA_DIR=<unzipped upload f6379f02> NEW2_DISCOVERY_ROOT=<fresh> NEW2_DISC_MODE=evolve NEW2_COST_PROFILE=public_estimate:data/public_market_estimate/estimate_v1.json python3 run/discovery/runner.py --mode evolve --dev-only --rounds 8 --evals 4000
    # 2026-10-22T13:12:17Z..14:09:48Z (3450 s), exit 0, CYCLE_OK (= the pipeline ran; not a strategy verdict)
    # context C2-dc89a2574236479535ef (R6: C2-bf2caecb65585a8d89c7)

- `r7_cycle_summary.json`: runner summary. 894 evaluated, RESEARCH_ONLY (DEV pass) 63, INCONCLUSIVE 93, REJECTED 738.
- `r7_validation_candidates.json`: `EvolutionPipeline.validation_candidates()` on the R7 state. 33 candidates, 30 skipped as "same entry signal, different exit", each with its representative id.
- No validation, locked or IW1 window was opened. DEV only.
