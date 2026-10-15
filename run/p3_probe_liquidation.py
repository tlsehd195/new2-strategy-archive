"""Task 29 - P3 청산 데이터 소스 재실측 probe
감사 교훈 준용: '데이터 미확보/누적 대기' 분류는 재개 시점마다 재실측.
probe 대상: OKX liquidation-orders, BitMEX liquidation, Binance forceOrder(역사 유무 확인)
"""
import json, urllib.request, urllib.parse, time, datetime

def get(url, params=None, timeout=30):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research probe)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())

log = {"probe_time_utc": datetime.datetime.utcnow().isoformat() + "Z", "sources": {}}

# --- 1. OKX liquidation-orders (SWAP) ---
try:
    st, js = get("https://www.okx.com/api/v5/public/liquidation-orders", {"instType": "SWAP", "uly": "BTC-USDT", "state": "filled", "limit": "20"})
    ok = js.get("code") == "0"
    data = js.get("data", [])
    recs = []
    if ok and data:
        details = data[0].get("details", [])
        recs = details[:3]
    log["sources"]["okx_liq_v5_public"] = {
        "http": st, "code": js.get("code"), "msg": js.get("msg"),
        "n_groups": len(data) if ok else 0,
        "sample": recs[:2],
    }
except Exception as e:
    log["sources"]["okx_liq_v5_public"] = {"error": repr(e)}
time.sleep(0.3)

# --- 1b. OKX history (market endpoint variant) ---
try:
    st, js = get("https://www.okx.com/api/v5/market/history-liquidation-orders", {"instId": "BTC-USDT-SWAP", "limit": "20"})
    log["sources"]["okx_liq_history"] = {"http": st, "code": js.get("code"), "msg": js.get("msg"), "n": len(js.get("data", [])) if js.get("code") == "0" else 0}
except Exception as e:
    log["sources"]["okx_liq_history"] = {"error": repr(e)}
time.sleep(0.3)

# --- 2. BitMEX liquidation (역사 깊이 확인) ---
try:
    st, js = get("https://www.bitmex.com/api/v1/liquidation", {"count": 5, "reverse": "false", "symbol": "XBTUSD"})
    if isinstance(js, list) and js:
        first = js[0]
        log["sources"]["bitmex_liquidation_oldest"] = {
            "http": st, "n": len(js),
            "oldest_timestamp": first.get("timestamp"),
            "sample_keys": sorted(first.keys())[:20],
        }
        st2, js2 = get("https://www.bitmex.com/api/v1/liquidation", {"count": 5, "reverse": "true", "symbol": "XBTUSD"})
        if isinstance(js2, list) and js2:
            log["sources"]["bitmex_liquidation_newest"] = {"http": st2, "newest_timestamp": js2[0].get("timestamp")}
    else:
        log["sources"]["bitmex_liquidation_oldest"] = {"http": st, "resp": str(js)[:200]}
except Exception as e:
    log["sources"]["bitmex_liquidation_oldest"] = {"error": repr(e)}
time.sleep(0.3)

# --- 3. Binance forceOrders (공개 역사 유무) ---
try:
    st, js = get("https://fapi.binance.com/fapi/v1/allForceOrders", {"symbol": "BTCUSDT", "limit": 5})
    log["sources"]["binance_allForceOrders"] = {"http": st, "resp": (js if isinstance(js, dict) else str(js)[:200])}
except Exception as e:
    log["sources"]["binance_allForceOrders"] = {"error": repr(e)}
time.sleep(0.3)

# --- 4. Coinglass 무키 접근 재실측 ---
try:
    st, js = get("https://open-api-v3.coinglass.com/api/liquidation/exchange-list", {"ex": "Binance", "sym": "BTC"})
    log["sources"]["coinglass_v3_nokey"] = {"http": st, "code": js.get("code"), "msg": js.get("msg")}
except Exception as e:
    log["sources"]["coinglass_v3_nokey"] = {"error": repr(e)}

with open("research/p3_liquidation_probe_log.json", "w") as f:
    json.dump(log, f, indent=2, ensure_ascii=False)
print(json.dumps(log, indent=2, ensure_ascii=False)[:3500])
