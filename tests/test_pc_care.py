import os
import tempfile
import unittest
from pathlib import Path

from bot.pc_care import get_pc_care_report, read_startup_entries, scan_stale_files


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
