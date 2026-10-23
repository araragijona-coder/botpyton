import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from bot.architect_order_contract import ArchitectOrderError, validate_architect_order
from bot.architect_order_response import ArchitectResponseError, ArchitectResponseWriter
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

    def test_unknown_task_fields_are_rejected(self):
        raw = packet()
        raw["tasks"][0]["shell"] = "whoami"
        with self.assertRaisesRegex(ArchitectOrderError, "Task 0 has unknown fields"):
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
            self.assertTrue(result["workspace_policy"]["required_for_execution"])
            self.assertEqual(result["workspace_policy"]["mode"], "isolated_git_worktree")
            self.assertFalse(result["workspace_policy"]["prepared"])
            self.assertFalse((root / "generated.py").exists())

    def test_file_write_order_executes_in_isolated_worktree_and_returns_diff_evidence(self):
        if not shutil.which("git"):
            self.skipTest("Git is required for isolated architect workspace tests")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()

            def git(*args):
                subprocess.run(
                    ["git", *args], cwd=project, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
                )

            git("init", "-b", "main")
            git("config", "user.email", "botpyton-test@example.invalid")
            git("config", "user.name", "BotPyton Tests")
            (project / "sample.py").write_text("VALUE = 1\\n", encoding="utf-8")
            git("add", "sample.py")
            git("commit", "-m", "baseline")

            task_value = task(
                "ARCH-WRITE-003",
                actions=["apply_local_plan", "prepare_report"],
                checks=[
                    {"type": "local_plan_applied"},
                    {"type": "file_exists", "path": "generated.py"},
                ],
            )
            task_value["success_conditions"] = ["local_plan_applied", "file_exists"]
            task_value["local_change_plan"] = {
                "schema_version": 1,
                "task_id": "ARCH-WRITE-003",
                "changes": [{"path": "generated.py", "content": "VALUE = 2\\n"}],
            }
            order_path = root / "architect-order.json"
            queue_path = root / "queue.json"
            response_path = root / "architect-response.json"
            workspace_parent = root / "workspaces"
            order_path.write_text(json.dumps(packet([task_value])), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main([
                    "--architect-order", str(order_path),
                    "--execute-architect-order", "--authorize-local-writes",
                    "--architect-response", str(response_path),
                    "--project-root", str(project), "--memory", str(queue_path),
                    "--architect-workspace-parent", str(workspace_parent),
                ])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0, output.getvalue())
            self.assertEqual(result["status"], "succeeded")
            self.assertTrue(result["workspace_policy"]["prepared"])
            self.assertEqual(result["workspace"]["status"], "inspection_complete")
            self.assertFalse(result["workspace"]["merge_performed"])
            self.assertFalse(result["workspace"]["push_performed"])
            self.assertTrue(result["workspace"]["final_inspection"]["has_changes"])
            self.assertIn("generated.py", result["workspace"]["final_inspection"]["working_tree_status"])
            workspace = Path(result["workspace"]["workspace_path"])
            self.assertTrue((workspace / "generated.py").is_file())
            self.assertEqual((workspace / "generated.py").read_text(encoding="utf-8"), "VALUE = 2\\n")
            self.assertFalse((project / "generated.py").exists())
            self.assertEqual(
                json.loads(response_path.read_text(encoding="utf-8")),
                result,
            )

    def test_file_write_order_refuses_non_git_project_before_queue_mutation(self):
        if not shutil.which("git"):
            self.skipTest("Git is required for isolated architect workspace tests")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            task_value = task(
                "ARCH-WRITE-004",
                actions=["apply_local_plan", "prepare_report"],
                checks=[
                    {"type": "local_plan_applied"},
                    {"type": "file_exists", "path": "generated.py"},
                ],
            )
            task_value["success_conditions"] = ["local_plan_applied", "file_exists"]
            task_value["local_change_plan"] = {
                "schema_version": 1,
                "task_id": "ARCH-WRITE-004",
                "changes": [{"path": "generated.py", "content": "VALUE = 4\\n"}],
            }
            order_path = root / "architect-order.json"
            queue_path = root / "queue.json"
            response_path = root / "architect-response.json"
            order_path.write_text(json.dumps(packet([task_value])), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main([
                    "--architect-order", str(order_path),
                    "--execute-architect-order", "--authorize-local-writes",
                    "--architect-response", str(response_path),
                    "--project-root", str(project), "--memory", str(queue_path),
                ])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 1, output.getvalue())
            self.assertEqual(result["status"], "execution_error")
            self.assertEqual(result["workspace"]["status"], "setup_failed")
            self.assertFalse(result["execution_started"])
            self.assertFalse(result["queue_mutated"])
            self.assertFalse(queue_path.exists())
            self.assertFalse((project / "generated.py").exists())
            self.assertEqual(json.loads(response_path.read_text(encoding="utf-8")), result)

    def test_reviewed_patch_requires_local_write_approval_and_static_acceptance(self):
        patch_text = (
            "diff --git a/app.py b/app.py\\n"
            "--- a/app.py\\n"
            "+++ b/app.py\\n"
            "@@ -1 +1 @@\\n"
            "-VALUE = 1\\n"
            "+VALUE = 2\\n"
        )
        patch_task = task(
            "ARCH-PATCH-SCHEMA-001",
            actions=["apply_reviewed_patch", "prepare_report"],
            checks=[{"type": "patch_applied"}, {"type": "python_syntax"}],
        )
        patch_task["success_conditions"] = ["patch_applied", "python_syntax"]
        patch_task["reviewed_patch"] = patch_text
        validated = validate_architect_order(packet([patch_task]))
        self.assertEqual(validated["required_local_approvals"], ["local_file_write"])
        self.assertFalse(validated["external_authorization_trusted"])
        self.assertFalse(validated["packet_can_enable_execution"])

        unsafe = task(
            "ARCH-PATCH-UNSAFE-001",
            actions=["apply_reviewed_patch", "prepare_report"],
            checks=[{"type": "patch_applied"}, {"type": "python_syntax"}],
        )
        unsafe["reviewed_patch"] = (
            "diff --git a/../outside.py b/../outside.py\\n"
            "--- a/../outside.py\\n"
            "+++ b/../outside.py\\n"
            "@@ -1 +1 @@\\n-old\\n+new\\n"
        )
        with self.assertRaisesRegex(ArchitectOrderError, "Task batch failed strict validation|unsafe relative path|canonical relative path"):
            validate_architect_order(packet([unsafe]))

    def test_inline_reviewed_patch_is_preflighted_and_applied_only_in_order_worktree(self):
        if not shutil.which("git"):
            self.skipTest("Git is required for isolated architect workspace tests")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()

            def git(*args):
                subprocess.run(
                    ["git", *args], cwd=project, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
                )

            git("init", "-b", "main")
            git("config", "user.email", "botpyton-test@example.invalid")
            git("config", "user.name", "BotPyton Tests")
            source_file = project / "app.py"
            source_file.write_text("VALUE = 1\\n", encoding="utf-8")
            git("add", "app.py")
            git("commit", "-m", "baseline")

            patch_task = task(
                "ARCH-PATCH-EXEC-001",
                actions=["apply_reviewed_patch", "prepare_report"],
                checks=[{"type": "patch_applied"}, {"type": "python_syntax"}],
            )
            patch_task["success_conditions"] = ["patch_applied", "python_syntax"]
            patch_task["reviewed_patch"] = (
                "diff --git a/app.py b/app.py\\n"
                "--- a/app.py\\n"
                "+++ b/app.py\\n"
                "@@ -1 +1 @@\\n"
                "-VALUE = 1\\n"
                "+VALUE = 2\\n"
            )
            order_path = root / "order.json"
            queue_path = root / "queue.json"
            response_path = root / "response.json"
            workspace_parent = root / "workspaces"
            order_path.write_text(json.dumps(packet([patch_task])), encoding="utf-8")

            preview = io.StringIO()
            with contextlib.redirect_stdout(preview), contextlib.redirect_stderr(preview):
                preview_code = main([
                    "--architect-order", str(order_path), "--project-root", str(project),
                    "--memory", str(queue_path),
                ])
            preview_result = json.loads(preview.getvalue())
            self.assertEqual(preview_code, 0, preview.getvalue())
            self.assertTrue(preview_result["workspace_policy"]["required_for_execution"])
            self.assertFalse(preview_result["workspace_policy"]["prepared"])
            self.assertFalse(workspace_parent.exists())
            self.assertFalse(queue_path.exists())
            self.assertEqual(source_file.read_text(encoding="utf-8"), "VALUE = 1\\n")

            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main([
                    "--architect-order", str(order_path),
                    "--execute-architect-order", "--authorize-local-writes",
                    "--architect-response", str(response_path),
                    "--project-root", str(project), "--memory", str(queue_path),
                    "--architect-workspace-parent", str(workspace_parent),
                ])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0, output.getvalue())
            self.assertEqual(result["status"], "succeeded")
            workspace = Path(result["workspace"]["workspace_path"])
            self.assertEqual(result["workspace"]["status"], "inspection_complete")
            self.assertEqual(result["workspace"]["patch_preflight"][0]["task_id"], "ARCH-PATCH-EXEC-001")
            self.assertEqual(result["workspace"]["patch_preflight"][0]["files"], ["app.py"])
            self.assertTrue(result["workspace"]["patch_preflight"][0]["applicable"])
            self.assertTrue(result["workspace"]["final_inspection"]["has_changes"])
            self.assertEqual(source_file.read_text(encoding="utf-8"), "VALUE = 1\\n")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "VALUE = 2\\n")
            report_path = Path(result["execution"]["tasks"][0]["report_path"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            patch_action = next(item for item in report["actions"] if item["action"] == "apply_reviewed_patch")
            self.assertTrue(patch_action["ok"])
            self.assertTrue(patch_action["details"]["patch_applied"])
            self.assertTrue(patch_action["details"]["git_diff_check"])
            self.assertFalse(result["automatic_merge"])
            self.assertFalse(result["workspace"]["merge_performed"])
            self.assertEqual(json.loads(response_path.read_text(encoding="utf-8")), result)

    def test_cli_executes_only_after_explicit_order_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample.py").write_text("VALUE = 7\n", encoding="utf-8")
            order_path = root / "architect-order.json"
            queue_path = root / "queue.json"
            response_path = root / "architect-response.json"
            order_path.write_text(json.dumps(packet()), encoding="utf-8")
            request_hash = hashlib.sha256(order_path.read_bytes()).hexdigest()
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main([
                    "--architect-order", str(order_path), "--execute-architect-order",
                    "--architect-response", str(response_path),
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
            self.assertEqual(result["response_type"], "botpyton.architect_response")
            self.assertEqual(result["request_sha256"], request_hash)
            self.assertEqual(json.loads(response_path.read_text(encoding="utf-8")), result)
            self.assertEqual(result["evidence"]["succeeded_task_count"], 1)
            self.assertEqual(result["next_action"]["action"], "review_execution_evidence")

    def test_cli_writes_a_correlated_review_response_without_queue_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_path = root / "order.json"
            response_path = root / "response.json"
            queue_path = root / "queue.json"
            raw = json.dumps(packet(), indent=2).encode("utf-8")
            order_path.write_bytes(raw)
            expected_hash = hashlib.sha256(raw).hexdigest()
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main([
                    "--architect-order", str(order_path),
                    "--architect-response", str(response_path),
                    "--memory", str(queue_path),
                ])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0, output.getvalue())
            self.assertEqual(result, json.loads(response_path.read_text(encoding="utf-8")))
            self.assertEqual(result["request_sha256"], expected_hash)
            self.assertEqual(result["status"], "validated_for_review")
            self.assertFalse(result["execution_started"])
            self.assertFalse(result["queue_mutated"])
            self.assertFalse(queue_path.exists())
            self.assertFalse(result["authorization"]["packet_can_grant_permission"])
            self.assertEqual(result["next_action"]["action"], "review_and_authorize")

    def test_response_path_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_path = root / "order.json"
            response_path = root / "response.json"
            order_path.write_text(json.dumps(packet()), encoding="utf-8")
            response_path.write_text("keep-existing-evidence", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main([
                    "--architect-order", str(order_path),
                    "--architect-response", str(response_path),
                ])
            self.assertEqual(code, 2)
            self.assertEqual(response_path.read_text(encoding="utf-8"), "keep-existing-evidence")
            self.assertIn("already exists", output.getvalue())

    def test_response_writer_can_replace_its_own_in_progress_record(self):
        with tempfile.TemporaryDirectory() as temp:
            response_path = Path(temp) / "response.json"
            with ArchitectResponseWriter(response_path) as writer:
                writer.write({"response_type": "botpyton.architect_response", "status": "execution_in_progress"})
                writer.write({"response_type": "botpyton.architect_response", "status": "succeeded"})
            self.assertEqual(
                json.loads(response_path.read_text(encoding="utf-8"))["status"],
                "succeeded",
            )

    def test_execute_flags_require_the_architect_order(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(["--execute-architect-order"])
        self.assertEqual(code, 2)
        self.assertIn("requires --architect-order", output.getvalue())


if __name__ == "__main__":
    unittest.main()
