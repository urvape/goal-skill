#!/usr/bin/env python3
"""End-to-end tests for undo_journal.py (stdlib only, no dependencies).

Run from anywhere:
    python .agents/skills/goal/scripts/test_undo_journal.py
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "undo_journal.py"


def run(sandbox: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(sandbox),
        capture_output=True,
        text=True,
    )


class UndoJournalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="undo-journal-test-")
        self.sandbox = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, content):
        path = self.sandbox / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def read(self, rel):
        return (self.sandbox / rel).read_text(encoding="utf-8")

    def journal_index(self):
        return json.loads((self.sandbox / ".freebuff" / "undo-journal" / "index.json").read_text(encoding="utf-8"))

    # --- core rollback behavior ---

    def test_rollback_restores_original_bytes(self):
        target = self.sandbox / "src" / "app.py"
        target.parent.mkdir(parents=True)
        original = b"v0 \x00 raw bytes\r\nsecond line\n"
        target.write_bytes(original)

        result = run(self.sandbox, "snapshot", "--milestone", "m1", "src/app.py")
        self.assertEqual(result.returncode, 0, result.stderr)

        target.write_bytes(b"v1 mangled \xff")
        result = run(self.sandbox, "rollback", "--milestone", "m1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), original)
        self.assertIn("restored src/app.py", result.stdout)

    def test_rollback_removes_created_file(self):
        created = self.sandbox / "newfile.txt"
        result = run(self.sandbox, "snapshot", "--milestone", "m1", "newfile.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("absent", result.stdout)

        created.write_text("brand new", encoding="utf-8")
        result = run(self.sandbox, "rollback", "--milestone", "m1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(created.exists())
        self.assertIn("removed  newfile.txt", result.stdout)

    def test_rollback_restores_deleted_file(self):
        self.write("gone.txt", "bring me back")
        run(self.sandbox, "snapshot", "--milestone", "m1", "gone.txt")
        (self.sandbox / "gone.txt").unlink()

        result = run(self.sandbox, "rollback", "--milestone", "m1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read("gone.txt"), "bring me back")

    # --- precision guarantees ---

    def test_milestone_scope_is_precise(self):
        self.write("f.txt", "v0")
        run(self.sandbox, "snapshot", "--milestone", "m1", "f.txt")
        self.write("f.txt", "v1")
        run(self.sandbox, "snapshot", "--milestone", "m2", "f.txt")
        self.write("f.txt", "v2")
        self.write("untouched.txt", "outside any milestone")

        self.assertEqual(run(self.sandbox, "rollback", "--milestone", "m2").returncode, 0)
        self.assertEqual(self.read("f.txt"), "v1")  # m1's edit survives

        self.assertEqual(run(self.sandbox, "rollback", "--milestone", "m1").returncode, 0)
        self.assertEqual(self.read("f.txt"), "v0")
        self.assertEqual(self.read("untouched.txt"), "outside any milestone")

    def test_preexisting_changes_are_preserved(self):
        self.write("f.txt", "user's pre-session edit")
        run(self.sandbox, "snapshot", "--milestone", "m1", "f.txt")
        self.write("f.txt", "agent overwrote it")

        run(self.sandbox, "rollback", "--milestone", "m1")
        self.assertEqual(self.read("f.txt"), "user's pre-session edit")

    def test_first_snapshot_wins(self):
        self.write("f.txt", "v0")
        run(self.sandbox, "snapshot", "--milestone", "m1", "f.txt")
        self.write("f.txt", "v1")
        again = run(self.sandbox, "snapshot", "--milestone", "m1", "f.txt")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("keep     f.txt", again.stdout)

        run(self.sandbox, "rollback", "--milestone", "m1")
        self.assertEqual(self.read("f.txt"), "v0")

    def test_second_rollback_is_noop(self):
        self.write("f.txt", "v0")
        run(self.sandbox, "snapshot", "--milestone", "m1", "f.txt")
        self.write("f.txt", "v1")
        run(self.sandbox, "rollback", "--milestone", "m1")
        self.write("f.txt", "post-rollback edit")

        second = run(self.sandbox, "rollback", "--milestone", "m1")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already rolled back", second.stdout)
        self.assertEqual(self.read("f.txt"), "post-rollback edit")

    # --- safety refusals ---

    def test_path_escape_is_refused(self):
        outside = self.sandbox.parent / f"{self.sandbox.name}-outside.txt"
        result = run(self.sandbox, "snapshot", "--milestone", "m1", f"../{outside.name}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the workspace", result.stderr)
        self.assertFalse(outside.exists())
        self.assertFalse((self.sandbox / ".freebuff").exists())

    def test_directory_is_refused(self):
        (self.sandbox / "adir").mkdir()
        result = run(self.sandbox, "snapshot", "--milestone", "m1", "adir")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("directory", result.stderr)

    def test_missing_snapshot_aborts_without_touching_files(self):
        self.write("f.txt", "v0")
        run(self.sandbox, "snapshot", "--milestone", "m1", "f.txt")
        self.write("f.txt", "v1")

        entry = self.journal_index()["milestones"]["m1"]["entries"][0]
        snapshot_path = self.sandbox / ".freebuff" / "undo-journal" / entry["snapshot"]
        snapshot_path.unlink()

        result = run(self.sandbox, "rollback", "--milestone", "m1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("snapshot missing", result.stderr)
        self.assertEqual(self.read("f.txt"), "v1")

    def test_list_unknown_milestone_and_clear(self):
        self.write("f.txt", "v0")
        run(self.sandbox, "snapshot", "--milestone", "m1", "f.txt")

        listing = run(self.sandbox, "list")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn("m1 [active]", listing.stdout)

        run(self.sandbox, "rollback", "--milestone", "m1")
        listing = run(self.sandbox, "list")
        self.assertIn("m1 [rolled-back]", listing.stdout)

        missing = run(self.sandbox, "rollback", "--milestone", "nope")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("unknown milestone", missing.stderr)

        refused = run(self.sandbox, "clear")
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue((self.sandbox / ".freebuff" / "undo-journal" / "index.json").exists())

        cleared = run(self.sandbox, "clear", "--yes")
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertFalse((self.sandbox / ".freebuff" / "undo-journal").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
