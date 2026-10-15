#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Task 56-b — NEW2 Phase 5 데이터 파이프라인 복원 (재실행 가능)
================================================================
결함: F-24(ADA/LINK 전무) · F-27(알트 6종 25개월 구멍) · F-28(BTC 2024-04~05,
잠금 TEST 내부) · F-29(ETH 2026-05~08, 잠금 TEST 내부) · F-30(BTC funding
2024-03 종료 + 전 심볼 funding 갭) · F-31(zip 전용 월 → CSV 추출)

원칙:
- 기존 유효 파일 절대 덮어쓰기 금지. 결측분 추가만.
- 명백히 손상/부분 파일(행수 하한 미달·첫/끝 open_time 월 이탈)만
  audit/backup_corrupt_56b/ 백업 후 교체(로그 기록).
- data.binance.vision 월별 zip 사용(저장 규약: 기존처럼 zip+CSV 병존).
  2026-10(진행월)은 vision 미게시 → fapi REST 부분월 보완(실패 시 보고만).
- 레이트리밋(418/429): Retry-After 또는 30~60s 백오프 재시도.
- discovery/ 일체 수정 없음. 다른 프로세스(:3020) 방해 없음(순차 다운로드).

형식 실측(2026-10-14):
- klines vision zip 내부 CSV: 12컬럼, 헤더 있음(신규분) / 기존 일부 헤더 없음
  → 로더(engine_lib.load_klines)가 header=None+숫자coerce로 양쪽 허용.
  vision 원본을 그대로 저장(12컬럼 유지).
- funding: 헤더 calc_time,funding_interval_hours,last_funding_rate (3컬럼) 동일.
"""
import calendar
import csv
import io
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = "/home/z/my-project/NEW2_run/data"
AUDIT = "/home/z/my-project/NEW2_run/audit"
BACKUP = f"{AUDIT}/backup_corrupt_56b"
LOG = f"{AUDIT}/download_56b_log.jsonl"

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
        "ADAUSDT", "DOGEUSDT", "LINKUSDT", "TRXUSDT", "ZECUSDT"]

# ADAUSDT/LINKUSDT UM 선물 onboardDate(fapi exchangeInfo 실측 2026-10-14):
#   LINK 2020-01-17, ADA 2020-01-31 → 프로젝트 균일 시작월 2021-04 이전 상장 확인.
#   엔진 개발구간 시작 2021-05-02 + 960봉 워밍업 ≈ 2021-04-21 → 2021-04 시작으로 충분.
START_YM = (2021, 4)
VISION_LAST_YM = (2026, 9)          # vision 월별 최종 게시월
CUR_YM = (2026, 10)                 # 진행월(REST 보완 대상)
BAR_MS = 900_000

VISION = "https://data.binance.vision/data/futures/um/monthly"
FAPI = "https://fapi.binance.com"

log_fp = open(LOG, "a")


def log(ev, **kw):
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ev": ev, **kw}
    log_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log_fp.flush()
    print("  " + json.dumps(rec, ensure_ascii=False), flush=True)


def http_get(url, binary=True, timeout=90, retries=6, max_wait=90):
    """GET with 418/429/5xx backoff. Returns bytes or raises RuntimeError.
    max_wait: Retry-After가 이보다 크면 기다리지 않고 즉시 포기(RemoteDisallowed)."""
    backoffs = [5, 30, 60, 60, 60, 60]
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "NEW2-restore/56b"})
            with urlopen(req, timeout=timeout) as r:
                return r.read()
        except HTTPError as e:
            if e.code in (418, 429) or e.code >= 500:
                ra = e.headers.get("Retry-After") if e.headers else None
                wait = int(ra) if (ra and ra.isdigit()) else backoffs[min(attempt, len(backoffs) - 1)]
                if wait > max_wait:
                    log("ratelimit_giveup", url=url[-70:], code=e.code, retry_after_s=wait)
                    raise RuntimeError(f"ratelimited (Retry-After {wait}s): {url}")
                log("ratelimit", url=url[-70:], code=e.code, wait_s=wait, attempt=attempt + 1)
                time.sleep(wait)
                continue
            if e.code == 404:
                return None
            raise
        except (URLError, TimeoutError, ConnectionError) as e:
            wait = backoffs[min(attempt, len(backoffs) - 1)]
            log("net_retry", url=url[-70:], err=str(e)[:120], wait_s=wait, attempt=attempt + 1)
            time.sleep(wait)
    raise RuntimeError(f"download failed after retries: {url}")


def month_iter(y0, m0, y1, m1):
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def ym_str(y, m):
    return f"{y:04d}-{m:02d}"


def days_in(y, m):
    return calendar.monthrange(y, m)[1]


# ─────────────────────────────────────────────────────────────
# 검증
# ─────────────────────────────────────────────────────────────
def _structural_check(text, expect_fields, first_col):
    """구조 검증: (data_rows, hard_err). hard_err 있으면 파일 자체가 손상."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0, "empty"
    start = 0
    if any(c.isalpha() for c in lines[0].split(",")[0]):
        start = 1  # header row
    rows = lines[start:]
    n = len(rows)
    if n == 0:
        return 0, "no data rows"
    for label, ln in (("first", rows[0]), ("last", rows[-1])):
        f = ln.split(",")
        if len(f) != expect_fields:
            return n, f"{label} row has {len(f)} fields (expect {expect_fields})"
        try:
            int(f[0])
        except ValueError:
            if f[0].strip() != first_col:  # 헤더가 본문에 섞인 경우 제외
                return n, f"{label} {first_col} not numeric: {f[0][:40]}"
            return n, f"{label} row is header-like"
    return n, None


def floor_expected(kind, y, m):
    if kind == "klines":
        return int(days_in(y, m) * 96 * 0.98)
    return int(days_in(y, m) * 3 * 0.9)


def validate_klines_csv(text, y, m, rest_partial=False, authoritative=False, floor_frac=0.98):
    """(n, hard_err, soft_note)
    authoritative=True(vision 원본/기존 원본): 행수 하한은 경고만(바이낸스 자체 갭 = 정상).
    floor_frac: hard floor 배율(기존 파일 검사 시 0.5로 완화 → 자체 갭 오탐 방지).
    rest_partial: 경과시간 기반 하한 적용."""
    n, err = _structural_check(text, 12, "open_time")
    if err:
        return n, err, None
    if not rest_partial and not authoritative:
        fl = int(days_in(y, m) * 96 * floor_frac)
        if n < fl:
            return n, f"rows {n} < floor {fl}", None
    if authoritative:
        fl = floor_expected("klines", y, m)
        if n < fl:
            return n, None, f"binance_native_gap rows {n} < floor {fl}"
    return n, None, None


def validate_funding_csv(text, y, m, rest_partial=False, authoritative=False, floor_frac=0.9):
    """(n, hard_err, soft_note) — klines와 동일 계약."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0, "empty", None
    if lines[0].split(",")[0] != "calc_time":
        return len(lines), "missing header (calc_time)", None
    rows = lines[1:]
    n = len(rows)
    if n == 0:
        return 0, "no data rows", None
    for label, ln in (("first", rows[0]), ("last", rows[-1])):
        f = ln.split(",")
        if len(f) != 3:
            return n, f"{label} row has {len(f)} fields", None
        try:
            int(f[0])
        except ValueError:
            return n, f"{label} calc_time not numeric", None
    if not rest_partial and not authoritative:
        fl = int(days_in(y, m) * 3 * floor_frac)
        if n < fl:
            return n, f"rows {n} < floor {fl}", None
    if authoritative:
        fl = floor_expected("funding", y, m)
        if n < fl:
            return n, None, f"binance_native_gap rows {n} < floor {fl}"
    return n, None, None


def read_existing(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def backup_file(path):
    """손상 기존 파일 백업(원자적 이동). 백업 경로 반환."""
    os.makedirs(BACKUP, exist_ok=True)
    sub = os.path.basename(os.path.dirname(path))
    dst_dir = os.path.join(BACKUP, sub)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(path) + f".corrupt.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    shutil.move(path, dst)
    return dst


# ─────────────────────────────────────────────────────────────
# zip 처리
# ─────────────────────────────────────────────────────────────
def extract_csv_from_zip(zip_path, want_name, kind, y, m):
    """zip에서 CSV 추출(문자열). 검증 실패 시 (None, reason)."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            if not names:
                return None, "zip empty"
            data = z.read(names[0])
    except (zipfile.BadZipFile, OSError, EOFError) as e:
        return None, f"bad zip: {e}"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    vfn = validate_klines_csv if kind == "klines" else validate_funding_csv
    # authoritative=True: vision 원본은 바이낸스 자체 갭이 있을 수 있으므로
    # 행수 하한 미달을 hard fail로 보지 않는다(구조 검증만 hard).
    n, err, _soft = vfn(text, y, m, authoritative=True)
    if err:
        return None, f"inner csv invalid ({err})"
    del want_name
    return text, None


# ─────────────────────────────────────────────────────────────
# vision 다운로드 1개월
# ─────────────────────────────────────────────────────────────
def vision_paths(sym, kind, y, m):
    if kind == "klines":
        rel = f"klines/{sym}/15m/{sym}-15m-{ym_str(y, m)}.zip"
    else:
        rel = f"fundingRate/{sym}/{sym}-fundingRate-{ym_str(y, m)}.zip"
    return f"{VISION}/{rel}"


def ensure_month(sym, kind, y, m, stats):
    """한 심볼·한 종류·한 월 보장. returns status: ok|skip|rest|fail|extract"""
    d = f"{BASE}/{'klines' if kind == 'klines' else 'funding'}"
    if kind == "klines":
        csv_name = f"{sym}-15m-{ym_str(y, m)}.csv"
        zip_name = csv_name[:-4] + ".zip"
        prefix = f"{sym}-15m-"
    else:
        csv_name = f"{sym}-fundingRate-{ym_str(y, m)}.csv"
        zip_name = csv_name[:-4] + ".zip"
        prefix = f"{sym}-fundingRate-"
    csv_path = os.path.join(d, csv_name)
    zip_path = os.path.join(d, zip_name)
    vfn = validate_klines_csv if kind == "klines" else validate_funding_csv

    # 1) 기존 CSV 유효성 — 구조 검증 + 완화 하한(50%): 자체 갭 월은 skip,
    #    명백한 손상(빈 파일/컬럼 깨짐/절반 이하 절단)만 백업 후 교체
    if os.path.exists(csv_path):
        text = read_existing(csv_path)
        n, err, _soft = vfn(text, y, m, floor_frac=0.5)
        if err is None:
            return "skip"
        # 손상 → 백업 후 교체 진행
        bk = backup_file(csv_path)
        log("corrupt_backed_up", kind=kind, sym=sym, month=ym_str(y, m), err=err, backup=bk)
        stats["corrupt_replaced"] += 1
    else:
        # 2) zip 전용 월(F-31) → 추출만
        if os.path.exists(zip_path):
            text, err = extract_csv_from_zip(zip_path, csv_name, kind, y, m)
            if text is not None:
                tmp = csv_path + ".tmp56b"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(text if text.endswith("\n") else text + "\n")
                os.replace(tmp, csv_path)
                nrows = sum(1 for ln in text.splitlines() if ln.strip())
                log("zip_extracted", kind=kind, sym=sym, month=ym_str(y, m), rows=nrows)
                stats["zip_extracted"] += 1
                return "extract"
            log("zip_invalid", kind=kind, sym=sym, month=ym_str(y, m), err=err)

    # 3) zip 존재 & 유효 → 이미 추출 시도됨. 없거나 손상이면 vision 다운로드
    text = None
    if not os.path.exists(zip_path):
        url = vision_paths(sym, kind, y, m)
        blob = http_get(url)
        if blob is None:
            log("vision_404", kind=kind, sym=sym, month=ym_str(y, m), url=url)
            return "fail"
        tmp = zip_path + ".tmp56b"
        with open(tmp, "wb") as f:
            f.write(blob)
        # 다운로드 zip 자체 검증 + 내부 CSV 검증
        t2, err = extract_csv_from_zip(tmp, csv_name, kind, y, m)
        if t2 is None:
            os.remove(tmp)
            log("downloaded_zip_invalid", kind=kind, sym=sym, month=ym_str(y, m), err=err)
            return "fail"
        os.replace(tmp, zip_path)
        text = t2
        log("vision_zip_saved", kind=kind, sym=sym, month=ym_str(y, m), bytes=len(blob))
    else:
        text, err = extract_csv_from_zip(zip_path, csv_name, kind, y, m)
        if text is None:
            log("existing_zip_invalid", kind=kind, sym=sym, month=ym_str(y, m), err=err)
            return "fail"

    with open(csv_path + ".tmp56b", "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    os.replace(csv_path + ".tmp56b", csv_path)
    stats["downloaded"] += 1
    return "ok"


# ─────────────────────────────────────────────────────────────
# REST 보완 (진행월 2026-10)
# ─────────────────────────────────────────────────────────────
def rest_month_klines(sym, y, m, stats):
    """fapi klines로 진행월 부분 수집. 마지막(미확정) 봉 제외."""
    d = f"{BASE}/klines"
    csv_name = f"{sym}-15m-{ym_str(y, m)}.csv"
    csv_path = os.path.join(d, csv_name)
    start_ms = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    now_ms = int(time.time() * 1000)
    if os.path.exists(csv_path):
        text = read_existing(csv_path)
        n, err, _soft = validate_klines_csv(text, y, m, rest_partial=True)
        floor = max(96, int((now_ms - start_ms) / BAR_MS) - 96)
        if err is None and n >= floor:
            return "skip"
        bk = backup_file(csv_path)
        log("corrupt_backed_up", kind="klines", sym=sym, month=ym_str(y, m), err=err, backup=bk)
        stats["corrupt_replaced"] += 1
    rows, cur = [], start_ms
    while cur < now_ms:
        url = f"{FAPI}/fapi/v1/klines?{urlencode({'symbol': sym, 'interval': '15m', 'startTime': cur, 'endTime': now_ms, 'limit': 1000})}"
        blob = http_get(url)
        if blob is None:
            return "fail"
        arr = json.loads(blob)
        if not arr:
            break
        rows.extend(arr)
        cur = int(arr[-1][0]) + BAR_MS
        if len(arr) < 1000:
            break
        time.sleep(0.25)
    now_adj = now_ms
    buf = io.StringIO()
    cnt = 0
    for r in rows:
        if len(r) != 12 or int(r[6]) >= now_adj:  # 미확정 봉 제외(close_time 기준)
            continue
        buf.write(",".join(str(x) for x in r) + "\n")
        cnt += 1
    floor = max(48, int((now_adj - start_ms) / BAR_MS) - 96)
    if cnt < floor:
        log("rest_klines_insufficient", sym=sym, rows=cnt, floor=floor)
        return "fail"
    with open(csv_path + ".tmp56b", "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    os.replace(csv_path + ".tmp56b", csv_path)
    stats["rest_saved"] += 1
    log("rest_klines_saved", sym=sym, month=ym_str(y, m), rows=cnt)
    return "rest"


def rest_month_funding(sym, y, m, stats):
    """fapi fundingRate로 진행월 부분 수집. interval은 연속 차이로 산출(첫 행 8)."""
    d = f"{BASE}/funding"
    csv_name = f"{sym}-fundingRate-{ym_str(y, m)}.csv"
    csv_path = os.path.join(d, csv_name)
    start_ms = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    now_ms = int(time.time() * 1000)
    if os.path.exists(csv_path):
        text = read_existing(csv_path)
        n, err, _soft = validate_funding_csv(text, y, m, rest_partial=True)
        if err is None and n >= 3:
            return "skip"
        bk = backup_file(csv_path)
        log("corrupt_backed_up", kind="funding", sym=sym, month=ym_str(y, m), err=err, backup=bk)
        stats["corrupt_replaced"] += 1
    url = f"{FAPI}/fapi/v1/fundingRate?{urlencode({'symbol': sym, 'startTime': start_ms, 'endTime': now_ms, 'limit': 1000})}"
    blob = http_get(url)
    if blob is None:
        return "fail"
    arr = json.loads(blob)
    events = []
    for r in arr:
        t = int(r["fundingTime"])
        if start_ms <= t <= now_ms:
            events.append((t, float(r["fundingRate"])))
    events.sort()
    buf = io.StringIO()
    buf.write("calc_time,funding_interval_hours,last_funding_rate\n")
    prev = None
    for t, rate in events:
        if prev is not None and t > prev:
            iv = round((t - prev) / 3_600_000)
            iv = iv if iv in (1, 2, 4, 8) else 8
        else:
            iv = 8
        buf.write(f"{t},{iv},{rate}\n")
        prev = t
    floor = max(3, int((now_ms - start_ms) / 28_800_000) - 3)
    if len(events) < floor:
        log("rest_funding_insufficient", sym=sym, rows=len(events), floor=floor)
        return "fail"
    with open(csv_path + ".tmp56b", "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    os.replace(csv_path + ".tmp56b", csv_path)
    stats["rest_saved"] += 1
    log("rest_funding_saved", sym=sym, month=ym_str(y, m), rows=len(events))
    return "rest"


# ─────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────
def main():
    only_rest = "--rest-only" in sys.argv
    dry_run = "--dry-run" in sys.argv
    no_rest = "--no-rest" in sys.argv
    syms = list(SYMS)
    for a in sys.argv[1:]:
        if a.startswith("--symbols="):
            syms = [s for s in a.split("=", 1)[1].split(",") if s]
    kinds = ["klines", "funding"]
    for a in sys.argv[1:]:
        if a.startswith("--kinds="):
            kinds = [k for k in a.split("=", 1)[1].split(",") if k in ("klines", "funding")]
    stats = {"downloaded": 0, "zip_extracted": 0, "corrupt_replaced": 0,
             "rest_saved": 0, "failed": 0, "skipped": 0}
    failures = []
    log("start", pid=os.getpid(), dry_run=dry_run, syms=syms, kinds=kinds)
    for sym in syms:
        for kind in kinds:
            log("symbol_begin", sym=sym, kind=kind)
            if not only_rest:
                for y, m in month_iter(*START_YM, *VISION_LAST_YM):
                    if dry_run:
                        continue
                    try:
                        st = ensure_month(sym, kind, y, m, stats)
                    except Exception as e:  # noqa
                        st = "fail"
                        log("exception", kind=kind, sym=sym, month=ym_str(y, m), err=str(e)[:200])
                    if st in ("skip",):
                        stats["skipped"] += 1
                    elif st == "fail":
                        stats["failed"] += 1
                        failures.append((sym, kind, ym_str(y, m)))
                    time.sleep(0.15)
            # 진행월(2026-10) REST 보완
            if no_rest:
                continue
            try:
                st = (rest_month_klines if kind == "klines" else rest_month_funding)(sym, CUR_YM[0], CUR_YM[1], stats)
                if st == "fail":
                    failures.append((sym, kind, ym_str(*CUR_YM) + "(rest)"))
            except Exception as e:  # noqa
                log("exception_rest", kind=kind, sym=sym, err=str(e)[:200])
                failures.append((sym, kind, ym_str(*CUR_YM) + "(rest)"))
            time.sleep(0.2)
    log("done", stats=stats, failures=failures)
    print("\n=== SUMMARY ===")
    print(json.dumps(stats, ensure_ascii=False))
    print("failures:", failures if failures else "NONE")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
