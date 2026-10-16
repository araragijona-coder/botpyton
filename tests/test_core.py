import json
import tempfile
import unittest
from pathlib import Path

from bot.memory import JsonMemory, MemoryError
from bot.planner import order_tasks
from bot.policy import PolicyError, validate_relative_path
from bot.recovery import classify_failure


def task(task_id, deps=None):
    return {
        "id": task_id,
        "goal": "Test task",
        "depends_on": deps or [],
        "allowed_actions": ["validate_task"],
        "success_conditions": ["validation_passes"],
        "max_attempts": 3,
    }

class PolicyTests(unittest.TestCase):
    def test_rejects_unknown_action(self):
        record = task("A")
        record["allowed_actions"] = ["run_shell_from_user_input"]
        with self.assertRaises(PolicyError):
            from bot.policy import validate_task
            validate_task(record)

    def test_rejects_parent_path(self):
        with self.assertRaises(PolicyError):
            validate_relative_path("../outside.txt")

class PlannerTests(unittest.TestCase):
    def test_orders_dependencies(self):
        result = order_tasks([task("B", ["A"]), task("A")])
        self.assertEqual([x["id"] for x in result], ["A", "B"])

    def test_detects_cycle(self):
        with self.assertRaises(PolicyError):
            order_tasks([task("A", ["B"]), task("B", ["A"])])

class MemoryTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = JsonMemory(Path(directory) / "state.json")
            state = memory.load()
            state["tasks"]["TASK-1"] = {"status": "pending"}
            memory.save(state)
            self.assertEqual(memory.load()["tasks"]["TASK-1"]["status"], "pending")

    def test_rejects_bad_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
            with self.assertRaises(MemoryError):
                JsonMemory(path).load()

class RecoveryTests(unittest.TestCase):
    def test_known_error_is_classified(self):
        decision = classify_failure("ModuleNotFoundError: package", attempt=1)
        self.assertEqual(decision.category, "test_environment")
        self.assertIsNotNone(decision.strategy)

    def test_unknown_error_stops(self):
        decision = classify_failure("something unexpected")
        self.assertFalse(decision.retryable)
        self.assertIsNone(decision.strategy)

if __name__ == "__main__":
    unittest.main()
