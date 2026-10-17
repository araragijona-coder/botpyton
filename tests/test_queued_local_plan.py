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
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "def hello():\n    return 'hello'\n")
            self.assertEqual(result["actions"][0]["action"], "apply_local_plan")
            self.assertTrue(Path(root / result["report_path"]).is_file())

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


if __name__ == "__main__":
    unittest.main()
