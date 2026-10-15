"""Task 29 - P3 probe 2차: BitMEX 파라미터 변형 + OKX begin/end 지원 여부"""
import json, urllib.request, urllib.parse, datetime, time

def get(url, params=None, timeout=30):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research probe)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())

log = {"probe_time_utc": datetime.datetime.now(datetime.UTC).isoformat(), "tests": {}}

# BitMEX: 심볼 변형
for sym in ["XBTUSDT", "ETHUSD", "ETHUSDT"]:
    try:
        st, js = get("https://www.bitmex.com/api/v1/liquidation", {"count": 3, "reverse": "false", "symbol": sym})
        log["tests"][f"bitmex_{sym}_oldest"] = {"http": st, "n": len(js), "first": str(js[0])[:200] if js else None}
    except Exception as e:
        log["tests"][f"bitmex_{sym}_oldest"] = {"error": repr(e)}
    time.sleep(0.4)

# BitMEX: 심볼 없이
try:
    st, js = get("https://www.bitmex.com/api/v1/liquidation", {"count": 3, "reverse": "false"})
    log["tests"]["bitmex_nosymbol_oldest"] = {"http": st, "n": len(js), "first": str(js[0])[:200] if js else None}
    st, js = get("https://www.bitmex.com/api/v1/liquidation", {"count": 3, "reverse": "true"})
    log["tests"]["bitmex_nosymbol_newest"] = {"http": st, "n": len(js), "first": str(js[0])[:200] if js else None}
except Exception as e:
    log["tests"]["bitmex_nosymbol"] = {"error": repr(e)}
time.sleep(0.4)

# OKX: begin/end 파라미터 지원 여부 (1년 전 시점 요청해도 최근 데이터만 돌아오는지)
begin_1y = 1752451200000  # 2025-07-14
try:
    st, js = get("https://www.okx.com/api/v5/public/liquidation-orders", {"instType": "SWAP", "uly": "BTC-USDT", "state": "filled", "limit": "5", "begin": str(begin_1y), "end": str(begin_1y + 86400000)})
    if js.get("code") == "0":
        times = [d.get("time") for g in js.get("data", []) for d in g.get("details", [])]
        log["tests"]["okx_begin_end_1y"] = {"http": st, "code": js.get("code"), "n": len(times), "times_ms": times[:5]}
    else:
        log["tests"]["okx_begin_end_1y"] = {"http": st, "code": js.get("code"), "msg": js.get("msg")}
except Exception as e:
    log["tests"]["okx_begin_end_1y"] = {"error": repr(e)}
time.sleep(0.4)

# OKX: uly 없이 ETH도
try:
    st, js = get("https://www.okx.com/api/v5/public/liquidation-orders", {"instType": "SWAP", "instId": "ETH-USDT-SWAP", "state": "filled", "limit": "5"})
    if js.get("code") == "0":
        times = [d.get("time") for g in js.get("data", []) for d in g.get("details", [])]
        log["tests"]["okx_eth_instid"] = {"http": st, "n": len(times), "times_ms": times[:5]}
    else:
        log["tests"]["okx_eth_instid"] = {"http": st, "code": js.get("code"), "msg": js.get("msg")}
except Exception as e:
    log["tests"]["okx_eth_instid"] = {"error": repr(e)}

with open("research/p3_liquidation_probe2_log.json", "w") as f:
    json.dump(log, f, indent=2, ensure_ascii=False)
print(json.dumps(log, indent=2, ensure_ascii=False)[:3000])
