"""Tests for safe startup command parsing and result behavior."""
import tempfile
import unittest
from pathlib import Path

from bot.startup_advisor import _extract_executable_token, _expand_windows_environment, _path_state, read_startup_advisor


class StartupAdvisorTests(unittest.TestCase):
    def test_quoted_executable_is_separated_from_arguments(self):
        self.assertEqual(
            _extract_executable_token('"C:\\Program Files\\Example\\app.exe" --token secret-value'),
            "C:\\Program Files\\Example\\app.exe",
        )

    def test_unquoted_executable_with_spaces_is_extracted_without_arguments(self):
        self.assertEqual(
            _extract_executable_token(r"C:\Program Files\Example\app.exe --flag value"),
            r"C:\Program Files\Example\app.exe",
        )

    def test_unsupported_or_oversized_command_is_unresolved(self):
        self.assertIsNone(_extract_executable_token("not a supported startup command"))
        self.assertIsNone(_extract_executable_token("x" * 4097))
        self.assertIsNone(_extract_executable_token('"unterminated.exe --arg'))

    def test_environment_expansion_is_case_insensitive_and_local(self):
        self.assertEqual(
            _expand_windows_environment("%SYSTEMROOT%\\System32\\app.exe", {"SystemRoot": r"C:\Windows"}),
            r"C:\Windows\System32\app.exe",
        )

    def test_path_state_does_not_return_command_arguments(self):
        target, state = _path_state(
            '"C:\\Tools\\safe.exe" --password dont-report-this',
            environ={}, is_file=lambda value: value == "C:\\Tools\\safe.exe",
        )
        self.assertEqual(target, "C:\\Tools\\safe.exe")
        self.assertEqual(state, "target_present")
        self.assertNotIn("dont-report-this", target or "")

    def test_default_path_check_uses_a_path_object_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sample.exe"
            target.write_text("fixture", encoding="utf-8")
            path, state = _path_state(str(target), environ={})
            self.assertEqual(path, str(target))
            self.assertEqual(state, "target_present")

    def test_non_windows_platform_is_explicitly_unsupported(self):
        report = read_startup_advisor(windows=False)
        self.assertEqual(report["status"], "unsupported_platform")
        self.assertEqual(report["items_count"], 0)
        self.assertTrue(report["read_only"])
        self.assertFalse(report["network_used"])


if __name__ == "__main__":
    unittest.main()
