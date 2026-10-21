import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.local_changes import (
    LocalChangeError,
    apply_local_change_plan,
    read_local_plan,
    validate_local_change_plan,
    inspect_local_change_transaction,
    rollback_local_change_transaction,
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

    def test_protected_paths_are_case_insensitive_for_windows_compatibility(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = (
                ".GIT/hooks/pre-commit.py",
                ".GiThUb/workflows/ci.yml",
                ".CEREBRO/CONFIG.JSON",
                ".VENV/tools.py",
                "NODE_MODULES/loader.js",
                "DiSt/generated.py",
                "BuIlD/generated.py",
            )
            for path in paths:
                plan = {
                    "schema_version": 1,
                    "task_id": "mixed-case-protected",
                    "changes": [{"path": path, "content": "unsafe = True\\n"}],
                }
                with self.subTest(path=path), self.assertRaisesRegex(
                    LocalChangeError, "Protected path"
                ):
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
            injected = False

            def create_collision_then_link(source, destination, *args, **kwargs):
                nonlocal injected
                candidate = Path(destination)
                if (
                    not injected
                    and candidate.name.casefold() == target.name.casefold()
                    and candidate.parent.name.casefold() == root.name.casefold()
                ):
                    candidate.write_bytes(sentinel)
                    injected = True
                return real_link(source, destination, *args, **kwargs)

            with patch("bot.local_changes.os.link", side_effect=create_collision_then_link):
                with self.assertRaises(LocalChangeError):
                    apply_local_change_plan(plan, root, backup_root=base / "backups")

            self.assertTrue(injected, "the race must be injected at the project-file destination")
            self.assertEqual(target.read_bytes(), sentinel)
            self.assertEqual(list(root.glob(".*.botpyton-tmp")), [])

    def test_new_file_cleanup_failure_rolls_back_the_published_destination(self):
        import os

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            target = root / "new_after_cleanup.py"
            plan = {
                "schema_version": 1,
                "task_id": "new-cleanup-recovery",
                "changes": [{"path": target.name, "content": "VALUE = 2\\n"}],
            }
            real_unlink = os.unlink
            injected = False

            def fail_first_staging_cleanup(path, *args, **kwargs):
                nonlocal injected
                candidate = Path(path)
                if (
                    not injected
                    and target.name in str(path)
                    and candidate.name.endswith(".botpyton-tmp")
                ):
                    injected = True
                    raise PermissionError("injected post-publication staging cleanup failure")
                return real_unlink(path, *args, **kwargs)

            with patch("bot.local_changes.os.unlink", side_effect=fail_first_staging_cleanup):
                with self.assertRaisesRegex(
                    LocalChangeError, "Staging alias cleanup failed after publication"
                ):
                    apply_local_change_plan(plan, root, backup_root=base / "backups")

            self.assertTrue(injected, "failure must occur after the new destination was linked")
            self.assertFalse(target.exists(), "a confirmed BOTPYTON publication must be rolled back")
            self.assertEqual(
                list(root.glob(f".{target.name}.*.botpyton-tmp")),
                [],
                "the cleanup retry should remove the staging alias",
            )

    def test_existing_file_changed_during_backup_is_not_overwritten(self):
        import os

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
                "task_id": "existing-file-race",
                "changes": [{
                    "path": "app.py",
                    "content": proposed.decode("utf-8"),
                    "expected_sha256": digest(original),
                }],
            }
            sentinel = b"external change must survive"
            real_fsync = os.fsync
            fsync_count = 0
            changed = False

            def inject_external_change(fd):
                nonlocal changed, fsync_count
                result = real_fsync(fd)
                fsync_count += 1
                # The durable transaction journal is fsynced first. Inject the
                # conflict after the original backup has been staged instead.
                if not changed and fsync_count >= 2:
                    changed = True
                    target.write_bytes(sentinel)
                return result

            with patch("bot.local_changes.os.fsync", side_effect=inject_external_change):
                with self.assertRaises(LocalChangeError):
                    apply_local_change_plan(plan, root, backup_root=base / "backups")

            self.assertTrue(changed)
            self.assertEqual(target.read_bytes(), sentinel)
            self.assertEqual(
                next((base / "backups").rglob("app.py")).read_bytes(),
                original,
                "the backup must retain the original bytes even when a concurrent change is detected",
            )

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

    def test_successful_multifile_plan_persists_content_free_transaction_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            existing = root / "app.py"
            existing.write_bytes(b"VALUE = 1\n")
            backup_root = base / "backups"
            plan = {
                "schema_version": 1,
                "task_id": "journal-success",
                "changes": [
                    {
                        "path": "app.py",
                        "content": "VALUE = 2\n",
                        "expected_sha256": digest(b"VALUE = 1\n"),
                    },
                    {"path": "new.py", "content": "VALUE = 3\n"},
                ],
            }
            result = apply_local_change_plan(plan, root, backup_root=backup_root)
            journal_path = Path(result["transaction_journal_path"])
            journal = json.loads(journal_path.read_text(encoding="utf-8"))

            self.assertEqual(journal["status"], "completed")
            self.assertEqual(journal["transaction_id"], result["transaction_id"])
            self.assertEqual(
                [item["status"] for item in journal["changes"]],
                ["published", "published"],
            )
            self.assertTrue(all("content" not in item for item in journal["changes"]))
            self.assertEqual(existing.read_bytes(), b"VALUE = 2\n")
            self.assertEqual((root / "new.py").read_bytes(), b"VALUE = 3\n")
            self.assertEqual(
                inspect_local_change_transaction(journal_path, root)["writes_performed"],
                False,
            )

    def test_explicit_transaction_rollback_restores_originals_and_removes_new_files(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            existing = root / "app.py"
            original = b"VALUE = 1\n"
            existing.write_bytes(original)
            plan = {
                "schema_version": 1,
                "task_id": "journal-rollback",
                "changes": [
                    {
                        "path": "app.py",
                        "content": "VALUE = 2\n",
                        "expected_sha256": digest(original),
                    },
                    {"path": "new.py", "content": "VALUE = 3\n"},
                ],
            }
            result = apply_local_change_plan(plan, root, backup_root=base / "backups")
            journal_path = Path(result["transaction_journal_path"])

            rolled_back = rollback_local_change_transaction(
                journal_path, root, expected_transaction_id=result["transaction_id"]
            )

            self.assertEqual(rolled_back["recovery"], "rollback_complete")
            self.assertEqual(rolled_back["transaction_status"], "rolled_back")
            self.assertEqual(existing.read_bytes(), original)
            self.assertFalse((root / "new.py").exists())
            self.assertTrue(rolled_back["writes_performed"])

    def test_transaction_rollback_requires_exact_transaction_id(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            result = apply_local_change_plan(
                {
                    "schema_version": 1,
                    "task_id": "journal-id-guard",
                    "changes": [{"path": "new.py", "content": "safe = True\n"}],
                },
                root,
                backup_root=base / "backups",
            )
            target = root / "new.py"
            with self.assertRaisesRegex(LocalChangeError, "confirmation does not match"):
                rollback_local_change_transaction(
                    result["transaction_journal_path"], root,
                    expected_transaction_id="0" * 64,
                )
            self.assertEqual(target.read_text(encoding="utf-8"), "safe = True\n")

    def test_transaction_rollback_preserves_external_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            target = root / "existing.py"
            target.write_bytes(b"VALUE = 1\n")
            plan = {
                "schema_version": 1,
                "task_id": "journal-external-change",
                "changes": [{
                    "path": "existing.py",
                    "content": "VALUE = 2\n",
                    "expected_sha256": digest(b"VALUE = 1\n"),
                }],
            }
            result = apply_local_change_plan(plan, root, backup_root=base / "backups")
            target.write_bytes(b"external update\n")

            report = rollback_local_change_transaction(
                result["transaction_journal_path"], root,
                expected_transaction_id=result["transaction_id"],
            )

            self.assertEqual(target.read_bytes(), b"external update\n")
            self.assertEqual(report["recovery"], "rollback_partial")
            self.assertEqual(report["changes"][0]["recommended_action"], "manual_review_preserve_current_state")

    def test_interrupted_publication_with_unknown_identity_requires_manual_review(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            plan = {
                "schema_version": 1,
                "task_id": "journal-uncertain",
                "changes": [{"path": "new.py", "content": "VALUE = 2\n"}],
            }
            result = apply_local_change_plan(plan, root, backup_root=base / "backups")
            journal_path = Path(result["transaction_journal_path"])
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            journal["status"] = "applying"
            journal["changes"][0]["status"] = "publishing"
            journal["changes"][0]["published_identity"] = None
            journal_path.write_text(
                json.dumps(journal, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            inspected = inspect_local_change_transaction(journal_path, root)
            self.assertEqual(
                inspected["changes"][0]["recommended_action"],
                "manual_review_ambiguous_publication",
            )
            report = rollback_local_change_transaction(
                journal_path, root, expected_transaction_id=result["transaction_id"]
            )
            self.assertEqual((root / "new.py").read_text(encoding="utf-8"), "VALUE = 2\n")
            self.assertEqual(report["recovery"], "rollback_partial")

    def test_rollback_waits_for_active_apply_transaction(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            backup_root = base / "backups"
            target = root / "created.py"
            plan = {
                "schema_version": 1,
                "task_id": "concurrent-journal-guard",
                "changes": [{"path": "created.py", "content": "VALUE = 1\n"}],
            }
            writer_at_publication = threading.Event()
            release_writer = threading.Event()
            rollback_started = threading.Event()
            rollback_finished = threading.Event()
            outcomes = {}
            errors = []
            real_write_atomic = __import__("bot.local_changes", fromlist=["_write_atomic"])._write_atomic

            def pause_project_publication(path, raw, **kwargs):
                if Path(path) == target and raw == b"VALUE = 1\n":
                    writer_at_publication.set()
                    if not release_writer.wait(5):
                        raise TimeoutError("test did not release publication gate")
                return real_write_atomic(path, raw, **kwargs)

            def apply_worker():
                try:
                    with patch("bot.local_changes._write_atomic", side_effect=pause_project_publication):
                        outcomes["apply"] = apply_local_change_plan(
                            plan, root, backup_root=backup_root
                        )
                except Exception as exc:
                    errors.append(exc)

            def rollback_worker(journal_path, transaction_id):
                rollback_started.set()
                try:
                    outcomes["rollback"] = rollback_local_change_transaction(
                        journal_path, root, expected_transaction_id=transaction_id
                    )
                except Exception as exc:
                    errors.append(exc)
                finally:
                    rollback_finished.set()

            writer = threading.Thread(target=apply_worker, daemon=True)
            writer.start()
            self.assertTrue(writer_at_publication.wait(5), "apply should reach target publication")
            journal_dir = backup_root / ".botpyton-transactions"
            journal_files = list(journal_dir.glob("concurrent-journal-guard-*.json"))
            self.assertEqual(len(journal_files), 1)
            journal = json.loads(journal_files[0].read_text(encoding="utf-8"))

            rollback = threading.Thread(
                target=rollback_worker,
                args=(journal_files[0], journal["transaction_id"]),
                daemon=True,
            )
            rollback.start()
            self.assertTrue(rollback_started.wait(5))
            self.assertFalse(
                rollback_finished.wait(0.2),
                "rollback must wait while apply owns the transaction lock",
            )

            release_writer.set()
            writer.join(5)
            rollback.join(5)
            self.assertFalse(writer.is_alive(), "apply thread did not finish")
            self.assertFalse(rollback.is_alive(), "rollback thread did not finish")
            self.assertEqual(errors, [])
            self.assertEqual(outcomes["rollback"]["recovery"], "rollback_complete")
            self.assertFalse(target.exists(), "serialized rollback should remove the new file")

    def test_reads_bounded_json_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "plan.json"
            path.write_text('{"schema_version":1}', encoding="utf-8")
            self.assertEqual(read_local_plan(path)["schema_version"], 1)
            with self.assertRaises(LocalChangeError):
                read_local_plan(path, max_bytes=2)


if __name__ == "__main__":
    unittest.main()
