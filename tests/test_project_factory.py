import json
import tempfile
import unittest
from pathlib import Path

from bot.project_factory import CATALOG, create_project, list_templates, safe_name


class ProjectFactoryTests(unittest.TestCase):
    def test_catalog_is_fixed_and_ai_disabled(self):
        ids = {item["id"] for item in list_templates()}
        self.assertEqual(ids, set(CATALOG))
        self.assertIn("telegram-bot", ids)
        self.assertIn("card-rpg-web", ids)
        self.assertIn("desktop-utility", ids)

    def test_name_is_sanitized_and_invalid_empty_rejected(self):
        self.assertEqual(safe_name("Mi juego / nuevo"), "Mi-juego-nuevo")
        with self.assertRaises(ValueError):
            safe_name(" / ")

    def test_creates_manifest_and_template_files(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "JuegoPrueba"
            result = create_project("card-rpg-web", "Juego Prueba", target)
            self.assertEqual(result["status"], "created")
            self.assertFalse(result["ai_enabled"])
            self.assertTrue((target / "index.html").is_file())
            self.assertIn("Juego-Prueba", (target / "index.html").read_text(encoding="utf-8"))
            manifest = json.loads((target / "botpyton-project.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["template"], "card-rpg-web")
            self.assertEqual(manifest["files"], ["README.md", "index.html"])

    def test_existing_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "already-here"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                create_project("desktop-utility", "App", target)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_unknown_template_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                create_project("../not-a-template", "App", Path(temp) / "app")


if __name__ == "__main__":
    unittest.main()
