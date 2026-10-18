import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_json_valid_rejects_excessive_nesting_and_continues_other_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nested = root / "nested.json"
            nested.write_text("[" * 65 + "0" + "]" * 65, encoding="utf-8")
            (root / "present.txt").write_text("present", encoding="utf-8")

            result = evaluate_verification_checks(
                [
                    {"type": "json_valid", "path": "nested.json"},
                    {"type": "file_exists", "path": "present.txt"},
                ],
                root,
                action_results=[],
            )

            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["all_passed"])
            self.assertFalse(result["checks"][0]["ok"])
            self.assertIn("nesting depth limit", result["checks"][0]["reason"])
            self.assertTrue(result["checks"][1]["ok"])
            self.assertFalse(result["code_executed"])

    def test_json_valid_reports_decoder_recursion_errors_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "deep.json").write_text("[]", encoding="utf-8")
            with patch("bot.verification.json.loads", side_effect=RecursionError("nesting too deep")):
                result = evaluate_verification_checks(
                    [{"type": "json_valid", "path": "deep.json"}],
                    root,
                    action_results=[],
                )

            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["checks"][0]["ok"])
            self.assertIn("RecursionError", result["checks"][0]["reason"])

    def test_verification_rejects_a_reparse_point_component(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real_directory = root / "real"
            real_directory.mkdir()
            (real_directory / "settings.json").write_text('{"safe":true}', encoding="utf-8")
            junction_like_directory = root / "alias"
            junction_like_directory.mkdir()
            # This file stands in for the contents a junction could expose. The
            # detector is patched to classify the alias directory as a reparse
            # point, so the test runs the same on Linux and Windows CI.
            (junction_like_directory / "settings.json").write_text('{"safe":true}', encoding="utf-8")

            from bot import verification

            real_detector = verification._is_link_or_reparse

            def detect_alias(path: Path) -> bool:
                if path == junction_like_directory:
                    return True
                return real_detector(path)

            with patch("bot.verification._is_link_or_reparse", side_effect=detect_alias):
                result = evaluate_verification_checks(
                    [{"type": "file_exists", "path": "alias/settings.json"}],
                    root,
                    action_results=[],
                )

            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["checks"][0]["ok"])
            self.assertIn("reparse-point", result["checks"][0]["reason"])

    def test_missing_files_remain_normal_failed_checks_not_link_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = evaluate_verification_checks(
                [{"type": "file_exists", "path": "not-here.json"}],
                root,
                action_results=[],
            )
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["checks"][0]["ok"])
            self.assertEqual(result["checks"][0]["reason"], "file does not exist")


if __name__ == "__main__":
    unittest.main()
