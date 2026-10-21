"""Cycle runner — evaluation flow, recovery, summaries (directive §3, §10, §13).

Path configuration (§13-경로): every path comes from an environment variable
or the module directory.  A missing REQUIRED path fails loudly — the runner
never quietly reads old data from another location.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).parent))

from data_quality import (  # noqa: E402
    Bar,
    DatasetHandle,
    dataset_for,
    funding_quality,
    staleness,
)
from engine_lib import (  # noqa: E402
    CODE_VERSION,
    VALIDATION_CFG_VERSION,
    CandidateState,
    LockRegistryError,
    LockTestRegistry,
    LockedWindowError,
    ProcessLock,
    TRANSIENT_ERROR_CODES,
    assert_no_locked_overlap,
    atomic_write_json,
    code_identity,
    git_commit,
    evaluation_id,
    experiment_context,
    iso_now,
    locked_window_for,
    new_run_id,
    runtime_identity,
    symbols_without_locked_test,
)
from costs_bridge import ASSUMED_PROFILES, COST_MODEL_SCOPE, CostConfigError, CostContext  # noqa: E402
from evaluator import cell_stats, run_event_study  # noqa: E402
from generation import (  # noqa: E402
    FDR_SPEC,
    ContextStore,
    FdrDecisionStore,
    PreregistrationStore,
    build_manifest,
    DEV_OUTCOME_STATUSES,
    FdrDecisionConflict,
    decide_family,
    family_status,
)
from generator import FAMILY_DATA_REQUIREMENTS, enumerate_candidates  # noqa: E402
from portfolio import PortfolioConfig, portfolio_passes, simulate_portfolio  # noqa: E402
from store import CandidateStore, Ledger, ReverifyQueue, reconcile  # noqa: E402
from promotion import PromotionQueue, combo_promotion_gate, is_combo, promotion_gate  # noqa: E402


def load_fng_series(data_dir: Path) -> Optional[dict]:
    """Load the Fear&Greed daily series (A-18/A-19).

    Returns {utc_day_index: value} or ``None`` when the dataset is absent.
    Day index = epoch_ms // 86_400_000 (UTC day of the value's calendar
    date).  A day's value is only usable for decisions taken on LATER days
    — the publication timing is enforced by the consumers of this map.
    """
    import datetime as dt
    fdir = Path(data_dir) / "fng"
    if not fdir.exists():
        return None
    out: dict = {}
    for p in sorted(fdir.glob("*.csv")):
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                val = float(parts[1])
            except ValueError:
                continue
            try:
                d = dt.date.fromisoformat(parts[0][:10])
            except ValueError:
                continue  # header or malformed row — skipped, never guessed
            day_idx = int(dt.datetime(d.year, d.month, d.day,
                                      tzinfo=dt.timezone.utc).timestamp()
                          * 1000) // 86_400_000
            out[day_idx] = val
    return out or None


class PathConfigError(RuntimeError):
    pass


@dataclass
class PathConfig:
    root: Path
    data_dir: Path
    state_dir: Path
    results_dir: Path
    audit_dir: Path

    @classmethod
    def from_env(cls) -> "PathConfig":
        root = Path(os.environ.get(
            "NEW2_DISCOVERY_ROOT",
            str(Path(__file__).resolve().parent),
        ))
        data_dir = Path(os.environ.get(
            "NEW2_DATA_DIR", "/home/z/my-project/NEW2_run/data"))
        state_dir = Path(os.environ.get("NEW2_STATE_DIR", root / "state"))
        results_dir = Path(os.environ.get("NEW2_RESULTS_DIR", root / "results"))
        audit_dir = Path(os.environ.get("NEW2_AUDIT_DIR", root.parent / "audit"))
        if not data_dir.exists():
            raise PathConfigError(
                f"NEW2_DATA_DIR does not exist: {data_dir} "
                f"(fail-closed — set the variable to the real data directory)"
            )
        for d in (state_dir, results_dir):
            d.mkdir(parents=True, exist_ok=True)
        return cls(root=root, data_dir=data_dir, state_dir=state_dir,
                   results_dir=results_dir, audit_dir=audit_dir)


# ---------------------------------------------------------------------------
# dataset window loading (monthly files, merged + quality gated)
# ---------------------------------------------------------------------------


def merged_bars(data_dir: Path, symbol: str, start_iso: str, end_iso: str) -> DatasetHandle:
    """Monthly-file merge, HARD-TRUNCATED to the requested date window.

    A-16 fix: monthly files contain days beyond ``end_iso`` (e.g. the whole
    March file when end is 2024-03-24).  Bars after the end-of-day of
    ``end_iso`` are dropped BEFORE the quality gate runs, so bars inside a
    locked TEST window can never leak into a dev/validation handle even at
    file granularity.  No interpolation, no silent extension.
    """
    import datetime as dt
    kdir = data_dir / "klines"
    if not kdir.exists():
        raise PathConfigError(f"klines directory missing under {data_dir}")
    d0 = dt.date.fromisoformat(start_iso).replace(day=1)
    d1 = dt.date.fromisoformat(end_iso)
    cur = d0
    files: list[Path] = []
    while cur <= d1:
        p = kdir / f"{symbol}-15m-{cur:%Y-%m}.csv"
        if p.exists():
            files.append(p)
        cur = (cur.replace(day=28) + dt.timedelta(days=6)).replace(day=1)
    if not files:
        return DatasetHandle(dataset_id=f"{symbol}-15m-MISSING", symbol=symbol,
                             bars=[], sha256="", first_ts=0, last_ts=0,
                             quality=type("Q", (), {"ok": False, "to_json": lambda s: {"missing": True}})(),
                             missing=[f"no kline files for window [{start_iso},{end_iso}]"])
    # hard date cut (UTC): keep ts in [start 00:00, end_of(end_iso) 23:59:59.999]
    start_ms = int(dt.datetime(d0.year, d0.month, d0.day,
                               tzinfo=dt.timezone.utc).timestamp() * 1000)
    end_ms = int((dt.datetime(d1.year, d1.month, d1.day, tzinfo=dt.timezone.utc)
                  + dt.timedelta(days=1)).timestamp() * 1000) - 1
    bars: list[Bar] = []
    sha = hashlib.sha256()
    for p in sorted(files):
        # A-28 fix: dataset_for returns ONE handle (the draft unpacked two and
        # crashed every evaluation before any test exercised the full path)
        h = dataset_for(symbol, p)
        if h is None or not h.quality.ok:
            return DatasetHandle(
                dataset_id=f"{symbol}-15m-REJECTED", symbol=symbol, bars=[],
                sha256="", first_ts=0, last_ts=0,
                quality=h.quality if h else type(
                    "Q", (), {"ok": False, "to_json": lambda s: {"missing": True}})(),
                missing=[f"file {p.name} failed the quality gate"])
        bars.extend(b for b in h.bars if start_ms <= b.ts <= end_ms)
        sha.update(p.name.encode())
        sha.update(h.sha256.encode())
    from data_quality import check_bars
    rep = check_bars(bars, symbol)
    rep.file_sha256 = sha.hexdigest()
    if not rep.ok:
        return DatasetHandle(dataset_id=f"{symbol}-15m-REJECTED", symbol=symbol,
                             bars=[], sha256=rep.file_sha256, first_ts=0, last_ts=0,
                             quality=rep, missing=[f"merged window failed quality: {rep.errors}"])
    return DatasetHandle(dataset_id=f"{symbol}-15m-{rep.file_sha256[:12]}",
                         symbol=symbol, bars=bars, sha256=rep.file_sha256,
                         first_ts=rep.first_ts or 0, last_ts=rep.last_ts or 0,
                         quality=rep)


def load_funding_window_checked(data_dir: Path, symbol: str, start_iso: str,
                                end_iso: str) -> tuple[list[tuple[int, float]], dict]:
    """Monthly funding files merged; parse errors / torn tails of ANY file are
    reported (and make :func:`funding_quality` fail) instead of vanishing."""
    from data_quality import load_funding_csv_checked
    fdir = data_dir / "funding"
    rep = {"parse_errors": [], "torn_tail": False, "intervals": {}}
    if not fdir.exists():
        return [], rep
    import datetime as dt
    d0 = dt.date.fromisoformat(start_iso).replace(day=1)
    d1 = dt.date.fromisoformat(end_iso)
    out: list[tuple[int, float]] = []
    cur = d0
    while cur <= d1:
        p = fdir / f"{symbol}-fundingRate-{cur:%Y-%m}.csv"
        if p.exists():
            pairs, r = load_funding_csv_checked(p)
            out.extend(pairs)
            rep["parse_errors"] += [f"{p.name}:{ln}" for ln in r["parse_errors"]]
            rep["torn_tail"] = rep["torn_tail"] or r["torn_tail"]
            rep["intervals"].update(r["intervals"])
        cur = (cur.replace(day=28) + dt.timedelta(days=6)).replace(day=1)
    out.sort(key=lambda x: x[0])
    return out, rep


def load_funding_window(data_dir: Path, symbol: str, start_iso: str, end_iso: str) -> list[tuple[int, float]]:
    return load_funding_window_checked(data_dir, symbol, start_iso, end_iso)[0]


def checked_funding_quality(pairs, rep: Mapping) -> dict:
    return funding_quality(pairs, parse_errors=rep.get("parse_errors", ()),
                           torn_tail=rep.get("torn_tail", False),
                           intervals=rep.get("intervals"))


# ---------------------------------------------------------------------------
# windows with embargo (§6)
# ---------------------------------------------------------------------------

VALIDATION_MONTHS = 6


#: minimum embargo between dev and validation (and before the locked window):
#: one UTC day covers the FNG publication lag and daily-feature overlap even
#: for short holding periods (auto-evolution §2-3)
EMBARGO_MIN_BARS = 96


def split_windows(symbol: str, first_ts: int, last_ts: int, horizon: int,
                  ts_list: Optional[Sequence[int]] = None) -> dict:
    """dev / independent-validation / locked-TEST index windows.

    - symbols without a locked window get NO_LOCKED_TEST (final claims are
      impossible; directive §6)
    - an embargo of max(horizon, EMBARGO_MIN_BARS) bars separates dev from
      validation so no holding period or publication lag straddles the
      boundary; validation also ends one embargo before the readable end
    - with ``ts_list`` the indices come from the actual bar timestamps
      (robust to documented exchange gaps and to a first bar that is not at
      00:00 UTC); without it a gap-free 15m grid is assumed (legacy callers)
    """
    import datetime as dt
    from bisect import bisect_left
    lock = locked_window_for(symbol)
    ms = 15 * 60 * 1000
    embargo = max(int(horizon), EMBARGO_MIN_BARS)
    last_dt = dt.datetime.fromtimestamp(last_ts / 1000, tz=dt.timezone.utc).date()
    if lock:
        lock_start = dt.date.fromisoformat(lock[0])
        val_start = lock_start - dt.timedelta(days=30 * VALIDATION_MONTHS)
    else:
        val_start = last_dt - dt.timedelta(days=30 * VALIDATION_MONTHS)
    val_start_ms = int(dt.datetime(val_start.year, val_start.month, val_start.day,
                                   tzinfo=dt.timezone.utc).timestamp() * 1000)
    if ts_list is not None:
        n_bars = len(ts_list)
        idx_val_start = bisect_left(list(ts_list), val_start_ms)
    else:
        n_bars = (last_ts - first_ts) // ms + 1
        idx_val_start = (val_start_ms - first_ts) // ms
    # A-27: data may start AFTER the validation window begins — clamp
    idx_val_start = max(0, min(int(idx_val_start), n_bars))
    idx_dev_end = max(1, idx_val_start - embargo)
    return {
        "dev": (0, max(2, idx_dev_end)),
        "validation": (idx_val_start, n_bars - 1 - embargo),
        "locked": lock,
        "no_locked_test": lock is None,
        "embargo_bars": embargo,
        "validation_start_ms": val_start_ms,
    }


# ---------------------------------------------------------------------------
# signal builders — every signal uses ONLY the prefix view (no lookahead)
# ---------------------------------------------------------------------------


def _rolling_z(vals: Sequence[Optional[float]], i: int, window: int) -> Optional[float]:
    if i < window:
        return None
    xs = [v for v in vals[i - window:i] if v is not None and v == v]
    if len(xs) < window * 0.9:
        return None
    mu = sum(xs) / len(xs)
    var = sum((x - mu) ** 2 for x in xs) / max(1, len(xs) - 1)
    sd = var ** 0.5
    if sd <= 0:
        return None
    cur = vals[i]
    if cur is None or cur != cur:
        return None
    return (cur - mu) / sd


def build_signal_fn(defn: Mapping, aux: Mapping) -> Callable:
    if is_spec(defn):
        from strategy_spec import build_spec_signal
        return build_spec_signal(defn, aux)
    fam = defn["family"]
    # canonical definitions drop default-valued params — resolve_params is the
    # ONE resolver shared with build_aux (auto-evolution §2-2)
    from generator import resolve_params
    p = resolve_params(defn)

    if fam == "flow_z":
        share = aux["taker_share"]
        zw, ez = p["z_window"], p["entry_z"]

        def sig(view):
            i = len(view) - 1
            z = _rolling_z(share, i, zw)
            if z is None:
                return 0
            return -1 if z > ez else 0  # preregistered contrarian
        return sig

    if fam == "ret_rev":
        closes = aux["closes"]
        lb, tz = p["lookback"], p["thresh_z"]
        rets = aux["lb_returns"]

        def sig(view):
            i = len(view) - 1
            if i < lb * 4:
                return 0
            z = _rolling_z(rets, i, max(64, lb * 2))
            if z is None:
                return 0
            return 1 if z < -tz else 0
        return sig

    if fam == "ts_mom":
        rets = aux["lb_returns"]
        lb, tz = p["lookback"], p["thresh_z"]

        def sig(view):
            i = len(view) - 1
            z = _rolling_z(rets, i, max(64, lb * 2))
            if z is None:
                return 0
            if z > tz:
                return 1
            if z < -tz:
                return -1
            return 0
        return sig

    if fam == "vol_break":
        closes = aux["closes"]
        vw, k = p["vol_window"], p["k"]

        def sig(view):
            i = len(view) - 1
            if i < vw + 1:
                return 0
            w = closes[i - vw:i]
            roll_max = max(w)
            mu = sum(w) / vw
            sd = (sum((x - mu) ** 2 for x in w) / max(1, vw - 1)) ** 0.5
            if sd <= 0:
                return 0
            return 1 if closes[i] > roll_max + k * sd else 0
        return sig

    if fam == "tod":
        h0, h1 = p["utc_start"], p["utc_end"]
        d = 1 if defn["direction"].startswith("long") else -1

        def sig(view):
            i = len(view) - 1
            hour = (view[i].ts // 3_600_000) % 24
            if h0 <= hour < h1:
                return d
            return 0
        return sig

    if fam == "fund_z":
        fz = aux["funding_z"]
        tz = p["thresh_z"]

        def sig(view):
            i = len(view) - 1
            z = fz[i] if i < len(fz) else None
            if z is None:
                return 0
            return -1 if z > tz else 0  # contrarian
        return sig

    if fam == "fng":
        fng_hist = aux.get("fng_hist")
        if fng_hist is None:
            raise LookupError("FNG dataset absent")
        th, conf = p["threshold"], p["confirm_days"]
        lo = defn["direction"].startswith("long")

        def sig(view):
            i = len(view) - 1
            h = fng_hist[i] if i < len(fng_hist) else None
            if not h:
                return 0
            days = h[:conf]           # the last `conf` COMPLETED days
            if any(v is None for v in days):
                return 0
            if lo and all(v <= th for v in days):
                return 1
            if not lo and all(v >= th for v in days):
                return -1
            return 0
        return sig

    raise ValueError(f"unknown family {fam}")


#: publication lag of the daily Fear&Greed value: the value labelled UTC day
#: D is used only from day D + FNG_PUBLICATION_LAG_DAYS on (alternative.me
#: stamps the value with the day's 00:00 UTC; it is updated during that day,
#: so the same-day value is never treated as known)
FNG_PUBLICATION_LAG_DAYS = 1


def funding_known_series(bars: Sequence[Bar], funding: Sequence[tuple[int, float]]) -> list[int]:
    """For each bar i: the number of settlements with calc_time <= close of
    bar i (the settlements a decision at that close may know)."""
    horizon_ms = 15 * 60 * 1000
    out: list[int] = []
    j = 0
    for b in bars:
        close_ts = b.ts + horizon_ms
        while j < len(funding) and funding[j][0] <= close_ts:
            j += 1
        out.append(j)
    return out


def funding_settlement_z(funding: Sequence[tuple[int, float]], known: Sequence[int],
                         window: int) -> list[Optional[float]]:
    """z-score of the LATEST known settlement against the ``window``
    settlements before it (auto-evolution §2-3).  The previous per-bar version
    took a rolling z over 15-minute bars of a step function: the window held
    one or two distinct rates, its sd was mostly 0 and the signal almost
    never fired; ``funding_window`` now counts SETTLEMENTS."""
    rates = [r for _, r in funding]
    cache: dict[int, Optional[float]] = {}
    out: list[Optional[float]] = []
    for k in known:
        if k not in cache:
            if k - 1 < window:
                cache[k] = None
            else:
                prev = rates[k - 1 - window:k - 1]
                mu = sum(prev) / window
                var = sum((x - mu) ** 2 for x in prev) / max(1, window - 1)
                sd = var ** 0.5
                cache[k] = (rates[k - 1] - mu) / sd if sd > 0 else None
        out.append(cache[k])
    return out


def build_aux(bars: Sequence[Bar], defn: Mapping,
              funding: Optional[Sequence[tuple[int, float]]],
              fng: Optional[Mapping] = None) -> dict:
    """Per-bar auxiliary series computed from PAST data only.

    Funding information at bar i uses settlements with calc_time <= the bar's
    close time — a decision at the close of bar i can only know funding that
    has already been published (§9-4 발표 시점 구분).

    FNG (A-18): per-bar ``fng_hist[i]`` holds the last 10 CONFIRMED daily
    values as of bar i — values of days at least FNG_PUBLICATION_LAG_DAYS
    before the bar's own UTC day.  Today's value can never leak.

    Parameters come from :func:`generator.resolve_params` — the SAME resolver
    as the signal builder, so a default-omitted canonical definition computes
    exactly what the explicit one computes (the old code read the stripped
    ``defn["params"]`` and fell back to unrelated numbers: ret_rev's default
    lookback 4 ran as 32, fund_z's default window 30 ran as 90).
    """
    if is_spec(defn):
        from strategy_spec import build_spec_aux
        return build_spec_aux(bars, defn, funding, fng)
    from generator import resolve_params
    n = len(bars)
    closes = [b.close for b in bars]
    taker_share = [None] * n
    for i, b in enumerate(bars):
        if b.volume > 0:
            taker_share[i] = b.taker_buy_volume / b.volume
    p = resolve_params(defn)
    lb = int(p.get("lookback") or 32)
    lb_returns: list[Optional[float]] = [None] * n
    for i in range(lb, n):
        if closes[i - lb] > 0:
            lb_returns[i] = closes[i] / closes[i - lb] - 1.0
    aux: dict = {"closes": closes, "taker_share": taker_share, "lb_returns": lb_returns,
                 "resolved_params": dict(p)}
    if funding is not None:
        known = funding_known_series(bars, funding)
        zwin = int(p.get("funding_window") or 30)
        aux["funding_z"] = funding_settlement_z(funding, known, zwin)
    if fng is not None:
        day_ms = 86_400_000
        k_max = 10  # generator caps confirm_days at 3; headroom stays bounded
        lag = FNG_PUBLICATION_LAG_DAYS
        fng_hist: list = [None] * n
        for i, b in enumerate(bars):
            d = b.ts // day_ms
            fng_hist[i] = [fng.get(d - k) for k in range(lag, lag + k_max)]
        aux["fng_hist"] = fng_hist
    return aux


def is_spec(defn: Mapping) -> bool:
    return defn.get("schema") == "strategy_spec_v1"


def defn_horizon(defn: Mapping) -> int:
    if is_spec(defn):
        return int(defn["exit"]["horizon"])
    return int(defn["holding"])


def defn_requirements(defn: Mapping) -> list[str]:
    if is_spec(defn):
        from strategy_spec import spec_requirements
        return spec_requirements(defn)
    return FAMILY_DATA_REQUIREMENTS.get(defn["family"], ["klines"])


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------

ORDER_VALUE_DEFAULT = 10_000.0
READ_START = "2021-01-01"
READ_END_NO_LOCK = "2026-12-31"

#: data cleaning / timing rules that change results (P1-7).  Changing any of
#: these changes the experiment context id.
DATA_POLICY = {
    "version": "datapol-v1",
    "timezone": "UTC (epoch ms; day boundaries in UTC)",
    "kline_interval_ms": 900_000,
    "window_cut": "hard truncation to [start 00:00, end 23:59:59.999] UTC before the quality gate",
    "missing_candles": "fail-closed, never interpolated (exact 15m grid; only documented exchange exceptions excuse a gap)",
    "quality_policy": "dq-v2 (OK/WARN/BLOCK; BLOCK never reaches a backtest)",
    "embargo": "max(horizon, 96 bars) between dev and validation and before the readable end",
    "fng_publication_lag_days": 1,
    "funding_z": "z of the latest known settlement vs the previous funding_window settlements",
    "funding_gaps": "holding windows overlapping a settlement gap are dropped (never summed short)",
    "max_abs_logret_15m": 0.20,
    "funding_known_at": "calc_time <= bar close (no early use)",
    "funding_quality": "duplicates/inversions fail-closed; event dropped when holding window not covered",
    "fng_known_at": "values of UTC days strictly before the bar's day",
    "read_start": READ_START,
    "read_end": "day before the symbol's locked TEST window, else " + READ_END_NO_LOCK,
}


def data_policy() -> dict:
    """DATA_POLICY plus the identity of the documented exchange-exception
    file (changing an exception changes which data may be used)."""
    from data_quality import exceptions_identity
    return {**DATA_POLICY, "data_exceptions": exceptions_identity()}


def _series_sha(pairs) -> str:
    h = hashlib.sha256()
    for t, r in sorted(pairs):          # order-independent: same series -> same hash
        h.update(f"{int(t)}:{float(r)!r};".encode())
    return h.hexdigest()


@dataclass
class CycleSummary:
    status: str  # SUCCESS | SUCCESS_WITH_WARNINGS | PARTIAL_FAILURE | FAILED | INTERRUPTED
    started_at: str = ""
    finished_at: str = ""
    run_id: str = ""
    batch_requested: int = 0
    generation_id: str = ""
    context_id: str = ""
    total_candidates: int = 0
    evaluated: int = 0
    verdict_counts: dict = field(default_factory=dict)
    errors: int = 0
    reverify_queue: int = 0
    duplicates_collapsed: int = 0
    runtime_seconds: float = 0.0
    fatal: Optional[str] = None
    search_space_state: str = ""
    fdr_family: dict = field(default_factory=dict)
    finalized: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def to_json(self) -> dict:
        return dict(self.__dict__)


def selection_read_end(sym: str) -> str:
    """Last day dev/validation selection may read for ``sym``: the day before
    its locked window, or :data:`READ_END_NO_LOCK`."""
    import datetime as dt
    lock_s = locked_window_for(sym)
    return (dt.date.fromisoformat(lock_s[0]) - dt.timedelta(days=1)).isoformat() \
        if lock_s else READ_END_NO_LOCK


def locked_independence_issues(symbols: Sequence[str]) -> list[str]:
    """ADR 0010: a multi-symbol candidate's locked TEST is independent only if
    no OTHER symbol's selection read window covers a symbol's locked window
    (BTC and ETH share market-wide shocks, so ETH selection data from inside
    the BTC locked window carries information about it).  Pure, reads no data;
    an empty list means independent."""
    out: list[str] = []
    syms = list(dict.fromkeys(symbols))
    for x in syms:
        w = locked_window_for(x)
        if not w:
            continue
        lo, hi = w
        for y in syms:
            if y == x:
                continue
            y_end = selection_read_end(y)
            if READ_START <= hi and lo <= y_end:
                out.append(f"{y} selection data [{READ_START},{y_end}] covers "
                           f"{x} locked window [{lo},{hi}]")
    return out


class Runner:
    def __init__(self, paths: PathConfig, *, order_value: float = ORDER_VALUE_DEFAULT,
                 cost: Optional[CostContext] = None,
                 candidates_fn: Optional[Callable] = None,
                 allow_test_cost: bool = False):
        self.paths = paths
        self.order_value = order_value
        self.cost = cost or CostContext.futures_default()
        if self.cost.test_only and not allow_test_cost:
            raise ValueError("test-only cost context refused by the production runner")
        self.candidates_fn = candidates_fn or enumerate_candidates
        self.store = CandidateStore(paths.state_dir / "state.json")
        self.queue = ReverifyQueue(paths.state_dir / "queue.json", self.store)
        self.ledger = Ledger(paths.state_dir / "discovery_ledger.jsonl")
        self.lock_registry = LockTestRegistry(paths.state_dir / "lock_test_usage.json")
        self.promo = PromotionQueue(paths.state_dir / "promotion_queue.jsonl")
        self.prereg = PreregistrationStore(paths.state_dir)
        self.fdr_store = FdrDecisionStore(paths.state_dir / "fdr_decisions.jsonl")
        self.contexts = ContextStore(paths.state_dir)
        from campaigns import CampaignLedger
        self.campaigns = CampaignLedger(paths.state_dir / "locked_window_campaigns.jsonl")
        self._campaign: Optional[dict] = None
        self.run_id = new_run_id()
        self._interrupted = False
        self._fng_cache: Optional[dict] = None
        self._fng_loaded = False
        self._data_cache: dict = {}
        self._manifest: Optional[dict] = None
        self._cands: list = []
        self._defn_by_sid: dict = {}
        self._ctx: Optional[str] = None
        for _sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(_sig, self._on_signal)
            except (ValueError, OSError):
                pass

    def _on_signal(self, *a) -> None:  # pragma: no cover
        self._interrupted = True

    # -- generation / preregistration (P1-8) ---------------------------------
    def register_all(self) -> dict:
        cands, dedup = self.candidates_fn()
        manifest = build_manifest(cands, dedup, settings={
            "validation": self._validation_cfg(), "data_policy": data_policy()})
        info = self.prereg.ensure(manifest)
        self._manifest = manifest
        self._cands = list(cands)
        self._defn_by_sid = dict(cands)
        known = self.store.all_states()
        for sid, _ in cands:
            if sid not in known:
                self.store.set(sid, CandidateState.PENDING)
        return {"registered": len(cands), "duplicates_collapsed": len(dedup),
                "generation_id": manifest["generation_id"], "created": info["created"]}

    def _ensure_registered(self) -> None:
        if self._manifest is None:
            self.register_all()

    @property
    def generation_id(self) -> str:
        self._ensure_registered()
        return self._manifest["generation_id"]

    # -- data + experiment context (P1-7) ----------------------------------
    def _pf_cfg(self) -> PortfolioConfig:
        return PortfolioConfig(initial_capital=self.order_value)

    def _validation_cfg(self) -> dict:
        from engine_lib import LOCKED_TEST_PERIODS, MIN_VALID_N
        from portfolio import PORTFOLIO_MODEL_VERSION, PORTFOLIO_PASS_CRITERIA
        return {
            "version": VALIDATION_CFG_VERSION,
            "validation_months": VALIDATION_MONTHS,
            "min_valid_n": MIN_VALID_N,
            "cell_alpha": 0.05,
            "cell_test": "one-sided Newey-West HAC t on non-overlapping events",
            "embargo": "horizon bars between dev and validation",
            "locked_windows": {k: list(v) for k, v in sorted(LOCKED_TEST_PERIODS.items())},
            "order_value": self.order_value,
            "portfolio": dict(self._pf_cfg().__dict__),
            "portfolio_model": PORTFOLIO_MODEL_VERSION,
            "portfolio_pass": PORTFOLIO_PASS_CRITERIA,
            "fdr": FDR_SPEC,
            "locked_campaigns": _locked_campaign_cfg(),
        }

    @staticmethod
    def _read_end(sym: str) -> str:
        return selection_read_end(sym)

    @staticmethod
    def _lock_independence(symbols: Sequence[str]) -> list[str]:
        return locked_independence_issues(symbols)

    def _sym_data(self, sym: str) -> dict:
        """Per-symbol dev/validation data, loaded once per runner.  The read
        window is fixed BEFORE any bars are loaded so locked-TEST data is
        never even read into memory (A-16, §6); each symbol gets its OWN
        read_end (A-29)."""
        if sym in self._data_cache:
            return self._data_cache[sym]
        import datetime as dt
        read_end = self._read_end(sym)
        h = merged_bars(self.paths.data_dir, sym, READ_START, read_end)
        entry: dict = {"handle": h, "funding": None, "fq": None, "missing": []}
        if not h.bars:
            entry["missing"] = list(h.missing or [f"{sym}: no data"])
        else:
            first_d = dt.datetime.fromtimestamp(h.first_ts / 1000, tz=dt.timezone.utc).date().isoformat()
            if locked_window_for(sym):
                assert_no_locked_overlap(sym, first_d, read_end, "dev/validation selection")
            # A-17: holding-period funding applies to EVERY family; missing or
            # corrupt funding is fail-closed, never replaced with a guess
            f, frep = load_funding_window_checked(self.paths.data_dir, sym, first_d, read_end)
            fq = checked_funding_quality(f, frep)
            entry["fq"] = fq
            if fq["ok"]:
                entry["funding"] = f
            else:
                entry["missing"] = [f"{sym}: funding unavailable/quality {fq['issues']}"]
        self._data_cache[sym] = entry
        return entry

    def _fng(self) -> Optional[dict]:
        if not self._fng_loaded:
            self._fng_cache = load_fng_series(self.paths.data_dir)
            self._fng_loaded = True
        return self._fng_cache

    def _current_context(self) -> str:
        """Experiment context over the generation's symbol union, so every
        candidate of the generation is tested under ONE context (the FDR
        family must not fragment by family-specific inputs)."""
        self._ensure_registered()
        syms = sorted({s for _, d in self._cands for s in d.get("symbols", [])})
        data: dict = {}
        for sym in syms:
            e = self._sym_data(sym)
            h = e["handle"]
            kl = ({"sha256": h.sha256, "first_ts": h.first_ts, "last_ts": h.last_ts,
                   "rows": len(h.bars)} if h.bars else {"missing": list(e["missing"])})
            if e["funding"] is not None:
                fu = {"sha256": _series_sha(e["funding"]), "n": len(e["funding"])}
            else:
                fu = {"missing": (e["fq"] or {}).get("issues") or ["not loaded (klines missing)"]}
            data[sym] = {"klines": kl, "funding": fu}
        fng = self._fng()
        data["_fng"] = ({"sha256": _series_sha(sorted(fng.items())), "n": len(fng)}
                        if fng else {"missing": "FNG dataset absent"})
        comps = {
            "data": data,
            "data_policy": data_policy(),
            "cost": self.cost.identity(),
            "code": {"code_hash": code_identity()["code_hash"]},
            "validation": {**self._validation_cfg(),
                           "strategy_space": {"generation_id": self.generation_id,
                                              "manifest_hash": self._manifest["manifest_hash"],
                                              "generator_version": self._manifest["generator_version"]}},
            "runtime": runtime_identity(),
        }
        ctx, norm = experiment_context(comps)
        self.contexts.ensure(ctx, norm, info={"git_commit": git_commit()})
        self._ctx = ctx
        return ctx

    # -- single candidate (dev phase) ------------------------------------------
    def run_one(self, sid: str, defn: Mapping) -> dict:
        self._ensure_registered()
        if self._ctx is None:
            self._current_context()
        cur = self.store.entry(sid)
        if CandidateState(cur.state) is not CandidateState.PENDING:
            return {"skipped": True, "state": cur.state}
        ctx = self._ctx
        ev_id = evaluation_id(sid, ctx)
        # the evaluation id is announced BEFORE the work starts so a crash
        # between "ledger written" and "state written" is recoverable without
        # a duplicate evaluation (store.reconcile)
        self.store.set(sid, CandidateState.RUNNING, pending_evaluation_id=ev_id)
        started = time.time()
        base = {"phase": "dev", "generation_id": self.generation_id, "context_id": ctx,
                "run_id": self.run_id, "code_version": CODE_VERSION,
                "validation_cfg_version": VALIDATION_CFG_VERSION,
                "in_generation": sid in self._defn_by_sid}
        try:
            rec = self._evaluate(sid, defn)
        except LockedWindowError as e:
            # selection must never read the locked window — hard stop
            rec = {"status": "ERROR", "error_code": "CONFIG_ERROR", "error_message": str(e)}
        except (FileNotFoundError, LookupError) as e:
            rec = {"status": "DATA_MISSING", "missing": [str(e)]}
        except Exception as e:  # noqa: BLE001 — classified below
            rec = {"status": "ERROR", "error_code": "LOGIC_ERROR",
                   "error_message": f"{type(e).__name__}: {e}"}
        rec = {**base, **rec}
        rec["evaluation_id"] = ev_id
        rec["strategy_id"] = sid
        rec["runtime_seconds"] = time.time() - started
        status = rec["status"]
        self.ledger.append(rec)
        if status == "AWAITING_FDR":
            self.store.set(sid, CandidateState.AWAITING_FDR, data_version=ctx, pending_evaluation_id=None)
        elif status in ("FAIL_COST", "FAIL_STAT", "UNDETERMINED_POWER", "DATA_MISSING"):
            self.store.set(sid, CandidateState(status),
                           error_code=rec.get("error_code"),
                           error_message=rec.get("error_message"),
                           data_version=ctx, pending_evaluation_id=None)
            self._maybe_enqueue_reverify(sid, status)
        else:
            e_code = rec.get("error_code", "LOGIC_ERROR")
            self.store.set(sid, CandidateState.ERROR,
                           error_code=e_code, error_message=rec.get("error_message"),
                           pending_evaluation_id=None)
            self._maybe_enqueue_reverify(sid, "ERROR", error_code=e_code)
        return {"skipped": False, "status": status, "record": rec}

    def prior_pass_needing_reevaluation(self) -> list[str]:
        """Terminal-PASS candidates (earned under ANOTHER context) that have no dev
        outcome under the current context.  They stay PASS (their locked TEST is
        consumed and immutable) but a PASS from another environment is not an
        evaluation of this one: the FDR family needs a fresh current-context
        dev statistic for them."""
        self._ensure_registered()
        if self._ctx is None:
            self._current_context()
        have = {r.get("strategy_id") for r in self.ledger.records()
                if r.get("phase") == "dev" and r.get("context_id") == self._ctx
                and r.get("generation_id") == self.generation_id
                and r.get("status") in DEV_OUTCOME_STATUSES}
        return [sid for sid, _ in self._cands
                if CandidateState(self.store.entry(sid).state) is CandidateState.PASS
                and sid not in have]

    def reevaluate_prior_pass(self, sid: str, defn: Mapping) -> dict:
        """Dev-phase re-evaluation of a prior-context PASS under the current
        context.  Writes a dev ledger record flagged ``prior_pass_reeval``; the
        candidate state stays PASS and the locked TEST is never touched."""
        ctx = self._ctx
        started = time.time()
        base = {"phase": "dev", "generation_id": self.generation_id, "context_id": ctx,
                "run_id": self.run_id, "code_version": CODE_VERSION,
                "validation_cfg_version": VALIDATION_CFG_VERSION,
                "in_generation": sid in self._defn_by_sid, "prior_pass_reeval": True}
        try:
            rec = self._evaluate(sid, defn)
        except LockedWindowError as e:
            rec = {"status": "ERROR", "error_code": "CONFIG_ERROR", "error_message": str(e)}
        except (FileNotFoundError, LookupError) as e:
            rec = {"status": "DATA_MISSING", "missing": [str(e)]}
        except Exception as e:  # noqa: BLE001
            rec = {"status": "ERROR", "error_code": "LOGIC_ERROR",
                   "error_message": f"{type(e).__name__}: {e}"}
        rec = {**base, **rec}
        rec["evaluation_id"] = evaluation_id(sid, ctx)
        rec["strategy_id"] = sid
        rec["runtime_seconds"] = time.time() - started
        self.ledger.append(rec)
        return {"skipped": False, "status": rec["status"], "record": rec, "prior_pass_reeval": True}

    def _maybe_enqueue_reverify(self, sid: str, status: str, error_code: Optional[str] = None) -> None:
        e = self.store.entry(sid)
        # A-22: the DATA_MISSING reason is a *check schedule*, not a claim that
        # data has arrived — the real availability gate is the re-evaluation
        # itself (data reload + quality gate) under max-attempt control.
        reason = {
            "DATA_MISSING": "data_refresh_check",
            "ERROR": "transient_error_retry",
        }.get(status)
        if status == "ERROR" and error_code not in TRANSIENT_ERROR_CODES:
            return  # permanent — never retried
        if reason:
            from engine_lib import backoff_seconds
            if self.queue.enqueue(sid, reason):
                self.queue.requeue_with_backoff(sid, backoff_seconds(e.retry.attempt_count + 1))

    def _evaluate(self, sid: str, defn: Mapping) -> dict:
        fam = defn["family"]
        required = list(defn["symbols"])
        horizon = defn_horizon(defn)
        needs = defn_requirements(defn)
        handles: dict[str, DatasetHandle] = {}
        fundings: dict[str, list] = {}
        missing: list[str] = []
        for sym in required:
            e = self._sym_data(sym)
            if e["missing"]:
                missing.extend(e["missing"])
                continue
            handles[sym] = e["handle"]
            fundings[sym] = e["funding"]
        common = {"primary_symbol": required[0], "primary_dev_p": None,
                  "dataset": {s: handles[s].identity() for s in handles},
                  "cost_model": {"version": self.cost.version,
                                 "status": self.cost.status,
                                 "assumption_profile": (self.cost.assumption or {}).get("profile_id"),
                                 "params": dict(self.cost.params),
                                 "test_only": self.cost.test_only,
                                 "scope": COST_MODEL_SCOPE}}
        if missing:
            return {**common, "status": "DATA_MISSING", "missing": missing}
        fng = None
        if "fng" in needs:
            fng = self._fng()
            if fng is None:
                return {**common, "status": "DATA_MISSING",
                        "missing": ["FNG dataset not present in this environment"]}
        ctx = self._ctx
        last_bars = max(h.last_ts for h in handles.values())
        st = staleness(last_bars, int(time.time() * 1000))
        # event studies on dev + validation windows per symbol
        results_dev: dict[str, dict] = {}
        results_val: dict[str, dict] = {}
        es_counts: dict[str, dict] = {}
        val_missing: list[str] = []
        for sym, h in handles.items():
            aux = build_aux(h.bars, defn, fundings.get(sym), fng=fng)
            sig_fn = build_signal_fn(defn, aux)
            win = split_windows(sym, h.first_ts, h.last_ts, horizon, [b.ts for b in h.bars])
            d0, d1 = win["dev"]
            sub = h.bars[:d1 + 1]
            es = run_event_study(sub, sym, sig_fn, horizon, self.cost,
                                 self.order_value, fundings.get(sym))
            results_dev[sym] = cell_stats(es.events, sym, f"{fam}|dev")
            cnt = {k: v for k, v in es.to_json().items() if k != "events"}
            v0, v1 = win["validation"]
            if v1 > v0 + horizon and 0 <= v0 < len(h.bars) and v1 < len(h.bars):
                subv = h.bars[v0:v1 + 1]
                auxv = build_aux(subv, defn, fundings.get(sym), fng=fng)
                sigv = build_signal_fn(defn, auxv)
                esv = run_event_study(subv, sym, sigv, horizon, self.cost,
                                      self.order_value, fundings.get(sym))
                results_val[sym] = cell_stats(esv.events, sym, f"{fam}|val")
                cnt = {"dev": cnt, "val": {k: v for k, v in esv.to_json().items() if k != "events"}}
            else:
                val_missing.append(sym)
                cnt = {"dev": cnt}
            es_counts[sym] = cnt
        # dev verdict (multi-symbol rules, §8)
        from engine_lib import CellResult, SymbolResult, verdict_from_cells

        def _verdict(results: Mapping) -> tuple[str, dict]:
            cells = [CellResult(symbol=r["symbol"], cell_id=r["cell_id"], n=r["n"],
                                mean_bp=r["mean_bp"], t=r["t"], p=r["p"],
                                status=r["status"]) for r in results.values()]
            symres = [SymbolResult(symbol=c.symbol, cells=[c],
                                   status=_agg_symbol_status([c])) for c in cells]
            return verdict_from_cells(symres, required)

        dev_verdict, dev_detail = _verdict(results_dev)
        if val_missing:
            val_verdict, val_detail = "DATA_MISSING", {"missing_symbols": val_missing}
        else:
            val_verdict, val_detail = _verdict(results_val)
        primary_p = None
        r0 = results_dev.get(required[0])
        if r0 is not None and isinstance(r0.get("p"), float) and math.isfinite(r0["p"]):
            primary_p = r0["p"]
        if dev_verdict == "PASS":
            status = "AWAITING_FDR"      # P0-1: no final verdict before the family is complete
        elif dev_verdict == "ERROR":
            status = "ERROR"
        else:
            status = dev_verdict
        # portfolio simulation for dev-passing candidates (dev data only)
        portfolio: Optional[dict] = None
        if dev_verdict == "PASS":
            grid_bars = {s: handles[s].bars[:win_for(handles[s], horizon)[1] + 1]
                         for s in handles}
            sigs: list[dict] = []
            for sym, h in handles.items():
                aux = build_aux(h.bars, defn, fundings.get(sym), fng=fng)
                sig_fn = build_signal_fn(defn, aux)
                for i in range(1, len(grid_bars[sym]) - horizon - 1):
                    side = sig_fn(PrefixShim(grid_bars[sym], i + 1))
                    if side:
                        sigs.append({"symbol": sym, "decision_index": i,
                                     "side": side, "horizon": horizon})
            try:
                pf = simulate_portfolio(grid_bars, sigs,
                                        {s: fundings.get(s, []) for s in grid_bars},
                                        self.cost, self._pf_cfg(), context_id=ctx)
                portfolio = pf.to_json()
                portfolio["passed"], portfolio["pass_failures"] = portfolio_passes(portfolio)
            except ValueError as e:   # e.g. symbols on different grids
                portfolio = {"error": str(e), "passed": False, "context_id": ctx}
        rec = {
            **common,
            "status": status,
            "primary_dev_p": primary_p,
            "dev_verdict": dev_verdict,
            "dev_detail": dev_detail,
            "validation_verdict": val_verdict,
            "validation_detail": val_detail,
            "cells_dev": list(results_dev.values()),
            "cells_val": list(results_val.values()),
            "event_study_counts": es_counts,
            "portfolio": portfolio,
            "staleness": st,
        }
        if status == "ERROR":
            rec["error_code"] = "LOGIC_ERROR"
            rec["error_message"] = "statistical computation error in a dev cell"
        return rec

    # -- generation FDR finalization (P0-1) + locked TEST (P1-6) ------------
    def finalize_fdr(self) -> dict:
        self._ensure_registered()
        if self._ctx is None:
            self._current_context()
        gid, ctx = self.generation_id, self._ctx
        records = list(self.ledger.records())
        states = self.store.all_states()
        terminal_pass = {s for s, st in states.items() if st == CandidateState.PASS.value}
        fam = family_status(records, self._manifest, ctx, terminal_pass=terminal_pass)
        report = {"generation_id": gid, "context_id": ctx, "m": fam["m"],
                  "evaluated": fam["evaluated"], "missing": len(fam["missing"]),
                  "complete": fam["complete"],
                  "manifest_complete": fam["manifest_complete"],
                  "current_context_complete": fam["current_context_complete"],
                  "fdr_decision_ready": fam["fdr_decision_ready"],
                  "n_without_statistic": fam["n_without_statistic"],
                  "prior_context_pass": len(fam["prior_context_pass"]),
                  "prior_context_only": len(fam["prior_context_only"]),
                  "decision": None, "resolved": [], "waiting": []}
        if not fam["fdr_decision_ready"]:
            return report
        try:
            decision = self.fdr_store.record(decide_family(fam))
        except FdrDecisionConflict as e:
            report["decision"] = None
            report["conflict"] = str(e)
            report["fdr_decision_ready"] = False
            return report
        entry = self.locked_test_eligible(decision)
        self._campaign, retired = self._legacy_campaign(decision, entry["eligible"])
        report["campaign"] = ({"campaign_id": self._campaign["campaign_id"],
                               "windows": self._campaign["windows"]} if self._campaign else None)
        report["campaign_window_retired"] = retired
        report["decision"] = {"survivors": decision["survivors"], "m": decision["m"],
                              "n_with_statistic": decision["n_with_statistic"],
                              "statistical_survivors": decision["survivors"],
                              "survivors_validation_pass": decision.get("survivors_validation_pass"),
                              "locked_test_eligible": entry["eligible"],
                              "lock_blocked": entry["blocked"]}
        by_eid = {r.get("evaluation_id"): r for r in records}
        survivors = set(decision["survivors"])
        for sid, st in sorted(states.items()):
            if st != CandidateState.AWAITING_FDR.value or sid not in self._defn_by_sid:
                continue
            dev_rec = by_eid.get(decision["first_records"].get(sid))
            if dev_rec is None:
                report["waiting"].append(sid)   # waiting under another context
                continue
            if sid in retired:
                report["waiting"].append(sid)   # locked window retired: needs new locked data
                continue
            out = self._resolve(sid, dev_rec, sid in survivors, decision)
            (report["resolved"] if out else report["waiting"]).append(
                {"strategy_id": sid, "status": out} if out else sid)
        self._close_legacy_campaign()
        return report

    # -- group-level locked-window control (campaigns.py) ----------------------
    def _legacy_campaign(self, decision: Mapping, eligible: Sequence[str]) -> tuple[Optional[dict], dict]:
        """The locked-TEST entrants of one BH decision form ONE campaign,
        preregistered (members + alpha) before any locked data is read.
        Members whose window is retired are held back (reported)."""
        if not eligible:
            return None, {}
        cfg = _locked_campaign_cfg()
        base = decision["input_hash"]
        k = 0
        while True:
            found = self.campaigns.find("legacy_fdr", f"{base}:{k}")
            if found is None:
                break
            if not found["aborted"]:
                return found["preregistered"], {}
            k += 1
        members, retired = {}, {}
        for sid in eligible:
            syms = list(self._defn_by_sid[sid]["symbols"])
            bad = [s for s in syms
                   if self.campaigns.window_status(s, max_campaigns=cfg["max_campaigns_per_window"])["retired"]]
            if bad:
                retired[sid] = f"WINDOW_RETIRED {bad}"
            else:
                members[sid] = syms
        if not members:
            return None, retired
        camp = self.campaigns.preregister(
            source="legacy_fdr", source_ref=f"{base}:{k}", context_id=self._ctx, members=members,
            alpha_total=_window_alpha(cfg), max_campaigns=cfg["max_campaigns_per_window"],
            method="legacy cell test (HAC t, n>=30, mean>0) AND p <= campaign member alpha",
            config_hash=cfg["config_hash"])
        return camp, retired

    def _close_legacy_campaign(self) -> None:
        camp, self._campaign = self._campaign, None
        if camp is None:
            return
        done = {}
        exposed = False
        for sid, syms in camp["members"].items():
            for sym in syms:
                r = self.lock_registry.result(sid, sym)
                if r and r.get("campaign_id") == camp["campaign_id"]:
                    done.setdefault(sid, {})[sym] = r
                if any(a.get("exposed") and a.get("context_id") == camp["context_id"]
                       for a in self.lock_registry.attempts(sid, sym)):
                    exposed = True
        if done and len(done) == len(camp["members"]):
            self.campaigns.record_result(camp, done)
        elif not exposed:
            self.campaigns.abort_no_exposure(camp, "no member reached locked-data exposure "
                                                   "(data missing / in progress elsewhere)")

    def lock_entry_status(self, sid: str, defn: Mapping) -> dict:
        """May a NEW locked-TEST attempt start for this candidate?  Independent of the
        statistics: a window consumed earlier (any context, any symbol of the
        candidate) can never be entered again.  Read-only.

        Checked first, before any window is looked up (ADR 0009/0010):
        ``COST_NOT_VERIFIED`` — the cost model does not cover these symbols and
        this order size, so the window is preserved and the candidate waits;
        ``NOT_INDEPENDENT`` — another symbol's selection data covers this
        symbol's locked window, so no independent locked TEST exists."""
        required = list(defn["symbols"])
        cost_ok, cost_why = self.cost.verified_for(required, self.order_value)
        if not cost_ok:
            return {"eligible": False, "reason": "COST_NOT_VERIFIED", "per_symbol": {},
                    "no_locked_test": [], "cost": cost_why}
        overlap = self._lock_independence(required)
        if overlap:
            return {"eligible": False, "reason": "NOT_INDEPENDENT", "per_symbol": {},
                    "no_locked_test": [], "overlap": overlap}
        per = {sym: self.lock_registry.entry_status(sid, sym) for sym in required}
        no_lock = symbols_without_locked_test(required)
        if no_lock:
            reason = "NO_LOCKED_TEST"
        elif any(v["reason"] == "ALREADY_CONSUMED" for v in per.values()):
            reason = "ALREADY_CONSUMED"
        elif any(v["reason"] == "IN_PROGRESS" for v in per.values()):
            reason = "IN_PROGRESS"
        else:
            reason = "AVAILABLE"
        return {"eligible": reason == "AVAILABLE", "reason": reason, "per_symbol": per,
                "no_locked_test": list(no_lock)}

    def locked_test_eligible(self, decision: Mapping) -> dict:
        """Split the statistical survivors (BH + validation PASS) from the candidates
        that may actually enter the locked TEST now."""
        eligible: list[str] = []
        blocked: dict = {}
        for sid in decision.get("survivors_validation_pass") or []:
            defn = self._defn_by_sid.get(sid)
            if defn is None:
                blocked[sid] = "NOT_IN_GENERATION"
                continue
            e = self.lock_entry_status(sid, defn)
            if e["eligible"]:
                eligible.append(sid)
            else:
                blocked[sid] = e["reason"]
        return {"eligible": sorted(eligible), "blocked": dict(sorted(blocked.items()))}

    def _resolve(self, sid: str, dev_rec: Mapping, survived: bool, decision: Mapping) -> Optional[str]:
        defn = self._defn_by_sid[sid]
        required = list(defn["symbols"])
        ctx = self._ctx
        lock_info: dict = {"windows": {s: locked_window_for(s) for s in required},
                           "consumed": False}
        promotion_check = None
        if not survived:
            final = "FAIL_STAT"
            lock_info["note"] = (f"did NOT survive the generation BH-FDR "
                                 f"(m={decision['m']}) — locked TEST not consumed")
        elif dev_rec.get("validation_verdict") != "PASS":
            vv = dev_rec.get("validation_verdict")
            final = vv if vv in ("FAIL_STAT", "FAIL_COST", "UNDETERMINED_POWER",
                                 "DATA_MISSING", "ERROR") else "FAIL_STAT"
            lock_info["note"] = "independent validation not passed — locked TEST not consumed"
        else:
            entry = self.lock_entry_status(sid, defn)
            lock_info["entry"] = entry
            if entry["reason"] in ("IN_PROGRESS", "COST_NOT_VERIFIED"):
                # another live process holds the window, or the cost model does
                # not cover this use yet (ADR 0010): wait, window untouched
                return None
            if entry["reason"] == "NOT_INDEPENDENT":
                final = "DATA_MISSING"
                lock_info["note"] = ("LOCKED_WINDOW_NOT_INDEPENDENT: "
                                     + "; ".join(entry["overlap"])
                                     + " — cannot claim final; nothing reserved or read")
            elif entry["reason"] == "ALREADY_CONSUMED":
                final = "FAIL_STAT"
                lock_info["reused"] = True
                lock_info["note"] = ("locked window already consumed earlier "
                                     f"({ {s: v['uses'] for s, v in entry['per_symbol'].items()} }) — "
                                     "not an independent test; nothing reserved")
            elif entry["reason"] == "NO_LOCKED_TEST":
                final = "DATA_MISSING"
                lock_info["note"] = (f"NO_LOCKED_TEST for {entry['no_locked_test']} — "
                                     "cannot claim final; nothing consumed")
            else:
                out = self._locked_test(sid, defn, ctx)
                if out is None:
                    return None      # another live process holds the window
                final, lock_info = out
        rec = {
            "phase": "final", "status": final, "strategy_id": sid,
            "evaluation_id": evaluation_id(sid, ctx), "run_id": self.run_id,
            "generation_id": self.generation_id, "context_id": ctx,
            "dev_evaluation_id": dev_rec.get("evaluation_id"),
            "multiple_testing": {"method": decision["method"], "q": decision["q"],
                                 "m": decision["m"], "survived": survived},
            "validation_verdict": dev_rec.get("validation_verdict"),
            "locked_test": lock_info,
            "cost_model": dev_rec.get("cost_model"),
        }
        if final == "PASS":
            promotion_check = self._promote(sid, defn, dev_rec, decision)
            rec["claim_scope"] = ("VERIFIED_COST" if self.cost.verified
                                  else self.cost.status)
        rec["promotion_check"] = promotion_check
        self.ledger.append(rec)
        self.store.set(sid, CandidateState(final), data_version=ctx)
        if final in ("DATA_MISSING",) and not str(lock_info.get("note", "")).startswith(
                "LOCKED_WINDOW_NOT_INDEPENDENT"):
            # no data refresh can make an overlapping window independent
            self._maybe_enqueue_reverify(sid, final)
        return final

    def _locked_test(self, sid: str, defn: Mapping, ctx: str) -> Optional[tuple[str, dict]]:
        required = list(defn["symbols"])
        fam = defn["family"]
        horizon = defn_horizon(defn)
        info: dict = {"windows": {s: locked_window_for(s) for s in required},
                      "consumed": False}
        no_lock = symbols_without_locked_test(required)
        if no_lock:
            info["note"] = f"NO_LOCKED_TEST for {no_lock} — cannot claim final; nothing consumed"
            return "DATA_MISSING", info
        # ADR 0010: never reserve or read a locked window on a cost model that does
        # not cover this use, or when the window is not independent of selection
        if not self.cost.verified_for(required, self.order_value)[0]:
            return None
        if self._lock_independence(required):
            info["note"] = "LOCKED_WINDOW_NOT_INDEPENDENT — nothing reserved or read"
            return "DATA_MISSING", info
        tokens: dict = {}
        for sym in required:
            r = self.lock_registry.reserve(sid, sym, ctx)
            if not r["ok"]:
                for t in tokens.values():
                    self.lock_registry.fail(t, f"sibling reservation refused ({r['reason']})")
                if r["reason"] == "IN_PROGRESS":
                    return None
                info["note"] = (f"locked window of {sym} already used {r['uses']}x — "
                                "not an independent test")
                info["reused"] = True
                return "FAIL_STAT", info
            tokens[sym] = r["token"]
        # load + check EVERY symbol's locked data before anything is exposed,
        # so a missing ETH window can never burn the BTC window
        loaded: dict = {}
        blocked: list[str] = []
        for sym in required:
            lo, hi = locked_window_for(sym)
            lh = merged_bars(self.paths.data_dir, sym, lo, hi)
            if not lh.bars:
                blocked.append(f"{sym}: locked klines missing/rejected {lh.missing}")
                continue
            # A-31: the locked window needs its OWN funding load
            lf, lfrep = load_funding_window_checked(self.paths.data_dir, sym, lo, hi)
            lfq = checked_funding_quality(lf, lfrep)
            if not lfq["ok"]:
                blocked.append(f"{sym}: locked-window funding {lfq['issues']}")
                continue
            loaded[sym] = (lh, lf)
        if blocked:
            for t in tokens.values():
                self.lock_registry.block(t, "; ".join(blocked))
            info["blocked"] = blocked
            info["note"] = "locked data unavailable — BLOCKED_DATA_MISSING, window NOT consumed"
            return "DATA_MISSING", info
        fng = self._fng() if "fng" in defn_requirements(defn) else None
        results: dict = {}
        try:
            for sym in required:
                self.lock_registry.start(tokens[sym])
            info["consumed"] = True
            for sym in required:
                lh, lf = loaded[sym]
                auxl = build_aux(lh.bars, defn, lf, fng=fng)
                sigl = build_signal_fn(defn, auxl)
                esl = run_event_study(lh.bars, sym, sigl, horizon, self.cost,
                                      self.order_value, lf)
                cl = cell_stats(esl.events, sym, f"{fam}|lock")
                from campaigns import member_alpha
                camp = self._campaign
                a = member_alpha(camp, sym) if camp and sid in camp["members"] else None
                p_ok = a is None or (isinstance(cl.get("p"), float) and cl["p"] <= a)
                res = {"status": cl["status"], "n": cl["n"], "mean_bp": cl.get("mean_bp"),
                       "p": cl.get("p"), "pass": cl["status"] == "PASS" and p_ok,
                       "campaign_id": camp["campaign_id"] if a is not None else None,
                       "campaign_alpha_member": a,
                       "lock_data_sha256": lh.sha256}
                self.lock_registry.complete(tokens[sym], res)
                results[sym] = res
        except Exception as e:
            for sym, t in tokens.items():
                if sym not in results:
                    try:
                        self.lock_registry.fail(t, f"{type(e).__name__}: {e}")
                    except LockRegistryError:
                        pass
            raise
        info["results"] = results
        info["pass"] = {s: r["pass"] for s, r in results.items()}
        info["independent"] = all(self.lock_registry.uses(sid, s) == 1 for s in required)
        sts = [r["status"] for r in results.values()]
        if all(r["pass"] for r in results.values()) and info["independent"]:
            return "PASS", info
        if "ERROR" in sts:
            return "ERROR", info
        if any(r["status"] == "PASS" and not r["pass"] for r in results.values()):
            info["note"] = "cell significant at 0.05 but not at the campaign member alpha"
            return "FAIL_STAT", info
        if "FAIL_STAT" in sts or not info["independent"]:
            return "FAIL_STAT", info
        info["note"] = "locked window consumed but under-powered — cannot be re-tested independently"
        return "UNDETERMINED_POWER", info

    def _promote(self, sid: str, defn: Mapping, dev_rec: Mapping, decision: Mapping) -> dict:
        required = list(defn["symbols"])
        lock_results = [self.lock_registry.result(sid, s) for s in required]
        lock_passed = (all(bool(r and r.get("pass")) for r in lock_results)
                       if all(r is not None for r in lock_results) else None)
        kw = dict(
            state="PASS",
            preregistered=self.prereg.is_preregistered(sid, self.generation_id),
            fdr_survived=sid in set(decision["survivors"]),
            independent_validation_passed=dev_rec.get("validation_verdict") == "PASS",
            lock_test_passed=lock_passed,
            lock_test_used=max((self.lock_registry.uses(sid, s) for s in required), default=0),
            required_symbols=required,
            cost_verified=bool(self.cost.verified),
        )
        if is_combo(defn):
            chk = combo_promotion_gate(portfolio_result=dev_rec.get("portfolio"),
                                       context_id=self._ctx, **kw)
        else:
            chk = promotion_gate(**kw)
        chk.strategy_id = sid
        if chk.eligible:
            if self.promo.duplicates(sid) == 0:
                self.promo.register(chk, sid, self._ctx, test_only=self.cost.test_only)
            else:
                chk.notes.append("already in the promotion queue — not registered twice")
        return chk.to_json()

    # -- re-evaluation scheduling --------------------------------------------
    def schedule_context_reevaluation(self) -> list[str]:
        """Candidates whose LAST evaluation ran under a different experiment
        context are re-verified under the current one (new data / cost /
        code / config = a new experiment).  Same-context retries stay under
        the normal reverify policy."""
        self._ensure_registered()
        ctx = self._ctx
        latest = self.ledger.latest_by_strategy()
        out: list[str] = []
        for sid, _ in self._cands:
            st = CandidateState(self.store.entry(sid).state)
            if st in (CandidateState.PENDING, CandidateState.RUNNING,
                      CandidateState.REVERIFY_PENDING, CandidateState.PASS):
                continue
            last = latest.get(sid)
            if last is None or last.get("context_id") == ctx:
                continue
            if self.queue.enqueue(sid, "context_changed"):
                out.append(sid)
        return out

    def _current_last_ts(self, symbol: str) -> int:
        """Cheap freshness probe: last bar ts of the newest MONTHLY kline file
        (daily files are ignored).  Used only to detect data growth (A-20)."""
        import re
        kdir = self.paths.data_dir / "klines"
        pat = re.compile(rf"^{re.escape(symbol)}-15m-\d{{4}}-\d{{2}}\.csv$")
        files = [p for p in kdir.glob(f"{symbol}-15m-*.csv") if pat.match(p.name)]
        if not files:
            return 0
        newest = max(files, key=lambda p: p.name)
        last = 0
        with open(newest, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ts = int(line.split(",")[0])
                except ValueError:
                    continue
                if ts > last:
                    last = ts
        return last

    def trigger_data_driven_reverify(self) -> list[str]:
        """A-20 / §3-2: when data extends beyond the dataset a previous
        evaluation used, UNDETERMINED_POWER (power condition) and FAIL_STAT
        (new data beyond dev window) candidates become re-verifiable.
        The ledger stays authoritative — this only schedules queue work.
        """
        triggered: list[str] = []
        latest = self.ledger.latest_by_strategy()
        if not latest:
            return triggered
        cur_ts: dict[str, int] = {}
        for sid, rec in latest.items():
            st = rec.get("status")
            if st not in ("UNDETERMINED_POWER", "FAIL_STAT"):
                continue
            ds = rec.get("dataset") or {}
            for sym, ident in ds.items():
                recorded = int((ident or {}).get("last_ts") or 0)
                if not recorded:
                    continue
                if sym not in cur_ts:
                    cur_ts[sym] = self._current_last_ts(sym)
                cur = cur_ts[sym]
                # A-30: a locked symbol's readable window is CAPPED at the
                # day before its locked TEST starts — raw files beyond that
                # cap can never enter any evaluation and must not count as
                # "new data" (they would cause endless spurious re-verify).
                lock_s = locked_window_for(sym)
                if lock_s:
                    import datetime as _dt
                    cap_ms = int((_dt.datetime.fromisoformat(lock_s[0] + "T00:00:00+00:00")
                                  - _dt.timedelta(days=1)).timestamp() * 1000) + 1
                    if cur > cap_ms:
                        continue
                if cur > recorded:
                    reason = "power_condition_met" if st == "UNDETERMINED_POWER" \
                        else "new_data_beyond_dev_window"
                    if self.queue.enqueue(sid, reason):
                        triggered.append(sid)
                    break
        return triggered

    # -- cycle ---------------------------------------------------------------
    def cycle(self, max_candidates: int = 10) -> CycleSummary:
        s = CycleSummary(status="SUCCESS", started_at=iso_now(), run_id=self.run_id,
                         batch_requested=max_candidates)
        t0 = time.time()
        reg = self.register_all()
        s.generation_id = reg["generation_id"]
        s.total_candidates = reg["registered"]
        s.duplicates_collapsed = reg["duplicates_collapsed"]
        if reg["created"]:
            s.notes.append(f"new search generation {reg['generation_id']} preregistered")
        reconcile(self.store, self.queue, self.ledger)
        recovered = self.lock_registry.recover_stale()
        if recovered:
            s.notes.append(f"lock attempts recovered after dead owners: {recovered}")
        s.context_id = self._current_context()
        resched = self.schedule_context_reevaluation()
        if resched:
            s.notes.append(f"{len(resched)} candidates re-queued: experiment context changed")
        self.trigger_data_driven_reverify()  # A-20: new-data reverify trigger
        done = 0
        verdicts: dict[str, int] = {}
        errors = 0

        def _count(r: dict) -> None:
            nonlocal errors
            if not r.get("skipped"):
                st = r.get("status", "ERROR")
                verdicts[st] = verdicts.get(st, 0) + 1
                if st == "ERROR":
                    errors += 1

        # 1) queue first (re-verifications), then fresh PENDING
        while done < max_candidates:
            if self._interrupted:
                s.status = "INTERRUPTED"
                break
            item = self.queue.pop_ready()
            if item is None:
                break
            sid = item["strategy_id"]
            defn = self._defn_by_sid.get(sid)
            if defn is None:
                errors += 1
                s.notes.append(f"queued {sid} is not in generation {s.generation_id} — "
                               "not evaluated (recorded, not silently dropped)")
                continue
            r = self.run_one(sid, defn)
            done += 1
            _count(r)
        for sid, defn in self._cands:
            if done >= max_candidates:
                break
            if self._interrupted:
                s.status = "INTERRUPTED"
                break
            if CandidateState(self.store.entry(sid).state) is CandidateState.PENDING:
                r = self.run_one(sid, defn)
                done += 1
                _count(r)
        # 1b) prior-context PASS candidates need a current-context dev statistic
        if s.status != "INTERRUPTED":
            defn_by = dict(self._cands)
            for sid in self.prior_pass_needing_reevaluation():
                if done >= max_candidates or self._interrupted:
                    break
                r = self.reevaluate_prior_pass(sid, defn_by[sid])
                done += 1
                _count(r)
        # 2) generation-wide FDR decision + locked tests of survivors
        if s.status != "INTERRUPTED":
            fdr = self.finalize_fdr()
            s.fdr_family = {k: fdr.get(k) for k in (
                "m", "evaluated", "missing", "complete", "manifest_complete",
                "current_context_complete", "fdr_decision_ready", "n_without_statistic",
                "prior_context_pass", "prior_context_only", "decision", "conflict")}
            s.finalized = fdr["resolved"]
            for x in fdr["resolved"]:
                verdicts["final_" + x["status"]] = verdicts.get("final_" + x["status"], 0) + 1
        s.evaluated = done
        s.verdict_counts = verdicts
        s.errors = errors
        s.reverify_queue = self.queue.size()
        from generator import search_space_status
        fng_dir = self.paths.data_dir / "fng"
        fng_available = fng_dir.exists() and any(fng_dir.glob("*.csv"))  # A-19
        space = search_space_status(self.store, self.ledger,
                                    fng_available=fng_available,
                                    funding_available=(self.paths.data_dir / "funding").exists(),
                                    cands=self._cands)
        s.search_space_state = space.state
        if errors and done and errors / max(1, done) >= 0.3:
            s.status = "PARTIAL_FAILURE"
        elif errors or s.reverify_queue:
            if s.status != "INTERRUPTED":
                s.status = "SUCCESS_WITH_WARNINGS"
        s.runtime_seconds = time.time() - t0
        s.finished_at = iso_now()
        atomic_write_json(self.paths.results_dir / "cycle_summary.json", s.to_json())
        return s


def win_for(h: DatasetHandle, horizon: int) -> tuple[int, int]:
    w = split_windows(h.symbol, h.first_ts, h.last_ts, horizon, [b.ts for b in h.bars])
    return w["dev"]


class PrefixShim:
    """Lightweight prefix view reusing evaluator.PrefixView semantics."""

    __slots__ = ("_v",)

    def __init__(self, bars, end):
        from evaluator import PrefixView
        self._v = PrefixView(bars, end)

    def __len__(self):
        return len(self._v)

    def __getitem__(self, i):
        return self._v[i]


def _locked_campaign_cfg() -> dict:
    """Locked-window campaign settings shared with the evolution pipeline
    (``locked`` section of the current promotion config; the per-window alpha
    comes from ``pipeline.window_alpha_total`` — promo-v3 splits one search-wide
    budget over the windows)."""
    from pipeline import load_promotion_config
    cfg, h = load_promotion_config()
    return {**cfg["locked"], "config_hash": h}


def _window_alpha(cfg: Mapping) -> float:
    from pipeline import window_alpha_total
    return window_alpha_total(cfg)


def _agg_symbol_status(cells) -> str:
    if any(c.status == "ERROR" for c in cells):
        return "ERROR"
    if any(c.status == "DATA_MISSING" for c in cells):
        return "DATA_MISSING"
    if any(c.status == "UNDETERMINED_POWER" for c in cells):
        return "UNDETERMINED_POWER"
    if any(c.status == "FAIL_COST" for c in cells):
        return "FAIL_COST"
    return cells[0].status if cells else "DATA_MISSING"


def find_defn(sid: str):
    for s, d in enumerate_candidates()[0]:
        if s == sid:
            return d
    return None


class UsageError(ValueError):
    pass


MODES = ("legacy", "evolve", "full")


def parse_options(argv: Sequence[str]) -> dict:
    """``--batch N`` (default 10), ``--mode legacy|evolve|full`` (default
    legacy), ``--rounds N`` and ``--evals N`` (evolution budgets per cycle).
    Anything else is a usage error — never silently ignored."""
    args = list(argv)
    opts = {"batch": 10, "mode": "legacy", "rounds": 2, "evals": 60, "selection": "pareto_niche"}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dev-only":
            # evolve only: seeds + search rounds + DEV evaluations, no validation /
            # robustness / locked / paper stage (no confirmatory window is looked at)
            opts["dev_only"] = True
            i += 1
            continue
        key = a[2:].split("=", 1)[0] if a.startswith("--") else None
        if key not in opts:
            raise UsageError(f"unknown argument {a!r}")
        if "=" in a:
            val = a.split("=", 1)[1]
            i += 1
        else:
            if i + 1 >= len(args):
                raise UsageError(f"--{key} needs a value")
            val = args[i + 1]
            i += 2
        if key == "selection":
            from search import SELECTIONS
            if val not in SELECTIONS:
                raise UsageError(f"--selection must be one of {SELECTIONS}, got {val!r}")
            opts["selection"] = val
            continue
        if key == "mode":
            if val not in MODES:
                raise UsageError(f"--mode must be one of {MODES}, got {val!r}")
            opts["mode"] = val
            continue
        if not val.isdigit() or int(val) < (0 if key == "rounds" else 1):
            raise UsageError(f"--{key} must be a positive integer, got {val!r}")
        opts[key] = int(val)
    if opts.get("dev_only") and opts["mode"] != "evolve":
        raise UsageError("--dev-only needs --mode evolve")
    if opts["selection"] != "pareto_niche" and not opts.get("dev_only"):
        # a new parent selection is a DEV experiment until compared (ADR 0019)
        raise UsageError("--selection map_elites needs --mode evolve --dev-only")
    return opts


def parse_args(argv: Sequence[str]) -> int:
    return parse_options(argv)["batch"]


EXIT_CODES = {"SUCCESS": 0, "SUCCESS_WITH_WARNINGS": 0, "PARTIAL_FAILURE": 2,
              "FAILED": 1, "INTERRUPTED": 1}
#: 4 = CYCLE_BLOCKED_DATA (evolution mode: no usable market data, nothing evaluated)
EXIT_BLOCKED_DATA = 4


def cost_from_env() -> CostContext:
    """``NEW2_COST_PROFILE``: unset/``new2_default`` → NEW2 defaults (UNVERIFIED);
    ``assumed_public_v1`` → public fee schedule + slippage margin (ASSUMED);
    ``public_estimate:<record.json>`` → spread/impact estimated from public
    order books (ESTIMATED_FROM_PUBLIC_DATA).  None is verified; only a sealed
    calibration record is (ADR 0006/0007)."""
    name = os.environ.get("NEW2_COST_PROFILE", "").strip()
    if name in ("", "new2_default"):
        return CostContext.futures_default()
    if name in ASSUMED_PROFILES:
        return CostContext.from_assumed_profile(name)
    if name.startswith("public_estimate:"):
        path = Path(name.split(":", 1)[1])
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            raise CostConfigError(f"public estimate {path} unreadable: {e}") from e
        return CostContext.from_public_estimate(rec)
    raise CostConfigError(f"NEW2_COST_PROFILE={name!r} unknown (new2_default | "
                          + " | ".join(ASSUMED_PROFILES) + " | public_estimate:<record.json>)")


USAGE = """usage: runner.py [--batch N] [--mode legacy|evolve|full] [--rounds R] [--evals E] [--dev-only]
                 [--selection pareto_niche|map_elites]
  --mode legacy  (default) v2 candidate engine: generation BH-FDR, state/state.json,
                 state/discovery_ledger.jsonl, results/cycle_summary.json
  --mode evolve  evolution engine: StepM validation batches + locked campaigns,
                 state/evolution/*, results/evolution_cycle_summary.json
  --mode full    legacy then evolve in one cycle
  --dev-only     (evolve) DEV evaluation and search only: no validation window is looked at
  --selection    (evolve --dev-only) parent selection: pareto_niche (default) or map_elites
                 (one champion per direction x holding band x signal group cell, ADR 0019)
Both share the single-use locked-window records (state/lock_test_usage.json,
state/locked_window_campaigns.jsonl).  A cycle status is not a strategy verdict."""


def main(argv: Sequence[str]) -> int:
    if list(argv[:1]) in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    try:
        opts = parse_options(argv)
    except UsageError as e:
        print(f"USAGE_ERROR: {e}", file=sys.stderr)
        return 1
    try:
        paths = PathConfig.from_env()
    except PathConfigError as e:
        print(f"CONFIG_ERROR: {e}", file=sys.stderr)
        return 1
    if os.environ.get("REFRESH") == "1":
        # §9-1 (R-4): the flag is explicitly NOT supported — say so, loudly,
        # instead of silently pretending data was refreshed.
        print("WARNING: REFRESH=1 is NOT supported by the v2 discovery engine. "
              "Data updates are performed by run/download_missing_data.py; "
              "every evaluation records data staleness honestly.", file=sys.stderr)
    try:
        cost = cost_from_env()
    except CostConfigError as e:
        print(f"CONFIG_ERROR: {e}", file=sys.stderr)
        return 1
    lock = ProcessLock(paths.state_dir / "run.lock")
    if not lock.acquire():
        print(f"CYCLE_SKIPPED lock held ({paths.state_dir / 'run.lock'})")
        return 3
    rc = 0
    try:
        print(f"RUN_MODE {opts['mode']} state_dir={paths.state_dir}", file=sys.stderr)
        if opts["mode"] in ("legacy", "full"):
            r = Runner(paths, cost=cost)
            s = r.cycle(max_candidates=opts["batch"])
            if os.environ.get("REFRESH") == "1":
                s.notes.append("REFRESH=1 requested but NOT supported — no data was refreshed")
            print(json.dumps(s.to_json(), default=str))
            rc = EXIT_CODES[s.status]
        if opts["mode"] in ("evolve", "full"):
            from autoloop import CYCLE_EXIT, run_evolution_cycle
            from paper_forward import FileFeed
            from pipeline import DEFAULT_VALIDATION_HISTORY, RunnerDataProvider
            # real windows are shared by every state directory: alpha spending
            # counts the batches recorded in the repository history (ADR 0013)
            vh = Path(os.environ.get("NEW2_VALIDATION_HISTORY") or DEFAULT_VALIDATION_HISTORY)
            if not vh.exists():
                raise PathConfigError(f"validation window history {vh} missing — refusing to "
                                      "restart alpha spending at k = 1")
            print(f"VALIDATION_HISTORY {vh}", file=sys.stderr)
            # SIGTERM / SIGINT stop the evolution cycle where it is: the cycle wrapper
            # records CYCLE_FAILED (never a lingering CYCLE_RUNNING), the lock is
            # released below and the next run resumes from the ledgers (§7)
            old = _install_interrupt_handlers()
            try:
                from search import SearchConfig
                es = run_evolution_cycle(paths.state_dir, RunnerDataProvider(paths.data_dir),
                                         cost, rounds=opts["rounds"],
                                         search_cfg=SearchConfig(selection=opts["selection"]),
                                         max_dev_evals=opts["evals"], results_dir=paths.results_dir,
                                         forward_feed=FileFeed(paths.data_dir), validation_history=vh,
                                         confirmatory=not opts.get("dev_only"))
            finally:
                _restore_handlers(old)
            print(json.dumps(es.to_json(), default=str))
            erc = CYCLE_EXIT[es.status]
            rc = erc if rc == 0 else (rc if erc == 0 else max(rc, erc))
    except CycleInterrupted as e:
        print(json.dumps({"status": "FAILED", "fatal": f"interrupted by {e}"}))
        return 1
    except Exception as e:  # noqa: BLE001 — a crash is FAILED, never success
        print(json.dumps({"status": "FAILED", "fatal": f"{type(e).__name__}: {e}"}))
        return 1
    finally:
        lock.release()
    return rc


class CycleInterrupted(BaseException):
    """SIGTERM / SIGINT during an evolution cycle (BaseException: no
    ``except Exception`` on the way can swallow it)."""


def _raise_interrupt(signum, _frame):  # pragma: no cover - exercised by a subprocess test
    raise CycleInterrupted(signal.Signals(signum).name)


def _install_interrupt_handlers() -> dict:
    old = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            old[sig] = signal.signal(sig, _raise_interrupt)
        except (ValueError, OSError):      # not the main thread
            pass
    return old


def _restore_handlers(old: dict) -> None:
    for sig, h in old.items():
        try:
            signal.signal(sig, h)
        except (ValueError, OSError):
            pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
