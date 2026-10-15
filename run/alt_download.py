#!/usr/bin/env python3
# Task 33 ALT-P1 데이터 다운로드 — data.binance.vision 월별 zip (KP 선례 우회 경로)
# 범위: BNB/XRP/SOL/TRX — 2021-04..2022-09 (s1+워밍업), 2024-11..2026-09 (s2+워밍업)
# klines 15m + fundingRate. 16자리(µs) 타임스탬프 보정(KP 선례). 재개 가능(기존 파일 스킵).
import io, json, os, time, zipfile, urllib.request

BASE = "/home/z/my-project/NEW2_run/data"
LOG = "/home/z/my-project/NEW2_run/research/alt_download_log.json"
SYMS = ["BNBUSDT", "XRPUSDT", "SOLUSDT", "TRXUSDT"]
MONTHS1 = [f"2021-{m:02d}" for m in range(4, 13)] + [f"2022-{m:02d}" for m in range(1, 10)]
MONTHS2 = [f"2024-{m:02d}" for m in range(11, 13)] + [f"2025-{m:02d}" for m in range(1, 13)] + [f"2026-{m:02d}" for m in range(1, 10)]
KURL = "https://data.binance.vision/data/futures/um/monthly/klines/{s}/15m/{s}-15m-{ym}.zip"
FURL = "https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{ym}.zip"

def fetch(url, retries=3):
    for a in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except Exception as e:
            if a == retries - 1:
                print(f"  FAIL {url}: {e}")
                return None
            time.sleep(2 * (a + 1))

log = {"files": [], "failed": [], "ts_fix": {}}
t0 = time.time()
for sym in SYMS:
    os.makedirs(f"{BASE}/klines", exist_ok=True)
    os.makedirs(f"{BASE}/funding", exist_ok=True)
    for ym in MONTHS1 + MONTHS2:
        for kind, url, path in (
            ("kline", KURL.format(s=sym, ym=ym), f"{BASE}/klines/{sym}-15m-{ym}.csv"),
            ("funding", FURL.format(s=sym, ym=ym), f"{BASE}/funding/{sym}-fundingRate-{ym}.csv"),
        ):
            if os.path.exists(path):
                continue
            blob = fetch(url)
            if blob is None:
                log["failed"].append(f"{kind}/{sym}/{ym}")
                continue
            try:
                zf = zipfile.ZipFile(io.BytesIO(blob))
                name = zf.namelist()[0]
                raw = zf.read(name)
                # 헤더 행 감지(KLINE_OPEN_TIME 문자열 헤더 — 최근 아카이브 변경분)
                first = raw[:64].decode("utf-8", "ignore")
                if first.upper().startswith("OPEN_TIME") or first.upper().startswith("KLINE"):
                    raw = b"\n".join(raw.split(b"\n")[1:])
                with open(path, "wb") as f:
                    f.write(raw)
                log["files"].append({"kind": kind, "sym": sym, "ym": ym, "bytes": len(blob)})
                if kind == "kline" and ym in ("2026-08", "2021-04"):
                    ts0 = raw.split(b"\n")[0].split(b",")[0].decode()
                    log["ts_fix"][f"{sym}-{ym}"] = ts0
            except Exception as e:
                log["failed"].append(f"{kind}/{sym}/{ym}:unzip:{e}")
    print(f"{sym} done {time.time()-t0:.0f}s files={len(log['files'])} failed={len(log['failed'])}")

with open(LOG, "w") as f:
    json.dump(log, f, indent=1)
print(json.dumps({"files": len(log["files"]), "failed": log["failed"][:20], "ts_samples": log["ts_fix"]}, indent=1))
