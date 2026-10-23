import json
import unittest

from bot.architect_revision import build_revision_context


class ArchitectRevisionContextTests(unittest.TestCase):
    def sample_snapshot(self):
        return {
            "status": "inspected",
            "tasks": [
                {
                    "task_id": "TASK-FAIL-01",
                    "status": "failed",
                    "attempts": 2,
                    "task_payload_sha256": "a" * 64,
                    "last_error": "fallback error should not replace report error",
                    "report_evidence": {
                        "status": "report_failure_or_unverified",
                        "sha256": "b" * 64,
                        "failed_action": "run_tests",
                        "error": "pytest failed; token=very-secret-value; Bearer abc.def.ghi",
                        "failed_checks": [
                            {"type": "tests_passed", "reason": "assertion failed in test_one"},
                            {"type": "python_syntax", "reason": "parse error"},
                        ],
                        "verification_checks": [
                            {"type": "tests_passed", "ok": False, "reason": "assertion failed in test_one"},
                        ],
                    },
                    "full_log": "THIS MUST NEVER LEAK",
                    "report_path": "C:/private/user/path/report.json",
                },
                {
                    "task_id": "TASK-OK-02",
                    "status": "succeeded",
                    "attempts": 1,
                    "task_payload_sha256": "c" * 64,
                    "report_evidence": {"status": "verified_success", "verified_success": True},
                },
            ],
            "report_root": {
                "status": "registered_git_worktree",
                "registered": True,
                "branch": "botpyton/task-fix",
                "head_sha": "d" * 40,
            },
        }

    def test_context_is_deterministic_bounded_and_excludes_logs_paths_and_secrets(self):
        kwargs = {
            "order_id": "ORDER-ORIGINAL",
            "request_sha256": "e" * 64,
            "queue_snapshot": self.sample_snapshot(),
            "report_root_evidence": {
                "status": "registered_git_worktree",
                "registered": True,
                "branch": "botpyton/task-fix",
                "head_sha": "d" * 40,
                "workspace_path": "C:/private/worktree",
                "diff": "secret full patch text",
            },
        }
        first = build_revision_context(**kwargs)
        second = build_revision_context(**kwargs)
        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )
        self.assertEqual(len(first["tasks"]), 1)
        failed = first["tasks"][0]
        self.assertEqual(failed["task_id"], "TASK-FAIL-01")
        self.assertEqual(failed["task_payload_sha256"], "a" * 64)
        self.assertEqual(failed["report_sha256"], "b" * 64)
        self.assertEqual(len(failed["failed_checks"]), 2)
        serialized = json.dumps(first, sort_keys=True)
        for forbidden in (
            "very-secret-value", "abc.def.ghi", "THIS MUST NEVER LEAK",
            "C:/private/user/path", "C:/private/worktree", "secret full patch text",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertIn("[REDACTED]", serialized)
        self.assertFalse(first["execution_policy"]["may_execute"])
        self.assertFalse(first["execution_policy"]["may_retry"])
        self.assertFalse(first["revision_requirements"]["local_approvals_inherited"])
        self.assertEqual(first["workspace"]["head_sha"], "d" * 40)

    def test_context_ignores_successful_tasks_and_limits_count_and_text(self):
        snapshot = {
            "tasks": [
                {
                    "task_id": f"FAIL-{index:02d}",
                    "status": "blocked",
                    "attempts": index,
                    "task_payload_sha256": "f" * 64,
                    "last_error": "x" * 10_000,
                    "report_evidence": {"status": "report_missing", "error": "y" * 10_000},
                }
                for index in range(40)
            ] + [{
                "task_id": "SUCCESS-01",
                "status": "succeeded",
                "report_evidence": {"status": "verified_success"},
            }],
        }
        context = build_revision_context(
            order_id="ORDER-BOUND",
            request_sha256="g" * 64,
            queue_snapshot=snapshot,
        )
        self.assertEqual(len(context["tasks"]), 25)
        self.assertLessEqual(len(context["tasks"][0]["error_summary"]), 240)
        self.assertEqual(context["workspace"]["status"], "unavailable")

    def test_invalid_hashes_and_workspace_claims_are_not_represented_as_verified(self):
        context = build_revision_context(
            order_id="has spaces",
            request_sha256="not-a-digest",
            queue_snapshot={
                "tasks": [{
                    "task_id": "../bad",
                    "status": "failed",
                    "attempts": 1,
                    "task_payload_sha256": "bad",
                    "report_evidence": {
                        "status": "report_invalid_json",
                        "sha256": "bad",
                        "error": "invalid report",
                    },
                }],
            },
            report_root_evidence={
                "status": "workspace_unavailable",
                "registered": False,
                "branch": "main",
                "head_sha": "not-a-sha",
            },
        )
        self.assertIsNone(context["source_order"]["order_id"])
        self.assertIsNone(context["source_order"]["request_sha256"])
        self.assertEqual(context["tasks"], [])
        self.assertNotIn("branch", context["workspace"])
        self.assertNotIn("head_sha", context["workspace"])
        self.assertFalse(context["workspace"]["registered"])

    def test_revision_context_never_inherits_local_authorizations(self):
        context = build_revision_context(
            order_id="ORDER-1",
            request_sha256="1" * 64,
            queue_snapshot={"tasks": [{
                "task_id": "TASK-1",
                "status": "failed",
                "attempts": 1,
                "report_evidence": {"status": "report_missing"},
            }]},
        )
        self.assertTrue(context["revision_requirements"]["new_order_id_required"])
        self.assertTrue(context["revision_requirements"]["new_task_ids_for_changed_work"])
        self.assertTrue(context["revision_requirements"]["changed_task_payload_required"])
        self.assertTrue(context["revision_requirements"]["unchanged_failed_payload_must_not_be_replayed"])
        self.assertFalse(context["revision_requirements"]["local_approvals_inherited"])
        self.assertFalse(context["execution_policy"]["may_change_queue"])
        self.assertFalse(context["execution_policy"]["may_publish_or_merge"])


if __name__ == "__main__":
    unittest.main()
