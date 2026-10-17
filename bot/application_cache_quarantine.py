"""Separate, reversible quarantine for explicitly selected browser-cache files."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import time
from pathlib import Path
from typing import Any

from .application_cache import (
    MAX_APPLICATION_CACHE_CANDIDATES,
    _cache_roots_for_profile,
    _is_link_or_reparse,
    _known_profile_roots,
    _profile_matches,
    _safe_directory,
    preview_application_cache,
)

CACHE_QUARANTINE_CONFIRMATION = "QUARANTINE SELECTED BROWSER CACHE"
CACHE_RESTORE_CONFIRMATION = "RESTORE BOTPYTON BROWSER CACHE"
MAX_SELECTED_CACHE_FILES = 25
MAX_CACHE_FILE_BYTES = 32 * 1024 * 1024
MAX_CACHE_BATCH_BYTES = 128 * 1024 * 1024
CACHE_HASH_CHUNK_BYTES = 128 * 1024
MAX_CACHE_MANIFEST_BYTES = 256 * 1024
MAX_CACHE_RESTORE_ITEMS = MAX_SELECTED_CACHE_FILES


def _canonical_digest(value: dict[str, Any]) -> str:
    clean = {key: item for key, item in value.items() if key != "manifest_sha256"}
    encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _local_app_data(env: dict[str, str] | None = None) -> Path:
    environ = os.environ if env is None else env
    raw = environ.get("LOCALAPPDATA")
    if not raw:
        raise ValueError("LOCALAPPDATA is unavailable; browser-cache quarantine is disabled.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("LOCALAPPDATA must be an absolute path.")
    try:
        if not path.is_dir() or _is_link_or_reparse(path):
            raise ValueError("LOCALAPPDATA is missing or is a link/reparse point.")
        return path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("LOCALAPPDATA could not be validated.") from exc


def _quarantine_root(env: dict[str, str] | None = None) -> Path:
    return _local_app_data(env) / "BOTPYTON" / "BrowserCacheQuarantine"


def _path_has_link_from(path: Path, stop: Path) -> bool:
    """Check each existing path component below a known real directory."""
    try:
        path_abs = Path(os.path.abspath(str(path)))
        stop_abs = Path(os.path.abspath(str(stop)))
        if not path_abs.is_relative_to(stop_abs):
            return True
        relative = path_abs.relative_to(stop_abs)
        current = stop_abs
        if _is_link_or_reparse(current):
            return True
        for part in relative.parts:
            current = current / part
            if current.exists() or current.is_symlink():
                if _is_link_or_reparse(current):
                    return True
        return False
    except (OSError, RuntimeError, ValueError):
        return True


def _sha256_checked(path: Path, *, byte_limit: int, expected: os.stat_result | None = None) -> tuple[str, os.stat_result]:
    """Hash bounded bytes and verify that the path refers to the opened regular file."""
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or _is_link_or_reparse(path):
        raise ValueError("Selected cache item is no longer a regular, unlinked file.")
    if before.st_size < 0 or before.st_size > byte_limit:
        raise ValueError("Selected cache item exceeds the approved per-file size limit.")
    if expected is not None and (
        before.st_size != expected.st_size
        or before.st_mtime_ns != expected.st_mtime_ns
        or before.st_dev != expected.st_dev
        or before.st_ino != expected.st_ino
    ):
        raise ValueError("Selected cache item changed after the preview.")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_size != before.st_size
            or opened.st_mtime_ns != before.st_mtime_ns
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise ValueError("Selected cache item changed while it was being opened.")
        total = 0
        while True:
            block = stream.read(CACHE_HASH_CHUNK_BYTES)
            if not block:
                break
            total += len(block)
            if total > byte_limit:
                raise ValueError("Selected cache item exceeded the approved hash budget.")
            digest.update(block)
        after_open = os.fstat(stream.fileno())
        if (
            after_open.st_size != opened.st_size
            or after_open.st_mtime_ns != opened.st_mtime_ns
            or after_open.st_dev != opened.st_dev
            or after_open.st_ino != opened.st_ino
        ):
            raise ValueError("Selected cache item changed during integrity hashing.")
    after_path = path.stat(follow_symlinks=False)
    if (
        after_path.st_size != before.st_size
        or after_path.st_mtime_ns != before.st_mtime_ns
        or after_path.st_dev != before.st_dev
        or after_path.st_ino != before.st_ino
        or _is_link_or_reparse(path)
    ):
        raise ValueError("Selected cache item changed after integrity hashing.")
    return digest.hexdigest(), after_path


def _atomic_write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["manifest_sha256"] = _canonical_digest(manifest)
    encoded = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_CACHE_MANIFEST_BYTES:
        raise ValueError("Browser-cache manifest exceeds the approved size limit.")
    temp = path.with_name(f".manifest-{secrets.token_hex(8)}.tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _move_without_overwriting(source: Path, destination: Path) -> None:
    """Move a file without a destination-overwrite window on Windows.

    The runtime operation is Windows-only. Path.rename() maps to os.rename(),
    which atomically moves the source directory entry and raises FileExistsError
    on Windows when the destination already exists. The hard-link fallback is
    retained for non-Windows test runs that explicitly simulate the Windows API.
    """
    if os.name == "nt":
        source.rename(destination)
        return
    # Test-only fallback: retain exclusive publication on hosts without Windows
    # rename semantics. os.replace() is deliberately not used here.
    os.link(source, destination)
    source.unlink()


def _known_cache_root_for_path(path: Path, env: dict[str, str] | None = None) -> Path | None:
    """Resolve an original path only against cache roots in the current allowlist."""
    try:
        absolute = Path(os.path.abspath(str(path)))
        for _browser, profile_root, kind in _known_profile_roots(os.environ if env is None else env):
            if not profile_root.is_dir() or _is_link_or_reparse(profile_root):
                continue
            try:
                with os.scandir(profile_root) as entries:
                    for entry in entries:
                        if not _profile_matches(entry.name, kind):
                            continue
                        profile = Path(entry.path)
                        if entry.is_symlink() or _is_link_or_reparse(profile) or not entry.is_dir(follow_symlinks=False):
                            continue
                        for _area, cache_root in _cache_roots_for_profile(profile, kind):
                            if not cache_root.is_dir() or not _safe_directory(cache_root, profile):
                                continue
                            resolved_root = cache_root.resolve(strict=True)
                            resolved_path = absolute.resolve(strict=False)
                            if resolved_path != resolved_root and resolved_root in resolved_path.parents:
                                if not _path_has_link_from(absolute, resolved_root):
                                    return resolved_root
            except OSError:
                continue
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def _manifest_location(path_value: str | Path, env: dict[str, str] | None = None) -> tuple[Path, Path, dict[str, Any]]:
    path = Path(path_value).expanduser()
    if not path.is_absolute() or path.name != "manifest.json" or _is_link_or_reparse(path):
        raise ValueError("Select an absolute manifest.json path from a BOTPYTON browser-cache quarantine session.")
    root = _quarantine_root(env)
    local_root = _local_app_data(env)
    if _path_has_link_from(root, local_root) or not root.exists() or _is_link_or_reparse(root):
        raise ValueError("BOTPYTON browser-cache quarantine root is unavailable or contains a link/reparse component.")
    root = root.resolve(strict=True)
    session = path.parent
    if session.parent != root or not session.name.startswith("session-") or _path_has_link_from(path, root):
        raise ValueError("Manifest is outside the approved BOTPYTON browser-cache quarantine root.")
    if not path.is_file():
        raise ValueError("Quarantine manifest was not found.")
    if path.stat().st_size > MAX_CACHE_MANIFEST_BYTES:
        raise ValueError("Quarantine manifest exceeds the approved size limit.")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Quarantine manifest could not be read as valid UTF-8 JSON.") from exc
    if not isinstance(manifest, dict) or manifest.get("product") != "BOTPYTON browser-cache quarantine":
        raise ValueError("Manifest product identity is invalid.")
    if manifest.get("schema_version") != 1 or manifest.get("session_id") != session.name:
        raise ValueError("Manifest schema/session identity is invalid.")
    items = manifest.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_CACHE_RESTORE_ITEMS:
        raise ValueError("Manifest item count is outside the approved range.")
    recorded_digest = manifest.get("manifest_sha256")
    if not isinstance(recorded_digest, str) or not secrets.compare_digest(recorded_digest, _canonical_digest(manifest)):
        raise ValueError("Manifest integrity check failed; no files were restored.")
    expected_files = session / "files"
    if expected_files.exists() and (not expected_files.is_dir() or _is_link_or_reparse(expected_files)):
        raise ValueError("Quarantine files directory is not a regular directory.")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("Manifest contains a malformed item.")
        original = item.get("original_path")
        quarantine = item.get("quarantine_path")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if (
            not isinstance(original, str) or not Path(original).is_absolute()
            or not isinstance(quarantine, str)
            or Path(quarantine) != expected_files / f"{index:03d}.cache"
            or not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_CACHE_FILE_BYTES
            or not isinstance(digest, str) or len(digest) != 64
            or item.get("state") not in {"pending", "moved", "failed", "restored", "skipped_changed"}
        ):
            raise ValueError("Manifest contains a path, size, digest, or state outside the approved contract.")
        # A manifest may contain entries for a browser profile that was later removed.
        # Keep the session readable; restore preview validates each destination separately
        # and marks stale/unrecognized paths as skipped without blocking other safe items.
    return path, session, manifest


def quarantine_application_cache_candidates(
    selected_paths: list[str],
    *,
    confirmation: str,
    env: dict[str, str] | None = None,
    windows: bool | None = None,
) -> dict[str, Any]:
    """Move only freshly re-validated user-selected browser cache files into quarantine."""
    is_windows = os.name == "nt" if windows is None else windows
    base_report: dict[str, Any] = {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "operation": "application_cache_quarantine",
        "read_only": False,
        "network_used": False,
        "ai_enabled": False,
        "files_deleted": 0,
        "settings_changed": False,
        "drivers_updated": False,
    }
    if not is_windows:
        return {**base_report, "status": "unsupported_platform", "moved_count": 0, "note": "Browser-cache quarantine is currently supported only on Windows."}
    if confirmation != CACHE_QUARANTINE_CONFIRMATION:
        return {**base_report, "status": "confirmation_mismatch", "moved_count": 0, "note": "Confirmation did not match; no files were moved."}
    if not isinstance(selected_paths, list) or not 1 <= len(selected_paths) <= MAX_SELECTED_CACHE_FILES:
        return {**base_report, "status": "cache_quarantine_blocked", "moved_count": 0, "note": f"Select between 1 and {MAX_SELECTED_CACHE_FILES} candidate files."}
    if any(not isinstance(path, str) or not path for path in selected_paths) or len(set(selected_paths)) != len(selected_paths):
        return {**base_report, "status": "cache_quarantine_blocked", "moved_count": 0, "note": "Selection contains invalid or duplicate paths."}

    try:
        _local_app_data(env)
        fresh = preview_application_cache(env=env, windows=True, max_candidates=MAX_APPLICATION_CACHE_CANDIDATES, timeout_seconds=5.0)
        if fresh.get("status") != "preview_complete" or fresh.get("scan_limited") is True or fresh.get("access_errors", 0) != 0:
            return {**base_report, "status": "cache_quarantine_blocked", "moved_count": 0, "preview_status": fresh.get("status"), "note": "A complete, unambiguous fresh browser-cache preview is required. Nothing was moved."}
        candidates = {item.get("path"): item for item in fresh.get("candidates", []) if isinstance(item, dict)}
        selected: list[tuple[dict[str, Any], Path, os.stat_result, str]] = []
        total_bytes = 0
        for raw_path in selected_paths:
            item = candidates.get(raw_path)
            if not item:
                raise ValueError("A selected path is not a current candidate in the allowlisted browser-cache preview.")
            source = Path(raw_path)
            root_text = item.get("cache_root")
            if not isinstance(root_text, str):
                raise ValueError("A selected candidate has no recognized cache root.")
            cache_root = Path(root_text).resolve(strict=True)
            source_resolved = source.resolve(strict=True)
            if source_resolved == cache_root or cache_root not in source_resolved.parents:
                raise ValueError("A selected candidate escaped its recognized cache root.")
            if _path_has_link_from(source_resolved, cache_root):
                raise ValueError("A selected candidate has a link/reparse component in its cache path.")
            st = source.stat(follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or _is_link_or_reparse(source):
                raise ValueError("A selected candidate is no longer a regular unlinked file.")
            if (
                st.st_size != item.get("size_bytes")
                or st.st_mtime_ns != item.get("modified_ns")
            ):
                raise ValueError(
                    "A selected candidate's size or nanosecond modification time changed after the preview; "
                    f"size_match={st.st_size == item.get('size_bytes')}, "
                    f"mtime_match={st.st_mtime_ns == item.get('modified_ns')}."
                )
            if st.st_size < 0 or st.st_size > MAX_CACHE_FILE_BYTES:
                raise ValueError(f"A selected file exceeds the {MAX_CACHE_FILE_BYTES // (1024 * 1024)} MiB per-file limit.")
            total_bytes += st.st_size
            if total_bytes > MAX_CACHE_BATCH_BYTES:
                raise ValueError(f"Selected files exceed the {MAX_CACHE_BATCH_BYTES // (1024 * 1024)} MiB total limit.")
            selected.append((item, source_resolved, st, str(cache_root)))

        prepared: list[dict[str, Any]] = []
        for item, source, st, cache_root in selected:
            digest, after_hash = _sha256_checked(source, byte_limit=MAX_CACHE_FILE_BYTES, expected=st)
            prepared.append({
                "browser": item["browser"],
                "profile": item["profile"],
                "cache_area": item["cache_area"],
                "original_path": str(source),
                "quarantine_path": "",
                "cache_root": cache_root,
                "size_bytes": int(after_hash.st_size),
                "modified_ns": int(after_hash.st_mtime_ns),
                "device": int(after_hash.st_dev),
                "inode": int(after_hash.st_ino),
                "sha256": digest,
                "state": "pending",
            })

        root = _quarantine_root(env)
        if _path_has_link_from(root, _local_app_data(env)):
            return {**base_report, "status": "cache_quarantine_blocked", "moved_count": 0, "note": "Quarantine root contains a link/reparse component."}
        root.mkdir(parents=True, exist_ok=True)
        if _is_link_or_reparse(root):
            raise ValueError("Quarantine root is not a regular directory.")
        session = root / f"session-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{secrets.token_hex(4)}"
        files_dir = session / "files"
        files_dir.mkdir(parents=True, exist_ok=False)
        manifest_path = session / "manifest.json"
        for index, item in enumerate(prepared):
            item["quarantine_path"] = str(files_dir / f"{index:03d}.cache")
        manifest: dict[str, Any] = {
            "product": "BOTPYTON browser-cache quarantine",
            "schema_version": 1,
            "session_id": session.name,
            "status": "in_progress",
            "created_epoch": int(time.time()),
            "confirmation": CACHE_QUARANTINE_CONFIRMATION,
            "items": prepared,
        }
        _atomic_write_manifest(manifest_path, manifest)
        moved_count = 0
        failed_count = 0
        persistence_failed = False
        for index, item in enumerate(prepared):
            source = Path(item["original_path"])
            destination = Path(item["quarantine_path"])
            try:
                root_path = Path(item["cache_root"])
                current_stat = source.stat(follow_symlinks=False)
                if (
                    not source.is_file() or _is_link_or_reparse(source)
                    or current_stat.st_size != item["size_bytes"]
                    or current_stat.st_mtime_ns != item["modified_ns"]
                    or current_stat.st_dev != item["device"]
                    or current_stat.st_ino != item["inode"]
                    or _path_has_link_from(source, root_path)
                    or destination.exists() or _is_link_or_reparse(destination.parent)
                ):
                    item["state"] = "skipped_changed"
                    failed_count += 1
                    _atomic_write_manifest(manifest_path, manifest)
                    continue
                # On Windows, rename is a single atomic directory-entry operation
                # and fails if another process creates the destination first. Avoid a
                # hard-link-plus-unlink sequence here: the source path could be replaced
                # between those operations, causing unlink() to remove a different file.
                _move_without_overwriting(source, destination)
                item["state"] = "moved"
                moved_count += 1
                try:
                    _atomic_write_manifest(manifest_path, manifest)
                except OSError:
                    # The last durable manifest says pending and records both paths/hash;
                    # recovery tooling can detect a matching file at the quarantine destination.
                    persistence_failed = True
                    break
            except (OSError, ValueError):
                item["state"] = "failed"
                failed_count += 1
                try:
                    _atomic_write_manifest(manifest_path, manifest)
                except OSError:
                    persistence_failed = True
                    break
        manifest["status"] = "partial" if failed_count or persistence_failed or moved_count != len(prepared) else "complete"
        try:
            _atomic_write_manifest(manifest_path, manifest)
        except OSError:
            persistence_failed = True
        if persistence_failed:
            final_status = "cache_quarantine_partial"
            note = "Manifest persistence failed during the move loop. Stop further changes and use the durable manifest to inspect/recover the session."
        elif moved_count == len(prepared):
            final_status = "cache_quarantined"
            note = "Selected cache files were moved to reversible quarantine, not permanently deleted."
        elif moved_count:
            final_status = "cache_quarantine_partial"
            note = "Some selected files moved; inspect the manifest. Files that changed or could not be moved were left in place."
        else:
            final_status = "cache_quarantine_blocked"
            note = "No selected files were moved. Inspect the report and manifest."
        return {
            **base_report, "status": final_status, "selected_count": len(selected),
            "moved_count": moved_count, "files_moved": moved_count,
            "files_skipped": failed_count, "bytes_quarantined": sum(x["size_bytes"] for x in prepared if x["state"] == "moved"),
            "manifest_path": str(manifest_path), "session_id": session.name,
            "manifest_sha256": manifest.get("manifest_sha256"),
            "note": note,
            "limitations": [
                "Files are moved, not permanently deleted. Close the relevant browser before selecting cache files.",
                "Only explicit paths in a fresh complete allowlisted preview are eligible.",
                "A move may fail when a browser has a file open, permissions prevent it, or the filesystem rejects an atomic same-volume move.",
                "SHA-256 is an integrity check, not a digital signature. The manifest contains local file paths.",
            ],
        }
    except (OSError, RuntimeError, ValueError) as exc:
        detail = str(exc).replace("\\r", " ").replace("\\n", " ")[:240]
        return {
            **base_report, "status": "cache_quarantine_blocked", "moved_count": 0,
            "failure_type": type(exc).__name__, "failure_detail": detail,
            "note": f"Quarantine stopped before a usable result ({type(exc).__name__}). No automatic retry was attempted.",
        }


def preview_application_cache_restore(
    manifest_path: str | Path,
    *,
    env: dict[str, str] | None = None,
    windows: bool | None = None,
) -> dict[str, Any]:
    """Validate a cache quarantine manifest and report which files could be restored."""
    base: dict[str, Any] = {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "operation": "application_cache_restore_preview",
        "read_only": True,
        "network_used": False,
        "ai_enabled": False,
        "files_restored": 0,
        "settings_changed": False,
    }
    is_windows = os.name == "nt" if windows is None else windows
    if not is_windows:
        return {**base, "status": "unsupported_platform", "ready_count": 0, "items": [], "note": "Browser-cache restore is currently supported only on Windows."}
    try:
        path, session, manifest = _manifest_location(manifest_path, env)
        checked: list[dict[str, Any]] = []
        ready_count = 0
        hash_bytes = 0
        partial = False
        for index, item in enumerate(manifest["items"]):
            original = Path(item["original_path"])
            quarantined = Path(item["quarantine_path"])
            state = item["state"]
            if state not in {"moved", "pending"} or not quarantined.is_file() or _is_link_or_reparse(quarantined):
                checked.append({"index": index + 1, "browser": item["browser"], "profile": item["profile"], "path": str(original), "status": "not_quarantined"})
                partial = True
                continue
            if original.exists() or original.is_symlink():
                checked.append({"index": index + 1, "browser": item["browser"], "profile": item["profile"], "path": str(original), "status": "destination_exists_no_overwrite"})
                partial = True
                continue
            recognized_root = _known_cache_root_for_path(original, env)
            if recognized_root is None:
                checked.append({"index": index + 1, "browser": item["browser"], "profile": item["profile"], "path": str(original), "status": "destination_not_allowlisted"})
                partial = True
                continue
            if not original.parent.is_dir() or _path_has_link_from(original.parent, recognized_root):
                checked.append({"index": index + 1, "browser": item["browser"], "profile": item["profile"], "path": str(original), "status": "destination_parent_unavailable_or_linked"})
                partial = True
                continue
            if hash_bytes + item["size_bytes"] > MAX_CACHE_BATCH_BYTES:
                checked.append({"index": index + 1, "browser": item["browser"], "profile": item["profile"], "path": str(original), "status": "hash_budget_exceeded"})
                partial = True
                continue
            hash_bytes += item["size_bytes"]
            digest, st = _sha256_checked(quarantined, byte_limit=MAX_CACHE_FILE_BYTES)
            if st.st_size != item["size_bytes"] or not secrets.compare_digest(digest, item["sha256"]):
                checked.append({"index": index + 1, "browser": item["browser"], "profile": item["profile"], "path": str(original), "status": "integrity_mismatch"})
                partial = True
                continue
            checked.append({"index": index + 1, "browser": item["browser"], "profile": item["profile"], "cache_area": item["cache_area"], "path": str(original), "size_bytes": item["size_bytes"], "sha256_verified": True, "status": "ready_to_restore"})
            ready_count += 1
        status = "restore_preview_complete" if not partial and ready_count else ("restore_preview_partial" if ready_count else "restore_preview_blocked")
        return {
            **base, "status": status, "manifest_path": str(path), "session_id": session.name,
            "manifest_sha256": manifest.get("manifest_sha256"), "items_count": len(checked),
            "ready_count": ready_count, "items": checked, "files_skipped": len(checked) - ready_count,
            "note": "Restore preview only. No file was moved. Existing destination files are never overwritten.",
            "limitations": [
                "Restore is allowed only beneath currently recognized browser cache roots.",
                "Parent directories must already exist and must not contain links/reparse points.",
                "Every quarantine file is checked against its recorded size and SHA-256 before it can be restored.",
                "The original location may have been recreated by the browser. Conflicts are skipped, never overwritten.",
            ],
        }
    except (OSError, RuntimeError, ValueError) as exc:
        return {**base, "status": "cache_manifest_invalid", "ready_count": 0, "items": [], "note": f"Restore preview blocked ({type(exc).__name__}); no files were moved."}


def restore_application_cache_quarantine(
    manifest_path: str | Path,
    *,
    confirmation: str,
    env: dict[str, str] | None = None,
    windows: bool | None = None,
) -> dict[str, Any]:
    """Restore only verified files; preserve conflicts and never overwrite."""
    base: dict[str, Any] = {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "operation": "application_cache_restore",
        "read_only": False,
        "network_used": False,
        "ai_enabled": False,
        "files_deleted": 0,
        "settings_changed": False,
    }
    is_windows = os.name == "nt" if windows is None else windows
    if not is_windows:
        return {**base, "status": "unsupported_platform", "files_restored": 0}
    if confirmation != CACHE_RESTORE_CONFIRMATION:
        return {**base, "status": "confirmation_mismatch", "files_restored": 0, "note": "Confirmation did not match; no files were restored."}
    preview = preview_application_cache_restore(manifest_path, env=env, windows=True)
    if preview.get("status") in {"cache_manifest_invalid", "restore_preview_blocked"} or preview.get("ready_count", 0) < 1:
        return {**base, "status": "cache_restore_blocked", "files_restored": 0, "preview": preview, "note": "No verified restore target is ready. No files were moved."}
    try:
        path, session, manifest = _manifest_location(manifest_path, env)
        item_results = {entry["index"] - 1: entry for entry in preview["items"]}
        restored = 0
        skipped = 0
        hash_bytes = 0
        for index, item in enumerate(manifest["items"]):
            result = item_results.get(index, {})
            if result.get("status") != "ready_to_restore":
                skipped += 1
                continue
            original = Path(item["original_path"])
            quarantined = Path(item["quarantine_path"])
            try:
                root = _known_cache_root_for_path(original, env)
                if root is None or not original.parent.is_dir() or _path_has_link_from(original.parent, root):
                    skipped += 1
                    continue
                if original.exists() or original.is_symlink() or not quarantined.is_file() or _is_link_or_reparse(quarantined):
                    skipped += 1
                    continue
                if hash_bytes + item["size_bytes"] > MAX_CACHE_BATCH_BYTES:
                    skipped += 1
                    continue
                hash_bytes += item["size_bytes"]
                digest, current = _sha256_checked(quarantined, byte_limit=MAX_CACHE_FILE_BYTES)
                if current.st_size != item["size_bytes"] or not secrets.compare_digest(digest, item["sha256"]):
                    skipped += 1
                    continue
                # Same-volume atomic move; no replacement of an existing destination.
                if original.exists() or original.is_symlink():
                    skipped += 1
                    continue
                # Use the same single-operation Windows rename as quarantine: if
                # another process recreates the original path, rename fails without
                # overwriting it and the verified quarantine copy remains untouched.
                _move_without_overwriting(quarantined, original)
                item["state"] = "restored"
                restored += 1
                _atomic_write_manifest(path, manifest)
            except (OSError, ValueError):
                skipped += 1
        manifest["status"] = "complete" if restored and not skipped else "partial"
        try:
            _atomic_write_manifest(path, manifest)
        except OSError:
            skipped += 1
        status = "cache_restored" if restored and not skipped else ("cache_restore_partial" if restored or skipped else "cache_restore_blocked")
        return {
            **base, "status": status, "manifest_path": str(path), "session_id": session.name,
            "files_restored": restored, "files_skipped": skipped,
            "manifest_sha256": manifest.get("manifest_sha256"),
            "note": "Verified files were restored only to missing paths under recognized cache roots. No existing file was overwritten.",
            "limitations": [
                "If the browser recreated a destination or still holds a file open, restore skips that item.",
                "Only files verified against the quarantine manifest can be restored.",
                "The manifest remains available for review. SHA-256 is an integrity check, not a digital signature.",
            ],
        }
    except (OSError, RuntimeError, ValueError) as exc:
        return {**base, "status": "cache_restore_blocked", "files_restored": 0, "note": f"Restore stopped safely ({type(exc).__name__}); inspect the manifest and existing files."}
