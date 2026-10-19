import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.unknown_recovery import (
    MAX_LOG_BYTES,
    UnknownRecoveryError,
    investigate_failure,
)


class UnknownRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_unknown_failure_without_discriminating_evidence_stops_and_requests_specific_data(self):
        (self.root / "app.py").write_text("def work(value):\n    return value\n", encoding="utf-8")
        original = (self.root / "app.py").read_bytes()
        report = investigate_failure("VendorEngine: failure 0xDEADBEEF", self.root)
        self.assertEqual(report["status"], "needs_more_evidence")
        self.assertEqual(report["category"], "unknown")
        self.assertEqual(report["hypotheses"], [])
        self.assertIn("exact_command_and_arguments", report["missing_evidence"])
        self.assertIn("process_exit_code", report["missing_evidence"])
        self.assertFalse(report["root_cause_confirmed"])
        self.assertFalse(report["automatic_retry_permitted"])
        self.assertFalse(report["automatic_source_edit_applied"])
        self.assertFalse(report["project_code_executed"])
        self.assertEqual((self.root / "app.py").read_bytes(), original)

    def test_static_python_json_and_toml_findings_create_falsifiable_hypotheses(self):
        (self.root / "broken.py").write_text("def broken(\n", encoding="utf-8")
        (self.root / "settings.json").write_text('{"broken": }', encoding="utf-8")
        (self.root / "pyproject.toml").write_text('[project\nname = "bad"\n', encoding="utf-8")
        originals = {p.name: p.read_bytes() for p in self.root.iterdir()}
        report = investigate_failure(
            "UnrecognizedToolFailure: process stopped",
            self.root,
            command="python tools/run.py",
            exit_code=7,
            expected="program completes successfully",
            observed="exits before producing the requested output",
        )
        self.assertEqual(report["status"], "investigation_complete")
        self.assertGreaterEqual(len(report["hypotheses"]), 3)
        self.assertEqual(report["hypotheses"][0]["id"], "HYP-PY-SYNTAX-01")
        self.assertIn("HYP-JSON-INPUT-01", [item["id"] for item in report["hypotheses"]])
        self.assertIn("HYP-TOML-CONFIG-01", [item["id"] for item in report["hypotheses"]])
        self.assertFalse(report["static_evidence"]["code_executed"])
        self.assertFalse(report["static_evidence"]["files_modified"])
        self.assertEqual(originals, {p.name: p.read_bytes() for p in self.root.iterdir()})
        for hypothesis in report["hypotheses"]:
            self.assertFalse(hypothesis["root_cause_confirmed"])
            self.assertTrue(hypothesis["experiment"])
            self.assertTrue(hypothesis["falsified_when"])

    def test_log_and_command_credentials_are_redacted_in_report(self):
        token = "ghp_" + "a" * 40
        report = investigate_failure(
            f"UnknownFailure: Authorization: Bearer {token}",
            self.root,
            command=f"tool --token={token}",
            exit_code=4,
        )
        rendered = json.dumps(report)
        self.assertNotIn(token, rendered)
        self.assertIn("[REDACTED", rendered)
        self.assertEqual(report["log_sha256"], hashlib.sha256(
            f"UnknownFailure: Authorization: Bearer {token}".encode()
        ).hexdigest())

    def test_known_signature_is_classified_but_never_retried(self):
        report = investigate_failure("subprocess.TimeoutExpired: timed out", self.root, exit_code=124)
        self.assertEqual(report["category"], "timeout")
        self.assertEqual(report["knowledge_rule_id"], "OP-TIMEOUT")
        self.assertFalse(report["automatic_retry_permitted"])
        self.assertTrue(any(item["id"] == "HYP-RESOURCE-BOUND-01" for item in report["hypotheses"]))

    def test_invalid_log_size_and_empty_log_fail_closed(self):
        with self.assertRaisesRegex(UnknownRecoveryError, "empty"):
            investigate_failure("   ", self.root)
        with self.assertRaisesRegex(UnknownRecoveryError, "exceeds"):
            investigate_failure("x" * (MAX_LOG_BYTES + 1), self.root)

    def test_invalid_attempt_budget_is_a_hard_stop(self):
        report = investigate_failure("opaque failure", self.root, attempt=4, max_attempts=3)
        self.assertEqual(report["status"], "blocked_attempt_limit")
        self.assertIn("attempt bound", report["next_step"].lower())
        self.assertFalse(report["automatic_retry_permitted"])

    def test_symlink_root_is_rejected_when_supported(self):
        parent = Path(self.temp.name).parent
        linked = parent / (Path(self.temp.name).name + "-link")
        try:
            linked.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        try:
            with self.assertRaisesRegex(UnknownRecoveryError, "symlink|reparse"):
                investigate_failure("unknown", linked)
        finally:
            linked.unlink(missing_ok=True)

    def test_generated_folders_are_not_scanned(self):
        generated = self.root / ".venv" / "lib"
        generated.mkdir(parents=True)
        (generated / "broken.py").write_text("def broken(\n", encoding="utf-8")
        (self.root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        report = investigate_failure("VendorEngineFailure: not recognized", self.root)
        self.assertEqual(report["static_evidence"]["python"]["files_checked"], 1)
        self.assertEqual(report["static_evidence"]["python"]["syntax_errors"], [])

    def test_scan_limit_is_reported_and_requests_remaining_scope(self):
        for n in range(4):
            (self.root / f"file_{n}.py").write_text("VALUE = 1\n", encoding="utf-8")
        with patch("bot.unknown_recovery.MAX_SCAN_FILES", 2):
            report = investigate_failure("Unknown failure", self.root)
        self.assertTrue(report["static_evidence"]["scan"]["scan_truncated"])
        self.assertIn("remaining_static_scan_scope", report["missing_evidence"])
        self.assertLessEqual(report["static_evidence"]["scan"]["candidate_files"], 2)

    def test_result_is_deterministic_except_for_no_time_fields(self):
        log = "VendorEngine: failure 0xABCD"
        first = investigate_failure(log, self.root, command="tool --safe", exit_code=9)
        second = investigate_failure(log, self.root, command="tool --safe", exit_code=9)
        for key in ("status", "category", "log_sha256", "static_evidence", "hypotheses", "missing_evidence"):
            self.assertEqual(first[key], second[key])


if __name__ == "__main__":
    unittest.main()
