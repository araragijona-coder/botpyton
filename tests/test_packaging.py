from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from bot.packaging import _collect_package_files, build_portable_package, verify_portable_archive


class PortablePackageTests(unittest.TestCase):
    def _minimal_package_source(self, root: Path) -> None:
        (root / ".cerebro" / "memory").mkdir(parents=True)
        (root / ".cerebro" / "examples").mkdir(parents=True)
        (root / "bot").mkdir()
        (root / "docs").mkdir()
        (root / "README.md").write_text("readme", encoding="utf-8")
        (root / "BOTPYTON.bat").write_text("@echo off", encoding="utf-8")
        (root / "BOTPYTON-CLI.bat").write_text("@echo off", encoding="utf-8")
        (root / "pyproject.toml").write_text("[project]\\nversion = \"0.0.0\"\\n", encoding="utf-8")
        (root / ".cerebro" / "config.json").write_text("{}", encoding="utf-8")
        (root / ".cerebro" / "memory" / "README.md").write_text("memory", encoding="utf-8")
        (root / ".cerebro" / "examples" / "sample.json").write_text("{}", encoding="utf-8")
        (root / "bot" / "main.py").write_text("pass\\n", encoding="utf-8")
        (root / "docs" / "guide.md").write_text("guide", encoding="utf-8")

    def test_package_collector_prunes_windows_reparse_like_directories(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            self._minimal_package_source(root)
            outside_like = root / "docs" / "mounted"
            outside_like.mkdir()
            (outside_like / "private.md").write_text("must not ship", encoding="utf-8")

            def reparse_probe(path):
                return Path(path).name == "mounted"

            with patch("bot.packaging._is_link_or_reparse", side_effect=reparse_probe):
                files = dict(_collect_package_files(root))

            self.assertIn("docs/guide.md", files)
            self.assertNotIn("docs/mounted/private.md", files)

    @unittest.skipUnless(__import__("os").name == "nt", "Windows junction integration test")
    def test_package_collector_does_not_follow_real_windows_junction(self):
        import os
        import subprocess

        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            root = temp_root / "source"
            root.mkdir()
            self._minimal_package_source(root)
            external = temp_root / "external-private"
            external.mkdir()
            (external / "secret.md").write_text("must not be included", encoding="utf-8")
            junction = root / "docs" / "mounted"

            created = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(external)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest("This Windows runner cannot create a test junction: " + created.stderr[-200:])

            try:
                files = dict(_collect_package_files(root))
                self.assertNotIn("docs/mounted/secret.md", files)
                self.assertIn("docs/guide.md", files)
            finally:
                try:
                    os.rmdir(junction)
                except OSError:
                    pass

    def test_package_collector_rejects_reparse_point_in_fixed_file_ancestry(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            self._minimal_package_source(root)

            def reparse_probe(path):
                return Path(path).name == ".cerebro"

            with patch("bot.packaging._is_link_or_reparse", side_effect=reparse_probe):
                with self.assertRaisesRegex(ValueError, "required portable package file.*linked"):
                    _collect_package_files(root)

    def test_portable_archive_builds_and_verifies_member_hashes(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "dist"
            result = build_portable_package(repo_root, output)
            archive_path = Path(result["archive_path"])
            checksum_path = Path(result["checksum_path"])
            self.assertEqual(result["status"], "verified")
            self.assertTrue(archive_path.is_file())
            self.assertTrue(checksum_path.is_file())
            self.assertEqual(verify_portable_archive(archive_path)["archive_sha256"], result["archive_sha256"])
            with ZipFile(archive_path) as archive:
                names = archive.namelist()
                self.assertIn("BOTPYTON/bot/main.py", names)
                self.assertIn("BOTPYTON/bot/gui.py", names)
                self.assertIn("BOTPYTON/BOTPYTON.bat", names)
                self.assertIn("BOTPYTON/BOTPYTON-CLI.bat", names)
                self.assertIn("BOTPYTON/package-manifest.json", names)
                self.assertNotIn("BOTPYTON/tests/test_pc_care.py", names)
                self.assertFalse(any("/state.json" in name or "/reports/" in name for name in names))
                self.assertTrue(all(name.startswith("BOTPYTON/") for name in names))
            checksum_line = checksum_path.read_text(encoding="ascii").strip()
            self.assertTrue(checksum_line.startswith(result["archive_sha256"] + "  "))

    def test_portable_builder_is_deterministic_for_unchanged_sources(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            first = build_portable_package(repo_root, Path(tmp) / "first")
            second = build_portable_package(repo_root, Path(tmp) / "second")
            self.assertEqual(first["archive_sha256"], second["archive_sha256"])
            self.assertEqual(
                Path(first["archive_path"]).read_bytes(),
                Path(second["archive_path"]).read_bytes(),
            )

    def test_portable_verifier_rejects_missing_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                verify_portable_archive(Path(tmp) / "missing.zip")

    def test_portable_verifier_detects_same_size_member_corruption(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            build = build_portable_package(repo_root, Path(tmp) / "source")
            source_path = Path(build["archive_path"])
            corrupt_path = Path(tmp) / "corrupt.zip"
            with ZipFile(source_path, "r") as source:
                with ZipFile(corrupt_path, "w", allowZip64=False) as target:
                    for info in source.infolist():
                        content = source.read(info.filename)
                        if info.filename == "BOTPYTON/README.md":
                            self.assertGreater(len(content), 0)
                            content = bytes([content[0] ^ 1]) + content[1:]
                        target.writestr(info, content)
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                verify_portable_archive(corrupt_path)


    def test_checksum_publish_failure_restores_previous_archive_and_sidecar(self):
        import os
        from unittest import mock

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            self._minimal_package_source(root)
            # This fixture is also consumed by tomllib when building a package.
            (root / "pyproject.toml").write_text(
                '[project]\nversion = "0.0.0"\n', encoding="utf-8"
            )
            output = Path(temp) / "dist"
            first = build_portable_package(root, output)
            archive_path = Path(first["archive_path"])
            checksum_path = Path(first["checksum_path"])
            previous_archive = archive_path.read_bytes()
            previous_checksum = checksum_path.read_bytes()

            # Make the next archive different so a partial publication is visible.
            (root / "README.md").write_text("changed readme content", encoding="utf-8")
            real_replace = os.replace
            fail_once = True

            def fail_checksum_publication(source, destination):
                nonlocal fail_once
                if (
                    fail_once
                    and Path(destination) == checksum_path
                    and Path(source).name.startswith(f".{checksum_path.name}.")
                    and Path(source).suffix == ".tmp"
                ):
                    fail_once = False
                    raise OSError("simulated checksum sidecar publication failure")
                return real_replace(source, destination)

            with mock.patch("bot.packaging.os.replace", side_effect=fail_checksum_publication):
                with self.assertRaisesRegex(OSError, "simulated checksum sidecar publication failure"):
                    build_portable_package(root, output)

            self.assertFalse(fail_once, "the failure must occur at the second publication")
            self.assertEqual(archive_path.read_bytes(), previous_archive)
            self.assertEqual(checksum_path.read_bytes(), previous_checksum)
            self.assertEqual(
                verify_portable_archive(archive_path)["archive_sha256"],
                previous_checksum.decode("ascii").split()[0],
            )
            self.assertFalse(list(output.glob(".BOTPYTON-portable-*.tmp")))
            self.assertFalse(list(output.glob(".*.sha256.*.tmp")))
