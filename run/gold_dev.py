#!/usr/bin/env python3
"""Gold (XAUUSD) feasibility check, DEV only (ADR 0086).

Runs exactly the 13 trials fixed in
``run/audit/evidence_gold_dev_20261117/prereg_gold_dev_v1.json`` (committed
before any gold result was computed) on a third-party spot gold M1 bid/ask
feed, 2016-09-01..2021-08-31 only.  Gold data after 2021-08-31 is never read.

No orders, no private API, no NEW2, no network.

Usage::

    python3 run/gold_dev.py --data-dir <dir with XAUUSD_M1_*.csv>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "discovery"))
from engine_lib import expected_max_sr  # noqa: E402

VERSION = "gold-dev-v1"
EVID = HERE / "audit" / "evidence_gold_dev_20261117"
PREREG = EVID / "prereg_gold_dev_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"

S1 = (pd.Timestamp("2016-09-01"), pd.Timestamp("2019-08-31 23:59:59"))
S2 = (pd.Timestamp("2019-09-01"), pd.Timestamp("2021-08-31 23:59:59"))
NEVER_FROM = pd.Timestamp("2021-09-01")
N_TRIALS = 13
CARRY = 0.03  # per year, LONG pays, SHORT receives
COSTS = {  # fee per side, spread multiplier, carry on
    "perp_taker": (5e-4, 1.0, True),
    "perp_taker_x2": (10e-4, 2.0, True),
    "futures_1bp": (1e-4, 1.0, True),
    "cost0": (0.0, 0.0, False),
}
DECIDES = "perp_taker"


# ----------------------------------------------------------------- data
def load_m1(data_dir: Path) -> tuple[pd.DataFrame, dict]:
    files = sorted(data_dir.glob("XAUUSD_M1_*.csv"))
    use, shas = [], {}
    for f in files:
        start = pd.Timestamp(f.stem.split("_")[2])
        if start >= NEVER_FROM:  # never read the gold reserve
            continue
        shas[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
        use.append(pd.read_csv(f))
    if not use:
        raise SystemExit("no XAUUSD_M1_*.csv before 2021-09-01 in " + str(data_dir))
    m = pd.concat(use, ignore_index=True)
    m["ts"] = pd.to_datetime(m["timestamp"])
    m = m[(m["ts"] >= S1[0]) & (m["ts"] < NEVER_FROM)]
    m = m.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return m, shas


def bars_15m(m: pd.DataFrame) -> pd.DataFrame:
    g = m.set_index("ts").resample("15min", label="left", closed="left")
    b = pd.DataFrame({
        "open_bid": g["open_bid"].first(), "open_ask": g["open_ask"].first(),
        "high": ((m.set_index("ts")["high_bid"] + m.set_index("ts")["high_ask"]) / 2).resample("15min").max(),
        "low": ((m.set_index("ts")["low_bid"] + m.set_index("ts")["low_ask"]) / 2).resample("15min").min(),
        "close": ((m.set_index("ts")["close_bid"] + m.set_index("ts")["close_ask"]) / 2).resample("15min").last(),
        "volume": (m.set_index("ts")["volume_bid"] + m.set_index("ts")["volume_ask"]).resample("15min").sum(),
    }).dropna(subset=["open_bid", "open_ask", "close"])
    b["open"] = (b["open_bid"] + b["open_ask"]) / 2
    b["hs"] = (b["open_ask"] - b["open_bid"]) / 2 / b["open"]  # half spread at open
    return b.reset_index().rename(columns={"index": "ts"})


def bars_daily(b: pd.DataFrame) -> pd.DataFrame:
    b = b.assign(date=b["ts"].dt.normalize())
    d = b.groupby("date").agg(open=("open", "first"), hs=("hs", "first"), high=("high", "max"),
                              low=("low", "min"), close=("close", "last"))
    return d.reset_index()


# --------------------------------------------------------------- trades
def trade_net(direction: int, entry: float, exit_: float, hs_in: float, hs_out: float,
              days: float, cost: str) -> float:
    fee, smul, carry = COSTS[cost]
    gross = direction * (exit_ / entry - 1.0)
    c = 2 * fee + smul * (hs_in + hs_out)
    car = (CARRY * days / 365.0 * direction) if carry else 0.0
    return gross - c - car


def hilo_trades(b: pd.DataFrame, N: int, m: float, hb: int, mirror: bool = False) -> list[dict]:
    hi = b["high"].to_numpy(); lo = b["low"].to_numpy(); cl = b["close"].to_numpy()
    vol = b["volume"].to_numpy(); ts = b["ts"].to_numpy()
    H = pd.Series(hi).shift(1).rolling(N).max().to_numpy()
    L = pd.Series(lo).shift(1).rolling(N).min().to_numpy()
    vm = pd.Series(vol).shift(1).rolling(96).mean().to_numpy()
    gaps = np.diff(ts).astype("timedelta64[m]").astype(float) > 30
    gap_cum = np.concatenate([[0], np.cumsum(gaps)])
    out, busy_until = [], -1
    n = len(b)
    for t in range(n):
        if t <= busy_until or not np.isfinite(H[t]) or not np.isfinite(vm[t]) or vm[t] <= 0:
            continue
        R = H[t] - L[t]
        if R <= 0 or vol[t] / vm[t] < m:
            continue
        p = (cl[t] - L[t]) / R
        if p >= 0.9:
            d = -1
        elif p <= 0.1:
            d = 1
        else:
            continue
        if mirror:
            d = -d
        i, j = t + 1, t + 1 + hb
        if j >= n or gap_cum[j] - gap_cum[i] > 0:  # gap inside the hold -> no entry
            continue
        out.append({"entry_i": i, "exit_i": j, "dir": d})
        busy_until = j - 1
    return out


def asia_trades(b: pd.DataFrame) -> list[dict]:
    out = []
    b = b.assign(date=b["ts"].dt.normalize(), hour=b["ts"].dt.hour)
    for _, g in b.groupby("date"):
        asia = g[g["hour"] < 7]
        if len(asia) < 20:
            continue
        hi, lo = asia["high"].max(), asia["low"].min()
        win = g[(g["hour"] >= 7) & (g["hour"] < 12)]
        exit_rows = g[g["hour"] >= 20]
        if exit_rows.empty:
            continue
        j = exit_rows.index[0]
        for idx, row in win.iterrows():
            d = 1 if row["close"] > hi else (-1 if row["close"] < lo else 0)
            if d:
                i = idx + 1
                if i < j:
                    out.append({"entry_i": i, "exit_i": j, "dir": d})
                break
    return out


def intraday_daily(b: pd.DataFrame, trades: list[dict], seg, cost: str):
    """daily return series (exit date) and trade list for one segment"""
    ts = b["ts"]; op = b["open"].to_numpy(); hs = b["hs"].to_numpy()
    rows = []
    for tr in trades:
        t_in, t_out = ts.iloc[tr["entry_i"]], ts.iloc[tr["exit_i"]]
        if not (seg[0] <= t_in and t_out <= seg[1]):
            continue
        days = (t_out - t_in).total_seconds() / 86400
        rows.append({"date": t_out.normalize(), "dir": tr["dir"],
                     "gross": tr["dir"] * (op[tr["exit_i"]] / op[tr["entry_i"]] - 1),
                     "net": trade_net(tr["dir"], op[tr["entry_i"]], op[tr["exit_i"]],
                                      hs[tr["entry_i"]], hs[tr["exit_i"]], days, cost)})
    return pd.DataFrame(rows, columns=["date", "dir", "gross", "net"])


def daily_positions_tsmom(d: pd.DataFrame, L: int) -> np.ndarray:
    sig = np.sign(d["close"] / d["close"].shift(L) - 1).fillna(0).to_numpy()
    return np.concatenate([[0], sig[:-1]])  # signal at close d -> held from open d+1


def daily_positions_donchian(d: pd.DataFrame, n: int = 20) -> np.ndarray:
    hi = d["high"].shift(1).rolling(n).max().to_numpy(); lo = d["low"].shift(1).rolling(n).min().to_numpy()
    cl = d["close"].to_numpy(); pos = np.zeros(len(d)); cur = 0
    for k in range(len(d)):
        pos[k] = cur  # held during day k (decided at close k-1)
        if np.isfinite(hi[k]) and cl[k] > hi[k]:
            cur = 1
        elif np.isfinite(lo[k]) and cl[k] < lo[k]:
            cur = -1
    return pos


def held_daily(d: pd.DataFrame, pos: np.ndarray, seg, cost: str):
    """open-to-open daily P&L of a held position; flat at the segment start/end."""
    fee, smul, carry = COSTS[cost]
    mask = ((d["date"] >= seg[0].normalize()) & (d["date"] <= seg[1].normalize())).to_numpy()
    idx = np.where(mask)[0]
    p = pos[idx].copy(); p[-1] = 0.0  # closed at the open of the last segment day
    op = d["open"].to_numpy(); hs = d["hs"].to_numpy(); dates = d["date"].to_numpy()
    rets, gross_rets, prev, changes = [], [], 0.0, 0
    for k, i in enumerate(idx):
        dp = abs(p[k] - prev)
        changes += int(p[k] != prev and p[k] != 0)
        c = dp * (fee + smul * hs[i])
        if k + 1 < len(idx):
            nxt = idx[k + 1]
            g = p[k] * (op[nxt] / op[i] - 1)
            days = (dates[nxt] - dates[i]) / np.timedelta64(1, "D")
        else:
            g, days = 0.0, 0
        car = (CARRY * days / 365 * p[k]) if carry else 0.0
        rets.append(g - c - car); gross_rets.append(g); prev = p[k]
    s = pd.Series(rets, index=pd.to_datetime(dates[idx]))
    return s, changes, int((p > 0).sum()), int((p < 0).sum())


# ----------------------------------------------------------------- stats
def stats(daily: pd.Series, bh: pd.Series) -> dict:
    n = len(daily)
    yrs = max((daily.index[-1] - daily.index[0]).days / 365.25, 1e-9)
    dpy = n / yrs
    sd = daily.std(ddof=1)
    sr = float(daily.mean() / sd * math.sqrt(dpy)) if sd > 0 else 0.0
    t = float(daily.mean() / sd * math.sqrt(n)) if sd > 0 else 0.0
    eq = (1 + daily).cumprod()
    mdd = float((eq / eq.cummax() - 1).min())
    h = n // 2
    halves = []
    for part in (daily.iloc[:h], daily.iloc[h:]):
        s = part.std(ddof=1)
        halves.append(float(part.mean() / s * math.sqrt(dpy)) if s > 0 else 0.0)
    corr = float(daily.corr(bh.reindex(daily.index).fillna(0))) if daily.std() > 0 else 0.0
    return {"days": n, "years": round(yrs, 3), "sharpe": round(sr, 3), "t": round(t, 3),
            "annual_return": round(float(daily.mean() * dpy), 4), "mdd": round(mdd, 4),
            "halves_sharpe": [round(x, 3) for x in halves], "corr_buyhold": round(corr, 3)}


def segment_days(d: pd.DataFrame, seg) -> pd.DatetimeIndex:
    m = (d["date"] >= seg[0].normalize()) & (d["date"] <= seg[1].normalize())
    return pd.DatetimeIndex(d.loc[m, "date"])


def evaluate(b, d, trial, seg, cost, bh):
    days = segment_days(d, seg)
    kind = trial["kind"]
    if kind in ("hilo", "asia"):
        tr = intraday_daily(b, trial["trades"], seg, cost)
        daily = tr.groupby("date")["net"].sum().reindex(days).fillna(0.0) if len(tr) else pd.Series(0.0, index=days)
        extra = {"trades": int(len(tr)), "long": int((tr["dir"] > 0).sum()) if len(tr) else 0,
                 "short": int((tr["dir"] < 0).sum()) if len(tr) else 0,
                 "hit_rate": round(float((tr["net"] > 0).mean()), 3) if len(tr) else None,
                 "mean_gross_bp": round(float(tr["gross"].mean() * 1e4), 2) if len(tr) else None,
                 "mean_net_bp": round(float(tr["net"].mean() * 1e4), 2) if len(tr) else None}
    else:
        daily, changes, nl, ns = held_daily(d, trial["pos"], seg, cost)
        daily = daily.reindex(days).fillna(0.0)
        extra = {"trades": changes, "long_days": nl, "short_days": ns}
    return {**stats(daily, bh), **extra}


def build_trials(b, d) -> list[dict]:
    out = []
    for N in (96, 480):
        for m in (2.0, 3.0):
            for hb in (4, 16):
                out.append({"id": f"A_R_N{N}_m{m}_H{hb}", "kind": "hilo", "trades": hilo_trades(b, N, m, hb)})
    out.append({"id": "A_B_N96_m2.0_H16", "kind": "hilo", "trades": hilo_trades(b, 96, 2.0, 16, mirror=True)})
    for L in (20, 60):
        out.append({"id": f"B_tsmom_L{L}", "kind": "held", "pos": daily_positions_tsmom(d, L)})
    out.append({"id": "C_donchian20", "kind": "held", "pos": daily_positions_donchian(d, 20)})
    out.append({"id": "D_asia_breakout", "kind": "asia", "trades": asia_trades(b)})
    assert len(out) == N_TRIALS
    return out


def feed_checks(m: pd.DataFrame, b: pd.DataFrame) -> dict:
    mid = (m["close_bid"] + m["close_ask"]) / 2
    sp = (m["close_ask"] - m["close_bid"]) / mid * 1e4
    by_hour = sp.groupby(m["ts"].dt.hour).median().round(2).to_dict()
    gaps = b["ts"].diff()
    wk = b.loc[gaps > pd.Timedelta(hours=24), "ts"]
    before = b["ts"].shift(1)[gaps > pd.Timedelta(hours=24)]
    return {"m1_rows": int(len(m)), "bars_15m": int(len(b)),
            "spread_bp_median": round(float(sp.median()), 3), "spread_bp_mean": round(float(sp.mean()), 3),
            "spread_bp_p90": round(float(sp.quantile(0.9)), 3),
            "spread_bp_median_by_utc_hour": {int(k): v for k, v in by_hour.items()},
            "weekly_gap_last_bar_before (weekday hour UTC, most common)":
                before.dt.strftime("%a %H:%M").value_counts().head(3).to_dict(),
            "weekly_gap_first_bar_after (most common)": wk.dt.strftime("%a %H:%M").value_counts().head(3).to_dict()}


def flag(r: dict, emax: float) -> dict:
    s1, s2, s1x2 = r["S1"][DECIDES], r["S2"][DECIDES], r["S1"]["perp_taker_x2"]
    c = {"a_S1_sharpe_gt_emax": s1["sharpe"] > emax, "b_S1_t_ge_2": s1["t"] >= 2,
         "c_S1_halves_pos": all(h > 0 for h in s1["halves_sharpe"]), "d_S2_sharpe_gt_0.5": s2["sharpe"] > 0.5,
         "e_S1_x2_cost_pos": s1x2["sharpe"] > 0, "f_S1_trades_ge_100": s1["trades"] >= 100}
    return {"conditions": c, "flag": "DEV_PROMISING" if all(c.values()) else "NOT_PROMISING"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "gold_dev_result.json")
    ap.add_argument("--no-trial-log", action="store_true")
    a = ap.parse_args(argv)
    m, shas = load_m1(a.data_dir)
    b = bars_15m(m)
    d = bars_daily(b)
    bh = (d.set_index("date")["close"].pct_change()).fillna(0)
    trials = build_trials(b, d)
    s1_years = (S1[1] - S1[0]).days / 365.25
    emax = expected_max_sr(N_TRIALS) / math.sqrt(s1_years)
    res = {"version": VERSION, "prereg": str(PREREG.relative_to(HERE.parent)),
           "prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(),
           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "data_files_sha256": shas, "data_status": "UNVERIFIED_THIRD_PARTY spot XAUUSD bid/ask feed",
           "never_read_from": str(NEVER_FROM.date()), "emax_S1_sharpe_13_trials": round(emax, 3),
           "feed": feed_checks(m, b), "buy_and_hold": {}, "trials": {}}
    for name, seg in (("S1", S1), ("S2", S2)):
        days = segment_days(d, seg)
        res["buy_and_hold"][name] = stats(bh.reindex(days).fillna(0), bh)
    for tr in trials:
        r = {seg_name: {c: evaluate(b, d, tr, seg, c, bh) for c in COSTS} for seg_name, seg in (("S1", S1), ("S2", S2))}
        r.update(flag(r, emax))
        res["trials"][tr["id"]] = r
    res["summary"] = {k: {"flag": v["flag"], "S1_sharpe": v["S1"][DECIDES]["sharpe"],
                          "S1_cost0_sharpe": v["S1"]["cost0"]["sharpe"], "S2_sharpe": v["S2"][DECIDES]["sharpe"],
                          "S1_trades": v["S1"][DECIDES]["trades"],
                          "S1_mean_gross_bp": v["S1"][DECIDES].get("mean_gross_bp")}
                      for k, v in res["trials"].items()}
    a.out.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
    if not a.no_trial_log:
        with TRIALS.open("a") as f:
            f.write(json.dumps({"date": "2026-11-17", "search": "gold XAUUSD DEV feasibility: 15m hi/lo line + volume spike (9), daily TSMOM (2), Donchian (1), Asia-range breakout (1) (ADR 0086)",
                                "context_id": "GOLD-XAUUSD-spot-v1", "dev_evaluated": N_TRIALS,
                                "data": "third-party spot XAUUSD M1 bid/ask, S1 2016-09-01..2019-08-31 choose, S2 2019-09-01..2021-08-31 report; gold after 2021-08-31 never read",
                                "validation_or_lock_opened": False}, ensure_ascii=False) + "\n")
    print(json.dumps(res["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
