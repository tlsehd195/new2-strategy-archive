#!/usr/bin/env python3
"""Round 15c, DEV only (ADR 0113): Fabio Valentini style auction-market rules, volume-profile approximation.

VP1 New York imbalance (close beyond the previous day's value area with a volume
spike and taker-flow in the same direction, exit back inside / 1 VA width / 20:00 UTC)
and VP2 London reversion (back inside the value area after trading outside it,
target POC, stop outside again, exit 12:00 UTC).  Order flow is approximated: no
tick data.  Runs exactly the 2 trials of
``run/audit/evidence_newsrc_r15_20261120/prereg_valentini_vp_r15c_v1.json`` through
the ADR 0045 engine (``fast_short_dev.simulate``) unchanged.

    python3 run/valentini_vp_r15c_dev.py --data-dir <dir with klines/ and funding/>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))
import broad_universe_dev as bu  # noqa: E402
import fast_short_dev as fs  # noqa: E402
from engine_lib import deflated_sharpe, expected_max_sr  # noqa: E402

VERSION = "valentini-vp-r15c-dev-v1"
EVID = HERE / "audit" / "evidence_newsrc_r15_20261120"
PREREG = EVID / "prereg_valentini_vp_r15c_v1.json"
TRIALS = HERE / "audit" / "dev_search_trials.jsonl"
CONTEXT_ID = "ALT15-15m-valentini-vp-r15c-v1"
CONFIGS = ["VP1_NY_IMBALANCE", "VP2_LONDON_REVERSION"]
BD = fs.BARS_DAY
NBINS, VA_SHARE, VOL_MULT, VOL_LB = 24, 0.70, 2.0, 20


def profile(price: np.ndarray, vol: np.ndarray):
    """(POC mid, VAL, VAH) from one day of bars; None if < 90 valid bars or no range."""
    ok = np.isfinite(price) & np.isfinite(vol) & (vol >= 0)
    if ok.sum() < 90:
        return None
    p, v = price[ok], vol[ok]
    lo, hi = float(p.min()), float(p.max())
    if not hi > lo or not v.sum() > 0:
        return None
    w = (hi - lo) / NBINS
    b = np.minimum(((p - lo) / w).astype(int), NBINS - 1)
    h = np.bincount(b, weights=v, minlength=NBINS)
    poc = int(np.argmax(h))
    a = z = poc
    tot = h[poc]
    while tot < VA_SHARE * h.sum():
        up = h[z + 1] if z + 1 < NBINS else -1.0
        dn = h[a - 1] if a - 1 >= 0 else -1.0
        if up >= dn:
            z += 1
            tot += up
        else:
            a -= 1
            tot += dn
    return lo + (poc + 0.5) * w, lo + a * w, lo + (z + 1) * w


def events_for(cfg: str, seg: dict) -> list:
    arr = seg["arr"]
    op, cl, qv, tb = arr["open"], arr["close"], arr["qv"], arr["tbq"]
    mid = (op + cl) / 2.0
    ev = []
    for k in range(len(fs.SYMBOLS)):
        qmean = np.full(seg["nb"], np.nan)
        cq = np.concatenate([[0.0], np.cumsum(np.nan_to_num(qv[:, k]))])
        qmean[VOL_LB:] = (cq[VOL_LB:-1] - cq[:-VOL_LB - 1]) / VOL_LB
        for d in range(1, seg["n_days"]):
            pr = profile(mid[(d - 1) * BD: d * BD, k], qv[(d - 1) * BD: d * BD, k])
            if pr is None:
                continue
            poc, val, vah = pr
            base = d * BD
            if cfg == "VP1_NY_IMBALANCE":
                bars, end = range(base + 54, base + 66), base + 80
            else:
                bars, end = range(base + 28, base + 40), base + 48
            for j in bars:
                c, q, t = cl[j, k], qv[j, k], tb[j, k]
                if not (np.isfinite(c) and q > 0 and np.isfinite(t)):
                    continue
                share = t / q
                side = 0.0
                if cfg == "VP1_NY_IMBALANCE":
                    if q > VOL_MULT * qmean[j]:
                        side = 1.0 if (c > vah and share > 0.55) else (-1.0 if (c < val and share < 0.45) else 0.0)
                else:
                    if val <= c <= vah:
                        prev = cl[base: j, k]
                        if np.nanmax(prev, initial=-np.inf) > vah and share < 0.5:
                            side = -1.0
                        elif np.nanmin(prev, initial=np.inf) < val and share > 0.5:
                            side = 1.0
                if side == 0.0:
                    continue
                e, x = j + 1, end
                for b in range(e, end):
                    cb = cl[b, k]
                    if not np.isfinite(cb):
                        continue
                    if cfg == "VP1_NY_IMBALANCE":
                        width = vah - val
                        hit = (cb <= vah or cb >= vah + width) if side > 0 else (cb >= val or cb <= val - width)
                    else:
                        hit = (cb >= poc or cb < val) if side > 0 else (cb <= poc or cb > vah)
                    if hit:
                        x = b + 1
                        break
                if x - e >= 1:
                    ev.append((int(j), int(k), float(side), 1.0, int(x - e)))
                break  # first trigger per coin per day
    ev.sort(key=lambda e: (e[0], -e[3], fs.SYMBOLS[e[1]]))
    return ev


def prior_trials() -> int:
    rows = [json.loads(ln) for ln in TRIALS.read_text().splitlines() if ln.strip()]
    return sum(r.get("dev_evaluated", 0) for r in rows if r.get("context_id") != CONTEXT_ID)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=EVID / "valentini_vp_r15c_dev.json")
    a = ap.parse_args(argv)
    costs = {"measured": bu.load_cost_record(fs.COST_RECORD), "assumed": bu.load_cost(),
             "volume_scaled": bu.load_cost_calibration(fs.CALIBRATION)}
    out = {"version": VERSION, "prereg_sha256": bu.sha(PREREG), "script_sha256": bu.sha(Path(__file__)),
           "engine_sha256": bu.sha(HERE / "fast_short_dev.py"), "configs": {}, "segments": {}}
    segs = {}
    for s in fs.SEGMENTS:
        seg = fs.load_segment(a.data_dir, s)
        arr = seg["arr"]
        qf = pd.DataFrame(arr["qv"]).where(lambda d: d > 0).ffill(limit=fs.GAP_FILL_BARS).values
        q30 = pd.DataFrame(arr["qv"]).rolling(fs.Q30_BARS, min_periods=480).mean().values
        fund_cum = [(ts, np.concatenate([[0.0], np.cumsum(rate)])) for ts, rate in seg["fund"]]
        ctx = {"qf": qf, "q30": q30, "fund_cum": fund_cum, "fns": fs.make_cost_fns(costs)}
        op = arr["open"]
        ref = np.array([op[min((d + 1) * BD, seg["nb"] - 1)] / op[d * BD] - 1.0 for d in range(seg["n_days"])])
        mkt = np.array([np.nanmean(r) if np.isfinite(r).any() else 0.0 for r in ref])
        runs = {}
        for cfg in CONFIGS:
            res = fs.simulate(events_for(cfg, seg), seg, ctx)
            res["gross_d"], res["cost_d"] = fs.daily_series(res["trades"], seg)
            runs[cfg] = res
        segs[s] = {"seg": seg, "mkt": mkt, "start_day": seg["start_bar"] // BD, "runs": runs}
        out["segments"][s] = {"n_days": seg["n_days"], "first_entry_day": seg["start_bar"] // BD}
    y1 = (segs["S1"]["seg"]["n_days"] - segs["S1"]["start_day"]) / 365.0
    n = len(CONFIGS)
    prior = prior_trials()
    null = expected_max_sr(n) / math.sqrt(y1)
    out["null_max_sr_ann"] = {f"N={n}": null, f"N={prior + n} cumulative": expected_max_sr(prior + n) / math.sqrt(y1)}
    s1_series = {}
    for cfg in CONFIGS:
        row = {}
        for s, r in segs.items():
            run, sd, mk = r["runs"][cfg], r["start_day"], r["mkt"]
            sl = slice(sd, None)
            d = {c: bu.stats((run["gross_d"] + run["cost_d"][c])[sl], mk[sl]) for c in fs.COST_ROWS}
            net = (run["gross_d"] + run["cost_d"]["measured"])[sl]
            half = len(net) // 2
            d["halves_sr_ann"] = [bu.stats(net[:half], mk[sl][:half])["sr_ann"], bu.stats(net[half:], mk[sl][half:])["sr_ann"]]
            d["trades"] = fs.trade_stats(run["trades"])
            d["skipped"] = {k: run[k] for k in ("skipped_capacity", "skipped_invalid", "participation_over_max")}
            row[s] = d
            if s == "S1":
                s1_series[cfg] = net
        row["flag"] = fs.flag(row, null)
        out["configs"][cfg] = row
    srs = {c: ne_moments(x) for c, x in s1_series.items()}
    var_sr = float(np.var([v[0] for v in srs.values()], ddof=1))
    for c, (sr, sk, ku) in srs.items():
        L = len(s1_series[c])
        out["configs"][c][f"dsr_N{n}"] = deflated_sharpe(sr, L, sk, ku, n, var_sr)
        out["configs"][c]["dsr_cumulative"] = deflated_sharpe(sr, L, sk, ku, prior + n, var_sr)
    out["multiple_testing"] = {"n_trials_here": n, "prior_trials": prior, "cumulative": prior + n}
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print("null", {k: round(v, 2) for k, v in out["null_max_sr_ann"].items()})
    for cid, row in out["configs"].items():
        for s in fs.SEGMENTS:
            t = row[s]["trades"]
            print(cid, s, row["flag"], f"n={t.get('n', 0)} gross {t.get('mean_gross_bp', 0):.1f}bp cost {t.get('mean_cost_bp', 0):.1f}"
                  f" net {t.get('mean_net_bp', 0):.1f} hit {t.get('hit_rate', 0):.2f} SR {row[s]['measured']['sr_ann']:.2f}"
                  f" c0 {row[s]['cost0']['sr_ann']:.2f} vs {row[s]['volume_scaled']['sr_ann']:.2f} at {row[s]['measured']['alpha_t']:.2f}"
                  f" halves {[round(v, 2) for v in row[s]['halves_sr_ann']]}")
        print(cid, "DSRcum", round(row["dsr_cumulative"], 3))
    return 0


def ne_moments(x):
    x = np.asarray(x, float)
    sd = x.std(ddof=1)
    if not sd > 0:
        return 0.0, 0.0, 3.0
    z = (x - x.mean()) / sd
    return float(x.mean() / sd), float(np.mean(z ** 3)), float(np.mean(z ** 4))


if __name__ == "__main__":
    sys.exit(main())
