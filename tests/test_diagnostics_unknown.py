import tempfile
import unittest
from pathlib import Path

from bot.diagnostics import diagnose_failure


class UnknownDiagnosticsTests(unittest.TestCase):
    def test_unknown_traceback_collects_bounded_source_context_without_editing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "app.py"
            original = "def work():\n    value = 1\n    return value + missing\n\nwork()\n"
            target.write_text(original, encoding="utf-8")
            log = (
                "Traceback (most recent call last):\n"
                f'  File "{target}", line 3, in work\n'
                "    return value + missing\n"
                "NameError: name 'missing' is not defined\n"
            )
            result = diagnose_failure(log, root)
            self.assertEqual(result["category"], "unknown")
            self.assertFalse(result["automatic_source_edit_applied"])
            self.assertEqual(result["details"]["error_type_guess"], "NameError")
            frame = result["details"]["traceback_frames"][0]
            self.assertEqual(frame["path"], "app.py")
            self.assertTrue(frame["source_read"])
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertIn("reversible experiment", result["details"]["recommendation"])
            self.assertIn("identifier spelling", result["details"]["suggested_experiment"])

    def test_external_traceback_path_is_not_read(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            external = Path(outside) / "secret.py"
            external.write_text("secret = True\n", encoding="utf-8")
            result = diagnose_failure(
                f'  File "{external}", line 1, in <module>\nRuntimeError: unknown',
                root,
            )
            frame = result["details"]["traceback_frames"][0]
            self.assertFalse(frame["source_read"])
            self.assertIn("outside project", frame["reason"])


if __name__ == "__main__":
    unittest.main()
