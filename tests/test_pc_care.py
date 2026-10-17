import os
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch, Mock

from bot.pc_care import get_cleanup_preview, get_pc_care_report, read_startup_entries, read_top_processes, scan_stale_files


class PcCareTests(unittest.TestCase):
    def test_stale_file_estimate_includes_only_files_older_than_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_file = root / "old.tmp"
            new_file = root / "new.tmp"
            old_file.write_bytes(b"old-data")
            new_file.write_bytes(b"new-data")
            now = 1_700_000_000.0
            os.utime(old_file, (now - 9 * 86400, now - 9 * 86400))
            os.utime(new_file, (now - 2 * 86400, now - 2 * 86400))

            result = scan_stale_files(root, now=now)

            self.assertEqual(result["status"], "scan_complete")
            self.assertEqual(result["candidate_files"], 1)
            self.assertEqual(result["candidate_bytes"], len(b"old-data"))

    def test_scan_is_bounded_and_marks_a_partial_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(5):
                (root / f"{index}.tmp").write_text("x", encoding="utf-8")
            now = 1_700_000_000.0
            for item in root.iterdir():
                os.utime(item, (now - 9 * 86400, now - 9 * 86400))

            result = scan_stale_files(root, now=now, max_entries=2)

            self.assertEqual(result["status"], "scan_partial")
            self.assertTrue(result["scan_limited"])
            self.assertLessEqual(result["entries_examined"], 2)

    def test_non_windows_startup_inventory_is_explicitly_unsupported(self):
        result = read_startup_entries(windows=False)
        self.assertEqual(result["status"], "unsupported_platform")
        self.assertEqual(result["items_count"], 0)

    def test_cleanup_preview_lists_old_temp_files_without_deleting_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_file = root / "old.tmp"
            old_file.write_bytes(b"temporary")
            now = 1_700_000_000.0
            os.utime(old_file, (now - 12 * 86400, now - 12 * 86400))
            result = get_cleanup_preview(scan_roots=[("temporary files", root)], now=now)
            self.assertEqual(result["status"], "preview_complete")
            self.assertEqual(result["candidate_count"], 1)
            self.assertEqual(result["candidates"][0]["path"], str(old_file.resolve()))
            self.assertEqual(result["files_deleted"], 0)
            self.assertTrue(old_file.exists())

    def test_cleanup_preview_excludes_browser_cache_and_personal_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_file = root / "old.tmp"
            old_file.write_bytes(b"not eligible")
            now = 1_700_000_000.0
            os.utime(old_file, (now - 12 * 86400, now - 12 * 86400))
            result = get_cleanup_preview(
                scan_roots=[("Chrome default-profile cache", root), ("downloads", root)],
                now=now,
            )
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(len(result["skipped_roots"]), 2)

    def test_cleanup_preview_enforces_candidate_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = 1_700_000_000.0
            for index in range(4):
                item = root / f"{index}.tmp"
                item.write_bytes(b"x")
                os.utime(item, (now - 12 * 86400, now - 12 * 86400))
            result = get_cleanup_preview(scan_roots=[("temporary files", root)], now=now, max_candidates=2)
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["status"], "preview_partial")

    def test_non_windows_process_inventory_is_explicitly_unsupported(self):
        result = read_top_processes(windows=False)
        self.assertEqual(result["status"], "unsupported_platform")
        self.assertEqual(result["items_count"], 0)

    @patch("bot.pc_care.subprocess.run")
    def test_process_inventory_sorts_by_memory_and_limits_output(self, run):
        run.return_value = Mock(
            returncode=0,
            stdout='"small.exe","21","Console","1","1,024 K"\n"large.exe","42","Console","1","20,480 K"\n',
        )
        result = read_top_processes(windows=True, limit=1)
        self.assertEqual(result["status"], "collected")
        self.assertEqual(result["items_count"], 2)
        self.assertEqual(result["returned_count"], 1)
        self.assertEqual(result["items"][0]["name"], "large.exe")
        self.assertEqual(result["items"][0]["pid"], 42)
        run.assert_called_once()
        self.assertFalse(run.call_args.kwargs["shell"])

    @patch("bot.pc_care.subprocess.run")
    def test_process_inventory_handles_timeout_without_modifying_processes(self, run):
        import subprocess
        run.side_effect = subprocess.TimeoutExpired(cmd="tasklist", timeout=5)
        result = read_top_processes(windows=True)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["items"], [])

    def test_report_never_claims_to_have_modified_the_pc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = get_pc_care_report(
                scan_roots=[("test folder", root)],
                startup_items={"status": "collected", "items": [], "items_count": 0},
                windows=False,
                now=1_700_000_000.0,
            )
        self.assertTrue(report["read_only"])
        self.assertFalse(report["network_used"])
        self.assertFalse(report["ai_enabled"])
        self.assertEqual(report["files_deleted"], 0)
        self.assertFalse(report["settings_changed"])
        self.assertFalse(report["drivers_updated"])
        self.assertFalse(report["applications_updated"])
        self.assertEqual(report["status"], "unsupported_platform")
        self.assertEqual(report["driver_updates"]["status"], "not_checked_non_windows")

    def test_missing_directory_is_not_reported_as_reclaimable(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            result = scan_stale_files(missing)
        self.assertEqual(result["status"], "not_present")
        self.assertEqual(result["candidate_files"], 0)
        self.assertEqual(result["candidate_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
