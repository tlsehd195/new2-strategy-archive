#!/usr/bin/env python3
# X2 이벤트 데이터 준비 — FOMC 성명서 날짜 + CPI 발표 날짜를 UTC 타임스탬프로 확정.
# 소스(실측):
#  - FOMC: federalreserve.gov/monetarypolicy/fomccalendars.htm (HTTP 200 실측, 성명서 PDF 파일명 monetaryYYYYMMDDa1 = 실제 성명일)
#  - CPI:  github.com/abusadat/CPI-release-dates cpi_releases.csv (2010~2025, 08:30 AM; 유명일 5건 교차검증 일치)
#          + 2025 셧다운 보정(웹 실측): 9월 CPI 실발표 2025-10-24, 10월 CPI 미발표(제외), 11월 CPI 2025-12-18
#          + FRED release calendar(웹 실측): 2026-01-13, 2026-02-13, 2026-03-11, 2026-04-10
# 규칙: 검증 불가한 스케줄 날짜는 배제(fail-closed). 시간은 14:00 ET(FOMC), 08:30 ET(CPI) → zoneinfo로 UTC 변환(DST 자동).
import csv, json, re
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
RES = "/home/z/my-project/NEW2_run/research"

def et_to_utc_ms(d: str, hhmm: str) -> int:
    dt = datetime.strptime(f"{d} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    return int(dt.timestamp() * 1000)

# --- FOMC: 성명서 PDF 파일명에서 실제 성명일 추출 (이미 다운로드된 HTML 사용) ---
html = open(f"{RES}/x2_fomc_calendar.html", encoding="utf-8", errors="ignore").read()
fomc_dates = sorted(set(re.findall(r"monetary(\d{8})a1\.pdf", html)))
assert len(fomc_dates) >= 40, f"FOMC parse fail: {len(fomc_dates)}"
fomc = [{"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}", "ts_utc": et_to_utc_ms(f"{d[:4]}-{d[4:6]}-{d[6:8]}", "14:00"),
         "src": "federalreserve.gov statement PDF filename"} for d in fomc_dates]

# --- CPI: CSV(2010~2025 스케줄) + 셧다운/2026 실측 보정 ---
cpi = []
with open(f"{RES}/x2_cpi_releases_2010_2025.csv", newline="") as fp:
    for row in csv.DictReader(fp):
        m = re.search(r"([A-Z][a-z]+) (\d{1,2}), (\d{4})", row["Date"])
        mo = {"January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
              "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12}[m.group(1)]
        d = f"{m.group(3)}-{mo:02d}-{int(m.group(2)):02d}"
        assert row["Time"] == "08:30 AM", f"unexpected CPI time {row['Time']}"
        cpi.append({"date": d, "ts_utc": et_to_utc_ms(d, "08:30"),
                    "src": "github abusadat/CPI-release-dates (BLS schedule)",
                    "covers": row["Release"]})
# 스케줄→실측 보정 (검증 출처 기록)
overrides = {
    "2025-10-15": ("2025-10-24", "셧다운 지연: 9월 CPI 실발표 2025-10-24 (웹 다중 출처 실측)"),
    "2025-12-10": ("2025-12-18", "셧다운 후 수정 일정: 11월 CPI 실발표 2025-12-18 08:30 ET (웹 다중 출처 실측)"),
}
dropped = []
fixed = []
for r in cpi:
    if r["date"] in overrides:
        nd, why = overrides[r["date"]]
        fixed.append({"old": r["date"], "new": nd, "why": why})
        r["date"], r["ts_utc"] = nd, et_to_utc_ms(nd, "08:30")
        r["src"] += " | " + why
# 2025-11-13(10월 CPI)는 미발표 → 제외
cpi = [r for r in cpi if r["date"] != "2025-11-13"]
dropped.append({"date": "2025-11-13", "why": "10월 CPI 미발표(셧다운 수집 중단, 11-18일 보도 실측) — 제외"})
# 2026 실측 확정분 (FRED release calendar 웹 실측 + CPI Summary 기사 실측)
for d, cov, why in [("2026-01-13", "December 2025", "Dec 22 2025 기사 'available January 13, 2026' 실측"),
                    ("2026-02-13", "January 2026", "FRED release calendar 실측 (Friday February 13, 2026)"),
                    ("2026-03-11", "February 2026", "FRED release calendar 실측 (Wednesday March 11, 2026)"),
                    ("2026-04-10", "March 2026", "FRED release calendar + CPI Summary(April 10, 2026) 실측")]:
    cpi.append({"date": d, "ts_utc": et_to_utc_ms(d, "08:30"), "src": "FRED release calendar (웹 실측)", "covers": cov})
cpi.sort(key=lambda r: r["ts_utc"])

out = {"fomc": fomc, "cpi": cpi, "quality": {
    "fomc_n": len(fomc), "fomc_years": sorted({r["date"][:4] for r in fomc}),
    "cpi_n": len(cpi), "cpi_range": [cpi[0]["date"], cpi[-1]["date"]],
    "schedule_overrides": fixed, "dropped": dropped,
    "note": "모든 시각 ET→UTC zoneinfo 변환(서머타임 자동). 검증 불가 날짜 배제 원칙 적용"}}
with open(f"{RES}/x2_macro_events.json", "w") as fp:
    json.dump(out, fp, indent=1)
print("FOMC:", len(fomc), "| CPI:", len(cpi))
print("CPI 2025-05~:", [r["date"] for r in cpi if r["date"] >= "2025-05-01"])
print("overrides:", [(x["old"], x["new"]) for x in fixed], "| dropped:", dropped)
print("fomc 2026:", [r["date"] for r in fomc if r["date"].startswith("2026")])
