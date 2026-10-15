"""Task 29 - KP(김치 프리미엄) 데이터 다운로드 v2
1) Upbit 1d KRW-BTC/ETH (to=ISO 형식)
2) Binance spot 1d: data.binance.vision monthly zip (2021-04~2026-09) + daily zip (2026-10)
3) Yahoo USDKRW=X (완료 — 재사용)
"""
import json, urllib.request, time, os, io, zipfile
import pandas as pd

BASE = "/home/z/my-project/NEW2_run/data/kp"
os.makedirs(BASE, exist_ok=True)
LOG = {}

def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def get_bytes(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

# --- 1. Upbit 일봉 ---
def upbit_days(market, start_utc, end_utc):
    rows = []
    to = f"{end_utc}T00:00:00Z"
    while True:
        url = f"https://api.upbit.com/v1/candles/days?market={market}&to={to}&count=200"
        js = get_json(url)
        if not js:
            break
        rows.extend(js)
        oldest = js[-1]["candle_date_time_utc"]
        if oldest[:10] <= start_utc or len(js) < 200:
            break
        to = oldest
        time.sleep(0.2)
    df = pd.DataFrame(rows)[["candle_date_time_utc", "opening_price", "high_price", "low_price", "trade_price", "candle_acc_trade_volume", "candle_acc_trade_price"]]
    df.columns = ["utc", "open", "high", "low", "close", "vol", "qv"]
    df["utc"] = df["utc"].str[:10]
    df = df.drop_duplicates("utc").sort_values("utc").reset_index(drop=True)
    return df

START, END = "2021-04-01", "2026-10-12"
for mkt, sym in [("KRW-BTC", "BTC"), ("KRW-ETH", "ETH")]:
    try:
        df = upbit_days(mkt, START, END)
        df.to_csv(f"{BASE}/upbit_{sym}_1d.csv", index=False)
        dts = pd.to_datetime(df["utc"])
        LOG[f"upbit_{sym}"] = {"rows": len(df), "span": [df['utc'].iloc[0], df['utc'].iloc[-1]], "gap_jumps": int((dts.diff().dt.days > 1).sum())}
        print(f"upbit {sym}: {LOG[f'upbit_{sym}']}")
    except Exception as e:
        LOG[f"upbit_{sym}"] = {"error": repr(e)}
        print(f"upbit {sym} ERR", e)
    time.sleep(0.5)

# --- 2. Binance spot 1d via data.binance.vision ---
COLS = ["open_time", "open", "high", "low", "close", "vol", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
def binance_1d_from_vision(symbol):
    frames = []
    months = pd.period_range("2021-04", "2026-09", freq="M")
    for m in months:
        try:
            data = get_bytes(f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{m}.zip")
            df = pd.read_csv(io.BytesIO(data), header=None, names=COLS, usecols=["open_time", "close"])
            frames.append(df)
        except Exception as e:
            LOG[f"vision_{symbol}_{m}"] = {"error": repr(e)[:100]}
        time.sleep(0.15)
    # 2026-10 daily
    for d in pd.date_range("2026-10-01", "2026-10-12"):
        try:
            data = get_bytes(f"https://data.binance.vision/data/spot/daily/klines/{symbol}/1d/{symbol}-1d-{d.date()}.zip")
            df = pd.read_csv(io.BytesIO(data), header=None, names=COLS, usecols=["open_time", "close"])
            frames.append(df)
        except Exception as e:
            LOG[f"vision_{symbol}_{d.date()}"] = {"error": repr(e)[:100]}
        time.sleep(0.15)
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    df["day"] = pd.to_datetime(df["open_time"], unit="ms").dt.strftime("%Y-%m-%d")
    df = df[["day", "close"]]
    return df

for sym in ["BTCUSDT", "ETHUSDT"]:
    try:
        df = binance_1d_from_vision(sym)
        df.to_csv(f"{BASE}/binance_spot_{sym}_1d.csv", index=False)
        dts = pd.to_datetime(df["day"])
        LOG[f"binance_{sym}"] = {"rows": len(df), "span": [df['day'].iloc[0], df['day'].iloc[-1]], "gap_jumps": int((dts.diff().dt.days > 1).sum()), "errs": len([k for k in LOG if k.startswith(f'vision_{sym}')])}
        print(f"binance spot {sym}: {LOG[f'binance_{sym}']}")
    except Exception as e:
        LOG[f"binance_{sym}"] = {"error": repr(e)}
        print(f"binance {sym} ERR", e)

with open("/home/z/my-project/NEW2_run/research/kp_download_log.json", "w") as f:
    json.dump(LOG, f, indent=2, ensure_ascii=False)
print("LOG saved.")
