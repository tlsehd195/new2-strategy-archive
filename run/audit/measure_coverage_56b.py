#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task 56-b: 데이터 커버리지 실측 (복원 전/후 동일 스크립트 사용)."""
import os, sys, calendar, json
BASE = "/home/z/my-project/NEW2_run/data"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
        "ADAUSDT", "DOGEUSDT", "LINKUSDT", "TRXUSDT", "ZECUSDT"]
# 월 → ms
def month_ms(y, m):
    return (y * 12 + (m - 1))  # 월 인덱스
def idx_to_ym(i):
    return (i // 12, i % 12 + 1)
def fname_ym(fn, prefix, ext):
    # prefix-YYYY-MM.ext
    try:
        if not fn.startswith(prefix) or not fn.endswith(ext): return None
        ym = fn[len(prefix):-len(ext)]
        parts = ym.split("-")
        if len(parts) != 2: return None
        return int(parts[0]) * 12 + int(parts[1]) - 1
    except Exception:
        return None

def days_in(mi):
    y, m = idx_to_ym(mi)
    return calendar.monthrange(y, m)[1]

def scan(dirpath, prefix, ext):
    """returns dict: month_idx -> list of (fname, size, nrows_or_None)"""
    found = {}
    zips = {}
    if not os.path.isdir(dirpath):
        return found, zips
    for fn in sorted(os.listdir(dirpath)):
        mi = fname_ym(fn, prefix, ext)
        if mi is not None:
            p = os.path.join(dirpath, fn)
            found.setdefault(mi, []).append((fn, os.path.getsize(p)))
        elif fn.endswith(".zip"):
            mi2 = fname_ym(fn, prefix, ".zip")
            if mi2 is not None:
                zips.setdefault(mi2, []).append(fn)
    return found, zips

def measure():
    report = {}
    for sym in SYMS:
        kl, klzip = scan(f"{BASE}/klines", f"{sym}-15m-", ".csv")
        fd, _ = scan(f"{BASE}/funding", f"{sym}-fundingRate-", ".csv")
        def summarize(d, zips, kind):
            if not d and not zips:
                return {"n": 0, "span": None, "missing": [], "zip_only": [],
                        "suspicious": []}
            mis = sorted(set(d) | set(zips))
            if not mis:
                return {"n": 0, "span": None, "missing": [], "zip_only": [],
                        "suspicious": []}
            lo, hi = mis[0], mis[-1]
            missing = [i for i in range(lo, hi + 1) if i not in d and i not in zips]
            zip_only = [i for i in range(lo, hi + 1) if i not in d and i in zips]
            susp = []
            for i, lst in d.items():
                for fn, sz in lst:
                    if sz < 500:  # 빈/잘림 의심 (klines 최소 2688행*few MB, funding 최소 ~30행)
                        susp.append({"file": fn, "size": sz})
            return {"n": len(d), "nzip": len(zips), "span": [idx_to_ym(lo), idx_to_ym(hi)],
                    "missing": [idx_to_ym(i) for i in missing],
                    "zip_only": [idx_to_ym(i) for i in zip_only],
                    "suspicious": susp}
        r = {"klines": summarize(kl, klzip, "kl"), "funding": summarize(fd, {}, "fd")}
        report[sym] = r
    return report

if __name__ == "__main__":
    rep = measure()
    tag = sys.argv[1] if len(sys.argv) > 1 else "before"
    out = f"/home/z/my-project/NEW2_run/audit/coverage_56b_{tag}.json"
    json.dump(rep, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"=== COVERAGE ({tag}) saved -> {out} ===")
    for sym, r in rep.items():
        for kind in ("klines", "funding"):
            d = r[kind]
            span = f"{d['span'][0][0]}-{d['span'][0][1]:02d} ~ {d['span'][1][0]}-{d['span'][1][1]:02d}" if d["span"] else "-"
            miss = ",".join(f"{y}-{m:02d}" for y, m in d["missing"]) or "-"
            zo = ",".join(f"{y}-{m:02d}" for y, m in d["zip_only"]) or "-"
            susp = ",".join(x["file"] for x in d["suspicious"]) or "-"
            print(f"[{sym}] {kind}: n={d['n']} zip={d.get('nzip',0)} span={span}")
            print(f"    missing={miss}")
            if zo != "-": print(f"    zip_only={zo}")
            if susp != "-": print(f"    SUSPICIOUS={susp}")
