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

    def test_workflows_default_to_read_only_permissions(self):
        for relative in (
            ".github/workflows/botpyton-worker.yml",
            ".github/workflows/botpyton-github-ops.yml",
        ):
            with self.subTest(workflow=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertRegex(source, r"(?m)^permissions:\n(?:  [a-z-]+: read\n?)+")


if __name__ == "__main__":
    unittest.main()
