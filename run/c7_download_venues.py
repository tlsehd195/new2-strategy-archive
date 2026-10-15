#!/usr/bin/env python3
# C7 데이터 다운로드 — PREREG_c7_cross_venue_spread_2026-10-13.md §2 규격.
# Bybit linear 15m (순방향 윈도우 워크) + OKX swap 15m (역방향 after 커서).
# 모든 요약 통계는 research/c7_download_log.json 에 정직 기록. 잠금 월도 저장은 하되 측정 스크립트가 마스킹(§2).
import json, os, sys, time, urllib.request, urllib.error
import pandas as pd

PHASE = sys.argv[1] if len(sys.argv) > 1 else "all"  # all | bybit | okx
BASE = "/home/z/my-project/NEW2_run"
LOG = f"{BASE}/research/c7_download_log.json"
HDRS = {"User-Agent": "Mozilla/5.0 (research; NEW2/1.0)"}
SYM = ["BTCUSDT", "ETHUSDT"]

def ms(s):
    import datetime
    return int(datetime.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(
        tzinfo=datetime.timezone.utc).timestamp() * 1000)

START = ms("2021-04-01 00:00")
END = ms("2026-10-12 00:00")
STEP = 900000  # 15m

def get(url, timeout=25, retries=2):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HDRS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8")), None
        except Exception as e:
            last = repr(e)
            time.sleep(1.0 + attempt)
    return None, None, last

log = {"started_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
       "window": {"start": "2021-04-01", "end": "2026-10-12"},
       "bybit": {}, "okx": {}}

# ---------- Bybit ----------
os.makedirs(f"{BASE}/data/bybit", exist_ok=True)
for sym in SYM if PHASE in ("all", "bybit") else []:
    rows, fails, reqs = [], 0, 0
    cur = START
    t0 = time.time()
    while cur < END:
        # Bybit v5는 [start, end] 폐구간에서 최신 limit개 반환 → 1000봉 요청 시 각 윈도우 첫 봉이 스킵됨(실측 결함).
        # 999봉 = [cur, cur+999S] 정확 커버 → 커서 연속성 보장.
        url = (f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}"
               f"&interval=15&start={cur}&end={min(cur + 999 * STEP, END)}&limit=1000")
        st, j, err = get(url)
        reqs += 1
        lst = (j or {}).get("result", {}).get("list") if st == 200 else None
        if st != 200 or lst is None:
            fails += 1
            log["bybit"].setdefault("errors", []).append({"at_ms": cur, "status": st, "err": err})
            cur += 1000 * STEP  # 윈도우 스킵(기록됨) — 재시도는 get() 내부에서 2회
            continue
        if lst:
            for x in lst:
                rows.append([int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]),
                             float(x[5]), float(x[6])])
            top = max(int(x[0]) for x in lst)
            if top < cur + STEP:
                cur += 1000 * STEP  # 빈 구간 스킵(기록됨)
            else:
                cur = top + STEP
        else:
            cur += 1000 * STEP
        if reqs % 40 == 0:
            print(f"[bybit {sym}] req={reqs} rows={len(rows)} cur={pd.Timestamp(cur, unit='ms', tz='UTC')}", flush=True)
        time.sleep(0.15)
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "qv"])
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    # 기존 파일과 병합(재실행 대비)
    fp = f"{BASE}/data/bybit/{sym}-15m.csv"
    if os.path.exists(fp):
        old = pd.read_csv(fp)
        df = pd.concat([old, df], ignore_index=True).drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    df.to_csv(fp, index=False)
    exp = (END - START) // STEP
    log["bybit"][sym] = {"requests": reqs, "fails": fails, "bars": int(len(df)),
                         "expected_bars": int(exp),
                         "first": str(pd.Timestamp(df["open_time"].iloc[0], unit="ms", tz="UTC")),
                         "last": str(pd.Timestamp(df["open_time"].iloc[-1], unit="ms", tz="UTC")),
                         "sec": round(time.time() - t0, 1)}
    print(f"[bybit {sym}] DONE bars={len(df)}/{exp} fails={fails} {log['bybit'][sym]['first']}~{log['bybit'][sym]['last']}", flush=True)

# ---------- OKX ----------
os.makedirs(f"{BASE}/data/okx", exist_ok=True)
for sym in SYM if PHASE in ("all", "okx") else []:
    inst = f"{sym[:3]}-USDT-SWAP" if sym.startswith("BTC") else f"{sym[:3]}-USDT-SWAP"
    inst = sym.replace("USDT", "-USDT-SWAP")
    rows, fails, reqs = [], 0, 0
    t0 = time.time()
    fp = f"{BASE}/data/okx/{sym}-15m.csv"
    if os.path.exists(fp):  # 재개: 기존 최소 시각 이전만 추가 수집
        old = pd.read_csv(fp)
        rows = old.values.tolist()
        cursor = int(old["open_time"].min())
        print(f"[okx {sym}] resume from {pd.Timestamp(cursor, unit='ms', tz='UTC')}", flush=True)
    else:
        cursor = END
    n_since_flush = 0
    while cursor > START:
        url = (f"https://www.okx.com/api/v5/market/history-candles?instId={inst}"
               f"&bar=15m&after={cursor}&limit=100")
        st, j, err = get(url)
        reqs += 1
        data = (j or {}).get("data") if st == 200 else None
        if st != 200 or data is None:
            fails += 1
            log["okx"].setdefault("errors", []).append({"after": cursor, "status": st, "err": err})
            if fails > 40:
                print(f"[okx {sym}] too many fails, stop at cursor={cursor}", flush=True)
                break
            continue
        if not data:
            break  # 더 깊은 역사 없음 — 실측 종점
        got = []
        for x in data:
            if str(x[8]) != "1":  # 미완결 봉 제외(fail-closed)
                continue
            got.append([int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]),
                        float(x[5]), float(x[7]) if len(x) > 7 and x[7] not in ("", None) else 0.0])
        rows.extend(got)
        mn = min(int(x[0]) for x in data)
        if mn >= cursor:
            break
        cursor = mn
        n_since_flush += 1
        if n_since_flush >= 200:  # 중간 저장 — 킬 대비 재개점 확보
            pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "qv"]).drop_duplicates("open_time").sort_values("open_time").to_csv(fp, index=False)
            n_since_flush = 0
        if reqs % 100 == 0:
            print(f"[okx {sym}] req={reqs} rows={len(rows)} cursor={pd.Timestamp(cursor, unit='ms', tz='UTC')}", flush=True)
        time.sleep(0.12)
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "qv"])
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    df.to_csv(fp, index=False)
    span_ms = int(df["open_time"].iloc[-1]) - int(df["open_time"].iloc[0])
    exp = span_ms // STEP + 1
    log["okx"][sym] = {"requests": reqs, "fails": fails, "bars": int(len(df)),
                       "expected_bars_in_range": int(exp),
                       "first": str(pd.Timestamp(df["open_time"].iloc[0], unit="ms", tz="UTC")),
                       "last": str(pd.Timestamp(df["open_time"].iloc[-1], unit="ms", tz="UTC")),
                       "sec": round(time.time() - t0, 1)}
    print(f"[okx {sym}] DONE bars={len(df)} (range {log['okx'][sym]['first']}~{log['okx'][sym]['last']}) fails={fails}", flush=True)

log["finished_utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
with open(LOG, "w") as f:
    json.dump(log, f, ensure_ascii=False, indent=1)
print("LOG ->", LOG)
