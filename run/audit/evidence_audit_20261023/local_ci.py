"""Run the `run:` steps of .github/workflows/research-ci.yml verbatim in a venv.

usage: local_ci.py <repo> <venv>   (pip installs inside steps are skipped: the venv is pre-provisioned
with the same pinned versions; everything else is executed exactly as written)."""
import os, subprocess, sys, time, yaml

repo, venv = sys.argv[1], sys.argv[2]
wf = yaml.safe_load(open(os.path.join(repo, ".github/workflows/research-ci.yml")))
env = dict(os.environ, PATH=os.path.join(venv, "bin") + ":" + os.environ["PATH"], VIRTUAL_ENV=venv)
fail = 0
for st in wf["jobs"]["test"]["steps"]:
    if "run" not in st:
        continue
    script = "\n".join(l for l in st["run"].splitlines() if "pip install" not in l)
    shell = st.get("shell", "bash --noprofile --norc -eo pipefail {0}").replace(" {0}", "").split()
    cwd = os.path.join(repo, st.get("working-directory", "."))
    t = time.time()
    p = subprocess.run(shell + ["-c", script], cwd=cwd, env=env, capture_output=True, text=True)
    ok = p.returncode == 0
    fail += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {st.get('name')} ({time.time()-t:.0f}s)")
    out = (p.stdout + p.stderr).strip().splitlines()
    print("\n".join("    " + l for l in out[-(40 if not ok else 4):]))
sys.exit(1 if fail else 0)
