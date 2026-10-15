import sys, json, tempfile, copy
from pathlib import Path
sys.path.insert(0, "."); sys.path.insert(0, "tests")
from test_residual_fixes import pbars, sim, long_sig, fee_cost, SPACE, _write_lock_window, LOCK_DEFN, LOCK_SID, make_paths, MS15
from portfolio import max_drawdown
from costs_bridge import CostContext, CostRefused, CostDataMissing
from engine_lib import experiment_context, LockTestRegistry
from generation import build_manifest

print("== D1 portfolio equity curve (long 100 contracts @100, size 1.0, lev 1, zero cost) ==")
prices=[100,100,100,80,50,50,100,100,100,100]
pf=sim(pbars(prices),[long_sig(1,6)])
print("prices      :", prices)
print("equity_curve:", [round(v,2) for v in pf.equity_curve])
print("hand MDD = (10000-5000)/10000 = 0.5 ; program MDD =", pf.max_drawdown, "; final", pf.final_equity)

print("== D1b fees+funding (fee 10bp, funding 1bp @bar4) ==")
pf=sim(pbars([100]*10),[long_sig(1,4)],cost=fee_cost(),funding={"A":[(4*MS15,1e-4),(8*MS15,1e-4)]})
print("hand: 10000 - 10 (entry) - 1 (funding) - 10 (exit) = 9979 ; program:", pf.final_equity, "fees", pf.total_fees, "funding", pf.total_funding, "curve[-1]", pf.equity_curve[-1])

print("== D1c liquidation (lev 2, low 50 <= liq 50.5) ==")
pf=sim(pbars([100]*10,lows=[None]*4+[50.0]+[None]*5),[long_sig(1,6)],cost=fee_cost(),leverage=2.0)
print("hand: 10000 - 10 (entry fee) - 5000 (margin) = 4990 ; program:", pf.final_equity, "liq events", pf.liquidation_events, "fees", pf.total_fees)

print("== D2 cost failure ==")
c=CostContext.futures_default()
for ov,bv in ((10000,1e8),(10000,1e5),(10000,0.0)):
    try: print(f"order {ov} vs bar {bv}: cost_fraction={c.quote(ov,bv):.6f}")
    except (CostRefused,CostDataMissing) as e: print(f"order {ov} vs bar {bv}: {type(e).__name__} status={e.status}: {e}")
from data_quality import Bar
bars=[Bar(ts=i*MS15,open=100,high=100,low=100,close=100,volume=1e6,quote_volume=(1e8 if i!=10 else 1000.0)) for i in range(20)]
pf=sim(bars,[long_sig(2,7)],cost=c,size_fraction=0.5)
print("exit-unpriceable portfolio: cost_status", pf.cost_status, "| failures", pf.cost_failures, "| total_cost_bp (entry only)", round(pf.total_cost_bp,4), "(before fix: 7.0 bp fallback, status OK)")

print("== D3 experiment context change ==")
base={"data":{"BTCUSDT":{"funding":{"sha256":"f"}}},"data_policy":{"v":1},"cost":{"fee":0.0005},"code":{"code_hash":"h"},"validation":{"n":30},"runtime":{"py":"3.13"}}
a=experiment_context(base)[0]; b=copy.deepcopy(base); b["data"]["BTCUSDT"]["funding"]["sha256"]="f2"
print("base", a, "| funding changed", experiment_context(b)[0], "| repeat", experiment_context(base)[0])

print("== D4 candidate space hash ==")
m=build_manifest(SPACE); print("6-cand generation", m["generation_id"], "| reversed order", build_manifest(list(reversed(SPACE)))["generation_id"], "| minus one", build_manifest(SPACE[:-1])["generation_id"])
from generator import enumerate_candidates
full=build_manifest(*enumerate_candidates()); print("real space:", full["n_candidates"], "candidates", full["n_families"], "families ->", full["generation_id"], full["manifest_hash"][:24])

print("== D5 locked TEST state transitions ==")
from runner import Runner
with tempfile.TemporaryDirectory() as td:
    paths=make_paths(td); r=Runner(paths,candidates_fn=lambda:([(LOCK_SID,LOCK_DEFN)],[])); r._ctx="CTX-demo"
    st,info=r._locked_test(LOCK_SID,LOCK_DEFN,"CTX-demo")
    print("no locked data  ->", st, [a["state"] for a in r.lock_registry.attempts(LOCK_SID,"BTCUSDT")], "uses", r.lock_registry.uses(LOCK_SID,"BTCUSDT"))
    _write_lock_window(paths.data_dir)
    st,info=r._locked_test(LOCK_SID,LOCK_DEFN,"CTX-demo")
    print("data present    ->", st, [a["state"] for a in r.lock_registry.attempts(LOCK_SID,"BTCUSDT")], "uses", r.lock_registry.uses(LOCK_SID,"BTCUSDT"), "result", info["results"]["BTCUSDT"])
    st,info=r._locked_test(LOCK_SID,LOCK_DEFN,"CTX-demo")
    print("second attempt  ->", st, info["note"], "uses", r.lock_registry.uses(LOCK_SID,"BTCUSDT"))
