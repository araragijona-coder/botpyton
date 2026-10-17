"""Static fail-closed checks for privileged GitHub Actions workflows."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PrivilegedWorkflowSecurityTests(unittest.TestCase):
    def test_both_live_submission_jobs_are_disabled_and_do_not_bind_token(self):
        cases = {
            ".github/workflows/botpyton-worker.yml": "submit-draft-prs:",
            ".github/workflows/botpyton-github-ops.yml": "submit-draft-pr:",
        }
        for relative, job_marker in cases.items():
            with self.subTest(workflow=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn(job_marker, source)
                job_start = source.index(job_marker)
                tail = source[job_start + len(job_marker):]
                next_match = re.search(r"\n  [A-Za-z0-9_-]+:\n", tail)
                job = source[job_start: job_start + len(job_marker) + next_match.start() if next_match else len(source)]
                self.assertRegex(job, r"(?m)^\s+if: false\s*$")
                self.assertNotIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", job)
                self.assertNotIn("actions/checkout", job)
                self.assertNotIn("contents: write", job)
                self.assertNotIn("pull-requests: write", job)
                self.assertNotIn("--execute-github-plan", job)
                self.assertNotIn("--execute-github-plans", job)

    def test_no_untrusted_workflow_trigger_or_write_permission_is_introduced(self):
        for relative in (
            ".github/workflows/botpyton-worker.yml",
            ".github/workflows/botpyton-github-ops.yml",
        ):
            with self.subTest(workflow=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                # These triggers can hand privileged workflow execution to code
                # or artifacts associated with an untrusted branch/PR.
                self.assertNotRegex(source, r"(?m)^\s*(?:pull_request_target|workflow_run)\s*:")
                # The worker may use the token read-only for artifact download; the sender job is separately checked above.
                self.assertNotRegex(source, r"(?m)^\s*(?:contents|pull-requests|actions):\s*write\s*$")
                self.assertNotIn("--execute-github-plan", source)
                self.assertNotIn("--execute-github-plans", source)

    def test_sender_has_no_execution_surface_even_while_disabled(self):
        cases = {
            ".github/workflows/botpyton-worker.yml": "submit-draft-prs:",
            ".github/workflows/botpyton-github-ops.yml": "submit-draft-pr:",
        }
        for relative, marker in cases.items():
            with self.subTest(workflow=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                start = source.index(marker)
                tail = source[start + len(marker):]
                next_job = re.search(r"\n  [A-Za-z0-9_-]+:\n", tail)
                job = source[start:start + len(marker) + next_job.start() if next_job else len(source)]
                self.assertNotIn("uses:", job)
                self.assertNotIn("run: python", job)
                self.assertNotIn("env:", job)
                self.assertRegex(job, r"(?m)^\s+if: false\s*$")

    def test_workflows_default_to_read_only_permissions(self):
        for relative in (
            ".github/workflows/botpyton-worker.yml",
            ".github/workflows/botpyton-github-ops.yml",
        ):
            with self.subTest(workflow=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertRegex(source, r"(?m)^permissions:\n(?:  [a-z-]+: read\n?)+")


    def test_external_actions_are_pinned_to_full_commit_shas(self):
        workflows = (
            ".github/workflows/bot-tests.yml",
            ".github/workflows/botpyton-worker.yml",
            ".github/workflows/botpyton-github-ops.yml",
        )
        for relative in workflows:
            with self.subTest(workflow=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                uses_lines = re.findall(r"(?m)^\\s+uses:\\s+([^\\s]+)", source)
                external = [item for item in uses_lines if not item.startswith("./")]
                self.assertTrue(external, "expected external actions to audit")
                for action_ref in external:
                    self.assertRegex(
                        action_ref,
                        r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$",
                        f"action is not pinned to a full commit SHA: {action_ref}",
                    )

                checkout_count = len(re.findall(r"(?m)^\\s+uses:\\s+actions/checkout@[0-9a-f]{40}\\b", source))
                credentials_disabled_count = len(re.findall(r"(?m)^\\s+persist-credentials:\\s+false\\s*$", source))
                self.assertEqual(
                    checkout_count,
                    credentials_disabled_count,
                    "every checkout must disable persisted Git credentials",
                )


if __name__ == "__main__":
    unittest.main()
