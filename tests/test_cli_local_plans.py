import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bot.main import main


class LocalPlanCliTests(unittest.TestCase):
    def test_local_plan_defaults_to_dry_run(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            plan_path = base / "plan.json"
            plan_path.write_text(json.dumps({
                "schema_version": 1,
                "task_id": "cli-dry-run",
                "changes": [{"path": "hello.py", "content": "print('hello')\n"}],
            }), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--local-plan", str(plan_path), "--project-root", str(root)])
            self.assertEqual(code, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["mode"], "validated_dry_run")
            self.assertFalse(report["writes_performed"])
            self.assertFalse((root / "hello.py").exists())

    def test_execute_flag_requires_plan(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = main(["--execute-local-plan"])
        self.assertEqual(code, 2)
        self.assertIn("--execute-local-plan requires --local-plan", errors.getvalue())

    def test_tools_cli_returns_json(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--tools"])
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "discovery_complete")
        self.assertFalse(report["ai_enabled"])

    def test_system_profile_cli_returns_bounded_memory_budget(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--system-profile"])
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertGreaterEqual(report["recommended_child_memory_limit_mb"], 64)
        self.assertLessEqual(report["recommended_child_memory_limit_mb"], 768)
        self.assertFalse(report["network_used"])


if __name__ == "__main__":
    unittest.main()
