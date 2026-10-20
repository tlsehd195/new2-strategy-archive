"""Import the user's own exchange fill export and turn it into cost evidence.

The cost model can only be VERIFIED from real account executions
(``cost_calibration``: ``exchange_fills``).  This module reads a Binance
USDⓈ-M futures trade-history export that the USER downloaded from the
exchange (CSV from the web export, or the JSON list returned by the
``userTrades`` endpoint saved to a file) — it never logs in, never calls a
private API and never sends an order.

    python3 fills_import.py --fills ~/Downloads/trades.csv --out-dir private_fills/run1 \\
        [--klines DATA/klines] [--decisions decisions.jsonl] \\
        [--quotes quotes.jsonl | --public-snapshots DATA/public_market] \\
        [--fee-prices fee_prices.jsonl] [--verify --ledger cost_verifications.jsonl]

What it does, in order (every rule below is a test):

1. **Columns and types.**  The header is mapped through declared aliases
   (``COLUMN_ALIASES``) or an explicit ``--column-map``; a missing required
   column makes the whole file ``DATA_INVALID``.  Required per fill: symbol,
   time, side, price, quantity, order id, trade id, fee, fee asset.  Maker /
   taker is kept when the export has it, else ``UNKNOWN``.
2. **Missing values.**  A row without a required value is quarantined with
   its reason.  A missing fee is NEVER read as 0.
3. **Time.**  Epoch milliseconds, or a date-time string.  A string without
   an explicit offset is read as UTC only when the column says so
   (``Date(UTC)``) or ``--timezone`` is given — never guessed.  Rows are
   sorted by time; the report says whether the input was unsorted.
4. **Duplicates.**  ``(exchange, symbol, trade id)`` identifies a fill.  An
   identical repeat is quarantined as a duplicate; a repeat with different
   content is a conflicting duplicate and makes the file ``DATA_INVALID``.
5. **Abnormal values.**  Non-positive price / quantity, ``quote_qty`` that
   is not price×qty, a price far from the neighbouring fills of the same
   symbol, a price outside the public 15m bar's [low, high] (when
   ``--klines`` is given), a fee above ``max_fee_rate`` of notional, a
   negative fee (rebates are not modelled).
6. **Fee assets.**  A fee paid in the symbol's quote asset is used as is.
   A fee in another asset (e.g. BNB) is converted only with an explicit
   price: ``--fee-prices`` rows ``{asset, ts_ms, price, source}`` (price in
   the quote asset), taking the latest price at or before the fill within
   one 15m bar, or the public ``<ASSET><QUOTE>`` 15m kline OPEN of the bar
   containing the fill (``--klines``).  The method and the price time are
   stored with the fill.  An unconvertible fee keeps ``fee_quote = None``
   and the fill is not cost evidence (counted, never priced at 0).
7. **Orders.**  Fills are grouped by order id: VWAP, filled notional, fee
   sum, first fill time, liquidity (TAKER if any fill was taker).  Slippage
   needs the quote AT DECISION TIME, which a trade export does not contain:
   it comes from the user's decision log (``--decisions``: order_id,
   decision_ts_ms, ref_price or bid/ask, requested_notional, status) or
   from a quote file / public order-book snapshots (best bid/ask at or
   before the first fill, at most ``reference_quote_max_staleness_ms``
   old).  An order without a reference is not an execution observation.
   A trades-only export also cannot show rejected or expired orders, so the
   reject-rate criterion is not observable without the complete order log
   (``--decisions``) — the result is then ``INSUFFICIENT_DATA``, never a
   reject rate of 0.

Privacy: normalized fills and quarantined rows are written only under
``--out-dir`` (default ``private_fills/``, git-ignored); stdout and the
summary carry counts, periods and hashes, never rows.  No API key, secret or
account identifier is read or stored.  Test data is synthetic and is marked
``data_origin = "synthetic_test"``; such data can never yield
``VERIFIED_FROM_ACTUAL_FILLS``.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from engine_lib import append_jsonl, atomic_write_json, canonical_json, iso_now, iter_jsonl

FILLS_SCHEMA = "account_fill_v1"
IMPORT_VERSION = "fills-import-v1"
DEFAULT_POLICY = Path(__file__).resolve().parent / "config" / "fills_import_v1.json"
DEFAULT_OUT = Path(__file__).resolve().parent / "private_fills"
DATA_ORIGINS = ("user_export", "synthetic_test")

#: export column name (lower-cased, spaces/underscores removed) -> field
COLUMN_ALIASES = {
    "symbol": "symbol", "pair": "symbol", "contract": "symbol",
    "time": "time", "dateutc": "time", "timeutc": "time", "date": "time", "datetime": "time",
    "side": "side", "buyer": "buyer",
    "price": "price", "avgprice": "price",
    "qty": "qty", "quantity": "qty", "executed": "qty", "filled": "qty",
    "quoteqty": "quote_qty", "amount": "quote_qty", "total": "quote_qty",
    "orderid": "order_id", "orderno": "order_id",
    "id": "trade_id", "tradeid": "trade_id", "transactionid": "trade_id",
    "commission": "fee", "fee": "fee",
    "commissionasset": "fee_asset", "feecoin": "fee_asset", "feeasset": "fee_asset",
    "maker": "maker", "role": "role", "liquidity": "role",
}
REQUIRED = ("symbol", "time", "side", "price", "qty", "order_id", "trade_id", "fee", "fee_asset")


class FillImportError(ValueError):
    """The file as a whole cannot be imported (DATA_INVALID)."""


def load_import_policy(path: Optional[Path] = None) -> tuple[dict, str]:
    p = Path(path or DEFAULT_POLICY)
    raw = p.read_bytes()
    pol = json.loads(raw)
    if pol.get("schema") != "fills_import_policy_v1":
        raise FillImportError(f"{p}: not a fills_import_policy_v1 file")
    return pol, hashlib.sha256(raw).hexdigest()[:16]


def _norm(name: str) -> str:
    return "".join(ch for ch in name.strip().lower() if ch.isalnum())


def _iso_ms(s: str) -> int:
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


# ---------------------------------------------------------------------------
# 1-3: read and normalise
# ---------------------------------------------------------------------------

def _read_rows(raw: bytes, fmt: str) -> tuple[list[dict], list[str]]:
    if fmt == "binance_api_json":
        data = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise FillImportError("JSON export must be a list of trade objects")
        header = sorted({k for r in data for k in r})
        return [{k: (None if v is None else str(v)) for k, v in r.items()} for r in data], header
    if fmt == "binance_csv":
        text = raw.decode("utf-8-sig")
        rd = csv.DictReader(io.StringIO(text))
        if not rd.fieldnames:
            raise FillImportError("CSV export has no header row")
        return [dict(r) for r in rd], list(rd.fieldnames)
    raise FillImportError(f"unknown format {fmt!r} (binance_csv | binance_api_json)")


def detect_format(path: Path) -> str:
    head = Path(path).read_bytes()[:64].lstrip(b"\xef\xbb\xbf \t\r\n")
    return "binance_api_json" if head.startswith(b"[") else "binance_csv"


def _column_map(header: Sequence[str], override: Optional[Mapping[str, str]]) -> dict:
    """export column -> field.  ``override`` maps field -> export column."""
    out: dict = {}
    if override:
        unknown = set(override) - set(REQUIRED) - {"quote_qty", "maker", "role", "buyer"}
        if unknown:
            raise FillImportError(f"--column-map names unknown fields {sorted(unknown)}")
        missing_cols = [c for c in override.values() if c not in header]
        if missing_cols:
            raise FillImportError(f"--column-map columns not in the file: {missing_cols}")
        out.update({col: f for f, col in override.items()})
    for col in header:
        if col in out:
            continue
        f = COLUMN_ALIASES.get(_norm(col))
        if f and f not in out.values():
            out[col] = f
    have = set(out.values())
    if "side" not in have and "buyer" in have:
        have.add("side")
    missing = [f for f in REQUIRED if f not in have]
    if missing:
        raise FillImportError(f"required columns missing: {missing} (header {list(header)}) — "
                              "a fill without order/trade id or fee is not usable evidence")
    return out


def _dec(v, what: str) -> Decimal:
    if v is None or str(v).strip() == "":
        raise ValueError(f"{what} missing")
    s = str(v).strip().replace(",", "")
    # web exports sometimes append the asset to amounts ("12.5 USDT")
    s = s.split()[0] if " " in s else s
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(f"{what}={v!r} is not a number") from e
    if not d.is_finite():
        raise ValueError(f"{what}={v!r} is not finite")
    return d


def _ts(v, *, column_is_utc: bool, timezone: Optional[str]) -> int:
    if v is None or str(v).strip() == "":
        raise ValueError("time missing")
    s = str(v).strip()
    if s.isdigit():
        n = int(s)
        if n < 10_000_000_000:
            raise ValueError(f"time {s} looks like seconds, not milliseconds")
        return n
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace("/", "-"))
    except ValueError as e:
        raise ValueError(f"time {s!r} is not a date-time") from e
    if t.tzinfo is None:
        if column_is_utc:
            t = t.replace(tzinfo=dt.timezone.utc)
        elif timezone:
            t = t.replace(tzinfo=_tz(timezone))
        else:
            raise ValueError(f"time {s!r} has no offset and the column does not say UTC — "
                             "pass --timezone (never guessed)")
    return int(t.timestamp() * 1000)


def _tz(name: str) -> dt.tzinfo:
    if name.upper() in ("UTC", "Z"):
        return dt.timezone.utc
    if name[:1] in "+-":                      # +09:00
        sign = 1 if name[0] == "+" else -1
        hh, mm = (name[1:].split(":") + ["0"])[:2]
        return dt.timezone(sign * dt.timedelta(hours=int(hh), minutes=int(mm)))
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)


def _quote_asset(symbol: str, quote_assets: Sequence[str]) -> Optional[str]:
    for q in sorted(quote_assets, key=len, reverse=True):
        if symbol.endswith(q) and len(symbol) > len(q):
            return q
    return None


def normalise_rows(rows: Sequence[Mapping], header: Sequence[str], *, policy: Mapping,
                   column_map: Optional[Mapping[str, str]] = None, timezone: Optional[str] = None,
                   exchange: str = "binance", market: str = "usdm_futures",
                   source_sha256: str = "", source_format: str = "") -> tuple[list[dict], list[dict]]:
    cmap = _column_map(header, column_map)
    col_of = {f: c for c, f in cmap.items()}
    time_col = col_of["time"]
    utc_col = "utc" in time_col.lower() or source_format == "binance_api_json"
    now = iso_now()
    out, bad = [], []
    for i, r in enumerate(rows, 1):
        try:
            g = {f: r.get(c) for c, f in cmap.items()}
            sym = str(g.get("symbol") or "").strip().upper()
            if not sym:
                raise ValueError("symbol missing")
            if g.get("side") not in (None, ""):
                side = str(g["side"]).strip().upper()
            elif str(g.get("buyer", "")).strip().lower() in ("true", "false"):
                side = "BUY" if str(g["buyer"]).strip().lower() == "true" else "SELL"
            else:
                raise ValueError("side missing")
            if side not in ("BUY", "SELL"):
                raise ValueError(f"side {side!r} is not BUY/SELL")
            price, qty = _dec(g.get("price"), "price"), _dec(g.get("qty"), "quantity")
            fee = _dec(g.get("fee"), "fee")          # missing -> ValueError, never 0
            fee_asset = str(g.get("fee_asset") or "").strip().upper()
            if not fee_asset:
                raise ValueError("fee asset missing")
            oid, tid = (str(g.get(k) or "").strip() for k in ("order_id", "trade_id"))
            if not oid:
                raise ValueError("order id missing")
            if not tid:
                raise ValueError("trade id missing")
            role = "UNKNOWN"
            mk = str(g.get("maker") or "").strip().lower()
            rl = str(g.get("role") or "").strip().upper()
            if mk in ("true", "false"):
                role = "MAKER" if mk == "true" else "TAKER"
            elif rl in ("MAKER", "TAKER"):
                role = rl
            rec = {"schema": FILLS_SCHEMA, "exchange": exchange, "market": market, "symbol": sym,
                   "ts_ms": _ts(g.get("time"), column_is_utc=utc_col, timezone=timezone),
                   "side": side, "price": str(price), "qty": str(qty),
                   "quote_qty": str(_dec(g["quote_qty"], "quote_qty")) if g.get("quote_qty") not in (None, "") else None,
                   "order_id": oid, "trade_id": tid, "liquidity": role,
                   "fee": str(fee), "fee_asset": fee_asset,
                   "quote_asset": _quote_asset(sym, policy["quote_assets"]),
                   "source_sha256": source_sha256, "source_format": source_format,
                   "source_row": i, "imported_at": now}
            out.append(rec)
        except (ValueError, KeyError) as e:
            bad.append({"row": i, "reason": str(e)})
    return out, bad


# ---------------------------------------------------------------------------
# 4-5: integrity checks
# ---------------------------------------------------------------------------

def _content(rec: Mapping) -> str:
    keep = ("symbol", "ts_ms", "side", "price", "qty", "quote_qty", "order_id", "fee", "fee_asset",
            "liquidity")
    return canonical_json({k: rec.get(k) for k in keep})


def check_fills(fills: Sequence[dict], *, policy: Mapping, klines: Optional[Mapping] = None,
                now_ms: Optional[int] = None) -> tuple[list[dict], list[dict], dict]:
    """Returns (valid fills sorted by time, quarantined [{row, reason}], flags)."""
    now_ms = now_ms if now_ms is not None else int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    lo_ms = _iso_ms(policy["min_fill_ts"])
    bad: list[dict] = []
    flags = {"input_sorted": all(fills[i]["ts_ms"] <= fills[i + 1]["ts_ms"] for i in range(len(fills) - 1)),
             "conflicting_duplicates": 0, "exact_duplicates": 0, "negative_fees": 0}
    seen: dict = {}
    conflicted: set = set()
    keep: list[dict] = []
    for f in fills:
        k = (f["exchange"], f["symbol"], f["trade_id"])
        if k in seen:
            first = seen[k]
            if _content(first) == _content(f):
                flags["exact_duplicates"] += 1
                bad.append({"row": f["source_row"], "reason": f"duplicate of row {first['source_row']} "
                                                             "(same trade id, same content)"})
            else:
                flags["conflicting_duplicates"] += 1
                conflicted.add(k)
                bad.append({"row": f["source_row"], "reason": f"conflicting duplicate of row "
                                                             f"{first['source_row']} (same trade id, different content)"})
            continue
        seen[k] = f
        why = []
        price, qty, fee = Decimal(f["price"]), Decimal(f["qty"]), Decimal(f["fee"])
        if price <= 0:
            why.append(f"price {price} <= 0")
        if qty <= 0:
            why.append(f"quantity {qty} <= 0")
        if not lo_ms <= f["ts_ms"] <= now_ms + policy["max_future_ms"]:
            why.append(f"time {f['ts_ms']} outside [{policy['min_fill_ts']}, now+1d]")
        if fee < 0:
            flags["negative_fees"] += 1
            why.append(f"negative fee {fee} (rebates are not modelled)")
        if f["quote_qty"] is not None and price > 0 and qty > 0:
            qq = Decimal(f["quote_qty"])
            if abs(qq / (price * qty) - 1) > Decimal(str(policy["quote_qty_rel_tol"])):
                why.append(f"quote_qty {qq} != price*qty {price * qty}")
        if f["quote_asset"] is not None and f["fee_asset"] == f["quote_asset"] and price > 0 and qty > 0:
            if fee / (price * qty) > Decimal(str(policy["max_fee_rate"])):
                why.append(f"fee {fee} is more than {policy['max_fee_rate']:.0%} of the notional — unit error?")
        if f["quote_asset"] is None:
            why.append(f"cannot tell the quote asset of {f['symbol']}")
        if why:
            bad.append({"row": f["source_row"], "reason": "; ".join(why)})
            continue
        keep.append(f)
    # the first row of a trade id that has a CONFLICTING repeat is not trustworthy either
    for f in [x for x in keep if (x["exchange"], x["symbol"], x["trade_id"]) in conflicted]:
        keep.remove(f)
        bad.append({"row": f["source_row"], "reason": "has a conflicting duplicate (same trade id)"})
    keep.sort(key=lambda f: (f["ts_ms"], f["symbol"], f["trade_id"]))
    # neighbour price check (per symbol): catches a price in another unit
    by_sym: dict = defaultdict(list)
    for f in keep:
        by_sym[f["symbol"]].append(f)
    drop = set()
    w, mn, dev = policy["neighbour_window"], policy["neighbour_min_count"], policy["max_neighbour_price_dev"]
    for sym, xs in by_sym.items():
        prices = [float(x["price"]) for x in xs]
        for i, x in enumerate(xs):
            nb = prices[max(0, i - w):i] + prices[i + 1:i + 1 + w]
            if len(nb) < mn:
                continue
            med = sorted(nb)[len(nb) // 2]
            if abs(prices[i] / med - 1) > dev:
                drop.add(id(x))
                bad.append({"row": x["source_row"],
                            "reason": f"price {x['price']} is {abs(prices[i] / med - 1):.2%} from the "
                                      f"median of neighbouring {sym} fills ({med}) — unit or symbol error?"})
    if klines:
        tol = policy["kline_price_tol"]
        for f in keep:
            if id(f) in drop:
                continue
            b = bar_at(klines.get(f["symbol"]), f["ts_ms"])
            if b is None:
                continue
            p = float(f["price"])
            if not b.low * (1 - tol) <= p <= b.high * (1 + tol):
                drop.add(id(f))
                bad.append({"row": f["source_row"], "reason": f"price {p} outside the public 15m bar "
                                                              f"[{b.low}, {b.high}] at {b.ts}"})
    keep = [f for f in keep if id(f) not in drop]
    bad.sort(key=lambda b: b["row"])
    return keep, bad, flags


# ---------------------------------------------------------------------------
# market data helpers (public data only)
# ---------------------------------------------------------------------------

BAR_MS = 15 * 60 * 1000


def load_klines(klines_dir: Path, symbols: Iterable[str], ts_list: Iterable[int]) -> dict:
    """Public 15m bars of the months that contain ``ts_list`` (and the month
    before, for the decision bar).  Quality-gated; a failing file is skipped
    and the fills of that month simply have no bar check / bar value."""
    from data_quality import load_kline_csv
    months = set()
    for t in ts_list:
        d = dt.datetime.fromtimestamp(t / 1000, tz=dt.timezone.utc).date().replace(day=1)
        months.add(d)
        months.add((d - dt.timedelta(days=1)).replace(day=1))
    out = {}
    for sym in sorted(set(symbols)):
        bars = []
        for m in sorted(months):
            p = Path(klines_dir) / f"{sym}-15m-{m:%Y-%m}.csv"
            if p.exists():
                bs, rep = load_kline_csv(p, sym)
                if rep.ok:
                    bars += bs
        bars.sort(key=lambda b: b.ts)
        if bars:
            out[sym] = (bars, [b.ts for b in bars])
    return out


def bar_at(entry, ts_ms: int):
    """The 15m bar whose open time <= ts_ms < open + 15m."""
    if not entry:
        return None
    bars, ts = entry
    i = bisect_right(ts, ts_ms) - 1
    if i < 0 or not bars[i].ts <= ts_ms < bars[i].ts + BAR_MS:
        return None
    return bars[i]


def last_closed_bar(entry, ts_ms: int):
    """The last bar that had CLOSED at ``ts_ms`` (the backtest prices an
    entry with the traded value of the bar before the entry bar)."""
    if not entry:
        return None
    bars, ts = entry
    i = bisect_right(ts, ts_ms - BAR_MS) - 1
    return bars[i] if i >= 0 and bars[i].ts + BAR_MS <= ts_ms else None


# ---------------------------------------------------------------------------
# 6: fee conversion
# ---------------------------------------------------------------------------

def load_fee_prices(path: Path) -> dict:
    """{asset: (sorted ts list, rows)} from ``{asset, ts_ms, price, quote, source}`` rows."""
    out: dict = defaultdict(list)
    for r in iter_jsonl(Path(path)):
        for k in ("asset", "ts_ms", "price", "source"):
            if k not in r:
                raise FillImportError(f"fee price row without {k}: keys {sorted(r)}")
        p = float(r["price"])
        if not (math.isfinite(p) and p > 0):
            raise FillImportError(f"fee price {r['price']!r} invalid")
        out[str(r["asset"]).upper()].append(r)
    return {a: ([int(r["ts_ms"]) for r in sorted(rs, key=lambda r: int(r["ts_ms"]))],
                sorted(rs, key=lambda r: int(r["ts_ms"]))) for a, rs in out.items()}


def convert_fees(fills: Sequence[dict], *, policy: Mapping, fee_prices: Optional[Mapping] = None,
                 klines: Optional[Mapping] = None) -> dict:
    """Adds ``fee_quote`` (float or None) and ``fee_conversion`` to each fill."""
    stats: dict = {"same_asset": 0, "converted": 0, "unconverted": 0, "by_asset": {}}
    for f in fills:
        a = f["fee_asset"]
        stats["by_asset"][a] = stats["by_asset"].get(a, 0) + 1
        if a == f["quote_asset"]:
            f["fee_quote"] = float(f["fee"])
            f["fee_conversion"] = {"method": "fee paid in the quote asset"}
            stats["same_asset"] += 1
            continue
        conv = None
        if fee_prices and a in fee_prices:
            ts, rows = fee_prices[a]
            i = bisect_right(ts, f["ts_ms"]) - 1
            if i >= 0 and f["ts_ms"] - ts[i] <= policy["fee_conversion_bar_ms"] and \
                    str(rows[i].get("quote", f["quote_asset"])).upper() == f["quote_asset"]:
                conv = (float(rows[i]["price"]), {"method": "supplied price at or before the fill",
                                                  "price_ts_ms": ts[i], "source": rows[i]["source"]})
        if conv is None and klines:
            b = bar_at(klines.get(f"{a}{f['quote_asset']}"), f["ts_ms"])
            if b is not None:
                conv = (b.open, {"method": f"public {a}{f['quote_asset']} 15m kline open of the bar "
                                           "containing the fill", "price_ts_ms": b.ts})
        if conv is None:
            f["fee_quote"] = None
            f["fee_conversion"] = {"method": "NOT CONVERTED — no price for the fee asset; "
                                             "the fill is not cost evidence (never priced at 0)"}
            stats["unconverted"] += 1
        else:
            f["fee_quote"] = float(f["fee"]) * conv[0]
            f["fee_conversion"] = {**conv[1], "price": conv[0]}
            stats["converted"] += 1
    return stats


# ---------------------------------------------------------------------------
# 7: orders -> cost_calibration observations
# ---------------------------------------------------------------------------

def load_decisions(path: Path) -> dict:
    """The user's own decision log: one row per order (also unfilled ones)."""
    out = {}
    for r in iter_jsonl(Path(path)):
        oid = str(r.get("order_id") or "").strip()
        if not oid:
            raise FillImportError("decision row without order_id")
        if oid in out:
            raise FillImportError(f"decision log lists order {oid} twice")
        out[oid] = r
    return out


def load_quotes(path: Path) -> dict:
    """``{symbol, ts_ms, bid, ask}`` rows -> {symbol: (ts list, rows)}."""
    by: dict = defaultdict(list)
    for r in iter_jsonl(Path(path)):
        by[str(r["symbol"]).upper()].append((int(r["ts_ms"]), float(r["bid"]), float(r["ask"])))
    return {s: ([x[0] for x in sorted(v)], sorted(v)) for s, v in by.items()}


def quotes_from_public_snapshots(root: Path, symbols: Iterable[str]) -> dict:
    """Best bid/ask from ``public_market_collect`` snapshots (public data)."""
    by: dict = defaultdict(list)
    for sym in symbols:
        for p in sorted((Path(root) / sym).glob("public_*.jsonl")):
            for r in iter_jsonl(p, strict=False):
                d = r.get("depth") or {}
                t = d.get("T") or d.get("E")
                if r.get("status") != "OK" or not t or not d.get("bids") or not d.get("asks"):
                    continue
                by[sym].append((int(t), float(d["bids"][0][0]), float(d["asks"][0][0])))
    return {s: ([x[0] for x in sorted(v)], sorted(v)) for s, v in by.items()}


def _reference(quotes: Optional[Mapping], sym: str, ts_ms: int, max_age: int):
    if not quotes or sym not in quotes:
        return None
    ts, rows = quotes[sym]
    i = bisect_right(ts, ts_ms) - 1
    if i < 0 or ts_ms - ts[i] > max_age:
        return None
    _, bid, ask = rows[i]
    if not 0 < bid <= ask:
        return None
    return {"ts_ms": ts[i], "bid": bid, "ask": ask}


def orders_to_observations(fills: Sequence[dict], *, policy: Mapping, decisions: Optional[Mapping] = None,
                           quotes: Optional[Mapping] = None, klines: Optional[Mapping] = None
                           ) -> tuple[list, dict]:
    """Groups fills by order and builds ``cost_calibration.Observation`` rows."""
    from cost_calibration import CalibrationError, Observation
    groups: dict = defaultdict(list)
    for f in fills:
        groups[(f["symbol"], f["order_id"])].append(f)
    obs: list = []
    why: dict = defaultdict(int)
    for (sym, oid), fs in sorted(groups.items(), key=lambda kv: min(f["ts_ms"] for f in kv[1])):
        if len({f["side"] for f in fs}) != 1:
            why["order with fills on both sides"] += 1
            continue
        if any(f.get("fee_quote") is None for f in fs):
            why["fee not convertible to the quote asset"] += 1
            continue
        side = 1 if fs[0]["side"] == "BUY" else -1
        filled = sum(float(f["price"]) * float(f["qty"]) for f in fs)
        qty = sum(float(f["qty"]) for f in fs)
        vwap = filled / qty
        fee = sum(f["fee_quote"] for f in fs)
        first = min(f["ts_ms"] for f in fs)
        liq = "taker" if any(f["liquidity"] == "TAKER" for f in fs) else (
            "maker" if all(f["liquidity"] == "MAKER" for f in fs) else None)
        d = (decisions or {}).get(oid)
        ref, bid, ask, notional, dts = None, None, None, filled, first
        status = "FILLED"
        if d is not None:
            dts = int(d.get("decision_ts_ms") or first)
            if d.get("bid") is not None and d.get("ask") is not None:
                bid, ask = float(d["bid"]), float(d["ask"])
                ref = 0.5 * (bid + ask)
            elif d.get("ref_price") is not None:
                ref = float(d["ref_price"])
            if d.get("requested_notional") is not None:
                notional = float(d["requested_notional"])
                status = "PARTIAL" if filled < notional * 0.999 else "FILLED"
            liq = liq or d.get("order_type")
        if ref is None:
            q = _reference(quotes, sym, dts, policy["reference_quote_max_staleness_ms"])
            if q is not None:
                bid, ask, ref = q["bid"], q["ask"], 0.5 * (q["bid"] + q["ask"])
        if ref is None:
            why["no reference quote at decision time"] += 1
            continue
        if liq is None:
            why["maker/taker unknown"] += 1
            continue
        b = last_closed_bar(klines.get(sym) if klines else None, dts)
        o = Observation(ts_ms=dts, symbol=sym, side=side, order_type=liq, notional=notional,
                        ref_price=ref, status=status, source_kind="exchange_fills", bid=bid, ask=ask,
                        fill_price=vwap, filled_notional=filled, fee=fee,
                        bar_traded_value=(b.quote_volume if b is not None and b.quote_volume > 0 else None),
                        order_id=oid)
        try:
            o.validate()
        except CalibrationError as e:
            why[f"invalid observation: {str(e).split(' — ')[0]}"] += 1
            continue
        obs.append(o)
    # unfilled orders are visible only in the decision log
    if decisions:
        filled_ids = {oid for (_, oid) in groups}
        for oid, d in sorted(decisions.items()):
            if oid in filled_ids or str(d.get("status", "")).upper() not in ("REJECTED", "EXPIRED"):
                continue
            ref = d.get("ref_price") or (0.5 * (float(d["bid"]) + float(d["ask"]))
                                         if d.get("bid") is not None and d.get("ask") is not None else None)
            if ref is None or d.get("requested_notional") is None or d.get("symbol") is None:
                why["unfilled decision without symbol / reference / notional"] += 1
                continue
            o = Observation(ts_ms=int(d["decision_ts_ms"]), symbol=str(d["symbol"]).upper(),
                            side=1 if str(d.get("side", "BUY")).upper() == "BUY" else -1,
                            order_type=d.get("order_type") or "taker",
                            notional=float(d["requested_notional"]), ref_price=float(ref),
                            status=str(d["status"]).upper(), source_kind="exchange_fills", order_id=oid)
            try:
                o.validate()
            except CalibrationError as e:
                why[f"invalid unfilled decision: {str(e).split(' — ')[0]}"] += 1
                continue
            obs.append(o)
    obs.sort(key=lambda o: o.ts_ms)
    return obs, dict(why)


# ---------------------------------------------------------------------------
# summary + CLI
# ---------------------------------------------------------------------------

def summarise(fills: Sequence[dict]) -> dict:
    per: dict = {}
    for f in fills:
        s = per.setdefault(f["symbol"], {"fills": 0, "orders": set(), "first_ts_ms": f["ts_ms"],
                                         "last_ts_ms": f["ts_ms"], "liquidity": {}})
        s["fills"] += 1
        s["orders"].add(f["order_id"])
        s["first_ts_ms"] = min(s["first_ts_ms"], f["ts_ms"])
        s["last_ts_ms"] = max(s["last_ts_ms"], f["ts_ms"])
        s["liquidity"][f["liquidity"]] = s["liquidity"].get(f["liquidity"], 0) + 1
    for s in per.values():
        s["orders"] = len(s["orders"])
        s["days"] = round((s["last_ts_ms"] - s["first_ts_ms"]) / 86_400_000, 3)
    return per


def import_fills(path: Path, *, fmt: Optional[str] = None, policy_path: Optional[Path] = None,
                 column_map: Optional[Mapping] = None, timezone: Optional[str] = None,
                 klines_dir: Optional[Path] = None, fee_prices: Optional[Path] = None,
                 data_origin: str = "user_export", now_ms: Optional[int] = None) -> dict:
    """Steps 1-6.  Returns {status, fills, quarantined, report} — the caller
    decides where (private) rows go; the report holds no row content."""
    if data_origin not in DATA_ORIGINS:
        raise FillImportError(f"data_origin must be one of {DATA_ORIGINS}")
    policy, phash = load_import_policy(policy_path)
    raw = Path(path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    fmt = fmt or detect_format(path)
    report = {"import_version": IMPORT_VERSION, "policy_hash": phash, "source_sha256": sha,
              "source_format": fmt, "source_bytes": len(raw), "data_origin": data_origin,
              "imported_at": iso_now()}
    try:
        rows, header = _read_rows(raw, fmt)
        fills, bad = normalise_rows(rows, header, policy=policy, column_map=column_map,
                                    timezone=timezone, source_sha256=sha, source_format=fmt)
    except (FillImportError, ValueError, UnicodeDecodeError) as e:
        report.update(status="DATA_INVALID", reason=str(e), rows=0)
        return {"status": "DATA_INVALID", "fills": [], "quarantined": [], "report": report}
    klines = load_klines(klines_dir, {f["symbol"] for f in fills} |
                         {f["fee_asset"] + (f["quote_asset"] or "") for f in fills},
                         [f["ts_ms"] for f in fills]) if klines_dir else None
    good, bad2, flags = check_fills(fills, policy=policy, klines=klines, now_ms=now_ms)
    bad = sorted(bad + bad2, key=lambda b: b["row"])
    fee_stats = convert_fees(good, policy=policy,
                             fee_prices=load_fee_prices(fee_prices) if fee_prices else None, klines=klines)
    report.update(rows=len(rows), valid_fills=len(good), quarantined=len(bad), **flags,
                  fee_assets=fee_stats, per_symbol=summarise(good),
                  quarantine_reasons=_reason_counts(bad),
                  kline_check=bool(klines))
    status = ("DATA_INVALID" if flags["conflicting_duplicates"] or not good
              else "IMPORTED_WITH_QUARANTINE" if bad else "IMPORTED")
    report["status"] = status
    return {"status": status, "fills": good, "quarantined": bad, "report": report, "klines": klines,
            "policy": policy}


def _reason_counts(bad: Sequence[Mapping]) -> dict:
    out: dict = {}
    for b in bad:
        k = b["reason"].split(" (")[0].split(":")[0][:80]
        k = "".join("#" if ch.isdigit() else ch for ch in k)
        out[k] = out.get(k, 0) + 1
    return out


def verify_from_import(res: Mapping, *, symbols: Sequence[str], order_value: float,
                       decisions: Optional[Path] = None, quotes: Optional[Path] = None,
                       public_snapshots: Optional[Path] = None,
                       criteria_path: Optional[Path] = None) -> dict:
    """Step 7 + ``cost_calibration.calibrate_and_verify`` (unsealed record)."""
    from cost_calibration import calibrate_and_verify
    from costs_bridge import CostContext
    q = load_quotes(quotes) if quotes else (
        quotes_from_public_snapshots(public_snapshots, symbols) if public_snapshots else None)
    dec = load_decisions(decisions) if decisions else None
    obs, dropped = orders_to_observations(res["fills"], policy=res["policy"], decisions=dec, quotes=q,
                                          klines=res.get("klines"))
    rep = res["report"]
    rec = calibrate_and_verify(
        obs, CostContext.futures_default(), required_symbols=symbols, order_value=order_value,
        criteria_path=criteria_path, source_ref=f"fills_import:{rep['source_sha256'][:16]}",
        quarantined=rep.get("quarantined", 0), order_outcomes_observable=dec is not None,
        data_origin=rep["data_origin"],
        import_integrity_failed=res["status"] == "DATA_INVALID")
    rec["import"] = {k: rep.get(k) for k in ("import_version", "policy_hash", "source_sha256",
                                              "source_format", "rows", "valid_fills", "quarantined",
                                              "status", "data_origin", "fee_assets")}
    rec["orders_not_used"] = dropped
    rec["reference_source"] = ("decision log" if dec else "quote file" if quotes
                               else "public order-book snapshots" if public_snapshots else "none")
    return rec


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI
    import argparse
    ap = argparse.ArgumentParser(description="import the user's exported exchange fills (no login, no API)")
    ap.add_argument("--fills", required=True)
    ap.add_argument("--format", choices=("binance_csv", "binance_api_json"))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--column-map", help='JSON {"field": "export column"}')
    ap.add_argument("--timezone", help="time zone of a time column without offset (e.g. UTC, +09:00)")
    ap.add_argument("--klines", help="public 15m klines directory (bar checks, bar values, fee prices)")
    ap.add_argument("--fee-prices", help="JSONL {asset, ts_ms, price, quote, source}")
    ap.add_argument("--decisions", help="your decision log JSONL (complete order log, incl. unfilled)")
    ap.add_argument("--quotes", help="JSONL {symbol, ts_ms, bid, ask}")
    ap.add_argument("--public-snapshots", help="public_market_collect output directory")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--order-value", type=float, default=10_000.0)
    ap.add_argument("--verify", action="store_true", help="run the cost verification on the import")
    ap.add_argument("--ledger", help="append the sealed verification record here")
    ap.add_argument("--data-origin", choices=DATA_ORIGINS, default="user_export",
                    help="synthetic_test for generated files (never verification evidence)")
    a = ap.parse_args(list(argv) if argv is not None else None)
    res = import_fills(Path(a.fills), fmt=a.format,
                       column_map=json.loads(a.column_map) if a.column_map else None,
                       timezone=a.timezone, klines_dir=Path(a.klines) if a.klines else None,
                       fee_prices=Path(a.fee_prices) if a.fee_prices else None,
                       data_origin=a.data_origin)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sha = res["report"]["source_sha256"][:16]
    for f in res["fills"]:
        append_jsonl(out / f"fills_{sha}.jsonl", f)
    for b in res["quarantined"]:
        append_jsonl(out / f"quarantine_{sha}.jsonl", b)
    atomic_write_json(out / f"import_report_{sha}.json", res["report"])
    summary = {"import": res["report"]}
    rc = 0 if res["status"] == "IMPORTED" else 2
    if a.verify:
        from cost_calibration import append_record, evidence_status, seal
        rec = seal(verify_from_import(res, symbols=a.symbols, order_value=a.order_value,
                                      decisions=Path(a.decisions) if a.decisions else None,
                                      quotes=Path(a.quotes) if a.quotes else None,
                                      public_snapshots=Path(a.public_snapshots) if a.public_snapshots else None))
        if a.ledger:
            append_record(Path(a.ledger), rec)
        summary["verification"] = {k: rec.get(k) for k in ("record_id", "status", "reasons", "scope",
                                                            "verification_errors", "orders_not_used",
                                                            "reference_source")}
        summary["cost_evidence_status"] = evidence_status(rec)
        rc = 0 if rec["status"] == "VERIFIED" else (4 if rec["status"] == "INSUFFICIENT_DATA" else 2)
    print(json.dumps(summary, indent=1, sort_keys=True, default=str))
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
