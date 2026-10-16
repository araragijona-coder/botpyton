import json
import tempfile
import unittest
from pathlib import Path

from bot.diagnostics import diagnose_failure
from bot.executor import TaskExecutor
from bot.policy import PolicyError


def task(task_id="TASK-EXECUTOR-TEST", actions=None, attempts=2):
    return {
        "id": task_id,
        "goal": "Exercise deterministic executor",
        "depends_on": [],
        "allowed_actions": actions or ["inspect_repository", "validate_task", "prepare_report"],
        "success_conditions": ["actions_pass"],
        "max_attempts": attempts,
    }


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "tests").mkdir()
        (self.root / ".git").mkdir()
        (self.root / ".git" / "hidden.py").write_text("this is not python syntax !!!", encoding="utf-8")
        self.executor = TaskExecutor(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_inspection_is_deterministic_and_skips_git(self):
        (self.root / "module.py").write_text("VALUE = 7\n", encoding="utf-8")
        result = self.executor._inspect_repository()
        self.assertIn("module.py", result["top_level_entries"])
        self.assertNotIn("hidden.py", json.dumps(result))
        self.assertEqual(result["python_syntax_errors"], [])

    def test_execute_valid_task_writes_machine_readable_report(self):
        outcome = self.executor.execute({"id": task()["id"], "payload": task(), "attempts": 1})
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["status"], "succeeded")
        report_path = self.root / outcome["report_path"]
        persisted = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["task_id"], task()["id"])
        self.assertFalse(persisted["ai_enabled"])
        self.assertTrue(all(item["ok"] for item in outcome["actions"]))

    def test_task_text_is_not_an_executable_action(self):
        unsafe = task(actions=["run_shell_from_task_text"])
        with self.assertRaises(PolicyError):
            self.executor.execute({"payload": unsafe, "attempts": 1})

    def test_fixed_unit_test_command_passes(self):
        (self.root / "tests" / "test_smoke.py").write_text(
            "import unittest\nclass Smoke(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        result = self.executor._run_tests()
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["command_id"], "python_unittest_discovery")

    def test_known_syntax_failure_is_diagnosed_and_retry_bounded(self):
        (self.root / "tests" / "test_broken.py").write_text("def missing_colon()\n    pass\n", encoding="utf-8")
        outcome = self.executor.execute({
            "id": task()["id"], "payload": task(actions=["run_tests"]),
            "attempts": 1, "last_error": None,
        })
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["diagnostic"]["category"], "source_syntax")
        self.assertTrue(outcome["diagnostic"]["retryable"])
        self.assertTrue(outcome["diagnostic"]["details"]["python_syntax_errors"])
        self.assertFalse(outcome["diagnostic"]["automatic_source_edit_applied"])

    def test_json_diagnostic_names_invalid_file_and_location(self):
        (self.root / "broken.json").write_text('{"bad": }', encoding="utf-8")
        result = diagnose_failure("JSONDecodeError: invalid JSON", self.root, attempt=2, max_attempts=3)
        self.assertEqual(result["strategy"], "validate_json_schema")
        self.assertEqual(result["details"]["invalid_json"][0]["path"], "broken.json")
        self.assertEqual(result["details"]["invalid_json"][0]["line"], 1)
        self.assertTrue(result["retryable"])

    def test_dependency_diagnostic_does_not_install_packages(self):
        (self.root / "pyproject.toml").write_text(
            '[project]\nname="fixture"\ndependencies=[]\n', encoding="utf-8"
        )
        result = diagnose_failure("ModuleNotFoundError: No module named 'missing_pkg'", self.root)
        self.assertEqual(result["strategy"], "verify_declared_dependencies")
        self.assertEqual(result["details"]["missing_module"], "missing_pkg")
        self.assertEqual(result["details"]["matching_declared_dependencies"], [])
        self.assertFalse(result["automatic_source_edit_applied"])

    def test_unknown_error_stops_without_retry(self):
        result = diagnose_failure("unrecognized error from an external process", self.root)
        self.assertEqual(result["category"], "unknown")
        self.assertIsNone(result["strategy"])
        self.assertFalse(result["retryable"])


if __name__ == "__main__":
    unittest.main()
