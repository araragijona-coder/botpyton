import hashlib
import tempfile
import unittest
from pathlib import Path

from bot.verification import evaluate_verification_checks


class VerificationTests(unittest.TestCase):
    def test_file_hash_and_report_checks_are_machine_verifiable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "module.py"
            target.write_text("VALUE = 3\n", encoding="utf-8")
            report = root / "report.json"
            report.write_text("{}", encoding="utf-8")
            expected = hashlib.sha256(target.read_bytes()).hexdigest()
            result = evaluate_verification_checks(
                [
                    {"type": "file_exists", "path": "module.py"},
                    {"type": "file_sha256", "path": "module.py", "sha256": expected},
                    {"type": "python_syntax"},
                    {"type": "report_exists"},
                ],
                root,
                action_results=[],
                report_path="report.json",
            )
            self.assertTrue(result["all_passed"])
            self.assertEqual(result["status"], "passed")
            self.assertFalse(result["code_executed"])

    def test_failed_hash_check_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
            result = evaluate_verification_checks(
                [{"type": "file_sha256", "path": "module.py", "sha256": "0" * 64}],
                root,
                action_results=[],
            )
            self.assertFalse(result["all_passed"])
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["checks"][0]["ok"])


if __name__ == "__main__":
    unittest.main()
