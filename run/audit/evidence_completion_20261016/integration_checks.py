"""Final integration checks (directive §9, steps 1-3 and 12-14) — run AFTER
the full real-data cycles.  Usage:

    COINTRADER_SRC=<NEW2>/src python3 -I integration_checks.py DISC_DIR REAL_DATA WORK OUT \
        STATE_A STATE_B

Read-only on REAL_DATA (a corrupted COPY is made under WORK).  Prints one
JSON document; every check states its expected and observed value."""
import contextlib, io, json, os, shutil, sys
from pathlib import Path

disc, real, work, out = map(Path, sys.argv[1:5])
state_a, state_b = Path(sys.argv[5]), Path(sys.argv[6])
sys.path.insert(0, str(disc))
import runner  # noqa: E402
from autoloop import run_evolution_cycle  # noqa: E402
from costs_bridge import CostContext  # noqa: E402
from data_manifest import build_manifest  # noqa: E402
from engine_lib import iter_jsonl  # noqa: E402
from ops import health  # noqa: E402
from pipeline import RunnerDataProvider  # noqa: E402

work.mkdir(parents=True, exist_ok=True)
out.mkdir(parents=True, exist_ok=True)
res = {}


def main_rc(env, argv):
    o = io.StringIO()
    old = dict(os.environ)
    os.environ.update(env)
    try:
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
            rc = runner.main(argv)
    finally:
        os.environ.clear()
        os.environ.update(old)
    return rc, o.getvalue().strip().splitlines()[-1:]

# 1) no data: fail closed
(work / "empty").mkdir(exist_ok=True)
rc, line = main_rc({"NEW2_DISCOVERY_ROOT": str(work / "root_empty"), "NEW2_DATA_DIR": str(work / "empty")},
                   ["--mode", "evolve"])
res["1_no_data"] = {"expected_exit": 4, "exit": rc, "summary": json.loads(line[0])["status"]}
rc, _ = main_rc({"NEW2_DISCOVERY_ROOT": str(work / "root_missing"), "NEW2_DATA_DIR": str(work / "does_not_exist")},
                ["--mode", "evolve"])
res["1_missing_data_dir"] = {"expected_exit": 1, "exit": rc}

# 2) corrupted copy: recorded and blocked (BTCUSDT only), ETHUSDT keeps going
cor = work / "corrupt"
if cor.exists():
    shutil.rmtree(cor)
shutil.copytree(real, cor)
p = cor / "klines" / "BTCUSDT-15m-2022-05.csv"
lines = p.read_text().splitlines()
lines[100] = lines[100].replace(",", ";", 3)
p.write_text("\n".join(lines) + "\n")
man = build_manifest(cor)
res["2_corrupt_manifest"] = {"ok": man["ok"], "blocking": [(i["code"], i["path"]) for i in man["issues"]
                                                          if i["severity"] == "BLOCK"]}
prov = RunnerDataProvider(cor)
res["2_corrupt_provider"] = {"BTCUSDT_missing": prov.symbol("BTCUSDT")["missing"],
                             "ETHUSDT_usable": not prov.symbol("ETHUSDT")["missing"]}
s = run_evolution_cycle(work / "state_corrupt", prov, CostContext.futures_default(), rounds=0, max_dev_evals=3)
res["2_corrupt_cycle"] = {"status": s.status, "symbols_used": s.symbols_used,
                          "symbols_blocked": sorted(s.symbols_blocked), "dev_evaluated": s.dev_evaluated}

# 3) real data identity
man = build_manifest(real)
(out / "manifest_real_data.json").write_text(json.dumps(man, indent=1, sort_keys=True))
res["3_real_manifest"] = {"ok": man["ok"], "dataset_sha256": man["dataset_sha256"], "n_files": man["n_files"],
                          "counts": man["counts"], "symbols": man["symbols"]}


# 12-13) reproducibility of the two independent full cycles + health
def summary(state):
    ev = list(iter_jsonl(state / "evolution" / "evaluations.jsonl"))
    st = {}
    for r in iter_jsonl(state / "evolution" / "statuses.jsonl"):
        st[(r["strategy_id"], r["context_id"])] = r["status"]
    counts = {}
    for v in st.values():
        counts[v] = counts.get(v, 0) + 1
    vb = [r for r in iter_jsonl(state / "evolution" / "validation_batches.jsonl") if r["kind"] == "RESULT"]
    return {"record_ids": sorted(r["record_id"] for r in ev), "contexts": sorted({r["context_id"] for r in ev}),
            "status_counts": counts, "validation_outcomes": [b.get("outcome") for b in vb],
            "lock_usage_file": (state / "lock_test_usage.json").exists(),
            "campaigns": sum(1 for _ in iter_jsonl(state / "locked_window_campaigns.jsonl"))}
a, b = summary(state_a), summary(state_b)
res["13_reproducible"] = {"same_context": a["contexts"] == b["contexts"],
                          "same_evaluation_records": a["record_ids"] == b["record_ids"],
                          "n_records_a": len(a["record_ids"]), "n_records_b": len(b["record_ids"]),
                          "status_counts_a": a["status_counts"], "status_counts_b": b["status_counts"],
                          "validation_outcomes_a": a["validation_outcomes"],
                          "locked_campaign_records_a": a["campaigns"], "lock_usage_file_a": a["lock_usage_file"]}
res["12_health_a"] = health(state_a, data_dir=real, results_dir=state_a.parent / "results")
print(json.dumps(res, indent=1, default=str))
