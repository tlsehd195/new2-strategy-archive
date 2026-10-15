#!/usr/bin/env python3
# C3 데이터 수집 1 — Coinbase Exchange candles 15m (BTC-USD, ETH-USD)
# PREREG_c3_coinbase_premium_2026-10-13.md §2 규격. HTTP 실측 로그 저장.
import json, time, urllib.request, urllib.error, os

OUT_DIR = "/home/z/my-project/NEW2_run/data/coinbase"
LOG = "/home/z/my-project/NEW2_run/research/c3_coinbase_download_log.json"
os.makedirs(OUT_DIR, exist_ok=True)

START = "2021-04-01T00:00:00Z"   # 프로젝트 표준 측정 창(선행 14건과 동일)
END = "2026-10-13T00:00:00Z"
STEP = 270000  # 75시간 = 300 x 15m (API 최대 300캔들/요청)

def get(url, tries=5):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research-c3/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep([2, 5, 10, 20, 30][min(a, 4)]); continue
            return e.code, ""
        except Exception:
            time.sleep([2, 5, 10, 20, 30][min(a, 4)])
    return 0, ""

def fetch_product(product):
    rows = {}
    log = {"product": product, "requests": 0, "http_200": 0, "http_429": 0, "http_5xx": 0,
           "http_other": 0, "fail_chunks": [], "candles": 0}
    t0 = int(time.mktime(time.strptime(START, "%Y-%m-%dT%H:%M:%SZ"))) - time.timezone
    t1 = int(time.mktime(time.strptime(END, "%Y-%m-%dT%H:%M:%SZ"))) - time.timezone
    s = t0
    while s < t1:
        e = min(s + STEP, t1)
        url = (f"https://api.exchange.coinbase.com/products/{product}/candles"
               f"?granularity=900&start={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(s))}"
               f"&end={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(e))}")
        code, body = get(url)
        log["requests"] += 1
        if code == 200:
            log["http_200"] += 1
            try:
                arr = json.loads(body)
                for row in arr:  # [time, low, high, open, close, volume] — 최신순 응답
                    rows[row[0]] = (row[3], row[4], row[5])  # time -> (open, close, volume)
            except Exception:
                log["fail_chunks"].append([s, "parse"])
        elif code == 429:
            log["http_429"] += 1; log["fail_chunks"].append([s, "429"])
        elif code >= 500:
            log["http_5xx"] += 1; log["fail_chunks"].append([s, "5xx"])
        else:
            log["http_other"] += 1; log["fail_chunks"].append([s, str(code)])
        s = e
        time.sleep(0.12)
    ks = sorted(rows)
    with open(f"{OUT_DIR}/{product}-15m.csv", "w") as f:
        f.write("open_time,open,close,volume\n")
        for k in ks:
            o, c, v = rows[k]
            f.write(f"{k},{o},{c},{v}\n")
    log["candles"] = len(ks)
    log["first"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(ks[0])) if ks else None
    log["last"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(ks[-1])) if ks else None
    return log

def main():
    logs = []
    for p in ["BTC-USD", "ETH-USD"]:
        lg = fetch_product(p)
        logs.append(lg)
        print(json.dumps(lg, ensure_ascii=False))
    with open(LOG, "w") as f:
        json.dump({"start": START, "end": END, "products": logs}, f, indent=1)

if __name__ == "__main__":
    main()
