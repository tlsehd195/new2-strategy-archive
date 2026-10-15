#!/usr/bin/env python3
"""Task 31 — F2/X6-R 조기 점검: F&G 재다운로드 후 잠금 외 신규 구간 극단공포 이벤트 확인.
기준 (Task 29 패턴 준용): ETH 잠금 구간 2026-04-28~08-31 제외, B80<=20 극단공포 일수.
"""
import json, urllib.request, datetime as dt

URL = "https://api.alternative.me/fng/?limit=0&format=json"
LOCK_ETH = (dt.date(2026, 4, 28), dt.date(2026, 8, 31))
# 신규 관측 구간: ETH 잠금 종료 다음날 ~ 최신 (Task 29 점검 대비 확장)
NEW_FROM = dt.date(2026, 9, 1)

req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.loads(r.read().decode())

rows = data["data"]  # 최신 우선
out = {"n_total": len(rows), "latest": [], "extreme_new_window": []}
oldest = None
for row in rows:
    ts = int(row["timestamp"])
    d = dt.date.fromtimestamp(ts, dt.timezone.utc).date() if ts > 10**12 else dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()
    v = int(row["value"])
    if oldest is None or d < oldest:
        oldest = d
    out["latest"].append((str(d), v))
    if d >= NEW_FROM and v <= 20:
        out["extreme_new_window"].append({"date": str(d), "value": v, "in_lock": LOCK_ETH[0] <= d <= LOCK_ETH[1]})

out["oldest"] = str(oldest)
out["latest"] = out["latest"][:5]  # 최근 5일만
out["extreme_count_new_window"] = len(out["extreme_new_window"])

with open("/home/z/my-project/NEW2_run/research/fng_refresh_task31.json", "w") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)

print(json.dumps({k: out[k] for k in ["n_total", "oldest", "latest", "extreme_count_new_window", "extreme_new_window"]}, indent=2, ensure_ascii=False))
