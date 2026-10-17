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
            self.assertEqual(result["status"], "cache_quarantined")
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
            self.assertEqual(result["status"], "cache_quarantined")
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
            self.assertEqual(result["status"], "cache_quarantined")
            manifest_path = Path(result["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["items"][0]["original_path"] = str(Path(tmp) / "not-a-cache-file")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            preview = preview_application_cache_restore(manifest_path, env=env, windows=True)
            self.assertEqual(preview["status"], "cache_manifest_invalid")
            self.assertTrue(Path(manifest["items"][0]["quarantine_path"]).exists())

    def test_non_windows_restore_is_explicitly_unsupported(self):
        result = preview_application_cache_restore("/not-used/manifest.json", env={}, windows=False)
        self.assertEqual(result["status"], "unsupported_platform")
        self.assertEqual(result["ready_count"], 0)


if __name__ == "__main__":
    unittest.main()
