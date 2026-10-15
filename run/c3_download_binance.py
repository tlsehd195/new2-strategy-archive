#!/usr/bin/env python3
# C3 데이터 수집 2 — Binance spot 15m 전체(2021-04~2026-10) + perp 15m 백필(잠금 제외 월)
# PREREG_c3_coinbase_premium_2026-10-13.md §2 규격. 2025+ 아카이브 microsecond 타임스탬프 -> ms 정규화.
import io, json, os, time, zipfile, urllib.request, urllib.error

KL = "/home/z/my-project/NEW2_run/data/klines"
SP = "/home/z/my-project/NEW2_run/data/spot15m"
LOG = "/home/z/my-project/NEW2_run/research/c3_binance_download_log.json"
os.makedirs(SP, exist_ok=True)

COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qv", "n", "tbv", "tbqv", "ig"]

def dl(url, tries=4):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research-c3/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep([2, 5, 10, 20][min(a, 3)]); continue
            return e.code, b""
        except Exception:
            time.sleep([2, 5, 10, 20][min(a, 3)])
    return 0, b""

def ts_to_ms(vals):
    v0 = float(vals[0])
    if v0 > 1e14:  # microseconds
        return [str(int(float(x) // 1000)) for x in vals]
    return [str(int(float(x))) for x in vals]

def save_zip_csv(data, out_path):
    zf = zipfile.ZipFile(io.BytesIO(data))
    name = zf.namelist()[0]
    raw = zf.read(name).decode()
    lines = raw.strip().split("\n")
    out = []
    for ln in lines:
        p = ln.split(",")
        if len(p) < 12: continue
        try:
            float(p[0])
        except ValueError:
            continue  # 헤더 행 스킵 (신규 아카이브 일부에 헤더 포함)
        p[0] = ts_to_ms([p[0]])[0]  # ms 정규화
        out.append(",".join(p[:12]))
    with open(out_path, "w") as f:
        f.write("\n".join(out) + "\n")
    return len(out)

def months(y0, m0, y1, m1):
    out, y, m = [], y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y}-{m:02d}"); m += 1
        if m == 13: y, m = y + 1, 1
    return out

def days(y, m, d0, d1):
    import calendar
    return [f"{y}-{m:02d}-{d:02d}" for d in range(d0, min(d1, calendar.monthrange(y, m)[1]) + 1)]

def main():
    log = {"spot": [], "perp": []}
    # 1) spot 15m: 2021-04~2026-09 monthly + 2026-10-01~12 daily (BTC/ETH)
    for sym in ["BTCUSDT", "ETHUSDT"]:
        for ym in months(2021, 4, 2026, 9) + days(2026, 10, 1, 12):
            kind = "monthly" if len(ym) == 7 else "daily"
            out = f"{SP}/{sym}-15m-{ym}.csv"
            if os.path.exists(out):
                log["spot"].append({"f": f"{sym}-{ym}", "skip": "exists"}); continue
            url = f"https://data.binance.vision/data/spot/{kind}/klines/{sym}/15m/{sym}-15m-{ym}.zip"
            code, data = dl(url)
            if code == 200 and data:
                n = save_zip_csv(data, out)
                log["spot"].append({"f": f"{sym}-{ym}", "http": 200, "rows": n})
            else:
                log["spot"].append({"f": f"{sym}-{ym}", "http": code, "note": "no-data"})
            time.sleep(0.15)
    # 2) perp 15m 백필: BTC 2024-06~2026-09 (잠금 2024-03-25~06-18 제외 월 스킵), ETH 2026-09 (잠금 2026-05~08 스킵)
    #    + 2026-10-01~12 daily 양 심볼
    perp_plan = [("BTCUSDT", months(2024, 6, 2026, 9)), ("ETHUSDT", months(2026, 9, 2026, 9))]
    for sym, mlist in perp_plan:
        for ym in mlist + days(2026, 10, 1, 12):
            kind = "monthly" if len(ym) == 7 else "daily"
            out = f"{KL}/{sym}-15m-{ym}.csv"
            if os.path.exists(out):
                log["perp"].append({"f": f"{sym}-{ym}", "skip": "exists"}); continue
            url = f"https://data.binance.vision/data/futures/um/{kind}/klines/{sym}/15m/{sym}-15m-{ym}.zip"
            code, data = dl(url)
            if code == 200 and data:
                n = save_zip_csv(data, out)
                log["perp"].append({"f": f"{sym}-{ym}", "http": 200, "rows": n})
            else:
                log["perp"].append({"f": f"{sym}-{ym}", "http": code, "note": "no-data"})
            time.sleep(0.15)
    with open(LOG, "w") as f:
        json.dump(log, f, indent=1)
    bad = [x for x in log["spot"] + log["perp"] if x.get("http") not in (200, None)]
    print(f"spot files: {len([x for x in log['spot'] if x.get('http') == 200])}, perp files: {len([x for x in log['perp'] if x.get('http') == 200])}, bad: {len(bad)}")
    for b in bad[:10]: print(" BAD:", b)

if __name__ == "__main__":
    main()
