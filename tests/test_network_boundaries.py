"""Regression tests for BotPyton's explicit network boundaries."""
import ast
import contextlib
import io
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from bot.main import main
from bot.github_ops import GitHubClient, GitHubOpsError

ROOT = Path(__file__).resolve().parents[1]
NETWORK_CLIENT_ROOTS = {
    "aiohttp", "anthropic", "cohere", "httpx", "litellm", "openai",
    "requests", "socket", "urllib3", "websocket", "websockets",
}


class NetworkBoundaryRegressionTests(unittest.TestCase):
    def test_direct_network_client_imports_are_confined_to_declared_gateway(self):
        urllib_request_importers = []
        for path in sorted((ROOT / "bot").glob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = [node.module]
                else:
                    continue

                for module in imported:
                    root = module.split(".", 1)[0]
                    self.assertNotIn(
                        root, NETWORK_CLIENT_ROOTS,
                        f"unapproved network-capable client imported in {relative}: {module}",
                    )
                    if module == "urllib.request" or module.startswith("urllib.request."):
                        urllib_request_importers.append(relative)

        self.assertEqual(
            urllib_request_importers,
            ["bot/github_ops.py"],
            "direct urllib HTTP access must remain inside the explicit GitHub gateway",
        )

    def test_github_dry_run_does_not_open_a_network_connection(self):
        # Dry-run must be useful without a token and must never reach the HTTP client.
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/repo"}):
            with patch("urllib.request.urlopen", side_effect=AssertionError("network attempted")) as opener:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    code = main(["--github-plan", ".cerebro/examples/github-change-plan.json"])
        self.assertEqual(code, 0, stdout.getvalue())
        opener.assert_not_called()

    def test_absolute_external_endpoint_is_rejected_before_http(self):
        client = GitHubClient(repository="example/repo", token="synthetic-test-token")
        with patch("urllib.request.urlopen", side_effect=AssertionError("network attempted")) as opener:
            with self.assertRaises(GitHubOpsError):
                client.request("GET", "https://example.invalid/collect")
        opener.assert_not_called()

    def test_local_pc_care_does_not_implicitly_query_updates(self):
        with patch("bot.main.get_pc_care_report", return_value={"status": "collected"}):
            with patch("bot.main.print_pc_result", return_value=0):
                with patch("bot.main.check_application_updates") as update_query:
                    code = main(["--pc-check"])
        self.assertEqual(code, 0)
        update_query.assert_not_called()

    def test_project_owned_code_requires_an_explicit_execution_switch(self):
        source = (ROOT / "bot/main.py").read_text(encoding="utf-8")
        self.assertIn("if args.execute_project_code and not args.run_project_check:", source)
        self.assertIn("if args.patch_check and not args.execute_project_code:", source)
        self.assertIn(
            "args.execute_project_code",
            source,
            "project execution must remain an explicit opt-in rather than part of local inspection",
        )

    def test_github_sender_workflows_remain_disabled_and_read_only(self):
        cases = {
            ".github/workflows/botpyton-worker.yml": "submit-draft-prs",
            ".github/workflows/botpyton-github-ops.yml": "submit-draft-pr",
        }
        for relative, job_name in cases.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            match = re.search(
                rf"(?ms)^  {re.escape(job_name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
                source,
            )
            self.assertIsNotNone(match, f"sender job block not found: {relative}/{job_name}")
            job = match.group(1)
            with self.subTest(workflow=relative):
                self.assertRegex(job, r"(?m)^\s+if:\s*false\s*$")
                self.assertNotIn("contents: write", job)
                self.assertNotIn("pull-requests: write", job)


if __name__ == "__main__":
    unittest.main()
