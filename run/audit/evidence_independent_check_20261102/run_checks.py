"""Driver for the 2026-11-02 independent pipeline re-check (DEV only, cost 0).

usage: python3 run_checks.py <raw_dir> <out_json>
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import independent_engine as E  # noqa: E402

DAY = E.DAY


def quality(panel, qlog):
    o, h, l, c, v = (panel[k] for k in ("open", "high", "low", "close", "volume"))
    seg_mask = pd.Series(E.allowed(pd.Series(c.index)).values, index=c.index)
    cc = c / c.shift(1) - 1
    # a gap day between consecutive rows of the same symbol inside a segment
    present = c.notna()
    first = present.idxmax()
    gaps = 0
    gap_list = []
    for s in c.columns:
        col = present[s]
        for (a, b, _) in (E.S1, E.S2):
            x = col.loc[a:b]
            if x.sum() == 0:
                continue
            fi, la = x.idxmax(), x[::-1].idxmax()
            inner = x.loc[fi:la]
            g = int((~inner).sum())
            if g:
                gaps += g
                gap_list.append((s, str(fi.date()), str(la.date()), g))
    ohlc_bad = int(((l > o.combine(c, np.minimum) + 1e-12) | (h < o.combine(c, np.maximum) - 1e-12) | (l > h)).sum().sum())
    nonpos = int(((o <= 0) | (c <= 0) | (l <= 0)).sum().sum())
    big = (cc.abs() > 0.5) & cc.notna()
    big_list = [(s, str(d.date()), round(float(cc.loc[d, s]), 3)) for d, s in zip(*np.where(big)) for d, s in [(c.index[d], c.columns[s])]]
    gap_open = (o / c.shift(1) - 1).abs()
    gap_open_n = int((gap_open > 0.05).sum().sum())
    gap_open_list = sorted([(s, str(c.index[i].date()), round(float(gap_open.iloc[i, j]), 3))
                            for i, j in zip(*np.where(gap_open > 0.05)) for s in [c.columns[j]]], key=lambda x: -x[2])[:15]
    zero_vol = int(((v == 0) & c.notna()).sum().sum())
    fund = panel["funding"]
    fund_abs = fund.abs()
    fund_big = int((fund_abs > 0.03).sum().sum())
    held_but_no_fund = int((c.notna() & fund.isna()).sum().sum())
    return {
        "symbols_loaded": int(c.shape[1]),
        "rows": int(present.sum().sum()),
        "duplicate_rows_dropped": int(qlog["dup"].sum()),
        "open_time_not_midnight": int(qlog["off_midnight"].sum()),
        "interior_missing_days": gaps,
        "interior_missing_examples": gap_list[:15],
        "ohlc_inconsistent": ohlc_bad,
        "nonpositive_price": nonpos,
        "abs_daily_return_gt_50pct": len(big_list),
        "abs_daily_return_gt_50pct_examples": sorted(big_list, key=lambda x: -abs(x[2]))[:15],
        "open_vs_prev_close_gap_gt_5pct": gap_open_n,
        "open_vs_prev_close_gap_examples": gap_open_list,
        "zero_volume_days_with_price": zero_vol,
        "funding_abs_daily_gt_3pct": fund_big,
        "price_days_without_funding_row": held_but_no_fund,
    }


CANDS = {
    "H1_MOM28_U50_w": (E.Spec("mom28", 7, True, "U50", drift=False), E.sig_mom(28), 0.59, "ADR 0035 cost0x"),
    "N4_TAKER_U50_28": (E.Spec("taker", 28, True, "U50", drift=True), E.sig_taker, 0.61, "ADR 0040 cost0"),
    "A101_U50_w": (E.Spec("a101", 7, True, "U50", drift=True), E.sig_a101, 0.65, "ADR 0054 cost0"),
    "R4_RESMAX_W200_28": (E.Spec("resmax", 28, False, "W200", drift=True), E.sig_resmax, 1.36, "ADR 0051 cost0"),
}


def main():
    raw, out = sys.argv[1], sys.argv[2]
    cache = os.path.join(raw, "panel.pkl")
    if os.path.exists(cache):
        panel, qlog = pickle.load(open(cache, "rb"))
        full = pd.date_range(E.S1[0], E.S2[1], freq="D")
        panel = {k: v.reindex(full) for k, v in panel.items()}
    else:
        panel, qlog = E.load_panel(raw)
        pickle.dump((panel, qlog), open(cache, "wb"))
    res = {"quality": quality(panel, qlog)}
    print("quality done", flush=True)

    # deterministic: return construction (BTC, ETH, S1 from 2021-03-01 open to 2023-09-17 close)
    det = {}
    for s in ("BTCUSDT", "ETHUSDT"):
        c, o = panel["close"][s], panel["open"][s]
        a, b = pd.Timestamp("2021-03-01"), pd.Timestamp("2023-09-17")
        r = pd.concat([pd.Series([c[a] / o[a] - 1], index=[a]), (c / c.shift(1) - 1).loc[a + DAY:b]])
        det[s] = {"prod_minus_ratio": float(np.prod(1 + r) - c[b] / o[a]), "price_ratio": float(c[b] / o[a]),
                  "open": float(o[a]), "close": float(c[b])}
    res["deterministic_buy_hold"] = det

    cand = {}
    for name, (spec, fn, old, src) in CANDS.items():
        t0 = time.time()
        row = {"old_s1_cost0": old, "old_source": src}
        for seg_name, seg in (("S1", E.S1), ("S2", E.S2)):
            s, meta = E.run(panel, spec, seg, fn)
            row[seg_name] = E.stats(s) | meta
            spec_nf = E.Spec(**{**spec.__dict__, "funding": False})
            s_nf, _ = E.run(panel, spec_nf, seg, fn)
            row[seg_name + "_no_funding"] = E.stats(s_nf)
            if seg_name == "S1":
                # sign flip check
                spec_f = E.Spec(**{**spec.__dict__, "funding": False, "long_high": not spec.long_high})
                s_f, _ = E.run(panel, spec_f, seg, fn)
                row["S1_signflip_no_funding"] = E.stats(s_f)
                row["S1_signflip_sum_err"] = float((s_f.reindex(s_nf.index) + s_nf).abs().max()) if not spec.drift else None
                # lag sensitivity
                for lag in (1, 3):
                    sp = E.Spec(**{**spec.__dict__, "lag": lag})
                    s_l, _ = E.run(panel, sp, seg, fn)
                    row[f"S1_lag{lag}"] = E.stats(s_l)
                # oracle (future information) to check alignment
                s_o, _ = E.run(panel, E.Spec(**{**spec.__dict__, "long_high": True}), seg, E.sig_oracle)
                row["S1_oracle"] = E.stats(s_o)
        row["diff_s1"] = row["S1"]["sr_ann"] - old
        cand[name] = row
        print(name, round(row["S1"]["sr_ann"], 3), "old", old, f"{time.time()-t0:.0f}s", flush=True)
    res["candidates"] = cand

    # random signals (same universe and weights machinery)
    rnd = {}
    for name in CANDS:
        spec, fn, _, _ = CANDS[name]
        rng = np.random.default_rng(20261102)
        vals = []
        for _ in range(200):
            s, _ = E.run(panel, spec, E.S1, fn, rng=rng)
            vals.append(E.stats(s)["sr_ann"])
        v = np.array(vals)
        rnd[name] = {"n": len(v), "mean": float(v.mean()), "sd": float(v.std(ddof=1)),
                     "p95": float(np.quantile(v, 0.95)), "max": float(v.max()),
                     "share_ge_candidate": float((v >= cand[name]["S1"]["sr_ann"]).mean())}
        print("random", name, rnd[name], flush=True)
    res["random_signals_S1"] = rnd

    # leak check: momentum through the entry-day close
    s_leak, _ = E.run(panel, E.Spec("leak", 7, True, "U50", drift=False), E.S1, E.sig_leak(28))
    res["leak_mom28_through_entry_close_S1"] = E.stats(s_leak)

    # literature: cross-sectional momentum 7/14/21/28, weekly
    lit = {}
    for L in (7, 14, 21, 28):
        for seg_name, seg in (("S1", E.S1), ("S2", E.S2)):
            s, _ = E.run(panel, E.Spec(f"mom{L}", 7, True, "U50", drift=False, funding=False), seg, E.sig_mom(L))
            lit[f"CSMOM{L}_{seg_name}"] = E.stats(s)
    # time-series momentum on BTC and ETH (S1; ETH also S2), sign of 28d return, weekly, 1x
    for sym in ("BTCUSDT", "ETHUSDT"):
        for seg_name, seg in (("S1", E.S1), ("S2", E.S2)):
            if sym == "BTCUSDT" and seg_name == "S2":
                continue
            c, o = panel["close"][sym], panel["open"][sym]
            cc = c / c.shift(1) - 1
            start, end, first = seg
            d = [x for x in pd.date_range(first, end) if x.weekday() == 0][0]
            rets = []
            idx = []
            while d + 6 * DAY <= end:
                cut = d - 2 * DAY
                pos = np.sign(c[cut] / c[cut - 28 * DAY] - 1)
                for j in range(7):
                    dd = d + j * DAY
                    r = c[dd] / o[dd] - 1 if j == 0 else cc[dd]
                    rets.append(pos * r)
                    idx.append(dd)
                d += 7 * DAY
            s = pd.Series(rets, index=idx)
            bh = pd.Series([c[x] / o[x] - 1 if i % 7 == 0 else cc[x] for i, x in enumerate(idx)], index=idx)
            lit[f"TSMOM28_{sym}_{seg_name}"] = E.stats(s) | {"buy_hold_sr": E.stats(bh)["sr_ann"]}
    res["literature"] = lit
    json.dump(res, open(out, "w"), indent=1, default=str)
    print("written", out)


if __name__ == "__main__":
    main()
