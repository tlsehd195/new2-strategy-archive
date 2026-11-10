"""Which strategy inputs exist, and which are DATA_MISSING (ADR 0066).

Directive 2026-11-10 §4: every feature family the search might use is listed
with its dataset, its point-in-time rule and whether the data is present.  A
family whose data is not in the data directory is ``DATA_MISSING`` — never
filled, interpolated or approximated by another series.  Nothing here is used
by an evaluation; ``availability(data_dir)`` reports, ``check_feature_sets()``
keeps the table and the feature registry consistent.

Status words:

* ``IN_ENGINE``     registered feature of the default registry (v1)
* ``OPT_IN``        implemented, causal, enabled only with ``--feature-set v2``
* ``DATA_MISSING``  needs a dataset that is not in the data directory
* ``NOT_DERIVABLE`` the needed field does not exist in the data we have
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: dataset -> (glob under NEW2_DATA_DIR that proves presence, point-in-time rule)
DATASETS: dict = {
    "klines": ("klines/*-15m-*.csv", "15m bar known at its close; signals act at the next bar open"),
    "funding": ("funding/*-fundingRate-*.csv", "a settlement is known at its timestamp; "
                "the z uses settlements strictly before the bar"),
    "fng": ("fng/*", "daily index known with a publication lag of FNG_PUBLICATION_LAG_DAYS"),
    "open_interest": ("open_interest/*.csv", "snapshot known at its timestamp"),
    "premium_index": ("premium_index/*.csv", "premium-index klines known at their close"),
    "liquidations": ("liquidations/*.csv", "event known at its exchange timestamp"),
    "order_book": ("order_book/*.jsonl", "snapshot known at its receive time"),
    "dvol": ("dvol/*.csv", "index known at its close"),
    "trade_count": (None, "Binance kline column 8 (number of trades) is not kept by data_quality.Bar"),
}

#: feature family -> (dataset(s), status, features / note)
FAMILIES: dict = {
    "momentum": (("klines",), "IN_ENGINE", ["ret_z", "breakout_k"]),
    "mean_reversion": (("klines",), "IN_ENGINE+OPT_IN", ["rsi", "ma_dist_z (v2)"]),
    "volatility": (("klines",), "IN_ENGINE+OPT_IN", ["vol_ratio", "range_z (v2)"]),
    "flow": (("klines",), "IN_ENGINE", ["taker_share_z", "volume_z"]),
    "liquidity": (("klines",), "OPT_IN", ["illiq_z (v2)"]),
    "distribution": (("klines",), "OPT_IN", ["skew_z (v2)"]),
    "calendar": (("klines",), "IN_ENGINE+OPT_IN", ["hour_utc", "dow_utc (v2)"]),
    "carry": (("klines", "funding"), "IN_ENGINE", ["funding_z"]),
    "sentiment": (("klines", "fng"), "IN_ENGINE", ["fng_max", "fng_min"]),
    "positioning": (("open_interest",), "DATA_MISSING",
                    "no open-interest history in the repository (only a probe, research/l2_probe_log.json)"),
    "basis": (("premium_index",), "DATA_MISSING",
              "premium-index / basis history not downloaded for the engine windows (ADR 0060 used daily spot index only)"),
    "liquidation": (("liquidations",), "DATA_MISSING",
                    "collector runs on the Oracle VM (NEW2 ADR-0085); only 1.7 days are in data/liquidations.db"),
    "order_book": (("order_book",), "DATA_MISSING",
                   "96 h of public books (2026-10) — enough for cost estimates, not for a backtest window"),
    "implied_vol": (("dvol",), "DATA_MISSING", "DVOL was downloaded on the retired v1 machine only"),
    "ticket_size": (("trade_count",), "NOT_DERIVABLE", "needs the trade count per bar (X9 idea, ADR 0029)"),
}


def availability(data_dir: Path) -> dict:
    """Per dataset: present (a matching file exists) or DATA_MISSING."""
    d = Path(data_dir)
    out = {}
    for name, (pattern, rule) in DATASETS.items():
        if pattern is None:
            out[name] = {"status": "NOT_DERIVABLE", "rule": rule}
            continue
        hit = next(iter(d.glob(pattern)), None) if d.exists() else None
        out[name] = {"status": "PRESENT" if hit else "DATA_MISSING", "rule": rule}
    return out


def family_report(data_dir: Path) -> dict:
    av = availability(data_dir)
    rep = {}
    for fam, (ds, status, what) in FAMILIES.items():
        missing = [x for x in ds if av[x]["status"] != "PRESENT"]
        rep[fam] = {"datasets": list(ds), "status": status if not missing or status in
                    ("DATA_MISSING", "NOT_DERIVABLE") else "DATA_MISSING",
                    "missing": missing, "features" if isinstance(what, list) else "note": what}
    return rep


def check_feature_sets() -> list:
    """Every feature named in FAMILIES exists in the default registry or in an
    opt-in set, with the dataset the table says."""
    import strategy_spec as ss
    known = {n: f for n, f in ss.FEATURES.items()}
    for fs in ss.FEATURE_SETS.values():
        known.update({f.name: f for f in fs})
    problems = []
    for fam, (ds, _, what) in FAMILIES.items():
        if not isinstance(what, list):
            continue
        for w in what:
            name = w.split(" ")[0]
            if name not in known:
                problems.append(f"{fam}: {name} not implemented")
            elif not set(known[name].data) <= set(ds):
                problems.append(f"{fam}: {name} needs {known[name].data}, table says {ds}")
    return problems


def main(argv=None) -> int:  # pragma: no cover - CLI
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: feature_data_registry.py <NEW2_DATA_DIR>", file=sys.stderr)
        return 1
    print(json.dumps({"datasets": availability(Path(args[0])), "families": family_report(Path(args[0])),
                      "registry_problems": check_feature_sets()}, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

