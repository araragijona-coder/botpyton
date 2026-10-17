import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.process_runner import (
    ProcessRunnerError,
    _find_executable_in_path,
    _safe_search_path,
    run_process,
)


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

    def test_rejects_unqualified_executable_even_when_allowlist_uses_same_name(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ProcessRunnerError, "absolute path"):
                run_process(
                    ["python", "--version"],
                    cwd=temp,
                    allowed_root=temp,
                    approved_executables=["python"],
                )

    def test_safe_search_path_drops_current_directory_relative_and_empty_entries(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as trusted:
            safe = _safe_search_path(
                os.pathsep.join([temp, ".", "", trusted, "missing-relative"]),
                cwd=temp,
            )
            self.assertEqual(safe.split(os.pathsep), [str(Path(trusted).resolve())])

    def test_tool_lookup_never_selects_executable_from_current_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            fake_name = "python.exe" if os.name == "nt" else "python"
            fake = Path(temp) / fake_name
            fake.write_text("not a real Python executable", encoding="utf-8")
            if os.name != "nt":
                fake.chmod(0o755)
            found = _find_executable_in_path("python", path_value=temp, cwd=temp)
            self.assertIsNone(found, "a project-local lookalike must not be treated as an installed tool")

    def test_child_path_does_not_contain_its_working_directory(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as other:
            env_path = os.pathsep.join([temp, other])
            code = "import os; print(os.environ.get('PATH', ''))"
            with mock.patch.dict(os.environ, {"PATH": env_path}):
                result = run_process(
                    [sys.executable, "-c", code],
                    cwd=temp,
                    allowed_root=temp,
                    approved_executables=[sys.executable],
                    timeout_seconds=5,
                )
            self.assertEqual(result["exit_code"], 0, result["stderr"])
            child_entries = result["stdout"].strip().split(os.pathsep)
            self.assertNotIn(str(Path(temp).resolve()), child_entries)
            self.assertIn(str(Path(other).resolve()), child_entries)

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

    def test_strips_inherited_python_code_loading_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "sitecustomize-ran.txt"
            payload = Path(temp) / "sitecustomize.py"
            payload.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('unexpected import', encoding='utf-8')\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"PYTHONPATH": temp}):
                result = run_process(
                    [sys.executable, "-c", "print('isolated-child')"],
                    cwd=temp,
                    allowed_root=temp,
                    approved_executables=[sys.executable],
                    timeout_seconds=5,
                )
            self.assertEqual(result["exit_code"], 0, result["stderr"])
            self.assertIn("isolated-child", result["stdout"])
            self.assertFalse(marker.exists(), "inherited PYTHONPATH must not inject sitecustomize into a child")

    def test_rejects_code_loading_environment_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            for variable in ("PYTHONPATH", "node_options", "JAVA_TOOL_OPTIONS", "LD_PRELOAD"):
                with self.subTest(variable=variable):
                    with self.assertRaises(ProcessRunnerError):
                        run_process(
                            [sys.executable, "--version"],
                            cwd=temp,
                            allowed_root=temp,
                            approved_executables=[sys.executable],
                            env_overrides={variable: "injected-loader-payload"},
                        )

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
