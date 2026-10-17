"""Safety tests for Discord/Slack read-only cache catalog."""
import os
import tempfile
import unittest
from pathlib import Path

from bot.desktop_app_cache import preview_desktop_app_cache


class DesktopAppCacheTests(unittest.TestCase):
    def old_file(self, path: Path, now: float) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture-cache")
        os.utime(path, (now - 5 * 86400, now - 5 * 86400))
        return path

    def test_non_windows_is_unsupported_and_read_only(self):
        result = preview_desktop_app_cache(windows=False, env={})
        self.assertEqual(result["status"], "unsupported_platform")
        self.assertTrue(result["read_only"])
        self.assertEqual(result["files_moved"], 0)
        self.assertFalse(result["network_used"])

    def test_only_allowlisted_cache_areas_are_reported(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp) / "Roaming"
            old = self.old_file(appdata / "discord" / "Cache" / "old.bin", now)
            self.old_file(appdata / "discord" / "Code Cache" / "js" / "old.js", now)
            self.old_file(appdata / "discord" / "Local Storage" / "leveldb" / "private.ldb", now)
            self.old_file(appdata / "discord" / "Cookies", now)
            self.old_file(appdata / "UnlistedApp" / "Cache" / "private.bin", now)
            self.old_file(appdata / "Slack" / "GPUCache" / "slack.bin", now)
            result = preview_desktop_app_cache(env={"APPDATA": str(appdata)}, windows=True, now=now)
            self.assertEqual(result["status"], "preview_complete")
            self.assertEqual(result["candidate_count"], 3)
            paths = {item["path"] for item in result["candidates"]}
            self.assertIn(str(old.resolve()), paths)
            self.assertFalse(any(path.endswith(("private.ldb", "Cookies", "private.bin")) for path in paths))
            self.assertEqual(result["files_deleted"], 0)
            self.assertEqual(result["files_moved"], 0)
            self.assertTrue(old.exists())

    def test_profile_base_with_symlinked_ancestor_is_rejected(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "real"
            appdata_real = root / "Roaming"
            old = self.old_file(appdata_real / "discord" / "Cache" / "old.bin", now)
            redirected = Path(tmp) / "redirected"
            try:
                redirected.symlink_to(root, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlink unavailable in this environment: {exc}")

            result = preview_desktop_app_cache(
                env={"APPDATA": str(redirected / "Roaming")},
                windows=True, now=now,
            )
            self.assertEqual(result["status"], "preview_partial")
            self.assertGreaterEqual(result["unsafe_paths_skipped"], 1)
            self.assertEqual(result["candidate_count"], 0)
            self.assertTrue(old.exists())

    def test_recent_files_are_not_candidates(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp) / "Roaming"
            path = appdata / "Slack" / "Cache" / "recent.bin"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"recent")
            os.utime(path, (now, now))
            result = preview_desktop_app_cache(env={"APPDATA": str(appdata)}, windows=True, now=now)
            self.assertEqual(result["candidate_count"], 0)
            self.assertEqual(result["status"], "preview_complete")

    def test_entry_limit_returns_partial(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp) / "Roaming"
            for i in range(6):
                self.old_file(appdata / "discord" / "GPUCache" / f"{i}.bin", now)
            result = preview_desktop_app_cache(env={"APPDATA": str(appdata)}, windows=True, now=now, max_entries=2)
            self.assertEqual(result["status"], "preview_partial")
            self.assertTrue(result["scan_limited"])
            self.assertLessEqual(result["entries_examined"], 2)

    def test_limits_are_validated(self):
        with self.assertRaises(ValueError):
            preview_desktop_app_cache(windows=True, env={}, max_entries=0)
        with self.assertRaises(ValueError):
            preview_desktop_app_cache(windows=True, env={}, max_candidates=151)
        with self.assertRaises(ValueError):
            preview_desktop_app_cache(windows=True, env={}, minimum_age_days=0)


if __name__ == "__main__":
    unittest.main()
