#!/usr/bin/env python3
# C5 납품계약 데이터 — data.binance.vision 월간 아카이브 방식 (fapi 418 회피).
# probe_results.json의 유효 계약 대상. 기존 파일(fapi 완료분)은 스킵. 헤더 행 제거 + 정렬/중복 제거.
import json, os, io, time, zipfile, urllib.request, urllib.error
import pandas as pd

BASE = "/home/z/my-project/NEW2_run/data"
OUTDIR = f"{BASE}/qfut"
os.makedirs(OUTDIR, exist_ok=True)

def fetch(url, retries=4):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read(), None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, "404"
            time.sleep(2)
        except Exception as e:
            if i == retries - 1:
                return None, str(e)
            time.sleep(2)
    return None, "retries"

def month_range_for(expiry_ms):
    """만기 월부터 6개월 전까지 (상장이 만기 ~3개월 전이므로 충분, 404는 스킵)."""
    e = pd.Timestamp(expiry_ms, unit="ms", tz="UTC")
    months = []
    y, m = e.year, e.month
    for k in range(6):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(months))

probe = json.load(open(f"{OUTDIR}/probe_results.json"))
n_done, n_skip, n_fail = 0, 0, []
for sym, info in sorted(probe.items()):
    if not info.get("ok"):
        continue
    d = sym.split("_")[1]
    expiry_ms = int(pd.Timestamp(f"20{d[:2]}-{d[2:4]}-{d[4:6]}", tz="UTC").timestamp() * 1000)
    if expiry_ms < pd.Timestamp("2021-02-01", tz="UTC").timestamp() * 1000:
        continue
    out_path = f"{OUTDIR}/{sym}-1h.csv"
    if os.path.exists(out_path):
        n_skip += 1
        continue
    rows = []
    for ym in month_range_for(expiry_ms):
        url = f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1h/{sym}-1h-{ym}.zip"
        data, err = fetch(url)
        if data is None:
            continue  # 404(미상장 월) 등 → 정상 스킵
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            with z.open(z.namelist()[0]) as f:
                text = f.read().decode()
        for line in text.strip().split("\n"):
            parts = line.split(",")
            if not parts[0].isdigit():
                continue  # 헤더
            ts = int(parts[0])
            if ts > 10**14:  # 방어: 마이크로초 → 밀리초
                ts //= 1000
            rows.append([ts] + parts[1:12])
        time.sleep(0.2)
    if not rows:
        n_fail.append(sym)
        print(f"FAIL {sym}: no rows", flush=True)
        continue
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume",
                                     "close_time", "qv", "n", "tbv", "tbqv", "ig"])
    df = df.drop_duplicates("open_time").sort_values("open_time")
    df.to_csv(out_path, index=False, header=False)
    rng = f"{pd.Timestamp(df['open_time'].iloc[0], unit='ms', tz='UTC')}~{pd.Timestamp(df['open_time'].iloc[-1], unit='ms', tz='UTC')}"
    print(f"OK {sym}: rows={len(df)} {rng}", flush=True)
    n_done += 1
print(f"DONE done={n_done} skip={n_skip} fail={len(n_fail)} {n_fail}", flush=True)
