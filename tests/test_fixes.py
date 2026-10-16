import json
import tempfile
import unittest
from pathlib import Path

from bot.fixes import apply_known_fix


class KnownFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_json_trailing_comma_is_removed_only_when_candidate_parses(self):
        target = self.root / "data.json"
        original = '{\n  "name": "Bot",\n  "enabled": true,\n}\n'
        target.write_text(original, encoding="utf-8")

        result = apply_known_fix("JSONDecodeError: Expecting property name enclosed in double quotes", self.root)

        self.assertTrue(result["applied"])
        self.assertEqual(result["rule"], "json.remove_single_trailing_comma")
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"name": "Bot", "enabled": True})
        backup = self.root / result["backup_path"]
        self.assertEqual(backup.read_text(encoding="utf-8"), original)
        self.assertTrue(result["automatic_source_edit_applied"])

    def test_python_missing_colon_is_repaired_only_for_exact_parser_message(self):
        target = self.root / "broken.py"
        original = "if True\n    value = 42\n"
        target.write_text(original, encoding="utf-8")

        result = apply_known_fix("SyntaxError: expected ':' at line 1", self.root)

        self.assertTrue(result["applied"])
        self.assertEqual(result["rule"], "python.add_missing_compound_header_colon")
        self.assertEqual(target.read_text(encoding="utf-8"), "if True:\n    value = 42\n")
        compile(target.read_text(encoding="utf-8"), str(target), "exec")
        backup = self.root / result["backup_path"]
        self.assertEqual(backup.read_text(encoding="utf-8"), original)

    def test_unsafe_syntax_errors_are_reported_without_modification(self):
        target = self.root / "broken.py"
        original = "value =\n"
        target.write_text(original, encoding="utf-8")

        result = apply_known_fix("SyntaxError: invalid syntax", self.root)

        self.assertFalse(result["applied"])
        self.assertEqual(target.read_text(encoding="utf-8"), original)
        self.assertFalse(result["automatic_source_edit_applied"])
        self.assertFalse((self.root / ".cerebro" / "backups").exists())

    def test_json_without_trailing_comma_is_not_rewritten(self):
        target = self.root / "data.json"
        original = '{"name": }\n'
        target.write_text(original, encoding="utf-8")

        result = apply_known_fix("JSONDecodeError: Expecting value", self.root)

        self.assertFalse(result["applied"])
        self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_multiple_invalid_json_files_are_ambiguous(self):
        first = self.root / "one.json"
        second = self.root / "two.json"
        first.write_text('{"a": 1,}\n', encoding="utf-8")
        second.write_text('{"b": 2,}\n', encoding="utf-8")
        originals = (first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8"))

        result = apply_known_fix("JSONDecodeError: trailing comma", self.root)

        self.assertFalse(result["applied"])
        self.assertEqual((first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8")), originals)

    def test_protected_workflow_json_cannot_be_modified(self):
        target = self.root / ".github" / "workflows" / "ci.json"
        target.parent.mkdir(parents=True)
        original = '{"name": "ci",}\n'
        target.write_text(original, encoding="utf-8")

        result = apply_known_fix("JSONDecodeError: trailing comma", self.root)

        self.assertFalse(result["applied"])
        self.assertIn("Protected path", result["reason"])
        self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_unknown_signature_never_changes_source(self):
        target = self.root / "data.json"
        original = '{"name": "ci",}\n'
        target.write_text(original, encoding="utf-8")

        result = apply_known_fix("Unrecognized failure", self.root)

        self.assertFalse(result["applied"])
        self.assertEqual(target.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
