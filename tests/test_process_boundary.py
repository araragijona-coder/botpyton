"""Regression tests for process-launch and project-code execution boundaries."""
import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

from bot.process_runner import (
    ProcessRunnerError,
    _is_windows_batch_wrapper,
    run_process,
)
from bot.project_checks import run_project_check

ROOT = Path(__file__).resolve().parents[1]


class ProcessBoundaryTests(unittest.TestCase):
    def test_process_report_discloses_that_network_access_is_not_sandboxed(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_process(
                [sys.executable, "-c", "print('boundary-check')"],
                cwd=temp,
                allowed_root=temp,
                approved_executables=[sys.executable],
                timeout_seconds=5,
            )
        self.assertEqual(result["exit_code"], 0, result["stderr"])
        self.assertFalse(result["network_isolation_enforced"])
        self.assertTrue(result["child_may_access_network"])
        self.assertFalse(result["windows_batch_wrapper_may_use_cmd"])

    def test_project_check_report_warns_that_project_code_may_use_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_smoke.py").write_text(
                "import unittest\n"
                "class Smoke(unittest.TestCase):\n"
                "    def test_true(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
            result = run_project_check(
                "python-unittest", root, execute_project_code=True, timeout_seconds=10
            )
        self.assertTrue(result["ok"], result["stderr"])
        self.assertTrue(result["project_code_may_access_network"])
        self.assertFalse(result["network_isolation_enforced"])
        self.assertIn("not network-sandboxed", result["network_security_note"])

    def test_windows_batch_wrapper_detection_is_explicit_and_platform_independent(self):
        self.assertTrue(_is_windows_batch_wrapper(r"C:\Program Files\nodejs\npm.cmd", windows=True))
        self.assertTrue(_is_windows_batch_wrapper(r"C:\Tools\helper.BAT", windows=True))
        self.assertFalse(_is_windows_batch_wrapper(r"C:\Tools\python.exe", windows=True))
        self.assertFalse(_is_windows_batch_wrapper(r"/usr/bin/npm.cmd", windows=False))

    def test_pc_care_subprocess_run_calls_are_time_bounded_and_shell_free(self):
        path = ROOT / "bot" / "pc_care.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ]
        self.assertGreater(len(calls), 0)
        for node in calls:
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            with self.subTest(line=node.lineno):
                self.assertIn("timeout", keywords, "every PC-care subprocess must be time-bounded")
                self.assertIn("shell", keywords, "every PC-care subprocess must explicitly set shell")
                self.assertIsInstance(keywords["shell"], ast.Constant)
                self.assertIs(
                    keywords["shell"].value, False,
                    "PC-care subprocess calls must not enable shell parsing",
                )

    def test_process_runner_uses_shell_false_and_closes_inherited_file_descriptors(self):
        path = ROOT / "bot" / "process_runner.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Popen"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ]
        self.assertEqual(len(calls), 1)
        self.assertTrue(
            any(keyword.arg is None and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "launch_options" for keyword in calls[0].keywords),
            "Popen must receive the audited launch_options mapping",
        )
        assignments = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "launch_options" for target in node.targets)
            and isinstance(node.value, ast.Dict)
        ]
        self.assertEqual(len(assignments), 1)
        options = {
            key.value: value for key, value in zip(assignments[0].value.keys, assignments[0].value.values)
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        self.assertIsInstance(options.get("shell"), ast.Constant)
        self.assertIs(options["shell"].value, False)
        self.assertIsInstance(options.get("close_fds"), ast.Constant)
        self.assertIs(options["close_fds"].value, True)

    def test_invalid_executable_still_fails_closed_before_process_start(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ProcessRunnerError):
                run_process(
                    ["not-an-absolute-tool", "--version"],
                    cwd=temp,
                    allowed_root=temp,
                    approved_executables=["not-an-absolute-tool"],
                )


if __name__ == "__main__":
    unittest.main()
