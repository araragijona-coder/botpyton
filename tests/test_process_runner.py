import sys
import tempfile
import unittest
from pathlib import Path

from bot.process_runner import ProcessRunnerError, run_process


class ProcessRunnerTests(unittest.TestCase):
    def test_runs_only_explicitly_approved_executable_without_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_process(
                [sys.executable, "-c", "print('runner-ok')"],
                cwd=temp,
                allowed_root=temp,
                approved_executables=[sys.executable],
                timeout_seconds=5,
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("runner-ok", result["stdout"])
            self.assertFalse(result["shell_used"])
            self.assertFalse(result["stdin_inherited"])

    def test_rejects_unapproved_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ProcessRunnerError):
                run_process(
                    [sys.executable, "--version"],
                    cwd=temp,
                    allowed_root=temp,
                    approved_executables=[],
                )

    def test_rejects_working_directory_outside_allowed_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            with self.assertRaises(ProcessRunnerError):
                run_process(
                    [sys.executable, "--version"],
                    cwd=outside,
                    allowed_root=root,
                    approved_executables=[sys.executable],
                )

    def test_bounds_timeout_and_output_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            timeout = run_process(
                [sys.executable, "-c", "import time; time.sleep(3)"],
                cwd=temp,
                allowed_root=temp,
                approved_executables=[sys.executable],
                timeout_seconds=1,
            )
            self.assertTrue(timeout["timed_out"])
            output = run_process(
                [sys.executable, "-c", "print('x' * 1000)"],
                cwd=temp,
                allowed_root=temp,
                approved_executables=[sys.executable],
                timeout_seconds=5,
                output_limit_bytes=32,
            )
            self.assertTrue(output["stdout_truncated"])
            self.assertLessEqual(len(output["stdout"].encode("utf-8")), 32)

    def test_rejects_secret_environment_overrides_and_invalid_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ProcessRunnerError):
                run_process(
                    [sys.executable, "--version"],
                    cwd=temp,
                    allowed_root=temp,
                    approved_executables=[sys.executable],
                    env_overrides={"GITHUB_TOKEN": "must-not-pass"},
                )
            with self.assertRaises(ProcessRunnerError):
                run_process(
                    [sys.executable, "--version"],
                    cwd=temp,
                    allowed_root=temp,
                    approved_executables=[sys.executable],
                    timeout_seconds=999,
                )
            with self.assertRaises(ProcessRunnerError):
                run_process(
                    [sys.executable, "--version"],
                    cwd=temp,
                    allowed_root=temp,
                    approved_executables=[sys.executable],
                    memory_limit_mb=2048,
                )


if __name__ == "__main__":
    unittest.main()
