from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from bot.packaging import build_portable_package, verify_portable_archive


class PortablePackageTests(unittest.TestCase):
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
                self.assertIn("BOTPYTON/package-manifest.json", names)
                self.assertNotIn("BOTPYTON/tests/test_pc_care.py", names)
                self.assertFalse(any("/state.json" in name or "/reports/" in name for name in names))
                self.assertTrue(all(name.startswith("BOTPYTON/") for name in names))
            checksum_line = checksum_path.read_text(encoding="ascii").strip()
            self.assertTrue(checksum_line.startswith(result["archive_sha256"] + "  "))

    def test_portable_verifier_rejects_corrupt_or_missing_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                verify_portable_archive(Path(tmp) / "missing.zip")
