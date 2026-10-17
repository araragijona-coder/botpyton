import tempfile
import unittest
from pathlib import Path

from bot.repository_analyzer import RepositoryAnalysisError, inspect_repository


class RepositoryAnalyzerTests(unittest.TestCase):
    def test_detects_manifests_and_languages_without_running_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            (root / "main.py").write_text("raise RuntimeError('must not run')\n", encoding="utf-8")
            (root / "package.json").write_text('{"scripts":{"test":"echo test"}}', encoding="utf-8")
            result = inspect_repository(root)
            self.assertEqual(result["status"], "inventory_complete")
            self.assertEqual(result["language_counts"]["Python"], 1)
            self.assertEqual(len(result["manifests"]), 2)
            self.assertFalse(result["project_code_executed"])
            self.assertFalse(result["network_used"])
            self.assertTrue(result["candidate_checks_not_executed"])

    def test_ignores_heavy_generated_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ignored = root / "node_modules"
            ignored.mkdir()
            (ignored / "vendor.js").write_text("ignored", encoding="utf-8")
            (root / "app.js").write_text("source", encoding="utf-8")
            result = inspect_repository(root)
            self.assertEqual(result["language_counts"], {"JavaScript": 1})
            self.assertNotIn("node_modules/vendor.js", result["sample_files"])

    def test_file_limit_is_enforced_and_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("a.py", "b.py", "c.py"):
                (root / name).write_text("", encoding="utf-8")
            result = inspect_repository(root, max_files=2)
            self.assertTrue(result["truncated"])
            self.assertLessEqual(result["files_seen"], 2)

    def test_depth_limit_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nested = root / "one" / "two"
            nested.mkdir(parents=True)
            (nested / "main.py").write_text("", encoding="utf-8")
            result = inspect_repository(root, max_depth=0)
            self.assertTrue(result["truncated"])
            self.assertEqual(result["files_seen"], 0)

    def test_invalid_root_and_limits_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RepositoryAnalysisError):
                inspect_repository(Path(temp) / "missing")
            with self.assertRaises(RepositoryAnalysisError):
                inspect_repository(temp, max_files=0)


if __name__ == "__main__":
    unittest.main()
