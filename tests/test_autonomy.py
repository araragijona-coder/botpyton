import json
import tempfile
import unittest
from pathlib import Path

from bot.autonomy import (
    AutonomousCycleError,
    run_autonomous_cycle,
)


class AutonomousCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "tests").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def _report_dir(self, result):
        return self.root / result["report_path"]

    def test_clean_suite_passes_and_writes_a_durable_attempt_and_summary(self):
        (self.root / "tests" / "test_smoke.py").write_text(
            "import unittest\n\nclass SmokeTests(unittest.TestCase):\n"
            "    def test_true(self):\n        self.assertTrue(True)\n",
            encoding="utf-8",
        )

        result = run_autonomous_cycle(self.root, max_attempts=2, test_timeout_seconds=20)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["attempts_completed"], 1)
        self.assertFalse(result["ai_enabled"])
        self.assertFalse(result["external_ai_apis_enabled"])
        report_dir = self._report_dir(result).parent
        attempt = json.loads((report_dir / "attempt-001.json").read_text(encoding="utf-8"))
        self.assertEqual(attempt["status"], "succeeded")
        self.assertEqual(attempt["test_result"]["exit_code"], 0)
        self.assertTrue(attempt["verification"]["ok"])
        self.assertTrue((report_dir / "summary.json").is_file())

    def test_exact_missing_colon_is_repaired_then_tests_are_reexecuted(self):
        target = self.root / "tests" / "test_repair_candidate.py"
        original = (
            "if True\n"
            "    import unittest\n\n"
            "class RepairCandidateTests(unittest.TestCase):\n"
            "    def test_repaired_source_imports(self):\n"
            "        self.assertTrue(True)\n"
        )
        target.write_text(original, encoding="utf-8")

        result = run_autonomous_cycle(self.root, max_attempts=3, test_timeout_seconds=20)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["attempts_completed"], 2)
        self.assertEqual(target.read_text(encoding="utf-8"), original.replace("if True\n", "if True:\n", 1))
        report_dir = self._report_dir(result).parent
        first = json.loads((report_dir / "attempt-001.json").read_text(encoding="utf-8"))
        second = json.loads((report_dir / "attempt-002.json").read_text(encoding="utf-8"))
        self.assertEqual(first["status"], "repaired_rerun_required")
        self.assertTrue(first["repair"]["applied"])
        self.assertTrue(first["repair"]["automatic_source_edit_applied"])
        self.assertTrue(first["repair"]["post_repair_verification"]["ok"])
        self.assertEqual(second["status"], "succeeded")
        self.assertEqual(second["test_result"]["exit_code"], 0)
        summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["attempt_records"], ["attempt-001.json", "attempt-002.json"])

    def test_unknown_test_failure_stops_without_edit_or_wasted_retries(self):
        target = self.root / "tests" / "test_failure.py"
        original = (
            "import unittest\n\nclass KnownFailureTests(unittest.TestCase):\n"
            "    def test_failure(self):\n        self.assertTrue(False)\n"
        )
        target.write_text(original, encoding="utf-8")

        result = run_autonomous_cycle(self.root, max_attempts=3, test_timeout_seconds=20)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["attempts_completed"], 1)
        self.assertEqual(target.read_text(encoding="utf-8"), original)
        report_dir = self._report_dir(result).parent
        attempt = json.loads((report_dir / "attempt-001.json").read_text(encoding="utf-8"))
        self.assertFalse(attempt["repair"]["applied"])
        self.assertIn("No automatic source repair", attempt["repair"]["reason"])

    def test_timeouts_and_attempt_count_are_hard_limited(self):
        with self.assertRaises(AutonomousCycleError):
            run_autonomous_cycle(self.root, max_attempts=4)
        with self.assertRaises(AutonomousCycleError):
            run_autonomous_cycle(self.root, max_attempts=1, test_timeout_seconds=121)
        with self.assertRaises(AutonomousCycleError):
            run_autonomous_cycle(self.root, max_attempts=True)
        with self.assertRaises(AutonomousCycleError):
            run_autonomous_cycle(self.root, test_timeout_seconds=0)

    def test_invalid_json_is_reported_and_never_guessed(self):
        target = self.root / "tests" / "test_smoke.py"
        target.write_text(
            "import unittest\n\nclass SmokeTests(unittest.TestCase):\n"
            "    def test_true(self):\n        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        broken_json = self.root / "data.json"
        original_json = '{"name": }'
        broken_json.write_text(original_json, encoding="utf-8")

        result = run_autonomous_cycle(self.root, max_attempts=2, test_timeout_seconds=20)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["attempts_completed"], 1)
        self.assertEqual(broken_json.read_text(encoding="utf-8"), original_json)
        report_dir = self._report_dir(result).parent
        attempt = json.loads((report_dir / "attempt-001.json").read_text(encoding="utf-8"))
        self.assertFalse(attempt["verification"]["ok"])
        self.assertFalse(attempt["repair"]["applied"])
        self.assertIn("trailing comma", attempt["repair"]["reason"].lower())

    def test_final_attempt_never_applies_an_unverified_repair(self):
        target = self.root / "tests" / "test_repair_candidate.py"
        original = "if True\n    value = 3\n"
        target.write_text(original, encoding="utf-8")

        result = run_autonomous_cycle(self.root, max_attempts=1, test_timeout_seconds=20)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["attempts_completed"], 1)
        self.assertEqual(target.read_text(encoding="utf-8"), original)
        report_dir = self._report_dir(result).parent
        attempt = json.loads((report_dir / "attempt-001.json").read_text(encoding="utf-8"))
        self.assertEqual(attempt["repair"]["reason"], "attempt_limit_reached; no unverified final repair will be made")
        self.assertFalse(attempt["repair"]["applied"])


if __name__ == "__main__":
    unittest.main()
