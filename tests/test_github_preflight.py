import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

from bot.github_ops import GitHubAPIError, GitHubClient, GitHubOpsError
from bot.github_preflight import run_github_preflight
from bot.main import main


class FakeReadOnlyGitHub:
    def __init__(self):
        self.calls = []
        self.errors = {}
        self.repository = {
            "full_name": "acme/botpyton",
            "default_branch": "main",
            "private": True,
            "permissions": {"pull": True, "push": True, "admin": False},
        }

    def __call__(self, method, endpoint, payload):
        self.calls.append((method, endpoint, payload))
        if endpoint in self.errors:
            raise self.errors[endpoint]
        if endpoint == "/repos/acme/botpyton":
            return self.repository
        if endpoint == "/repos/acme/botpyton/git/ref/heads/main":
            return {"ref": "refs/heads/main", "object": {"sha": "a" * 40}}
        if endpoint == "/repos/acme/botpyton/pulls?state=open&per_page=1":
            return [{"number": 12}]
        if endpoint == "/repos/acme/botpyton/actions/runs?per_page=1":
            return {"total_count": 3, "workflow_runs": [{"id": 34}]}
        raise AssertionError(f"Unexpected endpoint: {method} {endpoint}")


class GitHubPreflightTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeReadOnlyGitHub()
        self.client = GitHubClient(
            repository="acme/botpyton",
            token="synthetic-test-token-never-return-this",
            transport=self.fake,
        )

    def test_preflight_checks_repo_branch_pulls_and_actions_without_mutations(self):
        result = run_github_preflight(client=self.client)

        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["authenticated_credential_used"])
        self.assertFalse(result["writes_performed"])
        self.assertFalse(result["secrets_disclosed"])
        self.assertEqual(len(self.fake.calls), 4)
        self.assertTrue(all(method == "GET" and payload is None for method, _, payload in self.fake.calls))
        self.assertNotIn("synthetic-test-token-never-return-this", json.dumps(result))
        self.assertEqual(result["checks"]["target_branch"]["head_sha"], "a" * 40)

    def test_draft_pr_intent_explains_write_permissions_are_not_proven(self):
        result = run_github_preflight(client=self.client, intent="draft-pr")

        self.assertEqual(result["status"], "ready")
        write = result["future_write_capability"]
        self.assertEqual(write["status"], "not_verified_by_read_only_probe")
        self.assertEqual(write["required_fine_grained_token_permissions"], [
            {"permission": "Contents", "access": "write"},
            {"permission": "Pull requests", "access": "write"},
        ])
        self.assertFalse(result["writes_performed"])

    def test_403_on_actions_is_reported_without_claiming_full_readiness(self):
        self.fake.errors["/repos/acme/botpyton/actions/runs?per_page=1"] = GitHubAPIError(403, "private details must not leak")
        result = run_github_preflight(client=self.client)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["checks"]["actions_runs_read"]["code"], "permission_or_policy_blocked")
        self.assertNotIn("private details must not leak", json.dumps(result))

    def test_404_does_not_claim_repository_is_nonexistent(self):
        self.fake.errors["/repos/acme/botpyton"] = GitHubAPIError(404, "hidden private repository")
        result = run_github_preflight(client=self.client)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["checks"]["repository_metadata"]["code"], "missing_or_not_accessible")
        self.assertNotIn("hidden private repository", json.dumps(result))
        self.assertEqual(len(self.fake.calls), 1)

    def test_invalid_branch_stops_before_branch_or_other_follow_up_probes(self):
        with self.assertRaisesRegex(GitHubOpsError, "safe canonical ref"):
            run_github_preflight(client=self.client, branch="../secrets")
        self.assertEqual(self.fake.calls, [("GET", "/repos/acme/botpyton", None)])

    def test_missing_credentials_are_not_reported_as_ready(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False):
            client = GitHubClient(repository="acme/botpyton", token="")
            with patch("bot.github_ops._github_cli_token", return_value=""):
                result = run_github_preflight(client=client)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["checks"]["repository_metadata"]["code"], "credentials_missing")

    def test_cli_calls_preflight_and_prints_json(self):
        with (
            patch("bot.main.GitHubClient", return_value=self.client),
            patch("bot.github_preflight.GitHubClient", return_value=self.client),
            patch("bot.main.run_github_preflight", wraps=run_github_preflight) as runner,
            patch.dict(os.environ, {"GITHUB_TOKEN": "", "GITHUB_REPOSITORY": "acme/botpyton"}),
        ):
            output = io.StringIO()
            errors = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = main(["--github-preflight", "acme/botpyton", "--github-intent", "draft-pr"])
            self.assertEqual(code, 0, errors.getvalue())
            self.assertEqual(runner.call_args.kwargs["intent"], "draft-pr")
            self.assertEqual(json.loads(output.getvalue())["status"], "ready")

    def test_cli_rejects_combining_preflight_with_other_operations(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(["--github-preflight", "acme/botpyton", "--doctor"])
        self.assertEqual(code, 2)
        self.assertIn("standalone operation", output.getvalue())


if __name__ == "__main__":
    unittest.main()
