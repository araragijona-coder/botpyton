"""Build and verify a bounded, deterministic BOTPYTON portable source archive."""
from __future__ import annotations

import hashlib
import os
import stat
import time
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

PACKAGE_SCHEMA_VERSION = 1
PACKAGE_ROOT = "BOTPYTON"
PACKAGE_MANIFEST = f"{PACKAGE_ROOT}/package-manifest.json"
MAX_PACKAGE_FILE_BYTES = 5 * 1024 * 1024
MAX_PACKAGE_TOTAL_BYTES = 30 * 1024 * 1024
MAX_PACKAGE_FILES = 1_000
COPY_CHUNK_BYTES = 256 * 1024
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)


def _stream_sha256(path: Path, *, byte_limit: int) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > byte_limit:
                raise ValueError(f"package input exceeds the {byte_limit}-byte safety limit: {path.name}")
            digest.update(chunk)
    return digest.hexdigest()


def _collect_package_files(root: Path) -> list[tuple[str, Path]]:
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("package source root is not a directory")
    selected: dict[str, Path] = {}
    fixed_files = [
        "README.md",
        "BOTPYTON.bat",
        "BOTPYTON-CLI.bat",
        "pyproject.toml",
        ".cerebro/config.json",
        ".cerebro/memory/README.md",
    ]
    for relative in fixed_files:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"required portable package file is missing or linked: {relative}")
        selected[relative] = path

    tree_rules = [
        ("bot", {".py"}),
        ("docs", {".md", ".json"}),
        (".cerebro/examples", {".json"}),
    ]
    for relative_root, allowed_extensions in tree_rules:
        source_root = root / relative_root
        if source_root.is_symlink() or not source_root.is_dir():
            raise ValueError(f"required package folder is missing or linked: {relative_root}")
        for current, directory_names, file_names in os.walk(source_root, topdown=True, followlinks=False):
            current_path = Path(current)
            directory_names[:] = sorted(
                name for name in directory_names
                if name not in {"__pycache__", ".git", ".venv", "venv"} and not (current_path / name).is_symlink()
            )
            for name in sorted(file_names):
                file_path = current_path / name
                if file_path.suffix.casefold() not in allowed_extensions:
                    continue
                if file_path.is_symlink() or not file_path.is_file():
                    continue
                relative = file_path.relative_to(root).as_posix()
                selected[relative] = file_path

    if len(selected) > MAX_PACKAGE_FILES:
        raise ValueError(f"package file count exceeds the {MAX_PACKAGE_FILES}-file cap")
    total_size = 0
    result = []
    for relative, path in sorted(selected.items()):
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
            raise ValueError("package contains an unsafe source path")
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PACKAGE_FILE_BYTES:
            raise ValueError(f"package file is not a bounded regular file: {relative}")
        total_size += info.st_size
        if total_size > MAX_PACKAGE_TOTAL_BYTES:
            raise ValueError("package uncompressed source data exceeds the total size cap")
        result.append((relative, path))
    return result


def _zip_info(name: str, size: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.file_size = size
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.create_system = 3
    return info


def _write_member(archive: zipfile.ZipFile, archive_name: str, source: Path, expected_size: int) -> None:
    before = source.stat(follow_symlinks=False)
    if before.st_size != expected_size or not stat.S_ISREG(before.st_mode):
        raise ValueError(f"package input changed before copy: {source.name}")
    copied = 0
    with source.open("rb") as source_stream, archive.open(_zip_info(archive_name, expected_size), "w") as archive_stream:
        while True:
            chunk = source_stream.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            copied += len(chunk)
            if copied > expected_size:
                raise ValueError(f"package input grew while copying: {source.name}")
            archive_stream.write(chunk)
    after = source.stat(follow_symlinks=False)
    if copied != expected_size or after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        raise ValueError(f"package input changed while copying: {source.name}")


def verify_portable_archive(archive_path: str | Path) -> dict:
    """Verify archive bounds, path safety, member hashes and the archive CRC."""
    path = Path(archive_path).expanduser()
    if not path.is_file() or path.is_symlink():
        raise ValueError("portable package archive is missing or linked")
    if path.stat().st_size > MAX_PACKAGE_TOTAL_BYTES + 2_000_000:
        raise ValueError("portable package archive exceeds the allowed size")
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_PACKAGE_FILES + 1:
            raise ValueError("portable package member count is invalid")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("portable package contains duplicate member paths")
        total_size = 0
        for info in infos:
            pure = PurePosixPath(info.filename)
            mode = (info.external_attr >> 16) & 0xFFFF
            if (pure.is_absolute() or ".." in pure.parts or "\\" in info.filename
                    or not pure.parts or pure.parts[0] != PACKAGE_ROOT
                    or not stat.S_ISREG(mode) or info.is_dir()):
                raise ValueError("portable package contains an unsafe member")
            if info.file_size > MAX_PACKAGE_FILE_BYTES:
                raise ValueError("portable package member exceeds the size cap")
            total_size += info.file_size
            if total_size > MAX_PACKAGE_TOTAL_BYTES:
                raise ValueError("portable package expands beyond the total size cap")
        if archive.testzip() is not None:
            raise ValueError("portable package ZIP CRC validation failed")
        if PACKAGE_MANIFEST not in names:
            raise ValueError("portable package manifest is missing")
        import json
        manifest_info = archive.getinfo(PACKAGE_MANIFEST)
        if manifest_info.file_size > MAX_PACKAGE_FILE_BYTES:
            raise ValueError("portable package manifest exceeds the size cap")
        manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("product") != "BOTPYTON"
                or manifest.get("package_schema_version") != PACKAGE_SCHEMA_VERSION
                or manifest.get("requires_python") != ">=3.11"
                or manifest.get("includes_python_interpreter") is not False
                or not isinstance(manifest.get("files"), list)
                or len(manifest["files"]) != len(names) - 1):
            raise ValueError("portable package manifest has invalid schema or member count")
        expected = {}
        for record in manifest["files"]:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                raise ValueError("portable package manifest contains an invalid file record")
            member = record["path"]
            if member == PACKAGE_MANIFEST or member in expected or member not in names:
                raise ValueError("portable package manifest path set does not match archive")
            size = record.get("size_bytes")
            digest = record.get("sha256")
            if (not isinstance(size, int) or isinstance(size, bool) or size < 0
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)):
                raise ValueError("portable package manifest integrity metadata is invalid")
            expected[member] = (size, digest)
        if set(names) != set(expected) | {PACKAGE_MANIFEST}:
            raise ValueError("portable package contains an unlisted file")
        for member, (expected_size, expected_digest) in expected.items():
            info = archive.getinfo(member)
            if info.file_size != expected_size:
                raise ValueError(f"portable package size mismatch: {member}")
            digest = hashlib.sha256()
            count = 0
            with archive.open(info, "r") as stream:
                while True:
                    chunk = stream.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > expected_size or count > MAX_PACKAGE_FILE_BYTES:
                        raise ValueError(f"portable package member exceeds its declared size: {member}")
                    digest.update(chunk)
            if count != expected_size or digest.hexdigest() != expected_digest:
                raise ValueError(f"portable package SHA-256 mismatch: {member}")
    archive_digest = _stream_sha256(path, byte_limit=MAX_PACKAGE_TOTAL_BYTES + 2_000_000)
    return {
        "product": "BOTPYTON",
        "status": "verified",
        "archive_path": str(path.resolve()),
        "archive_sha256": archive_digest,
        "member_count": len(infos),
        "manifest_file_count": len(expected),
        "uncompressed_bytes": total_size,
        "requires_python": ">=3.11",
        "includes_python_interpreter": False,
        "network_used": False,
    }


def build_portable_package(source_root: str | Path, output_dir: str | Path) -> dict:
    """Create a reproducible source ZIP and SHA-256 sidecar without bundling runtime/user data."""
    import json

    root = Path(source_root).expanduser().resolve(strict=True)
    output = Path(output_dir).expanduser().resolve()
    files = _collect_package_files(root)
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = project.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("pyproject.toml has no valid project version")

    records = []
    for relative, path in files:
        size = path.stat(follow_symlinks=False).st_size
        digest = _stream_sha256(path, byte_limit=MAX_PACKAGE_FILE_BYTES)
        records.append({"path": f"{PACKAGE_ROOT}/{relative}", "size_bytes": size, "sha256": digest})
    manifest = {
        "product": "BOTPYTON",
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "version": version,
        "artifact_type": "portable-source-zip",
        "requires_python": ">=3.11",
        "includes_python_interpreter": False,
        "runtime_dependencies": [],
        "files": records,
        "limitations": [
            "This archive contains Python source, not a bundled Python interpreter or standalone EXE.",
            "Optional developer/GitHub worker functions may still require external tools or local authentication.",
            "The archive manifest provides integrity checksums, not a digital signature.",
        ],
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(manifest_bytes) > MAX_PACKAGE_FILE_BYTES:
        raise ValueError("generated package manifest exceeds the size cap")
    output.mkdir(parents=True, exist_ok=True)
    final_path = output / "BOTPYTON-portable.zip"
    temporary_path = output / f".BOTPYTON-portable-{time.time_ns()}.tmp"
    try:
        with zipfile.ZipFile(temporary_path, "w", allowZip64=False) as archive:
            for (relative, path), record in zip(files, records):
                _write_member(archive, f"{PACKAGE_ROOT}/{relative}", path, record["size_bytes"])
            manifest_info = _zip_info(PACKAGE_MANIFEST, len(manifest_bytes))
            with archive.open(manifest_info, "w") as stream:
                stream.write(manifest_bytes)
        verify_portable_archive(temporary_path)
        os.replace(temporary_path, final_path)
    except Exception:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    verified = verify_portable_archive(final_path)
    checksum_path = final_path.with_suffix(final_path.suffix + ".sha256")
    checksum_temp = checksum_path.with_name(f".{checksum_path.name}.{time.time_ns()}.tmp")
    checksum_text = f'{verified["archive_sha256"]}  {final_path.name}\n'
    with checksum_temp.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(checksum_text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(checksum_temp, checksum_path)
    return {
        **verified,
        "version": version,
        "checksum_path": str(checksum_path.resolve()),
        "file_count": len(files),
        "network_used": False,
    }
