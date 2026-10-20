import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from bot.local_agent import (
    LocalAgent, LocalAgentError, load_local_agent_config,
    _directory_chain_lease, _path_chain_signature, _publish_staged_file,
)


class LocalAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.audit_dir = self.base / "audit"
        self.audit_dir.mkdir()
        self.config_path = self.base / "agent.json"
        self.audit_path = self.audit_dir / "audit.jsonl"
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def config(self, *, roots=None, audit_log=None):
        return {
            "schema_version": 1,
            "roots": roots if roots is not None else [{
                "id": "project",
                "path": str(self.project),
                "permissions": ["list", "read", "write"],
                "max_file_bytes": 4096,
            }],
            "audit_log": str(audit_log if audit_log is not None else self.audit_path),
        }

    def write_config(self, payload=None):
        self.config_path.write_text(
            json.dumps(self.config() if payload is None else payload),
            encoding="utf-8",
        )

    def agent(self):
        return LocalAgent(self.config_path)

    def test_config_validation_does_not_create_audit_or_touch_project_files(self):
        summary = load_local_agent_config(self.config_path)
        self.assertEqual(summary["roots"][0]["id"], "project")
        self.assertFalse(self.audit_path.exists())
        self.assertEqual(list(self.project.iterdir()), [])

    def test_read_file_returns_content_and_audits_intent_and_result_without_content(self):
        (self.project / "hello.py").write_bytes(b"VALUE = 17\n")
        result = self.agent().read_file("project", "hello.py")
        self.assertEqual(result["content"], "VALUE = 17\n")
        self.assertEqual(result["sha256"], hashlib.sha256(b"VALUE = 17\n").hexdigest())
        status = self.agent().verify_audit()
        self.assertEqual(status["event_count"], 2)
        raw_audit = self.audit_path.read_text(encoding="utf-8")
        self.assertNotIn("VALUE = 17", raw_audit)
        self.assertIn('"outcome":"intent"', raw_audit)
        self.assertIn('"outcome":"success"', raw_audit)

    def test_read_refuses_directory_identity_swap_during_io(self):
        nested = self.project / "nested"
        replacement = self.project / "replacement"
        original_nested = self.project / "nested-original"
        nested.mkdir()
        replacement.mkdir()
        target = nested / "source.txt"
        target.write_text("authorized content", encoding="utf-8")
        (replacement / "source.txt").write_text("substituted content", encoding="utf-8")
        agent = self.agent()
        real_read = __import__("bot.local_agent", fromlist=["read_bounded_bytes"]).read_bounded_bytes
        swapped = False

        def swap_directory_then_read(path, *, limit_bytes, label="Input file"):
            nonlocal swapped
            if not swapped and Path(path).name == target.name and Path(path).parent.name == "nested":
                nested.rename(original_nested)
                replacement.rename(nested)
                swapped = True
            return real_read(path, limit_bytes=limit_bytes, label=label)

        try:
            with patch("bot.local_agent.read_bounded_bytes", side_effect=swap_directory_then_read):
                with self.assertRaisesRegex(LocalAgentError, "path changed during operation"):
                    agent.read_file("project", "nested/source.txt")
            self.assertTrue(swapped, "test must replace the directory during the read")
        finally:
            if nested.exists() and swapped:
                nested.rename(replacement)
            if original_nested.exists():
                original_nested.rename(nested)
        self.assertEqual(target.read_text(encoding="utf-8"), "authorized content")
        self.assertEqual((replacement / "source.txt").read_text(encoding="utf-8"), "substituted content")

    def test_list_refuses_directory_identity_swap_during_scan(self):
        nested = self.project / "nested"
        replacement = self.project / "replacement"
        original_nested = self.project / "nested-original"
        nested.mkdir()
        replacement.mkdir()
        (nested / "authorized.txt").write_text("authorized", encoding="utf-8")
        (replacement / "substituted.txt").write_text("substituted", encoding="utf-8")
        real_scandir = __import__("os").scandir
        swapped = False

        def swap_directory_then_scan(path):
            nonlocal swapped
            if (
                not swapped
                and Path(path).name.casefold() == "nested"
            ):
                nested.rename(original_nested)
                replacement.rename(nested)
                swapped = True
            return real_scandir(path)

        try:
            # Disable the Windows lease only in this injected race test so the
            # regression deterministically exercises the post-scan identity guard.
            with patch("bot.local_agent._directory_chain_lease", side_effect=lambda *_a, **_k: nullcontext()):
                with patch("bot.local_agent.os.scandir", side_effect=swap_directory_then_scan):
                    with self.assertRaisesRegex(LocalAgentError, "path changed during operation"):
                        self.agent().list_directory("project", "nested")
            self.assertTrue(swapped, "test must replace the listed directory during scanning")
        finally:
            if nested.exists() and swapped:
                nested.rename(replacement)
            if original_nested.exists():
                original_nested.rename(nested)
        self.assertEqual({item.name for item in nested.iterdir()}, {"authorized.txt"})
        self.assertEqual({item.name for item in replacement.iterdir()}, {"substituted.txt"})

    def test_write_refuses_parent_replacement_before_staging(self):
        nested = self.project / "nested"
        replacement = self.project / "replacement"
        original_nested = self.project / "nested-original"
        nested.mkdir()
        replacement.mkdir()
        real_lease = _directory_chain_lease
        swapped = False

        def replace_parent_before_lease(path, expected_signature, label):
            nonlocal swapped
            if label == "Write parent" and not swapped:
                nested.rename(original_nested)
                replacement.rename(nested)
                swapped = True
            return real_lease(path, expected_signature, label)

        try:
            with patch("bot.local_agent._directory_chain_lease", side_effect=replace_parent_before_lease):
                with self.assertRaisesRegex(LocalAgentError, "changed before its directory lease"):
                    self.agent().write_file("project", "nested/new.py", "VALUE = 1\\n")
            self.assertTrue(swapped, "test must replace the parent before a staged write")
        finally:
            if nested.exists() and swapped:
                nested.rename(replacement)
            if original_nested.exists():
                original_nested.rename(nested)
        self.assertFalse((nested / "new.py").exists())
        self.assertFalse((replacement / "new.py").exists())

    @unittest.skipUnless(os.name == "nt", "Native directory handle checks are Windows-specific")
    def test_windows_directory_handle_guard_allows_scoped_file_operations(self):
        parent = self.project / "pinned"
        parent.mkdir()
        signature = _path_chain_signature(parent, "Write parent")
        marker = parent / "marker.txt"
        with _directory_chain_lease(parent, signature, "Write parent"):
            marker.write_text("scoped", encoding="utf-8")
            self.assertTrue(marker.is_file())
        self.assertEqual(marker.read_text(encoding="utf-8"), "scoped")


    def test_list_directory_is_single_level_and_skips_links(self):
        (self.project / "folder").mkdir()
        (self.project / "one.txt").write_text("one", encoding="utf-8")
        result = self.agent().list_directory("project")
        names = {item["name"] for item in result["items"]}
        self.assertEqual(names, {"folder", "one.txt"})
        self.assertFalse(result["truncated"])
        self.assertTrue(result["read_only"])

    def test_directory_listing_is_bounded(self):
        for number in range(5):
            (self.project / f"{number}.txt").write_text("x", encoding="utf-8")
        with patch("bot.local_agent.MAX_DIRECTORY_ITEMS", 2):
            result = self.agent().list_directory("project")
        self.assertEqual(result["returned_count"], 2)
        self.assertTrue(result["truncated"])

    def test_read_only_root_cannot_write_and_denial_is_audited(self):
        self.write_config(self.config(roots=[{
            "id": "project", "path": str(self.project), "permissions": ["list", "read"],
        }]))
        with self.assertRaisesRegex(LocalAgentError, "does not grant"):
            self.agent().write_file("project", "new.py", "VALUE = 1\n")
        self.assertFalse((self.project / "new.py").exists())
        status = self.agent().verify_audit()
        self.assertEqual(status["event_count"], 1)
        record = json.loads(self.audit_path.read_text(encoding="utf-8"))
        self.assertEqual(record["outcome"], "denied")

    @unittest.skipIf(os.name == "nt", "dir_fd publication is the POSIX implementation")
    def test_atomic_publication_stays_anchored_to_open_parent_directory(self):
        parent = self.project / "parent"
        moved = self.project / "moved-parent"
        parent.mkdir()
        target = parent / "published.txt"
        staged = parent / ".published.txt.stage"
        staged.write_bytes(b"complete payload")
        existing = parent / "existing.txt"
        existing.write_bytes(b"previous payload")
        existing_staged = parent / ".existing.txt.stage"
        existing_staged.write_bytes(b"replacement payload")
        flags = __import__("os").O_RDONLY | getattr(__import__("os"), "O_DIRECTORY", 0)
        parent_fd = __import__("os").open(parent, flags)
        try:
            # Simulate a directory path being replaced after its descriptor was opened.
            parent.rename(moved)
            parent.mkdir()
            _publish_staged_file(staged, target, parent_fd, replace_existing=False)
            _publish_staged_file(existing_staged, existing, parent_fd, replace_existing=True)
            self.assertEqual((moved / "published.txt").read_bytes(), b"complete payload")
            self.assertEqual((moved / "existing.txt").read_bytes(), b"replacement payload")
            self.assertFalse((parent / "published.txt").exists())
            self.assertFalse((parent / "existing.txt").exists())
        finally:
            __import__("os").close(parent_fd)

    def test_failed_atomic_publication_cleans_staged_file(self):
        with patch("bot.local_agent._publish_staged_file", side_effect=LocalAgentError("injected publish failure")):
            with self.assertRaisesRegex(LocalAgentError, "injected publish failure"):
                self.agent().write_file("project", "failed.py", "VALUE = 8\\n")
        self.assertFalse((self.project / "failed.py").exists())
        self.assertFalse(list(self.project.glob(".failed.py.*.botpyton-agent-tmp")))

    def test_new_file_write_is_exclusive_and_audited(self):
        result = self.agent().write_file("project", "new.py", "VALUE = 2\n")
        self.assertTrue(result["created"])
        self.assertEqual((self.project / "new.py").read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertEqual(self.agent().verify_audit()["event_count"], 2)

    def test_concurrent_replacements_with_same_expected_hash_allow_only_one_writer(self):
        target = self.project / "shared.txt"
        target.write_bytes(b"starting value")
        expected = hashlib.sha256(b"starting value").hexdigest()
        barrier = threading.Barrier(2)

        def replace(content):
            barrier.wait(timeout=5)
            try:
                LocalAgent(self.config_path).write_file(
                    "project", "shared.txt", content, expected_sha256=expected
                )
                return ("success", content)
            except LocalAgentError as exc:
                return ("conflict", str(exc))

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(replace, ("writer one", "writer two")))

        successes = [value for status, value in outcomes if status == "success"]
        conflicts = [value for status, value in outcomes if status == "conflict"]
        self.assertEqual(len(successes), 1, outcomes)
        self.assertEqual(len(conflicts), 1, outcomes)
        self.assertIn("expected_sha256", conflicts[0])
        self.assertEqual(target.read_bytes(), successes[0].encode("utf-8"))
        audit = LocalAgent(self.config_path).verify_audit()
        self.assertEqual(audit["status"], "valid")
        records = [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(record["outcome"] == "success" and record["operation"] == "write" for record in records), 1)
        self.assertEqual(sum(record["outcome"] == "denied" and record["operation"] == "write" for record in records), 1)

    def test_concurrent_new_file_creation_never_overwrites_the_first_writer(self):
        target = self.project / "brand-new.txt"
        barrier = threading.Barrier(2)

        def create(content):
            barrier.wait(timeout=5)
            try:
                LocalAgent(self.config_path).write_file("project", "brand-new.txt", content)
                return ("success", content)
            except LocalAgentError as exc:
                return ("conflict", str(exc))

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(create, ("first payload", "second payload")))

        successes = [value for status, value in outcomes if status == "success"]
        conflicts = [value for status, value in outcomes if status == "conflict"]
        self.assertEqual(len(successes), 1, outcomes)
        self.assertEqual(len(conflicts), 1, outcomes)
        self.assertIn("expected_sha256", conflicts[0])
        self.assertEqual(target.read_bytes(), successes[0].encode("utf-8"))

    def test_external_in_place_change_at_publication_boundary_is_preserved(self):
        target = self.project / "shared.txt"
        target.write_bytes(b"authorized starting value")
        expected = hashlib.sha256(b"authorized starting value").hexdigest()
        real_publish = _publish_staged_file

        def external_change_then_publish(*args, **kwargs):
            target.write_bytes(b"external process update")
            return real_publish(*args, **kwargs)

        with patch(
            "bot.local_agent._publish_staged_file",
            side_effect=external_change_then_publish,
        ):
            with self.assertRaisesRegex(LocalAgentError, "changed at publication boundary"):
                self.agent().write_file(
                    "project", "shared.txt", "BOTPYTON replacement",
                    expected_sha256=expected,
                )

        self.assertEqual(target.read_bytes(), b"external process update")
        self.assertFalse(list(self.project.glob(".shared.txt.*.botpyton-agent-tmp")))
        self.assertEqual(self.agent().verify_audit()["status"], "valid")

    def test_external_path_replacement_at_publication_boundary_is_preserved(self):
        target = self.project / "shared.txt"
        displaced = self.project / "external-original.txt"
        target.write_bytes(b"authorized starting value")
        expected = hashlib.sha256(b"authorized starting value").hexdigest()
        real_publish = _publish_staged_file

        def external_swap_then_publish(*args, **kwargs):
            target.rename(displaced)
            target.write_bytes(b"external replacement file")
            return real_publish(*args, **kwargs)

        with patch(
            "bot.local_agent._publish_staged_file",
            side_effect=external_swap_then_publish,
        ):
            with self.assertRaisesRegex(LocalAgentError, "path changed during operation"):
                self.agent().write_file(
                    "project", "shared.txt", "BOTPYTON replacement",
                    expected_sha256=expected,
                )

        self.assertEqual(displaced.read_bytes(), b"authorized starting value")
        self.assertEqual(target.read_bytes(), b"external replacement file")
        self.assertFalse(list(self.project.glob(".shared.txt.*.botpyton-agent-tmp")))
        self.assertEqual(self.agent().verify_audit()["status"], "valid")

    def test_external_creation_at_publication_boundary_is_never_overwritten(self):
        target = self.project / "raced-create.txt"
        real_publish = _publish_staged_file

        def external_create_then_publish(*args, **kwargs):
            target.write_bytes(b"external first writer")
            return real_publish(*args, **kwargs)

        with patch(
            "bot.local_agent._publish_staged_file",
            side_effect=external_create_then_publish,
        ):
            with self.assertRaises(LocalAgentError):
                self.agent().write_file("project", "raced-create.txt", "BOTPYTON payload")

        self.assertEqual(target.read_bytes(), b"external first writer")
        self.assertFalse(list(self.project.glob(".raced-create.txt.*.botpyton-agent-tmp")))
        self.assertEqual(self.agent().verify_audit()["status"], "valid")

    def test_post_publish_external_change_is_preserved_without_rollback(self):
        target = self.project / "shared.txt"
        target.write_bytes(b"previous stable version")
        expected = hashlib.sha256(b"previous stable version").hexdigest()
        real_read = __import__("bot.local_agent", fromlist=["read_bounded_bytes"]).read_bounded_bytes
        injected = False

        def external_edit_after_publication(path, *, limit_bytes, label="Input file"):
            nonlocal injected
            if label == "Published write" and not injected:
                target.write_bytes(b"newer external version")
                injected = True
            return real_read(path, limit_bytes=limit_bytes, label=label)

        with patch("bot.local_agent.read_bounded_bytes", side_effect=external_edit_after_publication):
            with self.assertRaisesRegex(LocalAgentError, "changed externally.*preserve the external version"):
                self.agent().write_file(
                    "project", "shared.txt", "BOTPYTON version",
                    expected_sha256=expected,
                )

        self.assertTrue(injected, "test must edit the file after publication")
        self.assertEqual(target.read_bytes(), b"newer external version")
        self.assertEqual(self.agent().verify_audit()["status"], "valid")

    def test_post_publish_audit_failure_restores_previous_version_conditionally(self):
        target = self.project / "shared.txt"
        target.write_bytes(b"previous audited version")
        expected = hashlib.sha256(b"previous audited version").hexdigest()
        agent = self.agent()
        real_record = agent._record
        injected = False

        def fail_success_record(operation, root_id, relative, outcome, detail, byte_count=None):
            nonlocal injected
            if operation == "write" and outcome == "success" and not injected:
                injected = True
                raise LocalAgentError("injected post-publication audit failure")
            return real_record(operation, root_id, relative, outcome, detail, byte_count)

        with patch.object(agent, "_record", side_effect=fail_success_record):
            with self.assertRaisesRegex(LocalAgentError, "previous file version was conditionally restored"):
                agent.write_file(
                    "project", "shared.txt", "temporary BOTPYTON version",
                    expected_sha256=expected,
                )

        self.assertTrue(injected, "failure must occur after the destination was published")
        self.assertEqual(target.read_bytes(), b"previous audited version")
        audit = self.agent().verify_audit()
        self.assertEqual(audit["status"], "valid")
        records = [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]
        errors = [record for record in records if record["operation"] == "write" and record["outcome"] == "error"]
        self.assertTrue(errors)
        self.assertIn("previous_version_restored", errors[-1]["detail"])

    def test_post_publish_new_file_is_not_deleted_as_recovery(self):
        target = self.project / "new-after-publish.txt"
        real_read = __import__("bot.local_agent", fromlist=["read_bounded_bytes"]).read_bounded_bytes
        injected = False

        def external_edit_after_publication(path, *, limit_bytes, label="Input file"):
            nonlocal injected
            if label == "Published write" and not injected:
                target.write_bytes(b"external update to newly created file")
                injected = True
            return real_read(path, limit_bytes=limit_bytes, label=label)

        with patch("bot.local_agent.read_bounded_bytes", side_effect=external_edit_after_publication):
            with self.assertRaisesRegex(LocalAgentError, "changed externally.*preserve the external version"):
                self.agent().write_file("project", "new-after-publish.txt", "BOTPYTON new file")

        self.assertTrue(injected)
        self.assertEqual(target.read_bytes(), b"external update to newly created file")
        self.assertEqual(self.agent().verify_audit()["status"], "valid")

    @unittest.skipIf(os.name == "nt", "POSIX hard-link publication is specific to non-Windows")
    def test_new_file_link_cleanup_failure_keeps_truthful_publication_state(self):
        target = self.project / "new-link-cleanup.txt"
        real_unlink = os.unlink
        injected = False

        def fail_first_alias_cleanup(path, *args, **kwargs):
            nonlocal injected
            if (
                not injected
                and "new-link-cleanup.txt." in str(path)
                and kwargs.get("dir_fd") is not None
            ):
                injected = True
                raise PermissionError("injected alias cleanup failure after link publication")
            return real_unlink(path, *args, **kwargs)

        with patch("bot.local_agent.os.unlink", side_effect=fail_first_alias_cleanup):
            result = self.agent().write_file("project", "new-link-cleanup.txt", "published bytes")

        self.assertTrue(injected, "test must fail cleanup after the destination link has succeeded")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(target.read_bytes(), b"published bytes")
        self.assertNotIn("staging_cleanup", result, "final cleanup should retry and remove the alias")
        self.assertFalse(list(self.project.glob(".new-link-cleanup.txt.*.botpyton-agent-tmp")))
        self.assertEqual(self.agent().verify_audit()["status"], "valid")

    def test_internal_root_write_lock_is_hidden_and_protected(self):
        self.agent().write_file("project", "one.txt", "one")
        lock_files = list(self.project.glob(".botpyton-write-*.lock"))
        self.assertEqual(len(lock_files), 1)
        lock_name = lock_files[0].name

        listing = self.agent().list_directory("project")
        self.assertNotIn(lock_name, {item["name"] for item in listing["items"]})
        with self.assertRaisesRegex(LocalAgentError, "protected"):
            self.agent().read_file("project", lock_name)
        with self.assertRaisesRegex(LocalAgentError, "protected"):
            self.agent().write_file("project", lock_name, "do not overwrite lock")

    def test_existing_file_replacement_requires_read_permission_and_exact_hash(self):
        target = self.project / "app.py"
        target.write_bytes(b"VALUE = 1\n")
        with self.assertRaisesRegex(LocalAgentError, "expected_sha256"):
            self.agent().write_file("project", "app.py", "VALUE = 2\n")
        stale = "0" * 64
        with self.assertRaisesRegex(LocalAgentError, "expected_sha256"):
            self.agent().write_file("project", "app.py", "VALUE = 2\n", expected_sha256=stale)
        self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\n")
        digest = hashlib.sha256(b"VALUE = 1\n").hexdigest()
        result = self.agent().write_file(
            "project", "app.py", "VALUE = 3\n", expected_sha256=digest
        )
        self.assertFalse(result["created"])
        self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 3\n")

    def test_existing_file_cannot_be_replaced_without_read_grant(self):
        target = self.project / "app.py"
        target.write_text("original", encoding="utf-8")
        self.write_config(self.config(roots=[{
            "id": "project", "path": str(self.project), "permissions": ["write"],
        }]))
        digest = hashlib.sha256(b"original").hexdigest()
        with self.assertRaisesRegex(LocalAgentError, "also requires read permission"):
            self.agent().write_file("project", "app.py", "changed", expected_sha256=digest)
        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    def test_traversal_and_absolute_paths_are_rejected_and_audited(self):
        for path in ("../outside.txt", str(self.base / "outside.txt"), "C:/Windows/win.ini", "folder\\..\\x"):
            with self.subTest(path=path), self.assertRaises(LocalAgentError):
                self.agent().read_file("project", path)
        self.assertEqual(self.agent().verify_audit()["event_count"], 4)

    def test_protected_secret_names_are_blocked(self):
        (self.project / ".env").write_text("TOKEN=secret", encoding="utf-8")
        with self.assertRaisesRegex(LocalAgentError, "protected"):
            self.agent().read_file("project", ".env")
        with self.assertRaisesRegex(LocalAgentError, "protected"):
            self.agent().write_file("project", ".ssh/authorized_keys", "not allowed")
        self.assertEqual((self.project / ".env").read_text(encoding="utf-8"), "TOKEN=secret")

    def test_symlink_target_is_blocked_when_platform_allows_symlink(self):
        outside = self.base / "outside.txt"
        outside.write_text("external", encoding="utf-8")
        try:
            (self.project / "link.txt").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        with self.assertRaisesRegex(LocalAgentError, "Symlinks|reparse"):
            self.agent().read_file("project", "link.txt")
        self.assertEqual(outside.read_text(encoding="utf-8"), "external")

    def test_audit_path_cannot_be_inside_an_authorized_root(self):
        self.write_config(self.config(audit_log=self.project / "audit.jsonl"))
        with self.assertRaisesRegex(LocalAgentError, "outside every configured root"):
            self.agent()

    def test_agent_config_itself_must_live_outside_authorized_roots(self):
        inside_config = self.project / "agent.json"
        inside_config.write_text(json.dumps(self.config()), encoding="utf-8")
        with self.assertRaisesRegex(LocalAgentError, "config must be outside"):
            load_local_agent_config(inside_config)

    def test_overlapping_roots_are_rejected(self):
        nested = self.project / "nested"
        nested.mkdir()
        self.write_config(self.config(roots=[
            {"id": "project", "path": str(self.project), "permissions": ["read"]},
            {"id": "nested", "path": str(nested), "permissions": ["read", "write"]},
        ]))
        with self.assertRaisesRegex(LocalAgentError, "cannot overlap"):
            self.agent()

    def test_modified_audit_hash_blocks_the_next_operation(self):
        (self.project / "app.py").write_text("VALUE = 9\n", encoding="utf-8")
        self.agent().read_file("project", "app.py")
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["detail"] = "tampered"
        lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"))
        self.audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(LocalAgentError, "hash verification"):
            self.agent().read_file("project", "app.py")

    def test_invalid_utf8_and_large_files_are_blocked(self):
        (self.project / "binary.dat").write_bytes(b"\xff\xfe")
        with self.assertRaisesRegex(LocalAgentError, "File read failed"):
            self.agent().read_file("project", "binary.dat")
        with self.assertRaisesRegex(LocalAgentError, "configured limit"):
            self.agent().write_file("project", "large.txt", "x" * 4097)
        self.assertFalse((self.project / "large.txt").exists())

    def test_missing_parent_directories_are_not_created(self):
        with self.assertRaises(LocalAgentError):
            self.agent().write_file("project", "missing/new.py", "VALUE = 1\n")
        self.assertFalse((self.project / "missing").exists())

    def test_partial_audit_staging_write_preserves_previous_valid_log(self):
        (self.project / "audit-seed.txt").write_text("seed", encoding="utf-8")
        agent = self.agent()
        agent.read_file("project", "audit-seed.txt")
        original_audit = self.audit_path.read_bytes()
        real_write = os.write
        calls = 0

        def partial_then_fail(descriptor, data):
            nonlocal calls
            if calls == 0:
                calls += 1
                return real_write(descriptor, data[:min(7, len(data))])
            raise OSError("injected failure after a partial staging write")

        with patch("bot.local_agent.os.write", side_effect=partial_then_fail):
            with self.assertRaisesRegex(LocalAgentError, "Audit publication failed: OSError"):
                agent._record("read", "project", "audit-seed.txt", "success", "injected")

        self.assertGreater(calls, 0, "the staging writer must partially write before failing")
        self.assertEqual(self.audit_path.read_bytes(), original_audit)
        self.assertEqual(agent.verify_audit()["status"], "valid")
        self.assertFalse(list(self.audit_dir.glob(".*.botpyton-agent-tmp")))

    def test_audit_corruption_is_fail_closed_before_write(self):
        (self.project / "new.py").write_text("original", encoding="utf-8")
        self.agent().read_file("project", "new.py")
        self.audit_path.write_text('{"invalid":true}\n', encoding="utf-8")
        agent = self.agent()
        with self.assertRaises(LocalAgentError):
            agent.write_file("project", "another.py", "VALUE = 1\n")
        self.assertFalse((self.project / "another.py").exists())


if __name__ == "__main__":
    unittest.main()
