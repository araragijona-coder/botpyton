import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bot.architect_order_contract import ArchitectOrderError, validate_architect_order
from bot.main import main


def task(task_id="ARCH-TASK-001", *, actions=None, checks=None, depends_on=None):
    return {
        "id": task_id,
        "goal": "Inspect repository and verify a bounded result",
        "depends_on": depends_on or [],
        "allowed_actions": actions or ["validate_task", "prepare_report"],
        "success_conditions": ["task_validated", "report_created"],
        "max_attempts": 2,
        "verification_checks": checks or [{"type": "python_syntax"}],
    }


def packet(tasks=None, *, status="ready", questions=None):
    if tasks is None:
        tasks = [task()]
    return {
        "schema_version": 1,
        "order_id": "ARCH-ORDER-001",
        "source": {"agent_id": "external-architect-demo", "plan_id": "PLAN-001"},
        "summary": "Inspect a project and return verifiable execution evidence.",
        "status": status,
        "tasks": tasks,
        "questions": questions or [],
    }


class ArchitectOrderContractTests(unittest.TestCase):
    def test_valid_ready_packet_is_normalized_without_trusting_source(self):
        result = validate_architect_order(packet())
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["task_ids"], ["ARCH-TASK-001"])
        self.assertEqual(result["dependency_order"], ["ARCH-TASK-001"])
        self.assertEqual(result["required_local_approvals"], [])
        self.assertFalse(result["source_is_cryptographically_authenticated"])
        self.assertFalse(result["external_authorization_trusted"])
        self.assertFalse(result["packet_can_enable_execution"])
        self.assertFalse(result["github_writes_allowed"])
        self.assertFalse(result["ai_enabled"])

    def test_unhashable_status_values_are_rejected_as_contract_errors(self):
        for invalid_status in ([], {}, 7, True):
            with self.subTest(status=invalid_status):
                raw = packet()
                raw["status"] = invalid_status
                with self.assertRaisesRegex(ArchitectOrderError, "status must be"):
                    validate_architect_order(raw)

    def test_unknown_execution_or_authorization_fields_are_rejected(self):
        for name, value in (("approved", True), ("execute", True), ("shell", "whoami")):
            with self.subTest(name=name):
                raw = packet()
                raw[name] = value
                with self.assertRaisesRegex(ArchitectOrderError, "Unknown architect-order fields"):
                    validate_architect_order(raw)

    def test_source_does_not_accept_extra_authority_fields(self):
        raw = packet()
        raw["source"]["authorized"] = True
        with self.assertRaisesRegex(ArchitectOrderError, "Invalid source metadata"):
            validate_architect_order(raw)

    def test_needs_input_has_questions_and_no_executable_tasks(self):
        raw = packet(tasks=[], status="needs_input", questions=["Which project root is in scope?"])
        result = validate_architect_order(raw)
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["task_count"], 0)
        self.assertEqual(result["questions"], ["Which project root is in scope?"])
        self.assertFalse(result["packet_can_enable_execution"])

    def test_needs_input_rejects_tasks_and_ready_rejects_empty_tasks(self):
        raw = packet(status="needs_input", questions=["What is the target?"])
        with self.assertRaisesRegex(ArchitectOrderError, "must not contain executable tasks"):
            validate_architect_order(raw)
        raw = packet(tasks=[])
        with self.assertRaisesRegex(ArchitectOrderError, "must contain at least one task"):
            validate_architect_order(raw)

    def test_report_exists_alone_is_not_an_acceptance_check(self):
        raw = packet([task(checks=[{"type": "report_exists"}])])
        with self.assertRaisesRegex(ArchitectOrderError, "report_exists alone is insufficient"):
            validate_architect_order(raw)

    def test_arbitrary_action_is_rejected_before_any_execution(self):
        raw = packet([task(actions=["run_arbitrary_shell"])])
        with self.assertRaisesRegex(ArchitectOrderError, "Task batch failed strict validation"):
            validate_architect_order(raw)

    def test_local_write_and_project_code_permissions_are_derived_from_actions(self):
        write_task = task(
            "ARCH-WRITE-001",
            actions=["apply_local_plan", "run_tests", "prepare_report"],
            checks=[
                {"type": "local_plan_applied"},
                {"type": "file_exists", "path": "generated.py"},
                {"type": "tests_pass"},
            ],
        )
        write_task["local_change_plan"] = {
            "schema_version": 1,
            "task_id": "ARCH-WRITE-001",
            "changes": [{"path": "generated.py", "content": "VALUE = 1\n"}],
        }
        result = validate_architect_order(packet([write_task]))
        self.assertEqual(
            result["required_local_approvals"],
            ["local_file_write", "project_code_execution"],
        )

    def test_cli_dry_run_validates_but_does_not_create_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_path = root / "architect-order.json"
            queue_path = root / "queue.json"
            order_path.write_text(json.dumps(packet()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main([
                    "--architect-order", str(order_path),
                    "--project-root", str(root), "--memory", str(queue_path),
                ])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "validated_for_review")
            self.assertFalse(result["execution_started"])
            self.assertFalse(result["queue_mutated"])
            self.assertEqual(result["tasks_for_review"][0]["id"], "ARCH-TASK-001")
            self.assertFalse(queue_path.exists())

    def test_cli_needs_input_stops_without_queue_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_path = root / "architect-order.json"
            queue_path = root / "queue.json"
            order_path.write_text(json.dumps(packet(
                tasks=[], status="needs_input", questions=["Provide the target project root."]
            )), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main([
                    "--architect-order", str(order_path), "--execute-architect-order",
                    "--project-root", str(root), "--memory", str(queue_path),
                ])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "needs_input")
            self.assertFalse(result["execution_started"])
            self.assertFalse(queue_path.exists())

    def test_cli_requires_local_write_approval_before_queue_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_path = root / "architect-order.json"
            queue_path = root / "queue.json"
            write_task = task(
                "ARCH-WRITE-002",
                actions=["apply_local_plan", "prepare_report"],
                checks=[
                    {"type": "local_plan_applied"},
                    {"type": "file_exists", "path": "generated.py"},
                ],
            )
            write_task["local_change_plan"] = {
                "schema_version": 1,
                "task_id": "ARCH-WRITE-002",
                "changes": [{"path": "generated.py", "content": "VALUE = 2\n"}],
            }
            order_path.write_text(json.dumps(packet([write_task])), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main([
                    "--architect-order", str(order_path), "--execute-architect-order",
                    "--project-root", str(root), "--memory", str(queue_path),
                ])
            self.assertEqual(code, 2)
            self.assertIn("--authorize-local-writes", output.getvalue())
            self.assertFalse(queue_path.exists())
            self.assertFalse((root / "generated.py").exists())

    def test_cli_requires_project_code_approval_before_queue_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_path = root / "architect-order.json"
            queue_path = root / "queue.json"
            run_task = task(
                "ARCH-RUN-TESTS-001",
                actions=["run_tests", "prepare_report"],
                checks=[{"type": "tests_pass"}],
            )
            order_path.write_text(json.dumps(packet([run_task])), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main([
                    "--architect-order", str(order_path), "--execute-architect-order",
                    "--project-root", str(root), "--memory", str(queue_path),
                ])
            self.assertEqual(code, 2)
            self.assertIn("--execute-project-code", output.getvalue())
            self.assertFalse(queue_path.exists())

    def test_cli_dry_run_shows_exact_local_change_payload_for_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_path = root / "architect-order.json"
            text = "VALUE = 3\n"
            write_task = task(
                "ARCH-WRITE-PREVIEW-001",
                actions=["apply_local_plan", "prepare_report"],
                checks=[
                    {"type": "local_plan_applied"},
                    {"type": "file_exists", "path": "generated.py"},
                ],
            )
            write_task["local_change_plan"] = {
                "schema_version": 1,
                "task_id": "ARCH-WRITE-PREVIEW-001",
                "changes": [{"path": "generated.py", "content": text}],
            }
            order_path.write_text(json.dumps(packet([write_task])), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main(["--architect-order", str(order_path), "--project-root", str(root)])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(
                result["tasks_for_review"][0]["local_change_plan"]["changes"][0]["content"],
                text,
            )
            self.assertFalse((root / "generated.py").exists())

    def test_cli_executes_only_after_explicit_order_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample.py").write_text("VALUE = 7\n", encoding="utf-8")
            order_path = root / "architect-order.json"
            queue_path = root / "queue.json"
            order_path.write_text(json.dumps(packet()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main([
                    "--architect-order", str(order_path), "--execute-architect-order",
                    "--project-root", str(root), "--memory", str(queue_path),
                    "--check-timeout", "10",
                ])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0, output.getvalue())
            self.assertEqual(result["status"], "succeeded")
            self.assertTrue(result["execution_started"])
            self.assertTrue(result["queue_mutated"])
            self.assertFalse(result["automatic_merge"])
            self.assertEqual(result["execution"]["tasks"][0]["status"], "succeeded")

    def test_execute_flags_require_the_architect_order(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(["--execute-architect-order"])
        self.assertEqual(code, 2)
        self.assertIn("requires --architect-order", output.getvalue())


if __name__ == "__main__":
    unittest.main()
