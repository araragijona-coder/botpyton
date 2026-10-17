"""Regression tests for the read-only actionable preflight."""
import tempfile
import unittest
from pathlib import Path

from bot.doctor import CORE_FILES, run_preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in CORE_FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# test fixture\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_ready_environment_is_reported_without_launching_operations(self):
        report = run_preflight(self.root, python_version=(3, 12), platform_name="Linux",
                               available_memory_bytes=2 * 1024**3, disk_free_bytes=5 * 1024**3,
                               which=lambda name: "/usr/bin/git" if name == "git" else None)
        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["ready"])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["network_used"])
        self.assertEqual(report["operations_launched"], 0)
        self.assertEqual(report["files_written"], 0)
        self.assertEqual(report["settings_changed"], False)

    def test_old_python_blocks_and_provides_specific_next_step(self):
        report = run_preflight(self.root, python_version=(3, 10), platform_name="Linux",
                               available_memory_bytes=2 * 1024**3, disk_free_bytes=5 * 1024**3,
                               which=lambda _: None)
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["ready"])
        self.assertIn("Python 3.11", report["next_actions"][0])

    def test_low_memory_is_actionable_and_never_launches_extra_tools(self):
        report = run_preflight(self.root, python_version=(3, 12), platform_name="Linux",
                               available_memory_bytes=200 * 1024**2, disk_free_bytes=2 * 1024**3,
                               which=lambda _: None)
        self.assertEqual(report["status"], "blocked")
        check = next(item for item in report["checks"] if item["id"] == "available_memory")
        self.assertEqual(check["status"], "fail")
        self.assertIn("Close memory-heavy", check["next_action"])
        self.assertEqual(report["operations_launched"], 0)

    def test_missing_core_file_is_reported_without_guessing_or_writing(self):
        (self.root / "bot" / "policy.py").unlink()
        report = run_preflight(self.root, python_version=(3, 12), platform_name="Linux",
                               available_memory_bytes=2 * 1024**3, disk_free_bytes=2 * 1024**3,
                               which=lambda _: None)
        check = next(item for item in report["checks"] if item["id"] == "project_files")
        self.assertEqual(check["status"], "fail")
        self.assertIn("Do not recreate files by guessing", check["next_action"])
        self.assertFalse((self.root / "bot" / "policy.py").exists())

    def test_low_disk_space_warns_not_to_delete_arbitrary_files(self):
        report = run_preflight(self.root, python_version=(3, 12), platform_name="Linux",
                               available_memory_bytes=2 * 1024**3, disk_free_bytes=128 * 1024**2,
                               which=lambda _: None)
        check = next(item for item in report["checks"] if item["id"] == "disk_space")
        self.assertEqual(check["status"], "warn")
        self.assertIn("do not delete files", check["next_action"])

    def test_nonexistent_project_root_fails_closed(self):
        missing = self.root / "missing"
        report = run_preflight(missing, python_version=(3, 12), platform_name="Linux",
                               available_memory_bytes=2 * 1024**3, disk_free_bytes=2 * 1024**3,
                               which=lambda _: None)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["checks"][0]["id"], "project_root")
        self.assertEqual(report["files_written"], 0)


if __name__ == "__main__":
    unittest.main()
