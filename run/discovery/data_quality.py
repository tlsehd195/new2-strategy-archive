"""Data quality checks and dataset versioning (directive §9)."""

from __future__ import annotations

import hashlib
import math
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

MAX_MS_GAP_15M = 15 * 60 * 1000


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
        return cls(
            ts=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            quote_volume=float(row[7]),
            taker_buy_volume=float(row[9]) if len(row) > 9 and row[9] not in ("", "0") else 0.0,
        )


@dataclass
class QualityReport:
    symbol: str
    n_rows: int = 0
    duplicate_timestamps: int = 0
    time_inversions: int = 0
    missing_candles: int = 0          # gaps in the 15m grid (excluding known halts)
    bad_ohlc: int = 0
    negative_values: int = 0
    extreme_moves: int = 0            # |log return| > 20% in one 15m bar
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    latest_candle_ts: Optional[int] = None
    file_sha256: str = ""
    errors: list[str] = field(default_factory=list)
    unrecoverable: bool = False

    def to_json(self) -> dict:
        return dict(self.__dict__)

    @property
    def ok(self) -> bool:
        return (
            not self.unrecoverable
            and self.duplicate_timestamps == 0
            and self.time_inversions == 0
            and self.bad_ohlc == 0
            and self.negative_values == 0
            and self.missing_candles == 0
        )


def check_bars(bars: Sequence[Bar], symbol: str, *,
               expected_interval_ms: int = MAX_MS_GAP_15M,
               max_gap_multiplier: int = 2,
               max_abs_logret: float = 0.20) -> QualityReport:
    """Fail-closed quality gate.  Missing candles are counted, never
    interpolated — a gap is a fact about the data, not noise to paint over
    (directive §9-2: 임의 보간 금지)."""
    rep = QualityReport(symbol=symbol)
    rep.n_rows = len(bars)
    if rep.n_rows == 0:
        rep.unrecoverable = True
        rep.errors.append("no rows")
        return rep

    seen: set[int] = set()
    prev: Optional[Bar] = None
    for b in bars:
        if b.ts in seen:
            rep.duplicate_timestamps += 1
        seen.add(b.ts)
        if prev is not None:
            if b.ts < prev.ts:
                rep.time_inversions += 1
            gap = b.ts - prev.ts
            if gap > expected_interval_ms * max_gap_multiplier:
                rep.missing_candles += int(round(gap / expected_interval_ms)) - 1
        if not (b.low <= b.high and b.low <= max(b.open, b.close)
                and b.high >= min(b.open, b.close) and b.low <= b.open <= b.high
                and b.low <= b.close <= b.high):
            rep.bad_ohlc += 1
        if b.open <= 0 or b.high <= 0 or b.low <= 0 or b.close <= 0 \
                or b.volume < 0 or b.quote_volume < 0:
            rep.negative_values += 1
        if prev is not None and prev.close > 0 and b.close > 0:
            if abs(math.log(b.close / prev.close)) > max_abs_logret:
                rep.extreme_moves += 1
        prev = b
    rep.first_ts = bars[0].ts
    rep.last_ts = bars[-1].ts
    rep.latest_candle_ts = max(b.ts for b in bars)
    # a single unusual print is recorded, not fatal; systematic corruption is
    if rep.extreme_moves > max(5, rep.n_rows * 0.001):
        rep.errors.append(f"suspicious number of extreme moves: {rep.extreme_moves}")
    if rep.bad_ohlc > 0 or rep.negative_values > 0 or rep.time_inversions > 0 \
            or rep.duplicate_timestamps > 0:
        rep.unrecoverable = True
        rep.errors.append("structural corruption — file must be re-downloaded")
    return rep


def load_kline_csv(path: Path, symbol: str) -> tuple[list[Bar], QualityReport]:
    raw = Path(path).read_bytes()
    rep_file_sha = hashlib.sha256(raw).hexdigest()
    bars: list[Bar] = []
    torn_tail = False
    for i, line in enumerate(raw.decode("utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < 8:
            torn_tail = i == len(raw.decode("utf-8", errors="replace").splitlines()) - 1
            continue
        try:
            bars.append(Bar.from_row(parts))
        except (ValueError, IndexError):
            continue
    rep = check_bars(bars, symbol)
    rep.file_sha256 = rep_file_sha
    if torn_tail:
        rep.errors.append("torn trailing line (crash during write?)")
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
        }


def dataset_for(symbol: str, kline_path: Path, *, required: bool = True) -> Optional[DatasetHandle]:
    if not Path(kline_path).exists():
        if required:
            raise FileNotFoundError(f"required dataset missing: {kline_path}")
        return None
    bars, rep = load_kline_csv(kline_path, symbol)
    if not rep.ok:
        # fail-closed: corrupted data is never silently used
        h = DatasetHandle(
            dataset_id=f"{symbol}-15m-REJECTED",
            symbol=symbol, bars=[], sha256=rep.file_sha256,
            first_ts=0, last_ts=0, quality=rep,
            missing=[f"quality gate failed: {rep.errors}"],
        )
        return h
    return DatasetHandle(
        dataset_id=f"{symbol}-15m-{rep.file_sha256[:12]}",
        symbol=symbol, bars=bars, sha256=rep.file_sha256,
        first_ts=rep.first_ts or 0, last_ts=rep.last_ts or 0, quality=rep,
    )


def load_funding_csv(path: Path) -> list[tuple[int, float]]:
    """Return [(calc_time_ms, rate)] sorted by time; corrupt lines skipped
    and counted by the caller through len comparison."""
    out: list[tuple[int, float]] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split(",")
        if len(parts) < 3:
            continue
        try:
            out.append((int(parts[0]), float(parts[2])))
        except ValueError:
            continue
    out.sort(key=lambda x: x[0])
    return out


def funding_quality(pairs: Sequence[tuple[int, float]]) -> dict:
    issues: list[str] = []
    if not pairs:
        return {"ok": False, "issues": ["no funding rows"]}
    ts = [t for t, _ in pairs]
    dup = len(ts) - len(set(ts))
    if dup:
        issues.append(f"{dup} duplicate funding timestamps")
    inv = sum(1 for a, b in zip(ts, ts[1:]) if b < a)
    if inv:
        issues.append(f"{inv} time inversions")
    gaps: list[int] = []
    for a, b in zip(ts, ts[1:]):
        gap_h = (b - a) / 3_600_000
        if gap_h > 9.0:  # nominal 8h cadence
            gaps.append(int(round(gap_h / 8)))
    return {
        "ok": not issues,
        "issues": issues,
        "n": len(pairs),
        "first": ts[0],
        "last": ts[-1],
        "missing_periods_estimate": len(gaps),
    }


def staleness(bars_last_ts: int, now_ms: int, *, max_age_bars: int = 8) -> dict:
    """Data freshness: an evaluation on stale data must say so."""
    age_bars = (now_ms - bars_last_ts) / MAX_MS_GAP_15M
    return {"age_bars": age_bars, "stale": age_bars > max_age_bars}
