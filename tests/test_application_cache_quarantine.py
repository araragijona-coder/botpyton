"""Integration tests for the separate reversible browser-cache quarantine."""
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from bot.application_cache import preview_application_cache
from bot.application_cache_quarantine import (
    _canonical_digest,
    MAX_CACHE_MANIFEST_BYTES,
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
            sentinel = b"pre-existing quarantine data"

            if os.name == "nt":
                real_move = os.rename

                def create_collision_then_move(source, destination, *args, **kwargs):
                    Path(destination).write_bytes(sentinel)
                    return real_move(source, destination, *args, **kwargs)

                patch_target = "bot.application_cache_quarantine.os.rename"
                collision_action = create_collision_then_move
            else:
                real_link = os.link

                def create_collision_then_link(source, destination, *args, **kwargs):
                    Path(destination).write_bytes(sentinel)
                    return real_link(source, destination, *args, **kwargs)

                patch_target = "bot.application_cache_quarantine.os.link"
                collision_action = create_collision_then_link

            with mock.patch(patch_target, side_effect=collision_action):
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

    def test_windows_restore_move_preserves_destination_created_during_rename(self):
        from unittest import mock

        if os.name != "nt":
            self.skipTest("Requires native Windows os.rename no-overwrite semantics")

        with tempfile.TemporaryDirectory() as tmp:
            _local, chosen, _untouched, env = self._fixture(tmp, 1_700_000_000.0)
            result = quarantine_application_cache_candidates(
                [str(chosen.resolve())], confirmation=CACHE_QUARANTINE_CONFIRMATION,
                env=env, windows=True,
            )
            self.assertEqual(result["status"], "cache_quarantined", result.get("failure_detail") or result.get("note"))
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            quarantined = Path(manifest["items"][0]["quarantine_path"])
            self.assertTrue(quarantined.is_file())

            sentinel = b"browser recreated this path"
            real_rename = os.rename

            def create_collision_then_rename(source, destination, *args, **kwargs):
                Path(destination).write_bytes(sentinel)
                return real_rename(source, destination, *args, **kwargs)

            with mock.patch("bot.application_cache_quarantine.os.rename", side_effect=create_collision_then_rename):
                restored = restore_application_cache_quarantine(
                    result["manifest_path"], confirmation=CACHE_RESTORE_CONFIRMATION,
                    env=env, windows=True,
                )

            self.assertNotEqual(restored["status"], "cache_restored")
            self.assertEqual(chosen.read_bytes(), sentinel, "restore must not overwrite a concurrently recreated cache file")
            self.assertTrue(quarantined.is_file(), "the verified quarantine copy must remain available")

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

    def test_restore_manifest_write_failure_can_be_reconciled_without_moving_again(self):
        for failure_type in (OSError, ValueError):
            with self.subTest(failure_type=failure_type.__name__), tempfile.TemporaryDirectory() as tmp:
                _local, chosen, untouched, env = self._fixture(tmp, 1_700_000_000.0)
                result = quarantine_application_cache_candidates(
                    [str(chosen.resolve())], confirmation=CACHE_QUARANTINE_CONFIRMATION,
                    env=env, windows=True,
                )
                self.assertEqual(result["status"], "cache_quarantined", result.get("note"))
                manifest_path = Path(result["manifest_path"])
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                quarantined = Path(manifest["items"][0]["quarantine_path"])

                # Simulate the file move succeeding while both manifest writes fail.
                with mock.patch(
                    "bot.application_cache_quarantine._atomic_write_manifest",
                    side_effect=failure_type("injected manifest persistence failure"),
                ):
                    interrupted = restore_application_cache_quarantine(
                        manifest_path, confirmation=CACHE_RESTORE_CONFIRMATION,
                        env=env, windows=True,
                    )

                self.assertEqual(interrupted["status"], "cache_restore_partial")
                self.assertEqual(interrupted["files_restored"], 1)
                self.assertEqual(interrupted["files_reconciled"], 0)
                self.assertEqual(chosen.read_bytes(), b"selected cache")
                self.assertTrue(untouched.is_file())
                self.assertFalse(quarantined.exists())

                stale = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertIn(stale["items"][0]["state"], {"moved", "pending"})
                preview = preview_application_cache_restore(manifest_path, env=env, windows=True)
                self.assertEqual(preview["status"], "restore_preview_reconcile_available")
                self.assertEqual(preview["ready_count"], 0)
                self.assertEqual(preview["reconcile_count"], 1)
                self.assertEqual(preview["items"][0]["status"], "reconcile_manifest_state")

                reconciled = restore_application_cache_quarantine(
                    manifest_path, confirmation=CACHE_RESTORE_CONFIRMATION,
                    env=env, windows=True,
                )
                self.assertEqual(reconciled["status"], "cache_restore_reconciled")
                self.assertEqual(reconciled["files_restored"], 0)
                self.assertEqual(reconciled["files_reconciled"], 1)
                self.assertEqual(chosen.read_bytes(), b"selected cache")
                self.assertFalse(quarantined.exists())
                durable = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(durable["items"][0]["state"], "restored")
                self.assertEqual(durable["status"], "complete")

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


    def test_manifest_read_rejects_growth_after_initial_size_check(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            _local, chosen, _untouched, env = self._fixture(tmp, 1_700_000_000.0)
            result = quarantine_application_cache_candidates(
                [str(chosen.resolve())],
                confirmation=CACHE_QUARANTINE_CONFIRMATION,
                env=env,
                windows=True,
            )
            self.assertEqual(result["status"], "cache_quarantined")
            manifest_path = Path(result["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            quarantined = Path(manifest["items"][0]["quarantine_path"])
            self.assertTrue(quarantined.is_file())

            # Simulate another process growing the manifest after the caller's
            # initial metadata check but after its read stream is opened.
            real_open = Path.open
            grew = False

            def grow_after_open(path, mode="r", *args, **kwargs):
                nonlocal grew
                stream = real_open(path, mode, *args, **kwargs)
                if path == manifest_path and mode == "rb" and not grew:
                    grew = True
                    with real_open(path, "ab") as writer:
                        writer.write(b" " * (MAX_CACHE_MANIFEST_BYTES + 1))
                return stream

            with mock.patch.object(Path, "open", new=grow_after_open):
                preview = preview_application_cache_restore(
                    manifest_path, env=env, windows=True
                )

            self.assertTrue(grew, "the test must mutate the file in the read race window")
            self.assertEqual(preview["status"], "cache_manifest_invalid")
            self.assertEqual(preview["ready_count"], 0)
            self.assertTrue(
                quarantined.is_file(),
                "invalid/growing manifests must not move the quarantined file",
            )


    def test_manifest_rejects_excessive_json_nesting_without_restoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            _local, chosen, _untouched, env = self._fixture(tmp, 1_700_000_000.0)
            result = quarantine_application_cache_candidates(
                [str(chosen.resolve())],
                confirmation=CACHE_QUARANTINE_CONFIRMATION,
                env=env,
                windows=True,
            )
            self.assertEqual(result["status"], "cache_quarantined")
            manifest_path = Path(result["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            quarantined = Path(manifest["items"][0]["quarantine_path"])
            nested = "bounded"
            for _ in range(80):
                nested = [nested]
            manifest["test_depth_probe"] = nested
            manifest["manifest_sha256"] = _canonical_digest(manifest)
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            preview = preview_application_cache_restore(
                manifest_path, env=env, windows=True
            )
            self.assertEqual(preview["status"], "cache_manifest_invalid")
            self.assertEqual(preview["ready_count"], 0)
            self.assertTrue(quarantined.is_file())
            self.assertFalse(chosen.exists())


if __name__ == "__main__":
    unittest.main()
