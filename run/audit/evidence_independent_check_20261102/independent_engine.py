"""Independent minimal re-implementation of the cross-sectional DEV pipeline.

Written 2026-11-02 from the ADR / prereg text only, WITHOUT reading the
project's calculation code (run/broad_universe_dev.py, cross_section_v2_dev.py,
wide_universe_dev.py, residual_signals_dev.py, alpha101_dev.py).

Input: raw Binance USD-M monthly zips (1d klines, fundingRate) laid out as
<raw>/1d/<SYM>/*.zip and <raw>/fr/<SYM>/*.zip (see fetch_raw.py).
Rows inside never-read ranges are dropped at load time.
Cost is 0 everywhere (this is a gross-return check), funding optional.
"""
from __future__ import annotations

import io
import os
import sys
import zipfile
from dataclasses import dataclass

import numpy as np
import pandas as pd

DAY = pd.Timedelta(days=1)
S1 = (pd.Timestamp("2021-01-01"), pd.Timestamp("2023-09-18"), pd.Timestamp("2021-03-01"))
S2 = (pd.Timestamp("2024-06-19"), pd.Timestamp("2025-10-20"), pd.Timestamp("2024-08-19"))
STABLE = ("USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "USDE", "EUR", "GBP", "AEUR")


def allowed(ts: pd.Series) -> pd.Series:
    return ((ts >= S1[0]) & (ts <= S1[1])) | ((ts >= S2[0]) & (ts <= S2[1]))


def excluded_symbol(sym: str) -> bool:
    base = sym[:-4]
    return base in STABLE or "DOM" in sym or "DEFI" in sym


def _read_zip_csv(path: str, header_word: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0]).decode()
    first = raw.split("\n", 1)[0]
    has_header = header_word in first
    return pd.read_csv(io.StringIO(raw), header=0 if has_header else None)


def load_panel(raw: str):
    """Return dict of wide DataFrames (date x symbol) and a quality log."""
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
    frames = {}
    quality = []
    syms = sorted(os.listdir(os.path.join(raw, "1d")))
    for sym in syms:
        if not sym.endswith("USDT") or excluded_symbol(sym):
            continue
        d = os.path.join(raw, "1d", sym)
        parts = []
        for f in sorted(os.listdir(d)):
            df = _read_zip_csv(os.path.join(d, f), "open_time")
            df = df.iloc[:, :12]
            df.columns = cols
            parts.append(df)
        if not parts:
            continue
        df = pd.concat(parts, ignore_index=True)
        ot = pd.to_numeric(df["open_time"])
        if ot.max() > 1e14:  # microseconds in newer files
            ot = np.where(ot > 1e14, ot // 1000, ot)
        df["date"] = pd.to_datetime(ot, unit="ms")
        n_off = int((df["date"] != df["date"].dt.normalize()).sum())
        df = df[allowed(df["date"])]
        if sym == "BTCUSDT":
            df = df[df["date"] <= S1[1]]
        n_dup = int(df["date"].duplicated().sum())
        df = df.drop_duplicates("date", keep="last").set_index("date").sort_index()
        for c in cols[1:11]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        quality.append({"sym": sym, "rows": len(df), "dup": n_dup, "off_midnight": n_off})
        frames[sym] = df
    fields = ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote_volume"]
    # full calendar incl. the never-read gap as all-NaN rows, so that shift(1)
    # never links 2023-09-18 to 2024-06-19 (found in this check: a 17x fake return)
    all_dates = pd.date_range(S1[0], S2[1], freq="D")
    panel = {f: pd.DataFrame({s: frames[s][f] for s in frames}).reindex(all_dates) for f in fields}
    # funding: sum of settled rates per UTC day
    fund = {}
    for sym in frames:
        d = os.path.join(raw, "fr", sym)
        if not os.path.isdir(d):
            continue
        parts = []
        for f in sorted(os.listdir(d)):
            df = _read_zip_csv(os.path.join(d, f), "calc_time")
            df = df.iloc[:, [0, -1]]
            df.columns = ["t", "rate"]
            parts.append(df)
        if not parts:
            continue
        df = pd.concat(parts)
        t = pd.to_numeric(df["t"])
        df["day"] = pd.to_datetime(t, unit="ms").dt.normalize()
        df = df[allowed(df["day"])]
        fund[sym] = df.groupby("day")["rate"].sum()
    panel["funding"] = pd.DataFrame(fund).reindex(all_dates).reindex(columns=panel["close"].columns)
    return panel, pd.DataFrame(quality)


@dataclass
class Spec:
    signal: str          # key into SIGNALS
    hold: int            # days between rebalances
    long_high: bool      # True: highest signal values are long
    universe: str = "U50"  # U50 (top 50, no floor, K=10) or W200 (floor 2M, top 200, K=n//5)
    drift: bool = True   # weights drift with returns between rebalances
    lag: int = 2         # cut-off = rebalance day - lag (2 = Saturday bar for Monday)
    funding: bool = True


def day_returns(panel):
    o, c = panel["open"], panel["close"]
    cc = c / c.shift(1) - 1           # close-to-close
    co = c / o - 1                    # entry-day open-to-close
    return cc, co


def eligibility(panel, cut, day, universe):
    c = panel["close"]
    qv = panel["quote_volume"]
    hist = c.loc[:cut]
    nbars = hist.notna().sum()
    has_open = panel["open"].loc[day].notna() if day in panel["open"].index else pd.Series(False, index=c.columns)
    v30 = qv.loc[cut - 29 * DAY:cut].mean()
    ok = (nbars >= 60) & has_open & v30.notna()
    if universe == "W200":
        ok &= v30 >= 2_000_000
    names = v30[ok].reset_index()
    names.columns = ["sym", "v30"]
    names = names.sort_values(["v30", "sym"], ascending=[False, True])
    n = 50 if universe == "U50" else 200
    return list(names["sym"].iloc[:n])


def run(panel, spec: Spec, seg, sig_fn, rng=None):
    start, end, first = seg
    idx = panel["close"].index
    cc, co = day_returns(panel)
    fund = panel["funding"].fillna(0.0)
    mondays = [d for d in pd.date_range(first, end, freq="D") if d.weekday() == 0]
    first_mon = mondays[0]
    rebal = []
    d = first_mon
    while d + (spec.hold - 1) * DAY <= end:
        rebal.append(d)
        d = d + spec.hold * DAY
    out_days, out_ret = [], []
    missing = 0
    for i, day in enumerate(rebal):
        cut = day - spec.lag * DAY
        if cut < start:
            continue
        names = eligibility(panel, cut, day, spec.universe)
        if len(names) < 30:
            continue
        sig = sig_fn(panel, cut, names, day, spec)
        if rng is not None:
            sig = pd.Series(rng.permutation(sig.values), index=sig.index)
        sig = sig.dropna()
        if spec.universe == "U50":
            k = 10
        else:
            k = len(names) // 5
        if len(sig) < 2 * k:
            continue
        df = sig.reset_index()
        df.columns = ["sym", "v"]
        df = df.sort_values(["v", "sym"], ascending=[not spec.long_high, True])
        longs, shorts = list(df["sym"].iloc[:k]), list(df["sym"].iloc[-k:])
        w = pd.Series(0.0, index=longs + shorts)
        w[longs] = 1 / (2 * k)
        w[shorts] = -1 / (2 * k)
        for j in range(spec.hold):
            dd = day + j * DAY
            r = (co if j == 0 else cc).loc[dd, w.index]
            missing += int(r.isna().sum())
            r = r.fillna(0.0)
            pr = float((w * r).sum())
            if spec.funding:
                pr -= float((w * fund.loc[dd, w.index]).sum())
            out_days.append(dd)
            out_ret.append(pr)
            if spec.drift:
                w = w * (1 + r)
    s = pd.Series(out_ret, index=out_days)
    return s, {"rebalances": len(rebal), "missing_name_days": missing}


def stats(s: pd.Series):
    n = len(s)
    sr = s.mean() / s.std(ddof=1) * np.sqrt(365) if n > 2 and s.std() > 0 else float("nan")
    h = n // 2
    halves = [s.iloc[:h], s.iloc[h:]]
    hs = [float(x.mean() / x.std(ddof=1) * np.sqrt(365)) for x in halves]
    return {"n_days": n, "sr_ann": float(sr), "ann_ret_pct": float(s.mean() * 365 * 100),
            "first": str(s.index[0].date()), "last": str(s.index[-1].date()), "halves_sr": hs}


# ---------------- signals (value on cut-off bar, per name) ----------------
def sig_mom(L):
    def f(p, cut, names, day, spec):
        c = p["close"][names]
        return c.loc[cut] / c.loc[cut - L * DAY] - 1
    return f


def sig_taker(p, cut, names, day, spec):
    win = slice(cut - 27 * DAY, cut)
    tb = p["taker_buy_quote_volume"].loc[win, names].sum(min_count=1)
    qv = p["quote_volume"].loc[win, names].sum(min_count=1)
    return tb / qv


def sig_a101(p, cut, names, day, spec):
    o, h, l, c = (p[k].loc[cut, names] for k in ("open", "high", "low", "close"))
    return (c - o) / ((h - l) + 0.001 * c)


def market_series(p, cc, upto):
    """Equal-weight mean return of the day's top-50 by trailing 30d quote volume (cached)."""
    if "_mkt" not in p:
        qv = p["quote_volume"].rolling(30, min_periods=20).mean()
        rank = qv.rank(axis=1, ascending=False, method="first")
        p["_mkt"] = cc.where(rank <= 50).mean(axis=1)
    return p["_mkt"]


def sig_resmax(p, cut, names, day, spec):
    cc = p["close"] / p["close"].shift(1) - 1
    m = market_series(p, cc, cut)
    win = slice(cut - 89 * DAY, cut)
    R = cc.loc[win, names]
    M = m.loc[win]
    out = {}
    for s in names:
        y = R[s]
        ok = y.notna() & M.notna()
        if ok.sum() < 60:
            continue
        x = M[ok]
        yy = y[ok]
        beta = np.cov(x, yy, ddof=1)[0, 1] / np.var(x, ddof=1)
        e = (R[s] - beta * M).loc[cut - 27 * DAY:cut].dropna()
        if len(e) < 20:
            continue
        out[s] = e.max()
    return pd.Series(out)


def sig_oracle(p, cut, names, day, spec):
    c = p["close"][names]
    end = day + (spec.hold - 1) * DAY
    return c.loc[end] / p["open"].loc[day, names] - 1


def sig_leak(L):
    """momentum computed through the entry day's own close (look-ahead by one bar)."""
    def f(p, cut, names, day, spec):
        c = p["close"][names]
        return c.loc[day] / c.loc[day - L * DAY] - 1
    return f
