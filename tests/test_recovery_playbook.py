"""Regression tests for evidence-based failure diagnosis and stop gates."""
import unittest

from bot.recovery import classify_failure


class RecoveryPlaybookTests(unittest.TestCase):
    def test_known_failure_signatures_produce_steps_and_never_blind_retry(self):
        cases = [
            ("ModuleNotFoundError: No module named 'flask'", "PY-IMPORT-MISSING", "verify_declared_dependencies"),
            ("ImportError: cannot import name 'Client' from sdk", "PY-IMPORT-CONTRACT", "inspect_import_contract"),
            ("IndentationError: unexpected indent", "PY-INDENTATION", "inspect_syntax_location"),
            ("SyntaxError: expected ':'", "PY-SYNTAX", "inspect_syntax_location"),
            ("JSONDecodeError: Expecting value", "DATA-JSON", "validate_json_schema"),
            ("CONFLICT (content): app.py", "GIT-MERGE-CONFLICT", "rebuild_isolated_branch"),
            ("TypeError: expected str, got int", "PY-CALL-SIGNATURE", "inspect_call_contract"),
            ("AttributeError: object has no attribute value", "PY-OBJECT-ATTRIBUTE", "inspect_object_contract"),
            ("NameError: name 'entry' is not defined", "PY-NAME-SCOPE", "inspect_identifier_scope"),
            ("KeyError: 'status'", "PY-MAPPING-KEY", "inspect_mapping_schema"),
            ("IndexError: list index out of range", "PY-SEQUENCE-BOUNDS", "inspect_sequence_bounds"),
            ("FileNotFoundError: [Errno 2]", "PATH-MISSING", "inspect_file_path"),
            ("PermissionError: [Errno 13]", "PATH-PERMISSION", "inspect_path_permissions"),
            ("subprocess.TimeoutExpired: timed out", "OP-TIMEOUT", "isolate_slow_step"),
            ("AssertionError: expected 3 but got 4", "TEST-ASSERTION", "compare_expected_actual"),
            ("RecursionError: maximum recursion depth exceeded", "RESOURCE-RECURSION", "inspect_recursion_depth"),
            ("MemoryError: unable to allocate", "RESOURCE-MEMORY", "measure_memory_pressure"),
            ("Error [ERR_MODULE_NOT_FOUND]: Cannot find package", "NODE-MODULE", "resolve_javascript_module"),
            ("error TS2307: Cannot find module './x'", "TYPESCRIPT-MODULE", "resolve_typescript_module"),
            ("error TS2322: Type 'string' is not assignable", "TYPESCRIPT-TYPE", "inspect_typescript_contract"),
            ("go: no required module provides package example.invalid/pkg", "GO-MODULE", "resolve_go_module"),
            ("undefined: missingFunction", "GO-SYMBOL", "inspect_go_symbol"),
            ("error[E0308]: mismatched types", "RUST-COMPILE", "inspect_rust_compiler_diagnostic"),
            ("app.java: error: cannot find symbol", "JAVA-SYMBOL", "inspect_java_compile_resolution"),
            ("error CS0103: The name 'value' does not exist", "CS-SYMBOL", "inspect_csharp_scope"),
        ]
        for log, rule_id, strategy in cases:
            with self.subTest(log=log):
                decision = classify_failure(log, attempt=1, max_attempts=3)
                self.assertEqual(decision.rule_id, rule_id, decision)
                self.assertEqual(decision.strategy, strategy, decision)
                self.assertFalse(decision.retryable, "known signatures must not authorize an unchanged automatic retry")
                self.assertTrue(decision.diagnostic_steps)
                self.assertTrue(decision.safe_experiment)
                self.assertTrue(decision.stop_condition)
                self.assertTrue(decision.reference_url)
                self.assertIn("not yet confirmed", decision.reason)

    def test_go_dependency_error_is_not_misclassified_as_node_error(self):
        decision = classify_failure("cannot find module providing package example.invalid/x")
        self.assertEqual(decision.rule_id, "GO-MODULE")
        self.assertEqual(decision.strategy, "resolve_go_module")

    def test_specific_language_codes_beat_generic_wrapper_messages(self):
        syntax = classify_failure(
            "ImportError: Failed to import test module: test_broken\n"
            "Original traceback: SyntaxError: expected ':'",
        )
        self.assertEqual(syntax.rule_id, "PY-SYNTAX")
        self.assertEqual(syntax.strategy, "inspect_syntax_location")

        typescript = classify_failure(
            "error TS2307: Cannot find module './x' or its corresponding type declarations",
        )
        self.assertEqual(typescript.rule_id, "TYPESCRIPT-MODULE")
        self.assertEqual(typescript.strategy, "resolve_typescript_module")

    def test_unknown_failure_requests_evidence_and_stops(self):
        decision = classify_failure("unrecognized compiler crash", attempt=1, max_attempts=3)
        self.assertEqual(decision.category, "unknown")
        self.assertIsNone(decision.strategy)
        self.assertFalse(decision.retryable)
        self.assertIn("ask for help", decision.reason)
        self.assertIn("missing log", decision.safe_experiment)
        self.assertIn("Stop automatic recovery", decision.stop_condition)

    def test_attempt_limit_rejects_invalid_counts_and_never_retries(self):
        for attempt, maximum in ((0, 3), (4, 3), (True, 3), (1, 0)):
            with self.subTest(attempt=attempt, maximum=maximum):
                decision = classify_failure("SyntaxError: expected ':'", attempt=attempt, max_attempts=maximum)
                self.assertEqual(decision.category, "attempt_limit")
                self.assertFalse(decision.retryable)
                self.assertIsNone(decision.strategy)

    def test_attempt_number_does_not_change_a_deterministic_rule(self):
        first = classify_failure("SyntaxError: expected ':'", attempt=1, max_attempts=3)
        later = classify_failure("SyntaxError: expected ':'", attempt=2, max_attempts=3)
        self.assertEqual(first.rule_id, later.rule_id)
        self.assertFalse(first.retryable)
        self.assertFalse(later.retryable)


if __name__ == "__main__":
    unittest.main()
