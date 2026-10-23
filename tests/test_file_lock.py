import tempfile
import unittest
from pathlib import Path

from bot.file_lock import exclusive_file_lock


class ExistingFileLockTests(unittest.TestCase):
    def test_noncreating_lock_fails_when_sidecar_is_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "missing.lock"

            with self.assertRaises(FileNotFoundError):
                with exclusive_file_lock(path, create=False):
                    self.fail("missing lock should not be created")

            self.assertFalse(path.exists())

    def test_noncreating_lock_preserves_existing_sidecar_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "existing.lock"
            sentinel = b"do not rewrite this lock file"
            path.write_bytes(sentinel)

            with exclusive_file_lock(path, create=False):
                self.assertTrue(path.exists())

            self.assertEqual(path.read_bytes(), sentinel)


if __name__ == "__main__":
    unittest.main()
