"""Operations: health check and a bounded scheduler for research cycles.

``python3 ops.py health``  — read-only state inspection (exit 0 OK,
2 DEGRADED, 1 FAILED).  It never repairs anything: a corrupt ledger is
reported with its path and the operator decides (the engine itself
quarantines only torn tails, see ``engine_lib.repair_jsonl_tail``).

``python3 ops.py loop --max-cycles N [--interval S] [-- runner args]`` —
runs ``runner.main`` up to N times (N is required: there is no endless
mode), sleeping S seconds in between, and stops early on the first
FAILED cycle (exit 1), on BLOCKED_DATA (exit 4), or after
``--max-no-progress`` cycles without progress.  A loop that ran to its
limit exits 0 only if no cycle was PARTIAL_FAILURE (runner exit 2);
otherwise it exits 2.  Every cycle's exit code
and summary line is appended to ``state/ops_loop.jsonl``.  Single process
only: concurrency is excluded by ``state/run.lock`` (a second loop gets
CYCLE_SKIPPED, exit 3, and stops).  No alerting is configured here; the
loop log is the record.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

from engine_lib import JsonlCorruption, append_jsonl, iso_now, iter_jsonl

LOW_DISK_BYTES = 200 * 1024 * 1024
CONFIGS = {"promotion_v2.json": "promotion_config_v1", "promotion_v3.json": "promotion_config_v1",
           "promotion_v4.json": "promotion_config_v1", "promotion_v5.json": "promotion_config_v1",
           "fills_import_v1.json": "fills_import_policy_v1",
           "cost_verification_v1.json": "cost_verification_criteria_v1",
           "paper_forward_v1.json": "paper_forward_criteria_v1",
           "cost_assumed_public_v1.json": "cost_assumed_profile_v1",
           "cost_public_estimate_v1.json": "cost_public_estimate_criteria_v1",
           "cost_public_estimate_v2.json": "cost_public_estimate_criteria_v2"}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def health(state_dir: Path, *, data_dir: Optional[Path] = None,
           config_dir: Optional[Path] = None, results_dir: Optional[Path] = None) -> dict:
    state_dir = Path(state_dir)
    config_dir = Path(config_dir or Path(__file__).resolve().parent / "config")
    checks: dict[str, dict] = {}
    # lock
    lk = state_dir / "run.lock"
    if not lk.exists():
        checks["lock"] = {"status": "FREE"}
    else:
        try:
            pid = int(lk.read_text().strip())
            checks["lock"] = {"status": "HELD" if _pid_alive(pid) else "STALE", "pid": pid}
        except (ValueError, OSError) as e:
            checks["lock"] = {"status": "STALE", "error": str(e)}
    # ledgers
    bad, n = [], 0
    if state_dir.exists():
        for p in sorted(state_dir.rglob("*.jsonl")):
            n += 1
            try:
                for _ in iter_jsonl(p):
                    pass
            except JsonlCorruption as e:
                bad.append({"path": str(p), "error": str(e)})
            except OSError as e:
                bad.append({"path": str(p), "error": f"{type(e).__name__}: {e}"})
    quarantined = sorted(str(p) for p in state_dir.rglob("*.quarantine*")) if state_dir.exists() else []
    checks["ledgers"] = {"status": "CORRUPT" if bad else "OK", "files": n, "corrupt": bad,
                         "quarantined": quarantined}
    # evidence hashes of the evolution evaluation ledger
    ev = state_dir / "evolution" / "evaluations.jsonl"
    if ev.exists() and not bad:
        from pipeline import record_hash
        tampered = [r.get("record_id") for r in iter_jsonl(ev) if r.get("record_id") != record_hash(r)]
        checks["evidence"] = {"status": "TAMPERED" if tampered else "OK", "tampered": tampered[:20]}
    # locked TEST registry
    reg = state_dir / "lock_test_usage.json"
    if reg.exists():
        try:
            json.loads(reg.read_text())
            checks["lock_registry"] = {"status": "OK"}
        except (ValueError, OSError) as e:
            checks["lock_registry"] = {"status": "CORRUPT", "error": str(e)}
    # configuration files
    cfg_bad = []
    for name, schema in CONFIGS.items():
        try:
            c = json.loads((config_dir / name).read_text())
            if c.get("schema") != schema:
                cfg_bad.append(f"{name}: schema {c.get('schema')!r} != {schema!r}")
        except (OSError, ValueError) as e:
            cfg_bad.append(f"{name}: {type(e).__name__}: {e}")
    checks["config"] = {"status": "INVALID" if cfg_bad else "OK", "errors": cfg_bad}
    # disk
    try:
        free = shutil.disk_usage(state_dir if state_dir.exists() else state_dir.parent).free
        checks["disk"] = {"status": "LOW" if free < LOW_DISK_BYTES else "OK", "free_bytes": free}
    except OSError as e:
        checks["disk"] = {"status": "UNKNOWN", "error": str(e)}
    # data directory
    if data_dir is not None:
        checks["data_dir"] = {"status": "OK" if Path(data_dir).is_dir() else "MISSING", "path": str(data_dir)}
    # last cycle
    rd = Path(results_dir) if results_dir else None
    summ = None
    for cand in ([rd / "evolution_cycle_summary.json"] if rd else []) + [state_dir / "evolution" /
                                                                          "evolution_cycle_summary.json"]:
        if cand.exists():
            try:
                summ = json.loads(cand.read_text())
            except ValueError as e:
                checks["last_cycle"] = {"status": "CORRUPT", "error": str(e)}
            break
    if summ is not None:
        checks["last_cycle"] = {"status": summ.get("status"), "finished_at": summ.get("finished_at"),
                                "context_id": summ.get("context_id")}
        if summ.get("status") == "CYCLE_RUNNING" and checks["lock"]["status"] != "HELD":
            # nothing is running: the cycle that wrote this marker was killed
            checks["last_cycle"]["status"] = "CYCLE_INTERRUPTED"
    failed = (checks["ledgers"]["status"] != "OK" or checks.get("evidence", {}).get("status") == "TAMPERED"
              or checks.get("lock_registry", {}).get("status") == "CORRUPT"
              or checks["config"]["status"] != "OK" or checks.get("data_dir", {}).get("status") == "MISSING")
    degraded = (checks["lock"]["status"] == "STALE" or checks["disk"]["status"] != "OK"
                or checks.get("last_cycle", {}).get("status") in ("CYCLE_FAILED", "CORRUPT",
                                                                   "CYCLE_INTERRUPTED"))
    return {"status": "FAILED" if failed else "DEGRADED" if degraded else "OK", "checks": checks,
            "at": iso_now()}


def run_loop(main: Callable[[Sequence[str]], int], runner_args: Sequence[str], *, state_dir: Path,
             max_cycles: int, interval: float = 0.0, max_no_progress: int = 3,
             sleep: Callable[[float], None] = time.sleep) -> dict:
    """Bounded repetition of ``main(runner_args)``.  Returns the loop record."""
    if max_cycles < 1:
        raise ValueError("max_cycles must be >= 1 (no endless mode)")
    log = Path(state_dir) / "ops_loop.jsonl"
    no_prog, cycles, stop = 0, [], "MAX_CYCLES"
    for k in range(max_cycles):
        out = io.StringIO()
        t0 = time.time()
        try:
            with contextlib.redirect_stdout(out):
                rc = main(list(runner_args))
        except Exception as e:  # noqa: BLE001 — a crash is a failed cycle, never success
            rc = 1
            out.write(json.dumps({"status": "FAILED", "fatal": f"{type(e).__name__}: {e}"}))
        line = out.getvalue().strip().splitlines()[-1:] or [""]
        try:
            summ = json.loads(line[0])
        except ValueError:
            summ = {"raw": line[0][:500]}
        rec = {"cycle": k + 1, "exit": rc, "status": summ.get("status"), "seconds": round(time.time() - t0, 3),
               "at": iso_now()}
        append_jsonl(log, rec)
        cycles.append(rec)
        if rc == 1:
            stop = "CYCLE_FAILED"
            break
        if rc == 3:
            stop = "LOCK_HELD"
            break
        if rc == 4:
            stop = "BLOCKED_DATA"
            break
        no_prog = no_prog + 1 if summ.get("status") in ("CYCLE_NO_PROGRESS", "CYCLE_BUDGET_EXHAUSTED") else 0
        if no_prog >= max_no_progress:
            stop = "NO_PROGRESS"
            break
        if k + 1 < max_cycles and interval > 0:
            sleep(interval)
    partial = sum(1 for c in cycles if c["exit"] == 2)
    # a loop that ran to its limit is still not a success if a cycle only partly
    # succeeded (runner exit 2): the loop exit says so instead of 0
    exit_code = (0 if partial == 0 else 2) if stop in ("MAX_CYCLES", "NO_PROGRESS") \
        else (cycles[-1]["exit"] if cycles else 1) or 1
    res = {"stopped": stop, "cycles": len(cycles), "last_exit": cycles[-1]["exit"] if cycles else None,
           "partial_cycles": partial, "exit": exit_code, "at": iso_now()}
    append_jsonl(log, {"loop_end": res})
    return res


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("health")
    h.add_argument("--state-dir")
    lp = sub.add_parser("loop")
    lp.add_argument("--max-cycles", type=int, required=True)
    lp.add_argument("--interval", type=float, default=0.0)
    lp.add_argument("--max-no-progress", type=int, default=3)
    lp.add_argument("runner_args", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    from runner import PathConfig, PathConfigError
    if a.cmd == "health":
        try:
            pc = PathConfig.from_env()
            sd, dd, rd = Path(a.state_dir) if a.state_dir else pc.state_dir, pc.data_dir, pc.results_dir
        except PathConfigError as e:
            print(json.dumps({"status": "FAILED", "error": str(e)}))
            return 1
        r = health(sd, data_dir=dd, results_dir=rd)
        print(json.dumps(r, indent=1, default=str))
        return {"OK": 0, "DEGRADED": 2}.get(r["status"], 1)
    import runner
    try:
        pc = PathConfig.from_env()
    except PathConfigError as e:
        print(f"CONFIG_ERROR: {e}", file=sys.stderr)
        return 1
    args = [x for x in a.runner_args if x != "--"]
    r = run_loop(runner.main, args, state_dir=pc.state_dir, max_cycles=a.max_cycles,
                 interval=a.interval, max_no_progress=a.max_no_progress)
    print(json.dumps(r))
    return r["exit"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
