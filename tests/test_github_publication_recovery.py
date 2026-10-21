"""Failure-injection tests for interrupted GitHub publication.

The fake transport simulates failures both before a remote write is accepted and
after the server applies it but the response is lost. No network is used.
"""
import unittest
import urllib.parse

from bot.github_ops import GitHubClient, GitHubOpsError, submit_change_plan, validate_change_plan
from test_github_ops import FakeGitHub, make_plan


class FailOnceGitHub(FakeGitHub):
    def __init__(self, method, path_prefix, *, after_accept):
        super().__init__()
        self.fail_method = method
        self.fail_path_prefix = path_prefix
        self.after_accept = after_accept
        self.failed = False

    def __call__(self, method, endpoint, payload):
        path = urllib.parse.urlparse(endpoint).path
        should_fail = (
            not self.failed
            and method == self.fail_method
            and path.startswith(self.fail_path_prefix)
        )
        if not should_fail:
            return super().__call__(method, endpoint, payload)
        self.failed = True
        if not self.after_accept:
            raise GitHubOpsError("synthetic transport failure before acceptance")
        super().__call__(method, endpoint, payload)
        raise GitHubOpsError("synthetic response lost after remote acceptance")


class GitHubPublicationRecoveryTests(unittest.TestCase):
    WRITE_BOUNDARIES = (
        ("POST", "/repos/test/repo/git/refs"),
        ("POST", "/repos/test/repo/git/trees"),
        ("POST", "/repos/test/repo/git/commits"),
        ("PATCH", "/repos/test/repo/git/ref/heads/"),
        ("POST", "/repos/test/repo/pulls"),
    )

    def _client(self, method, path_prefix, *, after_accept):
        fake = FailOnceGitHub(method, path_prefix, after_accept=after_accept)
        client = GitHubClient(repository="test/repo", token="fake-token", transport=fake)
        return fake, client

    def _assert_retry_converges(self, method, path_prefix, *, after_accept):
        plan = make_plan()
        expected = validate_change_plan(plan)
        fake, client = self._client(method, path_prefix, after_accept=after_accept)

        with self.assertRaisesRegex(GitHubOpsError, "synthetic"):
            submit_change_plan(plan, client=client, execute=True)

        result = submit_change_plan(plan, client=client, execute=True)

        self.assertEqual(result["mode"], "executed")
        self.assertEqual(result["branch_name"], expected.branch_name)
        self.assertTrue(result["draft"])
        self.assertFalse(result["automatic_merge"])
        self.assertEqual(len(fake.pull_requests), 1, "retry must not duplicate the pull request")
        self.assertEqual(fake.pull_requests[0]["state"], "open")
        self.assertEqual(fake.pull_requests[0]["draft"], True)
        self.assertIn(expected.branch_name, fake.refs)
        self.assertEqual(
            fake.files[expected.branch_name]["docs/example.md"],
            "# Example\\n\\nCreated deterministically.\\n",
        )
        self.assertFalse(any(
            method == "PATCH" and payload and payload.get("force") is True
            for method, _, payload in fake.calls
        ))
        self.assertTrue(fake.failed, "the requested failure injection must have fired")

    def test_retry_converges_when_each_write_fails_before_acceptance(self):
        for method, prefix in self.WRITE_BOUNDARIES:
            with self.subTest(method=method, endpoint=prefix):
                self._assert_retry_converges(method, prefix, after_accept=False)

    def test_retry_converges_when_each_write_succeeds_but_response_is_lost(self):
        for method, prefix in self.WRITE_BOUNDARIES:
            with self.subTest(method=method, endpoint=prefix):
                self._assert_retry_converges(method, prefix, after_accept=True)

    def test_injected_failure_never_leaks_fake_credential_in_errors(self):
        fake, client = self._client("POST", "/repos/test/repo/pulls", after_accept=True)
        with self.assertRaises(GitHubOpsError) as caught:
            submit_change_plan(make_plan(), client=client, execute=True)
        self.assertNotIn("fake-token", str(caught.exception))
        self.assertNotIn("fake-token", repr(fake.calls))


if __name__ == "__main__":
    unittest.main()
