#!/usr/bin/env python3
"""Build the daily / funding / bar2345 inputs of run/dca_extreme_dev.py from the project's
15m alt archive (data/alts_2021_2026_binance_um_15m_funding.zip).  ADR 0088.

Only rows inside the DEV segments S1/S2 are written; rows of never-read ranges are
dropped while streaming and never aggregated or stored.

    python3 run/dca_extreme_prepare.py --zip <zip> --out <empty dir>
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402


def in_dev(t: int) -> bool:
    return any(bu.ms(lo) <= t <= bu.ms(hi) + bu.DAY_MS - 1 for lo, hi, _ in bu.SEGMENTS.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    for d in ("daily", "funding", "bar2345"):
        (a.out / d).mkdir(parents=True, exist_ok=False)
    days = defaultdict(dict)   # sym -> day_ms -> [o,h,l,c,v,qv,n_bars]
    b2345 = defaultdict(list)
    with zipfile.ZipFile(a.zip) as z:
        for name in sorted(z.namelist()):
            m = re.match(r"klines/([A-Z0-9]+)-15m-(\d{4}-\d{2})\.csv$", name)
            f = re.match(r"funding/([A-Z0-9]+)-fundingRate-(\d{4}-\d{2})\.csv$", name)
            if m:
                sym = m.group(1)
                for r in csv.reader(io.StringIO(z.read(name).decode())):
                    if not r or not r[0].isdigit():
                        continue
                    t = int(r[0])
                    if not in_dev(t):
                        continue
                    d = t - t % bu.DAY_MS
                    o, h, l, c, v, qv = (float(r[i]) for i in (1, 2, 3, 4, 5, 7))
                    x = days[sym].get(d)
                    if x is None:
                        days[sym][d] = [o, h, l, c, v, qv, 1, t]
                    else:
                        x[1], x[2] = max(x[1], h), min(x[2], l)
                        if t > x[7]:
                            x[3], x[7] = c, t
                        x[4] += v
                        x[5] += qv
                        x[6] += 1
                    if (t // 60_000) % 1440 == 23 * 60 + 45:
                        b2345[(sym, m.group(2))].append(f"{t},{r[7]}")
            elif f:
                rows = [r for r in csv.reader(io.StringIO(z.read(name).decode())) if r and r[0].isdigit() and in_dev(int(r[0]) - 1)]
                if rows:
                    (a.out / "funding" / f"{f.group(1)}-fundingRate-{f.group(2)}.csv").write_text(
                        "calc_time,interval,last_funding_rate\n" + "\n".join(",".join(r[:3]) for r in rows) + "\n")
    for sym, dd in days.items():
        lines = []
        for d in sorted(dd):
            o, h, l, c, v, qv, n, _ = dd[d]
            if n != 96:        # incomplete day (listing day or gap): not used
                continue
            lines.append(f"{d},{o},{h},{l},{c},{v},{d + bu.DAY_MS - 1},{qv}")
        (a.out / "daily" / f"{sym}-1d-all.csv").write_text("open_time,o,h,l,c,v,close_time,quote_volume\n" + "\n".join(lines) + "\n")
    for (sym, ym), rows in b2345.items():
        (a.out / "bar2345" / f"{sym}-2345-{ym}.csv").write_text("open_time,quote_volume\n" + "\n".join(rows) + "\n")
    print({s: len(d) for s, d in days.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
