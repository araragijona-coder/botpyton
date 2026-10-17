import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.main import main
from bot.github_ops import GitHubClient
from bot.orchestration import (
    OrchestrationError,
    submit_change_plan_batch,
    validate_change_plan_batch,
    validate_task_batch,
)


def task(task_id, depends_on=None):
    return {
        "id": task_id,
        "goal": "Run the fixed, allowlisted repository validation",
        "depends_on": depends_on or [],
        "allowed_actions": ["validate_task", "prepare_report"],
        "success_conditions": ["task_validated", "report_created"],
        "max_attempts": 2,
    }


def plan(task_id="PR-001", *, draft=True, path="docs/proposals/generated.md"):
    return {
        "schema_version": 1,
        "task_id": task_id,
        "base_branch": "main",
        "title": "BotPyton proposed change",
        "body": "Draft only; a human must review this change.",
        "commit_message": "docs: propose generated change",
        "draft": draft,
        "changes": [{"path": path, "content": "# Proposed change\n\nReview required.\n"}],
    }


class OrchestrationTests(unittest.TestCase):
    def test_empty_task_batch_is_valid_for_dispatch_with_no_new_tasks(self):
        result = validate_task_batch([])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["task_ids"], [])
        self.assertFalse(result["ai_enabled"])
        self.assertFalse(result["external_ai_apis_enabled"])

    def test_task_batch_checks_dependency_order_and_deduplicates(self):
        result = validate_task_batch([
            task("TASK-B", ["TASK-A"]),
            task("TASK-A"),
        ])
        self.assertEqual(result["dependency_order"], ["TASK-A", "TASK-B"])
        with self.assertRaises(OrchestrationError):
            validate_task_batch([task("DUP"), task("DUP")])

    def test_task_batch_rejects_bad_actions_and_cycle(self):
        bad = task("TASK-BAD")
        bad["allowed_actions"] = ["run_arbitrary_shell"]
        with self.assertRaises(ValueError):
            validate_task_batch([bad])
        with self.assertRaises(ValueError):
            validate_task_batch([task("A", ["B"]), task("B", ["A"])])

    def test_task_batch_hard_limit_is_25(self):
        with self.assertRaises(OrchestrationError):
            validate_task_batch([task(f"TASK-{n}") for n in range(26)])

    def test_external_dependencies_are_allowed_for_persistent_queue_resolution(self):
        result = validate_task_batch([task("LATER", ["EXISTING-PERSISTED-TASK"])])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["task_ids"], ["LATER"])

    def test_pr_batch_requires_array_and_draft_only(self):
        with self.assertRaises(OrchestrationError):
            validate_change_plan_batch(plan())
        with self.assertRaises(OrchestrationError):
            validate_change_plan_batch([plan(draft=False)])
        result = validate_change_plan_batch([plan()])
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["plans"][0]["draft"])
        self.assertFalse(result["automatic_merge"])

    def test_batch_normalizes_plan_validation_errors_to_orchestration_error(self):
        # The shared GitHub validator rejects invalid draft settings, but callers of
        # the batch API must consistently receive OrchestrationError.
        with self.assertRaisesRegex(OrchestrationError, "draft must be true"):
            validate_change_plan_batch([plan(draft=False)])
        with self.assertRaisesRegex(OrchestrationError, "draft must be a JSON boolean"):
            validate_change_plan_batch([plan(draft="false")])

    def test_invalid_batch_has_zero_mutating_transport_calls(self):
        calls = []

        def transport(method, endpoint, payload):
            calls.append((method, endpoint))
            raise AssertionError("invalid batch must be rejected before GitHub access")

        client = GitHubClient(repository="owner/repo", token="fake-token", transport=transport)
        invalid = [plan("VALID-FIRST"), plan("INVALID-SECOND", draft=False)]
        with self.assertRaisesRegex(OrchestrationError, "draft must be true"):
            submit_change_plan_batch(invalid, client=client, execute=True)
        self.assertEqual(calls, [])

    def test_pr_batch_rejects_duplicates_invalid_paths_and_excess_plans(self):
        with self.assertRaises(OrchestrationError):
            validate_change_plan_batch([plan("DUP"), plan("DUP")])
        unsafe = plan()
        unsafe["changes"][0]["path"] = ".github/workflows/unsafe.yml"
        with self.assertRaises(ValueError):
            validate_change_plan_batch([unsafe])
        with self.assertRaises(OrchestrationError):
            validate_change_plan_batch([plan(f"PLAN-{i}") for i in range(11)])

    def test_entire_batch_is_validated_before_any_github_call(self):
        calls = []
        def transport(method, endpoint, payload):
            calls.append((method, endpoint))
            raise AssertionError("dry-run must never contact GitHub")
        client = GitHubClient(repository="owner/repo", token="", transport=transport)
        result = submit_change_plan_batch([plan()], client=client, execute=False)
        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(result["count"], 1)
        self.assertEqual(calls, [])

        invalid = [plan("GOOD"), plan("BAD", draft=False)]
        with self.assertRaises(OrchestrationError):
            submit_change_plan_batch(invalid, client=client, execute=True)
        self.assertEqual(calls, [])

    def test_cli_reads_bounded_task_batch_from_absolute_temp_path(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "tasks.json"
            target.write_text(json.dumps([task("CLI-TASK")]), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--validate-task-batch", str(target)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["count"], 1)

    def test_cli_github_plan_batch_dry_run_never_needs_network(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "plans.json"
            target.write_text(json.dumps([plan()]), encoding="utf-8")
            output = io.StringIO()
            with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_TOKEN": ""}):
                with contextlib.redirect_stdout(output):
                    code = main(["--github-plans", str(target)])
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["mode"], "dry_run")
            self.assertEqual(result["count"], 1)
            self.assertFalse(result["automatic_merge"])

    def test_cli_rejects_oversized_json_before_parsing(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "too-large.json"
            target.write_text(" " * 50_001, encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                code = main(["--validate-task-batch", str(target)])
            self.assertEqual(code, 2)
            self.assertIn("50000 bytes", errors.getvalue())

    def test_cli_rejects_execute_flag_without_plan_batch(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = main(["--execute-github-plans"])
        self.assertEqual(code, 2)
        self.assertIn("--execute-github-plans requires --github-plans", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
