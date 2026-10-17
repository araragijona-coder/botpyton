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
                return candidate.name == "linked"

            with patch("bot.local_changes._is_link_or_reparse", side_effect=reparse_probe):
                with self.assertRaisesRegex(LocalChangeError, "reparse-point"):
                    validate_local_change_plan(plan, root)

    def test_rejects_reparse_point_project_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            plan = {
                "schema_version": 1,
                "task_id": "root-reparse-guard",
                "changes": [{"path": "new.py", "content": "safe = True\\n"}],
            }

            with patch("bot.local_changes._is_link_or_reparse", side_effect=lambda path: Path(path).name == "project"):
                with self.assertRaisesRegex(LocalChangeError, "Project root"):
                    validate_local_change_plan(plan, root)

    def test_new_file_publication_never_overwrites_concurrent_destination(self):
        import os

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            target = root / "new_file.py"
            plan = {
                "schema_version": 1,
                "task_id": "new-file-race",
                "changes": [{"path": "new_file.py", "content": "safe = True\\n"}],
            }
            sentinel = b"created concurrently; preserve me"
            real_link = os.link

            def create_collision_then_link(source, destination, *args, **kwargs):
                Path(destination).write_bytes(sentinel)
                return real_link(source, destination, *args, **kwargs)

            with patch("bot.local_changes.os.link", side_effect=create_collision_then_link):
                with self.assertRaises(LocalChangeError):
                    apply_local_change_plan(plan, root, backup_root=base / "backups")

            self.assertEqual(target.read_bytes(), sentinel)
            self.assertEqual(list(root.glob(".*.botpyton-tmp")), [])

    def test_existing_backup_with_different_content_is_never_overwritten(self):
        import json

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            target = root / "app.py"
            original = b"print('old')\\n"
            proposed = b"print('new')\\n"
            target.write_bytes(original)
            plan = {
                "schema_version": 1,
                "task_id": "backup-conflict",
                "changes": [{
                    "path": "app.py",
                    "content": proposed.decode("utf-8"),
                    "expected_sha256": digest(original),
                }],
            }
            fingerprint = digest(json.dumps(
                [{"path": "app.py", "new_sha256": digest(proposed), "expected_sha256": digest(original)}],
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8"))
            backup_path = base / "backups" / "backup-conflict" / fingerprint / "app.py"
            backup_path.parent.mkdir(parents=True)
            sentinel = b"pre-existing unrelated backup data"
            backup_path.write_bytes(sentinel)

            with self.assertRaisesRegex(LocalChangeError, "existing backup"):
                apply_local_change_plan(plan, root, backup_root=base / "backups")

            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(backup_path.read_bytes(), sentinel)

    def test_reads_bounded_json_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "plan.json"
            path.write_text('{"schema_version":1}', encoding="utf-8")
            self.assertEqual(read_local_plan(path)["schema_version"], 1)
            with self.assertRaises(LocalChangeError):
                read_local_plan(path, max_bytes=2)


if __name__ == "__main__":
    unittest.main()
