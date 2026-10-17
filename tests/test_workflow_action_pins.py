"""Prevent workflow dependencies from drifting to mutable action refs."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
USES_LINE = re.compile(r"^\s*uses:\s*(\S+)", re.MULTILINE)
FULL_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class WorkflowActionPinTests(unittest.TestCase):
    def test_every_external_action_is_pinned_to_a_full_commit_sha(self):
        failures: list[str] = []
        workflow_files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
        self.assertTrue(workflow_files, "No GitHub Actions workflow files were found.")

        for workflow in workflow_files:
            content = workflow.read_text(encoding="utf-8")
            for action_ref in USES_LINE.findall(content):
                if action_ref.startswith("./"):
                    continue
                action_name, separator, ref = action_ref.rpartition("@")
                if not separator or not FULL_COMMIT_SHA.fullmatch(ref):
                    failures.append(f"{workflow.relative_to(ROOT)}: {action_ref}")
        self.assertEqual(
            failures,
            [],
            "External GitHub Actions must be pinned to a full 40-character commit SHA; "
            "do not use mutable version tags:\n" + "\n".join(failures),
        )


if __name__ == "__main__":
    unittest.main()
