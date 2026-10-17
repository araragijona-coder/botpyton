import tempfile
import unittest
from pathlib import Path

from bot.executor import TaskExecutor
from bot.policy import PolicyError, validate_task


class QueuedLocalPlanTests(unittest.TestCase):
    def test_worker_applies_a_valid_local_plan_and_reports_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = {
                "id": "worker-create-file",
                "goal": "Create a small Python module from an explicit local change plan",
                "allowed_actions": ["apply_local_plan", "prepare_report"],
                "success_conditions": ["The requested file exists with the exact planned content"],
                "verification_checks": [
                    {"type": "local_plan_applied"},
                    {"type": "file_exists", "path": "src/hello.py"},
                    {"type": "python_syntax"},
                    {"type": "report_exists"},
                ],
                "local_change_plan": {
                    "schema_version": 1,
                    "task_id": "worker-create-file",
                    "changes": [{"path": "src/hello.py", "content": "def hello():\n    return 'hello'\n"}],
                },
            }
            validate_task(task)
            result = TaskExecutor(root).execute({"payload": task, "attempts": 1})
            target = root / "src" / "hello.py"
            self.assertTrue(result["ok"])
            self.assertTrue(result["verification"]["all_passed"])
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "def hello():\n    return 'hello'\n")
            self.assertEqual(result["actions"][0]["action"], "apply_local_plan")
            self.assertTrue(Path(root / result["report_path"]).is_file())

    def test_failed_verification_prevents_task_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = {
                "id": "worker-bad-hash-check",
                "goal": "Demonstrate failed postcondition",
                "allowed_actions": ["apply_local_plan", "prepare_report"],
                "success_conditions": ["The created file matches the expected digest"],
                "local_change_plan": {
                    "schema_version": 1,
                    "task_id": "worker-bad-hash-check",
                    "changes": [{"path": "created.py", "content": "VALUE = 1\\n"}],
                },
                "verification_checks": [
                    {"type": "local_plan_applied"},
                    {"type": "file_sha256", "path": "created.py", "sha256": "0" * 64},
                ],
            }
            result = TaskExecutor(root).execute({"payload": task, "attempts": 1})
            self.assertFalse(result["ok"])
            self.assertEqual(result["failed_action"], "verify_success_conditions")
            self.assertFalse(result["verification"]["all_passed"])
            self.assertTrue((root / "created.py").is_file())

    def test_local_plan_task_id_must_match_worker_task(self):
        task = {
            "id": "task-one",
            "goal": "Reject mismatched plan identity",
            "allowed_actions": ["apply_local_plan"],
            "success_conditions": ["Plan identity is consistent"],
            "local_change_plan": {"schema_version": 1, "task_id": "task-two", "changes": []},
        }
        with self.assertRaises(PolicyError):
            validate_task(task)

    def test_queued_project_check_requires_explicit_approval_and_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_smoke.py").write_text(
                "import unittest\nclass Smoke(unittest.TestCase):\n"
                "    def test_true(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
            task = {
                "id": "worker-python-check",
                "goal": "Run the reviewed Python test profile",
                "allowed_actions": ["run_project_check"],
                "success_conditions": ["The selected test profile exits successfully"],
                "project_check_id": "python-unittest",
                "allow_project_code_execution": True,
                "check_timeout_seconds": 10,
                "verification_checks": [
                    {"type": "project_check_pass"},
                    {"type": "tests_pass"},
                    {"type": "report_exists"},
                ],
            }
            validate_task(task)
            result = TaskExecutor(root).execute({"payload": task, "attempts": 1})
            self.assertTrue(result["ok"])
            self.assertTrue(result["verification"]["all_passed"])
            details = result["actions"][0]["details"]
            self.assertTrue(details["ok"])
            self.assertTrue(details["project_code_execution_approved"])


if __name__ == "__main__":
    unittest.main()
