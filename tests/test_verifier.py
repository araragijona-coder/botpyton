import tempfile
import unittest
from pathlib import Path

from bot.verifier import verify_python_files


class BoundedPythonVerifierTests(unittest.TestCase):
    def test_reports_syntax_errors_without_executing_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "broken.py"
            target.write_text("raise RuntimeError('must not run')\nif True print('bad')\n", encoding="utf-8")
            errors = verify_python_files(root)
            self.assertTrue(errors)
            self.assertIn("SyntaxError", errors[0])
            self.assertTrue(target.is_file())

    def test_skips_generated_directories_and_enforces_file_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ignored = root / "node_modules"
            ignored.mkdir()
            (ignored / "ignored.py").write_text("if broken", encoding="utf-8")
            (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "b.py").write_text("VALUE = 2\n", encoding="utf-8")
            errors = verify_python_files(root, max_files=1)
            self.assertTrue(any("file limit 1 reached" in item for item in errors))
            self.assertFalse(any("ignored.py" in item for item in errors))

    def test_oversized_python_file_is_reported_as_unverified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "large.py").write_text("x = 1\n" * 10, encoding="utf-8")
            errors = verify_python_files(root, max_file_bytes=5)
            self.assertTrue(any("source exceeds 5 bytes" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
