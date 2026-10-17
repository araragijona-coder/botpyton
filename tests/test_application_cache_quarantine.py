"""Integration tests for the separate reversible browser-cache quarantine."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from bot.application_cache import preview_application_cache
from bot.application_cache_quarantine import (
    CACHE_QUARANTINE_CONFIRMATION,
    CACHE_RESTORE_CONFIRMATION,
    preview_application_cache_restore,
    quarantine_application_cache_candidates,
    restore_application_cache_quarantine,
)


class ApplicationCacheQuarantineTests(unittest.TestCase):
    def _old_file(self, path: Path, now: float, payload: bytes = b"cache-payload") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        os.utime(path, (now - 5 * 86400, now - 5 * 86400))
        return path

    def _fixture(self, tmp: str, now: float):
        local = Path(tmp) / "LocalAppData"
        profile = local / "Google" / "Chrome" / "User Data" / "Default"
        chosen = self._old_file(profile / "Cache" / "Cache_Data" / "chosen.bin", now, b"selected cache")
        untouched = self._old_file(profile / "GPUCache" / "untouched.bin", now, b"keep me")
        env = {"LOCALAPPDATA": str(local)}
        return local, chosen, untouched, env

    def test_quarantine_only_selected_file_then_restore_with_integrity_check(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            _local, chosen, untouched, env = self._fixture(tmp, now)
            initial = preview_application_cache(env=env, windows=True, now=now)
            self.assertEqual(initial["status"], "preview_complete")
            result = quarantine_application_cache_candidates(
                [str(chosen.resolve())], confirmation=CACHE_QUARANTINE_CONFIRMATION,
                env=env, windows=True,
            )
            self.assertEqual(result["status"], "cache_quarantined", result.get("failure_detail") or result.get("note"))
            self.assertEqual(result["moved_count"], 1)
            self.assertFalse(chosen.exists())
            self.assertTrue(untouched.exists())
            manifest_path = Path(result["manifest_path"])
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            quarantined = Path(manifest["items"][0]["quarantine_path"])
            self.assertTrue(quarantined.is_file())
            self.assertEqual(quarantined.read_bytes(), b"selected cache")
            self.assertEqual(manifest["items"][0]["sha256"], __import__("hashlib").sha256(b"selected cache").hexdigest())

            preview = preview_application_cache_restore(manifest_path, env=env, windows=True)
            self.assertEqual(preview["status"], "restore_preview_complete")
            self.assertEqual(preview["ready_count"], 1)
            restored = restore_application_cache_quarantine(
                manifest_path, confirmation=CACHE_RESTORE_CONFIRMATION, env=env, windows=True
            )
            self.assertEqual(restored["status"], "cache_restored")
            self.assertEqual(restored["files_restored"], 1)
            self.assertEqual(chosen.read_bytes(), b"selected cache")
            self.assertTrue(untouched.exists())
            self.assertFalse(quarantined.exists())

    def test_wrong_confirmation_never_moves_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            _local, chosen, _untouched, env = self._fixture(tmp, 1_700_000_000.0)
            result = quarantine_application_cache_candidates(
                [str(chosen.resolve())], confirmation="yes", env=env, windows=True,
            )
            self.assertEqual(result["status"], "confirmation_mismatch")
            self.assertTrue(chosen.exists())
            self.assertFalse((Path(env["LOCALAPPDATA"]) / "BOTPYTON" / "BrowserCacheQuarantine").exists())

    def test_quarantine_never_overwrites_a_destination_created_during_move(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            _local, chosen, _untouched, env = self._fixture(tmp, 1_700_000_000.0)
            real_link = os.link
            sentinel = b"pre-existing quarantine data"

            def create_collision_then_link(source, destination, *args, **kwargs):
                Path(destination).write_bytes(sentinel)
                return real_link(source, destination, *args, **kwargs)

            with mock.patch("bot.application_cache_quarantine.os.link", side_effect=create_collision_then_link):
                result = quarantine_application_cache_candidates(
                    [str(chosen.resolve())], confirmation=CACHE_QUARANTINE_CONFIRMATION,
                    env=env, windows=True,
                )

            self.assertNotEqual(result["status"], "cache_quarantined")
            self.assertTrue(chosen.exists(), "original cache must remain when exclusive quarantine fails")
            self.assertEqual(chosen.read_bytes(), b"selected cache")
            manifest_path = Path(result["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            destination = Path(manifest["items"][0]["quarantine_path"])
            self.assertEqual(destination.read_bytes(), sentinel, "pre-existing destination must never be overwritten")

    def test_paths_outside_the_fresh_allowlisted_preview_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _local, _chosen, _untouched, env = self._fixture(tmp, 1_700_000_000.0)
            arbitrary = Path(tmp) / "important-document.txt"
            arbitrary.write_text("do not move", encoding="utf-8")
            result = quarantine_application_cache_candidates(
                [str(arbitrary.resolve())], confirmation=CACHE_QUARANTINE_CONFIRMATION,
                env=env, windows=True,
            )
            self.assertEqual(result["status"], "cache_quarantine_blocked")
            self.assertTrue(arbitrary.exists())
            self.assertEqual(arbitrary.read_text(encoding="utf-8"), "do not move")

    def test_changed_selected_file_is_rejected_without_moving_any_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            _local, chosen, _untouched, env = self._fixture(tmp, 1_700_000_000.0)
            # The preview's age window is evaluated with the real clock; make this old file eligible.
            initial = preview_application_cache(env=env, windows=True, now=1_700_000_000.0)
            self.assertGreater(initial["candidate_count"], 0)
            selected = str(chosen.resolve())
            chosen.write_bytes(b"changed after preview")
            os.utime(chosen, None)
            result = quarantine_application_cache_candidates(
                [selected], confirmation=CACHE_QUARANTINE_CONFIRMATION,
                env=env, windows=True,
            )
            self.assertEqual(result["status"], "cache_quarantine_blocked")
            self.assertTrue(chosen.exists())

    def test_restore_preview_never_overwrites_a_recreated_cache_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            _local, chosen, _untouched, env = self._fixture(tmp, 1_700_000_000.0)
            result = quarantine_application_cache_candidates(
                [str(chosen.resolve())], confirmation=CACHE_QUARANTINE_CONFIRMATION,
                env=env, windows=True,
            )
            self.assertEqual(result["status"], "cache_quarantined", result.get("failure_detail") or result.get("note"))
            chosen.write_bytes(b"new browser cache")
            preview = preview_application_cache_restore(result["manifest_path"], env=env, windows=True)
            self.assertEqual(preview["status"], "restore_preview_blocked")
            self.assertEqual(preview["ready_count"], 0)
            entry = next(item for item in preview["items"] if item["path"] == str(chosen.resolve()))
            self.assertEqual(entry["status"], "destination_exists_no_overwrite")
            restored = restore_application_cache_quarantine(
                result["manifest_path"], confirmation=CACHE_RESTORE_CONFIRMATION,
                env=env, windows=True,
            )
            self.assertEqual(restored["status"], "cache_restore_blocked")
            self.assertEqual(chosen.read_bytes(), b"new browser cache")

    def test_manifest_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _local, chosen, _untouched, env = self._fixture(tmp, 1_700_000_000.0)
            result = quarantine_application_cache_candidates(
                [str(chosen.resolve())], confirmation=CACHE_QUARANTINE_CONFIRMATION,
                env=env, windows=True,
            )
            self.assertEqual(result["status"], "cache_quarantined", result.get("failure_detail") or result.get("note"))
            manifest_path = Path(result["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["items"][0]["original_path"] = str(Path(tmp) / "not-a-cache-file")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            preview = preview_application_cache_restore(manifest_path, env=env, windows=True)
            self.assertEqual(preview["status"], "cache_manifest_invalid")
            self.assertTrue(Path(manifest["items"][0]["quarantine_path"]).exists())

    def test_removed_profile_does_not_block_restoring_other_session_items(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "LocalAppData"
            chrome_profile = local / "Google" / "Chrome" / "User Data" / "Default"
            edge_profile = local / "Microsoft" / "Edge" / "User Data" / "Default"
            chrome_file = self._old_file(chrome_profile / "Cache" / "Cache_Data" / "chrome.bin", now, b"chrome-cache")
            edge_file = self._old_file(edge_profile / "Cache" / "Cache_Data" / "edge.bin", now, b"edge-cache")
            env = {"LOCALAPPDATA": str(local)}
            preview = preview_application_cache(env=env, windows=True, now=now)
            self.assertEqual(preview["status"], "preview_complete")
            selected = [str(chrome_file.resolve()), str(edge_file.resolve())]
            result = quarantine_application_cache_candidates(
                selected, confirmation=CACHE_QUARANTINE_CONFIRMATION, env=env, windows=True,
            )
            self.assertEqual(result["status"], "cache_quarantined", result.get("failure_detail") or result.get("note"))
            self.assertEqual(result["moved_count"], 2)

            # Simulate Edge's profile being removed after quarantine. Its cache file
            # remains safely stored, but its original destination is no longer allowlisted.
            import shutil
            shutil.rmtree(edge_profile.parent)
            restore_preview = preview_application_cache_restore(result["manifest_path"], env=env, windows=True)
            self.assertEqual(restore_preview["status"], "restore_preview_partial")
            self.assertEqual(restore_preview["ready_count"], 1)
            statuses = {item["path"]: item["status"] for item in restore_preview["items"]}
            self.assertEqual(statuses[str(chrome_file.resolve())], "ready_to_restore")
            self.assertEqual(statuses[str(edge_file.resolve())], "destination_not_allowlisted")

            restored = restore_application_cache_quarantine(
                result["manifest_path"], confirmation=CACHE_RESTORE_CONFIRMATION, env=env, windows=True,
            )
            self.assertEqual(restored["status"], "cache_restore_partial")
            self.assertEqual(restored["files_restored"], 1)
            self.assertEqual(chrome_file.read_bytes(), b"chrome-cache")
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            edge_quarantined = Path(manifest["items"][1]["quarantine_path"])
            self.assertTrue(edge_quarantined.is_file())
            self.assertEqual(edge_quarantined.read_bytes(), b"edge-cache")

    def test_non_windows_restore_is_explicitly_unsupported(self):
        result = preview_application_cache_restore("/not-used/manifest.json", env={}, windows=False)
        self.assertEqual(result["status"], "unsupported_platform")
        self.assertEqual(result["ready_count"], 0)


if __name__ == "__main__":
    unittest.main()
