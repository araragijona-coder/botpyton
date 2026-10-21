import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bot.executor import TaskExecutor
from bot.main import main
from bot.memory import JsonMemory
from bot.order_pipeline import OrderPipelineError, run_order_batch
from bot.queue import QueueError, TaskQueue


def task(task_id, *, depends_on=None, actions=None, checks=None, attempts=2):
    return {
        "id": task_id,
        "goal": f"Verify a deterministic order step: {task_id}",
        "depends_on": depends_on or [],
        "allowed_actions": actions or ["validate_task", "prepare_report"],
        "success_conditions": ["task_validated", "report_created"],
        "max_attempts": attempts,
        "verification_checks": checks or [{"type": "python_syntax"}],
    }


class OrderPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "tests").mkdir()
        self.queue = TaskQueue(JsonMemory(self.root / "queue.json"))
        self.executor = TaskExecutor(self.root, test_timeout_seconds=10)

    def tearDown(self):
        self.temp.cleanup()

    def test_end_to_end_order_validates_runs_verifies_and_persists_evidence(self):
        (self.root / "module.py").write_text("VALUE = 7\n", encoding="utf-8")
        result = run_order_batch(task("ORDER-001"), queue=self.queue, executor=self.executor)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["attempts_executed"], 1)
        self.assertTrue(result["verification_required"])
        self.assertFalse(result["unrelated_queue_tasks_claimed"])
        self.assertEqual(self.queue.get("ORDER-001")["status"], "succeeded")
        report_path = self.root / result["tasks"][0]["report_path"]
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertTrue(report["ok"])
        self.assertTrue(report["verification"]["all_passed"])
        self.assertEqual(report["task_id"], "ORDER-001")

    def test_order_respects_dependencies_and_claims_only_its_own_ids(self):
        self.queue.enqueue_many([task("UNRELATED")])
        order = [
            task("ORDER-B", depends_on=["ORDER-A"]),
            task("ORDER-A"),
        ]
        result = run_order_batch(order, queue=self.queue, executor=self.executor)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual([item["task_id"] for item in result["attempt_results"]],
                         ["ORDER-A", "ORDER-B"])
        self.assertEqual(self.queue.get("UNRELATED")["status"], "pending")

    def test_order_requires_real_acceptance_check_before_any_queue_mutation(self):
        invalid = task("NO-CHECK", checks=[{"type": "report_exists"}])
        before = self.queue.memory.path.exists()
        with self.assertRaisesRegex(OrderPipelineError, "report_exists alone is insufficient"):
            run_order_batch(invalid, queue=self.queue, executor=self.executor)
        self.assertEqual(self.queue.list_tasks(), [])
        self.assertEqual(self.queue.memory.path.exists(), before)

    def test_external_dependency_must_already_be_succeeded(self):
        self.queue.enqueue_many([task("EXTERNAL")])
        target = task("ORDER-WAITING", depends_on=["EXTERNAL"])
        with self.assertRaisesRegex(OrderPipelineError, "not 'succeeded'"):
            run_order_batch(target, queue=self.queue, executor=self.executor)
        self.assertEqual(self.queue.get("EXTERNAL")["status"], "pending")
        with self.assertRaises(QueueError):
            self.queue.get("ORDER-WAITING")

    def test_invalid_later_task_rejects_whole_order_before_queue_mutation(self):
        first = task("VALID-FIRST")
        invalid = task("INVALID-SECOND")
        invalid["allowed_actions"] = ["run_arbitrary_shell"]
        with self.assertRaises(ValueError):
            run_order_batch([first, invalid], queue=self.queue, executor=self.executor)
        self.assertEqual(self.queue.list_tasks(), [])

    def test_order_cli_executes_fixed_tests_and_emits_machine_readable_result(self):
        (self.root / "tests" / "test_smoke.py").write_text(
            "import unittest\n"
            "class Smoke(unittest.TestCase):\n"
            "    def test_true(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        order_file = self.root / "order.json"
        order_file.write_text(json.dumps(task(
            "CLI-ORDER",
            actions=["run_tests", "prepare_report"],
            checks=[{"type": "tests_pass"}],
        )), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = main([
                "--run-order", str(order_file),
                "--project-root", str(self.root),
                "--memory", str(self.root / "queue-cli.json"),
                "--check-timeout", "10",
            ])
        self.assertEqual(code, 0, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["attempt_results"][0]["verification"]["all_passed"])

    def test_order_cli_rejects_combined_operations(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(["--run-order", "unused.json", "--doctor"])
        self.assertEqual(code, 2)
        self.assertIn("standalone operation", output.getvalue())


if __name__ == "__main__":
    unittest.main()
