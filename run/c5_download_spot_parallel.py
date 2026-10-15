#!/usr/bin/env python3
# 스팟 1h 병렬 다운로드 — api.binance.com은 fapi와 레이트리밋 분리. 재시도 강화.
import json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone

BASE = "/home/z/my-project/NEW2_run/data"
os.makedirs(f"{BASE}/spot1h", exist_ok=True)

def http_json(url, retries=8):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read()), None
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                wait = 70 if e.code == 418 else 20
                print(f"  ratelimit {e.code}, wait {wait}s", flush=True)
                time.sleep(wait)
                continue
            return None, f"HTTP {e.code}"
        except Exception as e:
            if i == retries - 1:
                return None, str(e)
            time.sleep(2.0)
    return None, "retries exhausted"

def ms_utc(s):
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

def ymd(ms_):
    return datetime.fromtimestamp(ms_ / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

for sym in ("BTCUSDT", "ETHUSDT"):
    out_path = f"{BASE}/spot1h/{sym}-1h.csv"
    if os.path.exists(out_path):
        print(f"SKIP spot {sym}: exists", flush=True)
        continue
    start, end = ms_utc("2021-01-01"), ms_utc("2026-07-01")
    rows = []
    while start < end:
        url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1h&startTime={start}&endTime={end}&limit=1500"
        data, err = http_json(url)
        if data is None:
            print(f"DL spot {sym}: FAIL {err} at {ymd(start)}", flush=True)
            break
        if not data:
            break
        rows.extend(data)
        start = int(data[-1][0]) + 3600000
        time.sleep(0.4)
    with open(out_path, "w") as f:
        for r in rows:
            f.write(",".join(str(x) for x in r[:12]) + "\n")
    print(f"DL spot {sym}: rows={len(rows)} {ymd(rows[0][0])}~{ymd(rows[-1][0])}", flush=True)
print("SPOT DONE", flush=True)
