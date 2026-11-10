"""Classification of every ``run/*.py`` (directive 2026-11-10 §2, §3, final report D).

``python3 run/audit/research_inventory_20261110.py`` rewrites
``RESEARCH_SCRIPTS_20261110.json`` and ``.md`` next to this file.  Every row
says what the script is, what the evidence of the survey is (read status), its
class and the reason.  The read status is honest: ``INVENTORIED`` = header,
imports, paths and risk patterns scanned (``scan`` below), not the whole body;
``LOGIC_TRACED`` = the risky lines were read and followed in the 2026-11-10
audit; ``TESTED`` = a ``run/test_*.py`` for it passes in the local CI run.
Stdlib only; reads source files, no data.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE.parent

CLASSES = {
    "INTEGRATED_CORE": "hypothesis built into the core engine (feature / legacy family)",
    "STANDARD_INTERFACE": "DEV tool that evaluates through the engine's spec / cost / evaluator interface",
    "KEPT_INDEPENDENT": "independent DEV research or simulation tool; strategy type the engine does not model, result recorded in its ADR",
    "DUPLICATE_RESEARCH": "superseded by a later tool for the same hypothesis",
    "RETIRED_OR_ON_HOLD": "retired v1 execution path (LEGACY_SCRIPTS.md) or on hold",
    "NEEDS_MORE_DATA": "hypothesis needs a dataset the repository does not have",
    "TEST": "test file of a run/ tool",
    "NOT_SURVEYED": "not surveyed",
}

#: legacy hypothesis prefix -> (class, reason)
LEGACY_PREFIX = {
    "a1": ("INTEGRATED_CORE", "A1 Amihud illiquidity z (prior-960 window) = feature illiq_z of opt-in set v2 (ADR 0066)"),
    "alt": ("RETIRED_OR_ON_HOLD", "alt funding extremes, v1 rounds; funding signal = funding_z in the engine (BTC/ETH), alt cross-sections re-done by broad/wide_universe_dev"),
    "c1": ("NEEDS_MORE_DATA", "basis momentum: premium-index / basis history not in the repository"),
    "c3": ("NEEDS_MORE_DATA", "Coinbase premium: Coinbase klines not in the repository"),
    "c4": ("NEEDS_MORE_DATA", "premium spike: premium-index history not in the repository"),
    "c5": ("NEEDS_MORE_DATA", "quarterly futures curve: dated-futures klines not in the repository"),
    "c7": ("NEEDS_MORE_DATA", "cross-venue spread: other-venue klines not in the repository"),
    "c9": ("NEEDS_MORE_DATA", "DVOL implied volatility: not in the repository"),
    "f1": ("NEEDS_MORE_DATA", "CME weekend gap: CME futures prices not in the repository"),
    "f2x6": ("INTEGRATED_CORE", "Fear & Greed extremes = legacy family fng and features fng_max / fng_min"),
    "fng": ("INTEGRATED_CORE", "F&G refresh check; F&G is a dataset of the engine (fng)"),
    "fr": ("INTEGRATED_CORE", "funding extremes = legacy family fund_z and feature funding_z"),
    "kp": ("NEEDS_MORE_DATA", "kimchi premium: KRW exchange prices / FX not in the repository"),
    "l1": ("NEEDS_MORE_DATA", "futures expiry: ~20 events (structurally underpowered) and dated futures data"),
    "l3": ("INTEGRATED_CORE", "realised-vol tail z ~ vol_ratio (engine) and range_z (v2); the exact RV-z rule is not a registered feature (partial)"),
    "n2": ("NEEDS_MORE_DATA", "maker execution layer needs queue / order-book history; maker cost estimated by maker_entry_estimate.py"),
    "x1": ("NEEDS_MORE_DATA", "ES/NQ external momentum: CME index futures 1h not in the repository"),
    "x1r": ("NEEDS_MORE_DATA", "X1 re-verification: same external data"),
    "x2": ("NEEDS_MORE_DATA", "macro event calendar not in the repository"),
    "x3": ("NEEDS_MORE_DATA", "basis collapse: basis history not in the repository"),
    "x4f3": ("RETIRED_OR_ON_HOLD", "BTC-ETH pair spread: klines exist, but the engine has no two-leg strategy type (unresolved F-item)"),
    "x5": ("NEEDS_MORE_DATA", "stablecoin supply history not in the repository"),
    "x7": ("DUPLICATE_RESEARCH", "listing events: superseded by newlisting_short_dev.py (ADR 0060, DEV-window guarded)"),
    "x8": ("INTEGRATED_CORE", "taker flow = legacy family flow_z and feature taker_share_z"),
    "x9": ("NEEDS_MORE_DATA", "ticket size needs the trade count per bar, which data_quality.Bar does not keep (NOT_DERIVABLE)"),
    "x10": ("INTEGRATED_CORE", "calendar = legacy family tod and features hour_utc / dow_utc (v2)"),
    "x12": ("INTEGRATED_CORE", "rolling skewness z (X12 rule) = feature skew_z of opt-in set v2 (ADR 0066)"),
    "download": ("RETIRED_OR_ON_HOLD", "v1 data pipeline; replaced by run/discovery/data_fetch.py"),
}

#: current (non-legacy) tools -> (class, reason)
CURRENT = {
    "alpha101_dev.py": ("KEPT_INDEPENDENT", "weekly cross-section of 101 Alphas (ADR 0054): multi-asset panel, engine is per-symbol; NOT_PROMISING"),
    "basis_harvest_dev.py": ("KEPT_INDEPENDENT", "spot-index long / perp short (ADR 0060): two-leg carry; NOT_PROMISING"),
    "broad_universe_dev.py": ("KEPT_INDEPENDENT", "broad-universe cross-section (ADR 0035); also the shared DEV-window guard / loaders of the panel tools"),
    "broad_universe_fetch.py": ("KEPT_INDEPENDENT", "public download for the panel tools (ADR 0035)"),
    "bundle_regime_dev.py": ("KEPT_INDEPENDENT", "bundle by regime vs buy-and-hold (ADR 0026); NOT stable"),
    "candidate_regime_dev.py": ("STANDARD_INTERFACE", "per-regime scorecard via canonicalize_spec + CostContext (ADR 0044)"),
    "compare_selection_dev.py": ("STANDARD_INTERFACE", "compares two evolve ledgers (ADR 0019)"),
    "cross_section_v2_dev.py": ("KEPT_INDEPENDENT", "five cross-sectional hypotheses (ADR 0040); NOT_PROMISING"),
    "evalue_v3_block.py": ("STANDARD_INTERFACE", "e-value v3 block tool on the EvolutionPipeline / evaluator (ADR 0042)"),
    "evalue_walkforward_sim.py": ("KEPT_INDEPENDENT", "synthetic error-rate simulation (ADR 0025)"),
    "fast_short_dev.py": ("KEPT_INDEPENDENT", "fast short-horizon panel strategies (ADR 0045); NOT_PROMISING"),
    "forward_v2_entry_bar_dev.py": ("STANDARD_INTERFACE", "prereg v2 entry bar via canonicalize_spec + CostContext (ADR 0036)"),
    "hilo_intraday_dev.py": ("KEPT_INDEPENDENT", "high/low line + volume spike, 15m/1h panel (ADR 0046); variant of hilo_volume_dev"),
    "hilo_volume_dev.py": ("KEPT_INDEPENDENT", "high/low line + volume spike, daily panel (ADR 0043); NOT_PROMISING"),
    "iw1_statistic_dev.py": ("STANDARD_INTERFACE", "IW1 statistic re-score via evaluator.run_event_study (ADR 0019/0036)"),
    "liquid_search_dev.py": ("STANDARD_INTERFACE", "DEV search on a liquid universe via evaluator + CostContext, forbidden-read guard (ADR 0024)"),
    "lowliq_cost_calibration.py": ("STANDARD_INTERFACE", "volume-scaled alt cost calibration (ADR 0041), feeds CostContext"),
    "maker_entry_estimate.py": ("KEPT_INDEPENDENT", "maker-entry cost estimate from public books (ADR 0049)"),
    "maker_entry_reeval.py": ("STANDARD_INTERFACE", "re-evaluates engine DEV candidates with maker cost (ADR 0049)"),
    "maker_rescore_dev.py": ("KEPT_INDEPENDENT", "maker re-pricing of published panel candidates (ADR 0056)"),
    "newlisting_short_dev.py": ("KEPT_INDEPENDENT", "new-listing short event study (ADR 0060); funding read per DEV segment since ADR 0066"),
    "null_power_report.py": ("KEPT_INDEPENDENT", "summary of null_power_sim runs (ADR 0048)"),
    "null_power_sim.py": ("STANDARD_INTERFACE", "null / planted-edge runs of the DEV pipeline (ADR 0048)"),
    "okx_real_cost.py": ("KEPT_INDEPENDENT", "round-trip cost back-calculation from a fills CSV (ADR 0058); demo records are not cost evidence"),
    "okx_tendency_dev.py": ("KEPT_INDEPENDENT", "OKX paper-record tendencies as DEV rules (ADR 0052); NOT_PROMISING"),
    "p3_probe2.py": ("NEEDS_MORE_DATA", "liquidation data source probe; liquidation history not in the repository"),
    "p3_probe_liquidation.py": ("NEEDS_MORE_DATA", "liquidation data source probe; liquidation history not in the repository"),
    "pdf_check_sim.py": ("KEPT_INDEPENDENT", "synthetic checks of two papers (ADR 0032)"),
    "power_audit_ledger.py": ("KEPT_INDEPENDENT", "reads one DEV state directory: what the gates removed (ADR 0022)"),
    "power_audit_synthetic.py": ("KEPT_INDEPENDENT", "synthetic miss rate of the gates (ADR 0022); superseded for promo-v5 by discovery/power_analysis.py (ADR 0064)"),
    "residual_signals_dev.py": ("KEPT_INDEPENDENT", "market-factor residual panel signals (ADR 0051); NOT_PROMISING"),
    "stepm_block_sim.py": ("KEPT_INDEPENDENT", "StepM FWER vs bootstrap block length (ADR 0027)"),
    "strong_search_dev.py": ("STANDARD_INTERFACE", "strong-candidate DEV search via evaluator + CostContext, forbidden-read guard (ADR 0019)"),
    "structural_search_dev.py": ("STANDARD_INTERFACE", "market-structure DEV search with engine DSR (ADR 0020)"),
    "unsup_regime_dev.py": ("KEPT_INDEPENDENT", "k-means regime classifier on DEV S1 (ADR 0050); NOT_USEFUL"),
    "wide_universe_dev.py": ("KEPT_INDEPENDENT", "wide-universe panel with effective independent names (ADR 0047); variant of broad_universe_dev"),
}

#: lines read and followed in the 2026-11-10 audit (beyond the scan)
TRACED = {
    "broad_universe_dev.py": "SEGMENTS / NEVER / assert_allowed and the four loaders (load_funding, load_bar2345 had no guard)",
    "newlisting_short_dev.py": "load_symbol (segment filter), fund_sum (read 2021-01..2025-10 incl. the never-read gap)",
    "hilo_intraday_dev.py": "swing_lines: future-looking pivot test shifted by k (known at j+k) — causal",
    "basis_harvest_dev.py": "shift(-1): open-to-next-open realised return of a position opened at the signal open",
    "cross_section_v2_dev.py": "shift(-1) realised return, as above",
    "hilo_volume_dev.py": "shift(-1) realised return, as above",
    "wide_universe_dev.py": "shift(-1) realised return, as above",
    "a1_illiq_diag.py": "header rule (z vs prior 960, shift 1) — used for illiq_z",
    "x12_skew_diag.py": "header rule (rolling skew z, shift 1) — used for skew_z",
    "l3_rv_tail_diag.py": "header rule only",
}

#: strategy hypotheses of the independent tools, as standard records (strategy_record.py)
#: file -> (family, universe, timeframe, entry, exit, datasets, verdict, adr)
STRATEGY_TOOLS = {
    "alpha101_dev.py": ("cross_section_formulaic", "top50_by_volume", "1w", "eight 101-Alphas ranks, long top / short bottom", "weekly rebalance", ["daily"], "NOT_PROMISING", "0054"),
    "basis_harvest_dev.py": ("basis_carry", "BTC,ETH spot index vs perp", "1d", "long spot index / short perp when funding positive", "daily rebalance", ["daily", "funding", "spot_index"], "NOT_PROMISING", "0060"),
    "broad_universe_dev.py": ("cross_section_classic", "top50_by_volume", "1d", "momentum / reversal / funding carry / low-vol ranks, 10 per side", "daily rebalance", ["daily", "funding"], "NOT_PROMISING", "0035"),
    "bundle_regime_dev.py": ("bundle", "top5 DEV candidates", "15m", "equal-weight bundle of the top-5 DEV candidates", "candidate exits", ["klines"], "NOT_STABLE", "0026"),
    "cross_section_v2_dev.py": ("cross_section_v2", "top50_by_volume", "1d", "five new cross-sectional ranks (ADR 0040)", "daily rebalance", ["daily", "funding", "bar2345"], "NOT_PROMISING", "0040"),
    "fast_short_dev.py": ("short_horizon", "top50_by_volume", "15m-1h", "20 fast short-horizon rules", "fixed short horizon", ["daily", "klines"], "NOT_PROMISING", "0045"),
    "hilo_intraday_dev.py": ("hilo_volume_spike", "top50_by_volume", "15m/1h", "rolling or swing high/low line + volume spike, fade or breakout", "fixed horizon H", ["klines", "funding"], "NOT_PROMISING", "0046"),
    "hilo_volume_dev.py": ("hilo_volume_spike", "top50_by_volume", "1d", "N-day high/low line + volume spike, fade or breakout", "fixed horizon", ["daily", "funding", "bar2345"], "NOT_PROMISING", "0043"),
    "newlisting_short_dev.py": ("new_listing_short", "new USDT perps", "1d", "short a new listing on day d", "hold 30 days", ["daily", "funding"], "NOT_PROMISING", "0060"),
    "okx_tendency_dev.py": ("paper_record_tendency", "top50_by_volume", "1d", "rules from OKX paper-record tendencies", "fixed horizon", ["daily"], "NOT_PROMISING", "0052"),
    "residual_signals_dev.py": ("market_residual", "top50_by_volume", "1d", "market-factor residual reversal / momentum ranks", "daily rebalance", ["daily"], "NOT_PROMISING", "0051"),
    "unsup_regime_dev.py": ("regime_filter", "BTC,ETH", "1d", "k-means regime label (trained on S1) as a filter", "regime change", ["daily"], "NOT_USEFUL", "0050"),
    "wide_universe_dev.py": ("cross_section_classic", "top200_min_2m_daily", "1d", "five cross-sectional hypotheses on a wider universe", "daily rebalance", ["daily", "funding", "bar2345"], "NOT_PROMISING", "0047"),
}


def strategy_records() -> tuple:
    import sys
    sys.path.insert(0, str(RUN / "discovery"))
    import strategy_record as sr
    recs = [sr.research_record(family=f, source_script=f"run/{name}", symbols=u, timeframe=tf, entry=e,
                               exit=x, data_requirements=d, evaluation_status=v, adr=a)
            for name, (f, u, tf, e, x, d, v, a) in sorted(STRATEGY_TOOLS.items())]
    return sr.dedup(recs)


PATTERNS = {
    "neg_shift": r"shift\(\s*-\d", "center": r"center\s*=\s*True", "bfill": r"bfill|backfill",
    "future_index": r"\[\s*i\s*\+\s*\d+\s*\]|iloc\[\s*i\s*\+", "network": r"urlopen|requests\.|http",
    "v1_path": r"/home/z/my-project", "cointrader": r"cointrader",
}


def scan(p: Path) -> dict:
    src = p.read_text(errors="replace")
    try:
        doc = (ast.get_docstring(ast.parse(src)) or "").strip().split("\n")[0]
    except SyntaxError as e:
        doc = f"SYNTAX ERROR {e}"
    if not doc:
        doc = next((ln.lstrip("# ").strip() for ln in src.splitlines()[1:4] if ln.startswith("#")), "")
    adr = sorted(set(re.findall(r"ADR[ -]?0?(\d{3,4})", src)))
    return {"lines": src.count("\n"), "doc": doc[:160], "adr": adr,
            "flags": {k: len(re.findall(v, src)) for k, v in PATTERNS.items() if re.search(v, src)}}


def classify(name: str, legacy: set, tests_ok: set) -> tuple:
    if name.startswith("test_"):
        return "TEST", "unit test of a run/ tool (root-level research tests, CI)"
    if name in CURRENT:
        return CURRENT[name]
    if name in legacy:
        if "engine_crosscheck" in name or name.startswith(("c3_download", "c5_download", "c7_download",
                                                             "c9_download", "kp_download", "x7_download")):
            return "RETIRED_OR_ON_HOLD", ("v1 cross-check against the main repository engine (cointrader) or v1 "
                                          "download; cannot run here (LEGACY_SCRIPTS.md, ADR 0029)")
        pre = name.split("_")[0]
        if pre in LEGACY_PREFIX:
            c, why = LEGACY_PREFIX[pre]
            return c, why + "; script itself retired (v1 paths, LEGACY_SCRIPTS.md)"
        return "RETIRED_OR_ON_HOLD", "listed in LEGACY_SCRIPTS.md"
    return "NOT_SURVEYED", "no rule — new file"


def build() -> list:
    legacy = set(re.findall(r"`([^`]+\.py)`", (RUN / "LEGACY_SCRIPTS.md").read_text()))
    tests = {p.name for p in RUN.glob("test_*.py")}
    audit = RUN / "audit" / "SOURCE_AUDIT_20261110.jsonl"
    src_audit = {}
    if audit.exists():
        for line in audit.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                src_audit[r["file"]] = r["audit_level"]
    rows = []
    for p in sorted(RUN.glob("*.py")):
        s = scan(p)
        cls, why = classify(p.name, legacy, tests)
        stem = p.stem
        tested = any(t in tests for t in (f"test_{stem}.py", f"test_{stem.removesuffix('_dev')}.py",
                                          f"test_{stem.removesuffix('_sim')}.py",
                                          f"test_{stem.removesuffix('_estimate')}.py"))
        status = "TESTED" if tested else ("LOGIC_TRACED" if p.name in TRACED else "INVENTORIED")
        if status == "INVENTORIED" and p.name in src_audit:
            status = src_audit[p.name]      # whole file read in the ADR 0070 source audit
        rows.append({"file": p.name, "class": cls, "reason": why, "read_status": status,
                     "traced": TRACED.get(p.name), "legacy": p.name in legacy, **s})
    return rows


def render_md(rows: list) -> str:
    from collections import Counter
    c = Counter(r["class"] for r in rows)
    st = Counter(r["read_status"] for r in rows)
    out = ["# run/*.py classification — 2026-11-10 audit (generated)", "",
           "Generated by `research_inventory_20261110.py`; do not edit by hand.", "",
           "Read status: INVENTORIED = header, imports, paths and risk patterns scanned (not the whole body); "
           "LOGIC_TRACED = the risky lines were read and followed; TESTED = its run/test_*.py passes in local CI "
           "(3.11 and 3.13); FULL_SOURCE_REVIEW / LOGIC_TRACED / TESTED from `SOURCE_AUDIT_20261110.jsonl` = the whole "
           "file was read by the ADR 0070 reviewer (see that table for who checked what).  "
           "No file is marked CODE_READ unless its body was read.", "",
           "| class | meaning | files |", "|---|---|---|"]
    out += [f"| {k} | {v} | {c.get(k, 0)} |" for k, v in CLASSES.items()]
    out += ["", "Read status: " + ", ".join(f"{k} {v}" for k, v in sorted(st.items())), "",
            "| file | lines | class | read status | reason | flags |", "|---|---|---|---|---|---|"]
    for r in rows:
        fl = ", ".join(f"{k}={v}" for k, v in r["flags"].items())
        out.append(f"| `{r['file']}` | {r['lines']} | {r['class']} | {r['read_status']} | "
                   f"{r['reason']}{(' — traced: ' + r['traced']) if r['traced'] else ''} | {fl} |")
    return "\n".join(out) + "\n"


def main() -> int:
    rows = build()
    (HERE / "RESEARCH_SCRIPTS_20261110.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    (HERE / "RESEARCH_SCRIPTS_20261110.md").write_text(render_md(rows))
    reps, dups = strategy_records()
    (HERE / "RESEARCH_STRATEGIES_20261110.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in reps))
    print(json.dumps({"files": len(rows), "strategy_records": len(reps), "duplicates": dups}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
