import sys, tempfile, json, math
from pathlib import Path
sys.path.insert(0, ".")
sys.path.insert(0, "tests")
from test_engine import make_bars, MS15
from engine_lib import experiment_context_id, bh_fdr, LockTestRegistry
from data_quality import Bar
from costs_bridge import CostContext
from portfolio import simulate_portfolio, PortfolioConfig
from runner import Runner, PathConfig

print("== D1 BH-FDR order dependence (current _bh_fdr_survival) ==")
td = tempfile.mkdtemp(); data = Path(td)/"data"; (data/"klines").mkdir(parents=True)
def mk():
    t = tempfile.mkdtemp()
    return Runner(PathConfig(root=Path(t), data_dir=data, state_dir=Path(t)/"s", results_dir=Path(t)/"r", audit_dir=Path(t)/"a"))
ps = {"A": 0.02, "B": 0.5, "C": 0.6, "D": 0.7, "E": 0.8}
for order in (["A","B","C","D","E"], ["B","C","D","E","A"]):
    r = mk(); out = {}
    for i, s in enumerate(order):
        rep = r._bh_fdr_survival(s, "CTX", ps[s]); out[s] = (rep["survived"], rep["trials"])
        r.ledger.append({"evaluation_id": f"E{i}", "strategy_id": s, "context_id": "CTX", "primary_dev_p": ps[s]})
    print(order, "-> A:", out["A"])
print("full-family BH (m=5):", dict(zip(ps, bh_fdr(list(ps.values())))), " m=779 equivalent threshold for rank1 =", 0.05/779)

print("== D2 portfolio equity_curve ignores unrealized PnL ==")
n=20; bars=[]
for i in range(n):
    p = 50.0 if 5 <= i < 10 else 100.0   # price halves then recovers while held
    bars.append(Bar(ts=i*MS15, open=p, high=p, low=p, close=p, volume=1e9, quote_volume=1e11))
pf = simulate_portfolio({"A": bars}, [{"symbol":"A","decision_index":2,"side":1,"horizon":12}], {"A": []},
                        CostContext.futures_default(), PortfolioConfig(initial_capital=10000, size_fraction=1.0, leverage=1.0))
print("equity_curve[3:16]:", [round(x) for x in pf.equity_curve[3:16]])
print("max_drawdown:", round(pf.max_drawdown,4), "(true MTM drawdown ~0.5 while held)")

print("== D3 exit cost ValueError -> favorable fallback ==")
bars2=[Bar(ts=i*MS15, open=100, high=100, low=100, close=100, volume=1e6, quote_volume=(1e8 if i!=10 else 1000.0)) for i in range(20)]
pf = simulate_portfolio({"A": bars2}, [{"symbol":"A","decision_index":2,"side":1,"horizon":7}], {"A": []},
                        CostContext.futures_default(), PortfolioConfig(initial_capital=10000, size_fraction=0.5))
print("trades:", pf.trades, "total_cost_bp:", round(pf.total_cost_bp,2), "notes:", pf.notes, " (exit bar participation = 5000/1000 = 500% -> priced at fee+half_spread only)")

print("== D4 lock registry use recorded before data load ==")
reg = LockTestRegistry(Path(td)/"lock.json")
reg.record_use("S1","BTCUSDT","C")  # runner calls this before merged_bars/funding quality
print("uses after data-missing path:", reg.uses("S1","BTCUSDT"), json.loads((Path(td)/"lock.json").read_text()))

print("== D5 experiment_context_id has no funding/fng/cost-param inputs ==")
import inspect; print(inspect.signature(experiment_context_id))

print("== D6 preregistration reuse by existence only ==")
r = mk(); pdir = r.paths.state_dir; pdir.mkdir(parents=True, exist_ok=True)
(pdir/"preregistration.json").write_text(json.dumps({"total_candidates": 3, "note": "different space"}))
print("returned existing doc:", r._write_preregistration([], []), " flag:", r._preregistered_flag())
