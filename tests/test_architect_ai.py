"""Tests for BOTPYTON's opt-in, bounded external architecture advisor."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from bot.architect_ai import (
    API_ENDPOINT,
    API_KEY_ENV,
    FREE_MODEL,
    ArchitectAIError,
    request_architecture_advice,
)
from bot.main import main


class ArchitectAIAdapterTests(unittest.TestCase):
    def test_no_api_key_fails_before_network(self):
        brief = {
            "task_type": "design",
            "goal": "Design a bounded plugin architecture for a desktop software tool",
        }
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "bot.architect_ai.urlopen"
        ) as open_url:
            with self.assertRaisesRegex(ArchitectAIError, "Set " + API_KEY_ENV):
                request_architecture_advice(brief)
        open_url.assert_not_called()

    def test_request_uses_only_free_router_and_selected_brief_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_path = str(Path(tmp))
            brief = {
                "schema_version": 1,
                "task_type": "design",
                "goal": "Design a maintainable plugin system with bounded memory use",
                "project_root": local_path,
                "constraints": ["Python standard library only"],
                "available_evidence": ["The test harness currently passes 20 local tests."],
            }
            response_bytes = json.dumps({
                "choices": [{"message": {"content": "PLAN\n1. Define interfaces.\n2. Add tests."}}]
            }).encode("utf-8")
            response = io.BytesIO(response_bytes)
            with mock.patch("bot.architect_ai.urlopen", return_value=response) as open_url:
                result = request_architecture_advice(brief, api_key="test-key-not-real")

            self.assertEqual(result["status"], "advice_received")
            self.assertEqual(result["model"], FREE_MODEL)
            self.assertIn("Define interfaces", result["advice"])
            self.assertTrue(result["safety"]["network_used"])
            self.assertFalse(result["safety"]["project_files_uploaded"])
            self.assertFalse(result["safety"]["project_code_executed"])
            self.assertFalse(result["safety"]["commands_executed"])
            open_url.assert_called_once()
            request = open_url.call_args.args[0]
            self.assertEqual(request.full_url, API_ENDPOINT)
            self.assertEqual(request.get_header("Authorization"), "Bearer test-key-not-real")
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(payload["model"], "openrouter/free")
            self.assertFalse(payload["stream"])
            self.assertLessEqual(payload["max_tokens"], 1400)
            sent = request.data.decode("utf-8")
            self.assertNotIn(local_path, sent)
            self.assertNotIn('"project_root"', sent)
            self.assertNotIn('"source_code"', sent)

    def test_blocked_local_brief_never_uses_the_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_root = str(Path(tmp) / "does-not-exist")
            brief = {
                "task_type": "implement",
                "goal": "Implement a bounded local data validation feature",
                "project_root": missing_root,
            }
            with mock.patch("bot.architect_ai.urlopen") as open_url:
                result = request_architecture_advice(brief, api_key="test-key-not-real")
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["safety"]["network_used"])
        open_url.assert_not_called()

    def test_rate_limit_is_reported_without_retry(self):
        brief = {
            "task_type": "design",
            "goal": "Design a reliable offline plugin loading architecture",
        }
        with mock.patch(
            "bot.architect_ai.urlopen",
            side_effect=HTTPError(API_ENDPOINT, 429, "rate limit", {}, None),
        ) as open_url:
            with self.assertRaisesRegex(ArchitectAIError, "free request limit") as raised:
                request_architecture_advice(brief, api_key="test-key-not-real")
        self.assertTrue(raised.exception.network_used)
        open_url.assert_called_once()

    def test_cli_requires_explicit_external_ai_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brief.json"
            path.write_text(json.dumps({
                "task_type": "design",
                "goal": "Design a bounded and testable plugin architecture",
            }), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), mock.patch(
                "bot.architect_ai.urlopen"
            ) as open_url:
                code = main(["--architect-ai", str(path)])
            self.assertEqual(code, 2)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["status"], "consent_required")
            self.assertFalse(result["safety"]["network_used"])
            open_url.assert_not_called()

    def test_allow_external_ai_flag_without_advisor_command_is_rejected(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["--allow-external-ai"])
        self.assertEqual(code, 2)



    def test_source_context_requires_separate_consent_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "src.py").write_text("def f(): return 1\n", encoding="utf-8")
            brief = {
                "task_type": "implement",
                "goal": "Implement a small safe input parsing feature",
                "project_root": str(project),
            }
            with mock.patch("bot.architect_ai.urlopen") as open_url:
                with self.assertRaisesRegex(ArchitectAIError, "Explicit source-context consent"):
                    request_architecture_advice(
                        brief, api_key="test-key-not-real", context_paths=["src.py"]
                    )
            open_url.assert_not_called()

    def test_explicit_source_context_is_bounded_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            src = project / "src" / "parser.py"
            src.parent.mkdir()
            src.write_text("def parse_count(value):\n    return int(value)\n", encoding="utf-8")
            brief = {
                "task_type": "implement",
                "goal": "Add explicit validation to the parser function",
                "project_root": str(project),
                "acceptance_criteria": ["Invalid input returns a controlled error"],
            }
            response_bytes = json.dumps({
                "choices": [{"message": {"content": "Inspect the conversion contract and add regression cases."}}]
            }).encode("utf-8")
            with mock.patch("bot.architect_ai.urlopen", return_value=io.BytesIO(response_bytes)) as open_url:
                result = request_architecture_advice(
                    brief, api_key="test-key-not-real",
                    context_paths=["src/parser.py"], send_source_context=True,
                )
            self.assertEqual(result["status"], "advice_received")
            self.assertTrue(result["safety"]["project_files_uploaded"])
            self.assertEqual(result["safety"]["source_context_file_count"], 1)
            self.assertEqual(result["safety"]["source_context_paths"], ["src/parser.py"])
            self.assertFalse(result["safety"]["project_code_executed"])
            request = open_url.call_args.args[0]
            payload = json.loads(request.data.decode("utf-8"))
            sent = payload["messages"][1]["content"]
            self.assertIn("def parse_count(value)", sent)
            self.assertIn('"src/parser.py"', sent)
            self.assertNotIn(str(project), sent)
            system_prompt = payload["messages"][0]["content"]
            self.assertIn("BORRADOR DE PARCHE (NO APLICADO)", system_prompt)
            self.assertIn("limited to those exact supplied paths", system_prompt)

    def test_source_context_rejects_path_traversal_and_secret_like_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "src.py").write_text("API_KEY = 'sk-or-v1-123456789012345678901234567890'\n", encoding="utf-8")
            brief = {
                "task_type": "implement",
                "goal": "Implement a small safe input parsing feature",
                "project_root": str(project),
            }
            with mock.patch("bot.architect_ai.urlopen") as open_url:
                with self.assertRaisesRegex(ArchitectAIError, "traversal"):
                    request_architecture_advice(
                        brief, api_key="test-key-not-real",
                        context_paths=["../src.py"], send_source_context=True,
                    )
                with self.assertRaisesRegex(ArchitectAIError, "credential or private key"):
                    request_architecture_advice(
                        brief, api_key="test-key-not-real",
                        context_paths=["src.py"], send_source_context=True,
                    )
            open_url.assert_not_called()

    def test_cli_source_context_requires_second_consent_and_accepts_selected_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            (project / "parser.py").write_text("def parse_value(value): return int(value)\n", encoding="utf-8")
            brief_path = Path(tmp) / "brief.json"
            brief_path.write_text(json.dumps({
                "task_type": "implement",
                "goal": "Implement a small safe input parsing feature",
                "project_root": str(project),
            }), encoding="utf-8")
            no_consent_output = io.StringIO()
            with contextlib.redirect_stdout(no_consent_output), mock.patch.dict(
                os.environ, {API_KEY_ENV: "test-key-not-real"}, clear=True
            ), mock.patch("bot.architect_ai.urlopen") as open_url:
                code = main([
                    "--architect-ai", str(brief_path), "--allow-external-ai",
                    "--architect-ai-context", "parser.py",
                ])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(no_consent_output.getvalue())["status"], "source_consent_required")
            open_url.assert_not_called()

            response_bytes = json.dumps({
                "choices": [{"message": {"content": "Review type conversion and test invalid input."}}]
            }).encode("utf-8")
            consent_output = io.StringIO()
            with contextlib.redirect_stdout(consent_output), mock.patch.dict(
                os.environ, {API_KEY_ENV: "test-key-not-real"}, clear=True
            ), mock.patch("bot.architect_ai.urlopen", return_value=io.BytesIO(response_bytes)) as open_url:
                code = main([
                    "--architect-ai", str(brief_path), "--allow-external-ai",
                    "--architect-ai-context", "parser.py", "--send-source-context",
                ])
            self.assertEqual(code, 0)
            result = json.loads(consent_output.getvalue())
            self.assertTrue(result["safety"]["project_files_uploaded"])
            open_url.assert_called_once()

if __name__ == "__main__":
    unittest.main()
