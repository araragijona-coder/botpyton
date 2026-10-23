import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bot.main import main


class UnknownRecoveryCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.log = self.root / "failure.log"
        self.log.write_text("OpaqueCompilerFailure: code Z-19\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def call_main(self, args):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_investigation_cli_emits_machine_readable_bounded_report(self):
        code, stdout, stderr = self.call_main([
            "--investigate-failure", "failure.log",
            "--project-root", str(self.root),
            "--investigation-command", "tool --check",
            "--investigation-exit-code", "19",
            "--investigation-expected", "build completes",
            "--investigation-observed", "build stops with code Z-19",
        ])
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "needs_more_evidence")
        self.assertEqual(report["exit_code"], 19)
        self.assertFalse(report["project_code_executed"])
        self.assertFalse(report["network_used"])
        self.assertFalse(report["automatic_retry_permitted"])

    def test_context_flags_require_investigation_log(self):
        code, _, stderr = self.call_main([
            "--investigation-exit-code", "2",
        ])
        self.assertEqual(code, 2)
        self.assertIn("require --investigate-failure", stderr)

    def test_diagnostic_commands_are_mutually_exclusive(self):
        code, _, stderr = self.call_main([
            "--investigate-failure", "failure.log",
            "--diagnose", "failure.log",
            "--project-root", str(self.root),
        ])
        self.assertEqual(code, 2)
        self.assertIn("Choose either", stderr)

    def test_investigation_log_must_be_project_relative_and_present(self):
        code, _, stderr = self.call_main([
            "--investigate-failure", "../not-allowed.log",
            "--project-root", str(self.root),
        ])
        self.assertEqual(code, 2)
        self.assertIn("Path must be repository-relative", stderr)


if __name__ == "__main__":
    unittest.main()
