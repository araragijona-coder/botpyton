"""Failure-injection tests for read-only interrupted-publication inspection."""
import json
import urllib.parse
import unittest

from bot.github_ops import (
    GitHubAPIError,
    GitHubClient,
    GitHubOpsError,
    submit_change_plan,
    validate_change_plan,
)
from bot.github_publication_inspector import inspect_github_publication
from test_github_ops import FakeGitHub, make_plan


class InspectableGitHub(FakeGitHub):
    """Extends the shared in-memory fixture with bounded read APIs and fault injection."""

    def __init__(self):
        super().__init__()
        self.failures = []
        self.race_ref = None
        self.race_count = 0

    def __call__(self, method, endpoint, payload):
        parsed = urllib.parse.urlparse(endpoint)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        for path_prefix, status in self.failures:
            if method == "GET" and path.startswith(path_prefix):
                self.calls.append((method, endpoint, payload))
                raise GitHubAPIError(status, "synthetic private response must not leak")

        if method == "GET" and path == "/repos/test/repo/branches":
            self.calls.append((method, endpoint, payload))
            page = int(query.get("page", ["1"])[0])
            all_items = [
                {"name": name, "commit": {"sha": sha}}
                for name, sha in sorted(self.refs.items())
            ]
            start = (page - 1) * 100
            return all_items[start:start + 100]

        if (
            method == "GET"
            and self.race_ref
            and path == self.race_ref
        ):
            self.race_count += 1
            if self.race_count == 2:
                self.refs[self.race_ref.rsplit("/", 1)[-1]] = "ref-moved-during-read"
        return super().__call__(method, endpoint, payload)


class GitHubPublicationInspectorTests(unittest.TestCase):
    TOKEN = "ghp_DO_NOT_RETURN_TEST_SECRET"

    def _client(self, fake):
        return GitHubClient(
            repository="test/repo", token=self.TOKEN, transport=fake
        )

    def _published_state(self, *, keep_pr=False):
        fake = InspectableGitHub()
        client = self._client(fake)
        plan = make_plan()
        submit_change_plan(plan, client=client, execute=True)
        normalized = validate_change_plan(plan)
        if not keep_pr:
            fake.pull_requests.clear()
        fake.calls.clear()
        return fake, client, plan, normalized

    def _inspect(self, fake, client, plan):
        result = inspect_github_publication(plan, client=client)
        self.assertTrue(all(method == "GET" and payload is None
                            for method, _, payload in fake.calls))
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(self.TOKEN, serialized)
        self.assertNotIn("synthetic private response must not leak", serialized)
        self.assertFalse(result["writes_performed"])
        self.assertFalse(result["secrets_disclosed"])
        return result

    def test_absent_branch_is_proven_by_complete_inventory_and_same_plan_has_effect(self):
        fake = InspectableGitHub()
        fake.files["main"]["docs/example.md"] = "previous content\n"
        client = self._client(fake)
        fake.calls.clear()

        result = self._inspect(fake, client, make_plan())

        self.assertEqual(result["recommendation"], "safe_to_retry_same_plan")
        self.assertEqual(result["branch"]["status"], "absent_in_complete_inventory")
        self.assertEqual(result["reason_code"], "branch_absence_confirmed_and_plan_changes_base")
        self.assertEqual(result["pull_request"]["status"], "absent")
        self.assertGreaterEqual(sum("/branches?" in endpoint for _, endpoint, _ in fake.calls), 2)

    def test_branch_still_at_base_commit_can_retry_only_when_plan_changes_base(self):
        fake = InspectableGitHub()
        fake.files["main"]["docs/example.md"] = "previous content\n"
        normalized = validate_change_plan(make_plan())
        fake.refs[normalized.branch_name] = fake.base_sha
        fake.files[normalized.branch_name] = dict(fake.files["main"])
        fake.branch_changed_paths[normalized.branch_name] = set()
        client = self._client(fake)

        result = self._inspect(fake, client, make_plan())

        self.assertEqual(result["recommendation"], "safe_to_retry_same_plan")
        self.assertEqual(result["branch"]["status"], "at_base_commit")
        self.assertEqual(result["reason_code"], "branch_at_base_and_plan_changes_base")

    def test_branch_with_partial_or_different_content_requires_manual_review(self):
        fake = InspectableGitHub()
        plan = make_plan()
        normalized = validate_change_plan(plan)
        fake.refs[normalized.branch_name] = "partial-commit"
        fake.files[normalized.branch_name] = {"docs/example.md": "partial content\n"}
        fake.branch_changed_paths[normalized.branch_name] = {"docs/example.md"}
        client = self._client(fake)

        result = self._inspect(fake, client, plan)

        self.assertEqual(result["recommendation"], "manual_review_required")
        self.assertEqual(result["reason_code"], "planned_file_content_mismatch")
        self.assertEqual(result["branch"]["mismatched_paths"], ["docs/example.md"])

    def test_branch_exactly_matching_plan_without_pr_is_safe_to_retry(self):
        fake, client, plan, normalized = self._published_state()
        self.assertIn(normalized.branch_name, fake.refs)

        result = self._inspect(fake, client, plan)

        self.assertEqual(result["recommendation"], "safe_to_retry_same_plan")
        self.assertEqual(result["branch"]["status"], "matches_plan")
        self.assertEqual(result["pull_request"]["status"], "absent")

    def test_unplanned_additional_file_blocks_safe_retry(self):
        fake, client, plan, normalized = self._published_state()
        fake.files[normalized.branch_name]["docs/unplanned.md"] = "unexpected\n"
        fake.branch_changed_paths[normalized.branch_name].add("docs/unplanned.md")
        fake.calls.clear()

        result = self._inspect(fake, client, plan)

        self.assertEqual(result["recommendation"], "manual_review_required")
        self.assertEqual(result["reason_code"], "unplanned_changed_paths_detected")
        self.assertEqual(result["branch"]["unplanned_changed_paths"], ["docs/unplanned.md"])

    def test_matching_open_pr_is_reported_and_exact_plan_retry_is_idempotent(self):
        fake, client, plan, _ = self._published_state(keep_pr=True)

        result = self._inspect(fake, client, plan)

        self.assertEqual(result["recommendation"], "safe_to_retry_same_plan")
        self.assertEqual(result["pull_request"]["status"], "open")
        self.assertEqual(result["pull_request"]["matching_count"], 1)

    def test_closed_pr_requires_manual_review_and_is_not_reported_absent(self):
        fake, client, plan, _ = self._published_state(keep_pr=True)
        fake.pull_requests[0]["state"] = "closed"
        fake.calls.clear()

        result = self._inspect(fake, client, plan)

        self.assertEqual(result["recommendation"], "manual_review_required")
        self.assertEqual(result["pull_request"]["status"], "closed")
        self.assertEqual(result["reason_code"], "matching_pull_request_closed")

    def test_403_and_404_on_remote_probe_never_claim_absence_or_safety(self):
        for code in (403, 404):
            with self.subTest(http_status=code):
                fake, client, plan, _ = self._published_state()
                fake.failures.append(("/repos/test/repo/pulls", code))
                fake.calls.clear()

                result = self._inspect(fake, client, plan)

                self.assertNotEqual(result["recommendation"], "safe_to_retry_same_plan")
                self.assertIn(result["recommendation"], {
                    "manual_review_required", "blocked"
                })
                self.assertNotEqual(result.get("pull_request", {}).get("status"), "absent")

    def test_transport_failure_requires_manual_review_and_keeps_error_bounded(self):
        fake, client, plan, _ = self._published_state()
        fake.failures.append(("/repos/test/repo/compare/", 503))
        fake.calls.clear()

        result = self._inspect(fake, client, plan)

        self.assertEqual(result["recommendation"], "manual_review_required")
        self.assertTrue(result.get("reason_code"))
        self.assertNotIn(self.TOKEN, json.dumps(result))

    def test_ref_change_during_inspection_is_detected(self):
        fake, client, plan, normalized = self._published_state()
        fake.race_ref = f"/repos/test/repo/git/ref/heads/{normalized.branch_name}"
        fake.race_count = 0
        fake.calls.clear()

        result = self._inspect(fake, client, plan)

        self.assertEqual(result["recommendation"], "manual_review_required")
        self.assertEqual(result["reason_code"], "branch_ref_changed_during_inspection")

    def test_plan_is_validated_before_any_github_request(self):
        fake = InspectableGitHub()
        client = self._client(fake)
        bad_plan = make_plan(changes=[{"path": "../outside.py", "content": "bad"}])

        result = self._inspect(fake, client, bad_plan)

        self.assertFalse(result["plan_validated"])
        self.assertEqual(result["recommendation"], "blocked")
        self.assertEqual(result["reason_code"], "plan_validation_failed")
        self.assertEqual(fake.calls, [])

    def test_no_effective_change_or_uncorroborated_404_does_not_claim_safety(self):
        fake = InspectableGitHub()
        plan = make_plan()
        expected = validate_change_plan(plan)
        fake.files["main"]["docs/example.md"] = expected.changes["docs/example.md"]
        client = self._client(fake)

        result = self._inspect(fake, client, plan)

        self.assertEqual(result["recommendation"], "manual_review_required")
        self.assertEqual(result["reason_code"], "plan_has_no_effective_change")

    def test_404_when_reading_base_file_is_unknown_not_proven_missing(self):
        fake = InspectableGitHub()
        client = self._client(fake)

        result = self._inspect(fake, client, make_plan())

        self.assertEqual(result["recommendation"], "manual_review_required")
        self.assertIn("effect_unknown", result["reason_code"])

    def test_branch_ref_inventory_is_bounded(self):
        fake = InspectableGitHub()
        # Ten full pages means the bounded inventory cannot prove absence.
        fake.refs = {f"branch-{i:04d}": f"sha-{i}" for i in range(1000)}
        fake.refs["main"] = fake.base_sha
        client = self._client(fake)

        result = self._inspect(fake, client, make_plan())

        self.assertNotEqual(result["recommendation"], "safe_to_retry_same_plan")
        self.assertEqual(result["reason_code"], "branch_inventory_limit_reached")
        self.assertLessEqual(sum("/branches?" in endpoint for _, endpoint, _ in fake.calls), 10)


if __name__ == "__main__":
    unittest.main()
