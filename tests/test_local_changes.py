import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.local_changes import (
    LocalChangeError,
    apply_local_change_plan,
    read_local_plan,
    validate_local_change_plan,
)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class LocalChangePlanTests(unittest.TestCase):
    def test_dry_run_validates_new_file_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            plan = {
                "schema_version": 1,
                "task_id": "create-readme",
                "changes": [{"path": "README.md", "content": "# Example\n"}],
            }
            result = validate_local_change_plan(plan, root)
            self.assertEqual(result["mode"], "validated_dry_run")
            self.assertFalse(result["writes_performed"])
            self.assertNotIn("content", result["changes"][0])
            self.assertEqual(result["changes"][0]["new_sha256"], digest(b"# Example\n"))
            self.assertFalse((root / "README.md").exists())

    def test_existing_file_requires_matching_hash_and_is_backed_up(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            target = root / "app.py"
            target.write_text("print('old')\n", encoding="utf-8")
            original = target.read_bytes()
            plan = {
                "schema_version": 1,
                "task_id": "update-app",
                "changes": [{
                    "path": "app.py",
                    "content": "print('new')\n",
                    "expected_sha256": digest(original),
                }],
            }
            result = apply_local_change_plan(plan, root, backup_root=base / "backups")
            self.assertEqual(target.read_text(encoding="utf-8"), "print('new')\n")
            self.assertTrue(result["writes_performed"])
            self.assertEqual(len(result["backups"]), 1)
            self.assertEqual(Path(result["backups"][0]["backup_path"]).read_bytes(), original)

    def test_stale_hash_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "app.py"
            target.write_text("actual", encoding="utf-8")
            plan = {
                "schema_version": 1,
                "task_id": "stale-plan",
                "changes": [{"path": "app.py", "content": "changed", "expected_sha256": "0" * 64}],
            }
            with self.assertRaises(LocalChangeError):
                apply_local_change_plan(plan, root, backup_root=root.parent / "backups")
            self.assertEqual(target.read_text(encoding="utf-8"), "actual")

    def test_rejects_protected_paths_and_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path in (".github/workflows/ci.yml", "../outside.py", "node_modules/a.js"):
                plan = {"schema_version": 1, "task_id": "unsafe", "changes": [{"path": path, "content": "x"}]}
                with self.subTest(path=path), self.assertRaises(LocalChangeError):
                    validate_local_change_plan(plan, root)

    def test_rejects_existing_file_without_hash_and_invalid_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.py").write_text("old", encoding="utf-8")
            plan = {"schema_version": 1, "task_id": "no-hash", "changes": [{"path": "a.py", "content": "new"}]}
            with self.assertRaises(LocalChangeError):
                validate_local_change_plan(plan, root)
            with self.assertRaises(LocalChangeError):
                validate_local_change_plan({"schema_version": 1, "task_id": "x", "changes": []}, root)

    def test_rejects_reparse_point_in_path_components(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            linked_dir = root / "linked"
            linked_dir.mkdir()
            plan = {
                "schema_version": 1,
                "task_id": "reparse-guard",
                "changes": [{"path": "linked/new.py", "content": "safe = True\\n"}],
            }

            def reparse_probe(path):
                candidate = Path(path)
                return candidate.name == "linked" and candidate.parent == root

            with patch("bot.local_changes._is_link_or_reparse", side_effect=reparse_probe):
                with self.assertRaisesRegex(LocalChangeError, "reparse-point"):
                    validate_local_change_plan(plan, root)

    def test_reads_bounded_json_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "plan.json"
            path.write_text('{"schema_version":1}', encoding="utf-8")
            self.assertEqual(read_local_plan(path)["schema_version"], 1)
            with self.assertRaises(LocalChangeError):
                read_local_plan(path, max_bytes=2)


if __name__ == "__main__":
    unittest.main()
