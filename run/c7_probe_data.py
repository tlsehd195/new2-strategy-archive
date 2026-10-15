#!/usr/bin/env python3
# C7 교차거래소 퍼페-퍼페 스프레드 — 데이터 가용성 재실측 (PREREG 작성 전 필수 단계)
# 라운드2(web_research_round2.md:96-97) 실측이 현재 시점에도 유효한지 + 실제 역사적 깊이 확인.
# 모든 HTTP 결과를 정직하게 research/c7_probe_log.json 에 기록. 추측 금지 — 응답 본문 기준으로만 판정.
import json, time, urllib.request, urllib.error

OUT = "/home/z/my-project/NEW2_run/research/c7_probe_log.json"
HDRS = {"User-Agent": "Mozilla/5.0 (research; NEW2/1.0)"}

def get(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers=HDRS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            return {"status": r.status, "len": len(body), "body": body[:2000]}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "len": 0, "body": e.read(500).decode("utf-8", errors="replace")}
    except Exception as e:
        return {"status": None, "err": repr(e)}

def ms(s):
    import datetime
    return int(datetime.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(
        tzinfo=datetime.timezone.utc).timestamp() * 1000)

log = {"probed_at_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "bybit": {}, "okx": {}}

# --- Bybit v5 linear kline: 깊이 프로브 (start 시점에 실데이터가 존재하는지) ---
bybit_probes = [
    ("BTCUSDT", "2021-06-01 00:00", "2021-06-02 00:00"),   # 라운드2 실측 시점 (재현 확인)
    ("BTCUSDT", "2020-06-01 00:00", "2020-06-02 00:00"),   # 상장 초기 — 최대 깊이 탐색
    ("BTCUSDT", "2021-04-01 00:00", "2021-04-02 00:00"),   # Binance 로컬 데이터 시작과 정렬 시점
    ("ETHUSDT", "2021-06-01 00:00", "2021-06-02 00:00"),
    ("ETHUSDT", "2020-06-01 00:00", "2020-06-02 00:00"),
    ("ETHUSDT", "2021-04-01 00:00", "2021-04-02 00:00"),
]
for sym, s, e in bybit_probes:
    url = (f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}"
           f"&interval=15&start={ms(s)}&end={ms(e)}&limit=10")
    r = get(url)
    entry = {"url": url, **r}
    if r["status"] == 200:
        try:
            j = json.loads(r["body"])
            lst = j.get("result", {}).get("list", [])
            entry["retCode"] = j.get("retCode")
            entry["retMsg"] = j.get("retMsg")
            if lst:
                entry["n_bars"] = len(lst)
                # Bybit 응답: [start(ms, str), open, high, low, close, volume, turnover] — 최신 우선 정렬
                ts = sorted(int(x[0]) for x in lst)
                entry["first_bar_utc"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts[0] / 1000))
                entry["last_bar_utc"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts[-1] / 1000))
                entry["sample_bar"] = lst[0]
            else:
                entry["n_bars"] = 0
        except Exception as ex:
            entry["parse_err"] = repr(ex)
    log["bybit"][f"{sym}@{s}"] = entry
    time.sleep(0.3)

# --- Bybit: 최신 봉 시점 확인 (종점 극한) ---
url = ("https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT"
       f"&interval=15&end={ms('2026-10-13 04:00')}&limit=3")
r = get(url)
entry = {"url": url, **r}
if r["status"] == 200:
    try:
        j = json.loads(r["body"])
        lst = j.get("result", {}).get("list", [])
        if lst:
            entry["last_bar_utc"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(lst[0][0]) / 1000))
            entry["n_bars"] = len(lst)
    except Exception as ex:
        entry["parse_err"] = repr(ex)
log["bybit"]["latest@2026-10-13"] = entry
time.sleep(0.3)

# --- OKX history-candles: 깊이 프로브 (after = 봉 시작 시각 이전 요청 — OKX는 after가 'older than' 커서) ---
okx_probes = [
    ("BTC-USDT-SWAP", "2021-06-02 00:00"),   # 라운드2 '2024-01-01 응답' 대비 깊이 확인용
    ("BTC-USDT-SWAP", "2022-06-02 00:00"),
    ("BTC-USDT-SWAP", "2023-06-02 00:00"),
    ("BTC-USDT-SWAP", "2024-06-02 00:00"),
    ("ETH-USDT-SWAP", "2021-06-02 00:00"),
    ("ETH-USDT-SWAP", "2024-06-02 00:00"),
]
for inst, anchor in okx_probes:
    url = (f"https://www.okx.com/api/v5/market/history-candles?instId={inst}"
           f"&bar=15m&after={ms(anchor)}&limit=100")
    r = get(url)
    entry = {"url": url, **r}
    if r["status"] == 200:
        try:
            j = json.loads(r["body"])
            data = j.get("data", [])
            entry["code"] = j.get("code")
            entry["msg"] = j.get("msg")
            if data:
                ts = sorted(int(x[0]) for x in data)
                entry["n_bars"] = len(data)
                entry["first_bar_utc"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts[0] / 1000))
                entry["last_bar_utc"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts[-1] / 1000))
                entry["sample_bar"] = data[0]
            else:
                entry["n_bars"] = 0
        except Exception as ex:
            entry["parse_err"] = repr(ex)
    log["okx"][f"{inst}@before_{anchor}"] = entry
    time.sleep(0.35)

with open(OUT, "w") as f:
    json.dump(log, f, ensure_ascii=False, indent=1)

# --- 요약 판독 (stdout) ---
print("=== BYBIT ===")
for k, v in log["bybit"].items():
    if "first_bar_utc" in v:
        print(f"{k}: 200 n={v.get('n_bars')} range {v['first_bar_utc']} ~ {v.get('last_bar_utc')}")
    else:
        print(f"{k}: status={v.get('status')} retCode={v.get('retCode')} body={v.get('body','')[:150]}")
print("=== OKX ===")
for k, v in log["okx"].items():
    if "first_bar_utc" in v:
        print(f"{k}: 200 code={v.get('code')} n={v.get('n_bars')} range {v['first_bar_utc']} ~ {v.get('last_bar_utc')}")
    else:
        print(f"{k}: status={v.get('status')} code={v.get('code')} msg={v.get('msg')} body={v.get('body','')[:150]}")
