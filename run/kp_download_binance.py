"""Task 29 - Binance spot 1d via data.binance.vision (zipfile 명시 해제 + 마이크로초 보정)"""
import urllib.request, time, os, io, zipfile, json
import pandas as pd

BASE = "/home/z/my-project/NEW2_run/data/kp"
LOG = {"errors": {}}

def get_bytes(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def fetch_zip_csv(url, symbol):
    data = get_bytes(url)
    zf = zipfile.ZipFile(io.BytesIO(data))
    name = zf.namelist()[0]
    content = zf.read(name)
    df = pd.read_csv(io.BytesIO(content), header=None)
    # 열 수 정규화 (12컬럼 표준)
    df = df.iloc[:, :12]
    df.columns = ["open_time", "open", "high", "low", "close", "vol", "close_time", "qv", "n", "tbv", "tbqv", "ig"]
    return df

def binance_1d_from_vision(symbol):
    frames = []
    months = pd.period_range("2021-04", "2026-09", freq="M")
    for m in months:
        try:
            frames.append(fetch_zip_csv(f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{m}.zip", symbol))
        except Exception as e:
            LOG["errors"][f"{symbol}_monthly_{m}"] = repr(e)[:120]
        time.sleep(0.12)
    for d in pd.date_range("2026-10-01", "2026-10-12"):
        try:
            frames.append(fetch_zip_csv(f"https://data.binance.vision/data/spot/daily/klines/{symbol}/1d/{symbol}-1d-{d.date()}.zip", symbol))
        except Exception as e:
            LOG["errors"][f"{symbol}_daily_{d.date()}"] = repr(e)[:120]
        time.sleep(0.12)
    df = pd.concat(frames, ignore_index=True)
    # 마이크로초 → ms 보정
    df["open_time"] = df["open_time"].astype("int64")
    df.loc[df["open_time"] > 1e14, "open_time"] //= 1000
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    df["day"] = pd.to_datetime(df["open_time"], unit="ms").dt.strftime("%Y-%m-%d")
    return df[["day", "close"]]

for sym in ["BTCUSDT", "ETHUSDT"]:
    df = binance_1d_from_vision(sym)
    df.to_csv(f"{BASE}/binance_spot_{sym}_1d.csv", index=False)
    dts = pd.to_datetime(df["day"])
    nerr = len([k for k in LOG["errors"] if k.startswith(sym)])
    LOG[sym] = {"rows": len(df), "span": [df['day'].iloc[0], df['day'].iloc[-1]], "gap_jumps": int((dts.diff().dt.days > 1).sum()), "fetch_errs": nerr}
    print(f"binance spot {sym}: {LOG[sym]}")

with open("/home/z/my-project/NEW2_run/research/kp_download_binance_log.json", "w") as f:
    json.dump(LOG, f, indent=2, ensure_ascii=False)
print("saved.")
