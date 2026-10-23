import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.main import main


class LocalAgentCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.audit_dir = self.base / "audit"
        self.audit_dir.mkdir()
        self.config = self.base / "agent.json"
        self.audit = self.audit_dir / "audit.jsonl"
        self._write_config()

    def tearDown(self):
        self.temp.cleanup()

    def _write_config(self, permissions=None):
        self.config.write_text(json.dumps({
            "schema_version": 1,
            "roots": [{
                "id": "project",
                "path": str(self.root),
                "permissions": permissions or ["list", "read", "write"],
                "max_file_bytes": 4096,
            }],
            "audit_log": str(self.audit),
        }), encoding="utf-8")

    def _call(self, args):
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(args)
        return code, output.getvalue(), errors.getvalue()

    def test_config_option_validates_without_performing_file_access(self):
        code, stdout, stderr = self._call(["--local-agent-config", str(self.config)])
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "config_valid")
        self.assertEqual(report["roots"][0]["id"], "project")
        self.assertFalse((self.root / "new.py").exists())

    def test_list_and_read_commands_use_the_selected_root(self):
        (self.root / "app.py").write_bytes(b"VALUE = 5\n")
        code, stdout, stderr = self._call([
            "--local-agent-config", str(self.config),
            "--local-agent-list", "project",
        ])
        self.assertEqual(code, 0, stderr)
        self.assertIn("app.py", [item["name"] for item in json.loads(stdout)["items"]])

        code, stdout, stderr = self._call([
            "--local-agent-config", str(self.config),
            "--local-agent-read", "project",
            "--local-agent-path", "app.py",
        ])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["content"], "VALUE = 5\n")

    def test_write_requires_explicit_stdin_and_creates_a_file(self):
        code, stdout, stderr = self._call([
            "--local-agent-config", str(self.config),
            "--local-agent-write", "project",
            "--local-agent-path", "created.py",
        ])
        self.assertEqual(code, 2)
        self.assertIn("--local-agent-content-stdin", stderr)
        self.assertFalse((self.root / "created.py").exists())

        with patch("sys.stdin", io.StringIO("VALUE = 6\n")):
            code, stdout, stderr = self._call([
                "--local-agent-config", str(self.config),
                "--local-agent-write", "project",
                "--local-agent-path", "created.py",
                "--local-agent-content-stdin",
            ])
        self.assertEqual(code, 0, stderr)
        self.assertTrue(json.loads(stdout)["created"])
        self.assertEqual((self.root / "created.py").read_text(encoding="utf-8"), "VALUE = 6\n")

    def test_replacement_requires_exact_hash_and_is_non_destructive_on_mismatch(self):
        target = self.root / "app.py"
        target.write_bytes(b"VALUE = 1\n")
        with patch("sys.stdin", io.StringIO("VALUE = 2\n")):
            code, _, stderr = self._call([
                "--local-agent-config", str(self.config),
                "--local-agent-write", "project",
                "--local-agent-path", "app.py",
                "--local-agent-content-stdin",
                "--local-agent-expected-sha256", "0" * 64,
            ])
        self.assertEqual(code, 2)
        self.assertIn("expected_sha256", stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\n")

        digest = hashlib.sha256(b"VALUE = 1\n").hexdigest()
        with patch("sys.stdin", io.StringIO("VALUE = 2\n")):
            code, stdout, stderr = self._call([
                "--local-agent-config", str(self.config),
                "--local-agent-write", "project",
                "--local-agent-path", "app.py",
                "--local-agent-content-stdin",
                "--local-agent-expected-sha256", digest,
            ])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 2\n")

    def test_audit_command_returns_integrity_summary(self):
        (self.root / "a.txt").write_text("a", encoding="utf-8")
        code, _, stderr = self._call([
            "--local-agent-config", str(self.config),
            "--local-agent-read", "project",
            "--local-agent-path", "a.txt",
        ])
        self.assertEqual(code, 0, stderr)
        code, stdout, stderr = self._call([
            "--local-agent-config", str(self.config), "--local-agent-audit",
        ])
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["event_count"], 2)

    def test_config_is_required_and_operations_are_mutually_exclusive(self):
        code, _, stderr = self._call(["--local-agent-list", "project"])
        self.assertEqual(code, 2)
        self.assertIn("--local-agent-config", stderr)
        code, _, stderr = self._call([
            "--local-agent-config", str(self.config),
            "--local-agent-list", "project",
            "--local-agent-read", "project",
            "--local-agent-path", "missing.txt",
        ])
        self.assertEqual(code, 2)
        self.assertIn("exactly one", stderr)


if __name__ == "__main__":
    unittest.main()
