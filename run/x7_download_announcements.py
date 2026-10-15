#!/usr/bin/env python3
# X7 데이터 수집 — Binance 공지 카탈로그 48 "New Cryptocurrency Listing" 전체 페이징.
# HTTP 실측 기록(상태코드/재시도), 실패 시 부분 저장 + 실패 사실 기록(정직).
import json, time, os, urllib.request, urllib.error

OUT = "/home/z/my-project/NEW2_run/research/x7_listing_announcements.json"
URL = "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query?type=1&pageNo={p}&pageSize={s}&catalogId=48"
HDRS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json", "Cache-Control": "no-cache", "clientType": "web"}

def fetch(page, size, tries=4):
    url = URL.format(p=page, s=size)
    last = None
    for t in range(tries):
        try:
            req = urllib.request.Request(url, headers=HDRS)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode("utf-8"))
                return r.status, body
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (429, 418):  # 레이트리밋 — 지수 백오프
                time.sleep(5 * (2 ** t)); continue
            time.sleep(2)
        except Exception as e:
            last = str(e); time.sleep(2)
    return None, last

def main():
    log = {"endpoint": URL.format(p=1, s=50), "pages": [], "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # 1페이지로 pageSize 수용 확인 — 응답은 catalogs[0].articles 형(E1 실측)
    st, body = fetch(1, 50)
    def parse(body):
        d = body.get("data") or {}
        a = d.get("articles")
        if a is None:
            cats = d.get("catalogs") or [{}]
            a = (cats[0].get("articles") or []) if cats else []
            total = int(cats[0].get("total") or 0) if cats else 0
            return a, total
        return a, int(d.get("total") or 0)
    if st is None:
        log["fatal"] = f"page1 fail: {body}"
        json.dump(log, open(OUT, "w"), ensure_ascii=False, indent=1)
        print("FATAL:", log); return
    arts, total = parse(body)
    size = 50
    log["pages"].append({"page": 1, "status": st, "articles": len(arts)})
    all_arts = list(arts)
    page = 2
    max_pages = (total + size - 1) // size + 5
    empty_streak = 0
    while page <= max_pages:
        st, body = fetch(page, size)
        if st is None:
            log["pages"].append({"page": page, "status": f"FAIL {body}"})
            break
        a, _ = parse(body)
        log["pages"].append({"page": page, "status": st, "articles": len(a)})
        if not a:
            empty_streak += 1
            if empty_streak >= 2:
                break
        else:
            empty_streak = 0
            all_arts.extend(a)
        if len(all_arts) >= total and total > 0:
            break
        page += 1
        time.sleep(0.4)
    ids = {x.get("id") for x in all_arts}
    dedup = [x for x in all_arts]
    seen, uniq = set(), []
    for x in all_arts:
        k = x.get("id")
        if k not in seen:
            seen.add(k); uniq.append(x)
    dates = sorted(x.get("releaseDate") for x in uniq if x.get("releaseDate"))
    log["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    log["catalog_total_declared"] = total
    log["articles_fetched"] = len(all_arts)
    log["articles_unique"] = len(uniq)
    if dates:
        log["date_range"] = [time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(dates[0]/1000)),
                             time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(dates[-1]/1000))]
    out = {"meta": log, "articles": uniq}
    json.dump(out, open(OUT, "w"), ensure_ascii=False)
    print(json.dumps(log, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
