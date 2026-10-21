import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.toolchain_matrix import (
    MAX_SCAN_ENTRIES,
    ToolchainMatrixError,
    inspect_toolchain_matrix,
)


class ToolchainMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _adapter_map(self, report):
        return {item["adapter_id"]: item for item in report["adapters"]}

    def test_detects_multiple_toolchains_without_running_project_code(self):
        (self.root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
        (self.root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "package.json").write_text(
            json.dumps({"scripts": {"test": "node test.js", "build": "node build.js"}}),
            encoding="utf-8",
        )
        (self.root / "app.ts").write_text("export const value = 1;\n", encoding="utf-8")
        (self.root / "tests").mkdir()

        report = inspect_toolchain_matrix(
            self.root,
            available_tool_ids={"python", "node", "npm"},
            available_check_ids={"python-unittest", "npm-test", "npm-build"},
        )

        adapters = self._adapter_map(report)
        self.assertEqual(report["status"], "discovery_complete")
        self.assertIn("python", adapters)
        self.assertIn("javascript-typescript", adapters)
        self.assertEqual(adapters["python"]["available_check_ids"], ["python-unittest"])
        self.assertEqual(
            adapters["javascript-typescript"]["available_check_ids"],
            ["npm-build", "npm-test"],
        )
        self.assertFalse(report["commands_executed"])
        self.assertFalse(report["project_code_executed"])
        self.assertFalse(report["files_modified"])
        self.assertFalse(report["network_used"])
        self.assertFalse(report["ai_enabled"])

    def test_missing_required_tools_are_reported_without_installing(self):
        (self.root / "Cargo.toml").write_text("[package]\nname='sample'\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")

        report = inspect_toolchain_matrix(self.root, available_tool_ids=set(), available_check_ids=set())
        rust = self._adapter_map(report)["rust"]
        self.assertEqual(rust["status"], "required_tool_missing")
        self.assertEqual(rust["missing_required_tool_groups"], [["cargo"]])
        self.assertEqual(report["tool_inventory"]["available_tool_ids"], [])

    def test_detects_graphical_project_markers_without_launching_gui(self):
        (self.root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
        (self.root / "scene.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
        (self.root / "scene.blend").write_bytes(b"fixture-not-opened")

        report = inspect_toolchain_matrix(
            self.root,
            available_tool_ids={"godot"},
            available_check_ids=set(),
        )
        adapters = self._adapter_map(report)
        self.assertIn("godot", adapters)
        self.assertEqual(adapters["godot"]["status"], "detected_no_matching_builtin_profile")
        self.assertIn("blender", adapters)
        self.assertEqual(adapters["blender"]["status"], "required_tool_missing")
        self.assertFalse(report["commands_executed"])
        self.assertFalse(report["project_code_executed"])

    def test_generated_directories_are_skipped(self):
        generated = self.root / "node_modules"
        generated.mkdir()
        (generated / "hidden.ts").write_text("export {};\n", encoding="utf-8")
        (self.root / "README.md").write_text("plain docs\n", encoding="utf-8")

        report = inspect_toolchain_matrix(
            self.root, available_tool_ids={"node"}, available_check_ids=set()
        )
        self.assertEqual(report["adapters"], [])
        self.assertEqual(report["scan"]["files_seen"], 1)

    def test_symlinked_directories_are_not_followed(self):
        external = tempfile.TemporaryDirectory()
        alias = self.root / "linked"
        try:
            target = Path(external.name)
            (target / "package.json").write_text(
                '{"scripts":{"test":"do-not-run"}}', encoding="utf-8"
            )
            try:
                alias.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation is unavailable: {type(exc).__name__}")
            report = inspect_toolchain_matrix(
                self.root, available_tool_ids={"node", "npm"}, available_check_ids={"npm-test"}
            )
            self.assertNotIn("javascript-typescript", self._adapter_map(report))
            self.assertEqual(report["scan"]["link_or_junctions_skipped"], 1)
        finally:
            alias.unlink(missing_ok=True)
            external.cleanup()

    def test_scan_limit_is_reported_as_partial(self):
        for index in range(5):
            (self.root / f"file_{index}.py").write_text("VALUE = 1\n", encoding="utf-8")
        with patch("bot.toolchain_matrix.MAX_SCAN_ENTRIES", 2):
            report = inspect_toolchain_matrix(
                self.root, available_tool_ids={"python"}, available_check_ids=set()
            )
        self.assertEqual(report["status"], "partial")
        self.assertTrue(report["scan"]["scan_truncated"])
        self.assertLessEqual(report["scan"]["entries_examined"], 2)

    def test_report_is_deterministic_for_unchanged_tree(self):
        (self.root / "pyproject.toml").write_text("[project]\nname='stable'\n", encoding="utf-8")
        (self.root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        kwargs = {
            "available_tool_ids": {"python"},
            "available_check_ids": set(),
        }
        first = inspect_toolchain_matrix(self.root, **kwargs)
        second = inspect_toolchain_matrix(self.root, **kwargs)
        self.assertEqual(first, second)

    def test_dotnet_solution_does_not_create_a_false_cpp_match(self):
        (self.root / "Example.sln").write_text("Microsoft Visual Studio Solution File\n", encoding="utf-8")
        report = inspect_toolchain_matrix(
            self.root, available_tool_ids={"dotnet"}, available_check_ids=set()
        )
        adapter_ids = set(self._adapter_map(report))
        self.assertIn("dotnet", adapter_ids)
        self.assertNotIn("c-cpp", adapter_ids)

    def test_invalid_project_root_is_rejected(self):
        with self.assertRaises(ToolchainMatrixError):
            inspect_toolchain_matrix(self.root / "missing")

    def test_cli_exposes_matrix_as_standalone_json_operation(self):
        from bot import main as main_module

        expected = {
            "status": "discovery_complete",
            "commands_executed": False,
            "project_code_executed": False,
            "files_modified": False,
            "network_used": False,
            "ai_enabled": False,
        }
        with patch("bot.main.inspect_toolchain_matrix", return_value=expected):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main_module.main(["--toolchain-matrix", "--project-root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), expected)


if __name__ == "__main__":
    unittest.main()
