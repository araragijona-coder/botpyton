import os
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch, Mock

from bot.pc_care import get_cleanup_preview, get_pc_care_report, read_installed_applications, read_startup_entries, read_top_processes, scan_stale_files


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

    def test_non_windows_application_inventory_is_explicitly_unsupported(self):
        result = read_installed_applications(windows=False)
        self.assertEqual(result["status"], "unsupported_platform")
        self.assertEqual(result["items_count"], 0)

    def test_application_inventory_limit_is_validated(self):
        with self.assertRaises(ValueError):
            read_installed_applications(windows=False, limit=0)

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


    def test_quarantine_requires_exact_confirmation_and_does_not_move_on_mismatch(self):
        from bot.pc_care import quarantine_cleanup_candidates, QUARANTINE_CONFIRMATION
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp) / "temporary"
            temp_root.mkdir()
            quarantine_root = Path(tmp) / "Quarantine"
            old_file = temp_root / "old.tmp"
            old_file.write_bytes(b"old data")
            now = 1_700_000_000.0
            os.utime(old_file, (now - 12 * 86400, now - 12 * 86400))
            preview = get_cleanup_preview(scan_roots=[("temporary files", temp_root)], now=now)
            with self.assertRaises(ValueError):
                quarantine_cleanup_candidates(
                    preview, confirmation="yes", scan_roots=[("temporary files", temp_root)],
                    quarantine_root=quarantine_root, now=now,
                )
            self.assertTrue(old_file.exists())
            self.assertFalse(quarantine_root.exists())
            self.assertEqual(QUARANTINE_CONFIRMATION, "QUARANTINE OLD TEMP FILES")

    def test_quarantine_and_restore_round_trip_with_manifest(self):
        from bot.pc_care import (
            QUARANTINE_CONFIRMATION, RESTORE_CONFIRMATION,
            quarantine_cleanup_candidates, restore_quarantined_files,
        )
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp) / "temporary"
            temp_root.mkdir()
            quarantine_root = Path(tmp) / "Quarantine"
            old_file = temp_root / "old.tmp"
            old_file.write_bytes(b"old data to restore")
            now = 1_700_000_000.0
            old_mtime = now - 12 * 86400
            os.utime(old_file, (old_mtime, old_mtime))
            original_bytes = old_file.read_bytes()
            preview = get_cleanup_preview(scan_roots=[("temporary files", temp_root)], now=now)
            result = quarantine_cleanup_candidates(
                preview, confirmation=QUARANTINE_CONFIRMATION,
                scan_roots=[("temporary files", temp_root)],
                quarantine_root=quarantine_root, now=now,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["files_quarantined"], 1)
            self.assertFalse(old_file.exists())
            manifest_path = Path(result["manifest_path"])
            self.assertTrue(manifest_path.is_file())
            self.assertTrue((manifest_path.parent / "0001-old.tmp").is_file())
            restored = restore_quarantined_files(
                manifest_path, confirmation=RESTORE_CONFIRMATION,
                scan_roots=[("temporary files", temp_root)],
            )
            self.assertEqual(restored["status"], "restored")
            self.assertEqual(restored["files_restored"], 1)
            self.assertTrue(old_file.is_file())
            self.assertEqual(old_file.read_bytes(), original_bytes)
            self.assertFalse((manifest_path.parent / "0001-old.tmp").exists())

    def test_quarantine_rejects_partial_preview(self):
        from bot.pc_care import quarantine_cleanup_candidates, QUARANTINE_CONFIRMATION
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "temporary"
            root.mkdir()
            old_file = root / "old.tmp"
            old_file.write_bytes(b"x")
            now = 1_700_000_000.0
            os.utime(old_file, (now - 12 * 86400, now - 12 * 86400))
            preview = get_cleanup_preview(scan_roots=[("temporary files", root)], now=now, max_candidates=1)
            preview["status"] = "preview_partial"
            with self.assertRaises(ValueError):
                quarantine_cleanup_candidates(
                    preview, confirmation=QUARANTINE_CONFIRMATION,
                    scan_roots=[("temporary files", root)],
                    quarantine_root=Path(tmp) / "Quarantine", now=now,
                )
            self.assertTrue(old_file.exists())

    def test_quarantine_skips_file_changed_since_preview(self):
        from bot.pc_care import quarantine_cleanup_candidates, QUARANTINE_CONFIRMATION
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "temporary"
            root.mkdir()
            old_file = root / "old.tmp"
            old_file.write_bytes(b"x")
            now = 1_700_000_000.0
            os.utime(old_file, (now - 12 * 86400, now - 12 * 86400))
            preview = get_cleanup_preview(scan_roots=[("temporary files", root)], now=now)
            old_file.write_bytes(b"changed size after preview")
            result = quarantine_cleanup_candidates(
                preview, confirmation=QUARANTINE_CONFIRMATION,
                scan_roots=[("temporary files", root)],
                quarantine_root=Path(tmp) / "Quarantine", now=now,
            )
            self.assertEqual(result["files_quarantined"], 0)
            self.assertTrue(old_file.exists())
            self.assertEqual(result["files_skipped"], 1)

    def test_restore_never_overwrites_an_existing_original_path(self):
        from bot.pc_care import (
            QUARANTINE_CONFIRMATION, RESTORE_CONFIRMATION,
            quarantine_cleanup_candidates, restore_quarantined_files,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "temporary"
            root.mkdir()
            quarantine_root = Path(tmp) / "Quarantine"
            old_file = root / "old.tmp"
            old_file.write_bytes(b"original")
            now = 1_700_000_000.0
            os.utime(old_file, (now - 12 * 86400, now - 12 * 86400))
            preview = get_cleanup_preview(scan_roots=[("temporary files", root)], now=now)
            moved = quarantine_cleanup_candidates(
                preview, confirmation=QUARANTINE_CONFIRMATION,
                scan_roots=[("temporary files", root)],
                quarantine_root=quarantine_root, now=now,
            )
            self.assertEqual(moved["files_quarantined"], 1)
            old_file.write_bytes(b"user's new file")
            restored = restore_quarantined_files(
                moved["manifest_path"], confirmation=RESTORE_CONFIRMATION,
                scan_roots=[("temporary files", root)],
            )
            self.assertEqual(restored["files_restored"], 0)
            self.assertEqual(old_file.read_bytes(), b"user's new file")
            self.assertEqual(restored["files_skipped"], 1)


    def test_cleanup_execution_root_must_be_canonical_windows_user_temp(self):
        from bot.pc_care import _is_conventional_temp_path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local_app_data = root / "AppData" / "Local"
            canonical_temp = local_app_data / "Temp"
            personal_documents = root / "Documents"
            canonical_temp.mkdir(parents=True)
            personal_documents.mkdir()
            env = {"LOCALAPPDATA": str(local_app_data), "TEMP": str(personal_documents)}
            self.assertTrue(_is_conventional_temp_path(canonical_temp, env=env, windows=True))
            self.assertFalse(_is_conventional_temp_path(personal_documents, env=env, windows=True))
            self.assertFalse(_is_conventional_temp_path(canonical_temp, env=env, windows=False))


    def test_update_check_is_unsupported_off_windows_and_does_not_start_process(self):
        from bot.pc_care import check_application_updates
        with patch("bot.pc_care.subprocess.run") as run:
            with patch("bot.pc_care.shutil.which") as which:
                result = check_application_updates(windows=False)
        self.assertEqual(result["status"], "unsupported_platform")
        self.assertEqual(result["updates_installed"], 0)
        run.assert_not_called()
        which.assert_not_called()

    def test_update_check_verifies_trusted_source_and_runs_listing_only(self):
        import json
        from bot.pc_care import check_application_updates
        with tempfile.TemporaryDirectory() as tmp:
            alias = Path(tmp) / "Microsoft" / "WindowsApps" / "winget.exe"
            source_json = json.dumps({
                "Name": "winget",
                "Arg": "https://cdn.winget.microsoft.com/cache",
                "Type": "Microsoft.PreIndexed.Package",
                "TrustLevel": ["Trusted", "StoreOrigin"],
            })
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}, clear=False):
                with patch("bot.pc_care.shutil.which", return_value=str(alias)):
                    with patch("bot.pc_care.subprocess.run") as run:
                        run.side_effect = [
                            Mock(returncode=0, stdout=source_json, stderr=""),
                            Mock(returncode=0, stdout="Name  Id  Version  Available  Source\nExample App  Example.App  1.0  1.1  winget", stderr=""),
                        ]
                        result = check_application_updates(windows=True)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["network_access_requested"])
        self.assertFalse(result["software_changes_made"])
        self.assertEqual(result["updates_installed"], 0)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0][1:4], ["source", "export", "winget"])
        listing_command = run.call_args_list[1].args[0]
        self.assertIn("list", listing_command)
        self.assertIn("--upgrade-available", listing_command)
        self.assertIn("--source", listing_command)
        self.assertNotIn("install", listing_command)
        self.assertNotIn("upgrade", listing_command)
        self.assertNotIn("--all", listing_command)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_update_check_refuses_untrusted_or_custom_source(self):
        import json
        from bot.pc_care import check_application_updates
        with tempfile.TemporaryDirectory() as tmp:
            alias = Path(tmp) / "Microsoft" / "WindowsApps" / "winget.exe"
            source_json = json.dumps({
                "Name": "winget",
                "Arg": "https://example.invalid/cache",
                "Type": "Microsoft.PreIndexed.Package",
                "TrustLevel": ["Trusted"],
            })
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}, clear=False):
                with patch("bot.pc_care.shutil.which", return_value=str(alias)):
                    with patch("bot.pc_care.subprocess.run") as run:
                        run.return_value = Mock(returncode=0, stdout=source_json, stderr="")
                        result = check_application_updates(windows=True)
        self.assertEqual(result["status"], "source_untrusted")
        self.assertEqual(run.call_count, 1)
        self.assertFalse(result["network_access_requested"])

    def test_update_check_timeout_is_bounded_and_never_installs(self):
        import json
        import subprocess
        from bot.pc_care import check_application_updates
        with tempfile.TemporaryDirectory() as tmp:
            alias = Path(tmp) / "Microsoft" / "WindowsApps" / "winget.exe"
            source_json = json.dumps({
                "Name": "winget",
                "Arg": "https://cdn.winget.microsoft.com/cache",
                "Type": "Microsoft.PreIndexed.Package",
                "TrustLevel": ["Trusted"],
            })
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}, clear=False):
                with patch("bot.pc_care.shutil.which", return_value=str(alias)):
                    with patch("bot.pc_care.subprocess.run") as run:
                        run.side_effect = [
                            Mock(returncode=0, stdout=source_json, stderr=""),
                            subprocess.TimeoutExpired(cmd="winget", timeout=30),
                        ]
                        result = check_application_updates(windows=True)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["network_access_requested"])
        self.assertEqual(result["updates_installed"], 0)

if __name__ == "__main__":
    unittest.main()
