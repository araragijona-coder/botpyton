import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from bot.workspace import WorkspaceError, create_isolated_workspace, inspect_workspace


@unittest.skipUnless(shutil.which("git"), "Git is required for worktree tests")
class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.email", "botpyton-test@example.invalid")
        self._git("config", "user.name", "BotPyton Tests")
        (self.root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        self._git("add", "app.py")
        self._git("commit", "-m", "initial commit")

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

    def test_creates_dedicated_branch_and_external_worktree(self):
        result = create_isolated_workspace(
            self.root, "task-create-file", workspace_parent=self.base / "workspaces"
        )
        workspace = Path(result["workspace_path"])
        self.assertTrue(workspace.is_dir())
        self.assertNotEqual(workspace, self.root)
        self.assertTrue((workspace / "app.py").is_file())
        self.assertTrue(result["branch"].startswith("botpyton/task-create-file-"))
        self.assertFalse(result["merge_performed"])
        status = inspect_workspace(workspace)
        self.assertFalse(status["has_changes"])
        self.assertEqual(status["head_sha"], result["base_sha"])

    def test_workspace_status_reports_changes_for_review(self):
        result = create_isolated_workspace(
            self.root, "task-review-diff", workspace_parent=self.base / "workspaces"
        )
        workspace = Path(result["workspace_path"])
        (workspace / "app.py").write_text("VALUE = 2\\n", encoding="utf-8")
        status = inspect_workspace(workspace)
        self.assertTrue(status["has_changes"])
        self.assertIn("app.py", status["working_tree_status"])
        self.assertIn("app.py", status["diff_stat"])
        self.assertFalse(status["merge_performed"])

    def test_refuses_dirty_source_repository(self):
        (self.root / "uncommitted.py").write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "not clean"):
            create_isolated_workspace(self.root, "dirty-task", workspace_parent=self.base / "workspaces")

    def test_refuses_workspace_parent_inside_source_repository(self):
        with self.assertRaisesRegex(WorkspaceError, "outside the source repository"):
            create_isolated_workspace(self.root, "inside-task", workspace_parent=self.root / "workspaces")

    def test_rejects_unsafe_task_id(self):
        with self.assertRaises(WorkspaceError):
            create_isolated_workspace(self.root, "../unsafe", workspace_parent=self.base / "workspaces")


if __name__ == "__main__":
    unittest.main()
