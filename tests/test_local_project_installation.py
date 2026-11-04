import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bot import local_project_installation as install
from bot.policy import PolicyError, validate_task
from bot.executor import ActionFailure, TaskExecutor


class LocalProjectInstallationTests(unittest.TestCase):
    def _make_project(self, root: Path) -> Path:
        project = root / "tools" / "pdf-to-markdown"
        (project / "src" / "pdf_to_markdown").mkdir(parents=True)
        (project / "scripts").mkdir()
        (project / "docs").mkdir()
        (project / "src" / "pdf_to_markdown" / "__init__.py").write_text(
            "__version__ = '0.1.0'\n", encoding="utf-8"
        )
        (project / "src" / "pdf_to_markdown" / "diagnostics.py").write_text(
            "def main(): return 0\n", encoding="utf-8"
        )
        (project / "scripts" / "generate_synthetic_benchmark.py").write_text(
            "def main(): return 0\n", encoding="utf-8"
        )
        (project / ".gitignore").write_text(
            ".venv/\nbenchmark/results/\n*.egg-info/\n", encoding="utf-8"
        )
        pyproject = {
            "build-system": {"requires": ["setuptools>=75"], "build-backend": "setuptools.build_meta"},
            "project": {
                "name": "pdf-to-markdown-local",
                "version": "0.1.0",
                "requires-python": ">=3.11",
                "dependencies": ["PyMuPDF==2.0.0", "psutil>=6.0,<8"],
                "scripts": {"pdf-to-markdown-diagnostics": "pdf_to_markdown.diagnostics:main"},
            },
        }
        # Keep the fixture readable TOML instead of serializing with a dependency.
        (project / "pyproject.toml").write_text(
            "[build-system]\n"
            'requires = ["setuptools>=75"]\n'
            'build-backend = "setuptools.build_meta"\n\n'
            "[project]\n"
            'name = "pdf-to-markdown-local"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.11"\n'
            'dependencies = ["PyMuPDF==2.0.0", "psutil>=6.0,<8"]\n\n'
            "[project.scripts]\n"
            'pdf-to-markdown-diagnostics = "pdf_to_markdown.diagnostics:main"\n\n'
            "[tool.setuptools]\n"
            'package-dir = {"" = "src"}\n\n'
            "[tool.setuptools.packages.find]\n"
            'where = ["src"]\n',
            encoding="utf-8",
        )
        (project / "requirements.txt").write_text(
            "PyMuPDF==2.0.0\npsutil>=6.0,<8\n", encoding="utf-8"
        )
        return project

    def test_repository_url_accepts_only_expected_https_repository(self):
        self.assertTrue(install._validate_repository_url(
            "https://github.com/jonhararagi/botimagen-referencias-privadas.git"
        ))
        self.assertTrue(install._validate_repository_url(
            "https://github.com/jonhararagi/botimagen-referencias-privadas"
        ))
        self.assertFalse(install._validate_repository_url(
            "https://user:token@github.com/jonhararagi/botimagen-referencias-privadas.git"
        ))
        self.assertFalse(install._validate_repository_url(
            "git@github.com:jonhararagi/botimagen-referencias-privadas.git"
        ))
        self.assertFalse(install._validate_repository_url(
            "https://github.com/other/repository.git"
        ))
        self.assertFalse(install._validate_repository_url(
            "https://github.com/jonhararagi/botimagen-referencias-privadas.git?token=secret"
        ))

    def test_project_metadata_allows_only_reviewed_runtime_and_build_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            project = self._make_project(Path(temp))
            report = install._validate_project_files(project)
            self.assertEqual(report["runtime_dependencies"], sorted(install.PYTHON_RUNTIME_DEPENDENCIES))
            self.assertEqual(report["build_dependencies"], ["setuptools>=75"])
            self.assertEqual(report["optional_dependencies_installed"], [])
            self.assertFalse(report["ocr_installed"])


    def test_project_metadata_rejects_unreviewed_build_backend_and_setup_hooks(self):
        with tempfile.TemporaryDirectory() as temp:
            project = self._make_project(Path(temp))
            pyproject = (project / "pyproject.toml").read_text(encoding="utf-8")
            pyproject = pyproject.replace(
                'build-backend = "setuptools.build_meta"',
                'build-backend = "arbitrary.backend:build"',
            )
            (project / "pyproject.toml").write_text(pyproject, encoding="utf-8")
            with self.assertRaises(install._StopInstallation) as caught:
                install._validate_project_files(project)
            self.assertEqual(caught.exception.code, "unreviewed_build_backend")

        with tempfile.TemporaryDirectory() as temp:
            project = self._make_project(Path(temp))
            (project / "setup.py").write_text("raise RuntimeError('must not run')\\n", encoding="utf-8")
            with self.assertRaises(install._StopInstallation) as caught:
                install._validate_project_files(project)
            self.assertEqual(caught.exception.code, "unreviewed_setup_hook")

    def test_project_metadata_rejects_unreviewed_runtime_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            project = self._make_project(Path(temp))
            pyproject = (project / "pyproject.toml").read_text(encoding="utf-8")
            pyproject = pyproject.replace(
                '"psutil>=6.0,<8"',
                '"psutil>=6.0,<8", "docling"',
            )
            (project / "pyproject.toml").write_text(pyproject, encoding="utf-8")
            with self.assertRaises(install._StopInstallation) as caught:
                install._validate_project_files(project)
            self.assertEqual(caught.exception.code, "unexpected_runtime_dependencies")

    def test_project_metadata_requires_local_output_paths_to_be_gitignored(self):
        with tempfile.TemporaryDirectory() as temp:
            project = self._make_project(Path(temp))
            (project / ".gitignore").write_text(".venv/\n", encoding="utf-8")
            with self.assertRaises(install._StopInstallation) as caught:
                install._validate_project_files(project)
            self.assertEqual(caught.exception.code, "local_artifacts_not_ignored")

    def test_quick_result_validation_requires_json_and_csv_for_this_run(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            results = project / install.RESULTS_SUBDIRECTORY
            run_dir = results / "20261030-120000-abcdef"
            run_dir.mkdir(parents=True)
            summary = {
                "tool": "pdf-to-markdown-local-diagnostics",
                "run_id": run_dir.name,
                "overall_status": "complete",
                "fixture_unchanged": True,
                "metadata": {
                    "profile_set": "quick",
                    "fixture_name": "F-12-batch-120-pages.pdf",
                    "privacy": {
                        "synthetic_fixture_only": True,
                        "network_used_by_diagnostic": False,
                    },
                },
                "results": [{
                    "profile": "01-first20-batch-auto",
                    "page_mode": "first20",
                    "total_pages": 120,
                    "selected_pages": 20,
                    "failed_pages": 0,
                    "image_only_pages": 0,
                    "wall_seconds": 2.0,
                    "conversion_seconds": 1.5,
                    "cpu_one_core_percent": 60.0,
                    "cpu_machine_percent": 5.0,
                    "peak_rss_mb": 110.0,
                    "peak_working_set_mb": 120.0,
                    "available_ram_start_mb": 1800,
                    "available_ram_end_mb": 1700,
                    "disk_free_start_mb": 9000,
                    "disk_free_end_mb": 8999,
                    "page_markers_valid": True,
                    "source_hash_unchanged": True,
                    "status": "complete",
                    "execution_ok": True,
                }],
            }
            (run_dir / "performance-report.json").write_text(
                json.dumps(summary), encoding="utf-8"
            )
            with (run_dir / "performance-report.csv").open(
                "w", newline="", encoding="utf-8-sig"
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["profile", "page_mode", "status", "execution_ok"],
                )
                writer.writeheader()
                writer.writerow({
                    "profile": "01-first20-batch-auto",
                    "page_mode": "first20",
                    "status": "complete",
                    "execution_ok": "True",
                })

            parsed, paths = install._collect_quick_results(project, set())
            self.assertEqual(parsed["metadata"]["profile_set"], "quick")
            self.assertEqual(parsed["results"][0]["selected_pages"], 20)
            self.assertTrue(Path(paths["json"]).is_file())
            self.assertTrue(Path(paths["csv"]).is_file())

    def test_quick_result_validation_rejects_standard_or_unrelated_run(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            run_dir = project / install.RESULTS_SUBDIRECTORY / "new-run"
            run_dir.mkdir(parents=True)
            (run_dir / "performance-report.json").write_text(
                json.dumps({
                    "tool": "pdf-to-markdown-local-diagnostics",
                    "run_id": "new-run",
                    "overall_status": "complete",
                    "fixture_unchanged": True,
                    "metadata": {
                        "profile_set": "standard",
                        "fixture_name": "F-12-batch-120-pages.pdf",
                        "privacy": {
                            "synthetic_fixture_only": True,
                            "network_used_by_diagnostic": False,
                        },
                    },
                    "results": [{}],
                }),
                encoding="utf-8",
            )
            (run_dir / "performance-report.csv").write_text(
                "profile,page_mode,execution_ok\nprofile,first20,True\n",
                encoding="utf-8",
            )
            with self.assertRaises(install._StopInstallation) as caught:
                install._collect_quick_results(project, set())
            self.assertEqual(caught.exception.code, "performance_report_mismatch")

    def test_interactive_git_mode_rejects_every_non_git_transfer_operation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            hooks = root / "empty-hooks"
            hooks.mkdir()
            config = root / "git-security.config"
            config.write_text("[credential]\n    helper = manager\n", encoding="utf-8")
            with self.assertRaisesRegex(
                install.LocalProjectInstallationError,
                "restricted to Git clone, fetch, and pull",
            ):
                install._run_git_interactive(
                    "C:\\Program Files\\Git\\bin\\git.exe",
                    ["status"],
                    cwd=root,
                    allowed_root=root,
                    hooks_path=hooks,
                    config_file=config,
                )

    def test_venv_inspection_script_is_valid_python(self):
        script = install._venv_inspection_script()
        compile(script, "<venv-inspection>", "exec")

    def test_destination_must_be_inside_user_profile(self):
        outside = Path(Path.home().anchor) / "Windows" / "Temp" / "botpyton-outside-profile"
        with self.assertRaises(install.LocalProjectInstallationError):
            install._expand_destination(outside)

    def test_preview_does_not_authorize_or_perform_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "source" / "repo"
            with (
                patch.object(install, "_expand_destination", return_value=destination),
                patch.object(install, "_existing_anchor", return_value=Path(temp)),
                patch.object(install, "get_system_profile", return_value={
                    "os": "Windows 11", "system": "Windows", "processor": "AMD Ryzen",
                    "cpu_count": 12, "available_memory_mb": 1800, "disk_free_mb": 9000,
                }),
                patch.object(install, "run_process") as run_process,
                patch.object(install.subprocess, "Popen") as popen,
            ):
                result = install.run_local_project_installation(execute=False)

            self.assertEqual(result["status"], "preview")
            self.assertFalse(result["execution_authorized"])
            self.assertFalse(result["network"]["github_clone_or_fetch_may_occur"])
            self.assertFalse(result["security"]["pdf_or_conversion_uploaded"])
            run_process.assert_not_called()
            popen.assert_not_called()


    def test_task_policy_requires_explicit_authorization_fixed_profile_and_one_attempt(self):
        base = {
            "id": "LOCAL-INSTALL-TEST",
            "goal": "Install the approved PDF converter profile",
            "allowed_actions": ["run_local_installation"],
            "success_conditions": ["installation_succeeded"],
            "max_attempts": 1,
            "allow_local_installation": True,
            "local_installation_profile": "pdf-to-markdown-quick",
        }
        validate_task(base)

        unsafe = dict(base, allow_local_installation=False)
        with self.assertRaises(PolicyError):
            validate_task(unsafe)

        unsafe = dict(base, local_installation_profile="arbitrary-shell")
        with self.assertRaises(PolicyError):
            validate_task(unsafe)

        unsafe = dict(base, max_attempts=2)
        with self.assertRaises(PolicyError):
            validate_task(unsafe)

        unsafe = dict(base, allowed_actions=["run_local_installation", "run_local_installation"])
        with self.assertRaises(PolicyError):
            validate_task(unsafe)

    def test_task_policy_rejects_installation_fields_without_installation_action(self):
        task = {
            "id": "LOCAL-INSTALL-FIELDS-TEST",
            "goal": "Validate task",
            "allowed_actions": ["validate_task"],
            "success_conditions": ["valid"],
            "allow_local_installation": True,
        }
        with self.assertRaises(PolicyError):
            validate_task(task)

    def test_executor_dispatches_the_fixed_profile_after_interactive_human_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            executor = TaskExecutor(Path(temp))
            task = {
                "id": "LOCAL-INSTALL-EXECUTOR-TEST",
                "allow_local_installation": True,
                "local_installation_profile": "pdf-to-markdown-quick",
                "local_installation_destination": r"%USERPROFILE%\source\botimagen-referencias-privadas",
            }
            expected = {"status": "succeeded", "profile_id": "pdf-to-markdown-quick"}
            phrase = "INSTALL pdf-to-markdown-quick LOCAL-INSTALL-EXECUTOR-TEST"
            with (
                patch("bot.executor._confirm_local_installation_human", return_value=phrase),
                patch(
                    "bot.local_project_installation.run_local_project_installation",
                    return_value=expected,
                ) as run_install,
            ):
                result = executor._dispatch("run_local_installation", task, {})
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["human_approval"]["confirmation_phrase"], phrase)
            run_install.assert_called_once_with(
                "pdf-to-markdown-quick",
                r"%USERPROFILE%\source\botimagen-referencias-privadas",
                execute=True,
            )

    def test_executor_treats_a_blocked_installation_as_a_failed_action(self):
        with tempfile.TemporaryDirectory() as temp:
            executor = TaskExecutor(Path(temp))
            task = {
                "id": "LOCAL-INSTALL-BLOCKED",
                "allow_local_installation": True,
                "local_installation_profile": "pdf-to-markdown-quick",
            }
            phrase = "INSTALL pdf-to-markdown-quick LOCAL-INSTALL-BLOCKED"
            with (
                patch("bot.executor._confirm_local_installation_human", return_value=phrase),
                patch(
                    "bot.local_project_installation.run_local_project_installation",
                    return_value={"status": "blocked", "error_code": "git_missing"},
                ),
            ):
                with self.assertRaises(ActionFailure):
                    executor._dispatch("run_local_installation", task, {})

    def test_executor_refuses_installation_when_no_interactive_terminal_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            executor = TaskExecutor(Path(temp))
            task = {
                "id": "LOCAL-INSTALL-NO-TTY",
                "allow_local_installation": True,
                "local_installation_profile": "pdf-to-markdown-quick",
            }
            noninteractive = Mock(isatty=Mock(return_value=False))
            with (
                patch("bot.executor.sys.stdin", noninteractive),
                patch("builtins.input") as read_input,
                patch("bot.local_project_installation.run_local_project_installation") as run_install,
            ):
                with self.assertRaisesRegex(PolicyError, "interactive terminal"):
                    executor._dispatch("run_local_installation", task, {})
            read_input.assert_not_called()
            run_install.assert_not_called()

    def test_executor_refuses_wrong_human_confirmation_phrase(self):
        with tempfile.TemporaryDirectory() as temp:
            executor = TaskExecutor(Path(temp))
            task = {
                "id": "LOCAL-INSTALL-WRONG-CONFIRM",
                "allow_local_installation": True,
                "local_installation_profile": "pdf-to-markdown-quick",
            }
            interactive = Mock(isatty=Mock(return_value=True))
            with (
                patch("bot.executor.sys.stdin", interactive),
                patch("builtins.input", return_value="INSTALL arbitrary-shell LOCAL-INSTALL-WRONG-CONFIRM"),
                patch("bot.local_project_installation.run_local_project_installation") as run_install,
            ):
                with self.assertRaisesRegex(PolicyError, "did not match"):
                    executor._dispatch("run_local_installation", task, {})
            run_install.assert_not_called()


    def test_quick_result_validation_preserves_a_safety_guard_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            run_dir = project / install.RESULTS_SUBDIRECTORY / "20261030-120001-abcdef"
            run_dir.mkdir(parents=True)
            summary = {
                "tool": "pdf-to-markdown-local-diagnostics",
                "run_id": run_dir.name,
                "overall_status": "partial",
                "fixture_unchanged": True,
                "stopped_by_safety_guard": True,
                "metadata": {
                    "profile_set": "quick",
                    "fixture_name": "F-12-batch-120-pages.pdf",
                    "privacy": {
                        "synthetic_fixture_only": True,
                        "network_used_by_diagnostic": False,
                    },
                },
                "results": [{
                    "profile": "profile-1",
                    "page_mode": "first20",
                    "status": "skipped_safety_guard",
                    "execution_ok": False,
                }],
            }
            (run_dir / "performance-report.json").write_text(
                json.dumps(summary), encoding="utf-8"
            )
            with (run_dir / "performance-report.csv").open(
                "w", newline="", encoding="utf-8-sig"
            ) as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["profile", "page_mode", "status", "execution_ok"]
                )
                writer.writeheader()
                writer.writerow({
                    "profile": "profile-1",
                    "page_mode": "first20",
                    "status": "skipped_safety_guard",
                    "execution_ok": "False",
                })
            parsed, paths = install._collect_quick_results(project, set())
            self.assertTrue(parsed["stopped_by_safety_guard"])
            self.assertEqual(parsed["overall_status"], "partial")
            self.assertTrue(Path(paths["json"]).is_file())
            self.assertTrue(Path(paths["csv"]).is_file())

    def test_git_credential_manager_rejects_url_specific_helper(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            with patch.object(install, "_capture", side_effect=[
                {"timed_out": False, "exit_code": 0, "stdout": "manager\n"},
                {"timed_out": False, "exit_code": 0, "stdout": "credential.https://github.com.helper !steal-token\n"},
            ]):
                with self.assertRaises(install._StopInstallation) as caught:
                    install._require_gcm("C:\\Git\\bin\\git.exe", home)
            self.assertEqual(caught.exception.code, "git_url_credential_helper_forbidden")

    def test_git_security_context_creates_empty_hooks_dir_and_minimal_tls_config(self):
        with tempfile.TemporaryDirectory() as temp:
            log_dir = Path(temp) / "run"
            log_dir.mkdir()
            hooks, config = install._create_git_security_context(log_dir, "manager")
            self.assertTrue(hooks.is_dir())
            self.assertEqual(list(hooks.iterdir()), [])
            content = config.read_text(encoding="utf-8")
            self.assertIn("helper = manager", content)
            self.assertIn("sslVerify = true", content)
            self.assertNotIn("include", content.casefold())
            self.assertNotIn("filter", content.casefold())

    def test_interactive_git_command_forces_hooks_fsmonitor_and_tls_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            hooks = root / "hooks"
            hooks.mkdir()
            config = root / "git-security.config"
            config.write_text("[credential]\n    helper = manager\n", encoding="utf-8")
            fake_process = Mock()
            fake_process.wait.return_value = 0
            fake_process.returncode = 0
            fake_process.poll.return_value = 0
            with (
                patch.object(install, "_resolve_approved_executable", return_value=str(root / "git.exe")),
                patch.object(install, "_safe_environment", return_value={"PATH": ""}),
                patch.object(install, "_attach_windows_memory_job", return_value=None),
                patch.object(install.subprocess, "Popen", return_value=fake_process) as popen,
            ):
                result = install._run_git_interactive(
                    str(root / "git.exe"),
                    ["clone", "--", install.REPOSITORY_URL, str(root / "checkout")],
                    cwd=root, allowed_root=root, hooks_path=hooks, config_file=config,
                )
            argv = popen.call_args.args[0]
            self.assertIn("core.hooksPath=" + hooks.resolve().as_posix(), argv)
            self.assertIn("core.fsmonitor=false", argv)
            self.assertIn("http.sslVerify=true", argv)
            self.assertIn("http.https://github.com/.sslVerify=true", argv)
            self.assertTrue(result["git_hooks_disabled"])
            self.assertTrue(result["http_tls_verification_enforced"])

    def test_expected_commit_requires_exact_sha_not_an_ancestor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            hooks = root / "hooks"
            hooks.mkdir()
            env = {"GIT_CONFIG_GLOBAL": str(root / "config"), "GIT_CONFIG_NOSYSTEM": "1"}
            with patch.object(install, "_git_capture", return_value=install.EXPECTED_COMMIT):
                install._verify_expected_commit(
                    "C:\\Git\\bin\\git.exe", root, root, "FETCH_HEAD",
                    hooks_path=hooks, env_overrides=env,
                )
            with patch.object(install, "_git_capture", return_value="f" * 40):
                with self.assertRaises(install._StopInstallation) as caught:
                    install._verify_expected_commit(
                        "C:\\Git\\bin\\git.exe", root, root, "FETCH_HEAD",
                        hooks_path=hooks, env_overrides=env,
                    )
            self.assertEqual(caught.exception.code, "unapproved_commit_refused")

    def test_quick_result_validation_rejects_json_csv_execution_disagreement(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            run_dir = project / install.RESULTS_SUBDIRECTORY / "20261030-120002-abcdef"
            run_dir.mkdir(parents=True)
            summary = {
                "tool": "pdf-to-markdown-local-diagnostics",
                "run_id": run_dir.name,
                "overall_status": "complete",
                "fixture_unchanged": True,
                "metadata": {
                    "profile_set": "quick",
                    "fixture_name": "F-12-batch-120-pages.pdf",
                    "privacy": {
                        "synthetic_fixture_only": True,
                        "network_used_by_diagnostic": False,
                    },
                },
                "results": [{
                    "profile": "01-first20-batch-auto",
                    "page_mode": "first20",
                    "status": "complete",
                    "execution_ok": False,
                    "page_markers_valid": True,
                    "source_hash_unchanged": True,
                }],
            }
            (run_dir / "performance-report.json").write_text(json.dumps(summary), encoding="utf-8")
            (run_dir / "performance-report.csv").write_text(
                "profile,page_mode,status,execution_ok,page_markers_valid,source_hash_unchanged\n"
                "01-first20-batch-auto,first20,complete,True,True,True\n",
                encoding="utf-8",
            )
            with self.assertRaises(install._StopInstallation) as caught:
                install._collect_quick_results(project, set())
            self.assertEqual(caught.exception.code, "performance_reports_disagree")

    def test_git_credential_manager_requires_one_official_helper_only(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            with patch.object(install, "_capture", side_effect=[
                {"timed_out": False, "exit_code": 0, "stdout": "manager\n"},
                {"timed_out": False, "exit_code": 1, "stdout": ""},
                {"timed_out": False, "exit_code": 0, "stdout": "Git Credential Manager 2.x"},
            ]):
                self.assertEqual(install._require_gcm("C:\\Git\\bin\\git.exe", home), "manager")
            with patch.object(install, "_capture", return_value={
                "timed_out": False, "exit_code": 0,
                "stdout": "manager\n!powershell -Command steal-token\n",
            }):
                with self.assertRaises(install._StopInstallation) as caught:
                    install._require_gcm("C:\\Git\\bin\\git.exe", home)
                self.assertEqual(caught.exception.code, "git_credential_manager_not_exclusive")

    def test_git_transport_rejects_url_rewrite_and_custom_auth_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(install, "_capture", return_value={
                "timed_out": False, "exit_code": 0,
                "stdout": "url.https://other.example/.insteadof https://github.com/\n",
            }):
                with self.assertRaises(install._StopInstallation) as caught:
                    install._check_git_transport_config("C:\\Git\\bin\\git.exe", root, root)
                self.assertEqual(caught.exception.code, "git_custom_transport_config")


if __name__ == "__main__":
    unittest.main()
