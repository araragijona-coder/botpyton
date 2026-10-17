"""Tests for the bounded, read-only per-application cache preview."""
import os
import tempfile
import unittest
from pathlib import Path

from bot.application_cache import preview_application_cache


class ApplicationCachePreviewTests(unittest.TestCase):
    def _old_file(self, path: Path, now: float) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"cache-data")
        os.utime(path, (now - 5 * 86400, now - 5 * 86400))
        return path

    def test_non_windows_is_explicitly_unsupported(self):
        result = preview_application_cache(windows=False, env={})
        self.assertEqual(result["status"], "unsupported_platform")
        self.assertEqual(result["candidate_count"], 0)
        self.assertTrue(result["read_only"])
        self.assertFalse(result["network_used"])

    def test_scans_allowlisted_browser_cache_only_and_preserves_personal_data(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "Local"
            profile = local / "Google" / "Chrome" / "User Data" / "Default"
            old = self._old_file(profile / "Cache" / "Cache_Data" / "old.bin", now)
            recent = profile / "GPUCache" / "recent.bin"
            recent.parent.mkdir(parents=True)
            recent.write_bytes(b"new")
            os.utime(recent, (now, now))
            self._old_file(profile / "Code Cache" / "js" / "old.js", now)
            self._old_file(profile / "Cookies", now)
            self._old_file(profile / "History", now)
            self._old_file(profile / "Login Data", now)
            self._old_file(local / "ArbitraryApp" / "Cache" / "private.bin", now)
            result = preview_application_cache(env={"LOCALAPPDATA": str(local)}, windows=True, now=now)
            self.assertEqual(result["status"], "preview_complete")
            self.assertEqual(result["candidate_count"], 2)
            paths = {item["path"] for item in result["candidates"]}
            self.assertIn(str(old.resolve()), paths)
            self.assertFalse(any(path.endswith(("Cookies", "History", "Login Data", "private.bin", "recent.bin")) for path in paths))
            self.assertEqual(result["files_deleted"], 0)
            self.assertEqual(result["files_moved"], 0)
            self.assertFalse(result["settings_changed"])
            self.assertTrue(old.exists())

    def test_recognizes_edge_brave_and_firefox_profile_cache_locations(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "Local"
            paths = [
                local / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache" / "Cache_Data" / "edge.bin",
                local / "BraveSoftware" / "Brave-Browser" / "User Data" / "Profile 1" / "GPUCache" / "brave.bin",
                local / "Mozilla" / "Firefox" / "Profiles" / "abcd1234.default-release" / "cache2" / "firefox.bin",
            ]
            for path in paths:
                self._old_file(path, now)
            result = preview_application_cache(env={"LOCALAPPDATA": str(local)}, windows=True, now=now)
            self.assertEqual(result["status"], "preview_complete")
            self.assertEqual(result["candidate_count"], 3)
            self.assertEqual({item["browser"] for item in result["candidates"]},
                             {"Microsoft Edge", "Brave", "Mozilla Firefox"})

    def test_arbitrary_profile_names_and_guest_profiles_are_not_scanned(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "Local"
            root = local / "Google" / "Chrome" / "User Data"
            self._old_file(root / "Guest Profile" / "Cache" / "Cache_Data" / "guest.bin", now)
            self._old_file(root / "Profile arbitrary" / "Cache" / "Cache_Data" / "arbitrary.bin", now)
            result = preview_application_cache(env={"LOCALAPPDATA": str(local)}, windows=True, now=now)
            self.assertEqual(result["candidate_count"], 0)

    def test_entry_limit_is_partial_not_success(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "Local"
            root = local / "Google" / "Chrome" / "User Data" / "Default" / "GPUCache"
            for index in range(10):
                self._old_file(root / f"{index}.bin", now)
            result = preview_application_cache(env={"LOCALAPPDATA": str(local)}, windows=True, now=now, max_entries=2)
            self.assertEqual(result["status"], "preview_partial")
            self.assertTrue(result["scan_limited"])
            self.assertLessEqual(result["entries_examined"], 2)

    def test_candidate_limit_is_enforced_and_partial(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "Local"
            root = local / "Google" / "Chrome" / "User Data" / "Default" / "GPUCache"
            for index in range(5):
                self._old_file(root / f"{index}.bin", now)
            result = preview_application_cache(env={"LOCALAPPDATA": str(local)}, windows=True, now=now, max_candidates=2)
            self.assertEqual(result["candidate_count"], 2)
            self.assertEqual(result["status"], "preview_partial")

    def test_invalid_limits_are_rejected(self):
        with self.assertRaises(ValueError):
            preview_application_cache(windows=True, env={}, max_entries=0)
        with self.assertRaises(ValueError):
            preview_application_cache(windows=True, env={}, max_candidates=301)
        with self.assertRaises(ValueError):
            preview_application_cache(windows=True, env={}, minimum_age_days=0)


if __name__ == "__main__":
    unittest.main()
