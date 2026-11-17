"""ADR 0085: point the shared locked-TEST record at a throw-away directory for the
whole unittest process, so no test ever writes to the committed
``run/discovery/locked_shared/`` (loaded first: alphabetical discovery order).
Tests that need their own shared directory patch NEW2_LOCK_SHARED_DIR themselves."""
import atexit
import os
import shutil
import tempfile

if not os.environ.get("NEW2_LOCK_SHARED_DIR"):
    _d = tempfile.mkdtemp(prefix="new2_shared_lock_")
    os.environ["NEW2_LOCK_SHARED_DIR"] = _d
    atexit.register(shutil.rmtree, _d, ignore_errors=True)
