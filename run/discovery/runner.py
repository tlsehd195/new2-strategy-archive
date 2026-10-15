"""Cycle runner — evaluation flow, recovery, summaries (directive §3, §10, §13).

Path configuration (§13-경로): every path comes from an environment variable
or the module directory.  A missing REQUIRED path fails loudly — the runner
never quietly reads old data from another location.
"""

from __future__ import annotations

import hashlib
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
    load_funding_csv,
    funding_quality,
    staleness,
)
from engine_lib import (  # noqa: E402
    CODE_VERSION,
    VALIDATION_CFG_VERSION,
    CandidateState,
    LockTestRegistry,
    LockedWindowError,
    TRANSIENT_ERROR_CODES,
    assert_no_locked_overlap,
    atomic_write_json,
    evaluation_id,
    experiment_context_id,
    iso_now,
    locked_window_for,
    symbols_without_locked_test,
)
from costs_bridge import CostContext, UNVERIFIED_COST_MODEL  # noqa: E402
from evaluator import cell_stats, run_event_study  # noqa: E402
from generator import FAMILY_DATA_REQUIREMENTS, SYMBOLS, enumerate_candidates  # noqa: E402
from portfolio import PortfolioConfig, simulate_portfolio  # noqa: E402
from store import CandidateStore, Ledger, ReverifyQueue, reconcile  # noqa: E402
from promotion import PromotionQueue, combo_promotion_gate, promotion_gate  # noqa: E402


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


def load_funding_window(data_dir: Path, symbol: str, start_iso: str, end_iso: str) -> list[tuple[int, float]]:
    fdir = data_dir / "funding"
    if not fdir.exists():
        return []
    import datetime as dt
    d0 = dt.date.fromisoformat(start_iso).replace(day=1)
    d1 = dt.date.fromisoformat(end_iso)
    out: list[tuple[int, float]] = []
    cur = d0
    while cur <= d1:
        p = fdir / f"{symbol}-fundingRate-{cur:%Y-%m}.csv"
        if p.exists():
            out.extend(load_funding_csv(p))
        cur = (cur.replace(day=28) + dt.timedelta(days=6)).replace(day=1)
    out.sort(key=lambda x: x[0])
    return out


# ---------------------------------------------------------------------------
# windows with embargo (§6)
# ---------------------------------------------------------------------------

VALIDATION_MONTHS = 6


def split_windows(symbol: str, first_ts: int, last_ts: int, horizon: int) -> dict:
    """dev / independent-validation / locked-TEST index windows.

    - symbols without a locked window get NO_LOCKED_TEST (final claims are
      impossible; directive §6)
    - an embargo of `horizon` bars separates dev from validation so no
      holding period straddles the boundary
    """
    import datetime as dt
    lock = locked_window_for(symbol)
    ms = 15 * 60 * 1000
    bars_per_day = 96
    embargo = horizon
    last_dt = dt.datetime.fromtimestamp(last_ts / 1000, tz=dt.timezone.utc).date()
    first_dt = dt.datetime.fromtimestamp(first_ts / 1000, tz=dt.timezone.utc).date()
    if lock:
        lock_start = dt.date.fromisoformat(lock[0])
        val_start = lock_start - dt.timedelta(days=30 * VALIDATION_MONTHS)
    else:
        val_start = last_dt - dt.timedelta(days=30 * VALIDATION_MONTHS)
    dev_end = val_start - dt.timedelta(days=1)
    idx_val_start = int((dt.datetime(val_start.year, val_start.month, val_start.day)
                         - dt.datetime(first_dt.year, first_dt.month, first_dt.day)
                         ).total_seconds() * 1000) // ms
    # A-27 fix: data may start AFTER the validation window begins (short
    # history) — clamp instead of letting a negative index wrap around
    idx_val_start = max(0, idx_val_start)
    idx_dev_end = max(1, idx_val_start - embargo)
    n_bars = (last_ts - first_ts) // ms + 1
    return {
        "dev": (0, max(2, idx_dev_end)),
        "validation": (idx_val_start, n_bars - 1 - embargo),
        "locked": lock,
        "no_locked_test": lock is None,
        "embargo_bars": embargo,
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
    fam = defn["family"]
    # canonical definitions drop default-valued params — merge the family
    # defaults back so a candidate that equals its defaults still resolves
    from generator import FAMILY_DEFAULTS
    p = {**FAMILY_DEFAULTS.get(fam, {}), **(defn.get("params") or {})}

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


def build_aux(bars: Sequence[Bar], defn: Mapping,
              funding: Optional[Sequence[tuple[int, float]]],
              fng: Optional[Mapping] = None) -> dict:
    """Per-bar auxiliary series computed from PAST data only.

    Funding information at bar i uses settlements with calc_time <= the bar's
    close time — a decision at the close of bar i can only know funding that
    has already been published (§9-4 발표 시점 구분).

    FNG (A-18): per-bar ``fng_hist[i]`` holds the last 10 CONFIRMED daily
    values as of bar i — values of days strictly BEFORE the bar's own UTC
    day.  Today's value can never leak into a decision at bar i.
    """
    n = len(bars)
    closes = [b.close for b in bars]
    taker_share = [None] * n
    for i, b in enumerate(bars):
        if b.volume > 0:
            taker_share[i] = b.taker_buy_volume / b.volume
    p = defn["params"]
    lb = p.get("lookback") or p.get("z_window") or p.get("vol_window") or 32
    lb_returns: list[Optional[float]] = [None] * n
    for i in range(lb, n):
        if closes[i - lb] > 0:
            lb_returns[i] = closes[i] / closes[i - lb] - 1.0
    aux: dict = {"closes": closes, "taker_share": taker_share, "lb_returns": lb_returns}
    if funding is not None:
        horizon_ms = 15 * 60 * 1000
        rates: list[Optional[float]] = [None] * n
        j = 0
        for i, b in enumerate(bars):
            close_ts = b.ts + horizon_ms
            while j < len(funding) and funding[j][0] <= close_ts:
                j += 1
            rates[i] = funding[j - 1][1] if j > 0 else None
        zwin = p.get("funding_window") or 90
        fz: list[Optional[float]] = [None] * n
        for i in range(n):
            fz[i] = _rolling_z(rates, i, zwin)
        aux["funding_z"] = fz
    if fng is not None:
        day_ms = 86_400_000
        k_max = 10  # generator caps confirm_days at 3; headroom stays bounded
        fng_hist: list = [None] * n
        for i, b in enumerate(bars):
            d = b.ts // day_ms
            fng_hist[i] = [fng.get(d - k) for k in range(1, k_max + 1)]
        aux["fng_hist"] = fng_hist
    return aux


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------

ORDER_VALUE_DEFAULT = 10_000.0


@dataclass
class CycleSummary:
    status: str  # SUCCESS | SUCCESS_WITH_WARNINGS | PARTIAL_FAILURE | FAILED | INTERRUPTED
    started_at: str = ""
    finished_at: str = ""
    total_candidates: int = 0
    evaluated: int = 0
    verdict_counts: dict = field(default_factory=dict)
    errors: int = 0
    reverify_queue: int = 0
    duplicates_collapsed: int = 0
    runtime_seconds: float = 0.0
    fatal: Optional[str] = None
    search_space_state: str = ""

    def to_json(self) -> dict:
        return dict(self.__dict__)


class Runner:
    def __init__(self, paths: PathConfig, *, order_value: float = ORDER_VALUE_DEFAULT):
        self.paths = paths
        self.order_value = order_value
        self.cost = CostContext.futures_default()
        self.store = CandidateStore(paths.state_dir / "state.json")
        self.queue = ReverifyQueue(paths.state_dir / "queue.json", self.store)
        self.ledger = Ledger(paths.state_dir / "discovery_ledger.jsonl")
        self.lock_registry = LockTestRegistry(paths.state_dir / "lock_test_usage.json")
        self.promo = PromotionQueue(paths.state_dir / "promotion_queue.jsonl")
        self._interrupted = False
        self._fng_cache: Optional[dict] = None
        for _sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(_sig, self._on_signal)
            except (ValueError, OSError):
                pass

    def _on_signal(self, *a) -> None:  # pragma: no cover
        self._interrupted = True

    # -- single candidate --------------------------------------------------
    def register_all(self) -> dict:
        cands, dedup = enumerate_candidates()
        known = self.store.all_states()
        for sid, _ in cands:
            if sid not in known:
                self.store.set(sid, CandidateState.PENDING)
        return {"registered": len(cands), "duplicates_collapsed": len(dedup)}

    def run_one(self, sid: str, defn: Mapping) -> dict:
        cur = self.store.entry(sid)
        if CandidateState(cur.state) is not CandidateState.PENDING:
            return {"skipped": True, "state": cur.state}
        self.store.set(sid, CandidateState.RUNNING)
        started = time.time()
        ev_id = evaluation_id(sid, "pending-context")
        try:
            rec = self._evaluate(sid, defn)
        except LockedWindowError as e:
            # selection must never read the locked window — hard stop
            rec = {"status": "ERROR", "error_code": "CONFIG_ERROR",
                   "error_message": str(e)}
        except FileNotFoundError as e:
            rec = {"status": "DATA_MISSING", "missing": [str(e)]}
        except LookupError as e:
            rec = {"status": "DATA_MISSING", "missing": [str(e)]}
        except Exception as e:  # noqa: BLE001 — classified below
            code = "LOGIC_ERROR"
            rec = {"status": "ERROR", "error_code": code,
                   "error_message": f"{type(e).__name__}: {e}"}
        rec["evaluation_id"] = ev_id
        rec["strategy_id"] = sid
        rec["runtime_seconds"] = time.time() - started
        status = rec["status"]
        self.ledger.append(rec)
        nxt = CandidateState(status)
        if status == "PASS":
            self.store.set(sid, CandidateState.PASS)
        elif status in ("FAIL_COST", "FAIL_STAT", "UNDETERMINED_POWER", "DATA_MISSING"):
            self.store.set(sid, nxt,
                           error_code=rec.get("error_code"),
                           error_message=rec.get("error_message"),
                           data_version=rec.get("context_id"))
            self._maybe_enqueue_reverify(sid, status)
        else:
            e_code = rec.get("error_code", "LOGIC_ERROR")
            self.store.set(sid, CandidateState.ERROR,
                           error_code=e_code, error_message=rec.get("error_message"))
            self._maybe_enqueue_reverify(sid, "ERROR", error_code=e_code)
        return {"skipped": False, "status": status, "record": rec}

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
            self.queue.enqueue(sid, reason)
            q = self.queue
            q.requeue_with_backoff(sid, backoff_seconds(e.retry.attempt_count + 1))

    def _evaluate(self, sid: str, defn: Mapping) -> dict:
        fam = defn["family"]
        required = list(defn["symbols"])
        horizon = int(defn["holding"])
        needs = FAMILY_DATA_REQUIREMENTS[fam]
        # 1) data — per-symbol read window is fixed BEFORE any bars are loaded
        #    so locked-TEST data is never even read into memory (A-16, §6).
        #    A-29: each symbol gets its OWN read_end from its own locked
        #    window — a later lock on one symbol must not truncate another's
        #    independent-validation data.
        import datetime as dt
        handles: dict[str, DatasetHandle] = {}
        fundings: dict[str, list] = {}
        missing: list[str] = []
        for sym in required:
            lock_s = locked_window_for(sym)
            sym_read_end = (dt.date.fromisoformat(lock_s[0]) - dt.timedelta(days=1)).isoformat() \
                if lock_s else "2026-12-31"
            h = merged_bars(self.paths.data_dir, sym, "2021-01-01", sym_read_end)
            if not h.bars:
                missing.extend(h.missing or [f"{sym}: no data"])
                continue
            if lock_s:
                first_d = dt.datetime.fromtimestamp(
                    h.first_ts / 1000, tz=dt.timezone.utc).date().isoformat()
                assert_no_locked_overlap(sym, first_d, sym_read_end,
                                         "dev/validation selection")
            handles[sym] = h
            # A-17: holding-period funding is part of the accounting for EVERY
            # family (futures position ⇒ funding applies).  Unavailable or
            # corrupt funding is fail-closed, never replaced with a guess.
            first_d = dt.datetime.fromtimestamp(
                h.first_ts / 1000, tz=dt.timezone.utc).date().isoformat()
            f = load_funding_window(self.paths.data_dir, sym, first_d, sym_read_end)
            fq = funding_quality(f)
            if not fq["ok"]:
                missing.append(f"{sym}: funding unavailable/quality {fq['issues']}")
            else:
                fundings[sym] = f
        if missing:
            return {"status": "DATA_MISSING", "missing": missing}
        if "fng" in needs:
            if self._fng_cache is None:
                self._fng_cache = load_fng_series(self.paths.data_dir)
            if self._fng_cache is None:
                return {"status": "DATA_MISSING",
                        "missing": ["FNG dataset not present in this environment"]}
        # 2) context
        ds_ids = "+".join(sorted(h.dataset_id for h in handles.values()))
        sha = hashlib.sha256("".join(sorted(h.sha256 for h in handles.values())).encode()).hexdigest()
        ctx = experiment_context_id(dataset_id=ds_ids, data_sha256=sha,
                                    cost_model=self.cost.version)
        # staleness note (never blocks a dev evaluation, but is recorded)
        last_bars = max(h.last_ts for h in handles.values())
        st = staleness(last_bars, int(time.time() * 1000))
        # 3) event studies on dev + validation windows per symbol
        results_dev: dict[str, list] = {}
        results_val: dict[str, list] = {}
        val_missing: list[str] = []
        for sym, h in handles.items():
            aux = build_aux(h.bars, defn, fundings.get(sym), fng=self._fng_cache)
            sig_fn = build_signal_fn(defn, aux)
            win = split_windows(sym, h.first_ts, h.last_ts, horizon)
            d0, d1 = win["dev"]
            sub = h.bars[:d1 + 1]
            es = run_event_study(sub, sym, sig_fn, horizon, self.cost,
                                 self.order_value, fundings.get(sym))
            cs = cell_stats(es.events, sym, f"{fam}|dev", )
            results_dev[sym] = cs
            v0, v1 = win["validation"]
            if v1 > v0 + horizon and 0 <= v0 < len(h.bars) and v1 < len(h.bars):
                subv = h.bars[v0:v1 + 1]
                auxv = build_aux(subv, defn, fundings.get(sym), fng=self._fng_cache)
                sigv = build_signal_fn(defn, auxv)
                esv = run_event_study(subv, sym, sigv, horizon, self.cost,
                                      self.order_value, fundings.get(sym))
                results_val[sym] = cell_stats(esv.events, sym, f"{fam}|val")
            else:
                val_missing.append(sym)
        # 4) dev verdict (multi-symbol rules, §8)
        from engine_lib import CellResult, SymbolResult, verdict_from_cells
        cells = [CellResult(symbol=r["symbol"], cell_id=r["cell_id"], n=r["n"],
                            mean_bp=r["mean_bp"], t=r["t"], p=r["p"],
                            status=r["status"]) for r in results_dev.values()]
        symres = [SymbolResult(symbol=c.symbol, cells=[c],
                               status=_agg_symbol_status([c])) for c in cells]
        dev_verdict, dev_detail = verdict_from_cells(symres, required)
        # 5) independent validation verdict
        if val_missing:
            val_verdict = "DATA_MISSING"
            val_detail = {"missing_symbols": val_missing}
        else:
            vcells = [CellResult(symbol=r["symbol"], cell_id=r["cell_id"], n=r["n"],
                                 mean_bp=r["mean_bp"], t=r["t"], p=r["p"],
                                 status=r["status"]) for r in results_val.values()]
            vsymres = [SymbolResult(symbol=c.symbol, cells=[c],
                                    status=_agg_symbol_status([c])) for c in vcells]
            val_verdict, val_detail = verdict_from_cells(vsymres, required)
        final = dev_verdict
        lock_info: dict = {"windows": {s: locked_window_for(s) for s in required}}
        if dev_verdict == "PASS":
            if val_verdict != "PASS":
                final = val_verdict if val_verdict in ("FAIL_STAT", "FAIL_COST",
                                                       "UNDETERMINED_POWER",
                                                       "DATA_MISSING") else "FAIL_STAT"
            else:
                # 6) locked TEST, once per strategy+symbol
                lock_pass: dict[str, Optional[bool]] = {}
                independent = True
                for sym in required:
                    if locked_window_for(sym) is None:
                        lock_pass[sym] = None
                        continue
                    use = self.lock_registry.record_use(sid, sym, ctx)
                    independent = independent and use["independent"]
                    lh = merged_bars(self.paths.data_dir, sym, locked_window_for(sym)[0],
                                     locked_window_for(sym)[1])
                    if not lh.bars:
                        lock_pass[sym] = None
                        continue
                    auxl = build_aux(lh.bars, defn, fundings.get(sym), fng=self._fng_cache)
                    sigl = build_signal_fn(defn, auxl)
                    esl = run_event_study(lh.bars, sym, sigl, horizon, self.cost,
                                          self.order_value, fundings.get(sym))
                    cl = cell_stats(esl.events, sym, f"{fam}|lock")
                    lock_pass[sym] = cl["status"] == "PASS"
                lock_info.update({"pass": lock_pass, "independent": independent})
                vals = [v for v in lock_pass.values() if v is not None]
                if any(v is None for v in lock_pass.values()):
                    final = "DATA_MISSING" if not vals else (
                        "PASS" if all(v for v in vals) else "FAIL_STAT")
                    if any(v is None for v in lock_pass.values()):
                        lock_info["note"] = "NO_LOCKED_TEST for some symbols — not final"
                elif not vals:
                    final = "DATA_MISSING"
                    lock_info["note"] = "NO_LOCKED_TEST — cannot claim final"
                elif all(vals) and independent:
                    final = "PASS"
                elif not all(vals):
                    final = "FAIL_STAT"
                else:
                    final = "FAIL_STAT"
                    lock_info["note"] = "locked window re-used — not independent"
        # 7) portfolio check for dev-passing candidates (survivor track)
        portfolio: Optional[dict] = None
        if dev_verdict == "PASS":
            grid_bars = {s: handles[s].bars[:win_for(handles[s], horizon)[1] + 1]
                         for s in handles}
            sigs: list[dict] = []
            for sym, h in handles.items():
                aux = build_aux(h.bars, defn, fundings.get(sym), fng=self._fng_cache)
                sig_fn = build_signal_fn(defn, aux)
                for i in range(1, len(grid_bars[sym]) - horizon - 1):
                    side = sig_fn(PrefixShim(grid_bars[sym], i + 1))
                    if side:
                        sigs.append({"symbol": sym, "decision_index": i,
                                     "side": side, "horizon": horizon})
            pf = simulate_portfolio(grid_bars, sigs,
                                    {s: fundings.get(s, []) for s in grid_bars},
                                    self.cost,
                                    PortfolioConfig(initial_capital=self.order_value))
            portfolio = pf.to_json()
            portfolio["passed"] = (pf.final_equity > pf.initial_capital
                                   and pf.max_drawdown < 0.5
                                   and pf.liquidation_events == 0)
        rec = {
            "status": final,
            "context_id": ctx,
            "dataset": {s: handles[s].identity() for s in handles},
            "cost_model": {"version": self.cost.version,
                           "status": UNVERIFIED_COST_MODEL if not self.cost.verified else "VERIFIED",
                           "params": self.cost.model.__dict__},
            "code_version": CODE_VERSION,
            "validation_cfg_version": VALIDATION_CFG_VERSION,
            "dev_verdict": dev_verdict,
            "dev_detail": dev_detail,
            "validation_verdict": val_verdict,
            "validation_detail": val_detail,
            "cells_dev": [r for r in results_dev.values()],
            "cells_val": [r for r in results_val.values()],
            "locked_test": lock_info,
            "portfolio": portfolio,
            "staleness": st,
        }
        return rec

    # -- cycle ---------------------------------------------------------------
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

    def cycle(self, max_candidates: int = 10) -> CycleSummary:
        s = CycleSummary(status="SUCCESS", started_at=iso_now())
        t0 = time.time()
        reg = self.register_all()
        s.total_candidates = reg["registered"]
        s.duplicates_collapsed = reg["duplicates_collapsed"]
        rec = reconcile(self.store, self.queue, self.ledger)
        self.trigger_data_driven_reverify()  # A-20: new-data reverify trigger
        # 1) queue first (re-verifications), then fresh PENDING
        done = 0
        verdicts: dict[str, int] = {}
        errors = 0
        while done < max_candidates:
            if self._interrupted:
                s.status = "INTERRUPTED"
                break
            item = self.queue.pop_ready()
            if item is not None:
                sid = item["strategy_id"]
                defn = find_defn(sid)
                if defn is None:
                    errors += 1
                    continue
                r = self.run_one(sid, defn)
                done += 1
                if not r.get("skipped"):
                    st = r.get("status", "ERROR")
                    verdicts[st] = verdicts.get(st, 0) + 1
                    if st == "ERROR":
                        errors += 1
            else:
                break
        # fresh candidates
        for sid, defn in enumerate_candidates()[0]:
            if done >= max_candidates:
                break
            if self._interrupted:
                s.status = "INTERRUPTED"
                break
            if CandidateState(self.store.entry(sid).state) is CandidateState.PENDING:
                r = self.run_one(sid, defn)
                done += 1
                if not r.get("skipped"):
                    st = r.get("status", "ERROR")
                    verdicts[st] = verdicts.get(st, 0) + 1
                    if st == "ERROR":
                        errors += 1
        s.evaluated = done
        s.verdict_counts = verdicts
        s.errors = errors
        s.reverify_queue = self.queue.size()
        from generator import search_space_status
        fng_dir = self.paths.data_dir / "fng"
        fng_available = fng_dir.exists() and any(fng_dir.glob("*.csv"))  # A-19
        space = search_space_status(self.store, self.ledger,
                                    fng_available=fng_available,
                                    funding_available=(self.paths.data_dir / "funding").exists())
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
    w = split_windows(h.symbol, h.first_ts, h.last_ts, horizon)
    return w["dev"]


class PrefixShim:
    """Lightweight prefix view reusing evaluator.PrefixView semantics."""

    __slots__ = ("_bars", "_end")

    def __init__(self, bars, end):
        from evaluator import PrefixView
        self._v = PrefixView(bars, end)

    def __len__(self):
        return len(self._v)

    def __getitem__(self, i):
        return self._v[i]


def _agg_symbol_status(cells) -> str:
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


def main(argv: Sequence[str]) -> int:
    args = list(argv)
    batch = 10
    if "--batch" in args:
        batch = int(args[args.index("--batch") + 1])
    try:
        paths = PathConfig.from_env()
    except PathConfigError as e:
        print(f"CONFIG_ERROR: {e}", file=sys.stderr)
        return 1
    r = Runner(paths)
    s = r.cycle(max_candidates=batch)
    print(s.to_json())
    return {"SUCCESS": 0, "SUCCESS_WITH_WARNINGS": 0, "PARTIAL_FAILURE": 2,
            "FAILED": 1, "INTERRUPTED": 1}[s.status]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
