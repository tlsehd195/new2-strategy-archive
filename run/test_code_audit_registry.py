"""ADR 0116 §10: code audit registry — temp git repository only (never the real tree)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("code_audit_registry", HERE / "audit" / "code_audit_registry.py")
car = importlib.util.module_from_spec(spec)
spec.loader.exec_module(car)


def sh(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        sh(self.root, "init", "-q")
        sh(self.root, "config", "user.email", "t@example.invalid")
        sh(self.root, "config", "user.name", "t")
        (self.root / "run" / "audit").mkdir(parents=True)
        (self.root / "run" / "a.py").write_text("x = 1\n")
        (self.root / "run" / "test_a.py").write_text("import a\n")
        (self.root / "run" / "audit" / "seed.jsonl").write_text(json.dumps(
            {"file": "a.py", "audit_level": "FULL_SOURCE_REVIEW",
             "reviewed_against": {"commit": "c0", "sha256": car.hashlib.sha256(b"x = 1\n").hexdigest()}}) + "\n")
        sh(self.root, "add", "-A")
        sh(self.root, "commit", "-qm", "base")
        self.saved = {k: getattr(car, k) for k in ("REPO", "OUT_JSON", "OUT_MD", "SEED", "BASELINE_COMMIT")}
        car.REPO, car.SEED = self.root, self.root / "run" / "audit" / "seed.jsonl"
        car.OUT_JSON, car.OUT_MD = self.root / "reg.json", self.root / "reg.md"
        car.BASELINE_COMMIT = sh(self.root, "rev-parse", "HEAD")
        self.assertEqual(car.cmd_init(), 0)

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(car, k, v)
        self.tmp.cleanup()

    def rows(self):
        return {r["path"]: r for r in car.load()["files"]}

    def test_seed_and_clean_check(self):
        r = self.rows()
        self.assertEqual(r["run/a.py"]["status"], "FULL_SOURCE_REVIEW")
        self.assertEqual(r["run/a.py"]["tests"], ["run/test_a.py"])
        self.assertEqual(r["run/test_a.py"]["status"], "NOT_REVIEWED")
        self.assertEqual(car.problems(car.load()), [])

    def test_change_is_stale_then_marked_changed_not_laundered(self):
        (self.root / "run" / "a.py").write_text("x = 2\n")
        self.assertTrue(any(p.startswith("STALE run/a.py") for p in car.problems(car.load())))
        car.cmd_build()
        r = self.rows()["run/a.py"]
        self.assertEqual((r["status"], r["prior_status"]), ("CHANGED_SINCE_REVIEW", "FULL_SOURCE_REVIEW"))
        self.assertEqual(car.problems(car.load()), [])          # honest status: allowed
        (self.root / "run" / "a.py").write_text("x = 1\n")      # reverted: claim comes back
        car.cmd_build()
        self.assertEqual(self.rows()["run/a.py"]["status"], "FULL_SOURCE_REVIEW")

    def test_new_file_unregistered_then_untested(self):
        (self.root / "run" / "b.py").write_text("y = 1\n")
        self.assertIn("UNREGISTERED run/b.py", " ".join(car.problems(car.load())))
        car.cmd_build()
        self.assertIn("UNTESTED_NEW_CODE run/b.py", " ".join(car.problems(car.load())))
        (self.root / "run" / "test_b.py").write_text("from b import y\n")
        car.cmd_build()
        self.assertEqual(car.problems(car.load()), [])

    def test_exemption_needs_a_note(self):
        (self.root / "run" / "c.sh").write_text("echo\n")
        car.cmd_build()
        self.assertEqual(car.cmd_review("run/c.sh", "EXEMPTED_WITH_REASON", "t", ""), 1)
        self.assertEqual(car.cmd_review("run/c.sh", "EXEMPTED_WITH_REASON", "t", "one-off helper"), 0)
        self.assertEqual(car.problems(car.load()), [])

    def test_deleted_or_moved_file(self):
        (self.root / "run" / "a.py").rename(self.root / "run" / "a2.py")
        bad = " ".join(car.problems(car.load()))
        self.assertIn("MISSING run/a.py", bad)
        self.assertIn("UNREGISTERED run/a2.py", bad)
        car.cmd_build()
        reg = car.load()
        self.assertEqual(reg["removed"][0]["path"], "run/a.py")
        self.assertEqual(self.rows()["run/a2.py"]["status"], "NOT_REVIEWED")   # a move does not carry a claim

    def test_forged_claim_is_caught(self):
        reg = car.load()
        for r in reg["files"]:
            if r["path"] == "run/test_a.py":
                r["status"] = "TESTED"                              # no review hash
        car.save(reg)
        self.assertIn("HASH_MISMATCH run/test_a.py", " ".join(car.problems(car.load())))
        reg["files"][0]["status"] = "VERIFIED"
        car.save(reg)
        self.assertIn("BAD_STATUS", " ".join(car.problems(car.load())))

    def test_review_records_hash_and_reviewer(self):
        self.assertEqual(car.cmd_review("run/test_a.py", "FULL_SOURCE_REVIEW", "author", "read"), 0)
        r = self.rows()["run/test_a.py"]
        self.assertEqual((r["reviewed_sha256"], r["review_by"]), (r["sha256"], "author"))
        self.assertEqual(car.cmd_review("run/test_a.py", "CHANGED_SINCE_REVIEW", "author", "x"), 1)


if __name__ == "__main__":
    unittest.main()
