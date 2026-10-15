"""Operational failure behaviour of runner.main (synthetic, temp dirs only):
disk-full, held lock, missing data. None of them may look like success, and
none may create locked-TEST usage."""
from __future__ import annotations

import contextlib
import errno
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import runner  # noqa: E402
from engine_lib import ProcessLock  # noqa: E402


class TestMainFailures(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "data").mkdir()
        self.env = {"NEW2_DISCOVERY_ROOT": str(self.td / "root"), "NEW2_DATA_DIR": str(self.td / "data")}

    def tearDown(self):
        self._td.cleanup()

    def run_main(self, argv):
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = runner.main(argv)
        return rc, out.getvalue()

    def test_empty_data_dir_blocks_with_exit_4_and_no_lock_usage(self):
        rc, out = self.run_main(["--mode", "evolve"])
        self.assertEqual(rc, 4, out)
        self.assertFalse((self.td / "root" / "state" / "lock_test_usage.json").exists()
                         and "CONSUMED" in (self.td / "root" / "state" / "lock_test_usage.json").read_text())

    def test_disk_full_is_failed_not_success_and_lock_is_released(self):
        with mock.patch("autoloop.run_evolution_cycle", side_effect=OSError(errno.ENOSPC, "No space left")):
            rc, out = self.run_main(["--mode", "evolve"])
        self.assertEqual(rc, 1)
        self.assertIn("FAILED", out)
        self.assertIn("No space left", out)
        lk = ProcessLock(self.td / "root" / "state" / "run.lock")
        self.assertTrue(lk.acquire())            # not left behind
        lk.release()

    def test_second_cycle_is_skipped_while_lock_held(self):
        lk = ProcessLock(self.td / "root" / "state" / "run.lock")
        self.assertTrue(lk.acquire())
        try:
            rc, out = self.run_main(["--mode", "evolve"])
        finally:
            lk.release()
        self.assertEqual(rc, 3)

    def test_unsupported_mode_is_usage_error(self):
        rc, _ = self.run_main(["--mode", "live"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
