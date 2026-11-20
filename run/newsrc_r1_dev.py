#!/usr/bin/env python3
"""Round 1 of the repeated new-source search, DEV only (ADR 0096).

Runs exactly the 7 trials fixed in
``run/audit/evidence_newsrc_r1_20261120/prereg_newsrc_r1_v1.json``
(committed before any spot or premium-index file was downloaded):

  P1_HIGH180 (U50, W200)       180-day-high proximity, quintile long-short
  V1_PERPSPOT_LEVEL (U50)      perp/spot quote-volume ratio level (Binance SPOT archive)
  V2_PERPSPOT_CHANGE (U50)     change of that ratio
  B1_PREMIUM_MOM (U50)         premium-index change (basis momentum)
  E1_SPOTLIST_SHORT (H7, H28)  short the perp after the coin's spot listing

Universe, cost (ADR 0041 calibration) and drift accounting are those of ADR
0047/0094 (``wide_universe_dev``).  Never reads 2020 (IW1),
2023-09-19..2024-06-18, 2025-10-21 onwards, BTCUSDT in S2.  No orders, no
private API, no NEW2.

Usage::

    python3 run/broad_universe_fetch.py --out <dir> --step daily
    python3 run/wide_universe_dev.py --data-dir <dir> --write-universe
    python3 run/broad_universe_fetch.py --out <dir> --step funding --symbols <dir>/ever_w200.json
    python3 run/broad_universe_fetch.py --out <dir> --step bar2345 --symbols <dir>/ever_w200.json
    python3 run/newsrc_r1_dev.py --data-dir <dir> --fetch      # spot 1d, premium 1d, spot listing dates
    python3 run/newsrc_r1_dev.py --data-dir <dir>
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import math
import re
import sys
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))
import broad_universe_dev as bu  # noqa: E402
import broad_universe_fetch as bf  # noqa: E402
import cross_section_v2_dev as cs  # noqa: E402
import wide_universe_dev as wu  # noqa: E402
from engine_lib import deflated_sharpe, expected_max_sr, pbo_cscv  # noqa: E402

VERSION = "newsrc-r1-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r1_20261120"
PREREG = EVID / "prereg_newsrc_r1_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"
SEGMENTS = bu.SEGMENTS
HOLD = 14
HIGH_LB = 180
EVAL_START = {"P": {"S1": "2021-07-05", "S2": "2024-12-16"},
              "X": {"S1": SEGMENTS["S1"][2], "S2": SEGMENTS["S2"][2]}}
XS_KEYS = [("P1_HIGH180", "U50"), ("P1_HIGH180", "W200"), ("V1_PERPSPOT_LEVEL", "U50"),
           ("V2_PERPSPOT_CHANGE", "U50"), ("B1_PREMIUM_MOM", "U50")]
EV_KEYS = [("E1_SPOTLIST_SHORT_H7", "EVT"), ("E1_SPOTLIST_SHORT_H28", "EVT")]
EV_HOLD = {"E1_SPOTLIST_SHORT_H7": 7, "E1_SPOTLIST_SHORT_H28": 28}
EV_WEIGHT = -0.10
EV_MIN_PERP_BARS = 30
EV_MIN_S1 = 20
TRIAL_KEYS = XS_KEYS + EV_KEYS
N_TRIALS = len(TRIAL_KEYS)
LONG_TOP = {"P1_HIGH180": True, "V1_PERPSPOT_LEVEL": False, "V2_PERPSPOT_CHANGE": False, "B1_PREMIUM_MOM": False}
COST_ROWS = wu.COST_ROWS
CONTEXT_ID = "U50W200-14d-newsrc-r1-v1"
SPOT_BASE = bf.S3 + "/data/spot/monthly/klines"
SPOT_LIST = bf.S3 + "?delimiter=/&prefix=data/spot/monthly/klines/"
PREM_BASE = bf.BASE + "/premiumIndexKlines"
UNIT_PREFIX = re.compile(r"^(1000000|1000|1M)(?=[A-Z])")


def spot_name(perp: str) -> str:
    return UNIT_PREFIX.sub("", perp)


def family(h: str) -> str:
    return "P" if h.startswith("P") else "X"


def day_iso(t: int) -> str:
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def norm_ms(t: int) -> int:
    """Spot archive switched to microseconds in 2025."""
    return t // 1000 if t > 10 ** 14 else t


def prior_trials() -> int:
    rows = [json.loads(ln) for ln in TRIALS.read_text().splitlines() if ln.strip()]
    return sum(r.get("dev_evaluated", 0) for r in rows if r.get("context_id") != CONTEXT_ID)


# --------------------------------------------------------------------------- fetch

def ever_u50(data: Path) -> list:
    ever = set()
    for seg in SEGMENTS:
        piv = cs.load_daily(data, seg)
        days = piv["open"].index.values
        n_bars = piv["close"].notna().cumsum()
        for i in wu.rebalance_days(days, SEGMENTS[seg][2], HOLD):
            ever |= set(wu.slice_universe(wu.eligible(piv, n_bars, i), "U50"))
    return sorted(ever)


def checked(base: str, rel: str) -> bytes | None:
    path = urllib.parse.quote(rel)
    body = bf.get(f"{base}/{path}")
    if body is None:
        return None
    chk = bf.get(f"{base}/{path}.CHECKSUM")
    if chk is None or chk.decode().split()[0].strip().lower() != hashlib.sha256(body).hexdigest():
        raise RuntimeError(f"CHECKSUM missing or mismatch {rel}")
    return body


def spot_months(spot: str) -> list:
    body = bf.get(SPOT_LIST + urllib.parse.quote(f"{spot}/1d/"))
    if body is None:
        return []
    return sorted(set(re.findall(rf"{re.escape(spot)}-1d-(\d{{4}}-\d{{2}})\.zip<", body.decode())))


def fetch_symbol(data: Path, perp: str, u50: bool, allowed: set) -> dict:
    spot = spot_name(perp)
    res = {"perp": perp, "spot": spot, "spot_months": spot_months(spot), "files": 0, "missing": 0}
    first = res["spot_months"][0] if res["spot_months"] else None
    want = []
    if u50:
        want += [("spot", m) for m in res["spot_months"] if m in allowed]
        want += [("prem", m) for m in allowed]
    if first and first in allowed and ("spot", first) not in want:
        want.append(("spot", first))
    for kind, m in want:
        sub = "spot1d" if kind == "spot" else "prem1d"
        dst = data / sub / f"{perp}-{m}.csv"
        if dst.exists():
            continue
        if kind == "spot":
            body = checked(SPOT_BASE, f"{spot}/1d/{spot}-1d-{m}.zip")
        else:
            body = checked(PREM_BASE, f"{perp}/1d/{perp}-1d-{m}.zip")
        if body is None:
            res["missing"] += 1
            continue
        dst.write_text(bf.unzip_one(body))
        res["files"] += 1
    return res


def fetch_all(data: Path, workers: int) -> int:
    for sub in ("spot1d", "prem1d"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    w200 = json.loads((data / "ever_w200.json").read_text())
    u50 = set(ever_u50(data))
    (data / "ever_u50_14d.json").write_text(json.dumps(sorted(u50)))
    allowed = set(bf.months())
    out, errors = {}, []
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_symbol, data, s, s in u50, allowed): s for s in sorted(set(w200) | u50)}
        for f in cf.as_completed(futs):
            try:
                r = f.result()
                out[r["perp"]] = r
            except Exception as e:  # recorded, never silently skipped
                errors.append(f"{futs[f]}: {e}")
    (data / "spot_listing.json").write_text(json.dumps(out, indent=0, sort_keys=True))
    print("symbols", len(out), "files", sum(r["files"] for r in out.values()),
          "missing", sum(r["missing"] for r in out.values()), "errors", len(errors))
    for e in errors[:20]:
        print("ERROR", e)
    return 1 if errors else 0


# --------------------------------------------------------------------------- loaders

def load_side(data: Path, sub: str, col: int, days: np.ndarray, syms: list) -> pd.DataFrame:
    t0, t1 = int(days[0]), int(days[-1])
    bu.assert_allowed(t0, t1)
    recs = []
    for p in sorted((data / sub).glob("*.csv")):
        sym = p.name.rsplit("-", 2)[0]
        if sym not in syms:
            continue
        for f in bu.read_rows(p):
            t = norm_ms(int(f[0]))
            if t0 <= t <= t1:
                recs.append((sym, t, float(f[col])))
    if not recs:
        return pd.DataFrame(np.nan, index=days, columns=syms)
    df = pd.DataFrame(recs, columns=["sym", "t", "v"]).drop_duplicates(["sym", "t"])
    return df.pivot(index="t", columns="sym", values="v").reindex(index=days, columns=syms)


def first_spot_days(data: Path) -> dict:
    """perp -> first spot day (ms), only when the first spot month is an allowed month and its file was read."""
    info = json.loads((data / "spot_listing.json").read_text())
    out = {}
    for perp, r in info.items():
        if not r["spot_months"]:
            continue
        m = r["spot_months"][0]
        p = data / "spot1d" / f"{perp}-{m}.csv"
        if not p.exists():
            continue
        rows = bu.read_rows(p)
        if rows:
            t = norm_ms(int(rows[0][0]))
            if not any(bu.ms(lo) <= t <= bu.ms(hi) for lo, hi, _ in SEGMENTS.values()):
                continue  # listing day inside a never-read range: event dropped, nothing else of the file is used
            out[perp] = t
    return out


# --------------------------------------------------------------------------- signals

def xs_signals(piv: dict, spot_qv: pd.DataFrame, prem: pd.DataFrame, cut: int) -> dict:
    close, qv = piv["close"], piv["qv"]
    out = {}
    if cut - (HIGH_LB - 1) >= 0:
        win = close.iloc[cut - HIGH_LB + 1: cut + 1]
        full = win.notna().sum() == HIGH_LB
        out["P1_HIGH180"] = (close.iloc[cut] / win.max()).where(full)
    else:
        out["P1_HIGH180"] = pd.Series(np.nan, index=close.columns)
    if cut - 34 >= 0:
        pq, sq = qv.iloc[cut - 27: cut + 1], spot_qv.iloc[cut - 27: cut + 1]
        ok = (sq > 0) & (pq > 0)
        lvl = np.log(pq.where(ok).sum() / sq.where(ok).sum())
        out["V1_PERPSPOT_LEVEL"] = lvl.where(ok.sum() >= 20)
        lr = np.log(qv.iloc[cut - 34: cut + 1] / spot_qv.iloc[cut - 34: cut + 1].where(spot_qv.iloc[cut - 34: cut + 1] > 0))
        lr = lr.replace([np.inf, -np.inf], np.nan)
        rec, old = lr.iloc[-7:], lr.iloc[:-7]
        out["V2_PERPSPOT_CHANGE"] = (rec.mean() - old.mean()).where((rec.notna().sum() >= 5) & (old.notna().sum() >= 20))
        pr = prem.iloc[cut - 34: cut + 1]
        rec, old = pr.iloc[-7:], pr.iloc[:-7]
        out["B1_PREMIUM_MOM"] = (rec.mean() - old.mean()).where((rec.notna().sum() >= 5) & (old.notna().sum() >= 20))
    else:
        for h in ("V1_PERPSPOT_LEVEL", "V2_PERPSPOT_CHANGE", "B1_PREMIUM_MOM"):
            out[h] = pd.Series(np.nan, index=close.columns)
    return out


def events(piv: dict, n_bars: pd.DataFrame, first_spot: dict, days: np.ndarray, ev0: int) -> list:
    """(perp, entry index L+1) of qualifying spot listings inside this segment."""
    qv, opn = piv["qv"], piv["open"]
    out = []
    for perp, t in sorted(first_spot.items()):
        if perp not in opn.columns:
            continue
        li = int(np.searchsorted(days, t))
        if li >= len(days) or int(days[li]) != t:
            continue
        e = li + 1
        if e >= len(days) - 1 or int(days[e]) < ev0:
            continue
        if li < 1 or n_bars.iloc[li - 1][perp] < EV_MIN_PERP_BARS:
            continue
        cut = e - 2
        v30 = qv.iloc[max(0, cut - 29): cut + 1][perp].mean()
        if math.isnan(opn.iloc[e][perp]) or not (v30 >= wu.FLOOR):
            continue
        out.append((perp, e))
    return out


# --------------------------------------------------------------------------- simulation

def impact_bar(bars, qv, s, i, bar_t, flags):
    b = bars.get((s, bar_t))
    if b is None or b <= 0:
        flags["impact_bar_fallback"] += 1
        dq = qv.iloc[i - 1][s]
        if not (dq > 0):
            dq = float(np.nanmean(qv[s].iloc[max(0, i - 30): i].values))
        b = dq / 96.0 if dq > 0 else 1.0
    return b


def run_segment(data: Path, seg: str, cal: dict, first_spot: dict) -> dict:
    piv = cs.load_daily(data, seg)
    days = piv["open"].index.values
    syms = list(piv["open"].columns)
    t0, t1 = int(days[0]), int(days[-1])
    fund = bu.load_funding(data, syms, t0, t1)
    fund["day"] = ((fund["t"] - 1) // bu.DAY_MS) * bu.DAY_MS
    fnd = fund.groupby(["day", "sym"])["rate"].sum().unstack().reindex(days).reindex(columns=syms).values
    bars = bu.load_bar2345(data, syms, t0, t1)
    spot_qv = load_side(data, "spot1d", 7, days, syms)
    prem = load_side(data, "prem1d", 4, days, syms)
    opn, qv = piv["open"], piv["qv"]
    ret = (opn.shift(-1) / opn - 1.0).values
    col = {s: k for k, s in enumerate(syms)}
    n_bars = piv["close"].notna().cumsum()
    keys = TRIAL_KEYS
    daily = {k: {r: np.zeros(len(days)) for r in COST_ROWS} for k in keys}
    mkt = np.full(len(days), np.nan)
    cost_paid = {k: 0.0 for k in keys}
    turnover = {k: [] for k in XS_KEYS}
    n_reb = {k: 0 for k in XS_KEYS}
    flat_reb = {k: 0 for k in XS_KEYS}
    names_sig = {k: [] for k in XS_KEYS}
    flags = {"missing_return_days": 0, "impact_bar_fallback": 0,
             "spot_symbol_days": int(spot_qv.notna().sum().sum()), "premium_symbol_days": int(prem.notna().sum().sum())}
    held = {k: {} for k in XS_KEYS}
    reb = wu.rebalance_days(days, SEGMENTS[seg][2], HOLD)
    ev = {f: bu.ms(EVAL_START[f][seg]) for f in EVAL_START}
    v30_cache = {}
    for j, i in enumerate(reb):
        cut = i - 2
        if cut < 0:
            continue
        ranked = wu.eligible(piv, n_bars, i)
        end = reb[j + 1] if j + 1 < len(reb) else len(days) - 1
        bar_t = int(days[i - 1]) + 23 * 3_600_000 + 45 * 60_000
        v30 = qv.iloc[max(0, cut - 29): cut + 1].mean()
        v30_cache[i] = v30
        u50 = wu.slice_universe(ranked, "U50")
        if u50:
            uc = [col[s] for s in u50]
            for d in range(i, end):
                r = ret[d, uc]
                mkt[d] = float(np.nanmean(r)) if np.isfinite(r).any() else 0.0
        sig = xs_signals(piv, spot_qv, prem, cut)
        for k in XS_KEYS:
            h, u = k
            if int(days[i]) < ev[family(h)]:
                continue
            universe = wu.slice_universe(ranked, u)
            n_reb[k] += 1
            w = wu.pick(sig[h], universe, LONG_TOP[h]) if universe else {}
            names_sig[k].append(int(sig[h].reindex(universe).notna().sum()))
            if not w:
                flat_reb[k] += 1
            prev = held[k]
            to = 0.0
            for s in sorted(set(w) | set(prev)):
                dw = abs(w.get(s, 0.0) - prev.get(s, 0.0))
                if dw == 0:
                    continue
                b = impact_bar(bars, qv, s, i, bar_t, flags)
                q15 = float(v30[s]) / 96.0 if not math.isnan(v30[s]) else 0.0
                for row in COST_ROWS:
                    daily[k][row][i] -= dw * wu.side_cost(row, cal, b, q15)
                cost_paid[k] += dw * wu.side_cost("cal", cal, b, q15)
                to += dw
            turnover[k].append(to)
            cur = dict(w)
            for d in range(i, end):
                pnl = 0.0
                for s, ws in cur.items():
                    r = ret[d, col[s]]
                    if math.isnan(r):
                        flags["missing_return_days"] += 1
                        r = 0.0
                    f = fnd[d, col[s]]
                    pnl += ws * r - ws * (0.0 if math.isnan(f) else f)
                    cur[s] = ws * (1.0 + r)
                for row in COST_ROWS:
                    daily[k][row][d] += pnl
            held[k] = cur
    # events
    evs = events(piv, n_bars, first_spot, days, ev["X"])
    ev_log = {k: [] for k in EV_KEYS}
    for k in EV_KEYS:
        hold = EV_HOLD[k[0]]
        for perp, e in evs:
            x = e + hold
            if x > len(days) - 1:
                continue
            c = col[perp]
            cut = e - 2
            q15e = float(qv.iloc[max(0, cut - 29): cut + 1][perp].mean()) / 96.0
            be = impact_bar(bars, qv, perp, e, int(days[e - 1]) + 23 * 3_600_000 + 45 * 60_000, flags)
            w = EV_WEIGHT
            for row in COST_ROWS:
                daily[k][row][e] -= abs(w) * wu.side_cost(row, cal, be, q15e)
            cost_paid[k] += abs(w) * wu.side_cost("cal", cal, be, q15e)
            tot = 0.0
            for d in range(e, x):
                r = ret[d, c]
                if math.isnan(r):
                    flags["missing_return_days"] += 1
                    r = 0.0
                f = fnd[d, c]
                pnl = w * r - w * (0.0 if math.isnan(f) else f)
                tot += pnl
                for row in COST_ROWS:
                    daily[k][row][d] += pnl
                w = w * (1.0 + r)
            cutx = x - 2
            q15x = float(qv.iloc[max(0, cutx - 29): cutx + 1][perp].mean()) / 96.0
            bx = impact_bar(bars, qv, perp, x, int(days[x - 1]) + 23 * 3_600_000 + 45 * 60_000, flags)
            for row in COST_ROWS:
                daily[k][row][x] -= abs(w) * wu.side_cost(row, cal, bx, q15x)
            cost_paid[k] += abs(w) * wu.side_cost("cal", cal, bx, q15x)
            ev_log[k].append({"perp": perp, "spot_day": day_iso(int(days[e - 1])), "gross_pnl_pct_of_capital": tot * 100,
                              "perp_return_pct": (opn.iloc[x][perp] / opn.iloc[e][perp] - 1) * 100})
    spans = {}
    for k in keys:
        a = int(np.searchsorted(days, ev[family(k[0])]))
        spans[k] = slice(a, len(days) - 1)
    return {"days": days, "spans": spans, "daily": daily, "mkt": mkt, "cost_paid": cost_paid,
            "turnover": turnover, "n_reb": n_reb, "flat_reb": flat_reb, "names_sig": names_sig,
            "flags": flags, "events": ev_log}


def flag(row: dict, n_events_s1: int | None = None) -> str:
    if n_events_s1 is not None and n_events_s1 < EV_MIN_S1:
        return "INSUFFICIENT_EVENTS (NOT_PROMISING)"
    s1 = row["S1"]["cal"]
    ok = (s1["sr_ann"] > row["null_max_sr_ann_N7"] and s1["alpha_t"] >= 2 and min(row["S1"]["halves_sr_ann"]) > 0
          and row["S2"]["cal"]["sr_ann"] > 0.5 and row["S1"]["cal_x2"]["sr_ann"] > 0
          and row["S1"]["cal_tail_x2"]["sr_ann"] > 0)
    return "DEV_PROMISING" if ok else "NOT_PROMISING"


def moments(x: np.ndarray) -> tuple:
    x = np.asarray(x, float)
    sd = x.std(ddof=1)
    if not sd > 0:
        return 0.0, 0.0, 3.0
    z = (x - x.mean()) / sd
    return float(x.mean() / sd), float(np.mean(z ** 3)), float(np.mean(z ** 4))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--out", type=Path, default=EVID / "newsrc_r1_dev.json")
    a = ap.parse_args(argv)
    if a.fetch:
        return fetch_all(a.data_dir, a.workers)
    cal = bu.load_cost_calibration(wu.CALIBRATION)
    cal = {**cal["calib"], "maxp": cal["maxp"]}
    first_spot = first_spot_days(a.data_dir)
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "helper_sha256": {n: bu.sha(HERE / n) for n in ("broad_universe_dev.py", "wide_universe_dev.py",
                                                           "cross_section_v2_dev.py")},
           "calibration_sha256": bu.sha(wu.CALIBRATION), "segments": {}, "trials": {}, "events": {}}
    segs = {s: run_segment(a.data_dir, s, cal, first_spot) for s in SEGMENTS}
    prior = prior_trials()
    for s, r in segs.items():
        out["segments"][s] = {"first_day": day_iso(int(r["days"][0])), "last_day": day_iso(int(r["days"][-2])),
                              "flags": r["flags"]}
    for k in TRIAL_KEYS:
        h, u = k
        row = {}
        for s, r in segs.items():
            sp = r["spans"][k]
            mk = r["mkt"][sp]
            row[s] = {c: bu.stats(v[sp], mk) for c, v in r["daily"][k].items()}
            x = r["daily"][k]["cal"][sp]
            half = len(x) // 2
            row[s]["halves_sr_ann"] = [bu.stats(x[:half], mk[:half])["sr_ann"], bu.stats(x[half:], mk[half:])["sr_ann"]]
            row[s]["eval_from"] = day_iso(int(r["days"][sp.start]))
            row[s]["years"] = len(x) / 365.0
            row[s]["cost_paid_pct_of_capital"] = r["cost_paid"][k] * 100
            if k in XS_KEYS:
                row[s]["rebalances"] = r["n_reb"][k]
                row[s]["flat_rebalances"] = r["flat_reb"][k]
                row[s]["mean_names_with_signal"] = float(np.mean(r["names_sig"][k])) if r["names_sig"][k] else 0.0
                row[s]["mean_turnover_per_rebalance"] = float(np.mean(r["turnover"][k])) if r["turnover"][k] else 0.0
            else:
                evl = r["events"][k]
                row[s]["n_events"] = len(evl)
                pr = [e["perp_return_pct"] for e in evl]
                row[s]["perp_return_mean_pct"] = float(np.mean(pr)) if pr else None
                row[s]["perp_return_median_pct"] = float(np.median(pr)) if pr else None
                row[s]["short_hit_rate"] = float(np.mean([v < 0 for v in pr])) if pr else None
                out["events"][f"{s}:{h}"] = evl
        y1 = row["S1"]["years"]
        row["null_max_sr_ann_N7"] = expected_max_sr(N_TRIALS) / math.sqrt(y1)
        row["null_max_sr_ann_cumulative"] = expected_max_sr(prior + N_TRIALS) / math.sqrt(y1)
        row["flag"] = flag(row, row["S1"].get("n_events") if k in EV_KEYS else None)
        out["trials"][f"{u}:{h}"] = row
    srs = {k: moments(segs["S1"]["daily"][k]["cal"][segs["S1"]["spans"][k]]) for k in TRIAL_KEYS}
    var_sr = float(np.var([v[0] for v in srs.values()], ddof=1))
    for k, (sr, sk, ku) in srs.items():
        n = segs["S1"]["spans"][k].stop - segs["S1"]["spans"][k].start
        row = out["trials"][f"{k[1]}:{k[0]}"]
        row["dsr_N7"] = deflated_sharpe(sr, n, sk, ku, N_TRIALS, var_sr)
        row["dsr_cumulative"] = deflated_sharpe(sr, n, sk, ku, prior + N_TRIALS, var_sr)
    common = max(segs["S1"]["spans"][k].start for k in XS_KEYS)
    stop = segs["S1"]["spans"][XS_KEYS[0]].stop
    mat = np.column_stack([segs["S1"]["daily"][k]["cal"][common:stop] for k in XS_KEYS])
    pbo, ncomb = pbo_cscv(mat.tolist(), n_partitions=16)
    out["multiple_testing"] = {"n_trials_here": N_TRIALS, "prior_trials_on_main": prior, "cumulative": prior + N_TRIALS,
                               "cross_trial_var_sr_daily": var_sr, "pbo_cscv_16_xs5": pbo, "pbo_combinations": ncomb,
                               "pbo_span": [day_iso(int(segs["S1"]["days"][common])), day_iso(int(segs["S1"]["days"][stop - 1]))]}
    z = wu.NormalDist().inv_cdf
    out["power"] = {f: {"s1_years": out["trials"][key]["S1"]["years"],
                        "min_detectable_annual_sr_80pct_bonf7": (z(1 - 0.05 / N_TRIALS) + z(0.8)) / math.sqrt(out["trials"][key]["S1"]["years"])}
                    for f, key in (("P", "U50:P1_HIGH180"), ("X", "U50:V1_PERPSPOT_LEVEL"))}
    out["s1_corr_common_span"] = {"keys": [f"{u}:{h}" for h, u in XS_KEYS], "corr": np.corrcoef(mat.T).round(3).tolist()}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("prior", prior, "PBO", round(pbo, 3), "power",
          {f: round(v["min_detectable_annual_sr_80pct_bonf7"], 2) for f, v in out["power"].items()})
    for key, row in out["trials"].items():
        line = [key, row["flag"], f"null7 {row['null_max_sr_ann_N7']:.2f} nullcum {row['null_max_sr_ann_cumulative']:.2f}"]
        for s in SEGMENTS:
            for c in ("cal", "cal_x2", "cost0"):
                st = row[s][c]
                line.append(f"{s}-{c} SR {st['sr_ann']:.2f} ret {st['ann_return_pct']:.1f}% dd {st['max_dd_pct']:.0f}% at {st['alpha_t']:.2f}")
            extra = row[s].get("n_events")
            line.append(f"{s} halves {[round(v, 2) for v in row[s]['halves_sr_ann']]} cost {row[s]['cost_paid_pct_of_capital']:.1f}%"
                        + (f" events {extra} mean {row[s]['perp_return_mean_pct']} hit {row[s]['short_hit_rate']}" if extra is not None
                           else f" names {row[s]['mean_names_with_signal']:.0f} to {row[s]['mean_turnover_per_rebalance']:.2f}"))
        line.append(f"DSR7 {row['dsr_N7']:.3f} DSRcum {row['dsr_cumulative']:.3f}")
        print(" | ".join(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
