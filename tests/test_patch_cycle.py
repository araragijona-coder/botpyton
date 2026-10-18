"""Tests for the isolated reviewed-patch cycle."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bot.patch_cycle import PatchCycleError, apply_reviewed_patch


@unittest.skipUnless(shutil.which("git"), "Git is required for worktree tests")
class ReviewedPatchCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.email", "botpyton-test@example.invalid")
        self._git("config", "user.name", "BotPyton Tests")

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )

    def _commit_all(self):
        self._git("add", ".")
        self._git("commit", "-m", "fixture baseline")

    def _patch(self, text):
        path = self.base / "reviewed.patch"
        path.write_text(text, encoding="utf-8")
        return path

    def _value_patch(self, before="VALUE = 1", after="VALUE = 2"):
        return self._patch(
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1 +1 @@\n"
            f"-{before}\n"
            f"+{after}\n"
        )

    def test_applies_reviewed_patch_only_to_isolated_worktree(self):
        target = self.root / "app.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")
        self._commit_all()

        result = apply_reviewed_patch(
            self._value_patch(), self.root, "phase27-smoke",
            workspace_parent=self.base / "workspaces",
        )

        self.assertEqual(result["status"], "candidate_ready_for_review")
        self.assertTrue(result["patch"]["applied"])
        self.assertEqual(result["patch"]["files"], ["app.py"])
        self.assertTrue(result["verification"]["changed_paths_match"])
        self.assertTrue(result["verification"]["changed_files"][0]["ok"])
        self.assertFalse(result["source_working_tree_modified"])
        self.assertFalse(result["project_code_executed"])
        self.assertFalse(result["merge_performed"])
        self.assertFalse(result["push_performed"])
        self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\n")
        candidate = Path(result["candidate_workspace"])
        self.assertEqual((candidate / "app.py").read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertEqual(self._git("status", "--porcelain").stdout.strip(), "")

    def test_rejects_path_traversal_before_creating_worktree(self):
        (self.root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        self._commit_all()
        patch = self._patch(
            "diff --git a/../outside.py b/../outside.py\n"
            "--- a/../outside.py\n"
            "+++ b/../outside.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )

        with self.assertRaisesRegex(PatchCycleError, "Unsafe relative path|canonical relative path"):
            apply_reviewed_patch(
                patch, self.root, "phase27-traversal",
                workspace_parent=self.base / "workspaces",
            )
        self.assertFalse((self.base / "workspaces").exists())
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_rejects_file_creation_and_binary_or_mode_changes(self):
        (self.root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        self._commit_all()
        patch = self._patch(
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1 @@\n"
            "+VALUE = 2\n"
        )
        with self.assertRaisesRegex(PatchCycleError, "creation/deletion|ordinary a/"):
            apply_reviewed_patch(
                patch, self.root, "phase27-newfile",
                workspace_parent=self.base / "workspaces",
            )
        self.assertFalse((self.base / "workspaces").exists())

    def test_refuses_to_run_project_code_without_separate_consent(self):
        (self.root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        self._commit_all()
        with self.assertRaisesRegex(PatchCycleError, "explicit --execute-project-code"):
            apply_reviewed_patch(
                self._value_patch(), self.root, "phase27-no-consent",
                project_check_id="python-unittest", execute_project_code=False,
                workspace_parent=self.base / "workspaces",
            )
        self.assertFalse((self.base / "workspaces").exists())

    def test_static_syntax_failure_stops_before_candidate_tests(self):
        (self.root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_smoke.py").write_text(
            "import unittest\nclass Smoke(unittest.TestCase):\n"
            "    def test_value(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self._commit_all()

        result = apply_reviewed_patch(
            self._value_patch("VALUE = 1", "VALUE ="),
            self.root, "phase27-static-stop",
            project_check_id="python-unittest", execute_project_code=True,
            workspace_parent=self.base / "workspaces", timeout_seconds=20,
        )

        self.assertEqual(result["status"], "candidate_static_verification_failed")
        self.assertFalse(result["verification"]["changed_files"][0]["ok"])
        self.assertFalse(result["verification"]["changed_paths_match"] is False)
        self.assertFalse(result["candidate_project_check_run"])
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "VALUE = 1\n")
        self.assertFalse(result["merge_performed"])

    def test_baseline_and_candidate_checks_are_compared_once(self):
        (self.root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_value.py").write_text(
            "import unittest\nfrom app import VALUE\n\n"
            "class ValueTests(unittest.TestCase):\n"
            "    def test_expected_contract(self):\n"
            "        self.assertEqual(VALUE, 2)\n",
            encoding="utf-8",
        )
        self._commit_all()

        result = apply_reviewed_patch(
            self._value_patch(), self.root, "phase27-test-compare",
            project_check_id="python-unittest", execute_project_code=True,
            workspace_parent=self.base / "workspaces", timeout_seconds=30,
        )

        self.assertEqual(result["status"], "candidate_tests_passed", result)
        self.assertFalse(result["baseline_check"]["ok"])
        self.assertTrue(result["candidate_check"]["ok"])
        self.assertEqual(result["baseline_check"]["exit_code"], 1)
        self.assertEqual(result["candidate_check"]["exit_code"], 0)
        self.assertTrue(result["project_code_executed"])
        self.assertFalse(result["source_working_tree_modified"])
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "VALUE = 1\n")
        candidate = Path(result["candidate_workspace"])
        self.assertEqual((candidate / "app.py").read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertFalse(result["merge_performed"])
        self.assertFalse(result["push_performed"])

    def test_normal_cli_import_does_not_load_optional_patch_engine(self):
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import bot.main; print('bot.patch_cycle' in sys.modules)",
            ],
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "False", result.stderr)

    def test_source_repository_must_be_clean(self):
        (self.root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        self._commit_all()
        (self.root / "dirty.py").write_text("not committed\n", encoding="utf-8")
        with self.assertRaisesRegex(PatchCycleError, "candidate worktree"):
            apply_reviewed_patch(
                self._value_patch(), self.root, "phase27-dirty",
                workspace_parent=self.base / "workspaces",
            )
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "VALUE = 1\n")


if __name__ == "__main__":
    unittest.main()
