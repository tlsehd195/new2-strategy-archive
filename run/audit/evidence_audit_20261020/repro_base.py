"""Reproduce the 2026-10-20 audit defects on the BASE code (main 900c71c)."""
import sys
from dataclasses import replace
sys.path[:0] = [sys.argv[1] + "/run/discovery", sys.argv[1] + "/run/discovery/tests"]
import cost_calibration as cc
from costs_bridge import CostContext
from test_completion import synth_fills
from engine_lib import strategy_id
from strategy_spec import canonicalize_spec, SPEC_SCHEMA

def two(n=1000, b=1.0, e=-3.0):
    out = []
    for i, o in enumerate(synth_fills(n=n, symbols=("BTCUSDT", "ETHUSDT"))):
        if i >= n // 2:
            bias = b if o.symbol == "BTCUSDT" else e
            o = replace(o, fill_price=o.fill_price * (1 + o.side * bias / 1e4))
        out.append(o)
    return out
def calib(obs):
    return cc.calibrate_and_verify(obs, CostContext.futures_default(), required_symbols=["BTCUSDT", "ETHUSDT"], order_value=10_000.0)

r = calib(two())
errs_btc = None
print("D1 pooled hides BTC underestimate: status =", r["status"], "pooled mean/p90 =",
      round(r["verification_errors"]["mean_bp"], 3), round(r["verification_errors"]["p90_bp"], 3))
good = synth_fills(n=1)[0]
for name, o in {"seconds ts": replace(good, ts_ms=1_700_000_000),
                "fee in bp": replace(good, fee=4.0 * good.filled_notional),
                "price x100": replace(good, fill_price=good.fill_price * 100)}.items():
    try:
        o.validate(); print("D2 accepted:", name)
    except cc.CalibrationError as e:
        print("D2 refused:", name, e)
obs = synth_fills(n=1000, symbols=("BTCUSDT", "ETHUSDT"))
print("D3 in-memory duplicates (5 repeated fills): status =", calib(obs + obs[:5])["status"])
print("D4 too few fills: status =", calib(synth_fills(n=100))["status"], "(sample shortfall reported as a failure)")
A = {"feature": "ret_z", "params": {"lookback": 16, "window": 64, "min_history": 0}, "op": "gt", "value": 1.5}
B = {"feature": "hour_utc", "params": {}, "op": "between", "value": [0.0, 6.0]}
mk = lambda e, f: canonicalize_spec({"schema": SPEC_SCHEMA, "direction": "long", "symbols": ["BTCUSDT"],
                                     "entry": {"logic": "all", "conditions": e}, "filters": f, "exit": {"horizon": 8}})
print("D5 entry[A,B] vs entry[A]+filter[B]: ids", strategy_id(mk([A, B], [])), strategy_id(mk([A], [B])), "(same behaviour, both accepted as new)")
