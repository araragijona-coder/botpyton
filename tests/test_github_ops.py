import base64
import hashlib
import json
import unittest
import urllib.parse

from bot.github_ops import (
    GitHubAPIError,
    GitHubClient,
    GitHubOpsError,
    submit_change_plan,
    validate_change_plan,
)


def make_plan(**overrides):
    plan = {
        "schema_version": 1,
        "task_id": "TEST-GITHUB-OPS",
        "base_branch": "main",
        "title": "Add a deterministic example",
        "body": "Review this proposed change.",
        "commit_message": "docs: add deterministic example",
        "draft": True,
        "changes": [
            {"path": "docs/example.md", "content": "# Example\n\nCreated deterministically.\n"}
        ],
    }
    plan.update(overrides)
    return plan


class FakeGitHub:
    """In-memory GitHub API fixture; never makes network requests."""

    def __init__(self):
        self.base_sha = "base-commit"
        self.base_tree = "base-tree"
        self.refs = {"main": self.base_sha}
        self.commits = {self.base_sha: {"tree": {"sha": self.base_tree}, "parents": []}}
        self.trees = {}
        self.files = {"main": {}}
        self.pull_requests = []
        self.branch_changed_paths = {}
        self.next_pr = 10
        self.calls = []

    @staticmethod
    def _not_found():
        raise GitHubAPIError(404, "Not Found")

    def __call__(self, method, endpoint, payload):
        self.calls.append((method, endpoint, payload))
        parts = urllib.parse.urlparse(endpoint)
        path, query = parts.path, urllib.parse.parse_qs(parts.query)

        if method == "GET" and path == "/repos/test/repo":
            return {"default_branch": "main"}
        if path.startswith("/repos/test/repo/git/ref/heads/"):
            ref_name = path[len("/repos/test/repo/git/ref/heads/"):]
            if method == "GET":
                if ref_name not in self.refs:
                    self._not_found()
                return {"ref": f"refs/heads/{ref_name}", "object": {"sha": self.refs[ref_name]}}
            if method == "PATCH":
                if ref_name not in self.refs:
                    self._not_found()
                if payload.get("force") is not False:
                    raise AssertionError("BotPyton must never force-update a branch")
                self.refs[ref_name] = payload["sha"]
                self.files[ref_name] = dict(self.files.get(ref_name, self.files["main"]))
                commit = self.commits[payload["sha"]]
                tree = self.trees[commit["tree"]["sha"]]
                self.branch_changed_paths.setdefault(ref_name, set())
                for entry in tree["tree"]:
                    self.files[ref_name][entry["path"]] = entry["content"]
                    self.branch_changed_paths[ref_name].add(entry["path"])
                return {"object": {"sha": payload["sha"]}}
        if method == "POST" and path == "/repos/test/repo/git/refs":
            ref_name = payload["ref"].removeprefix("refs/heads/")
            if ref_name in self.refs:
                raise GitHubAPIError(422, "Reference already exists")
            if payload["sha"] not in self.commits:
                raise AssertionError("new branch must be based on an existing commit")
            self.refs[ref_name] = payload["sha"]
            self.files[ref_name] = dict(self.files["main"])
            self.branch_changed_paths[ref_name] = set()
            return {"ref": payload["ref"], "object": {"sha": payload["sha"]}}
        if method == "GET" and path.startswith("/repos/test/repo/git/commits/"):
            sha = path.rsplit("/", 1)[-1]
            if sha not in self.commits:
                self._not_found()
            return self.commits[sha]
        if method == "POST" and path == "/repos/test/repo/git/trees":
            tree_sha = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            self.trees[tree_sha] = payload
            return {"sha": tree_sha}
        if method == "POST" and path == "/repos/test/repo/git/commits":
            commit_sha = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            self.commits[commit_sha] = {
                "sha": commit_sha,
                "tree": {"sha": payload["tree"]},
                "parents": [{"sha": parent} for parent in payload["parents"]],
                "message": payload["message"],
            }
            return {"sha": commit_sha}
        if method == "GET" and path.startswith("/repos/test/repo/compare/"):
            comparison_ref = path[len("/repos/test/repo/compare/"):]
            if "..." not in comparison_ref:
                raise AssertionError("compare endpoint must include base...head")
            base_name, branch_name = comparison_ref.split("...", 1)
            if base_name not in self.refs or branch_name not in self.refs:
                self._not_found()
            changed = sorted(self.branch_changed_paths.get(branch_name, set()))
            return {
                "status": "ahead" if changed else "identical",
                "ahead_by": 1 if changed else 0,
                "files": [{"filename": path, "status": "modified"} for path in changed],
            }
        if method == "GET" and path.startswith("/repos/test/repo/contents/"):
            file_path = urllib.parse.unquote(path[len("/repos/test/repo/contents/"):])
            branch_name = query.get("ref", ["main"])[0]
            if branch_name not in self.files or file_path not in self.files[branch_name]:
                self._not_found()
            content = self.files[branch_name][file_path]
            return {"type": "file", "encoding": "base64",
                    "content": base64.b64encode(content.encode()).decode()}
        if method == "GET" and path == "/repos/test/repo/pulls":
            state = query.get("state", ["open"])[0]
            head = query.get("head", [""])[0]
            base = query.get("base", [""])[0]
            return [
                pull for pull in self.pull_requests
                if (state == "all" or pull["state"] == state)
                and pull["head"]["ref"] == head.split(":", 1)[-1]
                and pull["base"]["ref"] == base
            ]
        if method == "POST" and path == "/repos/test/repo/pulls":
            pull = {
                "number": self.next_pr,
                "state": "open",
                "html_url": f"https://github.com/test/repo/pull/{self.next_pr}",
                "title": payload["title"],
                "body": payload["body"],
                "draft": payload["draft"],
                "head": {"ref": payload["head"]},
                "base": {"ref": payload["base"]},
            }
            self.next_pr += 1
            self.pull_requests.append(pull)
            return pull
        raise AssertionError(f"Unexpected fake GitHub API call: {method} {endpoint}")


class GitHubPlanValidationTests(unittest.TestCase):
    def test_individual_plan_rejects_draft_false(self):
        plan = make_plan(draft=False)
        with self.assertRaisesRegex(GitHubOpsError, "draft must be true"):
            validate_change_plan(plan)

    def test_fingerprint_and_branch_are_deterministic(self):
        first = validate_change_plan(make_plan())
        second = validate_change_plan(make_plan())
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.branch_name, second.branch_name)
        self.assertTrue(first.branch_name.startswith("botpyton/test-github-ops-"))

    def test_rejects_parent_traversal(self):
        plan = make_plan(changes=[{"path": "../outside.py", "content": "bad"}])
        with self.assertRaises(ValueError):
            validate_change_plan(plan)

    def test_rejects_workflow_and_policy_paths(self):
        for path in (".github/workflows/unsafe.yml", ".cerebro/config.json", ".cerebro/policies/rules.json"):
            with self.subTest(path=path):
                plan = make_plan(changes=[{"path": path, "content": "{}"}])
                with self.assertRaisesRegex(GitHubOpsError, "Protected path"):
                    validate_change_plan(plan)

    def test_rejects_unsupported_extensions(self):
        plan = make_plan(changes=[{"path": "scripts/deploy.sh", "content": "unsafe"}])
        with self.assertRaisesRegex(GitHubOpsError, "Unsupported file type"):
            validate_change_plan(plan)

    def test_rejects_duplicate_file_paths(self):
        plan = make_plan(changes=[
            {"path": "docs/a.md", "content": "A"},
            {"path": "docs/a.md", "content": "B"},
        ])
        with self.assertRaisesRegex(GitHubOpsError, "Duplicate path"):
            validate_change_plan(plan)

    def test_rejects_too_many_files(self):
        plan = make_plan(changes=[
            {"path": f"docs/{n}.md", "content": "x"} for n in range(21)
        ])
        with self.assertRaisesRegex(GitHubOpsError, "changes must be a list"):
            validate_change_plan(plan)


class GitHubOperationTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHub()
        self.client = GitHubClient(repository="test/repo", token="fake-token", transport=self.fake)

    def test_dry_run_performs_no_api_calls(self):
        result = submit_change_plan(make_plan(), client=self.client, execute=False)
        self.assertEqual(result["mode"], "dry_run")
        self.assertFalse(result["automatic_merge"])
        self.assertEqual(self.fake.calls, [])

    def test_execute_creates_branch_one_commit_and_draft_pr(self):
        result = submit_change_plan(make_plan(), client=self.client, execute=True)
        self.assertEqual(result["mode"], "executed")
        self.assertTrue(result["branch_created"])
        self.assertTrue(result["commit_created"])
        self.assertTrue(result["pull_request_created"])
        self.assertTrue(result["draft"])
        self.assertFalse(result["automatic_merge"])
        self.assertEqual(result["pull_request_url"], "https://github.com/test/repo/pull/10")
        self.assertIn(result["branch_name"], self.fake.refs)
        self.assertEqual(self.fake.files[result["branch_name"]]["docs/example.md"],
                         "# Example\n\nCreated deterministically.\n")
        mutation_calls = [
            method for method, endpoint, _ in self.fake.calls
            if endpoint.endswith("/git/refs") or endpoint.endswith("/git/trees")
            or endpoint.endswith("/git/commits") or endpoint.endswith("/pulls")
            or (method == "PATCH" and "/git/ref/heads/" in endpoint)
        ]
        self.assertEqual(mutation_calls, ["POST", "POST", "POST", "PATCH", "POST"])

    def test_repeating_the_same_plan_is_idempotent(self):
        first = submit_change_plan(make_plan(), client=self.client, execute=True)
        second = submit_change_plan(make_plan(), client=self.client, execute=True)
        self.assertEqual(first["branch_name"], second["branch_name"])
        self.assertFalse(second["branch_created"])
        self.assertFalse(second["commit_created"])
        self.assertFalse(second["pull_request_created"])
        self.assertEqual(second["commit_sha"], first["commit_sha"])
        self.assertEqual(len(self.fake.pull_requests), 1)

    def test_existing_branch_with_unrelated_content_is_not_overwritten(self):
        first = submit_change_plan(make_plan(), client=self.client, execute=True)
        self.fake.files[first["branch_name"]]["docs/example.md"] = "human change\n"
        with self.assertRaisesRegex(GitHubOpsError, "different change"):
            submit_change_plan(make_plan(), client=self.client, execute=True)

    def test_existing_branch_with_extra_unplanned_file_is_rejected(self):
        first = submit_change_plan(make_plan(), client=self.client, execute=True)
        self.fake.branch_changed_paths[first["branch_name"]].add("docs/added-by-human.md")
        with self.assertRaisesRegex(GitHubOpsError, "different change"):
            submit_change_plan(make_plan(), client=self.client, execute=True)

    def test_non_default_base_branch_is_rejected_before_mutation(self):
        plan = make_plan(base_branch="develop")
        with self.assertRaisesRegex(GitHubOpsError, "default branch"):
            submit_change_plan(plan, client=self.client, execute=True)
        self.assertEqual(self.fake.refs, {"main": self.fake.base_sha})

    def test_closed_prior_pull_request_is_not_duplicated(self):
        result = submit_change_plan(make_plan(), client=self.client, execute=True)
        self.fake.pull_requests[0]["state"] = "closed"
        with self.assertRaisesRegex(GitHubOpsError, "prior pull request"):
            submit_change_plan(make_plan(), client=self.client, execute=True)
        self.assertEqual(len(self.fake.pull_requests), 1)

    def test_github_token_is_required_for_real_transport(self):
        client = GitHubClient(repository="test/repo", token="")
        with self.assertRaisesRegex(GitHubOpsError, "GITHUB_TOKEN is required"):
            client.request("GET", "/repos/test/repo")

    def test_never_allows_non_repository_api_endpoint(self):
        with self.assertRaisesRegex(GitHubOpsError, "repository-scoped"):
            self.client.request("GET", "https://evil.example/steal")


if __name__ == "__main__":
    unittest.main()
