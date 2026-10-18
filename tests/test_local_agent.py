import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.local_agent import LocalAgent, LocalAgentError, load_local_agent_config


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

    def test_new_file_write_is_exclusive_and_audited(self):
        result = self.agent().write_file("project", "new.py", "VALUE = 2\n")
        self.assertTrue(result["created"])
        self.assertEqual((self.project / "new.py").read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertEqual(self.agent().verify_audit()["event_count"], 2)

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
