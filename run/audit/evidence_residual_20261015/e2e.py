import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, "."); sys.path.insert(0, "tests")
from test_engine import TestPromotion
from engine_lib import strategy_id, normalize_strategy
from runner import Runner, PathConfig
t = TestPromotion(); 
with tempfile.TemporaryDirectory() as td:
    data = Path(td)/"data"; t._write_btc_dataset(data, lock_drift=0.0005)
    paths = PathConfig(root=Path(td)/"disc", data_dir=data, state_dir=Path(td)/"state", results_dir=Path(td)/"results", audit_dir=Path(td)/"audit")
    d = t.TOD_DEFN; sid = strategy_id(d)
    r = Runner(paths, candidates_fn=lambda: ([(sid, normalize_strategy(d))], []))   # PRODUCTION (UNVERIFIED) cost
    s = r.cycle(max_candidates=5)
    print("cycle status", s.status, "| generation", s.generation_id, "| context", s.context_id)
    print("fdr_family", json.dumps(s.fdr_family)); print("finalized", s.finalized)
    for rec in r.ledger.records():
        print(rec["phase"], rec["status"], "p=", rec.get("primary_dev_p"), "| val", rec.get("validation_verdict"),
              "| portfolio passed" if rec.get("portfolio") else "", (rec.get("portfolio") or {}).get("passed"),
              "mdd", (rec.get("portfolio") or {}).get("max_drawdown"))
        if rec["phase"] == "final":
            print("  locked", rec["locked_test"].get("results"), "\n  claim_scope", rec.get("claim_scope"),
                  "\n  promotion", rec["promotion_check"]["eligible"], rec["promotion_check"]["failed_steps"])
    print("promotion queue entries:", r.promo.entries())
