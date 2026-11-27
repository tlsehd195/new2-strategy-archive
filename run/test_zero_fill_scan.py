"""ADR 0116: zero-fill ratchet — temp directory only."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("zero_fill_scan", HERE / "audit" / "zero_fill_scan.py")
zfs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zfs)


class ZeroFillScanTest(unittest.TestCase):
    def test_patterns(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "run"
            run.mkdir()
            (run / "a.py").write_text("\n".join([
                "x = s.fillna(0.0)",                       # counted
                "y = np.nan_to_num(r)",                    # counted
                "z = np.nan_to_num(r, nan=-9)",            # ranking sentinel: not counted
                "w = s.reindex(ix, fill_value=0)",         # counted
                "v = s.reindex(ix, fill_value=0.5)",       # not zero
                "u = s.fillna(0)  # zero-fill-ok: flat book -> 0 PnL observed",
                "# s.fillna(0) in a comment",
            ]) + "\n")
            (run / "test_b.py").write_text("s.fillna(0)\n")   # tests are not scanned
            now = zfs.scan(run)
            self.assertEqual(now, {"run/a.py": 3})
            self.assertEqual(zfs.problems(now, {"run/a.py": 3}), [])
            self.assertEqual(len(zfs.problems(now, {"run/a.py": 2})), 1)
            self.assertEqual(len(zfs.problems({"run/new.py": 1}, {})), 1)

    def test_repository_has_no_increase(self):
        self.assertEqual(zfs.main(["check"]), 0)


if __name__ == "__main__":
    unittest.main()
