"""Data quality checks and dataset versioning (directive §9, auto-evolution §2-1).

One quality verdict for every input file: OK / WARN / BLOCK.  A BLOCK verdict
means the data never reaches a backtest — the dataset handle carries no bars
(``DatasetHandle.bars == []``) and the caller records DATA_MISSING.  Warnings
are recorded with the evaluation but do not stop it.  There is no third path:
an issue is either blocking or a recorded warning, never only a log line.

Issue codes (``QualityReport.issues``):

======================  ========  ===========================================
code                    severity  meaning
======================  ========  ===========================================
NO_ROWS                 BLOCK     file has no parsable bar
PARSE_ERROR             BLOCK     an interior row could not be parsed (corruption)
TORN_TAIL               BLOCK     the LAST line is incomplete (interrupted write);
                                  distinct code: re-download, not a data fact
DUPLICATE_TS            BLOCK     repeated open time
TIME_INVERSION          BLOCK     open time goes backwards
MISALIGNED_TS           BLOCK     open time not on the 15m grid / gap not a
                                  multiple of the interval
MISSING_BARS            BLOCK     gap of >= 1 bar not covered by a documented
                                  exchange exception
KNOWN_EXCHANGE_GAP      WARN      gap fully inside a documented exchange halt
NON_FINITE              BLOCK     NaN / inf in a price or volume field
BAD_OHLC                BLOCK     low/high/open/close relation violated
NON_POSITIVE_PRICE      BLOCK     price <= 0
NEGATIVE_VOLUME         BLOCK     volume / quote volume / taker volume < 0
TAKER_GT_VOLUME         BLOCK     taker-buy volume above total volume
ZERO_VOLUME             WARN      bars without trades (counted, legitimate on
                                  quiet venues but recorded)
EXTREME_MOVES           WARN/BLK  single prints are WARN; systematic jumps BLOCK
STALE                   WARN      last bar older than the freshness limit (only
                                  when a reference time is given)
HEADER                  (info)    a recognised header line — not an error
======================  ========  ===========================================

Known exchange exceptions live in ``data_exceptions.json`` (versioned, part
of the experiment context through its hash).  Gaps are only excused when a
documented record covers EVERY missing bar; nothing is guessed.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

MAX_MS_GAP_15M = 15 * 60 * 1000
INTERVAL_15M_MS = MAX_MS_GAP_15M
QUALITY_POLICY_VERSION = "dq-v2"

BLOCK = "BLOCK"
WARN = "WARN"
OK = "OK"

#: kline CSV header tokens (Binance vision files are sometimes headerless,
#: newer ones carry this header)
_KLINE_HEADER_FIRST = {"open_time", "opentime", "open time", "timestamp", "ts"}
_FUNDING_HEADER_FIRST = {"calc_time", "calctime", "funding_time", "fundingtime"}

EXCEPTIONS_PATH = Path(__file__).resolve().parent / "data_exceptions.json"


def load_known_exceptions(path: Optional[Path] = None) -> list[dict]:
    """Documented exchange data exceptions.  A missing file means no
    exceptions (fail-closed: every gap blocks)."""
    p = Path(path) if path else EXCEPTIONS_PATH
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for e in raw.get("exceptions", []):
        if not {"symbol", "start_ts", "end_ts", "kind", "source"} <= set(e):
            raise ValueError(f"data exception entry lacks required fields: {e}")
        out.append(dict(e))
    return out


def exceptions_identity(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else EXCEPTIONS_PATH
    data = p.read_bytes() if p.exists() else b""
    return {"file": p.name, "sha256": hashlib.sha256(data).hexdigest(),
            "n": len(load_known_exceptions(p))}


@dataclass
class Bar:
    ts: int          # open time, ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    taker_buy_volume: float = 0.0   # Binance vision col 9 (taker buy base)

    @classmethod
    def from_row(cls, row: Sequence[str]) -> "Bar":
        ts = int(row[0])
        tb = row[9].strip() if len(row) > 9 else ""
        return cls(
            ts=ts,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            quote_volume=float(row[7]),
            taker_buy_volume=float(tb) if tb not in ("",) else 0.0,
        )


@dataclass
class QualityReport:
    symbol: str
    n_rows: int = 0
    duplicate_timestamps: int = 0
    time_inversions: int = 0
    missing_candles: int = 0          # bars missing from the 15m grid (unexcused)
    known_gap_bars: int = 0           # bars missing inside documented exceptions
    misaligned_timestamps: int = 0
    bad_ohlc: int = 0
    negative_values: int = 0          # rows with non-positive price or negative volume
    non_positive_price: int = 0
    negative_volume: int = 0
    non_finite: int = 0
    taker_gt_volume: int = 0
    zero_volume: int = 0
    extreme_moves: int = 0            # |log return| > 20% in one 15m bar
    parse_errors: int = 0             # interior rows that failed to parse
    header_rows: int = 0
    torn_tail: bool = False
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    latest_candle_ts: Optional[int] = None
    file_sha256: str = ""
    errors: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    unrecoverable: bool = False
    policy_version: str = QUALITY_POLICY_VERSION

    def add(self, code: str, severity: str, detail: str = "", **extra) -> None:
        self.issues.append({"code": code, "severity": severity, "detail": detail, **extra})
        if severity == BLOCK:
            self.errors.append(f"{code}: {detail}" if detail else code)

    def to_json(self) -> dict:
        d = dict(self.__dict__)
        d["verdict"] = self.verdict
        return d

    @property
    def verdict(self) -> str:
        if self.unrecoverable or any(i["severity"] == BLOCK for i in self.issues):
            return BLOCK
        if any(i["severity"] == WARN for i in self.issues):
            return WARN
        return OK

    @property
    def ok(self) -> bool:
        """True iff the data may be used (verdict OK or WARN)."""
        return self.verdict != BLOCK

    def blocking_codes(self) -> list[str]:
        return sorted({i["code"] for i in self.issues if i["severity"] == BLOCK})


def _finite(*xs: float) -> bool:
    return all(isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


def _excused(symbol: str, gap_start: int, gap_end: int,
             exceptions: Sequence[dict]) -> Optional[dict]:
    """A documented exception that covers EVERY missing bar in
    (gap_start, gap_end), or None."""
    for e in exceptions:
        if e.get("symbol") not in (symbol, "*"):
            continue
        if int(e["start_ts"]) <= gap_start + INTERVAL_15M_MS and gap_end - INTERVAL_15M_MS <= int(e["end_ts"]):
            return e
    return None


def check_bars(bars: Sequence[Bar], symbol: str, *,
               expected_interval_ms: int = MAX_MS_GAP_15M,
               max_gap_multiplier: int = 1,
               max_abs_logret: float = 0.20,
               exceptions: Optional[Sequence[dict]] = None,
               now_ms: Optional[int] = None,
               max_age_bars: Optional[int] = None) -> QualityReport:
    """Fail-closed quality gate over parsed bars.

    Missing candles are counted, never interpolated.  ``max_gap_multiplier``
    is kept for API compatibility: the grid check is exact — consecutive
    open times must differ by exactly one interval; ANY larger multiple is
    ``gap/interval - 1`` missing bars (the old ``gap > 2*interval`` rule let
    a single missing bar, i.e. a 30-minute step, pass unnoticed).
    """
    if max_gap_multiplier != 1:
        raise ValueError("max_gap_multiplier is fixed at 1 (exact grid); "
                         "excuse real exchange gaps via data_exceptions.json")
    exc = load_known_exceptions() if exceptions is None else list(exceptions)
    rep = QualityReport(symbol=symbol)
    rep.n_rows = len(bars)
    if rep.n_rows == 0:
        rep.unrecoverable = True
        rep.add("NO_ROWS", BLOCK, "no rows")
        return rep

    iv = int(expected_interval_ms)
    seen: set[int] = set()
    prev: Optional[Bar] = None
    for b in bars:
        if b.ts in seen:
            rep.duplicate_timestamps += 1
        seen.add(b.ts)
        if b.ts % iv != 0:
            rep.misaligned_timestamps += 1
        vals = (b.open, b.high, b.low, b.close, b.volume, b.quote_volume, b.taker_buy_volume)
        if not _finite(*vals):
            rep.non_finite += 1
        else:
            if not (b.low <= b.high and b.low <= b.open <= b.high
                    and b.low <= b.close <= b.high):
                rep.bad_ohlc += 1
            bad_px = b.open <= 0 or b.high <= 0 or b.low <= 0 or b.close <= 0
            bad_vol = b.volume < 0 or b.quote_volume < 0 or b.taker_buy_volume < 0
            if bad_px:
                rep.non_positive_price += 1
            if bad_vol:
                rep.negative_volume += 1
            if bad_px or bad_vol:
                rep.negative_values += 1
            if b.taker_buy_volume > b.volume * (1 + 1e-9) + 1e-12:
                rep.taker_gt_volume += 1
            if b.volume == 0:
                rep.zero_volume += 1
        if prev is not None:
            gap = b.ts - prev.ts
            if gap < 0:
                rep.time_inversions += 1
            elif gap > iv:
                if gap % iv:
                    rep.misaligned_timestamps += 1
                n_missing = gap // iv - 1
                if n_missing > 0:
                    e = _excused(symbol, prev.ts, b.ts, exc)
                    if e is not None:
                        rep.known_gap_bars += n_missing
                        rep.add("KNOWN_EXCHANGE_GAP", WARN,
                                f"{n_missing} bars after {prev.ts} ({e['kind']}: {e['source']})",
                                start_ts=prev.ts, end_ts=b.ts)
                    else:
                        rep.missing_candles += n_missing
            elif 0 < gap < iv:
                rep.misaligned_timestamps += 1
            if _finite(prev.close, b.close) and prev.close > 0 and b.close > 0:
                if abs(math.log(b.close / prev.close)) > max_abs_logret:
                    rep.extreme_moves += 1
        prev = b
    rep.first_ts = bars[0].ts
    rep.last_ts = bars[-1].ts
    rep.latest_candle_ts = max(b.ts for b in bars)

    if rep.duplicate_timestamps:
        rep.add("DUPLICATE_TS", BLOCK, f"{rep.duplicate_timestamps} duplicate open times")
    if rep.time_inversions:
        rep.add("TIME_INVERSION", BLOCK, f"{rep.time_inversions} time inversions")
    if rep.misaligned_timestamps:
        rep.add("MISALIGNED_TS", BLOCK, f"{rep.misaligned_timestamps} off-grid timestamps")
    if rep.missing_candles:
        rep.add("MISSING_BARS", BLOCK, f"{rep.missing_candles} missing 15m bars")
    if rep.non_finite:
        rep.add("NON_FINITE", BLOCK, f"{rep.non_finite} rows with NaN/inf")
    if rep.bad_ohlc:
        rep.add("BAD_OHLC", BLOCK, f"{rep.bad_ohlc} rows violate low<=open,close<=high")
    if rep.non_positive_price:
        rep.add("NON_POSITIVE_PRICE", BLOCK, f"{rep.non_positive_price} rows with price <= 0")
    if rep.negative_volume:
        rep.add("NEGATIVE_VOLUME", BLOCK, f"{rep.negative_volume} rows with negative volume")
    if rep.taker_gt_volume:
        rep.add("TAKER_GT_VOLUME", BLOCK, f"{rep.taker_gt_volume} rows taker volume > volume")
    if rep.zero_volume:
        rep.add("ZERO_VOLUME", WARN, f"{rep.zero_volume} bars without volume")
    if rep.extreme_moves:
        systematic = rep.extreme_moves > max(5, rep.n_rows * 0.001)
        rep.add("EXTREME_MOVES", BLOCK if systematic else WARN,
                f"{rep.extreme_moves} |logret| > {max_abs_logret}")
    if now_ms is not None and max_age_bars is not None:
        st = staleness(rep.last_ts, now_ms, max_age_bars=max_age_bars)
        if st["stale"]:
            rep.add("STALE", WARN, f"last bar {st['age_bars']:.0f} bars old")
    structural = {"DUPLICATE_TS", "TIME_INVERSION", "MISALIGNED_TS", "NON_FINITE",
                  "BAD_OHLC", "NON_POSITIVE_PRICE", "NEGATIVE_VOLUME", "TAKER_GT_VOLUME"}
    if structural & set(rep.blocking_codes()):
        rep.unrecoverable = True
        rep.errors.append("structural corruption — file must be re-downloaded")
    return rep


def _is_header(first_field: str, tokens: set) -> bool:
    return first_field.strip().strip('"').lower() in tokens


def load_kline_csv(path: Path, symbol: str, *,
                   exceptions: Optional[Sequence[dict]] = None) -> tuple[list[Bar], QualityReport]:
    """Parse a kline CSV.  A header on the FIRST line is recognised; any
    other unparsable row is PARSE_ERROR (interior) or TORN_TAIL (the last
    line, when the file does not end with a newline) — both block, with
    distinct codes so an interrupted write is not mistaken for corrupt data."""
    raw = Path(path).read_bytes()
    file_sha = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\n")
    ends_with_newline = text.endswith("\n")
    if ends_with_newline:
        lines = lines[:-1]
    bars: list[Bar] = []
    parse_errors: list[int] = []
    header_rows = 0
    torn = False
    last_idx = len(lines) - 1
    for i, line in enumerate(lines):
        line = line.strip().rstrip("\r")
        if not line:
            continue
        parts = line.split(",")
        if i == 0 and _is_header(parts[0], _KLINE_HEADER_FIRST):
            header_rows += 1
            continue
        try:
            if len(parts) < 8:
                raise ValueError(f"{len(parts)} columns")
            bars.append(Bar.from_row(parts))
        except (ValueError, IndexError):
            if i == last_idx and not ends_with_newline:
                torn = True
            else:
                parse_errors.append(i + 1)
    rep = check_bars(bars, symbol, exceptions=exceptions)
    rep.file_sha256 = file_sha
    rep.header_rows = header_rows
    rep.parse_errors = len(parse_errors)
    if parse_errors:
        rep.unrecoverable = True
        rep.add("PARSE_ERROR", BLOCK, f"{len(parse_errors)} unparsable interior rows",
                lines=parse_errors[:20])
    if torn:
        rep.torn_tail = True
        rep.add("TORN_TAIL", BLOCK, "incomplete last line (interrupted write) — re-download")
    return bars, rep


@dataclass
class DatasetHandle:
    """Versioned dataset identity for experiment_context_id (§9-3)."""

    dataset_id: str
    symbol: str
    bars: list[Bar]
    sha256: str
    first_ts: int
    last_ts: int
    quality: QualityReport
    missing: list[str] = field(default_factory=list)

    def identity(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "data_sha256": self.sha256,
            "rows": len(self.bars),
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "quality_ok": self.quality.ok,
            "quality_verdict": getattr(self.quality, "verdict", None),
        }


def dataset_for(symbol: str, kline_path: Path, *, required: bool = True) -> Optional[DatasetHandle]:
    if not Path(kline_path).exists():
        if required:
            raise FileNotFoundError(f"required dataset missing: {kline_path}")
        return None
    bars, rep = load_kline_csv(kline_path, symbol)
    if not rep.ok:
        # fail-closed: data with a BLOCK verdict never carries bars
        return DatasetHandle(
            dataset_id=f"{symbol}-15m-REJECTED",
            symbol=symbol, bars=[], sha256=rep.file_sha256,
            first_ts=0, last_ts=0, quality=rep,
            missing=[f"quality gate failed: {rep.blocking_codes()} {rep.errors}"],
        )
    return DatasetHandle(
        dataset_id=f"{symbol}-15m-{rep.file_sha256[:12]}",
        symbol=symbol, bars=bars, sha256=rep.file_sha256,
        first_ts=rep.first_ts or 0, last_ts=rep.last_ts or 0, quality=rep,
    )


# ---------------------------------------------------------------------------
# funding
# ---------------------------------------------------------------------------

FUNDING_NOMINAL_MS = 8 * 3_600_000
FUNDING_TOL_MS = 60_000


#: kinds of a SOURCE order error (ADR 0072).  Recorded in the order the rows
#: appear in the file(s), BEFORE the pairs are sorted for computation: a sort
#: is a normalisation for the calculations, never a repair of the source.
SOURCE_ORDER_KINDS = ("SOURCE_TIME_INVERSION", "SOURCE_DUPLICATE_TS",
                      "CROSS_FILE_TIME_INVERSION", "CROSS_FILE_DUPLICATE_TS")
SOURCE_ORDER_EFFECT = ("FUNDING_DATA_INVALID: funding_quality fails (SOURCE_ORDER_INVALID); the "
                       "series is not handed to the evaluator / portfolio (fail-closed, never 0)")


def source_order_error(kind: str, file: str, line: int, prev_ts: int, ts: int, **extra) -> dict:
    assert kind in SOURCE_ORDER_KINDS
    return {"kind": kind, "file": file, "line": line, "prev_ts": prev_ts, "ts": ts,
            **extra, "effect": SOURCE_ORDER_EFFECT}


def load_funding_csv_checked(path: Path) -> tuple[list[tuple[int, float]], dict]:
    """Parse a funding CSV (calc_time, funding_interval_hours, rate).

    Returns (pairs sorted by time, report).  A header on the first line is
    recognised; every other unparsable row is a parse error (fail-closed in
    :func:`funding_quality`), the torn last line is reported separately.

    ADR 0072: the SOURCE row order is checked while reading, before the sort.
    Each parsed row is compared with the previous parsed row of the file; a
    smaller timestamp is ``SOURCE_TIME_INVERSION``, an equal one
    ``SOURCE_DUPLICATE_TS`` (``source_order_errors``: file, 1-based line,
    previous / current timestamp, kind, effect).  The returned pairs are still
    sorted (the calculations need it), but the errors stay in the report and
    make :func:`funding_quality` fail.  Rows whose interval column is missing
    or not a positive number are listed in ``interval_missing_rows`` (their
    settlement then has no declared interval: FUNDING_SCHEDULE_UNKNOWN for a
    holding window through it, never "assumed 8h")."""
    path = Path(path)
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    ends = text.endswith("\n")
    lines = text.split("\n")
    if ends:
        lines = lines[:-1]
    out: list[tuple[int, float]] = []
    intervals: dict[int, int] = {}
    errors: list[int] = []
    order_errors: list[dict] = []
    interval_missing: list[int] = []
    torn = False
    prev_ts: Optional[int] = None
    first_src: Optional[list] = None
    prev_line: Optional[int] = None
    for i, line in enumerate(lines):
        line = line.strip().rstrip("\r")
        if not line:
            continue
        parts = line.split(",")
        if i == 0 and _is_header(parts[0], _FUNDING_HEADER_FIRST):
            continue
        try:
            if len(parts) < 3:
                raise ValueError("columns")
            t = int(parts[0])
            r = float(parts[2])
            if not math.isfinite(r):
                raise ValueError("non-finite rate")
        except ValueError:
            if i == len(lines) - 1 and not ends:
                torn = True
            else:
                errors.append(i + 1)
            continue
        if prev_ts is not None and t <= prev_ts:
            order_errors.append(source_order_error(
                "SOURCE_TIME_INVERSION" if t < prev_ts else "SOURCE_DUPLICATE_TS",
                path.name, i + 1, prev_ts, t))
        prev_ts, prev_line = t, i + 1
        if first_src is None:
            first_src = [i + 1, t]
        try:
            h = float(parts[1])
            ok_h = math.isfinite(h) and h > 0
        except ValueError:
            ok_h = False
        if ok_h:
            intervals[t] = int(round(h * 3_600_000))
        else:
            interval_missing.append(i + 1)
        out.append((t, r))
    n_source = len(out)
    out.sort(key=lambda x: x[0])
    return out, {"parse_errors": errors, "torn_tail": torn, "intervals": intervals,
                 "sha256": hashlib.sha256(raw).hexdigest(),
                 "source_order_errors": order_errors,
                 "interval_missing_rows": interval_missing,
                 "rows": n_source,
                 # first / last parsed row in SOURCE order ([line, ts]): the cross-file check
                 "first_source_row": first_src,
                 "last_source_row": None if prev_ts is None else [prev_line, prev_ts],
                 "pairs_sorted_for_computation": True}


def funding_file_issues(rep: Mapping) -> list[str]:
    """Integrity problems of ONE funding file report from
    :func:`load_funding_csv_checked` (ADR 0073: one shared definition for
    every caller that has to say whether a file is usable)."""
    out = []
    if rep.get("parse_errors"):
        out.append(f"PARSE_ERROR: {len(rep['parse_errors'])} unparsable rows "
                   f"(lines {list(rep['parse_errors'])[:5]})")
    if rep.get("torn_tail"):
        out.append("TORN_TAIL: incomplete last row")
    for e in rep.get("source_order_errors") or ():
        out.append(f"{e.get('kind')}: line {e.get('line')} ts {e.get('ts')} after {e.get('prev_ts')}")
    return out


def load_funding_files_checked(paths: Sequence[Path]) -> tuple[list[tuple[int, float]], dict]:
    """Funding files merged IN THE ORDER GIVEN (month order), with the ADR 0072
    checks: per file (:func:`load_funding_csv_checked`) and across files (the
    first row of a file must be later than the last row of the previous
    non-empty file: ``CROSS_FILE_TIME_INVERSION`` / ``CROSS_FILE_DUPLICATE_TS``).

    Shared by ``runner.load_funding_window_checked`` (research windows) and
    ``paper_forward.FileFeed`` (ADR 0073), so both use one definition of a
    valid funding file.  ``files`` lists name, sha256 and rows per file
    (unchanged ADR 0072 format); ``file_status`` adds the per-file verdict
    (``VALID`` / ``DATA_INVALID``) with its reasons — a cross-file error marks
    BOTH files.  The returned pairs are sorted for computation; the errors
    stay in the report."""
    rep = {"parse_errors": [], "torn_tail": False, "intervals": {}, "source_order_errors": [],
           "interval_missing_rows": [], "files": [], "file_status": []}
    out: list[tuple[int, float]] = []
    prev_last: Optional[tuple[str, int]] = None       # (file, last source ts) of the previous file
    status_by_name: dict = {}
    for p in paths:
        p = Path(p)
        pairs, r = load_funding_csv_checked(p)
        out.extend(pairs)
        rep["parse_errors"] += [f"{p.name}:{ln}" for ln in r["parse_errors"]]
        rep["torn_tail"] = rep["torn_tail"] or r["torn_tail"]
        rep["intervals"].update(r["intervals"])
        rep["source_order_errors"] += r["source_order_errors"]
        rep["interval_missing_rows"] += [f"{p.name}:{ln}" for ln in r["interval_missing_rows"]]
        rep["files"].append({"name": p.name, "sha256": r["sha256"], "rows": r["rows"]})
        fs = {"name": p.name, "sha256": r["sha256"], "rows": r["rows"],
              "first_ts": r["first_source_row"][1] if r["first_source_row"] else None,
              "last_ts": r["last_source_row"][1] if r["last_source_row"] else None,
              "interval_missing_rows": len(r["interval_missing_rows"]),
              "errors": funding_file_issues(r)}
        status_by_name[p.name] = fs
        rep["file_status"].append(fs)
        if r["first_source_row"] is not None:
            ln0, t0 = r["first_source_row"]
            t_last = r["last_source_row"][1]
            if prev_last is not None and t0 <= prev_last[1]:
                kind = "CROSS_FILE_TIME_INVERSION" if t0 < prev_last[1] else "CROSS_FILE_DUPLICATE_TS"
                rep["source_order_errors"].append(source_order_error(
                    kind, p.name, ln0, prev_last[1], t0, prev_file=prev_last[0]))
                fs["errors"].append(f"{kind}: first row {t0} not after {prev_last[1]} of {prev_last[0]}")
                status_by_name[prev_last[0]]["errors"].append(
                    f"{kind}: last row {prev_last[1]} not before {t0} of {p.name}")
            prev_last = (p.name, t_last)
    for fs in rep["file_status"]:
        fs["status"] = "DATA_INVALID" if fs["errors"] else "VALID"
    out.sort(key=lambda x: x[0])
    return out, rep


def load_funding_csv(path: Path) -> list[tuple[int, float]]:
    """Compatibility wrapper: pairs only.  Callers that gate on quality use
    :func:`load_funding_csv_checked` (parse errors are no longer invisible)."""
    return load_funding_csv_checked(path)[0]


def funding_gaps(pairs: Sequence[tuple[int, float]],
                 intervals: Optional[dict] = None) -> list[tuple[int, int]]:
    """(a, b) pairs of consecutive settlements further apart than the declared
    (or nominal 8h) interval — settlements are missing in between."""
    gaps = []
    intervals = intervals or {}
    for (a, _), (b, _) in zip(pairs, pairs[1:]):
        allowed = intervals.get(b, intervals.get(a, FUNDING_NOMINAL_MS))
        if b - a > allowed + FUNDING_TOL_MS:
            gaps.append((a, b))
    return gaps


def funding_quality(pairs: Sequence[tuple[int, float]], *,
                    parse_errors: Iterable[int] = (), torn_tail: bool = False,
                    intervals: Optional[dict] = None,
                    source_order_errors: Iterable[Mapping] = (),
                    interval_missing_rows: Iterable = ()) -> dict:
    """Dataset-level funding verdict.  ``status``: OK, FUNDING_DATA_MISSING
    (no rows) or FUNDING_DATA_INVALID (any issue).  ``source_order_errors``
    (ADR 0072) are the order errors of the SOURCE rows found before the sort
    (:func:`load_funding_csv_checked`, ``runner.load_funding_window_checked``):
    the sorted ``pairs`` cannot show them any more, so they are passed in and
    every one of them blocks (SOURCE_ORDER_INVALID)."""
    issues: list[str] = []
    warnings: list[str] = []
    soe = [dict(e) for e in source_order_errors]
    if not pairs:
        return {"ok": False, "status": "FUNDING_DATA_MISSING", "issues": ["no funding rows"],
                "warnings": [], "gaps": [], "source_order_errors": soe}
    if soe:
        kinds = sorted({e.get("kind") for e in soe})
        first = soe[0]
        issues.append(f"SOURCE_ORDER_INVALID: {len(soe)} rows out of source order ({', '.join(kinds)}; "
                      f"first {first.get('file')}:{first.get('line')} {first.get('ts')} after "
                      f"{first.get('prev_ts')})")
    imr = list(interval_missing_rows)
    if imr:
        warnings.append(f"{len(imr)} rows without a declared settlement interval "
                        "(FUNDING_SCHEDULE_UNKNOWN for windows through them)")
    ts = [t for t, _ in pairs]
    dup = len(ts) - len(set(ts))
    if dup:
        issues.append(f"{dup} duplicate funding timestamps")
    inv = sum(1 for a, b in zip(ts, ts[1:]) if b < a)
    if inv:
        issues.append(f"{inv} time inversions")
    pe = list(parse_errors)
    if pe:
        issues.append(f"PARSE_ERROR: {len(pe)} unparsable funding rows")
    if torn_tail:
        issues.append("TORN_TAIL: incomplete last funding row")
    if any(not math.isfinite(r) for _, r in pairs):
        issues.append("non-finite funding rate")
    gaps = funding_gaps(pairs, intervals)
    if gaps:
        # not a dataset-wide block: every holding window that spans a gap is
        # dropped by the evaluator (FundingIndex) — never summed short
        warnings.append(f"{len(gaps)} gaps in the settlement series")
    return {
        "ok": not issues,
        "status": "OK" if not issues else "FUNDING_DATA_INVALID",
        "issues": issues,
        "warnings": warnings,
        "source_order_errors": soe,
        "n": len(pairs),
        "first": ts[0],
        "last": ts[-1],
        "missing_periods_estimate": len(gaps),
        "gaps": [list(g) for g in gaps],
    }


def staleness(bars_last_ts: int, now_ms: int, *, max_age_bars: int = 8) -> dict:
    """Data freshness: an evaluation on stale data must say so."""
    age_bars = (now_ms - bars_last_ts) / MAX_MS_GAP_15M
    return {"age_bars": age_bars, "stale": age_bars > max_age_bars}
