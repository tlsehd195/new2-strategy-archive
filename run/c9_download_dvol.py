#!/usr/bin/env python3
# C9 Deribit DVOL 일간 시리즈 다운로드 — PREREG_c9_dvol_2026-10-13.md §2 규격.
# resolution=1D(현재 API 유일 실측 지원 해상도), 월간 청크, 재개 가능, 결측·중복 검증, HTTP 실측 로그.
import json, os, time, urllib.request, urllib.error

BASE = "/home/z/my-project/NEW2_run"
OUT_DIR = f"{BASE}/data/dvol"
LOG_PATH = f"{BASE}/research/c9_dvol_download_log.json"
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

END_MS = 1791763200000  # 2026-10-12 00:00 UTC (측정 종점, 사전 고정) — 초안 1760227200000은 2025-10-12(환산 오류, 측정 전 정정)
START_MS_CAND = 1612137600000  # 2021-02-01 (예상 시작 전 여유 — 공백 응답은 정상 기록)
URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data?currency={cur}&start_timestamp={s}&end_timestamp={e}&resolution=1D"

def fetch(cur, s_ms, e_ms, attempt=1):
    url = URL.format(cur=cur, s=s_ms, e=e_ms)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 research"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
            return r.status, body
    except urllib.error.HTTPError as e:
        if attempt <= 3:
            time.sleep(1.5 * attempt)
            return fetch(cur, s_ms, e_ms, attempt + 1)
        return e.code, None
    except Exception as e:
        if attempt <= 3:
            time.sleep(1.5 * attempt)
            return fetch(cur, s_ms, e_ms, attempt + 1)
        return -1, str(e)

def month_chunks(s_ms, e_ms):
    import datetime
    cur = s_ms
    while cur < e_ms:
        y, m = time.gmtime(cur // 1000)[:2]
        if m == 12:
            first_next = datetime.datetime(y + 1, 1, 1, tzinfo=datetime.timezone.utc)
        else:
            first_next = datetime.datetime(y, m + 1, 1, tzinfo=datetime.timezone.utc)
        nxt = int(first_next.timestamp() * 1000)
        yield cur, min(nxt, e_ms)
        cur = min(nxt, e_ms)

log = {"endpoint": URL, "resolution": "1D", "end_ms": END_MS, "currencies": {}}
for cur in ("BTC", "ETH"):
    rows = {}
    http_log = []
    for s_ms, e_ms in month_chunks(START_MS_CAND, END_MS):
        code, body = fetch(cur, s_ms, e_ms)
        n = 0
        if code == 200 and body and "result" in body:
            data = body["result"].get("data", [])
            for r in data:
                # r = [ts, open, high, low, close]
                rows[int(r[0])] = [float(r[1]), float(r[2]), float(r[3]), float(r[4])]
            n = len(data)
            cont = body["result"].get("continuation")
            if cont:
                http_log.append({"s": s_ms, "e": e_ms, "http": code, "rows": n, "continuation": True})
        http_log.append({"s": s_ms, "e": e_ms, "http": code, "rows": n})
        time.sleep(0.25)
    ts_sorted = sorted(rows)
    # 결측 검증: 첫~마지막 일자 연속성
    gaps = []
    if ts_sorted:
        DAY = 86_400_000
        for a, b in zip(ts_sorted, ts_sorted[1:]):
            if b - a != DAY:
                gaps.append({"from": a, "to": b, "days": (b - a) // DAY - 1})
    csv_path = f"{OUT_DIR}/c9_dvol_{cur}_1d.csv"
    with open(csv_path, "w") as f:
        f.write("ts_ms,open,high,low,close\n")
        for t in ts_sorted:
            o, h, l, c = rows[t]
            f.write(f"{t},{o},{h},{l},{c}\n")
    first_iso = time.strftime("%Y-%m-%d", time.gmtime(ts_sorted[0] // 1000)) if ts_sorted else None
    last_iso = time.strftime("%Y-%m-%d", time.gmtime(ts_sorted[-1] // 1000)) if ts_sorted else None
    log["currencies"][cur] = {
        "rows": len(ts_sorted), "first": first_iso, "last": last_iso,
        "gaps": gaps, "http_200": sum(1 for x in http_log if x["http"] == 200),
        "http_other": [x for x in http_log if x["http"] != 200],
        "csv": csv_path,
    }
    print(f"{cur}: rows={len(ts_sorted)} {first_iso}~{last_iso} gaps={len(gaps)} http200={log['currencies'][cur]['http_200']}")

with open(LOG_PATH, "w") as f:
    json.dump(log, f, indent=1)
print("saved:", LOG_PATH)
