#!/usr/bin/env python3
# C5 데이터 수집 v2 — 418/429 재시도 강화 + 느린 페이싱 + 재개 가능. HTTP 실측 기록 필수.
import json, os, time, calendar, urllib.request, urllib.error
from datetime import datetime, timezone

BASE = "/home/z/my-project/NEW2_run/data"
os.makedirs(f"{BASE}/qfut", exist_ok=True)
os.makedirs(f"{BASE}/spot1h", exist_ok=True)
LOG = []

def http_json(url, retries=6):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read()), None
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                wait = 70 if e.code == 418 else 20
                LOG.append(f"RATELIMIT {e.code} wait {wait}s url={url[:80]}")
                print(f"  ratelimit {e.code}, wait {wait}s", flush=True)
                time.sleep(wait)
                continue
            return None, f"HTTP {e.code}"
        except Exception as e:
            if i == retries - 1:
                return None, str(e)
            time.sleep(2.0)
    return None, "retries exhausted"

def quarter_expiry(y, m):
    last = calendar.monthrange(y, m)[1]
    for d in range(last, last - 7, -1):
        if datetime(y, m, d).weekday() == 4:
            return f"{y % 100:02d}{m:02d}{d:02d}"
    raise RuntimeError("no friday")

def ms_utc(s):
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

def ymd(ms_):
    return datetime.fromtimestamp(ms_ / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

CAND_DATES = [quarter_expiry(y, m) for y in (2021, 2022, 2023, 2024, 2025, 2026) for m in (3, 6, 9, 12)]
print("candidate expiries:", CAND_DATES, flush=True)

# 1) probe (결과 캐시 파일로 재개 지원)
probe_cache_path = f"{BASE}/qfut/probe_results.json"
probe = {}
if os.path.exists(probe_cache_path):
    probe = json.load(open(probe_cache_path))
for base_sym in ("BTCUSDT", "ETHUSDT"):
    for d in CAND_DATES:
        sym = f"{base_sym}_{d}"
        if sym in probe:
            continue
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&limit=1&startTime=0"
        data, err = http_json(url)
        time.sleep(0.4)
        if data and len(data):
            probe[sym] = {"ok": True, "first": ymd(int(data[0][0]))}
            print(f"PROBE {sym}: OK first={ymd(int(data[0][0]))}", flush=True)
        else:
            probe[sym] = {"ok": False, "err": err or "empty"}
            print(f"PROBE {sym}: {err or 'empty'}", flush=True)
        json.dump(probe, open(probe_cache_path, "w"), indent=1)
        LOG.append(f"PROBE {sym}: {probe[sym]}")

# 2) 납품계약 1h 전체 다운로드 (delivery ≥ 2021-02-01)
CUT = ms_utc("2021-02-01")
for base_sym in ("BTCUSDT", "ETHUSDT"):
    for d in CAND_DATES:
        sym = f"{base_sym}_{d}"
        if not probe.get(sym, {}).get("ok"):
            continue
        expiry_ms = ms_utc(f"20{d[:2]}-{d[2:4]}-{d[4:6]}")
        if expiry_ms < CUT:
            LOG.append(f"SKIP {sym}: expiry before 2021-02")
            continue
        out_path = f"{BASE}/qfut/{sym}-1h.csv"
        if os.path.exists(out_path):
            LOG.append(f"SKIP {sym}: file exists")
            continue
        start = ms_utc("2021-01-01")
        rows = []
        while True:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&startTime={start}&limit=1500"
            data, err = http_json(url)
            if data is None:
                LOG.append(f"DL {sym}: FAIL {err} at start={ymd(start)}")
                break
            if not data:
                break
            rows.extend(data)
            if len(data) < 1500:
                break
            start = int(data[-1][0]) + 3600000
            time.sleep(0.4)
        with open(out_path, "w") as f:
            for r in rows:
                f.write(",".join(str(x) for x in r[:12]) + "\n")
        rng = f"{ymd(rows[0][0])}~{ymd(rows[-1][0])}" if rows else "empty"
        LOG.append(f"DL {sym}: 200 OK rows={len(rows)} range={rng}")
        print(f"DL {sym}: rows={len(rows)} {rng}", flush=True)
        time.sleep(0.4)

# 3) 스팟 1h 다운로드 (2021-01-01 ~ 2026-07-01)
for sym in ("BTCUSDT", "ETHUSDT"):
    out_path = f"{BASE}/spot1h/{sym}-1h.csv"
    if os.path.exists(out_path):
        LOG.append(f"SKIP spot {sym}: file exists")
        continue
    start, end = ms_utc("2021-01-01"), ms_utc("2026-07-01")
    rows = []
    while start < end:
        url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1h&startTime={start}&endTime={end}&limit=1500"
        data, err = http_json(url)
        if data is None:
            LOG.append(f"DL spot {sym}: FAIL {err} at start={ymd(start)}")
            break
        if not data:
            break
        rows.extend(data)
        start = int(data[-1][0]) + 3600000
        time.sleep(0.4)
    with open(out_path, "w") as f:
        for r in rows:
            f.write(",".join(str(x) for x in r[:12]) + "\n")
    LOG.append(f"DL spot {sym}: rows={len(rows)} range={ymd(rows[0][0])}~{ymd(rows[-1][0])}")
    print(f"DL spot {sym}: rows={len(rows)} {ymd(rows[0][0])}~{ymd(rows[-1][0])}", flush=True)

with open(f"{BASE}/qfut/download_log.txt", "w") as f:
    f.write("\n".join(LOG) + "\n")
print("DONE. log -> data/qfut/download_log.txt", flush=True)
