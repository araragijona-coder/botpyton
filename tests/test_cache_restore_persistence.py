"""Regression tests for stopping cache restore after manifest persistence errors."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import bot.application_cache_quarantine as cache_quarantine
from bot.application_cache_quarantine import (
    CACHE_QUARANTINE_CONFIRMATION,
    CACHE_RESTORE_CONFIRMATION,
    quarantine_application_cache_candidates,
    restore_application_cache_quarantine,
)


class CacheRestorePersistenceTests(unittest.TestCase):
    @staticmethod
    def _old_file(path: Path, now: float, payload: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        old = now - (5 * 86400)
        os.utime(path, (old, old))
        return path

    def test_manifest_write_failure_stops_later_restore_moves(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "LocalAppData"
            files = [
                self._old_file(
                    local / "Google" / "Chrome" / "User Data" / "Default"
                    / "Cache" / "Cache_Data" / "first.bin",
                    now,
                    b"first-cache",
                ),
                self._old_file(
                    local / "Google" / "Chrome" / "User Data" / "Default"
                    / "GPUCache" / "second.bin",
                    now,
                    b"second-cache",
                ),
            ]
            env = {"LOCALAPPDATA": str(local)}
            quarantined_result = quarantine_application_cache_candidates(
                [str(path.resolve()) for path in files],
                confirmation=CACHE_QUARANTINE_CONFIRMATION,
                env=env,
                windows=True,
            )
            self.assertEqual(
                quarantined_result["status"],
                "cache_quarantined",
                quarantined_result.get("failure_detail") or quarantined_result.get("note"),
            )
            manifest_path = Path(quarantined_result["manifest_path"])
            original_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(original_manifest["items"]), 2)
            durable_write = cache_quarantine._atomic_write_manifest
            writes = 0

            def fail_once(path, manifest):
                nonlocal writes
                writes += 1
                if writes == 1:
                    raise OSError("simulated manifest storage failure")
                return durable_write(path, manifest)

            with mock.patch(
                "bot.application_cache_quarantine._atomic_write_manifest",
                side_effect=fail_once,
            ):
                restored = restore_application_cache_quarantine(
                    manifest_path,
                    confirmation=CACHE_RESTORE_CONFIRMATION,
                    env=env,
                    windows=True,
                )

            self.assertEqual(restored["status"], "cache_restore_partial", restored)
            self.assertEqual(restored["files_restored"], 1)
            self.assertEqual(restored["files_skipped"], 1)
            self.assertIn("persistence failed", restored["note"].lower())
            self.assertEqual(writes, 2, "retry the manifest once only after stopping the move loop")

            durable_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(durable_manifest["status"], "partial")
            self.assertEqual(
                sorted(item["state"] for item in durable_manifest["items"]),
                ["moved", "restored"],
            )
            self.assertEqual(sum(path.exists() for path in files), 1)
            quarantine_paths = [
                Path(item["quarantine_path"]) for item in durable_manifest["items"]
            ]
            self.assertEqual(sum(path.is_file() for path in quarantine_paths), 1)


if __name__ == "__main__":
    unittest.main()
