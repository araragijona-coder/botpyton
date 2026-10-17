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
                    quarantine_root=quarantine_root, now=now, audit_dir=Path(tmp) / "audit",
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
                quarantine_root=quarantine_root, now=now, audit_dir=Path(tmp) / "audit",
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["files_quarantined"], 1)
            self.assertFalse(old_file.exists())
            manifest_path = Path(result["manifest_path"])
            self.assertTrue(manifest_path.is_file())
            self.assertTrue((manifest_path.parent / "0001-old.tmp").is_file())
            restored = restore_quarantined_files(
                manifest_path, confirmation=RESTORE_CONFIRMATION,
                scan_roots=[("temporary files", temp_root)], audit_dir=Path(tmp) / "audit",
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
                    quarantine_root=Path(tmp) / "Quarantine", now=now, audit_dir=Path(tmp) / "audit",
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
                quarantine_root=Path(tmp) / "Quarantine", now=now, audit_dir=Path(tmp) / "audit",
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
                quarantine_root=quarantine_root, now=now, audit_dir=Path(tmp) / "audit",
            )
            self.assertEqual(moved["files_quarantined"], 1)
            old_file.write_bytes(b"user's new file")
            restored = restore_quarantined_files(
                moved["manifest_path"], confirmation=RESTORE_CONFIRMATION,
                scan_roots=[("temporary files", root)], audit_dir=Path(tmp) / "audit",
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

    def test_quarantine_manifest_contains_sha256_and_audit_is_verified(self):
        import hashlib
        import json
        from bot.pc_care import (
            QUARANTINE_CONFIRMATION, _persist_pc_care_audit_event, read_pc_care_audit,
            quarantine_cleanup_candidates,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "temporary"
            root.mkdir()
            quarantine_root = Path(tmp) / "Quarantine"
            audit_dir = Path(tmp) / "audit"
            old_file = root / "old.tmp"
            content = b"recoverable payload"
            old_file.write_bytes(content)
            now = 1_700_000_000.0
            os.utime(old_file, (now - 12 * 86400, now - 12 * 86400))
            preview = get_cleanup_preview(scan_roots=[("temporary files", root)], now=now)
            result = quarantine_cleanup_candidates(
                preview, confirmation=QUARANTINE_CONFIRMATION,
                scan_roots=[("temporary files", root)], quarantine_root=quarantine_root,
                now=now, audit_dir=audit_dir,
            )
            self.assertEqual(result["status"], "completed")
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["items"][0]["sha256"], hashlib.sha256(content).hexdigest())
            event = _persist_pc_care_audit_event(
                "restore", "started", "test-only", item_count=1, audit_dir=audit_dir,
            )
            self.assertEqual(event["integrity"], "written")
            audit = read_pc_care_audit(audit_dir=audit_dir)
            self.assertEqual(audit["integrity_status"], "verified")
            self.assertGreaterEqual(audit["records_returned"], 3)
            self.assertTrue(all("path" not in item for item in audit["items"]))

    def test_restore_rejects_tampered_quarantine_content_by_sha256(self):
        from bot.pc_care import (
            QUARANTINE_CONFIRMATION, RESTORE_CONFIRMATION,
            quarantine_cleanup_candidates, restore_quarantined_files,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "temporary"
            root.mkdir()
            quarantine_root = Path(tmp) / "Quarantine"
            audit_dir = Path(tmp) / "audit"
            original = root / "tamper.tmp"
            original.write_bytes(b"original")
            now = 1_700_000_000.0
            os.utime(original, (now - 12 * 86400, now - 12 * 86400))
            preview = get_cleanup_preview(scan_roots=[("temporary files", root)], now=now)
            moved = quarantine_cleanup_candidates(
                preview, confirmation=QUARANTINE_CONFIRMATION,
                scan_roots=[("temporary files", root)], quarantine_root=quarantine_root,
                now=now, audit_dir=audit_dir,
            )
            self.assertEqual(moved["files_quarantined"], 1)
            stored = Path(moved["manifest_path"]).parent / "0001-tamper.tmp"
            stored.write_bytes(b"tampered")
            result = restore_quarantined_files(
                moved["manifest_path"], confirmation=RESTORE_CONFIRMATION,
                scan_roots=[("temporary files", root)], audit_dir=audit_dir,
            )
            self.assertEqual(result["files_restored"], 0)
            self.assertEqual(result["files_skipped"], 1)
            self.assertIn(result["items"][0]["status"], {"skipped_integrity_mismatch", "skipped_quarantine_file_changed"})
            self.assertFalse(original.exists())
            self.assertTrue(stored.exists())

    def test_restore_preview_is_read_only_and_detects_destination_conflict(self):
        from bot.pc_care import QUARANTINE_CONFIRMATION, quarantine_cleanup_candidates, preview_quarantined_restore
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "temporary"
            root.mkdir()
            quarantine_root = Path(tmp) / "Quarantine"
            old_file = root / "conflict.tmp"
            old_file.write_bytes(b"original")
            now = 1_700_000_000.0
            os.utime(old_file, (now - 12 * 86400, now - 12 * 86400))
            preview = get_cleanup_preview(scan_roots=[("temporary files", root)], now=now)
            moved = quarantine_cleanup_candidates(
                preview, confirmation=QUARANTINE_CONFIRMATION,
                scan_roots=[("temporary files", root)], quarantine_root=quarantine_root,
                now=now, audit_dir=Path(tmp) / "audit",
            )
            stored = Path(moved["manifest_path"]).parent / "0001-conflict.tmp"
            old_file.write_bytes(b"new file created after quarantine")
            before = old_file.read_bytes()
            dry_run = preview_quarantined_restore(
                moved["manifest_path"], scan_roots=[("temporary files", root)],
            )
            self.assertTrue(dry_run["read_only"])
            self.assertEqual(dry_run["ready_count"], 0)
            self.assertEqual(dry_run["files_restored"], 0)
            self.assertEqual(dry_run["items"][0]["status"], "skipped_destination_exists_or_unsafe")
            self.assertEqual(old_file.read_bytes(), before)
            self.assertTrue(stored.exists())

    def test_audit_detects_modified_event_checksum(self):
        import json
        from bot.pc_care import _persist_pc_care_audit_event, read_pc_care_audit
        with tempfile.TemporaryDirectory() as tmp:
            audit_dir = Path(tmp) / "audit"
            _persist_pc_care_audit_event("quarantine", "started", "in_progress", audit_dir=audit_dir)
            event_path = next(audit_dir.glob("event-*.json"))
            data = json.loads(event_path.read_text(encoding="utf-8"))
            data["status"] = "altered"
            event_path.write_text(json.dumps(data), encoding="utf-8")
            result = read_pc_care_audit(audit_dir=audit_dir)
            self.assertEqual(result["status"], "integrity_error")
            self.assertEqual(result["integrity_status"], "error")
            self.assertEqual(result["items"][0]["integrity"], "failed")


    def test_no_replace_move_preserves_an_existing_destination(self):
        from bot.pc_care import _move_no_replace
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.tmp"
            destination = Path(tmp) / "destination.tmp"
            source.write_bytes(b"source content")
            destination.write_bytes(b"keep existing content")
            with self.assertRaises(FileExistsError):
                _move_no_replace(source, destination)
            self.assertEqual(source.read_bytes(), b"source content")
            self.assertEqual(destination.read_bytes(), b"keep existing content")


    def test_restore_preview_rejects_manifest_checksum_tampering(self):
        import json
        from bot.pc_care import QUARANTINE_CONFIRMATION, quarantine_cleanup_candidates, preview_quarantined_restore
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "temporary"
            root.mkdir()
            original = root / "manifest-tamper.tmp"
            original.write_bytes(b"manifest payload")
            now = 1_700_000_000.0
            os.utime(original, (now - 12 * 86400, now - 12 * 86400))
            preview = get_cleanup_preview(scan_roots=[("temporary files", root)], now=now)
            moved = quarantine_cleanup_candidates(
                preview, confirmation=QUARANTINE_CONFIRMATION,
                scan_roots=[("temporary files", root)],
                quarantine_root=Path(tmp) / "Quarantine", now=now,
                audit_dir=Path(tmp) / "audit",
            )
            manifest_path = Path(moved["manifest_path"])
            stored = manifest_path.parent / "0001-manifest-tamper.tmp"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["items"][0]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest integrity checksum"):
                preview_quarantined_restore(
                    manifest_path, scan_roots=[("temporary files", root)],
                )
            self.assertTrue(stored.exists())
            self.assertFalse(original.exists())


    def test_pc_resource_benchmark_is_read_only_bounded_and_reports_timings(self):
        from bot.pc_care import get_pc_care_benchmark
        result = get_pc_care_benchmark(windows=False, scan_roots=[])
        self.assertIn(result["status"], {"measured", "partial_measurement"})
        self.assertTrue(result["read_only"])
        self.assertFalse(result["network_used"])
        self.assertFalse(result["ai_enabled"])
        self.assertEqual(result["files_deleted"], 0)
        self.assertFalse(result["settings_changed"])
        self.assertEqual(len(result["measurements"]["operations"]), 5)
        self.assertGreaterEqual(result["measurements"]["total_elapsed_wall_ms"], 0)
        self.assertGreaterEqual(result["measurements"]["total_elapsed_cpu_ms"], 0)
        for operation in result["measurements"]["operations"]:
            self.assertGreaterEqual(operation["elapsed_wall_ms"], 0)
            self.assertGreaterEqual(operation["item_count"], 0)


    def test_storage_analyzer_reports_bytes_extensions_and_largest_files(self):
        from bot.pc_care import analyze_storage_path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "subfolder"
            sub.mkdir()
            (root / "small.txt").write_bytes(b"x" * 5)
            (sub / "large.bin").write_bytes(b"b" * 120)
            (sub / "medium.txt").write_bytes(b"t" * 40)
            result = analyze_storage_path(root, max_entries=100, max_depth=4, top_n=2, timeout_seconds=5)
            self.assertEqual(result["status"], "scan_complete", result)
            self.assertTrue(result["read_only"])
            self.assertFalse(result["file_contents_read"])
            self.assertFalse(result["network_used"])
            self.assertEqual(result["files_deleted"], 0)
            self.assertEqual(result["files_examined"], 3)
            self.assertEqual(result["file_bytes_examined"], 165)
            self.assertEqual([Path(item["path"]).name for item in result["largest_files"]], ["large.bin", "medium.txt"])
            extensions = {item["extension"]: item["bytes"] for item in result["extensions"]}
            self.assertEqual(extensions[".txt"], 45)
            self.assertEqual(extensions[".bin"], 120)
            self.assertTrue((root / "small.txt").exists())

    def test_storage_analyzer_marks_entry_limit_as_partial(self):
        from bot.pc_care import analyze_storage_path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(6):
                (root / f"{index}.dat").write_bytes(b"payload")
            result = analyze_storage_path(root, max_entries=2, max_depth=1, timeout_seconds=5)
            self.assertEqual(result["status"], "scan_partial")
            self.assertTrue(result["scan_limited"])
            self.assertLessEqual(result["entries_examined"], 2)
            self.assertTrue(all(Path(item["path"]).is_file() for item in result["largest_files"]))

    def test_storage_analyzer_respects_recursion_depth_and_result_caps(self):
        from bot.pc_care import analyze_storage_path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root
            for index in range(4):
                current = current / f"d{index}"
                current.mkdir()
                (current / f"{index}.dat").write_bytes(b"x" * (index + 1))
            result = analyze_storage_path(root, max_entries=100, max_depth=1, top_n=1)
            self.assertEqual(result["status"], "scan_partial")
            self.assertTrue(result["scan_limited"])
            self.assertLessEqual(len(result["largest_files"]), 1)

    def test_storage_analyzer_validates_limits_and_refuses_file_roots(self):
        from bot.pc_care import analyze_storage_path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "one.txt"
            file_path.write_text("one", encoding="utf-8")
            with self.assertRaises(ValueError):
                analyze_storage_path(root, max_entries=0)
            with self.assertRaises(ValueError):
                analyze_storage_path(root, timeout_seconds=100)
            result = analyze_storage_path(file_path)
            self.assertEqual(result["status"], "not_a_directory")
            self.assertEqual(result["files_examined"], 0)

    def test_storage_analyzer_refuses_symlink_root_when_supported(self):
        from bot.pc_care import analyze_storage_path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_root = root / "real"
            real_root.mkdir()
            link_path = root / "link"
            try:
                link_path.symlink_to(real_root, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is not permitted in this test environment")
            result = analyze_storage_path(link_path)
            self.assertEqual(result["status"], "unsafe_root")


    def test_duplicate_finder_detects_identical_files_not_merely_same_size(self):
        from bot.pc_care import find_duplicate_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "copy-a.bin"
            second = root / "copy-b.bin"
            different = root / "different.bin"
            first.write_bytes(b"identical payload")
            second.write_bytes(b"identical payload")
            different.write_bytes(b"unrelated content")
            result = find_duplicate_files(root, max_entries=100, max_depth=4, timeout_seconds=5)
            self.assertEqual(result["status"], "scan_complete")
            self.assertTrue(result["read_only"])
            self.assertTrue(result["file_contents_read_for_sha256"])
            self.assertFalse(result["network_used"])
            self.assertEqual(result["files_deleted"], 0)
            self.assertEqual(result["files_hashed"], 3)
            self.assertEqual(result["duplicate_group_count"], 1)
            group = result["duplicate_groups"][0]
            self.assertEqual(group["file_count"], 2)
            self.assertEqual(set(group["paths"]), {str(first), str(second)})
            self.assertEqual(group["potential_duplicate_bytes"], len(b"identical payload"))
            self.assertTrue(first.exists() and second.exists() and different.exists())

    def test_duplicate_finder_respects_hash_budget_and_reports_partial(self):
        from bot.pc_care import find_duplicate_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b"larger than test budget"
            (root / "a.bin").write_bytes(content)
            (root / "b.bin").write_bytes(content)
            result = find_duplicate_files(
                root, max_entries=100, max_hash_bytes=5, max_file_bytes=100, timeout_seconds=5
            )
            self.assertEqual(result["status"], "scan_partial")
            self.assertTrue(result["scan_limited"])
            self.assertEqual(result["files_hashed"], 0)
            self.assertGreater(result["hash_budget_skipped_files"], 0)
            self.assertEqual(result["duplicate_group_count"], 0)

    def test_duplicate_finder_skips_oversized_files(self):
        from bot.pc_care import find_duplicate_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b"too big for this pass"
            (root / "a.bin").write_bytes(content)
            (root / "b.bin").write_bytes(content)
            result = find_duplicate_files(
                root, max_entries=100, max_file_bytes=5, max_hash_bytes=100, timeout_seconds=5
            )
            self.assertEqual(result["status"], "scan_partial")
            self.assertEqual(result["files_hashed"], 0)
            self.assertEqual(result["oversize_candidate_files_skipped"], 2)

    def test_duplicate_finder_validates_limits_and_never_follows_symlink_root(self):
        from bot.pc_care import find_duplicate_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual"
            actual.mkdir()
            (actual / "a.txt").write_text("same", encoding="utf-8")
            with self.assertRaises(ValueError):
                find_duplicate_files(actual, max_entries=0)
            with self.assertRaises(ValueError):
                find_duplicate_files(actual, max_groups=100)
            link = root / "link"
            try:
                link.symlink_to(actual, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is not permitted in this test environment")
            result = find_duplicate_files(link)
            self.assertEqual(result["status"], "unsafe_root")


    def test_duplicate_finder_reports_exact_hash_budget_skips_and_deadline(self):
        from bot.pc_care import find_duplicate_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a-small.bin").write_bytes(b"x")
            (root / "b-small.bin").write_bytes(b"x")
            (root / "c-large.bin").write_bytes(b"yy")
            (root / "d-large.bin").write_bytes(b"yy")
            budget = find_duplicate_files(
                root, max_entries=100, max_hash_bytes=1, max_file_bytes=100, timeout_seconds=5
            )
            self.assertEqual(budget["files_hashed"], 1, budget)
            self.assertEqual(budget["hash_budget_skipped_files"], 3, budget)
            with patch("bot.pc_care._sha256_file", side_effect=TimeoutError("deadline")):
                timed = find_duplicate_files(root, max_entries=100, timeout_seconds=5)
            self.assertEqual(timed["status"], "scan_partial")
            self.assertTrue(timed["timeout_hit"])
            self.assertEqual(timed["files_hashed"], 0)

if __name__ == "__main__":
    unittest.main()
