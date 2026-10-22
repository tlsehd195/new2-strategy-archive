# Legacy scripts under `run/` (retired execution paths)

Recorded 2026-10-22 (ADR 0027). Every script below refers to the retired v1 machine
(`/home/z/my-project/NEW2_run`) and/or imports the main trading repository's package
(`cointrader`, via `COINTRADER_SRC`). They are **not** part of the research engine
(`run/discovery/`), are not imported by it, are not run by CI and cannot run in this
repository on their own. They are kept unchanged as the provenance of the v1 results
(PREREG_*.md, INCIDENT_2026-10-14.md) — rewriting them would change that history.

Status: **DEPRECATED — do not run.** The current entry points are
`run/discovery/runner.py`, `run/discovery/run_cycle.sh`, `run/discovery/independent_window.py`
and the `run/*_dev.py` DEV-only tools (which use only `run/discovery`).

`run/discovery/tests/test_pipeline_flow_20261022.py` fails if a script under `run/` refers
to one of these paths without being listed here.

| script | retired reference |
|---|---|
| `a1_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `a1_family_precheck.py` | /home/z/my-project |
| `a1_illiq_diag.py` | /home/z/my-project |
| `alt_download.py` | /home/z/my-project |
| `alt_download_r2.py` | /home/z/my-project |
| `alt_funding_diag.py` | /home/z/my-project |
| `alt_funding_engine_crosscheck.py` | /home/z/my-project |
| `alt_funding_r2_diag.py` | /home/z/my-project |
| `alt_funding_r2_engine_crosscheck.py` | /home/z/my-project |
| `alt_funding_r2_precheck.py` | /home/z/my-project |
| `backup_snapshot.sh` | /home/z/my-project |
| `c1_basis_momentum_diag.py` | /home/z/my-project |
| `c1_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `c3_coinbase_premium_diag.py` | /home/z/my-project |
| `c3_download_binance.py` | /home/z/my-project |
| `c3_download_coinbase.py` | /home/z/my-project |
| `c3_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `c4_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `c4_premium_spike_diag.py` | /home/z/my-project |
| `c5_download_archive.py` | /home/z/my-project |
| `c5_download_data.py` | /home/z/my-project |
| `c5_download_spot_parallel.py` | /home/z/my-project |
| `c5_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `c5_quarterly_curve_diag.py` | /home/z/my-project |
| `c7_cross_venue_spread_diag.py` | /home/z/my-project |
| `c7_download_venues.py` | /home/z/my-project |
| `c7_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `c7_probe_data.py` | /home/z/my-project |
| `c9_download_dvol.py` | /home/z/my-project |
| `c9_dvol_diag.py` | /home/z/my-project |
| `c9_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `c9_inspect.py` | /home/z/my-project |
| `download_missing_data.py` | /home/z/my-project |
| `f1_cme_gap_diag.py` | /home/z/my-project |
| `f1_engine_crosscheck.py` | /home/z/my-project |
| `f2x6_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `f2x6_fng_diag.py` | /home/z/my-project |
| `f2x6_fng_extreme_diag.py` | /home/z/my-project |
| `fng_refresh_check.py` | /home/z/my-project |
| `fr_crosscheck_engine_cost.py` | cointrader, /home/z/my-project |
| `fr_extreme_diag.py` | /home/z/my-project |
| `fr_supplementary.py` | /home/z/my-project |
| `git_backup_push.sh` | /home/z/my-project |
| `kp_diag.py` | /home/z/my-project |
| `kp_download.py` | /home/z/my-project |
| `kp_download_binance.py` | /home/z/my-project |
| `kp_engine_crosscheck.py` | /home/z/my-project |
| `l1_engine_crosscheck.py` | /home/z/my-project |
| `l1_expiry_diag.py` | /home/z/my-project |
| `l3_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `l3_rv_tail_diag.py` | /home/z/my-project |
| `n2_engine_crosscheck.py` | /home/z/my-project |
| `n2_maker_exec_diag.py` | /home/z/my-project |
| `x10_calendar_diag.py` | /home/z/my-project |
| `x10_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `x12_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `x12_family_precheck.py` | /home/z/my-project |
| `x12_skew_diag.py` | /home/z/my-project |
| `x1_engine_crosscheck.py` | /home/z/my-project |
| `x1_external_momentum_diag.py` | /home/z/my-project |
| `x1r_engine_crosscheck.py` | /home/z/my-project |
| `x1r_reverify_diag.py` | /home/z/my-project |
| `x2_engine_crosscheck.py` | /home/z/my-project |
| `x2_macro_events_diag.py` | /home/z/my-project |
| `x2_prep_events.py` | /home/z/my-project |
| `x3_basis_collapse_diag.py` | /home/z/my-project |
| `x3_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `x4f3_engine_crosscheck.py` | /home/z/my-project |
| `x4f3_pair_spread_diag.py` | /home/z/my-project |
| `x5_engine_crosscheck.py` | /home/z/my-project |
| `x5_stable_supply_diag.py` | /home/z/my-project |
| `x7_download_announcements.py` | /home/z/my-project |
| `x7_engine_crosscheck.py` | /home/z/my-project |
| `x7_listing_events_diag.py` | /home/z/my-project |
| `x8_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `x8_taker_flow_diag.py` | /home/z/my-project |
| `x9_engine_crosscheck.py` | cointrader, /home/z/my-project |
| `x9_ticket_size_diag.py` | /home/z/my-project |
