import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bot.bounded_io import BoundedReadError, enforce_json_depth, read_bounded_bytes


class BoundedIOTests(unittest.TestCase):
    def test_reads_small_regular_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.json"
            path.write_bytes(b'{"ok":true}')
            self.assertEqual(read_bounded_bytes(path, limit_bytes=20), b'{"ok":true}')

    def test_rejects_oversized_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.json"
            path.write_bytes(b"x" * 101)
            with self.assertRaisesRegex(BoundedReadError, "exceeds 100 bytes"):
                read_bounded_bytes(path, limit_bytes=100)

    def test_rejects_file_that_grows_after_initial_stat(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.json"
            path.write_bytes(b"x" * 101)
            actual = path.stat()
            fake_before = SimpleNamespace(
                st_dev=actual.st_dev,
                st_ino=actual.st_ino,
                st_size=10,
                st_mtime=actual.st_mtime,
                st_ctime=actual.st_ctime,
                st_mtime_ns=actual.st_mtime_ns,
                st_ctime_ns=actual.st_ctime_ns,
                st_mode=actual.st_mode,
            )
            with patch("bot.bounded_io.Path.stat", return_value=fake_before):
                with self.assertRaisesRegex(BoundedReadError, "changed while it was being opened"):
                    read_bounded_bytes(path, limit_bytes=100)

    def test_rejects_excessive_json_depth(self):
        value = 0
        for _ in range(65):
            value = [value]
        with self.assertRaisesRegex(BoundedReadError, "nesting depth limit"):
            enforce_json_depth(value, max_depth=64)


if __name__ == "__main__":
    unittest.main()
