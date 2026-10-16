import unittest

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

    def test_empty_pr_batch_does_not_make_calls(self):
        calls = []
        client = GitHubClient(
            repository="owner/repo",
            transport=lambda method, endpoint, payload: calls.append(endpoint),
        )
        result = submit_change_plan_batch([], client=client, execute=True)
        self.assertEqual(result["mode"], "executed")
        self.assertEqual(result["count"], 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
