"""Tests for BOTPYTON's deterministic architecture planning kernel."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bot.architect import ArchitectPlanError, build_architecture_plan
from bot.main import main


class ArchitectKernelTests(unittest.TestCase):
    def test_clear_design_request_produces_reviewable_plan_without_project(self):
        result = build_architecture_plan({
            "schema_version": 1,
            "task_type": "design",
            "goal": "Design an offline tool plugin system with bounded memory use",
            "constraints": ["standard library only", "no background service"],
        })
        self.assertEqual(result["decision"], "ready_to_plan")
        self.assertEqual(result["task_type"], "design")
        self.assertGreaterEqual(len(result["plan"]), 5)
        self.assertEqual(result["plan"][0]["id"], "A1")
        self.assertIn("evidence_to_collect", result["plan"][2])
        self.assertFalse(result["safety"]["ai_enabled"])
        self.assertFalse(result["safety"]["network_used"])
        self.assertFalse(result["safety"]["project_code_executed"])
        self.assertFalse(result["safety"]["files_written"])

    def test_implementation_without_project_path_must_ask(self):
        result = build_architecture_plan({
            "task_type": "implement",
            "goal": "Implement a configurable file validation feature",
            "acceptance_criteria": ["Invalid files are reported without modification"],
        })
        self.assertEqual(result["decision"], "needs_information")
        self.assertIn("project_root", {item["id"] for item in result["blocking_questions"]})
        self.assertTrue(result["plan"])

    def test_debug_without_error_or_reproduction_must_ask(self):
        result = build_architecture_plan({
            "task_type": "debug",
            "goal": "Debug the queue worker when it fails during a task",
            "project_root": ".",
        })
        self.assertEqual(result["decision"], "needs_information")
        missing = {item["id"] for item in result["blocking_questions"]}
        self.assertIn("failure_evidence", missing)

    def test_vague_request_is_not_silently_assumed(self):
        result = build_architecture_plan({"goal": "make it better"})
        self.assertEqual(result["decision"], "needs_information")
        self.assertIn("goal_specificity", {item["id"] for item in result["blocking_questions"]})

    def test_auto_classification_and_inventory_are_bounded_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
            (root / "app.py").write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
            before = sorted(p.name for p in root.iterdir())
            result = build_architecture_plan({
                "goal": "Fix the failing Python task executor",
                "project_root": str(root),
                "observed_error": "AssertionError: expected 1 but got 0",
            })
            after = sorted(p.name for p in root.iterdir())
            self.assertEqual(result["task_type"], "debug")
            self.assertEqual(result["decision"], "ready_to_plan")
            self.assertEqual(result["repository_inventory"]["language_counts"]["Python"], 1)
            self.assertEqual(before, after)
            self.assertFalse(result["safety"]["project_code_executed"])

    def test_nonexistent_project_path_blocks_without_inventing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "does-not-exist")
            result = build_architecture_plan({
                "task_type": "review",
                "goal": "Review the data validation and persistence boundaries",
                "project_root": missing,
            })
            self.assertEqual(result["decision"], "blocked")
            self.assertEqual(result["plan"], [])
            self.assertIn("project_root", {item["id"] for item in result["missing_information"]})
            self.assertFalse(Path(missing).exists())

    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(ArchitectPlanError):
            build_architecture_plan({
                "goal": "Design a bounded cache for local file metadata",
                "magic_auto_execute": True,
            })

    def test_invalid_schema_and_text_bounds_are_rejected(self):
        for brief in (
            {"schema_version": 2, "goal": "Design a bounded metadata cache"},
            {"schema_version": 1.0, "goal": "Design a bounded metadata cache"},
            {"goal": "x" * 2001},
            {"goal": "Design a bounded metadata cache", "constraints": ["x"] * 21},
        ):
            with self.subTest(brief_keys=list(brief)):
                with self.assertRaises(ArchitectPlanError):
                    build_architecture_plan(brief)

    def test_cli_outputs_json_and_returns_actionable_code_for_missing_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brief.json"
            path.write_text(json.dumps({
                "task_type": "debug",
                "goal": "Debug the parser crash on malformed input",
            }), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--architect-plan", str(path)])
            self.assertEqual(code, 2)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["decision"], "needs_information")
            self.assertIn("failure_evidence", {item["id"] for item in result["blocking_questions"]})

    def test_cli_plan_is_dry_run_and_declares_no_network_or_file_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brief.json"
            path.write_text(json.dumps({
                "task_type": "design",
                "goal": "Design a maintainable plugin boundary for local tools",
            }), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--architect-plan", str(path)])
            self.assertEqual(code, 0)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["decision"], "ready_to_plan")
            self.assertFalse(result["safety"]["network_used"])
            self.assertFalse(result["safety"]["files_written"])


if __name__ == "__main__":
    unittest.main()
