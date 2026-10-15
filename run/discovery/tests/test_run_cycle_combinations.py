"""run_cycle.sh exit-code matrix (final residual fixes P0 §2).

Uses a fake runner (PYTHON) and a fake log writer (NEW2_DISC_TEE) in an isolated temp
directory: no market data, no real runner, no network.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DISC = Path(__file__).resolve().parent.parent


class TestRunCycleMatrix(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.root = self.td / "disc"
        self.fake_py = self._script("fake_py.sh", '#!/bin/sh\necho "runner-out args: $*"\nexit "${FAKE_RC:-0}"\n')
        self.good_tee = self._script("good_tee.sh", '#!/bin/sh\nexec tee "$@"\n')   # passthrough writer
        self.bad_tee = self._script("bad_tee.sh", '#!/bin/sh\ncat >/dev/null\nexit 7\n')

    def _script(self, name, text):
        p = self.td / name
        p.write_text(text)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        return str(p)

    def _run(self, args, *, rc=0, tee=None, log=None, audit=None, extra=None):
        env = dict(os.environ, NEW2_DISCOVERY_ROOT=str(self.root), PYTHON=self.fake_py,
                   FAKE_RC=str(rc), NEW2_DISC_TEE=tee or self.good_tee,
                   NEW2_DISC_LOG=log or str(self.root / "cycle_log.txt"))
        env.pop("NEW2_DISC_BATCH", None)
        if audit:
            env["NEW2_DISC_AUDIT"] = audit
        env.update(extra or {})
        r = subprocess.run(["bash", str(DISC / "run_cycle.sh"), *args], env=env, cwd=str(self.td),
                           capture_output=True, text=True, timeout=60)
        a = Path(audit or self.root / "cycle_audit.log")
        return r, (a.read_text() if a.exists() else "")

    # --- 8 required combinations -------------------------------------------------
    def test_1_runner_ok_log_ok(self):
        r, audit = self._run(["3"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CYCLE_OK batch=3", r.stdout)
        self.assertIn("runner_rc=0 log_writer_rc=0", audit)
        self.assertIn("--batch 3", (self.root / "cycle_log.txt").read_text())

    def test_2_runner_fail_log_ok(self):
        r, audit = self._run(["1"], rc=5)
        self.assertEqual(r.returncode, 1)
        self.assertIn("CYCLE_FAILED rc=5", r.stdout)
        self.assertIn("runner_rc=5 log_writer_rc=0", audit)
        self.assertNotIn("CYCLE_LOG_ERROR", r.stderr)

    def test_3_runner_ok_log_fail(self):
        r, audit = self._run(["1"], tee=self.bad_tee)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("CYCLE_OK", r.stdout)
        self.assertIn("CYCLE_LOG_ERROR", r.stderr)
        self.assertIn("writer rc=7", r.stderr)
        self.assertIn("runner_rc=0 log_writer_rc=7", audit)
        self.assertIn("log_failure_after_runner_ok", audit)

    def test_4_runner_fail_log_fail_records_both(self):
        r, audit = self._run(["1"], rc=4, tee=self.bad_tee)
        self.assertEqual(r.returncode, 1)
        self.assertIn("CYCLE_FAILED rc=4", r.stdout)                 # runner failure not overwritten
        self.assertIn("runner rc=4", r.stderr)
        self.assertIn("writer rc=7", r.stderr)
        self.assertIn("runner_rc=4 log_writer_rc=7", audit)

    def test_5_partial_ok_and_partial_log_fail(self):
        r, audit = self._run(["1"], rc=2)
        self.assertEqual(r.returncode, 2)
        self.assertIn("CYCLE_PARTIAL", r.stdout)
        r, audit = self._run(["1"], rc=2, tee=self.bad_tee)
        self.assertEqual(r.returncode, 1)                             # documented policy: lost record -> FAILED
        self.assertIn("CYCLE_LOG_ERROR", r.stderr)
        self.assertIn("log_failure_after_partial", audit)
        self.assertNotIn("CYCLE_PARTIAL", r.stdout)

    def test_6_skipped_ok_and_skipped_log_fail(self):
        r, audit = self._run(["1"], rc=3)
        self.assertEqual(r.returncode, 3)
        self.assertIn("CYCLE_SKIPPED", r.stdout)
        r, audit = self._run(["1"], rc=3, tee=self.bad_tee)
        self.assertEqual(r.returncode, 3)                             # nothing ran: status kept, error recorded
        self.assertIn("CYCLE_LOG_ERROR", r.stderr)
        self.assertIn("LOG_ERROR_ON_SKIP", audit)

    def test_7_log_dir_creation_fails(self):
        blocker = self.td / "blocker"
        blocker.write_text("a file, not a directory")
        r, audit = self._run(["1"], log=str(blocker / "sub" / "cycle.log"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("CYCLE_FAILED cannot create log directory", r.stdout)
        self.assertIn("log_dir_create_failed", r.stderr)
        self.assertIn("log_dir_create_failed", audit)
        self.assertNotIn("runner-out", r.stdout)                      # runner never started

    def test_7b_real_tee_on_unwritable_log(self):
        # real tee, log path is a directory -> tee fails; runner rc must still be reported
        d = self.td / "logdir"
        d.mkdir()
        env_tee = "tee"
        r, audit = self._run(["1"], rc=2, tee=env_tee, log=str(d))
        self.assertEqual(r.returncode, 1)
        self.assertIn("CYCLE_LOG_ERROR", r.stderr)
        self.assertIn("runner_rc=2", audit)

    def test_8_batch_argument(self):
        r, _ = self._run(["7"])
        self.assertIn("--batch 7", r.stdout)
        r, _ = self._run([], extra={"NEW2_DISC_BATCH": "4"})
        self.assertIn("--batch 4", r.stdout)
        r, _ = self._run([])
        self.assertIn("--batch 10", r.stdout)
        for bad in (["abc"], ["0"], ["-1"], ["1", "2"], ["1.5"], [""], ["1000000"]):
            r, _ = self._run(bad)
            self.assertEqual(r.returncode, 1, bad)
            self.assertIn("CYCLE_FAILED usage", r.stdout)
            self.assertNotIn("runner-out", r.stdout)
        r, _ = self._run([], extra={"NEW2_DISC_BATCH": "zzz"})
        self.assertEqual(r.returncode, 1)

    def test_audit_write_failure_is_reported_not_fatal_to_status(self):
        r, _ = self._run(["1"], rc=3, audit=str(self.td / "nodir" / "x" / "audit.log"))
        self.assertEqual(r.returncode, 3)
        self.assertIn("CYCLE_AUDIT_WRITE_FAILED", r.stderr)


if __name__ == "__main__":
    unittest.main()
