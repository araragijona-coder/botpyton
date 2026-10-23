"""Regression tests proving external architecture advice remains disabled offline."""
from __future__ import annotations

import contextlib
import io
import json
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import bot.architect_ai as architect_ai
from bot.architect_ai import ArchitectAIError, request_architecture_advice
from bot.main import main


class OfflineArchitectureAdviceTests(unittest.TestCase):
    def test_adviser_module_has_no_network_client_or_provider_endpoint(self):
        source = Path(architect_ai.__file__).read_text(encoding="utf-8").casefold()
        self.assertNotIn("urllib.request", source)
        self.assertNotIn("urlopen", source)
        self.assertNotIn("https://", source)
        self.assertNotIn("openrouter", source)
        self.assertNotIn("api_key", source)

    def test_direct_adviser_call_fails_before_network(self):
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network attempted")) as open_url:
            with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network attempted")) as connect:
                with self.assertRaises(ArchitectAIError) as raised:
                    request_architecture_advice({"goal": "design a local tool"}, api_key="must-not-be-read")
        self.assertFalse(raised.exception.network_used)
        self.assertIn("offline policy", str(raised.exception))
        open_url.assert_not_called()
        connect.assert_not_called()

    def test_every_legacy_ai_cli_flag_fails_closed_before_file_access(self):
        nonexistent = "this-file-must-not-be-opened-by-the-disabled-feature.json"
        flag_sets = (
            ["--architect-ai", nonexistent],
            ["--allow-external-ai"],
            ["--architect-ai-context", "src/module.py"],
            ["--send-source-context"],
            ["--architect-ai-failure-report", nonexistent],
            ["--send-failure-report"],
        )
        for args in flag_sets:
            with self.subTest(args=args):
                output = io.StringIO()
                with contextlib.redirect_stdout(output), mock.patch(
                    "bot.main._read_bounded_json",
                    side_effect=AssertionError("disabled feature must not read input files"),
                ) as reader, mock.patch.object(
                    urllib.request, "urlopen", side_effect=AssertionError("network attempted")
                ) as open_url, mock.patch.object(
                    socket, "create_connection", side_effect=AssertionError("network attempted")
                ) as connect:
                    code = main(args)
                self.assertEqual(code, 2)
                result = json.loads(output.getvalue())
                self.assertEqual(result["status"], "disabled_offline_policy")
                self.assertFalse(result["safety"]["network_used"])
                self.assertFalse(result["safety"]["external_ai_enabled"])
                self.assertFalse(result["safety"]["project_files_uploaded"])
                self.assertFalse(result["safety"]["failure_report_uploaded"])
                reader.assert_not_called()
                open_url.assert_not_called()
                connect.assert_not_called()

    def test_deterministic_architecture_plan_remains_available_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            brief_path = Path(temp_dir) / "brief.json"
            brief_path.write_text(json.dumps({
                "schema_version": 1,
                "task_type": "design",
                "goal": "Design a bounded offline plugin architecture for a desktop utility",
                "constraints": ["No external AI or model APIs"],
                "must_not_change": ["Do not make network requests"],
            }), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(
                urllib.request, "urlopen", side_effect=AssertionError("network attempted")
            ) as open_url, mock.patch.object(
                socket, "create_connection", side_effect=AssertionError("network attempted")
            ) as connect:
                code = main(["--architect-plan", str(brief_path)])
            result = json.loads(output.getvalue())
            self.assertIn(code, (0, 2))
            self.assertIn("decision", result)
            open_url.assert_not_called()
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
