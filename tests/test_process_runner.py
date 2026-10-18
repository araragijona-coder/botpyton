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
    _terminate_process_tree,
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

    def test_safe_search_path_drops_working_tree_descendants_relative_and_empty_entries(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as trusted:
            nested = Path(temp) / "tools"
            nested.mkdir()
            safe = _safe_search_path(
                os.pathsep.join([temp, str(nested), ".", "", trusted, "missing-relative"]),
                cwd=temp,
            )
            self.assertEqual(safe.split(os.pathsep), [str(Path(trusted).resolve())])

    def test_safe_search_path_rejects_repository_local_symlink_alias_even_if_it_resolves_outside(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as trusted:
            local_alias = Path(temp) / "external-tools"
            try:
                local_alias.symlink_to(Path(trusted), target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlinks unavailable on this runner: {exc}")
            safe = _safe_search_path(os.pathsep.join([str(local_alias)]), cwd=temp)
            self.assertEqual(
                safe,
                "",
                f"temp={temp!r}, trusted={trusted!r}, alias={str(local_alias)!r}, "
                f"alias_resolved={str(local_alias.resolve())!r}, cwd_resolved={str(Path(temp).resolve())!r}",
            )

    def test_tool_lookup_never_selects_executable_from_current_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            fake_name = "python.exe" if os.name == "nt" else "python"
            fake = Path(temp) / fake_name
            fake.write_text("not a real Python executable", encoding="utf-8")
            if os.name != "nt":
                fake.chmod(0o755)
            found = _find_executable_in_path("python", path_value=temp, cwd=temp)
            self.assertIsNone(found, "a project-local lookalike must not be treated as an installed tool")

    def test_child_path_excludes_working_tree_and_nested_tool_directories(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as other:
            nested = Path(temp) / "tools"
            nested.mkdir()
            env_path = os.pathsep.join([temp, str(nested), other])
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
            self.assertNotIn(str(nested.resolve()), child_entries)
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


    def test_timeout_kills_descendants_that_inherit_output_pipes(self):
        import time

        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "child-survived-timeout.txt"
            child_code = (
                "import time; from pathlib import Path; "
                f"time.sleep(1.3); Path({str(marker)!r}).write_text('survived', encoding='utf-8')"
            )
            parent_code = (
                "import subprocess, sys, time; "
                "time.sleep(0.2); "
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
                "time.sleep(30)"
            )
            result = run_process(
                [sys.executable, "-c", parent_code],
                cwd=temp,
                allowed_root=temp,
                approved_executables=[sys.executable],
                timeout_seconds=1,
            )
            self.assertTrue(result["timed_out"])
            # The child would create this marker shortly after the leader is
            # killed if timeout cleanup only terminated the direct process.
            time.sleep(0.8)
            self.assertFalse(marker.exists(), "a descendant survived the process timeout")


    def test_windows_job_close_failure_uses_terminate_job_fallback(self):
        class FakeProcess:
            killed = False

            def poll(self):
                return -9 if self.killed else None

            def kill(self):
                self.killed = True

        process = FakeProcess()
        kernel32 = mock.Mock()
        kernel32.CloseHandle.return_value = 0
        kernel32.TerminateJobObject.return_value = 1

        with mock.patch("bot.process_runner.os.name", "nt"):
            handle_closed = _terminate_process_tree(process, (123, kernel32))

        self.assertFalse(handle_closed, "a failed CloseHandle must leave final cleanup ownership intact")
        kernel32.CloseHandle.assert_called_once_with(123)
        kernel32.TerminateJobObject.assert_called_once_with(123, 1)
        self.assertFalse(process.killed, "the full Job Object should be terminated before leader fallback")

    def test_windows_job_close_and_terminate_failure_falls_back_to_leader(self):
        class FakeProcess:
            killed = False

            def poll(self):
                return -9 if self.killed else None

            def kill(self):
                self.killed = True

        process = FakeProcess()
        kernel32 = mock.Mock()
        kernel32.CloseHandle.return_value = 0
        kernel32.TerminateJobObject.return_value = 0

        with mock.patch("bot.process_runner.os.name", "nt"):
            handle_closed = _terminate_process_tree(process, (123, kernel32))

        self.assertFalse(handle_closed)
        kernel32.TerminateJobObject.assert_called_once_with(123, 1)
        self.assertTrue(process.killed, "the direct process must be killed if both Job Object operations fail")


if __name__ == "__main__":
    unittest.main()
