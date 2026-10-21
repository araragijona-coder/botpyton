import json
import tempfile
import unittest
from pathlib import Path

from bot.project_checks import ProjectCheckError, discover_project_checks, run_project_check


class ProjectCheckTests(unittest.TestCase):
    def test_discovers_python_tests_without_executing_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_smoke.py").write_text(
                "import unittest\nclass Smoke(unittest.TestCase):\n"
                "    def test_true(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
            result = discover_project_checks(root)
            self.assertEqual(result["checks_found"], 1)
            self.assertEqual(result["checks"][0]["check_id"], "python-unittest")
            self.assertFalse(result["commands_executed"])
            self.assertFalse(result["project_code_executed"])

    def test_detects_package_scripts_but_does_not_run_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text(json.dumps({
                "scripts": {"test": "echo test", "build": "echo build"}
            }), encoding="utf-8")
            result = discover_project_checks(root, which=lambda name: f"/fake/{name}" if name == "npm" else None)
            self.assertEqual({item["check_id"] for item in result["checks"]}, {"npm-test", "npm-build"})
            self.assertTrue(all(item["requires_project_code_execution_approval"] for item in result["checks"]))
            self.assertFalse(result["commands_executed"])

    def test_requires_explicit_project_code_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "tests").mkdir()
            with self.assertRaises(ProjectCheckError):
                run_project_check("python-unittest", root, execute_project_code=False)

    def test_runs_fixed_python_check_after_explicit_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_smoke.py").write_text(
                "import unittest\nclass Smoke(unittest.TestCase):\n"
                "    def test_true(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
            result = run_project_check(
                "python-unittest", root, execute_project_code=True, timeout_seconds=10
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["exit_code"], 0)
            self.assertFalse(result["shell_used"])
            self.assertTrue(result["project_code_execution_approved"])

    def test_discovers_gradle_test_profile_without_executing_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "build.gradle.kts").write_text("tasks.test { useJUnitPlatform() }\n", encoding="utf-8")
            result = discover_project_checks(
                root,
                which=lambda name: "/fake/gradle" if name == "gradle" else None,
            )
            self.assertEqual([item["check_id"] for item in result["checks"]], ["gradle-test"])
            self.assertEqual(result["checks"][0]["argv"], ["/fake/gradle", "test"])
            self.assertTrue(result["checks"][0]["requires_project_code_execution_approval"])
            self.assertFalse(result["commands_executed"])
            self.assertFalse(result["project_code_executed"])

    def test_unknown_check_and_invalid_timeout_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ProjectCheckError):
                run_project_check("unknown", root, execute_project_code=True)
            with self.assertRaises(ProjectCheckError):
                run_project_check("unknown", root, execute_project_code=True, timeout_seconds=301)


if __name__ == "__main__":
    unittest.main()
