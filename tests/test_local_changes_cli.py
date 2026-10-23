"""CLI regression tests for durable local-change transactions."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bot.local_changes import apply_local_change_plan
from bot.main import main


class LocalChangeTransactionCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.backups = self.base / "backups"

    def tearDown(self):
        self.temp.cleanup()

    def make_transaction(self):
        report = apply_local_change_plan({
            "schema_version": 1,
            "task_id": "transaction-cli",
            "changes": [{"path": "created.py", "content": "VALUE = 1\n"}],
        }, self.root, backup_root=self.backups)
        return report, Path(report["transaction_journal_path"])

    def invoke(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_inspection_is_read_only_and_emits_json(self):
        report, journal_path = self.make_transaction()
        target = self.root / "created.py"
        before = target.read_bytes()

        code, stdout, stderr = self.invoke([
            "--local-transaction-inspect", str(journal_path),
            "--project-root", str(self.root),
        ])

        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["transaction_id"], report["transaction_id"])
        self.assertFalse(result["writes_performed"])
        self.assertEqual(target.read_bytes(), before)

    def test_cli_rollback_requires_exact_transaction_id_and_removes_its_new_file(self):
        report, journal_path = self.make_transaction()
        code, stdout, stderr = self.invoke([
            "--local-transaction-rollback", str(journal_path),
            "--project-root", str(self.root),
            "--confirm-transaction-id", report["transaction_id"],
        ])

        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["recovery"], "rollback_complete")
        self.assertFalse((self.root / "created.py").exists())

    def test_cli_missing_confirmation_fails_closed(self):
        _, journal_path = self.make_transaction()
        code, stdout, stderr = self.invoke([
            "--local-transaction-rollback", str(journal_path),
            "--project-root", str(self.root),
        ])

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--confirm-transaction-id", stderr)
        self.assertTrue((self.root / "created.py").is_file())


if __name__ == "__main__":
    unittest.main()
