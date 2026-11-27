"""ADR 0085: every test gets its OWN throw-away shared locked-TEST record.

The shared record (``NEW2_LOCK_SHARED_DIR``, default ``run/discovery/locked_shared/``)
is repository-level by design, so tests that reuse names such as S1/BTCUSDT in
different temp state directories would block each other, and none may write the
committed directory.  Loaded first by discovery (alphabetical): it wraps
``unittest.TestCase.run`` so each test runs with a fresh shared directory.  Tests
that need two directories to share one patch the variable themselves in ``setUp``
(they run inside this wrapper, so theirs wins).  To run single test files, name this
module first: ``python -m unittest tests.test_00_isolate_shared_lock_dir tests.test_x``
(ADR 0116 F-15: ``discover -p <file>`` does NOT load this module)."""
import os
import tempfile
import unittest
from unittest import mock

_orig_run = unittest.TestCase.run


def _run_isolated(self, result=None):
    with tempfile.TemporaryDirectory(prefix="new2_shared_lock_") as d:
        with mock.patch.dict(os.environ, {"NEW2_LOCK_SHARED_DIR": d}):
            return _orig_run(self, result)


if not getattr(unittest.TestCase.run, "_new2_isolated", False):
    _run_isolated._new2_isolated = True
    unittest.TestCase.run = _run_isolated
